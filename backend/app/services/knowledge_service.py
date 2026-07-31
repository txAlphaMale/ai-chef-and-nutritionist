"""Storage and text extraction for imported nutritionist knowledge files
(PDF/txt/md) -- lets the household ground the AI with reference material
(e.g. a specific diet plan, a doctor's guidance sheet) that meal-plan
generation (and, eventually, Phase 7 chat) can draw on. See
health_service.build_knowledge_context() for how the extracted text is
actually used.

Files are stored on disk (same Docker volume as the SQLite DB, same
pattern as secrets_crypto.py's key file) under a UUID-based name to
avoid filename collisions/traversal from user-supplied names; the
original filename is kept in the DB row for display.
"""
from __future__ import annotations

import os
import uuid

from app.services.recipe_service import extract_pdf_text

KNOWLEDGE_FILES_DIR = os.environ.get("KNOWLEDGE_FILES_DIR", "/app/data/knowledge")

TEXT_EXTENSIONS = (".txt", ".md", ".markdown")


def save_file(filename: str, raw_bytes: bytes) -> str:
    """Writes raw_bytes to a new UUID-named file under KNOWLEDGE_FILES_DIR
    and returns the full storage path. Atomic write (temp file + rename)
    so a crash mid-write can't leave a corrupt file behind."""
    os.makedirs(KNOWLEDGE_FILES_DIR, exist_ok=True)
    ext = os.path.splitext(filename)[1][:20]  # keep the extension, bound its length
    storage_path = os.path.join(KNOWLEDGE_FILES_DIR, f"{uuid.uuid4().hex}{ext}")
    tmp_path = storage_path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(raw_bytes)
    os.replace(tmp_path, storage_path)
    return storage_path


def extract_text(filename: str, content_type: str | None, raw_bytes: bytes) -> str | None:
    """Best-effort plain-text extraction for grounding purposes. Returns
    None (not an error) for a type we can't extract from -- the file is
    still stored and viewable/downloadable, it just won't ground the AI."""
    lower_name = filename.lower()
    if (content_type == "application/pdf") or lower_name.endswith(".pdf"):
        try:
            return extract_pdf_text(raw_bytes) or None
        except Exception:  # noqa: BLE001 -- corrupt/unsupported PDF shouldn't block upload
            return None

    if lower_name.endswith(TEXT_EXTENSIONS) or (content_type or "").startswith("text/"):
        try:
            return raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return None

    return None


def delete_file(storage_path: str) -> None:
    try:
        os.remove(storage_path)
    except OSError:
        pass  # already gone, or never existed -- not fatal for a DB-row delete
