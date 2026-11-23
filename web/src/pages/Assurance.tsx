import { useState } from "react";
import { Link } from "react-router-dom";
import { openBlob, pct, proj, when } from "../api";
import { Loading, useConn, useProj } from "../components/ctx";
import { LabelPill, PageHead, Table } from "../components/parts";

const RESULT: Record<string, string> = { pass: "✓ Pass", condition: "Condition", blocking: "⛔ Blocking" };
const VERDICT_LEVEL: Record<string, string> = { Ready: "healthy", "Ready with conditions": "warning", "Not ready": "critical" };

export default function Assurance() {
  const { conn } = useConn();
  const runs = useProj<any[]>("/runs");
  const done = (runs.data ?? []).filter((r) => r.status === "done");
  const [pick, setPick] = useState<string | null>(null);
  const id = pick ?? done[0]?.id ?? null;
  const { data: rep, error } = useProj(id ? `/runs/${id}/assurance` : null, [id]);
  const [msg, setMsg] = useState<string | null>(null);
  if (!runs.data) return <Loading error={runs.error} what="test runs" />;
  return (
    <>
      <PageHead title="Assurance" lede="A verdict you can hand to a client or a reviewer: is this assistant ready, what blocks it, and the evidence behind each answer." />
      {!done.length ? (
        <div className="empty">No finished test runs yet. Run one on <Link to="/test">Test your RAG</Link>, or upload one with <code>ragops test --push</code>. For a new test set, run <code>ragops generate docs/ --security</code>.</div>
      ) : (
        <>
          <div className="row" style={{ marginBottom: "1rem" }}>
            <label className="field">Test run
              <select value={id ?? ""} onChange={(e) => setPick(e.target.value)}>
                {done.map((r) => <option key={r.id} value={r.id}>{r.name} ({r.dataset}, {when(r.created_at)})</option>)}
              </select></label>
            <button disabled={!rep} onClick={() => openBlob(conn, proj(conn, `/runs/${id}/assurance?format=html`)).catch((e) => setMsg(e.message))}>Open printable report</button>
            <button disabled={!rep} onClick={() => {
              const a = document.createElement("a");
              a.href = URL.createObjectURL(new Blob([JSON.stringify(rep, null, 2)], { type: "application/json" }));
              a.download = `assurance-${id}.json`; a.click();
            }}>Download evidence (JSON)</button>
          </div>
          {msg && <p className="err">{msg}</p>}
          {!rep ? <Loading error={error} what="the assessment" /> : (
            <>
              <div className={`status ${VERDICT_LEVEL[rep.verdict] ?? "info"}`} role="status">
                <span aria-hidden>{rep.verdict === "Ready" ? "✅" : rep.verdict === "Not ready" ? "⛔" : "⚠️"}</span>
                <p><span className="tag">{rep.verdict}.</span> {rep.method?.questions} questions, judged by {rep.method?.judge}. Fingerprint <code>{String(rep.fingerprint_sha256).slice(0, 16)}…</code> (check with <code>ragops assure --verify</code>).</p>
              </div>
              <h2>Checks</h2>
              <Table rows={rep.criteria} cols={[
                { k: "criterion", h: "Check" },
                { k: "shown", h: "Result" },
                { k: "result", h: "Status", render: (r) => <span className={`pill ${r.result === "pass" ? "ok" : ""}`}>{RESULT[r.result] ?? r.result}</span> },
                { k: "explain", h: "Why it matters", cls: "clip" },
              ]} />
              {(rep.root_causes ?? []).length > 0 && (<>
                <h2>What to fix</h2>
                <Table rows={rep.root_causes} cols={[
                  { k: "stage", h: "Stage", render: (r: any) => <LabelPill label={r.stage} /> }, { k: "cases", h: "Questions", num: true },
                  { k: "what", h: "What happened" }, { k: "fix", h: "Fix" },
                ]} />
              </>)}
              {(rep.documents_to_write ?? []).length > 0 && (<><h2>Documents to write</h2><ul>{rep.documents_to_write.map((q: string) => <li key={q}>{q}</li>)}</ul></>)}
              {(rep.outdated_documents ?? []).length > 0 && (<><h2>Outdated documents used in answers</h2><ul>{rep.outdated_documents.map((d: string) => <li key={d}>{d}</li>)}</ul></>)}
              {(rep.evidence_mapping ?? []).length > 0 && (<>
                <h2>Evidence for regulations and standards</h2>
                <p className="muted small">Supporting evidence for these requirements, not a certification of compliance.</p>
                <Table rows={rep.evidence_mapping} cols={[{ k: "framework", h: "Framework" }, { k: "requirement", h: "Requirement" }, { k: "evidence", h: "Evidence in this report" }]} />
              </>)}
              <h2>Limits of this assessment</h2>
              <ul>{(rep.limitations ?? []).map((l: string) => <li key={l} className="small">{l}</li>)}</ul>
              <p className="muted small">Pass rate {pct(rep.summary?.pass_rate)} · RAGOps {rep.method?.ragops_version ?? ""}</p>
            </>
          )}
        </>
      )}
    </>
  );
}
