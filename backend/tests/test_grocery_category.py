"""Tests for the B5.4 grocery-list category/aisle grouping:
meal_plan_service.guess_grocery_category (the pure keyword guesser) and
subtract_inventory's category-attachment behavior (a real inventory
match's own category always wins over a guess -- see
test_unit_conversion_wiring.py for the two pre-existing subtract_inventory
tests updated alongside this feature).
"""
from __future__ import annotations

from app.models import InventoryItem
from app.services import meal_plan_service

# --- guess_grocery_category ----------------------------------------------


def test_guess_produce():
    assert meal_plan_service.guess_grocery_category("Fresh Tomato") == "produce"


def test_guess_fridge():
    assert meal_plan_service.guess_grocery_category("cheddar cheese") == "fridge"


def test_guess_freezer():
    assert meal_plan_service.guess_grocery_category("frozen peas") == "freezer"


def test_guess_spice():
    assert meal_plan_service.guess_grocery_category("ground cumin") == "spice"


def test_guess_pantry():
    assert meal_plan_service.guess_grocery_category("all-purpose flour") == "pantry"


def test_guess_case_insensitive():
    assert meal_plan_service.guess_grocery_category("MILK") == "fridge"


def test_guess_no_match_returns_none():
    assert meal_plan_service.guess_grocery_category("saffron") is None


def test_guess_empty_name_returns_none():
    assert meal_plan_service.guess_grocery_category("") is None
    assert meal_plan_service.guess_grocery_category(None) is None


def test_guess_bell_pepper_resolves_produce_not_spice():
    # The exact ambiguity this feature's own module docstring calls out --
    # "bell pepper" (produce) vs. a bare "pepper" that would otherwise
    # false-match the spice-aisle "black pepper"/"white pepper" entries.
    assert meal_plan_service.guess_grocery_category("bell pepper") == "produce"


# --- subtract_inventory: category attachment ------------------------------


def _item(name, quantity, unit, category):
    return InventoryItem(name=name, quantity=quantity, unit=unit, category=category)


def test_subtract_inventory_uses_matched_items_own_category_over_a_guess():
    # "flour" would guess "pantry" via keywords, but this household has
    # logged it under a different category -- the real inventory row
    # wins, since it's a better signal than any keyword list.
    aggregated = [{"ingredient_name": "flour", "quantity": 5, "unit": "cup"}]
    inventory = [_item("flour", 1, "cup", "other")]
    result = meal_plan_service.subtract_inventory(aggregated, inventory)
    assert result[0]["category"] == "other"


def test_subtract_inventory_falls_back_to_guess_when_no_inventory_match():
    aggregated = [{"ingredient_name": "frozen peas", "quantity": 1, "unit": "cup"}]
    result = meal_plan_service.subtract_inventory(aggregated, [])
    assert result[0]["category"] == "freezer"


def test_subtract_inventory_no_quantity_line_still_gets_a_category():
    # The "salt to taste" (quantity=None) branch is a separate code path
    # from the quantity-bearing one -- make sure it wasn't missed.
    aggregated = [{"ingredient_name": "salt", "quantity": None, "unit": None}]
    result = meal_plan_service.subtract_inventory(aggregated, [])
    assert result == [{"ingredient_name": "salt", "quantity": None, "unit": None, "category": "spice"}]
