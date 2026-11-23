"""OpenTelemetry / OpenInference ingestion.

Teams that already instrument with OpenInference (Arize Phoenix, OpenLLMetry-style exporters, LlamaIndex/LangChain
auto-instrumentation) can point an OTLP/HTTP exporter at RAGOps Observatory with no code changes:

    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://ragops:8000/otlp/v1/traces
    OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/json
    OTEL_EXPORTER_OTLP_TRACES_HEADERS=Authorization=Bearer rgo_xxx

Spans sharing a traceId are assembled into one RAG trace: RETRIEVER spans -> retrieved chunks, RERANKER spans ->
reranked chunks, LLM spans -> model/tokens/answer, root CHAIN span -> query and final answer.
The same assembler is used by the Phoenix importer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ragops.schemas import ChunkIn, TraceIn


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_id: str | None
    name: str
    start_ns: int
    end_ns: int
    attrs: dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        k = str(self.attrs.get("openinference.span.kind") or self.attrs.get("span.kind") or "").upper()
        if not k:
            n = self.name.lower()
            if "retriev" in n or "search" in n:
                return "RETRIEVER"
            if "rerank" in n:
                return "RERANKER"
            if "llm" in n or "chat" in n or "generat" in n or "completion" in n:
                return "LLM"
            return "CHAIN"
        return k


def _any_value(v: dict) -> Any:
    if "stringValue" in v:
        return v["stringValue"]
    if "intValue" in v:
        return int(v["intValue"])
    if "doubleValue" in v:
        return float(v["doubleValue"])
    if "boolValue" in v:
        return bool(v["boolValue"])
    if "arrayValue" in v:
        return [_any_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return {kv["key"]: _any_value(kv["value"]) for kv in v["kvlistValue"].get("values", [])}
    return None


def parse_otlp_json(body: dict) -> list[Span]:
    spans = []
    for rs in body.get("resourceSpans", []):
        res_attrs = {a["key"]: _any_value(a["value"]) for a in rs.get("resource", {}).get("attributes", [])}
        for ss in rs.get("scopeSpans", []) or rs.get("instrumentationLibrarySpans", []):
            for s in ss.get("spans", []):
                attrs = dict(res_attrs)
                attrs.update({a["key"]: _any_value(a["value"]) for a in s.get("attributes", [])})
                spans.append(Span(s["traceId"], s["spanId"], s.get("parentSpanId") or None, s.get("name", ""),
                                  int(s.get("startTimeUnixNano", 0)), int(s.get("endTimeUnixNano", 0)), attrs))
    return spans


def _docs(attrs: dict, prefix: str) -> list[ChunkIn]:
    """Read OpenInference document lists: '<prefix>.<i>.document.(id|content|score|metadata)' (flattened)
    or a nested list under '<prefix>'."""
    nested = attrs.get(prefix)
    out: list[ChunkIn] = []
    if isinstance(nested, list):
        for i, d in enumerate(nested):
            d = d.get("document", d) if isinstance(d, dict) else {"content": str(d)}
            out.append(_doc_to_chunk(d, i))
        return out
    idx = 0
    while True:
        base = f"{prefix}.{idx}.document."
        if not any(k.startswith(base) for k in attrs):
            break
        d = {k[len(base):]: v for k, v in attrs.items() if k.startswith(base)}
        out.append(_doc_to_chunk(d, idx))
        idx += 1
    return out


def _doc_to_chunk(d: dict, i: int) -> ChunkIn:
    meta = d.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = {}
    cid = d.get("id") or meta.get("chunk_id")
    doc_id = meta.get("doc_id") or meta.get("source") or meta.get("file_name") or (str(cid).split("#")[0] if cid else None)
    ver = meta.get("doc_version") or meta.get("version")
    return ChunkIn(text=d.get("content") or d.get("text") or d.get("page_content"), chunk_id=None if cid is None else str(cid),
                   doc_id=None if doc_id is None else str(doc_id), doc_version=int(ver) if ver is not None else None,
                   score=float(d["score"]) if d.get("score") is not None else None, rank=i + 1)


def _text(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, str):
        try:
            parsed = json.loads(v)
        except ValueError:
            return v
        v = parsed
    if isinstance(v, dict):
        for k in ("query", "question", "input", "answer", "output", "result", "response", "content", "text"):
            if isinstance(v.get(k), str):
                return v[k]
        return json.dumps(v)[:4000]
    return str(v)


def assemble(spans: list[Span], source: str = "otel") -> list[TraceIn]:
    by_trace: dict[str, list[Span]] = {}
    for s in spans:
        by_trace.setdefault(s.trace_id, []).append(s)
    out = []
    for tid, group in by_trace.items():
        group.sort(key=lambda s: s.start_ns)
        ids = {s.span_id for s in group}
        roots = [s for s in group if not s.parent_id or s.parent_id not in ids]
        root = roots[0] if roots else group[0]
        retr = [s for s in group if s.kind == "RETRIEVER"]
        rer = [s for s in group if s.kind == "RERANKER"]
        llms = [s for s in group if s.kind == "LLM"]
        if not retr and not llms:
            continue  # not a RAG trace
        query = _text(root.attrs.get("input.value")) or (retr and _text(retr[0].attrs.get("input.value"))) or ""
        answer = _text(root.attrs.get("output.value")) if root.kind != "RETRIEVER" else None
        if not answer and llms:
            last = llms[-1]
            answer = _text(last.attrs.get("llm.output_messages.0.message.content")) or _text(last.attrs.get("output.value"))
        retrieved = [c for s in retr for c in _docs(s.attrs, "retrieval.documents")]
        reranked = [c for s in rer for c in _docs(s.attrs, "reranker.output_documents")]
        model = llms[-1].attrs.get("llm.model_name") if llms else None
        pt = sum(int(s.attrs.get("llm.token_count.prompt", 0) or 0) for s in llms) or None
        ct = sum(int(s.attrs.get("llm.token_count.completion", 0) or 0) for s in llms) or None
        from datetime import datetime, timezone

        start = min(s.start_ns for s in group)
        end = max(s.end_ns for s in group)
        fb = root.attrs.get("ragops.user_feedback")
        out.append(TraceIn(
            id=f"otel-{tid}"[:64], query=query, answer=answer, retrieved=retrieved, reranked=reranked,
            contexts=reranked or retrieved, model=model, prompt_tokens=pt, completion_tokens=ct,
            latency_ms=(end - start) / 1e6 if end > start else None,
            session_id=root.attrs.get("session.id"), config_label=root.attrs.get("ragops.config_label"),
            user_feedback=float(fb) if fb is not None else None,
            timestamp=datetime.fromtimestamp(start / 1e9, tz=timezone.utc) if start else None,
            metadata={"_source": source, "otel_trace_id": tid}))
    return out


def protobuf_to_otlp_json(body: bytes, content_encoding: str | None = None) -> dict:
    """Decode an OTLP/HTTP protobuf ExportTraceServiceRequest into the OTLP/JSON shape parse_otlp_json reads.

    Needs ``opentelemetry-proto`` (installed with the server extra). Trace and span ids are bytes in protobuf and
    base64 in protobuf's JSON mapping, but hex in OTLP/JSON, so they are converted.
    """
    import base64
    import gzip

    from google.protobuf.json_format import MessageToDict
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

    if content_encoding and "gzip" in content_encoding.lower():
        body = gzip.decompress(body)
    msg = ExportTraceServiceRequest()
    msg.ParseFromString(body)
    data = MessageToDict(msg)
    for rs in data.get("resourceSpans", []):
        for ss in rs.get("scopeSpans", []):
            for sp in ss.get("spans", []):
                for k in ("traceId", "spanId", "parentSpanId"):
                    if sp.get(k):
                        sp[k] = base64.b64decode(sp[k]).hex()
    return data
