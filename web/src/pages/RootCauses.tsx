import { Link } from "react-router-dom";
import { pct } from "../api";
import { StackedDaily } from "../components/charts";
import { Loading, useConn, useProj } from "../components/ctx";
import { LabelPill, PageHead, Table } from "../components/parts";

export default function RootCauses() {
  const { days } = useConn();
  const { data, error } = useProj(`/root-causes?days=${days}`);
  if (!data) return <Loading error={error} what="root causes" />;
  return (
    <>
      <PageHead title="Root causes" lede="Each failed request is assigned to exactly one stage, with the evidence behind it. Fix the largest one first." />
      {!data.total_failures ? <div className="empty">No diagnosed failures in this window.</div> : (
        <>
          <h2>Failures per day</h2>
          <StackedDaily daily={data.daily} />
          <h2>What each stage means</h2>
          <Table rows={data.breakdown} cols={[
            { k: "label", h: "Stage", render: (r) => <LabelPill label={r.label} /> },
            { k: "count", h: "Failures", num: true },
            { k: "share", h: "Share", num: true, render: (r) => pct(r.share) },
            { k: "fix", h: "What to do" },
            { k: "go", h: "", render: (r) => <Link to={`/traces?label=${r.label}`}>Requests</Link> },
          ]} />
          {data.breakdown.map((b: any) => (
            <section key={b.label}>
              <h3><LabelPill label={b.label} /> clearest examples</h3>
              <Table rows={data.examples[b.label] ?? []} cols={[
                { k: "query", h: "Question", cls: "clip" },
                { k: "confidence", h: "Confidence", num: true, render: (r) => pct(r.confidence) },
                { k: "trace_id", h: "", render: (r) => <Link to={`/traces/${r.trace_id}`}>Inspect</Link> },
              ]} />
            </section>
          ))}
        </>
      )}
    </>
  );
}
