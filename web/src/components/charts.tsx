import { Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { LABEL_COLOR, nice } from "../api";
import { cssVar } from "./viz";

const axis = () => ({ stroke: cssVar("--faint"), fontSize: 12, tickLine: false });
const SERIES = ["#2a78d6", "#eb6834", "#1baf7a"];

function Tip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tooltip">
      <div style={{ fontWeight: 500 }}>{label}</div>
      {payload.map((p: any) => (
        <div key={p.dataKey}><i className="swatch" style={{ background: p.color }} /> {nice(p.dataKey)}: {typeof p.value === "number" ? p.value.toFixed(3) : p.value}</div>
      ))}
    </div>
  );
}

/** Quality lines with every config deploy and document change marked, so cause and effect line up. */
export function QualityTimeline({ quality, events, metrics = ["faithfulness", "answer_relevance", "context_relevance"] }: {
  quality: Record<string, { day: string; mean: number }[]>; events: { time: string; kind: string; label: string }[];
  metrics?: string[];
}) {
  const days = new Map<string, any>();
  metrics.forEach((m) => (quality[m] ?? []).forEach((p) => days.set(p.day, { ...(days.get(p.day) ?? { day: p.day }), [m]: p.mean })));
  const data = [...days.values()].sort((a, b) => a.day.localeCompare(b.day));
  const marks = events.filter((e) => e.kind === "config" || /changed|deleted/.test(e.label));
  return (
    <>
      <div className="legend" style={{ marginBottom: 6 }}>
        {metrics.map((m, i) => <span key={m}><i className="swatch" style={{ background: SERIES[i] }} />{nice(m)}</span>)}
        <span><i className="swatch" style={{ background: cssVar("--muted") }} />Config deployed</span>
        <span><i className="swatch" style={{ background: cssVar("--warn") }} />Documents changed</span>
      </div>
      <div className="chart">
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 22, right: 12, left: -18, bottom: 0 }}>
            <CartesianGrid stroke={cssVar("--line-soft")} vertical={false} />
            <XAxis dataKey="day" {...axis()} />
            <YAxis domain={[0, 1]} {...axis()} />
            <Tooltip content={<Tip />} />
            {marks.map((e, i) => (
              <ReferenceLine key={i} x={e.time.slice(0, 10)} stroke={e.kind === "config" ? cssVar("--muted") : cssVar("--warn")} strokeDasharray="3 3"
                             label={{ value: e.kind === "config" ? e.label : "docs changed", position: e.kind === "config" ? "top" : "insideBottomLeft",
                                      fontSize: 11, fill: e.kind === "config" ? cssVar("--muted") : cssVar("--warn") }} />
            ))}
            {metrics.map((m, i) => (
              <Line key={m} type="monotone" dataKey={m} stroke={SERIES[i]} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}

export function StackedDaily({ daily }: { daily: { day: string; label: string; count: number }[] }) {
  const labels = Object.keys(LABEL_COLOR).filter((l) => daily.some((d) => d.label === l));
  const byDay = new Map<string, any>();
  daily.forEach((d) => byDay.set(d.day, { ...(byDay.get(d.day) ?? { day: d.day }), [d.label]: d.count }));
  const data = [...byDay.values()].sort((a, b) => a.day.localeCompare(b.day));
  return (
    <>
      <div className="legend" style={{ marginBottom: 6 }}>
        {labels.map((l) => <span key={l}><i className="swatch" style={{ background: LABEL_COLOR[l] }} />{nice(l)}</span>)}
      </div>
      <div className="chart">
        <ResponsiveContainer>
          <BarChart data={data} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
            <CartesianGrid stroke={cssVar("--line-soft")} vertical={false} />
            <XAxis dataKey="day" {...axis()} />
            <YAxis allowDecimals={false} {...axis()} />
            <Tooltip content={<Tip />} cursor={{ fill: "rgba(42,91,215,0.06)" }} />
            {labels.map((l, i) => (
              <Bar key={l} dataKey={l} stackId="s" fill={LABEL_COLOR[l]} stroke="#fff" strokeWidth={1}
                   radius={i === labels.length - 1 ? [3, 3, 0, 0] : 0} maxBarSize={36} />
            ))}
          </BarChart>
        </ResponsiveContainer>
      </div>
    </>
  );
}

export function MetricLine({ points, change }: { points: { day: string; mean: number }[]; change?: string | null }) {
  return (
    <div style={{ width: "100%", height: 170 }}>
      <ResponsiveContainer>
        <LineChart data={points} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
          <CartesianGrid stroke={cssVar("--line-soft")} vertical={false} />
          <XAxis dataKey="day" {...axis()} />
          <YAxis domain={[0, 1]} {...axis()} />
          <Tooltip content={<Tip />} />
          {change && <ReferenceLine x={change} stroke={cssVar("--bad")} strokeDasharray="3 3" label={{ value: "change", fontSize: 11, position: "top", fill: cssVar("--bad") }} />}
          <Line dataKey="mean" stroke={cssVar("--c1")} strokeWidth={2} dot={{ r: 3 }} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
