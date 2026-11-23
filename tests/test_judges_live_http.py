"""Run the real Ragas library and the RAGOps LLM judge over real HTTP against the deterministic stub server.

This exercises everything except the model itself: OpenAI client, JSON mode, Ragas prompts and parsing, retries,
embeddings and score extraction. (For a real model, see `ragops judge check`.)
"""

import socket
import threading
import time

import pytest
import uvicorn


@pytest.fixture(scope="module")
def stub_url():
    from ragops.testing.stub_llm import app

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/v1"
    server.should_exit = True


def _samples():
    from ragops.eval.types import ContextChunk, RagSample

    ctx = [ContextChunk("Audit logs are retained for one year on the Enterprise plan.", "data-retention")]
    good = RagSample("How long are audit logs kept?", "Audit logs are retained for one year on the Enterprise plan.",
                     ctx, expected_answer="Audit logs are retained for one year.")
    bad = RagSample("How long are audit logs kept?", "Invoices are emailed on the first business day of every month.",
                    [ContextChunk("Backups run every six hours across three regions.", "backups")],
                    expected_answer="Audit logs are retained for one year.")
    return good, bad


def test_real_ragas_library_end_to_end(stub_url, monkeypatch):
    pytest.importorskip("ragas")
    monkeypatch.setenv("OPENAI_API_KEY", "not-needed")
    from ragops.eval.judges import RagasJudge

    j = RagasJudge("stub-model", stub_url)
    good, bad = _samples()
    for metric in ("faithfulness", "answer_relevance", "context_relevance", "context_recall"):
        g, b = j.score(metric, good), j.score(metric, bad)
        assert g.judge == b.judge == "ragas", (metric, g.reasoning)
        assert g.score > b.score, metric
    assert j.score("citation_correctness", good).judge == "heuristic"  # deterministic metrics stay local


def test_ragas_falls_back_when_model_errors(monkeypatch):
    pytest.importorskip("ragas")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    from ragops.eval.judges import RagasJudge

    r = RagasJudge("m", "http://127.0.0.1:9/v1").score("faithfulness", _samples()[0])
    assert r.judge == "heuristic" and "ragas error" in r.reasoning


def test_ragops_llm_judge_over_http(stub_url):
    from ragops.eval.judges import LLMJudge, evaluate

    j = LLMJudge("stub-model", stub_url, api_key="x")
    good, bad = _samples()
    g, b = evaluate(good, j), evaluate(bad, j)
    assert g["faithfulness"].judge == "llm:stub-model" and g["faithfulness"].score > b["faithfulness"].score
    assert g["answer_relevance"].score > b["answer_relevance"].score


def test_judge_check(stub_url, monkeypatch):
    from ragops.eval.judges import HeuristicJudge, LLMJudge
    from ragops.judge_check import run_check

    r = run_check(HeuristicJudge())
    assert r["cases"] == 12 and r["fell_back"] == 0 and r["agreement"] >= 0.75
    r = run_check(LLMJudge("stub-model", stub_url, api_key="x"))
    assert r["fell_back"] == 0 and r["judge"] == "llm:stub-model"
    r = run_check(LLMJudge("m", "http://127.0.0.1:9/v1", api_key="x"))
    assert r["verdict"].startswith("not working")


def test_llm_judge_scores_answer_correctness_by_meaning():
    """A correct paraphrase must not be failed for using different words (the heuristic would score it low)."""
    import json as _json

    import httpx

    from ragops.eval.judges import HeuristicJudge, LLMJudge
    from ragops.eval.types import RagSample

    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        body = _json.loads(req.content)
        seen["system"] = body["messages"][0]["content"]
        seen["user"] = body["messages"][1]["content"]
        # Gemini-style reply: JSON inside a Markdown fence
        content = "```json\n{\"score\": 1.0, \"reason\": \"same facts: 30 days, annual plans\"}\n```"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "usage": {"total_tokens": 90}})

    s = RagSample("What is the refund window for annual plans?",
                  "If you're on a yearly subscription you can get your money back up to 30 days after being billed.",
                  expected_answer="Refunds are available within 30 days of a charge for annual plans.")
    assert HeuristicJudge().score("answer_correctness", s).score < 0.5  # word overlap misses the paraphrase
    j = LLMJudge("judge-model", "http://judge.test/v1")
    j.client = httpx.Client(base_url="http://judge.test/v1", transport=httpx.MockTransport(handler))
    r = j.score("answer_correctness", s)
    assert r.score == 1.0 and r.judge == "llm:judge-model"
    assert "by meaning" in seen["system"] and "REFERENCE:\nRefunds are available" in seen["user"]
    assert j.score("answer_correctness", RagSample("q", "a")).score is None  # no reference -> not scored


def test_llm_judge_waits_on_rate_limit_instead_of_falling_back(monkeypatch):
    """Free tiers answer 429 under a burst; the judge must wait and retry, not silently use the heuristic."""
    import httpx

    from ragops.eval.judges import LLMJudge
    from ragops.eval.types import ContextChunk, RagSample

    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(429, headers={"retry-after": "0.01"}, json={"error": {"message": "quota"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"score": 0.9, "reason": "ok"}'}}]})

    j = LLMJudge("m", "http://j.test/v1")
    j.client = httpx.Client(base_url="http://j.test/v1", transport=httpx.MockTransport(handler))
    r = j.score("faithfulness", RagSample("q", "a", [ContextChunk("a", "d", "d#0")]))
    assert calls["n"] == 3 and r.score == 0.9 and r.judge == "llm:m"
