import { pct } from "../api";
import { Loading, useProj } from "../components/ctx";
import { Figure, PageHead, Table } from "../components/parts";

export default function Knowledge() {
  const { data: kb, error } = useProj("/kb-health?days=30");
  const stats = useProj("/corpus/stats");
  if (!kb) return <Loading error={error} what="knowledge-base health" />;
  return (
    <>
      <PageHead title="Knowledge base" lede="A to-do list for whoever owns the docs: what to write, what to fix, and what nobody uses." />
      <div className="figures">
        <Figure v={kb.health_score.toFixed(2)} k="health score (1.0 is best)" />
        <Figure v={stats.data?.documents ?? "–"} k="documents" />
        <Figure v={kb.total_chunks} k="chunks" />
        <Figure v={kb.coverage_gaps.length} k="missing topics" />
        <Figure v={pct(kb.never_retrieved_share)} k="chunks never retrieved" />
      </div>
      <h2>Write these next</h2>
      {!kb.coverage_gaps.length ? <div className="empty">No coverage gaps: every recent question had a matching document.</div> :
        kb.coverage_gaps.map((g: any) => (
          <div className="panel" key={g.topic} style={{ marginBottom: "0.75rem" }}>
            <strong>{g.topic}</strong> <span className="muted">({g.query_count} unanswered questions)</span>
            <p className="small" style={{ margin: "0.3rem 0" }}>{g.suggestion}</p>
            <ul className="small muted" style={{ margin: 0 }}>{g.examples.slice(0, 3).map((e: string) => <li key={e}>{e}</li>)}</ul>
          </div>
        ))}
      <div className="grid2">
        <section>
          <h2>Chunks that keep appearing in bad answers</h2>
          {kb.hot_chunks.length ? <Table rows={kb.hot_chunks} cols={Object.keys(kb.hot_chunks[0]).map((k) => ({ k, h: k.replace(/_/g, " ") }))} />
            : <div className="empty">None in this window.</div>}
        </section>
        <section>
          <h2>Duplicates and contradictions</h2>
          {[...kb.contradictions, ...kb.duplicates].length ? <Table rows={[...kb.contradictions, ...kb.duplicates]}
            cols={Object.keys([...kb.contradictions, ...kb.duplicates][0]).map((k) => ({ k, h: k.replace(/_/g, " "), render: (r: any) => typeof r[k] === "object" ? JSON.stringify(r[k]) : String(r[k]) }))} />
            : <div className="empty">None found.</div>}
        </section>
      </div>
    </>
  );
}
