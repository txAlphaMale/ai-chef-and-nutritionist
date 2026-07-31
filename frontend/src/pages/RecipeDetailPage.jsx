import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, backendOrigin } from "../api";
import RecipeForm from "../components/RecipeForm";
import RecipeChat from "../components/RecipeChat";

export default function RecipeDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [recipe, setRecipe] = useState(null);
  const [servings, setServings] = useState(null);
  const [error, setError] = useState(null);
  const [editing, setEditing] = useState(false);
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
      <Link to="/recipes">&larr; All recipes</Link>
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

      <div className="form-row" style={{ alignItems: "center" }}>
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

      {Object.keys(recipe.nutrition || {}).length > 0 && (
        <>
          <h3>Nutrition (per serving)</h3>
          <ul className="nutrition-list">
            {Object.entries(recipe.nutrition).map(([k, v]) => (
              <li key={k}>
                {k}: {v}
              </li>
            ))}
          </ul>
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

      <div className="form-actions">
        <button className="btn btn-secondary" onClick={() => setEditing(true)}>
          Edit
        </button>
        <button className="btn-link btn-link-danger" onClick={handleDelete}>
          Delete recipe
        </button>
      </div>

      <RecipeChat recipeId={id} servings={recipe.servings_shown} onRecipeUpdated={() => load(servings)} />
    </div>
  );
}
