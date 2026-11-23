import type { ReactNode } from "react";
import { LABEL_COLOR, NICE, STAGES, nice, pct } from "../api";

export function PageHead({ title, lede }: { title: string; lede: string }) {
  return (<><h1>{title}</h1><p className="lede">{lede}</p></>);
}

const STATUS_WORD: Record<string, string> = { critical: "Needs attention now", warning: "Watch", healthy: "Healthy", info: "Getting started" };
export function StatusSentence({ level, message }: { level: string; message: string }) {
  const icon = { critical: "⛔", warning: "⚠️", healthy: "✅", info: "ℹ️" }[level] ?? "ℹ️";
  return (
    <div className={`status ${level}`} role="status">
      <span aria-hidden>{icon}</span>
      <p><span className="tag">{STATUS_WORD[level] ?? level}.</span> {message}</p>
    </div>
  );
}

export function Figure({ v, k }: { v: ReactNode; k: string }) {
  return <div className="figure"><div className="v">{v}</div><div className="k">{k}</div></div>;
}

export function Swatch({ label }: { label: string }) {
  return <i className="swatch" style={{ background: LABEL_COLOR[label] ?? "#8a93a3" }} aria-hidden />;
}

export function LabelPill({ label }: { label?: string | null }) {
  if (!label) return <span className="muted">–</span>;
  if (label === "ok") return <span className="pill ok">✓ Passed</span>;
  return <span className="pill"><Swatch label={label} />{nice(label)}</span>;
}

/** The signature view: failures drawn onto the RAG pipeline, stage by stage. */
export function PipelineSchematic({ counts }: { counts: Record<string, number> }) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const per = STAGES.map((s) => ({ ...s, n: s.labels.reduce((a, l) => a + (counts[l] ?? 0), 0) }));
  const max = Math.max(1, ...per.map((s) => s.n));
  return (
    <div className="pipe" role="list" aria-label="Failures by pipeline stage">
      {per.map((s) => {
        const deg = total ? (s.n / total) * 360 : 0;
        const ring = s.labels.reduce<{ stops: string[]; at: number }>((acc, l) => {
          const span = total ? ((counts[l] ?? 0) / total) * 360 : 0;
          if (span > 0) acc.stops.push(`${LABEL_COLOR[l]} ${acc.at}deg ${acc.at + span}deg`);
          acc.at += span;
          return acc;
        }, { stops: [], at: 0 });
        return (
          <div role="listitem" key={s.key} className={`stage ${s.n === max && s.n > 0 ? "hot" : ""}`}>
            <div className="node">
              {deg > 0 && (
                <span className="ring" style={{
                  background: `conic-gradient(${ring.stops.join(",")}, transparent ${ring.at}deg)`,
                  WebkitMask: "radial-gradient(farthest-side, transparent calc(100% - 5px), #000 calc(100% - 4px))",
                  mask: "radial-gradient(farthest-side, transparent calc(100% - 5px), #000 calc(100% - 4px))",
                }} />
              )}
              {s.n}
            </div>
            <div className="sname">{s.name}</div>
            <div className="share">{total ? `${pct(s.n / total)} of failures` : "no failures"}</div>
            {s.labels.length > 1 && (
              <ul>{s.labels.map((l) => <li key={l}><Swatch label={l} />{NICE[l]}<b>{counts[l] ?? 0}</b></li>)}</ul>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function Table({ cols, rows, onRow, selected }: {
  cols: { k: string; h: string; num?: boolean; render?: (r: any) => ReactNode; cls?: string }[];
  rows: any[]; onRow?: (r: any) => void; selected?: (r: any) => boolean;
}) {
  return (
    <div className="wrap">
      <table className="tbl">
        <thead><tr>{cols.map((c) => <th key={c.k} className={c.num ? "num" : ""}>{c.h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.id ?? i} className={`${onRow ? "click" : ""} ${selected?.(r) ? "sel" : ""}`}
                onClick={onRow ? () => onRow(r) : undefined} tabIndex={onRow ? 0 : undefined}
                onKeyDown={onRow ? (e) => { if (e.key === "Enter") onRow(r); } : undefined}>
              {cols.map((c) => (
                <td key={c.k} className={`${c.num ? "num" : ""} ${c.cls ?? ""}`}>{c.render ? c.render(r) : r[c.k] ?? "–"}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
