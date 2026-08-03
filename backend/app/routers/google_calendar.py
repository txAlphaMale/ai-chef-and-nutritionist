"""Backlog B12.1: Google Calendar OAuth connect/disconnect/status and a
manual "force resync" trigger. The actual per-entry sync logic lives in
google_calendar_service.py; the automatic sync-on-change calls into it
live alongside the meal-plan mutations themselves in routers/meal_plan.py
(create/update/skip/delete), not here -- this router is just the
connection-management surface a household visits once (or rarely), not
part of the meal-plan request path.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.schemas.jobs import JobEnqueuedResponse
from app.schemas.system import SyncEnabledUpdate
from app.services import google_calendar_service, job_queue

router = APIRouter(prefix="/api/calendar/google", tags=["calendar"])


@router.get("/status")
def get_status(db: Session = Depends(get_db)):
    return google_calendar_service.connection_status(db)


@router.get("/authorize")
def authorize(return_to: str, db: Session = Depends(get_db)):
    """Returns the Google consent-screen URL as JSON rather than issuing
    a server-side redirect itself -- the frontend fetches this first,
    THEN sets window.location to the returned URL to actually navigate.

    This is deliberate, not just a style choice: a raw server redirect
    means any failure here (not configured, a bad client id) shows up as
    a full-page navigation to a bare FastAPI JSON error blob with none of
    Chef's own styling -- easy to misread as "nothing happened" (this is
    the exact bug an author screenshot reported: clicking Connect gave no
    visible feedback). Fetching first lets the frontend catch a 400 the
    normal way and show it inline via the same gcalError state every
    other action on this card already uses, and only ever navigates the
    browser away on a real, working URL.

    `return_to` is the frontend's own origin (window.location.origin),
    captured so the callback below can send the browser back to
    whichever device actually initiated this, not a hardcoded address."""
    try:
        url = google_calendar_service.build_authorization_url(db, return_to)
    except google_calendar_service.GoogleCalendarError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return {"authorize_url": url}


@router.get("/callback")
def callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    """Google redirects here after the consent screen. Resolves `state`
    back to the originating frontend origin and bounces the browser
    there with a query flag the Settings page reads to show a result --
    falling back to a plain HTML message if `state` is missing/expired
    (e.g. the user sat on Google's consent screen past the 10-minute
    pending-state TTL) rather than guessing at an address to redirect to."""
    pending = google_calendar_service.resolve_pending_state(state) if state else None
    return_to = pending["return_to"] if pending else None

    if error:
        message = f"Google Calendar connection was not completed: {error}"
    elif not code:
        message = "Google Calendar connection failed: no authorization code was returned."
    else:
        try:
            google_calendar_service.exchange_code_for_tokens(db, code)
            message = None  # success
        except google_calendar_service.GoogleCalendarError as e:
            message = str(e)

    if return_to:
        if message:
            from urllib.parse import quote

            return RedirectResponse(f"{return_to}/#/settings?google_calendar=error&message={quote(message)}")
        return RedirectResponse(f"{return_to}/#/settings?google_calendar=connected")

    # No known return address -- show something readable rather than a
    # blank redirect to nowhere.
    body = message or "Google Calendar is connected. You can close this tab and return to Chef."
    return HTMLResponse(f"<html><body><p>{body}</p></body></html>")


@router.post("/disconnect")
def disconnect(db: Session = Depends(get_db)):
    google_calendar_service.disconnect(db)
    return google_calendar_service.connection_status(db)


@router.patch("/sync-enabled")
def set_sync_enabled(payload: SyncEnabledUpdate, db: Session = Depends(get_db)):
    try:
        status = google_calendar_service.set_sync_enabled(db, payload.enabled)
    except google_calendar_service.GoogleCalendarError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    # Turning sync ON is the moment a household most wants "and now make
    # it match reality" -- auto-trigger a resync rather than requiring a
    # second, separate click to notice nothing has pushed yet.
    if payload.enabled:
        job_queue.enqueue("google_calendar_sync", "Google Calendar resync", _resync_job)
    return status


def _resync_job() -> dict:
    db = SessionLocal()
    try:
        return google_calendar_service.resync_all(db)
    finally:
        db.close()


@router.post("/resync", response_model=JobEnqueuedResponse, status_code=202)
def resync(db: Session = Depends(get_db)):
    if not google_calendar_service.is_connected(db):
        raise HTTPException(status_code=400, detail="Google Calendar is not connected.")
    job_id, created = job_queue.enqueue("google_calendar_sync", "Google Calendar resync", _resync_job)
    return JobEnqueuedResponse(job_id=job_id, created=created)
