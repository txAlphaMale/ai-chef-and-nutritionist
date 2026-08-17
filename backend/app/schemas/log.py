"""Response shapes for the application log (backlog B24.2)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AppLogEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    level: str
    source: str
    message: str
    job_id: str | None = None


class AppLogPage(BaseModel):
    """Paginated from the start, unlike the recipes list was -- the log is
    the one table here guaranteed to outgrow "just return them all", and
    B24.1 is a recent enough lesson to not repeat."""

    entries: list[AppLogEntryRead] = Field(default_factory=list)
    total: int = 0
    limit: int = 200
    offset: int = 0
    # The sources actually present, so the filter offers what exists
    # instead of a hardcoded list that drifts as services are added.
    sources: list[str] = Field(default_factory=list)


class AppLogClearResult(BaseModel):
    deleted: int
