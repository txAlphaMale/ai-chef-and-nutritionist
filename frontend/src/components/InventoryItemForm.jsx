import { useState } from "react";

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
