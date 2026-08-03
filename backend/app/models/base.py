"""Shared model mixins and the app's one timestamp type."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, TypeDecorator
from sqlalchemy.orm import Mapped, mapped_column


def utc_now() -> datetime:
    """The single source of "now" for every stored timestamp.

    Replaces `datetime.utcnow()`, which is deprecated in Python 3.12 (the
    image's interpreter) and, worse, returns a NAIVE datetime that merely
    happens to hold UTC. Naive values compare fine against each other and
    raise `TypeError` the moment one meets an aware value -- so the bug
    only ever surfaces at the one call site that got it right."""
    return datetime.now(timezone.utc)


class UtcDateTime(TypeDecorator):
    """A timestamp column that is timezone-aware in Python and stored as
    naive UTC on disk.

    This exists because of what SQLite does, not because of a preference.
    SQLite has no native timestamp type and SQLAlchemy's `DateTime`
    serialises to a string with no offset, so an aware value written to a
    plain `DateTime` column silently comes back naive. Switching the
    defaults to `datetime.now(timezone.utc)` without this would have
    fixed the deprecation warning and left the actual defect -- mixed
    naive/aware comparisons -- exactly where it was.

    The decorator closes the loop at both ends: aware in, naive UTC on
    disk, aware back out. Nothing downstream has to track which kind it
    is holding, which is the only version of this that stays correct as
    the app grows.

    A naive value passed in is interpreted as UTC rather than rejected.
    That is not sloppiness: every naive timestamp already in this
    database was written by `datetime.utcnow()`, so UTC is what they
    factually are, and reading them back as aware UTC is a correct
    reinterpretation rather than a guess. No data migration is needed for
    that reason, and none is included.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, default=utc_now, onupdate=utc_now, nullable=False
    )
