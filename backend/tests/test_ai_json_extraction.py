"""Tests for app/services/ai_json_extraction.py -- the shared, defensive
JSON-from-model-text extraction used by recipe import, recipe-chat edits,
health/bloodwork parsing, meal-plan generation (all object-shaped, via
extract_json_object), and receipt/vision intake (array-shaped, via
extract_json_array).

Bug fix under test (2026-08-03, author-reported: a plain, well-formed
recipe PDF import failed outright -- "Could not extract a recipe from
that input"). Root cause: `recipe_service._extract_json_object` (used by
every one of the object-shaped consumers above) was still using a naive
`re.search(r"\\{.*\\}", raw_text, re.DOTALL)` greedy fallback with zero
defense against a `<think>` reasoning trace landing inline in the
response -- the exact bug already found and fixed once before for the
ARRAY-shaped receipt/vision responses (see test_inventory_import.py's
own "Bug fix (2026-08-02...)" test group, which this file's object-shape
tests directly mirror). Most tests below are the object-shaped twin of
an existing array test, checked against the same real-world response
shapes a local thinking-capable model (the household's configured
`qwen3.6:27b`) actually produces.
"""
from __future__ import annotations

from app.services.ai_json_extraction import extract_json_array, extract_json_object, strip_reasoning


# --- strip_reasoning -----------------------------------------------------


def test_strip_reasoning_removes_a_complete_think_block():
    raw = "<think>scratch notes about { and } and [brackets]</think>{\"title\": \"Pie\"}"
    assert strip_reasoning(raw).strip() == '{"title": "Pie"}'


def test_strip_reasoning_removes_an_orphan_closing_tag_only():
    # What you get when the chat template itself opens <think>, so only
    # the closing tag appears in the returned content.
    raw = 'Let me think about the title field.\n</think>\n{"title": "Pie"}'
    assert strip_reasoning(raw).strip() == '{"title": "Pie"}'


def test_strip_reasoning_leaves_text_with_no_think_tags_alone():
    raw = '{"title": "Pie"}'
    assert strip_reasoning(raw) == raw


# --- extract_json_object: the actual reported bug -------------------------


def test_extract_json_object_ignores_an_inline_thinking_trace_containing_scratch_json():
    # This is the exact failure shape that broke recipe import: a
    # thinking-capable model's reasoning trace lands inline in
    # message.content (ollama_client requests think=False, but that isn't
    # honored by every server/model/template combination -- see this
    # module's own docstring) and contains a SCRATCH DRAFT of the JSON
    # answer, complete with its own braces. The old greedy
    # `re.search(r"\{.*\}")` spanned from the trace's first "{" to the
    # real answer's last "}", producing unparseable garbage and silently
    # returning {} -- indistinguishable from "the model found no recipe."
    raw = (
        "<think>\nLet me structure this: { \"title\": \"WRONG DRAFT\" } "
        "but I should double check the ingredients first.\n</think>\n"
        '{"title": "Pumpkin Chiffon Pie", "ingredients": []}'
    )
    result = extract_json_object(raw)
    assert result["title"] == "Pumpkin Chiffon Pie"


def test_extract_json_object_survives_trailing_commentary_containing_braces():
    raw = (
        '{"title": "Pumpkin Chiffon Pie"}\n\n'
        'Note: I skipped the ad content, e.g. { "type": "advertisement" }.'
    )
    result = extract_json_object(raw)
    assert result["title"] == "Pumpkin Chiffon Pie"


def test_extract_json_object_survives_a_lead_in_containing_braces():
    raw = 'Here is the recipe (schema: {"title": string}):\n{"title": "Pumpkin Chiffon Pie"}'
    result = extract_json_object(raw)
    assert result["title"] == "Pumpkin Chiffon Pie"


def test_extract_json_object_is_not_confused_by_braces_inside_string_values():
    raw = '{"title": "Pie", "description": "a { brace } inside a string"}'
    result = extract_json_object(raw)
    assert result["title"] == "Pie"
    assert result["description"] == "a { brace } inside a string"


def test_extract_json_object_salvages_a_response_truncated_mid_generation():
    # What a generation that hits the context/num_predict limit looks
    # like (done_reason "length"): the object never closes. Everything
    # before the cut -- here, title/description/a short ingredients array
    # -- is complete and usable; only a long "instructions" array got cut
    # off mid-string. Salvage should recover title/description/
    # ingredients rather than the whole import failing.
    raw = (
        '{"title": "Pumpkin Chiffon Pie", "description": "A chiffon pie", '
        '"ingredients": [{"ingredient_name": "sugar", "quantity": 2, "unit": "Tbsp."}], '
        '"instructions": ["Preheat oven to 325", "Pulse graham crackers unt'
    )
    result = extract_json_object(raw)
    assert result["title"] == "Pumpkin Chiffon Pie"
    assert result["description"] == "A chiffon pie"
    assert result["ingredients"] == [{"ingredient_name": "sugar", "quantity": 2, "unit": "Tbsp."}]
    # The cut-off field itself was truncated mid-string and dropped by the
    # salvage cut point -- never a corrupted/partial value.
    assert "instructions" not in result


def test_extract_json_object_finds_a_second_object_when_the_first_is_a_false_start():
    raw = 'Example: {} is what an empty response looks like.\n{"title": "Pumpkin Chiffon Pie"}'
    result = extract_json_object(raw)
    assert result["title"] == "Pumpkin Chiffon Pie"


def test_extract_json_object_returns_empty_dict_for_prose_only_and_empty_responses():
    assert extract_json_object("I could not find a recipe in that content.") == {}
    assert extract_json_object("") == {}
    assert extract_json_object("   ") == {}
    assert extract_json_object(None) == {}


def test_extract_json_object_honors_a_strict_well_formed_response():
    raw = '{"title": "Pumpkin Chiffon Pie", "ingredients": []}'
    result = extract_json_object(raw)
    assert result == {"title": "Pumpkin Chiffon Pie", "ingredients": []}


def test_extract_json_object_handles_markdown_fenced_json():
    raw = '```json\n{"title": "Pumpkin Chiffon Pie"}\n```'
    result = extract_json_object(raw)
    assert result["title"] == "Pumpkin Chiffon Pie"


# --- extract_json_array: regression coverage for the pre-existing fix ----
#
# ai_json_extraction.extract_json_array is a straight move (not a
# behavior change) of inventory_service's own already-fixed
# _extract_json_array -- test_inventory_import.py already exercises this
# thoroughly end-to-end through inventory_service.parse_vision_response.
# These few tests check the moved function directly, at its new home.


def test_extract_json_array_still_ignores_an_inline_thinking_trace():
    raw = (
        "<think>\nThe receipt has lines [1-15]. A first draft would be "
        '[{"name": "Wrong draft item"}] but let me re-check.\n</think>\n'
        '[{"name": "Eggs", "category": "fridge"}]'
    )
    items = extract_json_array(raw)
    assert [i["name"] for i in items] == ["Eggs"]


def test_extract_json_array_still_honors_a_genuinely_empty_array():
    assert extract_json_array("[]") == []


def test_extract_json_array_returns_nothing_for_prose_only_and_empty_responses():
    assert extract_json_array("I could not find any food items on this receipt.") == []
    assert extract_json_array("") == []
    assert extract_json_array(None) == []
