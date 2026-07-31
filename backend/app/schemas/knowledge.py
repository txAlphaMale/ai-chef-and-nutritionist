"""Pydantic response/update models for imported nutritionist knowledge
files. Full extracted `content` is intentionally never returned from the
list/get endpoints (it can be large and isn't meant for display) -- only
a short excerpt, for a quick sanity-check preview in the UI."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class KnowledgeFileUpdate(BaseModel):
    description: str | None = None
    is_active: bool | None = None


class KnowledgeFileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    content_type: str | None = None
    description: str | None = None
    is_active: bool
    has_content: bool  # whether text extraction succeeded (grounds meal-plan generation)
    content_excerpt: str | None = None
    created_at: datetime
