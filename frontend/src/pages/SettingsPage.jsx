import { useEffect, useMemo, useState } from "react";
import { api, backendOrigin } from "../api";
import { THEME_OPTIONS, applyTheme } from "../themes";

// Settings GUI (Phase 8): DB-backed settings (Ollama endpoint/models,
// Tavily key) and system prompts are edited here, one field/prompt at a
// time, each with its own save action -- so updating the Tavily key
// doesn't require re-entering the Ollama URL, and vice versa. Backed by
// the PATCH /api/system/settings/{key} and /api/system/prompts/{key}
// endpoints added alongside this page; the read side (GET) already
// existed from Phase 2. Household size and dietary restrictions live on
// the Health page (Phase 6) since they're tied to household-preferences
// CRUD there -- linked from here rather than duplicated.

const PROMPT_LABELS = {
  main_chef: "Main chef system prompt",
  dietary_onboarding: "Dietary onboarding prompt",
};

// Backlog B12.1 -- these four settings are written automatically by
// google_calendar_service.py's OAuth flow, never hand-typed, so they're
// excluded from the generic settings loop below (which renders a plain
// text/password box per key) and instead surfaced through the dedicated
// Google Calendar card's proper connect/status/toggle UI.
const GOOGLE_CALENDAR_MANAGED_KEYS = [
  "google_calendar_refresh_token",
  "google_calendar_calendar_id",
  "google_calendar_account_email",
  "google_calendar_sync_enabled",
];

export default function SettingsPage() {
  const [settings, setSettings] = useState([]);
  const [settingEdits, setSettingEdits] = useState({});
  const [settingSaving, setSettingSaving] = useState({});
  const [settingSaved, setSettingSaved] = useState({});

  const [prompts, setPrompts] = useState([]);
  const [promptEdits, setPromptEdits] = useState({});
  const [promptSaving, setPromptSaving] = useState({});
  const [promptSaved, setPromptSaved] = useState({});

  const [status, setStatus] = useState(null);
  const [statusLoading, setStatusLoading] = useState(true);

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [themeSaving, setThemeSaving] = useState(false);

  // Backlog B9.4 (via the author-requested B10.2 group, 2026-08-01) --
  // the lightweight, opt-in single-shared-password gate. See
  // backend/app/services/auth_service.py's module docstring for why
  // this is deliberately smaller than Fiduciary's own multi-user/MFA
  // system (confirmed with the author directly, not assumed).
  const [authStatus, setAuthStatus] = useState(null);
  const [authBusy, setAuthBusy] = useState(false);
  const [authError, setAuthError] = useState(null);
  const [authSaved, setAuthSaved] = useState(false);
  const [newPassword, setNewPassword] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [disableCurrentPassword, setDisableCurrentPassword] = useState("");

  async function refreshAuthStatus() {
    try {
      setAuthStatus(await api.get("/auth/status"));
    } catch (e) {
      setAuthStatus(null);
      setAuthError(e.message);
    }
  }

  async function handleSetPassword(e) {
    e.preventDefault();
    setAuthBusy(true);
    setAuthError(null);
    setAuthSaved(false);
    try {
      await api.post("/auth/set-password", {
        password: newPassword,
        current_password: authStatus?.enabled ? currentPassword : null,
      });
      setNewPassword("");
      setCurrentPassword("");
      setAuthSaved(true);
      setTimeout(() => setAuthSaved(false), 2000);
      await refreshAuthStatus();
    } catch (err) {
      setAuthError(err.message);
    } finally {
      setAuthBusy(false);
    }
  }

  async function handleDisableAuth(e) {
    e.preventDefault();
    setAuthBusy(true);
    setAuthError(null);
    try {
      await api.post("/auth/disable", { current_password: disableCurrentPassword });
      setDisableCurrentPassword("");
      await refreshAuthStatus();
    } catch (err) {
      setAuthError(err.message);
    } finally {
      setAuthBusy(false);
    }
  }

  // Backlog: Appearance/theme picker. ui_theme round-trips through the
  // same generic DB-backed settings API as everything else (see
  // settings_service.py), but gets its own swatch-card UI here rather
  // than the plain text/password field the generic settings loop below
  // renders for every other key -- so it's read out of `settings`
  // directly instead of duplicated in its own state.
  const currentTheme = settings.find((s) => s.key === "ui_theme")?.value || "default";
  const otherSettings = settings.filter((s) => s.key !== "ui_theme" && !GOOGLE_CALENDAR_MANAGED_KEYS.includes(s.key));

  // Backlog B12.1 -- Google Calendar connection status + the OAuth
  // connect/disconnect/sync-toggle/resync actions. Kept separate from
  // the generic settings state above since this isn't a plain text-box
  // edit -- it's a multi-step connection with server-driven state
  // (connected?, which account, which calendar).
  const [gcalStatus, setGcalStatus] = useState(null);
  const [gcalBusy, setGcalBusy] = useState(false);
  const [gcalError, setGcalError] = useState(null);
  const [gcalBanner, setGcalBanner] = useState(null); // {type: "success"|"error", message}

  async function refreshGcalStatus() {
    try {
      setGcalStatus(await api.get("/calendar/google/status"));
    } catch (e) {
      setGcalError(e.message);
    }
  }

  useEffect(() => {
    // The OAuth callback (backend) redirects back here with
    // #/settings?google_calendar=connected|error&message=... -- read it
    // once on mount, same "parse the hash's own query half" approach
    // WikiPage's deep-link handling uses (HashRouter puts the route's
    // query params after the route's own '?', not the page URL's).
    const hashQuery = window.location.hash.split("?")[1] || "";
    const params = new URLSearchParams(hashQuery);
    const result = params.get("google_calendar");
    if (result === "connected") {
      setGcalBanner({ type: "success", message: "Google Calendar connected -- sync is now on." });
    } else if (result === "error") {
      setGcalBanner({ type: "error", message: params.get("message") || "Google Calendar connection failed." });
    }
    if (result) {
      // Strip the query so a page refresh doesn't re-show a stale banner.
      window.history.replaceState(null, "", window.location.pathname + window.location.search + "#/settings");
    }
    refreshGcalStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function connectGoogleCalendar() {
    // A real browser navigation, not a fetch -- this has to follow
    // Google's own redirect chain through the consent screen, which an
    // XHR/fetch call can't do. return_to carries this device's own
    // origin so the backend callback can send the browser back here
    // specifically, regardless of which device on the LAN initiated it.
    window.location.href = `${backendOrigin}/api/calendar/google/authorize?return_to=${encodeURIComponent(window.location.origin)}`;
  }

  async function disconnectGoogleCalendar() {
    setGcalBusy(true);
    setGcalError(null);
    try {
      await refreshAfter(api.post("/calendar/google/disconnect", {}));
    } catch (e) {
      setGcalError(e.message);
    } finally {
      setGcalBusy(false);
    }
  }

  async function toggleGcalSync(enabled) {
    setGcalBusy(true);
    setGcalError(null);
    try {
      await refreshAfter(api.patch("/calendar/google/sync-enabled", { enabled }));
    } catch (e) {
      setGcalError(e.message);
    } finally {
      setGcalBusy(false);
    }
  }

  async function resyncGoogleCalendar() {
    setGcalBusy(true);
    setGcalError(null);
    try {
      // Fire-and-forget -- the global JobsBadge (already mounted in
      // App.jsx outside <Routes>) picks up the enqueued job and shows
      // its progress, so there's nothing further to poll here.
      await api.post("/calendar/google/resync", {});
    } catch (e) {
      setGcalError(e.message);
    } finally {
      setGcalBusy(false);
    }
  }

  async function refreshAfter(promise) {
    const result = await promise;
    setGcalStatus(result);
    return result;
  }
  const themeGroups = useMemo(() => {
    const groups = [];
    for (const t of THEME_OPTIONS) {
      let g = groups.find((g) => g.name === t.group);
      if (!g) {
        g = { name: t.group, themes: [] };
        groups.push(g);
      }
      g.themes.push(t);
    }
    return groups;
  }, []);

  function applySettings(list) {
    setSettings(list);
    setSettingEdits((prev) => {
      const next = { ...prev };
      for (const s of list) {
        // Secrets are never sent back in the clear (masked as ********
        // by the backend) -- leave that field blank so a save only
        // happens if the user actually types a new value. Non-secret
        // fields are prefilled with their current value for editing.
        if (!(s.key in next)) next[s.key] = s.is_secret ? "" : s.value;
      }
      return next;
    });
  }

  function applyPrompts(list) {
    setPrompts(list);
    setPromptEdits((prev) => {
      const next = { ...prev };
      for (const p of list) {
        if (!(p.prompt_key in next)) next[p.prompt_key] = { content: p.content, is_active: p.is_active };
      }
      return next;
    });
  }

  async function refreshStatus() {
    setStatusLoading(true);
    try {
      setStatus(await api.get("/system/status"));
    } catch (e) {
      setStatus({ error: e.message });
    } finally {
      setStatusLoading(false);
    }
  }

  useEffect(() => {
    (async () => {
      setLoading(true);
      setError(null);
      try {
        const [settingsList, promptsList] = await Promise.all([
          api.get("/system/settings"),
          api.get("/system/prompts"),
        ]);
        applySettings(settingsList);
        applyPrompts(promptsList);
      } catch (e) {
        setError(e.message);
      } finally {
        setLoading(false);
      }
    })();
    refreshStatus();
    refreshAuthStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function saveSetting(spec) {
    const value = settingEdits[spec.key] ?? "";
    if (spec.is_secret && value.trim() === "") return; // don't blank a secret by accident
    setSettingSaving((s) => ({ ...s, [spec.key]: true }));
    setSettingSaved((s) => ({ ...s, [spec.key]: false }));
    try {
      const updated = await api.patch(`/system/settings/${spec.key}`, { value });
      applySettings(updated);
      // A saved secret goes back to a blank "enter to change" field;
      // a saved plain value is re-synced from the server response.
      setSettingEdits((prev) => ({ ...prev, [spec.key]: spec.is_secret ? "" : value }));
      setSettingSaved((s) => ({ ...s, [spec.key]: true }));
      setTimeout(() => setSettingSaved((s) => ({ ...s, [spec.key]: false })), 2000);
    } catch (e) {
      setError(e.message);
    } finally {
      setSettingSaving((s) => ({ ...s, [spec.key]: false }));
    }
  }

  async function selectTheme(key) {
    if (key === currentTheme || themeSaving) return;
    setThemeSaving(true);
    setError(null);
    try {
      // Apply instantly rather than waiting on the round trip -- the
      // save is expected to succeed (no per-key validation on this
      // field, same as every other setting), and an instant preview is
      // the whole point of a swatch picker.
      applyTheme(key);
      const updated = await api.patch("/system/settings/ui_theme", { value: key });
      applySettings(updated);
    } catch (e) {
      setError(e.message);
      applyTheme(currentTheme); // save failed -- revert the instant preview
    } finally {
      setThemeSaving(false);
    }
  }

  async function savePrompt(promptKey) {
    const edit = promptEdits[promptKey];
    setPromptSaving((s) => ({ ...s, [promptKey]: true }));
    setPromptSaved((s) => ({ ...s, [promptKey]: false }));
    try {
      const updated = await api.patch(`/system/prompts/${promptKey}`, edit);
      setPrompts((prev) => prev.map((p) => (p.prompt_key === promptKey ? updated : p)));
      setPromptSaved((s) => ({ ...s, [promptKey]: true }));
      setTimeout(() => setPromptSaved((s) => ({ ...s, [promptKey]: false })), 2000);
    } catch (e) {
      setError(e.message);
    } finally {
      setPromptSaving((s) => ({ ...s, [promptKey]: false }));
    }
  }

  return (
    <div>
      {error && <p className="error-text">{error}</p>}

      <div className="card">
        <h3>Appearance</h3>
        <p className="hint">
          Applies instantly and is saved to the database (persists across container rebuilds, same as every other
          setting here).
        </p>
        {themeGroups.map((group) => (
          <div key={group.name}>
            <p className="theme-group-label">{group.name}</p>
            <div className="theme-swatch-grid">
              {group.themes.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  className={`theme-swatch-card${t.key === currentTheme ? " active" : ""}`}
                  onClick={() => selectTheme(t.key)}
                  disabled={themeSaving}
                >
                  <span className="theme-swatch-dots">
                    {Object.values(t.swatches).map((color, i) => (
                      <span className="theme-swatch-dot" key={i} style={{ background: color }} />
                    ))}
                  </span>
                  <span className="theme-swatch-label">{t.label}</span>
                  {t.key === currentTheme && <span className="theme-swatch-active-marker">&#10003; active</span>}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="page-toolbar">
          <h3 style={{ margin: 0 }}>Connection status</h3>
          <button className="btn btn-secondary btn-sm" onClick={refreshStatus} disabled={statusLoading}>
            {statusLoading ? "Checking..." : "Refresh"}
          </button>
        </div>
        {status && !status.error ? (
          <div className="settings-status-grid">
            <span className={`status-dot ${status.ollama_reachable ? "status-ok" : "status-bad"}`} />
            <span>Ollama {status.ollama_reachable ? "reachable" : "not reachable"}</span>
            <span className={`status-dot ${status.tavily_configured ? "status-ok" : "status-bad"}`} />
            <span>Tavily {status.tavily_configured ? "configured" : "not configured"}</span>
          </div>
        ) : (
          <p className="hint">{status?.error || "Checking..."}</p>
        )}
      </div>

      <div className="card">
        <h3>Security</h3>
        <p className="hint">
          Off by default -- fine on a private LAN. Turning this on protects every page and API request behind one
          shared household password (no per-person accounts, no MFA -- see the notes in PROJECT-PLAN.md for why
          this is deliberately lighter than a full multi-user login system).
        </p>
        {authStatus ? (
          <>
            <p>
              Password protection is currently{" "}
              <strong>{authStatus.enabled ? "enabled" : "disabled"}</strong>.
            </p>
            <form onSubmit={handleSetPassword} className="settings-row">
              {authStatus.enabled && (
                <label>
                  Current password
                  <input
                    type="password"
                    value={currentPassword}
                    onChange={(e) => setCurrentPassword(e.target.value)}
                    required
                  />
                </label>
              )}
              <label>
                {authStatus.enabled ? "New password" : "Set a password"}
                <input
                  type="password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  minLength={8}
                  placeholder="at least 8 characters"
                  required
                />
              </label>
              <div className="form-actions">
                <button className="btn btn-primary btn-sm" type="submit" disabled={authBusy || newPassword.length < 8}>
                  {authBusy ? "Saving..." : authStatus.enabled ? "Change password" : "Enable password protection"}
                </button>
                {authSaved && <span className="hint">Saved.</span>}
              </div>
            </form>
            {authStatus.enabled && (
              <form onSubmit={handleDisableAuth} className="settings-row">
                <label>
                  Current password (to disable)
                  <input
                    type="password"
                    value={disableCurrentPassword}
                    onChange={(e) => setDisableCurrentPassword(e.target.value)}
                    required
                  />
                </label>
                <div className="form-actions">
                  <button className="btn btn-secondary btn-sm" type="submit" disabled={authBusy || !disableCurrentPassword}>
                    Disable password protection
                  </button>
                </div>
              </form>
            )}
            {authError && <p className="error-text">{authError}</p>}
          </>
        ) : (
          <p className="hint">{authError || "Loading..."}</p>
        )}
      </div>

      <div className="card">
        <h3>AI services &amp; secrets</h3>
        <p className="hint">
          Stored in the database (encrypted at rest for secrets), not in <code>.env</code> -- changes take effect on
          the next request, no rebuild needed.
        </p>
        {loading ? (
          <p>Loading...</p>
        ) : (
          otherSettings.map((spec) => (
            <div className="settings-row" key={spec.key}>
              <label>
                {spec.label}
                {spec.options ? (
                  // Backlog fix 2026-08-01 -- a setting with a fixed,
                  // enumerated set of valid values (e.g. default_unit_system)
                  // gets a <select>, not a free-text box the user has no way
                  // to know the accepted values for.
                  <select
                    value={settingEdits[spec.key] ?? spec.value}
                    onChange={(e) => setSettingEdits((prev) => ({ ...prev, [spec.key]: e.target.value }))}
                  >
                    {spec.options.map((opt) => (
                      <option key={opt} value={opt}>
                        {opt}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    type={spec.is_secret ? "password" : "text"}
                    placeholder={spec.is_secret ? (spec.is_set ? "•••••••• (set -- enter a new value to change)" : "not set") : ""}
                    value={settingEdits[spec.key] ?? ""}
                    onChange={(e) => setSettingEdits((prev) => ({ ...prev, [spec.key]: e.target.value }))}
                  />
                )}
              </label>
              <p className="hint">{spec.description}</p>
              <div className="form-actions">
                <button
                  className="btn btn-primary btn-sm"
                  onClick={() => saveSetting(spec)}
                  disabled={settingSaving[spec.key] || (spec.is_secret && (settingEdits[spec.key] ?? "").trim() === "")}
                >
                  {settingSaving[spec.key] ? "Saving..." : "Save"}
                </button>
                {settingSaved[spec.key] && <span className="hint">Saved.</span>}
              </div>
            </div>
          ))
        )}
      </div>

      <div className="card">
        <h3>Google Calendar</h3>
        <p className="hint">
          Backlog B12.1 -- pushes your weekly meal plan into a dedicated "Chef Meal Plan" calendar in your Google
          account, kept in sync automatically as the plan changes. Share that calendar with the rest of the
          household from Google Calendar's own sharing settings. First-time setup needs your own free Google Cloud
          OAuth client -- see the full walkthrough in the <a href="#/wiki?entry=google-calendar-setup">WIKI</a>.
        </p>

        {gcalBanner && (
          <p className={`gcal-banner ${gcalBanner.type}`}>{gcalBanner.message}</p>
        )}
        {gcalError && <p className="error-text">{gcalError}</p>}

        {gcalStatus ? (
          gcalStatus.connected ? (
            <>
              <p>
                Connected as <strong>{gcalStatus.account_email || "(unknown account)"}</strong>.{" "}
                {gcalStatus.calendar_html_link && (
                  <a href={gcalStatus.calendar_html_link} target="_blank" rel="noreferrer">
                    Open "Chef Meal Plan" in Google Calendar
                  </a>
                )}
              </p>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={gcalStatus.sync_enabled}
                  disabled={gcalBusy}
                  onChange={(e) => toggleGcalSync(e.target.checked)}
                />
                Sync meal-plan changes to Google Calendar
              </label>
              <div className="form-actions">
                <button className="btn btn-secondary btn-sm" onClick={resyncGoogleCalendar} disabled={gcalBusy}>
                  Force resync
                </button>
                <button className="btn btn-secondary btn-sm" onClick={disconnectGoogleCalendar} disabled={gcalBusy}>
                  Disconnect
                </button>
              </div>
            </>
          ) : (
            <>
              <p className="hint">
                {gcalStatus.configured
                  ? "OAuth client is configured -- click Connect to finish linking your Google account."
                  : "Enter your Google OAuth client ID, secret, and redirect URI below first (see the WIKI guide above), then Connect."}
              </p>
              <button
                className="btn btn-primary btn-sm"
                onClick={connectGoogleCalendar}
                disabled={!gcalStatus.configured}
              >
                Connect Google Calendar
              </button>
            </>
          )
        ) : (
          <p className="hint">Loading...</p>
        )}
      </div>

      <div className="card">
        <h3>System prompts</h3>
        <p className="hint">
          The persona and rules the AI follows for chat, meal planning, and onboarding. Edit with care -- the app
          relies on these mentioning things like confirming before writes and respecting dietary restrictions.
        </p>
        {loading ? (
          <p>Loading...</p>
        ) : (
          prompts.map((p) => (
            <div className="settings-row" key={p.prompt_key}>
              <label>
                {PROMPT_LABELS[p.prompt_key] || p.prompt_key}
                <textarea
                  rows={8}
                  value={promptEdits[p.prompt_key]?.content ?? ""}
                  onChange={(e) =>
                    setPromptEdits((prev) => ({
                      ...prev,
                      [p.prompt_key]: { ...prev[p.prompt_key], content: e.target.value },
                    }))
                  }
                />
              </label>
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={promptEdits[p.prompt_key]?.is_active ?? true}
                  onChange={(e) =>
                    setPromptEdits((prev) => ({
                      ...prev,
                      [p.prompt_key]: { ...prev[p.prompt_key], is_active: e.target.checked },
                    }))
                  }
                />
                Active
              </label>
              <div className="form-actions">
                <button className="btn btn-primary btn-sm" onClick={() => savePrompt(p.prompt_key)} disabled={promptSaving[p.prompt_key]}>
                  {promptSaving[p.prompt_key] ? "Saving..." : "Save"}
                </button>
                {promptSaved[p.prompt_key] && <span className="hint">Saved.</span>}
              </div>
            </div>
          ))
        )}
      </div>

      <div className="card">
        <h3>Household size &amp; dietary restrictions</h3>
        <p className="hint">
          Managed on the <a href="#/health">Health page</a> under "Household preferences," alongside member profiles
          and body metrics that also steer meal planning.
        </p>
      </div>
    </div>
  );
}
