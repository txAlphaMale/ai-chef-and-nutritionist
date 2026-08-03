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

// Results are ranked server-side (dining_service.restriction_sort_key), so
// anything the household can actually eat is already at the top. This is
// the client-side "hide the rest" toggle, kept OPT-IN and off by default:
// a missing OSM tag means UNKNOWN, never "unsafe", and hiding untagged
// venues by default would imply a data completeness OpenStreetMap does
// not have.
function hasMatchingTag(result) {
  return Object.values(result.per_allergen || {}).some((v) => v === "only" || v === "yes" || v === "limited");
}

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
  const [ipLocating, setIpLocating] = useState(false);
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [allergenLabels, setAllergenLabels] = useState({});
  const [onlyTagged, setOnlyTagged] = useState(false);

  const [plans, setPlans] = useState([]);
  const [sendTarget, setSendTarget] = useState(null); // { restaurant, planId, entryId }
  const [sendBusy, setSendBusy] = useState(false);
  const [sendError, setSendError] = useState(null);
  const [sendDone, setSendDone] = useState(null);

  // A zip code or address as a third way to set a location, alongside
  // manual lat/lon and "Use my location". Nominatim (OSM's free
  // geocoder) returns several plausible matches for an ambiguous query
  // (a bare zip spanning towns, a common street name), so those are
  // shown as a pick list rather than silently trusting the first.
  const [addressQuery, setAddressQuery] = useState("");
  const [geocoding, setGeocoding] = useState(false);
  const [geocodeError, setGeocodeError] = useState(null);
  const [geocodeCandidates, setGeocodeCandidates] = useState(null);

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
    setGeoStatus("Locating (GPS)...");

    // Two things here are load-bearing, both about iOS geolocation.
    //
    // getCurrentPosition's default `timeout` is Infinity. Called with no
    // options, a fix that never resolves fires NEITHER callback, and the
    // button sticks on "Locating..." until the page is reloaded. Hence
    // the explicit timeout/maximumAge, plus a JS-side fallback timer --
    // some WebKit versions ship with geolocation callbacks that do not
    // reliably fire at all in certain states, e.g. Low Power Mode.
    //
    // High accuracy is attempted FIRST, then falls back. Hardcoding
    // `enableHighAccuracy: false` tells iOS Core Location it may answer
    // from WiFi/cell-tower positioning alone, which is coarser than a
    // real GPS fix even on hardware that could get one. Trying GPS first
    // (bounded by its own timeout, so the hang above cannot return) and
    // falling back on failure works for both GPS-capable and WiFi-only
    // devices without guessing which the user has.
    let settled = false;
    const fallbackTimer = setTimeout(() => {
      if (settled) return;
      settled = true;
      setGeoStatus("Still couldn't get a location fix -- enter coordinates manually, or try the approximate network-based option below.");
    }, 35000);

    function onSuccess(pos) {
      if (settled) return;
      settled = true;
      clearTimeout(fallbackTimer);
      setLat(String(pos.coords.latitude.toFixed(5)));
      setLon(String(pos.coords.longitude.toFixed(5)));
      setGeoStatus(
        pos.coords.accuracy != null
          ? `Located (accuracy ~${Math.round(pos.coords.accuracy)}m).`
          : null
      );
    }

    function tryLowAccuracy() {
      setGeoStatus("GPS fix unavailable -- trying network-based location...");
      navigator.geolocation.getCurrentPosition(onSuccess, onFinalError, {
        enableHighAccuracy: false,
        timeout: 12000,
        maximumAge: 60000,
      });
    }

    function onFinalError(err) {
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
        setGeoStatus("Timed out getting your location -- enter coordinates manually, or try the approximate network-based option below.");
      } else if (window.location.protocol !== "https:" && window.location.hostname !== "localhost") {
        // Only blame HTTPS when the page genuinely isn't in a secure
        // context -- most browsers refuse to even ask in that case.
        setGeoStatus("Couldn't get your location (this needs HTTPS in most browsers) -- enter coordinates manually.");
      } else {
        setGeoStatus("Couldn't get your location -- enter coordinates manually, or try the approximate network-based option below.");
      }
    }

    navigator.geolocation.getCurrentPosition(
      onSuccess,
      (err) => {
        if (settled) return;
        // A denied permission won't change on retry with different
        // accuracy options -- go straight to the final error instead of
        // wasting another round trip (and prompt) on it.
        if (err.code === 1) {
          onFinalError(err);
        } else {
          tryLowAccuracy();
        }
      },
      { enableHighAccuracy: true, timeout: 20000, maximumAge: 0 }
    );
  }

  // A third location option alongside GPS and manual address/zip entry:
  // approximate, network-based, no permission prompt. See
  // dining_service.py's IPWHOIS_URL comment for exactly what this
  // reflects (the backend's own outbound IP) and its caveat.
  async function useApproximateNetworkLocation() {
    setIpLocating(true);
    setGeoStatus("Looking up an approximate location from your network...");
    try {
      const result = await api.get("/dining/geolocate-by-ip");
      setLat(String(result.lat.toFixed(5)));
      setLon(String(result.lon.toFixed(5)));
      const place = [result.city, result.region].filter(Boolean).join(", ");
      setGeoStatus(
        `Approximate location${place ? ` (${place})` : ""} -- network-based, city-level accuracy at best, not your exact position.`
      );
    } catch (err) {
      setGeoStatus(`Couldn't get an approximate network location: ${err.message}`);
    } finally {
      setIpLocating(false);
    }
  }

  async function handleGeocode(e) {
    e.preventDefault();
    if (!addressQuery.trim()) return;
    setGeocoding(true);
    setGeocodeError(null);
    setGeocodeCandidates(null);
    try {
      const candidates = await api.get(`/dining/geocode?query=${encodeURIComponent(addressQuery.trim())}`);
      if (candidates.length === 1) {
        applyGeocodeCandidate(candidates[0]);
      } else {
        setGeocodeCandidates(candidates);
      }
    } catch (err) {
      setGeocodeError(err.message);
    } finally {
      setGeocoding(false);
    }
  }

  function applyGeocodeCandidate(candidate) {
    setLat(String(candidate.lat.toFixed(5)));
    setLon(String(candidate.lon.toFixed(5)));
    setGeocodeCandidates(null);
    setGeoStatus(null);
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

  const visibleResults = onlyTagged ? (results || []).filter(hasMatchingTag) : results || [];
  const taggedCount = (results || []).filter(hasMatchingTag).length;

  return (
    <div>
      <div className="card">
        <h3>Find a place to eat out</h3>
        <p className="hint">
          Results come from OpenStreetMap's crowd-sourced dietary tags, checked against your household's
          restricted allergens. This is a best-effort check, not a guarantee -- always confirm with the
          restaurant directly, especially for a strict/celiac-level restriction.
        </p>
        <div className="form-row dining-geocode-row">
          <label className="u-flex-1">
            Address or zip code
            <input
              placeholder="e.g. 78701 or 500 Congress Ave, Austin TX"
              value={addressQuery}
              onChange={(e) => setAddressQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleGeocode(e);
              }}
            />
          </label>
          <button type="button" className="btn btn-secondary" onClick={handleGeocode} disabled={geocoding || !addressQuery.trim()}>
            {geocoding && <span className="busy-spinner" aria-hidden="true" />}
            {geocoding ? "Looking up..." : "Look up"}
          </button>
        </div>
        {geocodeError && <p className="error-text">{geocodeError}</p>}
        {geocodeCandidates && geocodeCandidates.length > 0 && (
          <div className="dining-geocode-candidates">
            <p className="hint">Multiple matches -- pick the right one:</p>
            <ul>
              {geocodeCandidates.map((c, i) => (
                <li key={i}>
                  <button type="button" className="btn-link" onClick={() => applyGeocodeCandidate(c)}>
                    {c.display_name}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

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
                // min must line up with step: with min="0.1" step="0.5"
                // the browser only accepts 0.1, 0.6, 1.1 ... so the
                // default value of 5 is not a valid step and native
                // validation rejects it. min="0.5" makes every half-step
                // round number valid.
                min="0.5"
                max="20"
                step="0.5"
                value={radiusKm}
                onChange={(e) => setRadiusKm(e.target.value)}
              />
            </label>
          </div>
          <div className="form-actions">
            <button type="button" className="btn btn-secondary" onClick={useMyLocation}>
              Use my location (GPS)
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={useApproximateNetworkLocation}
              disabled={ipLocating}
              title="Approximate, city-level location based on this server's network -- no permission prompt, less precise than GPS"
            >
              {ipLocating && <span className="busy-spinner" aria-hidden="true" />}
              {ipLocating ? "Looking up..." : "Use approximate location"}
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
          {results.length > 0 && (
            <>
              <p className="hint">
                Sorted by how well each place matches your household's restrictions first, then by distance.{" "}
                {taggedCount} of {results.length} have a relevant dietary tag in OpenStreetMap; the rest are
                untagged, which means unknown -- not that they're unsuitable.
              </p>
              <label className="dining-filter-toggle">
                <input
                  type="checkbox"
                  checked={onlyTagged}
                  onChange={(e) => setOnlyTagged(e.target.checked)}
                />
                Only show places with a matching dietary tag
              </label>
            </>
          )}
          {results.length === 0 && <p className="hint">Nothing turned up in this radius -- try widening the search.</p>}
          {visibleResults.length === 0 && results.length > 0 && (
            <p className="hint">
              None of the places found here carry a dietary tag. Untick the filter to see them all and call ahead.
            </p>
          )}
          {visibleResults.map((r) => (
            <div className="dining-result" key={`${r.osm_type}-${r.osm_id}`}>
              <div className="dining-result-header">
                <strong>{r.name}</strong>
                <span className="tag">{metersToDistanceLabel(r.distance_m)}</span>
                {r.cuisine && <span className="tag">{r.cuisine}</span>}
                {r.amenity && <span className="tag">{r.amenity}</span>}
              </div>
              {r.address && <p className="hint">{r.address}</p>}
              {/* Contact details come from OSM tags the search already
                  returned. Calling ahead is the action every caution
                  message on this page asks for, so the phone number
                  belongs next to it rather than nowhere. */}
              <p className="dining-contact">
                {r.phone && (
                  <a href={`tel:${r.phone.replace(/[^+\d]/g, "")}`} className="dining-contact-link">
                    {r.phone}
                  </a>
                )}
                {r.website && (
                  <a href={r.website} target="_blank" rel="noopener noreferrer" className="dining-contact-link">
                    Website
                  </a>
                )}
                {r.map_url && (
                  <a href={r.map_url} target="_blank" rel="noopener noreferrer" className="dining-contact-link">
                    Map
                  </a>
                )}
                <a
                  href={`geo:${r.lat},${r.lon}?q=${encodeURIComponent(r.name)}`}
                  className="dining-contact-link"
                >
                  Directions
                </a>
              </p>
              {r.opening_hours && <p className="hint">Hours (as mapped): {r.opening_hours}</p>}

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
