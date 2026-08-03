"""Tests for the B5.3 wiring of unit_conversion_service into
meal_plan_service's grocery-list math and inventory_service's name-based
deduction -- the actual behavior change, not just the standalone
conversion table (see test_unit_conversion_service.py for that).
"""
from __future__ import annotations

from app.models import InventoryItem
from app.services import inventory_service, meal_plan_service


# --- aggregate_ingredients: cross-unit merging --------------------------


def test_aggregate_merges_convertible_units_for_same_ingredient():
    lists = [
        [{"ingredient_name": "flour", "quantity": 2, "unit": "cup"}],
        [{"ingredient_name": "flour", "quantity": 8, "unit": "tbsp"}],  # = 0.5 cup
    ]
    result = meal_plan_service.aggregate_ingredients(lists)
    assert len(result) == 1
    assert result[0]["ingredient_name"] == "flour"
    assert result[0]["unit"] == "cup"
    assert result[0]["quantity"] == pytest_approx(2.5)


def test_aggregate_keeps_incompatible_units_as_separate_lines():
    lists = [
        [{"ingredient_name": "chicken", "quantity": 2, "unit": "cup"}],
        [{"ingredient_name": "chicken", "quantity": 1, "unit": "lb"}],  # no density -> not mergeable
    ]
    result = meal_plan_service.aggregate_ingredients(lists)
    assert len(result) == 2
    units = {r["unit"] for r in result}
    assert units == {"cup", "lb"}


def test_aggregate_exact_same_unit_still_merges_as_before():
    lists = [
        [{"ingredient_name": "rice", "quantity": 1, "unit": "cup"}],
        [{"ingredient_name": "rice", "quantity": 2, "unit": "cup"}],
    ]
    result = meal_plan_service.aggregate_ingredients(lists)
    assert len(result) == 1
    assert result[0]["quantity"] == 3


def test_aggregate_count_units_still_merge_when_identical():
    lists = [
        [{"ingredient_name": "eggs", "quantity": 2, "unit": "whole"}],
        [{"ingredient_name": "eggs", "quantity": 3, "unit": "whole"}],
    ]
    result = meal_plan_service.aggregate_ingredients(lists)
    assert len(result) == 1
    assert result[0]["quantity"] == 5


def test_aggregate_no_quantity_ingredient_unchanged():
    lists = [[{"ingredient_name": "salt", "quantity": None, "unit": None}]]
    result = meal_plan_service.aggregate_ingredients(lists)
    assert result == [{"ingredient_name": "salt", "unit": None, "quantity": None}]


# --- subtract_inventory: unit-aware on-hand comparison -------------------


def _item(name, quantity, unit):
    return InventoryItem(name=name, quantity=quantity, unit=unit, category="pantry")


def test_subtract_inventory_converts_on_hand_into_grocery_units():
    aggregated = [{"ingredient_name": "chicken", "quantity": 2, "unit": "lb"}]
    inventory = [_item("chicken", 500, "g")]  # ~1.10231 lb on hand
    result = meal_plan_service.subtract_inventory(aggregated, inventory)
    assert len(result) == 1
    assert result[0]["unit"] == "lb"
    assert result[0]["quantity"] == pytest_approx(2 - 500 / 453.592, tol=0.01)


def test_subtract_inventory_fully_covered_by_converted_on_hand():
    # Volume<->volume conversion (no density needed): 32 tbsp = 2 cups on
    # hand, which fully covers a 1-cup grocery need.
    aggregated = [{"ingredient_name": "milk", "quantity": 1, "unit": "cup"}]
    inventory = [_item("milk", 32, "tbsp")]
    result = meal_plan_service.subtract_inventory(aggregated, inventory)
    assert result == []  # fully covered, nothing left to buy


def test_subtract_inventory_keeps_the_line_when_units_are_unconvertible():
    """Inventory logged in a count unit against a volume-denominated
    grocery line cannot be reconciled, so the full quantity stays on the
    list with an explanation attached.

    This replaced a test asserting the opposite. The old behaviour
    compared the raw numbers when conversion failed -- 2 cup needed
    against 3 whole on hand computed 2 - 3 = -1, which is <= 0, so the
    line was dropped as "fully covered". Worse in the reverse direction:
    "2 lb chicken" against a "500 g" row computed 2 - 500 and silently
    removed chicken from the shopping list. Buying a little extra is
    recoverable; not buying dinner is not."""
    aggregated = [{"ingredient_name": "onion", "quantity": 2, "unit": "cup"}]
    inventory = [_item("onion", 3, "whole")]
    result = meal_plan_service.subtract_inventory(aggregated, inventory)
    assert len(result) == 1
    assert result[0]["quantity"] == 2
    assert "can't be compared automatically" in result[0]["needs_review"]


def test_subtract_inventory_keeps_mass_line_when_on_hand_is_in_volume():
    # The case that actually bit: a mass-denominated need against a
    # volume-denominated row, where the raw comparison produced a large
    # negative and dropped the item entirely.
    aggregated = [{"ingredient_name": "chicken", "quantity": 2, "unit": "lb"}]
    inventory = [_item("chicken", 500, "cup")]
    result = meal_plan_service.subtract_inventory(aggregated, inventory)
    assert len(result) == 1
    assert result[0]["quantity"] == 2


def test_subtract_inventory_same_unit_unchanged_behavior():
    aggregated = [{"ingredient_name": "rice", "quantity": 3, "unit": "cup"}]
    inventory = [_item("rice", 1, "cup")]
    result = meal_plan_service.subtract_inventory(aggregated, inventory)
    # Backlog B5.4 -- category now rides along, sourced from the matched
    # inventory row's own category (see test_grocery_category.py for
    # dedicated coverage of that behavior).
    assert result == [{"ingredient_name": "rice", "quantity": 2, "unit": "cup", "category": "pantry"}]


def test_subtract_inventory_no_match_uses_full_quantity():
    aggregated = [{"ingredient_name": "saffron", "quantity": 1, "unit": "tsp"}]
    result = meal_plan_service.subtract_inventory(aggregated, [])
    # "saffron" matches none of guess_grocery_category's keyword lists,
    # so category is None here -- see test_grocery_category.py.
    assert result == [{"ingredient_name": "saffron", "quantity": 1, "unit": "tsp", "category": None}]


# --- inventory_service.deduct_by_name: unit-aware deduction --------------


def test_deduct_by_name_converts_quantity_into_item_unit(db_session):
    item = InventoryItem(name="butter", quantity=1, unit="lb", category="pantry")
    db_session.add(item)
    db_session.commit()

    # Recipe calls for 8 oz of butter -- item is logged in lb.
    updated = inventory_service.deduct_by_name(db_session, "butter", 8, "oz")
    assert updated is not None
    assert updated.quantity == pytest_approx(1 - 8 / 16, tol=0.01)


def test_deduct_by_name_same_unit_unchanged_behavior(db_session):
    item = InventoryItem(name="rice", quantity=5, unit="cup", category="pantry")
    db_session.add(item)
    db_session.commit()
    updated = inventory_service.deduct_by_name(db_session, "rice", 2, "cup")
    assert updated.quantity == 3


def test_deduct_by_name_treats_a_missing_unit_as_already_in_the_item_unit(db_session):
    # No unit stated by the caller means there is nothing to reconcile --
    # the quantity is taken at face value, which is the only sensible
    # reading of "use 2 onions" against a row counted in whole onions.
    item = InventoryItem(name="onion", quantity=5, unit="whole", category="pantry")
    db_session.add(item)
    db_session.commit()
    updated = inventory_service.deduct_by_name(db_session, "onion", 2, None)
    assert updated.quantity == 3


def test_deduct_by_name_refuses_to_deduct_across_unconvertible_units(db_session):
    """A stated unit that cannot be converted into the row's own unit
    leaves the quantity alone and only marks the item as used.

    This replaced a test asserting the opposite. The old behaviour
    subtracted the raw numbers regardless of unit, so confirming a recipe
    calling for "2 cup flour" against a "5 lb flour" row wrote 3 lb to the
    database -- a wrong number, with nothing telling the user it had
    happened. A stale count the user can see beats a confident wrong one."""
    item = InventoryItem(name="pepper", quantity=5, unit="whole", category="pantry")
    db_session.add(item)
    db_session.commit()
    updated = inventory_service.deduct_by_name(db_session, "pepper", 2, "cup")
    assert updated.quantity == 5  # unchanged -- not 3
    assert updated.last_used_date is not None  # but we know it was used


def test_deduct_by_name_no_quantity_still_decrements_by_one(db_session):
    item = InventoryItem(name="lemon", quantity=3, unit="whole", category="pantry")
    db_session.add(item)
    db_session.commit()
    updated = inventory_service.deduct_by_name(db_session, "lemon")
    assert updated.quantity == 2


def pytest_approx(value, tol=0.001):
    import pytest

    return pytest.approx(value, abs=tol)
