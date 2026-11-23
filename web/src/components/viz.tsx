import { useMemo, useState } from "react";
import { Area, AreaChart, Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";

/** Read a theme colour (CSS variable) so charts follow the light / dark theme. */
export const cssVar = (name: string, fallback = "#888") => {
  try { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback; } catch { return fallback; }
};
export const PALETTE = () => ["--c1", "--c3", "--c2", "--c4", "--c5"].map((v) => cssVar(v));

export function Spark({ data, color, kind = "area" }: { data: { t: string; v: number | null }[]; color?: string; kind?: "area" }) {
  const c = color ?? cssVar("--c1");
  const id = useMemo(() => `g${Math.random().toString(36).slice(2)}`, []);
  const pts = data.map((d) => ({ t: d.t, v: d.v ?? undefined }));
  if (!pts.some((p) => p.v !== undefined)) return <div className="spark" />;
  return (
    <div className="spark" aria-hidden={kind !== "area"}>
      <ResponsiveContainer>
        <AreaChart data={pts} margin={{ top: 4, right: 4, left: 4, bottom: 0 }}>
          <defs><linearGradient id={id} x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor={c} stopOpacity={0.35} /><stop offset="100%" stopColor={c} stopOpacity={0} /></linearGradient></defs>
          <Tooltip cursor={false} content={({ active, payload }: any) => active && payload?.length && payload[0].value !== undefined
            ? <div className="tooltip">{payload[0].payload.t.replace("T", " ")}: <b>{fmtNum(payload[0].value)}</b></div> : null} />
          <Area type="monotone" dataKey="v" stroke={c} strokeWidth={1.6} fill={`url(#${id})`} connectNulls isAnimationActive={false} dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

export const fmtNum = (x: number) => (Math.abs(x) >= 1e6 ? `${(x / 1e6).toFixed(2)}M` : Math.abs(x) >= 1e4 ? `${(x / 1e3).toFixed(1)}k`
  : Number.isInteger(x) ? x.toLocaleString() : x.toFixed(Math.abs(x) < 1 ? 3 : 1));

/** "↑ 12% vs previous" where up is good or bad depending on the metric. */
export function Delta({ cur, prev, goodUp = true, asPoints = false }: { cur?: number | null; prev?: number | null; goodUp?: boolean; asPoints?: boolean }) {
  if (cur === null || cur === undefined) return <div className="d">not reported</div>;
  if (prev === null || prev === undefined || (prev === 0 && cur !== 0)) return <div className="d">no data in the previous period</div>;
  const diff = asPoints ? (cur - prev) * 100 : prev === 0 ? 0 : ((cur - prev) / Math.abs(prev)) * 100;
  if (Math.abs(diff) < 0.05) return <div className="d">no change vs previous</div>;
  const up = diff > 0;
  return <div className={`d ${up === goodUp ? "up" : "down"}`}>{up ? "↑" : "↓"} {Math.abs(diff).toFixed(1)}{asPoints ? " pts" : "%"} vs previous</div>;
}

export function KpiCard({ k, v, cur, prev, series, goodUp = true, asPoints = false, color }: {
  k: string; v: string; cur?: number | null; prev?: number | null; series: { t: string; v: number | null }[];
  goodUp?: boolean; asPoints?: boolean; color?: string;
}) {
  return (
    <div className="card">
      <div className="k">{k}</div>
      <div className="v">{v}</div>
      <Delta cur={cur} prev={prev} goodUp={goodUp} asPoints={asPoints} />
      <Spark data={series} color={color} />
    </div>
  );
}

export function Donut({ parts, center, sub, size = 150 }: { parts: { name: string; value: number; color: string }[]; center: string; sub?: string; size?: number }) {
  const data = parts.filter((p) => p.value > 0);
  return (
    <div className="donut" style={{ width: size, height: size }}>
      <ResponsiveContainer>
        <PieChart>
          <Pie data={data.length ? data : [{ name: "none", value: 1, color: cssVar("--line") }]} dataKey="value" innerRadius="72%" outerRadius="100%"
               paddingAngle={data.length > 1 ? 2 : 0} stroke="none" isAnimationActive={false}>
            {(data.length ? data : [{ color: cssVar("--line") }]).map((p: any, i) => <Cell key={i} fill={p.color} />)}
          </Pie>
          {data.length > 0 && <Tooltip content={({ active, payload }: any) => active && payload?.length
            ? <div className="tooltip">{payload[0].name}: <b>{payload[0].value}</b></div> : null} />}
        </PieChart>
      </ResponsiveContainer>
      <div className="center"><div><b>{center}</b>{sub && <span>{sub}</span>}</div></div>
    </div>
  );
}

/** Span waterfall from the per-stage timings the SDK records. */
export function Waterfall({ steps, total }: { steps: Record<string, number>; total?: number | null }) {
  const order = ["retrieve", "rerank", "context", "generate"].filter((s) => steps[s] !== undefined);
  const extra = Object.keys(steps).filter((s) => !order.includes(s));
  const all = [...order, ...extra];
  const sum = all.reduce((a, s) => a + (steps[s] || 0), 0);
  const end = Math.max(total ?? 0, sum, 1);
  const names: Record<string, string> = { retrieve: "Retrieval", rerank: "Reranking", context: "Prompt building", generate: "Generation" };
  const colors = PALETTE();
  let at = 0;
  return (
    <div className="wf" role="table" aria-label="Time spent per stage">
      <span className="muted">Request</span><div className="track"><i style={{ left: 0, width: "100%", background: cssVar("--faint") }} /></div><span className="ms">{Math.round(end)} ms</span>
      {all.map((s, i) => {
        const left = (at / end) * 100, w = Math.max(0.8, ((steps[s] || 0) / end) * 100);
        at += steps[s] || 0;
        return [
          <span key={`${s}n`}>{names[s] ?? s}</span>,
          <div key={`${s}t`} className="track" title={`${s}: ${steps[s]} ms`}><i style={{ left: `${left}%`, width: `${w}%`, background: colors[i % colors.length] }} /></div>,
          <span key={`${s}m`} className="ms">{Math.round(steps[s])} ms</span>,
        ];
      })}
    </div>
  );
}

const VERDICT_COLOR = () => ({ supported: cssVar("--good"), contradicted: cssVar("--bad"), unsupported: cssVar("--warn"), contains: cssVar("--line") });

/** Evidence graph: documents → chunks → claims. Hover a node to light up its path; click to read it. */
export function EvidenceGraph({ g }: { g: any }) {
  const [hover, setHover] = useState<string | null>(null);
  const [sel, setSel] = useState<any>(null);
  const cols = { document: g.nodes.filter((n: any) => n.kind === "document"), chunk: g.nodes.filter((n: any) => n.kind === "chunk"),
                 claim: g.nodes.filter((n: any) => n.kind === "claim") };
  const W = 940, colX = { document: 10, chunk: 300, claim: 600 }, colW = { document: 220, chunk: 230, claim: 330 }, rowH = 58, top = 34;
  const height = Math.max(...Object.values(cols).map((c: any) => c.length), 1) * rowH + top + 10;
  const pos: Record<string, { x: number; y: number; w: number }> = {};
  (Object.keys(cols) as (keyof typeof cols)[]).forEach((k) => {
    const list = cols[k];
    const offset = (height - top - list.length * rowH) / 2;
    list.forEach((n: any, i: number) => { pos[n.id] = { x: colX[k], y: top + offset + i * rowH, w: colW[k] }; });
  });
  const linked = useMemo(() => {
    if (!hover) return null;
    const s = new Set([hover]);
    let grew = true;
    while (grew) {
      grew = false;
      for (const e of g.edges) {
        if (s.has(e.source) && !s.has(e.target) && (hover.startsWith("doc") || hover.startsWith("chunk") && e.source === hover)) { s.add(e.target); grew = true; }
        if (s.has(e.target) && !s.has(e.source) && (hover.startsWith("claim") || hover.startsWith("chunk") && e.target === hover)) { s.add(e.source); grew = true; }
      }
    }
    return s;
  }, [hover, g.edges]);
  const colorOf = VERDICT_COLOR();
  const short = (t: string, n: number) => (t.length > n ? `${t.slice(0, n - 1)}…` : t);
  const badge: Record<string, string> = { supported: "✓ supported", contradicted: "✕ contradicted", unsupported: "? no source found" };
  return (
    <>
      <div className="legend" style={{ marginBottom: 6 }}>
        <span><i className="swatch" style={{ background: colorOf.supported }} />supports the claim</span>
        <span><i className="swatch" style={{ background: colorOf.contradicted }} />contradicts it</span>
        <span><i className="swatch" style={{ background: colorOf.unsupported }} />claim with no source</span>
        <span><i className="swatch" style={{ background: cssVar("--warn") }} />outdated version</span>
      </div>
      <div className="egraph">
        <svg width="100%" style={{ minWidth: 640, maxWidth: W }} viewBox={`0 0 ${W} ${height}`} role="img" aria-label={`Evidence graph: ${g.summary.supported} of ${g.summary.claims} claims supported`}>
          <text className="col" x={colX.document} y={16}>DOCUMENTS</text>
          <text className="col" x={colX.chunk} y={16}>PASSAGES SENT TO THE MODEL</text>
          <text className="col" x={colX.claim} y={16}>CLAIMS IN THE ANSWER</text>
          {g.edges.map((e: any, i: number) => {
            const a = pos[e.source], b = pos[e.target];
            if (!a || !b) return null;
            const x1 = a.x + a.w, y1 = a.y + 22, x2 = b.x, y2 = b.y + 22, mx = (x1 + x2) / 2;
            const on = !linked || (linked.has(e.source) && linked.has(e.target));
            return <path key={i} className={on ? "" : "dim"} d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} fill="none"
                         stroke={colorOf[e.kind as keyof ReturnType<typeof VERDICT_COLOR>] ?? colorOf.contains} strokeWidth={e.kind === "contains" ? 1.5 : 2.5} />;
          })}
          {g.nodes.map((n: any) => {
            const p = pos[n.id];
            const dim = linked && !linked.has(n.id);
            const stroke = n.kind === "claim" ? colorOf[n.verdict as keyof ReturnType<typeof VERDICT_COLOR>] : n.stale ? cssVar("--warn") : n.gold ? cssVar("--good") : undefined;
            const title = n.kind === "document" ? n.label : n.kind === "chunk" ? `${n.label}` : n.label;
            const sub = n.kind === "document" ? `${n.chunks} passage${n.chunks > 1 ? "s" : ""}${n.version ? ` · v${n.version}` : ""}${n.stale ? " · outdated" : ""}`
              : n.kind === "chunk" ? short(n.text || "", 34) : badge[n.verdict];
            return (
              <g key={n.id} className={`node ${dim ? "dim" : ""} ${sel?.id === n.id ? "sel" : ""}`} transform={`translate(${p.x},${p.y})`}
                 tabIndex={0} role="button" aria-label={`${n.kind}: ${title}`}
                 onMouseEnter={() => setHover(n.id)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(n.id)} onBlur={() => setHover(null)}
                 onClick={() => setSel(n)} onKeyDown={(ev) => { if (ev.key === "Enter") setSel(n); }}>
                <rect width={p.w} height={46} rx={7} style={stroke ? { stroke, strokeWidth: 1.6 } : undefined} />
                <text x={10} y={19}>{short(title, n.kind === "claim" ? 44 : 30)}</text>
                <text className="sub" x={10} y={36} style={n.kind === "claim" ? { fill: stroke } : undefined}>{sub}</text>
              </g>
            );
          })}
        </svg>
      </div>
      {sel && (
        <div className="inspect">
          <strong>{sel.kind === "document" ? "Document" : sel.kind === "chunk" ? "Passage" : "Claim"}:</strong>{" "}
          {sel.kind === "chunk" ? sel.text : sel.label}
          {sel.kind === "claim" && sel.evidence && <div className="muted small">Source says: “{sel.evidence}”</div>}
          {sel.kind === "claim" && sel.verdict === "unsupported" && <div className="muted small">No passage sent to the model states this: check whether the model invented it or the right passage was not retrieved.</div>}
          {sel.kind === "chunk" && sel.score != null && <div className="muted small">Rank {sel.rank}, retrieval score {Number(sel.score).toFixed(3)}{sel.gold ? ", the passage that should answer this" : ""}</div>}
        </div>
      )}
    </>
  );
}
