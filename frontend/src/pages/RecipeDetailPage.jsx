import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, backendOrigin } from "../api";
import RecipeForm from "../components/RecipeForm";
import RecipeChat from "../components/RecipeChat";

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
const PROVENANCE_INFO = {
  computed: { label: "Computed from ingredient data", className: "provenance-computed" },
  partial: {
    label: "Partially computed -- some ingredients couldn't be matched or weighed",
    className: "provenance-partial",
  },
  ai_estimated: { label: "AI estimate -- not verified against a food database", className: "provenance-estimated" },
};

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

  async function load(withServings) {
    setError(null);
    try {
      const qs = withServings ? `?servings=${withServings}` : "";
      const r = await api.get(`/recipes/${id}${qs}`);
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
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  function handleServingsChange(value) {
    setServings(value);
    load(value);
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

      <div className="recipe-meta">
        <span className="tag">Prep: {recipe.prep_time_minutes ?? "?"} min</span>
        <span className="tag">Cook: {recipe.cook_time_minutes ?? "?"} min</span>
        {(recipe.tags || []).map((t) => (
          <span className="tag" key={t}>
            {t}
          </span>
        ))}
      </div>

      <div className="form-row no-print" style={{ alignItems: "center" }}>
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
      <ul>
        {recipe.ingredients.map((ing, i) => (
          <li key={i}>
            {ing.quantity != null ? `${ing.quantity} ` : ""}
            {ing.unit ? `${ing.unit} ` : ""}
            {ing.ingredient_name}
            {ing.prep_note ? `, ${ing.prep_note}` : ""}
          </li>
        ))}
      </ul>

      <h3>Instructions</h3>
      <ol>
        {recipe.instructions.map((step, i) => (
          <li key={i}>{step}</li>
        ))}
      </ol>

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
        <button className="btn btn-secondary" onClick={() => setEditing(true)}>
          Edit
        </button>
        <button className="btn btn-secondary" onClick={() => window.print()}>
          Print recipe
        </button>
        <button className="btn-link btn-link-danger" onClick={handleDelete}>
          Delete recipe
        </button>
      </div>

      <RecipeChat recipeId={id} servings={recipe.servings_shown} onRecipeUpdated={() => load(servings)} />
    </div>
  );
}
