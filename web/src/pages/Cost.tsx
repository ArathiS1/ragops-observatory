import { useState } from "react";
import { Loading, useConn, useProj } from "../components/ctx";
import { Figure, PageHead, Table } from "../components/parts";

const usd = (x?: number | null) => (x === null || x === undefined ? "–" : x === 0 ? "$0" : x < 0.01 ? `$${x.toFixed(5)}` : `$${x.toFixed(2)}`);

export default function Cost() {
  const { days } = useConn();
  const [env, setEnv] = useState("");
  const envs = useProj<Record<string, number>>(`/environments?days=${days}`);
  const { data, error } = useProj(`/costs?days=${days}${env ? `&environment=${env}` : ""}`);
  if (!data) return <Loading error={error} what="costs" />;
  const cols = [
    { k: "key", h: "" }, { k: "requests", h: "Requests", num: true },
    { k: "cost_usd", h: "Cost", num: true, render: (r: any) => usd(r.cost_usd) },
    { k: "per_request_usd", h: "Per request", num: true, render: (r: any) => usd(r.per_request_usd) },
  ];
  return (
    <>
      <PageHead title="Cost" lede="What answering and checking your users' questions costs, by model, version and environment." />
      <label className="field" style={{ maxWidth: 240, marginBottom: "1rem" }}>Environment
        <select value={env} onChange={(e) => setEnv(e.target.value)}>
          <option value="">All</option>
          {Object.keys(envs.data ?? {}).map((e) => <option key={e} value={e}>{e} ({envs.data![e]})</option>)}
        </select></label>
      <div className="figures">
        <Figure v={data.priced_requests ? usd(data.serving_cost_usd) : "–"} k={data.priced_requests ? "answering (your models)" : "answering: not reported"} />
        <Figure v={usd(data.evaluation_cost_usd)} k="checking (the judge, estimated)" />
        <Figure v={usd(data.per_request_usd)} k="per request" />
        <Figure v={`${data.priced_requests} / ${data.requests}`} k="requests with a known cost" />
      </div>
      {data.priced_requests < data.requests && (
        <p className="muted small">Requests without a cost: send <code>cost_usd</code>, or send the model and token counts and add the model's price to the project setting <code>pricing</code>. Prices are never guessed.</p>
      )}
      {data.estimated_share > 0 && <p className="muted small">{Math.round(data.estimated_share * 100)}% of the serving cost is estimated from token counts with price table <code>{data.pricing_version}</code>.</p>}
      <p className="muted small">Judge cost is an estimate from a default per-token rate (<code>RAGOPS_JUDGE</code> pricing is not read from your provider's bill); check the real amount in your provider's dashboard.</p>
      <div className="grid2">
        <section><h2>By model</h2><Table rows={data.by_model} cols={cols.map((c, i) => i ? c : { ...c, h: "Model" })} /></section>
        <section><h2>By version</h2><Table rows={data.by_version} cols={cols.map((c, i) => i ? c : { ...c, h: "Config version" })} /></section>
      </div>
      <div className="grid2">
        <section><h2>By environment</h2><Table rows={data.by_environment} cols={cols.map((c, i) => i ? c : { ...c, h: "Environment" })} /></section>
        <section><h2>Checking cost by judge</h2><Table rows={data.evaluation_by_judge} cols={[
          { k: "judge", h: "Judge" }, { k: "scores", h: "Scores", num: true },
          { k: "cost_usd", h: "Cost", num: true, render: (r: any) => usd(r.cost_usd) }]} /></section>
      </div>
      <h2>Per day</h2>
      <Table rows={data.daily} cols={cols.map((c, i) => i ? c : { ...c, h: "Day" })} />
      <p className="muted small">Tokens in this window: {data.tokens.input.toLocaleString()} in, {data.tokens.output.toLocaleString()} out.</p>
    </>
  );
}
