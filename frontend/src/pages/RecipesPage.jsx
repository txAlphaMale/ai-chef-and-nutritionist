import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, backendOrigin } from "../api";
import IngredientProvenance from "../components/IngredientProvenance";
import RecipeForm from "../components/RecipeForm";
import RecipeFacetFilter from "../components/RecipeFacetFilter";
import RestrictionWarnings from "../components/RestrictionWarnings";
import { useBackgroundJob } from "../hooks/useBackgroundJob";
import { applyFacets, derivedTagBases, derivedTagLabel, emptySelection } from "../utils/recipeFacets";

export default function RecipesPage() {
  const [recipes, setRecipes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [stapleOnly, setStapleOnly] = useState(false);
  const [search, setSearch] = useState("");
  // Facet selection lives here rather than in the query string: derived
  // tags are recomputed per read and never stored, so the server cannot
  // filter on them at all. The server still narrows by title/staple; the
  // facets narrow what came back. See utils/recipeFacets.js.
  const [facets, setFacets] = useState(emptySelection());

  // POST /recipes/import enqueues a background job (see recipes.py's
  // import_recipe) rather than blocking for the full
  // URL-fetch/PDF-extract/Ollama duration.
  // importJob.busy/.status drive the button/label states below;
  // enqueueError covers the synchronous "you didn't provide anything"
  // 400 that the endpoint still raises before ever creating a job, which
  // importJob itself has no way to see.
  const importJob = useBackgroundJob("chef.job.recipe_import");
  const [enqueueError, setEnqueueError] = useState(null);
  const [importPreview, setImportPreview] = useState(null); // RecipeCreate-shaped
  // Backlog B3.1 -- RecipeImportResponse carries these alongside the
  // parsed recipe, computed against current household restrictions at
  // import time so a conflict is visible before the recipe is ever saved.
  const [importWarnings, setImportWarnings] = useState(null);
  // Whether the ingredients in the preview were checked against a source
  // or only produced by a model -- stated on the review screen rather
  // than only in the container log. See IngredientProvenance.jsx.
  const [importProvenance, setImportProvenance] = useState(null);
  // Times/temperatures a step states that the source never did (see
  // recipe_service.unverified_instruction_facts). Instructions are the
  // one list nothing used to check at all.
  const [importInstructionWarnings, setImportInstructionWarnings] = useState([]);
  const [importText, setImportText] = useState("");
  const [importUrl, setImportUrl] = useState("");

  useEffect(() => {
    if (!importJob.result) return;
    setImportPreview(importJob.result.recipe);
    setImportWarnings({
      matches: importJob.result.restriction_warnings,
      crossContactMatches: importJob.result.cross_contact_warnings,
    });
    setImportProvenance(importJob.result.ingredient_provenance || null);
    setImportInstructionWarnings(importJob.result.instruction_warnings || []);
    importJob.clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [importJob.result]);

  // Batch import from a folder mounted into the container (see
  // Settings > Integrations > "Recipe import folder path", and the WIKI
  // for the compose volume mount). One scan can mean dozens of
  // sequential Ollama calls, so it reuses the same background-job plus
  // review-then-confirm shape as every other AI batch import -- nothing
  // lands in the recipes table until the household confirms.
  const [showFolderImport, setShowFolderImport] = useState(false);
  const folderScanJob = useBackgroundJob("chef.job.recipe_folder_import");
  const [folderScanEnqueueError, setFolderScanEnqueueError] = useState(null);
  const [folderItems, setFolderItems] = useState(null); // editable rows, or null when no preview is active
  const [folderScanMeta, setFolderScanMeta] = useState(null); // {skipped, truncated, scanned_folder, error}
  const [folderConfirmBusy, setFolderConfirmBusy] = useState(false);
  const [folderConfirmError, setFolderConfirmError] = useState(null);

  useEffect(() => {
    if (!folderScanJob.result) return;
    const { items, skipped, truncated, scanned_folder, error } = folderScanJob.result;
    setFolderScanMeta({ skipped, truncated, scanned_folder, error });
    setFolderItems(
      (items || []).map((it) => ({
        filename: it.filename,
        relative_path: it.relative_path,
        status: it.status,
        recipe: it.recipe,
        error: it.error,
        title: it.recipe?.title || it.filename,
        included: it.status === "ok",
      }))
    );
    folderScanJob.clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [folderScanJob.result]);

  // Bulk import from a browser's exported bookmarks HTML. Mirrors the
  // folder importer's shape deliberately -- upload, preview every item,
  // confirm a chosen subset -- and reuses its confirm endpoint, which
  // already takes a plain list of recipes.
  const [showBookmarkImport, setShowBookmarkImport] = useState(false);
  const bookmarkFileRef = useRef(null);
  const [bookmarkFolders, setBookmarkFolders] = useState(null); // [{path,count}] once a file is read
  const [bookmarkFolder, setBookmarkFolder] = useState("");
  const [bookmarkTotal, setBookmarkTotal] = useState(0);
  const [bookmarkError, setBookmarkError] = useState(null);
  const bookmarkScanJob = useBackgroundJob("chef.job.recipe_bookmark_import");

  // Bookmark results feed the SAME review list the folder scan uses --
  // the two differ only in where the item came from, and one review UI
  // that both fill is one place to get right.
  //
  // MUST stay below `bookmarkScanJob`'s declaration. The dependency
  // array is built during render, so reading `bookmarkScanJob.result`
  // above the `const` is a temporal-dead-zone ReferenceError that
  // blanks the entire page -- which is exactly what shipped, because a
  // TDZ error is invisible to both `vite build` and eslint's defaults.
  // `no-use-before-define` is now on to make that structural.
  useEffect(() => {
    if (!bookmarkScanJob.result) return;
    const { items, skipped, truncated } = bookmarkScanJob.result;
    setFolderScanMeta({ skipped, truncated, scanned_folder: bookmarkFolder || "all bookmarks", error: null });
    setFolderItems(
      (items || []).map((it) => ({
        filename: it.title,
        relative_path: it.folder_path ? `${it.folder_path} -- ${it.url}` : it.url,
        status: it.status,
        recipe: it.recipe,
        error: it.error,
        title: it.recipe?.title || it.title,
        included: it.status === "ok",
      }))
    );
    bookmarkScanJob.clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookmarkScanJob.result]);


  async function readBookmarkFolders() {
    setBookmarkError(null);
    setBookmarkFolders(null);
    const file = bookmarkFileRef.current?.files?.[0];
    if (!file) {
      setBookmarkError("Choose your exported bookmarks file first.");
      return;
    }
    const body = new FormData();
    body.append("file", file);
    try {
      const result = await api.post("/recipes/import-bookmarks/folders", body);
      setBookmarkFolders(result.folders);
      setBookmarkTotal(result.total);
      // Pre-select the folder that looks like the one they meant, rather
      // than making them find it in a list that includes every folder in
      // the browser.
      const likely = result.folders.find((f) => /recipe|food|cook/i.test(f.path));
      setBookmarkFolder(likely ? likely.path : "");
    } catch (e) {
      setBookmarkError(e.message);
    }
  }

  async function handleScanBookmarks() {
    setBookmarkError(null);
    bookmarkScanJob.clear();
    setFolderItems(null);
    const file = bookmarkFileRef.current?.files?.[0];
    if (!file) {
      setBookmarkError("Choose your exported bookmarks file first.");
      return;
    }
    const body = new FormData();
    body.append("file", file);
    if (bookmarkFolder) body.append("folder_path", bookmarkFolder);
    try {
      const enqueued = await api.post("/recipes/import-bookmarks/scan", body);
      bookmarkScanJob.poll(enqueued.job_id);
    } catch (e) {
      setBookmarkError(e.message);
    }
  }

  async function handleScanFolder() {
    setFolderScanEnqueueError(null);
    setFolderConfirmError(null);
    folderScanJob.clear();
    setFolderItems(null);
    setFolderScanMeta(null);
    try {
      const enqueued = await api.post("/recipes/import-folder/scan", {});
      folderScanJob.poll(enqueued.job_id);
    } catch (e) {
      setFolderScanEnqueueError(e.message);
    }
  }

  function updateFolderItemField(index, field, value) {
    setFolderItems((rows) => rows.map((r, i) => (i === index ? { ...r, [field]: value } : r)));
  }

  async function confirmFolderImport() {
    const toCreate = folderItems
      .filter((r) => r.included && r.status === "ok")
      .map((r) => ({ ...r.recipe, title: r.title }));
    if (toCreate.length === 0) return;
    setFolderConfirmBusy(true);
    setFolderConfirmError(null);
    try {
      await api.post("/recipes/import-folder/confirm", { recipes: toCreate });
      setFolderItems(null);
      setFolderScanMeta(null);
      refresh();
    } catch (e) {
      setFolderConfirmError(e.message);
    } finally {
      setFolderConfirmBusy(false);
    }
  }

  function discardFolderImport() {
    setFolderItems(null);
    setFolderScanMeta(null);
    setFolderConfirmError(null);
  }

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

  async function submitImport(formData) {
    setEnqueueError(null);
    importJob.clear();
    setImportPreview(null);
    setImportWarnings(null);
    try {
      const enqueued = await api.post("/recipes/import", formData);
      importJob.poll(enqueued.job_id);
    } catch (e) {
      setEnqueueError(e.message);
    }
  }

  async function handleImportText() {
    if (!importText.trim()) return;
    const formData = new FormData();
    formData.append("text", importText);
    await submitImport(formData);
  }

  async function handleImportUrl() {
    if (!importUrl.trim()) return;
    const formData = new FormData();
    formData.append("url", importUrl.trim());
    await submitImport(formData);
  }

  async function handleImportFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    await submitImport(formData);
    e.target.value = "";
  }

  async function confirmImport(editedPayload, imageFile) {
    const created = await api.post("/recipes", editedPayload);
    await uploadImageIfNeeded(created.id, imageFile);
    setImportPreview(null);
    setImportWarnings(null);
    setImportProvenance(null);
    setImportInstructionWarnings([]);
    setImportText("");
    setImportUrl("");
    refresh();
  }

  const visibleRecipes = applyFacets(recipes, facets);

  return (
    <div>
      <div className="page-toolbar">
        <input
          placeholder="Search recipes..."
          aria-label="Search recipes"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <label className="checkbox-label inline">
          <input type="checkbox" checked={stapleOnly} onChange={(e) => setStapleOnly(e.target.checked)} />
          Staples only
        </label>
        <button className="btn btn-primary" onClick={() => setShowAddForm((v) => !v)}>
          {showAddForm ? "Close" : "+ Add recipe"}
        </button>
        <button className="btn btn-secondary" onClick={() => setShowFolderImport((v) => !v)}>
          {showFolderImport ? "Close" : "📁 Import from folder"}
        </button>
        <button className="btn btn-secondary" onClick={() => setShowBookmarkImport((v) => !v)}>
          🔖 Import from bookmarks
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
            className="u-flex-1"
          />
          <button className="btn btn-secondary" onClick={handleImportUrl} disabled={importJob.busy || !importUrl.trim()}>
            {importJob.busy && <span className="busy-spinner" aria-hidden="true" />}
            {importJob.status === "queued" ? "Queued..." : importJob.busy ? "Fetching..." : "Import from URL"}
          </button>
        </div>
        <div className="form-row">
          <textarea
            rows={3}
            placeholder="...or paste a recipe's text here"
            value={importText}
            onChange={(e) => setImportText(e.target.value)}
            className="u-flex-1"
          />
        </div>
        <div className="form-actions">
          <button
            className="btn btn-secondary"
            onClick={handleImportText}
            disabled={importJob.busy || !importText.trim()}
          >
            {importJob.busy && <span className="busy-spinner" aria-hidden="true" />}
            {importJob.status === "queued" ? "Queued..." : importJob.busy ? "Parsing..." : "Parse text"}
          </button>
          <label className="btn btn-secondary file-btn">
            {importJob.busy && <span className="busy-spinner" aria-hidden="true" />}
            {importJob.status === "queued" ? "Queued..." : importJob.busy ? "Parsing..." : "Upload photo or PDF"}
            <input
              type="file"
              accept="image/*,application/pdf"
              onChange={handleImportFile}
              disabled={importJob.busy}
              hidden
            />
          </label>
        </div>
        {(enqueueError || importJob.error) && (
          <p className="error-text">Import failed: {enqueueError || importJob.error}</p>
        )}
      </div>

      {importPreview && (
        <div className="card">
          <h3>Review imported recipe</h3>
          <IngredientProvenance provenance={importProvenance} />
          {importInstructionWarnings.length > 0 && (
            <div className="provenance-note provenance-warn">
              <strong>Check these steps against the original</strong>
              <ul>
                {importInstructionWarnings.map((warning, i) => (
                  <li key={i}>{warning}</li>
                ))}
              </ul>
            </div>
          )}
          {importWarnings && (
            <RestrictionWarnings
              matches={importWarnings.matches}
              crossContactMatches={importWarnings.crossContactMatches}
              title="This recipe's ingredients conflict with a household restriction"
            />
          )}
          <RecipeForm
            initial={importPreview}
            importProvenance={importProvenance}
            onSubmit={confirmImport}
            onCancel={() => {
              setImportPreview(null);
              setImportWarnings(null);
              setImportProvenance(null);
              setImportInstructionWarnings([]);
            }}
          />
        </div>
      )}

      {showBookmarkImport && (
        <div className="card">
          <h3>Import from bookmarks</h3>
          <p className="hint">
            Export your bookmarks from your browser as an HTML file (Chrome and Edge: Bookmarks &gt; Bookmark
            manager &gt; ⋮ &gt; Export; Firefox: Bookmarks &gt; Manage bookmarks &gt; Import and Backup &gt; Export
            to HTML), then pick the folder your recipes are in. Each one is fetched and parsed the same way a
            single URL import is -- pages that publish structured recipe data cost no AI time at all. Nothing is
            saved until you review and confirm below.
          </p>
          <div className="form-row">
            <label>
              Bookmarks file
              <input ref={bookmarkFileRef} type="file" accept=".html,.htm,text/html" onChange={readBookmarkFolders} />
            </label>
            {bookmarkFolders && (
              <label>
                Folder ({bookmarkTotal} bookmark{bookmarkTotal === 1 ? "" : "s"} in the file)
                <select value={bookmarkFolder} onChange={(e) => setBookmarkFolder(e.target.value)}>
                  <option value="">Everything</option>
                  {bookmarkFolders.map((f) => (
                    <option key={f.path} value={f.path}>
                      {f.path} ({f.count})
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>
          <div className="form-actions">
            <button
              className="btn btn-secondary"
              onClick={handleScanBookmarks}
              disabled={bookmarkScanJob.busy || !bookmarkFolders}
            >
              {bookmarkScanJob.busy && <span className="busy-spinner" aria-hidden="true" />}
              {bookmarkScanJob.status === "queued"
                ? "Queued..."
                : bookmarkScanJob.busy
                  ? "Importing..."
                  : "Import these bookmarks"}
            </button>
          </div>
          {(bookmarkError || bookmarkScanJob.error) && (
            <p className="error-text">Bookmark import failed: {bookmarkError || bookmarkScanJob.error}</p>
          )}
        </div>
      )}

      {showFolderImport && (
        <div className="card">
          <h3>Import from folder</h3>
          <p className="hint">
            Scans the folder configured in <a href="#/settings">Settings &gt; Integrations</a> ("Recipe import
            folder path" -- e.g. a mounted OneDrive-synced folder) for recipe files (.txt, .md, .pdf, .json/.jsonld,
            .html/.htm) and parses each one, the same way a single upload does. Nothing is saved until you review
            and confirm below.
          </p>
          <div className="form-actions">
            <button className="btn btn-secondary" onClick={handleScanFolder} disabled={folderScanJob.busy}>
              {folderScanJob.busy && <span className="busy-spinner" aria-hidden="true" />}
              {folderScanJob.status === "queued" ? "Queued..." : folderScanJob.busy ? "Scanning..." : "Scan folder"}
            </button>
          </div>
          {(folderScanEnqueueError || folderScanJob.error) && (
            <p className="error-text">Folder scan failed: {folderScanEnqueueError || folderScanJob.error}</p>
          )}
        </div>
      )}

      {folderScanMeta?.error && <p className="error-text">{folderScanMeta.error}</p>}

      {folderItems && (
        <div className="card">
          <h3>Review folder import</h3>
          {folderScanMeta?.scanned_folder && (
            <p className="hint">
              Scanned: {folderScanMeta.scanned_folder}
              {folderScanMeta.truncated && " -- more files were found than this scan's cap allows; narrow the folder to catch the rest."}
            </p>
          )}
          {folderScanMeta?.skipped?.length > 0 && (
            <p className="hint">
              Skipped {folderScanMeta.skipped.length} file(s) (too large): {folderScanMeta.skipped.map((s) => s[0].split("/").pop()).join(", ")}
            </p>
          )}
          {folderItems.length === 0 ? (
            <p>No supported recipe files found in that folder.</p>
          ) : (
            <>
              <table className="data-table">
                <thead>
                  <tr>
                    <th></th>
                    <th>File</th>
                    <th>Title</th>
                    <th>Ingredients</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {folderItems.map((row, i) => (
                    <tr key={row.relative_path}>
                      <td data-label="Include">
                        <input
                          type="checkbox"
                          checked={row.included}
                          disabled={row.status !== "ok"}
                          onChange={(e) => updateFolderItemField(i, "included", e.target.checked)}
                        />
                      </td>
                      <td data-label="File">{row.relative_path}</td>
                      <td data-label="Title">
                        {row.status === "ok" ? (
                          <input value={row.title} onChange={(e) => updateFolderItemField(i, "title", e.target.value)} />
                        ) : (
                          "—"
                        )}
                      </td>
                      <td data-label="Ingredients">{row.status === "ok" ? row.recipe.ingredients.length : "—"}</td>
                      <td data-label="Status">
                        {row.status === "ok" ? (
                          <span className="tag">parsed</span>
                        ) : (
                          <span className="error-text">{row.error}</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {folderConfirmError && <p className="error-text">{folderConfirmError}</p>}
              <div className="form-actions">
                <button
                  className="btn btn-primary"
                  onClick={confirmFolderImport}
                  disabled={folderConfirmBusy || folderItems.filter((r) => r.included && r.status === "ok").length === 0}
                >
                  {folderConfirmBusy
                    ? "Adding..."
                    : `Add ${folderItems.filter((r) => r.included && r.status === "ok").length} recipe(s)`}
                </button>
                <button className="btn btn-secondary" onClick={discardFolderImport} disabled={folderConfirmBusy}>
                  Discard
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {error && <p className="error-text">{error}</p>}
      {!loading && recipes.length > 0 && (
        <RecipeFacetFilter
          recipes={recipes}
          value={facets}
          onChange={setFacets}
          matchCount={visibleRecipes.length}
        />
      )}
      {loading ? (
        <p>Loading recipes...</p>
      ) : recipes.length === 0 ? (
        <p>No recipes yet. Add one, or import from text/photo/PDF above.</p>
      ) : visibleRecipes.length === 0 ? (
        <p>
          No recipes match these filters.{" "}
          <button className="btn-link" onClick={() => setFacets(emptySelection())}>
            Clear them
          </button>{" "}
          to see all {recipes.length}.
        </p>
      ) : (
        <ul className="recipe-list">
          {visibleRecipes.map((r) => {
            // The evidence behind each derived tag, so a chip can explain
            // itself on hover rather than asserting something the
            // household has no way to check. See smart_tag_service.
            const bases = derivedTagBases(r);
            return (
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
                  {/* Derived tags are styled apart from the editable ones
                      above deliberately: one set is what somebody said
                      about this recipe, the other is what the app worked
                      out from its ingredients, and a household deciding
                      what to cook should be able to tell which is which
                      without clicking anything. */}
                  {(r.derived_tags || []).map((d) => (
                    <span
                      className="tag tag-derived"
                      key={d.tag}
                      title={bases.get(d.tag) ? `Worked out from: ${bases.get(d.tag)}` : undefined}
                    >
                      {derivedTagLabel(d.tag)}
                    </span>
                  ))}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
