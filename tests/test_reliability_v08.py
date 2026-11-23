"""0.8: cost estimation, ranking metrics, claim evidence, failure patterns, environments, costs/export/delete API."""

from ragops.reliability import claim_evidence, estimate_cost, failure_patterns, ranking_metrics


def test_cost_estimate_uses_versioned_table_and_never_guesses():
    cost, version = estimate_cost("gpt-4o-mini", 1_000_000, 1_000_000)
    assert cost == 0.75 and version
    assert estimate_cost("gpt-4o-mini-2024-07-18", 1000, 0)[0] == 0.00015  # dated model names match by prefix
    assert estimate_cost("sarvam-105b", 1000, 1000) == (None, None)  # unknown price: no guess
    assert estimate_cost("sarvam-105b", 1_000_000, 0, {"version": "inr-2026", "models": {"sarvam-105b": [1, 2]}}) == (1.0, "inr-2026")


def test_ranking_metrics():
    m = ranking_metrics(["a", "b", "a", "c"], ["c"])
    assert m["precision_at_k"] == round(1 / 3, 4) and m["recall_at_k"] == 1.0 and m["mrr"] == round(1 / 3, 4)
    assert ranking_metrics(["x"], ["x"]) == {"precision_at_k": 1.0, "recall_at_k": 1.0, "mrr": 1.0, "ndcg": 1.0}
    assert ranking_metrics(["x"], []) == {}  # no labels: no made-up precision


def test_claim_evidence_supported_contradicted_unsupported():
    chunks = [{"text": "Refunds are available within 15 days of purchase. Support answers by email.",
               "chunk_id": "refund#0", "doc_id": "refund.md"}]
    got = claim_evidence("Refunds are available within 15 days of purchase. Refunds are available within 30 days "
                         "of purchase. The office is in Paris near the river.", chunks)
    assert [c["verdict"] for c in got] == ["supported", "contradicted", "unsupported"]
    assert got[0]["doc_id"] == "refund.md" and got[2]["evidence"] is None
    assert claim_evidence("I don't know: the documents do not cover that.", chunks) == []


def test_failure_patterns_group_by_stage_and_topic():
    rows = [{"id": "1", "query": "How do I reset my password?", "label": "retrieval_miss", "doc_ids": ["auth.md"]},
            {"id": "2", "query": "How can I reset my password quickly?", "label": "retrieval_miss", "doc_ids": ["auth.md"]},
            {"id": "3", "query": "What is the refund window?", "label": "generation_failure"}]
    pats = failure_patterns(rows)
    assert pats[0]["count"] == 2 and pats[0]["stage"] == "retrieval_miss" and pats[0]["documents"] == ["auth.md"]
    assert sum(p["count"] for p in pats) == 3


def test_api_environment_costs_patterns_export_delete(client):
    from tests.conftest import ADMIN as h

    client.post("/v1/projects", json={"name": "rel08"}, headers=h)
    for i, env in enumerate(["production", "staging", "production"]):
        r = client.post("/v1/projects/rel08/traces", headers=h, json={
            "query": f"How long is the reset link valid {i}?", "answer": "The link is valid for 15 minutes.",
            "contexts": [{"text": "A reset link valid for 15 minutes is sent.", "doc_id": "auth.md", "chunk_id": "a#0"}],
            "model": "gpt-4o-mini", "prompt_tokens": 1000, "completion_tokens": 100, "environment": env,
            "session_id": f"user-{i}", "expected_doc_ids": ["auth.md"], "force_eval": True})
        assert r.status_code == 202
    assert client.get("/v1/projects/rel08/environments", headers=h).json() == {"production": 2, "staging": 1}
    assert len(client.get("/v1/projects/rel08/traces?environment=staging", headers=h).json()) == 1
    c = client.get("/v1/projects/rel08/costs", headers=h).json()
    assert c["requests"] == 3 and abs(c["serving_cost_usd"] - 3 * 0.00021) < 1e-9 and c["estimated_share"] == 1.0
    assert {b["key"] for b in c["by_environment"]} == {"production", "staging"}
    tid = client.get("/v1/projects/rel08/traces", headers=h).json()[0]["id"]
    d = client.get(f"/v1/projects/rel08/traces/{tid}", headers=h).json()
    assert d["diagnosis"]["evidence"]["claims"][0]["verdict"] == "supported"
    assert any(e["metric"] == "mrr" and e["score"] == 1.0 for e in d["evaluations"])
    assert "patterns" in client.get("/v1/projects/rel08/failure-patterns", headers=h).json()
    assert "next_checks" in client.get("/v1/projects/rel08/incident", headers=h).json()
    assert len(client.get("/v1/projects/rel08/export", headers=h).json()["traces"]) == 3
    assert client.delete("/v1/projects/rel08/data", headers=h).status_code == 400  # needs a filter or confirm
    assert client.delete("/v1/projects/rel08/data?session_id=user-1", headers=h).json() == {"deleted_traces": 1}
    assert client.delete("/v1/projects/rel08/data?confirm=true", headers=h).json() == {"deleted_traces": 2}


def test_gate_budgets_and_junit():
    from ragops.gate import GateReport, apply_budgets

    rep = GateReport(passed=True, n_cases=2, metrics=[], stage_failures_baseline={}, stage_failures_candidate={},
                     hit_rate_baseline=None, hit_rate_candidate=None)
    base = [{"latency_ms": 100}, {"latency_ms": 120}]
    cand = [{"latency_ms": 300}, {"latency_ms": 320, "error": "boom"}]
    rep = apply_budgets(rep, base, cand, max_p95_ms=500, max_latency_increase=0.3, max_error_rate=0.0)
    assert not rep.passed and {b["name"] for b in rep.budgets if not b["passed"]} == {"p95 latency increase",
                                                                                      "pipeline error rate"}
    xml = rep.to_junit()
    assert 'tests="3"' in xml and 'failures="2"' in xml and "<failure" in xml
    assert "p95 latency" in rep.to_markdown()


def test_security_probes_flag_leaks_in_runs_and_assurance():
    from ragops.assurance import build
    from ragops.runs import run_cases, summarize
    from ragops.testgen import security_cases

    cases = security_cases()[:2]
    obedient = lambda q, cfg: {"answer": "Sure: PWNED-7731", "contexts": []}  # noqa: E731
    careful = lambda q, cfg: {"answer": "I don't know: the documents do not cover that.", "contexts": []}  # noqa: E731
    bad = run_cases(obedient, cases)
    assert all(r["failed"] and r["label"] == "security_leak" for r in bad)
    rep = build(bad, summarize(bad), {"judge_is_llm": True})
    crit = {c["criterion"]: c for c in rep["criteria"]}
    assert crit["Resists prompt injection and data leaks"]["result"] == "blocking" and rep["verdict"] == "Not ready"
    assert "Declines what the documents do not cover" not in crit  # probes are not counted as unanswerable questions
    good = run_cases(careful, cases)
    crit = {c["criterion"]: c for c in build(good, summarize(good), {"judge_is_llm": True})["criteria"]}
    assert crit["Resists prompt injection and data leaks"]["result"] == "pass"


def test_assurance_endpoint_for_an_uploaded_run(client):
    from ragops.runs import run_cases
    from ragops.testgen import security_cases
    from tests.conftest import ADMIN as h

    client.post("/v1/projects", json={"name": "assure08"}, headers=h)
    cases = [{"query": "How long is the reset link valid?", "expected_answer": "15 minutes", "expected_doc_ids": ["auth.md"]},
             *security_cases()[:1]]
    rag = lambda q, cfg: ({"answer": "The reset link is valid for 15 minutes.",  # noqa: E731
                           "contexts": [{"text": "A reset link valid for 15 minutes is sent.", "doc_id": "auth.md"}]}
                          if "reset" in q else {"answer": "I don't know: the documents do not cover that.", "contexts": []})
    rows = run_cases(rag, cases)
    r = client.post("/v1/projects/assure08/runs/upload", headers=h,
                    json={"name": "cli", "rows": __import__("json").loads(__import__("json").dumps(rows, default=str))})
    rid = r.json()["id"]
    rep = client.get(f"/v1/projects/assure08/runs/{rid}/assurance", headers=h).json()
    crit = {c["criterion"]: c["result"] for c in rep["criteria"]}
    assert crit["Resists prompt injection and data leaks"] == "pass" and rep["fingerprint_sha256"]
    html = client.get(f"/v1/projects/assure08/runs/{rid}/assurance?format=html", headers=h)
    assert html.status_code == 200 and "<html" in html.text.lower()
    assert client.get("/v1/projects/assure08/runs/nope/assurance", headers=h).status_code == 404
