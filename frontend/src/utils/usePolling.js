// Calls `callback` every `ms` milliseconds while `enabled` is true.
// The latest callback is always used, so it can close over fresh state.
//
// Usage:
//   const health = useAsync(getDbHealth, []);
//   usePolling(health.reload, 30000);                  // refresh every 30 s
//   usePolling(backups.reload, 2000, jobRunning);      // only while a job runs
import { useEffect, useRef } from 'react';

export function usePolling(callback, ms, enabled = true) {
  const saved = useRef(callback);
  useEffect(() => { saved.current = callback; }, [callback]);

  useEffect(() => {
    if (!enabled || !ms) return undefined;
    const t = setInterval(() => saved.current(), ms);
    return () => clearInterval(t);
  }, [ms, enabled]);
}
