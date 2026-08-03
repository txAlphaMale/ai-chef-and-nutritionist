"""Backlog B9.4 (via the author-requested B10.2 group, 2026-08-01): a
lightweight, OPT-IN single-shared-password gate for the whole app.

Scoped down from Fiduciary's own auth.py after checking directly with
the author (see PROJECT-PLAN.md's B10.2 notes section). Fiduciary's
system is genuinely multi-user (admin/viewer roles), TOTP MFA with
single-use backup codes, a recovery-key flow, rate-limited login, and a
host-CLI recovery/wipe tool -- built to protect real brokerage
credentials and portfolio data. Chef has no financial data behind it,
and this backlog item's own original text only ever asked for "optional
single-password gating." Porting the full system would be substantial
new security-review-worthy surface area disproportionate to a household
meal-planning app's actual risk profile -- the author confirmed the
lightweight scope directly rather than this being assumed.

What IS ported near-verbatim, because it's genuinely domain-neutral and
cheap insurance regardless of scope: the in-process sliding-window login
rate limiter (same shape as Fiduciary's auth.rate_limited/
record_attempt).

What's built new for Chef's simpler model (no per-user accounts, since
there is exactly one shared password, not accounts to distinguish): a
session-cookie gate via Starlette's SessionMiddleware (ships with
FastAPI/Starlette already, no new dependency) instead of re-deriving
Fiduciary's own request.session usage from scratch -- see app/main.py
for where the cookie/session machinery and the actual request-gating
middleware live. Auth defaults OFF (`is_enabled()` is false until a
password is explicitly set) so every existing Chef install keeps
working completely unchanged after this ships; a household opts in from
Settings -> Security.

Storage note: the enabled flag and password hash deliberately do NOT go
through settings_service.SETTING_SPECS/AppSetting's generic
PATCH /api/system/settings/{key} path. That endpoint is reachable by any
caller who already has API access, and letting it ALSO double as a way
to silently overwrite the password hash or flip auth on/off outside the
dedicated, more carefully-reasoned endpoints in routers/auth.py (which
require the CURRENT password before changing or disabling it) would be
a real, if narrow, security smell. Both fields live in the same
AppSetting table Chef already has, under key names never registered in
SETTING_SPECS -- settings_service.is_known_key() correctly refuses them,
so they can never appear in GET /api/system/settings or be written via
its PATCH."""

from __future__ import annotations

import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerifyMismatchError
from sqlalchemy.orm import Session

from app.models import AppSetting

_ph = PasswordHasher()

_ENABLED_KEY = "__auth_enabled"
_HASH_KEY = "__auth_password_hash"

LOGIN_RATE_LIMIT_PER_MIN = 8


def _get_raw(db: Session, key: str) -> str | None:
    row = db.query(AppSetting).filter_by(key=key).first()
    return row.value if row else None


def _set_raw(db: Session, key: str, value: str) -> None:
    row = db.query(AppSetting).filter_by(key=key).first()
    if row is None:
        db.add(AppSetting(key=key, value=value, is_secret=True))
    else:
        row.value = value
    db.commit()


# Cached because main.py's auth_gate middleware calls is_enabled() on
# EVERY /api/* request -- including the 1.5-second job poll and the jobs
# badge poll -- and it did so by opening a fresh SQLAlchemy session and
# hitting the database each time, from inside an `async` middleware. That
# is blocking I/O on the event loop, per request, to read a value that
# changes only when a household sets or clears its password.
#
# Invalidated explicitly by the two functions that can change it
# (set_password, disable) rather than by a time-based expiry: this is a
# single-process app, so an explicit invalidation is exact, and a TTL
# would mean a window where the gate is wrong in one direction or the
# other.
_enabled_cache: bool | None = None


def invalidate_enabled_cache() -> None:
    """Called by anything that changes whether the gate is on."""
    global _enabled_cache
    _enabled_cache = None


def enabled_cache_is_warm() -> bool:
    """Lets a caller skip opening a database session it would only need
    on a cold cache -- see main.py's auth_gate middleware."""
    return _enabled_cache is not None


def is_enabled(db: Session | None) -> bool:
    """`db` may be None ONLY when the cache is already warm (check with
    `enabled_cache_is_warm`), which is how the per-request middleware
    avoids opening a session it doesn't need."""
    global _enabled_cache
    if _enabled_cache is None:
        if db is None:
            raise ValueError("is_enabled() needs a database session on a cold cache")
        _enabled_cache = _get_raw(db, _ENABLED_KEY) == "true"
    return _enabled_cache


def is_configured(db: Session) -> bool:
    """True once a password has actually been set at some point --
    distinct from is_enabled() in case that distinction ever matters to
    a caller (e.g. showing different first-run copy)."""
    return bool(_get_raw(db, _HASH_KEY))


def set_password(db: Session, password: str) -> None:
    """Sets (or changes) the shared password and turns the gate on.
    Callers needing "require the current password first" semantics for
    an already-enabled gate implement that check themselves before
    calling this (see routers/auth.py's set_password endpoint) -- this
    function has no notion of "who is calling," same convention as
    every other service-layer function in this app."""
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    _set_raw(db, _HASH_KEY, _ph.hash(password))
    _set_raw(db, _ENABLED_KEY, "true")
    invalidate_enabled_cache()


def disable(db: Session) -> None:
    """Turns the gate off AND clears the stored hash -- re-enabling
    later always means setting a fresh password, rather than leaving a
    disabled-but-still-present hash sitting in the database."""
    _set_raw(db, _ENABLED_KEY, "false")
    _set_raw(db, _HASH_KEY, "")
    invalidate_enabled_cache()


def verify_password(db: Session, password: str) -> bool:
    stored = _get_raw(db, _HASH_KEY)
    if not stored or not password:
        return False
    try:
        _ph.verify(stored, password)
        return True
    except (VerifyMismatchError, InvalidHash):
        return False
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Rate limiting -- ported near-verbatim from Fiduciary's auth.py
# (rate_limited/record_login_attempt): a simple in-process sliding
# window, one independent bucket per client IP. Deliberately not backed
# by a shared store (Redis etc.) -- this is a single-instance local app,
# so in-memory state is fine and resets harmlessly on restart.
# ---------------------------------------------------------------------------
_login_attempts: dict[str, list[float]] = {}


def rate_limited(ip: str, limit: int = LOGIN_RATE_LIMIT_PER_MIN) -> bool:
    now = time.time()
    hist = [t for t in _login_attempts.get(ip, []) if now - t < 60]
    _login_attempts[ip] = hist
    return len(hist) >= limit


def record_attempt(ip: str) -> None:
    _login_attempts.setdefault(ip, []).append(time.time())
