"""Unit tests for backlog B5.5's pantry-staple exclusion:
meal_plan_service.is_pantry_staple (the pure matcher) and its wiring into
subtract_inventory/compute_grocery_list -- a household-declared staple
should never appear on the grocery list, regardless of stated quantity
or whether anything matching is currently tracked in inventory (a
stronger exclusion than the pre-existing "no quantity + inventory match"
omission this sits alongside).
"""

from __future__ import annotations

from datetime import date

from app.models import HouseholdPreferences, InventoryItem, MealPlan, MealPlanEntry, Recipe, RecipeIngredient
from app.services import meal_plan_service

# --- is_pantry_staple: pure matcher -----------------------------------------


def test_no_staples_configured_never_matches():
    assert meal_plan_service.is_pantry_staple("salt", None) is False
    assert meal_plan_service.is_pantry_staple("salt", []) is False


def test_exact_match_case_insensitive():
    assert meal_plan_service.is_pantry_staple("Salt", ["salt", "pepper"]) is True


def test_a_generic_staple_covers_a_more_specific_ingredient():
    """A household writes staples generically and means them to cover the
    specific spellings recipes use."""
    assert meal_plan_service.is_pantry_staple("kosher salt", ["salt"]) is True
    assert meal_plan_service.is_pantry_staple("black pepper", ["pepper"]) is True


def test_a_specific_staple_does_not_cover_a_generic_ingredient():
    """Rewritten 2026-08-03 for audit P1-5. This asserted the opposite --
    that a staple of "olive oil" suppressed a grocery line for plain
    "oil" -- because matching was substring-in-either-direction.

    The new behaviour is deliberate, not incidental. A staple hit removes
    the line from the grocery list ENTIRELY, with nothing on screen to
    notice, so the failure mode is arriving at the stove without an
    ingredient. "Olive oil is always on hand" is not a claim that any oil
    a recipe asks for is on hand -- the recipe may well mean canola. The
    line stays, and buying oil you did not strictly need is the
    recoverable half of the trade."""
    assert meal_plan_service.is_pantry_staple("oil", ["olive oil"]) is False


def test_a_derived_product_is_not_covered_by_a_staple_for_its_source():
    """The failure this whole layer exists for: substring matching had a
    staple of "oil" suppressing a grocery line for "oil-packed tuna", and
    a staple of "chicken" suppressing "chicken broth"."""
    assert meal_plan_service.is_pantry_staple("oil-packed tuna", ["oil"]) is False
    assert meal_plan_service.is_pantry_staple("chicken broth", ["chicken"]) is False
    assert meal_plan_service.is_pantry_staple("eggplant", ["egg"]) is False


def test_non_matching_ingredient_returns_false():
    assert meal_plan_service.is_pantry_staple("chicken breast", ["salt", "pepper", "olive oil"]) is False


def test_blank_entries_in_staples_list_are_ignored():
    assert meal_plan_service.is_pantry_staple("salt", ["", "  ", "salt"]) is True
    assert meal_plan_service.is_pantry_staple("chicken", ["", "  "]) is False


# --- subtract_inventory wiring -----------------------------------------


def _item(name, quantity, unit):
    return InventoryItem(name=name, quantity=quantity, unit=unit, category="pantry")


def test_staple_with_quantity_and_no_inventory_is_still_excluded():
    # Without pantry_staples this would normally appear (no quantity-less
    # exemption applies, and there's no inventory match either) -- this
    # is the actual behavior change B5.5 adds.
    aggregated = [{"ingredient_name": "olive oil", "quantity": 2, "unit": "tbsp"}]
    result = meal_plan_service.subtract_inventory(aggregated, [], pantry_staples=["olive oil"])
    assert result == []


def test_staple_excluded_even_when_inventory_would_leave_a_remainder():
    # A tiny amount on hand would normally still leave a remaining need
    # to buy -- the staple exclusion short-circuits before that math runs
    # at all.
    aggregated = [{"ingredient_name": "black pepper", "quantity": 1, "unit": "tbsp"}]
    inventory = [_item("black pepper", 0.1, "tbsp")]
    result = meal_plan_service.subtract_inventory(aggregated, inventory, pantry_staples=["pepper"])
    assert result == []


def test_quantity_less_staple_excluded():
    aggregated = [{"ingredient_name": "salt", "quantity": None, "unit": None}]
    result = meal_plan_service.subtract_inventory(aggregated, [], pantry_staples=["salt"])
    assert result == []


def test_non_staple_ingredient_unaffected_by_staples_list():
    aggregated = [{"ingredient_name": "chicken breast", "quantity": 2, "unit": "lb"}]
    result = meal_plan_service.subtract_inventory(aggregated, [], pantry_staples=["salt", "pepper"])
    assert len(result) == 1
    assert result[0]["ingredient_name"] == "chicken breast"


def test_default_pantry_staples_none_preserves_old_behavior():
    # No third argument at all -- every pre-existing caller/test of
    # subtract_inventory (test_unit_conversion_wiring.py, etc.) must be
    # completely unaffected by this feature.
    aggregated = [{"ingredient_name": "salt", "quantity": None, "unit": None}]
    result = meal_plan_service.subtract_inventory(aggregated, [])
    # category is "spice" here (guess_grocery_category's own keyword
    # match for "salt"), not None -- see test_grocery_category.py for
    # dedicated coverage of that guesser.
    assert result == [{"ingredient_name": "salt", "quantity": None, "unit": None, "category": "spice"}]


# --- compute_grocery_list: real DB-backed wiring ----------------------------


def _recipe(ingredients: list[tuple[str, float, str]]) -> Recipe:
    recipe = Recipe(title="Test Recipe", default_servings=2)
    recipe.ingredients = [
        RecipeIngredient(ingredient_name=name, quantity=qty, unit=unit) for name, qty, unit in ingredients
    ]
    return recipe


def _entry(day_of_week: int, recipe: Recipe, servings: int = 2) -> MealPlanEntry:
    entry = MealPlanEntry(day_of_week=day_of_week, servings=servings)
    entry.recipe = recipe
    return entry


def test_compute_grocery_list_excludes_configured_staples(db_session):
    db_session.add(
        HouseholdPreferences(household_size=2, dietary_restrictions=[], pantry_staples=["salt", "olive oil"])
    )
    recipe = _recipe([("chicken breast", 2, "lb"), ("salt", 1, "tsp"), ("olive oil", 2, "tbsp")])
    db_session.add(MealPlan(week_start_date=date(2026, 8, 10), entries=[_entry(0, recipe)]))
    db_session.commit()

    plan = db_session.query(MealPlan).first()
    grocery_list = meal_plan_service.compute_grocery_list(db_session, plan)

    names = {item["ingredient_name"] for item in grocery_list}
    assert names == {"chicken breast"}


def test_compute_grocery_list_no_staples_configured_includes_everything(db_session):
    db_session.add(HouseholdPreferences(household_size=2, dietary_restrictions=[], pantry_staples=[]))
    recipe = _recipe([("chicken breast", 2, "lb"), ("salt", 1, "tsp")])
    db_session.add(MealPlan(week_start_date=date(2026, 8, 10), entries=[_entry(0, recipe)]))
    db_session.commit()

    plan = db_session.query(MealPlan).first()
    grocery_list = meal_plan_service.compute_grocery_list(db_session, plan)

    names = {item["ingredient_name"] for item in grocery_list}
    assert names == {"chicken breast", "salt"}
