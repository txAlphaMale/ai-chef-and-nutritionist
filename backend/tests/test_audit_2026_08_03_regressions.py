"""Regression tests for the 2026-08-03 audit fixes.

Grouped in one file on purpose. Each test below pins a specific defect
that had already been "fixed" once or twice by treating a symptom, and
names the wrong behaviour it replaced -- so a future session can tell at a
glance whether a change is reintroducing something already understood.

See AUDIT-2026-08-03.md for the full findings each of these corresponds to.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import httpx
import pytest

from app.schemas.ai_extraction import (
    ExtractedInventoryList,
    ExtractedMealPlan,
    ExtractedRecipe,
    schema_of,
)
from app.services import (
    ai_json_extraction,
    dining_service,
    inventory_service,
    ollama_client,
    recipe_service,
    unit_conversion_service,
)

# --- P0-1 / P0-5: constrained decoding replaces prompt-only JSON ---------


def test_extraction_schemas_are_flat_and_ref_free():
    """Ollama's grammar builder gets a self-contained schema.

    Pydantic emits `$defs` + `$ref` for nested models. Flattening removes a
    compatibility variable across llama.cpp versions; a `$ref` surviving
    here means `schema_of` regressed and constrained decoding may silently
    fall back to unconstrained on some servers."""
    for model in (ExtractedRecipe, ExtractedInventoryList, ExtractedMealPlan):
        rendered = json.dumps(schema_of(model))
        assert "$ref" not in rendered, f"{model.__name__} schema still contains a $ref"
        assert "$defs" not in rendered, f"{model.__name__} schema still contains $defs"


def test_recipe_schema_requires_a_title():
    # `finish_recipe_parse` treats a missing title as "no recipe found",
    # so the schema has to make the model produce one rather than leaving
    # it to the prompt to ask nicely.
    assert "title" in schema_of(ExtractedRecipe)["required"]


def test_extraction_sampling_is_deterministic_and_penalty_free():
    """Extraction runs at temperature 0 with no repetition penalties.

    Replaces temperature 0.7 / top_p 0.8 / top_k 20 / presence_penalty 1.5
    -- Qwen's published GENERAL CHAT sampling applied to a deterministic
    extraction job. The presence penalty penalises tokens that have already
    appeared, and an extraction response repeats the same keys, units and
    categories on every element, so it pushed the model to stop emitting
    the structure partway down a long list. That is the "captured 4 of 8
    receipt items" symptom, caused by the parameter added to fix it."""
    assert ollama_client.EXTRACTION_OPTIONS["temperature"] == 0.0
    assert "presence_penalty" not in ollama_client.EXTRACTION_OPTIONS
    assert "frequency_penalty" not in ollama_client.EXTRACTION_OPTIONS
    assert "repeat_penalty" not in ollama_client.EXTRACTION_OPTIONS


def test_extract_json_array_unwraps_the_items_object():
    """List-shaped extractions are constrained to `{"items": [...]}`.

    Ollama's `format` takes an object schema -- a bare top-level array is
    not portably supported -- so every caller that used to receive a plain
    array must keep receiving one."""
    assert ai_json_extraction.extract_json_array('{"items": [{"name": "milk"}]}') == [{"name": "milk"}]
    # Bare arrays still work: an older server falling back to unconstrained
    # output must not break the parser.
    assert ai_json_extraction.extract_json_array('[{"name": "milk"}]') == [{"name": "milk"}]


# --- P0-2: bounded Ollama calls -----------------------------------------


def test_ollama_client_sets_an_explicit_timeout(db_session):
    """ollama-python defaults to no timeout at all, and this app runs every
    AI call on ONE serial worker thread -- so an unbounded call doesn't
    fail one feature, it wedges chat, imports, vision intake and meal
    planning together until the container is restarted."""
    client = ollama_client._client(db_session)
    timeout = client._client.timeout
    assert timeout.read is not None
    assert timeout.connect is not None
    assert timeout.read >= 30


def test_timeout_surfaces_as_an_actionable_error(db_session):
    with patch(
        "app.services.ollama_client.ollama.Client.chat",
        side_effect=httpx.ReadTimeout("timed out"),
    ), pytest.raises(ollama_client.OllamaTimeout) as excinfo:
        ollama_client.chat(db_session, [{"role": "user", "content": "hi"}])
    message = str(excinfo.value)
    assert "timeout" in message.lower()
    assert "Settings" in message  # tells the user what to actually do


def test_num_predict_reserves_response_space(db_session):
    """`num_ctx` covers prompt AND response. Without an explicit
    `num_predict` a long prompt can consume the whole window and the answer
    gets cut off mid-structure (done_reason "length")."""
    with patch("app.services.ollama_client.ollama.Client.chat", return_value={"message": {"content": "{}"}}) as m:
        ollama_client.chat(db_session, [{"role": "user", "content": "hi"}], response_tokens=1234)
    assert m.call_args.kwargs["options"]["num_predict"] == 1234


def test_fit_prompt_trims_the_tail_not_the_head(db_session):
    """Ollama clips an over-length prompt from the FRONT, which is where
    the system prompt and output contract live -- so an over-budget prompt
    degraded into "the model was never told what to do". Trimming the tail
    keeps the instructions."""
    head = "INSTRUCTIONS THAT MUST SURVIVE"
    prompt = head + ("x" * 500_000)
    fitted, truncated = ollama_client.fit_prompt(db_session, prompt, response_tokens=1000)
    assert truncated
    assert fitted.startswith(head)
    assert len(fitted) < len(prompt)


def test_extract_content_falls_back_to_the_thinking_field():
    """A thinking-capable model whose chat template ignores `think=False`
    routes its entire answer into `message.thinking` and leaves `content`
    empty. Returning the thinking text lets the JSON extractors work on it
    instead of the caller seeing nothing at all."""
    assert ollama_client.extract_content({"message": {"content": "", "thinking": '{"title": "x"}'}}) == '{"title": "x"}'
    # Content still wins when both are present.
    assert ollama_client.extract_content({"message": {"content": "real", "thinking": "trace"}}) == "real"


# --- P1-3: T is tablespoon, t is teaspoon --------------------------------


def test_single_letter_unit_abbreviations_are_case_sensitive():
    """`normalize_unit` lowercased before lookup, collapsing "T"
    (tablespoon) into "t" (teaspoon) -- a silent 3x error applied to the
    display toggle, grocery aggregation, deduction and cost math alike."""
    assert unit_conversion_service.normalize_unit("T") == "tbsp"
    assert unit_conversion_service.normalize_unit("t") == "tsp"
    assert unit_conversion_service.normalize_unit("T.") == "tbsp"
    # Multi-letter spellings stay case-insensitive -- these come from AI
    # extraction and third-party imports and are spelled inconsistently.
    for spelling in ("tbsp", "Tbsp", "TBSP", "Tablespoon", "tablespoons"):
        assert unit_conversion_service.normalize_unit(spelling) == "tbsp"


def test_tablespoon_conversion_is_not_three_times_off():
    result = unit_conversion_service.convert(3, "T", "tsp")
    assert result is not None
    assert result.quantity == pytest.approx(9, rel=0.01)


# --- P1-4: refuse rather than guess on incomparable units ----------------


def test_deduction_refuses_across_unconvertible_units(db_session):
    """See test_unit_conversion_wiring for the full pair of cases. Pinned
    here too because this is the one that silently wrote wrong numbers to
    the database: "2 cup flour" against a "5 lb flour" row left 3 lb."""
    from app.models import InventoryItem

    item = InventoryItem(name="flour", quantity=5, unit="lb", category="pantry")
    db_session.add(item)
    db_session.commit()
    outcome = inventory_service.deduct_by_name(db_session, "flour", 2, "cup")
    assert outcome.status == inventory_service.DEDUCT_UNIT_MISMATCH
    assert outcome.item.quantity == 5
    assert outcome.item.last_used_date is not None


# --- P1-8: an image-only PDF fails honestly ------------------------------


def test_recipe_pdf_with_no_text_layer_reports_why(db_session):
    """A scanned PDF has no extractable text, so the empty string used to
    be sent on as the prompt's SOURCE block -- producing "Could not extract
    a recipe from that input", indistinguishable from a parse failure on a
    perfectly readable file."""
    with (
        patch("app.services.recipe_service.extract_pdf_text", return_value="   \n  "),
        pytest.raises(RuntimeError) as excinfo,
    ):
        recipe_service.parse_recipe_file_content(db_session, b"%PDF-1.4", "scan.pdf", "application/pdf")
    message = str(excinfo.value).lower()
    assert "scan" in message or "no extractable text" in message


# --- P1-1 / P1-2: dining results are ranked, and contact data survives ---


def _place(name, distance, diet_tags=None, tags=None):
    raw = {
        "type": "node",
        "id": abs(hash(name)) % 100000,
        "lat": 30.0,
        "lon": -97.0,
        "tags": {"name": name, "amenity": "restaurant", **(diet_tags or {}), **(tags or {})},
    }
    return raw


def test_overpass_parser_keeps_contact_website_hours_and_map_link():
    """These tags were already on the wire -- `out tags center` returns
    every tag -- and the parser simply dropped them, leaving results with
    no way to call ahead, which is the action every caution message on the
    page asks the household to take."""
    data = {
        "elements": [
            _place(
                "Testaurant",
                0,
                tags={
                    "website": "https://example.com",
                    "phone": "+1 512 555 0100",
                    "opening_hours": "Mo-Fr 09:00-17:00",
                },
            )
        ]
    }
    parsed = dining_service.parse_overpass_response(data, 30.0, -97.0)
    assert parsed[0]["website"] == "https://example.com"
    assert parsed[0]["phone"] == "+1 512 555 0100"
    assert parsed[0]["opening_hours"] == "Mo-Fr 09:00-17:00"
    assert parsed[0]["map_url"].startswith("https://www.openstreetmap.org/node/")


def test_overpass_query_includes_relations():
    # A venue mapped as a multipolygon is a relation, and was missing from
    # every search.
    query = dining_service.build_overpass_query(30.0, -97.0, 5000)
    assert "relation[" in query


def test_restriction_ranking_puts_a_tagged_match_above_a_nearer_untagged_one():
    """Results used to be sorted by distance and cut to the nearest 50,
    which in a dense area discarded genuinely gluten-free-tagged venues
    before the household ever saw them -- strictly worse than not
    filtering at all."""
    near_untagged = {"distance_m": 100.0, "per_allergen": {"gluten": "unknown"}}
    far_tagged = {"distance_m": 3000.0, "per_allergen": {"gluten": "only"}}
    ranked = sorted([near_untagged, far_tagged], key=dining_service.restriction_sort_key)
    assert ranked[0] is far_tagged


def test_restriction_ranking_uses_the_worst_verdict_not_the_best():
    # A place that is gluten-free but explicitly not dairy-free is not a
    # match for a household restricting both.
    mixed = {"distance_m": 10.0, "per_allergen": {"gluten": "only", "milk": "no"}}
    plain = {"distance_m": 20.0, "per_allergen": {"gluten": "yes", "milk": "yes"}}
    ranked = sorted([mixed, plain], key=dining_service.restriction_sort_key)
    assert ranked[0] is plain


def test_no_restrictions_falls_back_to_distance_order():
    a = {"distance_m": 500.0, "per_allergen": {}}
    b = {"distance_m": 100.0, "per_allergen": {}}
    assert sorted([a, b], key=dining_service.restriction_sort_key)[0] is b


# --- P2-1: SQLite pragmas ------------------------------------------------


def test_sqlite_enforces_foreign_keys_and_uses_wal(db_session):
    """SQLite ignores foreign keys unless enabled per connection, and
    SQLAlchemy does not enable them -- so every FK in this schema was
    decorative, and a delete could leave orphaned ingredients, plan entries
    and knowledge chunks behind."""
    from sqlalchemy import text

    assert db_session.execute(text("PRAGMA foreign_keys")).scalar() == 1
    # Tests run against a temp file DB; WAL is only available for
    # file-backed databases, so accept memory's own mode there.
    journal_mode = db_session.execute(text("PRAGMA journal_mode")).scalar()
    assert journal_mode.lower() in ("wal", "memory")
