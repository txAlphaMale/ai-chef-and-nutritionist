"""Tests for the B10.1 dining-out finder's pure functions
(dining_service.py). No live Overpass network reaches this sandbox --
search_nearby_restaurants (the one function that makes an HTTP call) is
exercised only indirectly via its two pure halves tested here, same
standing constraint as every other external-network feature in this
project."""
from __future__ import annotations

from typing import ClassVar

import httpx

from app.services import dining_service

# ---- build_overpass_query ---------------------------------------------


def test_build_overpass_query_includes_coordinates_and_radius():
    q = dining_service.build_overpass_query(30.2672, -97.7431, 5000)
    assert "30.2672" in q
    assert "-97.7431" in q
    assert "5000" in q
    assert "amenity" in q
    assert "restaurant|cafe|fast_food" in q


# ---- parse_overpass_response -------------------------------------------


def test_parse_overpass_response_extracts_node_results():
    data = {
        "elements": [
            {
                "type": "node",
                "id": 1,
                "lat": 30.27,
                "lon": -97.74,
                "tags": {"name": "Test Cafe", "amenity": "cafe", "cuisine": "coffee_shop", "diet:gluten_free": "yes"},
            }
        ]
    }
    places = dining_service.parse_overpass_response(data, 30.2672, -97.7431)
    assert len(places) == 1
    p = places[0]
    assert p["name"] == "Test Cafe"
    assert p["diet_tags"] == {"diet:gluten_free": "yes"}
    assert p["distance_m"] >= 0


def test_parse_overpass_response_uses_center_for_way_results():
    data = {
        "elements": [
            {
                "type": "way",
                "id": 2,
                "center": {"lat": 30.3, "lon": -97.7},
                "tags": {"name": "Way Restaurant", "amenity": "restaurant"},
            }
        ]
    }
    places = dining_service.parse_overpass_response(data, 30.2672, -97.7431)
    assert len(places) == 1
    assert places[0]["lat"] == 30.3


def test_parse_overpass_response_skips_unnamed_elements():
    data = {"elements": [{"type": "node", "id": 3, "lat": 30.3, "lon": -97.7, "tags": {"amenity": "restaurant"}}]}
    assert dining_service.parse_overpass_response(data, 30.2672, -97.7431) == []


def test_parse_overpass_response_skips_elements_with_no_coordinates():
    data = {"elements": [{"type": "way", "id": 4, "tags": {"name": "No Center"}}]}
    assert dining_service.parse_overpass_response(data, 30.2672, -97.7431) == []


def test_parse_overpass_response_sorts_by_distance():
    origin = (30.0, -97.0)
    data = {
        "elements": [
            {"type": "node", "id": 1, "lat": 30.1, "lon": -97.0, "tags": {"name": "Farther"}},
            {"type": "node", "id": 2, "lat": 30.01, "lon": -97.0, "tags": {"name": "Closer"}},
        ]
    }
    places = dining_service.parse_overpass_response(data, *origin)
    assert [p["name"] for p in places] == ["Closer", "Farther"]


def test_parse_overpass_response_formats_address_from_tags():
    data = {
        "elements": [
            {
                "type": "node",
                "id": 5,
                "lat": 30.0,
                "lon": -97.0,
                "tags": {
                    "name": "Addressed Place",
                    "addr:housenumber": "123",
                    "addr:street": "Main St",
                    "addr:city": "Austin",
                },
            }
        ]
    }
    places = dining_service.parse_overpass_response(data, 30.0, -97.0)
    assert places[0]["address"] == "123 Main St, Austin"


def test_parse_overpass_response_address_none_when_no_addr_tags():
    data = {"elements": [{"type": "node", "id": 6, "lat": 30.0, "lon": -97.0, "tags": {"name": "No Address"}}]}
    places = dining_service.parse_overpass_response(data, 30.0, -97.0)
    assert places[0]["address"] is None


# ---- evaluate_restrictions ------------------------------------------------


def test_evaluate_restrictions_covered_allergen_reads_the_tag():
    place = {"diet_tags": {"diet:gluten_free": "only"}}
    result = dining_service.evaluate_restrictions(place, ["gluten"], "strict_no_cross_contact")
    assert result["per_allergen"] == {"gluten": "only"}
    assert result["allergens_with_no_data_source"] == []
    assert "cross-contact" in result["caution"]


def test_evaluate_restrictions_covered_allergen_missing_tag_is_unknown_not_safe():
    place = {"diet_tags": {}}
    result = dining_service.evaluate_restrictions(place, ["gluten"], "flexible")
    assert result["per_allergen"] == {"gluten": "unknown"}


def test_evaluate_restrictions_uncovered_allergen_reports_no_data():
    place = {"diet_tags": {"diet:gluten_free": "yes"}}
    result = dining_service.evaluate_restrictions(place, ["peanuts", "shellfish"], None)
    assert result["per_allergen"] == {"peanuts": "no_data", "shellfish": "no_data"}
    assert set(result["allergens_with_no_data_source"]) == {"peanuts", "shellfish"}


def test_evaluate_restrictions_milk_maps_to_dairy_free_tag():
    place = {"diet_tags": {"diet:dairy_free": "no"}}
    result = dining_service.evaluate_restrictions(place, ["milk"], None)
    assert result["per_allergen"] == {"milk": "no"}


def test_evaluate_restrictions_no_restrictions_gives_generic_caution():
    result = dining_service.evaluate_restrictions({"diet_tags": {}}, [], None)
    assert result["per_allergen"] == {}
    assert result["allergens_with_no_data_source"] == []
    assert "Always confirm" in result["caution"]


def test_evaluate_restrictions_never_returns_a_bare_safe_verdict():
    # The whole point of this function: no matter how favorable the
    # tags are, there is no "safe": True anywhere in the return shape.
    place = {"diet_tags": {"diet:gluten_free": "only"}}
    result = dining_service.evaluate_restrictions(place, ["gluten"], "strict_no_cross_contact")
    assert "safe" not in str(result).lower().replace("unsafe", "")


# ---- parse_nominatim_response (backlog B10.1 follow-up, 2026-08-02) ----


def test_parse_nominatim_response_extracts_candidates():
    data = [
        {"lat": "30.267200", "lon": "-97.743100", "display_name": "Austin, Travis County, Texas, USA"},
        {"lat": "30.3000", "lon": "-97.7500", "display_name": "North Austin, Travis County, Texas, USA"},
    ]
    results = dining_service.parse_nominatim_response(data)
    assert len(results) == 2
    assert results[0] == {"lat": 30.2672, "lon": -97.7431, "display_name": "Austin, Travis County, Texas, USA"}


def test_parse_nominatim_response_skips_unparseable_entries():
    data = [
        {"lat": "not-a-number", "lon": "-97.7431", "display_name": "Bad entry"},
        {"lon": "-97.7431", "display_name": "Missing lat"},
        "not even a dict",
        {"lat": "30.27", "lon": "-97.74", "display_name": "Good entry"},
    ]
    results = dining_service.parse_nominatim_response(data)
    assert len(results) == 1
    assert results[0]["display_name"] == "Good entry"


def test_parse_nominatim_response_empty_input():
    assert dining_service.parse_nominatim_response([]) == []
    assert dining_service.parse_nominatim_response(None) == []


def test_parse_nominatim_response_falls_back_to_coordinates_when_no_display_name():
    data = [{"lat": "30.27", "lon": "-97.74"}]
    results = dining_service.parse_nominatim_response(data)
    assert results[0]["display_name"] == "30.27, -97.74"


# ---- parse_ip_geolocation_response (backlog B10.1 follow-up, 2026-08-02, round 2) ----


def test_parse_ip_geolocation_response_extracts_fields():
    data = {
        "success": True,
        "latitude": 30.2672,
        "longitude": -97.7431,
        "city": "Austin",
        "region": "Texas",
        "country": "United States",
    }
    result = dining_service.parse_ip_geolocation_response(data)
    assert result == {"lat": 30.2672, "lon": -97.7431, "city": "Austin", "region": "Texas", "country": "United States"}


def test_parse_ip_geolocation_response_none_on_unsuccessful():
    data = {"success": False, "message": "reserved range"}
    assert dining_service.parse_ip_geolocation_response(data) is None


def test_parse_ip_geolocation_response_none_on_missing_coordinates():
    data = {"success": True, "city": "Austin"}
    assert dining_service.parse_ip_geolocation_response(data) is None


def test_parse_ip_geolocation_response_none_on_unparseable_coordinates():
    data = {"success": True, "latitude": "not-a-number", "longitude": -97.7431}
    assert dining_service.parse_ip_geolocation_response(data) is None


def test_parse_ip_geolocation_response_optional_fields_default_none():
    data = {"success": True, "latitude": 30.0, "longitude": -97.0}
    result = dining_service.parse_ip_geolocation_response(data)
    assert result == {"lat": 30.0, "lon": -97.0, "city": None, "region": None, "country": None}


# ---- search_nearby_restaurants headers + mirror fallback (bug fixes,
# 2026-08-02, both author-reported live from the same "Look up" button)
# -------------------------------------------------------------------
#
# First: "502 Could not reach OpenStreetMap's Overpass API: Client error
# 406 Not Acceptable" -- this call went out with no headers at all, so
# httpx sent its own generic default User-Agent, which overpass-api.de
# rejects with 406 as an anti-abuse measure. Fixed by adding the
# identifying User-Agent header.
#
# Then, AFTER that fix deployed: "502 Could not reach OpenStreetMap's
# Overpass API: Server error '504 Gateway Timeout'" -- a genuinely
# different failure, the free public overpass-api.de instance itself
# being overloaded, not anything wrong with this app's request. Fixed by
# falling back to a second, verified-live public mirror (private.coffee,
# formerly kumi.systems -- see OVERPASS_FALLBACK_URL's own comment for
# the live source used to confirm it) on a server-side (5xx/timeout/
# connection) failure, while still failing fast (no fallback attempt) on
# a 4xx client error, which would fail identically on the mirror.
#
# parse_overpass_response/build_overpass_query above were already
# covered, but nothing exercised the actual search_nearby_restaurants
# HTTP call itself before the User-Agent test below -- this fakes
# httpx.AsyncClient (same "no live egress from this sandbox" constraint
# as every other external call in this project, see test_barcode_lookup
# .py's sync equivalent) with a SCRIPTED sequence of per-call behaviors,
# since search_nearby_restaurants opens a fresh `async with
# httpx.AsyncClient(...)` per attempt -- a single instance's own call
# count can't distinguish "primary attempt" from "fallback attempt".


class _FakeOverpassResponse:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://example.invalid")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(f"{self.status_code} error", request=request, response=response)

    def json(self):
        return self._payload


class _ScriptedAsyncClient:
    """Each test sets `.behaviors` to a list consumed one-per-`.post()`
    call, in order -- a `_FakeOverpassResponse` to return, or an
    `Exception` instance to raise (simulating a timeout/connection
    failure that never even produces a response object)."""

    # Class-level on purpose: the test double collects calls across
    # every instance the code under test constructs.
    behaviors: ClassVar[list] = []
    captured_urls: ClassVar[list] = []
    captured_headers: ClassVar[list] = []

    def __init__(self, timeout=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, data=None, headers=None):
        _ScriptedAsyncClient.captured_urls.append(url)
        _ScriptedAsyncClient.captured_headers.append(headers)
        behavior = _ScriptedAsyncClient.behaviors.pop(0)
        if isinstance(behavior, Exception):
            raise behavior
        return behavior


def _script(monkeypatch, behaviors):
    _ScriptedAsyncClient.behaviors = list(behaviors)
    _ScriptedAsyncClient.captured_urls = []
    _ScriptedAsyncClient.captured_headers = []
    monkeypatch.setattr(dining_service.httpx, "AsyncClient", _ScriptedAsyncClient)


def test_search_nearby_restaurants_sends_identifying_user_agent(monkeypatch):
    import asyncio

    _script(monkeypatch, [_FakeOverpassResponse({"elements": []})])
    asyncio.run(dining_service.search_nearby_restaurants(30.2672, -97.7431, 5000))
    headers = _ScriptedAsyncClient.captured_headers[0]
    assert headers is not None
    assert "User-Agent" in headers
    assert headers["User-Agent"] != ""
    # Must not be left to httpx's own generic default -- that's the exact
    # bug: a real, non-empty, app-identifying string.
    assert "chef-meal-planner" in headers["User-Agent"]


def test_search_nearby_restaurants_falls_back_to_mirror_on_server_error(monkeypatch):
    import asyncio

    _script(
        monkeypatch,
        [_FakeOverpassResponse(status_code=504), _FakeOverpassResponse({"elements": []}, status_code=200)],
    )
    result = asyncio.run(dining_service.search_nearby_restaurants(30.2672, -97.7431, 5000))
    assert result == []
    assert _ScriptedAsyncClient.captured_urls == [dining_service.OVERPASS_URL, dining_service.OVERPASS_FALLBACK_URL]


def test_search_nearby_restaurants_does_not_fall_back_on_client_error(monkeypatch):
    # A 4xx means this app's own request is malformed -- retrying against
    # the mirror would fail identically and just hide a real bug.
    import asyncio

    import pytest

    _script(monkeypatch, [_FakeOverpassResponse(status_code=400)])
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(dining_service.search_nearby_restaurants(30.2672, -97.7431, 5000))
    assert _ScriptedAsyncClient.captured_urls == [dining_service.OVERPASS_URL]  # never tried the mirror


def test_search_nearby_restaurants_falls_back_to_mirror_on_timeout(monkeypatch):
    import asyncio

    _script(
        monkeypatch,
        [httpx.TimeoutException("timed out"), _FakeOverpassResponse({"elements": []})],
    )
    result = asyncio.run(dining_service.search_nearby_restaurants(30.2672, -97.7431, 5000))
    assert result == []
    assert _ScriptedAsyncClient.captured_urls == [dining_service.OVERPASS_URL, dining_service.OVERPASS_FALLBACK_URL]


def test_search_nearby_restaurants_raises_if_both_primary_and_mirror_fail(monkeypatch):
    import asyncio

    import pytest

    _script(monkeypatch, [_FakeOverpassResponse(status_code=504), _FakeOverpassResponse(status_code=504)])
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(dining_service.search_nearby_restaurants(30.2672, -97.7431, 5000))
    assert _ScriptedAsyncClient.captured_urls == [dining_service.OVERPASS_URL, dining_service.OVERPASS_FALLBACK_URL]
