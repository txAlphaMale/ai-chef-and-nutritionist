"""Pydantic request models for the Settings GUI (Phase 8): updating a
DB-backed setting's value or a system prompt's content. Responses reuse
the plain dicts settings_service/system router already built for the
read-only endpoints (Phase 2) -- no separate Read schema needed."""
from __future__ import annotations

from pydantic import BaseModel


class SettingUpdate(BaseModel):
    value: str


class PromptUpdate(BaseModel):
    content: str | None = None
    is_active: bool | None = None
