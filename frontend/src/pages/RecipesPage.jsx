import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import RecipeForm from "../components/RecipeForm";

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
  const [importText, setImportText] = useState("");

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

  async function handleCreate(payload) {
    await api.post("/recipes", payload);
    setShowAddForm(false);
    refresh();
  }

  async function handleImportText() {
    if (!importText.trim()) return;
    setImportBusy(true);
    setImportError(null);
    setImportPreview(null);
    try {
      const formData = new FormData();
      formData.append("text", importText);
      const result = await api.post("/recipes/import", formData);
      setImportPreview(result.recipe);
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
    try {
      const formData = new FormData();
      formData.append("file", file);
      const result = await api.post("/recipes/import", formData);
      setImportPreview(result.recipe);
    } catch (err) {
      setImportError(err.message);
    } finally {
      setImportBusy(false);
      e.target.value = "";
    }
  }

  async function confirmImport(editedPayload) {
    await api.post("/recipes", editedPayload);
    setImportPreview(null);
    setImportText("");
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
        <div className="form-row">
          <textarea
            rows={3}
            placeholder="Paste a recipe's text here..."
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
          <RecipeForm initial={importPreview} onSubmit={confirmImport} onCancel={() => setImportPreview(null)} />
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
            <li key={r.id} className="recipe-list-item">
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
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
