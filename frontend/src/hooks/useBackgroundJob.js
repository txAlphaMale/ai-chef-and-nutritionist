import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";

const POLL_INTERVAL_MS = 1500;

/** Backlog B11.1 (2026-08-01) -- every AI-consuming endpoint in this app
 * now returns { job_id } immediately (202) instead of blocking the
 * request until Ollama finishes (see job_queue.py's module docstring for
 * why: several endpoints were freezing the ENTIRE app, not just their
 * own request, for the full duration of a vision/receipt/recipe-import
 * call). This hook is the one place that knows how to track one of
 * those jobs to completion.
 *
 * `storageKey` is persisted to localStorage for the lifetime of the job
 * -- this is what makes a page survive being navigated away from and
 * back to (or the whole tab being reloaded): on mount, this hook checks
 * localStorage for a job_id left over from before, and if it finds one,
 * resumes polling it immediately instead of the page coming back with no
 * idea anything was ever in flight (the exact bug reported: uploading a
 * PDF, switching tabs, and coming back to a blank page with no memory of
 * the parse in progress). Cleared once the job reaches a terminal state.
 *
 * Each call site should use a storageKey specific enough that two
 * different in-flight jobs of the same TYPE don't collide (e.g. a chat
 * session id, a recipe id) -- see each component's own usage. */
export function useBackgroundJob(storageKey) {
  const [status, setStatus] = useState(null); // null | "queued" | "running" | "done" | "error"
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  const intervalRef = useRef(null);

  const stopPolling = useCallback(() => {
    if (intervalRef.current) {
      clearInterval(intervalRef.current);
      intervalRef.current = null;
    }
  }, []);

  const clear = useCallback(() => {
    stopPolling();
    localStorage.removeItem(storageKey);
    setStatus(null);
    setResult(null);
    setError(null);
  }, [storageKey, stopPolling]);

  const poll = useCallback(
    (jobId) => {
      stopPolling();
      localStorage.setItem(storageKey, jobId);
      setStatus("queued");
      setResult(null);
      setError(null);

      async function tick() {
        let job;
        try {
          job = await api.get(`/jobs/${jobId}`);
        } catch {
          // A transient network hiccup (or the backend restarting) --
          // keep polling rather than abandoning the job on one failed
          // check. A genuinely gone job_id (404) will keep failing here
          // forever, which is an acceptable, quiet degradation: the
          // busy indicator just stays up until the user navigates away.
          return;
        }
        setStatus(job.status);
        if (job.status === "done") {
          stopPolling();
          localStorage.removeItem(storageKey);
          setResult(job.result);
        } else if (job.status === "error") {
          stopPolling();
          localStorage.removeItem(storageKey);
          setError(job.error || "The background task failed.");
        }
      }

      tick(); // first check immediately, don't wait a full interval
      intervalRef.current = setInterval(tick, POLL_INTERVAL_MS);
    },
    [storageKey, stopPolling]
  );

  // Resume-on-mount: a job_id saved here before this component
  // unmounted (or the page reloaded) means work may still be in
  // progress server-side -- the job queue keeps running regardless of
  // whether anyone's watching, so pick the poll back up rather than
  // silently losing track of it.
  useEffect(() => {
    const existing = localStorage.getItem(storageKey);
    if (existing) poll(existing);
    return stopPolling;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storageKey]);

  const busy = status === "queued" || status === "running";

  return { status, result, error, busy, poll, clear };
}
