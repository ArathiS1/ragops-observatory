import { useEffect, useState } from "react";
import { call, pct } from "../api";
import { useConn } from "../components/ctx";
import { PageHead, Table } from "../components/parts";

const LEVEL: Record<string, string> = { critical: "bad", warning: "warn", healthy: "ok", info: "" };

export default function Org() {
  const { conn, setConn, days } = useConn();
  const [orgs, setOrgs] = useState<any[]>([]);
  const [org, setOrg] = useState<string>("");
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    call<any[]>(conn, "GET", "/v1/orgs").then((o) => { setOrgs(o); setOrg((cur) => cur || o[0]?.name || ""); }).catch((e) => setErr(e.message));
  }, [conn]);
  useEffect(() => {
    if (!org) return;
    call(conn, "GET", `/v1/orgs/${encodeURIComponent(org)}?days=${days}`).then(setData).catch((e) => setErr(e.message));
  }, [conn, org, days]);
  return (
    <>
      <PageHead title="Organisation" lede="Every RAG application in your organisation: traffic, failure rate, main cause and SLO status." />
      {orgs.length > 1 && <label className="field" style={{ maxWidth: 260, marginBottom: "1rem" }}>Organisation
        <select value={org} onChange={(e) => setOrg(e.target.value)}>{orgs.map((o) => <option key={o.id}>{o.name}</option>)}</select></label>}
      {err && <p className="err">{err === "HTTP 403" ? "Your key is scoped to one project; organisation views need an organisation or instance key." : err}</p>}
      {data && (
        <Table rows={data.projects} cols={[
          { k: "name", h: "Application", render: (p) => <button className="link" onClick={() => setConn({ ...conn, project: p.name })}>{p.name}</button> },
          { k: "health", h: "Status", render: (p) => <span className={`pill ${LEVEL[p.health.level] ?? ""}`}>{p.health.level}</span> },
          { k: "traces", h: "Requests", num: true },
          { k: "failure_rate", h: "Failure rate", num: true, render: (p) => pct(p.failure_rate) },
          { k: "top_root_cause", h: "Main cause", render: (p) => p.top_root_cause?.replace(/_/g, " ") ?? "–" },
          { k: "slos_breached", h: "SLOs breached", num: true },
        ]} />
      )}
    </>
  );
}
