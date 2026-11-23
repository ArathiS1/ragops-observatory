"""Optional Redis/Arq dispatch for the job queue.

PostgreSQL stays the source of truth: every job is written to the ``jobs`` table first (a transactional outbox), so
nothing is lost if Redis restarts. With ``RAGOPS_REDIS_URL`` set:

* after each commit, new job ids are pushed to an Arq queue on Redis, so Arq workers start them within milliseconds
  and you can scale workers horizontally on Redis;
* every Arq worker also runs a sweep each ``RAGOPS_SWEEP_SECONDS`` (default 30) that picks up anything Redis lost,
  plus the periodic SLO checks and retention.

    RAGOPS_REDIS_URL=redis://redis:6379/0 ragops worker      # the CLI switches to Arq automatically
"""

from __future__ import annotations

import asyncio
import logging
import os
import queue
import threading

from sqlalchemy import event
from sqlalchemy.orm import Session

log = logging.getLogger("ragops.queue")


def queue_name() -> str:
    """Separate installations sharing one Redis must use different names (RAGOPS_QUEUE_NAME)."""
    return os.environ.get("RAGOPS_QUEUE_NAME", "ragops:jobs")


def redis_url() -> str | None:
    return os.environ.get("RAGOPS_REDIS_URL") or None


# ------------------------------------------------------------------------------------------- producer side
class _Dispatcher:
    """Background thread with its own event loop that forwards committed job ids to Arq."""

    def __init__(self, url: str):
        self.url, self.q = url, queue.Queue(maxsize=100_000)
        self.sent = self.failed = 0
        threading.Thread(target=self._run, name="ragops-arq-dispatch", daemon=True).start()

    def _run(self) -> None:
        asyncio.run(self._loop())

    async def _loop(self) -> None:
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = None
        while True:
            # Wait with a timeout: a blocking get() would park an executor thread forever, and the interpreter joins
            # executor threads on exit, so the API/CLI process could never shut down.
            try:
                job_id = await asyncio.to_thread(self.q.get, True, 0.5)
            except queue.Empty:
                continue
            except RuntimeError:  # interpreter shutting down: executors are closed, stop quietly
                return
            try:
                if pool is None:
                    rs = RedisSettings.from_dsn(self.url)
                    rs.conn_retries, rs.conn_retry_delay = 1, 0  # fail fast: the database sweep is the safety net
                    pool = await create_pool(rs, default_queue_name=queue_name())
                await pool.enqueue_job("run_db_job", job_id, _job_id=f"ragops-{job_id}")
                self.sent += 1
            except Exception as e:  # noqa: BLE001 - the DB sweep will pick the job up
                self.failed += 1
                pool = None
                log.warning("could not dispatch job %s to Redis (%s); the sweep will run it", job_id, e)

    def push(self, ids: list[int]) -> None:
        for i in ids:
            try:
                self.q.put_nowait(i)
            except queue.Full:
                self.failed += 1


_dispatcher: _Dispatcher | None = None


def dispatcher() -> _Dispatcher | None:
    global _dispatcher
    url = redis_url()
    if not url:
        return None
    if _dispatcher is None or _dispatcher.url != url:
        _dispatcher = _Dispatcher(url)
    return _dispatcher


def note_new_job(db: Session, job_id: int) -> None:
    db.info.setdefault("ragops_new_jobs", []).append(job_id)


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    ids = session.info.pop("ragops_new_jobs", None)
    if ids:
        d = dispatcher()
        if d is not None:
            d.push(ids)


@event.listens_for(Session, "after_rollback")
def _after_rollback(session: Session) -> None:
    session.info.pop("ragops_new_jobs", None)


# ------------------------------------------------------------------------------------------- worker side
async def run_db_job(ctx, job_id: int) -> bool:
    from ragops.worker import process_job_id

    return await asyncio.to_thread(process_job_id, job_id)


async def sweep(ctx) -> int:
    """Run anything queued in the database that Redis did not deliver, and schedule periodic duties."""
    from ragops.worker import drain, schedule_periodic

    await asyncio.to_thread(schedule_periodic, ctx.setdefault("periodic", {}))
    return await asyncio.to_thread(drain, 500)


async def startup(ctx) -> None:
    from ragops.db import init_db

    await asyncio.to_thread(init_db)
    log.info("arq worker ready on %s", queue_name())


def worker_settings():
    from arq import cron
    from arq.connections import RedisSettings

    every = max(5, int(os.environ.get("RAGOPS_SWEEP_SECONDS", "30")))

    class WorkerSettings:
        functions = [run_db_job]
        cron_jobs = [cron(sweep, second=set(range(0, 60, every)) if every < 60 else {0}, run_at_startup=True)]
        on_startup = startup
        queue_name = globals()["queue_name"]()
        redis_settings = RedisSettings.from_dsn(redis_url() or "redis://localhost:6379/0")
        max_jobs = int(os.environ.get("RAGOPS_WORKER_CONCURRENCY", "8"))
        keep_result = 60
        job_timeout = 900

    return WorkerSettings


def run_worker() -> None:  # pragma: no cover - long running
    from arq.worker import run_worker as arq_run

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    arq_run(worker_settings())
