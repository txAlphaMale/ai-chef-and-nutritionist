/** One source for how NOVA group and Nutri-Score are worded and drawn.
 *
 * Backlog B19.1. Both are captured from Open Food Facts on a barcode
 * scan (see backend/app/services/food_data_service.py). Two rules govern
 * everything in this file, and both exist because these are health
 * claims about somebody's food rather than decoration:
 *
 * 1. **Null is absence, never a good score.** Open Food Facts often has
 *    no NOVA group -- Nutella itself has none, because too many of its
 *    ingredients were unreadable -- and returns the literal strings
 *    "unknown" and "not-applicable" for Nutri-Score on things like
 *    coffee and wine. The backend normalises all of that to null. Null
 *    must render as "not classified", never as a blank chip that reads
 *    like a pass and never as a 1 or an A.
 * 2. **The label says whose judgement it is.** Neither scale is this
 *    app's opinion, and neither is a medical instruction. Every tooltip
 *    below attributes the scale and says what it does and does not
 *    measure -- Nutri-Score in particular grades nutrient composition
 *    per 100g, which is why olive oil scores poorly and diet soda scores
 *    well, and a household reading it as "good food / bad food" would be
 *    misled by the app rather than informed by it.
 */

export const NUTRISCORE_GRADES = ["a", "b", "c", "d", "e"];

export const NOVA_LABELS = {
  1: "NOVA 1 — unprocessed or minimally processed",
  2: "NOVA 2 — processed culinary ingredient",
  3: "NOVA 3 — processed food",
  4: "NOVA 4 — ultra-processed",
};

export const NOVA_SHORT = { 1: "NOVA 1", 2: "NOVA 2", 3: "NOVA 3", 4: "NOVA 4" };

/** What the NOVA classification actually is, in the words a household
 * needs. Kept here rather than in a page so the Inventory table, the
 * scanner preview and the wiki entry cannot drift apart. */
export const NOVA_EXPLANATION =
  "NOVA is a food-classification system from the University of São Paulo that groups foods by how much " +
  "industrial processing they have had, not by their nutrients. Group 1 is whole food, group 4 is " +
  "ultra-processed. It is assigned by Open Food Facts from the product's ingredient list, and it is " +
  "blank whenever that list was too incomplete to classify.";

export const NUTRISCORE_EXPLANATION =
  "Nutri-Score is the front-of-pack grade used across much of Europe, A (best) to E (worst). It scores a " +
  "product's nutrient composition per 100g — energy, sugar, saturated fat and salt against fibre, protein, " +
  "fruit and vegetable content — so it compares like with like within a category and nothing else. It is " +
  "not a verdict on whether a food belongs in your diet: olive oil grades poorly and diet soda grades well.";

/** Group 4 is the one B19.2 will count, and the only one worth colouring.
 * Groups 1-3 get the same neutral treatment: highlighting "NOVA 2" as
 * though it were a warning would make the chip noise. */
export function novaChipClass(group) {
  return group === 4 ? "food-grade-chip food-grade-nova-4" : "food-grade-chip";
}

export function nutriscoreChipClass(grade) {
  const letter = (grade || "").toLowerCase();
  return NUTRISCORE_GRADES.includes(letter)
    ? `food-grade-chip food-grade-nutriscore food-grade-nutriscore-${letter}`
    : "food-grade-chip";
}

export function novaLabel(group) {
  return NOVA_LABELS[group] || null;
}

export function nutriscoreLabel(grade) {
  const letter = (grade || "").toLowerCase();
  return NUTRISCORE_GRADES.includes(letter) ? `Nutri-Score ${letter.toUpperCase()}` : null;
}

/** True when there is anything at all to draw. Call sites use this
 * rather than testing the two fields themselves, so "we know one of the
 * two" is handled the same way everywhere -- a product can carry a
 * Nutri-Score and no NOVA group (Nutella) or a NOVA group and no
 * Nutri-Score (wine), and both are normal. */
export function hasAnyFoodGrade(item) {
  return Boolean(item && (item.nova_group || nutriscoreLabel(item.nutriscore_grade)));
}
