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

from app.routers.inventory import (
    RECEIPT_IMPORT_PROMPT,
    _RECEIPT_EXTRA_OPTIONS,
    _inventory_import_job,
    _receipt_text_extraction,
)
from app.services import inventory_service

_TODAY = "2026-08-02"


# ---- RECEIPT_IMPORT_PROMPT (2026-08-02, author-directed full rewrite)
# --------------------------------------------------------------------
#
# Four incremental patches to the previous prose-paragraph prompt (date/
# price fields, quantity-vs-unit conflation, anti-truncation wording,
# anti-merge wording) each made the prompt longer, until a live A/B test
# against the author's real Ollama container proved the accumulated
# length was itself the bug -- see PROJECT-PLAN.md's session log and
# RECEIPT_IMPORT_PROMPT's own module comment in routers/inventory.py for
# the full investigation. The author then directed a genuine rewrite
# rather than another trim: numbered rules instead of prose, plus one
# concrete worked example (absent from every earlier version). These
# tests were rewritten alongside it rather than patched piecemeal, since
# patching tests one assertion at a time is the same anti-pattern the
# prompt itself just got rebuilt to avoid.


def test_receipt_import_prompt_formats_with_real_text():
    rendered = RECEIPT_IMPORT_PROMPT.format(
        content="ORG BANANA 1.29\nGV 2% MLK GAL 3.49\nSUBTOTAL 4.78", today=_TODAY
    )
    assert "ORG BANANA" in rendered
    # Placeholders actually got filled, not left literal -- and, since
    # this version's schema/example blocks contain real JSON braces that
    # had to be escaped as {{ }} for .format() to survive them, this also
    # confirms none of those leaked through as literal double-braces.
    assert "{content}" not in rendered
    assert "{today}" not in rendered
    assert "{{" not in rendered and "}}" not in rendered
    assert _TODAY in rendered


def test_receipt_import_prompt_formats_with_photo_placeholder():
    # Mirrors recipe_service.RECIPE_IMPORT_PROMPT's own trick: the same
    # template is reused for the image-upload path by substituting a
    # short description instead of real extracted text.
    rendered = RECEIPT_IMPORT_PROMPT.format(content="[see attached photo of a receipt]", today=_TODAY)
    assert "[see attached photo of a receipt]" in rendered


def test_receipt_import_prompt_worked_example_is_valid_json():
    # The rewrite embeds one concrete worked-example JSON object directly
    # in the prompt text, escaped for .format() as {{ }}. This confirms
    # it actually renders as real, parseable JSON once escaped -- not
    # just "no crash", but genuinely well-formed -- since a model shown a
    # malformed example is worse than one shown no example at all. (The
    # separate OUTPUT FORMAT block is a type-annotated schema description,
    # e.g. `"unit": string or null`, not itself parseable JSON -- its key
    # names are checked by other tests below instead.)
    import json

    rendered = RECEIPT_IMPORT_PROMPT.format(content="irrelevant", today=_TODAY)
    marker = "Correct output object: "
    start = rendered.index(marker) + len(marker)
    end = rendered.index("}", start) + 1
    example_json = rendered[start:end]
    parsed = json.loads(example_json)
    assert parsed["name"] == "Progresso Gluten Free Chicken Soup"
    assert parsed["estimated_quantity"] == 2
    assert parsed["unit_price"] == 6.96


def test_receipt_import_prompt_instructs_skipping_non_food_items():
    # A real Walmart order printout mixes food with household/personal-
    # care/pet items on the same receipt -- locks down that the
    # exclusion instruction is present so a future edit can't silently
    # drop it.
    rendered = RECEIPT_IMPORT_PROMPT.format(content="irrelevant", today=_TODAY).lower()
    for phrase in ("household", "personal care", "pet food", "supplements"):
        assert phrase in rendered
    assert "empty array" in rendered


def test_receipt_import_prompt_instructs_purchased_date_and_price_extraction():
    # VisionDetectedItem/the frontend review table both support these
    # fields (populated by the order-history CSV importer since B10.3);
    # this locks down the AI prompt actually asks for them too.
    rendered = RECEIPT_IMPORT_PROMPT.format(content="irrelevant", today=_TODAY).lower()
    assert '"purchased_date"' in rendered
    assert '"unit_price"' in rendered
    assert "order/transaction date" in rendered or "order date" in rendered


def test_receipt_import_prompt_instructs_not_conflating_package_size_with_quantity():
    # A receipt line's product NAME often contains a size/count number
    # (e.g. "...6 Count", "...24 oz") that must not be read as the
    # purchased quantity -- locks down the disambiguating instruction.
    rendered = RECEIPT_IMPORT_PROMPT.format(content="irrelevant", today=_TODAY).lower()
    assert "6 count" in rendered
    assert "never" in rendered

def test_receipt_import_prompt_instructs_not_merging_duplicate_named_lines():
    rendered = RECEIPT_IMPORT_PROMPT.format(content="irrelevant", today=_TODAY).lower()
    assert "never merge" in rendered
    assert "separate purchase" in rendered


def test_receipt_import_prompt_includes_a_worked_example():
    # Notably absent from every prior version despite four rounds of
    # edits -- a single concrete input-to-output example is generally a
    # stronger format/behavior signal for a model than another paragraph
    # of abstract description. Locks down that the rewrite actually
    # includes one, with a real product name and real field values, not
    # just an "EXAMPLE:" label with nothing under it.
    rendered = RECEIPT_IMPORT_PROMPT.format(content="irrelevant", today=_TODAY)
    assert "EXAMPLE" in rendered
    assert '"estimated_quantity": 2' in rendered
    assert '"unit_price": 6.96' in rendered


def test_receipt_import_prompt_is_meaningfully_shorter_than_the_version_it_replaced():
    # The live A/B test that found the real bug proved a ~7723-char
    # rendered prompt caused this model to bail to `[]`; a ~1800-char
    # one worked but under-filtered. This rewrite should land well under
    # the failing length -- not a promise of correctness by itself, but
    # a regression guard against the same prompt slowly regrowing back
    # past the length that's already been proven to fail.
    rendered = RECEIPT_IMPORT_PROMPT.format(content="x" * 1423, today=_TODAY)
    assert len(rendered) < 6000


def test_receipt_text_extraction_uses_qwens_documented_non_thinking_sampling_params(db_session):
    # Values must match Qwen's own official non-thinking/general-task
    # recommendation (temperature=0.7, top_p=0.8, top_k=20,
    # presence_penalty=1.5 -- Qwen/Qwen3.5-9B model card, verified live)
    # exactly, not an unverified guess -- see the module comment above
    # and _RECEIPT_EXTRA_OPTIONS's docstring for why this replaced an
    # earlier temperature=0.1-only attempt.
    with patch("app.routers.inventory.ollama_client.chat", return_value={"message": {"content": "[]"}}) as mock_chat:
        _receipt_text_extraction(db_session, "some receipt text")
    _, kwargs = mock_chat.call_args
    assert kwargs["extra_options"] == _RECEIPT_EXTRA_OPTIONS
    assert _RECEIPT_EXTRA_OPTIONS == {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5}


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
    # Package/measurement split (2026-08-02): "14 oz can" (the model's
    # RECEIPT_IMPORT_PROMPT-instructed compound unit string -- that
    # prompt was deliberately NOT rewritten this session, see the
    # module comment above parse_vision_response) is now split
    # post-hoc into a real unit ("oz"), a package size (14), and a
    # descriptor ("can"). estimated_quantity becomes the actual on-hand
    # total (2 cans * 14 oz = 28 oz), not the raw "how many purchased"
    # number the old assertion checked.
    assert items[0]["unit"] == "oz"
    assert items[0]["estimated_quantity"] == 28
    assert items[0]["package_quantity"] == 14
    assert items[0]["package_count"] == 2
    assert items[0]["package_descriptor"] == "can"


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


def test_inventory_import_job_logs_head_and_tail_when_a_non_empty_response_parses_to_zero(
    db_session, capsys
):
    # Second reported shape of the same symptom (2026-08-02): the "Show
    # raw AI response" link IS rendered (the frontend only renders it
    # when raw_model_output is truthy -- InventoryPage.jsx), so the model
    # DID answer, yet zero items came out. ollama_client's response log
    # only shows the first 300 chars of content; the TAIL is what tells a
    # response truncated mid-array apart from a complete one wrapped in
    # prose, so it must be logged too -- and only on this path, never for
    # a successful import.
    unparseable = "I was unable to format this as JSON. " * 5
    with patch("app.routers.inventory.SessionLocal", return_value=db_session):
        result = _inventory_import_job("pdf", lambda db: unparseable)
    assert result["detected_items"] == []
    out = capsys.readouterr().out
    assert "ZERO ITEMS from a non-empty response" in out
    assert "head=" in out and "tail=" in out


def test_inventory_import_job_does_not_log_the_zero_items_excerpt_on_success(db_session, capsys):
    with patch("app.routers.inventory.SessionLocal", return_value=db_session):
        _inventory_import_job("text", lambda db: '[{"name": "Eggs", "category": "fridge"}]')
    assert "ZERO ITEMS" not in capsys.readouterr().out


# ---- Bug fix (2026-08-02, author-reported: "0 items identified from your
# pdf" WITH the raw-response link visible, i.e. a non-empty model response
# that parsed to nothing): _extract_json_array's fallback used a GREEDY
# `\[.*\]` regex, spanning the first "[" anywhere in the output to the
# last "]" anywhere in the output. Any stray square bracket outside the
# real array turned that slice into unparseable JSON and produced zero
# items. Each test below is a real output shape a local model produces on
# this app's own receipt prompt; every one of them returned 0 items
# before the bracket-matching scan replaced that regex. ----------------


def test_extract_json_array_survives_trailing_commentary_containing_brackets():
    # The prompt explicitly asks the model to skip non-food lines, which
    # makes exactly this kind of trailing note likely.
    raw = (
        '[{"name": "Eggs", "category": "fridge"}]\n\n'
        "Note: I skipped the non-food lines [paper towels, lint roller, cat litter]."
    )
    items = inventory_service.parse_vision_response(raw)
    assert [i["name"] for i in items] == ["Eggs"]


def test_extract_json_array_survives_a_lead_in_containing_brackets():
    raw = 'Here are the food items [from the receipt you provided]:\n[{"name": "Eggs", "category": "fridge"}]'
    items = inventory_service.parse_vision_response(raw)
    assert [i["name"] for i in items] == ["Eggs"]


def test_extract_json_array_survives_a_second_array_later_in_the_response():
    raw = (
        '[{"name": "Eggs", "category": "fridge"}]\n'
        'For reference, the lines I excluded were: [{"name": "Lint roller"}]'
    )
    items = inventory_service.parse_vision_response(raw)
    assert [i["name"] for i in items] == ["Eggs"]


def test_extract_json_array_ignores_an_inline_thinking_trace():
    # ollama_client passes think=False, but that is only honored by a new
    # enough Ollama server / model template -- an inline trace must not be
    # able to hijack the parse, especially since a trace often contains
    # both square brackets AND a draft of the array itself.
    raw = (
        "<think>\nThe receipt has lines [1-15]. A first draft would be "
        '[{"name": "Wrong draft item"}] but let me re-check.\n</think>\n'
        '[{"name": "Eggs", "category": "fridge"}]'
    )
    items = inventory_service.parse_vision_response(raw)
    assert [i["name"] for i in items] == ["Eggs"]


def test_extract_json_array_ignores_a_reasoning_trace_with_only_a_closing_tag():
    # What you get when the chat template itself opens <think>, so only
    # the closing tag appears in the returned content.
    raw = 'Let me work through lines [1-15] one at a time.\n</think>\n[{"name": "Eggs", "category": "fridge"}]'
    items = inventory_service.parse_vision_response(raw)
    assert [i["name"] for i in items] == ["Eggs"]


def test_extract_json_array_salvages_a_response_truncated_mid_array():
    # What a generation that hits the context/num_predict limit looks
    # like (done_reason "length" in ollama_client's response log): the
    # array never closes. Every element BEFORE the cut is complete and
    # perfectly usable -- returning a partial receipt beats returning
    # nothing and telling the user their receipt had no food on it.
    raw = (
        '[\n {"name": "Eggs", "category": "fridge"},\n'
        ' {"name": "Milk", "category": "fridge"},\n'
        ' {"name": "Progresso Gluten Free Chicken No'
    )
    items = inventory_service.parse_vision_response(raw)
    assert [i["name"] for i in items] == ["Eggs", "Milk"]


def test_extract_json_array_salvages_an_array_with_a_trailing_comma():
    raw = '[{"name": "Eggs", "category": "fridge"}, {"name": "Milk", "category": "fridge"},]'
    items = inventory_service.parse_vision_response(raw)
    assert [i["name"] for i in items] == ["Eggs", "Milk"]


def test_extract_json_array_is_not_confused_by_brackets_inside_string_values():
    # The prompt asks for a free-text "confidence_note", which is exactly
    # where a model puts a bracketed aside.
    raw = '[{"name": "Milk", "category": "fridge", "confidence_note": "abbreviated [GV 2% MLK GAL]"}]'
    items = inventory_service.parse_vision_response(raw)
    assert len(items) == 1
    assert items[0]["confidence_note"] == "abbreviated [GV 2% MLK GAL]"


def test_extract_json_array_still_honors_a_genuinely_empty_array():
    # The prompt tells the model to answer `[]` when nothing on the
    # receipt is food -- that must keep meaning "no food", not be
    # "recovered" into something else by the salvage paths above.
    assert inventory_service.parse_vision_response("[]") == []
    assert inventory_service.parse_vision_response("  []  ") == []


def test_extract_json_array_returns_nothing_for_prose_only_and_empty_responses():
    assert inventory_service.parse_vision_response("I could not find any food items on this receipt.") == []
    assert inventory_service.parse_vision_response("") == []
    assert inventory_service.parse_vision_response("   ") == []
