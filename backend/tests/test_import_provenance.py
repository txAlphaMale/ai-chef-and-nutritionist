"""The review screen has to say whether the ingredients were verified.

The incident this exists for, 2026-08-06: a capture of THIS APP'S OWN
REVIEW FORM was imported as if it were a recipe. Pass 1 copied its rows --
`graham crackers 12 unit prep note X`, input placeholders and delete
button and all -- and every one of them failed verification, correctly,
because a form is not an ingredient list. Two-pass declined. The single
call's unverified guesses were then shown in a review screen identical to
a verified one, and the only trace of the refusal was a print line in the
container log.

The copied lines below are the real ones, from that log. They are checked
in because "would this recipe have been flagged" is a question the app
must be able to answer about a real document, not a synthetic one.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app.services import recipe_service

FIXTURES = Path(__file__).parent / "fixtures"
PIE = FIXTURES / "pumpkin_chiffon_pie_pypdf.txt"

KEY = recipe_service.INGREDIENT_PROVENANCE_KEY

# Verbatim from `docker compose logs chef`, 2026-08-06. The trailing glyph
# is the review form's delete button; `prep note` and `unit` are its input
# placeholders. Nothing here is invented.
FORM_ROWS_COPIED_FROM_A_REVIEW_SCREEN = [
    "graham crackers 12 unit prep note \u2715",
    "sugar 2 tbsp prep note \u2715",
    "kosher salt 0.25 tsp prep note \u2715",
    "unsalted butter 6 tbsp melted, slightly cooled \u2715",
]

SINGLE_CALL_OUTPUT = json.dumps(
    {
        "title": "Pumpkin Chiffon Pie",
        "default_servings": 8,
        "instructions": [{"component": "main", "text": "Bake it."}],
        "ingredients": [
            {"ingredient_name": "graham crackers", "quantity": None, "unit": None, "component": "Crust"},
            {"ingredient_name": "sugar", "quantity": 2, "unit": "Tbsp.", "component": "Crust"},
        ],
    }
)


@contextmanager
def _stub_pass1(lines_by_component, done_reason="stop"):
    """Pass 1 stubbed at the model boundary. Mirrors the helper in
    test_welded_sources.py deliberately rather than importing it: these
    two files assert about different things and should not be able to
    break each other."""
    raw = json.dumps({"blocks": [{"component": c, "lines": ls} for c, ls in lines_by_component]})
    with (
        patch.object(recipe_service, "get_ingredient_lines_prompt", return_value="{content}"),
        patch.object(recipe_service.ollama_client, "content_char_budget", return_value=100_000),
        patch.object(recipe_service.ollama_client, "get_extraction_model", return_value="stub"),
        patch.object(recipe_service.ollama_client, "chat_json_with_reason", return_value=(raw, done_reason)),
    ):
        yield


class _AnyDb:
    """finish_recipe_parse only checks `db is not None` before handing it
    to a two-pass path that is fully stubbed here."""


def _finish(source_text, db=None):
    return recipe_service.finish_recipe_parse(
        SINGLE_CALL_OUTPUT, "import_file", {}, None, None, db=db, source_text=source_text
    )


def test_a_review_form_imported_as_a_recipe_is_reported_unverified():
    """The incident itself. Every copied line fails, the block is dropped,
    and the preview must not present the result as checked."""
    source = PIE.read_text(encoding="utf-8")
    with _stub_pass1([("Crust", FORM_ROWS_COPIED_FROM_A_REVIEW_SCREEN)]):
        parsed = _finish(source, db=_AnyDb())
    assert parsed[KEY]["path"] == "single_call"
    assert parsed[KEY]["reason"] == "nothing_verified"
    # The single call's list still stands -- this is a disclosure, not a
    # new refusal. Changing what imports is a separate decision.
    assert len(parsed["ingredients"]) == 2


def test_a_verified_block_is_reported_verified_with_its_count():
    source = PIE.read_text(encoding="utf-8")
    with _stub_pass1([("Crust", ["12 graham crackers", "\u00bc tsp. kosher salt"])]):
        parsed = _finish(source, db=_AnyDb())
    assert parsed[KEY]["path"] == "two_pass"
    assert parsed[KEY]["verified"] == 2
    assert parsed[KEY]["reason"] is None
    assert [i["quantity"] for i in parsed["ingredients"]] == [12.0, 0.25]


def test_a_block_too_thin_to_replace_the_single_call_says_so_with_both_counts():
    """The other half of the gate: something verified, just not enough.
    The counts are the judgement, so the household sees the judgement."""
    single_call = json.loads(SINGLE_CALL_OUTPUT)
    single_call["ingredients"] = single_call["ingredients"] * 5  # 10 rows
    source = PIE.read_text(encoding="utf-8")
    with _stub_pass1([("Crust", ["12 graham crackers"])]):
        parsed = recipe_service.finish_recipe_parse(
            json.dumps(single_call), "import_file", {}, None, None, db=_AnyDb(), source_text=source
        )
    assert parsed[KEY]["path"] == "single_call"
    assert parsed[KEY]["reason"] == "fewer_than_single_call"
    assert parsed[KEY]["verified"] == 1
    assert parsed[KEY]["single_call"] == 10


def test_a_photo_import_is_unverified_for_a_reason_that_is_not_a_failure():
    """No text layer, so there is nothing a copied line could be checked
    against. Still unverified, and the wording differs."""
    parsed = _finish(None, db=_AnyDb())
    assert parsed[KEY]["path"] == "single_call"
    assert parsed[KEY]["reason"] == "no_source_text"


def test_structured_page_data_is_its_own_path_not_a_failed_verification():
    parsed = recipe_service.finish_recipe_parse(
        "(from schema.org)",
        "import_url_jsonld",
        {},
        None,
        {"title": "Pie", "ingredients": [], "instructions": []},
        db=_AnyDb(),
        source_text=None,
    )
    assert parsed[KEY]["path"] == "jsonld"


def test_provenance_never_reaches_a_saved_recipe():
    """The key is `_`-prefixed so RecipeCreate drops it. If that ever
    stops being true, an import would try to write a column that does not
    exist."""
    from app.schemas.recipe import RecipeCreate

    parsed = _finish(None, db=_AnyDb())
    assert KEY not in RecipeCreate(**parsed).model_dump()
