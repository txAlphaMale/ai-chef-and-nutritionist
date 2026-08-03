"""Chef app API entrypoint.

Phase 1 added the data layer; Phase 2 adds DB-backed settings/secrets
and the Ollama/Tavily service wrappers, surfaced here through the
read-only /api/system/* router. Inventory (Phase 3), recipes (Phase 4),
meal planning (Phase 5), and health/knowledge tracking (Phase 6)
followed; the chat router lands in Phase 7 -- see PROJECT-PLAN.md.

Backlog B10.2 (2026-08-01) added an opt-in session-cookie auth gate
(auth_service.py/routers/auth.py) -- SessionMiddleware below issues the
signed cookie, and the auth_gate middleware enforces it on every
/api/* route except the two the frontend needs reachable pre-login
(/api/auth/status, /api/auth/login). Deliberately a single, centralized
gate rather than a per-router dependency sprinkled onto each endpoint --
see auth_gate's own docstring for why that specific mistake (checked on
only a subset of endpoints) is the one this design avoids.
"""
import contextlib
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware

from app.database import SessionLocal, get_db
from app.models import HouseholdPreferences
from app.routers import (
    auth,
    chat,
    dining,
    google_calendar,
    health,
    household,
    icloud_calendar,
    inventory,
    jobs,
    kitchen,
    knowledge,
    meal_plan,
    recipes,
    system,
    tls,
)
from app.services import auth_service, tls_service


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    # Backlog B15.1 -- whatever certificate exists on disk right now is,
    # by definition, what run_server.py just chose to serve for this
    # process, so any earlier 'restart_required' flag left over from a
    # tls_service mutation is stale the instant we're actually running.
    # Mirrors Fiduciary's own app.py startup call to the same effect.
    tls_service.mark_applied()
    yield


app = FastAPI(title="Chef", version="0.1.0", lifespan=_lifespan)


def get_configured_cors_origins() -> list[str]:
    """Extra browser origins allowed to make credentialed API calls.

    Read once, at import, because Starlette's CORSMiddleware fixes its
    origin list when it is constructed -- editing the setting therefore
    needs a container restart, which the setting's own description says.
    Re-reading per request was the alternative and it would reintroduce
    exactly the per-request database hit audit P2-3 just removed from the
    auth gate.

    Falls back to the CORS_ALLOW_ORIGINS environment variable, then to
    nothing at all. Deliberately tolerant of the database being absent or
    unmigrated: this runs at import time, which on a first-ever boot can
    precede the migration, and an unreachable settings table must not
    stop the app from starting."""
    raw = os.environ.get("CORS_ALLOW_ORIGINS", "")
    try:
        from app.services import settings_service

        db = SessionLocal()
        try:
            raw = settings_service.get_setting(db, "cors_allow_origins") or raw
        finally:
            db.close()
    except Exception:
        pass  # unmigrated or unreachable DB -- env/empty is a safe answer
    return [origin.strip() for origin in raw.replace("\n", ",").split(",") if origin.strip()]

# CORS: no cross-origin credentialed access by default.
#
# The browser never talks to this origin. `frontend/server.js` reverse-
# proxies /api/* and /health over the internal Docker network, so every
# request the browser makes is same-origin with the frontend -- and CORS
# does not apply to same-origin requests at all. Allowing any origin on
# the internet to make credentialed requests to this API bought nothing
# and said, literally, that it was fine for them to do so.
#
# Non-browser clients (curl, scripts, an HTTP client in another language)
# are unaffected: CORS is a browser policy, enforced by the browser. A
# script hitting the backend port directly never sends an Origin header
# and never has one checked.
#
# The empty default is therefore correct for the normal deployment. The
# setting exists for the case where a browser page served from some OTHER
# origin genuinely needs to call this API -- e.g. reaching the host by a
# new name (`chef.lan`) while the frontend still knows itself by IP, or a
# separately-hosted dashboard. Adding an origin is a Settings edit, not a
# rebuild, because a household adding a local DNS name should not have to
# rebuild an image to keep working.
_extra_origins = get_configured_cors_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_extra_origins,
    # Only meaningful when an explicit origin is listed above. With the
    # default empty list this grants nothing -- Starlette will not emit
    # an Access-Control-Allow-Origin for an origin that is not on it.
    allow_credentials=bool(_extra_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)


def _load_or_create_session_secret() -> str:
    """Generated once and persisted alongside the Fernet key
    (secrets_crypto.py) and the future USDA/other keys -- same
    generate-on-first-run, 0600-permission, atomic-write spirit, kept as
    its own small helper here rather than importing secrets_crypto's
    private helper, since this signs SESSION cookies, a different kind
    of key than the Fernet key used for encrypting settings at rest."""
    path = os.environ.get("SESSION_SECRET_FILE", "/app/data/session_secret.key")
    try:
        with open(path) as f:
            existing = f.read().strip()
            if existing:
                return existing
    except FileNotFoundError:
        pass
    import secrets as _secrets

    key = _secrets.token_hex(32)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(key)
    with contextlib.suppress(Exception):
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return key


# Reachable without an authenticated session even when the gate is on --
# the frontend needs /status to even know whether to show a login
# screen, and /login obviously can't itself require being logged in.
# Every other /api/auth/* endpoint (logout/set-password/disable) is
# gated like everything else.
_AUTH_EXEMPT_PATHS = {"/api/auth/status", "/api/auth/login"}


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    """Centralized session-cookie enforcement for every /api/* route.
    When auth_service.is_enabled() is False (the default -- no existing
    install is affected until a household explicitly sets a password),
    this is a no-op passthrough for every request.

    Registered BEFORE SessionMiddleware is added below on purpose: a
    live curl test caught, rather than assumed, that Starlette's
    middleware stack makes the LAST-added middleware outermost (its
    request-phase logic runs first) -- registering this dispatch
    function first and adding SessionMiddleware second means
    SessionMiddleware's request-phase code (populating request.session)
    runs BEFORE this dispatch executes, which is required for
    `request.session.get(...)` below to work at all rather than raising
    "SessionMiddleware must be installed" (the exact error the first,
    reversed ordering produced)."""
    path = request.url.path
    if not path.startswith("/api/") or path in _AUTH_EXEMPT_PATHS:
        return await call_next(request)
    # `is_enabled` caches, so the session below is opened once per
    # process (and again after a password change), not once per request.
    # It used to open a fresh session and hit the database on EVERY
    # /api/* call -- blocking I/O on the event loop, at the frequency of
    # the 1.5-second job poll.
    if auth_service.enabled_cache_is_warm():
        enabled = auth_service.is_enabled(None)
    else:
        db = SessionLocal()
        try:
            enabled = auth_service.is_enabled(db)
        finally:
            db.close()
    if enabled and not request.session.get("authenticated"):
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    return await call_next(request)


app.add_middleware(SessionMiddleware, secret_key=_load_or_create_session_secret())

app.include_router(auth.router)
app.include_router(jobs.router)
app.include_router(dining.router)
app.include_router(system.router)
app.include_router(inventory.router)
app.include_router(recipes.router)
app.include_router(kitchen.router)
app.include_router(meal_plan.router)
app.include_router(household.router)
app.include_router(health.router)
app.include_router(knowledge.router)
app.include_router(chat.router)
app.include_router(google_calendar.router)
app.include_router(icloud_calendar.router)
app.include_router(tls.router)


@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    prefs = db.query(HouseholdPreferences).first()
    return {
        "status": "ok",
        "household_size": prefs.household_size if prefs else None,
    }
