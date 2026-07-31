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
