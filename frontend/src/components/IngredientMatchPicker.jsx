import { useState } from "react";
import { formatDate } from "../utils/datetime";

/**
 * Audit P1-5 -- the "which item did you mean?" prompt.
 *
 * The backend resolver deliberately refuses to guess between near
 * matches, because a wrong guess writes a wrong number into inventory
 * with nothing on screen to notice. That refusal is only workable if the
 * user is asked ONCE and the answer sticks, which is what this component
 * is for: it renders the ranked candidates the API returned, lets the
 * user pick one, and (checked by default) saves the choice as an alias so
 * the same name resolves straight through next time.
 *
 * Rendered from two places with the same props: a 409 body from
 * /inventory/deduct or /inventory/update-by-name, and a direct
 * GET /inventory/resolve. Both return the same IngredientResolutionResponse
 * shape, which is why this is one component rather than two.
 *
 * All styling lives in styles/theme.css under .match-picker-* -- no
 * inline style objects, matching this project's single-source-of-truth
 * CSS rule.
 */

const CONFIDENCE_LABELS = {
  exact: "Exact",
  high: "Confident",
  medium: "Likely",
  low: "Possible",
  none: "No match",
};

export function ConfidenceTag({ confidence }) {
  if (!confidence) return null;
  return (
    <span className={`tag match-confidence match-confidence-${confidence}`}>
      {CONFIDENCE_LABELS[confidence] || confidence}
    </span>
  );
}

export default function IngredientMatchPicker({ resolution, onPick, busy = false, disabled = false }) {
  const [selectedId, setSelectedId] = useState(resolution?.candidates?.[0]?.item_id ?? null);
  const [remember, setRemember] = useState(true);

  if (!resolution) return null;
  const candidates = resolution.candidates || [];
  const blocked = resolution.blocked_candidates || [];

  return (
    <div className="match-picker">
      <p className="match-picker-question">
        {resolution.message || `No confident match for "${resolution.query}".`}
      </p>

      {candidates.length === 0 && (
        <p className="hint">Nothing in your inventory resembles this name closely enough to suggest.</p>
      )}

      {candidates.map((c) => (
        <label className="match-picker-option" key={c.item_id ?? c.name}>
          <input
            type="radio"
            name={`match-${resolution.normalized}`}
            checked={selectedId === c.item_id}
            onChange={() => setSelectedId(c.item_id)}
            disabled={disabled || busy}
          />
          <span className="match-picker-option-body">
            <span className="match-picker-option-name">
              {c.name}
              <ConfidenceTag confidence={c.confidence} />
            </span>
            <span className="match-picker-option-meta">
              {c.quantity != null && `${c.quantity}${c.unit ? ` ${c.unit}` : ""} on hand`}
              {c.expiration_date && ` · expires ${formatDate(c.expiration_date)}`}
            </span>
            {/* The resolver's own explanation, shown verbatim. A candidate
                list that only showed names would throw away the useful
                half of what this layer computes. */}
            <span className="match-picker-option-reason">{c.reason}</span>
          </span>
        </label>
      ))}

      {blocked.length > 0 && (
        <details className="match-picker-blocked">
          <summary>
            {blocked.length} item{blocked.length === 1 ? "" : "s"} deliberately excluded
          </summary>
          {/* An unexplained non-match invites the same bug report twice.
              These are items that DO share a word with what was asked
              for, and were ruled out on purpose. */}
          <ul>
            {blocked.map((b) => (
              <li key={b.item_id ?? b.name}>
                <strong>{b.name}</strong> — {b.blocked_by}
              </li>
            ))}
          </ul>
        </details>
      )}

      {candidates.length > 0 && (
        <div className="match-picker-actions">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={remember}
              onChange={(e) => setRemember(e.target.checked)}
              disabled={disabled || busy}
            />
            Remember this — don't ask again for "{resolution.query}"
          </label>
          <button
            className="btn btn-primary"
            disabled={disabled || busy || selectedId == null}
            onClick={() => onPick(selectedId, remember)}
          >
            {busy ? "Applying..." : "Use this item"}
          </button>
        </div>
      )}
    </div>
  );
}
