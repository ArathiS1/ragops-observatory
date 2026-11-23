# Test your own RAG

RAGOps can test **any** RAG system: yours, in any language or framework. You give it questions; it runs them through
your system, scores every answer, names the pipeline stage behind each failure, and compares versions with statistical
significance. There are three ways in.

## 1. From your laptop or CI: `ragops test`

```bash
pip install "ragops-observatory[server]"
ragops init            # writes ragops.yaml, my_rag.py and rag_tests.jsonl
# edit my_rag.py so answer(query, config) calls your retriever and LLM
ragops test            # runs the dataset, prints and writes ragops-report.md
```

`my_rag.py` only has to return a dict:

```python
def answer(query: str, config: dict) -> dict:
    docs = retriever.search(query, k=config.get("top_k", 5))
    reply = llm(query, docs)
    return {"answer": reply, "contexts": docs}   # dicts, strings, LangChain / LlamaIndex / Haystack objects
```

Or skip Python entirely and point it at your service: `ragops test --endpoint https://rag.internal/ask --answer-path
data.answer --contexts-path data.sources`.

Useful options:

| Option | What it does |
|---|---|
| `--dataset cases.jsonl` | JSONL, JSON or CSV with `query` and optionally `expected_answer`, `expected_doc_ids` |
| `--golden-set production-failures` | Use the regression cases collected from production instead of a file |
| `--corpus docs/` | Your documents (folder, JSON or `module:ATTR`). Enables knowledge-gap, retrieval-miss and chunking diagnosis |
| `--config new.yaml` | Passed to your function as `config`, so one wrapper can test many variants |
| `--push` | Upload the run to the server so it appears on the **Test your RAG** page next to other runs |
| `--judge openai` | Use an LLM judge instead of the deterministic heuristic judge |

`thresholds` in `ragops.yaml` (`max_failure_rate`, `min_metrics`) make `ragops test` exit 1 when they are violated,
so the same command works as a CI check. **To find out how to fix the mistakes, run `ragops doctor`**: it explains
each mistake by cause and tests candidate fixes on your pipeline ([RAG doctor](doctor.md)). To compare two configurations on the same questions with confidence
intervals, use `ragops gate` (see [Release gate](gate.md)).

## 2. From the web UI: register an endpoint

Open **Test your RAG** at `/ui`:

1. **Connect your RAG**: name, endpoint URL and where the answer and the chunks are in your JSON response
   (dotted paths such as `data.output.text`). Secret headers (API keys) are stored and never shown again.
2. **Check the connection**: ask one question and see exactly what RAGOps parsed and how it scored.
3. **Run a test** on a golden set or questions you paste, with any config you want sent to your endpoint.
4. **Read the diagnosis** on the run page: verdict, mistakes by cause with examples, and **Test these fixes**, which
   re-runs your endpoint with candidate config changes and marks the verified fix.
5. **Compare two runs**: paired on the same questions; a metric only regresses when it drops more than 2% and the
   95% bootstrap interval is below zero. Newly failing questions are listed with their failing stage.

RAGOps calls your endpoint with `POST {"query": "...", "config": {...}}` (the question field name is configurable).
Server-side calls are protected against SSRF: private and loopback addresses are refused unless listed in
`RAGOPS_PIPELINE_ALLOWED_HOSTS`. Runs execute in the worker, so start `ragops worker` (Docker Compose does).

The demo project registers a built-in endpoint (`/demo/rag/ask`) so you can try the whole flow immediately.

## 3. From code: SDKs

```python
rg.register_pipeline("support-rag", "https://rag.internal/ask", answer_path="data.answer", contexts_path="data.sources")
run = rg.start_run("support-rag", golden_set="production-failures", config={"top_k": 8})
```

```ts
await rg.registerPipeline("support-rag", "https://rag.internal/ask", { answer_path: "data.answer" });
const run = await rg.startRun({ pipeline: "support-rag", goldenSet: "production-failures" });
```

## Framework wrappers

```python
from ragops.pipelines import LangChainPipeline, LlamaIndexPipeline, HaystackPipeline, HttpPipeline
from ragops.runs import run_cases, summarize, load_dataset

fn = LangChainPipeline(retrieval_chain)                      # or LlamaIndexPipeline(index.as_query_engine())
# fn = HaystackPipeline(pipe, lambda q: {"retriever": {"query": q}, "prompt_builder": {"question": q}})
rows = run_cases(fn, load_dataset("cases.jsonl"))
print(summarize(rows))
```
