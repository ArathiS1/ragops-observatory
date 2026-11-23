import { useState } from "react";
import { Link } from "react-router-dom";
import { LABEL_COLOR, pct, when } from "../api";
import { Loading, useConn, useProj } from "../components/ctx";
import { LabelPill, PageHead } from "../components/parts";
import { cssVar, Spark } from "../components/viz";

const TREND: Record<string, string> = { new: "New", growing: "Growing", steady: "Steady", shrinking: "Shrinking" };

export default function Patterns() {
  const { days } = useConn();
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState<number | null>(0);
  const { data, error } = useProj(`/failure-intelligence?days=${Math.max(days, 3)}`);
  const inc = useProj(`/incident?days=${Math.min(days, 7)}`);
  if (!data) return <Loading error={error} what="failure intelligence" />;
  const shown = data.patterns.filter((p: any) => filter === "all" || p.trend === filter);
  return (
    <>
      <PageHead title="Failure intelligence" lede="Recurring problems, how they are trending, their likely root cause and the evidence. Fix a pattern once instead of reading requests one by one." />
      {inc.data && (inc.data.signals.length > 0 || inc.data.affected_trace_ids.length > 0) && (
        <div className="box" style={{ marginBottom: "1rem" }}>
          <div className="box-head"><h2>What changed recently</h2><span className="small muted">last {Math.min(days, 7)} day(s) vs the week before</span></div>
          <p style={{ marginBottom: 6 }}>{inc.data.signals.length ? inc.data.signals.join("; ") + "." : "No metric moved noticeably."}</p>
          <ol style={{ margin: 0 }}>{inc.data.next_checks.map((s: string) => <li key={s} className="small">{s}</li>)}</ol>
        </div>
      )}
      <div className="chips" role="group" aria-label="Filter by trend">
        {["all", "new", "growing", "steady", "shrinking"].map((t) => (
          <button key={t} className={`chip ${filter === t ? "on" : ""}`} onClick={() => setFilter(t)}>
            {t === "all" ? "All patterns" : TREND[t]}<span className="c">{t === "all" ? data.patterns.length : data.trends[t] ?? 0}</span>
          </button>
        ))}
      </div>
      {!shown.length ? <div className="empty">No failure patterns {filter === "all" ? "in this window" : `that are ${TREND[filter].toLowerCase()}`}.</div> : shown.map((p: any, i: number) => (
        <div key={`${p.stage}${p.topic}${i}`} className="pattern">
          <button className="pattern-head" aria-expanded={open === i} onClick={() => setOpen(open === i ? null : i)}>
            <span><LabelPill label={p.stage} /></span>
            <span><strong>{p.topic}</strong> <span className={`trend ${p.trend}`}>{TREND[p.trend]}</span>
              <div className="muted small clip">{p.likely_cause} e.g. “{p.examples[0]}”</div></span>
            <Spark data={p.series} color={LABEL_COLOR[p.stage] ?? cssVar("--c1")} />
            <span style={{ textAlign: "right" }}><strong>{p.count}</strong><div className="muted small">failures</div></span>
          </button>
          {open === i && (
            <div className="pattern-body">
              <div className="board" style={{ marginTop: "0.8rem", marginBottom: 0 }}>
                <div className="span-6">
                  <h3>Likely root cause</h3>
                  <p>{p.likely_cause} {p.confidence != null && <span className="muted">({pct(p.confidence)} average diagnosis confidence)</span>}</p>
                  <p><strong>Fix:</strong> {p.fix}</p>
                  <h3>Questions in this pattern</h3>
                  <ul>{p.examples.map((q: string) => <li key={q} className="small">{q}</li>)}</ul>
                </div>
                <div className="span-6">
                  <h3>Evidence</h3>
                  <div className="kv">
                    <span /><span className="muted">First seen</span><span className="num">{when(p.first_seen)}</span>
                    <span /><span className="muted">Last seen</span><span className="num">{when(p.last_seen)}</span>
                    <span /><span className="muted">Documents in the prompt</span><span className="num">{p.documents.join(", ") || "–"}</span>
                    <span /><span className="muted">Closest documents in the corpus</span><span className="num">{p.evidence.closest_documents.join(", ") || "–"}</span>
                    {p.evidence.stale_documents.length > 0 && <><span /><span className="muted">Outdated documents</span><span className="num">{p.evidence.stale_documents.join(", ")}</span></>}
                    <span /><span className="muted">Mostly on version</span><span className="num">{p.evidence.mostly_on_version ?? "–"}{p.evidence.version_share ? ` (${pct(p.evidence.version_share)})` : ""}</span>
                  </div>
                  <div className="row" style={{ marginTop: "0.8rem" }}>
                    {p.trace_ids[0] && <Link className="btn primary" to={`/traces/${p.trace_ids[0]}`}>Open the evidence graph</Link>}
                    <Link className="btn" to={`/traces?label=${p.stage}`}>All {p.stage.replace(/_/g, " ")} requests</Link>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      ))}
      <p className="muted small">{data.failures} failed requests in the last {data.window_days} days, grouped by stage and by how similar the questions are. Trend compares the second half of the window with the first.</p>
    </>
  );
}
