import { useEffect, useState } from "react";
import { api } from "../api";
import GroceryListPanel from "../components/GroceryListPanel";
import MealPlanEntryRow from "../components/MealPlanEntryRow";

const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const MEAL_TYPES = ["breakfast", "lunch", "dinner", "snack"];

/** The Monday of the current week, or today if today is already Monday --
 * a sensible default for "week starting". */
function defaultWeekStart() {
  const d = new Date();
  const day = d.getDay(); // 0=Sun..6=Sat
  const diff = day === 0 ? 1 : day === 1 ? 0 : 8 - day;
  d.setDate(d.getDate() + diff);
  return d.toISOString().slice(0, 10);
}

function emptyGuidance() {
  return DAY_NAMES.map(() => ({ tags: "", notes: "" }));
}

export default function MealPlanPage() {
  const [plans, setPlans] = useState([]);
  const [selectedPlanId, setSelectedPlanId] = useState(null);
  const [recipeCatalog, setRecipeCatalog] = useState([]);
  const [kitchenProfiles, setKitchenProfiles] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [refreshKey, setRefreshKey] = useState(0);

  const [showGenerateForm, setShowGenerateForm] = useState(false);
  const [showGuidance, setShowGuidance] = useState(false);
  const [weekStartDate, setWeekStartDate] = useState(defaultWeekStart());
  const [mealTypes, setMealTypes] = useState({ breakfast: false, lunch: false, dinner: true, snack: false });
  const [householdSize, setHouseholdSize] = useState("");
  const [kitchenProfileId, setKitchenProfileId] = useState("");
  const [notes, setNotes] = useState("");
  const [guidance, setGuidance] = useState(emptyGuidance());
  const [generating, setGenerating] = useState(false);
  const [generateError, setGenerateError] = useState(null);

  const [preview, setPreview] = useState(null); // { entries: [...], meta: {...} }
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const [planList, recipes, kitchens] = await Promise.all([
        api.get("/meal-plans"),
        api.get("/recipes"),
        api.get("/kitchen-profiles"),
      ]);
      setPlans(planList);
      setRecipeCatalog(recipes);
      setKitchenProfiles(kitchens);
      const activeKitchen = kitchens.find((k) => k.is_active);
      if (activeKitchen && !kitchenProfileId) setKitchenProfileId(String(activeKitchen.id));
      if (planList.length > 0 && selectedPlanId === null) {
        setSelectedPlanId(planList[0].id);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selectedPlan = plans.find((p) => p.id === selectedPlanId) || null;

  async function refreshPlans(keepSelected) {
    const planList = await api.get("/meal-plans");
    setPlans(planList);
    if (!keepSelected && planList.length > 0) setSelectedPlanId(planList[0].id);
    setRefreshKey((k) => k + 1);
  }

  function updateGuidance(dayIndex, field, value) {
    setGuidance((g) => g.map((row, i) => (i === dayIndex ? { ...row, [field]: value } : row)));
  }

  async function handleGenerate(e) {
    e.preventDefault();
    setGenerating(true);
    setGenerateError(null);
    setPreview(null);
    try {
      const selectedMealTypes = MEAL_TYPES.filter((m) => mealTypes[m]);
      const entryGuidance = guidance
        .map((row, day_of_week) => ({
          day_of_week,
          meal_type: selectedMealTypes[0] || "dinner",
          tags: row.tags.split(",").map((t) => t.trim()).filter(Boolean),
          notes: row.notes.trim() || null,
        }))
        .filter((g) => g.tags.length > 0 || g.notes);

      const result = await api.post("/meal-plans/generate", {
        week_start_date: weekStartDate,
        meal_types: selectedMealTypes.length ? selectedMealTypes : ["dinner"],
        household_size: householdSize === "" ? null : Number(householdSize),
        kitchen_profile_id: kitchenProfileId === "" ? null : Number(kitchenProfileId),
        entry_guidance: entryGuidance,
        notes: notes.trim() || null,
      });

      setPreview({
        meta: {
          week_start_date: result.plan.week_start_date,
          household_size_snapshot: result.plan.household_size_snapshot,
          kitchen_profile_id: result.plan.kitchen_profile_id,
        },
        entries: result.plan.entries.map((e) => ({
          day_of_week: e.day_of_week,
          meal_type: e.meal_type,
          servings: e.servings,
          requested_tags: (e.requested_tags || []).join(", "),
          is_indulgence: e.is_indulgence,
          notes: e.notes || "",
          selection: e.recipe_id != null ? String(e.recipe_id) : e.new_recipe ? "new" : "",
          new_recipe: e.new_recipe,
        })),
      });
    } catch (e) {
      setGenerateError(e.message);
    } finally {
      setGenerating(false);
    }
  }

  function updatePreviewEntry(index, field, value) {
    setPreview((p) => ({ ...p, entries: p.entries.map((e, i) => (i === index ? { ...e, [field]: value } : e)) }));
  }

  async function handleSavePlan() {
    if (!preview) return;
    setSaving(true);
    setSaveError(null);
    try {
      const payload = {
        week_start_date: preview.meta.week_start_date,
        household_size_snapshot: preview.meta.household_size_snapshot,
        kitchen_profile_id: preview.meta.kitchen_profile_id,
        status: "draft",
        entries: preview.entries.map((e) => ({
          day_of_week: e.day_of_week,
          meal_type: e.meal_type,
          servings: Number(e.servings) || 1,
          requested_tags: e.requested_tags.split(",").map((t) => t.trim()).filter(Boolean),
          is_indulgence: !!e.is_indulgence,
          notes: e.notes || null,
          recipe_id: e.selection && e.selection !== "new" ? Number(e.selection) : null,
          new_recipe: e.selection === "new" ? e.new_recipe : null,
        })),
      };
      const created = await api.post("/meal-plans", payload);
      setPreview(null);
      setShowGenerateForm(false);
      const recipes = await api.get("/recipes");
      setRecipeCatalog(recipes);
      setSelectedPlanId(created.id);
      await refreshPlans(true);
      setSelectedPlanId(created.id);
    } catch (e) {
      setSaveError(e.message);
    } finally {
      setSaving(false);
    }
  }

  async function handleDeletePlan(planId) {
    if (!window.confirm("Delete this meal plan?")) return;
    await api.del(`/meal-plans/${planId}`);
    setSelectedPlanId(null);
    await refreshPlans(false);
  }

  function onEntryChanged() {
    refreshPlans(true);
  }

  return (
    <div>
      <div className="page-toolbar">
        {plans.length > 0 && (
          <select value={selectedPlanId ?? ""} onChange={(e) => setSelectedPlanId(Number(e.target.value))}>
            {plans.map((p) => (
              <option key={p.id} value={p.id}>
                Week of {p.week_start_date} ({p.status})
              </option>
            ))}
          </select>
        )}
        <button className="btn btn-primary" onClick={() => setShowGenerateForm((v) => !v)}>
          {showGenerateForm ? "Close" : "+ Generate a weekly plan"}
        </button>
      </div>

      {error && <p className="error-text">{error}</p>}

      {showGenerateForm && (
        <div className="card">
          <h3>Generate a weekly meal plan</h3>
          <p className="hint">
            The chef will favor ingredients expiring soon or flagged as priority in your inventory, reuse
            staple recipes where they fit, and respect your household's dietary preferences and current
            kitchen setup.
          </p>
          <form onSubmit={handleGenerate}>
            <div className="form-row">
              <label>
                Week starting
                <input type="date" value={weekStartDate} onChange={(e) => setWeekStartDate(e.target.value)} required />
              </label>
              <label>
                Household size (optional override)
                <input
                  type="number"
                  min="1"
                  placeholder="use household default"
                  value={householdSize}
                  onChange={(e) => setHouseholdSize(e.target.value)}
                />
              </label>
              <label>
                Kitchen setup
                <select value={kitchenProfileId} onChange={(e) => setKitchenProfileId(e.target.value)}>
                  <option value="">(household default)</option>
                  {kitchenProfiles.map((k) => (
                    <option key={k.id} value={k.id}>
                      {k.name}
                      {k.is_active ? " (active)" : ""}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            <fieldset>
              <legend>Meals to plan</legend>
              <div className="form-row">
                {MEAL_TYPES.map((m) => (
                  <label className="checkbox-label inline" key={m}>
                    <input
                      type="checkbox"
                      checked={mealTypes[m]}
                      onChange={(e) => setMealTypes((mt) => ({ ...mt, [m]: e.target.checked }))}
                    />
                    {m}
                  </label>
                ))}
              </div>
            </fieldset>

            <label>
              Notes for the chef (optional)
              <textarea
                rows={2}
                placeholder="e.g. going camping Saturday, keep Friday flexible for leftovers"
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
              />
            </label>

            <button type="button" className="btn-link" onClick={() => setShowGuidance((v) => !v)}>
              {showGuidance ? "Hide" : "Show"} per-day guidance (quick, portable, non-refrigerated, etc.)
            </button>
            {showGuidance && (
              <fieldset>
                <legend>Per-day guidance (applies to the first checked meal type above)</legend>
                {DAY_NAMES.map((day, i) => (
                  <div className="form-row" key={day}>
                    <span className="day-guidance-label">{day}</span>
                    <input
                      placeholder="tags, e.g. quick, portable"
                      value={guidance[i].tags}
                      onChange={(e) => updateGuidance(i, "tags", e.target.value)}
                    />
                    <input
                      placeholder="notes, e.g. picnic lunch"
                      value={guidance[i].notes}
                      onChange={(e) => updateGuidance(i, "notes", e.target.value)}
                    />
                  </div>
                ))}
              </fieldset>
            )}

            <div className="form-actions">
              <button className="btn btn-primary" type="submit" disabled={generating}>
                {generating ? "Thinking..." : "Generate plan"}
              </button>
            </div>
            {generateError && <p className="error-text">{generateError}</p>}
          </form>
        </div>
      )}

      {preview && (
        <div className="card">
          <h3>Review generated plan</h3>
          <p className="hint">
            Swap any slot to a different catalog recipe, adjust servings, or leave the AI's proposed new
            recipe as-is. Nothing is saved until you confirm below.
          </p>
          {preview.entries.map((entry, i) => (
            <div className="form-row preview-entry-row" key={i}>
              <span className="preview-entry-label">
                {DAY_NAMES[entry.day_of_week]} <span className="tag">{entry.meal_type}</span>
              </span>
              <select value={entry.selection} onChange={(e) => updatePreviewEntry(i, "selection", e.target.value)}>
                <option value="">-- no recipe --</option>
                {entry.new_recipe && <option value="new">New: {entry.new_recipe.title}</option>}
                {recipeCatalog.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.title}
                  </option>
                ))}
              </select>
              <input
                type="number"
                min="1"
                value={entry.servings}
                onChange={(e) => updatePreviewEntry(i, "servings", e.target.value)}
                style={{ maxWidth: 70 }}
              />
              <input
                placeholder="tags"
                value={entry.requested_tags}
                onChange={(e) => updatePreviewEntry(i, "requested_tags", e.target.value)}
              />
              <label className="checkbox-label inline">
                <input
                  type="checkbox"
                  checked={entry.is_indulgence}
                  onChange={(e) => updatePreviewEntry(i, "is_indulgence", e.target.checked)}
                />
                indulgence
              </label>
            </div>
          ))}
          <div className="form-actions">
            <button className="btn btn-primary" onClick={handleSavePlan} disabled={saving}>
              {saving ? "Saving..." : "Confirm & save plan"}
            </button>
            <button className="btn btn-secondary" onClick={() => setPreview(null)} disabled={saving}>
              Discard
            </button>
          </div>
          {saveError && <p className="error-text">{saveError}</p>}
        </div>
      )}

      {loading ? (
        <p>Loading meal plans...</p>
      ) : !selectedPlan ? (
        <p>No meal plans yet. Generate one above to get started.</p>
      ) : (
        <>
          <div className="card">
            <div className="page-toolbar">
              <h3 style={{ margin: 0 }}>
                Week of {selectedPlan.week_start_date} <span className="tag">{selectedPlan.status}</span>
              </h3>
              <button className="btn-link btn-link-danger" onClick={() => handleDeletePlan(selectedPlan.id)}>
                Delete plan
              </button>
            </div>
            {selectedPlan.entries.length === 0 ? (
              <p>This plan has no meal slots yet.</p>
            ) : (
              <div className="meal-entry-grid">
                {selectedPlan.entries.map((entry) => (
                  <MealPlanEntryRow
                    key={entry.id}
                    entry={entry}
                    planId={selectedPlan.id}
                    recipeCatalog={recipeCatalog}
                    onChanged={onEntryChanged}
                  />
                ))}
              </div>
            )}
          </div>

          <GroceryListPanel planId={selectedPlan.id} refreshKey={refreshKey} />
        </>
      )}
    </div>
  );
}
