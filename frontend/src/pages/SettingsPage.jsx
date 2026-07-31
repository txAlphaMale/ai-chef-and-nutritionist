import { useEffect, useState } from "react";
import { api } from "../api";

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
        <h3>AI services &amp; secrets</h3>
        <p className="hint">
          Stored in the database (encrypted at rest for secrets), not in <code>.env</code> -- changes take effect on
          the next request, no rebuild needed.
        </p>
        {loading ? (
          <p>Loading...</p>
        ) : (
          settings.map((spec) => (
            <div className="settings-row" key={spec.key}>
              <label>
                {spec.label}
                <input
                  type={spec.is_secret ? "password" : "text"}
                  placeholder={spec.is_secret ? (spec.is_set ? "•••••••• (set -- enter a new value to change)" : "not set") : ""}
                  value={settingEdits[spec.key] ?? ""}
                  onChange={(e) => setSettingEdits((prev) => ({ ...prev, [spec.key]: e.target.value }))}
                />
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
