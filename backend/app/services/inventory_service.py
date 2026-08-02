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
from app.services import unit_conversion_service

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
# string-aware bracket-matching scan for the first parseable [...] block
# (see _extract_json_array for why a plain greedy regex was not enough).

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


_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_ORPHAN_THINK_CLOSE_RE = re.compile(r"^.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_reasoning(raw_text: str) -> str:
    """Removes a thinking/reasoning trace from model output before any
    JSON extraction is attempted.

    Bug fix (2026-08-02): `ollama_client.chat()` passes `think=False`, and
    on a new enough Ollama server a thinking model's trace is returned in
    `message.thinking` rather than `message.content` -- but neither of
    those is a guarantee for every model/server/template combination a
    self-hoster can configure (an Ollama server predating the `think`
    request parameter silently ignores it and leaves the trace inline in
    content; some community/custom Modelfile templates emit the tags
    inline regardless). An inline trace is prose, and prose routinely
    contains square brackets ("lines [1-15]", "[see attached]"), which is
    exactly what defeated the old first-`[`-to-last-`]` regex below. Two
    shapes are handled: a complete `<think>...</think>` block, and a bare
    trailing `</think>` with no opening tag (what you get when the chat
    template itself opens the tag, so the opener never appears in the
    returned content)."""
    text = _THINK_BLOCK_RE.sub(" ", raw_text)
    if "</think>" in text.lower():
        text = _ORPHAN_THINK_CLOSE_RE.sub(" ", text)
    return text


def _scan_json_array(text: str, start: int) -> tuple[int | None, list[tuple[int, int]]]:
    """Bracket-matching scan of a JSON array starting at `text[start] ==
    "["`, string-aware so brackets/braces inside string values (a
    "confidence_note" reading `included [borderline]`, say) don't throw
    off the depth count.

    Returns `(end_index_exclusive, top_level_object_spans)` where
    `end_index_exclusive` is None if the array is never closed -- i.e.
    the model's output was cut off mid-array, which is what a generation
    that hits the context/`num_predict` limit looks like (`done_reason`
    "length" in ollama_client's response log). The object spans are
    returned in both cases so a truncated array's already-complete
    elements can still be salvaged rather than thrown away wholesale."""
    depth = 0
    in_string = False
    escaped = False
    object_start: int | None = None
    spans: list[tuple[int, int]] = []
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            if char == "{" and depth == 1 and object_start is None:
                object_start = index
            depth += 1
        elif char in "]}":
            depth -= 1
            if char == "}" and depth == 1 and object_start is not None:
                spans.append((object_start, index + 1))
                object_start = None
            if depth == 0:
                return index + 1, spans
    return None, spans


def _extract_json_array(raw_text: str) -> list:
    """Pulls the JSON array out of a model's raw answer.

    Bug fix (2026-08-02, author-reported "0 items identified" with a
    NON-EMPTY raw model response): this used to fall back to
    `re.search(r"\\[.*\\]", raw_text, re.DOTALL)` -- a GREEDY match, so it
    spanned from the FIRST "[" anywhere in the output to the LAST "]"
    anywhere in the output. Any square bracket outside the real array
    silently corrupted the slice into unparseable JSON and this returned
    `[]`, which the UI reports as "0 items identified" -- indistinguish-
    able, to the user, from the model genuinely finding no food. Real,
    reproducible cases that a model in this app's own receipt-import path
    produces routinely (each one is a test in test_inventory_import.py):

      * a correct array followed by a note -- `[{...}]\\n\\nNote: I skipped
        the non-food lines [paper towels, lint roller].` (the prompt
        explicitly asks the model to skip non-food items, which makes
        exactly this trailing commentary likely)
      * a lead-in containing brackets -- `Here are the items [from the
        receipt]:\\n[{...}]`
      * an inline `<think>` trace whose prose mentions "[1-15]"
      * two arrays in one response (the items, then the skipped ones)

    Replaced with: strip any reasoning trace, then walk each "[" in order
    doing a real string-aware bracket-matching scan (`_scan_json_array`)
    and take the first candidate that actually parses as a list. That
    also makes a genuinely-empty `[]` distinguishable from a parse
    failure instead of collapsing both to the same answer.

    Also added: salvage of a TRUNCATED array. If generation stopped
    mid-array (context/num_predict limit -- `done_reason` "length"), the
    array never closes and strict parsing of the whole thing is
    impossible, but every element before the cut is complete and
    perfectly good. Previously that produced zero items from a response
    that had already correctly identified most of the receipt; now the
    complete elements are returned. The same path incidentally recovers
    an array with a trailing comma before "]", another routine
    small-model JSON slip."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        return []

    text = _strip_reasoning(raw_text)

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    for start in (index for index, char in enumerate(text) if char == "["):
        end, spans = _scan_json_array(text, start)
        if end is not None:
            try:
                parsed = json.loads(text[start:end])
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                if parsed:
                    return parsed
                # An empty `[]` embedded in prose is far more often a
                # stray example or a false start than the real answer --
                # keep looking for a populated array before accepting it.
                # (A response that is ONLY `[]` never reaches here: it
                # parses strictly above and is returned as-is, so a model
                # genuinely reporting "no food on this receipt" is still
                # honored.)
                continue
        salvaged = _salvage_objects(text, spans)
        if salvaged:
            return salvaged
    return []


def _salvage_objects(text: str, spans: list[tuple[int, int]]) -> list:
    """Parses each individually-complete `{...}` element of an array that
    could not be parsed as a whole (truncated mid-generation, or a
    trailing comma). Elements that don't parse on their own are skipped
    rather than failing the batch -- a partial receipt is strictly better
    than the zero items this used to return."""
    salvaged = []
    for object_start, object_end in spans:
        try:
            entry = json.loads(text[object_start:object_end])
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            salvaged.append(entry)
    return salvaged


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
