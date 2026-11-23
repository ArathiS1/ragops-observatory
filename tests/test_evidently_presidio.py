"""Optional integrations executed for real: Evidently 0.7 reports and Presidio PII redaction."""

import pytest

from tests.conftest import demo_trace


def test_evidently_report_endpoint(client, project):
    pytest.importorskip("evidently")
    from datetime import datetime, timedelta, timezone

    name, h = project
    now = datetime.now(timezone.utc)
    qs = ["How much does the Pro plan cost per user?", "How long are audit logs retained?",
          "How do I reset my password?", "What is the API rate limit on the Pro plan?"]
    traces = []
    for i in range(40):  # reference window: healthy
        traces.append({**demo_trace(qs[i % 4], {}), "id": f"r{i}", "latency_ms": 800 + i,
                       "timestamp": (now - timedelta(days=5, hours=i % 20)).isoformat()})
    for i in range(30):  # current window: broken reranker
        traces.append({**demo_trace(qs[i % 4], {"rerank": "bad", "context_k": 1}), "id": f"c{i}", "latency_ms": 1400 + i,
                       "timestamp": (now - timedelta(hours=10 + i % 20)).isoformat()})
    client.post(f"/v1/projects/{name}/traces/batch", json={"traces": traces}, headers=h)
    r = client.get(f"/v1/projects/{name}/drift/evidently", params={"current_days": 2, "reference_days": 5}, headers=h)
    assert r.status_code == 200, r.text[:300]
    html = r.text
    assert "<html" in html.lower() and "latency_ms" in html and "query_embedding" in html


def test_evidently_needs_data(client, project):
    pytest.importorskip("evidently")
    name, h = project
    assert client.get(f"/v1/projects/{name}/drift/evidently", headers=h).status_code == 422


def test_presidio_redaction_for_real(client, project, monkeypatch):
    pytest.importorskip("presidio_analyzer")
    spacy = pytest.importorskip("spacy")
    if not spacy.util.is_package("en_core_web_sm") and not spacy.util.is_package("en_core_web_lg"):
        pytest.skip("no spaCy English model installed")
    monkeypatch.setenv("RAGOPS_REDACTOR", "presidio")
    from ragops.redaction import redact

    text, counts = redact("I am Priya Sharma from Berlin. Mail priya.s@acme.com or call +1 415 555 0134.",
                          ["EMAIL", "PHONE", "PERSON", "LOCATION"])
    assert text == "I am <PERSON> from <LOCATION>. Mail <EMAIL> or call <PHONE>."
    assert counts == {"PERSON": 1, "LOCATION": 1, "EMAIL": 1, "PHONE": 1}
    # through ingestion: the project's redaction settings apply Presidio to stored queries
    name, h = project
    client.patch(f"/v1/projects/{name}/settings", headers=h,
                 json={"redact_fields": ["query", "answer"], "redaction_entities": ["EMAIL", "PERSON"]})
    body = demo_trace("How do I reset my password?", id="pp")
    body["query"] = "I'm Jane Doe (jane@acme.com). How do I reset my password?"
    client.post(f"/v1/projects/{name}/traces", json=body, headers=h)
    q = client.get(f"/v1/projects/{name}/traces/pp", headers=h).json()["query"]
    assert "Jane Doe" not in q and "jane@acme.com" not in q and "<PERSON>" in q and "<EMAIL>" in q
