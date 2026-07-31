"""Read-only system/status endpoints: settings list (secrets masked),
system prompts, and Ollama/Tavily connectivity checks. Full CRUD for
settings/prompts arrives with the Settings GUI (Phase 8) -- this is
enough for the frontend to show status now, and for local verification
without a browser."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import SystemPrompt
from app.services import ollama_client, settings_service, tavily_client

router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/settings")
def list_settings(db: Session = Depends(get_db)):
    return settings_service.list_settings_for_display(db)


@router.get("/prompts")
def list_prompts(db: Session = Depends(get_db)):
    rows = db.query(SystemPrompt).all()
    return [
        {"prompt_key": r.prompt_key, "content": r.content, "is_active": r.is_active}
        for r in rows
    ]


@router.get("/status")
def status(db: Session = Depends(get_db)):
    return {
        "ollama_reachable": ollama_client.ping(db),
        "tavily_configured": tavily_client.is_configured(db),
    }
