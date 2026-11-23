import { useEffect, useState } from "react";
import { call, proj, when } from "../api";
import { Loading, useConn, useProj } from "../components/ctx";
import { PageHead, Table } from "../components/parts";

export default function Settings() {
  const { conn } = useConn();
  const st = useProj("/settings");
  const keys = useProj<any[]>("/keys");
  const audit = useProj<any[]>("/audit?limit=50");
  const [s, setS] = useState<any>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [newKey, setNewKey] = useState({ name: "", role: "ingest" });
  const [created, setCreated] = useState<string | null>(null);
  useEffect(() => { if (st.data) setS(st.data); }, [st.data]);
  if (!s) return <Loading error={st.error} what="settings" />;
  const ag = s.auto_golden ?? { enabled: false, min_confidence: 0.75, golden_set: "production-failures" };
  const save = async () => {
    try { await call(conn, "PATCH", proj(conn, "/settings"), s); setMsg("Settings saved."); } catch (e: any) { setMsg(e.message); }
  };
  const mkKey = async () => {
    try { const r = await call(conn, "POST", proj(conn, "/keys"), newKey); setCreated(r.key); keys.reload(); } catch (e: any) { setMsg(e.message); }
  };
  return (
    <>
      <PageHead title="Settings" lede="Evaluation budget, privacy, retention, automatic golden sets, API keys and the audit log." />
      <div className="panel stack">
        <h3 style={{ marginTop: 0 }}>Evaluation</h3>
        <div className="grid-form">
          <label className="field">Share of normal traffic to evaluate<input type="number" step="0.05" min="0" max="1" value={s.sample_rate} onChange={(e) => setS({ ...s, sample_rate: +e.target.value })} /></label>
          <label className="field">Daily judge budget (USD)<input type="number" min="0" value={s.daily_eval_budget_usd} onChange={(e) => setS({ ...s, daily_eval_budget_usd: +e.target.value })} /></label>
        </div>
        <label><input type="checkbox" checked={s.always_eval_negative_feedback} onChange={(e) => setS({ ...s, always_eval_negative_feedback: e.target.checked })} /> Always evaluate requests that got a thumbs-down</label>
        <h3>Privacy and retention</h3>
        <label><input type="checkbox" checked={s.store_raw_text} onChange={(e) => setS({ ...s, store_raw_text: e.target.checked })} /> Store raw text (turn off to keep only salted hashes)</label>
        <div className="grid-form">
          <label className="field">Redact personal data in (comma separated)<input value={s.redact_fields.join(", ")} onChange={(e) => setS({ ...s, redact_fields: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })} /></label>
          <label className="field">Delete traces after (days)<input type="number" min="1" value={s.retention_days} onChange={(e) => setS({ ...s, retention_days: +e.target.value })} /></label>
        </div>
        <h3>Failures to golden set</h3>
        <label><input type="checkbox" checked={ag.enabled} onChange={(e) => setS({ ...s, auto_golden: { ...ag, enabled: e.target.checked } })} /> Add confidently diagnosed failures to a golden set automatically</label>
        <div className="grid-form">
          <label className="field">Golden set<input value={ag.golden_set} onChange={(e) => setS({ ...s, auto_golden: { ...ag, golden_set: e.target.value } })} /></label>
          <label className="field">Minimum confidence<input type="number" step="0.05" min="0.5" max="1" value={ag.min_confidence} onChange={(e) => setS({ ...s, auto_golden: { ...ag, min_confidence: +e.target.value } })} /></label>
        </div>
        <div className="row"><button className="primary" onClick={save}>Save settings</button>{msg && <span className="small" role="status">{msg}</span>}</div>
      </div>
      <h2>API keys</h2>
      {keys.error ? <p className="muted">Managing keys needs an admin key.</p> : (
        <>
          <p className="small muted">Give services an <strong>ingest</strong> key: it can send traces but cannot read anything back. Dashboards and CI need <strong>viewer</strong>.</p>
          {keys.data && <Table rows={keys.data} cols={[{ k: "name", h: "Name" }, { k: "role", h: "Role" }, { k: "prefix", h: "Starts with" }, { k: "revoked", h: "Revoked", render: (r) => r.revoked ? "yes" : "no" }]} />}
          <div className="row" style={{ marginTop: "0.75rem" }}>
            <label className="field">Key name<input value={newKey.name} onChange={(e) => setNewKey({ ...newKey, name: e.target.value })} placeholder="checkout-service" /></label>
            <label className="field">Role<select value={newKey.role} onChange={(e) => setNewKey({ ...newKey, role: e.target.value })}>{["ingest", "viewer", "editor", "admin"].map((r) => <option key={r}>{r}</option>)}</select></label>
            <button disabled={!newKey.name} onClick={mkKey}>Create key</button>
          </div>
          {created && <div className="status warning" style={{ marginTop: "0.75rem" }}><span aria-hidden>🔑</span><p><span className="tag">Copy this key now.</span> It will not be shown again: <code>{created}</code></p></div>}
          <h2>Audit log</h2>
          {audit.data && <Table rows={audit.data} cols={[{ k: "at", h: "When", render: (r) => when(r.at) }, { k: "actor", h: "Who" }, { k: "action", h: "What" }, { k: "detail", h: "Details", cls: "clip", render: (r) => JSON.stringify(r.detail) }]} />}
        </>
      )}
    </>
  );
}
