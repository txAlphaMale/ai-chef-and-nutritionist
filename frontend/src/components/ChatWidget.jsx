import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

const SESSION_STORAGE_KEY = "chef.chat.session_id";

function getOrCreateSessionId() {
  let id = localStorage.getItem(SESSION_STORAGE_KEY);
  if (!id) {
    id = crypto.randomUUID();
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
 * session_id kept in localStorage. */
export default function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [sessionId] = useState(getOrCreateSessionId);
  const [messages, setMessages] = useState([]); // ChatMessageRead[]
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
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
    setBusy(true);
    try {
      const result = await api.post("/chat/messages", { session_id: sessionId, message });
      setMessages((prev) => [...prev, result.user_message, result.assistant_message]);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

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
              {busy ? "Thinking..." : "Send"}
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
