import json

from tests.conftest import ADMIN


def _attr(k, v):
    if isinstance(v, float):
        return {"key": k, "value": {"doubleValue": v}}
    return {"key": k, "value": {"stringValue": str(v)}}


def otlp_payload():
    q = "What is the API rate limit on the Pro plan?"
    root = {"traceId": "t1", "spanId": "a", "name": "query", "startTimeUnixNano": "1700000000000000000",
            "endTimeUnixNano": "1700000000900000000",
            "attributes": [_attr("openinference.span.kind", "CHAIN"), _attr("input.value", q),
                           _attr("output.value", "600 requests per minute. [api-rate-limits]"),
                           _attr("ragops.config_label", "otel-v1")]}
    retr = {"traceId": "t1", "spanId": "b", "parentSpanId": "a", "name": "retrieve",
            "startTimeUnixNano": "1700000000100000000", "endTimeUnixNano": "1700000000200000000",
            "attributes": [_attr("openinference.span.kind", "RETRIEVER"), _attr("input.value", q),
                           _attr("retrieval.documents.0.document.id", "api-rate-limits#0"),
                           _attr("retrieval.documents.0.document.content",
                                 "The REST API allows 600 requests per minute per API token on the Pro plan."),
                           _attr("retrieval.documents.0.document.score", 0.81)]}
    llm = {"traceId": "t1", "spanId": "c", "parentSpanId": "a", "name": "llm",
           "startTimeUnixNano": "1700000000300000000", "endTimeUnixNano": "1700000000800000000",
           "attributes": [_attr("openinference.span.kind", "LLM"), _attr("llm.model_name", "gpt-4o-mini"),
                          {"key": "llm.token_count.prompt", "value": {"intValue": "420"}}]}
    return {"resourceSpans": [{"resource": {"attributes": []}, "scopeSpans": [{"spans": [root, retr, llm]}]}]}


def test_otlp_receiver(client, project):
    name, h = project
    r = client.post("/otlp/v1/traces", json=otlp_payload(), headers=h)
    assert r.status_code == 200 and r.json()["rag_traces"] == 1
    t = client.get(f"/v1/projects/{name}/traces/otel-t1", headers=h).json()
    assert t["model"] == "gpt-4o-mini" and t["config"]["label"] == "otel-v1" and t["source"] == "otel"
    ctx = [c for c in t["chunks"] if c["step"] == "context"]
    assert ctx[0]["doc_id"] == "api-rate-limits" and ctx[0]["score"] == 0.81
    assert abs(t["latency_ms"] - 900) < 1


def test_otlp_admin_needs_project_header(client, project):
    assert client.post("/otlp/v1/traces", json=otlp_payload(), headers=ADMIN).status_code == 400
    r = client.post("/otlp/v1/traces", json=otlp_payload(), headers={**ADMIN, "X-Ragops-Project": "p1"})
    assert r.json()["rag_traces"] == 1


def test_langfuse_import(client, project):
    name, h = project
    export = [{"id": "abc", "input": {"question": "How long are audit logs retained?"},
               "output": "Audit logs are retained for 1 year on the Enterprise plan.", "timestamp": "2026-11-01T10:00:00Z",
               "latency": 1.2, "observations": [
                   {"type": "SPAN", "name": "retriever", "startTime": "2026-11-01T10:00:00Z",
                    "output": [{"page_content": "Audit logs are retained for 1 year on the Enterprise plan.",
                                "metadata": {"doc_id": "data-retention", "chunk_id": "data-retention#1"}}]},
                   {"type": "GENERATION", "name": "llm", "model": "gpt-4o", "usage": {"input": 300, "output": 20},
                    "startTime": "2026-11-01T10:00:01Z"}],
               "scores": [{"name": "user-feedback", "value": 0}]}]
    r = client.post(f"/v1/projects/{name}/import/langfuse", files={"file": ("lf.json", json.dumps(export))}, headers=h)
    assert r.json()["imported_traces"] == 1
    t = client.get(f"/v1/projects/{name}/traces/lf-abc", headers=h).json()
    assert t["model"] == "gpt-4o" and t["source"] == "import:langfuse" and t["user_feedback"] == 0


def test_unknown_import_source(client, project):
    name, h = project
    r = client.post(f"/v1/projects/{name}/import/datadog", files={"file": ("x.json", "[]")}, headers=h)
    assert r.status_code == 400


def _protobuf_payload(gz=False) -> bytes:
    """Encode the same trace as the JSON test, as a real OTLP protobuf ExportTraceServiceRequest."""
    import gzip

    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
    from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
    from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span

    def kv(k, v):
        if isinstance(v, float):
            return KeyValue(key=k, value=AnyValue(double_value=v))
        if isinstance(v, int):
            return KeyValue(key=k, value=AnyValue(int_value=v))
        return KeyValue(key=k, value=AnyValue(string_value=v))

    tid = bytes.fromhex("0af7651916cd43dd8448eb211c80319c")
    q = "What is the API rate limit on the Pro plan?"
    root = Span(trace_id=tid, span_id=bytes.fromhex("b7ad6b7169203331"), name="query",
                start_time_unix_nano=1_700_000_000_000_000_000, end_time_unix_nano=1_700_000_000_900_000_000,
                attributes=[kv("openinference.span.kind", "CHAIN"), kv("input.value", q),
                            kv("output.value", "600 requests per minute. [api-rate-limits]"),
                            kv("ragops.config_label", "otel-proto")])
    retr = Span(trace_id=tid, span_id=bytes.fromhex("00f067aa0ba902b7"), parent_span_id=bytes.fromhex("b7ad6b7169203331"),
                name="retrieve", start_time_unix_nano=1_700_000_000_100_000_000,
                end_time_unix_nano=1_700_000_000_200_000_000,
                attributes=[kv("openinference.span.kind", "RETRIEVER"), kv("input.value", q),
                            kv("retrieval.documents.0.document.id", "api-rate-limits#0"),
                            kv("retrieval.documents.0.document.content",
                               "The REST API allows 600 requests per minute per API token on the Pro plan."),
                            kv("retrieval.documents.0.document.score", 0.81)])
    llm = Span(trace_id=tid, span_id=bytes.fromhex("1111111111111111"), parent_span_id=bytes.fromhex("b7ad6b7169203331"),
               name="llm", start_time_unix_nano=1_700_000_000_300_000_000, end_time_unix_nano=1_700_000_000_800_000_000,
               attributes=[kv("openinference.span.kind", "LLM"), kv("llm.model_name", "gpt-4o-mini"),
                           kv("llm.token_count.prompt", 420)])
    body = ExportTraceServiceRequest(resource_spans=[ResourceSpans(scope_spans=[ScopeSpans(spans=[root, retr, llm])])]
                                     ).SerializeToString()
    return gzip.compress(body) if gz else body


def test_otlp_protobuf_and_gzip(client, project):
    name, h = project
    for gz in (False, True):
        hdr = {**h, "Content-Type": "application/x-protobuf", **({"Content-Encoding": "gzip"} if gz else {})}
        r = client.post("/otlp/v1/traces", content=_protobuf_payload(gz), headers=hdr)
        assert r.status_code == 200 and r.json()["rag_traces"] == 1, r.text
    t = client.get(f"/v1/projects/{name}/traces/otel-0af7651916cd43dd8448eb211c80319c", headers=h).json()
    assert t["model"] == "gpt-4o-mini" and t["config"]["label"] == "otel-proto"
    assert [c["doc_id"] for c in t["chunks"] if c["step"] == "context"] == ["api-rate-limits"]
    assert abs(t["latency_ms"] - 900) < 1
    bad = client.post("/otlp/v1/traces", content=b"\x00not-protobuf", headers={**h, "Content-Type": "application/x-protobuf"})
    assert bad.status_code == 400
