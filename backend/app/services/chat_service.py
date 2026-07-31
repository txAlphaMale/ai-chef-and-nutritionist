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

from app.models import HouseholdPreferences, InventoryItem, MealPlan
from app.services.recipe_service import _extract_json_object, _safe_float, _safe_int

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

ALLOWED_ACTION_TYPES = {
    "inventory_deduct",
    "inventory_update",
    "inventory_add",
    "meal_plan_confirm_entry",
    "meal_plan_skip_entry",
}

CATEGORY_VALUES = {"pantry", "fridge", "freezer", "produce", "spice", "other"}


# --- Context building ----------------------------------------------------


def get_relevant_meal_plan(db: Session) -> MealPlan | None:
    """The plan the chat should reference: the active one if there is
    one, else the most recently created (by week_start_date) plan at
    all, so chat is still useful before a plan's status is manually
    flipped to "active"."""
    active = db.query(MealPlan).filter_by(status="active").order_by(MealPlan.week_start_date.desc()).first()
    if active is not None:
        return active
    return db.query(MealPlan).order_by(MealPlan.week_start_date.desc()).first()


def build_chat_context(db: Session, inventory_limit: int = 80) -> dict:
    household = db.query(HouseholdPreferences).first()
    plan = get_relevant_meal_plan(db)
    inventory_items = db.query(InventoryItem).order_by(InventoryItem.name).limit(inventory_limit).all()

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

    return {
        "household_size": household.household_size if household else 2,
        "dietary_restrictions": (household.dietary_restrictions if household else []) or [],
        "meal_plan_id": plan.id if plan else None,
        "meal_plan_week_start": str(plan.week_start_date) if plan else None,
        "meal_plan_entries": plan_entries,
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

Only propose an action when the user's message clearly implies one -- e.g. \
"we're out of milk" could be inventory_update (set quantity to 0) or \
inventory_deduct; "we skipped taco night" is meal_plan_skip_entry for that \
specific entry; "we made the lentil soup" is meal_plan_confirm_entry (and \
usually also an inventory_deduct per ingredient, if you know the recipe). \
NEVER invent an entry_id or meal_plan_id that isn't listed below -- if you \
can't tell which entry the user means, ask a clarifying question in "reply" \
instead of guessing. Each action's "description" is a short human-readable \
label for a confirm button (e.g. "Mark milk as out of stock"), not a repeat \
of the reply. Actions are proposals only -- nothing happens until the user \
confirms them in the UI, so it's fine to suggest something reasonable.

Household: {household_size} people. Dietary restrictions: {dietary_restrictions}.

Current meal plan{plan_label}:
{plan_entries}

Current inventory (id, name, quantity, unit, category{priority_note}):
{inventory_lines}
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


def build_chat_system_prompt(base_prompt: str, context: dict) -> str:
    dietary = ", ".join(context.get("dietary_restrictions") or []) or "none specified"
    plan_id = context.get("meal_plan_id")
    plan_label = f" (id={plan_id}, week of {context.get('meal_plan_week_start')})" if plan_id else " (none yet)"

    context_block = CHAT_CONTEXT_TEMPLATE.format(
        household_size=context.get("household_size", 2),
        dietary_restrictions=dietary,
        plan_label=plan_label,
        plan_entries=_format_plan_entries(context.get("meal_plan_entries") or []),
        priority_note="",
        inventory_lines=_format_inventory(context.get("inventory_items") or []),
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

    return None
