"""Optional ClickHouse analytics backend for high trace volume.

PostgreSQL stays the system of record (traces, corpus, golden sets, settings). When ``RAGOPS_CLICKHOUSE_URL`` is set,
every evaluated trace is also mirrored, one wide row per trace, into a ClickHouse ``ReplacingMergeTree`` table, and
the high-volume analytics endpoint (``/v1/projects/{p}/analytics``) is answered from ClickHouse instead of Postgres.

Writes use ClickHouse's server-side ``async_insert`` so the worker can insert row by row without creating thousands
of tiny parts. Everything goes over the HTTP interface, so no extra driver is needed.

    RAGOPS_CLICKHOUSE_URL=http://default:password@clickhouse:8123/ragops
    ragops clickhouse init         # create database + table
    ragops clickhouse backfill     # copy existing history
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from urllib.parse import urlparse

import httpx

log = logging.getLogger("ragops.clickhouse")

TABLE = "ragops_traces"

DDL = f"""
CREATE TABLE IF NOT EXISTS {{db}}.{TABLE}
(
    project_id     LowCardinality(String),
    trace_id       String,
    ts             DateTime64(3),
    config_label   LowCardinality(String),
    source         LowCardinality(String),
    model          LowCardinality(String),
    latency_ms     Nullable(Float64),
    cost_usd       Nullable(Float64),
    user_feedback  Nullable(Float32),
    eval_status    LowCardinality(String),
    is_failure     Nullable(UInt8),
    root_cause     LowCardinality(String),
    confidence     Nullable(Float32),
    metrics        Map(String, Float64),
    n_context      UInt16,
    top_score      Nullable(Float32),
    updated_at     DateTime64(3)
)
ENGINE = ReplacingMergeTree(updated_at)
PARTITION BY toYYYYMM(ts)
ORDER BY (project_id, ts, trace_id)
"""


class ClickHouse:
    def __init__(self, url: str, transport: httpx.BaseTransport | None = None):
        u = urlparse(url)
        self.database = (u.path or "/ragops").strip("/") or "ragops"
        base = f"{u.scheme or 'http'}://{u.hostname}:{u.port or 8123}"
        auth = (u.username or "default", u.password or "")
        self.http = httpx.Client(base_url=base, auth=auth, timeout=30, transport=transport)
        # server-side batching of row-by-row inserts; set RAGOPS_CLICKHOUSE_ASYNC_INSERT=false to disable
        import os

        self.async_inserts = os.environ.get("RAGOPS_CLICKHOUSE_ASYNC_INSERT", "true").lower() != "false"

    # -------------------------------------------------------------------------------- low level
    def execute(self, sql: str, data: str | None = None, settings: dict | None = None) -> str:
        params = {"database": self.database, **(settings or {})}
        body = sql if data is None else f"{sql}\n{data}"
        r = self.http.post("/", params=params, content=body.encode())
        if r.status_code >= 400:
            raise RuntimeError(f"ClickHouse error {r.status_code}: {r.text[:300]}")
        return r.text

    def query(self, sql: str) -> list[dict]:
        out = self.execute(sql.rstrip().rstrip(";") + " FORMAT JSON")
        return json.loads(out)["data"] if out.strip() else []

    def init(self) -> None:
        r = self.http.post("/", content=f"CREATE DATABASE IF NOT EXISTS {self.database}".encode())
        if r.status_code >= 400:
            raise RuntimeError(r.text[:300])
        self.execute(DDL.format(db=self.database))

    def insert_rows(self, rows: list[dict], async_insert: bool | None = None) -> None:
        if not rows:
            return
        async_insert = self.async_inserts if async_insert is None else async_insert
        data = "\n".join(json.dumps(r, default=str) for r in rows)
        settings = {"async_insert": 1, "wait_for_async_insert": 0} if async_insert else {}
        self.execute(f"INSERT INTO {self.database}.{TABLE} FORMAT JSONEachRow", data, settings)

    # -------------------------------------------------------------------------------- analytics
    def analytics(self, project_id: str, days: int = 30) -> dict:
        t = f"{self.database}.{TABLE} FINAL"
        where = f"project_id = '{_q(project_id)}' AND ts >= now64(3) - INTERVAL {int(days)} DAY"
        totals = self.query(f"""
            SELECT count() AS traces, countIf(eval_status = 'done') AS evaluated,
                   countIf(is_failure = 1) AS failures,
                   quantile(0.5)(latency_ms) AS latency_p50_ms, quantile(0.95)(latency_ms) AS latency_p95_ms,
                   sum(cost_usd) AS llm_cost_usd
            FROM {t} WHERE {where}""")[0]
        daily = self.query(f"""
            SELECT toDate(ts) AS day, count() AS traces, countIf(eval_status = 'done') AS evaluated,
                   countIf(is_failure = 1) AS failures,
                   if(evaluated = 0, NULL, failures / evaluated) AS failure_rate,
                   quantile(0.95)(latency_ms) AS latency_p95_ms
            FROM {t} WHERE {where} GROUP BY day ORDER BY day""")
        causes = self.query(f"""
            SELECT root_cause AS label, count() AS count FROM {t}
            WHERE {where} AND is_failure = 1 AND root_cause NOT IN ('', 'ok')
            GROUP BY label ORDER BY count DESC""")
        by_config = self.query(f"""
            SELECT config_label, count() AS traces, countIf(is_failure = 1) / greatest(countIf(eval_status = 'done'), 1)
                   AS failure_rate, avg(metrics['faithfulness']) AS faithfulness,
                   avg(metrics['answer_relevance']) AS answer_relevance, quantile(0.95)(latency_ms) AS latency_p95_ms
            FROM {t} WHERE {where} GROUP BY config_label ORDER BY traces DESC""")
        return {"backend": "clickhouse", "window_days": days, "totals": _clean(totals),
                "daily": [_clean(r) for r in daily], "root_causes": {r["label"]: int(r["count"]) for r in causes},
                "by_config": [_clean(r) for r in by_config]}


def _q(s: str) -> str:
    return s.replace("\\", "\\\\").replace("'", "\\'")


def _clean(r: dict) -> dict:
    out = {}
    for k, v in r.items():
        if isinstance(v, str):
            try:
                v = float(v) if any(c in v for c in ".e") else int(v)
            except ValueError:
                pass
        out[k] = v
    return out


_client: ClickHouse | None = None


def get_clickhouse() -> ClickHouse | None:
    global _client
    from ragops.config import get_settings

    url = get_settings().clickhouse_url
    if not url:
        return None
    if _client is None:
        _client = ClickHouse(url)
    return _client


def reset_client(client: ClickHouse | None = None) -> None:
    global _client
    _client = client


def trace_row(t, config_label: str = "") -> dict:
    """One wide analytics row for a Trace (with chunks, evaluations, diagnosis loaded)."""
    ctx = [c for c in t.chunks if c.step == "context"]
    scores = [c.score for c in ctx if c.score is not None]
    dx = t.diagnosis
    return {
        "project_id": t.project_id, "trace_id": t.id, "ts": t.created_at.isoformat(sep=" ", timespec="milliseconds"),
        "config_label": config_label or "", "source": t.source or "", "model": t.model or "",
        "latency_ms": t.latency_ms, "cost_usd": t.cost_usd, "user_feedback": t.user_feedback,
        "eval_status": t.eval_status, "is_failure": None if t.is_failure is None else int(t.is_failure),
        "root_cause": dx.label if dx else "", "confidence": dx.confidence if dx else None,
        "metrics": {e.metric: float(e.score) for e in t.evaluations if e.score is not None},
        "n_context": len(ctx), "top_score": max(scores) if scores else None,
        "updated_at": datetime.utcnow().isoformat(sep=" ", timespec="milliseconds"),
    }


def mirror(t, config_label: str = "") -> None:
    """Best-effort mirror of one processed trace. Never raises: Postgres remains the source of truth."""
    ch = get_clickhouse()
    if ch is None:
        return
    try:
        ch.insert_rows([trace_row(t, config_label)])
    except Exception as e:  # noqa: BLE001
        log.warning("clickhouse mirror failed for %s: %s", t.id, e)


def backfill(db, project_id: str | None = None, days: int = 400, batch: int = 2000) -> int:
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from ragops.models import ConfigVersion, Trace, utcnow

    ch = get_clickhouse()
    if ch is None:
        raise RuntimeError("RAGOPS_CLICKHOUSE_URL is not set")
    ch.init()
    stmt = (select(Trace).where(Trace.created_at >= utcnow() - timedelta(days=days))
            .options(selectinload(Trace.chunks), selectinload(Trace.evaluations), selectinload(Trace.diagnosis))
            .order_by(Trace.created_at))
    if project_id:
        stmt = stmt.where(Trace.project_id == project_id)
    labels = dict(db.execute(select(ConfigVersion.id, ConfigVersion.label)).all())
    n, rows = 0, []
    for t in db.scalars(stmt).yield_per(500):
        rows.append(trace_row(t, labels.get(t.config_version_id, "")))
        if len(rows) >= batch:
            ch.insert_rows(rows, async_insert=False)
            n += len(rows)
            rows = []
    ch.insert_rows(rows, async_insert=False)
    return n + len(rows)
