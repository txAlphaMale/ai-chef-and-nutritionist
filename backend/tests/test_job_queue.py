"""Tests for the B11.1 background job queue (job_queue.py) -- the
serial-worker-thread infrastructure every AI-consuming endpoint now
routes through, ported architecturally from Fiduciary's job queue.
Uses time.sleep() to synchronize with the real background thread rather
than mocking it out -- this module's whole point is real concurrency
behavior (strict serialization, a job body's exception never taking the
worker thread down), so exercising the actual thread is the honest test,
not a mock standing in for it.
"""

from __future__ import annotations

import time

import pytest

from app.services import job_queue


@pytest.fixture(autouse=True)
def _clean_registry():
    job_queue._reset_for_tests()
    yield
    job_queue._reset_for_tests()


def _wait_for(job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = job_queue.get_job(job_id)
        if job["status"] in ("done", "error"):
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


def test_enqueue_runs_and_returns_result():
    jid, created = job_queue.enqueue("test", "Test job", lambda: {"value": 42})
    assert created is True
    job = _wait_for(jid)
    assert job["status"] == "done"
    assert job["result"] == {"value": 42}
    assert job["error"] is None
    assert job["started_at"] is not None
    assert job["finished_at"] is not None


def test_enqueue_captures_exception_as_error_status():
    def _boom():
        raise ValueError("something went wrong")

    jid, _ = job_queue.enqueue("test", "Boom job", _boom)
    job = _wait_for(jid)
    assert job["status"] == "error"
    assert "something went wrong" in job["error"]
    assert job["result"] is None


def test_worker_survives_a_failed_job_and_keeps_processing():
    # The single shared worker thread must not die just because one job's
    # body raised -- every job type in this app funnels through the SAME
    # thread, so one bad recipe-import request must not silently stop
    # chat/meal-plan-generation/everything else from ever running again.
    jid1, _ = job_queue.enqueue("test", "Boom", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    _wait_for(jid1)
    jid2, _ = job_queue.enqueue("test", "Fine", lambda: {"ok": True})
    job2 = _wait_for(jid2)
    assert job2["status"] == "done"
    assert job2["result"] == {"ok": True}


def test_jobs_run_strictly_serially_not_concurrently():
    # The entire point of this queue: two "heavy" jobs enqueued back to
    # back must never overlap in wall-clock time, even though nothing
    # here awaits the first one before submitting the second.
    events = []

    def _slow(tag):
        start = time.time()
        events.append((tag, "start", start))
        time.sleep(0.15)
        end = time.time()
        events.append((tag, "end", end))
        return {"tag": tag}

    jid1, _ = job_queue.enqueue("test", "Slow 1", lambda: _slow("a"))
    jid2, _ = job_queue.enqueue("test", "Slow 2", lambda: _slow("b"))
    _wait_for(jid1)
    _wait_for(jid2)

    starts_ends = {tag: {} for tag in ("a", "b")}
    for tag, phase, ts in events:
        starts_ends[tag][phase] = ts

    # b must not start until a has ended -- serial execution, not overlap.
    assert starts_ends["b"]["start"] >= starts_ends["a"]["end"]


def test_dedup_coalesces_duplicate_in_flight_submission():
    started = {"count": 0}

    def _slow():
        started["count"] += 1
        time.sleep(0.15)
        return {"n": started["count"]}

    jid1, created1 = job_queue.enqueue("test", "First", _slow, dedup_key="same-key")
    jid2, created2 = job_queue.enqueue("test", "Second", lambda: {"n": "should-not-run"}, dedup_key="same-key")
    assert created1 is True
    assert created2 is False
    assert jid1 == jid2
    job = _wait_for(jid1)
    assert job["result"] == {"n": 1}
    assert started["count"] == 1  # the second submission never actually ran its own fn


def test_dedup_allows_a_new_job_once_the_first_has_finished():
    jid1, _ = job_queue.enqueue("test", "First", lambda: {"n": 1}, dedup_key="reusable-key")
    _wait_for(jid1)
    jid2, created2 = job_queue.enqueue("test", "Second", lambda: {"n": 2}, dedup_key="reusable-key")
    assert created2 is True
    assert jid2 != jid1
    job2 = _wait_for(jid2)
    assert job2["result"] == {"n": 2}


def test_get_job_returns_none_for_unknown_id():
    assert job_queue.get_job("does-not-exist") is None


def test_list_jobs_reports_queue_depth_and_running_job():
    # Hold the worker busy with a slow first job so the second and third
    # are still genuinely "queued" when we inspect list_jobs().
    def _slow():
        time.sleep(0.2)
        return {}

    jid1, _ = job_queue.enqueue("alpha", "Slow", _slow)
    jid2, _ = job_queue.enqueue("beta", "Queued 1", lambda: {})
    jid3, _ = job_queue.enqueue("beta", "Queued 2", lambda: {})

    # Give the worker a moment to pick up jid1 and mark it running.
    deadline = time.time() + 2.0
    while time.time() < deadline:
        snapshot = job_queue.list_jobs()
        if snapshot["running"] is not None:
            break
        time.sleep(0.01)

    assert snapshot["running"]["id"] == jid1
    assert snapshot["running"]["kind"] == "alpha"
    assert snapshot["queued"] == 2

    _wait_for(jid1)
    _wait_for(jid2)
    _wait_for(jid3)
    final = job_queue.list_jobs()
    assert final["running"] is None
    assert final["queued"] == 0
    assert len(final["jobs"]) == 3
    # newest-first ordering
    assert final["jobs"][0]["id"] == jid3


def test_list_jobs_progress_reports_no_history_then_a_typical_estimate():
    jid1, _ = job_queue.enqueue("gamma", "Run 1", lambda: (time.sleep(0.05), {})[1])
    _wait_for(jid1)

    def _slow_second_run():
        time.sleep(0.1)
        return {}

    jid2, _ = job_queue.enqueue("gamma", "Run 2", _slow_second_run)
    deadline = time.time() + 2.0
    progress = None
    while time.time() < deadline:
        snapshot = job_queue.list_jobs()
        if snapshot["running"] is not None:
            progress = snapshot["progress"]
            break
        time.sleep(0.01)
    _wait_for(jid2)

    assert progress is not None
    # A completed prior "gamma" run exists (jid1), so typical_seconds
    # should now be populated rather than None.
    assert progress["typical_seconds"] is not None
    assert progress["elapsed_seconds"] >= 0


def test_jobs_list_response_omits_result_payloads():
    """Capstone review 2026-08-16. GET /api/jobs is polled every 2s by the
    always-mounted header badge; including each job's full `result` made
    that a 298 KB response against the live deployment, for data the badge
    never reads. Guarding the schema rather than the router, because the
    field being present on the model is the whole defect."""
    from app.schemas.jobs import JobListResponse, JobRead, JobSummary

    assert "result" in JobRead.model_fields, "the single-job endpoint must still return the result"
    assert "result" not in JobSummary.model_fields
    assert JobListResponse.model_fields["jobs"].annotation == list[JobSummary]
