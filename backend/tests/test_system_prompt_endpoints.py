"""The /api/system/prompts contract, which is what makes a household edit
visible as an edit.

A SystemPrompt row overrides the shipped default permanently, so the
endpoint has to report which of the two is in force. It previously
returned rows only, and since an untouched install was seeded with a copy
of every default, an override and a default were the same bytes over the
wire -- nothing could tell which text the model was about to run.

Router functions are called directly with a session, the pattern the rest
of this suite uses (see test_barcode_lookup.py) -- these take a plain
path param and the db dependency, so a TestClient adds nothing."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.models import SystemPrompt
from app.prompt_defaults import IMPORT_PROMPT_DEFAULTS
from app.routers.system import delete_prompt_override, list_prompts, update_prompt
from app.schemas.system import PromptUpdate


def _by_key(listing):
    return {p["prompt_key"]: p for p in listing}


def test_extraction_prompts_are_listed_with_their_default_and_no_override(db_session):
    listing = _by_key(list_prompts(db=db_session))

    for prompt_key, default_content in IMPORT_PROMPT_DEFAULTS.items():
        entry = listing[prompt_key]
        assert entry["has_override"] is False
        assert entry["default_content"] == default_content
        # Empty, not prefilled: the Settings box shows default_content as
        # placeholder, so prefilling would make an untouched install look
        # mid-edit and one stray Save would pin it to today's text.
        assert entry["content"] == ""
        assert entry["is_active"] is False


def test_a_saved_edit_is_reported_as_an_override(db_session):
    db_session.add(SystemPrompt(prompt_key="recipe_import", content="MY PROMPT", is_active=True))
    db_session.commit()

    entry = _by_key(list_prompts(db=db_session))["recipe_import"]
    assert entry["has_override"] is True
    assert entry["is_active"] is True
    assert entry["content"] == "MY PROMPT"
    assert entry["default_content"] == IMPORT_PROMPT_DEFAULTS["recipe_import"]


def test_persona_prompts_report_no_default_to_revert_to(db_session):
    # main_chef has no code-level fallback -- its row IS the value -- so
    # the UI must not offer to delete it.
    db_session.add(SystemPrompt(prompt_key="main_chef", content="PERSONA", is_active=True))
    db_session.commit()

    entry = _by_key(list_prompts(db=db_session))["main_chef"]
    assert entry["default_content"] is None
    assert entry["has_override"] is True


def test_first_save_creates_the_row(db_session):
    result = update_prompt("vision_intake", PromptUpdate(content="LOOK HARDER"), db=db_session)

    assert result["has_override"] is True
    assert result["content"] == "LOOK HARDER"
    assert db_session.query(SystemPrompt).filter_by(prompt_key="vision_intake").count() == 1


def test_saving_the_shipped_text_verbatim_drops_the_override(db_session):
    # Otherwise the table fills back up with copies of defaults and the
    # original ambiguity returns through the front door.
    db_session.add(SystemPrompt(prompt_key="recipe_modify", content="AN EDIT", is_active=True))
    db_session.commit()

    result = update_prompt(
        "recipe_modify",
        PromptUpdate(content=IMPORT_PROMPT_DEFAULTS["recipe_modify"]),
        db=db_session,
    )

    assert result["has_override"] is False
    assert db_session.query(SystemPrompt).filter_by(prompt_key="recipe_modify").count() == 0


def test_saving_the_shipped_text_when_there_is_no_row_creates_nothing(db_session):
    """Found by running the endpoint, not by reading it.

    The first version built the row, added it, then reconsidered and
    deleted it -- which raises "Instance is not persisted" against an
    object that was never flushed. Every unit test happened to seed a row
    first, so all of them passed. The default-into-an-untouched-install
    case is the likeliest one a household hits: open Settings, paste the
    text you see, press Save."""
    result = update_prompt(
        "vision_intake",
        PromptUpdate(content=IMPORT_PROMPT_DEFAULTS["vision_intake"]),
        db=db_session,
    )

    assert result["has_override"] is False
    assert db_session.query(SystemPrompt).filter_by(prompt_key="vision_intake").count() == 0


def test_activating_a_prompt_that_has_no_override_is_a_no_op(db_session):
    # Nothing to activate: the shipped default is already what runs.
    result = update_prompt("recipe_import", PromptUpdate(is_active=True), db=db_session)

    assert result["has_override"] is False
    assert db_session.query(SystemPrompt).filter_by(prompt_key="recipe_import").count() == 0


def test_parking_a_draft_keeps_the_row_but_deactivates_it(db_session):
    update_prompt("receipt_import", PromptUpdate(content="A DRAFT"), db=db_session)

    result = update_prompt("receipt_import", PromptUpdate(is_active=False), db=db_session)

    assert result["has_override"] is True
    assert result["is_active"] is False
    assert result["content"] == "A DRAFT"


def test_delete_reverts_to_the_shipped_default(db_session):
    db_session.add(SystemPrompt(prompt_key="recipe_import", content="AN EDIT", is_active=True))
    db_session.commit()

    result = delete_prompt_override("recipe_import", db=db_session)

    assert result["has_override"] is False
    assert result["default_content"] == IMPORT_PROMPT_DEFAULTS["recipe_import"]
    assert db_session.query(SystemPrompt).filter_by(prompt_key="recipe_import").count() == 0


def test_delete_is_a_no_op_when_there_was_no_override(db_session):
    result = delete_prompt_override("vision_intake", db=db_session)
    assert result["has_override"] is False


def test_a_persona_prompt_cannot_be_deleted(db_session):
    # Deleting main_chef would leave the chef running an empty system
    # prompt, not a default.
    db_session.add(SystemPrompt(prompt_key="main_chef", content="PERSONA", is_active=True))
    db_session.commit()

    with pytest.raises(HTTPException) as excinfo:
        delete_prompt_override("main_chef", db=db_session)

    assert excinfo.value.status_code == 404
    assert db_session.query(SystemPrompt).filter_by(prompt_key="main_chef").count() == 1


def test_an_unknown_key_is_still_rejected(db_session):
    # A key with neither a row nor a shipped default is a typo or a stale
    # frontend build, not a new setting.
    with pytest.raises(HTTPException) as excinfo:
        update_prompt("not_a_prompt", PromptUpdate(content="x"), db=db_session)

    assert excinfo.value.status_code == 404


def test_an_existing_persona_row_is_still_editable(db_session):
    db_session.add(SystemPrompt(prompt_key="dietary_onboarding", content="OLD", is_active=True))
    db_session.commit()

    result = update_prompt("dietary_onboarding", PromptUpdate(content="NEW"), db=db_session)

    assert result["content"] == "NEW"
    assert result["default_content"] is None
