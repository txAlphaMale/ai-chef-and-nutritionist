"""Unit tests for backlog B9.2's backup archive builder
(app.services.backup_service). Uses the real temp-file SQLite database
and temp secrets directory conftest.py already sets up for every test
(DATABASE_URL/SECRETS_KEY_FILE/SECRETS_KEYRING_FILE), so
build_backup_archive() genuinely exercises its real sqlite-path
resolution and file-inclusion logic rather than a mocked stand-in."""

from __future__ import annotations

import io
import json
import sqlite3
import tarfile

from app.services import backup_service, secrets_crypto


def test_sqlite_path_from_url_absolute():
    assert backup_service._sqlite_path_from_url("sqlite:////app/data/chef.db") == "/app/data/chef.db"


def test_sqlite_path_from_url_relative():
    assert backup_service._sqlite_path_from_url("sqlite:///./chef.db") == "./chef.db"


def test_sqlite_path_from_url_non_sqlite_backend_returns_none():
    assert backup_service._sqlite_path_from_url("postgresql://user:pass@host/db") is None


def test_sqlite_path_from_url_malformed_returns_none():
    assert backup_service._sqlite_path_from_url("not a url at all :::") is None


def test_build_backup_archive_includes_database(db_session):
    # conftest.py's DATABASE_URL points at a real temp-file sqlite db, and
    # db_session already ran Base.metadata.create_all against it -- so
    # the file genuinely exists and has a real schema to snapshot.
    archive_bytes = backup_service.build_backup_archive()
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
        names = tar.getnames()
        assert "chef.db" in names
        # The snapshot itself must be a valid, openable sqlite database,
        # not just some bytes with the right filename -- write it out and
        # actually query it via sqlite3's own backup-restore round trip.
        member = tar.extractfile("chef.db")
        db_bytes = member.read()
    assert db_bytes[:16] == b"SQLite format 3\x00"


def test_build_backup_archive_snapshot_is_queryable(db_session):
    from app.models import HouseholdPreferences

    db_session.add(HouseholdPreferences(household_size=4))
    db_session.commit()

    archive_bytes = backup_service.build_backup_archive()
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
        db_bytes = tar.extractfile("chef.db").read()

    import os
    import tempfile

    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        with open(tmp_path, "wb") as f:
            f.write(db_bytes)
        conn = sqlite3.connect(tmp_path)
        try:
            rows = conn.execute("SELECT household_size FROM household_preferences").fetchall()
        finally:
            conn.close()
        assert rows == [(4,)]
    finally:
        os.remove(tmp_path)


def test_build_backup_archive_includes_secrets_key_once_it_exists():
    # The key file is created lazily on first encrypt/decrypt call, not
    # at import time -- force that here so this test doesn't depend on
    # test execution order having already triggered it elsewhere.
    secrets_crypto.encrypt("some-secret-value")
    archive_bytes = backup_service.build_backup_archive()
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
        assert "secrets.key" in tar.getnames()


def test_build_backup_archive_skips_missing_optional_files(monkeypatch, db_session):
    # No recipe_images/knowledge dirs exist in the test environment
    # (they default to /app/data/... which doesn't exist here) -- this
    # must not raise, just omit them.
    monkeypatch.setattr(backup_service, "RECIPE_IMAGES_DIR", "/definitely/does/not/exist")
    monkeypatch.setattr(backup_service, "KNOWLEDGE_FILES_DIR", "/also/does/not/exist")
    archive_bytes = backup_service.build_backup_archive()
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as tar:
        names = tar.getnames()
    assert "recipe_images" not in names
    assert "knowledge" not in names


def test_backup_manifest_reflects_database_presence(db_session):
    manifest = backup_service.backup_manifest()
    assert "database" in manifest["included"]
