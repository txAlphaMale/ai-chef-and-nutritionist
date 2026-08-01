"""Backlog B10.1 (author-requested group, 2026-08-01): a dining-out
finder -- restaurants near a given location, checked deterministically
against the household's actual restriction taxonomy (allergen_service.py)
rather than an LLM guessing at safety.

RESEARCH, verified live this session rather than assumed:
- Find Me Gluten Free (the celiac-tooling category leader) publishes no
  public API -- confirmed again, same finding as the original B10.1
  backlog research.
- OpenStreetMap's `diet:*` tag namespace (wiki.openstreetmap.org/wiki/
  Key:diet:*, fetched live) is free, keyless, and has an APPROVED tag
  for exactly this household's stated need: `diet:gluten_free`, with
  documented values `only`/`yes`/`no` across the whole `diet:*`
  namespace's own page, and `diet:gluten_free`'s OWN dedicated page
  additionally documenting a fourth value, `limited` (flagged there as
  "dubious/under discussion," not an official addition to the general
  namespace) -- checked both pages separately rather than assuming they
  agree, since the general Key:diet:* page's own "Possible tagging
  mistakes" section explicitly lists `diet:vegetarian=limited` as a
  MISTAKE to avoid, while diet:gluten_free's own page treats it as a
  real, if contested, value. All four values are handled by
  `evaluate_restrictions()` below.
- CRITICAL LIMITATION, found by reading the wiki's own "Diet types"
  table directly rather than assuming coverage: OSM's `diet:*` namespace
  has NO tag for 7 of this app's 10 allergen-taxonomy entries (eggs,
  fish, shellfish, tree_nuts, peanuts, soybeans, sesame --
  allergen_service.ALLERGEN_CHOICES). The namespace covers dietary
  PATTERNS (vegetarian/vegan/halal/kosher/gluten_free/dairy_free/keto/
  etc.), not individual FDA-style allergen avoidance. This is a hard,
  structural gap -- surfaced explicitly in every search result via
  `allergens_with_no_data_source`, never silently dropped or glossed
  over as "looks fine."

SAFETY FRAMING (the backlog's own explicit requirement): this module
NEVER asserts a restaurant is safe. It reports what a crowd-sourced OSM
tag says (or that none exists), always paired with a caution to verify
directly with the restaurant, and treats a missing tag as UNKNOWN, not
as an implicit "no allergens present." Even a `diet:gluten_free=only`
tag is a crowd-sourced claim about general menu composition, not a
guarantee against a specific kitchen's cross-contact practices on a
given day -- the caution message says so explicitly.

Tavily enrichment (the backlog's OPTIONAL secondary data source, for
surfacing menu pages/recent reviews) is deliberately NOT built in this
pass -- the backlog's own text calls it optional, and the required,
safety-relevant piece is the deterministic OSM check above. A
reasonable follow-up once real usage shows whether the in-app OSM-only
result is enough on its own.
"""
from __future__ import annotations

import math

import httpx

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT = 20.0

# The diet:* keys this app actually surfaces per result -- restricted to
# ones with real coverage in this app's own allergen taxonomy plus the
# handful of broader dietary-pattern tags a household might also find
# useful to see (vegetarian/vegan/halal/kosher), even though those
# aren't part of the allergen-restriction check itself.
DIET_TAG_KEYS = [
    "diet:gluten_free",
    "diet:dairy_free",
    "diet:vegetarian",
    "diet:vegan",
    "diet:halal",
    "diet:kosher",
]

# Only these two of allergen_service.ALLERGEN_CHOICES' ten entries have
# a corresponding OSM diet:* tag at all -- see module docstring.
_ALLERGEN_TO_OSM_DIET_KEY: dict[str, str] = {
    "gluten": "diet:gluten_free",
    "milk": "diet:dairy_free",
}


def build_overpass_query(lat: float, lon: float, radius_m: int) -> str:
    """Pure and unit-testable without a network call. Fetches
    amenity=restaurant/cafe/fast_food nodes AND ways within radius_m of
    (lat, lon). `out tags center` returns every tag present (Overpass QL
    has no server-side "only these keys" filter) plus a `center` point
    for way results, which parse_overpass_response uses since a way
    (e.g. a food court building) has no single lat/lon of its own."""
    return (
        f"[out:json][timeout:25];\n"
        f"(\n"
        f'  node["amenity"~"^(restaurant|cafe|fast_food)$"](around:{radius_m},{lat},{lon});\n'
        f'  way["amenity"~"^(restaurant|cafe|fast_food)$"](around:{radius_m},{lat},{lon});\n'
        f");\n"
        f"out tags center;"
    )


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0  # Earth radius, meters
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _format_address(tags: dict) -> str | None:
    parts = [tags.get("addr:housenumber"), tags.get("addr:street")]
    line = " ".join(p for p in parts if p)
    city = tags.get("addr:city")
    if city:
        line = f"{line}, {city}" if line else city
    return line or None


def parse_overpass_response(data: dict, origin_lat: float, origin_lon: float) -> list[dict]:
    """Pure -- turns raw Overpass JSON `elements` into a normalized,
    distance-sorted list. No network call, independently testable
    against a fixture, kept separate from search_nearby_restaurants
    (which does the actual HTTP call) for exactly that reason."""
    places = []
    for el in data.get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name")
        if not name:
            continue  # an unnamed node isn't a usable, presentable result
        if el.get("type") == "node":
            lat, lon = el.get("lat"), el.get("lon")
        else:
            center = el.get("center") or {}
            lat, lon = center.get("lat"), center.get("lon")
        if lat is None or lon is None:
            continue
        diet_tags = {k: tags[k] for k in DIET_TAG_KEYS if k in tags}
        places.append(
            {
                "osm_type": el.get("type"),
                "osm_id": el.get("id"),
                "name": name,
                "lat": lat,
                "lon": lon,
                "distance_m": round(_haversine_m(origin_lat, origin_lon, lat, lon), 1),
                "amenity": tags.get("amenity"),
                "cuisine": tags.get("cuisine"),
                "address": _format_address(tags),
                "diet_tags": diet_tags,
            }
        )
    places.sort(key=lambda p: p["distance_m"])
    return places


async def search_nearby_restaurants(lat: float, lon: float, radius_m: int = 5000) -> list[dict]:
    """The only network call in this module -- everything else here is
    pure and independently testable. No live Overpass network reaches
    the dev/test sandbox this app is built in (same standing constraint
    as USDA/Ollama/every other external service this project talks to),
    so this function itself is verified only by its two pure halves
    (build_overpass_query, parse_overpass_response) plus a live curl
    pass against the actual real deployment target once network access
    exists there."""
    query = build_overpass_query(lat, lon, radius_m)
    async with httpx.AsyncClient(timeout=OVERPASS_TIMEOUT) as client:
        response = await client.post(OVERPASS_URL, data={"data": query})
        response.raise_for_status()
    return parse_overpass_response(response.json(), lat, lon)


def evaluate_restrictions(place: dict, restricted_allergens: list[str], gluten_observance_level: str | None) -> dict:
    """Deterministic, never-guessing safety evaluation against the
    household's actual restricted_allergens/gluten_observance_level
    (HouseholdPreferences, same fields B3.1/B3.2 already added). Returns
    a per-allergen verdict ('only'/'yes'/'limited'/'no'/'unknown'/
    'no_data') plus which restricted allergens OSM has no tag for at
    all -- never collapses to a single "safe"/"unsafe" boolean, since
    that would be asserting a confidence this data source doesn't
    support (the backlog's own explicit safety-framing requirement)."""
    diet_tags = place.get("diet_tags") or {}
    per_allergen: dict[str, str] = {}
    no_data_source: list[str] = []
    for allergen in restricted_allergens:
        osm_key = _ALLERGEN_TO_OSM_DIET_KEY.get(allergen)
        if not osm_key:
            per_allergen[allergen] = "no_data"
            no_data_source.append(allergen)
            continue
        per_allergen[allergen] = diet_tags.get(osm_key, "unknown")

    gluten_flagged = "gluten" in restricted_allergens
    if gluten_flagged and gluten_observance_level == "strict_no_cross_contact":
        caution = (
            "This tag is a crowd-sourced claim about general menu composition, not a guarantee against "
            "cross-contact in a specific kitchen. Given a celiac-strict restriction, call ahead and confirm "
            "gluten-free preparation practices before ordering, even for an 'only' or 'yes' result."
        )
    elif gluten_flagged:
        caution = (
            "This tag is crowd-sourced and may be outdated. Confirm gluten-free options directly with the "
            "restaurant before ordering."
        )
    else:
        caution = "Always confirm dietary needs directly with the restaurant before ordering."

    return {
        "per_allergen": per_allergen,
        "allergens_with_no_data_source": no_data_source,
        "caution": caution,
    }
