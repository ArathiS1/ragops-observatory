/**
 * RAGOps Observatory TypeScript SDK.
 *
 *   import { RagopsClient } from "@ragops/sdk";
 *   const rg = new RagopsClient({ baseUrl: "http://localhost:8000", apiKey: "rgo_...", project: "support-bot" });
 *
 *   await rg.trace(question, { configLabel: "v7" }, async (t) => {
 *     const docs = await t.step("retrieve", () => retriever.search(question), (d) => t.retrieved(d));
 *     t.context(docs.slice(0, 4));
 *     const answer = await t.step("generate", () => llm(question, docs));
 *     t.generation(answer, { model: "gpt-4o-mini" });
 *     return answer;
 *   });
 *
 * Design rules (same as the Python SDK): never throw into the host application, never block the request path
 * (traces are batched and sent in the background with retries), and drop data instead of growing without bound.
 * Works in Node 18+, Deno, Bun, edge runtimes and browsers (uses global fetch).
 */

export interface Chunk {
  text?: string | null;
  chunk_id?: string | null;
  doc_id?: string | null;
  doc_version?: number | null;
  score?: number | null;
  rank?: number;
}

export type ChunkLike =
  | string
  | Chunk
  | { pageContent: string; metadata?: Record<string, unknown>; id?: string } // LangChain.js Document
  | [unknown, number] // [doc, score]
  | Record<string, unknown>;

export interface TracePayload {
  id: string;
  query: string;
  answer?: string;
  retrieved: Chunk[];
  reranked: Chunk[];
  contexts: Chunk[];
  model?: string;
  latency_ms?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  cost_usd?: number;
  session_id?: string;
  config_label?: string;
  config?: Record<string, unknown>;
  expected_answer?: string;
  expected_doc_ids?: string[];
  user_feedback?: number;
  force_eval?: boolean;
  metadata: Record<string, unknown>;
  timestamp: string;
}

export interface ClientOptions {
  baseUrl?: string;
  apiKey?: string;
  project?: string;
  flushIntervalMs?: number;
  maxBatch?: number;
  maxQueue?: number;
  timeoutMs?: number;
  sampleRate?: number;
  enabled?: boolean;
  /** send each trace immediately (useful in serverless functions; remember to await rg.flush()) */
  sync?: boolean;
  fetch?: typeof fetch;
  logger?: (msg: string) => void;
}

export interface TraceOptions {
  configLabel?: string;
  config?: Record<string, unknown>;
  sessionId?: string;
  metadata?: Record<string, unknown>;
  id?: string;
}

const env = (k: string): string | undefined =>
  typeof process !== "undefined" && process.env ? process.env[k] : undefined;

function uuid(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (c?.randomUUID) return c.randomUUID().replace(/-/g, "");
  return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
}

/** Normalise retriever outputs from common frameworks into RAGOps chunks. */
export function toChunk(d: ChunkLike, rank: number): Chunk {
  if (typeof d === "string") return { text: d, rank };
  if (Array.isArray(d) && d.length === 2 && typeof d[1] === "number") {
    return { ...toChunk(d[0] as ChunkLike, rank), score: d[1] };
  }
  const o = d as Record<string, any>;
  const meta = (o.metadata ?? {}) as Record<string, any>;
  if (typeof o.pageContent === "string") {
    return { text: o.pageContent, chunk_id: meta.chunk_id ?? o.id ?? null, doc_id: meta.doc_id ?? meta.source ?? null,
             doc_version: meta.doc_version ?? null, score: meta.score ?? null, rank };
  }
  return {
    text: o.text ?? o.content ?? o.page_content ?? null,
    chunk_id: o.chunk_id ?? o.id ?? meta.chunk_id ?? null,
    doc_id: o.doc_id ?? meta.doc_id ?? meta.source ?? null,
    doc_version: o.doc_version ?? meta.doc_version ?? null,
    score: o.score ?? null,
    rank,
  };
}

export class Trace {
  readonly id: string;
  readonly data: TracePayload;
  private readonly t0 = Date.now();
  private ended = false;

  constructor(private readonly client: RagopsClient, query: string, opts: TraceOptions = {}) {
    this.id = opts.id ?? uuid();
    this.data = {
      id: this.id, query, retrieved: [], reranked: [], contexts: [], config_label: opts.configLabel,
      config: opts.config, session_id: opts.sessionId, metadata: { ...(opts.metadata ?? {}) },
      timestamp: new Date().toISOString(),
    };
  }

  retrieved(docs: ChunkLike[] = []): this { this.data.retrieved = docs.map((d, i) => toChunk(d, i + 1)); return this; }
  reranked(docs: ChunkLike[] = []): this { this.data.reranked = docs.map((d, i) => toChunk(d, i + 1)); return this; }
  context(docs: ChunkLike[] = []): this { this.data.contexts = docs.map((d, i) => toChunk(d, i + 1)); return this; }

  generation(answer: string, o: { model?: string; promptTokens?: number; completionTokens?: number; costUsd?: number } = {}): this {
    Object.assign(this.data, { answer, model: o.model, prompt_tokens: o.promptTokens,
                               completion_tokens: o.completionTokens, cost_usd: o.costUsd });
    return this;
  }

  expected(o: { answer?: string; docIds?: string[] }): this {
    if (o.answer) this.data.expected_answer = o.answer;
    if (o.docIds?.length) this.data.expected_doc_ids = o.docIds;
    return this;
  }

  set(meta: Record<string, unknown>): this { Object.assign(this.data.metadata, meta); return this; }

  /** Always evaluate this trace, bypassing the project's sampling (e.g. synthetic checks, canaries). */
  forceEval(): this { this.data.force_eval = true; return this; }

  /** Time one pipeline stage. `record` receives the stage output (e.g. t.retrieved). Errors are re-thrown. */
  async step<T>(name: string, fn: () => T | Promise<T>, record?: (out: T) => unknown): Promise<T> {
    const s = Date.now();
    const lat = ((this.data.metadata.step_latency_ms ??= {}) as Record<string, number>);
    try {
      const out = await fn();
      if (record) record(out);
      return out;
    } catch (e) {
      this.data.metadata.failed_step = name;
      throw e;
    } finally {
      lat[name] = Date.now() - s;
    }
  }

  end(): void {
    if (this.ended) return;
    this.ended = true;
    this.data.latency_ms ??= Date.now() - this.t0;
    if (!this.data.contexts.length) this.data.contexts = this.data.reranked.length ? this.data.reranked : this.data.retrieved;
    this.client.submit(this.data);
  }
}

export class RagopsClient {
  readonly baseUrl: string;
  readonly project: string;
  dropped = 0;
  private readonly apiKey: string;
  private readonly opts: Required<Omit<ClientOptions, "baseUrl" | "apiKey" | "project" | "fetch" | "logger">>;
  private readonly fetchImpl: typeof fetch;
  private readonly log: (m: string) => void;
  private queue: TracePayload[] = [];
  private timer: ReturnType<typeof setInterval> | null = null;
  private inflight: Promise<unknown> = Promise.resolve();

  constructor(o: ClientOptions = {}) {
    this.baseUrl = (o.baseUrl ?? env("RAGOPS_URL") ?? "http://localhost:8000").replace(/\/$/, "");
    this.apiKey = o.apiKey ?? env("RAGOPS_API_KEY") ?? "";
    this.project = o.project ?? env("RAGOPS_PROJECT") ?? "default";
    this.opts = { flushIntervalMs: o.flushIntervalMs ?? 2000, maxBatch: o.maxBatch ?? 50, maxQueue: o.maxQueue ?? 10_000,
                  timeoutMs: o.timeoutMs ?? 10_000, sampleRate: o.sampleRate ?? 1, enabled: o.enabled ?? true,
                  sync: o.sync ?? false };
    this.fetchImpl = o.fetch ?? globalThis.fetch.bind(globalThis);
    this.log = o.logger ?? (() => {});
    if (this.opts.enabled && !this.opts.sync) {
      this.timer = setInterval(() => void this.flush(), this.opts.flushIntervalMs);
      (this.timer as { unref?: () => void }).unref?.();
    }
  }

  /** Start a trace. With a callback, the trace ends automatically (also on error) and the callback's value is returned. */
  trace(query: string, opts?: TraceOptions): Trace;
  trace<T>(query: string, opts: TraceOptions, fn: (t: Trace) => T | Promise<T>): Promise<T>;
  trace<T>(query: string, opts: TraceOptions = {}, fn?: (t: Trace) => T | Promise<T>): Trace | Promise<T> {
    const t = new Trace(this, query, opts);
    if (!fn) return t;
    return (async () => {
      try {
        return await fn(t);
      } catch (e) {
        t.set({ error: `${(e as Error)?.name ?? "Error"}: ${(e as Error)?.message ?? e}` });
        throw e;
      } finally {
        t.end();
      }
    })();
  }

  /** Wrap an async RAG function `(query) => {answer, contexts, retrieved?}` so every call is traced. */
  observe<A extends unknown[], R extends { answer: string; contexts?: ChunkLike[]; retrieved?: ChunkLike[]; model?: string }>(
    fn: (query: string, ...rest: A) => Promise<R>, opts: TraceOptions = {},
  ): (query: string, ...rest: A) => Promise<R> {
    return (query, ...rest) => this.trace(query, opts, async (t) => {
      const out = await fn(query, ...rest);
      if (out.retrieved) t.retrieved(out.retrieved);
      if (out.contexts) t.context(out.contexts);
      t.generation(out.answer, { model: out.model });
      return out;
    });
  }

  /** @internal */
  submit(p: TracePayload): void {
    if (!this.opts.enabled || (this.opts.sampleRate < 1 && Math.random() > this.opts.sampleRate)) return;
    if (this.opts.sync) {
      this.inflight = this.inflight.then(() => this.send([p]));
      return;
    }
    if (this.queue.length >= this.opts.maxQueue) { this.dropped++; return; }
    this.queue.push(p);
    if (this.queue.length >= this.opts.maxBatch) void this.flush();
  }

  /** Send everything queued now. Await this before a serverless function returns. */
  async flush(): Promise<void> {
    while (this.queue.length) {
      const batch = this.queue.splice(0, this.opts.maxBatch);
      this.inflight = this.inflight.then(() => this.send(batch));
    }
    await this.inflight;
  }

  async close(): Promise<void> {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    await this.flush();
  }

  private async send(batch: TracePayload[]): Promise<void> {
    const ok = await this.request("POST", `/v1/projects/${encodeURIComponent(this.project)}/traces/batch`, { traces: batch });
    if (ok === undefined) this.dropped += batch.length;
  }

  /** Never throws. Returns parsed JSON, or undefined after retries / on 4xx. */
  async request<T = any>(method: string, path: string, body?: unknown): Promise<T | undefined> {
    for (let attempt = 0; attempt < 3; attempt++) {
      const ctrl = new AbortController();
      const timer = setTimeout(() => ctrl.abort(), this.opts.timeoutMs);
      try {
        const r = await this.fetchImpl(`${this.baseUrl}${path}`, {
          method, signal: ctrl.signal,
          headers: { "content-type": "application/json", authorization: `Bearer ${this.apiKey}` },
          body: body === undefined ? undefined : JSON.stringify(body),
        });
        if (r.status < 500) {
          if (r.status >= 400) { this.log(`ragops ${method} ${path} -> ${r.status} ${await r.text()}`); return undefined; }
          const text = await r.text();
          return (text ? JSON.parse(text) : {}) as T;
        }
      } catch (e) {
        this.log(`ragops transport error: ${(e as Error).message}`);
      } finally {
        clearTimeout(timer);
      }
      await new Promise((res) => setTimeout(res, 300 * 2 ** attempt));
    }
    return undefined;
  }

  // ---------------------------------------------------------------- other APIs
  private p(path: string) { return `/v1/projects/${encodeURIComponent(this.project)}${path}`; }

  feedback(traceId: string, score: number, comment?: string) {
    return this.request("POST", this.p(`/traces/${traceId}/feedback`), { score, comment });
  }

  upsertCorpus(documents: { doc_id: string; title?: string; text?: string; chunks?: Chunk[] }[], chunkWords = 120,
               deleteMissing = false) {
    return this.request("POST", this.p("/corpus"), { documents, chunk_words: chunkWords, delete_missing: deleteMissing });
  }

  /** Register your RAG's HTTP endpoint so it can be tested from the RAGOps UI and API. */
  registerPipeline(name: string, url: string, spec: Record<string, unknown> = {}) {
    return this.request("POST", this.p("/pipelines"), { name, url, ...spec });
  }

  startRun(o: { pipeline: string; goldenSet?: string; cases?: { query: string; expected_answer?: string; expected_doc_ids?: string[] }[];
                config?: Record<string, unknown>; name?: string }) {
    return this.request("POST", this.p("/runs"), { pipeline: o.pipeline, golden_set: o.goldenSet, cases: o.cases ?? [],
                                                   config: o.config ?? {}, name: o.name });
  }

  getRun(id: string) { return this.request("GET", this.p(`/runs/${id}`)); }
  goldenSet(name: string) { return this.request("GET", this.p(`/golden-sets/${encodeURIComponent(name)}/export`)); }
  overview(days = 7) { return this.request("GET", this.p(`/overview?days=${days}`)); }
}

export default RagopsClient;
