from ragops.demo.corpus import GAP_QUESTIONS
from tests.conftest import ADMIN, demo_trace


def test_health_and_version(client):
    assert client.get("/health").json()["status"] == "ok"
    assert client.get("/v1/version").json()["judge"] == "heuristic"


def test_auth_required(client):
    assert client.get("/v1/projects").status_code == 401
    assert client.get("/v1/projects", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_rbac(client, project):
    name, h = project
    ingest = client.post(f"/v1/projects/{name}/keys", json={"name": "svc", "role": "ingest"}, headers=h).json()["key"]
    viewer = client.post(f"/v1/projects/{name}/keys", json={"name": "v", "role": "viewer"}, headers=h).json()["key"]
    hi, hv = {"Authorization": f"Bearer {ingest}"}, {"Authorization": f"Bearer {viewer}"}
    # ingest keys can write traces but not read data back
    assert client.post(f"/v1/projects/{name}/traces", json=demo_trace("How much does the Pro plan cost per user?"),
                       headers=hi).status_code == 202
    assert client.get(f"/v1/projects/{name}/traces", headers=hi).status_code == 403
    # viewers can read but not change settings
    assert client.get(f"/v1/projects/{name}/traces", headers=hv).status_code == 200
    assert client.patch(f"/v1/projects/{name}/settings", json={"sample_rate": 0.1}, headers=hv).status_code == 403
    # project keys cannot touch other projects or create projects
    client.post("/v1/projects", json={"name": "other"}, headers=ADMIN)
    assert client.get("/v1/projects/other/traces", headers=h).status_code == 403
    assert client.post("/v1/projects", json={"name": "x"}, headers=h).status_code == 403
    # revoked keys stop working
    kid = next(k["id"] for k in client.get(f"/v1/projects/{name}/keys", headers=h).json() if k["name"] == "v")
    client.delete(f"/v1/projects/{name}/keys/{kid}", headers=h)
    assert client.get(f"/v1/projects/{name}/traces", headers=hv).status_code == 401
    actions = [a["action"] for a in client.get(f"/v1/projects/{name}/audit", headers=h).json()]
    assert "key.revoke" in actions and "key.create" in actions


def test_ingest_evaluate_diagnose(client, project):
    name, h = project
    good = demo_trace("How much does the Pro plan cost per user?", id="good-1")
    gap = demo_trace(GAP_QUESTIONS[0], id="gap-1")
    gap["answer"] = "Yes, offline mode works on every plan."
    r = client.post(f"/v1/projects/{name}/traces/batch", json={"traces": [good, gap]}, headers=h)
    assert r.json()["count"] == 2
    t = client.get(f"/v1/projects/{name}/traces/good-1", headers=h).json()
    assert t["eval_status"] == "done" and t["diagnosis"]["label"] == "ok"
    assert {e["metric"] for e in t["evaluations"]} >= {"faithfulness", "answer_relevance", "context_relevance"}
    t = client.get(f"/v1/projects/{name}/traces/gap-1", headers=h).json()
    assert t["diagnosis"]["label"] == "knowledge_gap"
    ov = client.get(f"/v1/projects/{name}/overview", headers=h).json()
    assert ov["traces"] == 2 and ov["failures"] == 1 and ov["root_causes"] == {"knowledge_gap": 1}


def test_idempotent_ingest(client, project):
    name, h = project
    body = demo_trace("How long are audit logs retained?", id="dup")
    client.post(f"/v1/projects/{name}/traces", json=body, headers=h)
    client.post(f"/v1/projects/{name}/traces", json=body, headers=h)
    assert len(client.get(f"/v1/projects/{name}/traces", headers=h).json()) == 1


def test_sampling_and_negative_feedback(client, project):
    name, h = project
    client.patch(f"/v1/projects/{name}/settings", json={"sample_rate": 0.0}, headers=h)
    client.post(f"/v1/projects/{name}/traces", json=demo_trace("How long are audit logs retained?", id="s1"), headers=h)
    assert client.get(f"/v1/projects/{name}/traces/s1", headers=h).json()["eval_status"] == "sampled_out"
    client.post(f"/v1/projects/{name}/traces/s1/feedback", json={"score": -1, "comment": "wrong"}, headers=h)
    t = client.get(f"/v1/projects/{name}/traces/s1", headers=h).json()
    assert t["eval_status"] == "done" and t["is_failure"] is True


def test_pii_redaction_and_hashing(client, project):
    name, h = project
    body = demo_trace("How do I reset my password?", id="pii")
    body["query"] = "I'm jane.doe@acme.com, call +1 415 555 0134. How do I reset my password?"
    client.post(f"/v1/projects/{name}/traces", json=body, headers=h)
    q = client.get(f"/v1/projects/{name}/traces/pii", headers=h).json()["query"]
    assert "jane.doe@acme.com" not in q and "555" not in q and "<EMAIL>" in q
    client.patch(f"/v1/projects/{name}/settings", json={"store_raw_text": False}, headers=h)
    client.post(f"/v1/projects/{name}/traces", json=demo_trace("How do I reset my password?", id="hashed"), headers=h)
    q = client.get(f"/v1/projects/{name}/traces/hashed", headers=h).json()["query"]
    assert "password" not in q


def test_corpus_versioning_and_stale_detection(client, project):
    from ragops.demo.corpus import PRICING_V2

    name, h = project
    r = client.post(f"/v1/projects/{name}/corpus", json={"documents": [
        {"doc_id": "pricing-plans", "text": PRICING_V2}], "chunk_words": 45}, headers=h)
    assert r.json()["updated"] == 1
    # the serving index still returns version 1 of the pricing page
    from ragops.demo.corpus import DOCS

    old = next(d["text"] for d in DOCS if d["doc_id"] == "pricing-plans")
    body = demo_trace("How much does the Pro plan cost per user?",
                      {"serve_docs": {"pricing-plans": {"text": old, "version": 1}}}, id="stale")
    client.post(f"/v1/projects/{name}/traces", json=body, headers=h)
    t = client.get(f"/v1/projects/{name}/traces/stale", headers=h).json()
    assert t["diagnosis"]["label"] == "stale_knowledge"
    assert any(c["stale"] for c in t["chunks"])
    assert client.get(f"/v1/projects/{name}/corpus/stats", headers=h).json()["document_versions"] == 19


def test_golden_promotion_dedup_and_auto(client, project):
    name, h = project
    body = demo_trace("How much does the Pro plan cost per user?", {"exclude_docs": ["pricing-plans"]}, id="miss")
    client.post(f"/v1/projects/{name}/traces", json={**body, "user_feedback": -1}, headers=h)
    r = client.post(f"/v1/projects/{name}/golden/promote", json={"trace_id": "miss"}, headers=h).json()
    assert r["added"] and r["expected_doc_ids"] == ["pricing-plans"]
    r = client.post(f"/v1/projects/{name}/golden/promote", json={"trace_id": "miss"}, headers=h).json()
    assert r["added"] is False
    g = client.get(f"/v1/projects/{name}/golden-sets/production-failures/export", headers=h).json()
    assert g["version"] == 1 and g["cases"][0]["failure_label"] == "retrieval_miss"
    # auto-promotion rule
    client.patch(f"/v1/projects/{name}/settings", json={"auto_golden": {"enabled": True, "golden_set": "auto"}}, headers=h)
    body = demo_trace("What is the API rate limit on the Pro plan?", {"exclude_docs": ["api-rate-limits"]}, id="miss2")
    client.post(f"/v1/projects/{name}/traces", json={**body, "user_feedback": -1}, headers=h)
    assert client.get(f"/v1/projects/{name}/golden-sets/auto/export", headers=h).json()["cases"]


def test_slo_alerting(client, project, monkeypatch):
    name, h = project
    sent = []
    monkeypatch.setattr("ragops.services.deliver", lambda *a, **k: sent.append(a) or True)
    client.post(f"/v1/projects/{name}/alert-channels", json={"kind": "webhook", "target": "https://hooks.example/x"}, headers=h)
    client.post(f"/v1/projects/{name}/slos", json={"name": "relevance", "metric": "answer_relevance", "threshold": 0.5,
                                                   "target": 0.99}, headers=h)
    # off-topic answers to covered questions (a correct "I don't know" must NOT breach a relevance SLO)
    from ragops.demo.corpus import DOCS

    qs = [q for d in DOCS for q in d["questions"]][:22]  # SLOs need >= 20 samples
    traces = [{**demo_trace(q, id=f"g{i}"), "answer": "Thanks for reaching out! Our team loves hearing from you."}
              for i, q in enumerate(qs)]
    client.post(f"/v1/projects/{name}/traces/batch", json={"traces": traces}, headers=h)
    r = client.post(f"/v1/projects/{name}/slos/check", headers=h).json()
    assert r["slos"][0]["status"] == "breached" and len(r["alerts"]) == 1
    assert sent and "Root causes" in sent[0][3]
    # same status again does not re-alert
    assert client.post(f"/v1/projects/{name}/slos/check", headers=h).json()["alerts"] == []


def test_analytics_endpoints(client, project):
    name, h = project
    from ragops.demo.corpus import DOCS

    traces = [demo_trace(q) for d in DOCS for q in d["questions"][:2]]
    client.post(f"/v1/projects/{name}/traces/batch", json={"traces": traces}, headers=h)
    for path in ["overview", "root-causes", "timeline", "drift", "drift/query", "drift/retrieval", "drift/quality",
                 "drift/corpus", "kb-health", "calibration", "slos", "alerts", "golden-sets"]:
        assert client.get(f"/v1/projects/{name}/{path}", headers=h).status_code == 200, path
    assert client.get(f"/v1/projects/{name}/drift/nope", headers=h).status_code == 404
    md = client.get(f"/v1/projects/{name}/report", headers=h).text
    assert md.startswith("# RAG reliability report")
    assert "<html" in client.get(f"/v1/projects/{name}/report?format=html", headers=h).text


def test_human_labels_calibration(client, project):
    name, h = project
    from ragops.demo.corpus import DOCS

    traces = [demo_trace(q, id=f"c{i}") for i, q in enumerate(q for d in DOCS for q in d["questions"][:1])]
    client.post(f"/v1/projects/{name}/traces/batch", json={"traces": traces}, headers=h)
    for i in range(len(traces)):
        client.post(f"/v1/projects/{name}/labels", json={"trace_id": f"c{i}", "metric": "faithfulness", "passed": True},
                    headers=h)
    cal = client.get(f"/v1/projects/{name}/calibration", headers=h).json()
    assert cal[0]["metric"] == "faithfulness" and cal[0]["n"] == len(traces)


def test_retention(client, project):
    name, h = project
    client.post(f"/v1/projects/{name}/traces", json={**demo_trace("How long are audit logs retained?"),
                                                    "timestamp": "2020-01-01T00:00:00Z"}, headers=h)
    from ragops import services as svc
    from ragops.db import session_scope

    with session_scope() as db:
        assert svc.apply_retention(db, svc.get_project_by_name(db, name)) == 1


def test_rechunking_is_not_a_new_document_version(client, project):
    from ragops.demo.corpus import DOCS

    name, h = project
    docs = [{"doc_id": d["doc_id"], "text": d["text"]} for d in DOCS]
    r = client.post(f"/v1/projects/{name}/corpus", json={"documents": docs, "chunk_words": 20}, headers=h).json()
    assert r["rechunked"] == len(DOCS) and r["updated"] == 0
    st = client.get(f"/v1/projects/{name}/corpus/stats", headers=h).json()
    assert st["document_versions"] == len(DOCS) and st["chunks"] > 60
    r = client.post(f"/v1/projects/{name}/corpus", json={"documents": docs, "chunk_words": 20}, headers=h).json()
    assert r["unchanged"] == len(DOCS)


def test_trace_field_aliases(client, project):
    name, h = project
    body = {"id": "al1", "question": "What is the refund window?", "response": "30 days.",
            "chunks": ["Refunds are accepted within 30 days of purchase.", {"page_content": "Shipping takes 5 days."}]}
    assert client.post(f"/v1/projects/{name}/traces", json=body, headers=h).status_code in (200, 201, 202)
    t = client.get(f"/v1/projects/{name}/traces/al1", headers=h).json()
    assert t["query"] == "What is the refund window?" and t["answer"] == "30 days."
    assert [c["text"] for c in t["chunks"]][:2] == ["Refunds are accepted within 30 days of purchase.", "Shipping takes 5 days."]
