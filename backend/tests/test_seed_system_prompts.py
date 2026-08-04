"""The extraction prompts (recipe import, recipe-chat edit proposals,
receipt/list import, pantry/fridge photo intake) are NOT seeded.

A SystemPrompt row overrides the shipped default for good, so a seeded
copy of a default made an untouched install indistinguishable from an
edited one: every improved default shipped after an install's first boot
was dead on arrival there, and a prompt change could be measured against
a live model as "no better" when it had never executed. These tests pin
the replacement contract -- no row means "use what this build ships", a
row exists only because someone saved an edit, and an install seeded by
an older build repairs itself on the next boot.

main_chef and dietary_onboarding are still seeded and must stay seeded:
they have no code-level fallback, so their row is the only copy of the
value.

Mirrors test_seed_default_knowledge.py's knowledge_service.
KNOWLEDGE_FILES_DIR monkeypatch (seed.seed() also seeds default knowledge
files, which write to a real filesystem path that doesn't exist -- and
shouldn't be created -- outside a real container)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.models import SystemPrompt
from app.prompt_defaults import IMPORT_PROMPT_DEFAULTS, is_shipped_default, prune_unedited_prompt_rows
from app.seed import seed
from app.services import knowledge_service

_EXTRACTION_KEYS = sorted(IMPORT_PROMPT_DEFAULTS)
SHIPPED_PROMPTS_DIR = Path(__file__).parent / "fixtures" / "shipped_prompts"


@pytest.fixture
def seeded(monkeypatch, tmp_path):
    monkeypatch.setattr(knowledge_service, "KNOWLEDGE_FILES_DIR", str(tmp_path))
    return seed


def test_seed_creates_no_extraction_prompt_rows(db_session, seeded):
    seeded()

    for prompt_key in _EXTRACTION_KEYS:
        assert db_session.query(SystemPrompt).filter_by(prompt_key=prompt_key).first() is None, (
            f"{prompt_key} must not be seeded -- a row means a household edit"
        )


def test_seed_still_creates_the_persona_prompts(db_session, seeded):
    # These two have no constant to fall back to, so an absent row means
    # the chef runs with an empty system prompt.
    seeded()

    for prompt_key in ("main_chef", "dietary_onboarding"):
        row = db_session.query(SystemPrompt).filter_by(prompt_key=prompt_key).first()
        assert row is not None
        assert row.is_active is True
        assert row.content.strip()


def test_seed_prunes_a_row_left_by_an_older_build(db_session, seeded):
    # The repair path. An older build wrote a copy of the shipped default
    # into the table; content equal to a shipped text is not an edit, so
    # the row goes and this install lands where a fresh one starts.
    for prompt_key, default_content in IMPORT_PROMPT_DEFAULTS.items():
        db_session.add(SystemPrompt(prompt_key=prompt_key, content=default_content, is_active=True))
    db_session.commit()

    seeded()

    for prompt_key in _EXTRACTION_KEYS:
        assert db_session.query(SystemPrompt).filter_by(prompt_key=prompt_key).first() is None


@pytest.mark.parametrize(
    ("prompt_key", "fixture_name"),
    [
        ("recipe_import", "recipe_import__1660aa3.txt"),
        ("receipt_import", "receipt_import__1fd5b77.txt"),
        ("vision_intake", "vision_intake__1fd5b77.txt"),
    ],
)
def test_seed_prunes_a_row_holding_a_previously_shipped_default(db_session, seeded, prompt_key, fixture_name):
    # Seeding existed for one commit window, so a row can hold a default
    # this build no longer ships. That is still not an edit.
    #
    # Driven off byte-exact copies of the real historical texts rather
    # than off the digest constants, so a wrong digest fails here instead
    # of quietly never matching anything. See fixtures/shipped_prompts.
    old_text = (SHIPPED_PROMPTS_DIR / fixture_name).read_text(encoding="utf-8")
    assert old_text != IMPORT_PROMPT_DEFAULTS[prompt_key], "fixture should be a superseded text, not the current one"
    db_session.add(SystemPrompt(prompt_key=prompt_key, content=old_text, is_active=True))
    db_session.commit()

    seeded()

    assert db_session.query(SystemPrompt).filter_by(prompt_key=prompt_key).first() is None


def test_a_household_edit_is_not_mistaken_for_a_shipped_default():
    assert is_shipped_default("recipe_import", IMPORT_PROMPT_DEFAULTS["recipe_import"])
    assert not is_shipped_default("recipe_import", "something a household typed")
    # One character off is an edit. A household that changed one word
    # meant to change one word.
    assert not is_shipped_default("recipe_import", IMPORT_PROMPT_DEFAULTS["recipe_import"] + " ")
    # main_chef has no shipped default to compare against, so nothing is
    # ever pruned for it.
    assert not is_shipped_default("main_chef", "anything at all")


def test_seed_never_touches_a_real_household_edit(db_session, seeded):
    db_session.add(SystemPrompt(prompt_key="recipe_import", content="MY CUSTOM PROMPT", is_active=True))
    db_session.commit()

    seeded()
    seeded()

    row = db_session.query(SystemPrompt).filter_by(prompt_key="recipe_import").first()
    assert row is not None
    assert row.content == "MY CUSTOM PROMPT"


def test_seed_never_touches_an_inactive_household_draft(db_session, seeded):
    # Unchecking "Active" parks a draft without running it. Pruning must
    # not read that as "unused, therefore disposable".
    db_session.add(SystemPrompt(prompt_key="vision_intake", content="A PARKED DRAFT", is_active=False))
    db_session.commit()

    seeded()

    row = db_session.query(SystemPrompt).filter_by(prompt_key="vision_intake").first()
    assert row is not None
    assert row.content == "A PARKED DRAFT"
    assert row.is_active is False


def test_seed_is_idempotent(db_session, seeded):
    seeded()
    seeded()

    assert db_session.query(SystemPrompt).filter_by(prompt_key="main_chef").count() == 1
    for prompt_key in _EXTRACTION_KEYS:
        assert db_session.query(SystemPrompt).filter_by(prompt_key=prompt_key).count() == 0


def test_prune_reports_what_it_removed(db_session):
    db_session.add(SystemPrompt(prompt_key="recipe_modify", content=IMPORT_PROMPT_DEFAULTS["recipe_modify"]))
    db_session.add(SystemPrompt(prompt_key="vision_intake", content="AN EDIT"))
    db_session.commit()

    assert prune_unedited_prompt_rows(db_session) == ["recipe_modify"]


def test_every_extraction_default_is_non_blank():
    # A blank default would make "no row" mean "no instructions at all",
    # which fails silently rather than loudly.
    for prompt_key, default_content in IMPORT_PROMPT_DEFAULTS.items():
        assert default_content.strip(), f"{prompt_key} has no shipped text"
