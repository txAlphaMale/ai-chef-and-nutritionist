"""Inventory business logic: urgency scoring (what should the meal
planner prioritize using up), vision-photo-intake response parsing, and
a deduction primitive for when a meal gets confirmed as made (wired up
by Phase 5/7, which own the meal-plan/chat flows that call it)."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.models import InventoryItem

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


# --- Vision photo intake parsing --------------------------------------
#
# Ollama vision models are asked to respond with a JSON array (see
# app/routers/inventory.py's VISION_PROMPT) but real-world model output
# often wraps that in prose or markdown fences. Parse defensively: try
# strict JSON first, then fall back to extracting the first [...] block.

CATEGORY_VALUES = {"pantry", "fridge", "freezer", "produce", "spice", "other"}


def parse_vision_response(raw_text: str, today: date | None = None) -> list[dict]:
    today = today or date.today()
    data = _extract_json_array(raw_text)
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

        items.append(
            {
                "name": str(entry["name"]).strip(),
                "estimated_quantity": _safe_float(entry.get("estimated_quantity")),
                "unit": entry.get("unit") or None,
                "category": category,
                "expiration_date": expiration_date,
                "confidence_note": entry.get("confidence_note") or None,
            }
        )
    return items


def _extract_json_array(raw_text: str) -> list:
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    match = re.search(r"\[.*\]", raw_text or "", re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                return parsed
        except json.JSONDecodeError:
            pass
    return []


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


def deduct_by_name(db: Session, ingredient_name: str, quantity: float | None = None) -> InventoryItem | None:
    """Best-effort: finds the closest-matching inventory item by name and
    decrements its quantity (floored at 0), marking it as just used.
    Returns the affected item, or None if nothing matched. Does not
    delete zeroed-out items -- an empty-but-known item is still useful
    context (e.g. "we're out of X") rather than disappearing silently."""
    item = find_by_name(db, ingredient_name)
    if item is None:
        return None

    if quantity is not None:
        item.quantity = max(0.0, item.quantity - quantity)
    else:
        item.quantity = max(0.0, item.quantity - 1)
    item.last_used_date = date.today()
    db.commit()
    db.refresh(item)
    return item


UPDATABLE_FIELDS_BY_NAME = {
    "quantity",
    "unit",
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
