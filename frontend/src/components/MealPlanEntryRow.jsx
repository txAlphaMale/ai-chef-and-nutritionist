import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import RestrictionWarnings from "./RestrictionWarnings";

const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

/** One slot in the weekly grid for a persisted (already-created) meal
 * plan -- lets the user reassign the recipe, adjust servings, and
 * confirm ("we made this" -> deducts inventory) or skip it. */
export default function MealPlanEntryRow({ entry, planId, recipeCatalog, onChanged }) {
  const [servings, setServings] = useState(entry.servings);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  // Backlog B3.1 -- confirm returns 409 with match details when the
  // recipe conflicts with a restricted allergen; holding that here (not
  // in `error`, which is a plain string) lets us render the actual
  // matches plus an explicit "confirm anyway" override instead of just
  // an error message.
  const [conflict, setConflict] = useState(null);

  async function patch(payload) {
    setBusy(true);
    setError(null);
    try {
      await api.patch(`/meal-plans/${planId}/entries/${entry.id}`, payload);
      onChanged();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleConfirm(acknowledgeConflict) {
    setBusy(true);
    setError(null);
    if (!acknowledgeConflict) setConflict(null);
    try {
      await api.post(`/meal-plans/${planId}/entries/${entry.id}/confirm`, {
        acknowledge_restriction_conflict: !!acknowledgeConflict,
      });
      setConflict(null);
      onChanged();
    } catch (e) {
      if (e.status === 409 && e.detail && typeof e.detail === "object") {
        setConflict(e.detail);
      } else {
        setError(e.message);
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleSkip() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/meal-plans/${planId}/entries/${entry.id}/skip`, {});
      onChanged();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const statusLabel = entry.is_confirmed ? "made" : entry.is_skipped ? "skipped" : null;

  // Backlog B10.1 -- a recipe-less entry already confirms/skips without
  // touching inventory and is already excluded from grocery/nutrition
  // aggregation (see MealPlanEntry.is_eating_out's model docstring); this
  // toggle just lets the slot say "eating out" instead of looking like a
  // forgotten/empty one.
  async function toggleEatingOut() {
    if (entry.is_eating_out) {
      await patch({ is_eating_out: false });
    } else {
      await patch({ is_eating_out: true, recipe_id: null });
    }
  }

  return (
    <div className={`meal-entry-row${statusLabel ? ` meal-entry-${statusLabel}` : ""}`}>
      <div className="meal-entry-slot">
        <strong>{DAY_NAMES[entry.day_of_week]}</strong>
        <span className="tag">{entry.meal_type}</span>
        {entry.is_indulgence && <span className="tag indulgence-tag">indulgence</span>}
        {entry.is_eating_out && <span className="tag">eating out</span>}
        {statusLabel && <span className="tag">{statusLabel}</span>}
      </div>

      <div className="meal-entry-recipe">
        {entry.is_eating_out ? (
          <em className="hint">Eating out -- no recipe needed</em>
        ) : entry.recipe ? (
          <Link to={`/recipes/${entry.recipe.id}`}>{entry.recipe.title}</Link>
        ) : (
          <em className="hint">No recipe assigned</em>
        )}
        {(entry.requested_tags || []).map((t) => (
          <span className="tag" key={t}>
            {t}
          </span>
        ))}
      </div>

      <select
        value={entry.recipe_id ?? ""}
        onChange={(e) =>
          patch({
            recipe_id: e.target.value ? Number(e.target.value) : null,
            is_eating_out: e.target.value ? false : entry.is_eating_out,
          })
        }
        disabled={busy || entry.is_confirmed || entry.is_eating_out}
      >
        <option value="">-- no recipe --</option>
        {recipeCatalog.map((r) => (
          <option key={r.id} value={r.id}>
            {r.title}
          </option>
        ))}
      </select>

      <input
        type="number"
        min="1"
        value={servings}
        onChange={(e) => setServings(e.target.value)}
        onBlur={() => Number(servings) !== entry.servings && patch({ servings: Number(servings) || 1 })}
        disabled={busy || entry.is_confirmed}
        style={{ maxWidth: 70 }}
      />

      <div className="meal-entry-actions">
        {!entry.is_confirmed && !entry.is_skipped && (
          <>
            <button className="btn btn-secondary btn-sm" onClick={() => handleConfirm(false)} disabled={busy}>
              We made this
            </button>
            <button className="btn-link" onClick={handleSkip} disabled={busy}>
              Skip
            </button>
            <button className="btn-link" onClick={toggleEatingOut} disabled={busy}>
              {entry.is_eating_out ? "Undo eating out" : "Eating out instead"}
            </button>
          </>
        )}
      </div>
      {error && <p className="error-text">{error}</p>}
      {conflict && (
        <div className="restriction-conflict-dialog">
          <RestrictionWarnings matches={conflict.matches} crossContactMatches={conflict.cross_contact_matches} />
          <div className="form-actions">
            <button className="btn btn-secondary btn-sm" onClick={() => handleConfirm(true)} disabled={busy}>
              Confirm anyway
            </button>
            <button className="btn-link" onClick={() => setConflict(null)} disabled={busy}>
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
