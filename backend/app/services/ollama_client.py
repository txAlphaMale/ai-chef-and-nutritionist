"""Thin wrapper around the `ollama` Python client, configured from
DB-backed settings (settings_service) rather than static env vars, so
the base URL/models can be changed from the Settings UI (Phase 8)
without a container rebuild."""
from __future__ import annotations

import ollama
from sqlalchemy.orm import Session

from app.models import SystemPrompt
from app.services import settings_service


def _client(db: Session) -> ollama.Client:
    base_url = settings_service.get_setting(db, "ollama_base_url")
    return ollama.Client(host=base_url)


def get_active_prompt(db: Session, prompt_key: str) -> str | None:
    """e.g. prompt_key='main_chef' or 'dietary_onboarding' -- see
    app/seed.py for the seeded content."""
    row = db.query(SystemPrompt).filter_by(prompt_key=prompt_key, is_active=True).first()
    return row.content if row else None


def chat(db: Session, messages: list[dict], model: str | None = None) -> dict:
    """messages: OpenAI/Ollama-style list of {"role", "content"} dicts.
    Returns the raw Ollama response dict. Connection errors propagate --
    callers (chat endpoint, Phase 7) decide how to surface a friendly
    "Ollama unreachable" message."""
    client = _client(db)
    chat_model = model or settings_service.get_setting(db, "ollama_chat_model")
    return client.chat(model=chat_model, messages=messages)


def describe_image(db: Session, image_bytes: bytes, prompt: str, model: str | None = None) -> dict:
    """For inventory photo intake (Phase 3): send an image to the
    configured vision model with a text prompt asking it to identify
    food items and, where visible, quantity/expiration."""
    client = _client(db)
    vision_model = model or settings_service.get_setting(db, "ollama_vision_model")
    return client.chat(
        model=vision_model,
        messages=[{"role": "user", "content": prompt, "images": [image_bytes]}],
    )


def ping(db: Session) -> bool:
    """Best-effort reachability check for the configured Ollama host."""
    try:
        _client(db).list()
        return True
    except Exception:
        return False
