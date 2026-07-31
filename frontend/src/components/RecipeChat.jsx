import { useState } from "react";
import { api } from "../api";

/** Ephemeral, recipe-scoped "Ask the Chef" widget -- for substitution
 * questions etc. while actually cooking. History lives only in this
 * component's state (lost on navigation/refresh) since it's explicitly
 * not meant to be a persisted conversation -- see Phase 7 for that. */
export default function RecipeChat({ recipeId, servings }) {
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState([]); // [{role, content}]
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function send() {
    const message = input.trim();
    if (!message || busy) return;
    setInput("");
    setError(null);
    const nextHistory = [...history, { role: "user", content: message }];
    setHistory(nextHistory);
    setBusy(true);
    try {
      const result = await api.post(`/recipes/${recipeId}/chat`, {
        message,
        history,
        servings,
      });
      setHistory([...nextHistory, { role: "assistant", content: result.reply }]);
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

  return (
    <div className="card recipe-chat">
      <div className="recipe-chat-header" onClick={() => setOpen((v) => !v)}>
        <h3>💬 Ask the Chef about this recipe</h3>
        <span>{open ? "▲" : "▼"}</span>
      </div>
      {open && (
        <>
          <p className="hint">
            Missing an ingredient? Need a substitute? Ask here -- suggestions are for this cooking
            session only and won't change the saved recipe.
          </p>
          {history.length > 0 && (
            <div className="recipe-chat-history">
              {history.map((m, i) => (
                <div key={i} className={`chat-msg chat-msg-${m.role}`}>
                  <strong>{m.role === "user" ? "You" : "Chef"}:</strong> {m.content}
                </div>
              ))}
            </div>
          )}
          {error && <p className="error-text">{error}</p>}
          <div className="form-row">
            <input
              placeholder="I'm out of buttermilk, what can I use?"
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
        </>
      )}
    </div>
  );
}
