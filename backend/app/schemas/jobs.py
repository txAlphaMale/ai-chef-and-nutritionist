"""Backlog B11.1 (2026-08-01): response shapes for the background job
queue (job_queue.py). Every AI-consuming endpoint that used to return its
result directly now returns a JobEnqueuedResponse immediately; the
frontend polls GET /api/jobs/{job_id} for a JobRead until status is
"done" or "error", then reads `result` -- the exact same JSON shape that
endpoint used to return synchronously, so existing frontend state-
population code barely needs to change, only *how* it gets there."""

from __future__ import annotations

from pydantic import BaseModel


class JobEnqueuedResponse(BaseModel):
    job_id: str
    status: str = "queued"
    # False when a duplicate in-flight submission was coalesced into an
    # already-queued/running job rather than a new one being started --
    # lets the frontend show "already in progress" instead of implying a
    # second, separate job was created.
    created: bool = True


class RunningJobSummary(BaseModel):
    id: str
    kind: str
    label: str


class JobProgress(BaseModel):
    elapsed_seconds: float
    typical_seconds: float | None = None
    pct_of_typical: float | None = None
    over_typical: bool = False
    # True once a job has been running longer than any generation
    # plausibly should (job_queue.STALLED_AFTER_SECONDS). The job is not
    # killed -- this exists so the UI can say "this looks stuck, the
    # Ollama host may be unreachable" instead of showing an indefinite
    # spinner that looks identical to normal progress.
    stalled: bool = False


class JobRead(BaseModel):
    id: str
    kind: str
    label: str
    status: str  # queued|running|done|error
    submitted_at: float
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    result: dict | None = None


class JobListResponse(BaseModel):
    queued: int
    running: RunningJobSummary | None = None
    progress: JobProgress | None = None
    jobs: list[JobRead]
