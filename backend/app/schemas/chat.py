"""Pydantic request/response models for the persistent chat API."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChatMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    session_id: str
    role: str  # user|assistant|system|tool
    content: str
    actions: list[dict] | None = None
    created_at: datetime


class ChatSendRequest(BaseModel):
    session_id: str = "default"
    message: str


class ChatSendResponse(BaseModel):
    user_message: ChatMessageRead
    assistant_message: ChatMessageRead


class ChatSessionSummary(BaseModel):
    session_id: str
    message_count: int
    last_message_at: datetime
    preview: str = Field(..., description="Truncated content of the most recent message")
