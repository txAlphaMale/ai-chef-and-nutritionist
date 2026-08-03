"""Unit tests for the B1.1 ingredient-to-food-database resolution layer
(app/services/food_data_service.py).

No live network access to USDA FoodData Central or Open Food Facts exists
in Claude's sandbox (same constraint as every Ollama-dependent test in
this project) -- `httpx.get` is monkeypatched with fixture payloads shaped
like each API's documented response schema. The pure parsing functions
(`_parse_usda_nutrients`, `_parse_off_nutrients`) are exercised directly
against representative nutrient data with no network involved at all.
"""
from __future__ import annotations

import httpx
import pytest

from app.models import RecipeIngredient
from app.services import food_data_service as fds
from app.services import settings_service

# --- Pure parsing functions ---------------------------------------------


def test_parse_usda_nutrients_maps_known_keys():
    food_nutrients = [
        {"nutrientName": "Energy", "value": 165},
        {"nutrientName": "Protein", "value": 31},
        {"nutrientName": "Fatty acids, total saturated", "value": 1.0},
        {"nutrientName": "Some Untracked Nutrient", "value": 42},
    ]
    assert fds._parse_usda_nutrients(food_nutrients) == {
        "calories": 165.0,
        "protein_g": 31.0,
        "saturated_fat_g": 1.0,
    }


def test_parse_usda_nutrients_handles_missing_or_bad_values():
    food_nutrients = [
        {"nutrientName": "Energy", "value": None},
        {"nutrientName": "Protein"},  # no value/amount key at all
        {"nutrientName": "Total lipid (fat)", "value": "not-a-number"},
    ]
    assert fds._parse_usda_nutrients(food_nutrients) == {}


def test_parse_usda_nutrients_empty_input():
    assert fds._parse_usda_nutrients([]) == {}
    assert fds._parse_usda_nutrients(None) == {}


def test_parse_usda_nutrients_falls_back_to_amount_key():
    # Some USDA endpoints use "amount" instead of "value" for the same field.
    assert fds._parse_usda_nutrients([{"nutrientName": "Energy", "amount": 52}]) == {"calories": 52.0}


def test_parse_off_nutrients_maps_and_converts_grams_to_mg():
    nutriments = {
        "energy-kcal_100g": 52,
        "proteins_100g": 0.3,
        "sodium_100g": 0.001,  # OFF reports grams -> this app wants mg
        "cholesterol_100g": 0.0,
    }
    result = fds._parse_off_nutrients(nutriments)
    assert result["calories"] == 52.0
    assert result["protein_g"] == 0.3
    assert result["sodium_mg"] == pytest.approx(1.0)
    assert result["cholesterol_mg"] == 0.0


def test_parse_off_nutrients_ignores_unknown_and_missing():
    assert fds._parse_off_nutrients({}) == {}
    assert fds._parse_off_nutrients({"unknown_field_100g": 5}) == {}


def test_parse_off_nutrients_skips_non_numeric_values():
    assert fds._parse_off_nutrients({"energy-kcal_100g": "unknown"}) == {}


# --- _parse_usda_density (backlog B10.5) ---------------------------------


def test_parse_usda_density_finds_volume_portion():
    # 1 cup (236.588 mL) weighing 125g -> ~0.5284 g/mL, a realistic
    # flour-ish density.
    portions = [{"amount": 1, "gramWeight": 125, "measureUnit": {"name": "cup"}}]
    assert fds._parse_usda_density(portions) == round(125 / 236.588, 5)


def test_parse_usda_density_skips_non_volume_portions_first():
    portions = [
        {"amount": 1, "gramWeight": 148, "measureUnit": {"name": "medium"}, "modifier": "medium"},
        {"amount": 1, "gramWeight": 236, "measureUnit": {"name": "cup"}},
    ]
    assert fds._parse_usda_density(portions) == round(236 / 236.588, 5)


def test_parse_usda_density_uses_abbreviation_when_name_missing():
    portions = [{"amount": 1, "gramWeight": 14.79, "measureUnit": {"abbreviation": "tbsp"}}]
    assert fds._parse_usda_density(portions) == round(14.79 / 14.7868, 5)


def test_parse_usda_density_uses_modifier_when_no_measure_unit():
    portions = [{"amount": 1, "gramWeight": 4.93, "modifier": "tsp"}]
    assert fds._parse_usda_density(portions) == round(4.93 / 4.92892, 5)


def test_parse_usda_density_returns_none_when_no_volume_portion():
    portions = [{"amount": 1, "gramWeight": 148, "measureUnit": {"name": "medium"}}]
    assert fds._parse_usda_density(portions) is None


def test_parse_usda_density_returns_none_for_empty_or_missing_fields():
    assert fds._parse_usda_density([]) is None
    assert fds._parse_usda_density(None) is None
    assert fds._parse_usda_density([{"measureUnit": {"name": "cup"}}]) is None  # no gramWeight/amount
    assert fds._parse_usda_density([{"amount": 1, "gramWeight": 0, "measureUnit": {"name": "cup"}}]) is None


# --- Network-calling functions, httpx.get monkeypatched ------------------


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json


def test_search_usda_returns_empty_without_api_key(db_session):
    # No usda_fdc_api_key setting configured -- should not attempt a request.
    assert fds.search_usda(db_session, "chicken breast") == []


def test_search_usda_returns_foods_with_key(monkeypatch, db_session):
    settings_service.set_setting(db_session, "usda_fdc_api_key", "test-key")

    def fake_get(url, params=None, timeout=None):
        assert params["api_key"] == "test-key"
        return _FakeResponse({"foods": [{"fdcId": 123, "description": "Chicken, broiler"}]})

    monkeypatch.setattr(fds.httpx, "get", fake_get)
    assert fds.search_usda(db_session, "chicken") == [{"fdcId": 123, "description": "Chicken, broiler"}]


def test_search_usda_returns_empty_on_http_error(monkeypatch, db_session):
    settings_service.set_setting(db_session, "usda_fdc_api_key", "test-key")

    def fake_get(url, params=None, timeout=None):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(fds.httpx, "get", fake_get)
    assert fds.search_usda(db_session, "chicken") == []


def test_get_usda_food_returns_none_without_api_key(db_session):
    assert fds.get_usda_food(db_session, 123) is None


def test_search_off_returns_products(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse({"products": [{"product_name": "Greek Yogurt", "code": "0001"}]})

    monkeypatch.setattr(fds.httpx, "get", fake_get)
    result = fds.search_off("greek yogurt")
    assert result[0]["product_name"] == "Greek Yogurt"


def test_search_off_returns_empty_on_failure(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        raise httpx.ConnectError("boom")

    monkeypatch.setattr(fds.httpx, "get", fake_get)
    assert fds.search_off("anything") == []


def test_get_off_product_returns_none_when_not_found(monkeypatch):
    monkeypatch.setattr(fds.httpx, "get", lambda url, timeout=None: _FakeResponse({"status": 0}))
    assert fds.get_off_product("000000000000") is None


def test_get_off_product_returns_product_when_found(monkeypatch):
    monkeypatch.setattr(
        fds.httpx,
        "get",
        lambda url, timeout=None: _FakeResponse(
            {"status": 1, "product": {"product_name": "Oat Milk", "nutriments": {}}}
        ),
    )
    product = fds.get_off_product("123456")
    assert product["product_name"] == "Oat Milk"


# --- resolve_ingredient_name: source preference and fallback -------------


def test_resolve_ingredient_name_prefers_usda_when_it_has_a_match(monkeypatch, db_session):
    settings_service.set_setting(db_session, "usda_fdc_api_key", "test-key")
    monkeypatch.setattr(
        fds, "search_usda", lambda db, name, page_size=1: [{"fdcId": 999, "description": "Rolled Oats"}]
    )
    monkeypatch.setattr(
        fds,
        "get_usda_food",
        lambda db, fdc_id: {"foodNutrients": [{"nutrientName": "Energy", "value": 389}]},
    )
    monkeypatch.setattr(fds, "search_off", lambda name, page_size=1: [{"product_name": "should not be used"}])

    result = fds.resolve_ingredient_name(db_session, "oats")
    assert result.source == "usda"
    assert result.fdc_id == 999
    assert result.nutrition_per_100g == {"calories": 389.0}


def test_resolve_ingredient_name_includes_density_from_usda_food_portions(monkeypatch, db_session):
    settings_service.set_setting(db_session, "usda_fdc_api_key", "test-key")
    monkeypatch.setattr(
        fds, "search_usda", lambda db, name, page_size=1: [{"fdcId": 999, "description": "All-purpose flour"}]
    )
    monkeypatch.setattr(
        fds,
        "get_usda_food",
        lambda db, fdc_id: {
            "foodNutrients": [{"nutrientName": "Energy", "value": 364}],
            "foodPortions": [{"amount": 1, "gramWeight": 125, "measureUnit": {"name": "cup"}}],
        },
    )
    result = fds.resolve_ingredient_name(db_session, "flour")
    assert result.density_g_per_ml == round(125 / 236.588, 5)


def test_resolve_ingredient_name_density_none_when_no_volume_portion(monkeypatch, db_session):
    settings_service.set_setting(db_session, "usda_fdc_api_key", "test-key")
    monkeypatch.setattr(fds, "search_usda", lambda db, name, page_size=1: [{"fdcId": 1, "description": "Egg"}])
    monkeypatch.setattr(
        fds,
        "get_usda_food",
        lambda db, fdc_id: {
            "foodNutrients": [{"nutrientName": "Energy", "value": 143}],
            "foodPortions": [{"amount": 1, "gramWeight": 50, "measureUnit": {"name": "large"}}],
        },
    )
    result = fds.resolve_ingredient_name(db_session, "egg")
    assert result.density_g_per_ml is None


def test_resolve_ingredient_name_falls_back_to_off_when_usda_has_no_match(monkeypatch, db_session):
    monkeypatch.setattr(fds, "search_usda", lambda db, name, page_size=1: [])
    monkeypatch.setattr(
        fds,
        "search_off",
        lambda name, page_size=1: [
            {"product_name": "Gluten-Free Bread", "code": "5555", "nutriments": {"energy-kcal_100g": 250}}
        ],
    )

    result = fds.resolve_ingredient_name(db_session, "gluten free bread")
    assert result.source == "off"
    assert result.off_barcode == "5555"
    assert result.nutrition_per_100g == {"calories": 250.0}


def test_resolve_ingredient_name_falls_back_to_off_when_usda_match_has_no_usable_nutrients(monkeypatch, db_session):
    """A USDA candidate with no nutrient data this app tracks (e.g. only
    exotic nutrients) should not "win" over an OFF match that has usable
    data -- resolve_ingredient_name checks for a non-empty parsed dict,
    not just the presence of a candidate."""
    settings_service.set_setting(db_session, "usda_fdc_api_key", "test-key")
    monkeypatch.setattr(fds, "search_usda", lambda db, name, page_size=1: [{"fdcId": 1, "description": "X"}])
    monkeypatch.setattr(fds, "get_usda_food", lambda db, fdc_id: {"foodNutrients": []})
    monkeypatch.setattr(
        fds,
        "search_off",
        lambda name, page_size=1: [{"product_name": "Fallback", "code": "1", "nutriments": {"proteins_100g": 1}}],
    )
    result = fds.resolve_ingredient_name(db_session, "x")
    assert result.source == "off"


def test_resolve_ingredient_name_returns_none_when_nothing_matches(monkeypatch, db_session):
    monkeypatch.setattr(fds, "search_usda", lambda db, name, page_size=1: [])
    monkeypatch.setattr(fds, "search_off", lambda name, page_size=1: [])
    assert fds.resolve_ingredient_name(db_session, "a completely made up ingredient xyz") is None


def test_resolve_ingredient_name_empty_name_returns_none(db_session):
    assert fds.resolve_ingredient_name(db_session, "") is None
    assert fds.resolve_ingredient_name(db_session, "   ") is None


# --- resolve_and_cache_ingredient: caching semantics ----------------------


def test_resolve_and_cache_ingredient_writes_fields_on_success(monkeypatch, db_session):
    monkeypatch.setattr(
        fds,
        "resolve_ingredient_name",
        lambda db, name: fds.ResolvedFood(
            source="usda", matched_name="Chicken Breast", nutrition_per_100g={"calories": 165.0}, fdc_id=42
        ),
    )
    ingredient = RecipeIngredient(recipe_id=1, ingredient_name="chicken breast")
    resolved = fds.resolve_and_cache_ingredient(db_session, ingredient)
    assert resolved is True
    assert ingredient.resolution_source == "usda"
    assert ingredient.fdc_id == 42
    assert ingredient.resolved_food_name == "Chicken Breast"
    assert ingredient.nutrition_per_100g == {"calories": 165.0}
    assert ingredient.resolved_at is not None
    assert ingredient.density_g_per_ml is None  # no density in this ResolvedFood


def test_resolve_and_cache_ingredient_writes_density_when_present(monkeypatch, db_session):
    monkeypatch.setattr(
        fds,
        "resolve_ingredient_name",
        lambda db, name: fds.ResolvedFood(
            source="usda", matched_name="Flour", nutrition_per_100g={"calories": 364.0}, density_g_per_ml=0.529
        ),
    )
    ingredient = RecipeIngredient(recipe_id=1, ingredient_name="flour")
    fds.resolve_and_cache_ingredient(db_session, ingredient)
    assert ingredient.density_g_per_ml == 0.529


def test_resolve_and_cache_ingredient_marks_unresolved_on_no_match(monkeypatch, db_session):
    monkeypatch.setattr(fds, "resolve_ingredient_name", lambda db, name: None)
    ingredient = RecipeIngredient(recipe_id=1, ingredient_name="mystery item")
    resolved = fds.resolve_and_cache_ingredient(db_session, ingredient)
    assert resolved is False
    assert ingredient.resolution_source == "unresolved"
    assert ingredient.fdc_id is None


def test_resolve_and_cache_ingredient_skips_network_if_already_resolved(monkeypatch, db_session):
    called = []
    monkeypatch.setattr(fds, "resolve_ingredient_name", lambda db, name: called.append(name))
    ingredient = RecipeIngredient(recipe_id=1, ingredient_name="chicken breast", resolution_source="usda", fdc_id=1)
    resolved = fds.resolve_and_cache_ingredient(db_session, ingredient)
    assert resolved is True
    assert called == []  # resolve_ingredient_name never called


def test_resolve_and_cache_ingredient_force_reresolves(monkeypatch, db_session):
    monkeypatch.setattr(
        fds,
        "resolve_ingredient_name",
        lambda db, name: fds.ResolvedFood(source="off", matched_name="New Match", nutrition_per_100g={}),
    )
    ingredient = RecipeIngredient(recipe_id=1, ingredient_name="chicken breast", resolution_source="usda", fdc_id=1)
    resolved = fds.resolve_and_cache_ingredient(db_session, ingredient, force=True)
    assert resolved is True
    assert ingredient.resolution_source == "off"
    assert ingredient.resolved_food_name == "New Match"


def test_resolve_and_cache_ingredient_reresolves_previously_unresolved_without_force(monkeypatch, db_session):
    """A prior 'unresolved' result is deliberately NOT sticky the way a
    real match is -- e.g. after the user adds a USDA key, ingredients
    that failed before should retry on the next /resolve-nutrition call
    without needing force=True."""
    monkeypatch.setattr(
        fds,
        "resolve_ingredient_name",
        lambda db, name: fds.ResolvedFood(source="usda", matched_name="Now Found", nutrition_per_100g={}),
    )
    ingredient = RecipeIngredient(recipe_id=1, ingredient_name="chicken breast", resolution_source="unresolved")
    resolved = fds.resolve_and_cache_ingredient(db_session, ingredient)
    assert resolved is True
    assert ingredient.resolution_source == "usda"
