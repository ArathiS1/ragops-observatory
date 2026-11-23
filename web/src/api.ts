// Thin client for the RAGOps REST API. Connection settings persist per browser (localStorage, guarded).

export interface Conn { url: string; key: string; project: string }

const STORE = "ragops.conn";

export function loadConn(): Conn {
  try {
    const raw = localStorage.getItem(STORE);
    if (raw) return JSON.parse(raw);
  } catch { /* storage unavailable */ }
  return { url: "", key: "", project: "" };
}

export function saveConn(c: Conn): void {
  try { localStorage.setItem(STORE, JSON.stringify(c)); } catch { /* ignore */ }
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

export async function call<T = any>(c: Conn, method: string, path: string, body?: unknown): Promise<T> {
  const r = await fetch(`${c.url.replace(/\/$/, "")}${path}`, {
    method,
    headers: { authorization: `Bearer ${c.key}`, ...(body !== undefined ? { "content-type": "application/json" } : {}) },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await r.text();
  let data: any = text;
  try { data = text ? JSON.parse(text) : {}; } catch { /* plain text response */ }
  if (!r.ok) throw new ApiError(r.status, typeof data === "object" && data?.detail ? String(data.detail) : `HTTP ${r.status}`);
  return data as T;
}

/** Fetch a binary/HTML response (PDF report, Evidently report) and hand it to the browser. */
export async function openBlob(c: Conn, path: string, filename?: string): Promise<void> {
  const r = await fetch(`${c.url.replace(/\/$/, "")}${path}`, { headers: { authorization: `Bearer ${c.key}` } });
  if (!r.ok) {
    let msg = `HTTP ${r.status}`;
    try { msg = (await r.json()).detail ?? msg; } catch { /* not JSON */ }
    throw new ApiError(r.status, msg);
  }
  const url = URL.createObjectURL(await r.blob());
  if (filename) {
    const a = document.createElement("a"); a.href = url; a.download = filename; a.click();
  } else {
    window.open(url, "_blank", "noopener");
  }
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

export const proj = (c: Conn, path: string) => `/v1/projects/${encodeURIComponent(c.project)}${path}`;

export const STAGES = [
  { key: "corpus", name: "Knowledge base", labels: ["knowledge_gap", "stale_knowledge", "chunking_defect", "incomplete_context"] },
  { key: "retrieve", name: "Retrieval", labels: ["retrieval_miss"] },
  { key: "rank", name: "Ranking", labels: ["ranking_failure"] },
  { key: "prompt", name: "Prompt", labels: ["context_overload"] },
  { key: "generate", name: "Generation", labels: ["generation_failure"] },
] as const;

// Fixed colour per root cause, shared by every chart (colour follows the entity, never its rank).
export const LABEL_COLOR: Record<string, string> = {
  knowledge_gap: "#2a78d6", stale_knowledge: "#eb6834", chunking_defect: "#1baf7a", retrieval_miss: "#eda100",
  ranking_failure: "#e87ba4", context_overload: "#008300", generation_failure: "#6250d6", incomplete_context: "#0e8fa3", pipeline_error: "#8a93a3",
};

export const NICE: Record<string, string> = {
  knowledge_gap: "Knowledge gap", stale_knowledge: "Stale knowledge", chunking_defect: "Chunking defect",
  retrieval_miss: "Retrieval miss", ranking_failure: "Ranking failure", context_overload: "Context overload",
  generation_failure: "Generation failure", incomplete_context: "Right doc, wrong passage", pipeline_error: "Pipeline error", security_leak: "Security leak", ok: "Passed",
  answer_correctness: "Answer correctness",
  faithfulness: "Faithfulness", answer_relevance: "Answer relevance", context_relevance: "Context relevance",
  context_recall: "Context recall", citation_correctness: "Citation correctness", abstention_quality: "Abstention quality",
  context_consistency: "Context consistency",
};

export const nice = (s?: string | null) => (s ? NICE[s] ?? s.replace(/_/g, " ") : "");
export const pct = (x?: number | null, d = 0) => (x === null || x === undefined ? "–" : `${(x * 100).toFixed(d)}%`);
export const num = (x?: number | null, d = 2) => (x === null || x === undefined ? "–" : x.toFixed(d));
export const when = (iso?: string) => (iso ? new Date(iso.endsWith("Z") ? iso : iso + "Z").toLocaleString() : "");
