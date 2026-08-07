"""DB-backed, GUI-editable settings. User-customizable configuration
lives in the database rather than in `.env`, so it can be changed from
the Settings UI without a container rebuild. `.env` is kept
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
from app.services.ingredient_resolution_service import DEFAULT_TRANSFORMATION_WORDS

# The curated default for the `ingredient_transformation_words` spec
# below, rendered as the comma-separated text the Settings UI edits.
# Sourced from the matcher's own constant rather than retyped here, so
# the shipped default and the code default cannot drift apart.
# ingredient_resolution_service imports settings_service only lazily
# (inside load_transformation_words), so this direction is safe.
_DEFAULT_TRANSFORMATION_WORDS_CSV = ", ".join(DEFAULT_TRANSFORMATION_WORDS)


@dataclass(frozen=True)
class SettingSpec:
    key: str
    label: str
    is_secret: bool
    default: str
    description: str
    env_fallback: str | None = None  # optional env var read only at first-run seed time
    # Fixed set of valid values, e.g. ["original", "metric", "imperial",
    # "weight"] for default_unit_system -- when set, the Settings UI
    # renders a <select> instead of a free-text box, since a value
    # outside this set isn't just "unusual", it's silently ignored by
    # every consumer (recipe_service.scale_ingredients/apply_unit_system
    # already only recognize these exact strings and pass through
    # anything else as a no-op "original"). None means "no fixed set" --
    # arbitrary text is genuinely correct there (an Ollama model name, a
    # URL, an API key).
    options: list[str] | None = None


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
        default="qwen3.6:27b",
        description=(
            "Ollama model used for chat, meal planning, recipe generation, and "
            "receipt/list import. Smaller models in this family bail out with an "
            "empty response on moderately complex prompts, so prefer a larger one "
            "if generation returns nothing. Ollama splits a model across multiple "
            "visible GPUs automatically when it does not fit on one, so the budget "
            "here is the combined VRAM of every GPU Ollama can see, not a single "
            "card. Non-thinking sampling guidance and think=False handling apply "
            "to this model family."
        ),
        env_fallback="OLLAMA_CHAT_MODEL",
    ),
    SettingSpec(
        key="ollama_extraction_model",
        label="Ollama extraction model (optional)",
        is_secret=False,
        default="",
        description=(
            "Ollama model used for structured extraction from a recipe or "
            "document -- reading an ingredient list out of a PDF, photo or "
            "pasted text. Leave blank to use the chat model. "
            "Set it when extraction and conversation want different models: "
            "extraction is a short, exacting, schema-constrained job that a "
            "larger model does better, while chat is long-running and shares "
            "the same single worker thread, so making every message pay for a "
            "bigger model to fix imports is a poor trade. Blank is the safe "
            "default and changes nothing."
        ),
        env_fallback="OLLAMA_EXTRACTION_MODEL",
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
        key="ollama_num_ctx",
        label="Ollama context window (tokens)",
        is_secret=False,
        default="8192",
        description=(
            "Maximum prompt+response length (in tokens) sent to Ollama on every "
            "call. Ollama's own default is a conservative 2048 tokens if this "
            "isn't set explicitly -- and critically, it does NOT error when a "
            "prompt exceeds this: it silently clips the overflow with zero "
            "warning, which is a real, hard-to-diagnose failure mode (e.g. a "
            "long receipt or recipe import producing an empty or garbled "
            "result with no visible error at all). 8192 comfortably fits this "
            "app's longer prompts (a full receipt/recipe plus instructions) "
            "with headroom; raise it further for very long imports, or lower "
            "it if a smaller/quantized model runs out of VRAM at this size."
        ),
        env_fallback="OLLAMA_NUM_CTX",
    ),
    SettingSpec(
        key="ollama_timeout_seconds",
        label="Ollama request timeout (seconds)",
        is_secret=False,
        default="600",
        description=(
            "How long to wait for a single Ollama generation before giving up. "
            "The ollama-python client's own default is NO timeout at all, which "
            "means one stalled generation (a model still loading into VRAM, a "
            "large model thrashing, a network blip to the Ollama host) blocks "
            "the app's single background worker thread forever -- taking down "
            "chat, imports, vision intake and meal planning together, with no "
            "error shown. This bounds that. Raise it if a genuinely slow model "
            "on this hardware needs longer than 10 minutes for one call; the "
            "connect timeout is separate and always short."
        ),
        env_fallback="OLLAMA_TIMEOUT_SECONDS",
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
        # Constrained so the UI renders a <select>: recipe_service's
        # apply_unit_system only recognizes these four exact strings, and
        # any other typed value silently behaves like "original".
        options=["original", "metric", "imperial", "weight"],
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
        key="openfda_api_key",
        label="openFDA API key",
        is_secret=True,
        default="",
        description=(
            "Optional. Backlog B3.3's recall check works against openFDA's food "
            "enforcement API with no key at all; a free key from "
            "https://open.fda.gov/apis/authentication/ just raises the shared "
            "rate limit, which only matters for a very large inventory checked "
            "very frequently. The USDA FSIS recall check (the other half of B3.3) "
            "needs no key at all, with or without this one set."
        ),
        env_fallback="OPENFDA_API_KEY",
    ),
    SettingSpec(
        key="recipe_import_folder_path",
        label="Recipe import folder path",
        is_secret=False,
        default="",
        description=(
            "Backlog B13.1 -- a path INSIDE the backend container to scan for "
            "recipe files (text/markdown/PDF/HTML/schema.org JSON) to batch-import, "
            "e.g. a OneDrive/Dropbox/Google Drive folder that already syncs to your "
            "server as a normal directory. Not a cloud API integration -- point "
            "docker-compose.yml at the real host folder first (see the in-app WIKI's "
            "'Recipe folder import' entry for the exact volume-mount line), then set "
            "this to wherever that ends up mounted inside the container (commonly "
            "/app/data/recipe_import). Files are only ever READ, never modified or "
            "deleted."
        ),
        env_fallback="RECIPE_IMPORT_FOLDER_PATH",
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
            "on your Google Cloud OAuth client. Google's own validation REJECTS a "
            "raw LAN IP address here (e.g. http://10.11.24.21:5173/...) even though "
            "that's how most LAN-hosted apps are normally reached -- only a real "
            "domain name or http://localhost:<port> (any port, plain HTTP, no "
            "certificate needed) is accepted. The Settings page auto-suggests "
            "localhost for this reason; see the in-app WIKI's Google Calendar setup "
            "entry for what that means for who can click 'Connect' and for a "
            "domain-based alternative if you'd rather not be limited to the server "
            "machine itself."
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
            '"true"/"false". Whether meal-plan changes push to the connected '
            "Google Calendar. Turned on automatically on first successful "
            "connect; toggle off any time to pause pushing without disconnecting."
        ),
    ),
    # ---- Backlog B12.2: iCloud Calendar push sync, the same
    # one-way-push architecture as B12.1 above but authenticated with an
    # app-specific password instead of OAuth -- see
    # icloud_calendar_service.py's module docstring for the full
    # rationale. `icloud_calendar_calendar_href` is written automatically
    # (the discovered/created "Chef Meal Plan" CalDAV collection URL),
    # same "never hand-typed" treatment google_calendar_calendar_id gets.
    SettingSpec(
        key="icloud_calendar_username",
        label="iCloud Apple ID",
        is_secret=False,
        default="",
        description="The Apple ID (email address) that owns the iCloud calendar to sync into.",
    ),
    SettingSpec(
        key="icloud_calendar_app_password",
        label="iCloud app-specific password",
        is_secret=True,
        default="",
        description=(
            "An app-specific password generated at appleid.apple.com (Sign-In and Security -> "
            "App-Specific Passwords) -- NOT your normal Apple ID password, which iCloud's CalDAV "
            "server will reject outright for a third-party client. See the in-app WIKI's iCloud "
            "Calendar setup guide."
        ),
    ),
    SettingSpec(
        key="icloud_calendar_calendar_href",
        label="iCloud Calendar dedicated calendar URL",
        is_secret=False,
        default="",
        description=(
            "The 'Chef Meal Plan' CalDAV collection icloud_calendar_service discovers/creates "
            "automatically on first connect. Written automatically."
        ),
    ),
    SettingSpec(
        key="icloud_calendar_sync_enabled",
        label="iCloud Calendar sync enabled",
        is_secret=False,
        default="false",
        description=(
            '"true"/"false". Whether meal-plan changes push to the connected iCloud '
            "Calendar. Turned on automatically on first successful connect; toggle off any time "
            "to pause pushing without disconnecting."
        ),
    ),
    SettingSpec(
        key="cook_timer_max_widgets",
        label="Cook mode: maximum simultaneous timers",
        is_secret=False,
        default="3",
        description=(
            "How many cooking timers can run at once. Three covers the usual case -- something "
            "in the oven, something on the hob, something resting -- and the cap exists so the "
            "cook-mode panel stays readable at a glance from across the kitchen rather than "
            "becoming a list to search. Raise it if you routinely run more."
        ),
    ),
    SettingSpec(
        key="cors_allow_origins",
        label="Extra allowed browser origins (CORS)",
        is_secret=False,
        default="",
        description=(
            "Comma-separated, and empty is correct for a normal setup. Chef serves the "
            "app and the API from one origin, so every request your browser makes is "
            "already same-origin and needs no CORS permission at all. "
            "Add an entry here only if a web page served from a DIFFERENT origin needs "
            "to call this API from a browser -- for example after adding a local DNS "
            "name like http://chef.lan:5173 while the app still knows itself by IP. "
            "Include the scheme and port, e.g. http://chef.lan:5173. Scripts and curl "
            "are unaffected either way: CORS is enforced by browsers, not by this API. "
            "Takes effect on container restart."
        ),
        env_fallback="CORS_ALLOW_ORIGINS",
    ),
    SettingSpec(
        key="ingredient_transformation_words",
        label="Ingredient transformation words",
        is_secret=False,
        default=_DEFAULT_TRANSFORMATION_WORDS_CSV,
        description=(
            'Comma-separated. Audit finding P1-5. Words that mean "a product made FROM '
            'an ingredient" rather than "a specific kind of that ingredient". When one '
            "of these appears in an inventory item's name but not in the ingredient being "
            "looked up (or vice versa), the two are treated as different foods and never "
            'matched -- this is what stops a recipe\'s "chicken" being deducted from a '
            'carton of "chicken broth", or "almond" from "almond milk". '
            "Editable because no fixed list can be complete: add a word if you find a pair "
            "the matcher wrongly treats as the same ingredient, remove one if it is "
            "blocking a match you actually want. Clearing this box entirely restores the "
            "shipped default list. Getting it wrong is safe in both directions -- a "
            "missing word downgrades a match to a suggestion you confirm rather than "
            "applying it silently, and an extra word makes the app ask which item you "
            "meant rather than picking the wrong one."
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
                "options": spec.options,
            }
        )
    return out
