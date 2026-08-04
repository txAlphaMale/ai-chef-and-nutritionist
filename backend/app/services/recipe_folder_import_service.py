"""Backlog B13.1: batch recipe import from
a folder mounted into the container. The author has an existing OneDrive
folder pre-populated with recipes and wants those pulled into Chef
without re-uploading each file one at a time through the browser.

Deliberately reads from a LOCAL FOLDER MOUNT (a Docker volume the author
points at their OneDrive-synced folder on the host -- or any folder),
NOT a Microsoft Graph/OneDrive cloud API integration. The author chose
this explicitly (see AskUserQuestion in this session, and PROJECT-PLAN.md's
B13.1 notes) over standing up a second OAuth integration right after the
Google Calendar OAuth friction (B12.1) -- and it works for literally any
synced-folder service (OneDrive, Dropbox, Google Drive desktop, or just a
plain folder), not only OneDrive specifically. Nothing here ever talks to
Microsoft's (or anyone else's) cloud API; it is a plain directory scan
over whatever `recipe_import_folder_path` (settings_service.py) points at
inside the container.

Files are only ever READ, never modified or deleted -- this is a source
folder the author keeps their own copies in, not a working directory
Chef owns.

Each importable file is parsed via recipe_service.parse_recipe_file_
content + finish_recipe_parse -- the exact same per-file logic
routers/recipes.py's single-upload POST /api/recipes/import already
uses (refactored out of that endpoint specifically so this batch path
and the single-upload path can never silently drift apart -- see that
module's B13.1 comments)."""

from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.orm import Session

from app.services import recipe_service

# A folder of "recipes" is overwhelmingly text/PDF/JSON/HTML in practice.
# Deliberately narrower than what the single-upload endpoint accepts (no
# bare image files): batch-vision-parsing a folder full of photos would
# be a much heavier, slower, and more error-prone operation than this
# feature was asked for -- a future backlog item could add photo support
# as its own explicit opt-in.
SUPPORTED_EXTENSIONS = {".txt", ".md", ".markdown", ".pdf", ".json", ".jsonld", ".html", ".htm"}

# Safety caps -- a misconfigured mount (e.g. pointed at a huge, unrelated
# directory) shouldn't turn one "scan my recipes folder" click into an
# hours-long job or a memory problem. Generous enough for a real home
# recipe collection; the response reports if the cap was hit so the
# household knows to narrow the folder rather than silently missing files.
MAX_FILES = 300
MAX_FILE_SIZE_BYTES = 5_000_000  # 5 MB -- generous for text/PDF, guards against an accidental huge file


def list_importable_files(folder_path: str) -> dict:
    """Walks folder_path recursively (skipping dotfiles/dot-directories --
    e.g. a sync client's own .tmp/housekeeping folders), and returns:
        {"files": [str, ...absolute paths, sorted],
         "skipped": [(path, reason), ...],
         "truncated": bool,
         "error": str | None}
    Never raises for "folder doesn't exist" -- returns an empty result
    with `error` set instead, since this is surfaced to the user as a
    normal response (e.g. "set a valid path in Settings"), not a 500."""
    root = Path(folder_path) if folder_path else None
    if not root or not root.is_dir():
        return {"files": [], "skipped": [], "truncated": False, "error": f"Folder not found: {folder_path!r}"}

    files: list[Path] = []
    skipped: list[tuple[str, str]] = []
    truncated = False

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            path = Path(dirpath) / name
            ext = path.suffix.lower()
            if ext not in SUPPORTED_EXTENSIONS:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_SIZE_BYTES:
                skipped.append((str(path), f"file too large ({size // 1_000_000} MB, limit 5 MB)"))
                continue
            if len(files) >= MAX_FILES:
                truncated = True
                continue
            files.append(path)

    return {"files": [str(p) for p in files], "skipped": skipped, "truncated": truncated, "error": None}


def _guess_content_type(ext: str) -> str:
    return {
        ".json": "application/json",
        ".jsonld": "application/ld+json",
        ".pdf": "application/pdf",
        ".html": "text/html",
        ".htm": "text/html",
    }.get(ext, "text/plain")


def scan_and_parse(db: Session, folder_path: str) -> dict:
    """The actual batch job body -- meant to run inside job_queue (a
    folder of even a couple dozen files means a couple dozen sequential
    Ollama calls, easily minutes -- same B11.1 discipline as every other
    Ollama-consuming batch operation in this app). Returns:
        {"items": [{"filename", "relative_path", "status": "ok"|"error",
                     "recipe": dict | None, "error": str | None}, ...],
         "skipped": [[path, reason], ...],
         "truncated": bool,
         "scanned_folder": str,
         "error": str | None}

    Never raises for a single file's failure -- one bad file (unreadable,
    or the model couldn't extract a recipe from it) is reported as that
    file's own "error" entry, not a fatal error for the whole batch; a
    household with 40 real recipe files and 2 junk ones should still get
    the other 38 back for review."""
    listing = list_importable_files(folder_path)
    if listing["error"]:
        return {
            "items": [],
            "skipped": [],
            "truncated": False,
            "scanned_folder": folder_path,
            "error": listing["error"],
        }

    items = []
    for path_str in listing["files"]:
        path = Path(path_str)
        try:
            relative_path = str(path.relative_to(folder_path))
        except ValueError:
            relative_path = path.name  # shouldn't happen -- path always came from under folder_path above

        try:
            raw_bytes = path.read_bytes()
            content_type = _guess_content_type(path.suffix.lower())
            file_result = recipe_service.parse_recipe_file_content(db, raw_bytes, path.name, content_type)
            parsed = recipe_service.finish_recipe_parse(
                file_result["raw_output"],
                file_result["default_source"],
                file_result["citation"],
                file_result["image_path"],
                file_result["jsonld_parsed"],
                db=db,
                source_text=file_result["source_text"],
            )
            items.append(
                {"filename": path.name, "relative_path": relative_path, "status": "ok", "recipe": parsed, "error": None}
            )
        except Exception as exc:
            items.append(
                {
                    "filename": path.name,
                    "relative_path": relative_path,
                    "status": "error",
                    "recipe": None,
                    "error": str(exc),
                }
            )

    return {
        "items": items,
        "skipped": [list(s) for s in listing["skipped"]],
        "truncated": listing["truncated"],
        "scanned_folder": folder_path,
        "error": None,
    }
