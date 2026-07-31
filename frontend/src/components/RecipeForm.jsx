import { useState } from "react";

const emptyIngredient = { ingredient_name: "", quantity: "", unit: "", prep_note: "" };

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
  nutrition_calories: "",
  nutrition_protein_g: "",
  nutrition_carbs_g: "",
  nutrition_fat_g: "",
};

/** Shared add/edit recipe form. `initial` (from RecipeRead) pre-fills for edit mode. */
export default function RecipeForm({ initial, onSubmit, onCancel }) {
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
      nutrition_calories: initial.nutrition?.calories ?? "",
      nutrition_protein_g: initial.nutrition?.protein_g ?? "",
      nutrition_carbs_g: initial.nutrition?.carbs_g ?? "",
      nutrition_fat_g: initial.nutrition?.fat_g ?? "",
    };
  });

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
    if (form.nutrition_calories !== "") nutrition.calories = Number(form.nutrition_calories);
    if (form.nutrition_protein_g !== "") nutrition.protein_g = Number(form.nutrition_protein_g);
    if (form.nutrition_carbs_g !== "") nutrition.carbs_g = Number(form.nutrition_carbs_g);
    if (form.nutrition_fat_g !== "") nutrition.fat_g = Number(form.nutrition_fat_g);

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
    onSubmit(payload);
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
        <div className="form-row">
          <label>
            Calories
            <input type="number" value={form.nutrition_calories} onChange={(e) => set("nutrition_calories", e.target.value)} />
          </label>
          <label>
            Protein (g)
            <input type="number" value={form.nutrition_protein_g} onChange={(e) => set("nutrition_protein_g", e.target.value)} />
          </label>
          <label>
            Carbs (g)
            <input type="number" value={form.nutrition_carbs_g} onChange={(e) => set("nutrition_carbs_g", e.target.value)} />
          </label>
          <label>
            Fat (g)
            <input type="number" value={form.nutrition_fat_g} onChange={(e) => set("nutrition_fat_g", e.target.value)} />
          </label>
        </div>
      </fieldset>

      <label>
        Tags (comma-separated)
        <input value={form.tags} onChange={(e) => set("tags", e.target.value)} placeholder="quick, one_pot, gluten_free" />
      </label>

      <div className="form-actions">
        <button type="submit" className="btn btn-primary">
          Save recipe
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
