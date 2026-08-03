"""Tests for backlog B16.1 (author-requested 2026-08-03): the AI import/
extraction prompts (recipe import, recipe-chat edit proposals, receipt/
list import, pantry/fridge photo intake) are now seeded as SystemPrompt
rows, exactly like the pre-existing main_chef/dietary_onboarding prompts
-- so a fresh install's Settings page shows the real, editable prompt
text instead of an empty box, and each prompt's own get_*_prompt()
getter (recipe_service.py, routers/inventory.py) has a real default row
to read on first boot.

Mirrors test_seed_default_knowledge.py's own knowledge_service.
KNOWLEDGE_FILES_DIR monkeypatch (seed.seed() also seeds default knowledge
files, which write to a real filesystem path that doesn't exist -- and
shouldn't be created -- outside a real container)."""

from __future__ import annotations

from app.models import SystemPrompt
from app.routers.inventory import RECEIPT_IMPORT_PROMPT, VISION_PROMPT
from app.seed import seed
from app.services import knowledge_service
from app.services.recipe_service import RECIPE_IMPORT_PROMPT, RECIPE_MODIFY_INSTRUCTIONS

_NEW_PROMPT_KEYS = {
    "recipe_import": RECIPE_IMPORT_PROMPT,
    "recipe_modify": RECIPE_MODIFY_INSTRUCTIONS,
    "receipt_import": RECEIPT_IMPORT_PROMPT,
    "vision_intake": VISION_PROMPT,
}


def test_seed_creates_every_import_extraction_prompt_active_with_real_content(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(knowledge_service, "KNOWLEDGE_FILES_DIR", str(tmp_path))

    seed()

    for prompt_key, default_content in _NEW_PROMPT_KEYS.items():
        row = db_session.query(SystemPrompt).filter_by(prompt_key=prompt_key).first()
        assert row is not None, f"{prompt_key} should be seeded"
        assert row.is_active is True
        assert row.content == default_content
        assert row.content.strip(), f"{prompt_key} should have real, non-blank content"


def test_seed_does_not_clobber_a_households_existing_customization(db_session, monkeypatch, tmp_path):
    # Re-running seed() (every container start, per docker-entrypoint.sh)
    # must never overwrite a household's saved edit -- same "only create
    # if missing" discipline every other seeded row in this module
    # already follows.
    monkeypatch.setattr(knowledge_service, "KNOWLEDGE_FILES_DIR", str(tmp_path))
    db_session.add(SystemPrompt(prompt_key="recipe_import", content="MY CUSTOM PROMPT", is_active=True))
    db_session.commit()

    seed()

    row = db_session.query(SystemPrompt).filter_by(prompt_key="recipe_import").first()
    assert row.content == "MY CUSTOM PROMPT"


def test_seed_is_idempotent_for_import_extraction_prompts(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(knowledge_service, "KNOWLEDGE_FILES_DIR", str(tmp_path))

    seed()
    seed()

    for prompt_key in _NEW_PROMPT_KEYS:
        count = db_session.query(SystemPrompt).filter_by(prompt_key=prompt_key).count()
        assert count == 1
