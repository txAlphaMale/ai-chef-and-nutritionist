"""Unit tests for the shared B5.3/B10.5 unit-conversion layer
(app/services/unit_conversion_service.py). Pure functions, no DB/network
involved at all."""

from __future__ import annotations

import pytest

from app.services import unit_conversion_service as ucs


def test_normalize_unit_handles_synonyms_and_case():
    assert ucs.normalize_unit("Tbsp") == "tbsp"
    assert ucs.normalize_unit("TABLESPOON") == "tbsp"
    assert ucs.normalize_unit("tablespoons") == "tbsp"
    assert ucs.normalize_unit("Cups") == "cup"
    assert ucs.normalize_unit("lbs") == "lb"
    assert ucs.normalize_unit("Fl. Oz.") == "fl_oz"


def test_normalize_unit_none_and_empty():
    assert ucs.normalize_unit(None) is None
    assert ucs.normalize_unit("") is None


def test_normalize_unit_passes_through_count_units():
    assert ucs.normalize_unit("clove") == "clove"
    assert ucs.normalize_unit("can") == "can"


def test_unit_family():
    assert ucs.unit_family("cup") == "volume"
    assert ucs.unit_family("tablespoons") == "volume"
    assert ucs.unit_family("lb") == "mass"
    assert ucs.unit_family("grams") == "mass"
    assert ucs.unit_family("clove") == "count"
    assert ucs.unit_family(None) == "count"


def test_convert_volume_to_volume():
    result = ucs.convert(1, "cup", "tbsp")
    assert result is not None
    assert result.unit == "tbsp"
    assert result.quantity == pytest.approx(16, abs=0.05)
    assert result.used_density is False


def test_convert_mass_to_mass():
    result = ucs.convert(1, "lb", "g")
    assert result.unit == "g"
    assert result.quantity == pytest.approx(453.592, abs=0.01)


def test_convert_handles_synonym_units():
    result = ucs.convert(2, "tablespoons", "teaspoon")
    assert result.unit == "tsp"
    assert result.quantity == pytest.approx(6, abs=0.05)


def test_convert_returns_none_for_count_units():
    assert ucs.convert(2, "clove", "tsp") is None
    assert ucs.convert(2, "cup", "clove") is None


def test_convert_returns_none_cross_family_without_density():
    assert ucs.convert(1, "cup", "g") is None


def test_convert_cross_family_with_density_volume_to_mass():
    # Water: ~1 g/mL. 1 cup (236.588 mL) -> ~236.6g
    result = ucs.convert(1, "cup", "g", density_g_per_ml=1.0)
    assert result is not None
    assert result.used_density is True
    assert result.quantity == pytest.approx(236.588, abs=0.5)


def test_convert_cross_family_with_density_mass_to_volume():
    result = ucs.convert(236.588, "g", "cup", density_g_per_ml=1.0)
    assert result.quantity == pytest.approx(1.0, abs=0.01)


def test_convert_rejects_non_positive_density():
    assert ucs.convert(1, "cup", "g", density_g_per_ml=0) is None
    assert ucs.convert(1, "cup", "g", density_g_per_ml=-1) is None


def test_convert_returns_none_for_unknown_units():
    assert ucs.convert(1, None, "cup") is None
    assert ucs.convert(1, "cup", None) is None


def test_units_are_comparable():
    assert ucs.units_are_comparable("cup", "tbsp") is True
    assert ucs.units_are_comparable("g", "lb") is True
    assert ucs.units_are_comparable("cup", "g") is False
    assert ucs.units_are_comparable("cup", "g", have_density=True) is True
    assert ucs.units_are_comparable("clove", "cup") is False
    assert ucs.units_are_comparable("clove", "clove") is False  # count vs count -- still not comparable


# --- Display-unit conversion (backlog B10.5) -----------------------------


def test_pick_volume_unit_metric_switches_to_liters_at_1000ml():
    assert ucs._pick_volume_unit_metric(999) == "ml"
    assert ucs._pick_volume_unit_metric(1000) == "l"


def test_pick_mass_unit_metric_switches_to_kg_at_1000g():
    assert ucs._pick_mass_unit_metric(999) == "g"
    assert ucs._pick_mass_unit_metric(1000) == "kg"


def test_pick_volume_unit_imperial_picks_largest_unit_at_least_one():
    assert ucs._pick_volume_unit_imperial(1) == "tsp"  # tiny amount -- smallest unit
    assert ucs._pick_volume_unit_imperial(ucs.VOLUME_TO_ML["tbsp"]) == "tbsp"
    assert ucs._pick_volume_unit_imperial(ucs.VOLUME_TO_ML["cup"]) == "cup"
    assert ucs._pick_volume_unit_imperial(ucs.VOLUME_TO_ML["gal"] * 2) == "gal"


def test_pick_mass_unit_imperial_switches_to_lb_at_one_pound():
    assert ucs._pick_mass_unit_imperial(ucs.MASS_TO_G["lb"] - 1) == "oz"
    assert ucs._pick_mass_unit_imperial(ucs.MASS_TO_G["lb"]) == "lb"


def test_convert_for_display_count_unit_passthrough():
    result = ucs.convert_for_display(2, "clove", "metric")
    assert result.quantity == 2
    assert result.unit == "clove"
    assert result.used_density is False


def test_convert_for_display_metric_volume_and_mass():
    result = ucs.convert_for_display(1, "cup", "metric")
    assert result.unit == "ml"
    assert result.quantity == pytest.approx(236.588, abs=0.01)

    result = ucs.convert_for_display(1, "lb", "metric")
    assert result.unit == "g"
    assert result.quantity == pytest.approx(453.592, abs=0.01)


def test_convert_for_display_metric_large_quantity_uses_l_and_kg():
    result = ucs.convert_for_display(5, "cup", "metric")
    assert result.unit == "l"
    result = ucs.convert_for_display(5, "lb", "metric")
    assert result.unit == "kg"


def test_convert_for_display_imperial_volume_and_mass():
    result = ucs.convert_for_display(500, "ml", "imperial")
    assert result.unit in ucs.IMPERIAL_VOLUME_LADDER

    result = ucs.convert_for_display(500, "g", "imperial")
    assert result.unit == "lb"


def test_convert_for_display_weight_mode_mass_needs_no_density():
    result = ucs.convert_for_display(1, "lb", "weight")
    assert result.unit == "g"
    assert result.used_density is False
    assert result.quantity == pytest.approx(453.592, abs=0.01)


def test_convert_for_display_weight_mode_volume_with_density():
    result = ucs.convert_for_display(2, "cup", "weight", density_g_per_ml=0.529)
    assert result.unit == "g"
    assert result.used_density is True
    assert result.quantity == pytest.approx(2 * 236.588 * 0.529, abs=0.5)


def test_convert_for_display_weight_mode_volume_without_density_is_unavailable():
    assert ucs.convert_for_display(2, "cup", "weight", density_g_per_ml=None) is None
    assert ucs.convert_for_display(2, "cup", "weight", density_g_per_ml=0) is None


def test_convert_for_display_unknown_system_returns_none():
    assert ucs.convert_for_display(1, "cup", "bogus") is None
