"""Backfill history from Langfuse and Arize Phoenix exports so a team sees value on day one.

Langfuse: the JSON produced by the Langfuse export / public API (`GET /api/public/traces` with observations).
Phoenix:  spans exported with `px.Client().get_spans_dataframe().to_json(orient="records")` (or JSONL),
          which use OpenInference attributes; they go through the same assembler as live OTLP data.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ragops.schemas import ChunkIn, TraceIn
from ragops.server.otel import Span, _text, assemble


def _parse_ts(v: Any) -> datetime | None:
    if not v:
        return None
    if isinstance(v, (int, float)):
        return datetime.utcfromtimestamp(v / 1000 if v > 1e11 else v)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None


def load_records(raw: bytes | str) -> list[dict]:
    text = raw.decode() if isinstance(raw, bytes) else raw
    text = text.strip()
    if text.startswith("["):
        return json.loads(text)
    if text.startswith("{"):
        obj = json.loads(text)
        if isinstance(obj, dict) and "data" in obj and isinstance(obj["data"], list):
            return obj["data"]
        return [obj] if not text.count("\n{") else [json.loads(line) for line in text.splitlines() if line.strip()]
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ------------------------------------------------------------------------------------------- langfuse
def _lf_docs(output: Any) -> list[ChunkIn]:
    if isinstance(output, dict):
        output = output.get("documents") or output.get("docs") or output.get("output") or []
    if not isinstance(output, list):
        return []
    out = []
    for i, d in enumerate(output):
        if isinstance(d, str):
            out.append(ChunkIn(text=d, rank=i + 1))
            continue
        meta = d.get("metadata") or {}
        cid = d.get("id") or meta.get("chunk_id")
        out.append(ChunkIn(text=d.get("page_content") or d.get("content") or d.get("text"),
                           chunk_id=None if cid is None else str(cid),
                           doc_id=meta.get("doc_id") or meta.get("source") or (str(cid).split("#")[0] if cid else None),
                           doc_version=meta.get("doc_version"), score=d.get("score") or meta.get("score"), rank=i + 1))
    return out


def from_langfuse(records: list[dict]) -> list[TraceIn]:
    out = []
    for t in records:
        obs = sorted(t.get("observations") or [], key=lambda o: str(o.get("startTime") or ""))
        retrieved, reranked, model, pt, ct = [], [], None, None, None
        for o in obs:
            name = (o.get("name") or "").lower()
            if o.get("type") == "GENERATION":
                model = o.get("model") or model
                usage = o.get("usage") or {}
                pt = (pt or 0) + int(usage.get("input") or usage.get("promptTokens") or 0)
                ct = (ct or 0) + int(usage.get("output") or usage.get("completionTokens") or 0)
            elif "rerank" in name:
                reranked = _lf_docs(o.get("output"))
            elif "retriev" in name or "search" in name or o.get("type") == "RETRIEVER":
                retrieved = _lf_docs(o.get("output"))
        query = _text(t.get("input")) or ""
        if not query or not (retrieved or model):
            continue
        fb = None
        for s in t.get("scores") or []:
            if "feedback" in (s.get("name") or "").lower() and s.get("value") is not None:
                v = float(s["value"])
                fb = max(-1.0, min(1.0, v if -1 <= v <= 1 else (v - 3) / 2))
        out.append(TraceIn(id=f"lf-{t.get('id')}"[:64], query=query, answer=_text(t.get("output")), retrieved=retrieved,
                           reranked=reranked, contexts=reranked or retrieved, model=model, prompt_tokens=pt,
                           completion_tokens=ct, latency_ms=(t.get("latency") or 0) * 1000 or None,
                           session_id=t.get("sessionId"), user_feedback=fb, timestamp=_parse_ts(t.get("timestamp")),
                           cost_usd=t.get("totalCost"), metadata={"_source": "import:langfuse", "tags": t.get("tags") or []}))
    return out


# ------------------------------------------------------------------------------------------- phoenix
def from_phoenix(records: list[dict]) -> list[TraceIn]:
    spans = []
    for r in records:
        attrs: dict[str, Any] = {}
        nested = r.get("attributes")
        if isinstance(nested, dict):
            attrs.update(_flatten(nested))
        for k, v in r.items():
            if k.startswith("attributes."):
                attrs[k[len("attributes."):]] = v
        if r.get("span_kind"):
            attrs.setdefault("openinference.span.kind", r["span_kind"])
        ctx = r.get("context") or {}
        tid = r.get("context.trace_id") or ctx.get("trace_id") or r.get("trace_id")
        sid = r.get("context.span_id") or ctx.get("span_id") or r.get("span_id")
        if not tid or not sid:
            continue
        start, end = _parse_ts(r.get("start_time")), _parse_ts(r.get("end_time"))
        spans.append(Span(str(tid), str(sid), r.get("parent_id"), r.get("name", ""),
                          int(start.timestamp() * 1e9) if start else 0, int(end.timestamp() * 1e9) if end else 0, attrs))
    return assemble(spans, source="import:phoenix")


def _flatten(d: dict, prefix: str = "") -> dict:
    out = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out
