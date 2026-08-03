"""Unit tests for backlog B12.2's icloud_calendar_service.py: config
gating, calendar-href discovery (principal -> calendar-home-set ->
find-or-create the "Chef Meal Plan" collection), push/delete's
deterministic-URL create-or-replace behavior, and sync_entry's
skip-vs-push branching. Every CalDAV call is faked via a single
monkeypatched `httpx.request` (this module deliberately makes every
request through one function, `_request`, for exactly this reason) --
no real network access, same reasoning google_calendar_service's own
test file states for using plain httpx over a heavier SDK. XML fixtures
are shaped like Apple's own published CalDAV multistatus examples
(RFC 4791/RFC 5397), not guessed.
"""

from __future__ import annotations

import pytest

from app.models import MealPlan, MealPlanEntry
from app.services import icloud_calendar_service as icloud
from app.services import settings_service

_PRINCIPAL_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/</D:href>
    <D:propstat>
      <D:prop><D:current-user-principal><D:href>/1234567/principal/</D:href></D:current-user-principal></D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""

_HOME_SET_XML = b"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:" xmlns:C="urn:ietf:params:xml:ns:caldav">
  <D:response>
    <D:href>/1234567/principal/</D:href>
    <D:propstat>
      <D:prop><C:calendar-home-set><D:href>/1234567/calendars/</D:href></C:calendar-home-set></D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""

_CHILDREN_XML_NO_MATCH = b"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/1234567/calendars/home/</D:href>
    <D:propstat>
      <D:prop><D:displayname>Home</D:displayname></D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""

_CHILDREN_XML_WITH_MATCH = b"""<?xml version="1.0" encoding="utf-8"?>
<D:multistatus xmlns:D="DAV:">
  <D:response>
    <D:href>/1234567/calendars/chef-meal-plan/</D:href>
    <D:propstat>
      <D:prop><D:displayname>Chef Meal Plan</D:displayname></D:prop>
      <D:status>HTTP/1.1 200 OK</D:status>
    </D:propstat>
  </D:response>
</D:multistatus>"""


class _FakeResponse:
    def __init__(self, status_code=200, content=b"", text=None, url="https://caldav.icloud.com/"):
        self.status_code = status_code
        self.content = content
        self.text = text if text is not None else content.decode("utf-8", errors="replace")
        self.url = url


def _configure_credentials(db_session):
    settings_service.set_setting(db_session, "icloud_calendar_username", "chef@example.com")
    settings_service.set_setting(db_session, "icloud_calendar_app_password", "abcd-efgh-ijkl-mnop")


def test_is_configured_requires_both_username_and_password(db_session):
    assert icloud.is_configured(db_session) is False
    settings_service.set_setting(db_session, "icloud_calendar_username", "chef@example.com")
    assert icloud.is_configured(db_session) is False
    settings_service.set_setting(db_session, "icloud_calendar_app_password", "abcd-efgh-ijkl-mnop")
    assert icloud.is_configured(db_session) is True


def test_is_connected_requires_calendar_href(db_session):
    _configure_credentials(db_session)
    assert icloud.is_connected(db_session) is False
    settings_service.set_setting(
        db_session, "icloud_calendar_calendar_href", "https://caldav.icloud.com/1234567/calendars/chef-meal-plan/"
    )
    assert icloud.is_connected(db_session) is True


def test_discover_calendar_href_creates_new_calendar_when_none_exists(db_session, monkeypatch):
    _configure_credentials(db_session)
    calls = []

    def fake_request(method, url, headers=None, content=None, timeout=None, follow_redirects=None):
        calls.append((method, url))
        if method == "PROPFIND" and url.endswith("caldav.icloud.com/"):
            return _FakeResponse(207, _PRINCIPAL_XML, url="https://p12-caldav.icloud.com/")
        if method == "PROPFIND" and "principal" in url:
            return _FakeResponse(207, _HOME_SET_XML, url=url)
        if method == "PROPFIND" and url.endswith("/calendars/"):
            return _FakeResponse(207, _CHILDREN_XML_NO_MATCH, url=url)
        if method == "MKCALENDAR":
            return _FakeResponse(201, b"")
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(icloud.httpx, "request", fake_request)
    href = icloud._discover_calendar_href(db_session)
    assert href.endswith("/calendars/chef-meal-plan/")
    assert any(m == "MKCALENDAR" for m, _ in calls)
    # Cached -- a second call must not re-run discovery at all.
    calls.clear()
    href_again = icloud._discover_calendar_href(db_session)
    assert href_again == href
    assert calls == []


def test_discover_calendar_href_reuses_existing_calendar_by_displayname(db_session, monkeypatch):
    _configure_credentials(db_session)

    def fake_request(method, url, headers=None, content=None, timeout=None, follow_redirects=None):
        if method == "PROPFIND" and url.endswith("caldav.icloud.com/"):
            return _FakeResponse(207, _PRINCIPAL_XML, url="https://p12-caldav.icloud.com/")
        if method == "PROPFIND" and "principal" in url:
            return _FakeResponse(207, _HOME_SET_XML, url=url)
        if method == "PROPFIND" and url.endswith("/calendars/"):
            return _FakeResponse(207, _CHILDREN_XML_WITH_MATCH, url=url)
        if method == "MKCALENDAR":
            raise AssertionError("should not create a new calendar when one already exists")
        raise AssertionError(f"unexpected request: {method} {url}")

    monkeypatch.setattr(icloud.httpx, "request", fake_request)
    href = icloud._discover_calendar_href(db_session)
    assert "chef-meal-plan" in href


def test_discover_calendar_href_raises_readable_error_on_bad_credentials(db_session, monkeypatch):
    _configure_credentials(db_session)

    def fake_request(method, url, headers=None, content=None, timeout=None, follow_redirects=None):
        return _FakeResponse(401, b"", text="Unauthorized")

    monkeypatch.setattr(icloud.httpx, "request", fake_request)
    with pytest.raises(icloud.ICloudCalendarError, match="rejected"):
        icloud._discover_calendar_href(db_session)


def test_push_entry_puts_to_deterministic_url(db_session, monkeypatch):
    settings_service.set_setting(
        db_session, "icloud_calendar_calendar_href", "https://caldav.icloud.com/1234567/calendars/chef-meal-plan/"
    )
    _configure_credentials(db_session)
    plan = MealPlan(week_start_date=__import__("datetime").date(2026, 8, 3))
    entry = MealPlanEntry(id=42, day_of_week=0, meal_type="dinner", servings=2, meal_plan=plan)

    seen = {}

    def fake_request(method, url, headers=None, content=None, timeout=None, follow_redirects=None):
        seen["method"] = method
        seen["url"] = url
        seen["content"] = content
        return _FakeResponse(201, b"")

    monkeypatch.setattr(icloud.httpx, "request", fake_request)
    icloud.push_entry(db_session, plan, entry)
    assert seen["method"] == "PUT"
    assert seen["url"].endswith("chef-mealplan-entry-42@chef.local.ics")
    assert b"BEGIN:VEVENT" in seen["content"]
    assert b"UID:chef-mealplan-entry-42@chef.local" in seen["content"]


def test_delete_event_treats_404_as_success(db_session, monkeypatch):
    settings_service.set_setting(
        db_session, "icloud_calendar_calendar_href", "https://caldav.icloud.com/1234567/calendars/chef-meal-plan/"
    )
    _configure_credentials(db_session)

    def fake_request(method, url, headers=None, content=None, timeout=None, follow_redirects=None):
        assert method == "DELETE"
        return _FakeResponse(404, b"")

    monkeypatch.setattr(icloud.httpx, "request", fake_request)
    icloud.delete_event(db_session, 42)  # must not raise


def test_delete_event_is_a_noop_when_never_connected(db_session, monkeypatch):
    _configure_credentials(db_session)

    def fake_request(*args, **kwargs):
        raise AssertionError("should never make a request with no calendar href on file")

    monkeypatch.setattr(icloud.httpx, "request", fake_request)
    icloud.delete_event(db_session, 42)  # must not raise, must not call out


def test_sync_entry_deletes_when_skipped(db_session, monkeypatch):
    settings_service.set_setting(
        db_session, "icloud_calendar_calendar_href", "https://caldav.icloud.com/1234567/calendars/chef-meal-plan/"
    )
    _configure_credentials(db_session)
    plan = MealPlan(week_start_date=__import__("datetime").date(2026, 8, 3))
    entry = MealPlanEntry(id=7, day_of_week=0, meal_type="dinner", servings=2, is_skipped=True, meal_plan=plan)

    calls = []

    def fake_request(method, url, headers=None, content=None, timeout=None, follow_redirects=None):
        calls.append(method)
        return _FakeResponse(204, b"")

    monkeypatch.setattr(icloud.httpx, "request", fake_request)
    icloud.sync_entry(db_session, entry)
    assert calls == ["DELETE"]


def test_disconnect_clears_all_stored_settings(db_session):
    _configure_credentials(db_session)
    settings_service.set_setting(db_session, "icloud_calendar_calendar_href", "https://caldav.icloud.com/x/")
    settings_service.set_setting(db_session, "icloud_calendar_sync_enabled", "true")
    icloud.disconnect(db_session)
    assert icloud.is_configured(db_session) is False
    assert icloud.is_connected(db_session) is False
    assert icloud.is_sync_enabled(db_session) is False


def test_set_sync_enabled_requires_connection_first(db_session):
    _configure_credentials(db_session)
    with pytest.raises(icloud.ICloudCalendarError, match="Connect"):
        icloud.set_sync_enabled(db_session, True)
