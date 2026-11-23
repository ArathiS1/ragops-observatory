import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { LABEL_COLOR, NICE, nice, num, pct, when } from "../api";
import { QualityTimeline } from "../components/charts";
import { Loading, useConn, useProj } from "../components/ctx";
import { PipelineSchematic, StatusSentence } from "../components/parts";
import { cssVar, Donut, fmtNum, KpiCard, PALETTE, Spark } from "../components/viz";

const ms = (x?: number | null) => (x === null || x === undefined ? "–" : x >= 1000 ? `${(x / 1000).toFixed(2)} s` : `${Math.round(x)} ms`);
const usd = (x?: number | null) => (x === null || x === undefined ? "–" : x < 1 ? `$${x.toFixed(4)}` : `$${x.toFixed(2)}`);
const ICON: Record<string, string> = { query: "?", retrieve: "⌕", rerank: "⇅", generate: "✦", response: "✓" };

export default function Overview() {
  const { days } = useConn();
  const nav = useNavigate();
  const [env, setEnv] = useState("");
  const q = `days=${days}${env ? `&environment=${env}` : ""}`;
  const ck = useProj(`/cockpit?${q}`);
  const ov = useProj(`/overview?days=${days}`);
  const rc = useProj(`/root-causes?days=${days}`);
  const tl = useProj(`/timeline?days=${Math.max(days, 14)}`);
  if (!ck.data || !ov.data) return <Loading error={ck.error || ov.error} what="the overview" />;
  const c = ck.data, o = ov.data, k = c.cards;
  const pal = PALETTE();
  const top = rc.data?.breakdown?.[0];
  const hit = c.hit_at_k;
  const fbTotal = c.feedback.positive + c.feedback.neutral + c.feedback.negative;
  return (
    <>
      <div className="box-head" style={{ alignItems: "flex-end" }}>
        <div><h1>Overview</h1><p className="lede" style={{ margin: 0 }}>Is the RAG system healthy, and if not, which part of the pipeline is breaking?</p></div>
        <label className="field" style={{ minWidth: 180 }}>Environment
          <select value={env} onChange={(e) => setEnv(e.target.value)}>
            <option value="">All environments</option>
            {Object.entries(c.environments).map(([e, n]) => <option key={e} value={e}>{e} ({n as number})</option>)}
          </select></label>
      </div>
      <div style={{ marginTop: "1rem" }}><StatusSentence {...o.health} /></div>

      <div className="cards">
        <KpiCard k="Questions" v={fmtNum(k.requests.value ?? 0)} cur={k.requests.value} prev={k.requests.previous} series={k.requests.series} color={pal[0]} />
        <KpiCard k="Correct answers" v={pct(k.success_rate.value, 1)} cur={k.success_rate.value} prev={k.success_rate.previous} series={k.success_rate.series} asPoints color={pal[1]} />
        <KpiCard k="p95 response time" v={ms(k.latency_p95_ms.value)} cur={k.latency_p95_ms.value} prev={k.latency_p95_ms.previous} series={k.latency_p95_ms.series} goodUp={false} color={pal[3]} />
        <KpiCard k="Tokens" v={k.tokens.value == null ? "–" : fmtNum(k.tokens.value)} cur={k.tokens.value} prev={k.tokens.previous} series={k.tokens.series} goodUp={false} color={pal[4]} />
        <KpiCard k="Positive feedback" v={pct(k.positive_feedback.value)} cur={k.positive_feedback.value} prev={k.positive_feedback.previous} series={k.positive_feedback.series} asPoints color={pal[1]} />
        <KpiCard k="Cost (USD)" v={usd(k.cost_usd.value)} cur={k.cost_usd.value} prev={k.cost_usd.previous} series={k.cost_usd.series} goodUp={false} color={pal[2]} />
      </div>

      <div className="board">
        <section className="box span-7">
          <div className="box-head"><h2>RAG pipeline</h2><Link className="small" to="/traces">Open traces</Link></div>
          <div className="flow">
            {c.flow.map((s: any, i: number) => (
              <div key={s.stage} className="flowstep" role="button" tabIndex={0}
                   onClick={() => nav(s.stage === "response" ? "/traces?failed=true" : "/traces")}
                   onKeyDown={(e) => { if (e.key === "Enter") nav("/traces"); }}>
                <div className="dot" style={{ background: pal[i % pal.length] }} aria-hidden>{ICON[s.stage]}</div>
                <div className="s">{s.name}</div>
                <div className="n">{fmtNum(s.count)}</div>
                <div className="s">{s.share != null ? pct(s.share, 1) : "–"}{s.note ? ` ${s.note}` : ""}</div>
              </div>
            ))}
          </div>
          <table className="tbl" style={{ marginTop: "0.9rem" }}>
            <thead><tr><th>Stage</th><th>Reached</th><th className="num">p50</th><th className="num">p95</th></tr></thead>
            <tbody>
              {c.flow.filter((s: any) => s.stage !== "query").map((s: any, i: number) => (
                <tr key={s.stage}>
                  <td>{s.stage === "response" ? "Whole request" : s.name}</td>
                  <td style={{ width: "42%" }}><div className="bar"><i style={{ width: `${(s.share ?? 0) * 100}%`, background: pal[(i + 1) % pal.length] }} /></div></td>
                  <td className="num">{ms(s.p50_ms)}</td><td className="num">{ms(s.p95_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {c.flow.slice(1, 4).every((s: any) => s.p50_ms == null) && <p className="muted small" style={{ marginTop: 8 }}>Stage timings appear when your app uses the SDK's <code>with t.retrieve():</code> / <code>t.generate()</code> blocks.</p>}
        </section>

        <section className="box span-5">
          <div className="box-head"><h2>Retrieval: Hit@K</h2><Link className="small" to="/root-causes">Why it misses</Link></div>
          {hit.labelled ? (
            <div className="donutwrap">
              <Donut center={pct(hit.top_3, 1)} sub="Top-3 hit rate" parts={[
                { name: "found in top 3", value: Math.round((hit.top_3 ?? 0) * hit.labelled), color: pal[1] },
                { name: "found at 4–10", value: Math.round(((hit.top_10 ?? 0) - (hit.top_3 ?? 0)) * hit.labelled), color: pal[4] },
                { name: "not found", value: hit.labelled - Math.round((hit.top_10 ?? 0) * hit.labelled), color: cssVar("--bad") }]} />
              <div className="kv">
                {[1, 3, 5, 10].map((n, i) => [
                  <i key={`a${n}`} className="swatch" style={{ background: pal[i] }} />, <span key={`b${n}`}>Top-{n}</span>,
                  <span key={`c${n}`} className="num">{pct(hit[`top_${n}`], 1)}</span>])}
                <span /><span className="muted small">on {hit.labelled} labelled questions</span><span />
              </div>
            </div>
          ) : <div className="empty">Send <code>expected_doc_ids</code> with traces (or run a labelled test set) to measure Hit@K.</div>}
          {Object.keys(c.quality).length > 0 && (
            <div className="board" style={{ marginTop: "0.9rem", marginBottom: 0 }}>
              {["context_relevance", "context_recall"].filter((m) => c.quality[m]).map((m, i) => (
                <div key={m} className="span-6"><div className="k small muted">{nice(m)}</div>
                  <div style={{ fontWeight: 600, fontSize: "1.15rem" }}>{num(c.quality[m].value)}</div>
                  <Spark data={c.quality[m].series} color={pal[(i + 2) % pal.length]} /></div>
              ))}
            </div>
          )}
        </section>
      </div>

      <div className="board">
        <section className="box span-4">
          <div className="box-head"><h2>Where failures happen</h2><Link className="small" to="/patterns">Failure intelligence</Link></div>
          {Object.keys(c.failures_by_stage).length ? (
            <div className="donutwrap">
              <Donut center={String(Object.values(c.failures_by_stage).reduce((a: number, b: any) => a + b, 0))} sub="failures"
                     parts={Object.entries(c.failures_by_stage).map(([l, n]) => ({ name: nice(l), value: n as number, color: LABEL_COLOR[l] ?? cssVar("--faint") }))} />
              <div className="kv">
                {Object.entries(c.failures_by_stage).slice(0, 6).map(([l, n]) => [
                  <i key={`a${l}`} className="swatch" style={{ background: LABEL_COLOR[l] ?? cssVar("--faint") }} />,
                  <Link key={`b${l}`} to={`/traces?label=${l}`}>{nice(l)}</Link>, <span key={`c${l}`} className="num">{n as number}</span>])}
              </div>
            </div>
          ) : <div className="empty">No failures in this window.</div>}
        </section>
        <section className="box span-4">
          <div className="box-head"><h2>Answer quality</h2><Link className="small" to="/calibration">Check the judge</Link></div>
          <div className="board" style={{ marginBottom: 0 }}>
            {["faithfulness", "answer_relevance", "answer_correctness", "context_relevance"].filter((m) => c.quality[m]).slice(0, 4).map((m, i) => (
              <div key={m} className="span-6">
                <div className="small muted">{nice(m)}</div>
                <div style={{ fontWeight: 600, fontSize: "1.1rem" }}>{num(c.quality[m].value)}<span className="muted small"> / 1.00</span></div>
                <Spark data={c.quality[m].series} color={pal[i % pal.length]} />
              </div>
            ))}
            {!Object.keys(c.quality).length && <div className="empty span-12">No evaluated requests yet.</div>}
          </div>
        </section>
        <section className="box span-4">
          <div className="box-head"><h2>User feedback</h2><span className="small muted">{c.feedback.none} without feedback</span></div>
          {fbTotal ? (
            <div className="donutwrap">
              <Donut center={String(fbTotal)} sub="ratings" parts={[
                { name: "Positive", value: c.feedback.positive, color: cssVar("--good") },
                { name: "Neutral", value: c.feedback.neutral, color: cssVar("--warn") },
                { name: "Negative", value: c.feedback.negative, color: cssVar("--bad") }]} />
              <div className="kv">
                {[["Positive", "--good", c.feedback.positive], ["Neutral", "--warn", c.feedback.neutral], ["Negative", "--bad", c.feedback.negative]].map(([n, v, x]) => [
                  <i key={`a${n}`} className="swatch" style={{ background: cssVar(v as string) }} />, <span key={`b${n}`}>{n}</span>,
                  <span key={`c${n}`} className="num">{pct((x as number) / fbTotal)} ({x})</span>])}
              </div>
            </div>
          ) : <div className="empty">No ratings yet: send <code>client.feedback(trace_id, 1 or -1)</code>.</div>}
        </section>
      </div>

      <div className="board">
        <section className="box span-8">
          <div className="box-head"><h2>Where the pipeline breaks</h2></div>
          <PipelineSchematic counts={o.root_causes} />
          {top && (
            <div className="fixfirst">
              <strong>Fix first: {NICE[top.label]}</strong> ({pct(top.share)} of failures). {top.fix}{" "}
              <Link to={`/traces?label=${top.label}`}>See these requests</Link>
            </div>
          )}
        </section>
        <section className="box span-4">
          <div className="box-head"><h2>Recent alerts</h2><Link className="small" to="/slos">All alerts</Link></div>
          {!c.alerts.length && <div className="empty">No alerts. <Link to="/slos">Define an SLO</Link> to get them.</div>}
          <ul className="plain">
            {c.alerts.map((a: any) => (
              <li key={a.id}>
                <span className={`pill ${a.severity === "critical" ? "bad" : a.severity === "warning" ? "warn" : "ok"}`}>{a.severity}</span>{" "}
                <strong className="small">{a.title.replace(/^\[RAGOps\]\s*/, "")}</strong>
                <div className="muted small">{when(a.at)}</div>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <div className="board">
        <section className="box span-8">
          <div className="box-head"><h2>Quality over time</h2><span className="small muted">dashed lines: deploys and document changes</span></div>
          {tl.data ? <QualityTimeline quality={tl.data.quality} events={tl.data.events} /> : <Loading error={tl.error} what="the timeline" />}
        </section>
        <section className="box span-4">
          <div className="box-head"><h2>Service-level objectives</h2><Link className="small" to="/slos">Manage</Link></div>
          {!o.slos.length && <div className="empty">No SLOs yet. <Link to="/slos">Define one</Link> to get error budgets and alerts.</div>}
          {o.slos.map((s: any) => {
            const tone = s.status === "breached" ? "bad" : s.status === "at_risk" ? "warn" : "good";
            return (
              <div className="slo" key={s.id}>
                <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
                  <strong>{s.name}</strong>
                  <span className={`pill ${tone === "good" ? "ok" : tone}`}>
                    {{ ok: "✓ Met", at_risk: "⚠ At risk", breached: "⛔ Breached", no_data: "Waiting for data" }[s.status as string]}
                  </span>
                </div>
                <div className="small muted">{NICE[s.metric]} ≥ {s.threshold} on {pct(s.target)} of requests over {s.window_days} days</div>
                {s.n > 0 && (<>
                  <div className={`bar ${tone}`}><i style={{ width: `${Math.max(0, Math.min(1, s.budget_remaining ?? 0)) * 100}%` }} /></div>
                  <div className="small muted">Error budget left {pct(s.budget_remaining)} · burn {num(s.burn_rate_24h, 1)}×</div>
                </>)}
              </div>
            );
          })}
          {c.versions.length > 0 && (<>
            <h3>Traffic by version</h3>
            <div className="kv">{c.versions.map((v: any, i: number) => [
              <i key={`a${i}`} className="swatch" style={{ background: pal[i % pal.length] }} />, <span key={`b${i}`}>{v.label}</span>,
              <span key={`c${i}`} className="num">{v.requests}</span>])}</div>
          </>)}
        </section>
      </div>
    </>
  );
}
