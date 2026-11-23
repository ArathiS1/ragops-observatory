import { Loading, useProj } from "../components/ctx";
import { PageHead, Table } from "../components/parts";

const TONE: Record<string, string> = { active: "ok", installed: "ok", fallback: "warn", "not installed": "", off: "", sqlite: "warn", "database queue": "", "rules only": "warn" };

export default function Integrations() {
  const { data, error } = useProj("/integrations");
  if (!data) return <Loading error={error} what="integrations" />;
  const ok = data.security.filter((s: any) => s.ok).length;
  return (
    <>
      <PageHead title="Integrations and security" lede="What is installed and working in this deployment, and how the project's data is protected." />
      <div className="board">
        <section className="box span-7">
          <div className="box-head"><h2>Integrations</h2></div>
          <Table rows={data.integrations} cols={[
            { k: "name", h: "Integration", render: (r) => <strong>{r.name}</strong> },
            { k: "status", h: "Status", render: (r) => <span className={`pill ${TONE[r.status] ?? ""}`}>{r.status}</span> },
            { k: "detail", h: "What it does" },
            { k: "how", h: "Turn on", render: (r) => <code className="small">{r.how}</code> },
          ]} />
        </section>
        <section className="box span-5">
          <div className="box-head"><h2>Security posture</h2><span className="small muted">{ok} of {data.security.length} checks</span></div>
          <ul className="plain">{data.security.map((s: any) => (
            <li key={s.check}><span className="statusdot" style={{ background: s.ok ? "var(--good)" : "var(--warn)" }} aria-hidden />
              <strong className="small">{s.check}</strong><div className="muted small">{s.detail}</div></li>))}</ul>
          <h3>Active API keys by role</h3>
          {Object.keys(data.keys_by_role).length ? (
            <div className="kv">{Object.entries(data.keys_by_role).map(([r, n]) => [<span key={`a${r}`} />, <span key={`b${r}`}>{r}</span>, <span key={`c${r}`} className="num">{n as number}</span>])}</div>
          ) : <p className="muted small">Only the instance admin key is in use.</p>}
          <p className="muted small" style={{ marginTop: 8 }}>Keys, roles and single sign-on are managed on the Organisation and Settings pages; every change is in the audit log.</p>
        </section>
      </div>
    </>
  );
}
