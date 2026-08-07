"""Tags the app WORKS OUT, as opposed to tags the model guesses.

The distinction is the point of this module, and it is a safety one.
`gluten_free` is not a label -- for the household this app was built for
it is a claim about whether dinner is safe to eat. A guessed tag is fine
on `dessert` and unacceptable on anything dietary, because the moment the
recipe list can be FILTERED by it a wrong tag stops being cosmetic and
starts hiding the soy sauce.

**Every claim here is POSITIVE, and that is not a naming preference.**
The first version derived `gluten_free` and friends from the ABSENCE of a
match, and the first test run returned `gluten_free` for a graham-cracker
pie crust and `dairy_free` for a caesar salad containing parmesan.
`allergen_service` is built to flag what it RECOGNISES, so its misses are
false negatives; inverting it turns every miss into a false safety claim,
which is the worst output this app could produce. A match is evidence. A
non-match is not.

So the app says "this contains gluten", and the recipe list offers to
EXCLUDE those -- which gives the household the same shortlist without
ever asserting that what remains is safe.

  DERIVED           contains_gluten / dairy / egg / nuts / fish / soy /
                    meat / animal_products, from ingredient names.
  DERIVED, GATED    keto, low_carb, low_sodium, heart_healthy, from the
                    nutrition figures and ONLY when those were computed
                    from resolved ingredients. Never from an AI estimate:
                    the pie's estimate read 380mg of cholesterol against a
                    real ~115, and "heart-healthy" off a number that wrong
                    is worse than no tag.
  SUGGESTED (model) meal type, occasion, technique -- RECIPE_IMPORT_PROMPT
                    rule 6. No deterministic signal exists, the model is
                    the right tool, and being wrong is cosmetic.

**Nothing here is stored.** Derived tags are recomputed on every read, so
they cannot go stale when an ingredient is edited and cannot be typed into
existence by hand. That also means no migration and no per-row provenance
column: a tag is derived if this module derives it, and the question never
has a second answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services import allergen_service

# Animal flesh. Deliberately a plain keyword list, whole-word matched,
# rather than anything cleverer -- it fails predictably and is editable by
# someone who is not a programmer.
#
# The awkward entries are why a list exists at all: Worcestershire is
# anchovy, and a "chicken stock" is not vegetarian however green the
# carton looks.
_MEAT_WORDS = [
    "anchovy",
    "anchovies",
    "bacon",
    "beef",
    "brisket",
    "chicken",
    "chorizo",
    "clam",
    "cod",
    "crab",
    "duck",
    "fish",
    "ham",
    "lamb",
    "lobster",
    "meat",
    "mussel",
    "mutton",
    "octopus",
    "oyster",
    "pancetta",
    "pepperoni",
    "pork",
    "prawn",
    "prosciutto",
    "rabbit",
    "salami",
    "salmon",
    "sardine",
    "sausage",
    "scallop",
    "shrimp",
    "squid",
    "steak",
    "tuna",
    "turkey",
    "veal",
    "venison",
    "worcestershire",
    "bone broth",
    "chicken broth",
    "beef broth",
    "chicken stock",
    "beef stock",
    "fish sauce",
    "oyster sauce",
    "shrimp paste",
]

# Animal, but not flesh. Gelatin belongs here and NOT above: it is animal
# collagen, so a vegan needs to know, but calling a panna cotta "contains
# meat" is simply wrong and the household would stop trusting the tag.
# Honey divides vegans and is included, deliberately.
_ANIMAL_PRODUCT_WORDS = [
    "butter",
    "buttermilk",
    "casein",
    "cheese",
    "cream",
    "creme fraiche",
    "curd",
    "custard",
    "egg",
    "eggs",
    "gelatin",
    "gelatine",
    "ghee",
    "half-and-half",
    "honey",
    "kefir",
    "lard",
    "mascarpone",
    "milk",
    "parmesan",
    "pecorino",
    "ricotta",
    "tallow",
    "whey",
    "yoghurt",
    "yogurt",
]

_DAIRY_WORDS = [
    "butter",
    "buttermilk",
    "casein",
    "cheese",
    "cream",
    "creme fraiche",
    "curd",
    "custard",
    "ghee",
    "half-and-half",
    "kefir",
    "mascarpone",
    "milk",
    "parmesan",
    "pecorino",
    "ricotta",
    "whey",
    "yoghurt",
    "yogurt",
]

# `almond milk` is not milk and `peanut butter` is not butter. Checked
# before the animal words, because a plant qualifier in front of a dairy
# word is how every plant-based product on earth is named.
_PLANT_QUALIFIERS = [
    "almond",
    "cashew",
    "coconut",
    "flax",
    "hazelnut",
    "hemp",
    "macadamia",
    "nut",
    "oat",
    "pea",
    "peanut",
    "plant based",
    "plant-based",
    "rice",
    "soy",
    "soya",
    "sunflower",
    "vegan",
]


def _pattern(words: list[str]) -> re.Pattern:
    longest_first = sorted(words, key=len, reverse=True)
    return re.compile(r"(?<![a-z])(" + "|".join(re.escape(w) for w in longest_first) + r")(?![a-z])", re.IGNORECASE)


_MEAT_RE = _pattern(_MEAT_WORDS)
_ANIMAL_RE = _pattern(_ANIMAL_PRODUCT_WORDS)
_DAIRY_RE = _pattern(_DAIRY_WORDS)
_PLANT_RE = _pattern(_PLANT_QUALIFIERS)


@dataclass
class DerivedTag:
    tag: str
    # Why, in words the household can check against the page. A tag that
    # cannot explain itself is a tag nobody should filter a diet by.
    basis: str


def _is_plant_version(name: str, pattern: re.Pattern) -> bool:
    """True when a plant qualifier sits BEFORE the matched word, so
    `almond milk` is excluded and `milk chocolate almonds` is not."""
    match = pattern.search(name or "")
    return bool(match and _PLANT_RE.search(name[: match.start()]))


def _first_match(names: list[str], pattern: re.Pattern) -> str | None:
    for name in names:
        if pattern.search(name or "") and not _is_plant_version(name, pattern):
            return name
    return None


_CONTAINS_TAGS: list[tuple[str, list[str]]] = [
    ("contains_gluten", ["gluten", "wheat"]),
    ("contains_dairy", ["milk"]),
    ("contains_egg", ["eggs"]),
    ("contains_nuts", ["tree_nuts", "peanuts"]),
    ("contains_fish", ["fish", "crustacean_shellfish"]),
    ("contains_soy", ["soybeans"]),
]

# Per serving. The thresholds are the commonly used dietary ones, stated
# here rather than buried: keto is the widely used 20g net-carb ceiling
# approximated on TOTAL carbs (this app does not compute net carbs, and
# says so), low-sodium follows the FDA's 140mg labelling rule, and
# heart-healthy follows the FDA "healthy" claim limits for saturated fat
# and cholesterol. Approximations of published definitions, not medical
# advice -- and every tag carries the figures it came from.
_KETO_MAX_CARBS_G = 20.0
_LOW_CARB_MAX_CARBS_G = 40.0
_LOW_SODIUM_MAX_MG = 140.0
_HEART_MAX_SAT_FAT_G = 5.0
_HEART_MAX_CHOLESTEROL_MG = 60.0

TRUSTED_NUTRITION_PROVENANCE = ("computed", "partial")


def _number(nutrition: dict, key: str) -> float | None:
    try:
        value = nutrition.get(key)
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _summarise(names: set[str]) -> str:
    ordered = sorted(names)
    head = ", ".join(ordered[:3])
    return f"{head} and {len(ordered) - 3} more" if len(ordered) > 3 else head


def derive_tags(
    ingredient_names: list[str], nutrition: dict | None, nutrition_provenance: str | None
) -> list[DerivedTag]:
    """Every tag this recipe EARNS from its own data.

    `nutrition_provenance` gates the nutrition half completely: anything
    outside TRUSTED_NUTRITION_PROVENANCE means the figures are a model's
    estimate, and no threshold is applied to a guess."""
    names = [n for n in (ingredient_names or []) if n]
    derived: list[DerivedTag] = []
    if not names:
        return derived

    for tag, allergens in _CONTAINS_TAGS:
        hits = {m.ingredient_name for m in allergen_service.find_allergen_matches(names, allergens)}
        if tag == "contains_dairy":
            # allergen_service does not know `parmesan` is milk, and DOES
            # think `almond milk` is -- both measured. Its keyword list is
            # tuned for WARNINGS, where a false positive is the safe
            # direction. Correcting it here rather than there leaves the
            # restriction warnings, a different and more conservative job,
            # exactly as they were.
            hits = {name for name in hits if not _is_plant_version(name, _DAIRY_RE)}
            dairy = _first_match(names, _DAIRY_RE)
            if dairy:
                hits.add(dairy)
        if hits:
            derived.append(DerivedTag(tag, _summarise(hits)))

    meat = _first_match(names, _MEAT_RE)
    if meat:
        derived.append(DerivedTag("contains_meat", meat))
    animal = meat or _first_match(names, _ANIMAL_RE)
    if animal:
        derived.append(DerivedTag("contains_animal_products", animal))

    if nutrition_provenance not in TRUSTED_NUTRITION_PROVENANCE:
        return derived

    figures = nutrition or {}
    carbs = _number(figures, "carbs_g")
    sodium = _number(figures, "sodium_mg")
    sat_fat = _number(figures, "saturated_fat_g")
    cholesterol = _number(figures, "cholesterol_mg")
    source = f"per serving, {nutrition_provenance}"

    if carbs is not None and carbs <= _KETO_MAX_CARBS_G:
        derived.append(DerivedTag("keto", f"{carbs:g}g carbs {source}"))
    if carbs is not None and carbs <= _LOW_CARB_MAX_CARBS_G:
        derived.append(DerivedTag("low_carb", f"{carbs:g}g carbs {source}"))
    if sodium is not None and sodium <= _LOW_SODIUM_MAX_MG:
        derived.append(DerivedTag("low_sodium", f"{sodium:g}mg sodium {source}"))
    if (
        sat_fat is not None
        and cholesterol is not None
        and sat_fat <= _HEART_MAX_SAT_FAT_G
        and cholesterol <= _HEART_MAX_CHOLESTEROL_MG
    ):
        derived.append(
            DerivedTag("heart_healthy", f"{sat_fat:g}g saturated fat, {cholesterol:g}mg cholesterol {source}")
        )
    return derived
