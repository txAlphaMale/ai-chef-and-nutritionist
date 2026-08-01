"""Backlog B6.1: cost per recipe, cost per serving, and a projected
grocery-list spend, computed LIVE from currently-tracked inventory
`unit_price` data rather than persisted on the recipe the way nutrition
is. Unlike nutrition, a grocery price is inherently time-varying --
today's chicken breast price isn't a fixed property of a recipe the way
its nutrient content roughly is -- so caching this on `Recipe` would go
stale in a way `Recipe.nutrition_provenance` doesn't. Every result
honestly reports how many ingredients actually had resolvable price data
and never guesses a missing price, the same discipline B1.2's nutrition
computed/partial/ai_estimated split already established for this app.

Price source and its exact semantics matter here: `InventoryItem.unit_price`
is "price paid for THIS ROW'S WHOLE QUANTITY as purchased" (see the
model's own docstring), not a normalized per-single-unit price -- so the
actual $/unit signal this module needs is `unit_price / quantity`,
converted into the ingredient's requested unit via
`unit_conversion_service` when the two differ (same convention already
used by `inventory_service.deduct_by_name`/`meal_plan_service.subtract_inventory`).
When a household has bought the same ingredient more than once at
different prices, the most recently purchased PRICED row is used -- a
recent real price is a better cost signal than an old one, and never
averaged or otherwise invented.

Matching itself reuses the exact same case-insensitive
exact-then-substring convention as `inventory_service.find_by_name`,
restricted to rows that actually carry a `unit_price` -- so a name match
that's more expensive/matters more for accuracy doesn't silently win
over a cheaper matched row just because it happened first.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import GroceryListItem, InventoryItem, Recipe
from app.services import unit_conversion_service

# Recipe.nutrition_provenance's three-way split, reused here for the same
# "computed / some-but-not-all-resolved / none-resolved" reporting shape
# rather than inventing a fourth vocabulary for the same concept.
PROVENANCE_COMPUTED = "computed"
PROVENANCE_PARTIAL = "partial"
PROVENANCE_NO_DATA = "no_data"


def _find_priced_inventory_match(db: Session, ingredient_name: str) -> InventoryItem | None:
    """Same exact-then-substring, case-insensitive name matching as
    inventory_service.find_by_name, but restricted to rows that actually
    have a unit_price recorded, and preferring the most recently
    purchased priced match when more than one exists (a row with no
    purchased_date sorts last -- an unknown purchase date is a weaker
    signal than a known recent one, not a reason to prefer it)."""
    name_lower = (ingredient_name or "").strip().lower()
    if not name_lower:
        return None

    base = db.query(InventoryItem).filter(InventoryItem.unit_price.isnot(None))
    order = (InventoryItem.purchased_date.is_(None), InventoryItem.purchased_date.desc(), InventoryItem.id.desc())

    exact = base.filter(InventoryItem.name.ilike(name_lower)).order_by(*order).first()
    if exact is not None:
        return exact
    return base.filter(InventoryItem.name.ilike(f"%{name_lower}%")).order_by(*order).first()


def compute_ingredient_line_cost(
    db: Session, ingredient_name: str, quantity: float | None, unit: str | None
) -> dict:
    """Returns a per-ingredient cost line: {ingredient_name, quantity,
    unit, resolved, unit_cost (dollars per single `unit`, once matched),
    line_cost, matched_item_name, note}. `resolved` is False (never a
    guessed number) whenever: nothing in inventory has ever been priced
    under a matching name, the ingredient has no stated quantity (e.g.
    "salt to taste" -- there's a real unit cost but nothing to multiply
    it by), or the matched row's unit and the ingredient's requested unit
    aren't convertible into each other."""
    base = {
        "ingredient_name": ingredient_name,
        "quantity": quantity,
        "unit": unit,
        "resolved": False,
        "unit_cost": None,
        "line_cost": None,
        "matched_item_name": None,
        "note": None,
    }

    match = _find_priced_inventory_match(db, ingredient_name)
    if match is None:
        return {**base, "note": "no priced inventory purchase on record for this ingredient"}
    if not match.quantity:
        return {**base, "note": f"matched {match.name!r} but its own quantity is zero/unknown"}

    price_per_match_unit = match.unit_price / match.quantity

    if quantity is None:
        return {
            **base,
            "unit_cost": round(price_per_match_unit, 4),
            "matched_item_name": match.name,
            "note": "ingredient has no stated quantity (e.g. \"to taste\")",
        }

    qty_in_match_unit = quantity
    if unit and match.unit and unit_conversion_service.normalize_unit(unit) != unit_conversion_service.normalize_unit(
        match.unit
    ):
        converted = unit_conversion_service.convert(quantity, unit, match.unit)
        if converted is None:
            return {
                **base,
                "unit_cost": round(price_per_match_unit, 4),
                "matched_item_name": match.name,
                "note": f"matched {match.name!r} but its unit ({match.unit}) isn't convertible with {unit}",
            }
        qty_in_match_unit = converted.quantity

    line_cost = round(price_per_match_unit * qty_in_match_unit, 2)
    return {
        **base,
        "resolved": True,
        "unit_cost": round(price_per_match_unit, 4),
        "line_cost": line_cost,
        "matched_item_name": match.name,
    }


def _summarize(lines: list[dict]) -> dict:
    resolved_lines = [l for l in lines if l["resolved"]]
    total = round(sum(l["line_cost"] for l in resolved_lines), 2) if resolved_lines else None
    if not lines:
        provenance = PROVENANCE_NO_DATA
    elif len(resolved_lines) == len(lines):
        provenance = PROVENANCE_COMPUTED
    elif resolved_lines:
        provenance = PROVENANCE_PARTIAL
    else:
        provenance = PROVENANCE_NO_DATA
    return {
        "total_cost": total,
        "provenance": provenance,
        "resolved_count": len(resolved_lines),
        "total_count": len(lines),
        "lines": lines,
    }


def compute_recipe_cost(db: Session, recipe: Recipe) -> dict:
    """Cost across a recipe's full ingredient list, plus cost_per_serving
    (None whenever total_cost itself is None -- never divides a partial/
    unknown total and presents it as a real per-serving number)."""
    lines = [
        compute_ingredient_line_cost(db, ing.ingredient_name, ing.quantity, ing.unit) for ing in recipe.ingredients
    ]
    summary = _summarize(lines)
    servings = recipe.default_servings or 1
    summary["servings"] = servings
    summary["cost_per_serving"] = (
        round(summary["total_cost"] / servings, 2) if summary["total_cost"] is not None and servings else None
    )
    return summary


def compute_grocery_list_cost(db: Session, grocery_items: list[GroceryListItem]) -> dict:
    """Projected spend across a grocery list's still-unpurchased items --
    the actual "how much will this week cost" figure the backlog names.
    Already-purchased items are excluded: they're spent money, not a
    projection, and mixing the two would overstate what's left to buy."""
    unpurchased = [item for item in grocery_items if not item.is_purchased]
    lines = [
        compute_ingredient_line_cost(db, item.ingredient_name, item.quantity, item.unit) for item in unpurchased
    ]
    return _summarize(lines)
