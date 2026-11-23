"""The worker must not hold the database write lock while the (slow) LLM judge runs.

On SQLite one writer locks the whole database: if evaluation kept a write transaction open during judge calls
(seconds each, minutes under free-tier rate limits), ingestion in the API waited and failed with HTTP 500.
"""

import sqlite3
import threading
import time

from tests.conftest import ADMIN


class SlowJudge:
    name = "slow-llm"
    cost_per_call = 0.0

    def __init__(self, started: threading.Event):
        from ragops.eval.judges import HeuristicJudge

        self._h, self.started = HeuristicJudge(), started

    def score(self, metric, s):
        self.started.set()
        time.sleep(0.4)  # stands in for an LLM call
        return self._h.score(metric, s)


def test_ingest_succeeds_while_worker_is_judging(client, project, tmp_path, monkeypatch):
    name, h = project
    monkeypatch.setenv("RAGOPS_INLINE_JOBS", "false")
    from ragops.config import reset_settings

    reset_settings()
    body = {"id": "slow1", "query": "How do I reset my password?", "answer": "Open Settings.",
            "contexts": [{"text": "To reset your password open Settings.", "doc_id": "d1"}]}
    assert client.post(f"/v1/projects/{name}/traces", json=body, headers=h).status_code in (200, 201, 202)

    from ragops import services
    from ragops.db import session_scope
    from ragops.models import Trace

    with session_scope() as db:
        t = db.get(Trace, "slow1")
        t.meta = {**t.meta, "force_eval": True}

    started, errors = threading.Event(), []

    def work():
        try:
            with session_scope() as db:
                services.process_trace(db, "slow1", judge=SlowJudge(started))
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    th = threading.Thread(target=work)
    th.start()
    assert started.wait(10)
    # a second writer with a short lock timeout, like the API inserting a new trace
    con = sqlite3.connect(str(tmp_path / "t.db"), timeout=0.3)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS lock_probe (x INTEGER)")
        con.execute("INSERT INTO lock_probe VALUES (1)")
        con.commit()
    finally:
        con.close()
    th.join(60)
    assert not errors, errors
    r = client.get(f"/v1/projects/{name}/traces/slow1", headers=ADMIN).json()
    assert r["eval_status"] == "done"


def test_answer_relevance_slo_does_not_punish_correct_refusals(client, project):
    """'I don't know' on an uncovered question is right: it scores 0 relevance, but must not breach the SLO."""
    name, h = project
    client.patch(f"/v1/projects/{name}/settings", headers=h, json={"sample_rate": 1.0})
    for i in range(25):
        if i % 3 == 0:  # correct refusals: nothing in the corpus covers these
            body = {"query": f"Do you sell pizza number {i}?", "answer": "I don't know: the documents do not cover that.",
                    "contexts": []}
        else:
            body = {"query": "How do I reset my password?", "answer": "Open Settings, then Security, and click Reset password.",
                    "contexts": [{"text": "To reset your password open Settings, then Security, and click Reset password.",
                                  "doc_id": "security"}]}
        client.post(f"/v1/projects/{name}/traces", json={"id": f"r{i}", **body}, headers=h)
    client.post(f"/v1/projects/{name}/slos", headers=h, json={"name": "rel", "metric": "answer_relevance",
                                                              "threshold": 0.7, "target": 0.9, "window_days": 7})
    st = next(s for s in client.get(f"/v1/projects/{name}/slos", headers=h).json() if s["name"] == "rel")
    assert st["n"] < 25  # the refusals were left out
    assert st["status"] != "breached", st
    # ...they are not failures, but the missing content is still on the knowledge-base to-do list
    ov = client.get(f"/v1/projects/{name}/overview", headers=h).json()
    assert ov["failures"] == 0
    kb = client.get(f"/v1/projects/{name}/kb-health", headers=h).json()
    assert "pizza" in str(kb).lower()
