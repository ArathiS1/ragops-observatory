import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { call, nice, num, pct, proj, when } from "../api";
import { Loading, useConn, useProj } from "../components/ctx";
import { DoctorView } from "../components/Doctor";
import { LabelPill, PageHead, Table } from "../components/parts";

const BLANK = { name: "", url: "", answer_path: "answer", contexts_path: "contexts", retrieved_path: "retrieved",
                query_field: "query", headers: "", default_config: "{}" };

export default function TestRag() {
  const { conn } = useConn();
  const pipes = useProj<any[]>("/pipelines");
  const runs = useProj<any[]>("/runs");
  const golden = useProj<any[]>("/golden-sets");
  const [form, setForm] = useState({ ...BLANK });
  const [formMsg, setFormMsg] = useState<string | null>(null);
  const [check, setCheck] = useState<{ q: string; out?: any; err?: string }>({ q: "" });
  const [runForm, setRunForm] = useState({ pipeline: "", dataset: "golden", golden: "", cases: "", config: "{}", name: "" });
  const [runMsg, setRunMsg] = useState<string | null>(null);
  const [cmp, setCmp] = useState<{ base: string; cand: string; res?: any }>({ base: "", cand: "" });

  useEffect(() => {  // poll while a run is in progress
    if (!runs.data?.some((r) => r.status === "queued" || r.status === "running")) return;
    const t = setTimeout(runs.reload, 1500);
    return () => clearTimeout(t);
  }, [runs.data, runs.reload]);

  const register = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const headers = Object.fromEntries(form.headers.split("\n").filter((l) => l.includes(":"))
        .map((l) => [l.slice(0, l.indexOf(":")).trim(), l.slice(l.indexOf(":") + 1).trim()]));
      await call(conn, "POST", proj(conn, "/pipelines"), { ...form, headers, default_config: JSON.parse(form.default_config || "{}") });
      setFormMsg(`Registered ${form.name}. Send it a test question below.`);
      setRunForm((r) => ({ ...r, pipeline: form.name }));
      pipes.reload();
    } catch (err: any) { setFormMsg(`Not registered: ${err.message}`); }
  };
  const doCheck = async (name: string) => {
    setCheck((c) => ({ ...c, out: undefined, err: undefined }));
    try { setCheck((c) => ({ ...c, out: undefined })); const out = await call(conn, "POST", proj(conn, `/pipelines/${name}/check`), { query: check.q });
          setCheck((c) => ({ ...c, out })); } catch (err: any) { setCheck((c) => ({ ...c, err: err.message })); }
  };
  const startRun = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const cases = runForm.dataset === "paste" ? runForm.cases.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
        try { return JSON.parse(l); } catch { return { query: l }; }
      }) : [];
      await call(conn, "POST", proj(conn, "/runs"), { pipeline: runForm.pipeline, golden_set: runForm.dataset === "golden" ? runForm.golden : null,
                                                      cases, config: JSON.parse(runForm.config || "{}"), name: runForm.name || null });
      setRunMsg("Test started. Results appear below as cases finish."); runs.reload();
    } catch (err: any) { setRunMsg(`Test not started: ${err.message}`); }
  };
  const compare = async () => {
    try { setCmp((c) => ({ ...c, res: undefined }));
          const res = await call(conn, "GET", proj(conn, `/runs/compare?base=${cmp.base}&candidate=${cmp.cand}`)); setCmp((c) => ({ ...c, res })); }
    catch (err: any) { setCmp((c) => ({ ...c, res: { error: err.message } })); }
  };
  const done = (runs.data ?? []).filter((r) => r.status === "done");

  return (
    <>
      <PageHead title="Test your RAG" lede="Connect your own RAG system, run it on real failures or your own questions, and compare versions before you ship." />

      <h2>1. Connect your RAG</h2>
      <p className="muted small">Any language or framework works: RAGOps sends <code>{`{"query": "...", "config": {...}}`}</code> to your endpoint and reads the answer and the chunks you used from the JSON reply. Python pipelines can also be tested locally with <code>ragops test</code>.</p>
      {pipes.data?.length ? (
        <Table rows={pipes.data} cols={[
          { k: "name", h: "Pipeline" }, { k: "url", h: "Endpoint", render: (r) => r.spec.url },
          { k: "map", h: "Reads", render: (r) => `${r.spec.answer_path}, ${r.spec.contexts_path}` },
          { k: "created_at", h: "Added", render: (r) => when(r.created_at) },
        ]} />
      ) : null}
      <form onSubmit={register} className="panel stack" style={{ marginTop: "1rem" }}>
        <div className="grid-form">
          <label className="field">Name<input required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="support-rag" /></label>
          <label className="field" style={{ gridColumn: "span 2" }}>Endpoint URL<input required type="url" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://rag.internal/ask" /></label>
          <label className="field">Question field<input value={form.query_field} onChange={(e) => setForm({ ...form, query_field: e.target.value })} /></label>
          <label className="field">Answer is at<input value={form.answer_path} onChange={(e) => setForm({ ...form, answer_path: e.target.value })} /></label>
          <label className="field">Chunks are at<input value={form.contexts_path} onChange={(e) => setForm({ ...form, contexts_path: e.target.value })} /></label>
          <label className="field">Candidates are at (optional)<input value={form.retrieved_path} onChange={(e) => setForm({ ...form, retrieved_path: e.target.value })} /></label>
        </div>
        <div className="grid-form">
          <label className="field">Headers, one per line<textarea style={{ minHeight: "4rem" }} value={form.headers} onChange={(e) => setForm({ ...form, headers: e.target.value })} placeholder="Authorization: Bearer ..." /></label>
          <label className="field">Default config (JSON)<textarea style={{ minHeight: "4rem" }} value={form.default_config} onChange={(e) => setForm({ ...form, default_config: e.target.value })} /></label>
        </div>
        <div className="row"><button className="primary" type="submit">Register pipeline</button>{formMsg && <span className="small" role="status">{formMsg}</span>}</div>
      </form>

      {!!pipes.data?.length && (
        <>
          <h2>2. Check the connection</h2>
          <div className="row">
            <label className="field" style={{ flex: 1 }}>Test question<input value={check.q} onChange={(e) => setCheck({ q: e.target.value })} placeholder="How long do refunds take?" /></label>
            {pipes.data.map((p) => <button key={p.name} disabled={!check.q} onClick={() => doCheck(p.name)}>Ask {p.name}</button>)}
          </div>
          {check.err && <p className="err" style={{ marginTop: 8 }}>{check.err}</p>}
          {check.out && (
            <div className="panel stack" style={{ marginTop: "0.75rem" }}>
              <div><LabelPill label={check.out.label} /> {check.out.error && <span className="err">{check.out.error}</span>}</div>
              <div><strong>Answer.</strong> {check.out.answer || <span className="muted">empty: check “Answer is at”</span>}</div>
              <div className="small muted">{check.out.contexts.length} chunks read{check.out.contexts.length ? "" : ": check “Chunks are at”"}, {Math.round(check.out.latency_ms ?? 0)} ms.
                {" "}{Object.entries(check.out.scores).filter(([, v]) => v != null).map(([k, v]) => `${nice(k)} ${num(v as number)}`).join(", ")}</div>
            </div>
          )}

          <h2>3. Run a test</h2>
          <form onSubmit={startRun} className="panel stack">
            <div className="grid-form">
              <label className="field">Pipeline<select required value={runForm.pipeline} onChange={(e) => setRunForm({ ...runForm, pipeline: e.target.value })}>
                <option value="">Choose…</option>{pipes.data.map((p) => <option key={p.name}>{p.name}</option>)}</select></label>
              <label className="field">Questions<select value={runForm.dataset} onChange={(e) => setRunForm({ ...runForm, dataset: e.target.value })}>
                <option value="golden">A golden set</option><option value="paste">Paste my own</option></select></label>
              {runForm.dataset === "golden" ? (
                <label className="field">Golden set<select required value={runForm.golden} onChange={(e) => setRunForm({ ...runForm, golden: e.target.value })}>
                  <option value="">Choose…</option>{(golden.data ?? []).map((g) => <option key={g.name} value={g.name}>{g.name} ({g.cases} cases)</option>)}</select></label>
              ) : null}
              <label className="field">Run name (optional)<input value={runForm.name} onChange={(e) => setRunForm({ ...runForm, name: e.target.value })} placeholder="chunk 512 + reranker" /></label>
            </div>
            {runForm.dataset === "paste" && (
              <label className="field">One question per line, or JSON lines with expected_answer / expected_doc_ids
                <textarea required value={runForm.cases} onChange={(e) => setRunForm({ ...runForm, cases: e.target.value })}
                          placeholder={'How long do refunds take?\n{"query": "Can I cancel monthly plans?", "expected_doc_ids": ["billing-refunds"]}'} /></label>
            )}
            <label className="field">Config sent to your pipeline (JSON)<input value={runForm.config} onChange={(e) => setRunForm({ ...runForm, config: e.target.value })} /></label>
            <div className="row"><button className="primary" type="submit">Start test</button>{runMsg && <span className="small" role="status">{runMsg}</span>}</div>
          </form>
        </>
      )}

      <h2>Test runs</h2>
      {!runs.data ? <Loading error={runs.error} what="runs" /> : !runs.data.length ? <div className="empty">No test runs yet. Start one above, or push one from your laptop with <code>ragops test --push</code>.</div> : (
        <Table rows={runs.data} cols={[
          { k: "name", h: "Run", render: (r) => <Link to={`/test/${r.id}`}>{r.name}</Link> },
          { k: "status", h: "Status", render: (r) => r.status === "done" ? "Done" : r.status === "failed" ? <span className="err">Failed</span> :
              <div style={{ minWidth: 140 }}><div className="progress"><i style={{ width: `${(r.progress / Math.max(1, r.total)) * 100}%` }} /></div>
                <span className="small muted">{r.status === "queued" ? "Waiting for a worker (ragops worker)" : `${r.progress} of ${r.total}`}</span></div> },
          { k: "cases", h: "Cases", num: true, render: (r) => r.total },
          { k: "pass", h: "Pass rate", num: true, render: (r) => pct(r.summary?.pass_rate) },
          { k: "faith", h: "Faithfulness", num: true, render: (r) => num(r.summary?.metrics?.faithfulness) },
          { k: "rel", h: "Answer relevance", num: true, render: (r) => num(r.summary?.metrics?.answer_relevance) },
          { k: "src", h: "From", render: (r) => r.source === "cli" ? "ragops test" : "this page" },
          { k: "created_at", h: "Started", render: (r) => when(r.created_at) },
        ]} />
      )}

      {done.length >= 2 && (
        <>
          <h2>Compare two runs</h2>
          <p className="muted small">Paired on the same questions. A metric only counts as a regression when it drops by more than 2% and the 95% bootstrap interval is entirely below zero.</p>
          <div className="row">
            <label className="field">Before<select value={cmp.base} onChange={(e) => setCmp({ ...cmp, base: e.target.value })}><option value="">Choose…</option>{done.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
            <label className="field">After<select value={cmp.cand} onChange={(e) => setCmp({ ...cmp, cand: e.target.value })}><option value="">Choose…</option>{done.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}</select></label>
            <button className="primary" disabled={!cmp.base || !cmp.cand || cmp.base === cmp.cand} onClick={compare}>Compare</button>
          </div>
          {cmp.res?.error && <p className="err">{cmp.res.error}</p>}
          {cmp.res?.report && <Comparison rep={cmp.res.report} />}
        </>
      )}
    </>
  );
}

function Comparison({ rep }: { rep: any }) {
  return (
    <div className="stack" style={{ marginTop: "1rem" }}>
      <div className={`status ${rep.passed ? "healthy" : "critical"}`}><span aria-hidden>{rep.passed ? "✅" : "⛔"}</span>
        <p><span className="tag">{rep.passed ? "Safe to ship." : "Would be blocked."}</span> {rep.passed ? `No significant regression on ${rep.n_cases} paired questions.` : rep.reasons.join("; ")}</p></div>
      <Table rows={rep.metrics} cols={[
        { k: "metric", h: "Metric", render: (r) => nice(r.metric) },
        { k: "baseline", h: "Before", num: true, render: (r) => num(r.baseline, 3) },
        { k: "candidate", h: "After", num: true, render: (r) => num(r.candidate, 3) },
        { k: "delta", h: "Change", num: true, render: (r) => `${r.delta >= 0 ? "+" : ""}${r.delta.toFixed(3)}` },
        { k: "ci", h: "95% interval", num: true, render: (r) => `${r.ci_low.toFixed(3)} to ${r.ci_high.toFixed(3)}` },
        { k: "status", h: "Verdict", render: (r) => <span className={`pill ${r.status === "regressed" ? "bad" : r.status === "improved" ? "ok" : ""}`}>{r.status}</span> },
      ]} />
      {!!rep.newly_failing.length && (<><h3>Now failing ({rep.newly_failing.length})</h3>
        <Table rows={rep.newly_failing} cols={[{ k: "query", h: "Question", cls: "clip" }, { k: "label", h: "Stage", render: (r) => <LabelPill label={r.label} /> }]} /></>)}
      {!!rep.newly_fixed.length && <p className="small muted">{rep.newly_fixed.length} questions that failed before now pass.</p>}
    </div>
  );
}

export function RunDetail() {
  const { id } = useParams();
  const { data: r, error } = useProj(`/runs/${id}`);
  const [only, setOnly] = useState(true);
  const { conn } = useConn();
  const doc = useProj(r?.status === "done" ? `/runs/${id}/doctor` : null, [r?.status]);
  const [busy, setBusy] = useState(false);
  const pending = doc.data?.pending_trials?.length ?? 0;
  useEffect(() => {
    if (!pending) return;
    const t = setInterval(doc.reload, 2000);
    return () => clearInterval(t);
  }, [pending, doc.reload]);
  const trial = async () => {
    setBusy(true);
    try { await call(conn, "POST", proj(conn, `/runs/${id}/doctor/trial`), {}); doc.reload(); }
    finally { setBusy(false); }
  };
  if (!r) return <Loading error={error} what="the run" />;
  const s = r.summary ?? {};
  const rows = (r.cases ?? []).filter((c: any) => !only || c.failed);
  return (
    <>
      <p><Link to="/test">Back to test runs</Link></p>
      <PageHead title={r.name} lede={`${r.pipeline} on ${r.dataset}, ${r.total} questions. Config ${JSON.stringify(r.config)}`} />
      {r.error && <p className="err">{r.error}</p>}
      <div className="figures">
        <div className="figure"><div className="v">{pct(s.pass_rate)}</div><div className="k">passed</div></div>
        {Object.entries(s.metrics ?? {}).filter(([, v]) => v != null).map(([k, v]) => (
          <div className="figure" key={k}><div className="v">{num(v as number)}</div><div className="k">{nice(k)}</div></div>))}
        {s.latency_p95_ms != null && <div className="figure"><div className="v">{Math.round(s.latency_p95_ms)} ms</div><div className="k">latency p95</div></div>}
      </div>
      {s.stages && Object.keys(s.stages).length > 0 && (
        <p>Failures by stage: {Object.entries(s.stages).map(([k, v]) => <span key={k} style={{ marginRight: 12 }}><LabelPill label={k} /> {String(v)}</span>)}</p>)}
      {doc.data && (<><h2>Diagnosis</h2><DoctorView r={doc.data} onTrial={r.source !== "cli" ? trial : undefined} trialBusy={busy} /></>)}
      {r.source === "cli" && doc.data?.findings?.length > 0 && <p className="muted">This run was uploaded from the CLI. Run <code>ragops doctor</code> where your pipeline lives to test fixes on it.</p>}
      <h2>All cases</h2>
      <div className="row" style={{ margin: "1rem 0" }}>
        <div className="seg"><button className={only ? "on" : ""} onClick={() => setOnly(true)}>Failed</button><button className={!only ? "on" : ""} onClick={() => setOnly(false)}>All</button></div>
      </div>
      <Table rows={rows} cols={[
        { k: "query", h: "Question", cls: "clip" },
        { k: "label", h: "Result", render: (c) => <LabelPill label={c.label} /> },
        { k: "answer", h: "Answer", cls: "clip", render: (c) => c.error ? <span className="err">{c.error}</span> : c.answer },
        { k: "faith", h: "Faithfulness", num: true, render: (c) => num(c.scores?.faithfulness) },
        { k: "rel", h: "Relevance", num: true, render: (c) => num(c.scores?.answer_relevance) },
      ]} />
    </>
  );
}
