"""Inventory business logic: urgency scoring (what should the meal
planner prioritize using up), vision-photo-intake response parsing, and
a deduction primitive for when a meal gets confirmed as made (wired up
by Phase 5/7, which own the meal-plan/chat flows that call it)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import InventoryItem
from app.services import ingredient_resolution_service, package_parsing, unit_conversion_service
from app.services.ai_json_extraction import extract_json_array

# --- Urgency scoring -------------------------------------------------
#
# Used by GET /api/inventory/priority-suggestions and, later, by the
# meal-planning engine (Phase 5) to bias recipe selection toward
# ingredients that are expiring soon, have sat unused a long time, or
# were explicitly flagged by the user as something to work through.
# Deliberately simple/tunable rather than a black box -- returns a
# score plus the human-readable reasons behind it.

EXPIRED_SCORE = 100
EXPIRES_SOON_SCORE = 80  # <= 3 days
EXPIRES_THIS_WEEK_SCORE = 50  # <= 7 days
EXPIRES_SOON_ISH_SCORE = 20  # <= 14 days
STALE_LONG_SCORE = 30  # unused > 60 days
STALE_SCORE = 15  # unused > 30 days
PRIORITY_FLAG_SCORE = 25


def compute_urgency(item: InventoryItem, today: date | None = None) -> tuple[float, list[str]]:
    today = today or date.today()
    score = 0.0
    reasons: list[str] = []

    if item.expiration_date is not None:
        days_left = (item.expiration_date - today).days
        if days_left <= 0:
            score += EXPIRED_SCORE
            reasons.append("already past its expiration date")
        elif days_left <= 3:
            score += EXPIRES_SOON_SCORE
            reasons.append(f"expires in {days_left} day(s)")
        elif days_left <= 7:
            score += EXPIRES_THIS_WEEK_SCORE
            reasons.append(f"expires in {days_left} day(s)")
        elif days_left <= 14:
            score += EXPIRES_SOON_ISH_SCORE
            reasons.append(f"expires in {days_left} day(s)")

    reference_date = item.last_used_date or item.purchased_date
    if reference_date is not None:
        days_since_touch = (today - reference_date).days
        if days_since_touch > 60:
            score += STALE_LONG_SCORE
            reasons.append(f"unused for {days_since_touch} days")
        elif days_since_touch > 30:
            score += STALE_SCORE
            reasons.append(f"unused for {days_since_touch} days")

    if item.is_priority:
        score += PRIORITY_FLAG_SCORE
        reasons.append(item.priority_note or "flagged as a priority ingredient to use up")

    return score, reasons


def get_priority_suggestions(
    db: Session, limit: int = 10, min_score: float = 1.0
) -> list[tuple[InventoryItem, float, list[str]]]:
    items = db.query(InventoryItem).all()
    scored = [(item, *compute_urgency(item)) for item in items]
    scored = [s for s in scored if s[1] >= min_score]
    scored.sort(key=lambda s: s[1], reverse=True)
    return scored[:limit]


def get_expiring_digest(
    db: Session, within_days: int = 7, today: date | None = None
) -> dict[str, list[InventoryItem] | int]:
    """Backlog B4.4 (via the B10.2 author-requested group):
    the REQUIRED-minimum "in-app banner" piece the backlog named --
    "Chef computes urgency server-side already but only surfaces it
    passively when the user opens the Inventory page. Add a digest...
    so the app reaches out rather than waiting to be visited." This is
    that digest's data source, meant to back a persistent app-shell
    banner (see frontend/src/components/ExpiringDigestBanner.jsx)
    visible from every page, not just Inventory.

    Deliberately reuses compute_urgency()'s existing expiration-day
    buckets rather than a second scoring system, but filters to ONLY
    the expiration-driven reasons -- unlike get_priority_suggestions
    above (which also surfaces staleness and manually-flagged priority
    items, useful context on the Inventory page itself but not what a
    user means by "is something about to expire"). Push notifications
    (Web Push/VAPID) and email are explicitly NOT built here -- the
    backlog's own text calls both "optional" and the in-app banner "at
    minimum," and Fiduciary's notification stack (which those would be
    ported from) solves a materially different problem (fan-out for
    discrete security events via an audit log) than "periodically check
    inventory," so porting it wholesale wasn't a good fit even before
    weighing the added dependency/complexity cost."""
    today = today or date.today()
    items = db.query(InventoryItem).filter(InventoryItem.expiration_date.isnot(None)).all()
    expired: list[InventoryItem] = []
    expiring_soon: list[InventoryItem] = []
    for item in items:
        days_left = (item.expiration_date - today).days
        if days_left <= 0:
            expired.append(item)
        elif days_left <= within_days:
            expiring_soon.append(item)
    expired.sort(key=lambda i: i.expiration_date)
    expiring_soon.sort(key=lambda i: i.expiration_date)
    return {"expired": expired, "expiring_soon": expiring_soon, "within_days": within_days}


# --- Vision photo intake parsing --------------------------------------
#
# Ollama vision models are asked to respond with a JSON array (see
# app/routers/inventory.py's VISION_PROMPT) but real-world model output
# often wraps that in prose, markdown fences, or a reasoning trace.
# Parse defensively: try strict JSON first, then fall back to a
# string-aware bracket-matching scan for the first parseable [...] block.
# That scan (reasoning-trace stripping, bracket matching, truncated-array
# salvage) moved to app/services/ai_json_extraction.py's extract_json_array
# so recipe/health/meal-plan JSON-OBJECT extraction shares the same
# defense instead of only this module having it. See that module's
# docstring.

CATEGORY_VALUES = {"pantry", "fridge", "freezer", "produce", "spice", "other"}


def parse_vision_response(raw_text: str, today: date | None = None) -> list[dict]:
    today = today or date.today()
    data = extract_json_array(raw_text)
    items: list[dict] = []
    for entry in data:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        category = str(entry.get("category") or "other").lower()
        if category not in CATEGORY_VALUES:
            category = "other"

        expiration_date = None
        days = entry.get("estimated_expiration_days")
        if isinstance(days, (int, float)):
            expiration_date = today + timedelta(days=int(days))

        # Package/measurement split (2026-08-02, author-requested): the
        # model still returns `unit` as whatever freeform text the
        # source printed (RECEIPT_IMPORT_PROMPT/VISION_PROMPT were
        # deliberately NOT rewritten this session -- both were only just
        # stabilized against the author's real Ollama container, and
        # touching either prompt's wording risks re-triggering that same
        # regression; see PROJECT-PLAN.md's session log). Instead, this
        # is a pure post-processing step on whatever text comes back:
        # package_parsing.parse_package_text splits "8 oz bag" into a
        # canonical unit ("oz"), a package size (8), and a leftover
        # descriptor ("bag") whenever it can, and returns None (leaving
        # `unit`/`estimated_quantity` exactly as before) when it can't.
        # This fixes the SAME underlying bug (a compound, unconvertible
        # `unit` string breaking deduction and cost math) without
        # touching either prompt at all.
        raw_unit = entry.get("unit") or None
        raw_quantity = _safe_float(entry.get("estimated_quantity"))
        package_count = raw_quantity if raw_quantity is not None else 1.0
        unit = raw_unit
        package_quantity = None
        package_descriptor = None
        final_quantity = raw_quantity
        parsed_package = package_parsing.parse_package_text(raw_unit)
        if parsed_package is not None:
            unit = parsed_package.unit
            package_quantity = parsed_package.package_quantity
            package_descriptor = parsed_package.package_descriptor
            # A leading multipack pattern in the unit text itself (e.g.
            # OFF-style "12 x 355 ml") multiplies the source's own
            # purchased-count; the common receipt/vision case has no
            # such pattern and parsed_package.package_count stays 1.0.
            package_count = package_count * parsed_package.package_count
            final_quantity = package_count * package_quantity

        items.append(
            {
                "name": str(entry["name"]).strip(),
                "estimated_quantity": final_quantity,
                "unit": unit,
                "package_quantity": package_quantity,
                "package_count": package_count,
                "package_descriptor": package_descriptor,
                "category": category,
                "expiration_date": expiration_date,
                # Bug fix (2026-08-02, author-reported): these two keys
                # were never read here even though RECEIPT_IMPORT_PROMPT
                # now asks the model for them and VisionDetectedItem has
                # had fields for both since the B10.3 order-history
                # importer -- they were silently dropped on the floor for
                # every AI-driven import (receipt/photo/PDF/pasted text),
                # even when the model dutifully returned them, because
                # this function just never looked for them.
                "unit_price": _safe_float(entry.get("unit_price")),
                "purchased_date": _safe_iso_date(entry.get("purchased_date")),
                "confidence_note": entry.get("confidence_note") or None,
            }
        )
    return items


def _safe_iso_date(value) -> date | None:
    """Parses a model-provided "YYYY-MM-DD" string into a real date,
    returning None (never raising) for anything else -- a null, an
    empty string, or a malformed value a model occasionally produces
    despite the prompt's explicit format instruction."""
    if not value or not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _safe_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# --- Name-based lookup, deduction, and updates -------------------------
#
# Primitive for Phase 5 (meal plan entry confirmation), Phase 7 (chat:
# "we made the stir fry tonight" / "we're out of milk" / "flag the
# lentils as priority"), and anywhere else a natural-language ingredient
# name needs to resolve to an inventory row without the caller knowing
# its id.
#
# Audit P1-5: this used to be an exact case-insensitive
# compare followed by `ILIKE %name%`, taking whichever row the database
# returned first. That matched "egg" to "eggplant" and "chicken" to
# "chicken broth", and with no unique constraint on `inventory_items.name`
# the choice between two rows of the same thing was undefined. All of it
# now goes through ingredient_resolution_service -- see that module's
# docstring for the matching design and the per-call-site confidence
# policy.
#
# The important behavioural change for anything that WRITES: a match
# below `THRESHOLD_DESTRUCTIVE` no longer silently becomes "the best guess
# we had". It becomes a question, carrying the ranked alternatives, and
# the answer is remembered as an alias so the same question is never asked
# twice. Refusing to deduct is recoverable; deducting from the wrong row
# is a wrong number in the database with nothing on screen to notice.

# Outcome vocabulary for deduct_by_name. Distinct values rather than a
# bare None because the three failure modes want three different UI
# responses: "which one did you mean?", "nothing like that is tracked",
# and "we know you used it but not how much of the row it consumed".
DEDUCT_APPLIED = "applied"
DEDUCT_AMBIGUOUS = "ambiguous"
DEDUCT_NO_MATCH = "no_match"
DEDUCT_UNIT_MISMATCH = "unit_mismatch"


@dataclass
class DeductionOutcome:
    status: str
    item: InventoryItem | None = None
    message: str | None = None
    resolution: object | None = None

    @property
    def needs_confirmation(self) -> bool:
        return self.status == DEDUCT_AMBIGUOUS


def resolve_for_write(db: Session, name: str, items=None):
    """Resolution at the confidence bar required to modify a row. Returns
    the full `Resolution`, including the ranked candidates, so a caller
    that gets no match can ask a specific question rather than reporting a
    bare 404.

    `items` lets a caller resolving several names in a row (confirming a
    meal deducts every ingredient of a recipe) load inventory once and
    reuse it, instead of paying for a full table read per ingredient.
    Scoring is against the whole inventory either way -- word-boundary
    matching cannot be pushed into a SQL predicate the way `ILIKE` could,
    which is the one real cost of getting the matching right."""
    return ingredient_resolution_service.resolve(
        db, name, minimum_score=ingredient_resolution_service.THRESHOLD_DESTRUCTIVE, items=items
    )


def find_by_name(db: Session, name: str) -> InventoryItem | None:
    """Convenience wrapper kept for callers that only want the row and
    have nothing useful to do with an ambiguity. Prefer
    `resolve_for_write` anywhere the user is present to answer."""
    return resolve_for_write(db, name).item


def deduct_by_name(
    db: Session,
    ingredient_name: str,
    quantity: float | None = None,
    unit: str | None = None,
    items=None,
) -> DeductionOutcome:
    """Resolves an ingredient name to an inventory row and decrements its
    quantity (floored at 0), marking it as just used. Does not delete
    zeroed-out items -- an empty-but-known item is still useful context
    ("we're out of X") rather than disappearing silently.

    Returns a `DeductionOutcome`, not a bare item. The three non-applied
    statuses are genuinely different situations:

    - `DEDUCT_AMBIGUOUS`: something resembling this is in inventory, but
      not confidently enough to write to it. Nothing is modified. The
      resolution's ranked candidates are attached so the caller can ask.
    - `DEDUCT_NO_MATCH`: nothing in inventory resembles this name at all.
    - `DEDUCT_UNIT_MISMATCH`: the row was found, but the recipe's unit and
      the row's unit are not convertible, so the quantity is left alone
      and only `last_used_date` is stamped (audit P1-4).

    `unit` is the unit `quantity` is expressed in -- e.g. a recipe calling
    for "2 cup flour" while the inventory row is logged in pounds. When
    both units are known and differ, the quantity is converted into the
    item's unit before subtracting."""
    resolution = resolve_for_write(db, ingredient_name, items=items)
    item = resolution.item
    if item is None:
        if resolution.needs_confirmation:
            best = resolution.candidates[0]
            return DeductionOutcome(
                status=DEDUCT_AMBIGUOUS,
                message=(
                    f"Not confident enough to deduct from an inventory item for "
                    f"{ingredient_name!r}. Closest is {best.name!r} ({best.confidence} confidence). "
                    f"Confirm which item you meant and it will be remembered."
                ),
                resolution=resolution,
            )
        blocked_note = ""
        if resolution.blocked:
            blocked_note = f" ({resolution.blocked[0].name!r} was excluded: {resolution.blocked[0].blocked_by})"
        return DeductionOutcome(
            status=DEDUCT_NO_MATCH,
            message=f"Nothing in inventory matches {ingredient_name!r}{blocked_note}",
            resolution=resolution,
        )

    return deduct_item(db, item, quantity, unit, resolution=resolution)


def deduct_item(
    db: Session,
    item: InventoryItem,
    quantity: float | None = None,
    unit: str | None = None,
    resolution: object | None = None,
) -> DeductionOutcome:
    """The deduction itself, against an already-chosen row. Split out from
    `deduct_by_name` so a user who answers a disambiguation prompt can
    have their explicit choice applied directly, with no second trip
    through the matcher that already declined to pick."""
    if quantity is not None:
        amount = quantity
        units_differ = (
            unit
            and item.unit
            and unit_conversion_service.normalize_unit(unit) != unit_conversion_service.normalize_unit(item.unit)
        )
        if units_differ:
            converted = unit_conversion_service.convert(quantity, unit, item.unit)
            if converted is None:
                # Refuse rather than guess.
                #
                # This used to fall through and subtract the raw numbers as
                # if the units matched: a recipe calling for "2 cup flour"
                # against a "5 lb flour" row left 3 lb on hand. That is not
                # imprecision, it is a wrong number written to the database
                # with nothing telling the user it happened.
                #
                # Leaving the quantity alone and stamping last_used_date is
                # the honest outcome: we know the ingredient was used, we do
                # not know how much of the row it consumed. An inventory
                # count the user can see is stale beats one that is
                # confidently wrong.
                item.last_used_date = date.today()
                db.commit()
                db.refresh(item)
                print(
                    f"[inventory_service] not deducting {quantity} {unit!r} from {item.name!r} "
                    f"(stored in {item.unit!r}) -- no conversion available between those units; "
                    f"marked used but quantity left unchanged",
                    flush=True,
                )
                return DeductionOutcome(
                    status=DEDUCT_UNIT_MISMATCH,
                    item=item,
                    message=(
                        f"Marked {item.name!r} as used, but its quantity was left unchanged: "
                        f"the recipe asks for {unit} and the item is tracked in {item.unit}, "
                        f"which are not convertible without knowing its density."
                    ),
                    resolution=resolution,
                )
            amount = converted.quantity
        item.quantity = max(0.0, item.quantity - amount)
    else:
        item.quantity = max(0.0, item.quantity - 1)
    item.last_used_date = date.today()
    db.commit()
    db.refresh(item)
    return DeductionOutcome(status=DEDUCT_APPLIED, item=item, resolution=resolution)


UPDATABLE_FIELDS_BY_NAME = {
    "quantity",
    "unit",
    "package_quantity",
    "package_count",
    "package_descriptor",
    "category",
    "expiration_date",
    "is_priority",
    "priority_note",
    "notes",
}


def update_by_name(db: Session, name: str, **updates) -> InventoryItem | None:
    """Applies a partial update (any of UPDATABLE_FIELDS_BY_NAME) to the
    inventory item this name resolves to. Unknown keys are ignored rather
    than raising, since callers (chat action execution) pass through
    whatever the user/model specified, which may be a subset.

    Held to the same confidence bar as deduction (audit P1-5): this
    writes to a row, and "we're out of milk" setting the almond milk to
    zero is the same class of silent corruption. Returns None when the
    name does not resolve confidently -- the router turns that into a
    disambiguation response rather than a bare 404."""
    return update_item(db, resolve_for_write(db, name).item, **updates)


def update_item(db: Session, item: InventoryItem | None, **updates) -> InventoryItem | None:
    """The update itself, against an already-chosen row -- the
    counterpart to `deduct_item`, for applying a user's explicit
    disambiguation answer without re-running the matcher."""
    if item is None:
        return None
    for field, value in updates.items():
        if field in UPDATABLE_FIELDS_BY_NAME and value is not None:
            setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return item
