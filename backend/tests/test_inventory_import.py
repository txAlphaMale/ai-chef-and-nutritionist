"""Tests for the B4.2 receipt/list import (author-requested 2026-08-01,
app/routers/inventory.py's import_inventory endpoint and its
RECEIPT_IMPORT_PROMPT).

Router-level AI-dispatch logic in this project has never had dedicated
unit tests (recipe import's own history -- see PROJECT-PLAN.md -- was
verified via live curl instead, since there's no live Ollama reachable
in this sandbox to actually exercise the AI call). This file sticks to
what's genuinely pure and worth locking down: the new prompt template's
`.format()` safety (a stray unescaped brace would raise at request time,
not at import time, so it's worth catching here), and
inventory_service.parse_vision_response -- already covered elsewhere,
exercised once more here specifically against a RECEIPT-shaped fixture,
since that's the exact function this new intake source reuses unchanged.
"""
from __future__ import annotations

from app.routers.inventory import RECEIPT_IMPORT_PROMPT
from app.services import inventory_service


def test_receipt_import_prompt_formats_with_real_text():
    rendered = RECEIPT_IMPORT_PROMPT.format(content="ORG BANANA 1.29\nGV 2% MLK GAL 3.49\nSUBTOTAL 4.78")
    assert "ORG BANANA" in rendered
    assert "{content}" not in rendered  # placeholder actually got filled, not left literal


def test_receipt_import_prompt_instructs_skipping_non_food_items():
    # Bug fix (2026-08-02, author-reported): a real Walmart order printout
    # mixes food with household/personal-care/pet items on the same
    # receipt, and the original prompt only told the model to skip
    # subtotal/tax/tender lines -- nothing about non-food purchases. This
    # locks down that the exclusion instruction is actually present so a
    # future prompt edit can't silently drop it again.
    rendered = RECEIPT_IMPORT_PROMPT.format(content="irrelevant")
    for phrase in ("household", "personal care", "pet food", "supplements"):
        assert phrase in rendered.lower()
    assert "empty array" in rendered.lower()


def test_receipt_import_prompt_formats_with_photo_placeholder():
    # Mirrors recipe_service.RECIPE_IMPORT_PROMPT's own trick: the same
    # template is reused for the image-upload path by substituting a
    # short description instead of real extracted text.
    rendered = RECEIPT_IMPORT_PROMPT.format(content="[see attached photo of a receipt]")
    assert "[see attached photo of a receipt]" in rendered


def test_parse_vision_response_handles_receipt_shaped_output():
    raw = """[
        {"name": "Organic bananas", "estimated_quantity": 1, "unit": "bunch", "category": "produce",
         "estimated_expiration_days": 5, "confidence_note": null},
        {"name": "Great Value 2% milk, 1 gallon", "estimated_quantity": 1, "unit": "gallon", "category": "fridge",
         "estimated_expiration_days": 10, "confidence_note": "abbreviated as GV 2% MLK GAL on receipt"}
    ]"""
    items = inventory_service.parse_vision_response(raw)
    assert len(items) == 2
    assert items[0]["name"] == "Organic bananas"
    assert items[0]["category"] == "produce"
    assert items[1]["confidence_note"] == "abbreviated as GV 2% MLK GAL on receipt"


def test_parse_vision_response_skips_non_item_entries_missing_a_name():
    # A model that ignores the "skip subtotal/tax lines" instruction and
    # emits a malformed entry anyway shouldn't crash the parser -- same
    # defensive-parsing discipline as every other AI-output consumer in
    # this app.
    raw = '[{"estimated_quantity": 4.78, "category": "other"}, {"name": "Eggs", "category": "fridge"}]'
    items = inventory_service.parse_vision_response(raw)
    assert len(items) == 1
    assert items[0]["name"] == "Eggs"
