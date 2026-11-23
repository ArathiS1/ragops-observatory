"""Bring-your-own-RAG: adapters, server-side test runs, run comparison, CLI init/test/push."""

import json
import os

import httpx
import pytest
from typer.testing import CliRunner

from ragops.demo import pipeline as demo
from ragops.demo.corpus import DOCS
from ragops.pipelines import HaystackPipeline, HttpPipeline, PipelineError, PythonPipeline, check_url_allowed, normalise

CASES = [{"query": q, "expected_doc_ids": [d["doc_id"]]} for d in DOCS for q in d["questions"][:2]]


def fake_rag_transport(default_cfg=None):
    """A user's RAG service with a nested response format: {"data": {"output": ..., "sources": [...]}}."""

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        out = demo.run(body["question"], {**(default_cfg or {}), **(body.get("config") or {})})
        def docs(items):
            return [{"page_content": c["text"], "metadata": {"doc_id": c["doc_id"], "chunk_id": c["chunk_id"]},
                     "score": c["score"]} for c in items]

        return httpx.Response(200, json={"data": {"output": out["answer"], "sources": docs(out["contexts"]),
                                                  "candidates": docs(out["retrieved"])}})

    return httpx.MockTransport(handler)


def test_http_pipeline_field_mapping():
    p = HttpPipeline("https://rag.example/ask", answer_path="data.output", contexts_path="data.sources",
                     query_field="question", transport=fake_rag_transport())
    out = p("How long are audit logs retained?", {})
    assert "1 year" in out["answer"] and out["contexts"][0]["doc_id"] == "data-retention"


def test_http_pipeline_error_is_recorded_not_raised():
    from ragops.runs import run_cases

    p = HttpPipeline("https://rag.example/ask", transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    row = run_cases(p, [{"query": "x"}])[0]
    assert row["label"] == "pipeline_error" and row["failed"] and "503" in row["error"]


def test_ssrf_guard(monkeypatch):
    monkeypatch.delenv("RAGOPS_PIPELINE_ALLOWED_HOSTS", raising=False)
    for url in ["http://127.0.0.1:9000/ask", "http://169.254.169.254/latest", "ftp://x.example/"]:
        with pytest.raises(PipelineError):
            check_url_allowed(url)
    monkeypatch.setenv("RAGOPS_PIPELINE_ALLOWED_HOSTS", "127.0.0.1")
    check_url_allowed("http://127.0.0.1:9000/ask")


def test_python_and_framework_adapters():
    assert PythonPipeline(lambda q: f"echo {q}")("hi")["answer"] == "echo hi"  # 1-arg functions work too

    class Doc:  # Haystack 2.x Document
        def __init__(self, content, doc_id, score):
            self.content, self.meta, self.score, self.id = content, {"doc_id": doc_id}, score, f"{doc_id}#0"

    class Answer:
        data = "Backups run every 6 hours."

    class FakeHaystack:
        def run(self, data, include_outputs_from=None):
            return {"retriever": {"documents": [Doc("Backups every 6 hours.", "backups", 0.9)]},
                    "answer_builder": {"answers": [Answer()]}}

    out = HaystackPipeline(FakeHaystack(), lambda q: {"retriever": {"query": q}})("how often are backups?")
    assert out["answer"] == "Backups run every 6 hours." and out["contexts"][0]["doc_id"] == "backups"
    assert normalise({"response": "a", "sources": ["s1"]})["contexts"][0]["text"] == "s1"


def test_server_side_runs_and_compare(client, project, monkeypatch):
    name, h = project
    monkeypatch.setenv("RAGOPS_PIPELINE_ALLOWED_HOSTS", "rag.example")
    r = client.post(f"/v1/projects/{name}/pipelines", headers={**h}, json={
        "name": "support-rag", "url": "https://rag.example/ask", "answer_path": "data.output",
        "contexts_path": "data.sources", "retrieved_path": "data.candidates", "query_field": "question",
        "headers": {"Authorization": "Bearer secret"}, "default_config": {"rerank": "good"}})
    assert r.status_code == 201 and r.json()["spec"]["headers"]["Authorization"] == "••••"
    monkeypatch.setattr("ragops.run_service.build_pipeline", lambda p: HttpPipeline(
        p.spec["url"], answer_path=p.spec["answer_path"], contexts_path=p.spec["contexts_path"],
        retrieved_path=p.spec["retrieved_path"], query_field=p.spec["query_field"], transport=fake_rag_transport()))
    monkeypatch.setattr("ragops.server.app.svc.load_corpus_view", __import__("ragops.services").services.load_corpus_view)
    chk = client.post(f"/v1/projects/{name}/pipelines/support-rag/check", headers=h,
                      json={"query": "How much does the Pro plan cost per user?"}).json()
    assert chk["label"] == "ok" and chk["contexts"]
    good = client.post(f"/v1/projects/{name}/runs", headers=h, json={"pipeline": "support-rag", "cases": CASES,
                                                                      "name": "good"}).json()
    bad = client.post(f"/v1/projects/{name}/runs", headers=h, json={
        "pipeline": "support-rag", "cases": CASES, "config": {"rerank": "bad", "context_k": 2}, "name": "bad"}).json()
    g = client.get(f"/v1/projects/{name}/runs/{good['id']}", headers=h).json()
    b = client.get(f"/v1/projects/{name}/runs/{bad['id']}", headers=h).json()
    assert g["status"] == "done" and g["progress"] == len(CASES) and len(g["cases"]) == len(CASES)
    assert b["summary"]["failure_rate"] > g["summary"]["failure_rate"]
    assert b["summary"]["stages"].get("ranking_failure", 0) > 0
    cmp_ = client.get(f"/v1/projects/{name}/runs/compare", headers=h,
                      params={"base": good["id"], "candidate": bad["id"]}).json()
    assert cmp_["report"]["passed"] is False and "BLOCKED" in cmp_["markdown"]
    assert len(client.get(f"/v1/projects/{name}/runs", headers=h).json()) == 2


def test_register_rejects_private_endpoint(client, project, monkeypatch):
    monkeypatch.delenv("RAGOPS_PIPELINE_ALLOWED_HOSTS", raising=False)
    name, h = project
    r = client.post(f"/v1/projects/{name}/pipelines", headers=h, json={"name": "x", "url": "http://127.0.0.1:8000/ask"})
    assert r.status_code == 400 and "private" in r.json()["detail"]


def test_run_from_golden_set_and_expected_answer_autofill(client, project, monkeypatch):
    from tests.conftest import demo_trace

    name, h = project
    body = demo_trace("How much does the Pro plan cost per user?", {"exclude_docs": ["pricing-plans"]}, id="mx")
    client.post(f"/v1/projects/{name}/traces", json={**body, "user_feedback": -1}, headers=h)
    r = client.post(f"/v1/projects/{name}/golden/promote", json={"trace_id": "mx", "golden_set": "gs"}, headers=h).json()
    case = client.get(f"/v1/projects/{name}/golden-sets/gs/export", headers=h).json()["cases"][0]
    assert r["added"] and "$29" in case["expected_answer"] and case["expected_answer_source"] == "extracted"
    monkeypatch.setenv("RAGOPS_PIPELINE_ALLOWED_HOSTS", "rag.example")
    client.post(f"/v1/projects/{name}/pipelines", headers=h, json={"name": "p", "url": "https://rag.example/ask"})
    monkeypatch.setattr("ragops.run_service.build_pipeline", lambda p: lambda q, c: demo.run(q, c))
    run = client.post(f"/v1/projects/{name}/runs", headers=h, json={"pipeline": "p", "golden_set": "gs"}).json()
    got = client.get(f"/v1/projects/{name}/runs/{run['id']}", headers=h).json()
    assert got["dataset"] == "gs" or got["dataset"].startswith("golden:gs")
    assert got["status"] == "done" and got["cases"][0]["scores"]["context_recall"] == 1.0


def test_cli_init_test_thresholds_and_push(tmp_path, client, project, monkeypatch):
    from ragops.cli import app

    name, h = project
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["init", "--project", name]).exit_code == 0
    assert {"ragops.yaml", "my_rag.py", "rag_tests.jsonl"} <= set(os.listdir(tmp_path))
    r = runner.invoke(app, ["test"])
    assert r.exit_code == 0, r.output
    assert "RAG test report" in (tmp_path / "ragops-report.md").read_text(encoding="utf-8")
    # the user's real pipeline: demo RAG with a broken reranker, checked against thresholds
    (tmp_path / "cases.jsonl").write_text("\n".join(json.dumps(c) for c in CASES), encoding="utf-8")
    (tmp_path / "bad.yaml").write_text("rerank: bad\ncontext_k: 2\n", encoding="utf-8")
    (tmp_path / "strict.yaml").write_text("thresholds: {max_failure_rate: 0.1}\n", encoding="utf-8")
    r = runner.invoke(app, ["test", "-f", "strict.yaml", "--pipeline", "ragops.demo.pipeline:pipeline", "--dataset", "cases.jsonl",
                            "--config", "bad.yaml", "--corpus", "ragops.demo.corpus:DOCS"])
    assert r.exit_code == 1 and "FAILED thresholds" in r.output and "ranking failure" in r.output
    # push to the server
    monkeypatch.setattr("httpx.post", lambda url, **kw: client.post(url.split("8000")[-1], json=kw["json"], headers=h))
    r = runner.invoke(app, ["test", "--pipeline", "ragops.demo.pipeline:pipeline", "--dataset", "cases.jsonl",
                            "--push", "--server", "http://localhost:8000", "--api-key", "x", "--project", name,
                            "--corpus", "ragops.demo.corpus:DOCS"])
    assert "pushed run" in r.output, r.output
    runs = client.get(f"/v1/projects/{name}/runs", headers=h).json()
    assert runs[0]["source"] == "cli" and runs[0]["summary"]["cases"] == len(CASES)


def test_sdk_step_context_managers(client, project):
    from fastapi.testclient import TestClient

    from ragops import RagopsClient

    name, h = project
    rg = RagopsClient("http://testserver", api_key="x", project=name, sync=True)
    rg._http = TestClient(client.app, headers=h)
    q = "How often does Nimbus back up customer data?"
    with rg.trace(q) as t:
        out = demo.run(q)
        with t.retrieve() as s:
            s.output(out["retrieved"])
        with t.build_context() as s:
            s.output(out["contexts"])
        with t.generate(model="demo-llm") as s:
            s.output(out["answer"], prompt_tokens=100)
    got = client.get(f"/v1/projects/{name}/traces/{t.id}", headers=h).json()
    assert set(got["metadata"]["step_latency_ms"]) == {"retrieve", "context", "generate"}
    assert got["model"] == "demo-llm"
