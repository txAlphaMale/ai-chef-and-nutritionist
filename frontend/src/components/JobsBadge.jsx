import { useEffect, useState } from "react";
import { api } from "../api";

const POLL_INTERVAL_MS = 2000;

/** Backlog B11.1 (2026-08-01) -- a persistent, app-wide "something is
 * happening in the background" indicator, mounted in App.jsx outside
 * <Routes> (same placement as ChatWidget/ExpiringDigestBanner) so it's
 * visible from every page regardless of which one enqueued the job.
 * Architecturally mirrors the Fiduciary project's own header job badge
 * (poll /api/jobs, show running + queued depth + a rough ETA from
 * historical duration) -- this is exactly the "visual reference that
 * it's working on something" the author's bug report asked for, and it
 * doesn't depend on any single page still being mounted to show it. */
export default function JobsBadge() {
  const [snapshot, setSnapshot] = useState(null);

  useEffect(() => {
    let alive = true;
    async function tick() {
      try {
        const data = await api.get("/jobs");
        if (alive) setSnapshot(data);
      } catch {
        // Non-fatal -- worst case the badge just doesn't update this cycle.
      }
    }
    tick();
    const iv = setInterval(tick, POLL_INTERVAL_MS);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, []);

  if (!snapshot || (!snapshot.running && !snapshot.queued)) return null;

  const { running, queued, progress } = snapshot;
  let etaText = "";
  if (progress) {
    const elapsed = Math.round(progress.elapsed_seconds);
    etaText =
      progress.typical_seconds != null
        ? ` (${elapsed}s of ~${Math.round(progress.typical_seconds)}s typical${
            progress.pct_of_typical != null ? `, ${progress.pct_of_typical}%` : ""
          })`
        : ` (${elapsed}s, no history yet)`;
  }

  return (
    <div className={`jobs-badge${progress && progress.over_typical ? " jobs-badge-over" : ""}`} role="status">
      <span className="jobs-badge-spinner" aria-hidden="true" />
      <span>
        {running ? running.label : "Working..."}
        {etaText}
        {queued > 0 && ` · ${queued} more queued`}
      </span>
    </div>
  );
}
