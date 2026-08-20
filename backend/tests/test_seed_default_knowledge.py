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
    assert len(filenames) == 6
    assert "fda_major_food_allergens.md" in filenames
    assert "dietary_guidelines_2025_2030.md" in filenames
    assert "mediterranean_eating_pattern.md" in filenames  # B18.2, 2026-08-20


# --- B18.2: what the bundle is allowed to contain -------------------------


def test_every_bundled_file_has_a_hand_written_description(db_session, monkeypatch, tmp_path):
    """The seeding function falls back to a generic description for any
    filename it does not recognise. That fallback is a safety net, not a
    licence to add a file without saying where it came from -- the
    description is what a household reads in the Knowledge files list
    when deciding whether to switch a reference on."""
    from app.seed import DEFAULT_KNOWLEDGE_FILE_DESCRIPTIONS

    missing = [f for f in _bundled_filenames() if f not in DEFAULT_KNOWLEDGE_FILE_DESCRIPTIONS]
    assert not missing, f"bundled without a description: {missing}"


def test_no_bundled_file_states_a_lab_threshold_as_advice():
    """PROJECT-PLAN B18.2's own constraint, enforced rather than trusted:
    these are grounding documents for a MEAL PLANNER, and the framing has
    to stay dietary. A bundled file carrying "LDL-C <55 mg/dL" would be
    retrieved and quoted by the chat surface as though this app were
    qualified to say it.

    Matches a number immediately next to a clinical lipid/glucose unit,
    which is the shape a treatment target takes. Nutrition figures are
    unaffected: sodium is mg, not mg/dL, and a per-100g nutrient is not
    expressed in mmol/L.
    """
    import re

    pattern = re.compile(r"\d[\d.,]*\s*(mg/dL|mmol/L|nmol/L)", re.IGNORECASE)
    offenders = []
    for filename in _bundled_filenames():
        with open(os.path.join(DEFAULT_KNOWLEDGE_FILES_DIR, filename), encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if pattern.search(line):
                    offenders.append(f"{filename}:{number}: {line.strip()[:90]}")

    assert not offenders, "bundled reference states a clinical threshold:\n" + "\n".join(offenders)


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
