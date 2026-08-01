# Major Food Allergens -- FALCPA and the FASTER Act (FDA Reference)

**Source:** U.S. Food and Drug Administration (FDA), "Food Allergies"
https://www.fda.gov/food/nutrition-food-labeling-and-critical-foods/food-allergies
(content current as of March 11, 2026, retrieved for this bundle August 1,
2026).

**License/provenance note:** FDA is a federal agency; this content is a
work of the United States government (17 U.S.C. SS 105) and is not
subject to copyright in the United States. The material below closely
follows the FDA's own page content, condensed for use as retrieval-
grounding reference material.

## The nine major food allergens

The Food Allergen Labeling and Consumer Protection Act of 2004 (FALCPA)
identified eight foods as major food allergens, which at the time
accounted for roughly 90% of food allergies and serious allergic
reactions in the U.S.: **milk, eggs, fish, Crustacean shellfish, tree
nuts, peanuts, wheat, and soybeans**.

The Food Allergy Safety, Treatment, Education, and Research (FASTER) Act,
signed April 23, 2021, added **sesame** as the ninth major allergen,
effective January 1, 2023. All FDA labeling and manufacturing
requirements applicable to the original eight now apply equally to
sesame. Products already in commerce before that date were not required
to be relabeled, so some older-stock products may lack sesame labeling
even where present.

## Labeling requirements

FALCPA requires a food containing a major allergen to name that allergen's
food source on the label, in one of two ways:

- In parentheses following the ingredient name (e.g. "lecithin (soy),"
  "flour (wheat)," "whey (milk)").
- In a "Contains" statement immediately after the ingredient list (e.g.
  "Contains wheat, milk, and soy").

Tree nut type (almond, pecan, walnut, etc.), fish species (bass, flounder,
cod, etc.), and shellfish species (crab, lobster, shrimp, etc.) must each
be specifically named, not just the category.

**What FALCPA does not cover:** meat, poultry, and egg products (regulated
separately by USDA), alcoholic beverages (regulated by the Alcohol and
Tobacco Tax and Trade Bureau), raw agricultural commodities, highly
refined oils, and most foods sold at retail/food-service that are not
prepackaged with a label (e.g. a made-to-order sandwich). This matters
directly for a household eating out (this app's dining-out feature):
restaurant meals are generally **not** covered by FALCPA labeling
requirements at all.

"May contain" / "produced in a facility that also uses" advisory
statements are voluntary, not legally required, and are not a substitute
for actual allergen cross-contact controls -- their presence or absence
should not be read as a guarantee either way.

## Gluten (a related but legally distinct category)

Gluten is not one of the nine major allergens under FALCPA, but the FDA
separately regulates the "gluten-free" label claim: since August 2013, a
product may only be labeled "gluten-free" if it meets a defined threshold
(FDA final rule), with a 2020 follow-up rule extending compliance
requirements to fermented and hydrolyzed foods specifically, since
standard gluten-detection tests can be unreliable on those. For a celiac
or gluten-sensitive household, "gluten-free" labeling and the nine-
allergen system are two separate regulatory mechanisms -- a correctly
labeled gluten-containing product is not required to say "Contains
wheat" if wheat isn't actually one of its ingredients (e.g. a barley- or
rye-based product), which is a real gap this app's allergen model should
not assume away.

## Relevance to this app's design

- This is the exact nine-value taxonomy this app's own
  `allergen_service.py` and `HouseholdPreferences.restricted_allergens`
  are built around -- this file exists so chat and generation-time
  reasoning cite the same authoritative source the app's own code already
  encodes, not a second, possibly-drifting list.
- The **dining-out caveat** (restaurant meals generally aren't covered by
  FALCPA) is directly relevant to this app's dining-finder feature, which
  already avoids asserting a restaurant is "safe" for exactly this reason.
- The **gluten-vs-nine-allergens distinction** reinforces why this app
  treats gluten/celiac safety as its own dedicated cross-contact-aware
  check (see the companion NIAID file and this app's own
  `gluten_observance_level` setting) rather than folding it into the
  generic nine-allergen list.
