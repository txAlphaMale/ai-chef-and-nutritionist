import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { useBackgroundJob } from "../hooks/useBackgroundJob";

const SESSION_STORAGE_KEY = "chef.chat.session_id";

// crypto.randomUUID() is gated behind "secure contexts" (HTTPS or
// localhost) per spec -- it's undefined, not merely missing, when the app
// is reached over plain http:// at a LAN IP (e.g. a machine on the network
// hitting the host by IP through a port-forward). That throws inside the
// useState initializer below with no ErrorBoundary above it, which blanks
// the entire app -- this was diagnosed 2026-08-01 as the actual cause of
// "works on localhost, blank page from another device on the LAN".
// crypto.getRandomValues() has no such restriction, so it's the fallback;
// Math.random() is a last resort for environments without crypto at all.
// None of this needs to be cryptographically strong -- it's only a
// client-side chat session id, not a security boundary.
function generateId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    try {
      return crypto.randomUUID();
    } catch {
      // Fall through to the manual implementations below.
    }
  }
  if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, "0"));
    return (
      `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-` +
      `${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-` +
      `${hex.slice(10, 16).join("")}`
    );
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function getOrCreateSessionId() {
  let id = localStorage.getItem(SESSION_STORAGE_KEY);
  if (!id) {
    id = generateId();
    localStorage.setItem(SESSION_STORAGE_KEY, id);
  }
  return id;
}

/** Human-readable label for an action card, shown above the server-
 * provided `description` so the user can see roughly what kind of
 * change is being proposed at a glance. */
const ACTION_TYPE_LABELS = {
  inventory_deduct: "Inventory",
  inventory_update: "Inventory",
  inventory_add: "Inventory",
  meal_plan_confirm_entry: "Meal plan",
  meal_plan_skip_entry: "Meal plan",
  recipe_update_proposal: "Recipe",
};

/** Confirming an action calls the SAME pre-existing endpoint the rest
 * of the app already uses for that change (see chat_service.py's
 * module docstring and routers/chat.py) -- this widget never invents
 * new execution logic, it just fills in the payload from the action
 * the model proposed. A 404 here (most likely a stale/hallucinated
 * entry_id or an inventory name that doesn't match anything) is
 * surfaced to the user rather than silently swallowed. */
async function executeAction(action) {
  switch (action.type) {
    case "inventory_deduct":
      return api.post("/inventory/deduct", {
        ingredient_name: action.ingredient_name,
        quantity: action.quantity,
      });
    case "inventory_update":
      return api.post("/inventory/update-by-name", {
        ingredient_name: action.ingredient_name,
        quantity: action.quantity,
        unit: action.unit,
        category: action.category,
        is_priority: action.is_priority,
        priority_note: action.priority_note,
      });
    case "inventory_add":
      return api.post("/inventory", {
        name: action.name,
        quantity: action.quantity ?? 1,
        unit: action.unit,
        category: action.category || "other",
        source: "chat",
      });
    case "meal_plan_confirm_entry":
      return api.post(`/meal-plans/${action.meal_plan_id}/entries/${action.entry_id}/confirm`, {});
    case "meal_plan_skip_entry":
      return api.post(`/meal-plans/${action.meal_plan_id}/entries/${action.entry_id}/skip`, {});
    case "recipe_update_proposal":
      // mode "variant" (default): create a new Recipe row linked back to
      // the target via parent_recipe_id -- the original is untouched.
      // mode "overwrite": PATCH the target recipe's content directly.
      // ActionCard's onClick (below) requires an extra native confirm()
      // before this ever runs for "overwrite", since this widget's
      // single Confirm click has no review form the way the
      // recipe-scoped chat's RecipeForm step does.
      if (action.mode === "overwrite") {
        return api.patch(`/recipes/${action.target_recipe_id}`, action.recipe);
      }
      return api.post("/recipes", {
        ...action.recipe,
        parent_recipe_id: action.target_recipe_id,
        variant_label: action.variant_label,
      });
    default:
      throw new Error(`Unknown action type: ${action.type}`);
  }
}

const RECIPE_MODE_LABELS = {
  variant: "Recipe — new variant",
  overwrite: "Recipe — overwrite existing",
};

function handleActionButtonClick(action, actionKey, onConfirm) {
  // recipe_update_proposal's "overwrite" mode replaces a recipe's content
  // in place with no review form first -- everything else in this widget
  // is a single confirm click, but this one specific case gets an extra
  // native confirm() as a deliberate speed bump, since a stray click here
  // (unlike a stray inventory tweak) risks serving a recipe that no
  // longer matches dietary requirements. See chat_service.py's
  // recipe_update_proposal comment for the full rationale.
  if (action.type === "recipe_update_proposal" && action.mode === "overwrite") {
    const ok = window.confirm(
      `This will overwrite the existing recipe's content. This can't be undone. Continue?`
    );
    if (!ok) return;
  }
  onConfirm(actionKey, action);
}

function ActionCard({ action, actionKey, status, result, onConfirm }) {
  const isDone = status === "done";
  const isError = typeof status === "string" && status.startsWith("error:");
  const isRecipeAction = action.type === "recipe_update_proposal";
  const label = isRecipeAction ? RECIPE_MODE_LABELS[action.mode] || "Recipe" : ACTION_TYPE_LABELS[action.type] || "Action";
  return (
    <div className="chat-action-card">
      <div className="chat-action-label">{label}</div>
      <div className="chat-action-description">{action.description}</div>
      {isError && <div className="error-text">{status.slice("error:".length)}</div>}
      {isDone && isRecipeAction && result?.id && (
        <div>
          <Link to={`/recipes/${result.id}`}>
            {action.mode === "overwrite" ? "View updated recipe" : "View saved variant"} &rarr;
          </Link>
        </div>
      )}
      <button
        className="btn btn-sm btn-secondary"
        disabled={isDone || status === "pending"}
        onClick={() => handleActionButtonClick(action, actionKey, onConfirm)}
      >
        {isDone ? "Done" : status === "pending" ? "Applying..." : "Confirm"}
      </button>
    </div>
  );
}

/** Persistent chat widget -- mounted once at the App shell level
 * (outside <Routes>) so it survives route navigation instead of
 * remounting/losing state on every page change. History is also
 * persisted server-side (see chat_service.py / routers/chat.py), so a
 * full page reload restores the conversation too, keyed by a
 * session_id kept in localStorage.
 *
 * Backlog B11.1 (2026-08-01): POST /chat/messages now enqueues a
 * background job (see routers/chat.py's send_message, dedup_key=
 * f"chat:{session_id}") instead of blocking the request for the full
 * Ollama call -- this is the exact scenario the author's bug report
 * called out ("upload a receipt, then ask Chef chat a question, then go
 * mess with recipes" should never silently drop the chat send). Tracked
 * via useBackgroundJob keyed by session_id, so a page reload or even
 * this always-mounted widget's own remount (shouldn't normally happen,
 * but costs nothing to be safe) resumes polling an in-flight send
 * instead of losing track of it. */
export default function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [sessionId] = useState(getOrCreateSessionId);
  const [messages, setMessages] = useState([]); // ChatMessageRead[]
  const [input, setInput] = useState("");
  const [error, setError] = useState(null);
  const sendJob = useBackgroundJob(`chef.job.chat.${sessionId}`);
  const busy = sendJob.busy;
  const [actionStatus, setActionStatus] = useState({}); // actionKey -> "pending"|"done"|"error:<msg>"
  const [actionResults, setActionResults] = useState({}); // actionKey -> executeAction's resolved value
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const scrollRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    api
      .get(`/chat/messages?session_id=${encodeURIComponent(sessionId)}`)
      .then((history) => {
        if (!cancelled) setMessages(history);
      })
      .catch((e) => {
        if (!cancelled) setError(e.message);
      })
      .finally(() => {
        if (!cancelled) setHistoryLoaded(true);
      });
    return () => {
      cancelled = true;
    };
    // Runs once per mount -- and this component mounts exactly once
    // for the life of the app shell, so this is effectively "on app
    // load", not "on chat panel open".
  }, [sessionId]);

  useEffect(() => {
    if (open && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, open]);

  async function send() {
    const message = input.trim();
    if (!message || busy) return;
    setInput("");
    setError(null);
    try {
      const enqueued = await api.post("/chat/messages", { session_id: sessionId, message });
      sendJob.poll(enqueued.job_id);
    } catch (e) {
      setError(e.message);
    }
  }

  // The enqueued send's result carries BOTH the user message (persisted
  // synchronously before the job was even created, see send_message's
  // "why snapshot" comment) and the assistant's reply -- appending both
  // together here, only once the job finishes, keeps the two from ever
  // appearing out of order even if the user fires off another send
  // immediately (which the backend's dedup_key coalesces anyway).
  useEffect(() => {
    if (!sendJob.result) return;
    setMessages((prev) => [...prev, sendJob.result.user_message, sendJob.result.assistant_message]);
    sendJob.clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sendJob.result]);

  useEffect(() => {
    if (sendJob.error) {
      setError(sendJob.error);
      sendJob.clear();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sendJob.error]);

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  async function handleConfirmAction(actionKey, action) {
    setActionStatus((prev) => ({ ...prev, [actionKey]: "pending" }));
    try {
      const result = await executeAction(action);
      setActionResults((prev) => ({ ...prev, [actionKey]: result }));
      setActionStatus((prev) => ({ ...prev, [actionKey]: "done" }));
    } catch (e) {
      setActionStatus((prev) => ({ ...prev, [actionKey]: `error:${e.message}` }));
    }
  }

  const unreadHint = !open && historyLoaded && messages.length > 0;

  return (
    <div className="chat-widget">
      {open && (
        <div className="chat-widget-panel card">
          <div className="chat-widget-panel-header">
            <h3>💬 Chat with the Chef</h3>
            <button className="btn-link" onClick={() => setOpen(false)} aria-label="Minimize chat">
              _
            </button>
          </div>
          <p className="hint">
            Ask about the meal plan, tell it what you cooked or skipped, or update the pantry in
            plain language. Proposed changes show up as cards you confirm before anything happens.
          </p>
          <div className="chat-widget-history" ref={scrollRef}>
            {!historyLoaded && <p className="empty-state">Loading history...</p>}
            {historyLoaded && messages.length === 0 && (
              <p className="empty-state">No messages yet -- say hello!</p>
            )}
            {messages.map((m) => (
              <div key={m.id} className={`chat-msg chat-msg-${m.role}`}>
                <strong>{m.role === "user" ? "You" : "Chef"}:</strong> {m.content}
                {m.actions && m.actions.length > 0 && (
                  <div className="chat-action-list">
                    {m.actions.map((action, idx) => {
                      const actionKey = `${m.id}:${idx}`;
                      return (
                        <ActionCard
                          key={actionKey}
                          action={action}
                          actionKey={actionKey}
                          status={actionStatus[actionKey]}
                          result={actionResults[actionKey]}
                          onConfirm={handleConfirmAction}
                        />
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
          </div>
          {error && <p className="error-text">{error}</p>}
          <div className="form-row">
            <input
              placeholder="We made the lentil soup..."
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={busy}
              style={{ flex: 1 }}
            />
            <button className="btn btn-primary" onClick={send} disabled={busy || !input.trim()}>
              {busy && <span className="busy-spinner" aria-hidden="true" />}
              {sendJob.status === "queued" ? "Queued..." : busy ? "Thinking..." : "Send"}
            </button>
          </div>
        </div>
      )}
      <button className="chat-widget-toggle btn btn-primary" onClick={() => setOpen((v) => !v)}>
        {open ? "Close chat" : unreadHint ? "Chat with the Chef" : "💬 Chat with the Chef"}
      </button>
    </div>
  );
}
