"""Backlog B12.2: iCloud Calendar connect/disconnect/status and a manual
"force resync" trigger -- the CalDAV counterpart to routers/
google_calendar.py. No OAuth authorize/callback routes here (see
icloud_calendar_service.py's module docstring for why an app-specific
password needs none): the household saves their Apple ID/app-specific
password through the normal generic Settings form first, then this
router's `/connect` endpoint validates them by actually running
calendar discovery. The automatic sync-on-change calls live alongside
the meal-plan mutations themselves in routers/meal_plan.py, same
placement as the Google equivalent.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.schemas.jobs import JobEnqueuedResponse
from app.schemas.system import SyncEnabledUpdate
from app.services import icloud_calendar_service, job_queue

router = APIRouter(prefix="/api/calendar/icloud", tags=["calendar"])


@router.get("/status")
def get_status(db: Session = Depends(get_db)):
    return icloud_calendar_service.connection_status(db)


@router.post("/connect")
def connect(db: Session = Depends(get_db)):
    """Validates the Apple ID / app-specific password already saved via
    Settings by running real CalDAV discovery against them, then turns
    sync on. Returns a 400 with a readable message (bad credentials,
    nothing configured yet, a CalDAV error) rather than a bare 500."""
    try:
        return icloud_calendar_service.connect(db)
    except icloud_calendar_service.ICloudCalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/disconnect")
def disconnect(db: Session = Depends(get_db)):
    icloud_calendar_service.disconnect(db)
    return icloud_calendar_service.connection_status(db)


@router.patch("/sync-enabled")
def set_sync_enabled(payload: SyncEnabledUpdate, db: Session = Depends(get_db)):
    try:
        status = icloud_calendar_service.set_sync_enabled(db, payload.enabled)
    except icloud_calendar_service.ICloudCalendarError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if payload.enabled:
        job_queue.enqueue("icloud_calendar_sync", "iCloud Calendar resync", _resync_job)
    return status


def _resync_job() -> dict:
    db = SessionLocal()
    try:
        return icloud_calendar_service.resync_all(db)
    finally:
        db.close()


@router.post("/resync", response_model=JobEnqueuedResponse, status_code=202)
def resync(db: Session = Depends(get_db)):
    if not icloud_calendar_service.is_connected(db):
        raise HTTPException(status_code=400, detail="iCloud Calendar is not connected.")
    job_id, created = job_queue.enqueue("icloud_calendar_sync", "iCloud Calendar resync", _resync_job)
    return JobEnqueuedResponse(job_id=job_id, created=created)
