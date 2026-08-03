"""GUI-editable settings, system prompts, and imported nutritionist
knowledge files -- the project brief calls for all of these to be
user-customizable without a container rebuild, so they live in the DB
rather than only in .env."""
from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class AppSetting(Base, TimestampMixin):
    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_secret: Mapped[bool] = mapped_column(Boolean, default=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)


class SystemPrompt(Base, TimestampMixin):
    __tablename__ = "system_prompts"

    id: Mapped[int] = mapped_column(primary_key=True)
    # main_chef | dietary_onboarding | recipe_import | recipe_modify |
    # receipt_import | vision_intake -- the last four (backlog B16.1,
    # 2026-08-03) are the AI import/extraction prompts, seeded in
    # app/seed.py with the same default text their code-level fallback
    # constant carries (see recipe_service.get_recipe_import_prompt/
    # get_recipe_modify_prompt and routers/inventory.py's
    # get_receipt_import_prompt/get_vision_prompt).
    prompt_key: Mapped[str] = mapped_column(String(50), unique=True)
    content: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class KnowledgeFile(Base, TimestampMixin):
    __tablename__ = "knowledge_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    storage_path: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Extracted plain text (PDF/txt/md), populated once at upload time so
    # meal-plan generation and future chat grounding (Phase 7) don't need
    # to re-read/re-parse the original file on every request. Nullable
    # since extraction can fail for an unsupported/corrupt file without
    # blocking the upload itself.
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Which embedding model produced this file's current KnowledgeChunk
    # rows, or null if it hasn't been chunked/embedded yet. Compared
    # against the currently-configured ollama_embed_model setting
    # (knowledge_service.ensure_indexed) to decide whether a (re)index is
    # needed -- covers both "never indexed" (null) and "the embed model
    # changed since this was last indexed" (mismatch) in one check,
    # without needing a separate content-hash column: unlike Fiduciary's
    # watched-folder design, a KnowledgeFile's content is set once at
    # upload and never edited afterward, so the model is the only thing
    # that can make a previously-good index stale.
    indexed_embed_model: Mapped[str | None] = mapped_column(String(100), nullable=True)

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="knowledge_file", cascade="all, delete-orphan"
    )


class KnowledgeChunk(Base, TimestampMixin):
    """A chunked, embedded slice of a KnowledgeFile's extracted text --
    the unit real retrieval (knowledge_service.search_knowledge) ranks
    and returns, instead of injecting a whole file's text into every
    prompt. See knowledge_service.py's module docstring for the full
    RAG design writeup."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    knowledge_file_id: Mapped[int] = mapped_column(ForeignKey("knowledge_files.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # list[float] -- SQLite has no native vector type at this corpus
    # size a dedicated vector DB is overkill (per the backlog note this
    # was scoped from); cosine similarity runs in pure Python over these
    # JSON-decoded lists, same approach Fiduciary validated.
    vector: Mapped[list] = mapped_column(JSON)

    knowledge_file: Mapped["KnowledgeFile"] = relationship(back_populates="chunks")
