import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

/** One slot in the weekly grid for a persisted (already-created) meal
 * plan -- lets the user reassign the recipe, adjust servings, and
 * confirm ("we made this" -> deducts inventory) or skip it. */
export default function MealPlanEntryRow({ entry, planId, recipeCatalog, onChanged }) {
  const [servings, setServings] = useState(entry.servings);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

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

  async function handleConfirm() {
    setBusy(true);
    setError(null);
    try {
      await api.post(`/meal-plans/${planId}/entries/${entry.id}/confirm`, {});
      onChanged();
    } catch (e) {
      setError(e.message);
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

  return (
    <div className={`meal-entry-row${statusLabel ? ` meal-entry-${statusLabel}` : ""}`}>
      <div className="meal-entry-slot">
        <strong>{DAY_NAMES[entry.day_of_week]}</strong>
        <span className="tag">{entry.meal_type}</span>
        {entry.is_indulgence && <span className="tag indulgence-tag">indulgence</span>}
        {statusLabel && <span className="tag">{statusLabel}</span>}
      </div>

      <div className="meal-entry-recipe">
        {entry.recipe ? (
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
        onChange={(e) => patch({ recipe_id: e.target.value ? Number(e.target.value) : null })}
        disabled={busy || entry.is_confirmed}
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
            <button className="btn btn-secondary btn-sm" onClick={handleConfirm} disabled={busy}>
              We made this
            </button>
            <button className="btn-link" onClick={handleSkip} disabled={busy}>
              Skip
            </button>
          </>
        )}
      </div>
      {error && <p className="error-text">{error}</p>}
    </div>
  );
}
