"""Unit tests for the B1.4 nutrition roll-up
(meal_plan_service.compute_nutrition_summary).

Pure function over in-memory MealPlan/MealPlanEntry/Recipe objects --
same "transient ORM object, relationship assigned directly, no db.add
needed" pattern as test_nutrition_calc_service.py.
"""

from __future__ import annotations

from app.models import MealPlan, MealPlanEntry, Recipe
from app.services import meal_plan_service


def _plan(entries: list[MealPlanEntry]) -> MealPlan:
    plan = MealPlan()
    plan.entries = entries
    return plan


def _entry(day_of_week: int, recipe: Recipe | None, is_skipped: bool = False) -> MealPlanEntry:
    entry = MealPlanEntry(day_of_week=day_of_week, is_skipped=is_skipped, servings=4)
    entry.recipe = recipe
    return entry


def _recipe(nutrition: dict) -> Recipe:
    recipe = Recipe(nutrition=nutrition)
    recipe.ingredients = []
    return recipe


def test_empty_plan_returns_no_days():
    summary = meal_plan_service.compute_nutrition_summary(_plan([]))
    assert summary["days"] == []
    assert summary["week_totals"] == {}


def test_single_entry_contributes_its_recipe_nutrition_unscaled_by_servings():
    # servings=4 on the entry (see _entry) must NOT multiply the totals --
    # Recipe.nutrition is per-serving, and entry.servings only affects
    # what gets cooked/deducted, not per-person nutrition (see the
    # function's own docstring for why).
    recipe = _recipe({"calories": 500, "protein_g": 30})
    plan = _plan([_entry(0, recipe)])
    summary = meal_plan_service.compute_nutrition_summary(plan)
    assert summary["days"] == [
        {
            "day_of_week": 0,
            "entry_count": 1,
            "contributing_entry_count": 1,
            "totals": {"calories": 500, "protein_g": 30},
        }
    ]
    assert summary["week_totals"] == {"calories": 500, "protein_g": 30}


def test_multiple_entries_same_day_sum_together():
    recipe_a = _recipe({"calories": 500, "sodium_mg": 400})
    recipe_b = _recipe({"calories": 300, "sodium_mg": 200})
    plan = _plan([_entry(2, recipe_a), _entry(2, recipe_b)])
    summary = meal_plan_service.compute_nutrition_summary(plan)
    assert len(summary["days"]) == 1
    day = summary["days"][0]
    assert day["day_of_week"] == 2
    assert day["entry_count"] == 2
    assert day["contributing_entry_count"] == 2
    assert day["totals"] == {"calories": 800, "sodium_mg": 600}


def test_week_totals_sum_across_days():
    plan = _plan([_entry(0, _recipe({"calories": 500})), _entry(1, _recipe({"calories": 700}))])
    summary = meal_plan_service.compute_nutrition_summary(plan)
    assert summary["week_totals"] == {"calories": 1200}


def test_skipped_entries_are_excluded():
    plan = _plan([_entry(0, _recipe({"calories": 500}), is_skipped=True)])
    summary = meal_plan_service.compute_nutrition_summary(plan)
    assert summary["days"] == []
    assert summary["week_totals"] == {}


def test_entries_with_no_recipe_are_excluded():
    plan = _plan([_entry(0, None)])
    summary = meal_plan_service.compute_nutrition_summary(plan)
    assert summary["days"] == []


def test_entry_with_empty_nutrition_counts_toward_entry_count_but_not_contributing():
    # A recipe that's never had nutrition computed/estimated -- the day
    # should still show up (there IS a planned meal that day) with an
    # honest 0-for-2 contributing count, not silently vanish or show a
    # fake zero total blended in with real data.
    recipe_with_data = _recipe({"calories": 500})
    recipe_without_data = _recipe({})
    plan = _plan([_entry(3, recipe_with_data), _entry(3, recipe_without_data)])
    summary = meal_plan_service.compute_nutrition_summary(plan)
    day = summary["days"][0]
    assert day["entry_count"] == 2
    assert day["contributing_entry_count"] == 1
    assert day["totals"] == {"calories": 500}


def test_days_are_sorted_by_day_of_week():
    plan = _plan([_entry(5, _recipe({"calories": 100})), _entry(1, _recipe({"calories": 200}))])
    summary = meal_plan_service.compute_nutrition_summary(plan)
    assert [d["day_of_week"] for d in summary["days"]] == [1, 5]


def test_null_nutrition_values_within_a_dict_are_skipped_not_summed_as_zero_error():
    recipe = _recipe({"calories": 500, "fiber_g": None})
    plan = _plan([_entry(0, recipe)])
    summary = meal_plan_service.compute_nutrition_summary(plan)
    assert "fiber_g" not in summary["days"][0]["totals"]
    assert summary["days"][0]["totals"]["calories"] == 500
