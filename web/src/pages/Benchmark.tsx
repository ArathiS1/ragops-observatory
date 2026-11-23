import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { LabelPill, PageHead } from "../components/parts";
import { nice, num, pct } from "../api";
import { Loading, useProj } from "../components/ctx";
import { cssVar, PALETTE } from "../components/viz";

const QUAL = ["faithfulness", "answer_relevance", "answer_correctness", "context_relevance", "context_recall"];

export default function Benchmark() {
  const runs = useProj<any[]>("/runs");
  const done = useMemo(() => (runs.data ?? []).filter((r) => r.status === "done"), [runs.data]);
  const [pick, setPick] = useState<string[] | null>(null);
  const ids = pick ?? done.slice(0, 3).map((r) => r.id);
  const bm = useProj(ids.length >= 2 ? `/runs/benchmark?ids=${ids.join(",")}` : null, [ids.join(",")]);
  const [onlyDiff, setOnlyDiff] = useState(true);
  if (!runs.data) return <Loading error={runs.error} what="test runs" />;
  const pal = PALETTE();
  const toggle = (id: string) => setPick(ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id].slice(-5));
  const d = bm.data;
  const tick = { stroke: cssVar("--faint"), fontSize: 12 };
  const qualityData = d ? QUAL.filter((m) => d.runs.some((r: any) => r.metrics?.[m] != null)).map((m) => ({ metric: nice(m),
    ...Object.fromEntries(d.runs.map((r: any) => [r.name, r.metrics?.[m] ?? null])) })) : [];
  const opsData = d ? [{ metric: "p50 latency (ms)", ...Object.fromEntries(d.runs.map((r: any) => [r.name, r.latency_p50_ms])) },
                       { metric: "p95 latency (ms)", ...Object.fromEntries(d.runs.map((r: any) => [r.name, r.latency_p95_ms])) }] : [];
  const Tip = ({ active, payload, label }: any) => active && payload?.length ? (
    <div className="tooltip"><b>{label}</b>{payload.map((p: any) => <div key={p.dataKey}><i className="swatch" style={{ background: p.color }} /> {p.dataKey}: {typeof p.value === "number" ? p.value.toFixed(3) : "–"}</div>)}</div>) : null;
  const qs = d ? d.questions.filter((q: any) => !onlyDiff || q.disagree) : [];
  return (
    <>
      <PageHead title="Benchmark lab" lede="Run the same questions through different models, retrievers, rerankers or chunk sizes, then compare quality, speed and failures side by side." />
      {done.length < 2 ? <div className="empty">You need at least two finished runs. Run each configuration on <Link to="/test">Test your RAG</Link> with the same golden set, or push them with <code>ragops test --push</code>.</div> : (<>
        <div className="chips" role="group" aria-label="Runs to compare">
          {done.slice(0, 12).map((r) => (
            <button key={r.id} className={`chip ${ids.includes(r.id) ? "on" : ""}`} onClick={() => toggle(r.id)} title={JSON.stringify(r.config)}>
              {r.name}<span className="c">{pct(r.summary?.pass_rate)}</span></button>))}
        </div>
        <p className="muted small">Pick 2 to 5 runs. Runs are compared on the questions they share.</p>
        {ids.length < 2 ? <div className="empty">Pick at least two runs.</div> : !d ? <Loading error={bm.error} what="the comparison" /> : (<>
          {d.shared_questions === 0 && <div className="status warning"><span aria-hidden>⚠️</span><p><span className="tag">Different questions.</span> The selected runs have no question in common, so no best run is picked. Unselect the run that used a different test set (for example the one with fewer questions), or run each version on the same test set.</p></div>}
          {d.shared_questions > 0 && d.runs.some((r: any) => r.own_cases !== d.shared_questions) && <p className="muted small">Scored on the {d.shared_questions} questions every selected run answered, so the comparison is fair.</p>}
          <div className="cards">
            {d.runs.map((r: any, i: number) => (
              <div key={r.id} className={`card ${r.id === d.best ? "best" : ""}`}>
                <div className="k"><i className="swatch" style={{ background: pal[i % pal.length] }} /> {r.name}{r.id === d.best && <span className="pill ok" style={{ marginLeft: 6 }}>best</span>}</div>
                <div className="v">{pct(r.pass_rate, 1)}</div>
                <div className="d">{d.shared_questions === 0 ? `pass rate on its own ${r.cases} questions (not comparable)`
                  : `pass rate on ${r.cases} shared questions${r.own_cases !== r.cases ? ` (run has ${r.own_cases})` : ""}`}</div>
                <div className="kv small" style={{ marginTop: 6 }}>
                  <span /><span className="muted">Faithfulness</span><span className="num">{num(r.metrics?.faithfulness)}</span>
                  <span /><span className="muted">Hit rate</span><span className="num">{pct(r.hit_rate)}</span>
                  {r.ranking?.mrr != null && <><span /><span className="muted">MRR</span><span className="num">{num(r.ranking.mrr)}</span></>}
                  <span /><span className="muted">p95</span><span className="num">{r.latency_p95_ms != null ? `${Math.round(r.latency_p95_ms)} ms` : "–"}</span>
                  <span /><span className="muted">Errors</span><span className="num">{r.errors}</span>
                </div>
              </div>))}
          </div>
          <div className="board">
            <section className="box span-8">
              <div className="box-head"><h2>Quality by metric</h2></div>
              <div className="chart"><ResponsiveContainer>
                <BarChart data={qualityData} margin={{ top: 8, right: 8, left: -20, bottom: 0 }}>
                  <CartesianGrid stroke={cssVar("--line-soft")} vertical={false} />
                  <XAxis dataKey="metric" {...tick} tickLine={false} /><YAxis domain={[0, 1]} {...tick} tickLine={false} />
                  <Tooltip content={<Tip />} cursor={{ fill: cssVar("--hover") }} /><Legend wrapperStyle={{ fontSize: 12 }} />
                  {d.runs.map((r: any, i: number) => <Bar key={r.id} dataKey={r.name} fill={pal[i % pal.length]} radius={[4, 4, 0, 0]} />)}
                </BarChart></ResponsiveContainer></div>
            </section>
            <section className="box span-4">
              <div className="box-head"><h2>Latency</h2></div>
              <div className="chart"><ResponsiveContainer>
                <BarChart data={opsData} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
                  <CartesianGrid stroke={cssVar("--line-soft")} vertical={false} />
                  <XAxis dataKey="metric" {...tick} tickLine={false} /><YAxis {...tick} tickLine={false} />
                  <Tooltip content={<Tip />} cursor={{ fill: cssVar("--hover") }} />
                  {d.runs.map((r: any, i: number) => <Bar key={r.id} dataKey={r.name} fill={pal[i % pal.length]} radius={[4, 4, 0, 0]} />)}
                </BarChart></ResponsiveContainer></div>
            </section>
          </div>
          <section className="box">
            <div className="box-head"><h2>Question by question</h2>
              <label className="small"><input type="checkbox" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} /> only questions where the runs disagree</label></div>
            {!qs.length ? <div className="empty">The runs agree on every question.</div> : (
              <div className="wrap"><table className="tbl">
                <thead><tr><th>Question</th>{d.runs.map((r: any, i: number) => <th key={r.id}><i className="swatch" style={{ background: pal[i % pal.length] }} /> {r.name}</th>)}</tr></thead>
                <tbody>{qs.slice(0, 150).map((q: any) => (
                  <tr key={q.query}><td className="clip">{q.query}</td>
                    {d.runs.map((r: any) => { const x = q.results[r.id]; return (
                      <td key={r.id}>{!x ? <span className="cell none">—</span> : x.failed
                        ? <span title={nice(x.label)}><span className="cell fail">✕ {x.correctness != null ? num(x.correctness) : "fail"}</span> <LabelPill label={x.label} /></span>
                        : <span className="cell pass">✓ {x.correctness != null ? num(x.correctness) : "pass"}</span>}</td>); })}
                  </tr>))}</tbody>
              </table></div>)}
          </section>
        </>)}
      </>)}
    </>
  );
}
