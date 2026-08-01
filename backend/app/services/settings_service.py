"""DB-backed, GUI-editable settings -- the author's stated preference
(2026-07-30) is that user-customizable configuration lives in the
database rather than requiring `.env` edits, so it can be changed from
the Settings UI (Phase 8) without a container rebuild. `.env` is kept
only for true infra bootstrap values (DATABASE_URL, container ports)
that have to exist before the database is even reachable.

SETTING_SPECS is the single registry of what's editable: key, a human
label for the future Settings UI, whether it's a secret (-> encrypted at
rest via secrets_crypto), a default, and a description. Adding a new
setting means adding one entry here plus, if relevant, a seed.py
consumer -- no schema change needed since AppSetting is a generic
key/value table.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import AppSetting
from app.services import secrets_crypto


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    is_secret: bool
    default: str
    description: str
    env_fallback: str | None = None  # optional env var read only at first-run seed time


SETTING_SPECS: list[SettingSpec] = [
    SettingSpec(
        key="ollama_base_url",
        label="Ollama base URL",
        is_secret=False,
        default="http://host.docker.internal:11434",
        description="Base URL where Ollama is reachable from inside the backend container.",
        env_fallback="OLLAMA_BASE_URL",
    ),
    SettingSpec(
        key="ollama_chat_model",
        label="Ollama chat model",
        is_secret=False,
        default="qwen3.5:9b",
        description=(
            "Ollama model used for chat, meal planning, and recipe generation. "
            "Chosen (2026-07-31) to comfortably fit a single 11GB-class GPU (e.g. "
            "a GTX 1080 Ti) with headroom, leaving a second GPU free for the "
            "vision model -- a larger dense model like qwen3.6:27b is a "
            "reasonable upgrade on beefier/single-larger-GPU hardware, but "
            "doesn't fit one 11GB card and Ollama's multi-GPU split doesn't "
            "speed up a single request, only lets a bigger model fit."
        ),
        env_fallback="OLLAMA_CHAT_MODEL",
    ),
    SettingSpec(
        key="ollama_vision_model",
        label="Ollama vision model",
        is_secret=False,
        default="qwen2.5vl:7b",
        description=(
            "Vision-capable Ollama model used for inventory photo intake and "
            "recipe photo import. Qwen2.5-VL was trained on substantially more "
            "structured/document visual content than LLaVA, which matters for "
            "reading labels, nutrition facts, and (future) receipts."
        ),
        env_fallback="OLLAMA_VISION_MODEL",
    ),
    SettingSpec(
        key="ollama_embed_model",
        label="Ollama embedding model",
        is_secret=False,
        default="bge-m3",
        description="Ollama embedding model used to chunk/embed knowledge files for retrieval-based grounding.",
        env_fallback="OLLAMA_EMBED_MODEL",
    ),
    SettingSpec(
        key="tavily_api_key",
        label="Tavily API key",
        is_secret=True,
        default="",
        description="API key for Tavily web search, used to ground recipe/nutrition lookups.",
        env_fallback="TAVILY_API_KEY",
    ),
    SettingSpec(
        key="ui_theme",
        label="Theme",
        is_secret=False,
        default="default",
        description=(
            "Color theme key for the frontend (see frontend/src/themes.js's "
            "THEME_OPTIONS for the full, authoritative list -- 'default' plus "
            "four themes ported from the Fiduciary project (amber, cobalt, "
            "highcontrast, daylight) and all four Catppuccin flavors "
            "(catppuccin-latte/frappe/macchiato/mocha). Edited via the "
            "Settings page's Appearance picker, not this raw field -- present "
            "here mainly so it round-trips through the same generic settings "
            "API as everything else and persists in the database like every "
            "other user preference."
        ),
    ),
    SettingSpec(
        key="default_unit_system",
        label="Default unit system",
        is_secret=False,
        default="original",
        description=(
            "Backlog B10.5 -- how a recipe's ingredient quantities render by "
            "default: 'original' (as entered/imported), 'metric' (ml/l, g/kg), "
            "'imperial' (tsp/tbsp/cup/etc., oz/lb), or 'weight' (grams/kg for "
            "everything, including volume-measured ingredients with a known "
            "density -- the mode most useful for GF baking precision). Edited "
            "via the unit selector on a recipe's detail page, which saves back "
            "here the same way the Appearance theme picker does."
        ),
    ),
    SettingSpec(
        key="usda_fdc_api_key",
        label="USDA FoodData Central API key",
        is_secret=True,
        default="",
        description=(
            "Free API key from https://api.data.gov/signup/, used to resolve recipe "
            "ingredients against USDA's authoritative food-composition database "
            "instead of relying solely on AI-estimated nutrition. Optional -- "
            "ingredient resolution falls back to Open Food Facts (no key needed) "
            "and, failing that, recipes keep using AI-estimated nutrition as before."
        ),
        env_fallback="USDA_FDC_API_KEY",
    ),
    SettingSpec(
        key="household_timezone",
        label="Household timezone",
        is_secret=False,
        default="America/Chicago",
        description=(
            "IANA timezone name (e.g. America/Chicago, America/New_York, "
            "Europe/London) for this household's kitchen. Backlog B12.1 -- "
            "unlike the .ics export (B9.5), which deliberately uses 'floating' "
            "times with no timezone, Google Calendar events need a real one so "
            "a meal planned for 6pm actually shows at 6pm rather than shifting "
            "with whichever timezone the viewing device happens to be in."
        ),
    ),
    # ---- Backlog B12.1: Google Calendar push sync. Everything below is
    # either a value the household types in once (client id/secret/
    # redirect_uri, sourced from a Google Cloud OAuth client the household
    # registers themselves -- see the in-app WIKI's Google Calendar setup
    # guide) or a value google_calendar_service writes automatically as a
    # side effect of the OAuth connect flow (refresh token, calendar id,
    # account email, sync toggle) -- never hand-typed. All round-trip
    # through the same generic settings API as every other setting; kept
    # in the DB (not a dedicated table) since this is exactly the
    # "GUI-editable config" this registry already exists for, and a
    # household only ever has one Google connection, matching the
    # existing single-row-of-truth shape of tavily_api_key etc.
    SettingSpec(
        key="google_calendar_client_id",
        label="Google OAuth client ID",
        is_secret=False,
        default="",
        description=(
            "From your own Google Cloud project's OAuth client (Web application "
            "type) -- see the in-app WIKI: Getting started -> Google Calendar "
            "setup for the full walkthrough. Not a secret by itself (Google "
            "treats it as a public identifier), but useless without the client "
            "secret below."
        ),
    ),
    SettingSpec(
        key="google_calendar_client_secret",
        label="Google OAuth client secret",
        is_secret=True,
        default="",
        description="The client secret paired with the client ID above, from the same Google Cloud OAuth client.",
    ),
    SettingSpec(
        key="google_calendar_redirect_uri",
        label="Google OAuth redirect URI",
        is_secret=False,
        default="",
        description=(
            "Must exactly match one of the 'Authorized redirect URIs' registered "
            "on your Google Cloud OAuth client -- typically "
            "http://<this-machine's-address>:<backend port>/api/calendar/google/callback. "
            "Left blank by default since it depends on your deployment's "
            "reachable address; the WIKI guide shows how to work out the right value."
        ),
    ),
    SettingSpec(
        key="google_calendar_refresh_token",
        label="Google Calendar refresh token",
        is_secret=True,
        default="",
        description="Written automatically by the OAuth connect flow -- never entered by hand.",
    ),
    SettingSpec(
        key="google_calendar_calendar_id",
        label="Google Calendar dedicated calendar ID",
        is_secret=False,
        default="",
        description=(
            "The 'Chef Meal Plan' calendar google_calendar_service creates "
            "automatically on first connect. Written automatically."
        ),
    ),
    SettingSpec(
        key="google_calendar_account_email",
        label="Connected Google account",
        is_secret=False,
        default="",
        description="Display-only -- which Google account is currently connected. Written automatically.",
    ),
    SettingSpec(
        key="google_calendar_sync_enabled",
        label="Google Calendar sync enabled",
        is_secret=False,
        default="false",
        description=(
            "\"true\"/\"false\". Whether meal-plan changes push to the connected "
            "Google Calendar. Turned on automatically on first successful "
            "connect; toggle off any time to pause pushing without disconnecting."
        ),
    ),
]

_SPECS_BY_KEY: dict[str, SettingSpec] = {s.key: s for s in SETTING_SPECS}


def get_setting(db: Session, key: str) -> str | None:
    """Returns the decrypted value for `key`, or the spec's default if no
    row exists yet (should only happen if seed.py hasn't run)."""
    spec = _SPECS_BY_KEY.get(key)
    row = db.query(AppSetting).filter_by(key=key).first()
    if row is None:
        return spec.default if spec else None
    if spec and spec.is_secret:
        return secrets_crypto.decrypt_or_legacy(row.value)
    return row.value


def set_setting(db: Session, key: str, value: str) -> AppSetting:
    """Upserts `key` -> `value`, encrypting first if the spec marks it secret."""
    spec = _SPECS_BY_KEY.get(key)
    stored_value = value
    is_secret = spec.is_secret if spec else False
    if is_secret:
        stored_value = secrets_crypto.encrypt(value)

    row = db.query(AppSetting).filter_by(key=key).first()
    if row is None:
        row = AppSetting(
            key=key,
            value=stored_value,
            is_secret=is_secret,
            description=spec.description if spec else None,
        )
        db.add(row)
    else:
        row.value = stored_value
        row.is_secret = is_secret

    db.commit()
    db.refresh(row)
    return row


def is_known_key(key: str) -> bool:
    """True if `key` is a registered setting (i.e. editable through the
    Settings GUI). Lets routers validate without reaching into the
    private _SPECS_BY_KEY lookup directly."""
    return key in _SPECS_BY_KEY


def get_effective_config(db: Session) -> dict[str, str | None]:
    """All known settings, decrypted, keyed by SETTING_SPECS key -- e.g.
    for building the Ollama/Tavily clients."""
    return {spec.key: get_setting(db, spec.key) for spec in SETTING_SPECS}


def list_settings_for_display(db: Session) -> list[dict]:
    """Settings-UI-friendly view: secret values are masked, never
    returned in the clear over the API."""
    out = []
    for spec in SETTING_SPECS:
        row = db.query(AppSetting).filter_by(key=spec.key).first()
        has_value = bool(row and row.value)
        out.append(
            {
                "key": spec.key,
                "label": spec.label,
                "is_secret": spec.is_secret,
                "description": spec.description,
                "value": "********" if spec.is_secret and has_value else (row.value if row else spec.default),
                "is_set": has_value,
            }
        )
    return out
