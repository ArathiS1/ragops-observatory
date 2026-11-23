"""Arq/Redis dispatch against a real Redis server and a separate worker process.

Set RAGOPS_TEST_REDIS_URL (e.g. redis://localhost:6390/0) and RAGOPS_TEST_PG_URL to run.
"""

import os
import subprocess
import sys
import time

import pytest

REDIS = os.environ.get("RAGOPS_TEST_REDIS_URL")
PG = os.environ.get("RAGOPS_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not (REDIS and PG), reason="set RAGOPS_TEST_REDIS_URL and RAGOPS_TEST_PG_URL")


@pytest.fixture()
def stack(monkeypatch):
    from sqlalchemy import create_engine, text

    from ragops import queue_arq
    from ragops.config import reset_settings
    from ragops.db import reset_engine

    e = create_engine(PG)
    with e.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public"))
    e.dispose()
    import redis as r

    r.Redis.from_url(REDIS).flushdb()
    qname = f"ragops-test:{os.getpid()}:{time.time_ns()}"  # isolated from any other worker on this Redis
    env = {**os.environ, "RAGOPS_DATABASE_URL": PG, "RAGOPS_REDIS_URL": REDIS, "RAGOPS_SWEEP_SECONDS": "10",
           "RAGOPS_QUEUE_NAME": qname}
    monkeypatch.setenv("RAGOPS_QUEUE_NAME", qname)
    monkeypatch.setenv("RAGOPS_DATABASE_URL", PG)
    monkeypatch.setenv("RAGOPS_REDIS_URL", REDIS)
    monkeypatch.delenv("RAGOPS_INLINE_JOBS", raising=False)
    reset_settings()
    reset_engine()
    queue_arq._dispatcher = None
    from ragops.db import init_db

    init_db()
    log = open(os.path.join(os.environ.get("TMPDIR", "/tmp"), "ragops-arq-worker.log"), "w")
    w = subprocess.Popen([sys.executable, "-m", "ragops.cli", "worker", "--backend", "arq"], env=env,
                         stdout=log, stderr=subprocess.STDOUT)
    time.sleep(4)
    yield w
    w.terminate()
    try:
        w.wait(10)
    except subprocess.TimeoutExpired:
        w.kill()
    log.close()
    queue_arq._dispatcher = None
    reset_engine()
    reset_settings()


def _wait_done(client, name, h, tid, secs):
    t0 = time.time()
    while time.time() - t0 < secs:
        r = client.get(f"/v1/projects/{name}/traces/{tid}", headers=h)
        if r.status_code == 200 and r.json()["eval_status"] == "done":
            return time.time() - t0
        time.sleep(0.2)
    return None


def test_redis_dispatch_and_outage_fallback(stack, monkeypatch):
    import redis as r
    from fastapi.testclient import TestClient

    from ragops import queue_arq
    from ragops.server.app import app
    from tests.conftest import ADMIN, demo_trace

    with TestClient(app) as client:
        key = client.post("/v1/projects", json={"name": "rq", "settings": {"sample_rate": 1.0}}, headers=ADMIN).json()
        h = {"Authorization": f"Bearer {key['admin_key']}"}
        client.post("/v1/projects/rq/traces", json=demo_trace("How long are audit logs retained?", id="via-redis"),
                    headers=h)
        took = _wait_done(client, "rq", h, "via-redis", 9)
        assert took is not None and took < 9, "not processed through Redis"
        rr = r.Redis.from_url(REDIS)
        assert any(k.startswith(b"arq:result:ragops-") for k in rr.keys("arq:result:*")), "job did not go through Arq"
        assert queue_arq.dispatcher().sent >= 1

        # Redis outage: dispatch fails, the job stays in the Postgres outbox and the worker's sweep runs it
        monkeypatch.setenv("RAGOPS_REDIS_URL", "redis://127.0.0.1:1/0")
        queue_arq._dispatcher = None
        client.post("/v1/projects/rq/traces", json=demo_trace("How do I reset my password?", id="via-sweep"), headers=h)
        took = _wait_done(client, "rq", h, "via-sweep", 30)
        assert took is not None, "sweep did not pick up the job"
        for _ in range(50):
            if queue_arq.dispatcher().failed >= 1:
                break
            time.sleep(0.2)
        assert queue_arq.dispatcher().failed >= 1
        from sqlalchemy import select

        from ragops.db import session_scope
        from ragops.models import Job

        with session_scope() as db:  # the outage job, found by its payload (periodic jobs also get ids)
            jid = next(j.id for j in db.scalars(select(Job).where(Job.kind == "process_trace"))
                       if j.payload.get("trace_id") == "via-sweep")
        assert rr.exists(f"arq:result:ragops-{jid}") == 0  # it never went through Redis
