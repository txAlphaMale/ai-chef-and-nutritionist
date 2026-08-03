"""Inventory business logic: urgency scoring (what should the meal
planner prioritize using up), vision-photo-intake response parsing, and
a deduction primitive for when a meal gets confirmed as made (wired up
by Phase 5/7, which own the meal-plan/chat flows that call it)."""
from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import InventoryItem
from app.services import package_parsing, unit_conversion_service
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
    """Backlog B4.4 (via the B10.2 author-requested group, 2026-08-01):
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
# (2026-08-03) so recipe/health/meal-plan JSON-OBJECT extraction -- which
# had the exact same greedy-regex bug this one was already fixed for --
# could share the same defense instead of only this module having it. See
# that module's docstring for the fuller history/rationale.

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
# its id. Matching is inherently fuzzy -- exact-then-substring,
# case-insensitive as a first pass; a smarter matcher (aliases, unit
# conversion) is a documented future improvement, not solved here.


def find_by_name(db: Session, name: str) -> InventoryItem | None:
    name_lower = name.strip().lower()
    if not name_lower:
        return None
    item = db.query(InventoryItem).filter(InventoryItem.name.ilike(name_lower)).first()
    if item is None:
        item = db.query(InventoryItem).filter(InventoryItem.name.ilike(f"%{name_lower}%")).first()
    return item


def deduct_by_name(
    db: Session, ingredient_name: str, quantity: float | None = None, unit: str | None = None
) -> InventoryItem | None:
    """Best-effort: finds the closest-matching inventory item by name and
    decrements its quantity (floored at 0), marking it as just used.
    Returns the affected item, or None if nothing matched. Does not
    delete zeroed-out items -- an empty-but-known item is still useful
    context (e.g. "we're out of X") rather than disappearing silently.

    `unit` (backlog B5.3, added 2026-07-31) is the unit `quantity` is
    expressed in -- e.g. a recipe calling for "2 cup flour" while the
    inventory row is logged in pounds. When both `unit` and the item's
    own `item.unit` are known and differ, the quantity is converted into
    the item's unit via unit_conversion_service before subtracting,
    fixing a real bug where the two were previously subtracted as raw
    numbers regardless of unit. When conversion isn't possible (a count
    unit, an unknown unit, or a volume<->mass gap with no density) this
    falls back to the previous behavior -- treating quantity as already
    in the item's unit -- rather than refusing to deduct at all."""
    item = find_by_name(db, ingredient_name)
    if item is None:
        return None

    if quantity is not None:
        amount = quantity
        if unit and item.unit and unit_conversion_service.normalize_unit(unit) != unit_conversion_service.normalize_unit(item.unit):
            converted = unit_conversion_service.convert(quantity, unit, item.unit)
            if converted is not None:
                amount = converted.quantity
        item.quantity = max(0.0, item.quantity - amount)
    else:
        item.quantity = max(0.0, item.quantity - 1)
    item.last_used_date = date.today()
    db.commit()
    db.refresh(item)
    return item


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
    closest-matching inventory item by name. Unknown keys are ignored
    rather than raising, since callers (chat action execution) pass
    through whatever the user/model specified, which may be a subset.
    Returns the updated item, or None if nothing matched."""
    item = find_by_name(db, name)
    if item is None:
        return None
    for field, value in updates.items():
        if field in UPDATABLE_FIELDS_BY_NAME and value is not None:
            setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return item
