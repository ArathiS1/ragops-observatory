import { test } from "node:test";
import assert from "node:assert/strict";
import { RagopsClient, toChunk } from "../dist/index.js";

function mockFetch(responses = []) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init, body: init.body ? JSON.parse(init.body) : undefined });
    const r = responses.shift() ?? { status: 202, body: { ok: true } };
    if (r.throw) throw new Error(r.throw);
    return new Response(JSON.stringify(r.body ?? {}), { status: r.status });
  };
  fn.calls = calls;
  return fn;
}

test("toChunk normalises strings, tuples, dicts and LangChain.js documents", () => {
  assert.deepEqual(toChunk("hello", 1), { text: "hello", rank: 1 });
  assert.equal(toChunk([{ text: "a", doc_id: "d" }, 0.7], 2).score, 0.7);
  const lc = toChunk({ pageContent: "x", metadata: { source: "guide.md", chunk_id: "guide.md#3" } }, 1);
  assert.equal(lc.doc_id, "guide.md");
  assert.equal(lc.chunk_id, "guide.md#3");
});

test("trace callback records steps, batches, and sends on flush", async () => {
  const f = mockFetch();
  const rg = new RagopsClient({ baseUrl: "http://x", apiKey: "k", project: "p", fetch: f, flushIntervalMs: 60_000 });
  const out = await rg.trace("q1", { configLabel: "v1", metadata: { user: "u" } }, async (t) => {
    await t.step("retrieve", async () => ["doc a", "doc b"], (d) => t.retrieved(d));
    t.context(["doc a"]);
    t.generation("answer", { model: "m", promptTokens: 10 });
    return 42;
  });
  assert.equal(out, 42);
  assert.equal(f.calls.length, 0, "nothing sent before flush");
  await rg.flush();
  assert.equal(f.calls.length, 1);
  const [tr] = f.calls[0].body.traces;
  assert.equal(f.calls[0].url, "http://x/v1/projects/p/traces/batch");
  assert.equal(f.calls[0].init.headers.authorization, "Bearer k");
  assert.equal(tr.config_label, "v1");
  assert.equal(tr.retrieved.length, 2);
  assert.equal(tr.contexts[0].text, "doc a");
  assert.ok("retrieve" in tr.metadata.step_latency_ms);
  await rg.close();
});

test("errors in the app are recorded and re-thrown; SDK never throws on transport failure", async () => {
  const f = mockFetch([{ throw: "ECONNREFUSED" }, { throw: "ECONNREFUSED" }, { throw: "ECONNREFUSED" }]);
  const rg = new RagopsClient({ baseUrl: "http://x", fetch: f, sync: true });
  await assert.rejects(rg.trace("q", {}, async () => { throw new TypeError("boom"); }), TypeError);
  await rg.flush();
  assert.equal(rg.dropped, 1);
  assert.match(f.calls[0].body.traces[0].metadata.error, /TypeError: boom/);
});

test("4xx is not retried; 5xx is retried", async () => {
  const f = mockFetch([{ status: 401, body: { detail: "bad key" } }]);
  const rg = new RagopsClient({ baseUrl: "http://x", fetch: f, sync: true });
  assert.equal(await rg.feedback("t1", -1), undefined);
  assert.equal(f.calls.length, 1);
  const g = mockFetch([{ status: 503 }, { status: 200, body: { ok: true } }]);
  const rg2 = new RagopsClient({ baseUrl: "http://x", fetch: g, sync: true });
  assert.deepEqual(await rg2.feedback("t1", 1), { ok: true });
  assert.equal(g.calls.length, 2);
});

test("observe() wraps a RAG function and queue is bounded", async () => {
  const f = mockFetch();
  const rg = new RagopsClient({ baseUrl: "http://x", fetch: f, maxQueue: 1, flushIntervalMs: 60_000, maxBatch: 100 });
  const rag = rg.observe(async (q) => ({ answer: `A:${q}`, contexts: ["c1"] }), { configLabel: "obs" });
  assert.equal((await rag("hi")).answer, "A:hi");
  await rag("again");
  assert.equal(rg.dropped, 1);
  await rg.flush();
  assert.equal(f.calls[0].body.traces[0].answer, "A:hi");
  await rg.close();
});
