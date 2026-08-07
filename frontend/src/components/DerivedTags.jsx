// What Chef worked out about this recipe from its own ingredients, shown
// with the evidence rather than as bare chips.
//
// The recipes LIST shows these as tooltips, which is the right weight for
// a page you are scanning. The detail page is the page you are on when
// deciding whether to actually cook the thing, so the basis is spelled
// out: "contains dairy" is a label, "contains dairy — parmesan, butter"
// is something the household can check against the ingredient list
// directly below it.
//
// The panel says three things a chip cannot, and each is here because
// leaving it out would let the panel be read as something it is not:
//
//   1. These were worked out, not typed. They sit next to editable tags
//      that look similar and mean something entirely different.
//   2. They are recomputed every time this page loads, so editing an
//      ingredient changes them and nothing here can go stale.
//   3. **Nothing here says what the recipe is free of.** That is the
//      whole design (see smart_tag_service and utils/recipeFacets.js) and
//      it is easiest to misread precisely here, on a page that may also
//      be showing an editable `gluten_free` tag somebody's import
//      asserted years ago.

import { NUTRITION_FACETS, derivedTagLabel } from "../utils/recipeFacets";

const NUTRITION_TAGS = new Set(NUTRITION_FACETS.map((n) => n.tag));

function TagRow({ item }) {
  return (
    <li>
      <span className="tag tag-derived">{derivedTagLabel(item.tag)}</span>
      {item.basis && <span className="derived-basis">{item.basis}</span>}
    </li>
  );
}

export default function DerivedTags({ derivedTags, nutritionProvenance }) {
  const all = derivedTags || [];
  if (all.length === 0) return null;

  const contains = all.filter((d) => !NUTRITION_TAGS.has(d.tag));
  const nutrition = all.filter((d) => NUTRITION_TAGS.has(d.tag));

  return (
    <section className="derived-panel">
      <h3>What Chef worked out</h3>
      <p className="hint">
        From this recipe's own ingredients, recomputed each time this page opens — not stored, and not
        editable. The tags above this are the ones you or an import wrote.
      </p>

      {contains.length > 0 && (
        <>
          <h4 className="derived-subhead">Contains</h4>
          <ul className="derived-list">
            {contains.map((item) => (
              <TagRow key={item.tag} item={item} />
            ))}
          </ul>
        </>
      )}

      {nutrition.length > 0 && (
        <>
          <h4 className="derived-subhead">Nutrition profile</h4>
          <ul className="derived-list">
            {nutrition.map((item) => (
              <TagRow key={item.tag} item={item} />
            ))}
          </ul>
          <p className="hint">
            Thresholds are approximations of published dietary definitions, not medical advice. They are only
            applied to nutrition that was {nutritionProvenance === "partial" ? "partly " : ""}computed from
            resolved ingredients — never to an AI estimate.
          </p>
        </>
      )}

      <p className="derived-caveat">
        <strong>None of this says what the recipe is free of.</strong> Chef lists what it RECOGNISED in the
        ingredients, so an ingredient it does not know, an undeclared component in a sauce or stock, or a
        substitution made in your kitchen produces silence here — not a clean bill of health. An empty list
        below a heading means "nothing matched", never "nothing is there".
      </p>
    </section>
  );
}
