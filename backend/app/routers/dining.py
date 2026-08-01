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
from app.schemas.dining import RestaurantCandidate
from app.services import dining_service

router = APIRouter(prefix="/api/dining", tags=["dining"])


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

    results = []
    for place in places[:50]:  # cap -- a dense downtown search can return hundreds
        evaluation = dining_service.evaluate_restrictions(place, restricted, observance)
        results.append({**place, **evaluation})
    return results
