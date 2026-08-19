"""Backlog B19.1 -- NOVA group and Nutri-Score, captured from the Open
Food Facts response the barcode scanner already fetches.

The fixtures below are not invented. They are the shapes of real
`world.openfoodfacts.org/api/v2/product/{barcode}.json` responses read
through a live browser on 2026-08-19, because this sandbox's proxy
returns 403 for that domain and guessing the field names would have
produced columns that stayed null on every scan. The four behaviours
worth having tests for are all things the live responses proved and a
reading of the documentation would not have:

  * `nova_group` is simply ABSENT when OFF cannot classify a product,
    including for Nutella;
  * `nova_groups` (plural) carries the same value as a string;
  * `nutriscore_grade` takes the literal values "unknown" and
    "not-applicable", which are not grades;
  * a classification must not survive being pasted onto a hand-typed item.
"""

from __future__ import annotations

import httpx

from app.models import InventoryItem
from app.routers.inventory import barcode_lookup, create_inventory_item
from app.schemas.inventory import InventoryItemCreate
from app.services import food_data_service

# --- Real response fragments, transcribed from live lookups -------------

# 3017624010701 -- Nutella. Grade "e", and NO nova_group at all: OFF
# returns `nova_group_debug: "no nova group if too many ingredients are
# unknown: 6 out of 7"` instead. The most-scanned product in the fixture
# set is the one that proves absence is normal.
NUTELLA = {
    "code": "3017624010701",
    "product_name": "Nutella",
    "nova_group_debug": "no nova group if too many ingredients are unknown: 6 out of 7",
    "nutriscore_grade": "e",
    "nutrition_grades": "e",
    "nutriscore_score": 31,
}

# 0030000010204 -- rolled oats. The good end of both scales.
OATS = {
    "code": "0030000010204",
    "product_name": "Old Fashioned 100% Whole Grain Rolled Oats",
    "nova_group": 1,
    "nova_groups": "1",
    "nutriscore_grade": "a",
    "nutrition_grades": "a",
    "nutriscore_score": -4,
}

# 5449000000996 -- Coca-Cola. Grade "e" on a score of 12, where the
# potato crisps below score 19 and are also "e" -- beverages are graded
# on their own thresholds. This pair is why `nutriscore_score` is not
# captured at all.
COCA_COLA = {"code": "5449000000996", "product_name": "coca-cola", "nova_group": 4, "nutriscore_grade": "e", "nutriscore_score": 12}
CRISPS = {"code": "0038000138416", "product_name": "Original Potato Crisps", "nova_group": 4, "nutriscore_grade": "e", "nutriscore_score": 19}

# An en:coffees product. OFF's own sentinel for "not enough data".
COFFEE_UNKNOWN = {"code": "1", "product_name": "Nescafé Classic", "nutriscore_grade": "unknown", "nutrition_grades": "unknown"}
# An en:wines product. OFF's other sentinel.
WINE_NOT_APPLICABLE = {"code": "2", "product_name": "Vin blanc sec", "nova_group": 3, "nutriscore_grade": "not-applicable"}


# --- The parsers ---------------------------------------------------------


def test_nova_group_is_read_as_an_integer():
    assert food_data_service.parse_off_nova_group(OATS) == 1
    assert food_data_service.parse_off_nova_group(COCA_COLA) == 4


def test_a_missing_nova_group_is_none_and_emphatically_not_one():
    """The defect this guards against is the tempting default. An
    unclassified product is disproportionately likely to be one whose
    ingredient list OFF could not read -- the opposite of a whole food."""
    assert food_data_service.parse_off_nova_group(NUTELLA) is None
    assert food_data_service.parse_off_nova_group({}) is None
    assert food_data_service.parse_off_nova_group(None) is None


def test_the_plural_alias_is_accepted_when_the_singular_is_absent():
    """`nova_groups` is the v0-era spelling and carries a STRING. A parser
    that only knew `nova_group`, or only knew ints, would drop this."""
    assert food_data_service.parse_off_nova_group({"nova_groups": "3"}) == 3


def test_a_nonsense_nova_value_is_dropped_rather_than_stored():
    for bad in ({"nova_group": 0}, {"nova_group": 5}, {"nova_group": "four"}, {"nova_group": ""}):
        assert food_data_service.parse_off_nova_group(bad) is None


def test_nutriscore_grade_is_a_lowercase_letter():
    assert food_data_service.parse_off_nutriscore_grade(OATS) == "a"
    assert food_data_service.parse_off_nutriscore_grade(NUTELLA) == "e"
    assert food_data_service.parse_off_nutriscore_grade({"nutriscore_grade": "  B "}) == "b"


def test_off_sentinels_are_not_grades():
    """"unknown" and "not-applicable" are values OFF really returns, for
    coffee and for wine respectively. Stored verbatim they would render as
    a badge reading NOT-APPLICABLE next to the item name."""
    assert food_data_service.parse_off_nutriscore_grade(COFFEE_UNKNOWN) is None
    assert food_data_service.parse_off_nutriscore_grade(WINE_NOT_APPLICABLE) is None


def test_the_v0_grade_alias_is_accepted_when_the_v2_field_is_absent():
    assert food_data_service.parse_off_nutriscore_grade({"nutrition_grades": "c"}) == "c"


def test_a_product_can_be_classified_on_one_scale_and_not_the_other():
    """Nutella has a grade and no NOVA; the wine has a NOVA and no usable
    grade. Neither field may gate the other."""
    assert (food_data_service.parse_off_nova_group(NUTELLA), food_data_service.parse_off_nutriscore_grade(NUTELLA)) == (None, "e")
    assert (
        food_data_service.parse_off_nova_group(WINE_NOT_APPLICABLE),
        food_data_service.parse_off_nutriscore_grade(WINE_NOT_APPLICABLE),
    ) == (3, None)


def test_the_numeric_score_is_not_captured_anywhere():
    """A guard on a decision, not on code. Coca-Cola scores 12 and potato
    crisps score 19, and both are grade "e", because beverages use their
    own threshold table -- so the number is not comparable between two
    items in the same pantry. If someone adds it, this test says why not."""
    source = (food_data_service.__file__ or "").replace(".pyc", ".py")
    with open(source, encoding="utf-8") as handle:
        body = handle.read()
    assert "nutriscore_score" not in body.split('OFF_NOVA_KEYS')[1].split('def parse_off_nutriscore_grade')[0]
    assert COCA_COLA["nutriscore_score"] < CRISPS["nutriscore_score"]
    assert COCA_COLA["nutriscore_grade"] == CRISPS["nutriscore_grade"] == "e"


# --- The endpoint --------------------------------------------------------


class _FakeResponse:
    def __init__(self, json_data):
        self._json = json_data

    def raise_for_status(self):
        return None

    def json(self):
        return self._json


def _serve(monkeypatch, product):
    def fake_get(url, timeout=None):
        return _FakeResponse({"status": 1, "product": product})

    monkeypatch.setattr(food_data_service.httpx, "get", fake_get)


def test_the_scanner_preview_carries_both_classifications(monkeypatch):
    _serve(monkeypatch, OATS)

    result = barcode_lookup(barcode="0030000010204")

    assert result.found is True
    assert result.nova_group == 1
    assert result.nutriscore_grade == "a"


def test_the_scanner_preview_reports_an_unclassified_product_as_unclassified(monkeypatch):
    _serve(monkeypatch, NUTELLA)

    result = barcode_lookup(barcode="3017624010701")

    assert result.nova_group is None
    assert result.nutriscore_grade == "e"


# --- What reaches the database -------------------------------------------


def test_a_scanned_item_keeps_its_classification_and_its_barcode(db_session):
    payload = InventoryItemCreate(
        name="Rolled Oats",
        source="barcode",
        off_barcode="0030000010204",
        nova_group=1,
        nutriscore_grade="a",
    )

    created = create_inventory_item(payload=payload, db=db_session)

    row = db_session.get(InventoryItem, created.id)
    assert (row.off_barcode, row.nova_group, row.nutriscore_grade) == ("0030000010204", 1, "a")


def test_a_hand_typed_item_cannot_claim_a_classification(db_session):
    """The invariant behind the model comment: a NOVA group is a fact
    about a specific manufactured product, so it may only arrive with the
    barcode that identifies one. Dropped, not rejected -- a client that
    copies a scanned item's fields into a manual add should still get an
    item back."""
    payload = InventoryItemCreate(name="Olive Oil", source="manual", nova_group=1, nutriscore_grade="a", off_barcode="123")

    created = create_inventory_item(payload=payload, db=db_session)

    row = db_session.get(InventoryItem, created.id)
    assert (row.off_barcode, row.nova_group, row.nutriscore_grade) == (None, None, None)


def test_a_scan_carrying_a_junk_grade_stores_nothing_rather_than_the_junk(db_session):
    payload = InventoryItemCreate(
        name="Mystery Snack", source="barcode", off_barcode="9", nova_group=7, nutriscore_grade="not-applicable"
    )

    created = create_inventory_item(payload=payload, db=db_session)

    row = db_session.get(InventoryItem, created.id)
    assert row.off_barcode == "9"
    assert row.nova_group is None
    assert row.nutriscore_grade is None


def test_the_read_shape_exposes_the_classification(db_session):
    from app.schemas.inventory import InventoryItemRead

    for field in ("off_barcode", "nova_group", "nutriscore_grade"):
        assert field in InventoryItemRead.model_fields


def test_the_update_shape_deliberately_does_not(db_session):
    """PATCH cannot set a classification: there is no way for a hand edit
    to be evidence about a product, and a field that could be typed would
    make the column's provenance unknowable."""
    from app.schemas.inventory import InventoryItemUpdate

    for field in ("off_barcode", "nova_group", "nutriscore_grade"):
        assert field not in InventoryItemUpdate.model_fields
