"""Durable background worker.

Jobs live in the `jobs` table, so nothing is lost if the API or worker restarts (FastAPI BackgroundTasks would lose
in-flight work). Several workers can run side by side: claiming is an atomic conditional UPDATE, and failed jobs are
retried with exponential backoff.

Periodic duties: SLO checks + alerting (every RAGOPS_SLO_CHECK_MINUTES) and data retention (hourly).
"""

from __future__ import annotations

import logging
import os
import socket
import time
import traceback
from datetime import timedelta

from sqlalchemy import func, select, update

from ragops import services as svc
from ragops.config import get_settings
from ragops.db import init_db, session_scope
from ragops.models import Job, Project, utcnow

log = logging.getLogger("ragops.worker")
WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"


def _claim(db) -> Job | None:
    now = utcnow()
    # reclaim jobs stuck in 'running' for > 10 minutes (crashed worker)
    db.execute(update(Job).where(Job.status == "running", Job.run_after < now - timedelta(minutes=10))
               .values(status="queued"))
    cand = db.scalar(select(Job.id).where(Job.status == "queued", Job.run_after <= now).order_by(Job.id).limit(1))
    if cand is None:
        return None
    res = db.execute(update(Job).where(Job.id == cand, Job.status == "queued")
                     .values(status="running", locked_by=WORKER_ID, run_after=now, attempts=Job.attempts + 1))
    if res.rowcount != 1:
        return None
    return db.get(Job, cand)


def run_job(db, job: Job) -> None:
    if job.kind == "process_trace":
        svc.process_trace(db, job.payload["trace_id"])
    elif job.kind == "eval_run":
        from ragops.run_service import execute_run

        execute_run(job.payload["run_id"])
    elif job.kind == "check_slos":
        p = db.get(Project, job.payload["project_id"])
        if p:
            svc.check_slos_and_alert(db, p)
    elif job.kind == "retention":
        p = db.get(Project, job.payload["project_id"])
        if p:
            svc.apply_retention(db, p)
    else:
        raise ValueError(f"unknown job kind {job.kind}")


def _execute(job_id: int) -> None:
    """Run one claimed job; on failure schedule a retry with exponential backoff (or mark it failed)."""
    try:
        with session_scope() as db:
            job = db.get(Job, job_id)
            run_job(db, job)
            job.status, job.finished_at, job.error = "done", utcnow(), None
    except Exception as e:
        log.exception("job %s failed", job_id)
        with session_scope() as db:
            job = db.get(Job, job_id)
            job.error = f"{type(e).__name__}: {e}\n{traceback.format_exc()[-1500:]}"
            if job.attempts >= job.max_attempts:
                job.status = "failed"
            else:
                job.status = "queued"
                job.run_after = utcnow() + timedelta(seconds=2 ** job.attempts * 5)


def process_job_id(job_id: int) -> bool:
    """Claim and run a specific job (used by the Arq worker). False if another worker already took it."""
    with session_scope() as db:
        res = db.execute(update(Job).where(Job.id == job_id, Job.status == "queued", Job.run_after <= utcnow())
                         .values(status="running", locked_by=WORKER_ID, run_after=utcnow(),
                                 attempts=Job.attempts + 1))
        if res.rowcount != 1:
            return False
    _execute(job_id)
    return True


def drain(max_jobs: int = 10_000) -> int:
    """Process queued jobs until the queue is empty (or max_jobs). Returns jobs processed."""
    done = 0
    while done < max_jobs:
        with session_scope() as db:
            job = _claim(db)
            if job is None:
                return done
            job_id = job.id
        _execute(job_id)
        done += 1
    return done


def schedule_periodic(last: dict) -> None:
    st = get_settings()
    now = time.time()
    with session_scope() as db:
        projects = db.scalars(select(Project.id)).all()
        if now - last.get("slo", 0) >= st.slo_check_minutes * 60:
            for pid in projects:
                svc.enqueue(db, "check_slos", {"project_id": pid})
            last["slo"] = now
        if now - last.get("retention", 0) >= 3600:
            for pid in projects:
                svc.enqueue(db, "retention", {"project_id": pid})
            last["retention"] = now


def queue_stats() -> dict:
    with session_scope() as db:
        rows = db.execute(select(Job.status, func.count()).group_by(Job.status)).all()
        return {s: int(c) for s, c in rows}


def run_forever() -> None:  # pragma: no cover - long running
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    init_db()
    st = get_settings()
    log.info("worker %s started (judge=%s, embedder=%s)", WORKER_ID, st.judge, st.embedder)
    last: dict = {}
    while True:
        try:
            schedule_periodic(last)
            n = drain(200)
            if n == 0:
                time.sleep(st.worker_poll_seconds)
        except Exception:
            log.exception("worker loop error")
            time.sleep(5)
