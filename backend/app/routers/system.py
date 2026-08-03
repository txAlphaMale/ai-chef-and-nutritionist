"""System/status endpoints: settings list + update (secrets masked on
read, encrypted on write via settings_service), system prompts list +
update, and Ollama/Tavily connectivity checks. Phase 2 shipped this
read-only; Phase 8 (Settings GUI) adds the PATCH endpoints the frontend
settings page needs -- no new machinery, both PATCHes just call the
service/model layer Phase 2 already built."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SystemPrompt
from app.schemas.system import PromptUpdate, SettingUpdate
from app.services import (
    backup_service,
    google_calendar_service,
    icloud_calendar_service,
    ollama_client,
    settings_service,
    tavily_client,
)

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/settings")
def list_settings(db: Session = Depends(get_db)):
    return settings_service.list_settings_for_display(db)


@router.patch("/settings/{key}")
def update_setting(key: str, payload: SettingUpdate, db: Session = Depends(get_db)):
    # Only known keys are editable through the Settings GUI -- an
    # unrecognized key almost certainly means a typo/stale frontend build
    # rather than an intentional new setting (those are added via
    # settings_service.SETTING_SPECS, not through this endpoint).
    if not settings_service.is_known_key(key):
        raise HTTPException(status_code=404, detail=f"Unknown setting key: {key}")
    settings_service.set_setting(db, key, payload.value)
    return settings_service.list_settings_for_display(db)


@router.get("/prompts")
def list_prompts(db: Session = Depends(get_db)):
    rows = db.query(SystemPrompt).all()
    return [{"prompt_key": r.prompt_key, "content": r.content, "is_active": r.is_active} for r in rows]


@router.patch("/prompts/{prompt_key}")
def update_prompt(prompt_key: str, payload: PromptUpdate, db: Session = Depends(get_db)):
    row = db.query(SystemPrompt).filter_by(prompt_key=prompt_key).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Unknown prompt key: {prompt_key}")
    if payload.content is not None:
        row.content = payload.content
    if payload.is_active is not None:
        row.is_active = payload.is_active
    db.commit()
    db.refresh(row)
    return {"prompt_key": row.prompt_key, "content": row.content, "is_active": row.is_active}


@router.get("/status")
def status(db: Session = Depends(get_db)):
    """Backlog B14: the Connection status
    card originally only covered the two things Phase 2 shipped
    (Ollama/Tavily). As Chef gained real integrations (Google Calendar,
    B12.1) there was nowhere on this card to see whether one was
    actually configured -- the author asked for that directly. The new
    `integrations` list is deliberately generic (key/label/configured/
    connected/detail) rather than one hardcoded Google Calendar field,
    so a future integration (the recipe-folder-import path below, or
    B12.2's iCloud sync) only needs one new entry here, not a frontend
    schema change. `connected` is `None` (not `False`) for an
    integration with no real "connect" step of its own (recipe folder
    import is just configured-or-not, there's no handshake to complete)
    -- the frontend should treat `None` as "not applicable", not "not
    connected"."""
    gcal = google_calendar_service.connection_status(db)
    icloud = icloud_calendar_service.connection_status(db)
    folder_path = settings_service.get_setting(db, "recipe_import_folder_path")
    return {
        "ollama_reachable": ollama_client.ping(db),
        "tavily_configured": tavily_client.is_configured(db),
        "integrations": [
            {
                "key": "google_calendar",
                "label": "Google Calendar",
                "configured": gcal["configured"],
                "connected": gcal["connected"],
                "detail": gcal["account_email"],
            },
            {
                "key": "icloud_calendar",
                "label": "iCloud Calendar",
                "configured": icloud["configured"],
                "connected": icloud["connected"],
                "detail": icloud["username"],
            },
            {
                "key": "recipe_folder_import",
                "label": "Recipe folder import",
                "configured": bool(folder_path),
                "connected": None,
                "detail": folder_path or None,
            },
        ],
    }


@router.get("/backup/manifest")
def get_backup_manifest():
    """Backlog B9.2 -- a cheap, display-only preview of what a backup
    download currently contains, so the Settings UI can show something
    more useful than a bare button before the user clicks it."""
    return backup_service.backup_manifest()


@router.get("/backup")
def download_backup():
    """Backlog B9.2 -- streams a full .tar.gz backup (database + secret
    key files + recipe images + knowledge files) as a downloadable file.
    See backup_service.py's module docstring for exactly what's included
    and, importantly, why this endpoint does not also offer restore."""
    archive = backup_service.build_backup_archive()
    filename = f"chef-backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.tar.gz"
    return Response(
        content=archive,
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
