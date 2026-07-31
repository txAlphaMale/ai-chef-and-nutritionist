"""System/status endpoints: settings list + update (secrets masked on
read, encrypted on write via settings_service), system prompts list +
update, and Ollama/Tavily connectivity checks. Phase 2 shipped this
read-only; Phase 8 (Settings GUI) adds the PATCH endpoints the frontend
settings page needs -- no new machinery, both PATCHes just call the
service/model layer Phase 2 already built."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SystemPrompt
from app.schemas.system import PromptUpdate, SettingUpdate
from app.services import ollama_client, settings_service, tavily_client

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
    return [
        {"prompt_key": r.prompt_key, "content": r.content, "is_active": r.is_active}
        for r in rows
    ]


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
    return {
        "ollama_reachable": ollama_client.ping(db),
        "tavily_configured": tavily_client.is_configured(db),
    }
