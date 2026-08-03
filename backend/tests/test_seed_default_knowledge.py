"""Tests for the B2.1 bundled default-knowledge-file seeding
(app.seed.seed_default_knowledge_files): every .md file under
app/data/default_knowledge/ should land as an inactive KnowledgeFile row
on a fresh database, with its content actually extracted and its bytes
copied into the live knowledge_service storage dir -- and re-running
seeding against an already-seeded DB should add nothing a second time.

Redirects knowledge_service.KNOWLEDGE_FILES_DIR to a pytest tmp_path
rather than the real /app/data/knowledge default, the same isolation
principle conftest.py already applies to DATABASE_URL/secrets paths --
this is a module-level constant read at import time, so it's patched
directly via monkeypatch.setattr rather than an environment variable
(which knowledge_service already read once, before this test ever runs).
"""

from __future__ import annotations

import os

from app.models import KnowledgeFile
from app.seed import DEFAULT_KNOWLEDGE_FILES_DIR, seed_default_knowledge_files
from app.services import knowledge_service


def _bundled_filenames() -> list[str]:
    return sorted(f for f in os.listdir(DEFAULT_KNOWLEDGE_FILES_DIR) if f.endswith(".md"))


def test_bundled_files_exist_on_disk():
    # Sanity check the fixture directory itself before trusting anything
    # the seeding function reports about it.
    filenames = _bundled_filenames()
    assert len(filenames) == 5
    assert "fda_major_food_allergens.md" in filenames
    assert "dietary_guidelines_2025_2030.md" in filenames


def test_seed_creates_inactive_rows_for_every_bundled_file(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(knowledge_service, "KNOWLEDGE_FILES_DIR", str(tmp_path))

    added = seed_default_knowledge_files(db_session)

    expected = _bundled_filenames()
    assert sorted(added) == expected

    rows = db_session.query(KnowledgeFile).order_by(KnowledgeFile.filename).all()
    assert [r.filename for r in rows] == expected
    for row in rows:
        assert row.is_active is False, f"{row.filename} should be inactive by default"
        assert row.content, f"{row.filename} should have extracted text content"
        assert row.content_type == "text/markdown"
        assert row.description  # every bundled file has a real description, not blank
        assert os.path.isfile(row.storage_path), "storage_path should point at a real copied file"
        assert row.storage_path.startswith(str(tmp_path)), (
            "should be copied into the redirected knowledge storage dir, not read in place"
        )


def test_seed_is_idempotent(db_session, monkeypatch, tmp_path):
    monkeypatch.setattr(knowledge_service, "KNOWLEDGE_FILES_DIR", str(tmp_path))

    first = seed_default_knowledge_files(db_session)
    assert len(first) == len(_bundled_filenames())

    second = seed_default_knowledge_files(db_session)
    assert second == []

    count = db_session.query(KnowledgeFile).count()
    assert count == len(_bundled_filenames())


def test_seed_does_not_duplicate_a_same_named_user_upload(db_session, monkeypatch, tmp_path):
    """If a household already has a KnowledgeFile with the same filename
    as a bundled default (e.g. they uploaded their own
    dash_eating_pattern.md before ever running this seed step), seeding
    should not create a second, colliding row -- same match-on-filename
    behavior as the normal re-seed case, just via a different route to
    an existing row."""
    monkeypatch.setattr(knowledge_service, "KNOWLEDGE_FILES_DIR", str(tmp_path))

    db_session.add(
        KnowledgeFile(
            filename="dash_eating_pattern.md",
            storage_path=str(tmp_path / "user-uploaded-copy.md"),
            content_type="text/markdown",
            content="the household's own version",
            is_active=True,
        )
    )
    db_session.commit()

    added = seed_default_knowledge_files(db_session)

    assert "dash_eating_pattern.md" not in added
    rows = db_session.query(KnowledgeFile).filter_by(filename="dash_eating_pattern.md").all()
    assert len(rows) == 1
    assert rows[0].content == "the household's own version"
    assert rows[0].is_active is True  # the user's own row is untouched
