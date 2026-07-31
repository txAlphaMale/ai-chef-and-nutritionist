"""Recipe business logic: servings scaling, AI-assisted import parsing
(text, PDF, photo, or URL -> structured recipe JSON), and building
context for the recipe-scoped chat feature."""
from __future__ import annotations

import io
import json
import re

import trafilatura
from pypdf import PdfReader
from sqlalchemy.orm import Session

from app.models import MealTag, Recipe

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
or null: "calories", "protein_g", "carbs_g", "fat_g", "fiber_g", \
"sodium_mg", "cholesterol_mg"
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
"""


def parse_recipe_response(raw_text: str) -> dict | None:
    data = _extract_json_object(raw_text)
    if not data or not data.get("title"):
        return None

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
        return {"text": "", "title": None, "author": None, "sitename": None}
    return {
        "text": result.get("text") or "",
        "title": result.get("title"),
        "author": result.get("author"),
        "sitename": result.get("sitename"),
    }


def extract_url_content(url: str) -> dict:
    html = fetch_html(url)
    return extract_content_from_html(html, url=url)


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
