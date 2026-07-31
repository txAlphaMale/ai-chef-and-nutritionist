import { useEffect, useState } from "react";
import { api } from "../api";

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
  const [busy, setBusy] = useState(false);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const list = await api.get(`/meal-plans/${planId}/grocery-list`);
      setItems(list);
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
      });
      setNewName("");
      setNewQty("");
      setNewUnit("");
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
      {error && <p className="error-text">{error}</p>}
      {loading ? (
        <p>Loading grocery list...</p>
      ) : items.length === 0 ? (
        <p>Nothing needed -- inventory already covers this plan.</p>
      ) : (
        <ul className="grocery-list">
          {items.map((item) => (
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
      )}
      <form className="form-row" onSubmit={addItem}>
        <input placeholder="Add item" value={newName} onChange={(e) => setNewName(e.target.value)} />
        <input placeholder="qty" type="number" step="any" value={newQty} onChange={(e) => setNewQty(e.target.value)} style={{ maxWidth: 90 }} />
        <input placeholder="unit" value={newUnit} onChange={(e) => setNewUnit(e.target.value)} style={{ maxWidth: 90 }} />
        <button className="btn btn-secondary btn-sm" type="submit" disabled={busy || !newName.trim()}>
          Add
        </button>
      </form>
    </div>
  );
}
