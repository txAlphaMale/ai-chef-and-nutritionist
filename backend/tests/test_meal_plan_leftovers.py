"""Unit tests for backlog B5.1's leftover-entry handling in
meal_plan_service.compute_grocery_list.

Needs a real `db_session` (unlike test_meal_plan_nutrition_summary.py's
pure in-memory objects) because compute_grocery_list also queries
InventoryItem to subtract what's already on hand -- an empty table is
fine here since these tests are only about which entries CONTRIBUTE
ingredients in the first place, not the subtraction math itself (already
covered by test_unit_conversion_wiring.py).
"""
from __future__ import annotations

from datetime import date

from app.models import MealPlan, MealPlanEntry, Recipe, RecipeIngredient
from app.services import meal_plan_service


def _recipe(ingredients: list[tuple[str, float, str]]) -> Recipe:
    recipe = Recipe(title="Test Recipe", default_servings=2)
    recipe.ingredients = [
        RecipeIngredient(ingredient_name=name, quantity=qty, unit=unit) for name, qty, unit in ingredients
    ]
    return recipe


def _entry(day_of_week: int, recipe: Recipe, servings: int = 2, leftover_of_entry_id: int | None = None) -> MealPlanEntry:
    entry = MealPlanEntry(day_of_week=day_of_week, servings=servings, leftover_of_entry_id=leftover_of_entry_id)
    entry.recipe = recipe
    return entry


def test_normal_entry_contributes_its_ingredients(db_session):
    recipe = _recipe([("chicken breast", 2, "lb")])
    plan = MealPlan()
    plan.entries = [_entry(0, recipe)]
    grocery_list = meal_plan_service.compute_grocery_list(db_session, plan)
    assert any(item["ingredient_name"] == "chicken breast" for item in grocery_list)


def test_leftover_entry_does_not_contribute_its_own_ingredients(db_session):
    # The origin entry (id set after a flush, since leftover_of_entry_id
    # is a real FK to another entry's id) contributes normally; the
    # leftover entry pointing back at it should contribute NOTHING --
    # its ingredients are already covered by the origin's own
    # contribution, scaled to the combined servings.
    recipe = _recipe([("rice", 4, "cup")])
    origin = _entry(0, recipe, servings=6)  # cooked enough for both slots
    db_session.add(MealPlan(week_start_date=date(2026, 8, 3), entries=[origin]))
    db_session.commit()

    leftover = _entry(1, recipe, servings=2, leftover_of_entry_id=origin.id)
    plan = MealPlan()
    plan.entries = [origin, leftover]

    grocery_list = meal_plan_service.compute_grocery_list(db_session, plan)
    rice_lines = [item for item in grocery_list if item["ingredient_name"] == "rice"]
    # Exactly one line, scaled from the ORIGIN's servings (6/2 default = 3x
    # the base 4 cups = 12 cups) -- not doubled by the leftover entry
    # contributing its own 2-serving share on top.
    assert len(rice_lines) == 1
    assert rice_lines[0]["quantity"] == 12.0


def test_skipped_and_recipe_less_entries_still_excluded_alongside_leftovers(db_session):
    recipe = _recipe([("egg", 6, "count")])
    origin = _entry(0, recipe, servings=2)
    db_session.add(MealPlan(week_start_date=date(2026, 8, 3), entries=[origin]))
    db_session.commit()

    leftover = _entry(1, recipe, servings=2, leftover_of_entry_id=origin.id)
    skipped = MealPlanEntry(day_of_week=2, servings=2, is_skipped=True)
    skipped.recipe = recipe
    no_recipe = MealPlanEntry(day_of_week=3, servings=2)

    plan = MealPlan()
    plan.entries = [origin, leftover, skipped, no_recipe]
    grocery_list = meal_plan_service.compute_grocery_list(db_session, plan)
    egg_lines = [item for item in grocery_list if item["ingredient_name"] == "egg"]
    assert len(egg_lines) == 1
