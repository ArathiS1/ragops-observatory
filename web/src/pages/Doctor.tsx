import { Link } from "react-router-dom";
import { pct, when } from "../api";
import { Loading, useConn, useProj } from "../components/ctx";
import { DoctorView } from "../components/Doctor";
import { PageHead } from "../components/parts";

export default function Doctor() {
  const { days } = useConn();
  const { data, error } = useProj(`/doctor?days=${days}`);
  const runs = useProj<any[]>("/runs?limit=10");
  return (
    <>
      <PageHead title="RAG doctor" lede="Is your RAG working? What mistakes does it make, why, and what to change. Live traffic below; open a test run to also try fixes and verify which one helps." />
      {!data ? <Loading error={error} what="the diagnosis" /> : data.cases === 0
        ? <p className="muted">No evaluated traffic in this window yet. Send traces, or test your RAG on a question set.</p>
        : <DoctorView r={data} />}
      <h2>Test runs</h2>
      {runs.data?.length ? (
        <ul className="plain">{runs.data.filter((r) => !r.name.startsWith("doctor trial")).map((r) => (
          <li key={r.id}><Link to={`/test/${r.id}`}>{r.name}</Link> <span className="muted">· pass rate {pct(r.summary?.pass_rate)} · {when(r.created_at)}</span></li>))}
        </ul>
      ) : <p className="muted">No test runs yet. <Link to="/test">Test your RAG</Link> or run <code>ragops doctor</code> in your project.</p>}
    </>
  );
}
