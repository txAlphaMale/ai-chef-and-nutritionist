"""Capstone review 2026-08-16, backlog B24.2 -- the application log.

Before this, the backend had no logging configuration at all: 36 bare
`print()` calls to container stdout, nothing visible from inside the app,
and every operational question requiring a shell on the host.

The property that matters most here is not that logging works. It is that
logging **cannot break the thing it is logging** -- a log write is the last
call anybody wants to see in a traceback.
"""

from datetime import timedelta

import pytest

from app.models.base import utc_now
from app.models.log import AppLogEntry
from app.services import log_service


def _entries(db):
    return db.query(AppLogEntry).order_by(AppLogEntry.id).all()


# --- The safety property --------------------------------------------------


def test_a_failing_log_write_does_not_raise(db_session, monkeypatch, capsys):
    """The whole point. If persisting a line fails, the caller must carry
    on -- an import that dies because the log table was locked is strictly
    worse than an import with no log line."""

    def boom():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(log_service, "SessionLocal", boom)

    # No pytest.raises: the assertion is that this returns normally.
    log_service.error("some_service", "something went wrong")

    out = capsys.readouterr().out
    assert "something went wrong" in out, "the line must still reach stdout"
    assert "could not persist a log line" in out, "and the failure must be visible, not silent"


def test_stdout_still_gets_the_line(db_session, capsys):
    """`docker compose logs` has to keep working -- it is the only thing
    that helps when a container will not finish starting, which is exactly
    when a database row does not exist."""
    log_service.info("recipe_import", "parsed 12 ingredients")

    assert "[recipe_import] parsed 12 ingredients" in capsys.readouterr().out


def test_an_unknown_level_is_coerced_rather_than_rejected(db_session):
    log_service.log("SHOUTING", "some_service", "hello")

    assert _entries(db_session)[0].level == "info"


def test_an_enormous_message_is_truncated_not_refused(db_session):
    """A caller that accidentally passes a whole model response should get
    a clipped line, not an exception inside an exception handler."""
    log_service.info("ollama_client", "x" * (log_service.MAX_MESSAGE_CHARS + 500))

    stored = _entries(db_session)[0].message
    assert len(stored) < log_service.MAX_MESSAGE_CHARS + 100
    assert stored.endswith("...[truncated]")


# --- Writing --------------------------------------------------------------


def test_a_line_lands_with_its_level_source_and_job(db_session):
    log_service.warning("job_queue", "worker restarted", job_id="job-123")

    entry = _entries(db_session)[0]
    assert (entry.level, entry.source, entry.message, entry.job_id) == (
        "warning",
        "job_queue",
        "worker restarted",
        "job-123",
    )


def test_a_line_written_into_a_callers_session_lands_on_their_commit(db_session):
    """Committing somebody else's open transaction to write a log line
    would be a genuinely dangerous side effect, so `log(db=...)` only
    adds. The line lands when the caller commits."""
    log_service.info("some_service", "rides along", db=db_session)
    db_session.commit()

    assert [e.message for e in _entries(db_session)] == ["rides along"]


def test_a_line_written_into_a_callers_session_rolls_back_with_it(db_session):
    """The other half of the same contract, and the reason it is the right
    coupling: a log line describing an operation that was rolled back
    would be a record of something that did not happen."""
    log_service.info("some_service", "about to fail", db=db_session)
    db_session.rollback()

    assert _entries(db_session) == []


def test_log_with_a_session_does_not_flush_the_callers_unit_of_work(db_session):
    """Deliberately no flush. Flushing would make the row visible
    immediately, which reads as friendlier -- but it flushes the CALLER's
    pending objects too, so a half-built object in their session would hit
    the database early and raise from inside a logging call. That is
    exactly the failure mode this module exists to avoid."""
    from app.models import Recipe

    # A Recipe with no title violates NOT NULL. If logging flushed, this
    # would raise here rather than at the caller's own commit.
    db_session.add(Recipe(title=None, default_servings=2))

    log_service.info("some_service", "should not trigger a flush", db=db_session)  # must not raise

    db_session.rollback()


# --- Retention ------------------------------------------------------------


def test_trimming_drops_entries_past_the_age_limit(db_session):
    old = AppLogEntry(
        level="info",
        source="old",
        message="ancient",
        created_at=utc_now() - timedelta(days=log_service.MAX_AGE_DAYS + 1),
    )
    recent = AppLogEntry(level="info", source="new", message="recent")
    db_session.add_all([old, recent])
    db_session.commit()

    log_service._maybe_trim(db_session)

    assert [e.message for e in _entries(db_session)] == ["recent"]


def test_trimming_enforces_the_row_cap(db_session, monkeypatch):
    monkeypatch.setattr(log_service, "MAX_ROWS", 5)
    for i in range(12):
        db_session.add(AppLogEntry(level="info", source="bulk", message=f"line {i}"))
    db_session.commit()

    log_service._maybe_trim(db_session)

    remaining = _entries(db_session)
    assert len(remaining) == 5
    # The NEWEST are what survive -- a log that keeps the oldest lines and
    # discards what just happened is worse than no log.
    assert [e.message for e in remaining] == [f"line {i}" for i in range(7, 12)]


def test_trimming_does_not_run_on_every_write(db_session):
    """Trimming scans and deletes; doing it per line would make logging
    the expensive part of a bulk import."""
    log_service._write_counter = 0
    fired = [log_service._should_trim() for _ in range(log_service._TRIM_EVERY_N_WRITES)]

    assert fired.count(True) == 1
    assert fired[-1] is True


# --- Reading --------------------------------------------------------------


@pytest.fixture()
def populated(db_session):
    log_service.info("recipe_import", "parsed a recipe", db=db_session)
    log_service.error("ollama_client", "timed out talking to the model", db=db_session)
    log_service.warning("recipe_import", "pdfplumber failed, falling back", db=db_session)
    log_service.info("job_queue", "job finished", job_id="job-9", db=db_session)
    db_session.commit()
    return db_session


def test_entries_come_back_newest_first(populated):
    entries, total = log_service.list_entries(populated)

    assert total == 4
    assert entries[0].message == "job finished"


def test_filters_narrow_by_level_source_job_and_text(populated):
    by_level, _ = log_service.list_entries(populated, level="error")
    by_source, _ = log_service.list_entries(populated, source="recipe_import")
    by_job, _ = log_service.list_entries(populated, job_id="job-9")
    by_text, _ = log_service.list_entries(populated, search="pdfplumber")

    assert [e.source for e in by_level] == ["ollama_client"]
    assert len(by_source) == 2
    assert [e.message for e in by_job] == ["job finished"]
    assert [e.source for e in by_text] == ["recipe_import"]


def test_the_total_describes_the_filter_not_the_page(populated):
    """A page of 1 out of 4 matches must still report 4, or the UI cannot
    paginate and the user cannot tell how much they are not seeing."""
    entries, total = log_service.list_entries(populated, source="recipe_import", limit=1)

    assert len(entries) == 1
    assert total == 2


def test_sources_are_discovered_from_the_data(populated):
    assert log_service.list_sources(populated) == ["job_queue", "ollama_client", "recipe_import"]


def test_clear_empties_the_log_and_reports_how_many(populated):
    assert log_service.clear(populated) == 4
    assert _entries(populated) == []


# --- The endpoint ---------------------------------------------------------


def test_the_endpoint_clamps_an_unbounded_limit(populated):
    """This is the one table designed to get large, so `?limit=999999`
    must not hand back the whole thing."""
    from app.routers.system import get_logs

    page = get_logs(limit=10_000_000, db=populated)

    assert page.limit == 1000


def test_the_endpoint_returns_filters_and_sources_together(populated):
    from app.routers.system import get_logs

    page = get_logs(level="error", db=populated)

    assert page.total == 1
    assert page.entries[0].source == "ollama_client"
    # Sources list the whole table, not just the filtered slice -- otherwise
    # filtering to one source removes every other option from the dropdown
    # and you cannot get back.
    assert "recipe_import" in page.sources
