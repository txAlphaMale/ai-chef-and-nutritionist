"""Recipe business logic: servings scaling, AI-assisted import parsing
(text, PDF, photo, or URL -> structured recipe JSON), and building
context for the recipe-scoped chat feature."""
from __future__ import annotations

import io
import json
import re

import httpx
import trafilatura
from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.models import MealTag, Recipe, RecipeIngredient
from app.services import unit_conversion_service
from app.services.food_data_service import NUTRITION_PROMPT_HINT

# --- Servings scaling --------------------------------------------------
#
# Ingredient quantities scale linearly with the ratio of requested
# servings to the recipe's default_servings. Nutrition is stored
# per-serving (see Recipe model) so it does NOT get multiplied here --
# it's already correct for any serving size; only the shopping-list-
# relevant ingredient amounts need scaling.


def scale_ingredients(ingredients: list[dict], from_servings: int, to_servings: int) -> list[dict]:
    if from_servings <= 0 or to_servings == from_servings:
        return ingredients
    ratio = to_servings / from_servings
    scaled = []
    for ing in ingredients:
        new_ing = dict(ing)
        if new_ing.get("quantity") is not None:
            new_ing["quantity"] = round(new_ing["quantity"] * ratio, 3)
        scaled.append(new_ing)
    return scaled


def apply_display_unit_system(ingredients: list[dict], unit_system: str) -> list[dict]:
    """Backlog B10.5 -- re-renders each ingredient's quantity/unit for a
    requested display system ("metric" | "imperial" | "weight"); a no-op
    passthrough for "original" (or any other/unset value -- treated the
    same as "original" rather than erroring on an unexpected query
    param). Reads and then STRIPS each dict's `density_g_per_ml` key (an
    internal availability signal, never part of the public ingredient
    shape -- see RecipeIngredientRead) and sets `display_unavailable` to
    True when the requested system couldn't be honored for that specific
    ingredient (only possible for weight mode on a volume-quantity
    ingredient with no cached density). Quantity/unit are left exactly
    as `scale_ingredients` already produced in that case -- never a
    guessed conversion."""
    if unit_system not in ("metric", "imperial", "weight"):
        cleaned = []
        for ing in ingredients:
            new_ing = dict(ing)
            new_ing.pop("density_g_per_ml", None)
            new_ing["display_unavailable"] = False
            cleaned.append(new_ing)
        return cleaned

    result = []
    for ing in ingredients:
        new_ing = dict(ing)
        density = new_ing.pop("density_g_per_ml", None)
        quantity = new_ing.get("quantity")
        if quantity is None:
            new_ing["display_unavailable"] = False
            result.append(new_ing)
            continue
        converted = unit_conversion_service.convert_for_display(
            quantity, new_ing.get("unit"), unit_system, density_g_per_ml=density
        )
        if converted is None:
            new_ing["display_unavailable"] = True
        else:
            new_ing["quantity"] = converted.quantity
            new_ing["unit"] = converted.unit
            new_ing["display_unavailable"] = False
        result.append(new_ing)
    return result


# --- Tags: get-or-create by name ---------------------------------------


def resolve_tags(db: Session, tag_names: list[str]) -> list[MealTag]:
    resolved = []
    for raw_name in tag_names:
        name = raw_name.strip().lower().replace(" ", "_")
        if not name:
            continue
        tag = db.query(MealTag).filter_by(name=name).first()
        if tag is None:
            tag = MealTag(name=name)
            db.add(tag)
            db.flush()  # get an id without a full commit
        resolved.append(tag)
    return resolved


def create_recipe_from_parsed(db: Session, parsed: dict, source: str = "ai_generated") -> Recipe:
    """Creates a Recipe (+ ingredients + tags) from a dict shaped like
    coerce_recipe_fields()'s output -- used when meal-plan generation
    (meal_plan_service.py) proposes a brand-new recipe for a slot the
    existing catalog can't fill. Mirrors routers/recipes.py's
    create_recipe wiring without duplicating it wholesale: the input
    shape here is narrower (no source_url/is_staple/image_path -- those
    aren't meaningful for an AI-authored recipe with no external source
    and no explicit staple/photo yet)."""
    recipe = Recipe(
        title=parsed["title"],
        description=parsed.get("description"),
        default_servings=parsed.get("default_servings") or 2,
        prep_time_minutes=parsed.get("prep_time_minutes"),
        cook_time_minutes=parsed.get("cook_time_minutes"),
        instructions=parsed.get("instructions") or [],
        nutrition=parsed.get("nutrition") or {},
        # Backlog B1.2: an AI-generated recipe's nutrition is always a
        # guess at this point (meal-plan generation never resolves
        # ingredients against USDA/OFF before proposing a new_recipe) --
        # same "ai_estimated" bucket as a manually-typed or imported
        # estimate, see routers/recipes.py's create_recipe for the
        # matching logic on the normal creation path.
        nutrition_provenance="ai_estimated" if parsed.get("nutrition") else None,
        tips=parsed.get("tips") or [],
        source=source,
    )
    db.add(recipe)
    db.flush()
    for ing in parsed.get("ingredients") or []:
        recipe.ingredients.append(
            RecipeIngredient(
                ingredient_name=ing["ingredient_name"],
                quantity=ing.get("quantity"),
                unit=ing.get("unit"),
                prep_note=ing.get("prep_note"),
            )
        )
    recipe.tags = resolve_tags(db, parsed.get("tags") or [])
    db.flush()
    return recipe


# --- AI-assisted import -------------------------------------------------
#
# Ollama is asked to return a single JSON object matching (most of)
# RecipeCreate's shape. Real model output often wraps that in prose or
# markdown fences, so parsing is defensive: try strict JSON, then fall
# back to extracting the first {...} block. Mirrors
# inventory_service.parse_vision_response's approach for arrays.

RECIPE_IMPORT_PROMPT = """\
Extract a recipe from the following content and respond with ONLY a JSON \
object (no other text, no markdown fences) with these keys:
- "title": string
- "description": short string or null
- "default_servings": integer (your best estimate if not stated, else a \
reasonable default like 4)
- "prep_time_minutes": integer or null
- "cook_time_minutes": integer or null
- "instructions": array of strings, one per step
- "ingredients": array of objects with "ingredient_name" (string), \
"quantity" (number or null), "unit" (string or null), "prep_note" \
(string or null, e.g. "diced")
- "nutrition": object with best-effort per-serving estimates as numbers \
or null: {NUTRITION_PROMPT_HINT}
- "tags": array of short lowercase strings from this set where \
applicable: quick, portable, non_refrigerated, dutch_oven_only, \
backpacking, one_pot, make_ahead, freezer_friendly, kid_friendly, \
gluten_free (omit any that don't apply; you may also add a new tag if \
clearly relevant)
- "tips": array of short strings capturing any GENUINELY USEFUL asides \
from the source that aren't part of the core recipe structure above -- \
ingredient substitutions, optional variations, make-ahead/storage notes, \
or equipment alternatives the source explicitly mentions. Each entry \
should be a short paraphrase in your own words, not a long verbatim \
quote. Omit this entirely (empty array) if there's nothing like that.

Important: only extract factual/functional recipe information (what to \
buy, what to do, timing, substitutions). Do NOT reproduce the source's \
narrative prose, personal stories, advertisements, or other copyrightable \
writing -- summarize functionally instead of quoting at length.

Content to extract from:
---
{content}
---
""".replace("{NUTRITION_PROMPT_HINT}", NUTRITION_PROMPT_HINT)
# ^ plain str.replace (not .format) for the nutrition-key hint, since
# {content} below is a real .format() placeholder filled in per-call
# (see routers/recipes.py's _run_text_extraction) -- .format() would
# choke on the still-unfilled {NUTRITION_PROMPT_HINT} token if this were
# resolved via .format() at both places.


def parse_recipe_response(raw_text: str) -> dict | None:
    data = _extract_json_object(raw_text)
    if not data or not data.get("title"):
        return None
    return coerce_recipe_fields(data)


def coerce_recipe_fields(data: dict) -> dict:
    """Field coercion shared by recipe import (parse_recipe_response,
    above) and meal-plan generation (meal_plan_service.parse_meal_plan_
    response, which calls this for any AI-proposed "new_recipe" a
    generated meal-plan slot doesn't have a catalog match for) -- both
    consume the same JSON recipe shape from the model, so the
    ingredient/instruction/nutrition/tag/tip coercion logic lives here
    once rather than twice."""
    ingredients = []
    for ing in data.get("ingredients") or []:
        if not isinstance(ing, dict) or not ing.get("ingredient_name"):
            continue
        ingredients.append(
            {
                "ingredient_name": str(ing["ingredient_name"]).strip(),
                "quantity": _safe_float(ing.get("quantity")),
                "unit": ing.get("unit") or None,
                "prep_note": ing.get("prep_note") or None,
            }
        )

    instructions = [str(s).strip() for s in (data.get("instructions") or []) if str(s).strip()]
    nutrition_raw = data.get("nutrition") or {}
    nutrition = {k: _safe_float(v) for k, v in nutrition_raw.items() if _safe_float(v) is not None}
    tags = [str(t).strip().lower() for t in (data.get("tags") or []) if str(t).strip()]
    tips = [str(t).strip() for t in (data.get("tips") or []) if str(t).strip()]

    return {
        "title": str(data["title"]).strip(),
        "description": data.get("description") or None,
        "default_servings": _safe_int(data.get("default_servings")) or 2,
        "prep_time_minutes": _safe_int(data.get("prep_time_minutes")),
        "cook_time_minutes": _safe_int(data.get("cook_time_minutes")),
        "instructions": instructions,
        "ingredients": ingredients,
        "nutrition": nutrition,
        "tags": tags,
        "tips": tips,
    }


def extract_pdf_text(pdf_bytes: bytes) -> str:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


# --- URL import -----------------------------------------------------
#
# Recipe pages are frequently mostly ads, life stories, and navigation
# chrome around a small functional core. trafilatura is used specifically
# because it does main-content extraction (strips boilerplate) AND pulls
# byline/sitename metadata in one pass -- the metadata is what lets us
# capture citation info per the project's copyright-respect requirement,
# instead of just discarding where a recipe came from.
#
# Split into fetch (network) + extract (pure function over HTML) so the
# extraction logic is unit-testable without a live network call.


def fetch_html(url: str) -> str:
    html = trafilatura.fetch_url(url)
    if not html:
        raise ValueError(f"Could not download content from {url}")
    return html


def extract_content_from_html(html: str, url: str | None = None) -> dict:
    result = trafilatura.bare_extraction(html, url=url, with_metadata=True, favor_recall=True)
    if not result:
        return {"text": "", "title": None, "author": None, "sitename": None, "image": None}
    return {
        "text": result.get("text") or "",
        "title": result.get("title"),
        "author": result.get("author"),
        "sitename": result.get("sitename"),
        # trafilatura populates this from og:image/twitter:image when
        # present -- a plausible "hero" photo for a recipe page, used for
        # best-effort dish-image auto-capture on URL import. Often None;
        # that's expected, not an error.
        "image": result.get("image"),
    }


def extract_url_content(url: str) -> dict:
    html = fetch_html(url)
    return extract_content_from_html(html, url=url)


def fetch_image_bytes(image_url: str, max_bytes: int = 8_000_000) -> tuple[bytes, str] | None:
    """Best-effort download of a candidate dish image (e.g. a URL
    import's og:image) for auto-capture. Returns (raw_bytes, content_type)
    or None on any failure/mismatch -- this is a nice-to-have, never
    something that should block or fail a recipe import. Caps response
    size so a misbehaving/huge URL can't be used to balloon memory or
    disk usage."""
    try:
        with httpx.Client(follow_redirects=True, timeout=10.0) as client:
            resp = client.get(image_url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if not content_type.startswith("image/"):
                return None
            if len(resp.content) > max_bytes:
                return None
            return resp.content, content_type
    except Exception:  # noqa: BLE001 -- network/parsing failure here is never fatal to the import
        return None


# --- Recipe-scoped chat context ---------------------------------------
#
# Used by POST /api/recipes/{id}/chat (routers/recipes.py) to ground
# substitution/adjustment suggestions in the actual recipe the user is
# cooking, at the servings size they're actually making.


def _format_ingredient_line(ing: dict) -> str:
    line = f"- {ing['quantity'] or ''} {ing['unit'] or ''} {ing['ingredient_name']}".strip()
    if ing.get("prep_note"):
        line += f" ({ing['prep_note']})"
    return line


def build_recipe_chat_context(recipe_read: dict) -> str:
    ingredients_lines = "\n".join(
        _format_ingredient_line(ing) for ing in recipe_read.get("ingredients", [])
    )
    instructions_lines = "\n".join(
        f"{i + 1}. {step}" for i, step in enumerate(recipe_read.get("instructions", []))
    )
    tips_block = ""
    if recipe_read.get("tips"):
        tips_lines = "\n".join(f"- {t}" for t in recipe_read["tips"])
        tips_block = f"\n\nKnown tips/substitutions/variations for this recipe:\n{tips_lines}"

    return (
        f"The user is currently viewing/cooking this recipe, scaled to "
        f"{recipe_read.get('servings_shown')} servings. Help with questions about it -- "
        f"substitutions, technique, timing adjustments, or scaling -- grounded in what's "
        f"actually in the recipe below. Suggestions are for this cooking session only; "
        f"do not assume anything gets saved.\n\n"
        f"Recipe: {recipe_read.get('title')}\n\n"
        f"Ingredients ({recipe_read.get('servings_shown')} servings):\n{ingredients_lines}\n\n"
        f"Instructions:\n{instructions_lines}"
        f"{tips_block}"
    )


# --- Recipe-scoped chat: proposing an edit -----------------------------
#
# Added 2026-07-31 ("commit an AI-modified recipe" request): the
# recipe-scoped chat was originally read-only (see build_recipe_chat_
# context above), but a real use case -- "take this imported recipe and
# make it gluten-free" -- needs the AI to actually propose new recipe
# content the user can review and save, not just answer a question.
# Every chat turn can still be a plain answer; this only adds an
# additional, optional field to the response shape the model already
# returns free text through, following the same preview-then-confirm
# discipline as recipe import/vision intake/meal-plan generation:
# nothing is ever saved by chat code itself, only proposed.

RECIPE_MODIFY_INSTRUCTIONS = """\
Respond with ONLY a JSON object (no other text, no markdown fences) with \
these keys:
- "reply": string, your natural conversational response (always \
required -- a short summary of what you changed if proposing an edit, \
or a direct answer if it's just a question)
- "proposed_recipe": null, UNLESS the user's message clearly asks for a \
change to the recipe itself (not just a question) -- e.g. "make this \
gluten-free", "double the recipe", "remove the dairy", "cut back the \
sugar". When proposing a change, include the ENTIRE recipe as it should \
look after the change is applied -- every field, in this exact shape: \
{"title": string, "description": string or null, "default_servings": \
integer, "prep_time_minutes": integer or null, "cook_time_minutes": \
integer or null, "instructions": array of strings, "ingredients": array \
of objects with "ingredient_name", "quantity" (number or null), "unit" \
(string or null), "prep_note" (string or null), "nutrition": object \
with best-effort per-serving numeric estimates or null for these keys: \
{NUTRITION_PROMPT_HINT}, "tags": array of short lowercase tags, "tips": \
array of strings}. Keep every field the user \
didn't ask to change the same as the current recipe below -- this is \
the full recipe after your edit, not a list of just the changes. \
Update nutrition estimates if the ingredient changes would meaningfully \
affect them.
- "variant_label": null, UNLESS proposed_recipe is set -- then a short \
label (2-4 words, e.g. "Gluten-Free", "Dairy-Free", "Double Batch") \
describing what's different about this version.

Never silently modify the recipe -- only propose changes via \
proposed_recipe. Nothing is saved until the user reviews and confirms \
it themselves.
""".replace("{NUTRITION_PROMPT_HINT}", NUTRITION_PROMPT_HINT)


def parse_recipe_chat_response(raw_text: str) -> dict:
    """Defensively extracts {"reply", "proposed_recipe", "variant_label"}
    from the model's response to the recipe-scoped chat. Like chat_
    service.parse_chat_response, this is free-form conversation and far
    more likely to break a strict-JSON instruction than a pure
    extraction task is -- if no JSON object with a "reply" key is found
    at all, the raw text is used as the reply verbatim with no proposed
    change, rather than showing the user nothing."""
    data = _extract_json_object(raw_text)
    if not isinstance(data, dict) or "reply" not in data:
        return {"reply": (raw_text or "").strip(), "proposed_recipe": None, "variant_label": None}

    reply = str(data.get("reply") or "").strip() or (raw_text or "").strip()
    proposed = data.get("proposed_recipe")
    proposed_recipe = None
    variant_label = None
    if isinstance(proposed, dict) and proposed.get("title"):
        proposed_recipe = coerce_recipe_fields(proposed)
        proposed_recipe["source"] = "chat_modified"
        variant_label = str(data.get("variant_label") or "").strip() or None
    return {"reply": reply, "proposed_recipe": proposed_recipe, "variant_label": variant_label}


def _extract_json_object(raw_text: str) -> dict:
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    match = re.search(r"\{.*\}", raw_text or "", re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def _safe_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(value) -> int | None:
    f = _safe_float(value)
    return int(f) if f is not None else None
