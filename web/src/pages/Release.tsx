import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { call, nice, num, pct, proj, when } from "../api";
import { Loading, useConn, useProj } from "../components/ctx";
import { LabelPill, PageHead, Table } from "../components/parts";

const STATUS: Record<string, string> = { regression: "Regressions", fixed: "Fixed", still_failing: "Still failing", unchanged: "Unchanged", added: "Added", removed: "Removed" };
const WORD: Record<string, string> = { pass: "PASS", review: "REVIEW", block: "BLOCK" };
const SAY: Record<string, string> = {
  pass: "Every agreed gate passed. The candidate can be released.",
  review: "No blocking gate failed, but something changed that a person should look at before release.",
  block: "A blocking gate failed. Do not release this candidate unless an admin overrides with a reason.",
};
const GATE_HELP: Record<string, string> = {
  min_pass_rate: "Lowest acceptable pass rate (0–1)", max_pass_rate_drop: "Largest allowed fall in pass rate",
  max_metric_drop: "Smallest metric drop that can block (with significance)", max_p95_ms: "p95 latency limit in ms (empty = none)",
  max_latency_increase: "Largest p95 latency rise before review (0.3 = 30%)", max_error_rate: "Largest share of crashed cases",
  block_on_security_leak: "Block when an attack probe leaks", review_if_newly_failing: "Ask for review from this many newly failing questions",
};

export default function Release() {
  const { conn } = useConn();
  const runs = useProj<any[]>("/runs");
  const gates = useProj<any>("/release/gates");
  const hist = useProj<any[]>("/release/decisions");
  const done = useMemo(() => (runs.data ?? []).filter((r) => r.status === "done"), [runs.data]);
  const [base, setBase] = useState("");
  const [cand, setCand] = useState("");
  const b = base || done[1]?.id || "", c = cand || done[0]?.id || "";
  const chk = useProj(b && c ? `/release/check?base=${b}&candidate=${c}` : null, [b, c]);
  const [picked, setShow] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [edit, setEdit] = useState<Record<string, any> | null>(null);
  if (!runs.data) return <Loading error={runs.error} what="test runs" />;
  if (done.length < 2) return (<><PageHead title="Release guardian" lede="Test a new RAG version against the current one and stop it when it fails the agreed quality gates." />
    <div className="empty">You need two finished test runs on the same questions: run your current version and the candidate on <Link to="/test">Test your RAG</Link>, or push them with <code>ragops test --push</code>.</div></>);
  const r = chk.data;
  const show = picked ?? (r ? (["regression", "still_failing", "fixed"].find((s) => r.counts[s]) ?? "all") : "all");
  const cases = r ? r.cases.filter((x: any) => show === "all" || x.status === show) : [];
  const decide = async (decision: string) => {
    setMsg(null);
    try {
      await call(conn, "POST", proj(conn, "/release/decisions"), { base: b, candidate: c, decision, reason });
      setMsg(decision === "approve" ? "Approval recorded in the audit log." : "Rejection recorded in the audit log."); setReason(""); hist.reload();
    } catch (e: any) { setMsg(e.message); }
  };
  const saveGates = async () => {
    try {
      const body: any = {};
      Object.entries(edit!).forEach(([k, v]) => { body[k] = v === "" ? null : typeof gates.data[k] === "boolean" ? v : v === null ? null : Number(v); });
      await call(conn, "PUT", proj(conn, "/release/gates"), body); setEdit(null); gates.reload(); chk.reload();
    } catch (e: any) { setMsg(e.message); }
  };
  return (
    <>
      <PageHead title="Release guardian" lede="Test a new RAG version against the current one and stop it when it fails the agreed quality gates. Every decision is kept in the audit log." />
      <div className="row" style={{ marginBottom: "1rem" }}>
        <label className="field">Current version (baseline)
          <select value={b} onChange={(e) => setBase(e.target.value)}>{done.map((x) => <option key={x.id} value={x.id}>{x.name} · {when(x.created_at)}</option>)}</select></label>
        <span className="muted" style={{ paddingBottom: 8 }}>→</span>
        <label className="field">Candidate
          <select value={c} onChange={(e) => setCand(e.target.value)}>{done.map((x) => <option key={x.id} value={x.id}>{x.name} · {when(x.created_at)}</option>)}</select></label>
        <Link className="btn" to={`/benchmark`}>Compare more versions</Link>
      </div>
      {!r ? <Loading error={chk.error} what="the release check" /> : (<>
        <div className={`verdict ${r.decision}`} role="status">
          <span className="big">{WORD[r.decision]}</span>
          <div><strong>{r.candidate.name}</strong> vs <strong>{r.base.name}</strong>. {SAY[r.decision]}
            <div className="small muted">Pass rate {pct(r.base.summary.pass_rate)} → {pct(r.candidate.summary.pass_rate)} on {r.candidate.summary.cases} questions</div></div>
        </div>
        <div className="board">
          <section className="box span-7">
            <div className="box-head"><h2>Quality gates</h2><button className="link" onClick={() => setEdit(edit ? null : { ...gates.data })}>{edit ? "Cancel" : "Edit gates"}</button></div>
            {edit ? (
              <div className="stack">
                {Object.keys(GATE_HELP).map((k) => (
                  <label key={k} className="field">{GATE_HELP[k]}
                    {typeof gates.data[k] === "boolean"
                      ? <select value={String(edit[k])} onChange={(e) => setEdit({ ...edit, [k]: e.target.value === "true" })}><option value="true">yes</option><option value="false">no</option></select>
                      : <input value={edit[k] ?? ""} onChange={(e) => setEdit({ ...edit, [k]: e.target.value })} />}
                  </label>))}
                <button className="primary" onClick={saveGates}>Save gates (admin)</button>
              </div>
            ) : (
              <Table rows={r.checks} cols={[
                { k: "gate", h: "Gate" }, { k: "shown", h: "Candidate" },
                { k: "severity", h: "If it fails", render: (x) => x.severity === "block" ? "blocks" : "asks for review" },
                { k: "passed", h: "Result", render: (x) => <span className={`pill ${x.passed ? "ok" : x.severity === "block" ? "bad" : "warn"}`}>{x.passed ? "✓ passed" : x.severity === "block" ? "⛔ failed" : "⚠ review"}</span> },
              ]} />
            )}
          </section>
          <section className="box span-5">
            <div className="box-head"><h2>Decision</h2></div>
            <label className="field">Reason {r.decision !== "pass" && <span className="small">(required to approve: this is an override)</span>}
              <textarea value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. hotfix for a security issue; refund document is being rewritten" style={{ minHeight: "4.5rem" }} /></label>
            <div className="row" style={{ marginTop: 8 }}>
              <button className="primary" onClick={() => decide("approve")}>{r.decision === "pass" ? "Approve release" : "Override and approve"}</button>
              <button onClick={() => decide("reject")}>Reject</button>
            </div>
            {msg && <p className="small" role="status" style={{ marginTop: 8 }}>{msg}</p>}
            <h3>History</h3>
            {!hist.data?.length ? <p className="muted small">No decisions yet.</p> : (
              <ul className="plain">{hist.data.slice(0, 6).map((h: any, i: number) => (
                <li key={i} className="small"><span className={`pill ${h.decision === "approve" ? (h.override ? "warn" : "ok") : "bad"}`}>{h.decision}{h.override ? " (override)" : ""}</span>{" "}
                  <strong>{h.candidate_name}</strong> by {h.actor}, guardian said {h.guardian}<div className="muted">{when(h.at)}{h.reason ? `: ${h.reason}` : ""}</div></li>))}</ul>)}
          </section>
        </div>
        <section className="box" style={{ marginBottom: "0.9rem" }}>
          <div className="box-head"><h2>Metrics</h2><span className="small muted">paired bootstrap, 95% interval</span></div>
          <Table rows={r.metrics} cols={[
            { k: "metric", h: "Metric", render: (m) => nice(m.metric) },
            { k: "baseline", h: "Current", num: true, render: (m) => num(m.baseline, 3) },
            { k: "candidate", h: "Candidate", num: true, render: (m) => num(m.candidate, 3) },
            { k: "delta", h: "Change", num: true, render: (m) => <span style={{ color: m.status === "regressed" ? "var(--bad)" : m.status === "improved" ? "var(--good)" : undefined }}>{m.delta > 0 ? "+" : ""}{num(m.delta, 3)}</span> },
            { k: "ci", h: "95% interval", render: (m) => `${num(m.ci_low, 3)} … ${num(m.ci_high, 3)}` },
            { k: "status", h: "", render: (m) => <span className={`pill ${m.status === "regressed" ? "bad" : m.status === "improved" ? "ok" : ""}`}>{m.status}</span> },
          ]} />
        </section>
        <section className="box">
          <div className="box-head"><h2>Question by question</h2></div>
          <div className="chips" role="group" aria-label="Filter questions">
            {["all", "regression", "fixed", "still_failing", "unchanged", "added", "removed"].filter((s) => s === "all" || r.counts[s]).map((s) => (
              <button key={s} className={`chip ${s} ${show === s ? "on" : ""}`} onClick={() => setShow(s)}>
                {s === "all" ? "All" : STATUS[s]}<span className="c">{s === "all" ? r.cases.length : r.counts[s]}</span></button>))}
          </div>
          {!cases.length ? <div className="empty">No questions in this group.</div> : (
            <Table rows={cases} cols={[
              { k: "query", h: "Question", cls: "clip" },
              { k: "base", h: "Current", render: (x) => x.base ? <span className={`cell ${x.base.failed ? "fail" : "pass"}`}>{x.base.failed ? "✕ fail" : "✓ pass"}</span> : <span className="cell none">—</span> },
              { k: "candidate", h: "Candidate", render: (x) => x.candidate ? <span className={`cell ${x.candidate.failed ? "fail" : "pass"}`}>{x.candidate.failed ? "✕ fail" : "✓ pass"}</span> : <span className="cell none">—</span> },
              { k: "why", h: "Candidate's problem", render: (x) => x.candidate?.failed ? <LabelPill label={x.candidate.label} /> : "–" },
              { k: "ans", h: "Candidate answered", cls: "clip", render: (x) => x.candidate?.answer || "–" },
            ]} />)}
        </section>
      </>)}
    </>
  );
}
