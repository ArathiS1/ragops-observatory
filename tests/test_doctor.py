"""RAG doctor: diagnosis, treatment trials that verify a fix on the user's pipeline, CLI and API."""

from pathlib import Path

from typer.testing import CliRunner

from ragops import doctor as dr
from ragops.pipelines import HttpPipeline
from tests.test_byo_rag import CASES, fake_rag_transport

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "doctor"


def _example():
    import importlib.util

    from ragops.cli import _load_corpus
    from ragops.runs import load_dataset

    spec = importlib.util.spec_from_file_location("acme_rag_t", EXAMPLE / "acme_rag.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.answer, load_dataset(str(EXAMPLE / "tests.jsonl")), _load_corpus(str(EXAMPLE / "docs"), 40)


def test_doctor_finds_and_verifies_the_fix_on_a_real_pipeline():
    answer, cases, corpus = _example()
    rep, rows = dr.doctor(answer, cases, {"chunk_size": 12, "top_k": 1, "abstain_when_unsure": False}, corpus=corpus)
    assert rep.verdict == "broken" and rep.failures >= 10
    # the dominant cause: the right document was found but the passage with the answer was cut off (tiny chunks)
    assert rep.findings[0].stage == "incomplete_context" and rep.findings[0].examples
    assert {f.stage for f in rep.findings} >= {"incomplete_context", "knowledge_gap"}
    tried = [t.change for t in rep.trials]
    assert {"chunk_size": 24} in tried and {"top_k": 3} in tried and {"abstain_when_unsure": True} in tried
    # bigger top_k does not help this pipeline and must not be recommended
    assert not next(t for t in rep.trials if t.change == {"top_k": 3}).verified
    best = rep.best_fix["change"]
    assert best.get("chunk_size", 12) > 12
    # the recommendation is real: re-running with it gives fewer mistakes than before
    from ragops.runs import run_cases

    after = run_cases(answer, cases, {"chunk_size": 12, "top_k": 1, "abstain_when_unsure": False, **best}, corpus=corpus)
    assert sum(r["failed"] for r in after) < rep.failures
    md = rep.to_markdown()
    assert "Recommended change" in md and "Right document, wrong passage" in md


def test_correct_refusal_is_not_a_failure():
    answer, cases, corpus = _example()
    gap = [c for c in cases if "student discount" in c["query"]]
    from ragops.runs import run_cases

    guess = run_cases(answer, gap, {"abstain_when_unsure": False}, corpus=corpus)[0]
    refuse = run_cases(answer, gap, {"abstain_when_unsure": True}, corpus=corpus)[0]
    assert guess["failed"] and guess["label"] == "knowledge_gap"
    assert not refuse["failed"] and refuse["scores"]["answer_correctness"] == 1.0


def test_answer_correctness_metric():
    from ragops.eval.judges import HeuristicJudge
    from ragops.eval.types import RagSample

    j = HeuristicJudge()
    s = lambda a, e: j.score("answer_correctness", RagSample("q", a, expected_answer=e)).score  # noqa: E731
    assert s("Backups are taken every 6 hours.", "Backups are taken every 6 hours and kept for 35 days.") < 1
    assert s("Backups run every 12 hours and are kept 35 days.", "Backups every 6 hours, kept 35 days.") <= 0.5
    assert s("Backups every 6 hours, kept 35 days.", "Backups every 6 hours, kept 35 days.") == 1.0
    assert s("I don't know.", "I don't know: not covered.") == 1.0
    assert s("It costs 5 euros.", "I don't know.") == 0.0
    assert j.score("answer_correctness", RagSample("q", "x")).score is None


def test_no_knobs_explains_what_to_expose():
    rows = [{"query": "q", "answer": "a", "scores": {}, "failed": True, "label": "retrieval_miss", "evidence": {}}]
    rep = dr.diagnose_rows(rows)
    assert dr.propose_changes(rep, {}) == []
    assert "top_k" in dr.missing_knobs(rep, {})
    assert dr.propose_changes(rep, {"top_k": 4}) == [{"top_k": 8}, {"top_k": 12}]
    assert dr.propose_changes(rep, {"top_k": 4}, knobs={"top_k": [6, 4]}) == [{"top_k": 6}]


def test_cli_doctor(monkeypatch, tmp_path):
    from ragops.cli import app

    monkeypatch.chdir(EXAMPLE)
    out = tmp_path / "d.md"
    fixed = tmp_path / "fixed.yaml"
    r = CliRunner().invoke(app, ["doctor", "--out", str(out), "--try", "chunk_size=48", "--write-config", str(fixed)])
    assert r.exit_code == 2, r.output  # broken before treatment
    assert "chunk_size=48" in out.read_text(encoding="utf-8") and "✅" in out.read_text(encoding="utf-8")
    assert "chunk_size: 48" in fixed.read_text(encoding="utf-8")
    r2 = CliRunner().invoke(app, ["doctor", "--no-trial", "--config", str(fixed), "--out", str(out)])
    assert r2.exit_code == 1 and "Treatment trial" not in out.read_text(encoding="utf-8")


def test_api_doctor_with_treatment_trial(client, project, monkeypatch):
    name, h = project
    monkeypatch.setenv("RAGOPS_PIPELINE_ALLOWED_HOSTS", "rag.example")
    client.post(f"/v1/projects/{name}/pipelines", headers=h, json={
        "name": "support-rag", "url": "https://rag.example/ask", "answer_path": "data.output",
        "contexts_path": "data.sources", "retrieved_path": "data.candidates", "query_field": "question",
        "default_config": {"rerank": "bad", "context_k": 2}})
    monkeypatch.setattr("ragops.run_service.build_pipeline", lambda p: HttpPipeline(
        p.spec["url"], answer_path=p.spec["answer_path"], contexts_path=p.spec["contexts_path"],
        retrieved_path=p.spec["retrieved_path"], query_field=p.spec["query_field"], transport=fake_rag_transport()))
    run = client.post(f"/v1/projects/{name}/runs", headers=h, json={"pipeline": "support-rag", "cases": CASES}).json()
    d = client.get(f"/v1/projects/{name}/runs/{run['id']}/doctor", headers=h).json()
    assert d["verdict"] in ("needs work", "broken") and d["findings"]
    assert "ranking_failure" in {f["stage"] for f in d["findings"]}
    tr = client.post(f"/v1/projects/{name}/runs/{run['id']}/doctor/trial", headers=h,
                     json={"changes": [{"rerank": "good"}, {"context_k": 1}]}).json()
    assert len(tr["trials"]) == 2
    d2 = client.get(f"/v1/projects/{name}/runs/{run['id']}/doctor", headers=h).json()
    trials = {tuple(t["change"].items()): t for t in d2["trials"]}
    assert trials[(("rerank", "good"),)]["verified"] and d2["best_fix"]["change"] == {"rerank": "good"}
    md = client.get(f"/v1/projects/{name}/runs/{run['id']}/doctor", headers=h, params={"format": "md"}).text
    assert "Recommended change: `rerank=good`" in md


def test_production_doctor(client, project):
    from ragops.demo.corpus import DOCS

    name, h = project
    client.patch(f"/v1/projects/{name}/settings", headers=h, json={"sample_rate": 1.0})
    client.post(f"/v1/projects/{name}/corpus", headers=h, json={"documents": [
        {"doc_id": d["doc_id"], "title": d["title"], "text": d["text"]} for d in DOCS]})
    for i, q in enumerate(["What is the airspeed of an unladen swallow?", "How do I bake sourdough bread?"]):
        client.post(f"/v1/projects/{name}/traces", headers=h,
                    json={"id": f"pd{i}", "query": q, "answer": "It is 42 metres per second.",
                          "contexts": [{"text": DOCS[0]["text"][:200], "doc_id": DOCS[0]["doc_id"]}]})
    d = client.get(f"/v1/projects/{name}/doctor", headers=h).json()
    assert d["cases"] == 2 and d["failures"] == 2 and d["findings"][0]["stage"] == "knowledge_gap"
    assert "missing" in d["findings"][0]["title"].lower()
