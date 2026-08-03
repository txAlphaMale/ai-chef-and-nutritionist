"""Unit tests for backlog B12.1's google_calendar_service.py: OAuth
config gating, the authorize/callback state round-trip, token exchange
and its side effects (refresh token + account email + dedicated
calendar, sync auto-enabled), push_entry's create/update/stale-fallback
paths, delete_event's best-effort 404 handling, disconnect's cleanup,
and sync_entry's skip-vs-push branching. Every Google HTTP call is
faked via monkeypatched httpx.{post,get,patch,delete} -- no real
network access, consistent with this module's own explicit reasoning
for using plain httpx rather than a heavier SDK: it's a handful of
REST calls, easy to fake precisely in a test the same way they're made
in the real client.
"""
from __future__ import annotations

import json
from datetime import date

import pytest

from app.models import MealPlan, MealPlanEntry
from app.services import google_calendar_service as gcal
from app.services import settings_service


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=None):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {}
        self.text = text if text is not None else json.dumps(self._json)

    def json(self):
        return self._json


@pytest.fixture(autouse=True)
def _reset_module_state():
    gcal._ACCESS_TOKEN_CACHE["token"] = None
    gcal._ACCESS_TOKEN_CACHE["expires_at"] = 0.0
    gcal._PENDING_STATES.clear()
    yield
    gcal._ACCESS_TOKEN_CACHE["token"] = None
    gcal._ACCESS_TOKEN_CACHE["expires_at"] = 0.0
    gcal._PENDING_STATES.clear()


def _configure_client(db_session):
    settings_service.set_setting(db_session, "google_calendar_client_id", "client-123")
    settings_service.set_setting(db_session, "google_calendar_client_secret", "shh-secret")
    settings_service.set_setting(db_session, "google_calendar_redirect_uri", "http://localhost:8000/api/calendar/google/callback")


def _mark_connected(db_session, calendar_id="cal-abc"):
    settings_service.set_setting(db_session, "google_calendar_refresh_token", "refresh-xyz")
    settings_service.set_setting(db_session, "google_calendar_calendar_id", calendar_id)


def test_is_configured_false_until_all_three_client_fields_set(db_session):
    assert gcal.is_configured(db_session) is False
    settings_service.set_setting(db_session, "google_calendar_client_id", "abc")
    assert gcal.is_configured(db_session) is False  # secret + redirect still missing
    _configure_client(db_session)
    assert gcal.is_configured(db_session) is True


def test_is_connected_requires_both_refresh_token_and_calendar_id(db_session):
    assert gcal.is_connected(db_session) is False
    settings_service.set_setting(db_session, "google_calendar_refresh_token", "refresh-xyz")
    assert gcal.is_connected(db_session) is False
    settings_service.set_setting(db_session, "google_calendar_calendar_id", "cal-abc")
    assert gcal.is_connected(db_session) is True


def test_is_sync_enabled_requires_connection_and_the_toggle(db_session):
    _mark_connected(db_session)
    assert gcal.is_sync_enabled(db_session) is False  # toggle defaults to "false"
    settings_service.set_setting(db_session, "google_calendar_sync_enabled", "true")
    assert gcal.is_sync_enabled(db_session) is True


def test_build_authorization_url_raises_without_client_config(db_session):
    with pytest.raises(gcal.GoogleCalendarError):
        gcal.build_authorization_url(db_session, "http://10.0.0.5:5173")


def test_build_authorization_url_contains_expected_google_params(db_session):
    _configure_client(db_session)
    url = gcal.build_authorization_url(db_session, "http://10.0.0.5:5173")
    assert url.startswith(gcal.GOOGLE_AUTH_ENDPOINT)
    assert "client_id=client-123" in url
    assert "access_type=offline" in url
    assert "prompt=consent" in url
    assert "scope=" in url


def test_state_round_trips_to_the_originating_return_to_and_is_one_time_use(db_session):
    _configure_client(db_session)
    url = gcal.build_authorization_url(db_session, "http://10.0.0.5:5173")
    state = url.split("state=")[1].split("&")[0]
    resolved = gcal.resolve_pending_state(state)
    assert resolved is not None
    assert resolved["return_to"] == "http://10.0.0.5:5173"
    assert gcal.resolve_pending_state(state) is None  # consumed


def test_exchange_code_for_tokens_stores_refresh_token_email_calendar_and_enables_sync(db_session, monkeypatch):
    _configure_client(db_session)

    def fake_post(url, data=None, headers=None, json=None, timeout=None):
        if url == gcal.GOOGLE_TOKEN_ENDPOINT:
            return _FakeResponse(200, {"access_token": "access-1", "refresh_token": "refresh-1", "expires_in": 3600})
        if url == f"{gcal.GOOGLE_CALENDAR_API_BASE}/calendars":
            return _FakeResponse(200, {"id": "new-cal-id"})
        raise AssertionError(f"unexpected POST {url}")

    def fake_get(url, headers=None, timeout=None):
        assert url == gcal.GOOGLE_USERINFO_ENDPOINT
        return _FakeResponse(200, {"email": "cook@example.com"})

    monkeypatch.setattr(gcal.httpx, "post", fake_post)
    monkeypatch.setattr(gcal.httpx, "get", fake_get)

    status = gcal.exchange_code_for_tokens(db_session, "auth-code-abc")

    assert status["connected"] is True
    assert status["sync_enabled"] is True
    assert status["account_email"] == "cook@example.com"
    assert status["calendar_id"] == "new-cal-id"
    assert settings_service.get_setting(db_session, "google_calendar_refresh_token") == "refresh-1"


def test_exchange_code_for_tokens_raises_without_refresh_token(db_session, monkeypatch):
    _configure_client(db_session)

    def fake_post(url, data=None, headers=None, json=None, timeout=None):
        return _FakeResponse(200, {"access_token": "access-1", "expires_in": 3600})  # no refresh_token

    monkeypatch.setattr(gcal.httpx, "post", fake_post)

    with pytest.raises(gcal.GoogleCalendarError, match="refresh token"):
        gcal.exchange_code_for_tokens(db_session, "auth-code-abc")


def test_ensure_dedicated_calendar_is_idempotent(db_session, monkeypatch):
    _configure_client(db_session)
    settings_service.set_setting(db_session, "google_calendar_calendar_id", "already-there")

    def fake_post(*args, **kwargs):
        raise AssertionError("should not call the Calendar API when a calendar id is already stored")

    monkeypatch.setattr(gcal.httpx, "post", fake_post)
    assert gcal.ensure_dedicated_calendar(db_session, access_token="unused") == "already-there"


def _make_plan_and_entry(db_session, **entry_kwargs):
    plan = MealPlan(week_start_date=date(2026, 8, 3))
    db_session.add(plan)
    db_session.flush()
    entry = MealPlanEntry(meal_plan_id=plan.id, day_of_week=2, meal_type="lunch", servings=2, **entry_kwargs)
    db_session.add(entry)
    db_session.commit()
    db_session.refresh(entry)
    return plan, entry


def test_push_entry_creates_a_new_event_when_no_google_event_id(db_session, monkeypatch):
    _configure_client(db_session)
    _mark_connected(db_session)
    settings_service.set_setting(db_session, "google_calendar_refresh_token", "refresh-1")
    plan, entry = _make_plan_and_entry(db_session)

    monkeypatch.setattr(gcal, "_get_access_token", lambda db: "access-1")
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(("POST", url, json))
        return _FakeResponse(200, {"id": "event-new-1"})

    monkeypatch.setattr(gcal.httpx, "post", fake_post)
    event_id = gcal.push_entry(db_session, plan, entry)

    assert event_id == "event-new-1"
    assert entry.google_event_id == "event-new-1"
    assert calls[0][0] == "POST"
    assert calls[0][1].endswith("/events")
    assert calls[0][2]["start"]["timeZone"] == "America/Chicago"


def test_push_entry_patches_an_existing_event(db_session, monkeypatch):
    _configure_client(db_session)
    _mark_connected(db_session)
    plan, entry = _make_plan_and_entry(db_session, google_event_id="event-existing")

    monkeypatch.setattr(gcal, "_get_access_token", lambda db: "access-1")
    calls = []

    def fake_patch(url, headers=None, json=None, timeout=None):
        calls.append(url)
        return _FakeResponse(200, {"id": "event-existing"})

    def fake_post(*args, **kwargs):
        raise AssertionError("should PATCH, not POST, when an event id already exists")

    monkeypatch.setattr(gcal.httpx, "patch", fake_patch)
    monkeypatch.setattr(gcal.httpx, "post", fake_post)
    event_id = gcal.push_entry(db_session, plan, entry)

    assert event_id == "event-existing"
    assert calls[0].endswith("/events/event-existing")


def test_push_entry_falls_back_to_create_when_stored_event_id_is_stale(db_session, monkeypatch):
    _configure_client(db_session)
    _mark_connected(db_session)
    plan, entry = _make_plan_and_entry(db_session, google_event_id="event-gone")

    monkeypatch.setattr(gcal, "_get_access_token", lambda db: "access-1")
    monkeypatch.setattr(gcal.httpx, "patch", lambda *a, **k: _FakeResponse(404, text="gone"))
    monkeypatch.setattr(gcal.httpx, "post", lambda *a, **k: _FakeResponse(200, {"id": "event-recreated"}))

    event_id = gcal.push_entry(db_session, plan, entry)
    assert event_id == "event-recreated"
    assert entry.google_event_id == "event-recreated"


def test_delete_event_swallows_404(db_session, monkeypatch):
    _mark_connected(db_session)
    monkeypatch.setattr(gcal, "_get_access_token", lambda db: "access-1")
    monkeypatch.setattr(gcal.httpx, "delete", lambda *a, **k: _FakeResponse(404, text="not found"))
    gcal.delete_event(db_session, "some-event-id")  # must not raise


def test_delete_event_raises_on_a_real_error(db_session, monkeypatch):
    _mark_connected(db_session)
    monkeypatch.setattr(gcal, "_get_access_token", lambda db: "access-1")
    monkeypatch.setattr(gcal.httpx, "delete", lambda *a, **k: _FakeResponse(500, text="server error"))
    with pytest.raises(gcal.GoogleCalendarError):
        gcal.delete_event(db_session, "some-event-id")


def test_delete_event_is_a_no_op_without_a_stored_calendar_id(db_session, monkeypatch):
    def fake_delete(*a, **k):
        raise AssertionError("should never call out when there's no calendar id on file")

    monkeypatch.setattr(gcal.httpx, "delete", fake_delete)
    gcal.delete_event(db_session, "some-event-id")  # must not raise, must not call out


def test_disconnect_clears_settings_and_blanks_entry_event_ids(db_session):
    _configure_client(db_session)
    _mark_connected(db_session)
    settings_service.set_setting(db_session, "google_calendar_account_email", "cook@example.com")
    settings_service.set_setting(db_session, "google_calendar_sync_enabled", "true")
    _plan, entry = _make_plan_and_entry(db_session, google_event_id="event-1")

    gcal.disconnect(db_session)

    assert gcal.is_connected(db_session) is False
    assert settings_service.get_setting(db_session, "google_calendar_account_email") == ""
    assert settings_service.get_setting(db_session, "google_calendar_sync_enabled") == "false"
    db_session.refresh(entry)
    assert entry.google_event_id is None


def test_sync_entry_skipped_deletes_the_event_and_clears_the_id(db_session, monkeypatch):
    _mark_connected(db_session)
    _plan, entry = _make_plan_and_entry(db_session, google_event_id="event-1", is_skipped=True)

    deleted = []
    monkeypatch.setattr(gcal, "delete_event", lambda db, eid: deleted.append(eid))

    gcal.sync_entry(db_session, entry)
    assert deleted == ["event-1"]
    assert entry.google_event_id is None


def test_sync_entry_skipped_with_no_existing_event_is_a_no_op(db_session, monkeypatch):
    _mark_connected(db_session)
    _plan, entry = _make_plan_and_entry(db_session, is_skipped=True)

    def fail(*a, **k):
        raise AssertionError("nothing to delete")

    monkeypatch.setattr(gcal, "delete_event", fail)
    gcal.sync_entry(db_session, entry)  # must not raise


def test_sync_entry_not_skipped_pushes(db_session, monkeypatch):
    _mark_connected(db_session)
    plan, entry = _make_plan_and_entry(db_session)

    pushed = []
    monkeypatch.setattr(gcal, "push_entry", lambda db, p, e: pushed.append((p, e)) or "event-x")

    gcal.sync_entry(db_session, entry)
    assert pushed == [(plan, entry)]


def test_resync_all_reports_plan_and_entry_counts(db_session, monkeypatch):
    _mark_connected(db_session)
    plan1, _entry1 = _make_plan_and_entry(db_session)
    entry2 = MealPlanEntry(meal_plan_id=plan1.id, day_of_week=3, meal_type="dinner", servings=2)
    db_session.add(entry2)
    db_session.commit()

    monkeypatch.setattr(gcal, "sync_entry", lambda db, e: None)
    result = gcal.resync_all(db_session)
    assert result == {"plans_synced": 1, "entries_synced": 2}


def test_set_sync_enabled_requires_a_connection_first(db_session):
    with pytest.raises(gcal.GoogleCalendarError):
        gcal.set_sync_enabled(db_session, True)


def test_set_sync_enabled_true_then_false(db_session):
    _mark_connected(db_session)
    status = gcal.set_sync_enabled(db_session, True)
    assert status["sync_enabled"] is True
    status = gcal.set_sync_enabled(db_session, False)
    assert status["sync_enabled"] is False
