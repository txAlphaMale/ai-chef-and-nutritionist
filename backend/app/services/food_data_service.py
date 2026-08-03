"""Ingredient-to-food-database resolution (backlog B1.1), plus summing
resolved ingredients into Recipe.nutrition with a provenance tag (B1.2).

Maps a RecipeIngredient's free-text name to a canonical food record and a
per-100g nutrient profile, so downstream features (B1.2 computed nutrition,
B1.4 daily/weekly roll-ups, B2.2 diet-quality scoring, B4.1 barcode intake,
B10.5 weight-based unit conversion) rest on real food-composition data
instead of an LLM's unchecked estimate.

Two free, self-host-friendly sources, tried in order:

1. USDA FoodData Central (`api.nal.usda.gov/fdc/v1`) -- government-backed,
   ~300K+ foods, full nutrient profiles, requires a free API key
   (self-registered at https://api.data.gov/signup/, entered via the
   Settings GUI as `usda_fdc_api_key`, same DB-backed/encrypted pattern as
   the Tavily key). No barcode/weak branded coverage.
2. Open Food Facts (`world.openfoodfacts.org`) -- free, open, crowd-sourced,
   ~2.5M+ packaged products, no key required. Better for branded/packaged
   goods USDA doesn't carry, and is also the barcode-native source B4.1
   will reuse directly.

Deliberately rejected as a dependency: Nutritionix/Edamam/Spoonacular/
FatSecret are all paid past a low free tier, which would put a per-user
API bill in front of anyone who clones this repo (see PROJECT-PLAN.md's
capstone research section) -- fine as an optional user-configured
integration later, wrong as something this layer requires.

Resolution is cached on the RecipeIngredient row itself (fdc_id/
off_barcode/resolved_food_name/resolution_source/nutrition_per_100g/
resolved_at) so a given ingredient is only looked up once, not on every
request -- see `resolve_and_cache_ingredient`. Nothing here is invoked
automatically on recipe create/update; `POST /api/recipes/{id}/
resolve-nutrition` resolves (explicit, on demand) and `POST /api/recipes/
{id}/compute-nutrition` (B1.2, `routers/recipes.py`) resolves-if-needed
AND persists the summed result via `compute_recipe_nutrition` below.

`compute_recipe_nutrition` only sums an ingredient whose quantity
converts to grams -- today that means a mass-family unit (g/kg/oz/lb)
directly, OR a volume-family unit with a cached `density_g_per_ml`
(backlog B10.5 -- see `_parse_usda_density` below).
Density is only ever sourced from USDA's `foodPortions` data (a food
detail response's list of real-world measures with a gram weight per
measure); Open Food Facts products don't get a density in this pass, and
plenty of USDA foods never report a volume-unit portion either -- a
resolved ingredient can have real nutrition data and still have no
density. A recipe whose quantified ingredients are only volume/count-
based AND have no density will still come back "ai_estimated" -- a
known, honestly-reported limitation, not a bug, consistent with this
layer's "never invent a conversion" rule.

No live network access to api.nal.usda.gov or world.openfoodfacts.org
exists in Claude's sandbox (same constraint noted for Ollama in every
prior phase), so verification here mocks `httpx.Client.get` rather than
hitting the real APIs -- the parsing functions (`_parse_usda_nutrients`,
`_parse_off_nutrients`) are pure and unit-tested directly against
representative fixture payloads shaped like the real APIs' documented
response schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.models import Recipe, RecipeIngredient
from app.services import settings_service, unit_conversion_service

USDA_BASE_URL = "https://api.nal.usda.gov/fdc/v1"
OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
OFF_PRODUCT_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"

REQUEST_TIMEOUT_SECONDS = 8.0

# Backlog B1.3 -- the single, shared per-serving nutrition key set every
# AI surface in this app now asks for (recipe_service.RECIPE_IMPORT_PROMPT/
# RECIPE_MODIFY_INSTRUCTIONS, meal_plan_service.MEAL_PLAN_PROMPT_TEMPLATE),
# so a recipe's nutrition dict no longer depends on which surface created
# it. Previously recipe_service.py asked for 7 keys and meal_plan_service.py
# asked for only 4 -- a recipe born from meal-plan generation silently
# carried less data than an imported one. This list is also exactly what
# `_parse_usda_nutrients`/`_parse_off_nutrients` below produce per 100g, so
# `compute_recipe_nutrition`'s summed totals land in the same key space as
# an AI estimate without any translation step.
#
# `added_sugars_g` and `soluble_fiber_g` were considered (per the B1.3
# backlog text) but deliberately left out: neither USDA FoodData Central's
# Foundation/SR Legacy nutrient set nor Open Food Facts' core `nutriments`
# fields cleanly distinguish "added" sugars from total sugars, or soluble
# from total fiber. Labeling total sugars as "added" or total fiber as
# "soluble" would be worse than omitting the field -- this app doesn't
# invent a number a real food-composition source doesn't actually give it.
NUTRITION_KEYS: list[str] = [
    "calories",
    "protein_g",
    "carbs_g",
    "fat_g",
    "fiber_g",
    "sodium_mg",
    "cholesterol_mg",
    "saturated_fat_g",
    "sugars_g",
]

# Human-readable hint injected into every recipe/meal-plan generation
# prompt so all three AI surfaces request exactly NUTRITION_KEYS, not
# their own independently-drifted list.
NUTRITION_PROMPT_HINT = (
    '"calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "sodium_mg", '
    '"cholesterol_mg", "saturated_fat_g", "sugars_g"'
)

# USDA FoodData Central nutrient names (as returned in each food's
# `foodNutrients[].nutrientName`) mapped to this app's per-100g nutrient
# dict keys -- exactly NUTRITION_KEYS above.
USDA_NUTRIENT_MAP: dict[str, str] = {
    "Energy": "calories",
    "Protein": "protein_g",
    "Total lipid (fat)": "fat_g",
    "Carbohydrate, by difference": "carbs_g",
    "Fiber, total dietary": "fiber_g",
    "Sodium, Na": "sodium_mg",
    "Cholesterol": "cholesterol_mg",
    "Fatty acids, total saturated": "saturated_fat_g",
    "Sugars, total including NLEA": "sugars_g",
}

# Open Food Facts' `nutriments` dict uses its own flat key convention
# (per-100g values already suffixed `_100g`) rather than USDA's
# name/value/unit triples.
OFF_NUTRIENT_MAP: dict[str, str] = {
    "energy-kcal_100g": "calories",
    "proteins_100g": "protein_g",
    "fat_100g": "fat_g",
    "carbohydrates_100g": "carbs_g",
    "fiber_100g": "fiber_g",
    "sodium_100g": "sodium_mg",  # OFF reports sodium in grams, converted below
    "cholesterol_100g": "cholesterol_mg",  # OFF reports in grams, converted below
    "saturated-fat_100g": "saturated_fat_g",
    "sugars_100g": "sugars_g",
}
# OFF fields reported in grams that this app stores in milligrams, to
# match USDA's convention and what the rest of the app already expects
# (recipe_service.py's nutrition prompt uses sodium_mg/cholesterol_mg).
OFF_GRAMS_TO_MG_KEYS = {"sodium_mg", "cholesterol_mg"}


@dataclass
class ResolvedFood:
    source: str  # "usda" | "off"
    matched_name: str
    nutrition_per_100g: dict = field(default_factory=dict)
    fdc_id: int | None = None
    off_barcode: str | None = None
    # Backlog B10.5 -- implied density (g/mL), USDA-sourced only. See
    # _parse_usda_density below.
    density_g_per_ml: float | None = None


def is_usda_configured(db: Session) -> bool:
    return bool(settings_service.get_setting(db, "usda_fdc_api_key"))


def _parse_usda_nutrients(food_nutrients: list[dict]) -> dict:
    """Pure function: USDA's `foodNutrients` list -> this app's per-100g
    nutrient dict. USDA Foundation/SR Legacy foods report nutrients per
    100g natively, which is what this whole layer is keyed on. Unknown
    nutrient names are ignored rather than erroring, since USDA foods
    carry dozens of nutrients this app doesn't track."""
    result: dict[str, float] = {}
    for entry in food_nutrients or []:
        name = entry.get("nutrientName") or entry.get("nutrient", {}).get("name")
        value = entry.get("value")
        if value is None:
            value = entry.get("amount")
        key = USDA_NUTRIENT_MAP.get(name or "")
        if key and value is not None:
            try:
                result[key] = float(value)
            except (TypeError, ValueError):
                continue
    return result


def _parse_off_nutrients(nutriments: dict) -> dict:
    """Pure function: an Open Food Facts product's `nutriments` dict ->
    this app's per-100g nutrient dict, converting the two gram-reported
    fields to milligrams to match USDA's/this app's convention."""
    result: dict[str, float] = {}
    for off_key, our_key in OFF_NUTRIENT_MAP.items():
        value = (nutriments or {}).get(off_key)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if our_key in OFF_GRAMS_TO_MG_KEYS:
            value *= 1000
        result[our_key] = value
    return result


def _parse_usda_density(food_portions: list[dict]) -> float | None:
    """Pure function: USDA's `foodPortions` list (from a food DETAIL
    response, e.g. GET /food/{fdcId} -- the bare search-result candidate
    doesn't carry this) -> an implied density in g/mL, by finding the
    FIRST portion whose measure unit is a recognized volume unit
    (unit_conversion_service.normalize_unit/VOLUME_UNITS) and dividing
    its gramWeight by that portion's volume. USDA lists portions in an
    unspecified order and reports a mix of volume ("cup", "tbsp") and
    non-volume ("medium", "slice", "package") measures depending on the
    food -- taking the first volume match is a documented simplification
    (no attempt to pick the "most representative" one), same spirit as
    resolve_ingredient_name's own "first search result wins" choice.
    Returns None (never guesses) if no portion uses a recognized volume
    unit, or if amount/gramWeight is missing or non-positive."""
    for portion in food_portions or []:
        gram_weight = portion.get("gramWeight")
        amount = portion.get("amount")
        if not gram_weight or not amount:
            continue
        measure_unit = portion.get("measureUnit") or {}
        unit_name = measure_unit.get("name") or measure_unit.get("abbreviation") or portion.get("modifier")
        normalized = unit_conversion_service.normalize_unit(unit_name)
        if normalized not in unit_conversion_service.VOLUME_UNITS:
            continue
        try:
            volume_ml = float(amount) * unit_conversion_service.VOLUME_TO_ML[normalized]
            if volume_ml <= 0:
                continue
            return round(float(gram_weight) / volume_ml, 5)
        except (TypeError, ValueError):
            continue
    return None


def search_usda(db: Session, query: str, page_size: int = 5) -> list[dict]:
    """Raw candidate list from USDA FDC's food search -- network call.
    Returns [] (not an error) if no API key is configured or the request
    fails, so callers can fall back to Open Food Facts."""
    api_key = settings_service.get_setting(db, "usda_fdc_api_key")
    if not api_key:
        return []
    try:
        resp = httpx.get(
            f"{USDA_BASE_URL}/foods/search",
            params={
                "query": query,
                "pageSize": page_size,
                "dataType": ["Foundation", "SR Legacy"],
                "api_key": api_key,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json().get("foods", [])
    except (httpx.HTTPError, ValueError):
        return []


def get_usda_food(db: Session, fdc_id: int) -> dict | None:
    """Full nutrient detail for one USDA food -- network call."""
    api_key = settings_service.get_setting(db, "usda_fdc_api_key")
    if not api_key:
        return None
    try:
        resp = httpx.get(
            f"{USDA_BASE_URL}/food/{fdc_id}",
            params={"api_key": api_key},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError):
        return None


def search_off(query: str, page_size: int = 5) -> list[dict]:
    """Raw candidate list from Open Food Facts' text search -- network
    call, no API key needed. Returns [] on any failure."""
    try:
        resp = httpx.get(
            OFF_SEARCH_URL,
            params={"search_terms": query, "page_size": page_size, "json": 1},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        return resp.json().get("products", [])
    except (httpx.HTTPError, ValueError):
        return []


def get_off_product(barcode: str) -> dict | None:
    """Single product lookup by barcode -- what B4.1's barcode scanner
    will call directly. Returns None if not found or on any failure."""
    try:
        resp = httpx.get(OFF_PRODUCT_URL.format(barcode=barcode), timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != 1:
            return None
        return data.get("product")
    except (httpx.HTTPError, ValueError):
        return None


def resolve_ingredient_name(db: Session, name: str) -> ResolvedFood | None:
    """Tries USDA first (if configured), then Open Food Facts. Takes the
    first search result as the match -- no fuzzy ranking beyond what each
    API's own search already does, which is a documented simplification:
    a smarter matcher (unit-aware, alias-aware) is future work once real
    usage shows how often the top result is wrong. Returns None if
    neither source has anything (including "USDA not configured and OFF
    has no match") -- callers should treat that as "still AI-estimated
    only," not an error."""
    name = (name or "").strip()
    if not name:
        return None

    usda_candidates = search_usda(db, name, page_size=1)
    if usda_candidates:
        candidate = usda_candidates[0]
        fdc_id = candidate.get("fdcId")
        detail = get_usda_food(db, fdc_id) if fdc_id else None
        nutrients = _parse_usda_nutrients((detail or candidate).get("foodNutrients", []))
        if nutrients:
            density = _parse_usda_density(detail.get("foodPortions", [])) if detail else None
            return ResolvedFood(
                source="usda",
                matched_name=candidate.get("description", name),
                nutrition_per_100g=nutrients,
                fdc_id=fdc_id,
                density_g_per_ml=density,
            )

    off_candidates = search_off(name, page_size=1)
    if off_candidates:
        candidate = off_candidates[0]
        nutrients = _parse_off_nutrients(candidate.get("nutriments", {}))
        if nutrients:
            return ResolvedFood(
                source="off",
                matched_name=candidate.get("product_name") or name,
                nutrition_per_100g=nutrients,
                off_barcode=candidate.get("code"),
            )

    return None


def resolve_and_cache_ingredient(db: Session, ingredient: RecipeIngredient, force: bool = False) -> bool:
    """Resolves `ingredient.ingredient_name` and writes the result onto
    the ORM object's resolution columns -- does NOT commit, callers own
    the transaction (same convention as recipe_service.py's other
    ingredient-mutating helpers). Skips the network entirely if a REAL
    match is already cached (`resolution_source` is "usda"/"off") unless
    `force=True`, so re-viewing a recipe never re-triggers API calls for
    ingredients that already resolved successfully. A prior "unresolved"
    result is deliberately NOT treated as sticky the same way -- e.g.
    after the user adds a USDA key, ingredients that failed before should
    retry on the next call without needing `force=True`. Returns True if
    a match was found (or already cached), False if the ingredient is/
    remains unresolved."""
    if ingredient.resolution_source and ingredient.resolution_source != "unresolved" and not force:
        return True

    result = resolve_ingredient_name(db, ingredient.ingredient_name)
    if result is None:
        ingredient.resolution_source = "unresolved"
        ingredient.resolved_at = datetime.now(timezone.utc)
        return False

    ingredient.fdc_id = result.fdc_id
    ingredient.off_barcode = result.off_barcode
    ingredient.resolved_food_name = result.matched_name
    ingredient.resolution_source = result.source
    ingredient.nutrition_per_100g = result.nutrition_per_100g
    ingredient.density_g_per_ml = result.density_g_per_ml
    ingredient.resolved_at = datetime.now(timezone.utc)
    return True


# --- B1.2: summing resolved ingredients into Recipe.nutrition ----------
#
# The other half of B1.1's "plumbing before user-visible features"
# sequencing: resolve_and_cache_ingredient (above) only caches a per-100g
# snapshot per ingredient. Nothing until now actually sums those into
# Recipe.nutrition or tells the user whether a recipe's nutrition block
# is real data or an LLM's unchecked guess -- which is the entire B1
# backlog group's stated problem (see PROJECT-PLAN.md).


def compute_ingredient_grams(ingredient: RecipeIngredient) -> float | None:
    """Converts one ingredient's quantity+unit into grams, the unit
    nutrition_per_100g is denominated in. Returns None (never guesses)
    for: no stated quantity ("salt to taste"), a count-based unit ("2
    eggs", "1 can") which has no fixed weight this app can look up, or a
    volume unit ("1 cup") whose ingredient has no cached
    `density_g_per_ml` (backlog B10.5's `_parse_usda_density` is the only
    source for that -- plenty of resolved ingredients still won't have
    one). Mass units (g/kg/oz/lb) always convert; volume units convert
    only when a density is available -- this is the same "never invent a
    conversion" discipline unit_conversion_service.convert() itself
    already enforces, this function just passes along whatever density
    it actually has instead of none at all."""
    if ingredient.quantity is None:
        return None
    result = unit_conversion_service.convert(
        ingredient.quantity, ingredient.unit, "g", density_g_per_ml=ingredient.density_g_per_ml
    )
    if result is None:
        return None
    return result.quantity


def compute_recipe_nutrition(recipe: Recipe) -> tuple[dict, str]:
    """Sums each ingredient's cached `nutrition_per_100g` (see
    resolve_and_cache_ingredient) scaled to its actual weight in grams,
    divides by `default_servings` to land back in the per-serving unit
    `Recipe.nutrition` has always used, and returns (nutrition_dict,
    provenance):

    - "ai_estimated": no quantified ingredient contributed real data
      (nothing resolved, or everything that resolved is volume/count-
      based with no way to weigh it). The recipe's EXISTING nutrition
      dict is returned UNCHANGED -- this never overwrites a real AI
      estimate with an empty computed total just because resolution
      hasn't happened yet, and never fabricates a number from zero data.
    - "partial": some but not all quantified ingredients contributed.
      The returned dict is a real, honest sum -- just of an incomplete
      ingredient list, so it should be shown as a probable undercount,
      not a final number.
    - "computed": every quantified ingredient contributed. The returned
      dict fully replaces whatever was there before.

    "Quantified" excludes ingredients with no stated quantity (e.g. "salt
    to taste") from both the sum and the completeness check -- there's no
    way to weigh them, and that's not a resolution failure, so they
    shouldn't count against "computed" vs. "partial" the way an
    unresolved or unconvertible ingredient does. A recipe with only
    unquantified ingredients (unusual, but possible) reports
    "ai_estimated" rather than a false "computed" over zero ingredients.

    Callers own the transaction -- this only reads, never writes to the
    DB or the ORM objects themselves (see routers/recipes.py's
    compute_recipe_nutrition_endpoint, which does the actual persisting)."""
    quantified = [ing for ing in recipe.ingredients if ing.quantity is not None]
    if not quantified:
        return dict(recipe.nutrition or {}), "ai_estimated"

    totals: dict[str, float] = {}
    contributed = 0
    for ing in quantified:
        if not ing.nutrition_per_100g:
            continue
        grams = compute_ingredient_grams(ing)
        if grams is None:
            continue
        contributed += 1
        factor = grams / 100.0
        for key, per_100g in ing.nutrition_per_100g.items():
            try:
                totals[key] = totals.get(key, 0.0) + float(per_100g) * factor
            except (TypeError, ValueError):
                continue

    if contributed == 0:
        return dict(recipe.nutrition or {}), "ai_estimated"

    servings = recipe.default_servings or 1
    per_serving = {key: round(value / servings, 1) for key, value in totals.items()}
    provenance = "computed" if contributed == len(quantified) else "partial"
    return per_serving, provenance
