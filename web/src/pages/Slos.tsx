import { useState } from "react";
import { call, nice, num, pct, proj, when } from "../api";
import { Loading, useConn, useProj } from "../components/ctx";
import { PageHead, Table } from "../components/parts";

const METRICS = ["faithfulness", "answer_relevance", "context_relevance", "context_recall", "citation_correctness", "abstention_quality"];

export default function Slos() {
  const { conn } = useConn();
  const slos = useProj<any[]>("/slos");
  const alerts = useProj<any[]>("/alerts");
  const chans = useProj<any[]>("/alert-channels");
  const [f, setF] = useState({ name: "Grounded answers", metric: "faithfulness", threshold: 0.7, target: 0.9, window_days: 7 });
  const [c, setC] = useState({ kind: "slack", target: "" });
  const [msg, setMsg] = useState<string | null>(null);
  const post = async (path: string, body: any, ok: string, after: () => void) => {
    try { await call(conn, "POST", proj(conn, path), body); setMsg(ok); after(); } catch (e: any) { setMsg(e.message); }
  };
  return (
    <>
      <PageHead title="SLOs and alerts" lede="Promises about answer quality, with an error budget like an uptime SLO. Alerts name the stage that is failing." />
      {!slos.data ? <Loading error={slos.error} what="SLOs" /> : slos.data.length ? (
        <Table rows={slos.data} cols={[
          { k: "name", h: "SLO" }, { k: "metric", h: "Metric", render: (r) => `${nice(r.metric)} ≥ ${r.threshold}` },
          { k: "target", h: "Target", num: true, render: (r) => pct(r.target) },
          { k: "good_ratio", h: "Actual", num: true, render: (r) => pct(r.good_ratio, 1) },
          { k: "budget_remaining", h: "Budget left", num: true, render: (r) => pct(r.budget_remaining) },
          { k: "burn_rate_24h", h: "24h burn", num: true, render: (r) => r.burn_rate_24h == null ? "–" : `${num(r.burn_rate_24h, 1)}×` },
          { k: "status", h: "Status", render: (r) => <span className={`pill ${r.status === "breached" ? "bad" : r.status === "at_risk" ? "warn" : r.status === "ok" ? "ok" : ""}`}>{r.status.replace("_", " ")}</span> },
        ]} />
      ) : <div className="empty">No SLOs yet. Add one below.</div>}
      <form className="panel stack" style={{ marginTop: "1rem" }} onSubmit={(e) => { e.preventDefault(); post("/slos", f, `Created “${f.name}”.`, slos.reload); }}>
        <div className="grid-form">
          <label className="field">Name<input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} /></label>
          <label className="field">Metric<select value={f.metric} onChange={(e) => setF({ ...f, metric: e.target.value })}>{METRICS.map((m) => <option key={m} value={m}>{nice(m)}</option>)}</select></label>
          <label className="field">A request is good at score ≥<input type="number" step="0.05" min="0" max="1" value={f.threshold} onChange={(e) => setF({ ...f, threshold: +e.target.value })} /></label>
          <label className="field">Share of good requests promised<input type="number" step="0.005" min="0.5" max="0.999" value={f.target} onChange={(e) => setF({ ...f, target: +e.target.value })} /></label>
          <label className="field">Window (days)<input type="number" min="1" max="90" value={f.window_days} onChange={(e) => setF({ ...f, window_days: +e.target.value })} /></label>
        </div>
        <div className="row"><button className="primary" type="submit">Create SLO</button>
          <button type="button" onClick={() => post("/slos/check", {}, "Checked all SLOs.", () => { slos.reload(); alerts.reload(); })}>Check SLOs now</button>
          {msg && <span className="small" role="status">{msg}</span>}</div>
      </form>
      <h2>Where alerts go</h2>
      {chans.error ? <p className="muted">Alert channels need an admin key.</p> : (
        <>
          {!!chans.data?.length && <Table rows={chans.data} cols={[{ k: "kind", h: "Channel" }, { k: "target", h: "Destination" }]} />}
          <form className="row" style={{ marginTop: "0.75rem" }} onSubmit={(e) => { e.preventDefault(); post("/alert-channels", c, "Channel added.", chans.reload); }}>
            <label className="field">Channel<select value={c.kind} onChange={(e) => setC({ ...c, kind: e.target.value })}>{["slack", "webhook", "pagerduty", "email"].map((k) => <option key={k}>{k}</option>)}</select></label>
            <label className="field" style={{ flex: 1 }}>Webhook URL, routing key or address<input required value={c.target} onChange={(e) => setC({ ...c, target: e.target.value })} /></label>
            <button type="submit">Add channel</button>
          </form>
        </>
      )}
      <h2>Recent alerts</h2>
      {!alerts.data?.length ? <div className="empty">No alerts.</div> : alerts.data.map((a, i) => (
        <div key={i} className={`status ${a.severity === "critical" ? "critical" : a.severity === "warning" ? "warning" : "info"}`}>
          <span aria-hidden>{a.severity === "critical" ? "⛔" : a.severity === "warning" ? "⚠️" : "ℹ️"}</span>
          <p><span className="tag">{a.title.replace(/^\[RAGOps\] /, "")}</span> <span className="muted small">{when(a.at)}</span><br />{a.body}</p>
        </div>
      ))}
    </>
  );
}
