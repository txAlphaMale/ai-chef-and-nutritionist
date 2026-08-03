"""Persistent chat business logic: building the live app-state context
(household, active meal plan, inventory) injected alongside the
main_chef system prompt, and defensively parsing the model's response
into a conversational reply plus a list of proposed, confirmable
actions (e.g. "deduct 1 cup of lentils", "mark Tuesday's dinner
skipped").

Actions are deliberately NOT executed by this module -- routers/chat.py
returns them to the frontend as proposals, and the frontend calls the
*existing* inventory/meal-plan endpoints directly when the user clicks
confirm (see routers/chat.py's ACTION_ENDPOINTS docstring). This keeps
a single source of truth for what an action actually does rather than
duplicating that logic here, matching the preview-then-confirm pattern
used everywhere else in this app (vision intake, recipe import, meal
plan generation).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import HouseholdPreferences, InventoryItem, MealPlan, Recipe
from app.services import health_service
from app.services.recipe_service import _extract_json_object, _safe_float, _safe_int, coerce_recipe_fields

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

ALLOWED_ACTION_TYPES = {
    "inventory_deduct",
    "inventory_update",
    "inventory_add",
    "meal_plan_confirm_entry",
    "meal_plan_skip_entry",
    "recipe_update_proposal",
}

# recipe_update_proposal carries a "mode" field ("variant", the default,
# or "overwrite") so the model proposes whichever the user asked for --
# "save this as a new version" vs. "just update the recipe" / "replace it" /
# "overwrite it". The frontend (ChatWidget.jsx) still requires an explicit
# confirm click either way (same as every other action type here), but adds
# an extra native confirm() prompt specifically for "overwrite" -- this
# surface has no RecipeForm review step the way the recipe-scoped chat does,
# so that one extra deliberate step is the safeguard against a stray click
# silently replacing a recipe's content.

CATEGORY_VALUES = {"pantry", "fridge", "freezer", "produce", "spice", "other"}


# --- Context building ----------------------------------------------------


# Constrained-decoding schema for a chat turn: a conversational reply plus
# a list of proposed, confirmable actions.
#
# Built by hand rather than from a Pydantic model because the action list
# is a discriminated union of six shapes that share no common field set,
# and expressing that as a single model would mean either a permissive
# "everything optional" object (which constrains nothing useful) or six
# nested models whose $refs have to be flattened for the grammar builder.
# A hand-written `anyOf` says exactly what is meant and stays readable
# next to ALLOWED_ACTION_TYPES above it.
#
# `_coerce_action` below still validates everything this admits -- the
# schema stops the model emitting malformed JSON, it does not stop it
# emitting a plausible-looking action referencing an entry_id that does
# not exist. Those are different problems and both need handling.
def _action_schema() -> dict:
    common = {
        "type": {"type": "string", "enum": sorted(ALLOWED_ACTION_TYPES)},
        "description": {"type": "string"},
    }
    return {
        "type": "object",
        "properties": {
            **common,
            "ingredient_name": {"type": ["string", "null"]},
            "name": {"type": ["string", "null"]},
            "quantity": {"type": ["number", "null"]},
            "unit": {"type": ["string", "null"]},
            "category": {"type": ["string", "null"], "enum": [*sorted(CATEGORY_VALUES), None]},
            "is_priority": {"type": ["boolean", "null"]},
            "priority_note": {"type": ["string", "null"]},
            "meal_plan_id": {"type": ["integer", "null"]},
            "entry_id": {"type": ["integer", "null"]},
            "target_recipe_id": {"type": ["integer", "null"]},
            "mode": {"type": ["string", "null"], "enum": ["variant", "overwrite", None]},
            "variant_label": {"type": ["string", "null"]},
            "recipe": {"type": ["object", "null"]},
        },
        "required": ["type", "description"],
    }


CHAT_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string"},
        "actions": {"type": "array", "items": _action_schema()},
    },
    "required": ["reply", "actions"],
}

# A conversational reply plus any proposed actions. A recipe_update_proposal
# carries a whole recipe, so this needs real room.
CHAT_RESPONSE_TOKENS = 2000


def get_relevant_meal_plan(db: Session) -> MealPlan | None:
    """The plan the chat should reference: the active one if there is
    one, else the most recently created (by week_start_date) plan at
    all, so chat is still useful before a plan's status is manually
    flipped to "active"."""
    active = db.query(MealPlan).filter_by(status="active").order_by(MealPlan.week_start_date.desc()).first()
    if active is not None:
        return active
    return db.query(MealPlan).order_by(MealPlan.week_start_date.desc()).first()


def build_chat_context(db: Session, query: str | None = None, inventory_limit: int = 80) -> dict:
    """`query` -- when given (routers/chat.py passes the user's actual
    message) -- grounds the reply in relevant knowledge-file content via
    retrieval (health_service.build_knowledge_context /
    knowledge_service.search_knowledge). A better fit for retrieval than
    meal-plan generation's synthetic query: a real user question IS the
    query."""
    household = db.query(HouseholdPreferences).first()
    plan = get_relevant_meal_plan(db)
    inventory_items = db.query(InventoryItem).order_by(InventoryItem.name).limit(inventory_limit).all()
    # id+title only -- just enough for the model to reference an existing
    # recipe by a real id in a recipe_update_proposal action (see
    # ALLOWED_ACTION_TYPES above); the full recipe content isn't needed
    # here since a proposal always supplies the complete post-edit recipe.
    recipe_catalog = db.query(Recipe.id, Recipe.title).order_by(Recipe.title).limit(200).all()

    plan_entries = []
    if plan is not None:
        for e in sorted(plan.entries, key=lambda e: (e.day_of_week, e.meal_type)):
            plan_entries.append(
                {
                    "entry_id": e.id,
                    "day_of_week": e.day_of_week,
                    "meal_type": e.meal_type,
                    "recipe_title": e.recipe.title if e.recipe else None,
                    "servings": e.servings,
                    "is_confirmed": e.is_confirmed,
                    "is_skipped": e.is_skipped,
                }
            )

    knowledge_context = health_service.build_knowledge_context(db, query) if query else ""

    return {
        "household_size": household.household_size if household else 2,
        "dietary_restrictions": (household.dietary_restrictions if household else []) or [],
        "meal_plan_id": plan.id if plan else None,
        "meal_plan_week_start": str(plan.week_start_date) if plan else None,
        "meal_plan_entries": plan_entries,
        "knowledge_context": knowledge_context,
        "recipe_catalog": [{"id": r.id, "title": r.title} for r in recipe_catalog],
        "inventory_items": [
            {
                "id": i.id,
                "name": i.name,
                "quantity": i.quantity,
                "unit": i.unit,
                "category": i.category,
                "is_priority": i.is_priority,
            }
            for i in inventory_items
        ],
    }


# --- System prompt construction -------------------------------------------

CHAT_CONTEXT_TEMPLATE = """\

You are now chatting with the household in an ongoing, running conversation \
inside the app (not a one-off request). Respond with ONLY a JSON object (no \
other text, no markdown fences) with these keys:
- "reply": string, your natural conversational response
- "actions": array (empty if nothing actionable) of proposed app actions, \
each matching exactly one of these shapes:
  - {{"type": "inventory_deduct", "ingredient_name": string, "quantity": \
number or null, "description": short string}}
  - {{"type": "inventory_update", "ingredient_name": string, "quantity": \
number or null, "is_priority": true/false or null, "priority_note": string \
or null, "category": string or null, "unit": string or null, \
"description": short string}}
  - {{"type": "inventory_add", "name": string, "quantity": number or null, \
"unit": string or null, "category": one of pantry/fridge/freezer/produce/ \
spice/other, "description": short string}}
  - {{"type": "meal_plan_confirm_entry", "meal_plan_id": integer, \
"entry_id": integer, "description": short string}}
  - {{"type": "meal_plan_skip_entry", "meal_plan_id": integer, "entry_id": \
integer, "description": short string}}
  - {{"type": "recipe_update_proposal", "target_recipe_id": integer (must \
be an id from the recipe list below), "mode": "variant" or "overwrite" \
(see guidance below), "variant_label": short string (2-4 words, e.g. \
"Gluten-Free", "Dairy-Free" -- required if mode is "variant", ignored if \
"overwrite"), "recipe": the ENTIRE recipe as it should look after the \
change, every field, in this exact shape: {{"title": string, \
"description": string or null, "default_servings": integer, \
"prep_time_minutes": integer or null, "cook_time_minutes": integer or \
null, "instructions": array of strings, "ingredients": array of objects \
with "ingredient_name", "quantity" (number or null), "unit" (string or \
null), "prep_note" (string or null), "nutrition": object with \
best-effort per-serving numeric estimates, "tags": array of short \
lowercase tags, "tips": array of strings}}, "description": short string}}

Only propose an action when the user's message clearly implies one -- e.g. \
"we're out of milk" could be inventory_update (set quantity to 0) or \
inventory_deduct; "we skipped taco night" is meal_plan_skip_entry for that \
specific entry; "we made the lentil soup" is meal_plan_confirm_entry (and \
usually also an inventory_deduct per ingredient, if you know the recipe); \
"make the lentil soup gluten-free" (a change to an EXISTING recipe, named \
or clearly implied) is recipe_update_proposal. Default to "mode": \
"variant" (keeps the original recipe untouched, saves the change as a new \
linked recipe) UNLESS the user clearly asks to update/replace/overwrite \
the existing recipe itself rather than save a copy -- phrases like "just \
update it", "replace the recipe", "overwrite it", "don't make a new one" \
mean "mode": "overwrite" against that same target_recipe_id. If it's \
ambiguous whether they want a new version or to replace the existing one, \
default to "variant" and you can mention in "reply" that they can ask you \
to overwrite instead if that's what they meant. NEVER invent an entry_id, \
meal_plan_id, or target_recipe_id that isn't listed below -- if you can't \
tell which entry or recipe the user means, ask a clarifying question in \
"reply" instead of guessing. Each action's "description" is a short \
human-readable label for a confirm button (e.g. "Mark milk as out of \
stock"), not a repeat of the reply -- for recipe_update_proposal, make it \
clear which mode it is, e.g. "Save as a new Gluten-Free variant" vs. \
"Overwrite Traditional Beef Stew with this change". Actions are proposals \
only -- nothing happens until the user confirms them in the UI, so it's \
fine to suggest something reasonable.

Household: {household_size} people. Dietary restrictions: {dietary_restrictions}.
{knowledge_section}
Current meal plan{plan_label}:
{plan_entries}

Current inventory (id, name, quantity, unit, category{priority_note}):
{inventory_lines}

Known recipes (id: title) -- reference these by id for recipe_update_proposal:
{recipe_catalog_lines}
"""


def _format_plan_entries(entries: list[dict]) -> str:
    if not entries:
        return "(no meal plan entries to reference)"
    lines = []
    for e in entries:
        status = "confirmed" if e["is_confirmed"] else "skipped" if e["is_skipped"] else "planned"
        recipe = e["recipe_title"] or "(no recipe assigned)"
        lines.append(
            f"- entry_id={e['entry_id']}: {DAY_NAMES[e['day_of_week']]} {e['meal_type']} -- "
            f"{recipe}, {e['servings']} servings ({status})"
        )
    return "\n".join(lines)


def _format_inventory(items: list[dict]) -> str:
    if not items:
        return "(inventory is empty)"
    lines = []
    for i in items:
        priority = " [priority]" if i.get("is_priority") else ""
        lines.append(f"- id={i['id']}: {i['name']}, {i['quantity']} {i['unit'] or ''} ({i['category']}){priority}")
    return "\n".join(lines)


def _format_recipe_catalog(recipes: list[dict]) -> str:
    if not recipes:
        return "(no recipes saved yet)"
    return "\n".join(f"- {r['id']}: {r['title']}" for r in recipes)


def _format_knowledge_section(knowledge_context: str | None) -> str:
    if not knowledge_context:
        return ""
    return (
        "\nRelevant reference material the household has provided (a nutritionist's "
        "guidance, a specific diet plan, etc.), retrieved for this message -- follow it "
        "where relevant and it doesn't conflict with the dietary restrictions above:\n" + knowledge_context + "\n"
    )


def build_chat_system_prompt(base_prompt: str, context: dict) -> str:
    dietary = ", ".join(context.get("dietary_restrictions") or []) or "none specified"
    plan_id = context.get("meal_plan_id")
    plan_label = f" (id={plan_id}, week of {context.get('meal_plan_week_start')})" if plan_id else " (none yet)"

    context_block = CHAT_CONTEXT_TEMPLATE.format(
        household_size=context.get("household_size", 2),
        dietary_restrictions=dietary,
        knowledge_section=_format_knowledge_section(context.get("knowledge_context")),
        plan_label=plan_label,
        plan_entries=_format_plan_entries(context.get("meal_plan_entries") or []),
        priority_note="",
        inventory_lines=_format_inventory(context.get("inventory_items") or []),
        recipe_catalog_lines=_format_recipe_catalog(context.get("recipe_catalog") or []),
    )
    return f"{base_prompt}\n{context_block}"


# --- Response parsing ------------------------------------------------------


def parse_chat_response(raw_text: str) -> dict:
    """Defensively extracts {"reply", "actions"} from raw model output.
    Chat is the most free-form AI feature in this app -- conversational
    text is far more likely to break strict-JSON-only instructions than
    a structured extraction task is. If parsing fails entirely (no JSON
    object found, or no "reply" key), the raw text is used as the reply
    verbatim with an empty actions list rather than showing the user
    nothing -- a broken action proposal is an acceptable degradation, a
    broken reply is not."""
    data = _extract_json_object(raw_text)
    if not isinstance(data, dict) or "reply" not in data:
        return {"reply": (raw_text or "").strip(), "actions": []}

    reply = str(data.get("reply") or "").strip() or (raw_text or "").strip()
    raw_actions = data.get("actions")
    actions = []
    if isinstance(raw_actions, list):
        for a in raw_actions:
            coerced = _coerce_action(a)
            if coerced is not None:
                actions.append(coerced)
    return {"reply": reply, "actions": actions}


def _coerce_action(a) -> dict | None:
    if not isinstance(a, dict):
        return None
    action_type = a.get("type")
    if action_type not in ALLOWED_ACTION_TYPES:
        return None
    description = str(a.get("description") or "").strip() or "Apply this change"

    if action_type in ("inventory_deduct", "inventory_update"):
        name = str(a.get("ingredient_name") or "").strip()
        if not name:
            return None
        result = {
            "type": action_type,
            "ingredient_name": name,
            "quantity": _safe_float(a.get("quantity")),
            "description": description,
        }
        if action_type == "inventory_update":
            result["is_priority"] = a.get("is_priority") if isinstance(a.get("is_priority"), bool) else None
            result["priority_note"] = a.get("priority_note") or None
            result["category"] = a.get("category") or None
            result["unit"] = a.get("unit") or None
        return result

    if action_type == "inventory_add":
        name = str(a.get("name") or "").strip()
        if not name:
            return None
        category = str(a.get("category") or "other").lower()
        if category not in CATEGORY_VALUES:
            category = "other"
        return {
            "type": action_type,
            "name": name,
            "quantity": _safe_float(a.get("quantity")),
            "unit": a.get("unit") or None,
            "category": category,
            "description": description,
        }

    if action_type in ("meal_plan_confirm_entry", "meal_plan_skip_entry"):
        meal_plan_id = _safe_int(a.get("meal_plan_id"))
        entry_id = _safe_int(a.get("entry_id"))
        if meal_plan_id is None or entry_id is None:
            return None
        return {
            "type": action_type,
            "meal_plan_id": meal_plan_id,
            "entry_id": entry_id,
            "description": description,
        }

    if action_type == "recipe_update_proposal":
        target_recipe_id = _safe_int(a.get("target_recipe_id"))
        recipe_data = a.get("recipe")
        if target_recipe_id is None or not isinstance(recipe_data, dict) or not recipe_data.get("title"):
            return None
        mode = a.get("mode") if a.get("mode") in ("variant", "overwrite") else "variant"
        coerced_recipe = coerce_recipe_fields(recipe_data)
        coerced_recipe["source"] = "chat_variant" if mode == "variant" else "chat_modified"
        return {
            "type": action_type,
            "target_recipe_id": target_recipe_id,
            "mode": mode,
            "variant_label": str(a.get("variant_label") or "").strip() or "Chat Variant",
            "recipe": coerced_recipe,
            "description": description,
        }

    return None
