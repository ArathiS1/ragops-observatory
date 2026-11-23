import { useEffect, useState } from "react";
import { HashRouter, NavLink, Route, Routes } from "react-router-dom";
import { call, Conn, loadConn, saveConn } from "./api";
import { ConnCtx } from "./components/ctx";
import Assurance from "./pages/Assurance";
import Calibration from "./pages/Calibration";
import Cost from "./pages/Cost";
import Benchmark from "./pages/Benchmark";
import Integrations from "./pages/Integrations";
import Patterns from "./pages/Patterns";
import Release from "./pages/Release";
import Doctor from "./pages/Doctor";
import Org from "./pages/Org";
import Drift from "./pages/Drift";
import Golden from "./pages/Golden";
import Knowledge from "./pages/Knowledge";
import Overview from "./pages/Overview";
import Report from "./pages/Report";
import RootCauses from "./pages/RootCauses";
import Settings from "./pages/Settings";
import Slos from "./pages/Slos";
import TestRag, { RunDetail } from "./pages/TestRag";
import { TraceDetail, TraceList } from "./pages/Traces";

const Mark = () => (
  <svg width="22" height="22" viewBox="0 0 32 32" aria-hidden><circle cx="16" cy="16" r="13" fill="none" stroke="#2a5bd7" strokeWidth="3" /><circle cx="16" cy="16" r="4" fill="#2a5bd7" /></svg>
);

function Login({ onDone }: { onDone: (c: Conn) => void }) {
  const [c, setC] = useState<Conn>({ ...loadConn(), url: loadConn().url || window.location.origin });
  const [err, setErr] = useState<string | null>(null);
  const [sso, setSso] = useState<{ sso: boolean; provider_name?: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const connect = async (conn: Conn) => {
    const ps = await call<any[]>(conn, "GET", "/v1/projects");
    if (!ps.length) { setErr("Signed in, but there are no projects yet. Run `ragops demo` or create one."); return; }
    onDone({ ...conn, project: ps.find((p) => p.name === conn.project)?.name ?? ps[0].name });
  };
  useEffect(() => {
    const base = c.url.replace(/\/$/, "");
    fetch(`${base}/auth/config`).then((r) => r.json()).then(setSso).catch(() => setSso(null));
    // Back from the identity provider: swap the one-time code for a session token.
    const m = window.location.hash.match(/^#\/sso\?(.*)$/);
    if (m) {
      const q = new URLSearchParams(m[1]);
      history.replaceState(null, "", window.location.pathname + "#/");
      if (q.get("error")) { setErr(`Single sign-on failed: ${q.get("error")}`); return; }
      setBusy(true);
      fetch(`${base}/auth/oidc/exchange`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ code: q.get("code") }) })
        .then(async (r) => { const d = await r.json(); if (!r.ok) throw new Error(d.detail ?? `HTTP ${r.status}`); return d; })
        .then((d) => connect({ ...c, key: d.token }))
        .catch((e) => setErr(`Single sign-on failed: ${e.message}`))
        .finally(() => setBusy(false));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const go = async (e: React.FormEvent) => {
    e.preventDefault();
    try { await connect(c); }
    catch (e: any) { setErr(e.status === 401 ? "That key was not accepted." : `Could not reach the API: ${e.message}`); }
  };
  const ssoUrl = `${c.url.replace(/\/$/, "")}/auth/oidc/login?return_to=${encodeURIComponent(window.location.origin + window.location.pathname)}`;
  return (
    <form className="login panel stack" onSubmit={go}>
      <div className="brand" style={{ padding: 0 }}><Mark /> RAGOps Observatory</div>
      {busy && <p className="muted" style={{ margin: 0 }}>Signing you in…</p>}
      {sso?.sso && (
        <div className="sso">
          <a className="btn primary" href={ssoUrl} style={{ textAlign: "center" }}>Sign in with {sso.provider_name || "SSO"}</a>
          <div className="or">or use an API key</div>
        </div>
      )}
      {!sso?.sso && <p className="muted" style={{ margin: 0 }}>Sign in with a project, organisation or instance admin key.</p>}
      <label className="field">API address<input value={c.url} onChange={(e) => setC({ ...c, url: e.target.value })} /></label>
      <label className="field">API key<input type="password" required value={c.key} onChange={(e) => setC({ ...c, key: e.target.value })} placeholder="rgo_…" /></label>
      {err && <p className="err" style={{ margin: 0 }}>{err}</p>}
      <button className={sso?.sso ? "" : "primary"} type="submit">Connect</button>
    </form>
  );
}

const NAV: [string, string][][] = [
  [["/", "Overview"], ["/traces", "Traces"], ["/slos", "SLOs and alerts"], ["/cost", "Cost"]],
  [["/patterns", "Failure intelligence"], ["/root-causes", "Root causes"], ["/doctor", "RAG doctor"], ["/drift", "Drift"], ["/knowledge", "Knowledge health"]],
  [["/test", "Test your RAG"], ["/benchmark", "Benchmark lab"], ["/release", "Release guardian"], ["/golden", "Golden sets"], ["/calibration", "Judge calibration"]],
  [["/assurance", "Assurance"], ["/report", "Report"], ["/integrations", "Integrations and security"], ["/org", "Organisation"], ["/settings", "Settings"]],
];
const GROUPS = ["Observe", "Investigate", "Test and release", "Prove and govern"];

const THEME_KEY = "ragops.theme";
const loadTheme = () => { try { return localStorage.getItem(THEME_KEY) || "dark"; } catch { return "dark"; } };

export default function App() {
  const [theme, setTheme] = useState<string>(loadTheme());
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* storage unavailable */ }
  }, [theme]);
  const [conn, setConnState] = useState<Conn>(loadConn());
  const [days, setDays] = useState(7);
  const [projects, setProjects] = useState<string[]>([]);
  const [ok, setOk] = useState(false);
  const [me, setMe] = useState<any>(null);
  const setConn = (c: Conn) => { saveConn(c); setConnState(c); };
  useEffect(() => {
    if (!conn.key) return;
    call<any[]>(conn, "GET", "/v1/projects").then((ps) => { setProjects(ps.map((p) => p.name)); setOk(true); }).catch(() => setOk(false));
    call(conn, "GET", "/auth/me").then(setMe).catch(() => setMe(null));
  }, [conn.key, conn.url]);
  if (!conn.key || !ok) return <Login onDone={(c) => { setConn(c); setOk(false); }} />;
  return (
    <ConnCtx.Provider value={{ conn, setConn, days, setDays }}>
      <HashRouter>
        <div className="shell">
          <aside className="side">
            <div className="brand"><Mark /> RAGOps Observatory</div>
            <label className="field" style={{ padding: "0 0.5rem" }}>Project
              <select value={conn.project} onChange={(e) => setConn({ ...conn, project: e.target.value })}>{projects.map((p) => <option key={p}>{p}</option>)}</select></label>
            <label className="field" style={{ padding: "0 0.5rem" }}>Time window
              <select value={days} onChange={(e) => setDays(+e.target.value)}>{[1, 3, 7, 14, 30].map((d) => <option key={d} value={d}>Last {d} day{d > 1 ? "s" : ""}</option>)}</select></label>
            <nav aria-label="Main">
              {NAV.map((g, i) => (
                <div key={i} style={{ display: "contents" }}>
                  <div className="group">{GROUPS[i]}</div>
                  {g.map(([to, label]) => <NavLink key={to} to={to} end={to === "/"}>{label}</NavLink>)}
                </div>
              ))}
            </nav>
            <div className="foot">
              <button className="themebtn" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} aria-label="Switch colour theme">
                {theme === "dark" ? "☀ Light theme" : "☾ Dark theme"}</button>
              {me && <div className="who">{me.name} · {me.role}{me.org ? ` · ${me.org}` : ""}</div>}
              <button style={{ width: "100%" }} onClick={() => {
                if (conn.key.startsWith("rgs_")) call(conn, "POST", "/auth/logout").catch(() => undefined);
                setConn({ ...conn, key: "" }); setOk(false);
              }}>Sign out</button>
            </div>
          </aside>
          <main className="wide" key={theme}>
            <Routes>
              <Route path="/" element={<Overview />} />
              <Route path="/doctor" element={<Doctor />} />
              <Route path="/org" element={<Org />} />
              <Route path="/root-causes" element={<RootCauses />} />
              <Route path="/patterns" element={<Patterns />} />
              <Route path="/assurance" element={<Assurance />} />
              <Route path="/cost" element={<Cost />} />
              <Route path="/release" element={<Release />} />
              <Route path="/benchmark" element={<Benchmark />} />
              <Route path="/integrations" element={<Integrations />} />
              <Route path="/traces" element={<TraceList />} />
              <Route path="/traces/:id" element={<TraceDetail />} />
              <Route path="/test" element={<TestRag />} />
              <Route path="/test/:id" element={<RunDetail />} />
              <Route path="/golden" element={<Golden />} />
              <Route path="/calibration" element={<Calibration />} />
              <Route path="/drift" element={<Drift />} />
              <Route path="/knowledge" element={<Knowledge />} />
              <Route path="/slos" element={<Slos />} />
              <Route path="/report" element={<Report />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="*" element={<p>Page not found.</p>} />
            </Routes>
          </main>
        </div>
      </HashRouter>
    </ConnCtx.Provider>
  );
}
