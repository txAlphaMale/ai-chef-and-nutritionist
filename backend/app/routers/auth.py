"""Backlog B9.4 (via B10.2, 2026-08-01): endpoints for the lightweight
single-shared-password gate. See auth_service.py's module docstring for
the scope-down rationale, and app/main.py for the SessionMiddleware +
auth-gate middleware these endpoints rely on.

/status and /login are the two paths the auth-gate middleware always
lets through unauthenticated (see main.py's _AUTH_EXEMPT_PATHS) -- the
frontend needs /status reachable before it knows whether to show a
login screen at all, and /login obviously can't require being already
logged in. Every other endpoint here (/logout, /set-password, /disable)
requires an authenticated session already, enforced by the SAME gate
every other /api/* route goes through -- no per-route auth check
duplicated here, which is deliberately the discipline Fiduciary's own
auth.py docstring says its OWN predecessor got wrong (a token checked on
only 23 of 127 endpoints)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.services import auth_service

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


class SetPasswordRequest(BaseModel):
    password: str
    # Required only when the gate is already enabled -- see set_password
    # below. Optional here so the first-ever "turn auth on" call (no
    # password exists yet) doesn't need a placeholder value.
    current_password: str | None = None


class PasswordConfirmRequest(BaseModel):
    current_password: str


@router.get("/status")
def auth_status(request: Request, db: Session = Depends(get_db)):
    enabled = auth_service.is_enabled(db)
    return {
        "enabled": enabled,
        "configured": auth_service.is_configured(db),
        # When the gate is off, every request is treated as
        # "authenticated" for the frontend's purposes -- there's no
        # login state to be in. When it's on, reflect the actual session.
        "authenticated": bool(request.session.get("authenticated")) if enabled else True,
    }


@router.post("/login")
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    if auth_service.rate_limited(client_ip):
        raise HTTPException(status_code=429, detail="Too many login attempts. Wait a minute and try again.")
    auth_service.record_attempt(client_ip)
    if not auth_service.is_enabled(db) or not auth_service.verify_password(db, payload.password):
        raise HTTPException(status_code=401, detail="Incorrect password")
    request.session["authenticated"] = True
    return {"authenticated": True}


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"authenticated": False}


@router.post("/set-password")
def set_password(payload: SetPasswordRequest, request: Request, db: Session = Depends(get_db)):
    """Sets/changes the shared password and enables the gate. If it's
    already enabled, the caller must supply the CURRENT password --
    even though the session calling this is already authenticated --
    the lightweight equivalent of "re-type your current password to
    change it," so an already-open browser tab (or a hijacked session)
    can't silently swap the password to something the real household
    doesn't know."""
    if auth_service.is_enabled(db) and (
        not payload.current_password or not auth_service.verify_password(db, payload.current_password)
    ):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    try:
        auth_service.set_password(db, payload.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    request.session["authenticated"] = True
    return {"enabled": True}


@router.post("/disable")
def disable_auth(payload: PasswordConfirmRequest, db: Session = Depends(get_db)):
    """Requires the current password even though the caller must already
    be authenticated to reach this endpoint at all (the auth-gate
    middleware guarantees that) -- same "confirm with a password before
    a destructive security change" discipline as set_password's change
    path, since anyone with browser access to an already-open session
    could otherwise silently turn protection off with one click."""
    if not auth_service.verify_password(db, payload.current_password):
        raise HTTPException(status_code=401, detail="Current password is incorrect")
    auth_service.disable(db)
    return {"enabled": False}
