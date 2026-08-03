"""Backlog B4.3 (2026-08-01): USDA FSIS FoodKeeper-backed default shelf
life. Directly serves two things the project brief and the author both
called out explicitly: "keep expiration information as much as reasonably
possible," and the author's own stated forgotten-pantry-item problem ("we
have a bad habit of buy some items, putting them in the pantry, then
forgetting they exist"). Today, Chef only ever gets an expiration date if
the household types one in -- this module auto-suggests one instead, from
`InventoryItem.purchased_date` plus USDA's own published storage-duration
guidance, whenever the item was added without one.

**Data provenance, stated plainly rather than assumed.** Claude's own
sandbox has no network route to any `.gov` domain (confirmed directly via
both the web-fetch tool and a direct `curl` -- both failed outright for
`fsis.usda.gov`, `data.gov`, and every other `.gov` host tried, while
`github.com`/`npmjs.org`/`pypi.org` all worked fine -- this is a sandbox
network-allowlist gap, not a content restriction). The author's own
connected Chrome browser *does* reach `fsis.usda.gov`, so the live file
was fetched, inspected, and condensed to the CSV this module reads via
that browser session on 2026-08-01, not guessed or reconstructed from a
stale secondary source. Source: the official, CC0/public-domain FSIS
FoodKeeper dataset at `https://www.fsis.usda.gov/shared/data/EN/
foodkeeper.json` (per data.gov's own catalog metadata for
`fsis-foodkeeper-data`). One honesty-relevant wrinkle found while fetching
it: the dataset's own "Version" sheet lists version 128 (modified
2018-09-06, flagged `Current_Version: Yes`) as the newest entry -- so
despite data.gov's catalog page showing a "Dataset Last Updated:
2025-01-22" date, the actual FoodKeeper CONTENT hasn't changed since 2018;
that 2025 date is a metadata re-harvest timestamp, not a content refresh.
This is still genuinely the current official file, just itself several
years stale at the source -- not a limitation of how Chef fetched it.

**Shipped as a pre-processed CSV**, not the original nested JSON:
`backend/app/data/foodkeeper_shelf_life.csv`, 661 rows, pipe-delimited
(FoodKeeper's own free-text tip fields contain commas, so pipe avoids any
quoting ambiguity). Condensed from FSIS's `Product`/`Category` sheets down
to: `ID`, `Category`, `Name`, `NameSubtitle`, `Keywords`, and
Pantry/Fridge/Freezer day-count ranges (`*DaysMin`/`*DaysMax`), plus
after-opening ranges for pantry and fridge. FoodKeeper's source Min/Max/
Metric triples (e.g. "1-2, Months") were converted to a plain integer day
count at build time (Days=1, Weeks=7, Months=30, Years=365) so this module
never needs to parse unit strings at request time.

**One interpretation choice worth documenting.** FoodKeeper publishes two
parallel ranges per storage location: a plain one (`Pantry_Min/Max`, in
the source) and a `DOP_`-prefixed one (`DOP_Pantry_Min/Max`) -- "DOP"
being FSIS's own field-naming convention for "date of purchase." Chef only
ever has `InventoryItem.purchased_date` to anchor a suggestion to, never a
package print/best-by date, so the CSV build preferred the DOP_ variant
when populated and fell back to the plain variant otherwise. Many common
items (e.g. Butter) are *only* populated on the DOP_ side in the source
data, which is a further point in favor of that choice, not just a
matter of convenience.

**Matching an inventory item name to a FoodKeeper product is inherently
fuzzy** -- "chicken breast, boneless" needs to match FoodKeeper's
"Chicken, boneless breasts" without an exact string match. `match_item()`
scores every row (exact name > exact keyword > substring-of-name >
substring-of-keyword, longer/more specific keyword phrases winning ties)
and returns the best match above a minimum confidence floor, or `None`
rather than forcing a low-confidence guess. Every suggestion this module
returns is a *range*, always presented to the household as an estimate to
confirm, never silently written over a value they already set -- see
`routers/inventory.py`'s `/shelf-life-suggestion` endpoint and the
frontend's "Estimated (USDA FoodKeeper)" labeling."""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from datetime import date, timedelta

CSV_PATH = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data", "foodkeeper_shelf_life.csv"))

# InventoryItem.category values that have a direct FoodKeeper storage-
# location counterpart. "produce" deliberately checks fridge first (most
# fresh produce this app's household would log is refrigerated) falling
# back to pantry (e.g. onions, potatoes); "spice" and "other" have no
# clean FoodKeeper counterpart and are left out on purpose rather than
# guessing -- match_item() callers get `None` for those categories.
CATEGORY_FIELD_ORDER: dict[str, tuple[str, ...]] = {
    "pantry": ("pantry",),
    "fridge": ("fridge",),
    "freezer": ("freezer",),
    "produce": ("fridge", "pantry"),
}

# Below this score, match_item() returns no match rather than a guess a
# household would reasonably call wrong -- e.g. a bare substring hit on a
# 3-character keyword. Tuned by hand against the shipped CSV's real
# keyword lengths, not derived from any formula.
MIN_MATCH_SCORE = 50

# FSIS's own Keywords column splits compound words into their parts for
# their own app's search feature (e.g. "Surimi seafood"'s keywords
# include "sea" AND "food" as separate entries, not just "seafood") --
# found live via a failing test, not assumed. Left alone, a generic
# 4-letter word like "food" clears a naive length-based filter and
# matches almost any real-world item name that happens to contain it
# (e.g. "leftover food item" would falsely match Surimi seafood).
# Excluded from keyword scoring entirely, regardless of length.
_GENERIC_KEYWORD_STOPLIST = {
    "food",
    "foods",
    "sea",
    "meat",
    "fresh",
    "dried",
    "raw",
    "cooked",
    "whole",
    "item",
    "items",
    "product",
    "products",
    "general",
    "other",
}


@dataclass
class FoodKeeperEntry:
    id: int
    category: str
    name: str
    name_subtitle: str | None
    keywords: list[str] = field(default_factory=list)
    pantry_days: tuple[int | None, int | None] = (None, None)
    fridge_days: tuple[int | None, int | None] = (None, None)
    freezer_days: tuple[int | None, int | None] = (None, None)
    pantry_after_open_days: tuple[int | None, int | None] = (None, None)
    fridge_after_open_days: tuple[int | None, int | None] = (None, None)

    @property
    def display_name(self) -> str:
        return f"{self.name} ({self.name_subtitle})" if self.name_subtitle else self.name

    def days_for(self, storage: str) -> tuple[int | None, int | None]:
        return {
            "pantry": self.pantry_days,
            "fridge": self.fridge_days,
            "freezer": self.freezer_days,
        }.get(storage, (None, None))


_ENTRIES: list[FoodKeeperEntry] | None = None


def _to_int(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


def _load_entries() -> list[FoodKeeperEntry]:
    """Parses the shipped CSV once per process and caches the result --
    661 small rows, trivial to hold in memory, no reason to re-parse per
    request. Tests that need a fresh parse (e.g. against a fixture CSV)
    should call `_reset_cache()` first rather than relying on import
    order."""
    global _ENTRIES
    if _ENTRIES is not None:
        return _ENTRIES
    entries: list[FoodKeeperEntry] = []
    with open(CSV_PATH, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="|")
        for row in reader:
            keywords = [k.strip().lower() for k in (row.get("Keywords") or "").split(",") if k.strip()]
            entries.append(
                FoodKeeperEntry(
                    id=int(row["ID"]),
                    category=row.get("Category") or "",
                    name=row.get("Name") or "",
                    name_subtitle=row.get("NameSubtitle") or None,
                    keywords=keywords,
                    pantry_days=(_to_int(row.get("PantryDaysMin")), _to_int(row.get("PantryDaysMax"))),
                    fridge_days=(_to_int(row.get("FridgeDaysMin")), _to_int(row.get("FridgeDaysMax"))),
                    freezer_days=(_to_int(row.get("FreezerDaysMin")), _to_int(row.get("FreezerDaysMax"))),
                    pantry_after_open_days=(
                        _to_int(row.get("PantryAfterOpenDaysMin")),
                        _to_int(row.get("PantryAfterOpenDaysMax")),
                    ),
                    fridge_after_open_days=(
                        _to_int(row.get("FridgeAfterOpenDaysMin")),
                        _to_int(row.get("FridgeAfterOpenDaysMax")),
                    ),
                )
            )
    _ENTRIES = entries
    return entries


def _reset_cache() -> None:
    """Test-only hook -- forces the next `_load_entries()` call to
    re-parse from disk (or a monkeypatched `CSV_PATH`) instead of reusing
    the module-level cache."""
    global _ENTRIES
    _ENTRIES = None


def _score(item_name_lower: str, entry: FoodKeeperEntry) -> int:
    entry_name_lower = entry.name.lower()
    if item_name_lower == entry_name_lower:
        return 100
    if item_name_lower in entry.keywords:
        return 90
    if item_name_lower in entry_name_lower or entry_name_lower in item_name_lower:
        # Scales with how much of the FoodKeeper name matched, so a short
        # generic name (e.g. "Butter") can't outrank a longer, more
        # specific one (e.g. "Buttermilk") just because both are
        # substring hits and it happens to sort earlier in the catalog --
        # caught live via a failing test where "fresh buttermilk from the
        # farm" incorrectly matched "Butter" over "Buttermilk".
        return 55 + min(len(entry_name_lower), 25)
    best_keyword_hit = 0
    for kw in entry.keywords:
        if len(kw) < 5 or kw in _GENERIC_KEYWORD_STOPLIST:
            continue  # too short/generic to be a meaningful signal on its own
        if kw in item_name_lower or item_name_lower in kw:
            best_keyword_hit = max(best_keyword_hit, 45 + min(len(kw), 20))
    return best_keyword_hit


def match_item(name: str) -> FoodKeeperEntry | None:
    """Best-effort fuzzy match of a free-text inventory item name against
    the FoodKeeper catalog. Returns the highest-scoring entry at or above
    `MIN_MATCH_SCORE`, or `None` -- callers should treat `None` as "no
    confident guess," not an error."""
    name_lower = (name or "").strip().lower()
    if not name_lower:
        return None
    best_entry: FoodKeeperEntry | None = None
    best_score = 0
    for entry in _load_entries():
        score = _score(name_lower, entry)
        if score > best_score:
            best_score = score
            best_entry = entry
    if best_score < MIN_MATCH_SCORE:
        return None
    return best_entry


def suggest_shelf_life(name: str, category: str) -> dict | None:
    """Looks up `name` against the FoodKeeper catalog and returns a
    suggestion dict for the given `InventoryItem.category` (pantry/fridge/
    freezer/produce), or `None` if there's no confident match or no
    FoodKeeper data for that storage location. Shape:
    `{matched_name, foodkeeper_id, storage, days_min, days_max}` -- the
    caller (the router, or `suggest_expiration_date` below) decides how to
    turn a day range into an actual date."""
    entry = match_item(name)
    if entry is None:
        return None
    for storage in CATEGORY_FIELD_ORDER.get(category, ()):
        days_min, days_max = entry.days_for(storage)
        if days_min is not None or days_max is not None:
            return {
                "matched_name": entry.display_name,
                "foodkeeper_id": entry.id,
                "storage": storage,
                "days_min": days_min,
                "days_max": days_max,
            }
    return None


def suggest_expiration_date(name: str, category: str, purchased_date: date | None) -> dict | None:
    """Convenience wrapper: same lookup as `suggest_shelf_life`, plus an
    actual suggested `date` computed from `purchased_date` (defaulting to
    today if not given, since a household adding an item without a
    purchase date is almost always adding it the day they bought it).

    Deliberately anchors the suggested date on `days_min`, the sooner/more
    conservative end of FoodKeeper's range, not the midpoint or max --
    food-safety guidance should round down, not up, when a range has to
    collapse to one default date; `days_max` is still returned alongside
    it so the household can see the fuller range and move the date later
    themselves if they know it's likely to keep longer (e.g. a well-sealed
    freezer item)."""
    result = suggest_shelf_life(name, category)
    if result is None:
        return None
    anchor = purchased_date or date.today()
    days = result["days_min"] if result["days_min"] is not None else result["days_max"]
    result["suggested_expiration_date"] = anchor + timedelta(days=days) if days is not None else None
    return result
