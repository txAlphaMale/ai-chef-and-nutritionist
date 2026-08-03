"""Storage for recipe dish photos -- manually uploaded via the recipe
form, or auto-captured during import (the source photo itself for a
photo import, a fetched og:image for a URL import). Mirrors
knowledge_service.py's pattern: files live on disk under a UUID-based
name on the same Docker volume as the SQLite DB, and the Recipe row
(`Recipe.image_path`, existed since the Phase 1 schema) just holds the
path.

Deliberately a separate service/directory from knowledge_service.py's
KNOWLEDGE_FILES_DIR rather than a shared "uploads" bucket -- different
lifecycle (tied 1:1 to a recipe row, deleted when the recipe or the
image is replaced/removed) and different content (photos, not
extracted-text-bearing reference docs).
"""

from __future__ import annotations

import contextlib
import os
import uuid

RECIPE_IMAGES_DIR = os.environ.get("RECIPE_IMAGES_DIR", "/app/data/recipe_images")

# Deliberately a narrow allowlist -- these are the content types a
# browser file picker or a real-world og:image realistically produces;
# anything else is rejected rather than guessed at.
ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def save_image(content_type: str | None, raw_bytes: bytes) -> str:
    """Writes raw_bytes to a new UUID-named file under RECIPE_IMAGES_DIR
    and returns the full storage path. Atomic write (temp file + rename),
    same pattern as knowledge_service.save_file. Raises ValueError for an
    unrecognized content type -- callers decide whether that's a hard
    error (manual upload) or something to just skip (best-effort
    auto-capture during import)."""
    ext = ALLOWED_CONTENT_TYPES.get(content_type or "")
    if ext is None:
        raise ValueError(f"Unsupported image content type: {content_type}")
    os.makedirs(RECIPE_IMAGES_DIR, exist_ok=True)
    storage_path = os.path.join(RECIPE_IMAGES_DIR, f"{uuid.uuid4().hex}{ext}")
    tmp_path = storage_path + ".tmp"
    with open(tmp_path, "wb") as f:
        f.write(raw_bytes)
    os.replace(tmp_path, storage_path)
    return storage_path


def delete_image(storage_path: str | None) -> None:
    if not storage_path:
        return
    with contextlib.suppress(OSError):
        os.remove(storage_path)


def guess_content_type(storage_path: str) -> str:
    ext = os.path.splitext(storage_path)[1].lower()
    for content_type, mapped_ext in ALLOWED_CONTENT_TYPES.items():
        if mapped_ext == ext:
            return content_type
    return "application/octet-stream"
