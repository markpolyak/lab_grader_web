import { useCallback, useEffect, useState } from "react";
import { queueRequest } from "../../api";

export function usePolling(path, token = 0) {
  const [data, setData] = useState({ path: null, value: null });
  const [error, setError] = useState(null);
  const [revision, setRevision] = useState(0);
  const refresh = useCallback(() => setRevision(value => value + 1), []);
  useEffect(() => {
    if (!path) return;
    let disposed = false, running = false;
    const controller = new AbortController();
    setError(null);
    async function load() {
      if (running || document.hidden) return;
      running = true;
      try {
        const result = await queueRequest(path, { signal: controller.signal });
        if (!disposed) { setData({ path, value: result }); setError(null); }
      } catch (err) {
        if (!disposed && err.name !== "AbortError") setError(err);
      } finally { running = false; }
    }
    load();
    const timer = setInterval(load, 5000);
    document.addEventListener("visibilitychange", load);
    return () => { disposed = true; controller.abort(); clearInterval(timer); document.removeEventListener("visibilitychange", load); };
  }, [path, revision, token]);
  return { data: data.path === path ? data.value : null, error, refresh };
}
