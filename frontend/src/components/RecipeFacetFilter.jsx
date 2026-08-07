// The Recipes page's filter panel. All of the interesting decisions live
// in utils/recipeFacets.js; this renders them and takes care of the one
// thing a pure function cannot, which is saying out loud what the filter
// does not mean.
//
// The dietary group is phrased as EXCLUSION throughout -- checkbox
// labels, the group heading, and a standing note whenever one is active.
// A "gluten-free" checkbox would be one word shorter and would be a
// safety claim the app cannot support. See recipeFacets.js.

import { buildFacets, countSelected, emptySelection, toggleFacet } from "../utils/recipeFacets";

function FacetGroup({ heading, note, group, options, selected, onToggle, derived }) {
  if (options.length === 0) return null;
  return (
    <fieldset className="facet-group">
      <legend>{heading}</legend>
      {note && <p className="facet-note">{note}</p>}
      <div className="facet-options">
        {options.map((option) => (
          <label
            key={option.tag}
            className={derived ? "facet-option facet-option-derived" : "facet-option"}
          >
            <input
              type="checkbox"
              checked={selected.includes(option.tag)}
              onChange={() => onToggle(group, option.tag)}
            />
            <span>{option.label}</span>
            <span className="facet-count">{option.count}</span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

export default function RecipeFacetFilter({ recipes, value, onChange, matchCount }) {
  const selection = { ...emptySelection(), ...(value || {}) };
  const facets = buildFacets(recipes);
  const activeCount = countSelected(selection);
  const anyFacetExists =
    facets.mealTypes.length + facets.tags.length + facets.exclude.length + facets.nutrition.length > 0;

  if (!anyFacetExists) return null;

  function handleToggle(group, tag) {
    onChange(toggleFacet(selection, group, tag));
  }

  return (
    <div className="card facet-panel">
      <div className="facet-panel-head">
        <h3>Filter</h3>
        {activeCount > 0 && (
          <>
            <span className="hint">
              {matchCount} of {recipes.length} recipes
            </span>
            <button className="btn btn-secondary" onClick={() => onChange(emptySelection())}>
              Clear {activeCount} filter{activeCount === 1 ? "" : "s"}
            </button>
          </>
        )}
      </div>

      <div className="facet-groups">
        <FacetGroup
          heading="Meal"
          note="Any of these."
          group="mealTypes"
          options={facets.mealTypes}
          selected={selection.mealTypes}
          onToggle={handleToggle}
        />
        <FacetGroup
          heading="Tags"
          note="Any of these."
          group="tags"
          options={facets.tags}
          selected={selection.tags}
          onToggle={handleToggle}
        />
        <FacetGroup
          heading="Hide recipes containing"
          note="Worked out from each recipe's ingredients. Checking one hides recipes Chef found that ingredient in."
          group="exclude"
          options={facets.exclude}
          selected={selection.exclude}
          onToggle={handleToggle}
          derived
        />
        <FacetGroup
          heading="Nutrition"
          note="Worked out from computed nutrition only, never from an AI estimate — so a recipe qualifies once you press “Compute from ingredients” on it."
          group="nutrition"
          options={facets.nutrition}
          selected={selection.nutrition}
          onToggle={handleToggle}
          derived
        />
      </div>

      {selection.exclude.length > 0 && (
        <div className="provenance-note provenance-warn facet-safety-note">
          <strong>Hiding is not the same as “free of”</strong>
          <p>
            Chef hides a recipe when it RECOGNISED one of these in the ingredients. It cannot promise the
            recipes still shown are safe: an ingredient it does not know, a sauce or stock with an undeclared
            component, a substitution made in your own kitchen, or shared equipment will all pass straight
            through. Read the recipe before you cook it.
          </p>
        </div>
      )}
    </div>
  );
}
