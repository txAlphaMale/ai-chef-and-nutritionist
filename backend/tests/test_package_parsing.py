"""Tests for app.services.package_parsing -- the shared best-effort
parser that splits a freeform package-size string into a canonical
measurement unit, a package size, and a leftover descriptor. See the
module's own docstring for the full "why" (the inventory quantity/
packaging redesign, 2026-08-02 session) and PROJECT-PLAN.md's Phase 3
notes for how this feeds barcode-lookup, order-import, and the
inventory_service.parse_vision_response AI-import path.

Cases below are drawn directly from real examples the author's own
screenshot/receipt data produced under the OLD compound-unit design
(the exact strings that used to sit in InventoryItem.unit), so this
suite doubles as a regression guard against the specific real-world
inputs the redesign was meant to fix.
"""
from __future__ import annotations

from app.services.package_parsing import parse_package_text


def test_leading_measure_with_container_word():
    result = parse_package_text("8 oz Bag")
    assert result.package_quantity == 8
    assert result.unit == "oz"
    assert result.package_descriptor == "Bag"
    assert result.package_count == 1


def test_leading_measure_no_space_between_number_and_unit():
    result = parse_package_text("750ml")
    assert result.package_quantity == 750
    assert result.unit == "ml"
    assert result.package_descriptor is None


def test_real_receipt_shaped_compound_string():
    # The exact string this app's old RECEIPT_IMPORT_PROMPT rule 5 used
    # as its own worked example ("8 oz bag") plus a real multi-word
    # descriptor variant seen in the author's own inventory screenshot.
    result = parse_package_text("10oz 6 Count pack")
    assert result.package_quantity == 10
    assert result.unit == "oz"
    assert result.package_descriptor == "6 Count pack"


def test_descriptor_after_unit_is_kept_verbatim_not_further_parsed():
    result = parse_package_text("14 oz can each")
    assert result.package_quantity == 14
    assert result.unit == "oz"
    assert result.package_descriptor == "can each"


def test_pounds_and_ounces_normalize_to_canonical_abbreviations():
    result = parse_package_text("2 lbs")
    assert result.unit == "lb"
    result = parse_package_text("2 pounds ground beef")
    assert result.unit == "lb"
    assert result.package_descriptor == "ground beef"


def test_multipack_pattern_sets_package_count_and_size_separately():
    # Open Food Facts' own quantity-field convention for a case pack.
    result = parse_package_text("12 x 355 ml")
    assert result.package_count == 12
    assert result.package_quantity == 355
    assert result.unit == "ml"
    assert result.package_descriptor is None


def test_multipack_pattern_no_spaces():
    result = parse_package_text("6x8oz")
    assert result.package_count == 6
    assert result.package_quantity == 8
    assert result.unit == "oz"


def test_leading_count_word_is_canonicalized_to_count():
    result = parse_package_text("6 Count pack")
    assert result.package_quantity == 6
    assert result.unit == "count"
    assert result.package_descriptor == "pack"


def test_plain_gram_quantity_no_descriptor():
    result = parse_package_text("500 g")
    assert result.package_quantity == 500
    assert result.unit == "g"
    assert result.package_descriptor is None


def test_gallon_unit():
    result = parse_package_text("1 gal")
    assert result.package_quantity == 1
    assert result.unit == "gal"


def test_no_leading_number_returns_none():
    # No measurement to extract at all -- caller should keep treating
    # this as an opaque count-style descriptor, same as before this
    # module existed.
    assert parse_package_text("each") is None
    assert parse_package_text("bunch") is None
    assert parse_package_text("count") is None


def test_bare_number_with_no_unit_word_returns_none():
    # "3" alone isn't a measurement -- there's no unit to anchor on, so
    # this must not be misread as e.g. "3 count".
    assert parse_package_text("3") is None


def test_blank_and_none_input_returns_none():
    assert parse_package_text("") is None
    assert parse_package_text("   ") is None
    assert parse_package_text(None) is None


def test_unrecognized_unit_word_returns_none_rather_than_guessing():
    assert parse_package_text("12 pieces") is None
