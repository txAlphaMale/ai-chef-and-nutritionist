import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import BarcodeScanner from "../components/BarcodeScanner";
import InventoryItemForm from "../components/InventoryItemForm";
import { useBackgroundJob } from "../hooks/useBackgroundJob";
import { formatDate, formatDateTime } from "../utils/datetime";

// Same category enum the backend's InventoryItemBase and
// RECEIPT_IMPORT_PROMPT use, duplicated here like
// InventoryItemForm.jsx's local CATEGORIES rather than exported, since
// it is small and rarely changes.
const IMPORT_CATEGORIES = ["pantry", "fridge", "freezer", "produce", "spice", "other"];

function urgencyClass(score) {
  if (score >= 80) return "urgency-high";
  if (score >= 30) return "urgency-medium";
  if (score > 0) return "urgency-low";
  return "";
}

// Trims trailing float noise (e.g. 15.999999999999998 from repeated
// deduct_by_name subtractions) for display only -- the stored value is
// untouched.
function formatQuantity(value) {
  if (value == null) return "";
  const rounded = Math.round(value * 100) / 100;
  return Number.isInteger(rounded) ? rounded : rounded.toFixed(2);
}

// "On hand" vs. "at time of purchase" (the author's own stated
// distinction this redesign exists to capture) -- shown as a percentage
// so a glance at the table answers "how much of this do I actually have
// left" without doing the division themselves.
function onHandPercent(item) {
  if (!item.purchased_quantity) return null;
  return Math.round((item.quantity / item.purchased_quantity) * 100);
}

export default function InventoryPage() {
  const [items, setItems] = useState([]);
  const [urgencyByItemId, setUrgencyByItemId] = useState({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [showAddForm, setShowAddForm] = useState(false);
  const [editingId, setEditingId] = useState(null);
  const [categoryFilter, setCategoryFilter] = useState("");

  // The app-wide RecallBanner only shows itself when there is an active
  // match, so there is nowhere to trigger a manual check when everything
  // is clean, or to see when the last one ran. This page-scoped line
  // fills that gap.
  const [recallStatus, setRecallStatus] = useState(null);
  const [recallChecking, setRecallChecking] = useState(false);

  async function refreshRecallStatus() {
    try {
      setRecallStatus(await api.get("/inventory/recalls"));
    } catch {
      setRecallStatus(null);
    }
  }

  async function checkRecallsNow() {
    setRecallChecking(true);
    try {
      await api.post("/inventory/recalls/check", {});
      setTimeout(refreshRecallStatus, 4000);
    } catch {
      // JobsBadge still reflects reality even if this optimistic refresh fails
    } finally {
      setRecallChecking(false);
    }
  }

  useEffect(() => {
    refreshRecallStatus();
  }, []);

  // Camera barcode intake. Unlike vision-intake and receipt-import this
  // never touches Ollama or job_queue: GET /api/inventory/barcode-lookup
  // is one fast Open Food Facts round trip, so there is nothing to poll
  // -- just a normal async call.
  const [showScanner, setShowScanner] = useState(false);
  const [barcodeResult, setBarcodeResult] = useState(null);
  const [barcodeLookupBusy, setBarcodeLookupBusy] = useState(false);
  const [barcodeLookupError, setBarcodeLookupError] = useState(null);

  const handleBarcodeDetected = useCallback(async (barcode) => {
    setShowScanner(false);
    setBarcodeLookupBusy(true);
    setBarcodeLookupError(null);
    setBarcodeResult(null);
    try {
      setBarcodeResult(await api.get(`/inventory/barcode-lookup?barcode=${encodeURIComponent(barcode)}`));
    } catch (err) {
      setBarcodeLookupError(err.message);
    } finally {
      setBarcodeLookupBusy(false);
    }
  }, []);

  async function confirmBarcodeItem(payload) {
    await handleCreate(payload);
    setBarcodeResult(null);
  }

  // Vision intake enqueues a background job and polls rather than
  // blocking: the model can take minutes on this hardware.
  // useBackgroundJob persists the job_id to localStorage, so returning
  // to this page (even after a reload) resumes where it left off. See
  // job_queue.py for the full rationale.
  const visionJob = useBackgroundJob("chef.job.vision_intake");

  // Receipt photo/PDF or a plain-text/file list of PURCHASED items,
  // distinct from the pantry snapshot above (see routers/inventory.py).
  // Unlike the vision preview's read-only list, importItems is EDITABLE
  // row by row before confirming: a receipt produces many lines and
  // POS-abbreviation guesses that need correction more often than a
  // single pantry photo's few items.
  const [showImportForm, setShowImportForm] = useState(false);
  const importJob = useBackgroundJob("chef.job.inventory_import"); // B11.1, same rationale as visionJob above
  const [importSourceType, setImportSourceType] = useState(null); // "photo" | "pdf" | "text" | "order_history"
  // "Parse text" and the file-upload button share one importJob, so
  // without this both would show "Parsing..." at once regardless of
  // which was clicked. Tracks which one started the in-flight job so
  // only it shows the busy label; the other stays disabled but keeps
  // its normal text.
  const [importTrigger, setImportTrigger] = useState(null); // "text" | "file" | null
  const [importItems, setImportItems] = useState(null); // editable rows, or null when no preview is active
  const [importText, setImportText] = useState("");
  // Confirming an import needs visible error handling: a failed POST
  // that silently does nothing reads as "I imported a receipt and
  // nothing showed up". Also reports how many items were identified and
  // added, and surfaces the raw model output so an extraction problem
  // (the model returning prose instead of JSON) is visible without
  // needing the browser console or server logs.
  const [importConfirmError, setImportConfirmError] = useState(null);
  const [importConfirmBusy, setImportConfirmBusy] = useState(false);
  const [importResultMessage, setImportResultMessage] = useState(null);
  const [showImportRawOutput, setShowImportRawOutput] = useState(false);

  // The job body returns the exact same {detected_items, raw_model_output,
  // source_type} shape the old synchronous endpoint used to return
  // directly -- this effect is the one place that turns THAT into the
  // page's own editable-row shape, whether it just finished from a fresh
  // upload or was picked back up on mount from a job left running
  // before the page was last closed.
  useEffect(() => {
    if (!importJob.result) return;
    setImportConfirmError(null);
    setImportResultMessage(null);
    setShowImportRawOutput(false);
    setImportSourceType(importJob.result.source_type);
    setImportItems(
      importJob.result.detected_items.map((d) => ({
        name: d.name,
        category: d.category || "other",
        quantity: d.estimated_quantity ?? 1,
        unit: d.unit || "",
        // Carried through so the created InventoryItem gets real
        // package_quantity/package_count/package_descriptor, not just
        // the flattened on-hand total. Not shown as its own review
        // column (the table is already 8 wide); still editable
        // afterward via the item's Edit form.
        package_quantity: d.package_quantity ?? null,
        package_count: d.package_count ?? null,
        package_descriptor: d.package_descriptor ?? null,
        expiration_date: d.expiration_date || "",
        purchased_date: d.purchased_date || "",
        unit_price: d.unit_price ?? "",
        confidence_note: d.confidence_note || "",
        included: true,
      }))
    );
     
  }, [importJob.result]);

  // Generic order-history CSV/XLSX import (e.g. a Walmart order-history
  // export from a browser extension -- Walmart publishes neither a
  // consumer API nor a built-in export). Lands in the SAME `importItems`
  // editable preview table as the receipt/list import above; only the
  // upload-and-column-mapping step differs, since a spreadsheet needs
  // the user to say which column is which first.
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
    try {
      const formData = new FormData();
      formData.append("file", file);
      const { job_id } = await api.post("/inventory/vision-intake", formData);
      visionJob.poll(job_id);
    } catch (err) {
      // Enqueueing itself failed (e.g. a network error before the job
      // ever started) -- rare, but worth surfacing rather than silently
      // swallowing since visionJob has no error state for this case.
      window.alert(`Could not start photo analysis: ${err.message}`);
    } finally {
      e.target.value = "";
    }
  }

  async function confirmVisionItems() {
    const detected = visionJob.result?.detected_items;
    if (!detected?.length) return;
    const items = detected.map((d) => ({
      name: d.name,
      category: d.category || "other",
      quantity: d.estimated_quantity ?? 1,
      unit: d.unit || null,
      package_quantity: d.package_quantity ?? null,
      package_count: d.package_count ?? null,
      package_descriptor: d.package_descriptor ?? null,
      expiration_date: d.expiration_date || null,
      source: "vision",
    }));
    try {
      const created = await api.post("/inventory/vision-intake/confirm", { items });
      visionJob.clear();
      refresh();
      window.alert(`Added ${created.length} item(s) to inventory.`);
    } catch (err) {
      window.alert(`Could not add these items: ${err.message}`);
    }
  }

  async function runImport(formData) {
    try {
      const { job_id } = await api.post("/inventory/import", formData);
      importJob.poll(job_id);
    } catch (err) {
      window.alert(`Could not start import: ${err.message}`);
    }
  }

  async function handleImportText() {
    if (!importText.trim()) return;
    setImportTrigger("text");
    const formData = new FormData();
    formData.append("text", importText);
    await runImport(formData);
  }

  async function handleImportFile(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setImportTrigger("file");
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
    setImportConfirmError(null);
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
        package_quantity: r.package_quantity ?? null,
        package_count: r.package_count ?? null,
        package_descriptor: r.package_descriptor ?? null,
        expiration_date: r.expiration_date || null,
        purchased_date: r.purchased_date || null,
        unit_price: r.unit_price === "" || r.unit_price == null ? null : Number(r.unit_price),
        source: sourceTag,
      }));
    if (items.length === 0) return;
    setImportConfirmBusy(true);
    try {
      const created = await api.post("/inventory/import/confirm", { items });
      discardImport();
      refresh();
      // Set AFTER discardImport clears the review table, so the count
      // of what actually landed survives that call.
      setImportResultMessage(
        `Added ${created.length} item(s) to inventory` +
          (created.length < items.length ? ` (${items.length - created.length} did not save -- see below).` : ".")
      );
    } catch (err) {
      // A failed confirm must surface: silently doing nothing reads as
      // "I imported a receipt and nothing showed up in inventory."
      setImportConfirmError(err.message);
    } finally {
      setImportConfirmBusy(false);
    }
  }

  function discardImport() {
    setImportItems(null);
    setImportSourceType(null);
    setImportTrigger(null);
    importJob.clear();
    setImportText("");
    setImportConfirmError(null);
    setShowImportRawOutput(false);
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
          package_quantity: d.package_quantity ?? null,
          package_count: d.package_count ?? null,
          package_descriptor: d.package_descriptor ?? null,
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
        <select
          value={categoryFilter}
          aria-label="Filter inventory by category"
          onChange={(e) => setCategoryFilter(e.target.value)}
        >
          <option value="">All categories</option>
          <option value="pantry">Pantry</option>
          <option value="fridge">Fridge</option>
          <option value="freezer">Freezer</option>
          <option value="produce">Produce</option>
          <option value="spice">Spice</option>
          <option value="other">Other</option>
        </select>
        {/* Each toggle keeps its own distinct label at all times and
            shows a pressed style (`.btn-toggle-active`) plus
            `aria-pressed` while its panel is open. Swapping the label to
            a bare "Close" would leave several identical buttons in a row
            with several panels open, with no way to tell which belongs
            to which. */}
        <button
          className={showAddForm ? "btn btn-toggle-active" : "btn btn-primary"}
          aria-pressed={showAddForm}
          onClick={() => setShowAddForm((v) => !v)}
        >
          + Add item
        </button>
        <label className="btn btn-secondary file-btn">
          {visionJob.busy ? (
            <>
              <span className="busy-spinner" aria-hidden="true" />
              {visionJob.status === "queued" ? "Queued..." : "Analyzing..."}
            </>
          ) : (
            "📷 Add from photo"
          )}
          <input type="file" accept="image/*" onChange={handleVisionUpload} disabled={visionJob.busy} hidden />
        </label>
        <button
          className={showImportForm ? "btn btn-toggle-active" : "btn btn-secondary"}
          aria-pressed={showImportForm}
          onClick={() => setShowImportForm((v) => !v)}
        >
          🧾 Import receipt/list
        </button>
        <button
          className={showOrderImportForm ? "btn btn-toggle-active" : "btn btn-secondary"}
          aria-pressed={showOrderImportForm}
          onClick={() => setShowOrderImportForm((v) => !v)}
        >
          📊 Import order history
        </button>
        <button
          className={showScanner ? "btn btn-toggle-active" : "btn btn-secondary"}
          aria-pressed={showScanner}
          onClick={() => {
            setBarcodeResult(null);
            setBarcodeLookupError(null);
            setShowScanner((v) => !v);
          }}
        >
          🔎 Scan barcode
        </button>
      </div>

      {recallStatus && recallStatus.alerts.length === 0 && (
        <p className="hint recall-check-line">
          No active recall matches
          {recallStatus.last_checked_at
            ? ` -- last checked ${formatDateTime(recallStatus.last_checked_at)}.`
            : " -- not checked yet."}{" "}
          <button type="button" className="btn-link" onClick={checkRecallsNow} disabled={recallChecking}>
            {recallChecking ? "Checking..." : "Check for recalls now"}
          </button>
        </p>
      )}

      {showAddForm && (
        <div className="card">
          <h3>New item</h3>
          <InventoryItemForm onSubmit={handleCreate} onCancel={() => setShowAddForm(false)} />
        </div>
      )}

      {showScanner && (
        <div className="card">
          <h3>Scan a barcode</h3>
          <BarcodeScanner onDetected={handleBarcodeDetected} onClose={() => setShowScanner(false)} />
        </div>
      )}

      {barcodeLookupBusy && (
        <p className="hint">
          <span className="busy-spinner" aria-hidden="true" /> Looking up barcode...
        </p>
      )}
      {barcodeLookupError && <p className="error-text">Barcode lookup failed: {barcodeLookupError}</p>}

      {barcodeResult && (
        <div className="card">
          <h3>{barcodeResult.found ? "Scanned item" : "Barcode not found"}</h3>
          {barcodeResult.found ? (
            <p className="hint">
              {barcodeResult.brand && `${barcodeResult.brand} -- `}
              from Open Food Facts
              {barcodeResult.quantity_text && ` (package size: ${barcodeResult.quantity_text})`}. Review and adjust
              before adding.
            </p>
          ) : (
            <p className="hint">
              Barcode {barcodeResult.barcode} isn't in Open Food Facts' database -- it's crowd-sourced, so
              local/store-brand items are often missing. Fill in the details manually below.
            </p>
          )}
          {barcodeResult.image_url && (
            <img src={barcodeResult.image_url} alt="" className="barcode-result-image" />
          )}
          <InventoryItemForm
            initial={{
              name: barcodeResult.name || "",
              category: barcodeResult.category || "pantry",
              quantity: barcodeResult.estimated_quantity ?? 1,
              unit: barcodeResult.unit || "count",
              package_quantity: barcodeResult.package_quantity ?? "",
              package_count: barcodeResult.package_count ?? 1,
              package_descriptor: barcodeResult.package_descriptor ?? "",
              source: "barcode",
            }}
            onSubmit={confirmBarcodeItem}
            onCancel={() => setBarcodeResult(null)}
          />
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
              className="u-flex-1"
              disabled={importJob.busy}
            />
          </div>
          <div className="form-actions">
            <button
              className="btn btn-secondary"
              onClick={handleImportText}
              disabled={importJob.busy || !importText.trim()}
            >
              {importJob.busy && importTrigger === "text" && <span className="busy-spinner" aria-hidden="true" />}
              {importJob.busy && importTrigger === "text"
                ? importJob.status === "queued"
                  ? "Queued..."
                  : "Parsing..."
                : "Parse text"}
            </button>
            <label className="btn btn-secondary file-btn">
              {importJob.busy && importTrigger === "file" ? (
                <>
                  <span className="busy-spinner" aria-hidden="true" />
                  {importJob.status === "queued" ? "Queued..." : "Parsing..."}
                </>
              ) : (
                "Upload receipt photo, PDF, or text file"
              )}
              <input
                type="file"
                accept="image/*,application/pdf,.txt,.csv,text/plain"
                onChange={handleImportFile}
                disabled={importJob.busy}
                hidden
              />
            </label>
          </div>
          {importJob.error && <p className="error-text">Import failed: {importJob.error}</p>}
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
                  className="input-name-cap"
                />
                <button className="btn btn-secondary" onClick={handleSaveOrderProfile} disabled={!orderNewProfileName.trim()}>
                  Save profile
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {importResultMessage && <p className="hint import-result-message">{importResultMessage}</p>}

      {importItems && (
        <div className="card">
          <h3>Review imported items</h3>
          <p className="hint">
            {importItems.length} item{importItems.length === 1 ? "" : "s"} identified
            {importSourceType ? ` from your ${importSourceType === "order_history" ? "order history" : importSourceType}` : ""}
            . Uncheck or edit anything before adding to inventory.
          </p>
          {importJob.result?.raw_model_output && (
            <>
              <button type="button" className="btn-link" onClick={() => setShowImportRawOutput((v) => !v)}>
                {showImportRawOutput ? "Hide" : "Show"} raw AI response (for troubleshooting)
              </button>
              {showImportRawOutput && <pre className="import-raw-output">{importJob.result.raw_model_output}</pre>}
            </>
          )}
          {importItems.length === 0 ? (
            <p>
              No items recognized. This could mean the receipt genuinely had nothing food-related on it, or the
              AI model's response couldn't be parsed -- check the raw AI response above, or try a clearer photo/
              scan, or add items manually.
            </p>
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
                          className="input-amount"
                        />
                      </td>
                      <td data-label="Unit">
                        <input
                          value={row.unit}
                          onChange={(e) => updateImportRow(i, "unit", e.target.value)}
                          className="input-unit"
                        />
                      </td>
                      <td data-label="Price">
                        <input
                          type="number"
                          step="any"
                          placeholder="—"
                          value={row.unit_price}
                          onChange={(e) => updateImportRow(i, "unit_price", e.target.value)}
                          className="input-amount"
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
                <button className="btn btn-primary" onClick={confirmImportItems} disabled={importConfirmBusy}>
                  {importConfirmBusy && <span className="busy-spinner" aria-hidden="true" />}
                  {importConfirmBusy
                    ? "Adding..."
                    : `Add ${importItems.filter((r) => r.included).length} item(s) to inventory`}
                </button>
                <button className="btn btn-secondary" onClick={discardImport} disabled={importConfirmBusy}>
                  Discard
                </button>
              </div>
              {importConfirmError && <p className="error-text">Could not add these items: {importConfirmError}</p>}
            </>
          )}
        </div>
      )}

      {visionJob.busy && (
        <p className="hint">
          <span className="busy-spinner" aria-hidden="true" />
          {visionJob.status === "queued"
            ? "Photo queued for analysis -- it'll start as soon as the chef finishes whatever else is running."
            : "Analyzing photo..."}
        </p>
      )}
      {visionJob.error && <p className="error-text">Photo analysis failed: {visionJob.error}</p>}

      {visionJob.result && (
        <div className="card">
          <h3>Detected from photo</h3>
          {visionJob.result.detected_items.length === 0 ? (
            <p>No items recognized. Try a clearer photo, or add items manually.</p>
          ) : (
            <>
              <ul className="vision-preview-list">
                {visionJob.result.detected_items.map((d, i) => (
                  <li key={i}>
                    <strong>{d.name}</strong>
                    {d.estimated_quantity != null && ` — ${d.estimated_quantity}${d.unit ? " " + d.unit : ""}`}
                    {" "}
                    <span className="tag">{d.category}</span>
                    {d.expiration_date && <span className="tag">exp {formatDate(d.expiration_date)}</span>}
                    {d.confidence_note && <em> ({d.confidence_note})</em>}
                  </li>
                ))}
              </ul>
              <div className="form-actions">
                <button className="btn btn-primary" onClick={confirmVisionItems}>
                  Add all to inventory
                </button>
                <button className="btn btn-secondary" onClick={() => visionJob.clear()}>
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
                    {formatQuantity(item.quantity)} {item.unit || ""}
                    {item.package_count && item.package_quantity && (
                      <div className="hint">
                        ({item.package_count} {item.package_descriptor || "pkg"} of {formatQuantity(item.package_quantity)}{" "}
                        {item.unit || ""} each)
                      </div>
                    )}
                    {item.purchased_quantity ? (
                      <div className="hint">{onHandPercent(item)}% on hand</div>
                    ) : null}
                  </td>
                  <td data-label="Price">{item.unit_price != null ? `$${item.unit_price.toFixed(2)}` : "—"}</td>
                  <td data-label="Expires">{formatDate(item.expiration_date)}</td>
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
