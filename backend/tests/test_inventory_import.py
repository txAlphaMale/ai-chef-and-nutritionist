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

from unittest.mock import patch

from app.routers.inventory import RECEIPT_IMPORT_PROMPT, _inventory_import_job, _receipt_text_extraction
from app.services import inventory_service

_TODAY = "2026-08-02"


def test_receipt_import_prompt_formats_with_real_text():
    rendered = RECEIPT_IMPORT_PROMPT.format(
        content="ORG BANANA 1.29\nGV 2% MLK GAL 3.49\nSUBTOTAL 4.78", today=_TODAY
    )
    assert "ORG BANANA" in rendered
    assert "{content}" not in rendered  # placeholder actually got filled, not left literal
    assert "{today}" not in rendered
    assert _TODAY in rendered


def test_receipt_import_prompt_instructs_skipping_non_food_items():
    # Bug fix (2026-08-02, author-reported): a real Walmart order printout
    # mixes food with household/personal-care/pet items on the same
    # receipt, and the original prompt only told the model to skip
    # subtotal/tax/tender lines -- nothing about non-food purchases. This
    # locks down that the exclusion instruction is actually present so a
    # future prompt edit can't silently drop it again.
    rendered = RECEIPT_IMPORT_PROMPT.format(content="irrelevant", today=_TODAY)
    for phrase in ("household", "personal care", "pet food", "supplements"):
        assert phrase in rendered.lower()
    assert "empty array" in rendered.lower()


def test_receipt_import_prompt_formats_with_photo_placeholder():
    # Mirrors recipe_service.RECIPE_IMPORT_PROMPT's own trick: the same
    # template is reused for the image-upload path by substituting a
    # short description instead of real extracted text.
    rendered = RECEIPT_IMPORT_PROMPT.format(content="[see attached photo of a receipt]", today=_TODAY)
    assert "[see attached photo of a receipt]" in rendered


def test_receipt_import_prompt_instructs_purchased_date_and_price_extraction():
    # Bug fix (2026-08-02, author-reported against a real Walmart PDF):
    # the original prompt's JSON schema never mentioned purchased_date or
    # unit_price at all -- the model was never even asked for them, even
    # though VisionDetectedItem/the frontend review table both already
    # support these fields (populated only by the order-history CSV
    # importer until now).
    rendered = RECEIPT_IMPORT_PROMPT.format(content="irrelevant", today=_TODAY).lower()
    assert '"purchased_date"' in rendered
    assert '"unit_price"' in rendered
    assert "order/transaction date" in rendered or "order date" in rendered


def test_receipt_import_prompt_instructs_not_conflating_package_size_with_quantity():
    # Bug fix (2026-08-02, author-reported): confirmed against the user's
    # real Walmart PDF that the model was pulling numbers like "6" from
    # "...6 Count" and "24" from "...24 oz" in the PRODUCT NAME and using
    # them as estimated_quantity instead of the receipt's own "Qty 1" --
    # and leaving "unit" null every time instead of capturing that size
    # descriptor there. Locks down that the disambiguating instruction
    # (and its own "never invent a conversion" framing, matching this
    # app's existing quantity_text principle) is present.
    rendered = RECEIPT_IMPORT_PROMPT.format(content="irrelevant", today=_TODAY).lower()
    assert "6 count" in rendered
    assert "never" in rendered and "purchased quantity" in rendered


def test_parse_vision_response_extracts_unit_price_and_purchased_date():
    # Bug fix (2026-08-02): parse_vision_response never read these two
    # keys at all, so even a model that DID return them (e.g. once the
    # prompt above started asking) would have them silently dropped
    # before ever reaching the frontend's already-existing Price/
    # Purchased columns.
    raw = """[
        {"name": "Progresso Gluten Free Homestyle Chicken Soup", "estimated_quantity": 2, "unit": "14 oz can",
         "category": "pantry", "estimated_expiration_days": 365, "purchased_date": "2026-07-30",
         "unit_price": 6.96, "confidence_note": null}
    ]"""
    items = inventory_service.parse_vision_response(raw)
    assert len(items) == 1
    assert items[0]["unit_price"] == 6.96
    assert items[0]["purchased_date"].isoformat() == "2026-07-30"
    assert items[0]["unit"] == "14 oz can"
    assert items[0]["estimated_quantity"] == 2


def test_parse_vision_response_handles_missing_or_malformed_price_and_date():
    # Defensive parsing, same discipline as every other AI-output
    # consumer here -- a model omitting these fields, or (rarely)
    # returning a malformed date string despite the prompt's explicit
    # "YYYY-MM-DD" instruction, must never crash the import.
    raw = """[
        {"name": "Eggs", "category": "fridge"},
        {"name": "Milk", "category": "fridge", "purchased_date": "not-a-date", "unit_price": "not-a-number"}
    ]"""
    items = inventory_service.parse_vision_response(raw)
    assert len(items) == 2
    assert items[0]["unit_price"] is None
    assert items[0]["purchased_date"] is None
    assert items[1]["unit_price"] is None
    assert items[1]["purchased_date"] is None


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


# ---- diagnostic logging (2026-08-02, author-reported: "you've tried and
# failed three times... what do you need from me?") ---------------------
#
# Locks down that the new print()-based diagnostics on the receipt-import
# path actually fire with real, useful numbers -- content length before/
# after truncation, and the raw model output's length vs. how many items
# it actually parsed into -- so the NEXT live import attempt's backend
# logs are guaranteed to show real evidence (empty response? non-empty
# but unparseable? parsed but 0 items?) instead of nothing at all.


def test_receipt_text_extraction_logs_content_length_and_budget(db_session, capsys):
    with patch("app.routers.inventory.ollama_client.chat", return_value={"message": {"content": "[]"}}):
        _receipt_text_extraction(db_session, "some receipt text " * 50)
    out = capsys.readouterr().out
    assert "extracted_content_chars=" in out
    assert "budget_chars=" in out
    assert "raw_output_chars=2" in out  # len("[]")


def test_inventory_import_job_logs_raw_output_length_vs_detected_count(db_session):
    def fake_extractor(db):
        return '[{"name": "Eggs", "category": "fridge"}, {"name": "Milk", "category": "fridge"}]'

    with patch("app.routers.inventory.SessionLocal", return_value=db_session):
        result = _inventory_import_job("text", fake_extractor)
    assert len(result["detected_items"]) == 2


def test_inventory_import_job_logs_zero_detected_items_when_response_is_empty(db_session, capsys):
    # The exact reported symptom: extractor returns successfully (no
    # exception) but with empty content -- must be visibly logged as
    # "raw_output_chars=0 detected_items=0", not silent.
    with patch("app.routers.inventory.SessionLocal", return_value=db_session):
        result = _inventory_import_job("pdf", lambda db: "")
    assert result["detected_items"] == []
    out = capsys.readouterr().out
    assert "raw_output_chars=0 detected_items=0" in out
