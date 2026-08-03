"""Tests for the B4.4 (via B10.2) in-app expiration digest
(inventory_service.get_expiring_digest)."""

from __future__ import annotations

from datetime import date, timedelta

from app.models import InventoryItem


def _item(db_session, name, expiration_date=None, **kwargs):
    item = InventoryItem(name=name, expiration_date=expiration_date, **kwargs)
    db_session.add(item)
    db_session.commit()
    return item


def test_splits_expired_and_expiring_soon(db_session):
    from app.services import inventory_service

    today = date(2026, 8, 1)
    _item(db_session, "Old yogurt", expiration_date=today - timedelta(days=2))
    _item(db_session, "Milk", expiration_date=today + timedelta(days=3))
    _item(db_session, "Canned beans", expiration_date=today + timedelta(days=60))  # not within window
    _item(db_session, "No date item", expiration_date=None)  # excluded entirely

    digest = inventory_service.get_expiring_digest(db_session, within_days=7, today=today)
    assert [i.name for i in digest["expired"]] == ["Old yogurt"]
    assert [i.name for i in digest["expiring_soon"]] == ["Milk"]
    assert digest["within_days"] == 7


def test_expires_today_counts_as_expired_not_expiring_soon(db_session):
    from app.services import inventory_service

    today = date(2026, 8, 1)
    _item(db_session, "Expires today", expiration_date=today)
    digest = inventory_service.get_expiring_digest(db_session, today=today)
    assert [i.name for i in digest["expired"]] == ["Expires today"]
    assert digest["expiring_soon"] == []


def test_sorted_soonest_first(db_session):
    from app.services import inventory_service

    today = date(2026, 8, 1)
    _item(db_session, "Later", expiration_date=today + timedelta(days=5))
    _item(db_session, "Sooner", expiration_date=today + timedelta(days=1))
    digest = inventory_service.get_expiring_digest(db_session, today=today)
    assert [i.name for i in digest["expiring_soon"]] == ["Sooner", "Later"]


def test_empty_inventory_returns_empty_digest(db_session):
    from app.services import inventory_service

    digest = inventory_service.get_expiring_digest(db_session)
    assert digest == {"expired": [], "expiring_soon": [], "within_days": 7}


def test_within_days_is_configurable(db_session):
    from app.services import inventory_service

    today = date(2026, 8, 1)
    _item(db_session, "In 10 days", expiration_date=today + timedelta(days=10))
    assert inventory_service.get_expiring_digest(db_session, within_days=7, today=today)["expiring_soon"] == []
    result = inventory_service.get_expiring_digest(db_session, within_days=14, today=today)
    assert [i.name for i in result["expiring_soon"]] == ["In 10 days"]
