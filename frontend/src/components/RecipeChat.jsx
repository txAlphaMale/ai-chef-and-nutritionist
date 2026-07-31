import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import RecipeForm from "./RecipeForm";

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
 * recipe in place. Nothing is saved until the user submits that form. */
export default function RecipeChat({ recipeId, servings, onRecipeUpdated }) {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [history, setHistory] = useState([]); // [{role, content}]
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

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
    setBusy(true);
    try {
      const result = await api.post(`/recipes/${recipeId}/chat`, {
        message,
        history,
        servings,
      });
      setHistory([...nextHistory, { role: "assistant", content: result.reply }]);
      if (result.proposed_recipe) {
        setProposal({ recipe: result.proposed_recipe, variantLabel: result.variant_label || "" });
        setVariantLabel(result.variant_label || "");
        setSaveMode("variant");
        setSaveError(null);
      } else {
        setProposal(null);
      }
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
      <div className="recipe-chat-header" onClick={() => setOpen((v) => !v)}>
        <h3>💬 Ask the Chef about this recipe</h3>
        <span>{open ? "▲" : "▼"}</span>
      </div>
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
