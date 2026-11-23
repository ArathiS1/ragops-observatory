# Zero-code ingestion with OpenTelemetry / OpenInference

If your app is already instrumented with OpenInference (Arize Phoenix's instrumentors for LlamaIndex, LangChain,
Haystack, DSPy, the OpenAI SDK, ...), point the OTLP exporter at RAGOps Observatory. No code changes:

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://localhost:8000/otlp/v1/traces
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL=http/json
export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer rgo_your_ingest_key"
```

To send to Phoenix/Langfuse **and** RAGOps at the same time, run an OpenTelemetry Collector with two exporters
(see `collector.yaml`). Spans sharing a trace id are assembled into one RAG trace: `RETRIEVER` spans become
retrieved chunks, `RERANKER` spans become reranked chunks, `LLM` spans give the model and token counts.

Optional span attributes RAGOps understands on the root span: `ragops.config_label` (pipeline version),
`ragops.user_feedback` (-1..1), `session.id`. Put `doc_id` and `doc_version` in document metadata to enable
stale-knowledge detection.

Backfill history: `POST /v1/projects/{project}/import/phoenix` (spans export) or `/import/langfuse` (traces export).
