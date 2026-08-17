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

from app import prompt_defaults
from app.database import get_db
from app.models import SystemPrompt
from app.schemas.dashboard import DashboardResponse
from app.schemas.log import AppLogClearResult, AppLogPage
from app.schemas.system import PromptUpdate, SettingUpdate
from app.services import (
    backup_service,
    dashboard_service,
    google_calendar_service,
    icloud_calendar_service,
    log_service,
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


def _prompt_view(prompt_key: str, row: SystemPrompt | None) -> dict:
    """One entry of the prompts list.

    `default_content` is what this build ships and is non-null only for
    the extraction prompts, which have a code-level fallback. `main_chef`
    and `dietary_onboarding` have none -- their row IS the value -- so
    they report null and the UI offers no revert for them.

    `has_override` is the field that actually matters and it is simply
    "a row exists". Before this endpoint reported it, an untouched seeded
    copy of a default looked exactly like a household edit, so nobody
    could tell which text the model was about to run. See
    app/prompt_defaults.py."""
    default = prompt_defaults.IMPORT_PROMPT_DEFAULTS.get(prompt_key)
    return {
        "prompt_key": prompt_key,
        "content": row.content if row else "",
        "is_active": row.is_active if row else False,
        "default_content": default,
        "has_override": row is not None,
    }


@router.get("/prompts")
def list_prompts(db: Session = Depends(get_db)):
    rows = {r.prompt_key: r for r in db.query(SystemPrompt).all()}
    # Extraction prompts are listed whether or not a row exists, because
    # the shipped default is a real, editable thing the Settings page has
    # to be able to show. Any other key is listed only if it has a row.
    keys = list(rows) + [k for k in prompt_defaults.IMPORT_PROMPT_DEFAULTS if k not in rows]
    return [_prompt_view(k, rows.get(k)) for k in keys]


@router.patch("/prompts/{prompt_key}")
def update_prompt(prompt_key: str, payload: PromptUpdate, db: Session = Depends(get_db)):
    row = db.query(SystemPrompt).filter_by(prompt_key=prompt_key).first()
    default = prompt_defaults.IMPORT_PROMPT_DEFAULTS.get(prompt_key)
    # An extraction prompt has no row until someone saves an edit, so the
    # first save creates one. A key with neither a row nor a shipped
    # default is a typo or a stale frontend build -- same reasoning as
    # update_setting above.
    if row is None and default is None:
        raise HTTPException(status_code=404, detail=f"Unknown prompt key: {prompt_key}")

    # PATCH is partial, so resolve the result of this edit BEFORE touching
    # the session. Building the row first and reconsidering afterwards is
    # what the first version did, and it raised "not persisted" the moment
    # a household saved the default text with no row present: the object
    # had been added but never flushed, so there was nothing to delete.
    content = payload.content if payload.content is not None else (row.content if row else default)
    is_active = payload.is_active if payload.is_active is not None else (row.is_active if row else True)

    # Saving the shipped text back verbatim is a request to stop
    # overriding, not a request to store a duplicate of the default --
    # storing it would re-create the exact ambiguity this endpoint exists
    # to remove.
    if prompt_defaults.is_shipped_default(prompt_key, content):
        if row is not None:
            db.delete(row)
            db.commit()
        return _prompt_view(prompt_key, None)

    if row is None:
        row = SystemPrompt(prompt_key=prompt_key)
        db.add(row)
    row.content = content
    row.is_active = is_active
    db.commit()
    db.refresh(row)
    return _prompt_view(prompt_key, row)


@router.delete("/prompts/{prompt_key}")
def delete_prompt_override(prompt_key: str, db: Session = Depends(get_db)):
    """Discard a household override and go back to the shipped default.

    Only the extraction prompts can be reverted: main_chef and
    dietary_onboarding have no code-level fallback, so deleting one would
    leave the chef with an empty system prompt rather than a default."""
    if prompt_key not in prompt_defaults.IMPORT_PROMPT_DEFAULTS:
        raise HTTPException(status_code=404, detail=f"Prompt has no shipped default to revert to: {prompt_key}")
    row = db.query(SystemPrompt).filter_by(prompt_key=prompt_key).first()
    if row is not None:
        db.delete(row)
        db.commit()
    return _prompt_view(prompt_key, None)


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


@router.get("/dashboard", response_model=DashboardResponse)
def dashboard(db: Session = Depends(get_db)):
    """Backlog B24.3 -- everything the Home page shows, in one read.

    Deliberately NOT on `/status`, which the Settings page polls for
    connection state: this is DB-only and fast, `/status` makes network
    calls to Ollama and the calendar providers, and merging them would put
    a network round trip on the app's landing page."""
    return dashboard_service.build_dashboard(db)


@router.get("/logs", response_model=AppLogPage)
def get_logs(
    level: str | None = None,
    source: str | None = None,
    job_id: str | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """Backlog B24.2 -- the application log, readable from inside the app.

    Before this, answering "why did that import produce nothing" meant
    `docker compose logs` and a shell on the host. `limit` is clamped
    rather than trusted: this is the one endpoint whose table is designed
    to get large, and an unbounded `?limit=` would hand a caller the whole
    thing."""
    limit = max(1, min(limit, 1000))
    offset = max(0, offset)
    entries, total = log_service.list_entries(
        db, level=level, source=source, job_id=job_id, search=search, limit=limit, offset=offset
    )
    return AppLogPage(
        entries=entries,
        total=total,
        limit=limit,
        offset=offset,
        sources=log_service.list_sources(db),
    )


@router.delete("/logs", response_model=AppLogClearResult)
def clear_logs(db: Session = Depends(get_db)):
    """Empties the log. Retention trims it automatically (30 days /
    20,000 rows, see log_service), so this is for deliberately discarding
    a noisy period rather than routine housekeeping."""
    return AppLogClearResult(deleted=log_service.clear(db))


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
