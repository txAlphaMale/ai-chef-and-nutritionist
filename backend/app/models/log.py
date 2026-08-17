"""The application log, in the database.

Capstone review 2026-08-16, backlog B24.2. Until this table existed there
was no `logging` configuration anywhere in this backend -- no `getLogger`,
no handler, no level. Diagnostics were 36 bare `print()` calls to the
container's stdout, which means every operational question ("why did that
import produce nothing", "is Ollama timing out", "what did the model
actually return") required `docker compose logs` and a shell on the host.
Nothing was visible from inside the app at all, which is the concrete
reason a separate text "import health report" had to be built on
2026-08-07: none of that session's data defects could be seen in the UI.

It also closes a gap against the project's own stated rule -- settings,
data and logging live in the database so there is one thing to back up and
no sprawl. Everything else in this app already obeyed that; logging was the
exception.

**stdout is kept as well as this table, deliberately.** `docker compose
logs` is how you debug a container that will not start, and a database row
written by a process that crashed during startup is a row that does not
exist. Stdout is a stream, not storage, so this is not the sprawl the rule
is about.

**What must never be written here.** `ollama_client._log_response` already
established the rule this table inherits: log the SHAPE of a model
response, never its content. A reply in this app can contain bloodwork
values, weights and health metrics, and a log is the one place data
quietly accumulates without anybody deciding it should. Lengths, previews
truncated to a couple of hundred characters, counts and error types are
enough to tell "empty", "truncated" and "looks like real JSON" apart --
which is what debugging actually needs.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import UtcDateTime, utc_now

# The levels this app uses, narrowest set that is actually useful. No
# CRITICAL: nothing here is more urgent than ERROR in a household app, and
# a level nobody emits is a filter option that always returns nothing.
LOG_LEVELS = ("debug", "info", "warning", "error")


class AppLogEntry(Base):
    __tablename__ = "app_log_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Not TimestampMixin: a log row is written once and never updated, so
    # an `updated_at` on it would be a column that is always equal to
    # `created_at` and invites somebody to believe otherwise.
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, nullable=False, index=True)
    level: Mapped[str] = mapped_column(String(10), default="info", nullable=False, index=True)
    # The subsystem, matching the bracketed prefix the print() calls
    # already used -- "ollama_client", "recipe_import", "job_queue". Kept
    # as free text rather than an enum so a new service does not need a
    # migration to log.
    source: Mapped[str] = mapped_column(String(50), default="app", nullable=False, index=True)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    # Ties a line to the background job that produced it, so the Logs view
    # can answer "what happened during that import" rather than only
    # "what happened at 14:32".
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    __table_args__ = (
        # The Logs view's default query is "newest first, optionally
        # filtered by level" -- a composite so that stays one index scan
        # once the table has a few hundred thousand rows in it.
        Index("ix_app_log_entries_created_level", "created_at", "level"),
    )
