import { useEffect, useState } from "react";
import { api } from "../api";
import KnowledgeFilesPanel from "../components/KnowledgeFilesPanel";
import TrendChart from "../components/TrendChart";
import { useBackgroundJob } from "../hooks/useBackgroundJob";

const KG_PER_LB = 0.45359237;
const kgToLbs = (kg) => (kg == null ? "" : Math.round((kg / KG_PER_LB) * 10) / 10);
const lbsToKg = (lbs) => (lbs === "" || lbs == null ? null : Math.round(Number(lbs) * KG_PER_LB * 100) / 100);

function todayIso() {
  return new Date().toISOString().slice(0, 10);
}

const emptyMemberForm = { name: "", age: "", height_cm: "", sex: "", activity_level: "", notes: "" };
const emptyMetricForm = {
  entry_date: todayIso(),
  weight_lbs: "",
  ldl_mg_dl: "",
  hdl_mg_dl: "",
  total_cholesterol_mg_dl: "",
  triglycerides_mg_dl: "",
  blood_pressure_systolic: "",
  blood_pressure_diastolic: "",
  blood_glucose_mg_dl: "",
  notes: "",
};

export default function HealthPage() {
  const [preferences, setPreferences] = useState(null);
  const [prefsForm, setPrefsForm] = useState(null);
  const [prefsSaving, setPrefsSaving] = useState(false);
  // Backlog B3.1/B3.2 -- the fixed allergen taxonomy + observance levels
  // the deterministic check runs against (app/services/allergen_service.py),
  // fetched once from GET /household/allergen-options rather than
  // hardcoded here so backend and frontend can never drift apart.
  const [allergenOptions, setAllergenOptions] = useState({ allergens: [], observance_levels: [] });
  // Backlog B2.3 -- same server-driven pattern as allergenOptions above,
  // fetched from GET /household/dietary-pattern-options rather than
  // hardcoded so the dropdown can't drift out of sync with
  // dietary_pattern_service.DIETARY_PATTERNS.
  const [patternOptions, setPatternOptions] = useState({ patterns: [] });

  const [members, setMembers] = useState([]);
  const [selectedMemberId, setSelectedMemberId] = useState(null);
  const [showMemberForm, setShowMemberForm] = useState(false);
  const [memberForm, setMemberForm] = useState(emptyMemberForm);

  const [metrics, setMetrics] = useState([]);
  const [trends, setTrends] = useState(null);
  const [metricForm, setMetricForm] = useState(emptyMetricForm);
  const [metricBusy, setMetricBusy] = useState(false);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  // Backlog B8.1 -- bloodwork import (PDF/CSV/photo/pasted text) instead
  // of typing every field by hand. Same preview-then-confirm shape as
  // recipe import: the job returns extracted-but-unsaved rows, each
  // confirmed individually (or all at once) through the existing
  // POST /health/metrics endpoint below, so BMI computation/validation
  // stays on the one real code path.
  const [showBloodworkImport, setShowBloodworkImport] = useState(false);
  const [bloodworkFile, setBloodworkFile] = useState(null);
  const [bloodworkText, setBloodworkText] = useState("");
  const bloodworkJob = useBackgroundJob("chef.job.bloodwork_import");
  const [bloodworkEnqueueError, setBloodworkEnqueueError] = useState(null);
  const [bloodworkPreview, setBloodworkPreview] = useState([]); // [{ _key, entry_date, weight_lbs, ldl_mg_dl, ..., member_id }]
  const [bloodworkConfirming, setBloodworkConfirming] = useState(false);
  const bloodworkImporting = bloodworkJob.busy;

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const [prefs, memberList, allergenOpts, patternOpts] = await Promise.all([
        api.get("/household/preferences"),
        api.get("/household/members"),
        api.get("/household/allergen-options"),
        api.get("/household/dietary-pattern-options"),
      ]);
      setPreferences(prefs);
      setPrefsForm({
        household_size: prefs.household_size,
        dietary_restrictions: (prefs.dietary_restrictions || []).join(", "),
        goals: prefs.goals || "",
        indulgence_frequency: prefs.indulgence_frequency,
        notes: prefs.notes || "",
        restricted_allergens: prefs.restricted_allergens || [],
        gluten_observance_level: prefs.gluten_observance_level || "",
        dietary_pattern: prefs.dietary_pattern || "",
        pantry_staples: (prefs.pantry_staples || []).join(", "),
      });
      setAllergenOptions(allergenOpts);
      setPatternOptions(patternOpts);
      setMembers(memberList);
      if (memberList.length > 0 && selectedMemberId === null) {
        setSelectedMemberId(memberList[0].id);
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function refreshMemberData(memberId) {
    if (!memberId) {
      setMetrics([]);
      setTrends(null);
      return;
    }
    const [metricList, trendData] = await Promise.all([
      api.get(`/health/metrics?household_member_id=${memberId}`),
      api.get(`/health/trends?household_member_id=${memberId}`).catch(() => null),
    ]);
    setMetrics(metricList);
    setTrends(trendData);
  }

  useEffect(() => {
    refreshMemberData(selectedMemberId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedMemberId]);

  async function savePreferences(e) {
    e.preventDefault();
    setPrefsSaving(true);
    try {
      const updated = await api.patch("/household/preferences", {
        household_size: Number(prefsForm.household_size) || 2,
        dietary_restrictions: prefsForm.dietary_restrictions.split(",").map((s) => s.trim()).filter(Boolean),
        goals: prefsForm.goals || null,
        indulgence_frequency: prefsForm.indulgence_frequency,
        notes: prefsForm.notes || null,
        restricted_allergens: prefsForm.restricted_allergens,
        gluten_observance_level: prefsForm.restricted_allergens.includes("gluten")
          ? prefsForm.gluten_observance_level || "flexible"
          : null,
        dietary_pattern: prefsForm.dietary_pattern || null,
        pantry_staples: prefsForm.pantry_staples.split(",").map((s) => s.trim()).filter(Boolean),
      });
      setPreferences(updated);
      setPrefsForm((f) => ({
        ...f,
        restricted_allergens: updated.restricted_allergens || [],
        gluten_observance_level: updated.gluten_observance_level || "",
        dietary_pattern: updated.dietary_pattern || "",
        pantry_staples: (updated.pantry_staples || []).join(", "),
      }));
    } catch (e) {
      setError(e.message);
    } finally {
      setPrefsSaving(false);
    }
  }

  function toggleAllergen(key) {
    setPrefsForm((f) => {
      const has = f.restricted_allergens.includes(key);
      return {
        ...f,
        restricted_allergens: has ? f.restricted_allergens.filter((a) => a !== key) : [...f.restricted_allergens, key],
      };
    });
  }

  async function handleAddMember(e) {
    e.preventDefault();
    const created = await api.post("/household/members", {
      name: memberForm.name,
      age: memberForm.age === "" ? null : Number(memberForm.age),
      height_cm: memberForm.height_cm === "" ? null : Number(memberForm.height_cm),
      sex: memberForm.sex || null,
      activity_level: memberForm.activity_level || null,
      notes: memberForm.notes || null,
    });
    setMemberForm(emptyMemberForm);
    setShowMemberForm(false);
    const memberList = await api.get("/household/members");
    setMembers(memberList);
    setSelectedMemberId(created.id);
  }

  async function handleDeleteMember(memberId) {
    if (!window.confirm("Remove this household member and their logged metrics history?")) return;
    await api.del(`/household/members/${memberId}`);
    setSelectedMemberId(null);
    const memberList = await api.get("/household/members");
    setMembers(memberList);
    if (memberList.length > 0) setSelectedMemberId(memberList[0].id);
  }

  async function handleLogMetric(e) {
    e.preventDefault();
    if (!selectedMemberId) return;
    setMetricBusy(true);
    try {
      await api.post("/health/metrics", {
        household_member_id: selectedMemberId,
        entry_date: metricForm.entry_date,
        weight_kg: lbsToKg(metricForm.weight_lbs),
        ldl_mg_dl: metricForm.ldl_mg_dl === "" ? null : Number(metricForm.ldl_mg_dl),
        hdl_mg_dl: metricForm.hdl_mg_dl === "" ? null : Number(metricForm.hdl_mg_dl),
        total_cholesterol_mg_dl: metricForm.total_cholesterol_mg_dl === "" ? null : Number(metricForm.total_cholesterol_mg_dl),
        triglycerides_mg_dl: metricForm.triglycerides_mg_dl === "" ? null : Number(metricForm.triglycerides_mg_dl),
        blood_pressure_systolic: metricForm.blood_pressure_systolic === "" ? null : Number(metricForm.blood_pressure_systolic),
        blood_pressure_diastolic: metricForm.blood_pressure_diastolic === "" ? null : Number(metricForm.blood_pressure_diastolic),
        blood_glucose_mg_dl: metricForm.blood_glucose_mg_dl === "" ? null : Number(metricForm.blood_glucose_mg_dl),
        notes: metricForm.notes || null,
      });
      setMetricForm(emptyMetricForm);
      await refreshMemberData(selectedMemberId);
    } catch (e) {
      setError(e.message);
    } finally {
      setMetricBusy(false);
    }
  }

  async function handleDeleteMetric(entryId) {
    await api.del(`/health/metrics/${entryId}`);
    refreshMemberData(selectedMemberId);
  }

  // Backlog B8.1 -- bloodwork import handlers.
  useEffect(() => {
    if (!bloodworkJob.result) return;
    const entries = bloodworkJob.result.entries || [];
    setBloodworkPreview(
      entries.map((e, i) => ({
        _key: `${Date.now()}-${i}`,
        entry_date: e.entry_date || todayIso(),
        weight_lbs: e.weight_kg != null ? kgToLbs(e.weight_kg) : "",
        ldl_mg_dl: e.ldl_mg_dl ?? "",
        hdl_mg_dl: e.hdl_mg_dl ?? "",
        total_cholesterol_mg_dl: e.total_cholesterol_mg_dl ?? "",
        triglycerides_mg_dl: e.triglycerides_mg_dl ?? "",
        blood_pressure_systolic: e.blood_pressure_systolic ?? "",
        blood_pressure_diastolic: e.blood_pressure_diastolic ?? "",
        blood_glucose_mg_dl: e.blood_glucose_mg_dl ?? "",
        member_id: selectedMemberId ?? "",
      }))
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bloodworkJob.result]);

  async function handleBloodworkSubmit(e) {
    e.preventDefault();
    setBloodworkEnqueueError(null);
    bloodworkJob.clear();
    setBloodworkPreview([]);
    if (!bloodworkFile && !bloodworkText.trim()) {
      setBloodworkEnqueueError("Upload a file (PDF/CSV/photo) or paste the values as text.");
      return;
    }
    try {
      const form = new FormData();
      if (bloodworkFile) form.append("file", bloodworkFile);
      if (bloodworkText.trim()) form.append("text", bloodworkText.trim());
      const enqueued = await api.post("/health/import", form);
      bloodworkJob.poll(enqueued.job_id);
    } catch (err) {
      setBloodworkEnqueueError(err.message);
    }
  }

  function updateBloodworkRow(key, field, value) {
    setBloodworkPreview((rows) => rows.map((r) => (r._key === key ? { ...r, [field]: value } : r)));
  }

  function discardBloodworkRow(key) {
    setBloodworkPreview((rows) => rows.filter((r) => r._key !== key));
  }

  async function confirmBloodworkRow(row) {
    if (!row.member_id) {
      setError("Choose a household member for this entry before confirming.");
      return;
    }
    setBloodworkConfirming(true);
    try {
      await api.post("/health/metrics", {
        household_member_id: Number(row.member_id),
        entry_date: row.entry_date,
        weight_kg: lbsToKg(row.weight_lbs),
        ldl_mg_dl: row.ldl_mg_dl === "" ? null : Number(row.ldl_mg_dl),
        hdl_mg_dl: row.hdl_mg_dl === "" ? null : Number(row.hdl_mg_dl),
        total_cholesterol_mg_dl: row.total_cholesterol_mg_dl === "" ? null : Number(row.total_cholesterol_mg_dl),
        triglycerides_mg_dl: row.triglycerides_mg_dl === "" ? null : Number(row.triglycerides_mg_dl),
        blood_pressure_systolic: row.blood_pressure_systolic === "" ? null : Number(row.blood_pressure_systolic),
        blood_pressure_diastolic: row.blood_pressure_diastolic === "" ? null : Number(row.blood_pressure_diastolic),
        blood_glucose_mg_dl: row.blood_glucose_mg_dl === "" ? null : Number(row.blood_glucose_mg_dl),
        source: "import",
      });
      discardBloodworkRow(row._key);
      if (Number(row.member_id) === selectedMemberId) await refreshMemberData(selectedMemberId);
    } catch (err) {
      setError(err.message);
    } finally {
      setBloodworkConfirming(false);
    }
  }

  async function confirmAllBloodworkRows() {
    for (const row of bloodworkPreview) {
      // eslint-disable-next-line no-await-in-loop -- deliberately serial,
      // same reasoning as anywhere else in this app that posts several
      // rows in sequence: keeps error attribution to one row at a time
      // rather than racing several POSTs against the same member's log.
      await confirmBloodworkRow(row);
    }
  }

  const selectedMember = members.find((m) => m.id === selectedMemberId) || null;

  return (
    <div>
      {error && <p className="error-text">{error}</p>}

      <div className="card">
        <h3>Household preferences</h3>
        {prefsForm && (
          <form onSubmit={savePreferences}>
            <div className="form-row">
              <label>
                Household size
                <input
                  type="number"
                  min="1"
                  value={prefsForm.household_size}
                  onChange={(e) => setPrefsForm((f) => ({ ...f, household_size: e.target.value }))}
                />
              </label>
              <label>
                Indulgence frequency
                <select
                  value={prefsForm.indulgence_frequency}
                  onChange={(e) => setPrefsForm((f) => ({ ...f, indulgence_frequency: e.target.value }))}
                >
                  <option value="daily">daily</option>
                  <option value="weekly">weekly</option>
                  <option value="biweekly">biweekly</option>
                  <option value="rarely">rarely</option>
                </select>
              </label>
            </div>
            <label>
              Dietary restrictions (comma-separated)
              <input
                placeholder="gluten_free, celiac, low_sodium"
                value={prefsForm.dietary_restrictions}
                onChange={(e) => setPrefsForm((f) => ({ ...f, dietary_restrictions: e.target.value }))}
              />
            </label>
            <label>
              Goals
              <textarea
                rows={2}
                placeholder="e.g. reduce LDL cholesterol, quick weeknight prep"
                value={prefsForm.goals}
                onChange={(e) => setPrefsForm((f) => ({ ...f, goals: e.target.value }))}
              />
            </label>
            <label>
              Pantry staples -- always on hand, never on the grocery list (Backlog B5.5)
              <input
                placeholder="salt, pepper, olive oil"
                value={prefsForm.pantry_staples}
                onChange={(e) => setPrefsForm((f) => ({ ...f, pantry_staples: e.target.value }))}
              />
              <span className="hint">
                Matched ingredients are left off the grocery list entirely, regardless of quantity or what's
                currently tracked in inventory.
              </span>
            </label>
            <label>
              Dietary pattern (Backlog B2.3)
              <select
                value={prefsForm.dietary_pattern}
                onChange={(e) => setPrefsForm((f) => ({ ...f, dietary_pattern: e.target.value }))}
              >
                <option value="">None -- use Goals above as free text</option>
                {patternOptions.patterns.map((p) => (
                  <option key={p.key} value={p.key}>
                    {p.label}
                  </option>
                ))}
              </select>
              {prefsForm.dietary_pattern && (
                <span className="hint">
                  {patternOptions.patterns.find((p) => p.key === prefsForm.dietary_pattern)?.description}
                </span>
              )}
            </label>

            <fieldset>
              <legend>Allergens &amp; restrictions to check for (Backlog B3.1)</legend>
              <p className="hint">
                Checked items are matched against every recipe's ingredients automatically -- at import, in the
                weekly meal-plan preview, and again before a meal is confirmed as made.
              </p>
              <div className="form-row">
                {allergenOptions.allergens.map((a) => (
                  <label className="checkbox-label inline" key={a.key}>
                    <input
                      type="checkbox"
                      checked={prefsForm.restricted_allergens.includes(a.key)}
                      onChange={() => toggleAllergen(a.key)}
                    />
                    {a.label}
                  </label>
                ))}
              </div>
              {prefsForm.restricted_allergens.includes("gluten") && (
                <label>
                  Gluten observance level
                  <select
                    value={prefsForm.gluten_observance_level}
                    onChange={(e) => setPrefsForm((f) => ({ ...f, gluten_observance_level: e.target.value }))}
                  >
                    {allergenOptions.observance_levels.map((o) => (
                      <option key={o.key} value={o.key}>
                        {o.label}
                      </option>
                    ))}
                  </select>
                  <span className="hint">
                    "No cross-contact" also flags non-certified oats and similar ingredients that commonly share
                    equipment with wheat, even though oats aren't gluten-containing themselves.
                  </span>
                </label>
              )}
            </fieldset>

            <div className="form-actions">
              <button className="btn btn-primary" type="submit" disabled={prefsSaving}>
                {prefsSaving ? "Saving..." : "Save preferences"}
              </button>
            </div>
          </form>
        )}
      </div>

      <div className="card">
        <div className="page-toolbar">
          <h3 style={{ margin: 0 }}>Household members</h3>
          <button className="btn btn-secondary btn-sm" onClick={() => setShowMemberForm((v) => !v)}>
            {showMemberForm ? "Close" : "+ Add member"}
          </button>
        </div>

        {showMemberForm && (
          <form className="item-form" onSubmit={handleAddMember}>
            <div className="form-row">
              <label>
                Name
                <input required value={memberForm.name} onChange={(e) => setMemberForm((f) => ({ ...f, name: e.target.value }))} />
              </label>
              <label>
                Age
                <input type="number" min="0" value={memberForm.age} onChange={(e) => setMemberForm((f) => ({ ...f, age: e.target.value }))} />
              </label>
              <label>
                Height (cm)
                <input
                  type="number"
                  min="0"
                  value={memberForm.height_cm}
                  onChange={(e) => setMemberForm((f) => ({ ...f, height_cm: e.target.value }))}
                />
              </label>
            </div>
            <div className="form-row">
              <label>
                Sex
                <select value={memberForm.sex} onChange={(e) => setMemberForm((f) => ({ ...f, sex: e.target.value }))}>
                  <option value="">unspecified</option>
                  <option value="male">male</option>
                  <option value="female">female</option>
                  <option value="other">other</option>
                </select>
              </label>
              <label>
                Activity level
                <select
                  value={memberForm.activity_level}
                  onChange={(e) => setMemberForm((f) => ({ ...f, activity_level: e.target.value }))}
                >
                  <option value="">unspecified</option>
                  <option value="sedentary">sedentary</option>
                  <option value="light">light</option>
                  <option value="moderate">moderate</option>
                  <option value="active">active</option>
                </select>
              </label>
            </div>
            <div className="form-actions">
              <button className="btn btn-primary" type="submit">
                Add member
              </button>
            </div>
          </form>
        )}

        {loading ? (
          <p>Loading...</p>
        ) : members.length === 0 ? (
          <p>No household members yet. Add one to start tracking body metrics.</p>
        ) : (
          <ul className="member-list">
            {members.map((m) => (
              <li key={m.id} className={m.id === selectedMemberId ? "member-item selected" : "member-item"}>
                <button className="btn-link" onClick={() => setSelectedMemberId(m.id)}>
                  {m.name}
                </button>
                <span className="hint">
                  {m.age ? `${m.age}y` : ""} {m.height_cm ? `${m.height_cm}cm` : ""} {m.activity_level || ""}
                </span>
                <button className="btn-link btn-link-danger" onClick={() => handleDeleteMember(m.id)}>
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>

      {selectedMember && (
        <div className="card">
          <h3>{selectedMember.name}'s health metrics</h3>
          {!selectedMember.height_cm && (
            <p className="hint">Add a height for this member above to enable automatic BMI calculation.</p>
          )}

          {trends && (
            <div className="trend-chart-grid">
              <TrendChart
                label="Weight"
                unit=" lbs"
                points={metrics.map((m) => ({ date: m.entry_date, value: m.weight_kg != null ? kgToLbs(m.weight_kg) : null }))}
              />
              <TrendChart
                label="LDL cholesterol"
                unit=" mg/dL"
                points={metrics.map((m) => ({ date: m.entry_date, value: m.ldl_mg_dl }))}
              />
            </div>
          )}

          <form className="item-form" onSubmit={handleLogMetric}>
            <div className="form-row">
              <label>
                Date
                <input
                  type="date"
                  value={metricForm.entry_date}
                  onChange={(e) => setMetricForm((f) => ({ ...f, entry_date: e.target.value }))}
                  required
                />
              </label>
              <label>
                Weight (lbs)
                <input
                  type="number"
                  step="any"
                  value={metricForm.weight_lbs}
                  onChange={(e) => setMetricForm((f) => ({ ...f, weight_lbs: e.target.value }))}
                />
              </label>
            </div>
            <div className="form-row">
              <label>
                LDL (mg/dL)
                <input type="number" value={metricForm.ldl_mg_dl} onChange={(e) => setMetricForm((f) => ({ ...f, ldl_mg_dl: e.target.value }))} />
              </label>
              <label>
                HDL (mg/dL)
                <input type="number" value={metricForm.hdl_mg_dl} onChange={(e) => setMetricForm((f) => ({ ...f, hdl_mg_dl: e.target.value }))} />
              </label>
              <label>
                Total cholesterol (mg/dL)
                <input
                  type="number"
                  value={metricForm.total_cholesterol_mg_dl}
                  onChange={(e) => setMetricForm((f) => ({ ...f, total_cholesterol_mg_dl: e.target.value }))}
                />
              </label>
              <label>
                Triglycerides (mg/dL)
                <input
                  type="number"
                  value={metricForm.triglycerides_mg_dl}
                  onChange={(e) => setMetricForm((f) => ({ ...f, triglycerides_mg_dl: e.target.value }))}
                />
              </label>
            </div>
            <div className="form-row">
              <label>
                Blood pressure systolic
                <input
                  type="number"
                  value={metricForm.blood_pressure_systolic}
                  onChange={(e) => setMetricForm((f) => ({ ...f, blood_pressure_systolic: e.target.value }))}
                />
              </label>
              <label>
                Blood pressure diastolic
                <input
                  type="number"
                  value={metricForm.blood_pressure_diastolic}
                  onChange={(e) => setMetricForm((f) => ({ ...f, blood_pressure_diastolic: e.target.value }))}
                />
              </label>
              <label>
                Glucose (mg/dL)
                <input
                  type="number"
                  value={metricForm.blood_glucose_mg_dl}
                  onChange={(e) => setMetricForm((f) => ({ ...f, blood_glucose_mg_dl: e.target.value }))}
                />
              </label>
            </div>
            <div className="form-actions">
              <button className="btn btn-primary" type="submit" disabled={metricBusy}>
                {metricBusy ? "Saving..." : "Log entry"}
              </button>
            </div>
          </form>

          <button type="button" className="btn-link" onClick={() => setShowBloodworkImport((v) => !v)}>
            {showBloodworkImport ? "Hide" : "Show"} bloodwork import (backlog B8.1)
          </button>
          {showBloodworkImport && (
            <div className="bloodwork-import">
              <p className="hint">
                Upload a lab report (PDF or a photo of a printed report), a CSV/text export, or paste values
                directly -- extracted numbers are a preview to review before anything is saved. Unit
                conversion (lbs, mmol/L) is done by the AI model reading the source, not a guaranteed-exact
                calculation -- double-check anything that looks off before confirming.
              </p>
              <form onSubmit={handleBloodworkSubmit}>
                <div className="form-row">
                  <label>
                    File (PDF, CSV, or photo)
                    <input
                      type="file"
                      accept=".pdf,.csv,.txt,image/*"
                      onChange={(e) => setBloodworkFile(e.target.files?.[0] || null)}
                    />
                  </label>
                </div>
                <label>
                  Or paste values as text
                  <textarea
                    rows={2}
                    placeholder="e.g. LDL 130, HDL 45, Total 210, Triglycerides 150, dated 7/15/2026"
                    value={bloodworkText}
                    onChange={(e) => setBloodworkText(e.target.value)}
                  />
                </label>
                <div className="form-actions">
                  <button className="btn btn-secondary" type="submit" disabled={bloodworkImporting}>
                    {bloodworkImporting && <span className="busy-spinner" aria-hidden="true" />}
                    {bloodworkJob.status === "queued" ? "Queued..." : bloodworkImporting ? "Reading..." : "Extract"}
                  </button>
                </div>
                {(bloodworkEnqueueError || bloodworkJob.error) && (
                  <p className="error-text">{bloodworkEnqueueError || bloodworkJob.error}</p>
                )}
              </form>

              {bloodworkPreview.length > 0 && (
                <>
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>Member</th>
                        <th>Date</th>
                        <th>Weight (lbs)</th>
                        <th>LDL</th>
                        <th>HDL</th>
                        <th>Total chol.</th>
                        <th>Trig.</th>
                        <th>BP</th>
                        <th>Glucose</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {bloodworkPreview.map((row) => (
                        <tr key={row._key}>
                          <td data-label="Member">
                            <select
                              value={row.member_id}
                              onChange={(e) => updateBloodworkRow(row._key, "member_id", e.target.value)}
                            >
                              <option value="">-- choose --</option>
                              {members.map((m) => (
                                <option key={m.id} value={m.id}>
                                  {m.name}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td data-label="Date">
                            <input
                              type="date"
                              value={row.entry_date}
                              onChange={(e) => updateBloodworkRow(row._key, "entry_date", e.target.value)}
                            />
                          </td>
                          <td data-label="Weight (lbs)">
                            <input
                              type="number"
                              step="any"
                              value={row.weight_lbs}
                              onChange={(e) => updateBloodworkRow(row._key, "weight_lbs", e.target.value)}
                            />
                          </td>
                          <td data-label="LDL">
                            <input
                              type="number"
                              value={row.ldl_mg_dl}
                              onChange={(e) => updateBloodworkRow(row._key, "ldl_mg_dl", e.target.value)}
                            />
                          </td>
                          <td data-label="HDL">
                            <input
                              type="number"
                              value={row.hdl_mg_dl}
                              onChange={(e) => updateBloodworkRow(row._key, "hdl_mg_dl", e.target.value)}
                            />
                          </td>
                          <td data-label="Total cholesterol">
                            <input
                              type="number"
                              value={row.total_cholesterol_mg_dl}
                              onChange={(e) => updateBloodworkRow(row._key, "total_cholesterol_mg_dl", e.target.value)}
                            />
                          </td>
                          <td data-label="Triglycerides">
                            <input
                              type="number"
                              value={row.triglycerides_mg_dl}
                              onChange={(e) => updateBloodworkRow(row._key, "triglycerides_mg_dl", e.target.value)}
                            />
                          </td>
                          <td data-label="Blood pressure">
                            <input
                              type="number"
                              placeholder="sys"
                              value={row.blood_pressure_systolic}
                              onChange={(e) => updateBloodworkRow(row._key, "blood_pressure_systolic", e.target.value)}
                              style={{ width: "3.5em" }}
                            />
                            /
                            <input
                              type="number"
                              placeholder="dia"
                              value={row.blood_pressure_diastolic}
                              onChange={(e) => updateBloodworkRow(row._key, "blood_pressure_diastolic", e.target.value)}
                              style={{ width: "3.5em" }}
                            />
                          </td>
                          <td data-label="Glucose">
                            <input
                              type="number"
                              value={row.blood_glucose_mg_dl}
                              onChange={(e) => updateBloodworkRow(row._key, "blood_glucose_mg_dl", e.target.value)}
                            />
                          </td>
                          <td>
                            <button
                              type="button"
                              className="btn-link"
                              disabled={bloodworkConfirming}
                              onClick={() => confirmBloodworkRow(row)}
                            >
                              Add
                            </button>
                            <button
                              type="button"
                              className="btn-link btn-link-danger"
                              disabled={bloodworkConfirming}
                              onClick={() => discardBloodworkRow(row._key)}
                            >
                              Discard
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <div className="form-actions">
                    <button
                      type="button"
                      className="btn btn-primary btn-sm"
                      disabled={bloodworkConfirming}
                      onClick={confirmAllBloodworkRows}
                    >
                      {bloodworkConfirming ? "Adding..." : "Add all to log"}
                    </button>
                  </div>
                </>
              )}
            </div>
          )}

          {metrics.length > 0 && (
            <table className="data-table">
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Weight</th>
                  <th>BMI</th>
                  <th>LDL / HDL</th>
                  <th>BP</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {metrics.map((m) => (
                  <tr key={m.id}>
                    <td>{m.entry_date}</td>
                    <td>{m.weight_kg != null ? `${kgToLbs(m.weight_kg)} lbs` : "—"}</td>
                    <td>{m.bmi ?? "—"}</td>
                    <td>
                      {m.ldl_mg_dl ?? "—"} / {m.hdl_mg_dl ?? "—"}
                    </td>
                    <td>
                      {m.blood_pressure_systolic && m.blood_pressure_diastolic
                        ? `${m.blood_pressure_systolic}/${m.blood_pressure_diastolic}`
                        : "—"}
                    </td>
                    <td>
                      <button className="btn-link btn-link-danger" onClick={() => handleDeleteMetric(m.id)}>
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      <KnowledgeFilesPanel />
    </div>
  );
}
