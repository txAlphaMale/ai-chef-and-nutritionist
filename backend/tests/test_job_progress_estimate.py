"""A progress estimate has to compare a job against comparable jobs.

Measured 2026-08-07, from the author's screen: `Recipe import (108s of ~1s
typical, 21497%)`. A JSON-LD URL import runs no model and finishes in
about a second; a PDF import spends two model calls and takes ~100. Both
were kind `recipe_import`, so the median of the last five runs mixed them
and the badge cried wolf on a perfectly healthy import.

The bucket is not always knowable when the job is enqueued -- whether a
URL import runs a model depends on whether that page publishes schema.org
data, which is only discovered mid-job -- so a job can declare its own
bucket once it finds out.
"""

from app.services import job_queue


def _done(kind, estimate_key, seconds, jid):
    return {
        "id": jid,
        "kind": kind,
        "estimate_key": estimate_key,
        "status": "done",
        "started_at": 0.0,
        "finished_at": float(seconds),
    }


def test_the_bug_the_bucket_exists_to_stop():
    """Four one-second structured imports and one 100-second model import,
    all kind `recipe_import`. Judged as one population the median is 1."""
    jobs = [_done("recipe_import", "recipe_import", 1, f"s{i}") for i in range(4)]
    jobs.append(_done("recipe_import", "recipe_import", 100, "m0"))
    assert job_queue._typical_seconds("recipe_import", jobs) == 1.0


def test_bucketed_runs_are_compared_only_with_their_own_kind_of_work():
    jobs = [_done("recipe_import", "recipe_import:structured", 1, f"s{i}") for i in range(4)]
    jobs += [_done("recipe_import", "recipe_import:model", 100, f"m{i}") for i in range(3)]

    assert job_queue._typical_seconds("recipe_import:structured", jobs) == 1.0
    assert job_queue._typical_seconds("recipe_import:model", jobs) == 100.0


def test_a_job_with_no_bucket_falls_back_to_its_kind():
    """Every job enqueued before this change, and every job kind that
    never needed a bucket."""
    jobs = [{"id": "a", "kind": "chat", "status": "done", "started_at": 0.0, "finished_at": 7.0}]
    assert job_queue._typical_seconds("chat", jobs) == 7.0


def test_no_history_in_this_bucket_is_none_not_zero():
    """Shown as "no history yet" rather than a fabricated 0%."""
    jobs = [_done("recipe_import", "recipe_import:model", 100, "m0")]
    assert job_queue._typical_seconds("recipe_import:structured", jobs) is None


def test_enqueue_defaults_the_bucket_to_the_kind():
    job_queue._reset_for_tests()
    jid, _ = job_queue.enqueue("some_kind", "Some job", lambda: {"ok": True})
    assert job_queue.get_job(jid)["estimate_key"] == "some_kind"


def test_a_job_can_correct_its_own_bucket_while_running():
    job_queue._reset_for_tests()
    seen = {}

    def body():
        job_queue.set_estimate_key("some_kind:cheap")
        return {"ok": True}

    jid, _ = job_queue.enqueue("some_kind", "Some job", body, estimate_key="some_kind:expensive")
    for _ in range(200):
        job = job_queue.get_job(jid)
        if job["status"] in ("done", "error"):
            break
        import time

        time.sleep(0.01)
    seen = job_queue.get_job(jid)
    assert seen["status"] == "done", seen.get("error")
    assert seen["estimate_key"] == "some_kind:cheap"


def test_set_estimate_key_outside_a_job_is_a_no_op():
    """So a job body stays callable directly from a test, without the
    queue underneath it."""
    job_queue._reset_for_tests()
    job_queue.set_estimate_key("nothing_is_running")
