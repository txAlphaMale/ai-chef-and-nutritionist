"""Dining out: restaurants near a location, checked deterministically
against the household's own restriction taxonomy (allergen_service.py)
rather than an LLM guessing at safety.

**Safety framing, which governs everything below: this module never
asserts a restaurant is safe.** It reports what a crowd-sourced OSM tag
says, or that no tag exists, always paired with a caution to verify with
the restaurant directly. A missing tag is UNKNOWN, never an implicit "no
allergens present". Even `diet:gluten_free=only` is a crowd-sourced claim
about general menu composition, not a guarantee about a given kitchen's
cross-contact practices on a given day.

Data source: OpenStreetMap's `diet:*` tag namespace, via Overpass. Free,
keyless, and it has an approved tag for the household's stated need.
Find Me Gluten Free -- the obvious alternative -- publishes no public API.

Two facts about that namespace that the code has to respect:

- **Values.** The general `Key:diet:*` page documents `only`/`yes`/`no`.
  `diet:gluten_free`'s own page documents a fourth, `limited`, flagged
  there as contested. The two pages genuinely disagree: the general
  page's "Possible tagging mistakes" section lists `diet:vegetarian=
  limited` as a mistake, while diet:gluten_free's page treats `limited`
  as real. `evaluate_restrictions()` handles all four.
- **Coverage gap, structural and unfixable here.** `diet:*` describes
  dietary PATTERNS (vegetarian/vegan/halal/kosher/gluten_free/dairy_free/
  keto), not FDA-style individual allergen avoidance. It has no tag for 7
  of this app's 10 allergens: eggs, fish, shellfish, tree nuts, peanuts,
  soybeans, sesame. Every search result reports this explicitly via
  `allergens_with_no_data_source` -- it is never silently dropped, and
  absence of a tag is never rendered as "looks fine".

Tavily enrichment (menu pages, recent reviews) is not built: the
deterministic OSM check is the safety-relevant half, and a second source
would only add confidence this module deliberately declines to express.
"""

from __future__ import annotations

import math

import httpx

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
# One fallback mirror, because the primary is a shared community
# instance: overpass-api.de's usage policy asks callers to stay under
# 10,000 queries and 1GB per day, and promises no uptime in return. A 504
# from it means the public instance is overloaded, not that the request
# was wrong. private.coffee (formerly overpass.kumi.systems) is a listed
# global-coverage mirror with no rate limit of its own.
OVERPASS_FALLBACK_URL = "https://overpass.private.coffee/api/interpreter"
OVERPASS_TIMEOUT = 20.0

# Geocoding, so an address or zip code works as well as coordinates or
# browser geolocation -- the last of which needs a secure context and can
# still hang on some devices. Nominatim is OSM's own free, keyless
# geocoder: same data family as the Overpass search above, and no
# per-user API key to distribute with a self-hosted repo.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_TIMEOUT = 15.0

# A third location option alongside GPS and a typed address.
#
# KNOW WHAT THIS RETURNS: it geolocates the BACKEND CONTAINER's outbound
# IP, not the browsing device's. On the normal deployment -- self-hosted,
# household and backend on one home network -- those are the same ISP
# address, so it gives a genuine city-level fix. Accessed remotely (VPN,
# travelling, a cloud host) it reports the SERVER's location instead,
# which is wrong for the user's purpose. That is why the frontend labels
# it "approximate (network-based)" rather than implying device accuracy.
#
# ipwho.is for the same free/keyless reasoning as Nominatim and Overpass;
# it returns latitude/longitude/city/region/country plus a `success` bool.
IPWHOIS_URL = "https://ipwho.is/"
IPWHOIS_TIMEOUT = 10.0

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
        # Relations too: a venue mapped as a multipolygon (a mall food
        # court, a building with a courtyard) is a relation, not a node or
        # a way, and was silently missing from every search.
        f'  relation["amenity"~"^(restaurant|cafe|fast_food)$"](around:{radius_m},{lat},{lon});\n'
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
            # Ways and relations have no coordinate of their own; `out
            # center` supplies a representative point for both.
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
                # Contact and hours were being fetched and thrown away.
                # `out tags center` returns EVERY tag on the element, so
                # all of this was already on the wire and already paid
                # for -- the parser simply never read it, leaving results
                # with no way to call ahead (the one thing the safety
                # framing everywhere else in this module tells the user to
                # do) or check a menu.
                "website": tags.get("website") or tags.get("contact:website"),
                "phone": tags.get("phone") or tags.get("contact:phone"),
                "opening_hours": tags.get("opening_hours"),
                "wheelchair": tags.get("wheelchair"),
                # Deep link to the venue on OSM. Built from data already
                # present rather than adding a mapping dependency; the
                # frontend also renders a plain coordinate link so any map
                # app can take over.
                "map_url": _osm_map_url(el.get("type"), el.get("id"), lat, lon),
            }
        )
    places.sort(key=lambda p: p["distance_m"])
    return places


def _osm_map_url(osm_type: str | None, osm_id, lat: float, lon: float) -> str:
    """A link to the venue's own OSM page when we have its identity, else
    a pin at its coordinates. Keyless, no tile-server dependency, and it
    carries the venue's full tag list -- which is genuinely useful here,
    since a household verifying a gluten-free claim wants to see what the
    underlying data actually says and when it was last touched."""
    if osm_type in ("node", "way", "relation") and osm_id is not None:
        return f"https://www.openstreetmap.org/{osm_type}/{osm_id}"
    return f"https://www.openstreetmap.org/?mlat={lat}&mlon={lon}#map=18/{lat}/{lon}"


async def search_nearby_restaurants(lat: float, lon: float, radius_m: int = 5000) -> list[dict]:
    """The only network call in this module -- everything else here is
    pure and independently testable. No live Overpass network reaches
    the dev/test sandbox this app is built in (same standing constraint
    as USDA/Ollama/every other external service this project talks to),
    so this function itself is verified only by its two pure halves
    (build_overpass_query, parse_overpass_response) plus a live curl
    pass against the actual real deployment target once network access
    exists there.

    Tries the main instance, then OVERPASS_FALLBACK_URL, but ONLY on a
    server-side failure (5xx, timeout, connection error). A 4xx is
    deliberately not retried: a malformed query fails identically on the
    mirror, so retrying would only delay surfacing a real bug here."""
    query = build_overpass_query(lat, lon, radius_m)
    urls = (OVERPASS_URL, OVERPASS_FALLBACK_URL)
    for attempt, url in enumerate(urls):
        is_last_attempt = attempt == len(urls) - 1
        try:
            async with httpx.AsyncClient(timeout=OVERPASS_TIMEOUT) as client:
                # The User-Agent is required, not decorative:
                # overpass-api.de returns 406 for a default HTTP-client
                # User-Agent as an anti-abuse measure. Same courtesy
                # identification Nominatim's usage policy asks for in
                # writing, just enforced by the server here -- so one
                # header serves both OSM services.
                response = await client.post(url, data={"data": query}, headers=_osm_headers())
                response.raise_for_status()
            return parse_overpass_response(response.json(), lat, lon)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500 or is_last_attempt:
                raise
        except (httpx.TimeoutException, httpx.TransportError):
            if is_last_attempt:
                raise
    raise AssertionError("unreachable -- the loop above always returns or raises on its last attempt")


def _osm_headers() -> dict:
    # Nominatim's usage policy (operations.osmfoundation.org/policies/
    # nominatim/) requires a real, identifying User-Agent -- generic/
    # default HTTP client user-agents are explicitly disallowed and can
    # get silently rate-limited or blocked. No API key involved, this
    # header is the only non-optional part of using the service politely.
    # Also sent to Overpass (see search_nearby_restaurants above) since its
    # server enforces the same expectation, just with a hard 406 instead of
    # a documented policy.
    return {"User-Agent": "chef-meal-planner/1.0 (self-hosted personal use; no public deployment)"}


def parse_nominatim_response(data: list) -> list[dict]:
    """Pure -- turns raw Nominatim JSON results into a normalized list of
    candidates. Independently testable without a network call, same
    split as parse_overpass_response above. Skips any entry missing a
    parseable lat/lon rather than raising -- real-world geocoder
    responses occasionally include partial/malformed entries."""
    results = []
    for entry in data or []:
        if not isinstance(entry, dict):
            continue
        try:
            lat = float(entry["lat"])
            lon = float(entry["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        results.append(
            {
                "lat": lat,
                "lon": lon,
                "display_name": entry.get("display_name") or f"{lat}, {lon}",
            }
        )
    return results


async def geocode(query: str, limit: int = 5) -> list[dict]:
    """The only other network call in this module, same "pure parser +
    thin async network wrapper" split as search_nearby_restaurants above.
    Returns up to `limit` candidates rather than just the top hit -- a
    bare zip code or a common street name is genuinely ambiguous, and
    letting the user pick the right one from a short list beats silently
    trusting Nominatim's own ranking to be correct every time."""
    params = {"q": query, "format": "jsonv2", "limit": limit, "addressdetails": 0}
    async with httpx.AsyncClient(timeout=NOMINATIM_TIMEOUT) as client:
        response = await client.get(NOMINATIM_URL, params=params, headers=_osm_headers())
        response.raise_for_status()
    return parse_nominatim_response(response.json())


def parse_ip_geolocation_response(data: dict) -> dict | None:
    """Pure -- same split as parse_nominatim_response above. Returns None
    (not a raised exception) for a well-formed-but-unsuccessful response
    (ipwho.is's own `success: false`, e.g. for a private/reserved IP,
    which is exactly what a fully offline/LAN-isolated deployment could
    see) or missing/unparseable coordinates, so the router can turn that
    into a clean 404 rather than a 500."""
    if not isinstance(data, dict) or not data.get("success", True):
        return None
    try:
        lat = float(data["latitude"])
        lon = float(data["longitude"])
    except (KeyError, TypeError, ValueError):
        return None
    return {
        "lat": lat,
        "lon": lon,
        "city": data.get("city"),
        "region": data.get("region"),
        "country": data.get("country"),
    }


async def geolocate_by_ip() -> dict | None:
    """The backend's own outbound-IP-based location -- see the
    IPWHOIS_URL module comment above for exactly what this reflects and
    its real deployment-shape caveat."""
    async with httpx.AsyncClient(timeout=IPWHOIS_TIMEOUT) as client:
        response = await client.get(IPWHOIS_URL)
        response.raise_for_status()
    return parse_ip_geolocation_response(response.json())


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


# --- Ranking against the household's restrictions ----------------------
#
# `evaluate_restrictions` below produces a verdict per restricted
# allergen, but nothing used to act on it: results were sorted by distance
# and then cut to the nearest 50. In any moderately dense area that is
# strictly worse than not filtering, because the nearest 50 venues are
# almost all untagged, so a genuinely `diet:gluten_free=only` place three
# kilometres out was discarded before the household ever saw it. The one
# feature here with real safety relevance was the one throwing away the
# relevant results.
#
# Ranking, not filtering, is the right primitive. A missing tag means
# UNKNOWN, never "unsafe" -- OSM coverage is uneven and skews urban, and
# hiding untagged venues would imply a completeness the data does not
# have. So everything is returned, ordered so that what the household can
# actually eat is at the top.
_VERDICT_RANK = {
    "only": 0,  # entire menu fits the restriction
    "yes": 1,  # options available
    "limited": 2,  # contested tag value, but a real signal
    "unknown": 3,  # no tag on this venue -- not a judgement either way
    "no_data": 4,  # OSM has no tag for this allergen at all
    "no": 5,  # explicitly not available
}


def restriction_sort_key(evaluated_place: dict) -> tuple:
    """Sort key placing the best-evidenced matches first, distance second.

    Uses the WORST verdict across all restricted allergens, not the best:
    a place tagged gluten-free but explicitly not dairy-free is not a
    match for a household restricting both, and averaging would hide that.
    A household with no restrictions set gets pure distance ordering,
    which is the sensible default when there is nothing to rank against."""
    per_allergen = (evaluated_place.get("per_allergen") or {}).values()
    worst = max((_VERDICT_RANK.get(v, 3) for v in per_allergen), default=3)
    return (worst, evaluated_place.get("distance_m", 0))


def summarize_coverage(evaluated_places: list[dict]) -> dict:
    """Counts behind the results, so absence of data stays visible instead
    of looking like absence of options -- the safety-framing requirement
    this module is built around, applied to the result set as a whole
    rather than only to individual venues."""
    tagged = sum(
        1
        for p in evaluated_places
        if any(v in ("only", "yes", "limited") for v in (p.get("per_allergen") or {}).values())
    )
    untagged = sum(
        1
        for p in evaluated_places
        if (p.get("per_allergen") or {}) and all(v == "unknown" for v in p["per_allergen"].values())
    )
    return {
        "total": len(evaluated_places),
        "with_matching_tag": tagged,
        "untagged": untagged,
    }
