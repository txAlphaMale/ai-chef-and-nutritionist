"""Writing to and reading from the application log.

Capstone review 2026-08-16, backlog B24.2. See `app/models/log.py` for why
this table exists and what must never be written into it.

**Three properties this has to hold, in priority order.**

1. *Logging must never break the thing it is logging.* Every write is
   wrapped, and a failure to log is swallowed after being printed. An
   import that dies because the log table was locked would be a strictly
   worse outcome than an import with no log line.
2. *It must never block a request noticeably.* Writes go through a short-
   lived session of their own and commit immediately, on a database
   configured for WAL with a 15-second busy timeout (see
   `app/database.py`). That is adequate at this app's actual log volume,
   which is a handful of lines per AI call, not per request. If volume
   ever grows -- a per-request access log, say -- the next step is an
   in-memory buffer drained by a daemon thread, and this docstring is the
   note to whoever gets there.
3. *It must not grow without bound.* See `_maybe_trim`.

**Why not the stdlib `logging` module.** It was the obvious candidate and
was rejected for a specific reason: a `logging.Handler` that writes to the
ORM is called from inside arbitrary code paths, including exception
handlers and (on this app's job worker) other threads, and a handler that
raises is swallowed by `logging` itself in ways that hide the failure. The
call sites here are 36 existing `print()` statements with a bracketed
prefix already; giving them a function with the same shape is a smaller,
more auditable change than configuring a global logger with a custom
handler and hoping every future caller uses it correctly. The stdlib
remains the right answer for a library; this is an application with one
process and one destination.
"""

from __future__ import annotations

import threading
from datetime import timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.base import utc_now
from app.models.log import LOG_LEVELS, AppLogEntry

# Retention. Two limits, because either one alone fails a realistic case: a
# bulk import can produce thousands of lines in an hour (so age alone lets
# the table balloon), and a quiet month produces almost none (so a row cap
# alone would throw away a history worth keeping).
MAX_AGE_DAYS = 30
MAX_ROWS = 20_000

# Trimming scans and deletes, so it does not run on every write. Every
# Nth write is frequent enough to keep the table near its limits and rare
# enough to be invisible.
_TRIM_EVERY_N_WRITES = 250

# A stored message is truncated rather than rejected. A caller that
# accidentally passes a whole model response should produce a clipped log
# line, not an exception inside an exception handler.
MAX_MESSAGE_CHARS = 4000

_write_counter = 0
_counter_lock = threading.Lock()


def _should_trim() -> bool:
    global _write_counter
    with _counter_lock:
        _write_counter += 1
        return _write_counter % _TRIM_EVERY_N_WRITES == 0


def _maybe_trim(db: Session) -> None:
    """Age first, then row count. Cheap when there is nothing to do."""
    cutoff = utc_now() - timedelta(days=MAX_AGE_DAYS)
    db.execute(delete(AppLogEntry).where(AppLogEntry.created_at < cutoff))

    total = db.query(AppLogEntry.id).count()
    if total > MAX_ROWS:
        # Delete by id rather than by timestamp: ids are monotonic here and
        # an id comparison needs no date arithmetic per row.
        threshold = (
            db.query(AppLogEntry.id)
            .order_by(AppLogEntry.id.desc())
            .offset(MAX_ROWS)
            .limit(1)
            .scalar()
        )
        if threshold is not None:
            db.execute(delete(AppLogEntry).where(AppLogEntry.id <= threshold))
    db.commit()


def log(
    level: str,
    source: str,
    message: str,
    *,
    job_id: str | None = None,
    db: Session | None = None,
) -> None:
    """Write one line to stdout AND the log table.

    `db` is optional and mostly should not be passed. Several of the call
    sites this replaces (`ollama_client`'s request/response logging, the
    job worker's supervisor line) have no Session in scope at all, and
    threading one through to them purely for logging would spread a
    logging concern across signatures that have nothing to do with it. When
    a caller does pass one, its session is reused and NOT committed here --
    committing somebody else's open transaction to write a log line would
    be a genuinely dangerous side effect.
    """
    level = level if level in LOG_LEVELS else "info"
    text = message if len(message) <= MAX_MESSAGE_CHARS else message[:MAX_MESSAGE_CHARS] + " ...[truncated]"

    # stdout first, unconditionally, and before the database write can
    # fail. `docker compose logs` has to keep working for the case this
    # table cannot help with: a container that will not finish starting.
    print(f"[{source}] {text}", flush=True)

    entry = AppLogEntry(level=level, source=source, message=text, job_id=job_id)
    try:
        if db is not None:
            db.add(entry)
            # No commit AND no flush, both deliberate.
            #
            # No commit: the caller owns this transaction. The line lands
            # when they commit, and rolls back with whatever failed --
            # which is the right coupling, since a log line describing an
            # operation that was rolled back is a record of something that
            # did not happen.
            #
            # No flush either, which is less obvious. Flushing would make
            # the row readable immediately and reads as friendlier, but a
            # flush writes the CALLER's pending objects too -- so a
            # half-built object in their session would hit the database
            # early and raise from inside a logging call. That is exactly
            # the failure mode this module exists to prevent.
            return

        own = SessionLocal()
        try:
            own.add(entry)
            own.commit()
            if _should_trim():
                _maybe_trim(own)
        finally:
            own.close()
    except Exception as exc:
        print(f"[log_service] could not persist a log line ({type(exc).__name__}: {exc})", flush=True)


def debug(source: str, message: str, **kwargs) -> None:
    log("debug", source, message, **kwargs)


def info(source: str, message: str, **kwargs) -> None:
    log("info", source, message, **kwargs)


def warning(source: str, message: str, **kwargs) -> None:
    log("warning", source, message, **kwargs)


def error(source: str, message: str, **kwargs) -> None:
    log("error", source, message, **kwargs)


# --- Reading ---------------------------------------------------------------


def list_entries(
    db: Session,
    *,
    level: str | None = None,
    source: str | None = None,
    job_id: str | None = None,
    search: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> tuple[list[AppLogEntry], int]:
    """Newest first, with the total matching count alongside the page.

    Paginated from the start, unlike the recipes list was -- a log is the
    one table in this app guaranteed to outgrow any "just return them all"
    approach, and B24.1 is a recent enough lesson.
    """
    query = db.query(AppLogEntry)
    if level:
        query = query.filter(AppLogEntry.level == level)
    if source:
        query = query.filter(AppLogEntry.source == source)
    if job_id:
        query = query.filter(AppLogEntry.job_id == job_id)
    if search:
        query = query.filter(AppLogEntry.message.ilike(f"%{search}%"))

    total = query.count()
    rows = query.order_by(AppLogEntry.created_at.desc(), AppLogEntry.id.desc()).offset(offset).limit(limit).all()
    return rows, total


def list_sources(db: Session) -> list[str]:
    """The distinct sources actually present, so the filter dropdown offers
    what exists rather than a hardcoded list that drifts."""
    return [row[0] for row in db.query(AppLogEntry.source).distinct().order_by(AppLogEntry.source).all()]


def clear(db: Session) -> int:
    """Empty the log. Returns how many rows went, so the UI can say."""
    total = db.query(AppLogEntry.id).count()
    db.execute(delete(AppLogEntry))
    db.commit()
    return total
