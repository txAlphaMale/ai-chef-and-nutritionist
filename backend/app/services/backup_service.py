"""Backlog B9.2: one-click backup of everything Chef stores, as a single
downloadable archive -- the DB-plus-files half of "Add a one-click
backup (DB + files archive) and a recipe export in a portable format"
(the recipe-export half lives in recipe_service.recipe_to_jsonld /
routers/recipes.py's export endpoints).

**Scope, stated plainly**: this module builds a downloadable backup. It
deliberately does NOT implement an in-app restore/upload path -- restore
means overwriting a live database and secret-key files out from under a
running app, which is real, destructive, and easy to get wrong (partial
writes, restoring into a schema from a different app version, etc.). The
safer and still fully functional restore path for a self-hosted single-
household app is: stop the container, replace the files in the Docker
data volume with the ones from an extracted backup archive, restart --
documented in the in-app WIKI rather than built as an untested one-click
button here. This mirrors this project's general risk posture: build
that a user can affirmatively cause a lot of the same code paths every
other feature already exercises (opening a container's data volume) is
fine to defer to a strongly-reviewed later pass.

**What's included** -- everything under this app's data directory that
constitutes real, non-regenerable state:
  - the SQLite database itself, via sqlite3's own online backup API
    (Connection.backup()) rather than a raw file copy. This matters:
    copying the raw .db file bytes while the app is live risks capturing
    a torn/inconsistent snapshot (a write straddling the copy), whereas
    the sqlite3 backup API is SQLite's own supported mechanism for
    producing a consistent snapshot of a database that may have
    concurrent readers/writers.
  - secrets.key / secrets_keyring.json (secrets_crypto.py) -- required
    to decrypt every encrypted setting (Tavily/USDA/Google OAuth client
    secret/refresh token, etc.) stored in the database. Without these,
    an otherwise-intact database backup would have every secret setting
    permanently unreadable on restore -- the exact failure mode the
    WIKI's "Settings & secrets" entry already warns about for the key
    file alone. **Important, and stated to the user in both the WIKI and
    the Settings UI**: because this archive contains BOTH the encrypted
    secrets AND the key that decrypts them, the archive itself is
    exactly as sensitive as having those secrets in plain text, and
    should be stored/transmitted with the same care as a password
    export.
  - session_secret.key -- signs the optional B10.2 auth session cookie;
    losing it just invalidates existing sessions (a re-login), not a
    security-relevant loss on its own, but included for completeness
    since it lives in the same data directory.
  - the recipe_images/ and knowledge/ directories -- uploaded recipe
    photos and knowledge-base files, neither of which round-trips
    through the database (only their paths do).

Deliberately hand-built with tarfile/sqlite3 (both stdlib) rather than a
new backup-library dependency, consistent with this project's existing
preference for a small, well-understood implementation over a heavier
dependency where the surface needed is this contained (see
calendar_export_service.py's own .ics builder for the same reasoning).
"""

from __future__ import annotations

import io
import os
import sqlite3
import tarfile
import tempfile
import time

from sqlalchemy.engine import make_url

from app.config import settings
from app.services import secrets_crypto
from app.services.knowledge_service import KNOWLEDGE_FILES_DIR
from app.services.recipe_image_service import RECIPE_IMAGES_DIR


def _sqlite_path_from_url(database_url: str) -> str | None:
    """Extracts the on-disk file path from a SQLAlchemy sqlite:// URL --
    using SQLAlchemy's own URL parser rather than hand-splitting on
    slashes, since the exact slash count in a sqlite URL is meaningful
    (three = relative path, four = absolute) and easy to get subtly
    wrong by hand. Returns None for a non-sqlite backend (nothing to
    snapshot the same way -- out of scope, this app only ships sqlite)
    or an in-memory database (no file to back up)."""
    try:
        url = make_url(database_url)
    except Exception:
        return None
    if url.get_backend_name() != "sqlite" or not url.database:
        return None
    return url.database


def _snapshot_sqlite_to_bytes(db_path: str) -> bytes:
    """A consistent point-in-time copy of the live database via
    sqlite3's own backup API (not a raw file read), then returned as
    bytes for the caller to fold into the archive."""
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        src = sqlite3.connect(db_path)
        try:
            dest = sqlite3.connect(tmp_path)
            try:
                src.backup(dest)
            finally:
                dest.close()
        finally:
            src.close()
        with open(tmp_path, "rb") as f:
            return f.read()
    finally:
        os.remove(tmp_path)


def _add_bytes(tar: tarfile.TarFile, arcname: str, data: bytes) -> None:
    info = tarfile.TarInfo(name=arcname)
    info.size = len(data)
    info.mtime = int(time.time())
    tar.addfile(info, io.BytesIO(data))


def build_backup_archive() -> bytes:
    """Builds the full backup as an in-memory .tar.gz and returns its
    bytes. Every optional piece (secrets files, session key, the two
    data directories) is included only if it actually exists on disk --
    a fresh install with nothing uploaded yet still produces a valid,
    small archive rather than erroring on a missing directory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        db_path = _sqlite_path_from_url(settings.database_url)
        if db_path and os.path.exists(db_path):
            _add_bytes(tar, "chef.db", _snapshot_sqlite_to_bytes(db_path))

        for file_path, arcname in (
            (secrets_crypto.SECRETS_KEY_FILE, "secrets.key"),
            (secrets_crypto.SECRETS_KEYRING_FILE, "secrets_keyring.json"),
            (os.environ.get("SESSION_SECRET_FILE", "/app/data/session_secret.key"), "session_secret.key"),
        ):
            if file_path and os.path.exists(file_path):
                tar.add(file_path, arcname=arcname)

        for dir_path, arcname in (
            (RECIPE_IMAGES_DIR, "recipe_images"),
            (KNOWLEDGE_FILES_DIR, "knowledge"),
        ):
            if dir_path and os.path.isdir(dir_path):
                tar.add(dir_path, arcname=arcname)

    return buf.getvalue()


def backup_manifest() -> dict:
    """A lightweight, display-only summary of what a backup would
    currently contain -- lets the Settings UI show "3.2 MB, includes
    database, secrets, 14 recipe images" before the user commits to a
    download, without actually building the archive twice."""
    db_path = _sqlite_path_from_url(settings.database_url)
    included = []
    if db_path and os.path.exists(db_path):
        included.append("database")
    if os.path.exists(secrets_crypto.SECRETS_KEY_FILE):
        included.append("encrypted secrets")
    if os.path.isdir(RECIPE_IMAGES_DIR) and os.listdir(RECIPE_IMAGES_DIR):
        included.append("recipe images")
    if os.path.isdir(KNOWLEDGE_FILES_DIR) and os.listdir(KNOWLEDGE_FILES_DIR):
        included.append("knowledge files")
    return {"included": included}
