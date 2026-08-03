import { Fragment, useEffect, useMemo, useState } from "react";
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
//
// Backlog B14 (author-requested 2026-08-01): this page grew one card at
// a time across many sessions (Appearance, Backup, Connection status,
// Security, the generic settings loop, Google Calendar, System prompts)
// until it became one long scroll the author flagged directly as
// "crowded." Reorganized into sub-tabs mirroring the sibling Fiduciary
// project's own settings layout (its panelSubtabs() pattern -- a
// second-level tab bar within one top-level page, persisted per-panel).
// Connection status is deliberately kept OUTSIDE the tab content and
// pinned above the tab bar instead -- the author's own explicit ask --
// since "is anything broken right now" is relevant no matter which
// settings group you're editing.

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

// The three settings the household actually types in. Saving any of
// these should refresh the Google Calendar card's connection status
// (see saveSetting below) -- without this, an author-reported bug: the
// card's `configured` flag was only ever fetched once on page load, so
// the Connect button stayed silently disabled even after valid values
// were saved, with zero visible feedback explaining why.
const GOOGLE_CALENDAR_CONFIG_KEYS = [
  "google_calendar_client_id",
  "google_calendar_client_secret",
  "google_calendar_redirect_uri",
];

const GOOGLE_CALENDAR_CALLBACK_PATH = "/api/calendar/google/callback";

// Backlog B12.2 -- same "written automatically, excluded from the
// generic loop" treatment as GOOGLE_CALENDAR_MANAGED_KEYS above, minus
// account_email/refresh_token (iCloud's app-specific-password auth has
// neither -- see icloud_calendar_service.py's module docstring for why
// this needed no OAuth token dance at all).
const ICLOUD_CALENDAR_MANAGED_KEYS = ["icloud_calendar_calendar_href", "icloud_calendar_sync_enabled"];
const ICLOUD_CALENDAR_CONFIG_KEYS = ["icloud_calendar_username", "icloud_calendar_app_password"];

// Backlog B14 -- which generic (label/description/options-driven) setting
// keys render in which sub-tab. Anything not listed here still renders
// (a new setting added to settings_service.py without a bucket entry
// falls into DEFAULT_SETTINGS_TAB below rather than silently vanishing).
const AI_MODEL_SETTING_KEYS = [
  "ollama_base_url",
  "ollama_chat_model",
  "ollama_vision_model",
  "ollama_embed_model",
  "ollama_num_ctx",
];
const INTEGRATION_SETTING_KEYS = [
  "tavily_api_key",
  "usda_fdc_api_key",
  "openfda_api_key",
  "google_calendar_client_id",
  "google_calendar_client_secret",
  "google_calendar_redirect_uri",
  "icloud_calendar_username",
  "icloud_calendar_app_password",
  "recipe_import_folder_path",
];
const PREFERENCE_SETTING_KEYS = ["default_unit_system", "household_timezone"];
const DEFAULT_SETTINGS_TAB = "integrations";

const SETTINGS_TABS = [
  { key: "appearance", label: "Appearance" },
  { key: "ai", label: "AI & Models" },
  { key: "integrations", label: "Integrations" },
  { key: "preferences", label: "Preferences" },
  { key: "security", label: "Security" },
  { key: "backup", label: "Backup & Data" },
];
const SETTINGS_TAB_STORAGE_KEY = "chefSettingsTab";

// Corrected 2026-08-01 -- the author hit this directly in Google Cloud
// Console: a redirect URI built from the browser's own LAN address
// (e.g. http://10.11.24.21:8095/...) is REJECTED by Google's own
// redirect URI validation, not by anything Chef does. Verified against
// Google's own documented validation rules (Redirect URI validation
// rules table, developers.google.com/identity/protocols/oauth2/
// web-server): "Hosts cannot be raw IP addresses. Localhost IP
// addresses are exempted from this rule" and "Redirect URIs must use
// the HTTPS scheme, not plain HTTP. Localhost URIs (including localhost
// IP address URIs) are exempt from this rule." So a bare LAN IP is
// rejected outright, but http://localhost:<port> (or 127.0.0.1) is
// explicitly exempt from BOTH the HTTPS requirement and the
// raw-IP-host ban, for any OAuth client type including Web application
// -- it isn't a hack, it's Google's own documented carve-out. See the
// WIKI's Google Calendar setup entry for what this means in practice
// (the one-time "Connect" click needs to happen from a browser that can
// reach the backend AS localhost -- i.e. on the server machine itself,
// or via an SSH/port-forward tunnel -- unless the household would
// rather set up a public-DNS-to-LAN-IP hostname instead, also covered
// there).
function isRawIpHost(origin) {
  try {
    const host = new URL(origin).hostname;
    return /^(\d{1,3}\.){3}\d{1,3}$/.test(host) || host === "" || host.startsWith("[");
  } catch {
    return false;
  }
}

function suggestedRedirectUri(origin) {
  if (!origin) return null;
  if (isRawIpHost(origin)) {
    const port = new URL(origin).port || "80";
    return `http://localhost:${port}${GOOGLE_CALENDAR_CALLBACK_PATH}`;
  }
  return `${origin}${GOOGLE_CALENDAR_CALLBACK_PATH}`;
}

function settingsTabFor(key) {
  if (AI_MODEL_SETTING_KEYS.includes(key)) return "ai";
  if (INTEGRATION_SETTING_KEYS.includes(key)) return "integrations";
  if (PREFERENCE_SETTING_KEYS.includes(key)) return "preferences";
  return DEFAULT_SETTINGS_TAB;
}

export default function SettingsPage() {
  const [activeTab, setActiveTab] = useState(() => {
    try {
      return localStorage.getItem(SETTINGS_TAB_STORAGE_KEY) || "appearance";
    } catch {
      return "appearance";
    }
  });

  function selectTab(key) {
    setActiveTab(key);
    try {
      localStorage.setItem(SETTINGS_TAB_STORAGE_KEY, key);
    } catch {
      // localStorage unavailable (private browsing, etc) -- tab selection just won't persist
    }
  }

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

  // Backlog B15.1 (author-reported 2026-08-01) -- HTTPS certificate
  // management. Kept separate from the generic settings loop (like the
  // Google Calendar card) since this isn't a plain value edit: it's a
  // multi-step generate/import flow with server-driven status (active?,
  // method, SANs, expiry) and a self-triggered backend restart. See
  // backend/app/services/tls_service.py's module docstring for the full
  // two-container architecture this UI is driving.
  const [tlsStatus, setTlsStatus] = useState(null);
  const [tlsBusy, setTlsBusy] = useState(false);
  const [tlsError, setTlsError] = useState(null);
  const [tlsNotice, setTlsNotice] = useState(null);
  const [selfSignedHosts, setSelfSignedHosts] = useState("");
  const [csrCommonName, setCsrCommonName] = useState("");
  const [csrSans, setCsrSans] = useState("");
  const [csrResult, setCsrResult] = useState(null);
  const [importCertPem, setImportCertPem] = useState("");
  const [importChainPem, setImportChainPem] = useState("");

  async function refreshTlsStatus() {
    try {
      setTlsStatus(await api.get("/tls/status"));
    } catch (e) {
      setTlsError(e.message);
    }
  }

  async function generateSelfSigned(e) {
    e.preventDefault();
    const hostnames = selfSignedHosts
      .split(/[,\s]+/)
      .map((h) => h.trim())
      .filter(Boolean);
    setTlsBusy(true);
    setTlsError(null);
    setTlsNotice(null);
    try {
      const result = await api.post("/tls/self-signed", { hostnames });
      setTlsStatus(result.status);
      setTlsNotice(
        "Self-signed certificate generated -- the backend is restarting now to apply it (a few seconds), and " +
          "the frontend will pick it up on its own next check shortly after. See the note below about trusting " +
          "it in your browser."
      );
    } catch (err) {
      setTlsError(err.message);
    } finally {
      setTlsBusy(false);
    }
  }

  async function generateCsr(e) {
    e.preventDefault();
    const sans = csrSans
      .split(/[,\s]+/)
      .map((s) => s.trim())
      .filter(Boolean);
    setTlsBusy(true);
    setTlsError(null);
    setTlsNotice(null);
    try {
      const result = await api.post("/tls/csr", { common_name: csrCommonName.trim(), sans });
      setCsrResult(result);
    } catch (err) {
      setTlsError(err.message);
    } finally {
      setTlsBusy(false);
    }
  }

  async function importTlsCert(e) {
    e.preventDefault();
    setTlsBusy(true);
    setTlsError(null);
    setTlsNotice(null);
    try {
      const result = await api.post("/tls/import-cert", {
        cert_pem: importCertPem,
        chain_pem: importChainPem.trim() ? importChainPem : null,
      });
      setTlsStatus(result.status);
      setImportCertPem("");
      setImportChainPem("");
      setCsrResult(null);
      setTlsNotice("Certificate installed -- the backend is restarting now to apply it (a few seconds).");
    } catch (err) {
      setTlsError(err.message);
    } finally {
      setTlsBusy(false);
    }
  }

  async function clearTlsCert() {
    if (!window.confirm("Remove the active certificate and revert to plain HTTP? Camera scanning and location features will stop working until a new certificate is installed.")) {
      return;
    }
    setTlsBusy(true);
    setTlsError(null);
    setTlsNotice(null);
    try {
      const result = await api.post("/tls/clear", {});
      setTlsStatus(result.status);
      setTlsNotice("Certificate cleared -- reverting to plain HTTP now (a few seconds).");
    } catch (err) {
      setTlsError(err.message);
    } finally {
      setTlsBusy(false);
    }
  }

  // Backlog B9.2 -- one-click backup. The manifest is just a cheap
  // preview of what the archive currently contains (see
  // backup_service.backup_manifest) so the button isn't a total black
  // box; the actual download is a plain <a href> to the backend
  // endpoint (same pattern MealPlanPage.jsx already uses for the .ics
  // feed) rather than routed through api.js's fetch wrapper, since that
  // wrapper always parses the response as JSON.
  const [backupManifest, setBackupManifest] = useState(null);

  async function refreshBackupManifest() {
    try {
      setBackupManifest(await api.get("/system/backup/manifest"));
    } catch (e) {
      setBackupManifest(null);
    }
  }

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
  const otherSettings = settings.filter(
    (s) => s.key !== "ui_theme" && !GOOGLE_CALENDAR_MANAGED_KEYS.includes(s.key) && !ICLOUD_CALENDAR_MANAGED_KEYS.includes(s.key)
  );
  const settingsByTab = useMemo(() => {
    const groups = { ai: [], integrations: [], preferences: [] };
    for (const spec of otherSettings) {
      const tab = settingsTabFor(spec.key);
      if (!groups[tab]) groups[tab] = [];
      groups[tab].push(spec);
    }
    return groups;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [otherSettings]);

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
      selectTab("integrations");
    } else if (result === "error") {
      setGcalBanner({ type: "error", message: params.get("message") || "Google Calendar connection failed." });
      selectTab("integrations");
    }
    if (result) {
      // Strip the query so a page refresh doesn't re-show a stale banner.
      window.history.replaceState(null, "", window.location.pathname + window.location.search + "#/settings");
    }
    refreshGcalStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function connectGoogleCalendar() {
    // Fetches the Google consent-screen URL first (a normal JSON call,
    // so a 400 -- not configured, bad client id -- surfaces via gcalError
    // like every other action on this card) and only THEN navigates the
    // browser there. Fixed 2026-08-01 after an author-reported bug:
    // clicking Connect previously did a raw window.location.href straight
    // at the backend's own redirecting endpoint, so any failure there
    // showed up as a full-page navigation to an unstyled JSON error blob
    // easy to mistake for "nothing happened" -- see routers/
    // google_calendar.py's authorize() docstring for the matching
    // backend-side half of this fix.
    setGcalBusy(true);
    setGcalError(null);
    try {
      const result = await api.get(
        `/calendar/google/authorize?return_to=${encodeURIComponent(window.location.origin)}`
      );
      window.location.href = result.authorize_url; // navigates away -- no need to clear gcalBusy
    } catch (e) {
      setGcalError(e.message);
      setGcalBusy(false);
    }
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

  // Backlog B12.2 -- iCloud Calendar status + connect/disconnect/sync-
  // toggle/resync. Simpler than the Google block above: no OAuth
  // redirect/callback dance, no return-address bookkeeping -- "Connect"
  // just validates the Apple ID/app-specific password already saved via
  // the generic Integrations rows above by running real CalDAV
  // discovery (routers/icloud_calendar.py's /connect).
  const [icloudStatus, setIcloudStatus] = useState(null);
  const [icloudBusy, setIcloudBusy] = useState(false);
  const [icloudError, setIcloudError] = useState(null);

  async function refreshIcloudStatus() {
    try {
      setIcloudStatus(await api.get("/calendar/icloud/status"));
    } catch (e) {
      setIcloudError(e.message);
    }
  }

  useEffect(() => {
    refreshIcloudStatus();
  }, []);

  async function refreshIcloudAfter(promise) {
    const result = await promise;
    setIcloudStatus(result);
    return result;
  }

  async function connectIcloudCalendar() {
    setIcloudBusy(true);
    setIcloudError(null);
    try {
      await refreshIcloudAfter(api.post("/calendar/icloud/connect", {}));
    } catch (e) {
      setIcloudError(e.message);
    } finally {
      setIcloudBusy(false);
    }
  }

  async function disconnectIcloudCalendar() {
    setIcloudBusy(true);
    setIcloudError(null);
    try {
      await refreshIcloudAfter(api.post("/calendar/icloud/disconnect", {}));
    } catch (e) {
      setIcloudError(e.message);
    } finally {
      setIcloudBusy(false);
    }
  }

  async function toggleIcloudSync(enabled) {
    setIcloudBusy(true);
    setIcloudError(null);
    try {
      await refreshIcloudAfter(api.patch("/calendar/icloud/sync-enabled", { enabled }));
    } catch (e) {
      setIcloudError(e.message);
    } finally {
      setIcloudBusy(false);
    }
  }

  async function resyncIcloudCalendar() {
    setIcloudBusy(true);
    setIcloudError(null);
    try {
      await api.post("/calendar/icloud/resync", {});
    } catch (e) {
      setIcloudError(e.message);
    } finally {
      setIcloudBusy(false);
    }
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
        if (s.key in next) continue;
        // Secrets are never sent back in the clear (masked as ********
        // by the backend) -- leave that field blank so a save only
        // happens if the user actually types a new value.
        if (s.is_secret) {
          next[s.key] = "";
        } else if (s.key === "google_calendar_redirect_uri" && !s.value && backendOrigin) {
          // Author-requested (2026-08-01): don't make the household dig
          // through .env for BACKEND_PORT -- backendOrigin already knows
          // it (window.__CHEF_CONFIG__.backendPort, see api.js). Corrected
          // the same day: suggesting the browser's raw LAN IP address
          // directly doesn't work -- Google rejects it (see
          // suggestedRedirectUri's comment above) -- so this now suggests
          // http://localhost:<port> whenever the browser's own address is
          // a bare IP, and only suggests the browser's address directly
          // when it's already a real domain name (which Google accepts
          // as-is). Only pre-fills an EMPTY, never-saved field -- never
          // overwrites a value the household already set.
          next[s.key] = suggestedRedirectUri(backendOrigin);
        } else {
          // Non-secret fields are prefilled with their current value for editing.
          next[s.key] = s.value;
        }
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
    refreshBackupManifest();
    refreshTlsStatus();
    // Backlog B15.1 -- prefill the self-signed hostname/CSR common-name
    // fields with the address this browser is already using to reach
    // Chef (same source as the Google Calendar redirect-URI suggestion
    // above: backendOrigin, derived from window.location). A household
    // usually wants the cert to cover exactly the address they already
    // type into a browser -- only prefills empty fields, never
    // overwrites something the household typed.
    if (backendOrigin) {
      try {
        const host = new URL(backendOrigin).hostname;
        setSelfSignedHosts((prev) => prev || host);
        setCsrCommonName((prev) => prev || host);
      } catch {
        // backendOrigin not a parseable URL (e.g. dev mode's "") -- leave fields blank
      }
    }
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
      // Fixed 2026-08-01 (author-reported bug): the Google Calendar
      // card's `gcalStatus` was only ever fetched once on page load, so
      // saving a valid client id/secret/redirect URI here never updated
      // its `configured` flag -- the Connect button stayed silently
      // disabled with no visible explanation. Refresh it the moment any
      // of the three relevant keys saves successfully. Also refreshes
      // the pinned Connection Status card (B14) since it now surfaces
      // the same "configured" flag.
      if (GOOGLE_CALENDAR_CONFIG_KEYS.includes(spec.key)) {
        refreshGcalStatus();
      }
      if (ICLOUD_CALENDAR_CONFIG_KEYS.includes(spec.key)) {
        refreshIcloudStatus();
      }
      if (GOOGLE_CALENDAR_CONFIG_KEYS.includes(spec.key) || spec.key === "recipe_import_folder_path") {
        refreshStatus();
      }
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

  // Extracted so the AI/Integrations/Preferences tabs can all render the
  // same generic settings-row shape without duplicating this block three
  // times (backlog B14's whole point was less repetition, not more).
  function renderSettingRow(spec) {
    return (
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
        {spec.key === "google_calendar_redirect_uri" && backendOrigin && (
          <div className="settings-redirect-uri-suggestions">
            <button
              type="button"
              className="btn-link"
              onClick={() => setSettingEdits((prev) => ({ ...prev, [spec.key]: suggestedRedirectUri(backendOrigin) }))}
            >
              Use localhost{isRawIpHost(backendOrigin) ? " (recommended -- see below)" : ""}
            </button>
            {" · "}
            <button
              type="button"
              className="btn-link"
              disabled={isRawIpHost(backendOrigin)}
              title={
                isRawIpHost(backendOrigin)
                  ? "This browser is reaching Chef by a raw IP address, which Google's OAuth redirect URI validation rejects. Use localhost instead, or see the WIKI for a domain-based alternative."
                  : undefined
              }
              onClick={() =>
                setSettingEdits((prev) => ({
                  ...prev,
                  [spec.key]: `${backendOrigin}${GOOGLE_CALENDAR_CALLBACK_PATH}`,
                }))
              }
            >
              Use this browser's address ({backendOrigin})
              {isRawIpHost(backendOrigin) ? " -- won't work, it's a raw IP" : ""}
            </button>
          </div>
        )}
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
    );
  }

  return (
    <div>
      {error && <p className="error-text">{error}</p>}

      {/* Backlog B14 (author-requested 2026-08-01): pinned above the
          sub-tab bar, not inside any one tab -- "is anything broken"
          matters regardless of which settings group is open. Also now
          reports per-integration configured/connected state (the
          `integrations` list from GET /api/system/status), not just
          Ollama/Tavily -- another direct author ask, so a household can
          tell at a glance whether Google Calendar (or a future
          integration) has its credentials saved without opening the
          Integrations tab. */}
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
            {(status.integrations || []).map((it) => (
              <Fragment key={it.key}>
                <span
                  className={`status-dot ${
                    it.connected === false ? "status-bad" : it.configured ? "status-ok" : "status-bad"
                  }`}
                />
                <span>
                  {it.label}{" "}
                  {it.connected === true
                    ? `connected${it.detail ? ` (${it.detail})` : ""}`
                    : it.configured
                      ? it.connected === false
                        ? "configured, not yet connected"
                        : `configured${it.detail ? ` (${it.detail})` : ""}`
                      : "not configured"}
                </span>
              </Fragment>
            ))}
          </div>
        ) : (
          <p className="hint">{status?.error || "Checking..."}</p>
        )}
      </div>

      <div className="settings-tabbar" role="tablist">
        {SETTINGS_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={activeTab === t.key}
            className={`settings-tab${activeTab === t.key ? " active" : ""}`}
            onClick={() => selectTab(t.key)}
          >
            {t.label}
          </button>
        ))}
      </div>

      {activeTab === "appearance" && (
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
      )}

      {activeTab === "ai" && (
        <>
          <div className="card">
            <h3>AI &amp; Models</h3>
            <p className="hint">
              Ollama connection and which model handles chat/meal-planning vs. vision (photo/receipt) tasks. Stored
              in the database, not <code>.env</code> -- changes take effect on the next request, no rebuild needed.
            </p>
            {loading ? <p>Loading...</p> : settingsByTab.ai.map(renderSettingRow)}
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
                    <button
                      className="btn btn-primary btn-sm"
                      onClick={() => savePrompt(p.prompt_key)}
                      disabled={promptSaving[p.prompt_key]}
                    >
                      {promptSaving[p.prompt_key] ? "Saving..." : "Save"}
                    </button>
                    {promptSaved[p.prompt_key] && <span className="hint">Saved.</span>}
                  </div>
                </div>
              ))
            )}
          </div>
        </>
      )}

      {activeTab === "integrations" && (
        <>
          <div className="card">
            <h3>Integrations &amp; AI services</h3>
            <p className="hint">
              Optional external services -- web search, food-database lookups, recall checking. Stored in the
              database (encrypted at rest for secrets), not <code>.env</code>.
            </p>
            {loading ? <p>Loading...</p> : settingsByTab.integrations.map(renderSettingRow)}
          </div>

          <div className="card">
            <h3>Google Calendar</h3>
            <p className="hint">
              Backlog B12.1 -- pushes your weekly meal plan into a dedicated "Chef Meal Plan" calendar in your Google
              account, kept in sync automatically as the plan changes. Share that calendar with the rest of the
              household from Google Calendar's own sharing settings. First-time setup needs your own free Google
              Cloud OAuth client -- see the full walkthrough in the <a href="#/wiki?entry=google-calendar-setup">WIKI</a>.
            </p>

            {gcalBanner && <p className={`gcal-banner ${gcalBanner.type}`}>{gcalBanner.message}</p>}
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
                      : "Enter your Google OAuth client ID, secret, and redirect URI above first (see the WIKI guide above), then Connect."}
                  </p>
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={connectGoogleCalendar}
                    disabled={!gcalStatus.configured || gcalBusy}
                  >
                    {gcalBusy ? "Connecting..." : "Connect Google Calendar"}
                  </button>
                </>
              )
            ) : (
              <p className="hint">Loading...</p>
            )}
          </div>

          <div className="card">
            <h3>iCloud Calendar</h3>
            <p className="hint">
              Backlog B12.2 -- the same one-way push sync as Google Calendar above, into a dedicated "Chef Meal
              Plan" calendar in your iCloud account. Needs an app-specific password (NOT your normal Apple ID
              password) generated at{" "}
              <a href="https://appleid.apple.com" target="_blank" rel="noreferrer">
                appleid.apple.com
              </a>{" "}
              (Sign-In and Security -&gt; App-Specific Passwords) -- enter your Apple ID and that password above,
              then Connect.
            </p>

            {icloudError && <p className="error-text">{icloudError}</p>}

            {icloudStatus ? (
              icloudStatus.connected ? (
                <>
                  <p>
                    Connected as <strong>{icloudStatus.username || "(unknown account)"}</strong>.
                  </p>
                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={icloudStatus.sync_enabled}
                      disabled={icloudBusy}
                      onChange={(e) => toggleIcloudSync(e.target.checked)}
                    />
                    Sync meal-plan changes to iCloud Calendar
                  </label>
                  <div className="form-actions">
                    <button className="btn btn-secondary btn-sm" onClick={resyncIcloudCalendar} disabled={icloudBusy}>
                      Force resync
                    </button>
                    <button className="btn btn-secondary btn-sm" onClick={disconnectIcloudCalendar} disabled={icloudBusy}>
                      Disconnect
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <p className="hint">
                    {icloudStatus.configured
                      ? "Apple ID and app-specific password are set -- click Connect to verify them and finish linking."
                      : "Enter your iCloud Apple ID and an app-specific password above first, then Connect."}
                  </p>
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={connectIcloudCalendar}
                    disabled={!icloudStatus.configured || icloudBusy}
                  >
                    {icloudBusy ? "Connecting..." : "Connect iCloud Calendar"}
                  </button>
                </>
              )
            ) : (
              <p className="hint">Loading...</p>
            )}
          </div>
        </>
      )}

      {activeTab === "preferences" && (
        <>
          <div className="card">
            <h3>Preferences</h3>
            <p className="hint">How recipe quantities display by default, and your household's timezone.</p>
            {loading ? <p>Loading...</p> : settingsByTab.preferences.map(renderSettingRow)}
          </div>
          <div className="card">
            <h3>Household size &amp; dietary restrictions</h3>
            <p className="hint">
              Managed on the <a href="#/health">Health page</a> under "Household preferences," alongside member
              profiles and body metrics that also steer meal planning.
            </p>
          </div>
        </>
      )}

      {activeTab === "security" && (
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
                Password protection is currently <strong>{authStatus.enabled ? "enabled" : "disabled"}</strong>.
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
                    <button
                      className="btn btn-secondary btn-sm"
                      type="submit"
                      disabled={authBusy || !disableCurrentPassword}
                    >
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
      )}

      {activeTab === "security" && (
        <div className="card">
          <div className="page-toolbar">
            <h3 style={{ margin: 0 }}>Certificate (HTTPS)</h3>
            <button className="btn btn-secondary btn-sm" onClick={refreshTlsStatus} disabled={tlsBusy}>
              Refresh
            </button>
          </div>
          <p className="hint">
            Backlog B15.1 (author-reported 2026-08-01) -- most browsers block the camera (barcode scanner) and
            device location (Dining Out) unless the page is loaded over HTTPS, or from <code>localhost</code>.
            A self-signed certificate is the quickest fix for a LAN-only setup like this one -- it's not signed
            by a public authority, so browsers show a one-time "not trusted" warning to click through (see the
            note at the bottom of this card), which is expected and safe on your own private network.
          </p>

          {tlsError && <p className="error-text">{tlsError}</p>}
          {tlsNotice && <p className="hint">{tlsNotice}</p>}

          {tlsStatus ? (
            <>
              <div className="settings-status-grid">
                <span className={`status-dot ${tlsStatus.active ? "status-ok" : "status-bad"}`} />
                <span>
                  {tlsStatus.active
                    ? `HTTPS certificate active (${tlsStatus.method === "self_signed" ? "self-signed" : "CA-signed, imported"})`
                    : "No certificate installed -- Chef is serving plain HTTP"}
                </span>
              </div>
              {tlsStatus.active && !tlsStatus.error && (
                <p className="hint">
                  Common name: <strong>{tlsStatus.common_name}</strong>
                  {tlsStatus.sans?.length > 0 && <> · Covers: {tlsStatus.sans.join(", ")}</>}
                  {" · "}
                  {tlsStatus.expired ? (
                    <span className="error-text">expired</span>
                  ) : (
                    `expires ${new Date(tlsStatus.expires_at * 1000).toLocaleDateString()} (${tlsStatus.days_remaining} days)`
                  )}
                </p>
              )}
              {tlsStatus.error && <p className="error-text">{tlsStatus.error}</p>}
              {tlsStatus.restart_required && (
                <p className="hint">Applying the new certificate now -- this page may briefly disconnect and reconnect.</p>
              )}
              <p className="hint">
                Backend: plain HTTP on port {tlsStatus.http_port}, HTTPS on port {tlsStatus.https_port} once a
                certificate is active. The frontend (this page) has its own matching HTTP/HTTPS ports -- see the{" "}
                <a href="#/wiki?entry=https-setup">WIKI's HTTPS entry</a> for the exact addresses.
              </p>
              {tlsStatus.active && !tlsStatus.error && (
                <p className="hint">
                  <strong>iOS/iPadOS:</strong> if Safari won't offer to let you through the "not trusted" warning
                  at all, or you want the warning gone everywhere on the device (including a Chef PWA installed to
                  the home screen, which can't show that warning itself) rather than clicking through it per
                  browser tab, install this certificate as a trusted profile instead: {" "}
                  <a className="btn btn-secondary btn-sm" href={`${backendOrigin}/api/tls/mobileconfig`}>
                    Download certificate for iOS/iPadOS
                  </a>{" "}
                  -- see the <a href="#/wiki?entry=https-setup">WIKI's HTTPS entry</a> for the exact install steps
                  (it ends with a toggle in Settings &gt; General &gt; About &gt; Certificate Trust Settings,
                  which stays empty until a profile like this one has actually been installed).
                </p>
              )}
            </>
          ) : (
            <p className="hint">Loading...</p>
          )}

          <form onSubmit={generateSelfSigned} className="settings-row">
            <label>
              Hostnames / IP addresses to cover
              <input
                type="text"
                value={selfSignedHosts}
                onChange={(e) => setSelfSignedHosts(e.target.value)}
                placeholder="e.g. 10.11.24.21, chef.local, localhost"
              />
            </label>
            <p className="hint">
              Comma or space separated. Include every address you actually type into a browser to reach Chef -- a
              browser rejects a certificate that doesn't list the exact address in its URL bar, even if the
              certificate is otherwise valid and trusted.
            </p>
            <div className="form-actions">
              <button className="btn btn-primary btn-sm" type="submit" disabled={tlsBusy || !selfSignedHosts.trim()}>
                {tlsBusy
                  ? "Generating..."
                  : tlsStatus?.active
                    ? "Replace with new self-signed certificate"
                    : "Generate self-signed certificate"}
              </button>
            </div>
          </form>

          {tlsStatus?.active && (
            <div className="form-actions">
              <button className="btn btn-link-danger" onClick={clearTlsCert} disabled={tlsBusy}>
                Remove certificate (revert to plain HTTP)
              </button>
            </div>
          )}

          <details className="settings-row">
            <summary>Advanced: use a certificate from your own Certificate Authority</summary>
            <p className="hint">
              Generates a private key (stays on this server, never downloaded) and a Certificate Signing Request
              you submit to any CA -- an internal/self-signed CA is fine for a LAN-only deployment. Come back and
              paste the signed certificate below once you have it.
            </p>
            <form onSubmit={generateCsr} className="settings-row">
              <label>
                Common name (primary address)
                <input
                  type="text"
                  value={csrCommonName}
                  onChange={(e) => setCsrCommonName(e.target.value)}
                  placeholder="e.g. 10.11.24.21"
                />
              </label>
              <label>
                Additional names/IPs (optional)
                <input
                  type="text"
                  value={csrSans}
                  onChange={(e) => setCsrSans(e.target.value)}
                  placeholder="comma or space separated"
                />
              </label>
              <div className="form-actions">
                <button className="btn btn-secondary btn-sm" type="submit" disabled={tlsBusy || !csrCommonName.trim()}>
                  {tlsBusy ? "Generating..." : "Generate CSR"}
                </button>
              </div>
            </form>

            {csrResult && (
              <div className="settings-row">
                <label>
                  Certificate Signing Request (submit this to your CA)
                  <textarea rows={8} readOnly value={csrResult.csr_pem} onFocus={(e) => e.target.select()} />
                </label>
                <p className="hint">{csrResult.note}</p>
              </div>
            )}

            <form onSubmit={importTlsCert} className="settings-row">
              <label>
                Signed certificate (PEM)
                <textarea
                  rows={6}
                  value={importCertPem}
                  onChange={(e) => setImportCertPem(e.target.value)}
                  placeholder="-----BEGIN CERTIFICATE-----"
                />
              </label>
              <label>
                Certificate chain (optional, PEM)
                <textarea
                  rows={4}
                  value={importChainPem}
                  onChange={(e) => setImportChainPem(e.target.value)}
                  placeholder="-----BEGIN CERTIFICATE----- (intermediate CA, if your CA provided one)"
                />
              </label>
              <div className="form-actions">
                <button className="btn btn-primary btn-sm" type="submit" disabled={tlsBusy || !importCertPem.trim()}>
                  {tlsBusy ? "Installing..." : "Install certificate"}
                </button>
              </div>
            </form>
          </details>

          <p className="hint">
            <strong>After generating or installing a certificate, visit and accept the browser warning at BOTH
            addresses separately</strong> -- this page and the backend API it calls run as two different
            origins/ports, and a self-signed certificate's "not trusted" warning only shows up on a full page
            load, not on the background API calls this page makes. If you skip the backend address, every page
            will look broken (stuck "Loading...") even though the certificate installed correctly. See the{" "}
            <a href="#/wiki?entry=https-setup">WIKI's HTTPS / secure context entry</a> for the exact addresses to
            visit and a full walkthrough.
          </p>
        </div>
      )}

      {activeTab === "backup" && (
        <div className="card">
          <div className="page-toolbar">
            <h3 style={{ margin: 0 }}>Backup</h3>
            <button className="btn btn-secondary btn-sm" onClick={refreshBackupManifest}>
              Refresh
            </button>
          </div>
          <p className="hint">
            Downloads everything Chef stores as one file: the database, encrypted secrets (and the key that decrypts
            them), and any uploaded recipe images / knowledge files.{" "}
            {backupManifest && backupManifest.included.length > 0
              ? `Currently includes: ${backupManifest.included.join(", ")}.`
              : "Nothing to back up yet."}
          </p>
          <p className="hint">
            <strong>Treat the downloaded file like a password export</strong> -- it contains both your encrypted
            settings (Tavily/USDA/Google OAuth keys, etc.) and the key that decrypts them, so anyone with the file can
            read those secrets. There's no in-app restore button by design; see the WIKI for how to restore a backup
            by replacing the files in Chef's data volume.
          </p>
          <a className="btn btn-primary btn-sm" href={`${backendOrigin}/api/system/backup`}>
            Download backup (.tar.gz)
          </a>
        </div>
      )}
    </div>
  );
}
