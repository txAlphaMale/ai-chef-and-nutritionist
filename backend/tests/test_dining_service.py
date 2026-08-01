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
