import { useEffect, useState } from "react";
import { api } from "../api";

// Backlog B10.1 -- see dining_service.py's module docstring for the full
// research writeup (OSM diet:* tag coverage, its hard limitations, and
// the safety-framing discipline this page follows). The one-line version:
// this page NEVER tells the user a restaurant is "safe" -- only what a
// crowd-sourced tag says, or that no tag exists at all, always paired
// with a caution to confirm directly with the restaurant.

const PER_ALLERGEN_LABELS = {
  only: "Tagged: this option only (whole menu)",
  yes: "Tagged: available",
  limited: "Tagged: limited availability (a contested/under-discussion tag value)",
  no: "Tagged: not available",
  unknown: "No tag present for this place -- unknown, not necessarily safe",
  no_data: "OpenStreetMap has no tag at all for this allergen",
};

function metersToDistanceLabel(m) {
  const km = m / 1000;
  const mi = m / 1609.34;
  return `${km.toFixed(1)} km (${mi.toFixed(1)} mi)`;
}

export default function DiningPage() {
  const [lat, setLat] = useState("");
  const [lon, setLon] = useState("");
  const [radiusKm, setRadiusKm] = useState(5);
  const [geoStatus, setGeoStatus] = useState(null);
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [allergenLabels, setAllergenLabels] = useState({});

  const [plans, setPlans] = useState([]);
  const [sendTarget, setSendTarget] = useState(null); // { restaurant, planId, entryId }
  const [sendBusy, setSendBusy] = useState(false);
  const [sendError, setSendError] = useState(null);
  const [sendDone, setSendDone] = useState(null);

  useEffect(() => {
    api
      .get("/household/allergen-options")
      .then((opts) => {
        const map = {};
        (opts.allergens || []).forEach((a) => {
          map[a.key] = a.label;
        });
        setAllergenLabels(map);
      })
      .catch(() => {
        // Non-fatal -- falls back to raw allergen keys below.
      });
    api
      .get("/meal-plans")
      .then(setPlans)
      .catch(() => {
        // Non-fatal -- "send to meal plan" just won't have options.
      });
  }, []);

  function useMyLocation() {
    if (!navigator.geolocation) {
      setGeoStatus("Browser geolocation isn't available here -- enter coordinates manually.");
      return;
    }
    setGeoStatus("Locating...");

    // Bug fix (2026-08-02, author-reported on a real iPad): getCurrentPosition
    // was called with no options, so its default `timeout` is Infinity. A
    // WiFi-only iPad has no GPS chip and relies entirely on WiFi-based
    // positioning -- if that fix never resolves (weak/unfamiliar WiFi
    // environment, indoors, Location Services still initializing), NEITHER
    // callback ever fires and the button is stuck on "Locating..." forever
    // with no way out except reloading the page. Two independent fixes:
    // (1) pass an explicit timeout/maximumAge so the browser's own API gives
    // up and calls the error callback instead of hanging indefinitely, and
    // (2) a belt-and-suspenders JS-side fallback timer, since a small number
    // of WebKit versions have shipped with geolocation callbacks that don't
    // reliably fire at all in some states (e.g. Low Power Mode, background
    // tab during the permission prompt) -- if the browser's own timeout
    // doesn't save us, this one still will.
    let settled = false;
    const fallbackTimer = setTimeout(() => {
      if (settled) return;
      settled = true;
      setGeoStatus(
        "Still couldn't get a location fix after 20s (common on WiFi-only iPads away from a well-mapped network) -- enter coordinates manually."
      );
    }, 20000);

    navigator.geolocation.getCurrentPosition(
      (pos) => {
        if (settled) return;
        settled = true;
        clearTimeout(fallbackTimer);
        setLat(String(pos.coords.latitude.toFixed(5)));
        setLon(String(pos.coords.longitude.toFixed(5)));
        setGeoStatus(null);
      },
      (err) => {
        if (settled) return;
        settled = true;
        clearTimeout(fallbackTimer);
        // PERMISSION_DENIED=1, POSITION_UNAVAILABLE=2, TIMEOUT=3 (the
        // MDN-documented GeolocationPositionError codes) -- give a message
        // that actually matches what happened instead of always blaming
        // HTTPS, which as of B15.1 this app now supports either way.
        if (err.code === 1) {
          setGeoStatus(
            "Location permission was denied -- check your browser/iOS Settings > Privacy > Location Services for this site, or enter coordinates manually."
          );
        } else if (err.code === 3) {
          setGeoStatus("Timed out getting your location -- enter coordinates manually.");
        } else if (window.location.protocol !== "https:" && window.location.hostname !== "localhost") {
          // Only blame HTTPS when the page genuinely isn't in a secure
          // context -- most browsers refuse to even ask in that case.
          setGeoStatus("Couldn't get your location (this needs HTTPS in most browsers) -- enter coordinates manually.");
        } else {
          setGeoStatus("Couldn't get your location -- enter coordinates manually.");
        }
      },
      { enableHighAccuracy: false, timeout: 15000, maximumAge: 60000 }
    );
  }

  async function handleSearch(e) {
    e.preventDefault();
    if (lat === "" || lon === "") {
      setError("Enter coordinates, or use \"Use my location\" above.");
      return;
    }
    setLoading(true);
    setError(null);
    setResults(null);
    try {
      const data = await api.get(
        `/dining/nearby?lat=${encodeURIComponent(lat)}&lon=${encodeURIComponent(lon)}&radius_km=${encodeURIComponent(radiusKm)}`
      );
      setResults(data);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  function openSendForm(restaurant) {
    setSendDone(null);
    setSendError(null);
    setSendTarget({ restaurant, planId: plans[0]?.id ?? "", entryId: "" });
  }

  async function handleSendToPlan() {
    if (!sendTarget || !sendTarget.entryId) return;
    setSendBusy(true);
    setSendError(null);
    try {
      const plan = plans.find((p) => p.id === Number(sendTarget.planId));
      const entry = plan?.entries.find((en) => en.id === Number(sendTarget.entryId));
      const note = `Eating out at ${sendTarget.restaurant.name}`;
      const notes = entry?.notes ? `${entry.notes}; ${note}` : note;
      await api.patch(`/meal-plans/${sendTarget.planId}/entries/${sendTarget.entryId}`, {
        is_eating_out: true,
        recipe_id: null,
        notes,
      });
      setSendDone(sendTarget.restaurant.name);
      setSendTarget(null);
      const refreshed = await api.get("/meal-plans");
      setPlans(refreshed);
    } catch (err) {
      setSendError(err.message);
    } finally {
      setSendBusy(false);
    }
  }

  const targetPlan = sendTarget ? plans.find((p) => p.id === Number(sendTarget.planId)) : null;

  return (
    <div>
      <div className="card">
        <h3>Find a place to eat out</h3>
        <p className="hint">
          Results come from OpenStreetMap's crowd-sourced dietary tags, checked against your household's
          restricted allergens. This is a best-effort check, not a guarantee -- always confirm with the
          restaurant directly, especially for a strict/celiac-level restriction.
        </p>
        <form onSubmit={handleSearch}>
          <div className="form-row">
            <label>
              Latitude
              <input
                type="number"
                step="any"
                placeholder="e.g. 30.2672"
                value={lat}
                onChange={(e) => setLat(e.target.value)}
                required
              />
            </label>
            <label>
              Longitude
              <input
                type="number"
                step="any"
                placeholder="e.g. -97.7431"
                value={lon}
                onChange={(e) => setLon(e.target.value)}
                required
              />
            </label>
            <label>
              Search radius (km)
              <input
                type="number"
                min="0.1"
                max="20"
                step="0.5"
                value={radiusKm}
                onChange={(e) => setRadiusKm(e.target.value)}
              />
            </label>
          </div>
          <div className="form-actions">
            <button type="button" className="btn btn-secondary" onClick={useMyLocation}>
              Use my location
            </button>
            <button className="btn btn-primary" type="submit" disabled={loading}>
              {loading ? "Searching..." : "Search"}
            </button>
          </div>
          {geoStatus && <p className="hint">{geoStatus}</p>}
          {error && <p className="error-text">{error}</p>}
        </form>
      </div>

      {sendDone && <p className="hint">Marked as eating out at {sendDone} on your meal plan.</p>}

      {results && (
        <div className="card">
          <h3>
            {results.length} place{results.length === 1 ? "" : "s"} found
          </h3>
          {results.length === 0 && <p className="hint">Nothing turned up in this radius -- try widening the search.</p>}
          {results.map((r) => (
            <div className="dining-result" key={`${r.osm_type}-${r.osm_id}`}>
              <div className="dining-result-header">
                <strong>{r.name}</strong>
                <span className="tag">{metersToDistanceLabel(r.distance_m)}</span>
                {r.cuisine && <span className="tag">{r.cuisine}</span>}
                {r.amenity && <span className="tag">{r.amenity}</span>}
              </div>
              {r.address && <p className="hint">{r.address}</p>}

              {Object.keys(r.per_allergen || {}).length > 0 && (
                <ul className="dining-allergen-list">
                  {Object.entries(r.per_allergen).map(([key, value]) => (
                    <li key={key}>
                      <strong>{allergenLabels[key] || key}:</strong>{" "}
                      {PER_ALLERGEN_LABELS[value] || value}
                    </li>
                  ))}
                </ul>
              )}

              <p className="dining-caution">{r.caution}</p>

              <div className="form-actions">
                <button className="btn btn-secondary btn-sm" onClick={() => openSendForm(r)}>
                  Send to meal plan
                </button>
              </div>

              {sendTarget && sendTarget.restaurant === r && (
                <div className="dining-send-form">
                  <div className="form-row">
                    <label>
                      Meal plan
                      <select
                        value={sendTarget.planId}
                        onChange={(e) => setSendTarget((t) => ({ ...t, planId: e.target.value, entryId: "" }))}
                      >
                        {plans.map((p) => (
                          <option key={p.id} value={p.id}>
                            Week of {p.week_start_date} ({p.status})
                          </option>
                        ))}
                      </select>
                    </label>
                    <label>
                      Slot
                      <select
                        value={sendTarget.entryId}
                        onChange={(e) => setSendTarget((t) => ({ ...t, entryId: e.target.value }))}
                      >
                        <option value="">-- choose a slot --</option>
                        {(targetPlan?.entries || []).map((en) => (
                          <option key={en.id} value={en.id}>
                            {["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][en.day_of_week]} {en.meal_type}
                            {en.recipe ? ` (currently ${en.recipe.title})` : ""}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                  {plans.length === 0 && <p className="hint">No meal plans yet -- create one on the Meal Plan page first.</p>}
                  <div className="form-actions">
                    <button
                      className="btn btn-primary btn-sm"
                      onClick={handleSendToPlan}
                      disabled={sendBusy || !sendTarget.entryId}
                    >
                      {sendBusy ? "Saving..." : "Confirm"}
                    </button>
                    <button className="btn-link" onClick={() => setSendTarget(null)} disabled={sendBusy}>
                      Cancel
                    </button>
                  </div>
                  {sendError && <p className="error-text">{sendError}</p>}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
