# Integrations

## Python SDK

```python
from ragops import RagopsClient, observe_rag
rg = RagopsClient("https://ragops.internal", api_key="rgo_ingest...", project="support-bot")

with rg.trace(q, config_label="v7", config={"chunk": 512, "top_k": 8}, user_tier="pro") as t:
    t.retrieved(candidates); t.reranked(reranked); t.context(prompt_chunks)
    t.generation(answer, model="gpt-4o-mini", prompt_tokens=900, completion_tokens=80, cost_usd=0.0002)
    t.expected(doc_ids=["refund-policy"])    # optional, e.g. for synthetic checks
rg.feedback(t.id, -1, "wrong refund window")
```

Per-step timing: `with t.retrieve() as s: s.output(docs)`, likewise `t.rerank()`, `t.build_context()` and
`t.generate(model=...)`; step latencies appear in the trace metadata. `t.forceEval()` / `force_eval=True` bypasses
sampling for synthetic checks.

`@observe_rag(rg, config_label="v7")` wraps a function returning `{"answer", "contexts", "retrieved"}`.
`rg.log(query, answer, contexts)` is a one-liner for code that already has everything.
Chunks can be strings, dicts, `(doc, score)` tuples, LangChain `Document`s or LlamaIndex `NodeWithScore`s; put
`doc_id`, `chunk_id` and `doc_version` in metadata for full diagnosis.

## TypeScript / JavaScript

`sdk-ts/` (`@ragops/sdk`) works in Node 18+, Bun, Deno, edge functions and browsers, with the same guarantees as the
Python SDK. See [sdk-ts/README.md](https://github.com/ArathiS1/ragops-observatory/tree/main/sdk-ts).

## LangChain / LlamaIndex / Haystack

```python
from ragops.sdk.adapters import langchain_handler, log_haystack, log_llamaindex
chain.invoke({"input": q}, config={"callbacks": [langchain_handler(rg, "lc-v4")]})
response = query_engine.query(q); log_llamaindex(rg, q, response, config_label="li-v2")
result = pipe.run(inputs, include_outputs_from={"retriever", "ranker"}); log_haystack(rg, q, result, "hs-v1")
```

## OpenTelemetry / OpenInference (no code changes)

`POST /otlp/v1/traces` accepts OTLP/HTTP in both encodings — `application/x-protobuf` (the default of most OTel
exporters) and `application/json` — with or without `Content-Encoding: gzip`. See `examples/otel/` for exporter variables and a Collector config
that sends the same spans to Phoenix or Langfuse and to RAGOps.

## Backfill from Langfuse or Phoenix

```bash
curl -X POST .../v1/projects/support-bot/import/langfuse -F file=@langfuse-traces.json
curl -X POST .../v1/projects/support-bot/import/phoenix  -F file=@phoenix-spans.jsonl
```

## Judges

| `RAGOPS_JUDGE` | What | Needs |
|---|---|---|
| `heuristic` (default) | Deterministic, free, offline | nothing |
| `openai` | Any OpenAI-compatible chat model scores each metric | `OPENAI_API_KEY`, `RAGOPS_JUDGE_MODEL` |
| `ollama` | Local models via Ollama's OpenAI endpoint | `RAGOPS_JUDGE_BASE_URL=http://localhost:11434/v1`, e.g. `RAGOPS_JUDGE_MODEL=llama3.1` |
| `ragas` | Ragas 0.4 metrics (faithfulness, answer relevancy, context precision and recall) | `pip install "ragops-observatory[ragas]"`, an OpenAI-compatible model and embeddings |

Every LLM-based judge works with vLLM, Azure OpenAI, LiteLLM or any OpenAI-compatible server through
`RAGOPS_JUDGE_BASE_URL`. The judge's key comes from `RAGOPS_JUDGE_API_KEY` (falls back to `OPENAI_API_KEY`), so it
can use a different provider than your application. Free example with Google Gemini:

```bash
RAGOPS_JUDGE=openai
RAGOPS_JUDGE_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
RAGOPS_JUDGE_MODEL=gemini-2.5-flash-lite
RAGOPS_JUDGE_API_KEY=<key from aistudio.google.com/apikey>
``` If the judge fails, times out or returns nothing usable, that metric falls back to the
heuristic judge (marked in the result), so an evaluation is never lost.

**Check the judge before trusting it.** `ragops judge check` scores 12 labelled cases (good answers, hallucinations,
wrong refusals, irrelevant context) and reports agreement and Cohen's kappa with the labels, plus how often it fell
back. It exits 1 if the judge is not working, so it fits in CI:

```bash
ragops judge check --judge ollama --model llama3.1 --base-url http://localhost:11434/v1
ragops judge check --judge ragas --model gpt-4o-mini
```

For tests without a model or API key, `python -m ragops.testing.stub_llm --port 8301` starts a deterministic
OpenAI-compatible server (chat + embeddings) that exercises the full LLM and Ragas code paths.

## Drift reports with Evidently

With `pip install "ragops-observatory[evidently]"`, `GET /v1/projects/{p}/drift/evidently` (or *Drift → Open Evidently
report* in the UI) builds an Evidently report comparing the current and reference windows: column drift for query
length, retrieval scores, latency and quality metrics, plus embedding drift on the query embeddings.
