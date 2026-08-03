"""Backlog B10.1 (author-requested group, 2026-08-01): the dining-out
finder. See dining_service.py's module docstring for the full research
writeup (Overpass tag coverage, its hard limitations, and the safety-
framing discipline this endpoint follows)."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import HouseholdPreferences
from app.schemas.dining import GeocodeResult, IPGeolocationResult, RestaurantCandidate
from app.services import dining_service

router = APIRouter(prefix="/api/dining", tags=["dining"])

# Enough to be useful without returning an unscrollable wall. Applied
# AFTER restriction ranking, never before -- see nearby_restaurants.
MAX_RESULTS = 50


@router.get("/geocode", response_model=list[GeocodeResult])
async def geocode_location(query: str):
    """Backlog B10.1 follow-up (author-requested 2026-08-02): an address
    or zip code as a third way to set a search location, alongside manual
    lat/lon and browser geolocation (the latter needing a secure context
    and, even then, able to hang or fail on some devices -- see
    PROJECT-PLAN.md's geolocation bug-fix notes). Returns candidate
    matches for the frontend to let the user disambiguate, rather than
    silently trusting the first result is the right one."""
    query = query.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Provide an address or zip code.")
    try:
        results = await dining_service.geocode(query)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not reach OpenStreetMap's geocoding service: {exc}"
        ) from exc
    if not results:
        raise HTTPException(status_code=404, detail=f'Couldn\'t find a location matching "{query}".')
    return results


@router.get("/geolocate-by-ip", response_model=IPGeolocationResult)
async def geolocate_by_ip():
    """Backlog B10.1 follow-up (author-requested 2026-08-02, second
    round): a third location option. This is NOT the browsing device's
    location -- it's the backend container's own outbound IP, which for
    this app's normal self-hosted-on-the-home-network deployment shape
    is the household's real ISP-assigned address (a genuine, useful
    city-level fix), but would be wrong if accessed remotely. See
    dining_service.py's IPWHOIS_URL comment for the full reasoning --
    the frontend labels this clearly as approximate/network-based rather
    than implying GPS-level precision."""
    try:
        result = await dining_service.geolocate_by_ip()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not reach the IP geolocation service: {exc}"
        ) from exc
    if not result:
        raise HTTPException(status_code=404, detail="Could not determine an approximate location from this network.")
    return result


@router.get("/nearby", response_model=list[RestaurantCandidate])
async def nearby_restaurants(
    lat: float,
    lon: float,
    radius_km: float = 5.0,
    db: Session = Depends(get_db),
):
    """Manual lat/lon input is always required here -- the frontend
    supports EITHER typing coordinates directly OR browser geolocation
    (best-effort, since it needs HTTPS outside localhost, and this app
    is commonly served over plain HTTP on a LAN), per the backlog's own
    "manual entry always available" requirement."""
    radius_m = max(100, min(int(radius_km * 1000), 20000))  # sane bounds -- 100m to 20km
    try:
        places = await dining_service.search_nearby_restaurants(lat, lon, radius_m)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not reach OpenStreetMap's Overpass API: {exc}"
        ) from exc

    prefs = db.query(HouseholdPreferences).first()
    restricted = (prefs.restricted_allergens or []) if prefs else []
    observance = prefs.gluten_observance_level if prefs else None

    # Evaluate EVERY result before capping, then rank, then cap.
    #
    # The order used to be the other way round -- `places[:50]` (nearest
    # first) and only then evaluate -- which in a dense area threw away
    # every tagged match beyond the 50th-nearest venue. The restriction
    # check is pure local computation over data already fetched, so
    # running it on the full result set costs nothing.
    evaluated = [
        {**place, **dining_service.evaluate_restrictions(place, restricted, observance)} for place in places
    ]
    evaluated.sort(key=dining_service.restriction_sort_key)
    return evaluated[:MAX_RESULTS]
