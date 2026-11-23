from sqlalchemy import select

from ragops import services as svc
from ragops.db import session_scope
from ragops.models import Job


def test_worker_retries_then_fails(env):
    from ragops.worker import drain

    with session_scope() as db:
        svc.enqueue(db, "does_not_exist", {})
    drain()
    with session_scope() as db:
        j = db.scalars(select(Job)).one()
        assert j.status == "queued" and j.attempts == 1 and "unknown job kind" in j.error
        j.max_attempts = 1
    with session_scope() as db:  # make it runnable now
        j = db.scalars(select(Job)).one()
        j.run_after = j.created_at
    drain()
    with session_scope() as db:
        assert db.scalars(select(Job)).one().status == "failed"


def test_demo_seed_end_to_end(env):
    from ragops.demo.seed import seed_demo

    r = seed_demo("demo-test", days=12, per_day=25, verbose=False)
    assert r["traces"] > 250 and r["admin_key"].startswith("rgo_")
    # the storyline's incidents are all visible
    for label in ("knowledge_gap", "stale_knowledge", "ranking_failure"):
        assert r["root_causes"].get(label, 0) > 0, label
    with session_scope() as db:
        p = svc.get_project_by_name(db, "demo-test")
        q = svc.quality_drift(db, p, 30)
        assert any(x["detected"] and x.get("likely_causes") for x in q)
        assert svc.export_golden(db, svc.get_or_create_golden(db, p.id, "production-failures"))["cases"]
