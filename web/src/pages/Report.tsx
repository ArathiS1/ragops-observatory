import { useEffect, useState } from "react";
import { call, openBlob, proj } from "../api";
import { useConn } from "../components/ctx";
import { PageHead } from "../components/parts";

export default function Report() {
  const { conn, days } = useConn();
  const [html, setHtml] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { call<string>(conn, "GET", proj(conn, `/report?days=${days}&format=html`)).then(setHtml).catch((e) => setErr(e.message)); }, [conn, days]);
  const save = async (fmt: string) => {
    const body = await call<any>(conn, "GET", proj(conn, `/report?days=${days}&format=${fmt}`));
    const blob = new Blob([typeof body === "string" ? body : JSON.stringify(body, null, 2)], { type: fmt === "html" ? "text/html" : "text/markdown" });
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `${conn.project}-reliability.${fmt === "md" ? "md" : fmt}`; a.click();
  };
  return (
    <>
      <PageHead title="Reliability report" lede="A summary to forward to your manager: status, key numbers, top causes, quality changes and missing content." />
      <div className="row" style={{ marginBottom: "1rem" }}>
        <button className="primary" onClick={() => openBlob(conn, proj(conn, `/report?days=${days}&format=pdf`), `${conn.project}-reliability.pdf`).catch((e) => setErr(e.message))}>Download PDF</button>
        <button onClick={() => save("md")}>Download Markdown</button><button onClick={() => save("html")}>Download HTML</button></div>
      {err && <p className="err">{err}</p>}
      {html && <iframe title="Report" srcDoc={html} style={{ width: "100%", height: "75vh", border: "1px solid var(--line)", borderRadius: 8, background: "#fff" }} />}
    </>
  );
}
