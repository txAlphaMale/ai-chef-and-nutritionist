import { useEffect, useRef, useState } from "react";
import { api } from "../api";

const CATEGORIES = ["pantry", "fridge", "freezer", "produce", "spice", "other"];

// Quantity model redesign (2026-08-02, author-requested): a curated list
// of real, convertible measurement units (matches backend/app/services/
// unit_conversion_service.py's own registry, plus "count" for items with
// no sub-unit) offered via a <datalist> -- suggestions, not a hard
// enum, so a household with an unusual unit ("clove", "slice") isn't
// locked out. See InventoryItemForm's own module comment below for why
// this replaced the old free-text "lbs, count, box..." field, which
// used to double as BOTH the measurement and the packaging description.
const MEASUREMENT_UNITS = [
  "count",
  "oz",
  "lb",
  "g",
  "kg",
  "ml",
  "l",
  "cup",
  "tbsp",
  "tsp",
  "fl_oz",
  "pt",
  "qt",
  "gal",
];

const emptyForm = {
  name: "",
  category: "pantry",
  quantity: 1,
  unit: "",
  purchased_quantity: "",
  package_quantity: "",
  package_count: 1,
  package_descriptor: "",
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

  // Quantity model redesign (2026-08-02, author-requested): separates
  // "how is this measured" (unit + package size, both numeric/canonical)
  // from "how is this packaged" (a purely descriptive container word),
  // and tracks on-hand quantity distinctly from a purchase-time
  // snapshot -- see app/models/inventory.py's InventoryItem docstring
  // (backend) for the full before/after rationale. Package count *
  // package size auto-computes the on-hand "Quantity" field below
  // whenever a package size is entered (e.g. 2 packages x 8 oz = 16 oz
  // on hand) -- but only until the household directly edits Quantity
  // themselves, tracked by `quantityTouched`, so a manual correction
  // (e.g. "actually this one's already half used") doesn't keep getting
  // silently overwritten by a later package-field tweak.
  const quantityTouchedRef = useRef(false);

  useEffect(() => {
    if (quantityTouchedRef.current) return;
    const count = Number(form.package_count);
    const size = Number(form.package_quantity);
    if (form.package_quantity !== "" && !Number.isNaN(count) && !Number.isNaN(size) && size > 0) {
      setForm((f) => ({ ...f, quantity: count * size }));
    }
     
  }, [form.package_count, form.package_quantity]);

  function setQuantity(value) {
    quantityTouchedRef.current = true;
    set("quantity", value);
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
      purchased_quantity: form.purchased_quantity === "" || form.purchased_quantity == null ? null : Number(form.purchased_quantity),
      package_quantity: form.package_quantity === "" || form.package_quantity == null ? null : Number(form.package_quantity),
      package_count: form.package_count === "" || form.package_count == null ? null : Number(form.package_count),
      package_descriptor: form.package_descriptor || null,
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
      <p className="hint">
        Package details are optional -- fill them in to auto-calculate the on-hand quantity below (e.g. 2 packages x
        8 oz = 16 oz on hand). Skip them and just set the on-hand quantity/unit directly for loose items with no
        fixed package size.
      </p>
      <div className="form-row">
        <label>
          Package count
          <input
            type="number"
            step="any"
            value={form.package_count}
            onChange={(e) => set("package_count", e.target.value)}
            placeholder="1"
          />
        </label>
        <label>
          Package size
          <input
            type="number"
            step="any"
            value={form.package_quantity}
            onChange={(e) => set("package_quantity", e.target.value)}
            placeholder="e.g. 8"
          />
        </label>
        <label>
          Unit
          <input
            list="measurement-units"
            value={form.unit || ""}
            onChange={(e) => set("unit", e.target.value)}
            placeholder="oz, lb, count..."
          />
          <datalist id="measurement-units">
            {MEASUREMENT_UNITS.map((u) => (
              <option key={u} value={u} />
            ))}
          </datalist>
        </label>
        <label>
          Container
          <input
            value={form.package_descriptor || ""}
            onChange={(e) => set("package_descriptor", e.target.value)}
            placeholder="bag, can, bottle... (optional)"
          />
        </label>
      </div>
      <div className="form-row">
        <label>
          On-hand quantity
          <input type="number" step="any" value={form.quantity} onChange={(e) => setQuantity(e.target.value)} />
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
          Price paid (whole purchase)
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
      {initial?.id != null && (
        <p className="hint">
          Originally purchased:{" "}
          <input
            type="number"
            step="any"
            className="input-unit"
            value={form.purchased_quantity ?? ""}
            onChange={(e) => set("purchased_quantity", e.target.value)}
            placeholder={String(initial.quantity ?? "")}
          />{" "}
          {form.unit || ""} -- used as the cost-per-unit baseline (B6.1); leave blank to keep using the on-hand
          amount for that math.
        </p>
      )}
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
