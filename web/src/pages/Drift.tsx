import { useState } from "react";
import { nice, openBlob, pct, proj } from "../api";
import { MetricLine } from "../components/charts";
import { Loading, useConn, useProj } from "../components/ctx";
import { PageHead, Table } from "../components/parts";

export default function Drift() {
  const [cur, setCur] = useState(3);
  const [ref, setRef] = useState(7);
  const { data: d, error } = useProj(`/drift?current_days=${cur}&reference_days=${ref}`);
  const tl = useProj(`/timeline?days=${cur + ref + 14}`);
  const { conn } = useConn();
  const [evErr, setEvErr] = useState<string | null>(null);
  const [evBusy, setEvBusy] = useState(false);
  const evidently = () => {
    setEvBusy(true); setEvErr(null);
    openBlob(conn, proj(conn, `/drift/evidently?current_days=${cur}&reference_days=${ref}`))
      .catch((e) => setEvErr(e.status === 501 ? "Evidently is not installed on the server (pip install 'ragops-observatory[evidently]')." : e.message))
      .finally(() => setEvBusy(false));
  };
  return (
    <>
      <PageHead title="Drift" lede="What changed: the questions people ask, how well retrieval works, the documents themselves, and answer quality." />
      <div className="row" style={{ marginBottom: "1.25rem" }}>
        <label className="field">Compare the last<select value={cur} onChange={(e) => setCur(+e.target.value)}>{[1, 2, 3, 5, 7].map((x) => <option key={x} value={x}>{x} days</option>)}</select></label>
        <label className="field">with the previous<select value={ref} onChange={(e) => setRef(+e.target.value)}>{[3, 7, 14, 21].map((x) => <option key={x} value={x}>{x} days</option>)}</select></label>
        <button onClick={evidently} disabled={evBusy} style={{ alignSelf: "flex-end" }}>{evBusy ? "Building report…" : "Open Evidently report"}</button>
      </div>
      {evErr && <p className="err">{evErr}</p>}
      {!d ? <Loading error={error} what="drift" /> : (
        <>
          <h2>Questions {d.query.drift_detected ? <span className="pill bad">changed</span> : <span className="pill ok">stable</span>}</h2>
          <p>{d.query.summary}</p>
          {!!d.query.new_topics?.length && (
            <Table rows={d.query.new_topics} cols={[
              { k: "label", h: "Topic" },
              { k: "share", h: "Share of questions", num: true, render: (r) => pct(r.share) },
              { k: "novelty", h: "How new", num: true, render: (r) => r.novelty.toFixed(2) },
              { k: "covered", h: "Docs cover it", render: (r) => r.covered === false ? <span className="pill bad">No</span> : r.covered ? "Yes" : "–" },
              { k: "examples", h: "Example", cls: "clip", render: (r) => r.examples[0] },
            ]} />
          )}
          <h2>Retrieval {!d.retrieval.drift_detected ? <span className="pill ok">stable</span>
            : (d.retrieval.empty_rate_cur ?? 0) > (d.retrieval.empty_rate_ref ?? 0) || (d.retrieval.topk_overlap != null && d.retrieval.topk_overlap < 0.5)
              || (d.retrieval.top1_score_cur ?? 0) < (d.retrieval.top1_score_ref ?? 0)
              ? <span className="pill bad">degraded</span> : <span className="pill warn">changed (scores up)</span>}</h2>
          <p>{d.retrieval.summary}</p>
          <h2>Documents</h2>
          <p>Changed: {d.corpus.changed?.join(", ") || "none"}. Added: {d.corpus.added?.join(", ") || "none"}. Deleted: {d.corpus.deleted?.join(", ") || "none"}.</p>
          {d.corpus.outdated_trace_count > 0 && <div className="status warning"><span aria-hidden>📄</span><p><span className="tag">{d.corpus.outdated_trace_count} recent answers</span> relied on documents that have changed since. They may now be wrong.</p></div>}
          <h2>Answer quality</h2>
          {d.quality.map((q: any) => (
            <div key={q.metric} style={{ marginBottom: "1rem" }}>
              <p>{q.detected ? (q.direction === "degraded" ? "🔻 " : "🔺 ") : ""}<strong>{nice(q.metric)}</strong>: {q.summary.replace(q.metric, "").trim()}
                {q.likely_causes?.length ? <><br /><span className="muted">Likely cause: {q.likely_causes.join("; ")}</span></> : null}</p>
              {q.detected && tl.data?.quality?.[q.metric] && <MetricLine points={tl.data.quality[q.metric]} change={q.change_date} />}
            </div>
          ))}
        </>
      )}
    </>
  );
}
