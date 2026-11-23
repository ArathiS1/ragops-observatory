import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { call, Conn, proj } from "../api";

export const ConnCtx = createContext<{ conn: Conn; setConn: (c: Conn) => void; days: number; setDays: (d: number) => void }>(
  null as any,
);
export const useConn = () => useContext(ConnCtx);

/** GET a project endpoint; re-fetches when the project, window or `deps` change. */
export function useProj<T = any>(path: string | null, deps: unknown[] = []) {
  const { conn } = useConn();
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  useEffect(() => {
    if (!path || !conn.project) return;
    let alive = true;
    setLoading(true);
    call<T>(conn, "GET", proj(conn, path))
      .then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e) => { if (alive) setError(e.message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [conn.project, conn.key, conn.url, path, tick, ...deps]);
  return { data, error, loading, reload };
}

export function Loading({ error, what = "data" }: { error?: string | null; what?: string }) {
  if (error) return <p className="err">Couldn't load {what}: {error}</p>;
  return <p className="muted">Loading {what}…</p>;
}
