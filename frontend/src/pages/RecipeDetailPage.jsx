import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, backendOrigin } from "../api";
import InfoTip from "../components/InfoTip";
import DerivedTags from "../components/DerivedTags";
import RecipeForm from "../components/RecipeForm";
import RecipeChat from "../components/RecipeChat";
import RestrictionWarnings from "../components/RestrictionWarnings";
import CookMode from "../components/CookMode";

// Backlog B1.3: friendlier labels for the shared nutrition key set (see
// backend/app/services/food_data_service.py's NUTRITION_KEYS) -- falls
// back to the raw key for anything not listed, so an unrecognized key
// (e.g. from an older recipe, or a future addition) still renders instead
// of silently disappearing.
const NUTRIENT_LABELS = {
  calories: "Calories",
  protein_g: "Protein (g)",
  carbs_g: "Carbs (g)",
  fat_g: "Fat (g)",
  saturated_fat_g: "Saturated fat (g)",
  fiber_g: "Fiber (g)",
  sugars_g: "Sugars (g)",
  sodium_mg: "Sodium (mg)",
  cholesterol_mg: "Cholesterol (mg)",
};

// Backlog B1.2: how to describe each provenance value to a non-technical
// user -- distinguishing "we actually looked this up" from "this is a
// guess" is the entire point of B1's backlog group (see PROJECT-PLAN.md).
/** Instructions as {component, text}, whatever the API sent.
 *
 * Recipes saved before steps had components are plain strings, and the
 * backend does not migrate the column -- it coerces on read. The same
 * tolerance has to exist here, or an older recipe renders as blanks. */
function instructionSteps(instructions) {
  return (instructions || []).map((s) => (typeof s === "string" ? { component: null, text: s } : s));
}

/** Consecutive runs sharing a component, in source order.
 *
 * Runs rather than a group-by map: a recipe writes its parts in an order
 * that means something (crust before filling), and a Crust section that
 * appears twice is the source's business, not ours to merge. */
function groupByComponent(items) {
  const groups = [];
  for (const item of items || []) {
    const component = item.component || null;
    if (!groups.length || groups.at(-1).component !== component) {
      groups.push({ component, items: [] });
    }
    groups.at(-1).items.push(item);
  }
  return groups;
}

const PROVENANCE_INFO = {
  computed: { label: "Computed from ingredient data", className: "provenance-computed" },
  partial: {
    label: "Partially computed -- some ingredients couldn't be matched or weighed",
    className: "provenance-partial",
  },
  ai_estimated: { label: "AI estimate -- not verified against a food database", className: "provenance-estimated" },
};

// Backlog B6.1 -- same "distinguish a real number from an incomplete
// guess" discipline as the nutrition provenance above, for cost.
const COST_PROVENANCE_INFO = {
  computed: { label: "Estimated from your own recent purchase prices", className: "provenance-computed" },
  partial: {
    label: "Partial estimate -- some ingredients have no priced purchase on record",
    className: "provenance-partial",
  },
  no_data: {
    label: "No cost estimate yet -- add prices via a receipt/order import or manual entry",
    className: "provenance-estimated",
  },
};

// Backlog B10.5 -- unit-system options for the display toggle. "weight"
// is grams/kg for everything (including volume-measured ingredients
// with a known density), not just already-mass ones -- see the backend's
// unit_conversion_service.convert_for_display.
const UNIT_SYSTEM_OPTIONS = [
  { value: "original", label: "Original" },
  { value: "metric", label: "Metric" },
  { value: "imperial", label: "Imperial" },
  { value: "weight", label: "Weight" },
];

export default function RecipeDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [recipe, setRecipe] = useState(null);
  const [servings, setServings] = useState(null);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(false);
  const [computing, setComputing] = useState(false);
  const [computeError, setComputeError] = useState(null);
  // Variants (children of this recipe, via parent_recipe_id) -- fetched
  // separately since RecipeRead only carries the count, not the list.
  const [variants, setVariants] = useState([]);
  // Backlog B10.5 -- per-view display toggle, seeded from the DB-backed
  // default_unit_system setting on first load (see the mount effect
  // below) so it starts wherever the user last left it, same "instant
  // apply, persist as the new default" pattern as the Appearance theme
  // picker (SettingsPage.jsx).
  const [unitSystem, setUnitSystem] = useState("original");
  // Backlog B6.1 -- fetched separately from the recipe itself: cost is
  // computed live from currently-tracked inventory prices (see
  // cost_service.py), not a stored recipe field, so it doesn't change
  // with the servings/unit-system display toggles above and doesn't need
  // to be refetched when those change.
  const [cost, setCost] = useState(null);
  // Backlog B7.1 -- full-screen cook mode is a plain overlay toggle, not
  // a route change (see CookMode.jsx's own module comment for why).
  const [cookMode, setCookMode] = useState(false);

  async function load(withServings, withUnitSystem) {
    setError(null);
    try {
      const system = withUnitSystem ?? unitSystem;
      const params = new URLSearchParams();
      if (withServings) params.set("servings", withServings);
      if (system && system !== "original") params.set("unit_system", system);
      const qs = params.toString();
      const r = await api.get(`/recipes/${id}${qs ? `?${qs}` : ""}`);
      setRecipe(r);
      if (servings === null) setServings(r.default_servings);
      if (r.variant_count > 0) {
        api.get(`/recipes/${id}/variants`).then(setVariants).catch(() => setVariants([]));
      } else {
        setVariants([]);
      }
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    (async () => {
      let initialSystem = "original";
      try {
        const settings = await api.get("/system/settings");
        initialSystem = settings.find((s) => s.key === "default_unit_system")?.value || "original";
        setUnitSystem(initialSystem);
      } catch {
        // Non-fatal -- falls back to "original" for this view.
      }
      load(undefined, initialSystem);
    })();
    api.get(`/recipes/${id}/cost`).then(setCost).catch(() => setCost(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  function handleServingsChange(value) {
    setServings(value);
    load(value);
  }

  async function handleUnitSystemChange(value) {
    setUnitSystem(value);
    load(servings, value);
    try {
      await api.patch("/system/settings/default_unit_system", { value });
    } catch {
      // Non-fatal -- the view still switched, it just didn't persist as
      // the new default for next time.
    }
  }

  async function handleRate(value) {
    await api.post(`/recipes/${id}/rating`, { rating: value });
    load(servings);
  }

  async function toggleStaple() {
    await api.patch(`/recipes/${id}`, { is_staple: !recipe.is_staple });
    load(servings);
  }

  async function handleUpdate(payload) {
    // RecipeForm uploads/removes the dish photo immediately through its
    // own endpoints in edit mode (it has recipe.id to target), so the
    // second (image file) argument onSubmit receives here is always null
    // -- nothing extra to do with it.
    await api.patch(`/recipes/${id}`, payload);
    setEditing(false);
    load(servings);
  }

  async function handleDelete() {
    if (!window.confirm(`Delete "${recipe.title}"?`)) return;
    await api.del(`/recipes/${id}`);
    navigate("/recipes");
  }

  async function handleComputeNutrition() {
    setComputing(true);
    setComputeError(null);
    try {
      await api.post(`/recipes/${id}/compute-nutrition`);
      load(servings);
    } catch (e) {
      setComputeError(e.message);
    } finally {
      setComputing(false);
    }
  }

  if (error) return <p className="error-text">{error}</p>;
  if (!recipe) return <p>Loading...</p>;

  if (editing) {
    return (
      <div className="card">
        <h2>Edit recipe</h2>
        <RecipeForm initial={recipe} onSubmit={handleUpdate} onCancel={() => setEditing(false)} />
      </div>
    );
  }

  if (cookMode) {
    return <CookMode recipe={recipe} onExit={() => setCookMode(false)} />;
  }

  return (
    <div>
      <Link to="/recipes" className="no-print">
        &larr; All recipes
      </Link>
      <h2>
        {recipe.title}
        {recipe.variant_label && <span className="tag variant-tag"> {recipe.variant_label}</span>}
      </h2>
      {recipe.parent_recipe_id && (
        <p className="hint">
          Variant of{" "}
          <Link to={`/recipes/${recipe.parent_recipe_id}`}>{recipe.parent_recipe_title || "the original recipe"}</Link>
        </p>
      )}
      {recipe.image_path && (
        <img className="recipe-detail-image" src={`${backendOrigin}/api/recipes/${id}/image`} alt={recipe.title} />
      )}
      {recipe.description && <p>{recipe.description}</p>}

      <RestrictionWarnings matches={recipe.restriction_warnings} crossContactMatches={recipe.cross_contact_warnings} />

      <div className="recipe-meta">
        <span className="tag">Prep: {recipe.prep_time_minutes ?? "?"} min</span>
        <span className="tag">Cook: {recipe.cook_time_minutes ?? "?"} min</span>
        {(recipe.tags || []).map((t) => (
          <span className="tag" key={t}>
            {t}
          </span>
        ))}
      </div>

      {/* Directly under the editable tags, because the contrast between
          the two is the point: a recipe can carry an editable
          `gluten_free` an old import asserted AND a derived "contains
          gluten" worked out from its actual ingredients, and seeing those
          adjacent is what makes the difference legible. */}
      <DerivedTags derivedTags={recipe.derived_tags} nutritionProvenance={recipe.nutrition_provenance} />

      <div className="form-row no-print u-align-center">
        <label>
          Servings
          <input
            type="number"
            min="1"
            value={servings ?? recipe.default_servings}
            onChange={(e) => handleServingsChange(Number(e.target.value) || 1)}
          />
        </label>
        <label>
          Units
          <InfoTip label="Units" wikiEntry="units-and-scaling">
            Changes what is displayed only &mdash; the saved recipe is untouched. <strong>Weight</strong> is the
            one that matters for gluten-free baking, where flour blends measured by volume are unreliable. It
            needs a density for that specific ingredient; where none is known the conversion is shown as
            unavailable rather than guessed.
          </InfoTip>
          <select value={unitSystem} onChange={(e) => handleUnitSystemChange(e.target.value)}>
            {UNIT_SYSTEM_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Rating
          <select value={recipe.rating ?? ""} onChange={(e) => handleRate(Number(e.target.value))}>
            <option value="">Not rated</option>
            {[1, 2, 3, 4, 5].map((n) => (
              <option key={n} value={n}>
                {"★".repeat(n)}
              </option>
            ))}
          </select>
        </label>
        <label className="checkbox-label inline">
          <input type="checkbox" checked={recipe.is_staple} onChange={toggleStaple} />
          Staple
        </label>
      </div>

      <h3>Ingredients ({recipe.servings_shown} servings)</h3>
      {unitSystem !== "original" && recipe.ingredients.some((i) => i.display_unavailable) && (
        <p className="hint">
          Some ingredients can't be shown in this unit yet -- run "Compute from ingredients" below to resolve them
          against a food database first (weight mode needs a known density, which not every ingredient has).
        </p>
      )}
      {groupByComponent(recipe.ingredients).map(({ component, items }) => (
        <div key={component || "__main"} className="component-group">
          {component && <h4 className="component-heading">{component}</h4>}
          <ul>
            {items.map((ing, i) => (
              <li key={i}>
                {ing.quantity != null ? `${ing.quantity} ` : ""}
                {ing.unit ? `${ing.unit} ` : ""}
                {ing.ingredient_name}
                {ing.prep_note ? `, ${ing.prep_note}` : ""}
                {ing.display_unavailable && <span className="tag">not available in {unitSystem}</span>}
              </li>
            ))}
          </ul>
        </div>
      ))}

      <h3>Instructions</h3>
      {groupByComponent(instructionSteps(recipe.instructions)).map(({ component, items }, gi, groups) => (
        <div key={component || "__main"} className="component-group">
          {component && <h4 className="component-heading">{component}</h4>}
          <ol start={groups.slice(0, gi).reduce((n, g) => n + g.items.length, 0) + 1}>
            {items.map((step, i) => (
              <li key={i}>{step.text}</li>
            ))}
          </ol>
        </div>
      ))}

      {(Object.keys(recipe.nutrition || {}).length > 0 || recipe.ingredients.some((i) => i.quantity != null)) && (
        <>
          <h3>Nutrition (per serving)</h3>
          {(() => {
            const info = PROVENANCE_INFO[recipe.nutrition_provenance] || PROVENANCE_INFO.ai_estimated;
            return <p className={`provenance-badge ${info.className}`}>{info.label}</p>;
          })()}
          {Object.keys(recipe.nutrition || {}).length > 0 && (
            <ul className="nutrition-list">
              {Object.entries(recipe.nutrition).map(([k, v]) => (
                <li key={k}>
                  {NUTRIENT_LABELS[k] || k}: {v}
                </li>
              ))}
            </ul>
          )}
          <button type="button" className="btn btn-secondary btn-sm" onClick={handleComputeNutrition} disabled={computing}>
            {computing ? "Computing..." : "Compute from ingredients"}
          </button>
          {computeError && <p className="error-text">{computeError}</p>}
        </>
      )}

      {cost && cost.provenance !== "no_data" && (
        <>
          <h3>Estimated cost</h3>
          <p className={`provenance-badge ${COST_PROVENANCE_INFO[cost.provenance].className}`}>
            {COST_PROVENANCE_INFO[cost.provenance].label}
          </p>
          <p>
            ${cost.cost_per_serving?.toFixed(2)} per serving -- $
            {(cost.cost_per_serving * (recipe.servings_shown || cost.servings)).toFixed(2)} for {recipe.servings_shown || cost.servings}{" "}
            servings
            {cost.provenance === "partial" && (
              <span className="hint">
                {" "}
                ({cost.resolved_count}/{cost.total_count} ingredients priced)
              </span>
            )}
          </p>
        </>
      )}

      {recipe.tips?.length > 0 && (
        <>
          <h3>Tips, substitutions &amp; variations</h3>
          <ul>
            {recipe.tips.map((tip, i) => (
              <li key={i}>{tip}</li>
            ))}
          </ul>
        </>
      )}

      {(recipe.source_url || recipe.source_name || recipe.source_author) && (
        <p className="hint recipe-source">
          Source:{" "}
          {recipe.source_url ? (
            <a href={recipe.source_url} target="_blank" rel="noreferrer">
              {recipe.source_name || recipe.source_url}
            </a>
          ) : (
            recipe.source_name
          )}
          {recipe.source_author ? ` — ${recipe.source_author}` : ""}
        </p>
      )}

      {variants.length > 0 && (
        <>
          <h3>Variants</h3>
          <ul>
            {variants.map((v) => (
              <li key={v.id}>
                <Link to={`/recipes/${v.id}`}>{v.title}</Link>
                {v.variant_label ? ` — ${v.variant_label}` : ""}
              </li>
            ))}
          </ul>
        </>
      )}

      <div className="form-actions no-print">
        {recipe.instructions?.length > 0 && (
          <button className="btn btn-primary" onClick={() => setCookMode(true)}>
            Cook mode
          </button>
        )}
        <button className="btn btn-secondary" onClick={() => setEditing(true)}>
          Edit
        </button>
        <button className="btn btn-secondary" onClick={() => window.print()}>
          Print recipe
        </button>
        {/* Backlog B9.2 -- a plain <a href> to the backend, not routed through api.js's fetch
            wrapper (which always parses JSON), same download pattern as the meal plan's .ics
            link and the Settings page's backup download. schema.org JSON-LD is the same format
            the URL/file importer reads, so an exported recipe round-trips back in. */}
        <a className="btn btn-secondary" href={`${backendOrigin}/api/recipes/${id}/export/jsonld`}>
          Export recipe (JSON-LD)
        </a>
        <button className="btn-link btn-link-danger" onClick={handleDelete}>
          Delete recipe
        </button>
      </div>

      <RecipeChat recipeId={id} servings={recipe.servings_shown} onRecipeUpdated={() => load(servings)} />
    </div>
  );
}
