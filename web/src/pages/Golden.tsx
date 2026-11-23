import { useState } from "react";
import { nice } from "../api";
import { Loading, useConn, useProj } from "../components/ctx";
import { LabelPill, PageHead, Table } from "../components/parts";

export default function Golden() {
  const { conn } = useConn();
  const sets = useProj<any[]>("/golden-sets");
  const [name, setName] = useState<string | null>(null);
  const cur = name ?? sets.data?.[0]?.name ?? null;
  const g = useProj(cur ? `/golden-sets/${encodeURIComponent(cur)}/export` : null, [cur]);
  if (!sets.data) return <Loading error={sets.error} what="golden sets" />;
  const download = () => {
    const blob = new Blob([JSON.stringify(g.data, null, 2)], { type: "application/json" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `${cur}.json`; a.click();
  };
  return (
    <>
      <PageHead title="Golden sets" lede="Regression tests grown from real production failures. Use them on the Test your RAG page or in CI with ragops gate." />
      {!sets.data.length ? <div className="empty">No golden sets yet. Open a failed trace and choose “Add to golden set”, or turn on automatic promotion in Settings.</div> : (
        <>
          <div className="row" style={{ marginBottom: "1rem" }}>
            <label className="field">Golden set<select value={cur ?? ""} onChange={(e) => setName(e.target.value)}>
              {sets.data.map((s) => <option key={s.name} value={s.name}>{s.name} (version {s.version}, {s.cases} cases)</option>)}</select></label>
            <button onClick={download} disabled={!g.data}>Download JSON</button>
          </div>
          {g.data && <Table rows={g.data.cases} cols={[
            { k: "query", h: "Question", cls: "clip" },
            { k: "expected_doc_ids", h: "Expected documents", render: (r) => r.expected_doc_ids.join(", ") || "–" },
            { k: "expected_answer", h: "Reference answer", cls: "clip", render: (r) => r.expected_answer ? <>{r.expected_answer}{r.expected_answer_source === "extracted" && <span className="pill warn" style={{ marginLeft: 6 }}>draft, please review</span>}</> : "–" },
            { k: "failure_label", h: "Came from", render: (r) => r.failure_label ? <LabelPill label={r.failure_label} /> : "added by hand" },
            { k: "added_in_version", h: "Version", num: true },
          ]} />}
          <h2>Gate releases in CI</h2>
          <div className="code">{`- uses: ArathiS1/ragops-observatory@v0.5
  with:
    pipeline: my_app.rag:pipeline
    baseline: configs/prod.yaml
    candidate: configs/candidate.yaml
    server: \${{ secrets.RAGOPS_URL }}
    api-key: \${{ secrets.RAGOPS_API_KEY }}
    project: ${conn.project}
    golden-set: ${cur}`}</div>
          <p className="small muted" style={{ marginTop: 8 }}>{nice("")}</p>
        </>
      )}
    </>
  );
}
