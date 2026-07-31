"""Shared unit-conversion layer (backlog B5.3 / B10.5).

One conversion table used by two surfaces, per the dependency note at the
top of PROJECT-PLAN.md's Backlog section: B5.3 (accurate inventory
deduction and grocery-list aggregation, currently name-only matching that
leaves "2 cup flour" and "1 lb flour" as separate lines) and B10.5 (a
per-view Imperial/Metric/Weight toggle on a viewed recipe). Building it
once here rather than twice avoids two conversion tables drifting apart.

Three unit families, deliberately NOT interchangeable with each other
without a per-ingredient density:

- Volume (tsp, tbsp, fl_oz, cup, pt, qt, gal, ml, l) -- pure geometry,
  always convertible to each other.
- Mass (g, kg, oz, lb) -- pure geometry, always convertible to each other.
- Count / unitless (whole, clove, slice, can, package, ...) -- passthrough
  only; never convertible to volume or mass without recipe-specific
  knowledge this layer doesn't have (a "can" of beans is not a fixed
  volume or mass across brands).

Volume <-> mass (the weight-mode B10.5 asks for, and the piece B5.3's
grocery-aggregation needs to reconcile "2 cup flour" against "1 lb
flour") requires a per-ingredient density (g/mL) that this table cannot
supply -- that's what B1.1's food-database resolution layer is for
(USDA FoodData Central publishes gram-weight-per-portion data per food).
`convert()` accepts an optional `density_g_per_ml` for exactly that case;
callers with no density available should treat volume<->mass as
unavailable for that ingredient and say so, never guess -- same
"never let the LLM invent a conversion" discipline the backlog item
itself specifies for B10.5.

Unit-name matching is deliberately forgiving (`normalize_unit`): recipes
and inventory rows use inconsistent free-text spellings ("tbsp", "Tbsp",
"tablespoon", "tablespoons") because they come from AI extraction, manual
entry, and imports written by different sources at different times.
"""
from __future__ import annotations

from dataclasses import dataclass

# --- Canonical unit registry --------------------------------------------

# Every value is "how many base units does one of this unit equal."
# Volume base unit: milliliters. Mass base unit: grams.
VOLUME_TO_ML: dict[str, float] = {
    "ml": 1.0,
    "l": 1000.0,
    "tsp": 4.92892,
    "tbsp": 14.7868,
    "fl_oz": 29.5735,
    "cup": 236.588,
    "pt": 473.176,
    "qt": 946.353,
    "gal": 3785.41,
}

MASS_TO_G: dict[str, float] = {
    "g": 1.0,
    "kg": 1000.0,
    "oz": 28.3495,
    "lb": 453.592,
}

# Free-text spelling variants seen across AI extraction, manual entry, and
# imports -> canonical key in VOLUME_TO_ML / MASS_TO_G above. Lowercased,
# trailing "s" stripped, and punctuation-stripped before lookup (see
# normalize_unit), so this only needs to list the singular canonical-ish
# forms, not every plural/period variant.
UNIT_SYNONYMS: dict[str, str] = {
    "teaspoon": "tsp",
    "t": "tsp",
    "tablespoon": "tbsp",
    "tbl": "tbsp",
    "tbls": "tbsp",
    "fluid ounce": "fl_oz",
    "fluid oz": "fl_oz",
    "floz": "fl_oz",
    "fl oz": "fl_oz",
    "cups": "cup",
    "c": "cup",
    "pint": "pt",
    "pints": "pt",
    "quart": "qt",
    "quarts": "qt",
    "gallon": "gal",
    "gallons": "gal",
    "milliliter": "ml",
    "milliliters": "ml",
    "millilitre": "ml",
    "liter": "l",
    "liters": "l",
    "litre": "l",
    "gram": "g",
    "grams": "g",
    "gr": "g",
    "kilogram": "kg",
    "kilograms": "kg",
    "kilo": "kg",
    "kilos": "kg",
    "ounce": "oz",
    "ounces": "oz",
    "pound": "lb",
    "pounds": "lb",
    "lbs": "lb",
}

VOLUME_UNITS = frozenset(VOLUME_TO_ML)
MASS_UNITS = frozenset(MASS_TO_G)


def normalize_unit(unit: str | None) -> str | None:
    """Lowercases, strips whitespace/trailing periods, and resolves known
    synonyms to a canonical unit key. Returns None for an empty/None input
    (e.g. "salt to taste" has no unit at all) and returns the cleaned
    input unchanged if it isn't a recognized volume/mass unit -- most
    likely a count-based unit (e.g. "clove", "can"), which is valid and
    just not volume/mass-convertible."""
    if not unit:
        return None
    cleaned = unit.strip().lower().replace(".", "").strip()
    if cleaned in VOLUME_TO_ML or cleaned in MASS_TO_G:
        return cleaned
    if cleaned in UNIT_SYNONYMS:
        return UNIT_SYNONYMS[cleaned]
    # Try a naive singular form (strip one trailing "s") for anything not
    # already covered by an explicit synonym entry above.
    if cleaned.endswith("s") and cleaned[:-1] in (VOLUME_TO_ML.keys() | MASS_TO_G.keys() | UNIT_SYNONYMS.keys()):
        singular = cleaned[:-1]
        return UNIT_SYNONYMS.get(singular, singular)
    return cleaned


def unit_family(unit: str | None) -> str:
    """"volume" | "mass" | "count" (anything not recognized as either --
    count-based units are the norm for this app's free-text ingredient
    data, e.g. "2 eggs" or "1 can beans", and are valid, not an error)."""
    normalized = normalize_unit(unit)
    if normalized in VOLUME_UNITS:
        return "volume"
    if normalized in MASS_UNITS:
        return "mass"
    return "count"


@dataclass
class ConversionResult:
    quantity: float
    unit: str
    # True if a density was needed and used (volume<->mass); False for a
    # same-family conversion that needed no density at all.
    used_density: bool = False


def convert(
    quantity: float,
    from_unit: str | None,
    to_unit: str | None,
    density_g_per_ml: float | None = None,
) -> ConversionResult | None:
    """Converts `quantity` of `from_unit` into `to_unit`. Returns None
    (never guesses) when:
    - either unit is count-based (no fixed volume/mass), or
    - the units are in different families (volume vs. mass) and no
      `density_g_per_ml` was supplied.

    Same-family conversions (volume<->volume or mass<->mass) never need a
    density. Cross-family conversions (e.g. "2 cup" -> grams, the B10.5
    weight-mode case) do, and the caller is responsible for sourcing that
    density from B1.1's food-database resolution layer -- this function
    will not fabricate one."""
    from_norm = normalize_unit(from_unit)
    to_norm = normalize_unit(to_unit)
    if from_norm is None or to_norm is None:
        return None

    from_family = unit_family(from_norm)
    to_family = unit_family(to_norm)
    if from_family == "count" or to_family == "count":
        return None

    if from_family == to_family:
        if from_family == "volume":
            base_ml = quantity * VOLUME_TO_ML[from_norm]
            result_qty = base_ml / VOLUME_TO_ML[to_norm]
        else:
            base_g = quantity * MASS_TO_G[from_norm]
            result_qty = base_g / MASS_TO_G[to_norm]
        return ConversionResult(quantity=round(result_qty, 4), unit=to_norm, used_density=False)

    # Cross-family (volume <-> mass): requires density.
    if density_g_per_ml is None or density_g_per_ml <= 0:
        return None
    if from_family == "volume":
        grams = quantity * VOLUME_TO_ML[from_norm] * density_g_per_ml
        result_qty = grams / MASS_TO_G[to_norm]
    else:
        ml = (quantity * MASS_TO_G[from_norm]) / density_g_per_ml
        result_qty = ml / VOLUME_TO_ML[to_norm]
    return ConversionResult(quantity=round(result_qty, 4), unit=to_norm, used_density=True)


def units_are_comparable(unit_a: str | None, unit_b: str | None, have_density: bool = False) -> bool:
    """True if quantities in these two units could be reconciled by
    `convert()` -- same family always, or different families only if a
    density is available. Intended for callers like grocery-list
    aggregation and inventory deduction that need to decide "should I try
    to combine/compare these two ingredient lines" before doing the
    actual conversion."""
    fam_a, fam_b = unit_family(unit_a), unit_family(unit_b)
    if fam_a == "count" or fam_b == "count":
        return False
    if fam_a == fam_b:
        return True
    return have_density
