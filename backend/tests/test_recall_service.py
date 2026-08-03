"""Unit tests for backlog B3.3's recall awareness
(app.services.recall_service): the FSIS/openFDA response normalizers
(against fixture payloads shaped like each API's real, documented
response schema -- verified live during this feature's research, see
recall_service.py's module docstring), the check/dedup/throttle logic,
and dismiss. httpx.get is monkeypatched rather than hitting the real
network, same "no live egress from this sandbox" constraint every other
external-API service in this repo already works around (food_data_
service.py, tavily_client.py)."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.models import InventoryItem, RecallAlert, RecallCheckState
from app.services import recall_service as rs


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json


# --- _fsis_search -----------------------------------------------------


def test_fsis_search_normalizes_active_recall(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        assert url == rs.FSIS_RECALL_API_URL
        assert params["field_product_items_value"] == "chicken patties"
        return _FakeResponse(
            [
                {
                    "field_title": "Foster Farms Recalls Chicken Patties",
                    "field_active_notice": "True",
                    "field_recall_number": "040-2022",
                    "field_recall_reason": "Product Contamination",
                    "field_risk_level": "High - Class I",
                    "field_recall_date": "2022-10-29",
                    "field_states": "Arizona, California",
                    "field_summary": "<p><strong>WASHINGTON</strong> -- details.</p>",
                }
            ]
        )

    monkeypatch.setattr(rs.httpx, "get", fake_get)
    results = rs._fsis_search("chicken patties")
    assert len(results) == 1
    r = results[0]
    assert r["source"] == "fsis"
    assert r["external_id"] == "040-2022"
    assert r["status"] == "Active"
    assert r["classification"] == "High - Class I"
    assert r["recall_date"].isoformat() == "2022-10-29"
    assert r["summary"] == "WASHINGTON -- details."


def test_fsis_search_closed_recall_status(monkeypatch):
    monkeypatch.setattr(
        rs.httpx,
        "get",
        lambda *a, **k: _FakeResponse(
            [{"field_title": "Old Recall", "field_active_notice": "False", "field_recall_number": "001-2020"}]
        ),
    )
    results = rs._fsis_search("anything")
    assert results[0]["status"] == "Closed"


def test_fsis_search_returns_empty_on_http_error(monkeypatch):
    import httpx

    def fake_get(*a, **k):
        raise httpx.ConnectError("no route")

    monkeypatch.setattr(rs.httpx, "get", fake_get)
    assert rs._fsis_search("anything") == []


def test_fsis_search_returns_empty_on_non_list_response(monkeypatch):
    monkeypatch.setattr(rs.httpx, "get", lambda *a, **k: _FakeResponse({"error": "bad"}))
    assert rs._fsis_search("anything") == []


def test_fsis_search_skips_rows_without_title(monkeypatch):
    monkeypatch.setattr(rs.httpx, "get", lambda *a, **k: _FakeResponse([{"field_active_notice": "True"}]))
    assert rs._fsis_search("anything") == []


# --- _openfda_search ----------------------------------------------------


def test_openfda_search_normalizes_result(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        assert url == rs.OPENFDA_ENFORCEMENT_URL
        assert params["search"] == 'product_description:"peanut butter"'
        assert "api_key" not in params
        return _FakeResponse(
            {
                "results": [
                    {
                        "status": "Ongoing",
                        "classification": "Class II",
                        "recalling_firm": "Tom Bumble LLC",
                        "recall_number": "H-0391-2026",
                        "product_description": "Tom Bumble Nutty Peanut Butter Flake Candy",
                        "reason_for_recall": "Foreign material.",
                        "recall_initiation_date": "20251213",
                        "distribution_pattern": "Distributed nationwide",
                    }
                ]
            }
        )

    monkeypatch.setattr(rs.httpx, "get", fake_get)
    results = rs._openfda_search("peanut butter", api_key=None)
    assert len(results) == 1
    r = results[0]
    assert r["source"] == "openfda"
    assert r["external_id"] == "H-0391-2026"
    assert r["status"] == "Ongoing"
    assert r["classification"] == "Class II"
    assert r["recall_date"].isoformat() == "2025-12-13"
    assert "Tom Bumble LLC" in r["title"]
    assert "Peanut Butter" in r["title"]


def test_openfda_search_includes_api_key_when_configured(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured.update(params)
        return _FakeResponse({"results": []})

    monkeypatch.setattr(rs.httpx, "get", fake_get)
    rs._openfda_search("milk", api_key="test-key-123")
    assert captured["api_key"] == "test-key-123"


def test_openfda_search_404_no_matches_returns_empty(monkeypatch):
    # openFDA's real documented behavior for zero matches is HTTP 404,
    # not a 200 with an empty list -- verified live during this
    # feature's research (see module docstring).
    monkeypatch.setattr(rs.httpx, "get", lambda *a, **k: _FakeResponse({"error": "not found"}, status_code=404))
    assert rs._openfda_search("zzznonexistent", api_key=None) == []


def test_openfda_search_returns_empty_on_malformed_response(monkeypatch):
    monkeypatch.setattr(rs.httpx, "get", lambda *a, **k: _FakeResponse({"unexpected": "shape"}))
    assert rs._openfda_search("anything", api_key=None) == []


# --- check_item_against_recalls -----------------------------------------


def test_check_item_against_recalls_combines_both_sources(monkeypatch, db_session):
    monkeypatch.setattr(rs, "_fsis_search", lambda name: [{"source": "fsis", "title": "fsis-match"}])
    monkeypatch.setattr(rs, "_openfda_search", lambda name, api_key: [{"source": "openfda", "title": "fda-match"}])
    results = rs.check_item_against_recalls(db_session, "chicken")
    assert len(results) == 2
    assert {r["source"] for r in results} == {"fsis", "openfda"}


def test_check_item_against_recalls_blank_name_short_circuits(db_session):
    assert rs.check_item_against_recalls(db_session, "   ") == []


# --- check_inventory_for_recalls: dedup, throttling, persistence -------


def _fake_match(source="fsis", external_id="X-1", title="Some Recall"):
    return {
        "source": source,
        "external_id": external_id,
        "title": title,
        "reason": "reason text",
        "classification": "Class I",
        "status": "Active",
        "recall_date": None,
        "states": None,
        "summary": None,
    }


def test_check_inventory_for_recalls_creates_alerts_and_dedups_item_names(monkeypatch, db_session):
    db_session.add_all(
        [
            InventoryItem(name="Onion", category="pantry"),
            InventoryItem(name="onion", category="pantry"),  # same name, different case
            InventoryItem(name="Garlic", category="pantry"),
        ]
    )
    db_session.commit()

    calls = []

    def fake_check(db, name):
        calls.append(name)
        if name.lower() == "onion":
            return [_fake_match(external_id="ONION-RECALL-1")]
        return []

    monkeypatch.setattr(rs, "check_item_against_recalls", fake_check)
    result = rs.check_inventory_for_recalls(db_session, force=True)

    assert result["checked"] is True
    assert result["new_alert_count"] == 1
    # Deduplicated case-insensitively -- "Onion" and "onion" checked once, not twice.
    assert sorted(c.lower() for c in calls) == ["garlic", "onion"]

    alerts = db_session.query(RecallAlert).all()
    assert len(alerts) == 1
    assert alerts[0].external_id == "ONION-RECALL-1"


def test_check_inventory_for_recalls_does_not_duplicate_existing_alert(monkeypatch, db_session):
    db_session.add(InventoryItem(name="Spinach", category="produce"))
    db_session.commit()
    monkeypatch.setattr(rs, "check_item_against_recalls", lambda db, name: [_fake_match(external_id="SPINACH-1")])

    rs.check_inventory_for_recalls(db_session, force=True)
    first_count = db_session.query(RecallAlert).count()
    rs.check_inventory_for_recalls(db_session, force=True)
    second_count = db_session.query(RecallAlert).count()

    assert first_count == 1
    assert second_count == 1  # no duplicate row for the same (source, external_id)


def test_check_inventory_for_recalls_throttled_without_force(monkeypatch, db_session):
    db_session.add(InventoryItem(name="Beef", category="freezer"))
    db_session.commit()
    state = RecallCheckState(id=1, last_checked_at=datetime.utcnow(), last_check_item_count=1)
    db_session.add(state)
    db_session.commit()

    called = {"count": 0}

    def fake_check(db, name):
        called["count"] += 1
        return []

    monkeypatch.setattr(rs, "check_item_against_recalls", fake_check)
    result = rs.check_inventory_for_recalls(db_session, force=False)

    assert result["checked"] is False
    assert called["count"] == 0  # throttled -- never actually queried


def test_check_inventory_for_recalls_force_bypasses_throttle(monkeypatch, db_session):
    db_session.add(InventoryItem(name="Beef", category="freezer"))
    db_session.commit()
    db_session.add(RecallCheckState(id=1, last_checked_at=datetime.utcnow(), last_check_item_count=1))
    db_session.commit()

    monkeypatch.setattr(rs, "check_item_against_recalls", lambda db, name: [])
    result = rs.check_inventory_for_recalls(db_session, force=True)
    assert result["checked"] is True


def test_check_inventory_for_recalls_old_state_is_due_again(monkeypatch, db_session):
    db_session.add(InventoryItem(name="Beef", category="freezer"))
    db_session.commit()
    stale = datetime.utcnow() - timedelta(hours=rs.RECALL_CHECK_INTERVAL_HOURS + 1)
    db_session.add(RecallCheckState(id=1, last_checked_at=stale, last_check_item_count=1))
    db_session.commit()

    monkeypatch.setattr(rs, "check_item_against_recalls", lambda db, name: [])
    result = rs.check_inventory_for_recalls(db_session, force=False)
    assert result["checked"] is True


# --- is_check_due / get_check_state -------------------------------------


def test_is_check_due_true_when_never_checked(db_session):
    assert rs.is_check_due(db_session) is True


def test_is_check_due_false_right_after_a_check(db_session):
    db_session.add(RecallCheckState(id=1, last_checked_at=datetime.utcnow(), last_check_item_count=0))
    db_session.commit()
    assert rs.is_check_due(db_session) is False


def test_get_check_state_creates_singleton_row(db_session):
    state = rs.get_check_state(db_session)
    assert state.id == 1
    assert db_session.query(RecallCheckState).count() == 1
    # Calling again doesn't create a second row.
    state2 = rs.get_check_state(db_session)
    assert state2.id == state.id
    assert db_session.query(RecallCheckState).count() == 1


# --- list_active_alerts / dismiss_alert ----------------------------------


def test_list_active_alerts_excludes_dismissed(db_session):
    db_session.add_all(
        [
            RecallAlert(source="fsis", external_id="A1", matched_item_name="x", title="Active one", is_dismissed=False),
            RecallAlert(
                source="fsis", external_id="A2", matched_item_name="x", title="Dismissed one", is_dismissed=True
            ),
        ]
    )
    db_session.commit()
    active = rs.list_active_alerts(db_session)
    assert len(active) == 1
    assert active[0].title == "Active one"


def test_dismiss_alert_marks_dismissed_and_persists(db_session):
    alert = RecallAlert(source="fsis", external_id="A1", matched_item_name="x", title="T", is_dismissed=False)
    db_session.add(alert)
    db_session.commit()

    dismissed = rs.dismiss_alert(db_session, alert.id)
    assert dismissed is not None
    assert dismissed.is_dismissed is True
    assert rs.list_active_alerts(db_session) == []


def test_dismiss_alert_unknown_id_returns_none(db_session):
    assert rs.dismiss_alert(db_session, 999999) is None
