"""Backlog B10.1 (2026-08-01): the dining-out finder's response shapes."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RestaurantCandidate(BaseModel):
    """One Overpass result, deterministically evaluated against the
    household's restriction taxonomy -- see dining_service.py's module
    docstring for the safety-framing rationale (never asserts "safe")."""

    osm_type: str | None = None
    osm_id: int | None = None
    name: str
    lat: float
    lon: float
    distance_m: float
    amenity: str | None = None
    cuisine: str | None = None
    address: str | None = None
    # All four come straight off the OSM tags the Overpass query already
    # returns. They were being fetched and discarded, which left results
    # with no way to call ahead and confirm -- the exact action the
    # caution message on every result tells the household to take.
    website: str | None = None
    phone: str | None = None
    opening_hours: str | None = None
    wheelchair: str | None = None
    map_url: str | None = None
    diet_tags: dict[str, str] = Field(default_factory=dict)
    per_allergen: dict[str, str] = Field(default_factory=dict)
    allergens_with_no_data_source: list[str] = Field(default_factory=list)
    caution: str


class GeocodeResult(BaseModel):
    """One candidate match for a free-text address/zip lookup (backlog
    B10.1 follow-up, 2026-08-02) -- see dining_service.geocode's
    docstring for why this can return more than one result."""

    lat: float
    lon: float
    display_name: str


class IPGeolocationResult(BaseModel):
    """Approximate, network-based location (backlog B10.1 follow-up,
    2026-08-02) -- see dining_service.geolocate_by_ip's docstring for
    exactly what this reflects (the BACKEND's own outbound IP, i.e. the
    household's network, not the browsing device) and why that's still a
    genuinely useful third option alongside GPS and manual address entry
    for a self-hosted home server."""

    lat: float
    lon: float
    city: str | None = None
    region: str | None = None
    country: str | None = None
