// Facet filtering for the Recipes page. Pure, framework-free and
// independently testable, for the same reason cookingText.js is -- the
// interesting part here is a set of rules about what a filter is allowed
// to imply, and rules like that deserve to be exercised somewhere other
// than a browser.
//
// TWO KINDS OF TAG, and the difference is the whole design:
//
//   EDITABLE  meal type, quick, one_pot, kid_friendly...  A person or the
//             import model put them there. Being wrong is cosmetic.
//   DERIVED   contains_gluten, keto...  smart_tag_service worked them out
//             from the recipe's own ingredients and returns them on every
//             read as `derived_tags`. Never stored, never typed.
//
// **Dietary facets EXCLUDE, they never assert.** The list offers "hide
// recipes that contain gluten", not "show gluten-free recipes". Those
// sound equivalent and are not: the first is a claim about the recipes
// being hidden, which the app can support with evidence, and the second
// is a claim about the recipes that remain, which it cannot. Chef's
// allergen matching flags what it RECOGNISES, so an unfamiliar
// ingredient produces silence, and silence rendered as "gluten-free"
// would be this app's worst possible output. See smart_tag_service's
// module docstring for the measurement that settled this -- a
// graham-cracker crust came back gluten_free.
//
// The nutrition facets are include-style, because they are positive
// claims backed by real figures. They only exist at all when
// nutrition_provenance was computed or partial; a recipe carrying the
// model's estimate never earns one, which is why a keto recipe can be
// missing from the keto facet until someone presses "Compute from
// ingredients" on it. The UI says so rather than leaving it a mystery.

// The `contains_*` tags smart_tag_service can produce, in the order they
// are offered. Labels are the plain noun -- the checkbox already says
// what checking it does, and "Exclude contains_gluten" reads like a
// database column rather than a kitchen.
export const DIETARY_EXCLUSIONS = [
  { tag: "contains_gluten", label: "Gluten" },
  { tag: "contains_dairy", label: "Dairy" },
  { tag: "contains_egg", label: "Egg" },
  { tag: "contains_nuts", label: "Nuts" },
  { tag: "contains_fish", label: "Fish & shellfish" },
  { tag: "contains_soy", label: "Soy" },
  { tag: "contains_meat", label: "Meat" },
  { tag: "contains_animal_products", label: "Animal products" },
];

export const NUTRITION_FACETS = [
  { tag: "keto", label: "Keto" },
  { tag: "low_carb", label: "Low carb" },
  { tag: "low_sodium", label: "Low sodium" },
  { tag: "heart_healthy", label: "Heart healthy" },
];

// Rule 6's meal-type vocabulary. Broken out of the general tag list so it
// reads as its own question ("what meal is this?") rather than as four
// entries in an alphabetical pile.
export const MEAL_TYPES = ["breakfast", "lunch", "dinner", "dessert"];

const DERIVED_LABELS = new Map([
  ...DIETARY_EXCLUSIONS.map((e) => [e.tag, `contains ${e.label.toLowerCase()}`]),
  ...NUTRITION_FACETS.map((n) => [n.tag, n.label.toLowerCase()]),
]);

/** How a derived tag is written on a recipe chip. Falls back to the raw
 * name with underscores opened out, so a tag added to smart_tag_service
 * later still renders as words rather than vanishing. */
export function derivedTagLabel(tag) {
  return DERIVED_LABELS.get(tag) || String(tag).replace(/_/g, " ");
}

/** The derived tag names on one recipe, as a Set.
 *
 * Tolerates a recipe from an older API build that has no `derived_tags`
 * at all -- the field arrived after this app had been running for a
 * while, and a cached response should degrade to "no derived tags"
 * rather than throwing on a page the household uses daily. */
export function derivedTagSet(recipe) {
  return new Set((recipe?.derived_tags || []).map((d) => d.tag));
}

/** The bases (the evidence strings) for one recipe's derived tags,
 * keyed by tag name -- what the chip's tooltip shows. */
export function derivedTagBases(recipe) {
  const bases = new Map();
  for (const d of recipe?.derived_tags || []) bases.set(d.tag, d.basis);
  return bases;
}

function editableTagSet(recipe) {
  return new Set(recipe?.tags || []);
}

export function emptySelection() {
  return { mealTypes: [], tags: [], exclude: [], nutrition: [] };
}

export function countSelected(selection) {
  const s = selection || emptySelection();
  return s.mealTypes.length + s.tags.length + s.exclude.length + s.nutrition.length;
}

function countMatching(recipes, predicate) {
  let n = 0;
  for (const r of recipes) if (predicate(r)) n += 1;
  return n;
}

/** What to offer, and how many recipes each option would touch.
 *
 * Options with a zero count are dropped, deliberately: a household that
 * cooks no fish should not be shown a fish filter that can only ever
 * return nothing, and an empty facet is a question the data cannot
 * answer. The counts are computed against the WHOLE loaded list rather
 * than the currently filtered one, so a checkbox never renumbers or
 * disappears underneath the cursor that is about to click it. */
export function buildFacets(recipes) {
  const list = recipes || [];

  const mealTypes = MEAL_TYPES.map((tag) => ({
    tag,
    label: tag[0].toUpperCase() + tag.slice(1),
    count: countMatching(list, (r) => editableTagSet(r).has(tag)),
  })).filter((f) => f.count > 0);

  const mealTypeSet = new Set(MEAL_TYPES);
  const otherCounts = new Map();
  for (const recipe of list) {
    for (const tag of editableTagSet(recipe)) {
      if (mealTypeSet.has(tag)) continue;
      otherCounts.set(tag, (otherCounts.get(tag) || 0) + 1);
    }
  }
  const tags = [...otherCounts.entries()]
    .map(([tag, count]) => ({ tag, label: tag.replace(/_/g, " "), count }))
    .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag));

  const exclude = DIETARY_EXCLUSIONS.map(({ tag, label }) => ({
    tag,
    label,
    count: countMatching(list, (r) => derivedTagSet(r).has(tag)),
  })).filter((f) => f.count > 0);

  const nutrition = NUTRITION_FACETS.map(({ tag, label }) => ({
    tag,
    label,
    count: countMatching(list, (r) => derivedTagSet(r).has(tag)),
  })).filter((f) => f.count > 0);

  return { mealTypes, tags, exclude, nutrition };
}

/** Apply a selection to a list of recipes.
 *
 * OR within a group, AND across groups -- one rule, applied everywhere,
 * so the result is predictable without reading this file. Ticking two
 * meal types widens; ticking a meal type and a tag narrows. The panel
 * says "any of" on each group rather than leaving the household to infer
 * it from behaviour.
 *
 * `exclude` is the exception and inverts: a recipe is dropped when it
 * carries ANY of the ticked `contains_*` tags. Note what this does NOT
 * do -- it never promotes a recipe for lacking one. A recipe Chef could
 * not read is a recipe that survives every exclusion, which is the safe
 * direction for a filter and the unsafe direction for a promise, and is
 * why the panel refuses to phrase it as one. */
export function applyFacets(recipes, selection) {
  const list = recipes || [];
  const { mealTypes, tags, exclude, nutrition } = { ...emptySelection(), ...(selection || {}) };

  return list.filter((recipe) => {
    const editable = editableTagSet(recipe);
    const derived = derivedTagSet(recipe);

    if (mealTypes.length && !mealTypes.some((t) => editable.has(t))) return false;
    if (tags.length && !tags.some((t) => editable.has(t))) return false;
    if (nutrition.length && !nutrition.some((t) => derived.has(t))) return false;
    if (exclude.length && exclude.some((t) => derived.has(t))) return false;
    return true;
  });
}

/** Toggle one value inside one group, returning a new selection. */
export function toggleFacet(selection, group, tag) {
  const base = { ...emptySelection(), ...(selection || {}) };
  const current = base[group] || [];
  return {
    ...base,
    [group]: current.includes(tag) ? current.filter((t) => t !== tag) : [...current, tag],
  };
}
