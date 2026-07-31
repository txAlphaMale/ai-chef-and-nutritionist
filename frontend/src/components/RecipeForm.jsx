import { useState } from "react";
import { api, backendOrigin } from "../api";

const emptyIngredient = { ingredient_name: "", quantity: "", unit: "", prep_note: "" };

// Backlog B1.3: the single nutrition key set every AI surface in this app
// now asks for (see backend/app/services/food_data_service.py's
// NUTRITION_KEYS) -- kept here as one list so the form-state keys,
// hydration, and submit payload can't drift apart the way this form used
// to (it only ever knew about 4 of up to 9 possible keys, silently
// dropping fiber_g/sodium_mg/cholesterol_mg/etc. on every save).
const NUTRIENT_FIELDS = [
  { key: "calories", label: "Calories" },
  { key: "protein_g", label: "Protein (g)" },
  { key: "carbs_g", label: "Carbs (g)" },
  { key: "fat_g", label: "Fat (g)" },
  { key: "saturated_fat_g", label: "Saturated fat (g)" },
  { key: "fiber_g", label: "Fiber (g)" },
  { key: "sugars_g", label: "Sugars (g)" },
  { key: "sodium_mg", label: "Sodium (mg)" },
  { key: "cholesterol_mg", label: "Cholesterol (mg)" },
];

const emptyForm = {
  title: "",
  description: "",
  default_servings: 2,
  prep_time_minutes: "",
  cook_time_minutes: "",
  instructions: [""],
  ingredients: [{ ...emptyIngredient }],
  tags: "",
  tips: [],
  source_url: "",
  source_name: "",
  source_author: "",
  ...Object.fromEntries(NUTRIENT_FIELDS.map((f) => [`nutrition_${f.key}`, ""])),
};

/** Shared add/edit recipe form. `initial` (from RecipeRead, or a RecipeCreate-
 * shaped chat/import proposal) pre-fills the fields. `submitLabel` lets a
 * caller override the button text for non-"edit" submit flows (e.g.
 * RecipeChat's "Save as new variant" / "Update this recipe" review step). */
export default function RecipeForm({ initial, onSubmit, onCancel, submitLabel = "Save recipe" }) {
  const [form, setForm] = useState(() => {
    if (!initial) return emptyForm;
    return {
      title: initial.title || "",
      description: initial.description || "",
      default_servings: initial.default_servings || 2,
      prep_time_minutes: initial.prep_time_minutes ?? "",
      cook_time_minutes: initial.cook_time_minutes ?? "",
      instructions: initial.instructions?.length ? initial.instructions : [""],
      ingredients: initial.ingredients?.length
        ? initial.ingredients.map((i) => ({ ...i, quantity: i.quantity ?? "" }))
        : [{ ...emptyIngredient }],
      tags: (initial.tags || []).join(", "),
      tips: initial.tips?.length ? initial.tips : [],
      source_url: initial.source_url || "",
      source_name: initial.source_name || "",
      source_author: initial.source_author || "",
      ...Object.fromEntries(
        NUTRIENT_FIELDS.map((f) => [`nutrition_${f.key}`, initial.nutrition?.[f.key] ?? ""])
      ),
    };
  });

  // Dish photo: handled outside the JSON `form` state/payload since it's
  // a separate multipart endpoint (POST/DELETE /api/recipes/{id}/image),
  // not a field the create/update endpoints accept directly. In edit
  // mode (initial.id present) a new photo uploads immediately on
  // selection -- simpler than staging it alongside an unrelated field
  // edit. In add mode / the import-review step (no id yet) there's
  // nothing to upload to yet, so the selected File is instead handed to
  // the parent via onSubmit's second argument, to upload right after the
  // recipe is created. `hasImage` starts from whether the recipe already
  // has one (a persisted image, or -- in the import-preview case --
  // one auto-captured from the source but not yet attached to a saved
  // recipe) and is kept in local state so a remove/replace updates the
  // preview immediately without waiting on the parent to reload.
  const [hasImage, setHasImage] = useState(Boolean(initial?.image_path));
  const [imageVersion, setImageVersion] = useState(0);
  const [imageBusy, setImageBusy] = useState(false);
  const [imageError, setImageError] = useState(null);
  const [pendingImageFile, setPendingImageFile] = useState(null);

  async function handleImageSelect(e) {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    if (!initial?.id) {
      setPendingImageFile(file);
      setHasImage(true);
      return;
    }
    setImageBusy(true);
    setImageError(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      await api.post(`/recipes/${initial.id}/image`, formData);
      setHasImage(true);
      setImageVersion((v) => v + 1);
    } catch (err) {
      setImageError(err.message);
    } finally {
      setImageBusy(false);
    }
  }

  async function handleImageRemove() {
    if (pendingImageFile) {
      setPendingImageFile(null);
      setHasImage(false);
      return;
    }
    if (!initial?.id) return;
    setImageBusy(true);
    setImageError(null);
    try {
      await api.del(`/recipes/${initial.id}/image`);
      setHasImage(false);
      setImageVersion((v) => v + 1);
    } catch (err) {
      setImageError(err.message);
    } finally {
      setImageBusy(false);
    }
  }

  function set(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  function setInstruction(i, value) {
    setForm((f) => ({ ...f, instructions: f.instructions.map((s, idx) => (idx === i ? value : s)) }));
  }

  function addInstruction() {
    setForm((f) => ({ ...f, instructions: [...f.instructions, ""] }));
  }

  function removeInstruction(i) {
    setForm((f) => ({ ...f, instructions: f.instructions.filter((_, idx) => idx !== i) }));
  }

  function setIngredient(i, field, value) {
    setForm((f) => ({
      ...f,
      ingredients: f.ingredients.map((ing, idx) => (idx === i ? { ...ing, [field]: value } : ing)),
    }));
  }

  function addIngredient() {
    setForm((f) => ({ ...f, ingredients: [...f.ingredients, { ...emptyIngredient }] }));
  }

  function removeIngredient(i) {
    setForm((f) => ({ ...f, ingredients: f.ingredients.filter((_, idx) => idx !== i) }));
  }

  function setTip(i, value) {
    setForm((f) => ({ ...f, tips: f.tips.map((t, idx) => (idx === i ? value : t)) }));
  }

  function addTip() {
    setForm((f) => ({ ...f, tips: [...f.tips, ""] }));
  }

  function removeTip(i) {
    setForm((f) => ({ ...f, tips: f.tips.filter((_, idx) => idx !== i) }));
  }

  function handleSubmit(e) {
    e.preventDefault();
    const nutrition = {};
    for (const f of NUTRIENT_FIELDS) {
      const value = form[`nutrition_${f.key}`];
      if (value !== "") nutrition[f.key] = Number(value);
    }

    const payload = {
      title: form.title,
      description: form.description || null,
      default_servings: Number(form.default_servings) || 2,
      prep_time_minutes: form.prep_time_minutes === "" ? null : Number(form.prep_time_minutes),
      cook_time_minutes: form.cook_time_minutes === "" ? null : Number(form.cook_time_minutes),
      instructions: form.instructions.map((s) => s.trim()).filter(Boolean),
      ingredients: form.ingredients
        .filter((ing) => ing.ingredient_name.trim())
        .map((ing) => ({
          ingredient_name: ing.ingredient_name.trim(),
          quantity: ing.quantity === "" ? null : Number(ing.quantity),
          unit: ing.unit || null,
          prep_note: ing.prep_note || null,
        })),
      tags: form.tags
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean),
      tips: form.tips.map((t) => t.trim()).filter(Boolean),
      source_url: form.source_url || null,
      source_name: form.source_name || null,
      source_author: form.source_author || null,
      nutrition,
    };
    // Edit mode manages the image via its own dedicated endpoints (see
    // handleImageSelect/handleImageRemove above) and add-mode uploads
    // separately after creation (see pendingImageFile below), so
    // image_path is normally left out of this JSON payload entirely.
    // The one exception: confirming an import whose source already had
    // an auto-captured image (initial.image_path set, no initial.id yet,
    // no separately-picked pendingImageFile) -- there the file already
    // exists on disk from the import preview step, and this create call
    // is the only place that path can still be attached to the new row.
    if (!initial?.id && hasImage && !pendingImageFile && initial?.image_path) {
      payload.image_path = initial.image_path;
    }
    onSubmit(payload, pendingImageFile);
  }

  return (
    <form className="item-form recipe-form" onSubmit={handleSubmit}>
      <div className="form-row">
        <label>
          Title
          <input required value={form.title} onChange={(e) => set("title", e.target.value)} />
        </label>
        <label>
          Default servings
          <input type="number" min="1" value={form.default_servings} onChange={(e) => set("default_servings", e.target.value)} />
        </label>
      </div>
      <label>
        Description
        <textarea rows={2} value={form.description} onChange={(e) => set("description", e.target.value)} />
      </label>
      <div className="form-row">
        <label>
          Prep time (min)
          <input type="number" min="0" value={form.prep_time_minutes} onChange={(e) => set("prep_time_minutes", e.target.value)} />
        </label>
        <label>
          Cook time (min)
          <input type="number" min="0" value={form.cook_time_minutes} onChange={(e) => set("cook_time_minutes", e.target.value)} />
        </label>
      </div>

      <fieldset>
        <legend>Dish photo (optional)</legend>
        {hasImage && (
          <div className="recipe-image-preview">
            {initial?.id ? (
              <img
                src={`${backendOrigin}/api/recipes/${initial.id}/image?v=${imageVersion}`}
                alt="Recipe dish"
              />
            ) : (
              <p className="hint">
                {pendingImageFile
                  ? `Selected: ${pendingImageFile.name} -- uploads once you save.`
                  : "A dish photo was captured from the source and will be attached when you save."}
              </p>
            )}
          </div>
        )}
        <div className="form-actions">
          <label className="btn btn-secondary btn-sm file-btn">
            {imageBusy ? "Uploading..." : hasImage ? "Replace photo" : "Upload photo"}
            <input type="file" accept="image/jpeg,image/png,image/webp,image/gif" onChange={handleImageSelect} disabled={imageBusy} hidden />
          </label>
          {hasImage && (
            <button type="button" className="btn-link btn-link-danger" onClick={handleImageRemove} disabled={imageBusy}>
              Remove photo
            </button>
          )}
        </div>
        {imageError && <p className="error-text">{imageError}</p>}
      </fieldset>

      <fieldset>
        <legend>Ingredients</legend>
        {form.ingredients.map((ing, i) => (
          <div className="form-row ingredient-row" key={i}>
            <input
              placeholder="ingredient"
              value={ing.ingredient_name}
              onChange={(e) => setIngredient(i, "ingredient_name", e.target.value)}
            />
            <input
              placeholder="qty"
              type="number"
              step="any"
              value={ing.quantity}
              onChange={(e) => setIngredient(i, "quantity", e.target.value)}
            />
            <input placeholder="unit" value={ing.unit} onChange={(e) => setIngredient(i, "unit", e.target.value)} />
            <input
              placeholder="prep note"
              value={ing.prep_note}
              onChange={(e) => setIngredient(i, "prep_note", e.target.value)}
            />
            <button type="button" className="btn-link btn-link-danger" onClick={() => removeIngredient(i)}>
              ✕
            </button>
          </div>
        ))}
        <button type="button" className="btn btn-secondary btn-sm" onClick={addIngredient}>
          + Ingredient
        </button>
      </fieldset>

      <fieldset>
        <legend>Instructions</legend>
        {form.instructions.map((step, i) => (
          <div className="form-row instruction-row" key={i}>
            <span className="step-number">{i + 1}.</span>
            <input value={step} onChange={(e) => setInstruction(i, e.target.value)} placeholder={`Step ${i + 1}`} />
            <button type="button" className="btn-link btn-link-danger" onClick={() => removeInstruction(i)}>
              ✕
            </button>
          </div>
        ))}
        <button type="button" className="btn btn-secondary btn-sm" onClick={addInstruction}>
          + Step
        </button>
      </fieldset>

      <fieldset>
        <legend>Tips, substitutions &amp; variations (optional)</legend>
        {form.tips.map((tip, i) => (
          <div className="form-row instruction-row" key={i}>
            <input value={tip} onChange={(e) => setTip(i, e.target.value)} placeholder="e.g. swap butter for coconut oil" />
            <button type="button" className="btn-link btn-link-danger" onClick={() => removeTip(i)}>
              ✕
            </button>
          </div>
        ))}
        <button type="button" className="btn btn-secondary btn-sm" onClick={addTip}>
          + Tip
        </button>
      </fieldset>

      <fieldset>
        <legend>Source (optional, auto-filled when imported)</legend>
        <div className="form-row">
          <label>
            Source URL
            <input value={form.source_url} onChange={(e) => set("source_url", e.target.value)} placeholder="https://..." />
          </label>
          <label>
            Site / publication
            <input value={form.source_name} onChange={(e) => set("source_name", e.target.value)} />
          </label>
          <label>
            Author
            <input value={form.source_author} onChange={(e) => set("source_author", e.target.value)} />
          </label>
        </div>
      </fieldset>

      <fieldset>
        <legend>Nutrition (per serving, optional)</legend>
        {initial?.nutrition_provenance === "computed" || initial?.nutrition_provenance === "partial" ? (
          <p className="hint">
            These values were computed from real ingredient data. Editing and saving here will mark them as an
            unverified estimate again -- use "Compute from ingredients" on the recipe page instead if you just want
            to refresh them.
          </p>
        ) : (
          <p className="hint">Best-effort estimates -- leave blank for anything unknown.</p>
        )}
        <div className="nutrition-field-grid">
          {NUTRIENT_FIELDS.map((f) => (
            <label key={f.key}>
              {f.label}
              <input
                type="number"
                step="any"
                value={form[`nutrition_${f.key}`]}
                onChange={(e) => set(`nutrition_${f.key}`, e.target.value)}
              />
            </label>
          ))}
        </div>
      </fieldset>

      <label>
        Tags (comma-separated)
        <input value={form.tags} onChange={(e) => set("tags", e.target.value)} placeholder="quick, one_pot, gluten_free" />
      </label>

      <div className="form-actions">
        <button type="submit" className="btn btn-primary">
          {submitLabel}
        </button>
        {onCancel && (
          <button type="button" className="btn btn-secondary" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
