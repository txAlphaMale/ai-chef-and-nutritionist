"""Unit tests for the B1.2 nutrition-summation layer
(compute_ingredient_grams / compute_recipe_nutrition in
app/services/food_data_service.py).

These are pure functions over in-memory Recipe/RecipeIngredient objects --
no DB session needed. A transient (never-`db.add`ed) ORM object can still
have its `ingredients` relationship assigned a plain list directly, which
is all `compute_recipe_nutrition` reads; nothing here touches the DB.
"""
from __future__ import annotations

from app.models import Recipe, RecipeIngredient
from app.services import food_data_service as fds


def _recipe(default_servings: int, nutrition: dict, ingredients: list[RecipeIngredient]) -> Recipe:
    recipe = Recipe(default_servings=default_servings, nutrition=nutrition)
    recipe.ingredients = ingredients
    return recipe


def _ing(**kwargs) -> RecipeIngredient:
    kwargs.setdefault("ingredient_name", "test ingredient")
    return RecipeIngredient(**kwargs)


# --- compute_ingredient_grams --------------------------------------------


def test_compute_ingredient_grams_mass_unit_converts():
    ing = _ing(quantity=1, unit="lb")
    assert fds.compute_ingredient_grams(ing) == 453.592


def test_compute_ingredient_grams_grams_passthrough():
    ing = _ing(quantity=200, unit="g")
    assert fds.compute_ingredient_grams(ing) == 200.0


def test_compute_ingredient_grams_no_quantity_returns_none():
    ing = _ing(quantity=None, unit="g")
    assert fds.compute_ingredient_grams(ing) is None


def test_compute_ingredient_grams_volume_unit_returns_none_no_density():
    # No density source is wired in yet (documented limitation) -- volume
    # units never convert to grams today, even with a resolved ingredient.
    ing = _ing(quantity=2, unit="cup")
    assert fds.compute_ingredient_grams(ing) is None


def test_compute_ingredient_grams_count_unit_returns_none():
    ing = _ing(quantity=2, unit="clove")
    assert fds.compute_ingredient_grams(ing) is None


def test_compute_ingredient_grams_no_unit_returns_none():
    ing = _ing(quantity=2, unit=None)
    assert fds.compute_ingredient_grams(ing) is None


# --- compute_recipe_nutrition: provenance states -------------------------


def test_compute_recipe_nutrition_all_mass_units_resolved_is_computed():
    recipe = _recipe(
        default_servings=2,
        nutrition={"calories": 1},  # stale AI guess -- should be replaced
        ingredients=[
            _ing(quantity=200, unit="g", nutrition_per_100g={"calories": 165.0, "protein_g": 31.0}),
            _ing(quantity=100, unit="g", nutrition_per_100g={"calories": 50.0, "protein_g": 2.0}),
        ],
    )
    nutrition, provenance = fds.compute_recipe_nutrition(recipe)
    assert provenance == "computed"
    # (330 + 50) / 2 servings = 190.0 calories/serving; (62 + 2) / 2 = 32.0g protein/serving
    assert nutrition == {"calories": 190.0, "protein_g": 32.0}


def test_compute_recipe_nutrition_mixed_resolved_and_unresolved_is_partial():
    recipe = _recipe(
        default_servings=1,
        nutrition={"calories": 1},
        ingredients=[
            _ing(quantity=100, unit="g", nutrition_per_100g={"calories": 100.0}),
            # resolved but volume-based -- can't convert to grams, no density source
            _ing(quantity=1, unit="cup", nutrition_per_100g={"calories": 200.0}),
        ],
    )
    nutrition, provenance = fds.compute_recipe_nutrition(recipe)
    assert provenance == "partial"
    assert nutrition == {"calories": 100.0}  # only the gram-convertible ingredient counted


def test_compute_recipe_nutrition_mixed_resolved_and_unresolved_source_is_partial():
    recipe = _recipe(
        default_servings=1,
        nutrition={},
        ingredients=[
            _ing(quantity=100, unit="g", nutrition_per_100g={"calories": 100.0}),
            _ing(quantity=50, unit="g", nutrition_per_100g=None),  # never resolved / no match
        ],
    )
    nutrition, provenance = fds.compute_recipe_nutrition(recipe)
    assert provenance == "partial"
    assert nutrition == {"calories": 100.0}


def test_compute_recipe_nutrition_nothing_resolved_keeps_existing_ai_estimate():
    existing = {"calories": 500.0, "protein_g": 20.0}
    recipe = _recipe(
        default_servings=2,
        nutrition=existing,
        ingredients=[
            _ing(quantity=1, unit="cup", nutrition_per_100g=None),
            _ing(quantity=2, unit="clove", nutrition_per_100g=None),
        ],
    )
    nutrition, provenance = fds.compute_recipe_nutrition(recipe)
    assert provenance == "ai_estimated"
    # Must be the SAME dict content as before -- never blanked or replaced.
    assert nutrition == existing


def test_compute_recipe_nutrition_no_ingredients_at_all_is_ai_estimated():
    recipe = _recipe(default_servings=2, nutrition={"calories": 42.0}, ingredients=[])
    nutrition, provenance = fds.compute_recipe_nutrition(recipe)
    assert provenance == "ai_estimated"
    assert nutrition == {"calories": 42.0}


def test_compute_recipe_nutrition_only_unquantified_ingredients_is_ai_estimated():
    # "salt to taste" -- no quantity at all. Should not count against
    # completeness (it's not a resolution failure) and, since nothing else
    # is quantified either, the recipe has nothing to compute from.
    recipe = _recipe(
        default_servings=2,
        nutrition={"calories": 10.0},
        ingredients=[_ing(quantity=None, unit=None, nutrition_per_100g={"calories": 0.0})],
    )
    nutrition, provenance = fds.compute_recipe_nutrition(recipe)
    assert provenance == "ai_estimated"
    assert nutrition == {"calories": 10.0}


def test_compute_recipe_nutrition_unquantified_ingredient_excluded_from_completeness():
    # One real, quantified, fully-resolved+convertible ingredient plus an
    # unquantified "salt to taste" -- the salt should NOT drag this down
    # to "partial"; it isn't counted in the denominator at all.
    recipe = _recipe(
        default_servings=1,
        nutrition={},
        ingredients=[
            _ing(quantity=100, unit="g", nutrition_per_100g={"sodium_mg": 5.0}),
            _ing(ingredient_name="salt to taste", quantity=None, unit=None),
        ],
    )
    nutrition, provenance = fds.compute_recipe_nutrition(recipe)
    assert provenance == "computed"
    assert nutrition == {"sodium_mg": 5.0}


def test_compute_recipe_nutrition_rounds_to_one_decimal():
    recipe = _recipe(
        default_servings=3,
        nutrition={},
        ingredients=[_ing(quantity=100, unit="g", nutrition_per_100g={"calories": 100.0})],
    )
    nutrition, provenance = fds.compute_recipe_nutrition(recipe)
    assert provenance == "computed"
    assert nutrition == {"calories": round(100.0 / 3, 1)}


def test_compute_recipe_nutrition_zero_default_servings_does_not_divide_by_zero():
    recipe = _recipe(
        default_servings=0,
        nutrition={},
        ingredients=[_ing(quantity=100, unit="g", nutrition_per_100g={"calories": 100.0})],
    )
    nutrition, provenance = fds.compute_recipe_nutrition(recipe)
    assert provenance == "computed"
    assert nutrition == {"calories": 100.0}  # falls back to 1 serving, not a ZeroDivisionError
