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

app.add_middleware(
    CORSMiddleware,
    # Backlog B10.2 (2026-08-01): switched from allow_origins=["*"] to
    # allow_origin_regex -- the two are NOT equivalent once credentials
    # are involved. Starlette's CORSMiddleware echoes back the literal
    # string "*" for allow_origins=["*"] on ordinary (non-preflight)
    # responses even with allow_credentials=True, which is exactly the
    # invalid "Allow-Origin: * together with Allow-Credentials: true"
    # combination browsers refuse to honor for credentialed requests --
    # confirmed live via curl before this fix (the OPTIONS preflight
    # correctly reflected the request Origin; the actual GET response
    # did not). A regex match, by contrast, always echoes the SPECIFIC
    # request Origin, which is what the new session-cookie auth gate
    # (see auth_service.py/routers/auth.py) needs to actually receive
    # its cookie back cross-origin in the production deployment (backend
    # and frontend on different ports -- see api.js).
    #
    # Author-reported 2026-08-03: the browser no longer talks to this
    # origin directly at all in normal use -- frontend/server.js reverse-
    # proxies /api/* and /health to here over the internal Docker
    # network, so the frontend's own origin is what the browser actually
    # sees, and this CORS config only still matters for someone hitting
    # the backend's own port directly (advanced/scripting use, or a
    # deployment that hasn't rebuilt the frontend image yet). Left
    # permissive rather than tightened to a concrete origin list, since
    # narrowing it now would break exactly that direct-access case for no
    # benefit -- nothing routed through the proxy is a "cross-origin
    # browser request" in the first place, so CORS headers are moot for
    # it either way.
    allow_origin_regex=".*",
    allow_credentials=True,
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
        with open(path, "r") as f:
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
    try:
        os.chmod(tmp, 0o600)
    except Exception:
        pass
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
