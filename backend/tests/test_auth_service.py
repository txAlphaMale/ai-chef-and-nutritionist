"""Tests for the B9.4/B10.2 lightweight single-shared-password gate
(auth_service.py). Pure service-layer logic against a real (test) DB
session -- no HTTP/session-cookie machinery here, that's covered by a
live end-to-end curl pass instead (see PROJECT-PLAN.md's B10.2 notes)."""

from __future__ import annotations

import pytest

from app.services import auth_service


def test_disabled_by_default(db_session):
    assert auth_service.is_enabled(db_session) is False
    assert auth_service.is_configured(db_session) is False


def test_set_password_enables_and_verifies(db_session):
    auth_service.set_password(db_session, "correct horse battery")
    assert auth_service.is_enabled(db_session) is True
    assert auth_service.is_configured(db_session) is True
    assert auth_service.verify_password(db_session, "correct horse battery") is True
    assert auth_service.verify_password(db_session, "wrong password") is False


def test_set_password_rejects_short_password(db_session):
    with pytest.raises(ValueError, match="at least 8 characters"):
        auth_service.set_password(db_session, "short")


def test_verify_password_false_when_never_configured(db_session):
    assert auth_service.verify_password(db_session, "anything") is False


def test_disable_clears_hash_and_flag(db_session):
    auth_service.set_password(db_session, "correct horse battery")
    auth_service.disable(db_session)
    assert auth_service.is_enabled(db_session) is False
    assert auth_service.is_configured(db_session) is False
    assert auth_service.verify_password(db_session, "correct horse battery") is False


def test_changing_password_invalidates_the_old_one(db_session):
    auth_service.set_password(db_session, "first password here")
    auth_service.set_password(db_session, "second password here")
    assert auth_service.verify_password(db_session, "first password here") is False
    assert auth_service.verify_password(db_session, "second password here") is True


def test_password_hash_never_stored_as_known_setting_key():
    # The whole point of keeping this out of settings_service's registry
    # -- see auth_service.py's module docstring -- is that the generic
    # PATCH /api/system/settings/{key} endpoint must never be able to
    # touch it. Guard that invariant directly against the registry
    # rather than only against the router (belt-and-suspenders: even if
    # a future router refactor forgets the router-level check, the key
    # itself is structurally unreachable).
    from app.services import settings_service

    assert settings_service.is_known_key("__auth_enabled") is False
    assert settings_service.is_known_key("__auth_password_hash") is False


def test_rate_limited_after_threshold():
    ip = "203.0.113.5"
    for _ in range(auth_service.LOGIN_RATE_LIMIT_PER_MIN):
        assert auth_service.rate_limited(ip) is False
        auth_service.record_attempt(ip)
    assert auth_service.rate_limited(ip) is True


def test_rate_limit_buckets_are_independent_per_ip():
    for _ in range(auth_service.LOGIN_RATE_LIMIT_PER_MIN):
        auth_service.record_attempt("203.0.113.9")
    assert auth_service.rate_limited("203.0.113.9") is True
    assert auth_service.rate_limited("203.0.113.10") is False
