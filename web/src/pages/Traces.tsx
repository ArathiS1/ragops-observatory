import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { call, nice, num, pct, proj, when } from "../api";
import { Loading, useConn, useProj } from "../components/ctx";
import { LabelPill, PageHead, Table } from "../components/parts";
import { EvidenceGraph, Waterfall } from "../components/viz";

const LABELS = ["knowledge_gap", "stale_knowledge", "chunking_defect", "retrieval_miss", "ranking_failure", "context_overload", "generation_failure"];

export function TraceList() {
  const [params, setParams] = useSearchParams();
  const label = params.get("label") ?? "";
  const outcome = params.get("outcome") ?? (params.get("failed") === "true" ? "failed" : "failed");
  const env = params.get("environment") ?? "";
  const [q, setQ] = useState(params.get("q") ?? "");
  const qs = new URLSearchParams({ limit: "200" });
  if (label) qs.set("label", label);
  if (outcome !== "all") qs.set("failed", String(outcome === "failed"));
  if (params.get("q")) qs.set("q", params.get("q")!);
  if (env) qs.set("environment", env);
  const { data, error } = useProj(`/traces?${qs}`);
  const nav = useNavigate();
  const set = (k: string, v: string) => { const p = new URLSearchParams(params); v ? p.set(k, v) : p.delete(k); setParams(p); };
  return (
    <>
      <PageHead title="Traces" lede="Every request: what was retrieved, what the model saw, how it scored and why it failed." />
      <div className="row" style={{ marginBottom: "1rem" }}>
        <label className="field">Root cause
          <select value={label} onChange={(e) => set("label", e.target.value)}>
            <option value="">Any</option>{LABELS.map((l) => <option key={l} value={l}>{nice(l)}</option>)}
          </select>
        </label>
        <label className="field">Outcome
          <select value={outcome} onChange={(e) => set("outcome", e.target.value)}>
            <option value="failed">Failed</option><option value="passed">Passed</option><option value="all">All</option>
          </select>
        </label>
        <label className="field">Environment
          <select value={env} onChange={(e) => set("environment", e.target.value)}>
            <option value="">All</option><option value="production">production</option><option value="staging">staging</option><option value="dev">dev</option>
          </select>
        </label>
        <form onSubmit={(e) => { e.preventDefault(); set("q", q); }} className="row">
          <label className="field">Question contains<input value={q} onChange={(e) => setQ(e.target.value)} placeholder="refund" /></label>
          <button type="submit">Search</button>
        </form>
      </div>
      {!data ? <Loading error={error} what="traces" /> : !data.length ? <div className="empty">No requests match these filters.</div> : (
        <Table rows={data} onRow={(r) => nav(`/traces/${r.id}`)} cols={[
          { k: "created_at", h: "Time", render: (r) => when(r.created_at) },
          { k: "query", h: "Question", cls: "clip" },
          { k: "label", h: "Result", render: (r) => r.eval_status === "done" ? <LabelPill label={r.label} /> : <span className="muted">{r.eval_status.replace("_", " ")}</span> },
          { k: "environment", h: "Env", render: (r) => <span className="muted small">{r.environment ?? "–"}</span> },
          { k: "latency_ms", h: "Latency", num: true, render: (r) => r.latency_ms ? `${Math.round(r.latency_ms)} ms` : "–" },
          { k: "cost_usd", h: "Cost", num: true, render: (r) => r.cost_usd != null ? `$${r.cost_usd.toFixed(5)}` : "–" },
        ]} />
      )}
    </>
  );
}

export function TraceDetail() {
  const { id } = useParams();
  const { conn } = useConn();
  const { data: t, error } = useProj(`/traces/${id}`);
  const graph = useProj(`/traces/${id}/evidence-graph`);
  const [msg, setMsg] = useState<string | null>(null);
  if (!t) return <Loading error={error} what="the trace" />;
  const dx = t.diagnosis;
  const steps = ["retrieve", "rerank", "context"].map((s) => ({ s, chunks: t.chunks.filter((c: any) => c.step === s) }))
    .filter((x) => x.chunks.length);
  const act = async (path: string, body: any, done: string) => {
    try { await call(conn, "POST", proj(conn, path), body); setMsg(done); } catch (e: any) { setMsg(e.message); }
  };
  return (
    <>
      <p><Link to="/traces">Back to traces</Link></p>
      <h1 style={{ maxWidth: "60ch" }}>{t.query}</h1>
      <p className="lede">{[when(t.created_at), t.config?.label && `config ${t.config.label}`, t.latency_ms && `${Math.round(t.latency_ms)} ms`, t.model].filter(Boolean).join(", ")}</p>
      <div className="panel" style={{ marginBottom: "1.25rem" }}><strong>Answer.</strong> {t.answer ?? <span className="muted">none</span>}</div>
      {dx && dx.label !== "ok" && (
        <div className="status critical"><span aria-hidden>🩺</span><p><span className="tag">{nice(dx.label)}</span> ({pct(dx.confidence)} confident). {dx.fix}</p></div>
      )}
      {dx?.label === "ok" && <div className="status healthy"><span aria-hidden>✅</span><p><span className="tag">Passed.</span> No check failed for this request.</p></div>}
      {!dx && <div className="status info"><span aria-hidden>ℹ️</span><p><span className="tag">Not evaluated.</span> {t.metadata?.sample_reason ?? t.eval_status}</p></div>}
      <div className="board">
        <section className="box span-8">
          <div className="box-head"><h2>Evidence graph</h2>
            {graph.data && <span className="small muted">{graph.data.summary.supported} of {graph.data.summary.claims} claims backed by a source · {graph.data.summary.documents} documents · {graph.data.summary.chunks} passages</span>}</div>
          {!graph.data ? <Loading error={graph.error} what="the evidence graph" />
            : graph.data.summary.claims || graph.data.summary.chunks ? <EvidenceGraph g={graph.data} />
            : <div className="empty">No answer claims or passages to connect.</div>}
        </section>
        <section className="box span-4">
          <div className="box-head"><h2>Timeline</h2><span className="small muted">{t.latency_ms ? `${Math.round(t.latency_ms)} ms total` : ""}</span></div>
          {t.metadata?.step_latency_ms ? <Waterfall steps={t.metadata.step_latency_ms} total={t.latency_ms} />
            : <div className="empty small">Per-stage timings appear when your app uses the SDK's <code>with t.retrieve():</code> and <code>t.generate()</code> blocks.</div>}
          <h3>Details</h3>
          <div className="kv">
            <span /><span className="muted">Environment</span><span className="num">{t.metadata?.environment ?? "–"}</span>
            <span /><span className="muted">Model</span><span className="num">{t.model ?? "–"}</span>
            <span /><span className="muted">Cost</span><span className="num">{t.cost_usd != null ? `$${t.cost_usd.toFixed(5)}${t.metadata?.cost_estimated ? " (est.)" : ""}` : "–"}</span>
            <span /><span className="muted">Session</span><span className="num">{t.metadata?.session_id ?? "–"}</span>
            <span /><span className="muted">Source</span><span className="num">{t.source}</span>
          </div>
        </section>
      </div>
      <div className="grid2">
        <section>
          {steps.map(({ s, chunks }) => (
            <div key={s}>
              <h3>{{ retrieve: "Retrieved candidates", rerank: "After reranking", context: "Sent to the model" }[s]}</h3>
              {chunks.map((c: any) => (
                <div key={`${s}${c.rank}`} className={`chunk ${c.is_gold ? "gold" : ""} ${c.stale ? "stale" : ""}`}>
                  <div className="meta">#{c.rank} {c.doc_id}{c.doc_version ? ` v${c.doc_version}` : ""}{c.score != null ? `, score ${num(c.score, 3)}` : ""}
                    {c.is_gold && <strong style={{ color: "var(--good)" }}>, the chunk that should answer this</strong>}
                    {c.stale && <strong style={{ color: "var(--warn)" }}>, outdated: current version is v{c.current_version ?? "deleted"}</strong>}
                  </div>
                  {c.text}
                </div>
              ))}
            </div>
          ))}
        </section>
        <section>
          <h3>Scores</h3>
          <Table rows={t.evaluations} cols={[
            { k: "metric", h: "Metric", render: (r) => nice(r.metric) },
            { k: "score", h: "Score", num: true, render: (r) => num(r.score) },
            { k: "reasoning", h: "Why" },
          ]} />
          {(dx?.evidence?.claims ?? []).length > 0 && (<>
            <h3>Claims in the answer</h3>
            <Table rows={dx.evidence.claims} cols={[
              { k: "claim", h: "Claim" },
              { k: "verdict", h: "Evidence", render: (r) => <span className={`pill ${r.verdict === "supported" ? "ok" : ""}`}>{{ supported: "✓ Supported", contradicted: "✕ Contradicted", unsupported: "? Not found" }[r.verdict as string] ?? r.verdict}</span> },
              { k: "doc_id", h: "Source", render: (r) => r.doc_id ? `${r.doc_id}${r.evidence ? `: “${r.evidence.slice(0, 120)}”` : ""}` : "–" },
            ]} />
            <p className="muted small">"Not found" means no retrieved passage states it: a lead to check, not proof the model invented it.</p>
          </>)}
          {dx && dx.label !== "ok" && (<><h3>Evidence</h3><div className="code">{JSON.stringify(dx.evidence, null, 2)}</div></>)}
          <h3>Actions</h3>
          <div className="row">
            <button className="primary" onClick={() => act("/golden/promote", { trace_id: t.id }, "Added to the production-failures golden set.")}>Add to golden set</button>
            <button onClick={() => act("/labels", { trace_id: t.id, metric: "faithfulness", passed: true }, "Saved: answer marked correct.")}>Mark answer correct</button>
            <button onClick={() => act("/labels", { trace_id: t.id, metric: "faithfulness", passed: false }, "Saved: answer marked wrong.")}>Mark answer wrong</button>
          </div>
          {msg && <p className="small" role="status" style={{ marginTop: 8 }}>{msg}</p>}
        </section>
      </div>
    </>
  );
}
