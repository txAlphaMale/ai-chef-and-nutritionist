"""Ingredient-to-food-database resolution (backlog B1.1).

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
automatically on recipe create/update yet; `POST /api/recipes/{id}/
resolve-nutrition` is the current call site (explicit, on demand). Wiring
resolution into the create/import flow, and summing resolved nutrients
into `Recipe.nutrition` with a `computed`/`partial`/`ai_estimated`
provenance tag, is B1.2 -- a separate, not-yet-built pass.

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

from app.models import RecipeIngredient
from app.services import settings_service

USDA_BASE_URL = "https://api.nal.usda.gov/fdc/v1"
OFF_SEARCH_URL = "https://world.openfoodfacts.org/cgi/search.pl"
OFF_PRODUCT_URL = "https://world.openfoodfacts.org/api/v2/product/{barcode}.json"

REQUEST_TIMEOUT_SECONDS = 8.0

# USDA FoodData Central nutrient names (as returned in each food's
# `foodNutrients[].nutrientName`) mapped to this app's per-100g nutrient
# dict keys. Superset of what either recipe_service.py or
# meal_plan_service.py currently ask the LLM for (see B1.3 -- those two
# prompts disagree on a 7-key vs. 4-key set and both omit saturated fat,
# the single most LDL-relevant number for the author's stated goal) so
# this cache doesn't need a schema change once B1.2/B1.3 land.
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
            return ResolvedFood(
                source="usda",
                matched_name=candidate.get("description", name),
                nutrition_per_100g=nutrients,
                fdc_id=fdc_id,
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
    ingredient.resolved_at = datetime.now(timezone.utc)
    return True
