"""0.9 dashboard data: cockpit, evidence graph, failure intelligence, release guardian, benchmark, integrations."""

import json

from tests.conftest import ADMIN as H


def _upload(client, project, name, rag, cases):
    from ragops.runs import run_cases

    rows = json.loads(json.dumps(run_cases(rag, cases), default=str))
    return client.post(f"/v1/projects/{project}/runs/upload", headers=H, json={"name": name, "rows": rows}).json()["id"]


CTX = [{"text": "A reset link valid for 15 minutes is sent by email.", "doc_id": "auth.md", "chunk_id": "auth#0"},
       {"text": "Refunds are available within 15 days of purchase.", "doc_id": "refund.md", "chunk_id": "refund#0"}]
CASES = [{"query": "How long is the reset link valid?", "expected_answer": "15 minutes", "expected_doc_ids": ["auth.md"]},
         {"query": "What is the refund window?", "expected_answer": "15 days", "expected_doc_ids": ["refund.md"]}]


def good(q, cfg):
    return {"answer": "The reset link is valid for 15 minutes." if "reset" in q else "Refunds are available within 15 days.",
            "contexts": CTX}


def bad(q, cfg):
    return {"answer": "The reset link is valid for 15 minutes." if "reset" in q else "Refunds take 90 days.",
            "contexts": CTX[:1]}


def test_cockpit_and_evidence_graph(project, client):
    name, h = project
    for i, env in enumerate(["production", "staging"]):
        client.post(f"/v1/projects/{name}/traces", headers=h, json={
            "query": "How long is the password reset link valid?", "answer": "The link is valid for 60 minutes. It is sent by SMS.",
            "retrieved": [{"text": "x", "doc_id": "other", "chunk_id": "o#0"}, {"text": CTX[0]["text"], "doc_id": "password-reset", "chunk_id": "pr#0"}],
            "contexts": [{"text": "To reset your password click Forgot password. The reset link expires after 60 minutes.",
                          "doc_id": "password-reset", "chunk_id": "pr#0"}],
            "expected_doc_ids": ["password-reset"], "latency_ms": 900, "user_feedback": 1 if i else -1,
            "environment": env, "metadata": {"step_latency_ms": {"retrieve": 120, "generate": 700}}, "force_eval": True})
    c = client.get(f"/v1/projects/{name}/cockpit?days=1", headers=h).json()
    assert c["cards"]["requests"]["value"] == 2 and len(c["cards"]["requests"]["series"]) >= 24
    flow = {s["stage"]: s for s in c["flow"]}
    assert flow["retrieve"]["p50_ms"] == 120 and flow["generate"]["count"] == 2
    assert c["hit_at_k"]["labelled"] == 2 and c["hit_at_k"]["top_1"] == 0 and c["hit_at_k"]["top_3"] == 1
    assert c["feedback"]["positive"] == 1 and c["feedback"]["negative"] == 1
    assert client.get(f"/v1/projects/{name}/cockpit?days=1&environment=staging", headers=h).json()["cards"]["requests"]["value"] == 1
    tid = client.get(f"/v1/projects/{name}/traces", headers=h).json()[0]["id"]
    g = client.get(f"/v1/projects/{name}/traces/{tid}/evidence-graph", headers=h).json()
    kinds = {n["kind"] for n in g["nodes"]}
    assert kinds == {"document", "chunk", "claim"} and g["summary"]["claims"] == 2
    assert g["summary"]["supported"] == 1 and g["summary"]["unsupported"] == 1
    assert any(e["kind"] == "supported" for e in g["edges"]) and any(e["kind"] == "contains" for e in g["edges"])


def test_failure_intelligence(project, client):
    name, h = project
    from tests.conftest import demo_trace

    for q in ["Is there a mobile app for iPhone?", "Do you have an iPhone mobile app?", "Is there an Android mobile app?"]:
        t = demo_trace(q, force_eval=True)
        t["answer"] = "Yes, the mobile app is available for iPhone and Android with offline mode."
        client.post(f"/v1/projects/{name}/traces", headers=h, json=t)
    fi = client.get(f"/v1/projects/{name}/failure-intelligence?days=7", headers=h).json()
    assert fi["failures"] >= 1 and fi["patterns"]
    p = fi["patterns"][0]
    assert p["trend"] in ("new", "growing", "steady", "shrinking") and p["likely_cause"] and len(p["series"]) >= 7
    assert p["first_seen"] and "closest_documents" in p["evidence"]


def test_release_guardian_and_decisions(client):
    client.post("/v1/projects", json={"name": "rel09"}, headers=H)
    base = _upload(client, "rel09", "v1", good, CASES)
    cand = _upload(client, "rel09", "v2", bad, CASES)
    chk = client.get(f"/v1/projects/rel09/release/check?base={base}&candidate={cand}", headers=H).json()
    assert chk["decision"] == "block" and chk["counts"]["regression"] == 1 and chk["counts"]["unchanged"] == 1
    assert any(not c["passed"] and c["gate"] == "Pass rate" for c in chk["checks"])
    ok = client.get(f"/v1/projects/rel09/release/check?base={base}&candidate={base}", headers=H).json()
    assert ok["decision"] == "pass"
    r = client.post("/v1/projects/rel09/release/decisions", headers=H, json={"base": base, "candidate": cand, "decision": "approve"})
    assert r.status_code == 422  # overriding a block needs a reason
    r = client.post("/v1/projects/rel09/release/decisions", headers=H,
                    json={"base": base, "candidate": cand, "decision": "approve", "reason": "hotfix, refund doc being rewritten"})
    assert r.status_code == 201 and r.json()["override"] is True
    hist = client.get("/v1/projects/rel09/release/decisions", headers=H).json()
    assert hist[0]["guardian"] == "block" and hist[0]["reason"].startswith("hotfix")
    assert client.put("/v1/projects/rel09/release/gates", headers=H, json={"min_pass_rate": 0.5}).json()["min_pass_rate"] == 0.5
    assert client.put("/v1/projects/rel09/release/gates", headers=H, json={"min_pas_rate": 0.5}).status_code == 422


def test_benchmark_and_integrations(client):
    client.post("/v1/projects", json={"name": "bench09"}, headers=H)
    a = _upload(client, "bench09", "good", good, CASES)
    b = _upload(client, "bench09", "bad", bad, CASES)
    bm = client.get(f"/v1/projects/bench09/runs/benchmark?ids={a},{b}", headers=H).json()
    assert bm["best"] == a and len(bm["runs"]) == 2
    assert bm["questions"][0]["disagree"] is True and set(bm["questions"][0]["results"]) == {a, b}
    integ = client.get("/v1/projects/bench09/integrations", headers=H).json()
    names = {i["name"] for i in integ["integrations"]}
    assert {"Ragas", "Evidently", "LLM judge", "OpenTelemetry (OTLP)"} <= names
    assert any(c["check"].startswith("PII") for c in integ["security"])


def test_benchmark_compares_on_shared_questions(client):
    client.post("/v1/projects", json={"name": "bench-shared"}, headers=H)
    a = _upload(client, "bench-shared", "two", bad, CASES)
    b = _upload(client, "bench-shared", "one", good, CASES[:1])
    bm = client.get(f"/v1/projects/bench-shared/runs/benchmark?ids={a},{b}", headers=H).json()
    assert bm["shared_questions"] == 1
    assert {r["name"]: (r["cases"], r["own_cases"]) for r in bm["runs"]} == {"two": (1, 2), "one": (1, 1)}
    c = _upload(client, "bench-shared", "other", good, CASES[1:])
    none = client.get(f"/v1/projects/bench-shared/runs/benchmark?ids={b},{c}", headers=H).json()
    assert none["shared_questions"] == 0 and none["best"] is None
