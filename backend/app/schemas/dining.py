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
    diet_tags: dict[str, str] = Field(default_factory=dict)
    per_allergen: dict[str, str] = Field(default_factory=dict)
    allergens_with_no_data_source: list[str] = Field(default_factory=list)
    caution: str
