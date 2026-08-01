"""Backlog B11.1 (2026-08-01): read-only status endpoints for the
background job queue (job_queue.py). No auth-exempt special-casing needed
here beyond whatever the standing auth_gate middleware already applies
uniformly to every /api/* route."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.schemas.jobs import JobListResponse, JobRead
from app.services import job_queue

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=JobListResponse)
def list_jobs():
    """Polled by the persistent header badge (App.jsx, mounted outside
    <Routes> so it's visible from every page) roughly every few seconds
    -- cheap, in-memory, no DB hit."""
    return job_queue.list_jobs()


@router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: str):
    """Polled by whichever page enqueued this specific job (or a page
    that re-mounted and found a matching job_id still saved in
    localStorage) until status is "done" or "error"."""
    job = job_queue.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
