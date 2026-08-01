"""Tests for backlog B4.1's barcode-lookup endpoint (author-requested
2026-08-01, app/routers/inventory.py's barcode_lookup + food_data_
service.get_off_product). Calls the router function directly (same
pattern as test_inventory_import.py -- no TestClient anywhere in this
repo's test suite) since it takes no FastAPI dependency injection, only
a plain query param. httpx.get is monkeypatched on food_data_service's
own module reference, same "no live egress from this sandbox" pattern
used by test_recall_service.py."""
from __future__ import annotations

import httpx
import pytest

from app.routers.inventory import barcode_lookup
from app.services import food_data_service


class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json


def test_barcode_lookup_returns_prefilled_preview_on_match(monkeypatch):
    def fake_get(url, timeout=None):
        assert "0123456789012" in url
        return _FakeResponse(
            {
                "status": 1,
                "product": {
                    "product_name": "Gluten Free Rolled Oats",
                    "brands": "Bob's Red Mill",
                    "quantity": "32 oz",
                    "image_front_url": "https://images.example/oats.jpg",
                },
            }
        )

    monkeypatch.setattr(food_data_service.httpx, "get", fake_get)
    result = barcode_lookup("0123456789012")
    assert result.found is True
    assert result.barcode == "0123456789012"
    assert result.name == "Gluten Free Rolled Oats"
    assert result.brand == "Bob's Red Mill"
    assert result.quantity_text == "32 oz"
    assert result.image_url == "https://images.example/oats.jpg"
    # "oats" isn't in meal_plan_service's pantry/fridge/freezer/produce/
    # spice keyword lists, so this exercises the "or 'other'" fallback,
    # not a hardcoded assumption that every product guesses cleanly.
    assert result.category in {"pantry", "other"}
    assert result.estimated_quantity == 1
    assert result.unit == "count"


def test_barcode_lookup_returns_not_found_when_off_has_no_match(monkeypatch):
    def fake_get(url, timeout=None):
        return _FakeResponse({"status": 0}, status_code=200)

    monkeypatch.setattr(food_data_service.httpx, "get", fake_get)
    result = barcode_lookup("9999999999999")
    assert result.found is False
    assert result.name is None
    assert result.barcode == "9999999999999"


def test_barcode_lookup_treats_network_failure_as_not_found(monkeypatch):
    def fake_get(url, timeout=None):
        raise httpx.ConnectError("no route", request=None)

    monkeypatch.setattr(food_data_service.httpx, "get", fake_get)
    result = barcode_lookup("0000000000000")
    assert result.found is False


def test_barcode_lookup_rejects_blank_barcode():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        barcode_lookup("   ")
    assert exc_info.value.status_code == 400


def test_barcode_lookup_handles_product_with_no_name(monkeypatch):
    def fake_get(url, timeout=None):
        return _FakeResponse({"status": 1, "product": {"brands": "Generic Co"}})

    monkeypatch.setattr(food_data_service.httpx, "get", fake_get)
    result = barcode_lookup("1111111111111")
    assert result.found is True
    assert result.name is None
    # No name to guess a category from -- falls back to "other", same as
    # every other guess_grocery_category caller in this app treats "no
    # keyword match".
    assert result.category == "other"
    assert "no product name" in result.confidence_note
