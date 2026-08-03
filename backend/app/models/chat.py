"""Persistent chat history so the Chef chat can retain context and keep
running in the background while the user navigates the rest of the app."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import UtcDateTime, utc_now


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True, default="default")
    role: Mapped[str] = mapped_column(String(20))  # user|assistant|system|tool
    content: Mapped[str] = mapped_column(Text)
    # Structured action proposals attached to an assistant message (e.g.
    # "deduct 1 cup of lentils", "confirm Tuesday's dinner") -- stored so
    # history replay after a reload can still show actionable cards, not
    # just the reply text. Always null on user/system messages. See
    # chat_service.py for the action schema and routers/chat.py for how
    # a confirmed action maps to an existing endpoint call.
    actions: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, index=True)
