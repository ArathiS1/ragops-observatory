# @ragops/sdk: TypeScript / JavaScript SDK for RAGOps Observatory

Trace RAG calls from Node 18+, Bun, Deno, edge functions or the browser. Same guarantees as the Python SDK: it never
throws into your app, never blocks the request path (background batching with retries), and drops data rather than
growing memory when the server is unreachable.

```bash
npm install @ragops/sdk        # from this repo: cd sdk-ts && npm install && npm run build
```

```ts
import { RagopsClient } from "@ragops/sdk";

const rg = new RagopsClient({ baseUrl: "http://localhost:8000", apiKey: process.env.RAGOPS_API_KEY, project: "support-bot" });

// 1. trace a request (the trace ends automatically, also when your code throws)
const answer = await rg.trace(question, { configLabel: "chunk512-v3", sessionId }, async (t) => {
  const docs = await t.step("retrieve", () => retriever.invoke(question), (d) => t.retrieved(d)); // LangChain.js docs OK
  const top = await t.step("rerank", () => rerank(question, docs), (d) => t.context(d));
  const reply = await t.step("generate", () => llm(question, top));
  t.generation(reply, { model: "gpt-4o-mini", promptTokens: 812, completionTokens: 96 });
  return reply;
});

// 2. or wrap an existing function returning { answer, contexts, retrieved? }
const ask = rg.observe(myRag, { configLabel: "v3" });

// 3. feedback, corpus registry, bring-your-own-RAG testing
await rg.feedback(traceId, -1, "wrong refund window");
await rg.upsertCorpus([{ doc_id: "refunds.md", text: "..." }]);
await rg.registerPipeline("support-rag", "https://rag.internal/ask", { answer_path: "data.answer", contexts_path: "data.sources" });
const run = await rg.startRun({ pipeline: "support-rag", goldenSet: "production-failures" });

await rg.flush(); // in serverless functions, before returning
```

Configuration falls back to `RAGOPS_URL`, `RAGOPS_API_KEY` and `RAGOPS_PROJECT`. Options: `sampleRate`, `flushIntervalMs`,
`maxBatch`, `maxQueue`, `timeoutMs`, `sync`, `enabled`, a custom `fetch` and a `logger`.

Tests: `npm test` (unit, mocked fetch) and `npm run test:integration` against a live server.
