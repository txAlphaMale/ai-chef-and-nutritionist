import { useEffect, useState } from "react";
import { api } from "../api";
import KnowledgeFilesPanel from "../components/KnowledgeFilesPanel";
import TrendChart from "../components/TrendChart";

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

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const [prefs, memberList, allergenOpts] = await Promise.all([
        api.get("/household/preferences"),
        api.get("/household/members"),
        api.get("/household/allergen-options"),
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
      });
      setAllergenOptions(allergenOpts);
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
      });
      setPreferences(updated);
      setPrefsForm((f) => ({
        ...f,
        restricted_allergens: updated.restricted_allergens || [],
        gluten_observance_level: updated.gluten_observance_level || "",
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
