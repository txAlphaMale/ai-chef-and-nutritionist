"""Unit tests for backlog B2.2's HEI-2020-inspired diet-quality score
(diet_quality_service.py).

Same "transient in-memory ORM object, no db.add needed" pattern as
test_meal_plan_nutrition_summary.py -- compute_diet_quality_score is a
pure function over a MealPlan's entries/recipes/ingredients, no DB
session required.
"""

from __future__ import annotations

from app.models import MealPlan, MealPlanEntry, Recipe, RecipeIngredient
from app.services import diet_quality_service


def _plan(entries: list[MealPlanEntry]) -> MealPlan:
    plan = MealPlan()
    plan.entries = entries
    return plan


def _entry(day_of_week: int, recipe: Recipe | None, is_skipped: bool = False) -> MealPlanEntry:
    entry = MealPlanEntry(day_of_week=day_of_week, is_skipped=is_skipped, servings=4)
    entry.recipe = recipe
    return entry


def _ingredient(name: str, quantity: float | None, unit: str | None) -> RecipeIngredient:
    return RecipeIngredient(ingredient_name=name, quantity=quantity, unit=unit)


def _recipe(nutrition: dict, ingredients: list[RecipeIngredient] | None = None) -> Recipe:
    recipe = Recipe(nutrition=nutrition)
    recipe.ingredients = ingredients or []
    return recipe


# --- classify_food_group -------------------------------------------------


def test_classify_food_group_matches_expected_tags():
    assert diet_quality_service.classify_food_group("spinach") == {"vegetable_dark_green_or_legume"}
    assert diet_quality_service.classify_food_group("chicken breast") == {"protein_other"}
    assert diet_quality_service.classify_food_group("whole wheat bread") == {"grain_whole"}
    assert diet_quality_service.classify_food_group("white rice") == {"grain_refined"}
    assert diet_quality_service.classify_food_group("2% milk") == {"dairy"}


def test_classify_food_group_legume_matches_both_vegetable_and_protein_tags():
    # Documented simplification (see module docstring): legumes count
    # toward both Greens & Beans and Seafood/Plant Proteins here, rather
    # than the real HEI's either-or allocation.
    tags = diet_quality_service.classify_food_group("black beans")
    assert "vegetable_dark_green_or_legume" in tags
    assert "protein_seafood_or_plant" in tags


def test_classify_food_group_returns_empty_set_for_unrecognized_name():
    assert diet_quality_service.classify_food_group("xylophone seasoning") == set()


def test_classify_food_group_returns_empty_set_for_empty_name():
    assert diet_quality_service.classify_food_group("") == set()


# --- compute_diet_quality_score: no-data cases ---------------------------


def test_empty_plan_is_not_computed():
    result = diet_quality_service.compute_diet_quality_score(_plan([]))
    assert result["computed"] is False
    assert result["contributing_entries"] == 0


def test_plan_with_no_calorie_data_is_not_computed():
    recipe = _recipe({})
    plan = _plan([_entry(0, recipe)])
    result = diet_quality_service.compute_diet_quality_score(plan)
    assert result["computed"] is False


def test_skipped_entries_are_excluded_from_scoring():
    recipe = _recipe({"calories": 500})
    plan = _plan([_entry(0, recipe, is_skipped=True)])
    result = diet_quality_service.compute_diet_quality_score(plan)
    assert result["computed"] is False


# --- compute_diet_quality_score: computable moderation components -------


def test_low_sodium_and_saturated_fat_score_full_moderation_points():
    # 1 g sodium per 1000 kcal (well under the 1.1 "good" standard) and
    # 5% of energy from saturated fat (well under the 8% "good" standard)
    # should both score their full 10 points.
    recipe = _recipe({"calories": 2000, "sodium_mg": 2000, "saturated_fat_g": 11.1, "fat_g": 40})
    plan = _plan([_entry(0, recipe)])
    result = diet_quality_service.compute_diet_quality_score(plan)
    by_key = {c["key"]: c for c in result["components"]}
    assert by_key["sodium"]["points"] == 10.0
    assert by_key["saturated_fat"]["points"] == 10.0


def test_high_sodium_and_saturated_fat_score_zero_moderation_points():
    # 2 g sodium per 1000 kcal (at/above the "zero" standard of 2.0) and
    # 16%+ of energy from saturated fat (at/above the "zero" standard of
    # 16%) should both floor at 0.
    recipe = _recipe({"calories": 1000, "sodium_mg": 2500, "saturated_fat_g": 20, "fat_g": 40})
    plan = _plan([_entry(0, recipe)])
    result = diet_quality_service.compute_diet_quality_score(plan)
    by_key = {c["key"]: c for c in result["components"]}
    assert by_key["sodium"]["points"] == 0.0
    assert by_key["saturated_fat"]["points"] == 0.0


def test_moderation_component_interpolates_between_standards():
    # Sodium exactly halfway between the good (1.1) and zero (2.0)
    # standards should score exactly half of 10 points.
    midpoint = (1.1 + 2.0) / 2
    calories = 1000.0
    sodium_mg = midpoint * calories  # density = sodium_mg / calories, see service comment
    recipe = _recipe({"calories": calories, "sodium_mg": sodium_mg})
    plan = _plan([_entry(0, recipe)])
    result = diet_quality_service.compute_diet_quality_score(plan)
    by_key = {c["key"]: c for c in result["components"]}
    assert by_key["sodium"]["points"] == 5.0


# --- compute_diet_quality_score: unscored components ---------------------


def test_refined_grains_and_added_sugars_are_always_unscored():
    recipe = _recipe({"calories": 1000})
    plan = _plan([_entry(0, recipe)])
    result = diet_quality_service.compute_diet_quality_score(plan)
    unscored_keys = {c["key"] for c in result["unscored_components"]}
    assert "refined_grains" in unscored_keys
    assert "added_sugars" in unscored_keys
    by_key = {c["key"]: c for c in result["components"]}
    assert by_key["refined_grains"]["computable"] is False
    assert by_key["refined_grains"]["points"] is None
    assert by_key["added_sugars"]["computable"] is False


def test_max_points_scored_excludes_unscored_components():
    # Needs fat data present, or fatty_acids (10 pts) drops out too (see
    # test_fatty_acids_unscored_when_no_fat_data_present) -- this test is
    # specifically about refined_grains/added_sugars being the ONLY
    # always-excluded components.
    recipe = _recipe({"calories": 1000, "fat_g": 30, "saturated_fat_g": 10})
    plan = _plan([_entry(0, recipe)])
    result = diet_quality_service.compute_diet_quality_score(plan)
    # 100 real HEI-2020 max points minus 20 (refined grains + added sugars,
    # 10 each) that this app cannot score.
    assert result["score"]["max_points"] == diet_quality_service.MAX_COMPUTABLE_POINTS
    assert result["score"]["max_points"] == 80


def test_fatty_acids_unscored_when_no_fat_data_present():
    recipe = _recipe({"calories": 1000})
    plan = _plan([_entry(0, recipe)])
    result = diet_quality_service.compute_diet_quality_score(plan)
    by_key = {c["key"]: c for c in result["components"]}
    assert by_key["fatty_acids"]["computable"] is False
    unscored_keys = {c["key"] for c in result["unscored_components"]}
    assert "fatty_acids" in unscored_keys


def test_fatty_acids_scores_full_points_when_no_saturated_fat_present():
    recipe = _recipe({"calories": 1000, "fat_g": 30, "saturated_fat_g": 0})
    plan = _plan([_entry(0, recipe)])
    result = diet_quality_service.compute_diet_quality_score(plan)
    by_key = {c["key"]: c for c in result["components"]}
    assert by_key["fatty_acids"]["computable"] is True
    assert by_key["fatty_acids"]["points"] == 10.0


# --- compute_diet_quality_score: adequacy components via ingredients ----


def test_ingredient_classified_as_vegetable_contributes_to_total_vegetables():
    # 1000g of spinach (a dark-green/legume-tagged vegetable) across a
    # 2000-kcal plan is well above the 1.1 cup-eq/1000kcal "good"
    # standard at the service's 150g-per-cup-eq approximation
    # (1000g / 150g = 6.67 cup-eq total, / 2 (kcal_factor) = 3.3
    # density -- comfortably above 1.1).
    recipe = _recipe(
        {"calories": 2000},
        [_ingredient("spinach", 1000, "g")],
    )
    plan = _plan([_entry(0, recipe)])
    result = diet_quality_service.compute_diet_quality_score(plan)
    by_key = {c["key"]: c for c in result["components"]}
    assert by_key["total_vegetables"]["points"] == 5.0
    assert by_key["greens_and_beans"]["points"] == 5.0


def test_ingredient_with_no_quantity_does_not_contribute_to_food_groups():
    recipe = _recipe(
        {"calories": 2000},
        [_ingredient("salt", None, None)],
    )
    plan = _plan([_entry(0, recipe)])
    result = diet_quality_service.compute_diet_quality_score(plan)
    by_key = {c["key"]: c for c in result["components"]}
    assert by_key["total_vegetables"]["points"] == 0.0


def test_ingredient_with_unconvertible_unit_does_not_contribute():
    # A count-based unit ("2 eggs") has no fixed gram weight this app
    # will invent -- compute_ingredient_grams returns None for it, and
    # the classifier must not silently treat that as zero grams counted.
    recipe = _recipe(
        {"calories": 2000},
        [_ingredient("egg", 2, "count")],
    )
    plan = _plan([_entry(0, recipe)])
    result = diet_quality_service.compute_diet_quality_score(plan)
    by_key = {c["key"]: c for c in result["components"]}
    assert by_key["total_protein_foods"]["points"] == 0.0


def test_multiple_contributing_entries_aggregate_across_the_whole_plan():
    recipe_a = _recipe({"calories": 1000}, [_ingredient("kale", 500, "g")])
    recipe_b = _recipe({"calories": 1000}, [_ingredient("kale", 500, "g")])
    plan = _plan([_entry(0, recipe_a), _entry(1, recipe_b)])
    result = diet_quality_service.compute_diet_quality_score(plan)
    assert result["contributing_entries"] == 2
    assert result["total_calories"] == 2000.0
