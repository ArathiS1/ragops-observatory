import { nice, pct } from "../api";
import { Loading, useProj } from "../components/ctx";
import { PageHead } from "../components/parts";

export default function Calibration() {
  const { data, error } = useProj<any[]>("/calibration");
  if (!data) return <Loading error={error} what="calibration" />;
  return (
    <>
      <PageHead title="Judge calibration" lede="Automated scores are only useful if they agree with people. Mark a few answers right or wrong on the Traces page and this shows how far to trust the judge." />
      {!data.length ? <div className="empty">No human labels yet. Open any trace and choose “Mark answer correct” or “Mark answer wrong”. Around 30 labels per metric give a useful reading.</div> :
        data.map((r) => (
          <div className="panel" key={r.metric} style={{ marginBottom: "0.75rem" }}>
            <div className="figures">
              <div className="figure"><div className="v">{nice(r.metric)}</div><div className="k">{r.n} labels</div></div>
              <div className="figure"><div className="v">{pct(r.agreement)}</div><div className="k">agreement</div></div>
              <div className="figure"><div className="v">{r.kappa == null ? "–" : r.kappa.toFixed(2)}</div><div className="k">Cohen's κ (chance-corrected)</div></div>
            </div>
            <p style={{ margin: 0 }}><strong>Verdict:</strong> {r.verdict}. The judge passed {r.fp} answers people rejected and failed {r.fn} that people accepted.</p>
          </div>
        ))}
      <h2>How to read κ</h2>
      <p>Above 0.8, the judge can gate releases on its own. Between 0.6 and 0.8 it is good enough for alerts. Between 0.4 and 0.6, use it for trends and review its failures. Below 0.4, change the judge model or prompt before relying on it. Plain agreement can look high when most answers pass, which is why κ corrects for chance.</p>
    </>
  );
}
