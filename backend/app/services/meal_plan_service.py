"""Meal-plan business logic: gathering the grounding context for AI-
assisted weekly generation (household prefs, inventory urgency/priority,
kitchen equipment, existing recipe catalog), defensively parsing the
model's proposed plan, and deriving a grocery list (planned ingredients
minus what's already on hand).

Mirrors the patterns already established in inventory_service.py and
recipe_service.py: pure/testable functions are kept separate from the
DB-touching wrappers around them, and LLM output parsing is defensive
(strict JSON first, then a best-effort extraction) since real model
output often wraps JSON in prose or markdown fences.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import HouseholdPreferences, InventoryItem, KitchenProfile, MealPlan, Recipe
from app.schemas.ai_extraction import ExtractedMealPlan, schema_of
from app.services import (
    allergen_service,
    dietary_pattern_service,
    health_service,
    ingredient_resolution_service,
    inventory_service,
    recipe_service,
    unit_conversion_service,
)
from app.services.food_data_service import NUTRITION_PROMPT_HINT
from app.services.recipe_service import _extract_json_object, _safe_int

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
MEAL_TYPE_VALUES = {"breakfast", "lunch", "dinner", "snack"}


# --- Generation context --------------------------------------------------
#
# Everything the model needs to ground a sensible plan: how many people,
# dietary restrictions/goals, what equipment is available, which
# inventory items are worth prioritizing, and what's already in the
# recipe catalog (so it can reuse a good match -- especially staples --
# instead of inventing something new for every single slot).


def get_active_kitchen_profile(db: Session, kitchen_profile_id: int | None = None) -> KitchenProfile | None:
    if kitchen_profile_id is not None:
        return db.get(KitchenProfile, kitchen_profile_id)
    return db.query(KitchenProfile).filter_by(is_active=True).first()


def get_household_preferences(db: Session) -> HouseholdPreferences | None:
    return db.query(HouseholdPreferences).first()


def build_recipe_catalog_summary(db: Session, limit: int = 40) -> list[dict]:
    """Compact catalog for the generation prompt -- staples and highly
    rated recipes first, since those are the best candidates to reuse."""
    recipes = (
        db.query(Recipe)
        .order_by(Recipe.is_staple.desc(), Recipe.rating.is_(None), Recipe.rating.desc(), Recipe.title)
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "title": r.title,
            "default_servings": r.default_servings,
            "is_staple": r.is_staple,
            "rating": r.rating,
            "tags": [t.name for t in r.tags],
        }
        for r in recipes
    ]


def build_priority_ingredients_summary(db: Session, limit: int = 15) -> list[dict]:
    suggestions = inventory_service.get_priority_suggestions(db, limit=limit)
    return [{"name": item.name, "reasons": reasons} for item, _score, reasons in suggestions]


def gather_generation_context(
    db: Session,
    household_size: int | None,
    meal_types: list[str],
    kitchen_profile_id: int | None,
    entry_guidance: list[dict],
    notes: str | None,
    prep_day: int | None = None,
) -> dict:
    household = get_household_preferences(db)
    kitchen = get_active_kitchen_profile(db, kitchen_profile_id)
    guidance_by_slot = {(g["day_of_week"], g["meal_type"]): g for g in entry_guidance}

    slots = []
    for day in range(7):
        for meal_type in meal_types:
            g = guidance_by_slot.get((day, meal_type), {})
            slots.append(
                {
                    "day_of_week": day,
                    "meal_type": meal_type,
                    "tags": g.get("tags") or [],
                    "notes": g.get("notes"),
                }
            )

    return {
        "household_size": household_size or (household.household_size if household else 2),
        "dietary_restrictions": (household.dietary_restrictions if household else []) or [],
        "goals": household.goals if household else None,
        # Backlog B2.3 -- a structured preset's fixed guidance text, kept
        # separate from the free-text goals_line above so a household
        # gets the same concrete instructions every generation instead of
        # the model re-interpreting "reduce cholesterol" differently run
        # to run. None when no preset is selected.
        "dietary_pattern_guidance": dietary_pattern_service.get_pattern_guidance(
            household.dietary_pattern if household else None
        ),
        "indulgence_frequency": (household.indulgence_frequency if household else "weekly") or "weekly",
        "kitchen_name": kitchen.name if kitchen else None,
        "kitchen_profile_id": kitchen.id if kitchen else None,
        "equipment": (kitchen.equipment if kitchen else []) or [],
        "priority_ingredients": build_priority_ingredients_summary(db),
        "recipe_catalog": build_recipe_catalog_summary(db),
        "meal_types_requested": meal_types,
        "slots": slots,
        "notes": notes,
        # Backlog B5.2 -- see this module's build_generation_prompt/
        # _format_prep_day_section for what this actually does to the
        # prompt. None (the default) means no prep-day steering at all --
        # generation behaves exactly as it did before this backlog item.
        "prep_day": prep_day,
        # Phase 6: household health trends (BMI/cholesterol/weight) and
        # any imported nutritionist reference material -- both optional,
        # both empty strings when nothing's been logged/uploaded yet.
        # knowledge_context is now retrieval-based (2026-07-31, see
        # health_service.build_knowledge_context) rather than a fixed
        # concatenation, so it needs a query -- there's no single natural
        # user question during generation the way there is in chat, so a
        # synthetic one is built from what generation itself most needs
        # grounded: dietary restrictions and stated goals.
        "health_summary": health_service.build_health_context_summary(db),
        "knowledge_context": health_service.build_knowledge_context(db, _build_knowledge_query(household)),
    }


def _build_knowledge_query(household) -> str:
    """Synthetic retrieval query for meal-plan generation -- there's no
    single user question to embed the way chat has one, so this stands
    in for "what would make a reference document relevant to planning
    this household's meals," built from their stated dietary
    restrictions and goals."""
    if household is None:
        return ""
    parts = list(household.dietary_restrictions or [])
    if household.goals:
        parts.append(household.goals)
    return " ".join(parts).strip()


# --- Prompt construction ---------------------------------------------------

MEAL_PLAN_PROMPT_TEMPLATE = """\
Build a weekly meal plan and respond with ONLY a JSON object (no other \
text, no markdown fences) with a single key "entries": an array of meal \
slot objects, one per requested day/meal-type combination listed below. \
Each entry object has these keys:
- "day_of_week": integer 0-6 (0=Monday .. 6=Sunday)
- "meal_type": one of {meal_types}
- "recipe_id": integer id from the EXISTING RECIPE CATALOG below if a \
catalog recipe is a good fit for this slot, otherwise null
- "new_recipe": if and ONLY IF no catalog recipe fits, a full recipe \
object with keys "title", "description", "default_servings", \
"prep_time_minutes", "cook_time_minutes", "instructions" (array of \
strings), "ingredients" (array of objects with "ingredient_name", \
"quantity", "unit", "prep_note"), "nutrition" (object with best-effort \
per-serving numbers or null for these keys: {NUTRITION_PROMPT_HINT}), \
"tags" (array of short lowercase strings), "tips" (array of short \
strings, may be empty). Otherwise null.
- "servings": integer, defaulting to the household size below unless a \
different serving count makes more sense for this slot
- "requested_tags": array of short lowercase tag strings this slot \
should satisfy -- echo back any guidance given for this slot below, \
plus any tags of the chosen/proposed recipe that are relevant
- "is_indulgence": true for only as many meals across the whole week as \
the indulgence frequency below implies is reasonable; false otherwise
- "notes": short string or null

Household: {household_size} people. Dietary restrictions/goals: \
{dietary_restrictions}. {goals_line}Indulgence frequency: \
{indulgence_frequency} (roughly how often an indulgent/treat meal is \
appropriate across the whole plan -- most meals should still be \
balanced and nutritious).
{dietary_pattern_section}{prep_day_section}

Kitchen/equipment currently in use ({kitchen_name}): {equipment}. Do \
not plan meals that need equipment outside this list.

Ingredients to prioritize using up (expiring soon, gone unused a long \
time, or explicitly flagged by the household) -- work a sensible subset \
of these into the week without overusing any single one:
{priority_ingredients}

Existing recipe catalog (id, title, tags, staple/rating) -- STRONGLY \
prefer reusing a good-fit id from here, especially staples, over \
proposing a new recipe for every slot:
{recipe_catalog}
{health_section}{knowledge_section}
Meal slots to plan, with any specific guidance for that slot:
{slots}
{extra_notes}""".replace("{NUTRITION_PROMPT_HINT}", NUTRITION_PROMPT_HINT)
# ^ plain str.replace (not part of the .format(...) call build_generation_
# prompt() below makes with household_size/dietary_restrictions/etc.) --
# see recipe_service.RECIPE_IMPORT_PROMPT for why this has to be a
# separate substitution pass rather than an extra .format() kwarg.


# Constrained-decoding schema for weekly generation. See
# app/schemas/ai_extraction.py -- the prompt's own description of the
# entry shape above is a human-readable restatement of this.
MEAL_PLAN_SCHEMA = schema_of(ExtractedMealPlan)

# Up to 21 slots, any of which may carry a complete proposed recipe.
# Reserved explicitly so the grounding context (catalog, inventory,
# knowledge chunks) can never crowd the answer out of the window.
MEAL_PLAN_RESPONSE_TOKENS = 4000


def _format_priority_ingredients(items: list[dict]) -> str:
    if not items:
        return "(none currently flagged -- plan freely)"
    lines = []
    for it in items:
        reasons = ", ".join(it.get("reasons") or [])
        lines.append(f"- {it['name']}" + (f" ({reasons})" if reasons else ""))
    return "\n".join(lines)


def _format_recipe_catalog(catalog: list[dict]) -> str:
    if not catalog:
        return "(catalog is empty -- propose a new_recipe for every slot)"
    lines = []
    for r in catalog:
        tags = ", ".join(r.get("tags") or []) or "no tags"
        staple = "staple" if r.get("is_staple") else "not staple"
        rating = f"rated {r['rating']}/5" if r.get("rating") else "unrated"
        lines.append(f"- id={r['id']}: {r['title']} ({staple}, {rating}, tags: {tags})")
    return "\n".join(lines)


def _format_slots(slots: list[dict]) -> str:
    lines = []
    for s in slots:
        day_name = DAY_NAMES[s["day_of_week"]]
        guidance = ""
        if s.get("tags"):
            guidance += f" -- requested tags: {', '.join(s['tags'])}"
        if s.get("notes"):
            guidance += f" -- {s['notes']}"
        lines.append(f"- {day_name} {s['meal_type']}{guidance}")
    return "\n".join(lines)


def _format_health_section(health_summary: str | None) -> str:
    if not health_summary:
        return ""
    return (
        "\nHousehold health trends (use to favor lower-cholesterol, "
        "appropriately-portioned, nutrient-dense choices where relevant -- "
        "do not mention these numbers directly to the user, just let them "
        "quietly steer meal selection):\n" + health_summary + "\n"
    )


def _format_dietary_pattern_section(guidance: str | None) -> str:
    """Backlog B2.3 -- renders the selected preset's fixed guidance block,
    or an empty string when no preset is selected (the common case for a
    household that hasn't opted into one)."""
    if not guidance:
        return ""
    return "\n" + guidance + "\n"


def _format_prep_day_section(prep_day: int | None) -> str:
    """Backlog B5.2 -- prep-day / batch-cooking mode.

    Scoping note (read before changing this): Mealime Pro's "Meal Prep
    Day" and MealPrepPro both batch-cook shared components once, then
    mix-and-match them into several later meals. A literal version of
    that would need this app to model recipes as composed of reusable
    sub-components with their own inventory/quantity tracking -- a much
    bigger schema change than this backlog item asked for, and one nothing
    else in the app currently needs. This ships the same underlying
    OUTCOME (fewer independent stove-heavy cooking sessions across the
    week) as a generation-PROMPT heuristic instead: the model is asked to
    batch-cook a few base components on the prep day and steer other
    slots toward fast-assembly recipes that reuse them. The persisted
    result is still ordinary Recipe/MealPlanEntry rows -- no new linking
    concept, so grocery-list and inventory-deduction logic needs no
    changes and stays exactly as accurate (or inaccurate) as it already
    is for any other week. What this does NOT do: verify the model
    actually produced fewer distinct cooking sessions, or enforce that a
    "reuses prep day" tag is truthful -- same "prompted, not enforced"
    honesty limit every other generation-steering feature in this prompt
    already has (dietary pattern, indulgence frequency, etc.)."""
    if prep_day is None:
        return ""
    day_name = DAY_NAMES[prep_day]
    return (
        f"\nPrep-day / batch-cooking mode is ON for {day_name}: plan "
        f"{day_name}'s meal(s) to batch-cook 2-3 reusable base components "
        "in larger quantities than that single meal needs on its own -- "
        "e.g. a grain/starch, a protein, and a roasted or roastable "
        "vegetable. For meal slots on OTHER days, prefer recipes that are "
        "genuinely fast-assembly reuses of those same batch-cooked "
        f"components (a bowl, a wrap, a salad, a quick stir-fry) over "
        "fully independent recipes that need their own full cook -- the "
        "goal is fewer separate cooking sessions across the week, not "
        "identical repeated meals. Tag the prep-day recipe(s) with "
        "'make_ahead', tag the reuse meals with 'quick', and for a reuse "
        "meal's ingredient list, note which component(s) are \"already "
        f"cooked from {day_name}\" in that ingredient's prep_note rather "
        "than listing it as a raw ingredient to buy again.\n"
    )


def _format_knowledge_section(knowledge_context: str | None) -> str:
    if not knowledge_context:
        return ""
    return (
        "\nReference material the household has provided (a nutritionist's "
        "guidance, a specific diet plan, etc.) -- follow it where relevant "
        "and it doesn't conflict with the dietary restrictions above:\n" + knowledge_context + "\n"
    )


def build_generation_prompt(context: dict) -> str:
    goals_line = f"Goals: {context['goals']}. " if context.get("goals") else ""
    dietary = ", ".join(context.get("dietary_restrictions") or []) or "none specified"
    extra_notes = f"\nAdditional notes from the household: {context['notes']}\n" if context.get("notes") else ""
    return MEAL_PLAN_PROMPT_TEMPLATE.format(
        meal_types=sorted(set(context.get("meal_types_requested") or MEAL_TYPE_VALUES)),
        household_size=context.get("household_size", 2),
        dietary_restrictions=dietary,
        goals_line=goals_line,
        dietary_pattern_section=_format_dietary_pattern_section(context.get("dietary_pattern_guidance")),
        prep_day_section=_format_prep_day_section(context.get("prep_day")),
        indulgence_frequency=context.get("indulgence_frequency") or "weekly",
        kitchen_name=context.get("kitchen_name") or "unspecified kitchen",
        equipment=", ".join(context.get("equipment") or []) or "unspecified",
        priority_ingredients=_format_priority_ingredients(context.get("priority_ingredients") or []),
        recipe_catalog=_format_recipe_catalog(context.get("recipe_catalog") or []),
        health_section=_format_health_section(context.get("health_summary")),
        knowledge_section=_format_knowledge_section(context.get("knowledge_context")),
        slots=_format_slots(context.get("slots") or []),
        extra_notes=extra_notes,
    )


# --- Response parsing --------------------------------------------------


def parse_meal_plan_response(raw_text: str) -> list[dict]:
    """Defensively extracts the entries array from raw model output.
    Each returned dict has permissive/coerced fields; whether a
    recipe_id actually exists in the catalog is validated separately
    (validate_entries_against_catalog, below) since that needs DB
    access this pure parsing function doesn't."""
    data = _extract_json_object(raw_text)
    entries_raw = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries_raw, list):
        return []

    entries = []
    for e in entries_raw:
        if not isinstance(e, dict):
            continue
        day = _safe_int(e.get("day_of_week"))
        if day is None or not (0 <= day <= 6):
            continue
        meal_type = str(e.get("meal_type") or "dinner").strip().lower()
        if meal_type not in MEAL_TYPE_VALUES:
            meal_type = "dinner"

        new_recipe = None
        raw_new_recipe = e.get("new_recipe")
        if isinstance(raw_new_recipe, dict) and raw_new_recipe.get("title"):
            new_recipe = recipe_service.coerce_recipe_fields(raw_new_recipe)

        entries.append(
            {
                "day_of_week": day,
                "meal_type": meal_type,
                "recipe_id": _safe_int(e.get("recipe_id")),
                "new_recipe": new_recipe,
                "servings": _safe_int(e.get("servings")) or 2,
                "requested_tags": [str(t).strip().lower() for t in (e.get("requested_tags") or []) if str(t).strip()],
                "is_indulgence": bool(e.get("is_indulgence")),
                "notes": e.get("notes") or None,
            }
        )
    return entries


def validate_entries_against_catalog(entries: list[dict], catalog_ids: set[int]) -> list[dict]:
    """The model sometimes hallucinates a recipe_id that isn't in the
    catalog it was given. Null it out rather than trusting it blindly --
    if the entry also has no new_recipe, the slot is left recipe-less
    for the user to fix manually before confirming the plan, same as any
    other AI-preview-then-confirm flow in this app."""
    for e in entries:
        if e["recipe_id"] is not None and e["recipe_id"] not in catalog_ids:
            e["recipe_id"] = None
    return entries


def attach_restriction_warnings(db: Session, entries: list[dict]) -> list[dict]:
    """Backlog B3.1: runs the deterministic allergen check against each
    generated entry's actual ingredients (the catalog recipe's, if one
    was chosen, otherwise the proposed new_recipe's) and attaches
    `restriction_warnings`/`cross_contact_warnings` to the entry dict, so
    a conflict is visible in the generation review step -- the same
    "verify what the model did, don't just trust its instructions"
    discipline applied at recipe import (routers/recipes.py) and recipe
    view (routers/recipes.py's _to_read). An entry with neither a
    recipe_id nor a new_recipe (a slot the model left empty) gets an
    empty result, not an error. Recipe rows are cached by id within one
    call so a staple reused across several slots in the same week is
    only loaded once, and household restrictions are read once for the
    whole plan rather than once per entry (audit P1-9)."""
    recipe_cache: dict[int, Recipe] = {}
    restrictions = allergen_service.load_household_restrictions(db)
    for e in entries:
        if e.get("new_recipe"):
            ingredient_names = [i.get("ingredient_name", "") for i in e["new_recipe"].get("ingredients", [])]
        elif e.get("recipe_id") is not None:
            recipe_id = e["recipe_id"]
            if recipe_id not in recipe_cache:
                recipe_cache[recipe_id] = db.get(Recipe, recipe_id)
            recipe = recipe_cache[recipe_id]
            ingredient_names = [i.ingredient_name for i in recipe.ingredients] if recipe else []
        else:
            ingredient_names = []
        check = allergen_service.check_household_restrictions(db, ingredient_names, restrictions)
        e["restriction_warnings"] = [vars(m) for m in check.matches]
        e["cross_contact_warnings"] = [vars(m) for m in check.cross_contact_matches]
    return entries


# --- Grocery list --------------------------------------------------------
#
# Split the same way as recipe_service's URL import: a pure aggregation/
# subtraction pair that's unit-testable without a DB, plus a thin
# DB-touching wrapper that loads a persisted MealPlan's entries and the
# current inventory.


def _ingredient_key(name: str, unit: str | None) -> tuple[str, str]:
    return (name.strip().lower(), (unit or "").strip().lower())


def _same_unit(unit_a: str | None, unit_b: str | None) -> bool:
    return unit_conversion_service.normalize_unit(unit_a) == unit_conversion_service.normalize_unit(unit_b)


def _merge_same_name_group(ingredients: list[dict]) -> list[dict]:
    """Merges ingredient lines that share a name (case-insensitive) into
    as few unit-buckets as possible: an exact (post-normalization) unit
    match always merges; a different-but-convertible unit (e.g. tbsp
    into an existing cup bucket, backlog B5.3) converts and merges into
    whichever bucket it matches first; anything left over (count units,
    or a volume<->mass gap with no density -- see
    unit_conversion_service's module docstring for why that's not
    attempted here) stays as its own separate line, same as before this
    layer existed."""
    buckets: list[dict] = []
    for ing in ingredients:
        unit = ing.get("unit")
        quantity = ing.get("quantity")
        target = None
        for bucket in buckets:
            if _same_unit(unit, bucket["unit"]):
                target = bucket
                break
            if (
                quantity is not None
                and bucket["quantity"] is not None
                and unit_conversion_service.units_are_comparable(unit, bucket["unit"])
            ):
                converted = unit_conversion_service.convert(quantity, unit, bucket["unit"])
                if converted is not None:
                    target = bucket
                    quantity = converted.quantity  # now expressed in the bucket's unit
                    break
        if target is not None:
            if quantity is not None:
                target["quantity"] = round((target["quantity"] or 0) + quantity, 3)
        else:
            buckets.append({"ingredient_name": ing["ingredient_name"], "unit": unit, "quantity": quantity})
    return buckets


# Backlog B5.4 (2026-08-01) -- grocery-list aisle/category grouping.
# GroceryListItem.category has existed since Phase 5 explicitly "for a
# future group-by-aisle view" but nothing ever populated it for
# auto-generated lines (only a manually-added item could carry one, and
# even then only if the user typed it in themselves). Reuses the SAME
# six-value taxonomy InventoryItem already uses (pantry/fridge/freezer/
# produce/spice/other) rather than inventing a separate "aisle" taxonomy
# -- those buckets already map reasonably well onto how a grocery store
# is laid out (produce section, dairy case, freezer aisle, spice aisle,
# dry-goods aisles), and reusing them means a household's own inventory
# categorization stays the authoritative source whenever a name match
# exists (see subtract_inventory below), with this keyword guesser only
# filling in for ingredients that aren't already in inventory at all.
#
# Deliberately NOT held to the same "never guess" discipline as
# allergen_service's matching -- an allergen miss can be a real safety
# issue, a grocery item landing in the wrong aisle bucket is a minor
# daily-use inconvenience, easily hand-corrected in the UI. Keyword lists
# are intentionally not exhaustive; unmatched ingredients fall back to
# "other" rather than a wrong guess in a more specific-sounding bucket.
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    # Checked before "fridge"/"pantry" below -- "pepper" alone would
    # otherwise land ambiguously; multi-word produce items are listed
    # first specifically to win over a generic "pepper"/"onion" spice-
    # aisle false match.
    "produce": [
        "apple",
        "banana",
        "orange",
        "lemon",
        "lime",
        "grape",
        "melon",
        "berries",
        "strawberry",
        "blueberry",
        "raspberry",
        "avocado",
        "tomato",
        "onion",
        "garlic",
        "potato",
        "sweet potato",
        "carrot",
        "celery",
        "lettuce",
        "spinach",
        "kale",
        "broccoli",
        "cauliflower",
        "cucumber",
        "zucchini",
        "squash",
        "cabbage",
        "mushroom",
        "bell pepper",
        "jalapeno",
        "cilantro",
        "parsley",
        "fresh basil",
        "fresh herbs",
        "green onion",
        "scallion",
        "ginger root",
    ],
    "fridge": [
        "milk",
        "cheese",
        "yogurt",
        "yoghurt",
        "butter",
        "cream",
        "egg",
        "eggs",
        "sour cream",
        "cottage cheese",
        "cream cheese",
        "tofu",
        "hummus",
        "deli",
        "ham",
        "bacon",
        "sausage",
        "chicken breast",
        "chicken thigh",
        "ground beef",
        "steak",
        "pork chop",
        "salmon",
        "shrimp",
        "fish fillet",
        "tortilla",
    ],
    "freezer": ["frozen", "ice cream", "popsicle"],
    "spice": [
        "salt",
        "black pepper",
        "white pepper",
        "cumin",
        "paprika",
        "cinnamon",
        "oregano",
        "dried basil",
        "cayenne",
        "turmeric",
        "nutmeg",
        "chili powder",
        "curry powder",
        "spice",
        "seasoning",
        "vanilla extract",
        "bay leaf",
        "thyme",
        "rosemary",
    ],
    "pantry": [
        "flour",
        "sugar",
        "rice",
        "pasta",
        "noodle",
        "bread",
        "oil",
        "vinegar",
        "canned",
        "beans",
        "lentil",
        "broth",
        "stock",
        "cereal",
        "oats",
        "oatmeal",
        "nuts",
        "peanut butter",
        "honey",
        "syrup",
        "sauce",
        "ketchup",
        "mustard",
        "mayonnaise",
        "soy sauce",
        "cracker",
        "chip",
        "cookie",
        "baking powder",
        "baking soda",
        "yeast",
        "cornstarch",
    ],
}


def guess_grocery_category(ingredient_name: str) -> str | None:
    """Best-effort, keyword-based -- see the module note above for why
    this is held to a lighter standard than allergen_service's matching.
    Returns None (not "other") when nothing matches, so a caller can
    distinguish "guessed other" from "no guess made" if that distinction
    ever matters; `compute_grocery_list`/the manual-add endpoint both
    treat None the same as "other" for display purposes today."""
    name_lower = (ingredient_name or "").lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in name_lower for kw in keywords):
            return category
    return None


def aggregate_ingredients(ingredient_lists: list[list[dict]]) -> list[dict]:
    """Merges scaled ingredient dicts (ingredient_name/quantity/unit)
    across multiple recipes into one summed list. Two ingredient lines
    with the same name merge if their units are identical OR convertible
    into each other (backlog B5.3, added 2026-07-31 via
    unit_conversion_service) -- e.g. "2 cup flour" + "8 tbsp flour" now
    merges into one cup-denominated line instead of staying as two.
    Genuinely incompatible units for the same ingredient (a count unit,
    or volume vs. mass with no known density) still stay as separate
    lines -- this deliberately never guesses a conversion."""
    by_name: dict[str, list[dict]] = {}
    order: list[str] = []
    for ingredients in ingredient_lists:
        for ing in ingredients:
            name = str(ing.get("ingredient_name") or "").strip()
            if not name:
                continue
            key = name.lower()
            if key not in by_name:
                by_name[key] = []
                order.append(key)
            by_name[key].append({"ingredient_name": name, "quantity": ing.get("quantity"), "unit": ing.get("unit")})

    merged: list[dict] = []
    for key in order:
        merged.extend(_merge_same_name_group(by_name[key]))
    return merged


def is_pantry_staple(
    ingredient_name: str,
    pantry_staples: list[str] | None,
    transformation_words: frozenset[str] | None = None,
) -> bool:
    """Backlog B5.5 -- does this ingredient match the household's own
    free-text "always on hand" list?

    Rewritten 2026-08-03 (audit P1-5). This was substring matching in
    either direction, and the original comment argued that a household's
    arbitrary list "is not a safety property" so lighter-weight matching
    was appropriate. Re-reading what a hit actually DOES makes that
    argument look wrong: a staple match removes the ingredient from the
    grocery list entirely, before any quantity math runs. The failure
    mode is not a mis-sorted aisle label, it is not buying dinner, with
    nothing on screen to notice. A staple of "oil" suppressing a line for
    "oil-packed tuna" is exactly that, and it is what the old code did.

    So this is held to `THRESHOLD_SUPPRESSING` -- the same bar as a
    database write -- and goes through the same resolution layer as every
    other name match in the app. "salt" still covers "kosher salt"
    (0.83); "oil" no longer covers "oil-packed tuna" (0.40).

    Note the argument order below: the STAPLE is the query and the
    ingredient is the candidate, not the other way round. That is not
    incidental. The resolver deliberately discounts a match where the
    query is more specific than the candidate (asking for "olive oil"
    and finding a generic "oil" row -- the row might not be the thing
    asked for), and a staple list is written the other way: households
    declare generic staples ("salt", "pepper", "flour") intending them to
    cover the specific spellings recipes use. Querying staple-first puts
    generic-covers-specific on the full-credit side of that rule, which
    is what the household meant, while still leaving specific-covers-
    generic discounted -- a staple of "olive oil" does NOT suppress a
    line for plain "oil", because the recipe may well mean canola.

    An empty/None staples list (the default, no household opt-in yet)
    always returns False."""
    if not pantry_staples or not (ingredient_name or "").strip():
        return False
    for staple in pantry_staples:
        if not str(staple or "").strip():
            continue
        match, _ranked = ingredient_resolution_service.best_match(
            str(staple),
            [(ingredient_name, ingredient_name)],
            minimum_score=ingredient_resolution_service.THRESHOLD_SUPPRESSING,
            transformation_words=transformation_words,
        )
        if match is not None:
            return True
    return False


def subtract_inventory(
    aggregated: list[dict],
    inventory_items: list[InventoryItem],
    pantry_staples: list[str] | None = None,
    transformation_words: frozenset[str] | None = None,
) -> list[dict]:
    """For each aggregated ingredient, subtracts matching on-hand
    inventory and returns only what's still needed to buy. An ingredient
    with no stated quantity (e.g. "salt to taste") is only listed if
    nothing matching is already in inventory at all.

    Matching rewritten 2026-08-03 (audit P1-5): this was substring
    matching in EITHER direction against the inventory list, taking the
    first hit. That is how a grocery line for "egg" got reconciled
    against an "eggplant" row, and how "chicken" got reconciled against
    "chicken broth" -- in both cases silently removing or shrinking a
    line for something the household did not actually have.

    Held to `THRESHOLD_ADVISORY` rather than the stricter bar deduction
    uses. The asymmetry is deliberate and follows the cost of being
    wrong: this produces a list a human reads and shops from, and a
    medium-confidence match that shrinks a line is visible on that list,
    where a medium-confidence match that decrements a stored quantity is
    not. Matches below the bar are treated as no match at all, which
    keeps the line at its full quantity -- buying something already in
    the pantry is recoverable; not buying dinner is not. The matched
    item's name and confidence ride along on the line
    (`matched_item_name` / `match_confidence`) so the UI can show what
    was reconciled against what.

    Unit-aware as of backlog B5.3 (2026-07-31): previously this compared
    `ing["quantity"]` directly against `match.quantity` regardless of
    unit -- a real bug where e.g. a grocery line needing "2 lb chicken"
    against an inventory row logged as "500 g" would compare 2 vs. 500
    as raw numbers. Now the on-hand quantity is converted into the
    grocery line's unit first when the two differ and a conversion is
    available; when it isn't (count units, or a volume<->mass gap with
    no density), this falls back to the previous raw-number comparison
    rather than refusing to reconcile the line at all -- a known,
    unchanged imprecision for that remaining case.

    Backlog B5.5 (2026-08-01): a household-declared pantry staple is
    excluded from the list ENTIRELY -- before the inventory-match/
    quantity math below even runs, not just when it happens to have no
    stated quantity. "Always on hand" is a stronger claim than "there's
    currently some in inventory": the household is explicitly saying
    this ingredient should never generate grocery-list noise, tracked
    quantity or not. `pantry_staples` defaults to None/empty, which
    preserves prior behavior exactly for any caller that hasn't been
    updated to pass a household's list."""
    remaining = []
    inventory_candidates = [(item.name, item) for item in inventory_items]
    for ing in aggregated:
        if is_pantry_staple(ing["ingredient_name"], pantry_staples, transformation_words):
            continue

        best, _ranked = ingredient_resolution_service.best_match(
            ing["ingredient_name"],
            inventory_candidates,
            minimum_score=ingredient_resolution_service.THRESHOLD_ADVISORY,
            transformation_words=transformation_words,
            tiebreak_key=ingredient_resolution_service.inventory_tiebreak,
        )
        match = best.payload if best is not None else None
        match_confidence = best.confidence if best is not None else None

        # Backlog B5.4 -- a real inventory row's own category is a better
        # signal than a keyword guess (the household already classified
        # it themselves), so it wins whenever a name match exists at all,
        # even a partial one that still leaves a remaining quantity to
        # buy below. Only fall back to guessing when nothing in
        # inventory matches this ingredient name at all.
        category = match.category if match is not None else guess_grocery_category(ing["ingredient_name"])

        if ing["quantity"] is None:
            if match is None:
                item = dict(ing)
                item["category"] = category
                remaining.append(item)
            continue

        on_hand = 0.0
        unreconciled_unit = None
        if match is not None:
            on_hand = match.quantity or 0.0
            if not _same_unit(match.unit, ing["unit"]):
                converted = unit_conversion_service.convert(on_hand, match.unit, ing["unit"])
                if converted is None:
                    # Refuse rather than guess.
                    #
                    # This used to compare the raw numbers when conversion
                    # failed: a line needing "2 lb chicken" against a
                    # "500 g" inventory row computed 2 - 500 = -498 and
                    # dropped chicken off the grocery list entirely. The
                    # household then doesn't buy it.
                    #
                    # Keeping the full requested quantity and flagging the
                    # line is the honest outcome -- buying a little extra
                    # is recoverable, silently not buying dinner is not.
                    on_hand = 0.0
                    unreconciled_unit = match.unit
                else:
                    on_hand = converted.quantity

        needed = round(ing["quantity"] - on_hand, 3)
        if needed > 0:
            item = dict(ing)
            item["quantity"] = needed
            item["category"] = category
            if match is not None:
                # What this line was reconciled against, and how sure the
                # matcher was. Reported even on a confident match: the
                # user is about to shop from this list, and "we took 1 lb
                # off because you already have X" is only checkable if X
                # is named.
                item["matched_item_name"] = match.name
                item["match_confidence"] = match_confidence
            if unreconciled_unit is not None:
                # Surfaced in the UI so the user can see WHY a line they
                # thought they had stock for is still on the list.
                item["needs_review"] = (
                    f"You have some {match.name} on hand, but it's tracked in {unreconciled_unit} "
                    f"and this recipe asks for {ing['unit']} -- those can't be compared automatically, "
                    f"so the full amount is listed. Check before buying."
                )
            remaining.append(item)
    return remaining


def compute_grocery_list(db: Session, meal_plan: MealPlan) -> list[dict]:
    """Backlog B5.1: an entry with `leftover_of_entry_id` set is excluded
    from its OWN ingredient contribution -- the origin entry it's drawing
    from already contributes ingredients scaled to the combined servings
    across the whole cook event (the household is expected to set the
    origin entry's `servings` to the total it actually cooked, not just
    that one slot's portion -- see MealPlanEntry.leftover_of_entry_id's
    model docstring). Including a leftover entry's ingredients again here
    would double-count the grocery need for a meal that was never
    separately cooked."""
    ingredient_lists = []
    for entry in meal_plan.entries:
        if entry.is_skipped or entry.recipe is None or entry.leftover_of_entry_id is not None:
            continue
        recipe = entry.recipe
        base_ingredients = [
            {"ingredient_name": i.ingredient_name, "quantity": i.quantity, "unit": i.unit} for i in recipe.ingredients
        ]
        scaled = recipe_service.scale_ingredients(base_ingredients, recipe.default_servings, entry.servings)
        ingredient_lists.append(scaled)

    aggregated = aggregate_ingredients(ingredient_lists)
    inventory_items = db.query(InventoryItem).all()
    household = get_household_preferences(db)
    pantry_staples = (household.pantry_staples if household else []) or []
    return subtract_inventory(
        aggregated,
        inventory_items,
        pantry_staples,
        ingredient_resolution_service.load_transformation_words(db),
    )


def compute_nutrition_summary(meal_plan: MealPlan) -> dict:
    """Backlog B1.4 -- per-day and week nutrition totals over a saved
    plan's non-skipped, recipe-assigned entries, for comparison against
    dri_service's per-member daily targets (wired up in the router, not
    here, since this function has no DB session and no member data --
    it only needs the plan itself).

    Each contributing entry adds its recipe's nutrition dict EXACTLY
    ONCE, regardless of entry.servings. Recipe.nutrition is always
    per-serving (see food_data_service.compute_recipe_nutrition, which
    divides by default_servings) -- scaling a recipe up to feed more
    people changes how much gets cooked/deducted from inventory
    (compute_grocery_list above), not the per-serving figure itself, and
    the per-serving figure is exactly the right unit to sum for a
    per-person daily total. Known simplification, stated plainly: this
    assumes every household member eats exactly one serving of every
    planned meal -- there is no per-person meal-attendance or portion
    tracking anywhere in this schema to do better than that.
    """
    day_totals: dict[int, dict[str, float]] = {}
    day_entry_count: dict[int, int] = {}
    day_contributing_count: dict[int, int] = {}

    for entry in meal_plan.entries:
        if entry.is_skipped or entry.recipe is None:
            continue
        day = entry.day_of_week
        day_entry_count[day] = day_entry_count.get(day, 0) + 1
        nutrition = entry.recipe.nutrition or {}
        if not nutrition:
            continue
        day_contributing_count[day] = day_contributing_count.get(day, 0) + 1
        totals = day_totals.setdefault(day, {})
        for key, value in nutrition.items():
            if value is None:
                continue
            totals[key] = totals.get(key, 0) + value

    days = [
        {
            "day_of_week": day,
            "entry_count": day_entry_count[day],
            "contributing_entry_count": day_contributing_count.get(day, 0),
            "totals": {k: round(v, 1) for k, v in day_totals.get(day, {}).items()},
        }
        for day in sorted(day_entry_count)
    ]

    week_totals: dict[str, float] = {}
    for totals in day_totals.values():
        for key, value in totals.items():
            week_totals[key] = week_totals.get(key, 0) + value

    return {"days": days, "week_totals": {k: round(v, 1) for k, v in week_totals.items()}}
