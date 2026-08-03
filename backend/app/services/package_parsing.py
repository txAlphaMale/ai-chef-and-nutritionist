"""Best-effort parser that pulls a structured package size (a number plus
a canonical measurement unit) and a free-text container descriptor out of
a single freeform string -- "8 oz bag", "500 g", "12 x 355 ml", "10oz 6
Count pack", "14 oz can each".

Why this exists: `RECEIPT_IMPORT_PROMPT` tells the model to put a
packaging descriptor in `unit` ("put that descriptor in unit instead,
e.g. '8 oz bag'"), which jams a measurement and a packaging description
into one freeform string. That string is not a real, convertible unit --
`unit_conversion_service.normalize_unit("8 oz bag")` does not recognize
it, so every downstream
consumer that needs a real unit (recipe-confirm deduction via
`inventory_service.deduct_by_name`, and B6.1's `cost_service`, which
divides a purchase price by the matched row's quantity/unit to get a
per-unit price) silently fell back to treating the WHOLE package as one
unit -- e.g. a recipe asking for "4 oz bacon" against a row whose
`unit` was "24 oz pack" couldn't convert 4 oz into "24 oz pack"s, so it
fell back to subtracting 4 raw from a `quantity` of 1 (one package),
floored at 0 -- zeroing out an entire pack of bacon after using a
quarter of it. This module is the fix's foundation: split "8 oz bag"
into a real unit ("oz"), a real number (8), and a purely-descriptive
leftover string ("bag") that is never used in any arithmetic.

Used by three call sites, all best-effort with a documented "give up
rather than guess" floor:
  - `routers/inventory.py`'s barcode lookup, on Open Food Facts'
    `quantity` field (e.g. "500 g", "12 x 355 ml").
  - `order_import_service.apply_mapping`, on whatever raw text sits in
    a spreadsheet's mapped "unit" column.
  - `alembic/versions/<this session's migration>`'s one-time backfill of
    pre-existing `InventoryItem.unit` text -- that migration
    deliberately carries its OWN frozen copy of this same regex logic
    rather than importing this module, per this repo's standing
    convention that a migration's behavior should never silently change
    just because the application code it would otherwise depend on was
    edited later (every other migration in this repo is pure schema DDL
    with no app-code import; this is the one migration that needed real
    parsing logic, so it inlines it instead of breaking that pattern).

Deliberately does NOT attempt to parse count-only descriptions like "3
apples" or "1 dozen" -- those have no separate "package size" concept to
extract (the count IS the whole quantity), and the caller should just
keep treating them as a plain count, same as today.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services import unit_conversion_service

# Longest-first so "fl oz"/"fluid ounce" doesn't get shadowed by a
# shorter alternative match earlier in the pattern, and so multi-word
# synonyms ("fl oz") match before their single-word contraction. Built
# from unit_conversion_service's own registry rather than a second,
# potentially-drifting list of unit spellings.
_UNIT_WORDS = sorted(
    {
        *unit_conversion_service.VOLUME_TO_ML,
        *unit_conversion_service.MASS_TO_G,
        *unit_conversion_service.UNIT_SYNONYMS,
        "fl oz",
        "fl. oz",
        "fl. oz.",
        "count",
        "ct",
        "each",
        "ea",
    },
    key=len,
    reverse=True,
)
_UNIT_ALTERNATION = "|".join(re.escape(w) for w in _UNIT_WORDS)

# "12 x 355 ml", "6x8 oz", "2 X 500ml" -- an explicit multipack count
# times a per-unit size. Matched first since it's the most specific
# shape (two numbers, not one).
_MULTIPACK_RE = re.compile(
    rf"^\s*(?P<count>\d+(?:\.\d+)?)\s*[x×]\s*(?P<size>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_ALTERNATION})\b\.?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)

# "8 oz bag", "500 g", "10oz 6 Count pack", "14 oz can each" -- a
# leading number+unit, with anything left over kept verbatim as the
# package descriptor (never further parsed -- "6 Count pack" is
# genuinely ambiguous as a second measurement and this app's own "never
# guess a second time" discipline applies).
_LEADING_MEASURE_RE = re.compile(
    rf"^\s*(?P<size>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_ALTERNATION})\b\.?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)

_COUNT_ALIASES = {"count", "ct", "each", "ea"}


@dataclass
class PackageParseResult:
    package_quantity: float
    unit: str
    package_descriptor: str | None
    package_count: float = 1.0


def parse_package_text(text: str | None) -> PackageParseResult | None:
    """Returns None (never guesses) when no leading number+unit shape is
    found at all -- the caller should fall back to treating `text` as an
    opaque freeform string, exactly like this app did before this module
    existed."""
    if not text or not text.strip():
        return None
    cleaned = text.strip()

    match = _MULTIPACK_RE.match(cleaned)
    if match:
        unit = _canonical_unit(match.group("unit"))
        rest = match.group("rest").strip() or None
        return PackageParseResult(
            package_quantity=float(match.group("size")),
            unit=unit,
            package_descriptor=rest,
            package_count=float(match.group("count")),
        )

    match = _LEADING_MEASURE_RE.match(cleaned)
    if match:
        unit = _canonical_unit(match.group("unit"))
        rest = match.group("rest").strip() or None
        return PackageParseResult(
            package_quantity=float(match.group("size")),
            unit=unit,
            package_descriptor=rest,
        )

    return None


def _canonical_unit(raw_unit: str) -> str:
    lowered = raw_unit.strip().lower().rstrip(".")
    if lowered in _COUNT_ALIASES:
        return "count"
    normalized = unit_conversion_service.normalize_unit(lowered)
    return normalized or lowered
