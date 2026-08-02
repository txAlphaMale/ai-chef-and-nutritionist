"""Tests for the B10.1 dining-out finder's pure functions
(dining_service.py). No live Overpass network reaches this sandbox --
search_nearby_restaurants (the one function that makes an HTTP call) is
exercised only indirectly via its two pure halves tested here, same
standing constraint as every other external-network feature in this
project."""
from __future__ import annotations

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


# ---- search_nearby_restaurants headers (bug fix, 2026-08-02, author-
# reported live: "502 Could not reach OpenStreetMap's Overpass API:
# Client error 406 Not Acceptable") -------------------------------------
#
# Root cause: this call went out with no headers at all, so httpx sent
# its own generic default User-Agent, which overpass-api.de rejects with
# 406 as an anti-abuse measure. parse_overpass_response/build_overpass_
# query above were already covered, but nothing exercised the actual
# search_nearby_restaurants HTTP call itself -- this locks down the fix
# by faking httpx.AsyncClient (same "no live egress from this sandbox"
# constraint as every other external call in this project, see
# test_barcode_lookup.py's sync equivalent) and asserting the real
# identifying User-Agent header is present on the request.


class _FakeOverpassResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeAsyncClient:
    captured_headers = None
    captured_url = None

    def __init__(self, timeout=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def post(self, url, data=None, headers=None):
        _FakeAsyncClient.captured_url = url
        _FakeAsyncClient.captured_headers = headers
        return _FakeOverpassResponse({"elements": []})


def test_search_nearby_restaurants_sends_identifying_user_agent(monkeypatch):
    import asyncio

    monkeypatch.setattr(dining_service.httpx, "AsyncClient", _FakeAsyncClient)
    asyncio.run(dining_service.search_nearby_restaurants(30.2672, -97.7431, 5000))
    assert _FakeAsyncClient.captured_headers is not None
    assert "User-Agent" in _FakeAsyncClient.captured_headers
    assert _FakeAsyncClient.captured_headers["User-Agent"] != ""
    # Must not be left to httpx's own generic default -- that's the exact
    # bug: a real, non-empty, app-identifying string.
    assert "chef-meal-planner" in _FakeAsyncClient.captured_headers["User-Agent"]
