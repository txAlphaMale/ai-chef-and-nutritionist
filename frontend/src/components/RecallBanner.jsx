import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

// Backlog B3.3 (2026-08-01): app-shell recall-awareness banner, same
// "outside <Routes>, always mounted" placement as ExpiringDigestBanner
// (App.jsx) so it reaches the household rather than waiting to be
// found on the Inventory page. Deliberately NOT the same component --
// dismissal here is per-alert and persisted server-side (POST
// /recalls/{id}/dismiss), not a client-only "hide until next reload"
// flag, since a food safety alert shouldn't quietly reappear just
// because the browser tab was closed and reopened. See
// recall_service.py's module docstring for what's actually checked
// (USDA FSIS + openFDA, name-substring matching, throttled to roughly
// once a day rather than a true background cron) and its stated
// limitation (a generic inventory item name can miss a
// specific-brand recall).
export default function RecallBanner() {
  const [status, setStatus] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const [busyId, setBusyId] = useState(null);
  const [checking, setChecking] = useState(false);

  async function refresh() {
    try {
      setStatus(await api.get("/inventory/recalls"));
    } catch {
      // Non-fatal -- the banner just doesn't appear rather than
      // breaking page load over a background check.
    }
  }

  useEffect(() => {
    refresh();
     
  }, []);

  async function dismiss(alertId) {
    setBusyId(alertId);
    try {
      setStatus(await api.post(`/inventory/recalls/${alertId}/dismiss`, {}));
    } catch {
      // leave it visible on failure -- silent success only
    } finally {
      setBusyId(null);
    }
  }

  async function checkNow() {
    setChecking(true);
    try {
      // Enqueues via the job queue (see routers/inventory.py's
      // trigger_recall_check) -- this app has no real background
      // scheduler, so this IS the "check on demand" the WIKI/
      // PROJECT-PLAN document as the honest substitute for a true
      // daily cron. JobsBadge (mounted app-wide) shows its progress;
      // re-fetch status a few seconds later since the job itself runs
      // async on the backend, not synchronously with this click.
      await api.post("/inventory/recalls/check", {});
      setTimeout(refresh, 4000);
    } catch {
      // JobsBadge / next natural page load will still reflect reality
    } finally {
      setChecking(false);
    }
  }

  // No active alerts -- say nothing app-wide, same as
  // ExpiringDigestBanner's own "nothing to report, don't add chrome to
  // every page" behavior. The manual "Check for recalls now" control
  // when there's nothing active lives on the Inventory page itself
  // (RecallCheckCard, InventoryPage.jsx) instead -- a page-scoped
  // control, not global chrome, mirrors where Google Calendar's "Force
  // resync" lives (Settings, not app-wide) for the same reason.
  if (!status || status.alerts.length === 0) return null;

  const count = status.alerts.length;

  return (
    <div className="recall-banner no-print">
      <div className="recall-banner-summary">
        <span>
          ⚠ {count} active food recall {count === 1 ? "match" : "matches"} against items in your inventory.{" "}
          <button type="button" className="btn-link" onClick={() => setExpanded((v) => !v)}>
            {expanded ? "Hide details" : "Show details"}
          </button>
        </span>
        <button type="button" className="btn-link" onClick={checkNow} disabled={checking}>
          {checking ? "Checking..." : "Check again"}
        </button>
      </div>
      {expanded && (
        <ul className="recall-banner-list">
          {status.alerts.map((a) => (
            <li key={a.id}>
              <div>
                <strong>{a.matched_item_name}</strong> -- {a.title}
                {a.status && <span className="recall-status-tag"> [{a.status}]</span>}
              </div>
              {a.reason && <div className="hint">{a.reason}</div>}
              {a.recall_date && <div className="hint">Recall date: {a.recall_date}</div>}
              <button
                type="button"
                className="btn-link"
                disabled={busyId === a.id}
                onClick={() => dismiss(a.id)}
              >
                {busyId === a.id ? "Dismissing..." : "Dismiss"}
              </button>
            </li>
          ))}
        </ul>
      )}
      <Link to="/inventory" className="btn-link">
        View inventory
      </Link>
    </div>
  );
}
