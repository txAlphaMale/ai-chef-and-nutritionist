import { useEffect, useState } from "react";
import { api } from "../api";

/**
 * Audit P1-5 -- the saved-alias list, on the Settings page.
 *
 * Aliases are normally written implicitly: the resolver declines to guess
 * between two similar inventory names, the user picks one, and the choice
 * is remembered. That is the intended path, and most households will
 * never open this panel. It exists because an invisible store of
 * remembered decisions is a bad thing to have -- if the app starts
 * resolving a name somewhere unexpected, the reason has to be findable
 * and removable, not buried in a table with no UI.
 *
 * Adding one by hand is supported too, for the case where a household
 * already knows their own vocabulary ("scallions" is what the recipe
 * says, "green onions" is what the store calls them) and would rather
 * teach it once up front than wait to be asked.
 */
export default function IngredientAliasManager() {
  const [aliases, setAliases] = useState([]);
  const [inventory, setInventory] = useState([]);
  const [aliasText, setAliasText] = useState("");
  const [canonical, setCanonical] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function refresh() {
    try {
      const [rows, items] = await Promise.all([api.get("/inventory/aliases"), api.get("/inventory")]);
      setAliases(rows);
      setInventory(items);
    } catch (e) {
      setError(e.message);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function addAlias(e) {
    e.preventDefault();
    if (!aliasText.trim() || !canonical.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.post("/inventory/aliases", {
        alias_text: aliasText.trim(),
        canonical_name: canonical.trim(),
        note: "Added from Settings",
      });
      setAliasText("");
      setCanonical("");
      refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function removeAlias(alias) {
    setBusy(true);
    setError(null);
    try {
      await api.del(`/inventory/aliases/${alias.id}`);
      refresh();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card">
      <h3>Remembered ingredient names</h3>
      <p className="hint">
        When Chef isn't sure which pantry item an ingredient name refers to, it asks instead of
        guessing — deducting from the wrong item would quietly put a wrong number in your inventory.
        Every answer you give is saved here so it only ever asks once. Delete one to be asked again.
      </p>

      {error && <p className="error-text">{error}</p>}

      {aliases.length === 0 ? (
        <p className="empty-state">
          Nothing remembered yet. Answers you give to "which item did you mean?" will show up here.
        </p>
      ) : (
        <ul className="alias-list">
          {aliases.map((a) => (
            <li key={a.id}>
              <span>
                <strong>{a.alias_text}</strong>
                <span className="alias-arrow">→</span>
                {a.canonical_name}
                {a.note && <span className="tag">{a.note}</span>}
              </span>
              <button className="btn-link btn-link-danger" disabled={busy} onClick={() => removeAlias(a)}>
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}

      <form className="alias-add-form" onSubmit={addAlias}>
        <label>
          When a recipe says
          <input
            value={aliasText}
            onChange={(e) => setAliasText(e.target.value)}
            placeholder="scallions"
            disabled={busy}
          />
        </label>
        <label>
          use this pantry item
          {/* A datalist rather than a hard <select>: the target is stored
              as a NAME, not a row id, so that an alias keeps working after
              the item is used up and re-bought. Typing a name that isn't
              currently in stock is therefore valid, not a mistake. */}
          <input
            value={canonical}
            onChange={(e) => setCanonical(e.target.value)}
            list="alias-inventory-names"
            placeholder="green onions"
            disabled={busy}
          />
          <datalist id="alias-inventory-names">
            {inventory.map((item) => (
              <option key={item.id} value={item.name} />
            ))}
          </datalist>
        </label>
        <button className="btn btn-secondary" disabled={busy || !aliasText.trim() || !canonical.trim()}>
          Remember
        </button>
      </form>
    </div>
  );
}
