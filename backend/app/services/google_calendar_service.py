"""Backlog B12.1: one-way push sync of a household's meal plan into a
dedicated Google Calendar ("Chef Meal Plan"), auto-created on first
connect. Chef is the source of truth -- this module only ever pushes
(create/update/delete an event to match Chef's current state), never
reads events back or reconciles a manual edit made in Google Calendar
itself. That keeps this deliberately simpler than a two-way sync (no
conflict resolution to get wrong) and sidesteps the fact that Google
webhooks require a publicly reachable HTTPS callback URL this app has
no reason to assume exists on a home LAN deployment.

Explored the household's existing Open WebUI + `workspace-mcp` +
`mcpo` setup (a separate, already-working Google Calendar integration
in a sibling "Local AI" project) before building this, rather than
guessing at the shape from scratch. Deliberately NOT reused directly:
that stack exists to expose Calendar operations as LLM tool-calls
inside Open WebUI, a different problem (an agent deciding which
operation to call) than this module's (deterministic sync whenever a
meal plan changes) -- routing through it would add a live runtime
dependency on a whole separate docker-compose project most people
cloning THIS repo will never have running, which conflicts directly
with Chef's own "clone and `docker compose up`" distributability goal.
Plain REST calls against Google's stable Calendar v3 API (via `httpx`,
already a dependency) is less code, no new dependency, and has zero
external runtime dependency beyond Google itself -- consistent with
this app's existing preference for direct, minimal integrations (see
calendar_export_service.py's hand-built .ics for the same reasoning
applied to a different feature).

Bring-your-own-OAuth-client, same trust model as every other
integration in this app (Tavily, USDA FDC): Google gives no way for a
self-hosted, non-Google-verified app to avoid this, so the household
registers their own OAuth client in their own Google Cloud project
(walkthrough: in-app WIKI -> Getting started -> Google Calendar
setup) and pastes the client id/secret into Settings. Nothing here is
shared or bundled.

Auth flow shape: standard OAuth 2.0 authorization-code grant against a
"Web application" type client (not the "Desktop app"/loopback-PKCE
style the Local AI stack's workspace-mcp uses) -- deliberately, since
this app can be reached from several different devices/addresses on a
LAN (the machine running the backend, a phone, an iPad), and only a
Web-application client lets an explicit, stable redirect URI be
registered that works regardless of which device initiated the
"Connect" click. `access_type=offline&prompt=consent` on every
authorize request guarantees a refresh token comes back even on a
household's second/third connect attempt (Google otherwise only
issues one on a truly first-ever consent). The `state` parameter
carries a one-time, server-held pointer back to whichever frontend
origin actually initiated the flow (module-level `_PENDING_STATES`,
same "cheap in-memory, not durable business data" tradeoff job_queue.py
already documents for its own registry) so the callback can bounce the
browser back to the right device's Settings page rather than assuming
a fixed frontend address.
"""

from __future__ import annotations

import secrets
import time
from datetime import timedelta
from urllib.parse import urlencode

import httpx
from sqlalchemy.orm import Session

from app.models import MealPlan, MealPlanEntry
from app.services import calendar_export_service as cal
from app.services import settings_service

GOOGLE_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_ENDPOINT = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_CALENDAR_API_BASE = "https://www.googleapis.com/calendar/v3"

CALENDAR_SCOPE = "https://www.googleapis.com/auth/calendar"
DEDICATED_CALENDAR_SUMMARY = "Chef Meal Plan"
DEDICATED_CALENDAR_DESCRIPTION = (
    "Managed automatically by Chef -- meal-plan events are pushed here on "
    "every plan change. Safe to delete; Chef will create a fresh one on the "
    "next sync (any links to the old calendar's events go stale and are "
    "recreated, not restored)."
)
REQUEST_TIMEOUT = 20

# Module-global, in-memory, deliberately not persisted -- same tradeoff
# job_queue.py's own registry documents (transient operational state,
# not durable business data). A lost cache/pending-state entry across a
# container restart just means: the access token gets refreshed again
# (cheap), or an in-flight "Connect" click has to be retried (rare,
# user-visible, not data loss).
_ACCESS_TOKEN_CACHE: dict = {"token": None, "expires_at": 0.0}
_PENDING_STATES: dict[str, dict] = {}
_PENDING_STATE_TTL_SECONDS = 600  # 10 minutes -- generous for a human to click through Google's consent screen


class GoogleCalendarError(RuntimeError):
    """Raised for any Google Calendar integration failure a caller
    should surface to the user (not configured, not connected, or the
    Google API itself rejected a request) -- distinct from a bare
    RuntimeError so routers can catch it specifically and 400 rather
    than 500."""


def _get_client_config(db: Session) -> tuple[str, str, str]:
    client_id = settings_service.get_setting(db, "google_calendar_client_id") or ""
    client_secret = settings_service.get_setting(db, "google_calendar_client_secret") or ""
    redirect_uri = settings_service.get_setting(db, "google_calendar_redirect_uri") or ""
    if not (client_id and client_secret and redirect_uri):
        raise GoogleCalendarError(
            "Google Calendar client ID, client secret, and redirect URI must all be set in "
            "Settings first -- see the in-app WIKI's Google Calendar setup guide."
        )
    return client_id, client_secret, redirect_uri


def is_configured(db: Session) -> bool:
    try:
        _get_client_config(db)
        return True
    except GoogleCalendarError:
        return False


def is_connected(db: Session) -> bool:
    return bool(
        settings_service.get_setting(db, "google_calendar_refresh_token")
        and settings_service.get_setting(db, "google_calendar_calendar_id")
    )


def is_sync_enabled(db: Session) -> bool:
    """Cheap, request-scoped check callers use to decide whether to
    enqueue a sync job at all -- keeps the common "never connected"
    case a fast no-op with zero job-queue overhead."""
    return is_connected(db) and (settings_service.get_setting(db, "google_calendar_sync_enabled") or "false") == "true"


def connection_status(db: Session) -> dict:
    return {
        "configured": is_configured(db),
        "connected": is_connected(db),
        "sync_enabled": (settings_service.get_setting(db, "google_calendar_sync_enabled") or "false") == "true",
        "account_email": settings_service.get_setting(db, "google_calendar_account_email") or None,
        "calendar_id": settings_service.get_setting(db, "google_calendar_calendar_id") or None,
        "calendar_html_link": (
            f"https://calendar.google.com/calendar/r?cid={settings_service.get_setting(db, 'google_calendar_calendar_id')}"
            if settings_service.get_setting(db, "google_calendar_calendar_id")
            else None
        ),
    }


def _prune_pending_states() -> None:
    cutoff = time.time() - _PENDING_STATE_TTL_SECONDS
    stale = [s for s, v in _PENDING_STATES.items() if v["created_at"] < cutoff]
    for s in stale:
        _PENDING_STATES.pop(s, None)


def build_authorization_url(db: Session, return_to: str) -> str:
    """`return_to` is the frontend origin (e.g. http://10.11.24.21:5173)
    that initiated the connect click -- captured via `state` so the
    callback can send the browser back to the right device regardless
    of which one was used to click "Connect"."""
    client_id, _client_secret, redirect_uri = _get_client_config(db)
    _prune_pending_states()
    state = secrets.token_urlsafe(24)
    _PENDING_STATES[state] = {"return_to": return_to, "created_at": time.time()}
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": CALENDAR_SCOPE,
        "access_type": "offline",
        "prompt": "consent",  # forces a refresh_token on every connect, not just the very first ever
        "state": state,
    }
    return f"{GOOGLE_AUTH_ENDPOINT}?{urlencode(params)}"


def resolve_pending_state(state: str) -> dict | None:
    """One-time use -- pops so a replayed/guessed state can't be reused."""
    _prune_pending_states()
    return _PENDING_STATES.pop(state, None)


def exchange_code_for_tokens(db: Session, code: str) -> dict:
    """Completes the OAuth handshake: exchanges the authorization code
    for tokens, stores the refresh token, resolves and stores the
    connected account's email, and ensures the dedicated calendar
    exists -- then turns sync on by default (the household came here to
    connect it; an extra click to also enable it would just be friction
    for the common case, and it's one click to turn back off)."""
    client_id, client_secret, redirect_uri = _get_client_config(db)
    resp = httpx.post(
        GOOGLE_TOKEN_ENDPOINT,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise GoogleCalendarError(f"Google rejected the authorization code: {resp.text[:300]}")
    data = resp.json()
    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    if not access_token:
        raise GoogleCalendarError("Google did not return an access token.")
    if not refresh_token:
        # Happens if a household already granted access before and Google
        # decided not to resend one despite prompt=consent -- rare, but
        # possible if consent was revoked oddly. Tell the user exactly
        # what to do rather than silently leaving them half-connected.
        raise GoogleCalendarError(
            "Google did not return a refresh token. Revoke Chef's access at "
            "https://myaccount.google.com/permissions and try connecting again."
        )
    _ACCESS_TOKEN_CACHE["token"] = access_token
    _ACCESS_TOKEN_CACHE["expires_at"] = time.time() + float(data.get("expires_in", 3600)) - 60

    settings_service.set_setting(db, "google_calendar_refresh_token", refresh_token)

    email = _fetch_account_email(access_token)
    if email:
        settings_service.set_setting(db, "google_calendar_account_email", email)

    ensure_dedicated_calendar(db, access_token)
    settings_service.set_setting(db, "google_calendar_sync_enabled", "true")
    return connection_status(db)


def _fetch_account_email(access_token: str) -> str | None:
    resp = httpx.get(
        GOOGLE_USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}, timeout=REQUEST_TIMEOUT
    )
    if resp.status_code >= 400:
        return None
    return resp.json().get("email")


def ensure_dedicated_calendar(db: Session, access_token: str | None = None) -> str:
    """Idempotent -- returns the existing calendar id if one's already
    on file, otherwise creates the "Chef Meal Plan" calendar and stores
    its id. Only ever creates ONE per install; a household that deletes
    it in Google gets a fresh one on the next call that needs it (its
    stored id will start 404ing, which push_entry treats as "gone,
    recreate" -- see that function)."""
    existing = settings_service.get_setting(db, "google_calendar_calendar_id")
    if existing:
        return existing
    token = access_token or _get_access_token(db)
    resp = httpx.post(
        f"{GOOGLE_CALENDAR_API_BASE}/calendars",
        headers={"Authorization": f"Bearer {token}"},
        json={"summary": DEDICATED_CALENDAR_SUMMARY, "description": DEDICATED_CALENDAR_DESCRIPTION},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise GoogleCalendarError(f"Could not create the Chef Meal Plan calendar: {resp.text[:300]}")
    calendar_id = resp.json()["id"]
    settings_service.set_setting(db, "google_calendar_calendar_id", calendar_id)
    return calendar_id


def disconnect(db: Session) -> None:
    """Clears every stored credential/id and blanks every entry's
    google_event_id -- a future reconnect (possibly a different Google
    account, possibly a fresh calendar) starts clean rather than trying
    to PATCH event ids that belong to a now-disconnected account.
    Deliberately does NOT delete the calendar in Google itself -- the
    household's own calendar data shouldn't disappear just because Chef
    stopped managing it; they can delete it themselves if they want it
    gone."""
    for key in (
        "google_calendar_refresh_token",
        "google_calendar_calendar_id",
        "google_calendar_account_email",
    ):
        settings_service.set_setting(db, key, "")
    settings_service.set_setting(db, "google_calendar_sync_enabled", "false")
    _ACCESS_TOKEN_CACHE["token"] = None
    _ACCESS_TOKEN_CACHE["expires_at"] = 0.0
    db.query(MealPlanEntry).filter(MealPlanEntry.google_event_id.isnot(None)).update(
        {"google_event_id": None}, synchronize_session=False
    )
    db.commit()


def set_sync_enabled(db: Session, enabled: bool) -> dict:
    if enabled and not is_connected(db):
        raise GoogleCalendarError("Connect Google Calendar before enabling sync.")
    settings_service.set_setting(db, "google_calendar_sync_enabled", "true" if enabled else "false")
    return connection_status(db)


def _refresh_access_token(db: Session) -> str:
    client_id, client_secret, _redirect_uri = _get_client_config(db)
    refresh_token = settings_service.get_setting(db, "google_calendar_refresh_token")
    if not refresh_token:
        raise GoogleCalendarError("Google Calendar is not connected.")
    resp = httpx.post(
        GOOGLE_TOKEN_ENDPOINT,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise GoogleCalendarError(f"Could not refresh the Google access token: {resp.text[:300]}")
    data = resp.json()
    token = data["access_token"]
    _ACCESS_TOKEN_CACHE["token"] = token
    _ACCESS_TOKEN_CACHE["expires_at"] = time.time() + float(data.get("expires_in", 3600)) - 60
    return token


def _get_access_token(db: Session) -> str:
    if _ACCESS_TOKEN_CACHE["token"] and time.time() < _ACCESS_TOKEN_CACHE["expires_at"]:
        return _ACCESS_TOKEN_CACHE["token"]
    return _refresh_access_token(db)


def _event_body(meal_plan: MealPlan, entry: MealPlanEntry, tz_name: str) -> dict:
    """Reuses calendar_export_service's summary/description/start-time
    logic (same meal -> text, same day-of-week -> date, same per-meal-
    type default time) rather than re-deriving it -- the .ics feed and
    this push sync should always agree on what a given entry looks
    like. The one real difference: Google needs a genuine timezone
    (dateTime + timeZone) since these are real calendar invites synced
    across devices, unlike the .ics feed's deliberately "floating"
    times -- see calendar_export_service's module docstring for why
    that one stays floating."""
    start = cal._entry_event_start(meal_plan, entry)
    end = start + timedelta(minutes=cal.EVENT_DURATION_MINUTES)
    return {
        "summary": cal._entry_summary(entry),
        "description": cal._entry_description(entry),
        "start": {"dateTime": start.isoformat(), "timeZone": tz_name},
        "end": {"dateTime": end.isoformat(), "timeZone": tz_name},
    }


def push_entry(db: Session, meal_plan: MealPlan, entry: MealPlanEntry) -> str:
    """Creates or updates this entry's event so it matches Chef's
    current state, and stores the resulting event id back on the entry
    (caller commits). If a previously-stored event id 404s (the event
    or its whole calendar was deleted in Google itself), falls back to
    creating a fresh one rather than failing the whole sync -- Chef
    stays the source of truth, so "the calendar side drifted" is
    recovered from, not treated as an error."""
    calendar_id = ensure_dedicated_calendar(db)
    token = _get_access_token(db)
    headers = {"Authorization": f"Bearer {token}"}
    body = _event_body(meal_plan, entry, settings_service.get_setting(db, "household_timezone") or "America/Chicago")

    if entry.google_event_id:
        resp = httpx.patch(
            f"{GOOGLE_CALENDAR_API_BASE}/calendars/{calendar_id}/events/{entry.google_event_id}",
            headers=headers,
            json=body,
            timeout=REQUEST_TIMEOUT,
        )
        if resp.status_code in (404, 410):
            entry.google_event_id = None  # stale reference -- fall through to create
        elif resp.status_code >= 400:
            raise GoogleCalendarError(f"Could not update the calendar event: {resp.text[:300]}")
        else:
            return entry.google_event_id

    resp = httpx.post(
        f"{GOOGLE_CALENDAR_API_BASE}/calendars/{calendar_id}/events",
        headers=headers,
        json=body,
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400:
        raise GoogleCalendarError(f"Could not create the calendar event: {resp.text[:300]}")
    event_id = resp.json()["id"]
    entry.google_event_id = event_id
    return event_id


def delete_event(db: Session, event_id: str) -> None:
    """Best-effort -- a 404/410 means it's already gone (deleted
    manually in Google, or the calendar itself was recreated), which is
    exactly the end state this function is trying to reach anyway, not
    a failure."""
    calendar_id = settings_service.get_setting(db, "google_calendar_calendar_id")
    if not calendar_id:
        return
    token = _get_access_token(db)
    resp = httpx.delete(
        f"{GOOGLE_CALENDAR_API_BASE}/calendars/{calendar_id}/events/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=REQUEST_TIMEOUT,
    )
    if resp.status_code >= 400 and resp.status_code not in (404, 410):
        raise GoogleCalendarError(f"Could not delete the calendar event: {resp.text[:300]}")


def sync_entry(db: Session, entry: MealPlanEntry) -> None:
    """The one function that decides create/update vs. delete for a
    single entry, based purely on its current DB state -- callers
    (routers, the job-queue closures they enqueue) don't need to know
    the create/update/delete distinction themselves. Caller commits."""
    if entry.is_skipped:
        if entry.google_event_id:
            delete_event(db, entry.google_event_id)
            entry.google_event_id = None
        return
    push_entry(db, entry.meal_plan, entry)


def sync_meal_plan(db: Session, meal_plan: MealPlan) -> None:
    for entry in meal_plan.entries:
        sync_entry(db, entry)
    db.commit()


def resync_all(db: Session) -> dict:
    """Manual "force resync" button's target -- re-pushes every
    non-skipped entry across every meal plan in the database, and
    cleans up events for skipped ones. Useful after a long sync-disabled
    stretch, a client id/secret change, or general troubleshooting."""
    plans = db.query(MealPlan).all()
    entry_count = 0
    for plan in plans:
        for entry in plan.entries:
            sync_entry(db, entry)
            entry_count += 1
    db.commit()
    return {"plans_synced": len(plans), "entries_synced": entry_count}
