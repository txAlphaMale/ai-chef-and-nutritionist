import { useEffect, useState } from "react";
import { api } from "../api";
import InventoryItemForm from "../components/InventoryItemForm";

// Backlog B4.2 (author-requested 2026-08-01) -- same category enum the
// backend's InventoryItemBase/RECEIPT_IMPORT_PROMPT use, duplicated here
// like InventoryItemForm.jsx's own local CATEGORIES rather than exported,
// since it's small and rarely changes.
const IMPORT_CATEGORIES = ["pantry", "fridge", "freezer", "produce", "spice", "other"];

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

  // Backlog B4.2 (author-requested 2026-08-01) -- receipt photo/PDF or a
  // plain-text/file list of PURCHASED items, distinct from the pantry
  // snapshot above (see routers/inventory.py's module docstring for the
  // "what's here" vs "what did I just buy" rationale). Unlike the vision
  // preview's read-only list, importItems is genuinely EDITABLE row-by-
  // row before confirming, since a receipt import can produce many lines
  // and POS-abbreviation guesses that are more likely to need correction
  // than a single pantry photo's few items.
  const [showImportForm, setShowImportForm] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [importError, setImportError] = useState(null);
  const [importSourceType, setImportSourceType] = useState(null); // "photo" | "pdf" | "text" | "order_history"
  const [importItems, setImportItems] = useState(null); // editable rows, or null when no preview is active
  const [importText, setImportText] = useState("");

  // Backlog B10.3 (author-requested group, 2026-08-01) -- generic order-
  // history CSV/XLSX import (e.g. a Walmart order-history export from a
  // browser extension, since Walmart itself publishes neither a
  // consumer API nor a built-in export). Deliberately lands its results
  // in the SAME `importItems` editable preview table as the receipt/
  // list import above, per the backlog's own "same review screen, not
  // a separate UI" guidance -- only the file-upload-and-column-mapping
  // step below it differs, since a spreadsheet needs the user to say
  // which column is which before anything can be parsed.
  const [showOrderImportForm, setShowOrderImportForm] = useState(false);
  const [orderImportBusy, setOrderImportBusy] = useState(false);
  const [orderImportError, setOrderImportError] = useState(null);
  const [orderFile, setOrderFile] = useState(null); // kept so a mapping change can re-POST the same file
  const [orderHeaders, setOrderHeaders] = useState([]);
  const [orderMapping, setOrderMapping] = useState({
    name_column: "",
    quantity_column: "",
    unit_column: "",
    price_column: "",
    date_column: "",
  });
  const [orderRowInfo, setOrderRowInfo] = useState(null); // { row_count, skipped_row_count }
  const [orderProfiles, setOrderProfiles] = useState([]);
  const [orderProfileId, setOrderProfileId] = useState("");
  const [orderNewProfileName, setOrderNewProfileName] = useState("");

  useEffect(() => {
    if (showOrderImportForm) {
      api
        .get("/inventory/order-import/profiles")
        .then(setOrderProfiles)
        .catch(() => setOrderProfiles([]));
    }
  }, [showOrderImportForm]);

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

  async function runImport(formData) {
    setImportBusy(true);
    setImportError(null);
    setImportItems(null);
    try {
      const result = await api.post("/inventory/import", formData);
      setImportSourceType(result.source_type);
      setImportItems(
        result.detected_items.map((d) => ({
          name: d.name,
          category: d.category || "other",
          quantity: d.estimated_quantity ?? 1,
          unit: d.unit || "",
          expiration_date: d.expiration_date || "",
          purchased_date: d.purchased_date || "",
          unit_price: d.unit_price ?? "",
          confidence_note: d.confidence_note || "",
          included: true,
        }))
      );
    } catch (err) {
      setImportError(err.message);
    } finally {
      setImportBusy(false);
    }
  }

  async function handleImportText() {
    if (!importText.trim()) return;
    const formData = new FormData();
    formData.append("text", importText);
    await runImport(formData);
  }

  async function handleImportFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    const formData = new FormData();
    formData.append("file", file);
    await runImport(formData);
    e.target.value = "";
  }

  function updateImportRow(index, field, value) {
    setImportItems((rows) => rows.map((r, i) => (i === index ? { ...r, [field]: value } : r)));
  }

  function removeImportRow(index) {
    setImportItems((rows) => rows.filter((_, i) => i !== index));
  }

  async function confirmImportItems() {
    const sourceTag =
      importSourceType === "photo"
        ? "import_photo"
        : importSourceType === "pdf"
        ? "import_pdf"
        : importSourceType === "order_history"
        ? "import_order_history"
        : "import_text";
    const items = importItems
      .filter((r) => r.included)
      .map((r) => ({
        name: r.name,
        category: r.category || "other",
        quantity: Number(r.quantity) || 1,
        unit: r.unit || null,
        expiration_date: r.expiration_date || null,
        purchased_date: r.purchased_date || null,
        unit_price: r.unit_price === "" || r.unit_price == null ? null : Number(r.unit_price),
        source: sourceTag,
      }));
    if (items.length === 0) return;
    await api.post("/inventory/import/confirm", { items });
    discardImport();
    refresh();
  }

  function discardImport() {
    setImportItems(null);
    setImportSourceType(null);
    setImportError(null);
    setImportText("");
    setOrderFile(null);
    setOrderHeaders([]);
    setOrderMapping({ name_column: "", quantity_column: "", unit_column: "", price_column: "", date_column: "" });
    setOrderRowInfo(null);
    setOrderImportError(null);
    setOrderProfileId("");
  }

  async function runOrderImport(file, mappingOverride, profileId) {
    setOrderImportBusy(true);
    setOrderImportError(null);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const mapping = mappingOverride || {};
      if (mapping.name_column) formData.append("name_column", mapping.name_column);
      if (mapping.quantity_column) formData.append("quantity_column", mapping.quantity_column);
      if (mapping.unit_column) formData.append("unit_column", mapping.unit_column);
      if (mapping.price_column) formData.append("price_column", mapping.price_column);
      if (mapping.date_column) formData.append("date_column", mapping.date_column);
      if (profileId) formData.append("profile_id", profileId);
      const result = await api.post("/inventory/order-import", formData);
      setOrderHeaders(result.headers);
      setOrderMapping({
        name_column: result.mapping_used.name_column || "",
        quantity_column: result.mapping_used.quantity_column || "",
        unit_column: result.mapping_used.unit_column || "",
        price_column: result.mapping_used.price_column || "",
        date_column: result.mapping_used.date_column || "",
      });
      setOrderRowInfo({ row_count: result.row_count, skipped_row_count: result.skipped_row_count });
      setImportSourceType("order_history");
      setImportItems(
        result.detected_items.map((d) => ({
          name: d.name,
          category: d.category || "other",
          quantity: d.estimated_quantity ?? 1,
          unit: d.unit || "",
          expiration_date: d.expiration_date || "",
          purchased_date: d.purchased_date || "",
          unit_price: d.unit_price ?? "",
          confidence_note: d.confidence_note || "",
          included: true,
        }))
      );
    } catch (err) {
      setOrderImportError(err.message);
    } finally {
      setOrderImportBusy(false);
    }
  }

  async function handleOrderFileSelect(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setOrderFile(file);
    await runOrderImport(file, null, orderProfileId || null);
  }

  function handleOrderMappingChange(field, value) {
    setOrderMapping((m) => ({ ...m, [field]: value }));
  }

  async function handleReparseOrderImport() {
    if (!orderFile) return;
    await runOrderImport(orderFile, orderMapping, null);
  }

  async function handleSaveOrderProfile() {
    if (!orderNewProfileName.trim()) return;
    try {
      const profile = await api.post("/inventory/order-import/profiles", {
        name: orderNewProfileName.trim(),
        ...orderMapping,
      });
      setOrderProfiles((p) => [...p, profile]);
      setOrderNewProfileName("");
    } catch (err) {
      setOrderImportError(err.message);
    }
  }

  async function handleSelectOrderProfile(e) {
    const id = e.target.value;
    setOrderProfileId(id);
    if (orderFile && id) {
      await runOrderImport(orderFile, null, id);
    }
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
        <button className="btn btn-secondary" onClick={() => setShowImportForm((v) => !v)}>
          {showImportForm ? "Close" : "🧾 Import receipt/list"}
        </button>
        <button className="btn btn-secondary" onClick={() => setShowOrderImportForm((v) => !v)}>
          {showOrderImportForm ? "Close" : "📊 Import order history"}
        </button>
      </div>

      {showAddForm && (
        <div className="card">
          <h3>New item</h3>
          <InventoryItemForm onSubmit={handleCreate} onCancel={() => setShowAddForm(false)} />
        </div>
      )}

      {showImportForm && (
        <div className="card">
          <h3>Import a receipt or list</h3>
          <p className="hint">
            From a receipt photo, a receipt PDF, an uploaded text file, or pasted text -- for recording what
            was just PURCHASED (use "Add from photo" above instead for a snapshot of what's currently in the
            pantry/fridge). Review and correct the detected items below before adding them to inventory.
          </p>
          <div className="form-row">
            <textarea
              rows={3}
              placeholder="...or paste a list of purchased items here, e.g. one per line"
              value={importText}
              onChange={(e) => setImportText(e.target.value)}
              style={{ flex: 1 }}
              disabled={importBusy}
            />
          </div>
          <div className="form-actions">
            <button className="btn btn-secondary" onClick={handleImportText} disabled={importBusy || !importText.trim()}>
              {importBusy ? "Parsing..." : "Parse text"}
            </button>
            <label className="btn btn-secondary file-btn">
              {importBusy ? "Parsing..." : "Upload receipt photo, PDF, or text file"}
              <input
                type="file"
                accept="image/*,application/pdf,.txt,.csv,text/plain"
                onChange={handleImportFile}
                disabled={importBusy}
                hidden
              />
            </label>
          </div>
          {importError && <p className="error-text">Import failed: {importError}</p>}
        </div>
      )}

      {showOrderImportForm && (
        <div className="card">
          <h3>Import order history (CSV/XLSX)</h3>
          <p className="hint">
            From a retailer order-history export (e.g. a Walmart order-history file from a browser extension --
            Walmart itself publishes neither a public purchase-history API nor a built-in export button). No
            built-in retailer presets ship, since no retailer publishes a stable, verified export format -- map
            your file's columns below, then optionally save the mapping as a named profile to skip this step
            on future imports from the same source.
          </p>
          {orderProfiles.length > 0 && (
            <div className="form-row">
              <label>
                Saved profile
                <select value={orderProfileId} onChange={handleSelectOrderProfile}>
                  <option value="">-- none --</option>
                  {orderProfiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          )}
          <div className="form-row">
            <label className="btn btn-secondary file-btn">
              {orderImportBusy ? "Parsing..." : "Upload CSV or XLSX"}
              <input
                type="file"
                accept=".csv,.xlsx,text/csv,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                onChange={handleOrderFileSelect}
                disabled={orderImportBusy}
                hidden
              />
            </label>
          </div>
          {orderImportError && <p className="error-text">Import failed: {orderImportError}</p>}
          {orderHeaders.length > 0 && (
            <>
              <p className="hint">
                {orderRowInfo?.row_count} row(s) parsed, {orderRowInfo?.skipped_row_count} skipped (no usable name
                under the current mapping).
              </p>
              <div className="form-row">
                {[
                  ["name_column", "Name"],
                  ["quantity_column", "Quantity"],
                  ["unit_column", "Unit"],
                  ["price_column", "Price"],
                  ["date_column", "Purchase date"],
                ].map(([field, label]) => (
                  <label key={field}>
                    {label} column
                    <select value={orderMapping[field] || ""} onChange={(e) => handleOrderMappingChange(field, e.target.value)}>
                      <option value="">-- none --</option>
                      {orderHeaders.map((h) => (
                        <option key={h} value={h}>
                          {h}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
              <div className="form-actions">
                <button className="btn btn-secondary" onClick={handleReparseOrderImport} disabled={orderImportBusy}>
                  {orderImportBusy ? "Parsing..." : "Re-parse with this mapping"}
                </button>
                <input
                  placeholder="Save this mapping as..."
                  value={orderNewProfileName}
                  onChange={(e) => setOrderNewProfileName(e.target.value)}
                  style={{ maxWidth: "12em" }}
                />
                <button className="btn btn-secondary" onClick={handleSaveOrderProfile} disabled={!orderNewProfileName.trim()}>
                  Save profile
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {importItems && (
        <div className="card">
          <h3>Review imported items</h3>
          {importItems.length === 0 ? (
            <p>No items recognized. Try a clearer photo/scan, or add items manually.</p>
          ) : (
            <>
              <table className="data-table">
                <thead>
                  <tr>
                    <th></th>
                    <th>Name</th>
                    <th>Category</th>
                    <th>Qty</th>
                    <th>Unit</th>
                    <th>Price</th>
                    <th>Purchased</th>
                    <th>Expires</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {importItems.map((row, i) => (
                    <tr key={i}>
                      <td data-label="Include">
                        <input
                          type="checkbox"
                          checked={row.included}
                          onChange={(e) => updateImportRow(i, "included", e.target.checked)}
                        />
                      </td>
                      <td data-label="Name">
                        <input value={row.name} onChange={(e) => updateImportRow(i, "name", e.target.value)} />
                        {row.confidence_note && <div className="hint">{row.confidence_note}</div>}
                      </td>
                      <td data-label="Category">
                        <select value={row.category} onChange={(e) => updateImportRow(i, "category", e.target.value)}>
                          {IMPORT_CATEGORIES.map((c) => (
                            <option key={c} value={c}>
                              {c}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td data-label="Qty">
                        <input
                          type="number"
                          step="any"
                          value={row.quantity}
                          onChange={(e) => updateImportRow(i, "quantity", e.target.value)}
                          style={{ width: "5em" }}
                        />
                      </td>
                      <td data-label="Unit">
                        <input
                          value={row.unit}
                          onChange={(e) => updateImportRow(i, "unit", e.target.value)}
                          style={{ width: "6em" }}
                        />
                      </td>
                      <td data-label="Price">
                        <input
                          type="number"
                          step="any"
                          placeholder="—"
                          value={row.unit_price}
                          onChange={(e) => updateImportRow(i, "unit_price", e.target.value)}
                          style={{ width: "5em" }}
                        />
                      </td>
                      <td data-label="Purchased">
                        <input
                          type="date"
                          value={row.purchased_date}
                          onChange={(e) => updateImportRow(i, "purchased_date", e.target.value)}
                        />
                      </td>
                      <td data-label="Expires">
                        <input
                          type="date"
                          value={row.expiration_date}
                          onChange={(e) => updateImportRow(i, "expiration_date", e.target.value)}
                        />
                      </td>
                      <td className="row-actions" data-label="Actions">
                        <button className="btn-link btn-link-danger" onClick={() => removeImportRow(i)}>
                          Remove
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="form-actions">
                <button className="btn btn-primary" onClick={confirmImportItems}>
                  Add {importItems.filter((r) => r.included).length} item(s) to inventory
                </button>
                <button className="btn btn-secondary" onClick={discardImport}>
                  Discard
                </button>
              </div>
            </>
          )}
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
              <th>Price</th>
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
                  <td colSpan={8}>
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
                  <td data-label="Price">{item.unit_price != null ? `$${item.unit_price.toFixed(2)}` : "—"}</td>
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
