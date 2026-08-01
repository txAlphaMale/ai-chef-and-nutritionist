"""Backlog B2.2 -- an adapted Healthy Eating Index (HEI-2020) diet-quality
score for a saved weekly meal plan.

**What is real here, sourced directly from USDA/HHS rather than assumed:**
all 13 HEI-2020 component names, their maximum point values, and their
density-based minimum/maximum scoring standards, taken verbatim from the
official scoring-standards table (fetched live 2026-08-01 from
`fns-prod.azureedge.us/sites/default/files/media/file/
HEITableAge2Plus-508.pdf`, "Healthy Eating Index (HEI)-2020 components
and scoring standards"). Scores between the minimum and maximum standard
are interpolated proportionally, exactly as the real index does.

**What is honestly NOT the certified clinical HEI-2020, stated plainly
rather than glossed over, because presenting an approximation as the real
thing would be worse than not building this at all:**

1. **Two of the four moderation components cannot be scored with this
   app's current data and are excluded, not faked.** Refined Grains needs
   a whole-vs-refined classification per ingredient that nothing in this
   codebase's food-database resolution (B1.1) provides. Added Sugars needs
   added-sugar grams distinguished from naturally-occurring sugar; B1.3
   already investigated this and found neither USDA FoodData Central nor
   Open Food Facts cleanly supports that distinction (see B1.3's notes in
   PROJECT-PLAN.md) -- Chef only ever has *total* sugars. Scoring either
   as zero would unfairly penalize every plan; scoring either as full
   marks would be a lie dressed up as data. Both are reported in
   `unscored_components` instead, and the point total is out of 80, not
   100, until this app gains the data to score them for real.

2. **The 9 adequacy components need USDA "cup-equivalent"/"oz-equivalent"
   food-pattern units**, which come from the USDA Food Patterns
   Equivalents Database (FPED) -- a full per-food crosswalk this app does
   not have and did not attempt to build in this pass (a real one is a
   substantial follow-on project of its own). The substitute used here:
   each quantified ingredient is classified into a food group by
   name/category keyword (`classify_food_group`, deliberately held to the
   same "informational, not safety-critical" standard as
   meal_plan_service.guess_grocery_category -- a wrong classification
   nudges a score, it doesn't put anyone at risk), and its
   cup-/oz-equivalent count is estimated from its already-resolved gram
   weight (food_data_service.compute_ingredient_grams) divided by a single
   published *average* reference gram-weight for that food group's
   equivalent unit (see the `_EQUIVALENT_GRAMS` constants below) rather
   than a true per-food lookup. This is a real, bounded approximation, not
   a guess pulled from nowhere -- but it is an approximation, and every
   place this score is surfaced says so.

3. **The Fatty Acids ratio component is approximated as (total fat minus
   saturated fat) versus saturated fat**, since this app tracks total fat
   and saturated fat but not polyunsaturated/monounsaturated fat
   separately. Ignoring trans fat (typically small and unlabeled in this
   app's sources) is the same simplification most consumer nutrition
   tools make.

The result is best described to a user as "an HEI-2020-inspired diet
quality estimate," never as "your certified Healthy Eating Index score."
`compute_diet_quality_score`'s response always carries a `methodology`
string saying exactly that, so the caveat travels with the number instead
of living only in this docstring.
"""
from __future__ import annotations

import re

from app.models import MealPlan
from app.services import food_data_service

# --- Food-group classification (approximate, informational only) ------
#
# Word-boundary keyword matching, same lightweight discipline as
# meal_plan_service.guess_grocery_category -- this is a scoring nudge, not
# a safety check, so an inexhaustive keyword list with an honest "no
# match" fallback is the right tradeoff, unlike allergen_service's much
# stricter matching where a miss has real consequences.
#
# A single ingredient can match more than one tag (e.g. "black beans"
# matches both GREENS_AND_BEANS and SEAFOOD_AND_PLANT_PROTEIN) -- this
# mirrors HEI-2020's real either/or legume allocation rule in spirit
# (legumes can count toward vegetables OR protein) with a simpler, fully
# documented substitute: legumes count toward BOTH here, at full weight,
# rather than the real index's "whichever helps the score more" logic,
# which would require scoring the whole index twice to evaluate.

FOOD_GROUP_KEYWORDS: dict[str, list[str]] = {
    "fruit_juice": [r"fruit juice", r"orange juice", r"apple juice", r"grape juice", r"juice"],
    "fruit_whole": [
        r"apple", r"banana", r"berry", r"berries", r"orange", r"grape\b", r"melon", r"peach",
        r"pear\b", r"pineapple", r"mango", r"plum", r"cherry", r"cherries", r"kiwi", r"fruit",
        r"raisin", r"\bdate\b", r"dates\b", r"\bfig\b", r"figs\b", r"apricot", r"nectarine",
        r"pomegranate", r"tangerine", r"clementine", r"watermelon", r"cantaloupe", r"papaya",
        r"avocado",
    ],
    "vegetable_dark_green_or_legume": [
        r"spinach", r"kale", r"broccoli", r"collard", r"chard", r"romaine", r"arugula",
        r"lentil", r"\bbean\b", r"beans\b", r"chickpea", r"garbanzo", r"black bean",
        r"kidney bean", r"pinto", r"edamame", r"soybean", r"split pea", r"lima bean",
    ],
    "vegetable_other": [
        r"carrot", r"tomato", r"pepper", r"onion", r"potato", r"vegetable", r"zucchini",
        r"squash", r"cucumber", r"lettuce", r"cabbage", r"mushroom", r"\bcorn\b", r"celery",
        r"eggplant", r"asparagus", r"cauliflower", r"beet", r"radish", r"turnip", r"leek",
        r"scallion", r"green onion", r"artichoke", r"brussels sprout",
    ],
    "grain_whole": [
        r"whole wheat", r"whole grain", r"\boats?\b", r"oatmeal", r"brown rice", r"quinoa",
        r"\bbarley\b", r"bulgur", r"farro", r"whole grain bread", r"wild rice", r"buckwheat",
        r"whole wheat flour", r"whole wheat pasta",
    ],
    "grain_refined": [
        r"white rice", r"white flour", r"\bpasta\b", r"\bbread\b", r"bagel", r"cracker",
        r"white bread", r"tortilla", r"cereal", r"\bnoodle", r"\bmacaroni\b", r"\bflour\b",
        r"\brice\b",
    ],
    "dairy": [
        r"\bmilk\b", r"yogurt", r"yoghurt", r"cheese", r"cottage cheese", r"\bcream\b", r"kefir",
        r"buttermilk", r"ricotta", r"mozzarella", r"cheddar", r"parmesan",
    ],
    "protein_seafood_or_plant": [
        r"\bfish\b", r"salmon", r"tuna", r"shrimp", r"seafood", r"cod\b", r"tilapia", r"trout",
        r"crab", r"scallop", r"\btofu\b", r"tempeh", r"\bnuts?\b", r"almond", r"walnut", r"peanut",
        r"cashew", r"\bseeds?\b", r"pistachio", r"pecan", r"lentil", r"\bbean\b", r"beans\b",
        r"chickpea", r"garbanzo", r"soy\b", r"edamame",
    ],
    "protein_other": [
        r"chicken", r"\bbeef\b", r"\bpork\b", r"turkey", r"\begg\b", r"eggs\b", r"\bmeat\b",
        r"sausage", r"bacon", r"\blamb\b", r"poultry", r"steak", r"ground beef", r"ham\b",
    ],
}

_COMPILED_KEYWORDS: dict[str, list[re.Pattern]] = {
    group: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    for group, patterns in FOOD_GROUP_KEYWORDS.items()
}


def classify_food_group(name: str) -> set[str]:
    """Returns every food-group tag whose keywords match `name` (empty set
    if none match -- an honest "not classifiable," never a guess). A name
    can legitimately land in more than one tag (see the module
    docstring's legume note) -- EXCEPT grain_whole/grain_refined, which
    are made mutually exclusive here: "whole wheat bread" matches both
    the whole-grain keyword ("whole wheat") and the generic refined-grain
    keyword ("bread"), and a whole-grain qualifier in the name should win
    rather than the ingredient silently counting toward both the Whole
    Grains and Refined Grains groups at once. Found by a failing test,
    not designed in up front -- real bug, not a hypothetical."""
    if not name:
        return set()
    matched = set()
    for group, patterns in _COMPILED_KEYWORDS.items():
        if any(p.search(name) for p in patterns):
            matched.add(group)
    if "grain_whole" in matched:
        matched.discard("grain_refined")
    return matched


# --- Approximate cup-/oz-equivalent conversion --------------------------
#
# Real USDA reference gram weights for one equivalent unit, averaged
# across each food group rather than looked up per specific food (the
# honest limitation the module docstring names). Sources: USDA MyPlate's
# published "what counts as a cup/ounce" guidance (e.g. 1 cup fluid milk
# = 244g; 1 oz-equivalent of meat/poultry/fish/grains = 28g is USDA's own
# standing convention; 1 cup of most raw/cooked fruits and vegetables
# generally falls in the 130-180g range, so 150g is used as a single
# representative constant for both groups).
_FRUIT_VEG_CUP_G = 150.0
_GRAIN_OZ_G = 28.0
_DAIRY_CUP_G = 244.0
_PROTEIN_OZ_G = 28.0

# --- HEI-2020 component definitions -------------------------------------
#
# Verbatim from the sourced USDA scoring-standards table (see module
# docstring). `computable=False` marks the two moderation components this
# app's data cannot support (see point 1 above) -- kept in this table
# (rather than omitted entirely) so the response can still name them and
# their real max_points, instead of silently pretending they don't exist.
HEI_COMPONENTS: dict[str, dict] = {
    "total_fruits": {
        "label": "Total Fruits", "max_points": 5, "unit": "cup-eq per 1,000 kcal",
        "good": 0.8, "zero": 0.0, "higher_is_better": True, "computable": True,
    },
    "whole_fruits": {
        "label": "Whole Fruits", "max_points": 5, "unit": "cup-eq per 1,000 kcal",
        "good": 0.4, "zero": 0.0, "higher_is_better": True, "computable": True,
    },
    "total_vegetables": {
        "label": "Total Vegetables", "max_points": 5, "unit": "cup-eq per 1,000 kcal",
        "good": 1.1, "zero": 0.0, "higher_is_better": True, "computable": True,
    },
    "greens_and_beans": {
        "label": "Greens and Beans", "max_points": 5, "unit": "cup-eq per 1,000 kcal",
        "good": 0.2, "zero": 0.0, "higher_is_better": True, "computable": True,
    },
    "whole_grains": {
        "label": "Whole Grains", "max_points": 10, "unit": "oz-eq per 1,000 kcal",
        "good": 1.5, "zero": 0.0, "higher_is_better": True, "computable": True,
    },
    "dairy": {
        "label": "Dairy", "max_points": 10, "unit": "cup-eq per 1,000 kcal",
        "good": 1.3, "zero": 0.0, "higher_is_better": True, "computable": True,
    },
    "total_protein_foods": {
        "label": "Total Protein Foods", "max_points": 5, "unit": "oz-eq per 1,000 kcal",
        "good": 2.5, "zero": 0.0, "higher_is_better": True, "computable": True,
    },
    "seafood_and_plant_proteins": {
        "label": "Seafood and Plant Proteins", "max_points": 5, "unit": "oz-eq per 1,000 kcal",
        "good": 0.8, "zero": 0.0, "higher_is_better": True, "computable": True,
    },
    "fatty_acids": {
        "label": "Fatty Acids (unsaturated : saturated ratio)", "max_points": 10, "unit": "ratio",
        "good": 2.5, "zero": 1.2, "higher_is_better": True, "computable": True,
    },
    "refined_grains": {
        "label": "Refined Grains", "max_points": 10, "unit": "oz-eq per 1,000 kcal",
        "good": 1.8, "zero": 4.3, "higher_is_better": False, "computable": False,
        "why_not_computable": (
            "Requires distinguishing whole- from refined-grain content per ingredient, "
            "which this app's food-database resolution does not classify."
        ),
    },
    "sodium": {
        "label": "Sodium", "max_points": 10, "unit": "g per 1,000 kcal",
        "good": 1.1, "zero": 2.0, "higher_is_better": False, "computable": True,
    },
    "added_sugars": {
        "label": "Added Sugars", "max_points": 10, "unit": "% of energy",
        "good": 6.5, "zero": 26.0, "higher_is_better": False, "computable": False,
        "why_not_computable": (
            "Requires added sugars distinguished from naturally-occurring sugars; this "
            "app only tracks total sugars (see backlog B1.3's notes in PROJECT-PLAN.md)."
        ),
    },
    "saturated_fat": {
        "label": "Saturated Fat", "max_points": 10, "unit": "% of energy",
        "good": 8.0, "zero": 16.0, "higher_is_better": False, "computable": True,
    },
}

MAX_COMPUTABLE_POINTS = sum(c["max_points"] for c in HEI_COMPONENTS.values() if c["computable"])


def _linear_score(value: float, good: float, zero: float, max_points: float, higher_is_better: bool) -> float:
    """Proportional scoring between the "zero" and "good" standards,
    exactly as HEI-2020 specifies ("Intakes between the minimum and
    maximum standards are scored proportionately")."""
    if higher_is_better:
        if value >= good:
            return max_points
        if value <= zero:
            return 0.0
        return max_points * (value - zero) / (good - zero)
    else:
        if value <= good:
            return max_points
        if value >= zero:
            return 0.0
        return max_points * (zero - value) / (zero - good)


def compute_diet_quality_score(meal_plan: MealPlan) -> dict:
    """An HEI-2020-inspired diet-quality estimate over a saved plan's
    non-skipped, recipe-assigned entries -- see the module docstring for
    exactly what is and is not the real certified index. Scored over the
    WHOLE plan (not per day): HEI methodology already calls for averaging
    multiple days rather than judging a single day's intake, and a full
    week of planned meals is this app's natural unit for that.

    Each contributing entry's recipe contributes its ingredients' food
    groups and macro totals exactly once, same "one serving per person
    per meal" simplification meal_plan_service.compute_nutrition_summary
    already uses -- consistent with that function rather than
    independently reinvented.
    """
    calories_total = 0.0
    fat_total = 0.0
    saturated_fat_total = 0.0
    sodium_mg_total = 0.0
    group_grams: dict[str, float] = {}
    contributing_entries = 0
    total_entries = 0

    for entry in meal_plan.entries:
        if entry.is_skipped or entry.recipe is None:
            continue
        total_entries += 1
        recipe = entry.recipe
        nutrition = recipe.nutrition or {}
        if not nutrition or not nutrition.get("calories"):
            continue
        contributing_entries += 1
        calories_total += float(nutrition.get("calories") or 0)
        fat_total += float(nutrition.get("fat_g") or 0)
        saturated_fat_total += float(nutrition.get("saturated_fat_g") or 0)
        sodium_mg_total += float(nutrition.get("sodium_mg") or 0)

        for ingredient in recipe.ingredients:
            if ingredient.quantity is None:
                continue
            grams = food_data_service.compute_ingredient_grams(ingredient)
            if grams is None:
                continue
            groups = classify_food_group(ingredient.resolved_food_name or ingredient.ingredient_name)
            for group in groups:
                group_grams[group] = group_grams.get(group, 0.0) + grams

    if contributing_entries == 0 or calories_total <= 0:
        return {
            "computed": False,
            "reason": "No non-skipped, recipe-assigned entries with nutrition data to score.",
            "contributing_entries": contributing_entries,
            "total_entries": total_entries,
        }

    kcal_factor = calories_total / 1000.0  # every density standard is "per 1,000 kcal"

    fruit_whole_g = group_grams.get("fruit_whole", 0.0)
    fruit_juice_g = group_grams.get("fruit_juice", 0.0)
    veg_other_g = group_grams.get("vegetable_other", 0.0)
    veg_greens_beans_g = group_grams.get("vegetable_dark_green_or_legume", 0.0)
    grain_whole_g = group_grams.get("grain_whole", 0.0)
    dairy_g = group_grams.get("dairy", 0.0)
    protein_seafood_plant_g = group_grams.get("protein_seafood_or_plant", 0.0)
    protein_other_g = group_grams.get("protein_other", 0.0)

    raw_values = {
        "total_fruits": (fruit_whole_g + fruit_juice_g) / _FRUIT_VEG_CUP_G / kcal_factor,
        "whole_fruits": fruit_whole_g / _FRUIT_VEG_CUP_G / kcal_factor,
        "total_vegetables": (veg_other_g + veg_greens_beans_g) / _FRUIT_VEG_CUP_G / kcal_factor,
        "greens_and_beans": veg_greens_beans_g / _FRUIT_VEG_CUP_G / kcal_factor,
        "whole_grains": grain_whole_g / _GRAIN_OZ_G / kcal_factor,
        "dairy": dairy_g / _DAIRY_CUP_G / kcal_factor,
        "total_protein_foods": (protein_seafood_plant_g + protein_other_g) / _PROTEIN_OZ_G / kcal_factor,
        "seafood_and_plant_proteins": protein_seafood_plant_g / _PROTEIN_OZ_G / kcal_factor,
        "sodium": sodium_mg_total / calories_total if calories_total else 0.0,  # g/1000kcal reduces to mg/kcal
        "saturated_fat": (saturated_fat_total * 9.0) / calories_total * 100.0 if calories_total else 0.0,
    }

    # Fatty-acid ratio: unsaturated (fat minus saturated) vs. saturated.
    # No saturated fat at all with some unsaturated fat present is the
    # best possible ratio (score max); no fat data at all is unscorable,
    # not "perfect" -- distinguished via `fatty_acids_scorable` below.
    fatty_acids_scorable = fat_total > 0 or saturated_fat_total > 0
    if saturated_fat_total <= 0:
        fatty_acid_ratio = float("inf") if fat_total > 0 else 0.0
    else:
        fatty_acid_ratio = max(0.0, fat_total - saturated_fat_total) / saturated_fat_total
    raw_values["fatty_acids"] = fatty_acid_ratio

    components_out = []
    total_points = 0.0
    unscored_components = []

    for key, spec in HEI_COMPONENTS.items():
        if not spec["computable"]:
            unscored_components.append({"key": key, "label": spec["label"], "max_points": spec["max_points"],
                                          "reason": spec["why_not_computable"]})
            components_out.append({
                "key": key, "label": spec["label"], "max_points": spec["max_points"],
                "points": None, "value": None, "unit": spec["unit"], "computable": False,
            })
            continue

        value = raw_values[key]
        if key == "fatty_acids" and not fatty_acids_scorable:
            components_out.append({
                "key": key, "label": spec["label"], "max_points": spec["max_points"],
                "points": None, "value": None, "unit": spec["unit"], "computable": False,
            })
            unscored_components.append({
                "key": key, "label": spec["label"], "max_points": spec["max_points"],
                "reason": "No fat data available from this plan's contributing recipes.",
            })
            continue

        display_value = value if value != float("inf") else None
        points = _linear_score(value, spec["good"], spec["zero"], spec["max_points"], spec["higher_is_better"])
        total_points += points
        components_out.append({
            "key": key, "label": spec["label"], "max_points": spec["max_points"],
            "points": round(points, 1), "value": round(display_value, 3) if display_value is not None else None,
            "unit": spec["unit"], "computable": True,
        })

    max_points_scored = sum(c["max_points"] for c in components_out if c["computable"])

    return {
        "computed": True,
        "contributing_entries": contributing_entries,
        "total_entries": total_entries,
        "total_calories": round(calories_total, 1),
        "score": {
            "points": round(total_points, 1),
            "max_points": max_points_scored,
            "percent": round(total_points / max_points_scored * 100.0, 1) if max_points_scored else None,
        },
        "components": components_out,
        "unscored_components": unscored_components,
        "methodology": (
            "An HEI-2020-inspired estimate, not the certified clinical Healthy Eating "
            "Index. Component thresholds and point values are the real USDA/HHS "
            "HEI-2020 standards. Food-group quantities (fruits, vegetables, grains, "
            "dairy, protein) are approximated from ingredient-name keyword "
            "classification and averaged reference gram weights, not a true "
            "per-food USDA Food Patterns Equivalents lookup. Refined Grains and "
            "Added Sugars cannot be scored with this app's current nutrition data "
            "and are excluded from the total rather than guessed -- see "
            "`unscored_components`."
        ),
    }
