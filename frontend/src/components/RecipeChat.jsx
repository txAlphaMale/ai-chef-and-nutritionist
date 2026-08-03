import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import RecipeForm from "./RecipeForm";
import { useBackgroundJob } from "../hooks/useBackgroundJob";

/** Ephemeral, recipe-scoped "Ask the Chef" widget -- for substitution
 * questions etc. while actually cooking. History lives only in this
 * component's state (lost on navigation/refresh) since it's explicitly
 * not meant to be a persisted conversation -- see Phase 7 for that.
 *
 * Added 2026-07-31: the chat can also propose an actual edit (e.g. "make
 * this gluten-free") -- see recipe_service.RECIPE_MODIFY_INSTRUCTIONS.
 * When it does, a review card appears reusing RecipeForm, with an
 * explicit choice between saving as a new variant (the default, safer
 * path -- keeps the original recipe untouched) or overwriting the
 * recipe in place. Nothing is saved until the user submits that form.
 *
 * Backlog B11.1 (2026-08-01): POST /{id}/chat now enqueues a background
 * job (see recipes.py's chat_about_recipe) instead of blocking, so the
 * send is tracked via useBackgroundJob rather than a plain busy flag.
 * storageKey is scoped per recipeId so switching between two recipes'
 * chats (or a stray resume after remounting on the same recipe) can't
 * cross-contaminate. The reply text still only lives in this
 * component's own `history` state -- if the job finishes after a
 * navigation-triggered remount (history reset to []), the assistant's
 * reply is appended to a fresh, empty history rather than lost, since
 * pendingUserHistoryRef.current would be null in that case. */
export default function RecipeChat({ recipeId, servings, onRecipeUpdated }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState([]); // [{role, content}]
  const [input, setInput] = useState("");
  const [error, setError] = useState(null);
  const chatJob = useBackgroundJob(`chef.job.recipe_chat.${recipeId}`);
  const pendingUserHistoryRef = useRef(null);
  const busy = chatJob.busy;

  // The most recent proposed edit, if any -- {recipe, variantLabel}.
  // Deliberately replaced (not accumulated) on every reply: if the next
  // chat turn isn't itself another proposal, the previous one is
  // cleared rather than left dangling, since a fresh question implies
  // the user has moved on from reviewing it. Nothing is lost by this --
  // it was only ever a preview, never saved.
  const [proposal, setProposal] = useState(null);
  const [saveMode, setSaveMode] = useState("variant"); // variant|overwrite
  const [variantLabel, setVariantLabel] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);

  async function send() {
    const message = input.trim();
    if (!message || busy) return;
    setInput("");
    setError(null);
    const nextHistory = [...history, { role: "user", content: message }];
    setHistory(nextHistory);
    pendingUserHistoryRef.current = nextHistory;
    try {
      const enqueued = await api.post(`/recipes/${recipeId}/chat`, {
        message,
        history,
        servings,
      });
      chatJob.poll(enqueued.job_id);
    } catch (e) {
      setError(e.message);
    }
  }

  // Fires once the enqueued chat job reaches a terminal state (see the
  // poll() call in send() above). Reads pendingUserHistoryRef instead of
  // closing over `history` directly since this effect's own closure is
  // fixed at mount/dependency-change time, not at send() time.
  useEffect(() => {
    if (!chatJob.result) return;
    const result = chatJob.result;
    const base = pendingUserHistoryRef.current ?? history;
    setHistory([...base, { role: "assistant", content: result.reply }]);
    pendingUserHistoryRef.current = null;
    if (result.proposed_recipe) {
      setProposal({ recipe: result.proposed_recipe, variantLabel: result.variant_label || "" });
      setVariantLabel(result.variant_label || "");
      setSaveMode("variant");
      setSaveError(null);
    } else {
      setProposal(null);
    }
    chatJob.clear();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatJob.result]);

  useEffect(() => {
    if (chatJob.error) {
      setError(chatJob.error);
      pendingUserHistoryRef.current = null;
      chatJob.clear();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chatJob.error]);

  function handleKeyDown(e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  }

  async function handleSaveProposal(payload, pendingImageFile) {
    setSaving(true);
    setSaveError(null);
    try {
      if (saveMode === "variant") {
        const created = await api.post("/recipes", {
          ...payload,
          source: "chat_modified",
          parent_recipe_id: Number(recipeId),
          variant_label: variantLabel.trim() || "Chat Variant",
        });
        if (pendingImageFile) {
          const formData = new FormData();
          formData.append("file", pendingImageFile);
          await api.post(`/recipes/${created.id}/image`, formData);
        }
        setProposal(null);
        navigate(`/recipes/${created.id}`);
      } else {
        await api.patch(`/recipes/${recipeId}`, payload);
        setProposal(null);
        onRecipeUpdated?.();
      }
    } catch (e) {
      setSaveError(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="card recipe-chat">
      {/* Accessibility fix (2026-08-02, backlog B7.4): this was a bare
          <div onClick>, unreachable by keyboard and invisible to a
          screen reader as an interactive control. A real <button> is
          natively focusable/Enter-Space-activatable and gets a
          role/name for free; aria-expanded announces open/closed state,
          the same information the ▲/▼ glyph conveys visually. */}
      <button
        type="button"
        className="recipe-chat-header"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <h3>💬 Ask the Chef about this recipe</h3>
        <span aria-hidden="true">{open ? "▲" : "▼"}</span>
      </button>
      {open && (
        <>
          <p className="hint">
            Missing an ingredient? Need a substitute? Ask here. You can also ask for an actual edit
            (e.g. "make this gluten-free") -- you'll get a chance to review it before anything is
            saved.
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
              aria-label="Ask the Chef about this recipe"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              disabled={busy}
              className="u-flex-1"
            />
            <button className="btn btn-primary" onClick={send} disabled={busy || !input.trim()}>
              {busy && <span className="busy-spinner" aria-hidden="true" />}
              {chatJob.status === "queued" ? "Queued..." : busy ? "Thinking..." : "Send"}
            </button>
          </div>

          {proposal && (
            <div className="card recipe-chat-proposal">
              <h4>Review proposed change</h4>
              <div className="form-row">
                <label className="checkbox-label inline">
                  <input
                    type="radio"
                    name="save-mode"
                    checked={saveMode === "variant"}
                    onChange={() => setSaveMode("variant")}
                  />
                  Save as a new variant (keeps the original recipe unchanged)
                </label>
                <label className="checkbox-label inline">
                  <input
                    type="radio"
                    name="save-mode"
                    checked={saveMode === "overwrite"}
                    onChange={() => setSaveMode("overwrite")}
                  />
                  Update this recipe (overwrite)
                </label>
              </div>
              {saveMode === "variant" && (
                <label>
                  Variant label
                  <input
                    value={variantLabel}
                    onChange={(e) => setVariantLabel(e.target.value)}
                    placeholder="e.g. Gluten-Free"
                  />
                </label>
              )}
              {saveMode === "overwrite" && (
                <p className="hint">
                  This replaces the current recipe's content -- make sure the change is correct
                  before saving.
                </p>
              )}
              {saveError && <p className="error-text">{saveError}</p>}
              <RecipeForm
                initial={proposal.recipe}
                submitLabel={
                  saving
                    ? "Saving..."
                    : saveMode === "variant"
                      ? "Save as new variant"
                      : "Update this recipe"
                }
                onSubmit={handleSaveProposal}
                onCancel={() => setProposal(null)}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
