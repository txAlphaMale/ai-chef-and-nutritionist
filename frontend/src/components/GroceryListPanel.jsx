import { useEffect, useState } from "react";
import { api } from "../api";

// Backlog B5.4 -- same six-value taxonomy InventoryItem already uses,
// in a sensible shop-the-store order (produce/fridge/freezer first,
// since those are usually the outer ring of a grocery store; pantry/
// spice aisles last; "other" and uncategorized always at the very end).
const CATEGORY_ORDER = ["produce", "fridge", "freezer", "spice", "pantry", "other"];
const CATEGORY_LABELS = {
  produce: "Produce",
  fridge: "Fridge",
  freezer: "Freezer",
  spice: "Spice aisle",
  pantry: "Pantry",
  other: "Other",
};
const UNCATEGORIZED_LABEL = "Uncategorized";

function groupByCategory(items) {
  const groups = new Map();
  for (const item of items) {
    const key = item.category || null;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  const ordered = [];
  for (const key of CATEGORY_ORDER) {
    if (groups.has(key)) {
      ordered.push([key, groups.get(key)]);
      groups.delete(key);
    }
  }
  if (groups.has(null)) {
    ordered.push([null, groups.get(null)]);
    groups.delete(null);
  }
  // Any category value not in CATEGORY_ORDER (shouldn't happen via this
  // app's own UI, but a hand-edited DB row could have anything) -- shown
  // rather than silently dropped.
  for (const [key, value] of groups) ordered.push([key, value]);
  return ordered;
}

/** Self-contained grocery list for one meal plan: fetches its own items
 * given `planId`, and re-fetches whenever `refreshKey` changes (bumped
 * by the parent after actions that could change what's needed, like
 * confirming/skipping an entry or swapping a recipe). */
export default function GroceryListPanel({ planId, refreshKey }) {
  const [items, setItems] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [newName, setNewName] = useState("");
  const [newQty, setNewQty] = useState("");
  const [newUnit, setNewUnit] = useState("");
  const [newCategory, setNewCategory] = useState("");
  const [busy, setBusy] = useState(false);
  // Backlog B6.1 -- projected spend across still-unpurchased items,
  // computed live from currently-tracked inventory prices; refetched
  // alongside the list itself since checking an item off as purchased
  // changes what's still "projected."
  const [cost, setCost] = useState(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const list = await api.get(`/meal-plans/${planId}/grocery-list`);
      setItems(list);
      api
        .get(`/meal-plans/${planId}/grocery-list/cost`)
        .then(setCost)
        .catch(() => setCost(null));
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (planId) refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planId, refreshKey]);

  async function togglePurchased(item) {
    await api.patch(`/meal-plans/${planId}/grocery-list/${item.id}`, { is_purchased: !item.is_purchased });
    refresh();
  }

  async function removeItem(item) {
    await api.del(`/meal-plans/${planId}/grocery-list/${item.id}`);
    refresh();
  }

  async function addItem(e) {
    e.preventDefault();
    if (!newName.trim()) return;
    setBusy(true);
    try {
      await api.post(`/meal-plans/${planId}/grocery-list`, {
        ingredient_name: newName.trim(),
        quantity: newQty === "" ? null : Number(newQty),
        unit: newUnit || null,
        category: newCategory || null,
      });
      setNewName("");
      setNewQty("");
      setNewUnit("");
      setNewCategory("");
      refresh();
    } finally {
      setBusy(false);
    }
  }

  async function regenerate() {
    setBusy(true);
    try {
      await api.post(`/meal-plans/${planId}/grocery-list/regenerate`, {});
      refresh();
    } finally {
      setBusy(false);
    }
  }

  if (!planId) return null;

  return (
    <div className="card">
      <div className="page-toolbar">
        <h3 style={{ margin: 0 }}>Grocery list</h3>
        <button className="btn btn-secondary btn-sm" onClick={regenerate} disabled={busy}>
          Recompute from inventory
        </button>
      </div>
      <p className="hint">Auto items are recipe ingredients still needed after what's already in inventory.</p>
      {cost && cost.provenance !== "no_data" && cost.total_cost != null && (
        <p className="hint">
          Estimated remaining spend: ${cost.total_cost.toFixed(2)}
          {cost.provenance === "partial" && ` (${cost.resolved_count}/${cost.total_count} items priced -- not the full list)`}
        </p>
      )}
      {error && <p className="error-text">{error}</p>}
      {loading ? (
        <p>Loading grocery list...</p>
      ) : items.length === 0 ? (
        <p>Nothing needed -- inventory already covers this plan.</p>
      ) : (
        groupByCategory(items).map(([category, groupItems]) => (
          <div className="grocery-category-group" key={category ?? "uncategorized"}>
            <h4 className="grocery-category-heading">{category ? CATEGORY_LABELS[category] || category : UNCATEGORIZED_LABEL}</h4>
            <ul className="grocery-list">
              {groupItems.map((item) => (
                <li key={item.id} className={item.is_purchased ? "grocery-item purchased" : "grocery-item"}>
                  <label className="checkbox-label inline">
                    <input type="checkbox" checked={item.is_purchased} onChange={() => togglePurchased(item)} />
                    <span>
                      {item.ingredient_name}
                      {item.quantity != null && ` — ${item.quantity}${item.unit ? " " + item.unit : ""}`}
                      {item.source === "manual" && <span className="tag">manual</span>}
                    </span>
                  </label>
                  <button className="btn-link btn-link-danger" onClick={() => removeItem(item)}>
                    ✕
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
      {/* Accessibility fix (2026-08-02, backlog B7.4): these four fields
          previously relied on `placeholder` alone, which isn't a reliable
          accessible name (it disappears once text is entered and isn't
          treated as a label by every screen reader). The checkbox above
          (line ~155) is already fine as-is -- it's wrapped in a <label>
          with visible text, which is a valid native accessible name. */}
      <form className="form-row" onSubmit={addItem}>
        <input
          placeholder="Add item"
          aria-label="New grocery item name"
          value={newName}
          onChange={(e) => setNewName(e.target.value)}
        />
        <input
          placeholder="qty"
          aria-label="New grocery item quantity"
          type="number"
          step="any"
          value={newQty}
          onChange={(e) => setNewQty(e.target.value)}
          style={{ maxWidth: 90 }}
        />
        <input
          placeholder="unit"
          aria-label="New grocery item unit"
          value={newUnit}
          onChange={(e) => setNewUnit(e.target.value)}
          style={{ maxWidth: 90 }}
        />
        <select
          value={newCategory}
          aria-label="New grocery item category"
          onChange={(e) => setNewCategory(e.target.value)}
          style={{ maxWidth: 130 }}
        >
          <option value="">(guess aisle)</option>
          {CATEGORY_ORDER.map((c) => (
            <option key={c} value={c}>
              {CATEGORY_LABELS[c]}
            </option>
          ))}
        </select>
        <button className="btn btn-secondary btn-sm" type="submit" disabled={busy || !newName.trim()}>
          Add
        </button>
      </form>
    </div>
  );
}
