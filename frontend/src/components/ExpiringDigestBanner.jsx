import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

// Backlog B4.4 (via the B10.2 author-requested group, 2026-08-01): the
// REQUIRED-minimum "in-app banner" piece -- mounted in App.jsx outside
// <Routes> (same pattern as ChatWidget) so it's visible from every
// page, not just Inventory, per the backlog's explicit "the app
// reaches out rather than waiting to be visited" framing. Push/email
// notifications are NOT built here -- see
// inventory_service.get_expiring_digest's docstring for why (the
// backlog itself calls both "optional" and this banner "at minimum").
export default function ExpiringDigestBanner() {
  const [digest, setDigest] = useState(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    api
      .get("/inventory/expiring-digest")
      .then(setDigest)
      .catch(() => {
        // Non-fatal -- the banner just doesn't appear rather than
        // breaking page load over a background check.
      });
  }, []);

  if (dismissed || !digest) return null;
  const expiredCount = digest.expired.length;
  const soonCount = digest.expiring_soon.length;
  if (expiredCount === 0 && soonCount === 0) return null;

  const parts = [];
  if (expiredCount > 0) parts.push(`${expiredCount} item(s) past their expiration date`);
  if (soonCount > 0) parts.push(`${soonCount} expiring within ${digest.within_days} days`);

  return (
    <div className="expiring-digest-banner no-print">
      <span>
        {parts.join(", ")}. <Link to="/inventory">View inventory</Link>
      </span>
      <button className="btn-link" onClick={() => setDismissed(true)}>
        Dismiss
      </button>
    </div>
  );
}
