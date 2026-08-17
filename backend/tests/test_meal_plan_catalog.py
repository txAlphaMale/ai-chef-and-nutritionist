"""Capstone review 2026-08-16, backlog B24.5 -- the meal-plan recipe catalog.

`build_recipe_catalog_summary` sent the first 40 recipes by staple/rating
and said nothing about the rest. That is the same defect the chat catalog
had until earlier the same day, with one extra problem on top: the recipes
most worth surfacing to a PLANNER are the ones that use the food about to
go off, and "uses the spinach that expires Thursday" is uncorrelated with
"is a staple". So a household with a few hundred imported recipes got a
week planned as though it owned forty, chosen on the wrong axis, with the
model unable to tell that the list was a fraction.

As with B24.1, the tests that matter are the ones written to FAIL against
the previous behaviour.
"""

from datetime import date, timedelta

from app.models import HouseholdPreferences, InventoryItem, MealTag, Recipe, RecipeIngredient
from app.services import meal_plan_service


def _recipe(db, title, ingredients=(), tags=(), *, is_staple=False, rating=None):
    recipe = Recipe(title=title, default_servings=2, is_staple=is_staple, rating=rating)
    db.add(recipe)
    db.flush()
    for name in ingredients:
        db.add(RecipeIngredient(recipe_id=recipe.id, ingredient_name=name))
    for tag_name in tags:
        tag = db.query(MealTag).filter_by(name=tag_name).first() or MealTag(name=tag_name)
        db.add(tag)
        db.flush()
        recipe.tags.append(tag)
    db.commit()
    return recipe


def _fill_catalog(db, count, prefix="Filler"):
    """Staples, so they dominate the staple/rating head and crowd out
    anything that is not one."""
    for i in range(count):
        _recipe(db, f"{prefix} {i:03d}", ["water"], is_staple=True)


# --- The synthetic retrieval query ---------------------------------------


def test_the_query_is_built_from_expiring_food_and_slot_guidance():
    query = meal_plan_service.build_catalog_retrieval_query(
        [{"name": "baby spinach", "reasons": ["expires in 2 days"]}, {"name": "rotisserie chicken"}],
        [{"day_of_week": 0, "meal_type": "dinner", "tags": ["quick"], "notes": "something with rice"}],
    )

    assert "baby spinach" in query
    assert "rotisserie chicken" in query
    assert "quick" in query
    assert "rice" in query


def test_the_query_is_empty_when_there_is_nothing_to_steer_on():
    """No expiring food and no guidance means retrieval should not run at
    all -- pulling in arbitrary recipes would be worse than the head alone."""
    assert meal_plan_service.build_catalog_retrieval_query([], []).strip() == ""
    assert meal_plan_service.build_catalog_retrieval_query(None, None).strip() == ""


# --- What actually reaches the catalog ------------------------------------


def test_a_recipe_using_the_expiring_food_reaches_the_catalog_past_the_head(db_session):
    """The defect, reproduced. 60 staples crowd the staple/rating head, so
    the spinach recipe cannot be in the first 40 by any ordering -- and it
    is the single most relevant recipe for this week."""
    _fill_catalog(db_session, 60)
    _recipe(db_session, "Zzz Spinach and Feta Pie", ["baby spinach", "feta", "eggs"])

    head_only = meal_plan_service.build_recipe_catalog_summary(db_session)
    assert "Zzz Spinach and Feta Pie" not in [r["title"] for r in head_only], (
        "guard on the premise: without retrieval this recipe must be absent, "
        "otherwise the test proves nothing"
    )

    with_retrieval = meal_plan_service.build_recipe_catalog_summary(
        db_session,
        priority_ingredients=[{"name": "baby spinach", "reasons": ["expires in 2 days"]}],
        slots=[],
    )

    assert "Zzz Spinach and Feta Pie" in [r["title"] for r in with_retrieval]


def test_slot_guidance_also_pulls_recipes_in(db_session):
    _fill_catalog(db_session, 45)
    _recipe(db_session, "Zzz Weeknight Ramen", ["noodles", "broth"], tags=["quick"])

    catalog = meal_plan_service.build_recipe_catalog_summary(
        db_session,
        priority_ingredients=[],
        slots=[{"day_of_week": 1, "meal_type": "dinner", "tags": [], "notes": "ramen would be good"}],
    )

    assert "Zzz Weeknight Ramen" in [r["title"] for r in catalog]


def test_the_head_is_still_there_and_still_leads(db_session):
    """Retrieval ADDS; it must not displace what the household actually
    cooks. A staple stays in the catalog whether or not it matches this
    week."""
    _recipe(db_session, "Aaa House Chili", ["beans", "beef"], is_staple=True, rating=5)
    _recipe(db_session, "Zzz Spinach Pie", ["baby spinach"])

    catalog = meal_plan_service.build_recipe_catalog_summary(
        db_session, priority_ingredients=[{"name": "baby spinach"}], slots=[]
    )
    titles = [r["title"] for r in catalog]

    assert titles[0] == "Aaa House Chili"
    assert "Zzz Spinach Pie" in titles


def test_a_retrieved_recipe_is_not_listed_twice(db_session):
    """A staple that also matches the week is in both halves and must
    appear once -- a duplicated id in the prompt is wasted context and
    invites the model to treat it as two options."""
    _recipe(db_session, "House Spinach Bake", ["baby spinach"], is_staple=True, rating=5)

    catalog = meal_plan_service.build_recipe_catalog_summary(
        db_session, priority_ingredients=[{"name": "baby spinach"}], slots=[]
    )

    ids = [r["id"] for r in catalog]
    assert len(ids) == len(set(ids))


def test_retrieval_does_not_run_without_a_query(db_session):
    # The head has to be FULL for this to mean anything -- with six recipes
    # total, everything lands in the head whether retrieval runs or not.
    _fill_catalog(db_session, meal_plan_service.CATALOG_HEAD_LIMIT + 5)
    _recipe(db_session, "Zzz Unrelated", ["quinoa"])

    catalog = meal_plan_service.build_recipe_catalog_summary(db_session, priority_ingredients=[], slots=[])

    assert "Zzz Unrelated" not in [r["title"] for r in catalog]


# --- Telling the model the list is partial --------------------------------


def test_the_prompt_says_the_catalog_is_partial_and_by_how_much(db_session):
    _fill_catalog(db_session, 80)
    context = meal_plan_service.gather_generation_context(
        db_session,
        household_size=None,
        meal_types=["dinner"],
        kitchen_profile_id=None,
        entry_guidance=[],
        notes=None,
    )

    prompt = meal_plan_service.build_generation_prompt(context)

    assert "PARTIAL" in prompt
    assert "of 80 saved recipes" in prompt


def test_the_prompt_stays_quiet_when_the_catalog_is_complete(db_session):
    _fill_catalog(db_session, 3)

    prompt = meal_plan_service.build_generation_prompt(
        meal_plan_service.gather_generation_context(
        db_session,
        household_size=None,
        meal_types=["dinner"],
        kitchen_profile_id=None,
        entry_guidance=[],
        notes=None,
    )
    )

    assert "PARTIAL" not in prompt


def test_the_generation_context_wires_the_weeks_own_signals_into_retrieval(db_session):
    """End-to-end through the real context builder, not the summary
    function directly -- the B24.1 lesson was that testing the helper
    proves nothing about whether the caller uses it."""
    db_session.add(HouseholdPreferences(household_size=2))
    db_session.add(
        InventoryItem(
            name="baby spinach",
            category="produce",
            quantity=1,
            expiration_date=date.today() + timedelta(days=2),
        )
    )
    db_session.commit()
    _fill_catalog(db_session, 50)
    _recipe(db_session, "Zzz Spinach and Feta Pie", ["baby spinach", "feta"])

    context = meal_plan_service.gather_generation_context(
        db_session,
        household_size=None,
        meal_types=["dinner"],
        kitchen_profile_id=None,
        entry_guidance=[],
        notes=None,
    )
    prompt = meal_plan_service.build_generation_prompt(context)

    assert context["recipe_catalog_total"] == 51
    assert "Zzz Spinach and Feta Pie" in prompt, (
        "the recipe that uses the food expiring this week has to reach the prompt, "
        "which is the entire premise of an inventory-aware planner"
    )
