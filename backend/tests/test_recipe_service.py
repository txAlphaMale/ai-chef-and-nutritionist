"""Unit tests for recipe_service.py's pure ingredient-list transforms:
scale_ingredients (pre-existing, not previously under its own test file)
and apply_display_unit_system (backlog B10.5). Pure functions over plain
dicts, no DB/network involved.
"""
from __future__ import annotations

from app.services import recipe_service


def _ing(**kwargs) -> dict:
    base = {
        "ingredient_name": "test ingredient",
        "quantity": 1,
        "unit": "cup",
        "prep_note": None,
        "resolution_source": None,
        "resolved_food_name": None,
        "fdc_id": None,
        "off_barcode": None,
        "nutrition_per_100g": None,
        "density_g_per_ml": None,
    }
    base.update(kwargs)
    return base


# --- scale_ingredients -------------------------------------------------


def test_scale_ingredients_scales_quantity_proportionally():
    scaled = recipe_service.scale_ingredients([_ing(quantity=2)], 2, 4)
    assert scaled[0]["quantity"] == 4


def test_scale_ingredients_noop_when_servings_match():
    ingredients = [_ing(quantity=2)]
    assert recipe_service.scale_ingredients(ingredients, 2, 2) is ingredients


def test_scale_ingredients_leaves_null_quantity_alone():
    scaled = recipe_service.scale_ingredients([_ing(quantity=None)], 2, 4)
    assert scaled[0]["quantity"] is None


# --- apply_display_unit_system (backlog B10.5) --------------------------


def test_apply_display_unit_system_original_strips_density_only():
    ingredients = [_ing(quantity=1, unit="cup", density_g_per_ml=0.5)]
    result = recipe_service.apply_display_unit_system(ingredients, "original")
    assert result[0]["quantity"] == 1
    assert result[0]["unit"] == "cup"
    assert result[0]["display_unavailable"] is False
    assert "density_g_per_ml" not in result[0]


def test_apply_display_unit_system_unknown_system_treated_as_original():
    ingredients = [_ing(quantity=1, unit="cup")]
    result = recipe_service.apply_display_unit_system(ingredients, "bogus")
    assert result[0]["quantity"] == 1
    assert result[0]["display_unavailable"] is False


def test_apply_display_unit_system_metric_converts_and_clears_density_key():
    ingredients = [_ing(quantity=1, unit="cup")]
    result = recipe_service.apply_display_unit_system(ingredients, "metric")
    assert result[0]["unit"] == "ml"
    assert "density_g_per_ml" not in result[0]
    assert result[0]["display_unavailable"] is False


def test_apply_display_unit_system_weight_mode_marks_unavailable_without_density():
    ingredients = [_ing(quantity=1, unit="cup", density_g_per_ml=None)]
    result = recipe_service.apply_display_unit_system(ingredients, "weight")
    assert result[0]["display_unavailable"] is True
    # Original quantity/unit preserved, not a guess.
    assert result[0]["quantity"] == 1
    assert result[0]["unit"] == "cup"


def test_apply_display_unit_system_weight_mode_converts_with_density():
    ingredients = [_ing(quantity=2, unit="cup", density_g_per_ml=0.529)]
    result = recipe_service.apply_display_unit_system(ingredients, "weight")
    assert result[0]["display_unavailable"] is False
    assert result[0]["unit"] == "g"


def test_apply_display_unit_system_null_quantity_passthrough_not_unavailable():
    ingredients = [_ing(quantity=None, unit=None)]
    result = recipe_service.apply_display_unit_system(ingredients, "weight")
    assert result[0]["display_unavailable"] is False
    assert result[0]["quantity"] is None


def test_apply_display_unit_system_count_unit_never_marked_unavailable():
    ingredients = [_ing(quantity=2, unit="clove", density_g_per_ml=None)]
    result = recipe_service.apply_display_unit_system(ingredients, "weight")
    assert result[0]["display_unavailable"] is False
    assert result[0]["quantity"] == 2
    assert result[0]["unit"] == "clove"


def test_apply_display_unit_system_preserves_other_keys():
    ingredients = [_ing(quantity=1, unit="cup", ingredient_name="flour", resolution_source="usda")]
    result = recipe_service.apply_display_unit_system(ingredients, "metric")
    assert result[0]["ingredient_name"] == "flour"
    assert result[0]["resolution_source"] == "usda"
