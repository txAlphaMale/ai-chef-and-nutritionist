"""Unit tests for backlog B6.1's cost calculation
(app.services.cost_service): per-ingredient price resolution against
currently-tracked inventory unit_price data, and the recipe/grocery-list
rollups built on top of it. Needs a real db_session since price
resolution queries InventoryItem directly (same reason
test_meal_plan_leftovers.py/test_pantry_staples.py's DB-backed tests do).
"""
from __future__ import annotations

from datetime import date

from app.models import GroceryListItem, InventoryItem, MealPlan, MealPlanEntry, Recipe, RecipeIngredient
from app.services import cost_service


def _priced_item(name, quantity, unit, unit_price, purchased_date=None, purchased_quantity=None):
    return InventoryItem(
        name=name,
        quantity=quantity,
        unit=unit,
        unit_price=unit_price,
        purchased_date=purchased_date,
        purchased_quantity=purchased_quantity,
        category="pantry",
    )


# --- compute_ingredient_line_cost -----------------------------------------


def test_no_priced_match_is_unresolved(db_session):
    line = cost_service.compute_ingredient_line_cost(db_session, "saffron", 1, "tsp")
    assert line["resolved"] is False
    assert line["line_cost"] is None
    assert "no priced inventory purchase" in line["note"]


def test_same_unit_line_cost(db_session):
    # $6.00 for 2 lb of chicken -> $3.00/lb -> 1.5 lb costs $4.50
    db_session.add(_priced_item("chicken breast", 2, "lb", 6.00))
    db_session.commit()
    line = cost_service.compute_ingredient_line_cost(db_session, "chicken breast", 1.5, "lb")
    assert line["resolved"] is True
    assert line["unit_cost"] == 3.0
    assert line["line_cost"] == 4.5
    assert line["matched_item_name"] == "chicken breast"


def test_convertible_unit_mismatch_still_resolves(db_session):
    # $4.00 for 1 lb of butter -> recipe needs 8 oz (= 0.5 lb) -> $2.00
    db_session.add(_priced_item("butter", 1, "lb", 4.00))
    db_session.commit()
    line = cost_service.compute_ingredient_line_cost(db_session, "butter", 8, "oz")
    assert line["resolved"] is True
    assert line["line_cost"] == 2.0


def test_unconvertible_unit_mismatch_is_unresolved_not_guessed(db_session):
    # Inventory logged in a count unit against a volume-needed ingredient --
    # no density, so this must NOT silently multiply raw numbers together.
    db_session.add(_priced_item("onion", 3, "whole", 1.50))
    db_session.commit()
    line = cost_service.compute_ingredient_line_cost(db_session, "onion", 2, "cup")
    assert line["resolved"] is False
    assert line["line_cost"] is None
    assert "isn't convertible" in line["note"]


def test_quantity_less_ingredient_with_a_priced_match_reports_unit_cost_but_no_line_cost(db_session):
    db_session.add(_priced_item("salt", 26, "oz", 2.60))
    db_session.commit()
    line = cost_service.compute_ingredient_line_cost(db_session, "salt", None, None)
    assert line["resolved"] is False
    assert line["line_cost"] is None
    assert line["unit_cost"] == 0.1  # $2.60 / 26 oz
    assert "no stated quantity" in line["note"]


def test_prefers_most_recently_purchased_priced_match(db_session):
    db_session.add_all(
        [
            _priced_item("rice", 5, "lb", 3.00, purchased_date=date(2026, 6, 1)),  # $0.60/lb, older
            _priced_item("rice", 5, "lb", 6.00, purchased_date=date(2026, 8, 1)),  # $1.20/lb, more recent
        ]
    )
    db_session.commit()
    line = cost_service.compute_ingredient_line_cost(db_session, "rice", 1, "lb")
    assert line["unit_cost"] == 1.20


def test_zero_quantity_matched_item_is_unresolved(db_session):
    db_session.add(_priced_item("flour", 0, "lb", 3.00))
    db_session.commit()
    line = cost_service.compute_ingredient_line_cost(db_session, "flour", 1, "lb")
    assert line["resolved"] is False
    assert "quantity is zero" in line["note"]


def test_uses_purchased_quantity_not_depleted_on_hand_quantity(db_session):
    # Bug fix (2026-08-02, author-flagged design concern): $6.00 paid for
    # 2 lb of chicken breast, purchased_quantity=2 (the immutable
    # snapshot). Half has since been used, so `quantity` (on hand) has
    # dropped to 1 lb -- the unit cost must still be $3.00/lb (6.00/2),
    # NOT $6.00/lb (6.00/1), which is what dividing by the live,
    # depleted `quantity` used to produce.
    db_session.add(_priced_item("chicken breast", 1, "lb", 6.00, purchased_quantity=2))
    db_session.commit()
    line = cost_service.compute_ingredient_line_cost(db_session, "chicken breast", 1, "lb")
    assert line["resolved"] is True
    assert line["unit_cost"] == 3.0
    assert line["line_cost"] == 3.0


def test_falls_back_to_quantity_when_purchased_quantity_is_unset(db_session):
    # Rows created before purchased_quantity existed (or from an intake
    # source with no purchase concept) have no better signal -- must
    # keep behaving exactly as before this fix, not fail or return None.
    db_session.add(_priced_item("chicken breast", 2, "lb", 6.00, purchased_quantity=None))
    db_session.commit()
    line = cost_service.compute_ingredient_line_cost(db_session, "chicken breast", 1, "lb")
    assert line["resolved"] is True
    assert line["unit_cost"] == 3.0


# --- compute_recipe_cost ----------------------------------------------------


def _recipe(ingredients, default_servings=2):
    recipe = Recipe(title="Test Recipe", default_servings=default_servings)
    recipe.ingredients = [
        RecipeIngredient(ingredient_name=name, quantity=qty, unit=unit) for name, qty, unit in ingredients
    ]
    return recipe


def test_recipe_cost_fully_resolved_is_computed(db_session):
    db_session.add(_priced_item("chicken breast", 2, "lb", 6.00))
    db_session.commit()
    recipe = _recipe([("chicken breast", 1, "lb")], default_servings=2)
    result = cost_service.compute_recipe_cost(db_session, recipe)
    assert result["provenance"] == "computed"
    assert result["total_cost"] == 3.0
    assert result["cost_per_serving"] == 1.5
    assert result["resolved_count"] == 1
    assert result["total_count"] == 1


def test_recipe_cost_partially_resolved(db_session):
    db_session.add(_priced_item("chicken breast", 2, "lb", 6.00))
    db_session.commit()
    recipe = _recipe([("chicken breast", 1, "lb"), ("saffron", 1, "tsp")], default_servings=2)
    result = cost_service.compute_recipe_cost(db_session, recipe)
    assert result["provenance"] == "partial"
    assert result["total_cost"] == 3.0  # only the resolved line counts
    assert result["cost_per_serving"] == 1.5
    assert result["resolved_count"] == 1
    assert result["total_count"] == 2


def test_recipe_cost_no_data_when_nothing_resolves(db_session):
    recipe = _recipe([("saffron", 1, "tsp")], default_servings=2)
    result = cost_service.compute_recipe_cost(db_session, recipe)
    assert result["provenance"] == "no_data"
    assert result["total_cost"] is None
    assert result["cost_per_serving"] is None


def test_recipe_cost_no_ingredients_is_no_data(db_session):
    recipe = _recipe([], default_servings=2)
    result = cost_service.compute_recipe_cost(db_session, recipe)
    assert result["provenance"] == "no_data"
    assert result["total_count"] == 0


# --- compute_grocery_list_cost ----------------------------------------------


def test_grocery_list_cost_excludes_purchased_items(db_session):
    db_session.add(_priced_item("chicken breast", 2, "lb", 6.00))
    db_session.commit()
    items = [
        GroceryListItem(ingredient_name="chicken breast", quantity=1, unit="lb", is_purchased=False),
        GroceryListItem(ingredient_name="chicken breast", quantity=1, unit="lb", is_purchased=True),
    ]
    result = cost_service.compute_grocery_list_cost(db_session, items)
    assert result["total_count"] == 1  # the purchased line was never even priced
    assert result["total_cost"] == 3.0


def test_grocery_list_cost_end_to_end_via_compute_grocery_list_context(db_session):
    from app.models import HouseholdPreferences

    db_session.add(HouseholdPreferences(household_size=2, dietary_restrictions=[]))
    # Only 0.1 lb on hand (priced at $3, i.e. $30/lb) -- NOT enough to
    # cover the recipe's 1 lb need, so 0.9 lb still lands on the grocery
    # list, while the same row still supplies a real price for it. Using
    # an inventory row that fully covers the need would make
    # compute_grocery_list return nothing to buy at all, which would
    # test subtract_inventory's own coverage logic, not this wiring.
    db_session.add(_priced_item("chicken breast", 0.1, "lb", 3.00))
    recipe = _recipe([("chicken breast", 1, "lb")], default_servings=2)
    db_session.add(MealPlan(week_start_date=date(2026, 8, 10), entries=[MealPlanEntry(day_of_week=0, servings=2, recipe=recipe)]))
    db_session.commit()

    from app.services import meal_plan_service

    plan = db_session.query(MealPlan).first()
    grocery_list = meal_plan_service.compute_grocery_list(db_session, plan)
    assert len(grocery_list) == 1  # 0.9 lb still needed, confirms the setup itself is right
    _COLUMNS = {"ingredient_name", "quantity", "unit"}
    items = [GroceryListItem(**{k: v for k, v in item.items() if k in _COLUMNS}) for item in grocery_list]
    result = cost_service.compute_grocery_list_cost(db_session, items)
    assert result["provenance"] == "computed"
    assert result["total_cost"] == 27.0  # 0.9 lb remaining * $30/lb
