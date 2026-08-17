"""The Home page's data, assembled once on the server.

Capstone review 2026-08-16, backlog B24.3. The Home page had been the
Phase 0 stub since Phase 0 -- backend status, household size, and the
sentence "More here as phases land: dashboard widgets for expiring items,
this week's meal plan, and the persistent chat panel." All three of those
landed months ago. The front door of the app was a placeholder advertising
features that had already shipped.

**Why one endpoint rather than the page calling five.** Everything here
already exists as its own endpoint, and the obvious cheap move was to have
`HomePage.jsx` fetch the digest, the plan list, the grocery list, the
recipe list and the metrics list and stitch them together. That would have
meant five round trips on the app's most-visited page, one of which (the
recipe list) is 64 KB of data to extract a count from -- and it would have
put the "which of these is today's dinner" logic in the browser, where the
browser's clock and the server's clock disagree by up to a day for anyone
not in UTC. A purpose-built read is one trip, a few KB, and one definition
of "today".

**Nothing here computes anything new.** Every number is read from the
services that already own it -- `inventory_service.get_expiring_digest`,
`recall_service.list_active_alerts`, the meal-plan and health tables. If a
figure on the dashboard disagrees with the page it came from, this file is
wrong, not the page.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session, selectinload

from app.models import (
    GroceryListItem,
    HealthMetricEntry,
    HouseholdMember,
    HouseholdPreferences,
    InventoryItem,
    KnowledgeFile,
    MealPlan,
    MealPlanEntry,
    Recipe,
)
from app.services import inventory_service, recall_service, settings_service

# How many expiring items the dashboard names outright. The digest banner
# already reports the counts app-wide; the point of naming a few here is to
# answer "what do I cook tonight", which needs the actual food, not a
# number. Past a handful this stops being a prompt and starts being the
# Inventory page.
SOONEST_EXPIRING_SHOWN = 5

EXPIRING_WITHIN_DAYS = 7


def _todays_weekday(today: date) -> int:
    """`MealPlanEntry.day_of_week` is 0=Monday..6=Sunday, which is exactly
    `date.weekday()`. Written out because the app also has calendar code
    where 0=Sunday, and the two have been confused before."""
    return today.weekday()


def _active_plan(db: Session) -> MealPlan | None:
    """The same rule chat uses (`chat_service.get_relevant_meal_plan`):
    the active plan if there is one, else the most recent by week. Kept
    consistent deliberately -- the Chef saying "tonight is the lentil soup"
    and the dashboard showing something else would be worse than either
    being wrong alone."""
    active = (
        db.query(MealPlan)
        .filter_by(status="active")
        .order_by(MealPlan.week_start_date.desc())
        .first()
    )
    if active is not None:
        return active
    return db.query(MealPlan).order_by(MealPlan.week_start_date.desc()).first()


def _plan_section(db: Session, today: date) -> dict:
    plan = _active_plan(db)
    if plan is None:
        return {
            "plan_id": None,
            "week_start_date": None,
            "status": None,
            "is_current_week": False,
            "today_entries": [],
            "planned": 0,
            "confirmed": 0,
            "skipped": 0,
            "grocery_outstanding": 0,
        }

    entries = (
        db.query(MealPlanEntry)
        .options(selectinload(MealPlanEntry.recipe))
        .filter_by(meal_plan_id=plan.id)
        .all()
    )

    # "Is this plan about the week we are actually in" -- shown so a stale
    # plan cannot masquerade as tonight's dinner. A plan's week runs from
    # its Monday for seven days.
    days_since_start = (today - plan.week_start_date).days
    is_current_week = 0 <= days_since_start < 7

    weekday = _todays_weekday(today)
    today_entries = []
    if is_current_week:
        for entry in sorted(
            (e for e in entries if e.day_of_week == weekday),
            key=lambda e: e.meal_type,
        ):
            today_entries.append(
                {
                    "entry_id": entry.id,
                    "meal_type": entry.meal_type,
                    "recipe_id": entry.recipe_id,
                    "recipe_title": entry.recipe.title if entry.recipe else None,
                    "servings": entry.servings,
                    "is_confirmed": entry.is_confirmed,
                    "is_skipped": entry.is_skipped,
                    "is_eating_out": entry.is_eating_out,
                }
            )

    return {
        "plan_id": plan.id,
        "week_start_date": plan.week_start_date,
        "status": plan.status,
        "is_current_week": is_current_week,
        "today_entries": today_entries,
        # Counted over the whole week, not just today -- this is the
        # "how is the week going" line.
        "planned": sum(1 for e in entries if not e.is_confirmed and not e.is_skipped),
        "confirmed": sum(1 for e in entries if e.is_confirmed),
        "skipped": sum(1 for e in entries if e.is_skipped),
        "grocery_outstanding": (
            db.query(func.count(GroceryListItem.id))
            .filter(GroceryListItem.meal_plan_id == plan.id, GroceryListItem.is_purchased.is_(False))
            .scalar()
            or 0
        ),
    }


def _inventory_section(db: Session, today: date) -> dict:
    digest = inventory_service.get_expiring_digest(db, within_days=EXPIRING_WITHIN_DAYS, today=today)
    expired = digest["expired"]
    expiring_soon = digest["expiring_soon"]

    # Expired first, then soonest -- the order somebody standing in the
    # kitchen wants, rather than the order the digest happens to build.
    soonest = []
    for item in [*expired, *expiring_soon][:SOONEST_EXPIRING_SHOWN]:
        soonest.append(
            {
                "id": item.id,
                "name": item.name,
                "category": item.category,
                "expiration_date": item.expiration_date,
                "days_until": (item.expiration_date - today).days if item.expiration_date else None,
            }
        )

    return {
        "total_items": db.query(func.count(InventoryItem.id)).scalar() or 0,
        "expired": len(expired),
        "expiring_soon": len(expiring_soon),
        "within_days": digest["within_days"],
        "soonest": soonest,
    }


def _health_section(db: Session) -> dict:
    """The most recent reading of each tracked value, each with the date it
    was taken -- NOT the most recent entry's fields. A lipid panel and a
    weigh-in almost never happen on the same day, so showing "latest entry"
    would blank the cholesterol every time somebody stepped on a scale."""
    latest: dict[str, dict] = {}
    fields = ("weight_kg", "bmi", "ldl_mg_dl", "hdl_mg_dl", "total_cholesterol_mg_dl", "triglycerides_mg_dl")
    for field in fields:
        column = getattr(HealthMetricEntry, field)
        row = (
            db.query(HealthMetricEntry.entry_date, column)
            .filter(column.isnot(None))
            .order_by(HealthMetricEntry.entry_date.desc())
            .first()
        )
        if row is not None:
            latest[field] = {"value": row[1], "entry_date": row[0]}
    return {"latest": latest, "entry_count": db.query(func.count(HealthMetricEntry.id)).scalar() or 0}


def _setup_checklist(db: Session) -> list[dict]:
    """The first-run checklist, surfaced in the app rather than only in the
    README and the WIKI -- the author asked for the sibling Fiduciary
    project's "new user setup checklist" pattern, and the place a checklist
    is useful is the page you land on.

    Every item is a fast database read. Ollama reachability is deliberately
    NOT one of them, despite being the most useful first-run signal: it is a
    network round trip, this endpoint is on the app's most-visited page, and
    the Settings page already pins connection status above its tab bar where
    it is visible regardless of which tab is open.

    `done` is a fact, never a nag. The frontend hides the whole card once
    every item is done rather than showing a wall of ticks forever.
    """
    prefs = db.query(HouseholdPreferences).first()
    member_with_body_data = (
        db.query(HouseholdMember.id)
        .filter(HouseholdMember.height_cm.isnot(None), HouseholdMember.age.isnot(None))
        .first()
    )
    return [
        {
            "key": "household",
            "label": "Set your household size and dietary restrictions",
            "done": bool(prefs and (prefs.dietary_restrictions or prefs.restricted_allergens or prefs.goals)),
            "hint": "Health > Household. This influences meal plans more than any other setting.",
            "route": "#/health",
        },
        {
            "key": "member_body_data",
            "label": "Add age and height for a household member",
            "done": member_with_body_data is not None,
            "hint": "Health > Members. Unlocks BMI and DRI-based daily nutrient targets.",
            "route": "#/health",
        },
        {
            "key": "usda_key",
            "label": "Add a USDA FoodData Central API key",
            "done": bool(settings_service.get_setting(db, "usda_fdc_api_key")),
            "hint": "Settings > AI & Models. Free. Without it every nutrition figure is an AI estimate.",
            "route": "#/settings",
        },
        {
            "key": "knowledge",
            "label": "Activate a nutrition knowledge file",
            "done": (db.query(func.count(KnowledgeFile.id)).filter(KnowledgeFile.is_active.is_(True)).scalar() or 0) > 0,
            "hint": "Health > Knowledge files. The bundled references ship switched off.",
            "route": "#/health",
        },
        {
            "key": "inventory",
            "label": "Put food in the inventory",
            "done": (db.query(func.count(InventoryItem.id)).scalar() or 0) > 0,
            "hint": "Inventory. Meal planning is built around what you already have.",
            "route": "#/inventory",
        },
        {
            "key": "recipes",
            "label": "Save or import a recipe",
            "done": (db.query(func.count(Recipe.id)).scalar() or 0) > 0,
            "hint": "Recipes. Import from a URL, a PDF, a photo, or a whole bookmarks export.",
            "route": "#/recipes",
        },
        {
            "key": "meal_plan",
            "label": "Generate a weekly meal plan",
            "done": (db.query(func.count(MealPlan.id)).scalar() or 0) > 0,
            "hint": "Meal Plan. Everything above feeds into this.",
            "route": "#/meal-plan",
        },
    ]


def build_dashboard(db: Session, today: date | None = None) -> dict:
    """`today` is injectable so tests are not a hostage to the clock -- the
    same reason `inventory_service.get_expiring_digest` takes one."""
    today = today or date.today()
    prefs = db.query(HouseholdPreferences).first()

    return {
        "today": today,
        "household_size": prefs.household_size if prefs else 2,
        "inventory": _inventory_section(db, today),
        "meal_plan": _plan_section(db, today),
        "recipes": {
            "total": db.query(func.count(Recipe.id)).scalar() or 0,
            "staples": db.query(func.count(Recipe.id)).filter(Recipe.is_staple.is_(True)).scalar() or 0,
        },
        "health": _health_section(db),
        # A count only. RecallBanner is mounted app-wide and shows the
        # detail; repeating it here would be two places to read the same
        # warning, and a safety warning shown twice in different words is
        # worse than one shown once.
        "recalls": {"active": len(recall_service.list_active_alerts(db))},
        "setup": _setup_checklist(db),
    }
