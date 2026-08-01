# Dietary Guidelines for Americans, 2025-2030 -- Summary Reference

**Source:** U.S. Department of Agriculture (USDA) and U.S. Department of
Health and Human Services (HHS), *Dietary Guidelines for Americans,
2025-2030*, released January 7, 2026. Official site:
https://www.dietaryguidelines.gov/

**License/provenance note:** This document is a work of the United States
federal government (17 U.S.C. SS 105) and is not subject to copyright
protection in the United States. The summary below is Chef's own
condensation of the guidelines' publicly reported content, written for use
as retrieval-grounding reference material, not a verbatim reproduction of
the full federal document. Consult dietaryguidelines.gov directly for the
complete, authoritative text.

## Core framing

The 2025-2030 edition centers on "real food" -- food that is whole,
nutrient-dense, and naturally occurring rather than heavily processed --
and directs households to prioritize high-quality protein, healthy fats,
fruits, vegetables, and whole grains as the foundation of a healthy eating
pattern across the lifespan.

## Key numeric targets

- **Protein:** 1.2-1.6 grams per kilogram of body weight per day, adjusted
  for individual caloric needs and activity level -- a notable increase
  from the older 0.8 g/kg Recommended Dietary Allowance (RDA), which was
  set as a minimum to prevent deficiency rather than an optimal intake
  target. Protein should come from a mix of animal and plant sources
  (lean meats, poultry, fish, eggs, dairy, legumes, nuts, soy).
- **Saturated fat:** Kept at no more than 10% of total daily calories,
  unchanged from prior editions -- directly relevant to LDL cholesterol
  management.
- **Sodium:** 2,300 mg/day for most adults, unchanged from prior editions,
  with allowance for somewhat higher intake in highly active individuals
  who lose more sodium through sweat.
- **Dairy:** A shift toward full-fat dairy products with no added sugar,
  a change from earlier editions' low-fat/fat-free dairy emphasis.
- **Processed foods:** Explicit guidance to limit highly processed foods
  and refined carbohydrates in favor of whole-food forms of the same
  food groups.

## Special populations

The guidelines include additional considerations for infants, children,
pregnant/lactating individuals, and older adults, recognizing that
nutrient needs and risk profiles shift across the lifespan. This bundled
summary does not attempt to reproduce those population-specific tables;
consult the primary source for guidance specific to a household member
outside typical working-age adult ranges.

## Relevance to this app's design

- **"Balanced but allow an occasional indulgence"** (this app's own
  meal-planning philosophy) lines up with the guidelines' framing of an
  overall healthy *pattern* rather than a zero-tolerance rule for any
  single food -- occasional treats fit within a pattern that is
  predominantly whole-food and nutrient-dense.
- **Protein guidance (1.2-1.6 g/kg)** is the figure this app's own
  `dri_service.py` uses when computing a household member's daily protein
  target from their logged body weight -- this file exists to ground chat
  and generation-time reasoning in the same source, not to duplicate a
  second, possibly-drifting number.
- **Saturated fat and sodium ceilings** are directly actionable for a
  household focused on reducing LDL cholesterol: recipes and a week's plan
  can be evaluated against these percentages, not just against total
  calories.
- **Whole-food emphasis** supports this app's general recipe-generation
  bias toward minimally processed ingredients already resolvable against
  the USDA FoodData Central database (this app's `food_data_service.py`),
  as opposed to branded/ultra-processed products that are harder to
  verify nutritionally.

## What was deliberately not bundled

The Dietary Reference Intakes (DRI) numeric tables referenced elsewhere in
this app's backlog (vitamin/mineral RDAs, Estimated Average Requirements,
Tolerable Upper Intake Levels) are produced by the National Academies of
Sciences, Engineering, and Medicine (NASEM), a private nonprofit, not a
federal agency -- NASEM's Dietary Reference Intake reports are freely
readable but are **not** United States government works and are not
automatically public domain. This app does not reproduce those tables
here. Where this app needs specific DRI-derived numbers (e.g. per-member
daily targets), they are implemented as cited, sourced logic in
`app/services/dri_service.py` rather than redistributed as a knowledge
document -- verify the current guidance directly at
https://www.nationalacademies.org/ or https://ods.od.nih.gov/ (NIH Office
of Dietary Supplements, a federal source) before relying on any specific
numeric value.
