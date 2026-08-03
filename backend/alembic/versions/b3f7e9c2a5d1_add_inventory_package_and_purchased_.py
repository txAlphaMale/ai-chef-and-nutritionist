"""add inventory package and purchased quantity fields

Revision ID: b3f7e9c2a5d1
Revises: d2a7c4f19b3e
Create Date: 2026-08-02 00:00:00.000000

Author-requested quantity/packaging redesign (2026-08-02 session): see
app/models/inventory.py's InventoryItem docstring for the full
before/after rationale (the short version: `unit` used to hold a
compound string like "8 oz bag", which broke both recipe-confirm
deduction's unit conversion and B6.1's cost-per-serving math). This
migration adds the four new columns and, critically, BACKFILLS existing
rows on a best-effort basis rather than leaving every pre-existing item
in a permanently degraded state.

Backfill approach: for each existing row, try to split its current
`unit` text into a leading "<number> <unit word>" plus a leftover
descriptor (e.g. "8 oz bag" -> 8 / "oz" / "bag", "10oz 6 Count pack" ->
10 / "oz" / "6 Count pack"). When that succeeds: `unit` is REWRITTEN to
the clean canonical unit (fixing the row's display and making it
convertible going forward), `package_quantity`/`package_descriptor` are
set from the parse, `package_count` takes over the row's OLD `quantity`
value (which, before this session, was really "how many packages" --
see the model docstring), and both `purchased_quantity` and the NEW
`quantity` are set to `package_count * package_quantity` -- i.e. this
migration's one necessary, documented ASSUMPTION is that nothing has
been used yet as of the upgrade. That's the only honest default
available (there is no historical "how much has been used" data to
recover), and it's flagged here plainly rather than silently guessed:
a household with partially-used items should expect to spot-check and
adjust `quantity` down after upgrading, exactly the same way B4.3's
FoodKeeper suggestions or B1.2's AI-estimated nutrition ask to be
treated as a starting point, not a guarantee.

When the leading-measure parse does NOT succeed (the existing `unit`
text has no recognizable leading number+unit at all -- e.g. "count",
"bunch", "each", or already-clean text): the row is left COMPLETELY
alone beyond adding the new nullable columns as NULL -- same numeric
`quantity` value, same `unit` text, same behavior as before this
migration ran. No regression either way.

Deliberately carries its OWN frozen copy of the parsing regex rather
than importing app.services.package_parsing (which exists for going-
forward use by the barcode-lookup endpoint, the order-import service,
and any future caller) -- every other migration in this repo is pure
schema DDL with zero application-code imports, and this one keeping
that same independence means a later edit to package_parsing.py's
matching rules can never silently change what a fresh `alembic upgrade
head` does to someone's existing database. The regex logic below is
intentionally a snapshot, not a shared dependency.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import re


# revision identifiers, used by Alembic.
revision: str = 'b3f7e9c2a5d1'
down_revision: Union[str, None] = 'd2a7c4f19b3e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Frozen snapshot of package_parsing.py's unit-word list and
# leading-measure pattern, as of this migration's authoring -- see the
# module docstring above for why this is deliberately NOT imported from
# the live service module.
_UNIT_WORDS = [
    "fl oz", "fl. oz", "fl. oz.", "tablespoon", "teaspoon", "kilogram", "kilograms",
    "milliliter", "milliliters", "millilitre", "ounce", "ounces", "gallon", "gallons",
    "quart", "quarts", "pound", "pounds", "liter", "liters", "litre", "fluid ounce",
    "fluid oz", "pint", "pints", "tbsp", "tbls", "tbl", "floz", "each", "count",
    "kilo", "kilos", "cups", "gram", "grams", "gal", "cup", "tsp", "lbs", "lb", "kg",
    "ml", "l", "oz", "g", "t", "c", "pt", "qt", "ct", "ea",
]
_UNIT_WORDS.sort(key=len, reverse=True)
_UNIT_ALTERNATION = "|".join(re.escape(w) for w in _UNIT_WORDS)
_LEADING_MEASURE_RE = re.compile(
    rf"^\s*(?P<size>\d+(?:\.\d+)?)\s*(?P<unit>{_UNIT_ALTERNATION})\b\.?\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
_CANON = {
    "ounce": "oz", "ounces": "oz", "lbs": "lb", "pound": "lb", "pounds": "lb",
    "kilogram": "kg", "kilograms": "kg", "kilo": "kg", "kilos": "kg",
    "gram": "g", "grams": "g", "t": "tsp", "teaspoon": "tsp",
    "tablespoon": "tbsp", "tbl": "tbsp", "tbls": "tbsp",
    "c": "cup", "cups": "cup", "pint": "pt", "pints": "pt",
    "quart": "qt", "quarts": "qt", "gallon": "gal", "gallons": "gal",
    "milliliter": "ml", "milliliters": "ml", "millilitre": "ml",
    "liter": "l", "liters": "l", "litre": "l",
    "fl oz": "fl_oz", "fl. oz": "fl_oz", "fl. oz.": "fl_oz", "floz": "fl_oz",
    "fluid ounce": "fl_oz", "fluid oz": "fl_oz",
    "ct": "count", "ea": "count", "each": "count",
}


def _parse_backfill(text: str | None):
    if not text or not text.strip():
        return None
    match = _LEADING_MEASURE_RE.match(text.strip())
    if not match:
        return None
    raw_unit = match.group("unit").strip().lower().rstrip(".")
    unit = _CANON.get(raw_unit, raw_unit)
    rest = match.group("rest").strip() or None
    return float(match.group("size")), unit, rest


def upgrade() -> None:
    with op.batch_alter_table("inventory_items") as batch_op:
        batch_op.add_column(sa.Column("purchased_quantity", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("package_quantity", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("package_count", sa.Float(), nullable=True))
        batch_op.add_column(sa.Column("package_descriptor", sa.String(length=50), nullable=True))

    bind = op.get_bind()
    inventory_items = sa.table(
        "inventory_items",
        sa.column("id", sa.Integer),
        sa.column("quantity", sa.Float),
        sa.column("unit", sa.String),
        sa.column("purchased_quantity", sa.Float),
        sa.column("package_quantity", sa.Float),
        sa.column("package_count", sa.Float),
        sa.column("package_descriptor", sa.String),
    )

    rows = bind.execute(sa.select(inventory_items.c.id, inventory_items.c.quantity, inventory_items.c.unit)).fetchall()
    for row_id, old_quantity, old_unit in rows:
        parsed = _parse_backfill(old_unit)
        if parsed is None:
            continue  # leave completely alone -- see module docstring
        pkg_qty, canonical_unit, descriptor = parsed
        old_package_count = old_quantity if old_quantity is not None else 1.0
        new_quantity = old_package_count * pkg_qty
        bind.execute(
            inventory_items.update()
            .where(inventory_items.c.id == row_id)
            .values(
                unit=canonical_unit,
                package_quantity=pkg_qty,
                package_count=old_package_count,
                package_descriptor=descriptor,
                purchased_quantity=new_quantity,
                quantity=new_quantity,
            )
        )


def downgrade() -> None:
    # The backfill's `unit` rewrite (compound text -> canonical unit) and
    # `quantity` rewrite (package count -> on-hand measurement) are not
    # reversible without the original pre-migration strings, which are
    # not retained anywhere -- same one-way-migration posture this repo
    # already takes for other backfills (e.g. B1.1's food-database
    # resolution columns). Only the new columns themselves are dropped.
    with op.batch_alter_table("inventory_items") as batch_op:
        batch_op.drop_column("package_descriptor")
        batch_op.drop_column("package_count")
        batch_op.drop_column("package_quantity")
        batch_op.drop_column("purchased_quantity")
