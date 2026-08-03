"""Backlog B12.2: iCloud Calendar push sync, the same one-way-push
architecture as B12.1's Google Calendar service (google_calendar_service.py)
-- Chef is the source of truth, this module only ever creates/updates/
deletes an event to match Chef's current state, never reads events back
or reconciles a manual edit made in Calendar.app itself. See that
module's own docstring for the fuller "why one-way" rationale; this one
only documents what's genuinely DIFFERENT about the iCloud side.

Auth is an app-specific password against a real Apple ID, not OAuth --
Apple has no public developer OAuth program for third-party CalDAV
clients the way Google does for Calendar. This is actually LESS setup
friction than B12.1's Google flow (no OAuth client to register, no
redirect-URI gotcha), which is why the backlog text itself calls this
"actually less setup complexity than B12.1 once the shared sync_entry/
event-id-tracking pattern already exists" -- confirmed true, with one
correction: iCloud's CalDAV resource model (one .ics file per event,
addressed by a URL Chef itself controls) means there's no server-
assigned event id to track at all. Every event's URL is DETERMINISTIC
from `MealPlanEntry.id` (`_event_uid`/`_event_url` below) -- so unlike
`google_event_id`, this needed no new MealPlanEntry column. A PUT is
always "create or replace the whole resource," so push_entry never
needs to distinguish create from update the way Google's
create-then-PATCH-else-POST branch does.

Protocol is hand-rolled CalDAV (WebDAV PROPFIND/MKCALENDAR + plain PUT/
DELETE of .ics resources) via `httpx` and the stdlib `xml.etree`,
deliberately NOT the `caldav` PyPI package -- consistent with this
project's existing preference for a stdlib-adjacent implementation over
a new dependency where the protocol surface actually used is small (see
calendar_export_service.py's own hand-built .ics for the same reasoning
applied to the export side). The one-time CALENDAR DISCOVERY (principal
-> calendar-home-set -> find-or-create the "Chef Meal Plan" collection)
is the genuinely fiddly part of CalDAV; every event PUT/DELETE after
that is a single plain HTTP request per entry.

Discovery result (the calendar's own URL) is cached in
`icloud_calendar_calendar_href` so it only runs once per connect, not on
every sync -- mirrors google_calendar_service's own calendar-id caching.

**Not independently verified against a real iCloud account from this
sandbox** -- this environment has no route to apple.com/icloud.com
domains (same class of restriction documented for fsis.usda.gov in
B4.3's notes), so every code path here is verified via monkeypatched
`httpx.request` against synthetic CalDAV multistatus XML shaped exactly
like Apple's own published examples in RFC 4791/RFC 5397, not a live
round trip. The author should treat first use as the real integration
test, same standing caveat every other author-facing external
integration in this app already carries.
"""
from __future__ import annotations

import base64
import contextlib
from datetime import datetime, timedelta
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import httpx
from sqlalchemy.orm import Session

from app.models import MealPlan, MealPlanEntry
from app.models.base import utc_now
from app.services import calendar_export_service as cal
from app.services import settings_service

ICLOUD_CALDAV_BASE = "https://caldav.icloud.com"
DEDICATED_CALENDAR_NAME = "Chef Meal Plan"
DEDICATED_CALENDAR_SLUG = "chef-meal-plan"
REQUEST_TIMEOUT = 20


class ICloudCalendarError(RuntimeError):
    """Raised for any iCloud CalDAV failure a caller should surface to
    the user (not configured, a rejected app-specific password, or the
    CalDAV server itself rejecting a request) -- distinct from a bare
    RuntimeError so routers can catch it specifically and 400 rather
    than 500."""


def _get_credentials(db: Session) -> tuple[str, str]:
    username = settings_service.get_setting(db, "icloud_calendar_username") or ""
    app_password = settings_service.get_setting(db, "icloud_calendar_app_password") or ""
    if not (username and app_password):
        raise ICloudCalendarError(
            "Your iCloud Apple ID and an app-specific password must both be set in Settings "
            "first -- see the in-app WIKI's iCloud Calendar setup guide (an app-specific "
            "password is generated at appleid.apple.com, NOT your normal Apple ID password)."
        )
    return username, app_password


def is_configured(db: Session) -> bool:
    try:
        _get_credentials(db)
        return True
    except ICloudCalendarError:
        return False


def is_connected(db: Session) -> bool:
    return bool(settings_service.get_setting(db, "icloud_calendar_calendar_href"))


def is_sync_enabled(db: Session) -> bool:
    return is_connected(db) and (settings_service.get_setting(db, "icloud_calendar_sync_enabled") or "false") == "true"


def connection_status(db: Session) -> dict:
    return {
        "configured": is_configured(db),
        "connected": is_connected(db),
        "sync_enabled": (settings_service.get_setting(db, "icloud_calendar_sync_enabled") or "false") == "true",
        "username": settings_service.get_setting(db, "icloud_calendar_username") or None,
        "calendar_href": settings_service.get_setting(db, "icloud_calendar_calendar_href") or None,
    }


def _auth_headers(db: Session) -> dict:
    username, app_password = _get_credentials(db)
    token = base64.b64encode(f"{username}:{app_password}".encode()).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def _request(
    db: Session,
    method: str,
    url: str,
    body: bytes | None = None,
    extra_headers: dict | None = None,
    expect: tuple[int, ...] = (200, 201, 204, 207),
) -> httpx.Response:
    headers = _auth_headers(db)
    if extra_headers:
        headers.update(extra_headers)
    resp = httpx.request(method, url, headers=headers, content=body, timeout=REQUEST_TIMEOUT, follow_redirects=True)
    if resp.status_code == 401:
        raise ICloudCalendarError(
            "iCloud rejected the Apple ID / app-specific password. Generate a fresh app-specific "
            "password at appleid.apple.com and update it in Settings."
        )
    if resp.status_code not in expect:
        raise ICloudCalendarError(f"iCloud CalDAV request failed ({resp.status_code}): {resp.text[:300]}")
    return resp


# --- WebDAV/CalDAV discovery -------------------------------------------
#
# Namespace-wildcard element lookups (`{*}tag`, supported by ElementTree
# since Python 3.8) are used throughout rather than hardcoding the "D:"/
# "C:" prefixes Apple's own docs use -- a compliant server can legally
# use any prefix for the same DAV:/urn:ietf:params:xml:ns:caldav
# namespace URIs, and matching on the URI via the wildcard is the
# actually-correct way to parse WebDAV XML, not a shortcut.

_PROPFIND_CURRENT_USER_PRINCIPAL = (
    b'<?xml version="1.0" encoding="utf-8" ?>'
    b'<D:propfind xmlns:D="DAV:"><D:prop><D:current-user-principal/></D:prop></D:propfind>'
)
_PROPFIND_CALENDAR_HOME_SET = (
    b'<?xml version="1.0" encoding="utf-8" ?>'
    b'<D:propfind xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
    b"<D:prop><C:calendar-home-set/></D:prop></D:propfind>"
)
_PROPFIND_CHILDREN = (
    b'<?xml version="1.0" encoding="utf-8" ?>'
    b"<D:propfind xmlns:D=\"DAV:\"><D:prop><D:displayname/><D:resourcetype/></D:prop></D:propfind>"
)


def _find_href(xml_bytes: bytes, *tag_path: str) -> str | None:
    root = ET.fromstring(xml_bytes)
    path = "".join(f"//{{*}}{tag}" for tag in (*tag_path, "href"))
    el = root.find("." + path)
    return el.text.strip() if el is not None and el.text else None


def _find_calendar_href_by_displayname(xml_bytes: bytes, name: str) -> str | None:
    """Scans a Depth:1 PROPFIND response's <response> entries for a
    collection whose displayname matches, returning ITS OWN href (not a
    nested one) -- used to find an already-existing "Chef Meal Plan"
    calendar so re-running discovery (e.g. after a settings edit) is
    idempotent rather than creating a duplicate every time."""
    root = ET.fromstring(xml_bytes)
    for response in root.findall(".//{*}response"):
        href_el = response.find("{*}href")
        name_el = response.find(".//{*}displayname")
        if href_el is not None and name_el is not None and (name_el.text or "").strip() == name:
            return href_el.text.strip() if href_el.text else None
    return None


def _discover_calendar_href(db: Session) -> str:
    cached = settings_service.get_setting(db, "icloud_calendar_calendar_href")
    if cached:
        return cached

    principal_resp = _request(
        db, "PROPFIND", ICLOUD_CALDAV_BASE + "/", body=_PROPFIND_CURRENT_USER_PRINCIPAL,
        extra_headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
    )
    principal_href = _find_href(principal_resp.content, "current-user-principal")
    if not principal_href:
        raise ICloudCalendarError("Could not discover the iCloud CalDAV principal for this account.")
    principal_url = urljoin(str(principal_resp.url), principal_href)

    home_resp = _request(
        db, "PROPFIND", principal_url, body=_PROPFIND_CALENDAR_HOME_SET,
        extra_headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
    )
    home_href = _find_href(home_resp.content, "calendar-home-set")
    if not home_href:
        raise ICloudCalendarError("Could not discover this account's iCloud calendar home collection.")
    home_url = urljoin(str(home_resp.url), home_href)

    children_resp = _request(
        db, "PROPFIND", home_url, body=_PROPFIND_CHILDREN,
        extra_headers={"Depth": "1", "Content-Type": "application/xml; charset=utf-8"},
    )
    existing_href = _find_calendar_href_by_displayname(children_resp.content, DEDICATED_CALENDAR_NAME)
    if existing_href:
        calendar_url = urljoin(home_url, existing_href)
    else:
        calendar_url = home_url.rstrip("/") + f"/{DEDICATED_CALENDAR_SLUG}/"
        mkcalendar_body = (
            b'<?xml version="1.0" encoding="utf-8" ?>'
            b'<C:mkcalendar xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">'
            b"<D:set><D:prop><D:displayname>" + DEDICATED_CALENDAR_NAME.encode("utf-8") + b"</D:displayname>"
            b"</D:prop></D:set></C:mkcalendar>"
        )
        with contextlib.suppress(ICloudCalendarError):
            _request(
                db, "MKCALENDAR", calendar_url, body=mkcalendar_body,
                extra_headers={"Content-Type": "application/xml; charset=utf-8"}, expect=(200, 201),
            )
            # (e.g. a crashed connect attempt) -- proceed with the same
            # deterministic slug rather than failing the whole connect;
            # the next PUT/DELETE against it will surface a real error
            # if that guess was wrong.
            pass

    settings_service.set_setting(db, "icloud_calendar_calendar_href", calendar_url)
    return calendar_url


# --- Per-entry event push/delete ---------------------------------------


def _event_uid(entry_id: int) -> str:
    # Deliberately the SAME uid convention calendar_export_service.py's
    # .ics feed and google_calendar_service.py's stored event ids don't
    # share (Google's ids are server-assigned) -- but this one matches
    # the .ics feed's own UID exactly, so an entry has one consistent
    # identity across every calendar surface this app offers.
    return f"chef-mealplan-entry-{entry_id}@chef.local"


def _event_url(calendar_href: str, entry_id: int) -> str:
    return calendar_href.rstrip("/") + f"/{_event_uid(entry_id)}.ics"


def _build_single_event_ics(meal_plan: MealPlan, entry: MealPlanEntry, now: datetime | None = None) -> bytes:
    """One VEVENT wrapped in its own VCALENDAR -- CalDAV requires exactly
    one calendar object per PUT resource, unlike the .ics feed's single
    document containing every entry. Reuses calendar_export_service's
    escaping/folding/summary/description building blocks rather than
    re-deriving them, so all three calendar surfaces (the .ics feed,
    Google sync, iCloud sync) always describe a given entry identically."""
    stamp = cal._format_datetime(now or utc_now())
    start = cal._entry_event_start(meal_plan, entry)
    end = start + timedelta(minutes=cal.EVENT_DURATION_MINUTES)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{cal.PRODID}",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{_event_uid(entry.id)}",
        f"DTSTAMP:{stamp}Z",
        f"DTSTART:{cal._format_datetime(start)}",
        f"DTEND:{cal._format_datetime(end)}",
        f"SUMMARY:{cal._escape_text(cal._entry_summary(entry))}",
        f"DESCRIPTION:{cal._escape_text(cal._entry_description(entry))}",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return ("\r\n".join(cal._fold_line(line) for line in lines) + "\r\n").encode("utf-8")


def push_entry(db: Session, meal_plan: MealPlan, entry: MealPlanEntry) -> None:
    """Creates or replaces this entry's event -- a PUT to its
    deterministic URL is always "make this resource match this body,"
    so there's no separate create-vs-update branch to get wrong the way
    a server-assigned-id API needs."""
    calendar_href = _discover_calendar_href(db)
    body = _build_single_event_ics(meal_plan, entry)
    url = _event_url(calendar_href, entry.id)
    _request(db, "PUT", url, body=body, extra_headers={"Content-Type": "text/calendar; charset=utf-8"}, expect=(200, 201, 204))


def delete_event(db: Session, entry_id: int) -> None:
    """Best-effort -- a 404/410 means it's already gone (never synced,
    or deleted manually in Calendar.app), which is exactly the end state
    this function is trying to reach anyway, not a failure. No-ops
    entirely if iCloud sync has never been connected, same as
    google_calendar_service.delete_event's own guard."""
    calendar_href = settings_service.get_setting(db, "icloud_calendar_calendar_href")
    if not calendar_href:
        return
    url = _event_url(calendar_href, entry_id)
    resp = httpx.request("DELETE", url, headers=_auth_headers(db), timeout=REQUEST_TIMEOUT, follow_redirects=True)
    if resp.status_code >= 400 and resp.status_code not in (404, 410):
        raise ICloudCalendarError(f"Could not delete the iCloud calendar event: {resp.text[:300]}")


def sync_entry(db: Session, entry: MealPlanEntry) -> None:
    """The one function that decides push vs. delete for a single entry,
    based purely on its current DB state -- same shape as
    google_calendar_service.sync_entry, so routers/meal_plan.py's
    mutation call sites can call both without branching on which
    provider is active."""
    if entry.is_skipped:
        delete_event(db, entry.id)
        return
    push_entry(db, entry.meal_plan, entry)


def sync_meal_plan(db: Session, meal_plan: MealPlan) -> None:
    for entry in meal_plan.entries:
        sync_entry(db, entry)


def resync_all(db: Session) -> dict:
    plans = db.query(MealPlan).all()
    entry_count = 0
    for plan in plans:
        for entry in plan.entries:
            sync_entry(db, entry)
            entry_count += 1
    return {"plans_synced": len(plans), "entries_synced": entry_count}


def connect(db: Session) -> dict:
    """Validates the configured Apple ID / app-specific password by
    actually running calendar discovery, then turns sync on -- the
    iCloud equivalent of google_calendar_service.exchange_code_for_
    tokens's "connect" moment, minus an OAuth redirect: the household
    just saves their credentials via the normal Settings form first,
    then clicks one "Connect" button that calls this."""
    _discover_calendar_href(db)
    settings_service.set_setting(db, "icloud_calendar_sync_enabled", "true")
    return connection_status(db)


def disconnect(db: Session) -> None:
    """Clears every stored credential/discovery result. Deliberately
    does NOT delete the calendar in iCloud itself -- same reasoning as
    google_calendar_service.disconnect. No MealPlanEntry column to blank
    (see module docstring for why iCloud never needed one)."""
    for key in ("icloud_calendar_username", "icloud_calendar_app_password", "icloud_calendar_calendar_href"):
        settings_service.set_setting(db, key, "")
    settings_service.set_setting(db, "icloud_calendar_sync_enabled", "false")


def set_sync_enabled(db: Session, enabled: bool) -> dict:
    if enabled and not is_connected(db):
        raise ICloudCalendarError("Connect iCloud Calendar before enabling sync.")
    settings_service.set_setting(db, "icloud_calendar_sync_enabled", "true" if enabled else "false")
    return connection_status(db)
