import { useState } from "react";
import { nice, pct } from "../api";
import { LabelPill } from "./parts";

const VERDICT: Record<string, { level: string; icon: string }> = {
  healthy: { level: "healthy", icon: "✅" }, "needs work": { level: "warning", icon: "⚠️" }, broken: { level: "critical", icon: "⛔" },
};
/** Render `backticked` spans of a server-written sentence as code. */
const withCode = (s: string) => s.split("`").map((part, i) => (i % 2 ? <code key={i}>{part}</code> : part));
const fmt = (ch: Record<string, unknown>) => Object.entries(ch).map(([k, v]) => `${k}=${String(v)}`).join(", ");

/** Verdict → mistakes by cause (with fixes and real examples) → fixes tested on the pipeline. */
export function DoctorView({ r, onTrial, trialBusy }: { r: any; onTrial?: () => void; trialBusy?: boolean }) {
  const [open, setOpen] = useState<string | null>(r.findings?.[0]?.stage ?? null);
  const v = VERDICT[r.verdict] ?? VERDICT["needs work"];
  return (
    <div className="stack doctor">
      <div className={`status ${v.level}`} role="status">
        <span aria-hidden>{v.icon}</span>
        <p><span className="tag">{r.verdict[0].toUpperCase() + r.verdict.slice(1)}.</span> {r.summary}{" "}
          <span className="muted">{r.cases} questions, pass rate {pct(r.pass_rate)}, {r.failures} mistakes.</span></p>
      </div>

      {r.best_fix && (
        <div className="status healthy"><span aria-hidden>🩺</span>
          <p><span className="tag">Verified fix:</span> set <code>{fmt(r.best_fix.change)}</code> — {r.best_fix.why}.</p></div>
      )}

      {!!r.weak_metrics?.length && (
        <section>
          <h2>Weak spots</h2>
          <ul className="plain">{r.weak_metrics.map((w: any) => (
            <li key={w.metric}><strong>{nice(w.metric)}</strong> {w.score.toFixed(2)} <span className="muted">(target {w.target.toFixed(2)})</span> — {w.hint}</li>))}
          </ul>
        </section>
      )}

      {!!r.findings?.length && (
        <section>
          <h2>Mistakes, by cause</h2>
          <div className="findings">
            {r.findings.map((f: any) => {
              const isOpen = open === f.stage;
              return (
                <div key={f.stage} className={`finding ${isOpen ? "open" : ""}`}>
                  <button className="finding-head" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : f.stage)}>
                    <LabelPill label={f.stage} />
                    <span className="ftitle">{f.title}</span>
                    <span className="fcount">{f.cases} <span className="muted">· {pct(f.share)} of mistakes</span></span>
                  </button>
                  <div className="share" aria-hidden><i style={{ width: `${Math.max(2, f.share * 100)}%` }} /></div>
                  {isOpen && (
                    <div className="finding-body">
                      <p>{f.meaning}</p>
                      <p className="muted"><strong>How to confirm:</strong> {f.confirm}</p>
                      <h3>How to fix it</h3>
                      <ul>{f.fixes.map((x: string) => <li key={x}>{x}</li>)}</ul>
                      {!!f.examples?.length && (<>
                        <h3>Examples from your data</h3>
                        <ul className="examples">{f.examples.map((e: any, i: number) => (
                          <li key={i}><q>{e.query}</q>{e.answer && <div className="muted clip">Answered: {e.answer}</div>}{e.why && <div className="why">{e.why}</div>}</li>))}
                        </ul>
                      </>)}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}

      {(onTrial || r.trials?.length > 0 || r.pending_trials?.length > 0) && (
        <section>
          <h2>Fixes tested on your pipeline</h2>
          {!r.trials?.length && !r.pending_trials?.length && (
            <p className="muted">
              {r.proposed_changes?.length
                ? <>The doctor can re-run this test with {r.proposed_changes.map((c: any) => <code key={fmt(c)} style={{ marginRight: 6 }}>{fmt(c)}</code>)} and keep only changes that verifiably reduce mistakes.</>
                : r.missing_knobs?.length
                  ? <>To test fixes automatically, make your pipeline read {r.missing_knobs.map((k: string) => <code key={k} style={{ marginRight: 6 }}>{k}</code>)} from its config and add them to its default config.</>
                  : "No automatic fixes to try for these causes; follow the steps above."}
            </p>
          )}
          {onTrial && !!r.proposed_changes?.length && !r.trials?.length && !r.pending_trials?.length && (
            <button className="primary" disabled={trialBusy} onClick={onTrial}>{trialBusy ? "Starting…" : "Test these fixes"}</button>
          )}
          {(r.trials?.length > 0 || r.pending_trials?.length > 0) && (
            <div className="wrap"><table className="tbl">
              <thead><tr><th>Change</th><th className="num">Mistakes</th><th className="num">Fixed</th><th className="num">Newly broken</th><th>Metrics</th><th>Result</th></tr></thead>
              <tbody>
                {r.trials?.map((t: any) => (
                  <tr key={fmt(t.change)}>
                    <td><code>{fmt(t.change)}</code></td>
                    <td className="num">{t.failures_before} → {t.failures_after}</td>
                    <td className="num">{t.fixed}</td><td className="num">{t.broken}</td>
                    <td>{[...t.improved_metrics.map((m: string) => `+ ${nice(m)}`), ...t.regressed_metrics.map((m: string) => `− ${nice(m)}`)].join(", ") || <span className="muted">no significant change</span>}</td>
                    <td>{t.verified ? <span className="pill ok">✓ Verified</span> : <span className="pill bad">✗ {t.note || "not better"}</span>}</td>
                  </tr>))}
                {r.pending_trials?.map((t: any) => (
                  <tr key={t.run_id}><td><code>{fmt(t.change)}</code></td><td colSpan={5} className="muted">Running… {t.progress}/{t.total}</td></tr>))}
              </tbody>
            </table></div>
          )}
        </section>
      )}

      {!!r.next_steps?.length && (
        <section><h2>Next steps</h2><ol>{r.next_steps.map((s: string) => <li key={s}>{withCode(s)}</li>)}</ol></section>
      )}
    </div>
  );
}
