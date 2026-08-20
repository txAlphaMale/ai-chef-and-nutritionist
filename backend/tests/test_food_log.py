"""Backlog B17.1 -- the food log.

The app could say what was PLANNED and had no idea what was EATEN, so
every nutrition figure it showed described an intention. These tests pin
the three things that make the difference between a log that helps and a
log that misleads:

1. `Recipe.nutrition` is PER SERVING and a log row is the TOTAL. That is
   one multiplication, in one place, and getting it wrong produces a
   plausible number that is wrong by a factor of two to six.
2. An entry with no nutrition is COUNTED, never summed as zero. A day of
   unquantified meals must not render as the healthiest day of the week.
3. Confirming a planned meal logs it -- including a leftover portion,
   which deducts no inventory but is certainly eaten.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.models import FoodLogEntry, HouseholdMember, MealPlan, MealPlanEntry, Recipe
from app.services import food_log_service


def _recipe(db, title="Lentil Soup", nutrition=None, provenance="computed", servings=4):
    recipe = Recipe(
        title=title,
        default_servings=servings,
        nutrition=nutrition if nutrition is not None else {"calories": 300.0, "protein_g": 18.0},
        nutrition_provenance=provenance,
    )
    db.add(recipe)
    db.commit()
    db.refresh(recipe)
    return recipe


def _plan_entry(db, recipe=None, *, servings=2, leftover_of=None, meal_type="dinner"):
    plan = db.query(MealPlan).first()
    if plan is None:
        plan = MealPlan(week_start_date=datetime.now(timezone.utc).date(), status="active")
        db.add(plan)
        db.commit()
    entry = MealPlanEntry(
        meal_plan_id=plan.id,
        day_of_week=0,
        meal_type=meal_type,
        recipe_id=recipe.id if recipe else None,
        servings=servings,
        leftover_of_entry_id=leftover_of.id if leftover_of else None,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


# --- The multiplication ---------------------------------------------------


def test_a_log_row_holds_the_total_eaten_not_the_per_serving_figure(db_session):
    """The defect this whole file exists to prevent. `Recipe.nutrition`
    is per serving (food_data_service.compute_recipe_nutrition divides by
    default_servings); eating three servings is 900 calories, not 300."""
    recipe = _recipe(db_session, nutrition={"calories": 300.0, "protein_g": 18.0})

    entry = food_log_service.log_from_recipe(
        db_session, recipe=recipe, servings=3, meal_type="dinner", source="manual"
    )
    db_session.commit()

    assert entry.nutrition["calories"] == 900.0
    assert entry.nutrition["protein_g"] == 54.0


def test_half_a_portion_is_expressible(db_session):
    """`MealPlanEntry.servings` is an int; a log's is a float, because
    half a portion is a normal thing to eat and a field that could not
    hold it would be rounded by the person filling it in."""
    recipe = _recipe(db_session, nutrition={"calories": 300.0})

    entry = food_log_service.log_from_recipe(
        db_session, recipe=recipe, servings=0.5, meal_type="lunch", source="manual"
    )

    assert entry.nutrition["calories"] == 150.0


def test_scaling_refuses_rather_than_assuming_a_portion(db_session):
    assert food_log_service.scale_nutrition({"calories": 300.0}, 0) == {}
    assert food_log_service.scale_nutrition({"calories": 300.0}, -2) == {}
    assert food_log_service.scale_nutrition({"calories": 300.0}, None) == {}
    assert food_log_service.scale_nutrition(None, 2) == {}


def test_one_junk_value_does_not_discard_the_rest_of_the_dict(db_session):
    scaled = food_log_service.scale_nutrition({"calories": 300.0, "protein_g": "lots"}, 2)

    assert scaled == {"calories": 600.0}


# --- Provenance -----------------------------------------------------------


def test_provenance_is_carried_through_unchanged(db_session):
    """Multiplying a per-serving figure by a serving count does not make
    it more trustworthy. A partial recipe yields a partial log row."""
    recipe = _recipe(db_session, nutrition={"calories": 200.0}, provenance="partial")

    entry = food_log_service.log_from_recipe(
        db_session, recipe=recipe, servings=2, meal_type="dinner", source="manual"
    )

    assert entry.nutrition_provenance == "partial"


def test_a_recipe_with_no_nutrition_yields_null_not_a_false_estimate(db_session):
    """NULL means "no nutrition on this entry". Labelling it
    "ai_estimated" would claim something estimated something."""
    recipe = _recipe(db_session, nutrition={}, provenance="ai_estimated")

    entry = food_log_service.log_from_recipe(
        db_session, recipe=recipe, servings=2, meal_type="dinner", source="manual"
    )

    assert entry.nutrition == {}
    assert entry.nutrition_provenance is None


def test_a_days_provenance_is_its_weakest_entry(db_session):
    """A total is exactly as trustworthy as its worst input."""
    assert food_log_service.weakest_provenance(["computed", "computed"]) == "computed"
    assert food_log_service.weakest_provenance(["computed", "partial"]) == "partial"
    assert food_log_service.weakest_provenance(["partial", "ai_estimated", "computed"]) == "ai_estimated"
    assert food_log_service.weakest_provenance([None, None]) is None


# --- Unquantified meals are counted, never zeroed -------------------------


def test_an_unquantified_meal_is_counted_and_not_summed_as_zero(db_session):
    """The failure mode: a day of meals nobody could quantify has totals
    of zero, and a UI showing only totals renders the least-known day as
    the best one. The count is the denominator that stops that."""
    now = datetime.now(timezone.utc)
    entries = [
        FoodLogEntry(eaten_at=now, meal_type="lunch", source="manual", description="Sandwich", nutrition={}),
        FoodLogEntry(
            eaten_at=now,
            meal_type="dinner",
            source="manual",
            description="Lentil Soup",
            nutrition={"calories": 600.0},
            nutrition_provenance="computed",
        ),
    ]

    days = food_log_service.summarize_days(entries)

    assert len(days) == 1
    assert days[0]["entry_count"] == 2
    assert days[0]["unquantified_entries"] == 1
    assert days[0]["nutrition"]["calories"] == 600.0


def test_a_day_of_only_unquantified_meals_reports_the_count_not_a_clean_zero(db_session):
    now = datetime.now(timezone.utc)
    entries = [
        FoodLogEntry(eaten_at=now, meal_type="lunch", source="manual", description="Ate at my sister's"),
        FoodLogEntry(eaten_at=now, meal_type="dinner", source="dining_out", description="Thai place"),
    ]

    day = food_log_service.summarize_days(entries)[0]

    assert day["unquantified_entries"] == 2
    assert day["nutrition"] == {}
    assert day["nutrition_provenance"] is None, "nothing contributed, so there is no label to give"


# --- Day grouping happens in the eater's timezone -------------------------


def test_a_late_dinner_west_of_utc_lands_on_the_right_day(db_session):
    """Stored 2026-08-21T01:00Z, which is 8pm on the 20th in US Central.
    Grouped in UTC this dinner appears on the 21st, moving it off the day
    it was eaten and corrupting both days' totals at once."""
    dinner = FoodLogEntry(
        eaten_at=datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc),
        meal_type="dinner",
        source="manual",
        description="Late dinner",
        nutrition={"calories": 700.0},
        nutrition_provenance="computed",
    )

    utc_grouped = food_log_service.summarize_days([dinner])
    central_grouped = food_log_service.summarize_days([dinner], tz_offset_minutes=-300)

    assert utc_grouped[0]["date"] == "2026-08-21"
    assert central_grouped[0]["date"] == "2026-08-20"


def test_days_come_back_newest_first(db_session):
    base = datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc)
    entries = [
        FoodLogEntry(eaten_at=base + timedelta(days=n), meal_type="lunch", source="manual", description=f"Day {n}")
        for n in range(3)
    ]

    dates = [d["date"] for d in food_log_service.summarize_days(entries)]

    assert dates == ["2026-08-20", "2026-08-19", "2026-08-18"]


# --- Confirming a planned meal logs it ------------------------------------


def test_confirming_a_planned_meal_writes_a_log_row(db_session):
    """The one-click promise. Before B17.1 this endpoint deducted
    inventory and recorded nothing about intake."""
    from app.routers.meal_plan import confirm_meal_plan_entry

    recipe = _recipe(db_session, nutrition={"calories": 400.0})
    entry = _plan_entry(db_session, recipe, servings=2)

    confirm_meal_plan_entry(plan_id=entry.meal_plan_id, entry_id=entry.id, payload=None, db=db_session)

    logged = db_session.query(FoodLogEntry).filter_by(meal_plan_entry_id=entry.id).one()
    assert logged.source == "meal_plan"
    assert logged.description == "Lentil Soup"
    assert logged.servings == 2
    assert logged.nutrition["calories"] == 800.0, "two servings of a 400-calorie recipe"


def test_a_leftover_portion_is_logged_even_though_it_deducts_nothing(db_session):
    """Inventory and intake are different questions and this is where
    they diverge. The origin cook already deducted the ingredients for the
    whole batch, so deducting again would double-count the pantry -- but
    the leftovers were eaten on their own day, and skipping them would
    undercount that day and overcount the cook day."""
    from app.routers.meal_plan import confirm_meal_plan_entry

    recipe = _recipe(db_session, nutrition={"calories": 400.0})
    origin = _plan_entry(db_session, recipe, servings=4)
    leftover = _plan_entry(db_session, recipe, servings=2, leftover_of=origin)

    confirm_meal_plan_entry(plan_id=leftover.meal_plan_id, entry_id=leftover.id, payload=None, db=db_session)

    logged = db_session.query(FoodLogEntry).filter_by(meal_plan_entry_id=leftover.id).one()
    assert logged.nutrition["calories"] == 800.0


def test_confirming_an_empty_slot_invents_no_meal(db_session):
    """An eating-out or empty slot confirms with no recipe. Writing "you
    ate something" from that would put a phantom meal in the history."""
    from app.routers.meal_plan import confirm_meal_plan_entry

    entry = _plan_entry(db_session, None)

    confirm_meal_plan_entry(plan_id=entry.meal_plan_id, entry_id=entry.id, payload=None, db=db_session)

    assert db_session.query(FoodLogEntry).count() == 0


def test_one_plan_slot_can_only_produce_one_log_row(db_session):
    recipe = _recipe(db_session)
    entry = _plan_entry(db_session, recipe)

    first = food_log_service.log_for_confirmed_plan_entry(db_session, entry)
    db_session.commit()
    second = food_log_service.log_for_confirmed_plan_entry(db_session, entry)

    assert first is not None
    assert second is None
    assert db_session.query(FoodLogEntry).count() == 1


# --- The endpoints --------------------------------------------------------


def test_a_manual_entry_needs_something_to_identify_it(db_session):
    from app.schemas.food_log import FoodLogEntryCreate

    with pytest.raises(ValueError):
        FoodLogEntryCreate(meal_type="lunch")
    with pytest.raises(ValueError):
        FoodLogEntryCreate(description="   ")
    with pytest.raises(ValueError):
        FoodLogEntryCreate(description="Toast", servings=0)


def test_the_client_cannot_assert_nutrition_or_provenance(db_session):
    """Same posture RecipeCreate takes. A caller must not be able to
    claim "computed" over numbers nothing computed."""
    from app.schemas.food_log import FoodLogEntryCreate, FoodLogEntryUpdate

    for field in ("nutrition", "nutrition_provenance"):
        assert field not in FoodLogEntryCreate.model_fields
        assert field not in FoodLogEntryUpdate.model_fields
    # And a client cannot re-point an automatic row at a different plan
    # slot, which is what B17.4's adherence view is built on.
    assert "meal_plan_entry_id" not in FoodLogEntryUpdate.model_fields


def test_creating_from_a_recipe_derives_the_nutrition_server_side(db_session):
    from app.routers.food_log import create_food_log_entry
    from app.schemas.food_log import FoodLogEntryCreate

    recipe = _recipe(db_session, nutrition={"calories": 250.0})

    created = create_food_log_entry(
        payload=FoodLogEntryCreate(recipe_id=recipe.id, servings=2, meal_type="lunch"), db=db_session
    )

    assert created.nutrition["calories"] == 500.0
    assert created.nutrition_provenance == "computed"
    assert created.description == "Lentil Soup"


def test_a_typed_description_beats_the_recipe_title(db_session):
    from app.routers.food_log import create_food_log_entry
    from app.schemas.food_log import FoodLogEntryCreate

    recipe = _recipe(db_session)

    created = create_food_log_entry(
        payload=FoodLogEntryCreate(recipe_id=recipe.id, description="Lentil soup, no bread", servings=1),
        db=db_session,
    )

    assert created.description == "Lentil soup, no bread"
    assert created.recipe_id == recipe.id


def test_a_description_only_entry_carries_no_nutrition_and_says_so(db_session):
    from app.routers.food_log import create_food_log_entry
    from app.schemas.food_log import FoodLogEntryCreate

    created = create_food_log_entry(
        payload=FoodLogEntryCreate(description="Ate at my sister's", meal_type="dinner"), db=db_session
    )

    assert created.nutrition == {}
    assert created.nutrition_provenance is None


def test_editing_servings_rescales_from_the_recipe_not_the_stored_total(db_session):
    """The stored total is a rounded product. Re-scaling a rounded number
    compounds the error on every edit, so the recipe is the source."""
    from app.routers.food_log import create_food_log_entry, update_food_log_entry
    from app.schemas.food_log import FoodLogEntryCreate, FoodLogEntryUpdate

    recipe = _recipe(db_session, nutrition={"calories": 333.333})
    created = create_food_log_entry(payload=FoodLogEntryCreate(recipe_id=recipe.id, servings=3), db=db_session)
    assert created.nutrition["calories"] == 1000.0

    updated = update_food_log_entry(
        entry_id=created.id, payload=FoodLogEntryUpdate(servings=1), db=db_session
    )

    assert updated.nutrition["calories"] == 333.3, "re-derived from 333.333, not divided out of 1000.0"


def test_a_deleted_recipe_does_not_rewrite_what_was_eaten(db_session):
    """A log row is a historical fact. Every FK is SET NULL for this."""
    from app.routers.food_log import create_food_log_entry
    from app.schemas.food_log import FoodLogEntryCreate

    recipe = _recipe(db_session, nutrition={"calories": 250.0})
    created = create_food_log_entry(payload=FoodLogEntryCreate(recipe_id=recipe.id, servings=2), db=db_session)

    db_session.delete(recipe)
    db_session.commit()

    row = db_session.get(FoodLogEntry, created.id)
    assert row is not None
    assert row.description == "Lentil Soup"
    assert row.nutrition["calories"] == 500.0
    assert row.recipe_id is None


def test_a_removed_household_member_leaves_the_meals_behind(db_session):
    from app.routers.food_log import create_food_log_entry
    from app.schemas.food_log import FoodLogEntryCreate

    member = HouseholdMember(name="Sam")
    db_session.add(member)
    db_session.commit()
    created = create_food_log_entry(
        payload=FoodLogEntryCreate(description="Porridge", member_id=member.id), db=db_session
    )

    db_session.delete(member)
    db_session.commit()

    row = db_session.get(FoodLogEntry, created.id)
    assert row is not None, "removing a person must not delete the record of meals that were eaten"
    assert row.member_id is None


def test_an_unknown_member_or_recipe_is_a_404_not_a_dangling_row(db_session):
    from fastapi import HTTPException

    from app.routers.food_log import create_food_log_entry
    from app.schemas.food_log import FoodLogEntryCreate

    with pytest.raises(HTTPException) as exc:
        create_food_log_entry(payload=FoodLogEntryCreate(description="X", member_id=9999), db=db_session)
    assert exc.value.status_code == 404

    with pytest.raises(HTTPException):
        create_food_log_entry(payload=FoodLogEntryCreate(recipe_id=9999), db=db_session)


def test_deleting_a_log_row_frees_the_plan_slot_to_be_logged_again(db_session):
    from app.routers.food_log import delete_food_log_entry

    recipe = _recipe(db_session)
    entry = _plan_entry(db_session, recipe)
    first = food_log_service.log_for_confirmed_plan_entry(db_session, entry)
    db_session.commit()

    delete_food_log_entry(entry_id=first.id, db=db_session)

    again = food_log_service.log_for_confirmed_plan_entry(db_session, entry)
    db_session.commit()
    assert again is not None
