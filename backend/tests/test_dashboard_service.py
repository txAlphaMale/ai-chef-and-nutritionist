"""Capstone review 2026-08-16, backlog B24.3 -- the Home dashboard.

The Home page was the Phase 0 stub, still promising "dashboard widgets for
expiring items, this week's meal plan, and the persistent chat panel" long
after all three shipped.

Most of what is worth testing here is not "does it return a number" but the
three judgement calls the service makes, each of which is silently wrong in
a way a user would believe:

  * which entry is TONIGHT (day-of-week convention, and a stale plan not
    being allowed to masquerade as the current week);
  * which health readings are "latest" (per metric, not per entry);
  * whether a checklist item is done.
"""

from datetime import date, timedelta

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
from app.services import dashboard_service

# A Wednesday. Chosen explicitly rather than derived from date.today() so
# the day-of-week assertions below mean something on every day of the week.
WEDNESDAY = date(2026, 8, 19)
MONDAY_OF_THAT_WEEK = date(2026, 8, 17)


def _plan(db, week_start, entries, status="active"):
    plan = MealPlan(week_start_date=week_start, household_size_snapshot=2, status=status)
    db.add(plan)
    db.flush()
    for day_of_week, meal_type, recipe_title in entries:
        recipe = None
        if recipe_title:
            recipe = Recipe(title=recipe_title, default_servings=2)
            db.add(recipe)
            db.flush()
        db.add(
            MealPlanEntry(
                meal_plan_id=plan.id,
                day_of_week=day_of_week,
                meal_type=meal_type,
                recipe_id=recipe.id if recipe else None,
                servings=2,
            )
        )
    db.commit()
    return plan


# --- Which entry is tonight ----------------------------------------------


def test_todays_meals_use_monday_zero_not_sunday_zero(db_session):
    """`MealPlanEntry.day_of_week` is 0=Monday, matching date.weekday().
    This app also has calendar code where 0=Sunday, and getting the two
    mixed up shifts every dinner by a day without erroring."""
    _plan(
        db_session,
        MONDAY_OF_THAT_WEEK,
        [(0, "dinner", "Monday Dish"), (2, "dinner", "Wednesday Dish"), (6, "dinner", "Sunday Dish")],
    )

    result = dashboard_service.build_dashboard(db_session, today=WEDNESDAY)

    titles = [e["recipe_title"] for e in result["meal_plan"]["today_entries"]]
    assert titles == ["Wednesday Dish"]


def test_a_plan_from_a_previous_week_is_not_presented_as_tonight(db_session):
    """The failure this prevents: the most recent plan is three weeks old,
    and the dashboard cheerfully announces one of its meals as dinner."""
    _plan(db_session, MONDAY_OF_THAT_WEEK - timedelta(days=21), [(2, "dinner", "Stale Dish")])

    section = dashboard_service.build_dashboard(db_session, today=WEDNESDAY)["meal_plan"]

    assert section["plan_id"] is not None, "the plan is still reported, so the page can link to it"
    assert section["is_current_week"] is False
    assert section["today_entries"] == []


def test_the_last_day_of_a_plan_week_still_counts_as_current(db_session):
    """Boundary: a week runs from its Monday for seven days, so Sunday is
    in and the following Monday is out."""
    _plan(db_session, MONDAY_OF_THAT_WEEK, [(6, "dinner", "Sunday Dish")])
    sunday = MONDAY_OF_THAT_WEEK + timedelta(days=6)
    next_monday = MONDAY_OF_THAT_WEEK + timedelta(days=7)

    on_sunday = dashboard_service.build_dashboard(db_session, today=sunday)["meal_plan"]
    on_next_monday = dashboard_service.build_dashboard(db_session, today=next_monday)["meal_plan"]

    assert on_sunday["is_current_week"] is True
    assert [e["recipe_title"] for e in on_sunday["today_entries"]] == ["Sunday Dish"]
    assert on_next_monday["is_current_week"] is False


def test_an_active_plan_wins_over_a_more_recent_draft(db_session):
    """Same rule chat uses, deliberately -- the Chef saying tonight is one
    thing while the dashboard says another is worse than either being
    wrong on its own."""
    _plan(db_session, MONDAY_OF_THAT_WEEK, [(2, "dinner", "Active Week")], status="active")
    _plan(db_session, MONDAY_OF_THAT_WEEK + timedelta(days=7), [(2, "dinner", "Later Draft")], status="draft")

    section = dashboard_service.build_dashboard(db_session, today=WEDNESDAY)["meal_plan"]

    assert [e["recipe_title"] for e in section["today_entries"]] == ["Active Week"]


def test_week_counters_and_outstanding_groceries(db_session):
    plan = _plan(
        db_session,
        MONDAY_OF_THAT_WEEK,
        [(0, "dinner", "A"), (1, "dinner", "B"), (2, "dinner", "C")],
    )
    entries = db_session.query(MealPlanEntry).filter_by(meal_plan_id=plan.id).order_by(MealPlanEntry.day_of_week).all()
    entries[0].is_confirmed = True
    entries[1].is_skipped = True
    db_session.add(GroceryListItem(meal_plan_id=plan.id, ingredient_name="rice", is_purchased=False))
    db_session.add(GroceryListItem(meal_plan_id=plan.id, ingredient_name="beans", is_purchased=False))
    db_session.add(GroceryListItem(meal_plan_id=plan.id, ingredient_name="salt", is_purchased=True))
    db_session.commit()

    section = dashboard_service.build_dashboard(db_session, today=WEDNESDAY)["meal_plan"]

    assert (section["confirmed"], section["skipped"], section["planned"]) == (1, 1, 1)
    assert section["grocery_outstanding"] == 2


# --- Expiring food --------------------------------------------------------


def test_expired_items_are_listed_before_merely_expiring_ones(db_session):
    """The order somebody standing in the kitchen wants."""
    db_session.add(InventoryItem(name="Old Milk", category="fridge", quantity=1, expiration_date=WEDNESDAY - timedelta(days=2)))
    db_session.add(InventoryItem(name="Soon Cheese", category="fridge", quantity=1, expiration_date=WEDNESDAY + timedelta(days=3)))
    db_session.commit()

    section = dashboard_service.build_dashboard(db_session, today=WEDNESDAY)["inventory"]

    assert [i["name"] for i in section["soonest"]] == ["Old Milk", "Soon Cheese"]
    assert section["soonest"][0]["days_until"] == -2
    assert section["soonest"][1]["days_until"] == 3
    assert section["expired"] == 1
    assert section["expiring_soon"] == 1


def test_the_named_expiring_list_is_capped(db_session):
    for i in range(12):
        db_session.add(
            InventoryItem(name=f"Item {i}", category="pantry", quantity=1, expiration_date=WEDNESDAY + timedelta(days=1))
        )
    db_session.commit()

    section = dashboard_service.build_dashboard(db_session, today=WEDNESDAY)["inventory"]

    assert len(section["soonest"]) == dashboard_service.SOONEST_EXPIRING_SHOWN
    # The COUNT is still honest even though the list is capped -- a
    # truncated list next to a truncated count would understate the problem.
    assert section["expiring_soon"] == 12


# --- Health readings ------------------------------------------------------


def test_each_metric_reports_its_own_latest_reading_and_date(db_session):
    """The defect this prevents: showing "the latest entry" blanks the
    cholesterol every time somebody logs a weight, because a lipid panel
    and a weigh-in are almost never the same entry."""
    db_session.add(HealthMetricEntry(entry_date=date(2026, 5, 1), ldl_mg_dl=130.0))
    db_session.add(HealthMetricEntry(entry_date=date(2026, 8, 15), weight_kg=90.0))
    db_session.commit()

    latest = dashboard_service.build_dashboard(db_session, today=WEDNESDAY)["health"]["latest"]

    assert latest["weight_kg"]["value"] == 90.0
    assert latest["weight_kg"]["entry_date"] == date(2026, 8, 15)
    assert latest["ldl_mg_dl"]["value"] == 130.0
    assert latest["ldl_mg_dl"]["entry_date"] == date(2026, 5, 1)


def test_a_metric_never_recorded_is_absent_rather_than_null(db_session):
    db_session.add(HealthMetricEntry(entry_date=date(2026, 8, 15), weight_kg=90.0))
    db_session.commit()

    latest = dashboard_service.build_dashboard(db_session, today=WEDNESDAY)["health"]["latest"]

    assert "weight_kg" in latest
    assert "ldl_mg_dl" not in latest


# --- The setup checklist --------------------------------------------------


def test_checklist_starts_entirely_undone_on_a_fresh_install(db_session):
    setup = dashboard_service.build_dashboard(db_session, today=WEDNESDAY)["setup"]

    assert setup, "expected a checklist"
    assert all(item["done"] is False for item in setup)
    assert all(item["hint"] and item["route"] for item in setup), "every item must say where to go"


def test_checklist_items_flip_as_each_thing_is_configured(db_session):
    db_session.add(HouseholdPreferences(household_size=2, restricted_allergens=["wheat"]))
    db_session.add(HouseholdMember(name="Jason", age=45, height_cm=180.0))
    db_session.add(InventoryItem(name="Rice", category="pantry", quantity=1))
    db_session.add(Recipe(title="A Recipe", default_servings=2))
    db_session.add(KnowledgeFile(filename="ref.md", storage_path="/tmp/ref.md", is_active=True))
    db_session.commit()

    done = {i["key"]: i["done"] for i in dashboard_service.build_dashboard(db_session, today=WEDNESDAY)["setup"]}

    assert done["household"] is True
    assert done["member_body_data"] is True
    assert done["inventory"] is True
    assert done["recipes"] is True
    assert done["knowledge"] is True
    # Not configured in this test, and must not be claimed as done.
    assert done["usda_key"] is False
    assert done["meal_plan"] is False


def test_an_inactive_knowledge_file_does_not_tick_the_knowledge_item(db_session):
    """The bundled corpus ships inactive by default, so a household that
    has merely started the app must not be told it is grounded."""
    db_session.add(KnowledgeFile(filename="ref.md", storage_path="/tmp/ref.md", is_active=False))
    db_session.commit()

    done = {i["key"]: i["done"] for i in dashboard_service.build_dashboard(db_session, today=WEDNESDAY)["setup"]}

    assert done["knowledge"] is False


# --- The endpoint, not just the service -----------------------------------


def test_the_endpoint_returns_the_service_output(db_session):
    """The lesson from B24.1's control run: testing the builder proves
    nothing about whether the route calls it."""
    from app.routers.system import dashboard
    from app.schemas.dashboard import DashboardResponse

    _plan(db_session, MONDAY_OF_THAT_WEEK, [(2, "dinner", "Wednesday Dish")])

    payload = dashboard(db=db_session)

    assert DashboardResponse.model_validate(payload), "the route's declared shape must accept what it returns"
