# NIAID Guidelines for the Diagnosis and Management of Food Allergy -- Summary Reference

**Source:** National Institute of Allergy and Infectious Diseases (NIAID),
part of NIH. "Guidelines for Clinicians and Patients for Diagnosis and
Management of Food Allergy in the United States,"
https://www.niaid.nih.gov/diseases-conditions/guidelines-clinicians-and-patients-food-allergy.
Underlying guideline document: Boyce JA et al., "Guidelines for the
diagnosis and management of food allergy in the United States: report of
the NIAID-sponsored expert panel," *J Allergy Clin Immunol*, 2010 (PMC:
https://pmc.ncbi.nlm.nih.gov/articles/PMC3354236/); addendum: "2017
Addendum Guidelines for the Prevention of Peanut Allergy in the United
States."

**License/provenance note:** NIAID is a federal agency (NIH); the summary
page content is a work of the United States government (17 U.S.C. SS
105) and not subject to copyright in the United States. The underlying
2010 expert-panel report and 2017 addendum were developed by a NIAID-
convened, multi-organization expert panel and published in a peer-
reviewed journal (JACI) -- this app treats the NIAID summary page as the
public-domain source and does not reproduce the full journal article
text. This condensation is Chef's own.

## Origin and scope

In 2008, NIAID convened a coordinating committee of professional medical
organizations, federal agencies, and patient-advocacy groups to develop
concise, evidence-based clinical guidelines on food allergy diagnosis and
management. The resulting 2010 guidelines provide 43 clinical
recommendations covering epidemiology, natural history, diagnosis, and
management of food allergy, including management of severe reactions and
anaphylaxis, plus identified gaps in the science at the time.

A key definitional point the guidelines establish: **food allergy is an
immune-system (typically IgE-mediated) reaction to a food protein**, which
is a different mechanism from food intolerance (e.g. lactose intolerance)
or celiac disease (a T-cell-mediated autoimmune response to gluten
specifically, not a classic IgE food allergy at all, though it is
frequently discussed alongside allergies because avoidance is the primary
management strategy for both). This app's own allergen model already
distinguishes celiac/gluten handling (dedicated
`gluten_observance_level`, oats-type cross-contact flagging) from the
nine-allergen FALCPA list for this reason.

## Core management principle

As of these guidelines, there is no cure for food allergy: management
consists of **strict avoidance of the allergen** and **prompt treatment of
accidental exposure** (including epinephrine availability and an action
plan for anaphylaxis), not desensitization through casual exposure. This
directly grounds why this app treats a detected allergen match as a hard
stop requiring explicit user acknowledgment (see `allergen_service.py`'s
409-conflict behavior at recipe import, meal-plan generation, and plan-
entry confirm) rather than a soft warning.

## 2017 peanut-allergy-prevention addendum

Following the NIAID-funded LEAP (Learning Early About Peanut) trial, which
found that early introduction of peanut-containing foods to high-risk
infants produced an 81% relative reduction in subsequent peanut allergy
development, NIAID's expert panel issued a 2017 addendum with age- and
risk-stratified guidance on introducing peanut to infants, rather than
the older blanket-avoidance advice. This is included for completeness and
is most relevant to households with infants; it does not change management
guidance for someone who already has a diagnosed food allergy.

## Cross-contact, not just ingredient-list matching

The guidelines' broader risk-management framing -- that avoidance must
account for cross-contact (shared equipment, bulk-bin staples, and
non-certified processing), not just an ingredient appearing by name in a
recipe -- is the same principle this app's own cross-contact flagging
(oats, shared-equipment items, bulk-bin staples) implements for its
gluten-observance-level feature, generalized from a specifically celiac
context to the broader allergy context these guidelines cover.

## Relevance to this app's design

- Grounds the app's **hard-stop-on-match, always-confirm** behavior for
  detected allergens in an actual clinical-guidelines source, rather than
  an arbitrary design choice.
- Supports the **cross-contact-aware, not just label-text-aware**
  approach this app already takes for gluten and extends conceptually to
  any of the nine major allergens.
- Reinforces that this app (and any AI-generated meal plan or recipe) is
  not a substitute for a household's own allergist or physician,
  particularly for a new or unconfirmed allergy, initial diagnosis, or
  anaphylaxis action planning -- this app manages avoidance of *already
  known and declared* restrictions, it does not diagnose them.
