import { useEffect, useState } from "react";
import { api } from "../api";
import InventoryItemForm from "../components/InventoryItemForm";

function urgencyClass(score) {
  if (score >= 80) return "urgency-high";
  if (score >= 30) return "urgency-medium";
  if (score > 0) return "urgency-low";
  return "";
}

export default function InventoryPage() {
  const [items, setItems] = useState([]);
  const [urgencyByItemId, setUrgencyByItemId] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [categoryFilter, setCategoryFilter] = useState("");

  const [visionBusy, setVisionBusy] = useState(false);
  const [visionResult, setVisionResult] = useState(null); // { detected_items, raw_model_output }
  const [visionError, setVisionError] = useState(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const [list, suggestions] = await Promise.all([
        api.get(categoryFilter ? `/inventory?category=${encodeURIComponent(categoryFilter)}` : "/inventory"),
        api.get("/inventory/priority-suggestions?limit=50"),
      ]);
      setItems(list);
      const scoreMap = {};
      suggestions.forEach((s) => {
        scoreMap[s.item.id] = { score: s.urgency_score, reasons: s.reasons };
      });
      setUrgencyByItemId(scoreMap);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [categoryFilter]);

  async function handleCreate(payload) {
    await api.post("/inventory", payload);
    setShowAddForm(false);
    refresh();
  }

  async function handleUpdate(id, payload) {
    await api.patch(`/inventory/${id}`, payload);
    setEditingId(null);
    refresh();
  }

  async function handleDelete(id) {
    if (!window.confirm("Remove this item from inventory?")) return;
    await api.del(`/inventory/${id}`);
    refresh();
  }

  async function handleVisionUpload(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setVisionBusy(true);
    setVisionError(null);
    setVisionResult(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const result = await api.post("/inventory/vision-intake", formData);
      setVisionResult(result);
    } catch (err) {
      setVisionError(err.message);
    } finally {
      setVisionBusy(false);
      e.target.value = "";
    }
  }

  async function confirmVisionItems() {
    if (!visionResult?.detected_items?.length) return;
    const items = visionResult.detected_items.map((d) => ({
      name: d.name,
      category: d.category || "other",
      quantity: d.estimated_quantity ?? 1,
      unit: d.unit || null,
      expiration_date: d.expiration_date || null,
      source: "vision",
    }));
    await api.post("/inventory/vision-intake/confirm", { items });
    setVisionResult(null);
    refresh();
  }

  return (
    <div>
      <div className="page-toolbar">
        <select value={categoryFilter} onChange={(e) => setCategoryFilter(e.target.value)}>
          <option value="">All categories</option>
          <option value="pantry">Pantry</option>
          <option value="fridge">Fridge</option>
          <option value="freezer">Freezer</option>
          <option value="produce">Produce</option>
          <option value="spice">Spice</option>
          <option value="other">Other</option>
        </select>
        <button className="btn btn-primary" onClick={() => setShowAddForm((v) => !v)}>
          {showAddForm ? "Close" : "+ Add item"}
        </button>
        <label className="btn btn-secondary file-btn">
          {visionBusy ? "Analyzing..." : "📷 Add from photo"}
          <input type="file" accept="image/*" onChange={handleVisionUpload} disabled={visionBusy} hidden />
        </label>
      </div>

      {showAddForm && (
        <div className="card">
          <h3>New item</h3>
          <InventoryItemForm onSubmit={handleCreate} onCancel={() => setShowAddForm(false)} />
        </div>
      )}

      {visionError && <p className="error-text">Photo analysis failed: {visionError}</p>}

      {visionResult && (
        <div className="card">
          <h3>Detected from photo</h3>
          {visionResult.detected_items.length === 0 ? (
            <p>No items recognized. Try a clearer photo, or add items manually.</p>
          ) : (
            <>
              <ul className="vision-preview-list">
                {visionResult.detected_items.map((d, i) => (
                  <li key={i}>
                    <strong>{d.name}</strong>
                    {d.estimated_quantity != null && ` — ${d.estimated_quantity}${d.unit ? " " + d.unit : ""}`}
                    {" "}
                    <span className="tag">{d.category}</span>
                    {d.expiration_date && <span className="tag">exp {d.expiration_date}</span>}
                    {d.confidence_note && <em> ({d.confidence_note})</em>}
                  </li>
                ))}
              </ul>
              <div className="form-actions">
                <button className="btn btn-primary" onClick={confirmVisionItems}>
                  Add all to inventory
                </button>
                <button className="btn btn-secondary" onClick={() => setVisionResult(null)}>
                  Discard
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {error && <p className="error-text">{error}</p>}
      {loading ? (
        <p>Loading inventory...</p>
      ) : items.length === 0 ? (
        <p>No items yet. Add one manually or scan a photo of your pantry/fridge.</p>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Category</th>
              <th>Qty</th>
              <th>Expires</th>
              <th>Priority</th>
              <th>Why it matters</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) =>
              editingId === item.id ? (
                <tr key={item.id}>
                  <td colSpan={7}>
                    <InventoryItemForm
                      initial={item}
                      onSubmit={(payload) => handleUpdate(item.id, payload)}
                      onCancel={() => setEditingId(null)}
                    />
                  </td>
                </tr>
              ) : (
                <tr key={item.id} className={urgencyClass(urgencyByItemId[item.id]?.score || 0)}>
                  {/* data-label feeds the responsive-table CSS below the
                      mobile breakpoint (theme.css's .data-table rules) --
                      it re-labels each cell as a stacked "Label: value"
                      row via `content: attr(data-label)`, no JS needed for
                      the actual layout switch, just this one attribute. */}
                  <td data-label="Name">{item.name}</td>
                  <td data-label="Category">{item.category}</td>
                  <td data-label="Qty">
                    {item.quantity} {item.unit || ""}
                  </td>
                  <td data-label="Expires">{item.expiration_date || "—"}</td>
                  <td data-label="Priority">{item.is_priority ? "★" : ""}</td>
                  <td className="reasons-cell" data-label="Why it matters">
                    {(urgencyByItemId[item.id]?.reasons || []).join("; ")}
                  </td>
                  <td className="row-actions" data-label="Actions">
                    <button className="btn-link" onClick={() => setEditingId(item.id)}>
                      Edit
                    </button>
                    <button className="btn-link btn-link-danger" onClick={() => handleDelete(item.id)}>
                      Delete
                    </button>
                  </td>
                </tr>
              )
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
