"""Backlog B3.1/B3.2: a structured allergen/restriction taxonomy plus a
deterministic (non-LLM) check for whether a set of ingredient names
contains something a household has said it must avoid.

Why this exists: dietary restrictions were previously only ever free-text
JSON (`HouseholdPreferences.dietary_restrictions`, still around and still
useful for goals/preferences that don't map to a fixed allergen) fed to
the model as prose. Nothing structurally prevented a generated plan or an
imported recipe from actually containing gluten -- the app just trusted
the LLM to have followed the instruction. This module is the "trust but
verify" layer: a plain keyword match against ingredient names, run
independently of whatever the model did or didn't do, at recipe import,
recipe view, meal-plan generation preview, and meal-plan entry confirm.

Taxonomy (`ALLERGEN_CHOICES`): the nine FDA/FALCPA-and-FASTER-Act major
allergens (milk, eggs, fish, crustacean shellfish, tree nuts, peanuts,
wheat, soybeans, sesame) plus "gluten" as its own tenth entry. Gluten
isn't one of the FDA nine (it maps closest to "wheat" there), but it's
the single most relevant restriction for this app's stated primary use
case (celiac-friendly), and gluten-containing grains beyond wheat
(barley, rye, malt) aren't covered by a wheat-only check.

Matching (`find_allergen_matches`): whole-word/phrase, case-insensitive
substring search of each restricted allergen's keyword list against each
ingredient name. Deliberately conservative and explicitly NOT
NLP/synonym-aware -- this is the same category of tradeoff as
food_data_service.py's "take the top search result, no fuzzy ranking"
simplification: a real solution here (ingredient-database-backed
allergen tagging, e.g. via B1.1's USDA/OFF resolution once those APIs
expose allergen data) is future work, not attempted in this pass.
Known, accepted gaps:
- Compound words without a space (e.g. "cheesecake") won't match their
  component keyword ("cheese") since matching is word-boundary-anchored
  -- chosen to avoid worse false positives (e.g. "egg" inside
  "eggplant"), but it does mean a real compound-word ingredient can slip
  through undetected. Ingredient names are short, plain nouns in this
  app (from AI extraction or manual entry), so this tradeoff leans
  toward the common case, not against it.
- No handling of "may contain" / manufacturer cross-contamination
  disclosures -- there's no structured data source for that at the
  ingredient-name level; B3.3 (recall/allergen advisory feeds) is a
  separate, not-yet-built backlog item that's a better fit for that.
- An ingredient explicitly labeled "<allergen>-free" (e.g. "gluten-free
  flour") is deliberately NEVER flagged for that specific allergen (see
  `_is_negated`), even though its name contains the trigger word --
  otherwise this feature would flag the exact ingredients that make a
  recipe actually safe, which would be worse than not checking at all.

Cross-contact (`find_cross_contact_matches`, B3.2): a softer, separate
warning category -- ingredients that aren't inherently gluten-containing
but commonly carry real-world cross-contact risk (oats processed on
shared equipment being the standard example cited by celiac dietitians
and apps like Fig/Spoonful). Only surfaced when the household's
`gluten_observance_level` is `"strict_no_cross_contact"` AND "gluten" is
itself a restricted allergen -- a household that hasn't restricted
gluten at all shouldn't be warned about oats.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models import HouseholdPreferences

# --- Canonical taxonomy --------------------------------------------------

ALLERGEN_CHOICES: list[dict] = [
    {"key": "milk", "label": "Milk / dairy"},
    {"key": "eggs", "label": "Eggs"},
    {"key": "fish", "label": "Fish"},
    {"key": "shellfish", "label": "Crustacean shellfish"},
    {"key": "tree_nuts", "label": "Tree nuts"},
    {"key": "peanuts", "label": "Peanuts"},
    {"key": "wheat", "label": "Wheat"},
    {"key": "soybeans", "label": "Soybeans"},
    {"key": "sesame", "label": "Sesame"},
    {"key": "gluten", "label": "Gluten (wheat, barley, rye, and related grains)"},
]
ALLERGEN_KEYS: frozenset[str] = frozenset(a["key"] for a in ALLERGEN_CHOICES)

OBSERVANCE_LEVELS: list[dict] = [
    {"key": "flexible", "label": "Trying to avoid gluten (occasional exposure is OK)"},
    {"key": "strict_gluten_only", "label": "Must avoid gluten (cross-contact is not a concern)"},
    {"key": "strict_no_cross_contact", "label": "Must avoid gluten AND cross-contact (celiac-strict)"},
]
OBSERVANCE_LEVEL_KEYS: frozenset[str] = frozenset(o["key"] for o in OBSERVANCE_LEVELS)

# --- Keyword lists --------------------------------------------------------
#
# "gluten" is a deliberate superset of "wheat" plus the other
# gluten-containing grains -- kept as two separate lookups (rather than
# gluten always implying wheat) so a household can restrict wheat
# specifically (e.g. a wheat allergy, not celiac) without every barley/
# rye reference also lighting up, and vice versa.
ALLERGEN_KEYWORDS: dict[str, list[str]] = {
    "milk": [
        "milk", "dairy", "cream", "butter", "buttermilk", "cheese", "yogurt", "yoghurt",
        "whey", "casein", "ghee", "half and half", "half-and-half",
    ],
    "eggs": ["egg", "eggs", "egg white", "egg yolk", "albumin", "mayonnaise", "meringue"],
    "fish": [
        "fish", "salmon", "tuna", "cod", "anchovy", "anchovies", "sardine",
        "halibut", "tilapia", "trout", "fish sauce", "worcestershire",
    ],
    "shellfish": [
        "shrimp", "prawn", "prawns", "crab", "lobster", "crawfish", "crayfish",
        "scallop", "clam", "mussel", "oyster", "squid", "calamari",
    ],
    "tree_nuts": [
        "almond", "walnut", "pecan", "cashew", "pistachio", "hazelnut",
        "macadamia", "brazil nut", "chestnut", "pine nut",
    ],
    "peanuts": ["peanut", "peanuts", "groundnut", "peanut butter"],
    "wheat": [
        "wheat", "flour", "semolina", "durum", "spelt", "farro", "bulgur",
        "couscous", "seitan", "wheat starch", "panko", "breadcrumb", "bread crumb",
    ],
    "soybeans": ["soy", "soya", "soybean", "soybeans", "tofu", "edamame", "tempeh", "miso", "soy sauce", "tamari"],
    "sesame": ["sesame", "tahini", "benne"],
    "gluten": [
        "wheat", "flour", "semolina", "durum", "spelt", "farro", "bulgur",
        "couscous", "seitan", "wheat starch", "panko", "breadcrumb", "bread crumb",
        "barley", "rye", "malt", "triticale", "kamut", "brewer's yeast", "brewers yeast",
    ],
}

# B3.2 -- surfaced separately from ALLERGEN_KEYWORDS, see module docstring.
GLUTEN_CROSS_CONTACT_KEYWORDS: list[str] = ["oats", "oat", "oatmeal", "oat flour", "oat milk", "oat bran"]

# One negation-phrase word-set per allergen -- "<word>[-\s]*free" anywhere
# in an ingredient name suppresses a match for that allergen only (see
# module docstring's "-free" note).
_NEGATION_WORDS: dict[str, list[str]] = {
    "milk": ["milk", "dairy"],
    "eggs": ["egg"],
    "fish": ["fish"],
    "shellfish": ["shellfish"],
    "tree_nuts": ["nut", "tree nut"],
    "peanuts": ["peanut"],
    "wheat": ["wheat", "gluten"],
    "soybeans": ["soy"],
    "sesame": ["sesame"],
    "gluten": ["gluten", "wheat"],
}


def _build_pattern(words: list[str]) -> re.Pattern:
    escaped = sorted((re.escape(w) for w in words), key=len, reverse=True)
    return re.compile(r"\b(?:" + "|".join(escaped) + r")\b", re.IGNORECASE)


_ALLERGEN_PATTERNS: dict[str, re.Pattern] = {k: _build_pattern(v) for k, v in ALLERGEN_KEYWORDS.items()}
_CROSS_CONTACT_PATTERN = _build_pattern(GLUTEN_CROSS_CONTACT_KEYWORDS)
_NEGATION_PATTERNS: dict[str, re.Pattern] = {
    # \b before the alternation matters: without it, "nut...free" matches
    # inside "peanut-free" (since "nut" is a bare substring of "peanut"
    # immediately followed by "-free"), which would wrongly suppress a
    # real tree_nuts match on an ingredient that only disclaims peanuts.
    k: re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")[\s-]*free\b", re.IGNORECASE)
    for k, words in _NEGATION_WORDS.items()
}


@dataclass
class AllergenMatch:
    allergen: str
    ingredient_name: str
    matched_keyword: str


@dataclass
class RestrictionCheckResult:
    matches: list[AllergenMatch] = field(default_factory=list)
    cross_contact_matches: list[AllergenMatch] = field(default_factory=list)

    @property
    def has_conflict(self) -> bool:
        """True only for a hard allergen match -- cross-contact warnings
        are informational and never block a confirm on their own (see
        routers/meal_plan.py's confirm_meal_plan_entry)."""
        return bool(self.matches)

    def to_dict(self) -> dict:
        return {
            "matches": [vars(m) for m in self.matches],
            "cross_contact_matches": [vars(m) for m in self.cross_contact_matches],
        }


def _is_negated(allergen: str, ingredient_name: str) -> bool:
    pattern = _NEGATION_PATTERNS.get(allergen)
    return bool(pattern and pattern.search(ingredient_name))


def _find_keyword_hits(pattern: re.Pattern, name: str) -> list[str]:
    """All distinct keyword texts `pattern` matches within `name` -- use
    finditer, not search, so an ingredient naming more than one trigger
    ("barley malt syrup" containing both "barley" and "malt") reports
    every hit rather than only the first. Deduped so a repeated keyword
    within one ingredient name doesn't produce redundant matches."""
    seen: list[str] = []
    for hit in pattern.finditer(name):
        keyword = hit.group(0).lower()
        if keyword not in seen:
            seen.append(keyword)
    return seen


def find_allergen_matches(ingredient_names: list[str], restricted_allergens: list[str]) -> list[AllergenMatch]:
    """Pure function: every (allergen, ingredient, keyword) triple where
    a restricted allergen's keyword appears in the ingredient's name and
    the name isn't itself labeled free of that allergen."""
    matches: list[AllergenMatch] = []
    for allergen in restricted_allergens:
        pattern = _ALLERGEN_PATTERNS.get(allergen)
        if pattern is None:  # unknown/invalid key -- ignore rather than error
            continue
        for name in ingredient_names:
            if not name or _is_negated(allergen, name):
                continue
            for keyword in _find_keyword_hits(pattern, name):
                matches.append(AllergenMatch(allergen=allergen, ingredient_name=name, matched_keyword=keyword))
    return matches


def find_cross_contact_matches(
    ingredient_names: list[str], restricted_allergens: list[str], gluten_observance_level: str | None
) -> list[AllergenMatch]:
    """Pure function: B3.2's softer oats/cross-contact warning, gated on
    both "gluten is actually restricted" and "the household's observance
    level says cross-contact matters" -- see module docstring."""
    if gluten_observance_level != "strict_no_cross_contact" or "gluten" not in restricted_allergens:
        return []
    matches: list[AllergenMatch] = []
    for name in ingredient_names:
        if not name or _is_negated("gluten", name):
            continue
        for keyword in _find_keyword_hits(_CROSS_CONTACT_PATTERN, name):
            matches.append(AllergenMatch(allergen="gluten_cross_contact", ingredient_name=name, matched_keyword=keyword))
    return matches


def check_ingredients(
    ingredient_names: list[str], restricted_allergens: list[str], gluten_observance_level: str | None = None
) -> RestrictionCheckResult:
    """The one function every call site actually calls. Pure -- no DB
    access -- so it's directly unit-testable and reusable against a
    persisted recipe's ingredients, an unsaved import preview's parsed
    ingredients, or a meal-plan generation preview's proposed ingredients."""
    if not restricted_allergens:
        return RestrictionCheckResult()
    return RestrictionCheckResult(
        matches=find_allergen_matches(ingredient_names, restricted_allergens),
        cross_contact_matches=find_cross_contact_matches(ingredient_names, restricted_allergens, gluten_observance_level),
    )


def check_household_restrictions(db: Session, ingredient_names: list[str]) -> RestrictionCheckResult:
    """Convenience wrapper used by every router call site: loads the
    (singleton) household preferences and runs check_ingredients against
    them. Returns an empty (no-conflict) result rather than erroring if
    preferences don't exist yet (e.g. an unseeded DB) -- callers here
    shouldn't each have to think about that edge case the way routers/
    household.py's own endpoints explicitly 404 on it."""
    prefs = db.query(HouseholdPreferences).first()
    if prefs is None:
        return RestrictionCheckResult()
    return check_ingredients(ingredient_names, prefs.restricted_allergens or [], prefs.gluten_observance_level)
