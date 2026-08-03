"""In-process background job queue for Ollama-backed AI operations.

Backlog B11.1 (2026-08-01, author-reported): a direct architectural port
of the Fiduciary project's serial job queue (portfolio-api/app.py's
_JOBS/_JOB_Q/_job_worker, read in full before writing anything here, per
this project's own "read Fiduciary's actual implementation, don't assume
its shape" discipline already applied for B10.2's auth port). Two design
choices are carried over deliberately, not reinvented:

- SERIAL, not parallel (exactly one job runs at a time, via a single
  daemon worker thread pulling off a stdlib queue.Queue). This app's
  Ollama instance runs on the author's own dual-GTX-1080-Ti box. Running
  two generations concurrently against the same local Ollama server
  doesn't meaningfully parallelize the work -- it splits one GPU/VRAM
  budget between both calls, slowing both down, and risks an OOM
  depending on model size. Fiduciary made the identical call for the
  identical reason (a single local GPU budget shared across many heavy
  jobs) and it applies here without modification. If a future author
  session adds a second Ollama instance or confirms the hardware handles
  concurrent generations well, `_WORKER_COUNT` below is the one knob to
  revisit -- deliberately not built as configurable yet, since a second
  worker thread pulling from the same queue would need to be measured
  against real hardware first, not assumed safe.
- IN-MEMORY, not a DB table. Job status (queued/running/done/error,
  timestamps, result) is transient operational state, not the durable
  business data those jobs eventually write -- inventory rows, recipes,
  chat messages -- which was already, and remains, DB-backed via each
  job's own result being confirmed through the SAME existing endpoints
  every other write in this app goes through. A job in flight during a
  literal container restart is lost and must be resubmitted either way
  (there is no realistic way to checkpoint "a receipt photo was 60%
  analyzed by the vision model"), so persisting the registry itself buys
  nothing. Mirrors Fiduciary's own explicit "cheap: in-memory only" note
  on its /api/jobs endpoint.

WHY THIS EXISTS AT ALL (the actual bug this fixes, found by reading the
code rather than assumed): several endpoints were declared `async def`
but called the `ollama` package's synchronous, blocking HTTP client
directly with no thread offload. A blocking call inside `async def`
freezes FastAPI's single-process event loop for the FULL duration of
that call -- during which nothing else can be served by the app at all,
not chat, not another page load, not even a health check. On this
author's hardware, a vision/receipt parse can run tens of seconds to a
couple of minutes, so the whole app appeared to hang or "die" for that
whole window, which is exactly what was reported. Routing every
Ollama-consuming endpoint through this queue's worker thread fixes that
structurally (the blocking call now happens on a background thread, never
on the event loop) while also delivering the other three things asked
for: a visible progress/queue-depth signal (GET /api/jobs), resilience to
navigating away and back (the frontend polls a durable job_id instead of
holding a single request open in page-local React state), and one
consistent queue shared by every AI feature so a chat message sent while
a receipt import is running gets queued instead of racing it for the
same GPU.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from collections.abc import Callable

_JOBS: dict[str, dict] = {}
_JOBS_ORDER: list[str] = []
_JOBS_LOCK = threading.Lock()
_WORKER_LOCK = threading.Lock()
_JOB_Q: queue.Queue[tuple[str, Callable[[], dict]]] = queue.Queue()

# Ring-buffer cap on the in-memory registry -- mirrors Fiduciary's
# JOBS_MAX, prevents unbounded growth over a long-running container's
# lifetime. Finished jobs age out oldest-first; this only trims the
# STATUS record, never anything already written to the database.
JOBS_MAX = 100


# How long a single job may run before the status board reports it as
# stalled. This does NOT kill the job -- Python cannot safely interrupt a
# thread mid-call, and the real bound on a runaway generation is the
# httpx timeout in ollama_client (`ollama_timeout_seconds`, default 600s).
# What this buys is honesty: without it, a wedged worker is
# indistinguishable from a slow one, and the UI shows a spinner forever
# with no explanation. Set comfortably above the default Ollama timeout
# so a normal slow generation never trips it.
STALLED_AFTER_SECONDS = 900.0


def _ensure_worker_alive() -> None:
    """Restarts the worker thread if it ever dies.

    `_worker` catches `Exception` around each job body, but not
    `BaseException` -- a MemoryError or a SystemExit raised inside a job
    would kill the thread and silently take every future AI operation in
    the app down with it, with the queue quietly accepting work nothing
    will ever run. Checked on every enqueue: cheap, and the only moment
    that matters."""
    global _worker_thread
    with _WORKER_LOCK:
        if _worker_thread is not None and _worker_thread.is_alive():
            return
        print("[job_queue] worker thread not alive -- starting a new one", flush=True)
        _worker_thread = threading.Thread(target=_worker, daemon=True, name="chef-job-worker")
        _worker_thread.start()


def enqueue(kind: str, label: str, fn: Callable[[], dict], dedup_key: str | None = None) -> tuple[str, bool]:
    """`fn` takes no arguments and returns a JSON-serializable dict (the
    job's eventual `result`). Each call site closes over whatever raw
    inputs it needs (bytes, strings, ids) -- NEVER a request-scoped `db`
    Session, which is not safe to share across threads; `fn` must open
    its own `SessionLocal()` internally and close it when done (see any
    router's job-body closure for the pattern).

    `dedup_key`, when given, coalesces a duplicate submission (e.g. the
    same chat session already has a reply in flight) into the existing
    queued/running job instead of starting a second one racing it for
    the same GPU -- mirrors Fiduciary's per-kind coalescing, generalized
    here since this app's jobs aren't a fixed small kind list the way
    Fiduciary's cron-scheduled jobs are.

    Returns (job_id, created) -- created=False means an existing
    queued/running job was returned instead of a new one being started."""
    with _JOBS_LOCK:
        if dedup_key is not None:
            for jid in _JOBS_ORDER:
                existing = _JOBS.get(jid)
                if existing and existing["dedup_key"] == dedup_key and existing["status"] in ("queued", "running"):
                    return jid, False
        jid = uuid.uuid4().hex[:12]
        _JOBS[jid] = {
            "id": jid,
            "kind": kind,
            "label": label,
            "dedup_key": dedup_key,
            "status": "queued",
            "submitted_at": time.time(),
            "started_at": None,
            "finished_at": None,
            "error": None,
            "result": None,
        }
        _JOBS_ORDER.append(jid)
        while len(_JOBS_ORDER) > JOBS_MAX:
            _JOBS.pop(_JOBS_ORDER.pop(0), None)
    _ensure_worker_alive()
    _JOB_Q.put((jid, fn))
    return jid, True


def get_job(job_id: str) -> dict | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        return dict(job) if job else None


def list_jobs() -> dict:
    """Shape mirrors Fiduciary's GET /api/jobs: queue depth, the
    currently-running job (if any), a rough progress/ETA estimate for it
    (elapsed time vs. the median duration of that job kind's last few
    completed runs -- None/None, not a fake percentage, until history
    exists), and the full recent-jobs list newest-first for a page that
    wants to show more than just the badge."""
    with _JOBS_LOCK:
        jobs = [dict(_JOBS[j]) for j in _JOBS_ORDER if j in _JOBS]
    queued = sum(1 for j in jobs if j["status"] == "queued")
    running_job = next((j for j in jobs if j["status"] == "running"), None)
    progress = None
    if running_job and running_job.get("started_at"):
        typical = _typical_seconds(running_job["kind"], jobs, exclude_id=running_job["id"])
        elapsed = time.time() - running_job["started_at"]
        progress = {
            "elapsed_seconds": round(elapsed),
            "typical_seconds": round(typical) if typical is not None else None,
            "pct_of_typical": round(elapsed / typical * 100) if typical else None,
            "over_typical": bool(typical and elapsed > typical * 1.5),
            # See STALLED_AFTER_SECONDS -- an honest "this has been running
            # far longer than any generation should" signal the UI can show,
            # instead of an indefinite spinner that looks identical to
            # normal progress.
            "stalled": elapsed > STALLED_AFTER_SECONDS,
        }
    return {
        "queued": queued,
        "running": (
            {"id": running_job["id"], "kind": running_job["kind"], "label": running_job["label"]}
            if running_job
            else None
        ),
        "progress": progress,
        "jobs": list(reversed(jobs)),
    }


def _typical_seconds(kind: str, jobs: list[dict], exclude_id: str | None = None, n: int = 5) -> float | None:
    """Median duration of the last `n` completed runs of this job kind --
    same "no per-kind instrumentation, reuse the history the registry
    already carries" approach as Fiduciary's _job_typical_seconds.
    Returns None until at least one prior run of this kind has finished,
    shown by callers as "no history yet" rather than a fabricated 0%."""
    durs = sorted(
        j["finished_at"] - j["started_at"]
        for j in jobs
        if j["kind"] == kind
        and j["id"] != exclude_id
        and j["status"] == "done"
        and j.get("started_at") is not None
        and j.get("finished_at") is not None
    )
    if not durs:
        return None
    durs = durs[-n:]
    mid = len(durs) // 2
    return durs[mid] if len(durs) % 2 else (durs[mid - 1] + durs[mid]) / 2.0


def _worker() -> None:
    """The one and only worker thread. Deliberately does not hold
    _JOBS_LOCK while `fn()` itself runs (only briefly, to look the job
    record up) -- same tradeoff Fiduciary's _job_worker makes. A reader
    (get_job/list_jobs) taking a shallow dict copy while this thread is
    mid-mutation could in principle observe a torn interleaving (e.g.
    status already "running" but started_at not yet set) for one exact
    instant; this is an accepted, cheap tradeoff for a status board that
    is not itself the source of truth for anything durable, not a
    correctness bug in the data the job eventually writes."""
    while True:
        jid, fn = _JOB_Q.get()
        job = _JOBS.get(jid)
        if job is None:
            continue
        job["status"] = "running"
        job["started_at"] = time.time()
        try:
            job["result"] = fn()
            job["status"] = "done"
        except Exception as exc:
            job["status"] = "error"
            job["error"] = str(exc)[:500]
        finally:
            job["finished_at"] = time.time()


_worker_thread: threading.Thread | None = None
_ensure_worker_alive()


def _reset_for_tests() -> None:
    """Test-only seam -- the registry is process-global module state
    (by design, same as Fiduciary's), so a test suite that enqueues jobs
    needs an explicit way to clear it between tests rather than assuming
    an empty registry. Never called from application code."""
    with _JOBS_LOCK:
        _JOBS.clear()
        _JOBS_ORDER.clear()
