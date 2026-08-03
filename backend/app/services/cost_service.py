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
actual $/unit signal this module needs is `unit_price / purchased_quantity`,
converted into the ingredient's requested unit via
`unit_conversion_service` when the two differ (same convention already
used by `inventory_service.deduct_by_name`/`meal_plan_service.subtract_inventory`).
When a household has bought the same ingredient more than once at
different prices, the most recently purchased PRICED row is used -- a
recent real price is a better cost signal than an old one, and never
averaged or otherwise invented.

Bug fix (2026-08-02, author-flagged as a design concern, confirmed real
by re-reading this module): this used to divide by the row's live
`quantity` instead of `purchased_quantity`. `quantity` is "on hand" and
shrinks as `inventory_service.deduct_by_name` decrements it every time a
recipe using that ingredient gets confirmed -- so a $6.00-for-2-lb
chicken breast row priced at $3.00/lb when purchased would have silently
recomputed to $6.00/lb once 1 lb had been used (unit_price / remaining
quantity = 6.00 / 1), even though nothing about what was actually PAID
changed. `purchased_quantity` is the immutable snapshot this needed --
see InventoryItem's own docstring. Falls back to `quantity` when
`purchased_quantity` is unset (rows created before that column existed,
or an intake source with no real "purchase" concept) -- same value this
module always used, so pre-existing behavior is preserved for exactly
the rows that have no better signal available, never a hard failure.

Matching (rewritten 2026-08-03, audit P1-5) goes through
`ingredient_resolution_service` like every other name-to-inventory lookup
in the app, restricted to rows that actually carry a `unit_price`. It used
to be the same `ILIKE %name%` substring scan as everywhere else, which
here meant a recipe's "chicken" could be priced from a carton of "chicken
broth" -- a wrong dollar figure with a plausible-looking matched-item name
next to it.

This is an ADVISORY call site (`THRESHOLD_ADVISORY`), a deliberately lower
bar than inventory deduction uses: a cost estimate is read by a human
before anything acts on it, and the matched item's name is reported
alongside every figure, so a medium-confidence match is information the
user can evaluate rather than a silent write. `matched_confidence` is
returned on every line so the UI can mark the uncertain ones.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import GroceryListItem, InventoryItem, Recipe
from app.services import ingredient_resolution_service, unit_conversion_service

# Recipe.nutrition_provenance's three-way split, reused here for the same
# "computed / some-but-not-all-resolved / none-resolved" reporting shape
# rather than inventing a fourth vocabulary for the same concept.
PROVENANCE_COMPUTED = "computed"
PROVENANCE_PARTIAL = "partial"
PROVENANCE_NO_DATA = "no_data"


def _price_recency_tiebreak(item: InventoryItem) -> tuple:
    """Ordering among priced rows that score IDENTICALLY on name.

    Deliberately NOT `ingredient_resolution_service.inventory_tiebreak`,
    which prefers the soonest-expiring row because it is answering "which
    carton should we use up". This function answers a different question
    -- "what did this ingredient last cost" -- and a recent real price is
    the better signal for that, so the most recently purchased priced row
    wins. A row with no purchase date sorts last: an unknown date is
    weaker evidence than a known recent one, not a reason to prefer it.
    Never averages prices across rows and never invents one."""
    from datetime import date as _date

    return (
        0 if item.purchased_date is not None else 1,
        -(item.purchased_date or _date.min).toordinal(),
        -(item.id or 0),
    )


def _find_priced_inventory_match(db: Session, ingredient_name: str) -> tuple[InventoryItem | None, str, str]:
    """Resolves a name against priced inventory rows only. Returns
    `(item, confidence, reason)` -- the confidence travels with the match
    so every cost line can say how sure it is, rather than presenting a
    weak match with the same authority as an exact one."""
    if not (ingredient_name or "").strip():
        return None, ingredient_resolution_service.CONFIDENCE_NONE, ""

    priced_rows = db.query(InventoryItem).filter(InventoryItem.unit_price.isnot(None)).all()
    match, _ranked = ingredient_resolution_service.best_match(
        ingredient_name,
        [(row.name, row) for row in priced_rows],
        minimum_score=ingredient_resolution_service.THRESHOLD_ADVISORY,
        transformation_words=ingredient_resolution_service.load_transformation_words(db),
        tiebreak_key=_price_recency_tiebreak,
    )
    if match is None:
        return None, ingredient_resolution_service.CONFIDENCE_NONE, ""
    return match.payload, match.confidence, match.reason


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
        "matched_confidence": ingredient_resolution_service.CONFIDENCE_NONE,
        "match_reason": None,
        "note": None,
    }

    match, confidence, match_reason = _find_priced_inventory_match(db, ingredient_name)
    if match is None:
        return {**base, "note": "no priced inventory purchase on record for this ingredient"}
    base = {**base, "matched_confidence": confidence, "match_reason": match_reason}
    denominator = match.purchased_quantity or match.quantity
    if not denominator:
        return {**base, "note": f"matched {match.name!r} but its own quantity is zero/unknown"}

    price_per_match_unit = match.unit_price / denominator

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
    resolved_lines = [line for line in lines if line["resolved"]]
    total = round(sum(line["line_cost"] for line in resolved_lines), 2) if resolved_lines else None
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
