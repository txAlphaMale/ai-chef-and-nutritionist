import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, backendOrigin } from "../api";
import RecipeForm from "../components/RecipeForm";
import RestrictionWarnings from "../components/RestrictionWarnings";

export default function RecipesPage() {
  const [recipes, setRecipes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [stapleOnly, setStapleOnly] = useState(false);
  const [search, setSearch] = useState("");

  const [importBusy, setImportBusy] = useState(false);
  const [importError, setImportError] = useState(null);
  const [importPreview, setImportPreview] = useState(null); // RecipeCreate-shaped
  // Backlog B3.1 -- RecipeImportResponse carries these alongside the
  // parsed recipe, computed against current household restrictions at
  // import time so a conflict is visible before the recipe is ever saved.
  const [importWarnings, setImportWarnings] = useState(null);
  const [importText, setImportText] = useState("");
  const [importUrl, setImportUrl] = useState("");

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (stapleOnly) params.set("is_staple", "true");
      if (search) params.set("search", search);
      const qs = params.toString();
      const list = await api.get(`/recipes${qs ? `?${qs}` : ""}`);
      setRecipes(list);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stapleOnly, search]);

  async function uploadImageIfNeeded(recipeId, imageFile) {
    if (!imageFile) return;
    const formData = new FormData();
    formData.append("file", imageFile);
    // Best-effort -- the recipe itself is already saved at this point, so
    // an image upload failure shouldn't be treated as the whole save
    // having failed. Surfaced via the page-level error banner instead of
    // blocking navigation/refresh.
    try {
      await api.post(`/recipes/${recipeId}/image`, formData);
    } catch (e) {
      setError(`Recipe saved, but the photo upload failed: ${e.message}`);
    }
  }

  async function handleCreate(payload, imageFile) {
    const created = await api.post("/recipes", payload);
    await uploadImageIfNeeded(created.id, imageFile);
    setShowAddForm(false);
    refresh();
  }

  async function handleImportText() {
    if (!importText.trim()) return;
    setImportBusy(true);
    setImportError(null);
    setImportPreview(null);
    setImportWarnings(null);
    try {
      const formData = new FormData();
      formData.append("text", importText);
      const result = await api.post("/recipes/import", formData);
      setImportPreview(result.recipe);
      setImportWarnings({ matches: result.restriction_warnings, crossContactMatches: result.cross_contact_warnings });
    } catch (e) {
      setImportError(e.message);
    } finally {
      setImportBusy(false);
    }
  }

  async function handleImportUrl() {
    if (!importUrl.trim()) return;
    setImportBusy(true);
    setImportError(null);
    setImportPreview(null);
    setImportWarnings(null);
    try {
      const formData = new FormData();
      formData.append("url", importUrl.trim());
      const result = await api.post("/recipes/import", formData);
      setImportPreview(result.recipe);
      setImportWarnings({ matches: result.restriction_warnings, crossContactMatches: result.cross_contact_warnings });
    } catch (e) {
      setImportError(e.message);
    } finally {
      setImportBusy(false);
    }
  }

  async function handleImportFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportBusy(true);
    setImportError(null);
    setImportPreview(null);
    setImportWarnings(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const result = await api.post("/recipes/import", formData);
      setImportPreview(result.recipe);
      setImportWarnings({ matches: result.restriction_warnings, crossContactMatches: result.cross_contact_warnings });
    } catch (err) {
      setImportError(err.message);
    } finally {
      setImportBusy(false);
      e.target.value = "";
    }
  }

  async function confirmImport(editedPayload, imageFile) {
    const created = await api.post("/recipes", editedPayload);
    await uploadImageIfNeeded(created.id, imageFile);
    setImportPreview(null);
    setImportWarnings(null);
    setImportText("");
    setImportUrl("");
    refresh();
  }

  return (
    <div>
      <div className="page-toolbar">
        <input placeholder="Search recipes..." value={search} onChange={(e) => setSearch(e.target.value)} />
        <label className="checkbox-label inline">
          <input type="checkbox" checked={stapleOnly} onChange={(e) => setStapleOnly(e.target.checked)} />
          Staples only
        </label>
        <button className="btn btn-primary" onClick={() => setShowAddForm((v) => !v)}>
          {showAddForm ? "Close" : "+ Add recipe"}
        </button>
      </div>

      {showAddForm && (
        <div className="card">
          <h3>New recipe</h3>
          <RecipeForm onSubmit={handleCreate} onCancel={() => setShowAddForm(false)} />
        </div>
      )}

      <div className="card">
        <h3>Import a recipe</h3>
        <p className="hint">
          From a URL, pasted text, a photo, or a PDF. Ads, stories, and other boilerplate are
          filtered out; useful substitutions/variations and the source citation are kept.
        </p>
        <div className="form-row">
          <input
            placeholder="https://example.com/some-recipe"
            value={importUrl}
            onChange={(e) => setImportUrl(e.target.value)}
            style={{ flex: 1 }}
          />
          <button className="btn btn-secondary" onClick={handleImportUrl} disabled={importBusy || !importUrl.trim()}>
            {importBusy ? "Fetching..." : "Import from URL"}
          </button>
        </div>
        <div className="form-row">
          <textarea
            rows={3}
            placeholder="...or paste a recipe's text here"
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            style={{ flex: 1 }}
          />
        </div>
        <div className="form-actions">
          <button className="btn btn-secondary" onClick={handleImportText} disabled={importBusy || !importText.trim()}>
            {importBusy ? "Parsing..." : "Parse text"}
          </button>
          <label className="btn btn-secondary file-btn">
            {importBusy ? "Parsing..." : "Upload photo or PDF"}
            <input type="file" accept="image/*,application/pdf" onChange={handleImportFile} disabled={importBusy} hidden />
          </label>
        </div>
        {importError && <p className="error-text">Import failed: {importError}</p>}
      </div>

      {importPreview && (
        <div className="card">
          <h3>Review imported recipe</h3>
          {importWarnings && (
            <RestrictionWarnings
              matches={importWarnings.matches}
              crossContactMatches={importWarnings.crossContactMatches}
              title="This recipe's ingredients conflict with a household restriction"
            />
          )}
          <RecipeForm
            initial={importPreview}
            onSubmit={confirmImport}
            onCancel={() => {
              setImportPreview(null);
              setImportWarnings(null);
            }}
          />
        </div>
      )}

      {error && <p className="error-text">{error}</p>}
      {loading ? (
        <p>Loading recipes...</p>
      ) : recipes.length === 0 ? (
        <p>No recipes yet. Add one, or import from text/photo/PDF above.</p>
      ) : (
        <ul className="recipe-list">
          {recipes.map((r) => (
            <li key={r.id} className="recipe-list-item recipe-list-item-with-image">
              {r.image_path && (
                <img className="recipe-thumb" src={`${backendOrigin}/api/recipes/${r.id}/image`} alt="" />
              )}
              <div>
                <Link to={`/recipes/${r.id}`}>
                  <strong>{r.title}</strong>
                </Link>
                {r.is_staple && <span className="tag">★ staple</span>}
                {r.rating != null && <span className="tag">{"★".repeat(r.rating)}</span>}
                <span className="tag">{r.default_servings} servings</span>
                {(r.tags || []).map((t) => (
                  <span className="tag" key={t}>
                    {t}
                  </span>
                ))}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
