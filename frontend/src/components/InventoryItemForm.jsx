import { useEffect, useRef, useState } from "react";
import { api } from "../api";

const CATEGORIES = ["pantry", "fridge", "freezer", "produce", "spice", "other"];

const emptyForm = {
  name: "",
  category: "pantry",
  quantity: 1,
  unit: "",
  location: "",
  expiration_date: "",
  is_priority: false,
  priority_note: "",
  unit_price: "",
  notes: "",
};

/** Shared add/edit form. Pass `initial` + `onSubmit` for edit mode. */
export default function InventoryItemForm({ initial, onSubmit, onCancel }) {
  const [form, setForm] = useState({ ...emptyForm, ...initial });

  function set(field, value) {
    setForm((f) => ({ ...f, [field]: value }));
  }

  // Backlog B4.3 (2026-08-01): auto-suggests an expiration date from the
  // shipped USDA FoodKeeper catalog as the household types a name --
  // directly serves the brief's "keep expiration information as much as
  // reasonably possible" plus the author's own stated forgotten-pantry-
  // item problem. Deliberately NEVER auto-fills the field itself (that
  // would silently override a value the household might already be
  // mid-typing) -- shows the suggestion alongside an explicit "Use this
  // date" button instead, same pattern as SettingsPage's Google Calendar
  // redirect-URI suggestion buttons. Debounced (500ms) so it doesn't fire
  // a request on every keystroke, and only looks up once the name is at
  // least 3 characters (avoids a flood of near-meaningless single-letter
  // lookups). Skipped entirely once the household has already set an
  // expiration date -- nothing to suggest at that point.
  const [suggestion, setSuggestion] = useState(null);
  const [suggestionLoading, setSuggestionLoading] = useState(false);
  const debounceRef = useRef(null);

  useEffect(() => {
    setSuggestion(null);
    if (form.expiration_date || form.name.trim().length < 3) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      setSuggestionLoading(true);
      try {
        const params = new URLSearchParams({ name: form.name.trim(), category: form.category });
        const result = await api.get(`/inventory/shelf-life-suggestion?${params.toString()}`);
        if (result.found) setSuggestion(result);
      } catch {
        // Silent -- this is a nice-to-have suggestion, not a required
        // field; a failed lookup just means no suggestion shows, same as
        // an unmatched item name.
      } finally {
        setSuggestionLoading(false);
      }
    }, 500);
    return () => clearTimeout(debounceRef.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form.name, form.category, form.expiration_date]);

  function applySuggestedDate() {
    if (suggestion?.suggested_expiration_date) {
      set("expiration_date", suggestion.suggested_expiration_date);
      setSuggestion(null);
    }
  }

  function handleSubmit(e) {
    e.preventDefault();
    const payload = {
      ...form,
      quantity: Number(form.quantity) || 0,
      expiration_date: form.expiration_date || null,
      unit: form.unit || null,
      location: form.location || null,
      priority_note: form.priority_note || null,
      unit_price: form.unit_price === "" || form.unit_price == null ? null : Number(form.unit_price),
      notes: form.notes || null,
    };
    onSubmit(payload);
  }

  return (
    <form className="item-form" onSubmit={handleSubmit}>
      <div className="form-row">
        <label>
          Name
          <input required value={form.name} onChange={(e) => set("name", e.target.value)} />
        </label>
        <label>
          Category
          <select value={form.category} onChange={(e) => set("category", e.target.value)}>
            {CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </label>
      </div>
      <div className="form-row">
        <label>
          Quantity
          <input type="number" step="any" value={form.quantity} onChange={(e) => set("quantity", e.target.value)} />
        </label>
        <label>
          Unit
          <input value={form.unit || ""} onChange={(e) => set("unit", e.target.value)} placeholder="lbs, count, box..." />
        </label>
        <label>
          Expires
          <input type="date" value={form.expiration_date || ""} onChange={(e) => set("expiration_date", e.target.value)} />
        </label>
      </div>
      {suggestionLoading && <p className="hint">Checking USDA FoodKeeper for a shelf-life estimate...</p>}
      {suggestion && (
        <p className="hint">
          Estimated ({suggestion.days_min ?? "?"}
          {suggestion.days_max && suggestion.days_max !== suggestion.days_min ? `-${suggestion.days_max}` : ""} days,
          USDA FoodKeeper match: "{suggestion.matched_name}"):{" "}
          <button type="button" className="btn-link" onClick={applySuggestedDate}>
            Use {suggestion.suggested_expiration_date}
          </button>
        </p>
      )}
      <div className="form-row">
        <label>
          Location
          <input value={form.location || ""} onChange={(e) => set("location", e.target.value)} placeholder="top shelf..." />
        </label>
        <label>
          Price paid
          <input
            type="number"
            step="any"
            value={form.unit_price ?? ""}
            onChange={(e) => set("unit_price", e.target.value)}
            placeholder="optional"
          />
        </label>
        <label className="checkbox-label">
          <input type="checkbox" checked={!!form.is_priority} onChange={(e) => set("is_priority", e.target.checked)} />
          Priority: use this up
        </label>
      </div>
      {form.is_priority && (
        <label>
          Priority note
          <input value={form.priority_note || ""} onChange={(e) => set("priority_note", e.target.value)} placeholder="why / how often" />
        </label>
      )}
      <label>
        Notes
        <textarea value={form.notes || ""} onChange={(e) => set("notes", e.target.value)} rows={2} />
      </label>
      <div className="form-actions">
        <button type="submit" className="btn btn-primary">
          Save
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
