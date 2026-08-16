"""Recipe business logic: servings scaling, AI-assisted import parsing
(text, PDF, photo, or URL -> structured recipe JSON), and building
context for the recipe-scoped chat feature."""

from __future__ import annotations

import contextlib
import difflib
import io
import json
import re
from collections import Counter
from dataclasses import dataclass
from itertools import pairwise

import httpx
import lxml.html
import pdfplumber
import trafilatura
from pypdf import PdfReader
from sqlalchemy.orm import Session
from trafilatura import settings as trafilatura_config

from app.models import MealTag, Recipe, RecipeIngredient
from app.schemas.ai_extraction import (
    COMPONENT_UNSECTIONED,
    ExtractedIngredientLines,
    ExtractedRecipe,
    ExtractedRecipeEdit,
    schema_of,
)
from app.services import ollama_client, recipe_image_service, unit_conversion_service
from app.services.ai_json_extraction import extract_json_object
from app.services.food_data_service import NUTRITION_PROMPT_HINT
from app.services.unit_conversion_service import MASS_UNITS, VOLUME_UNITS, normalize_unit

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


# A heading that announces the ingredient list is not the name of a part
# of the dish. Sources write these constantly -- the measured one is
# `INGREDIENTS YOU'LL NEED:`, which was stored as the component on all ten
# ingredients of a single-component recipe. Matched on a folded key rather
# than literally, so punctuation and curly apostrophes do not each need
# their own entry.
_COMPONENT_GENERIC = frozenset(
    {
        COMPONENT_UNSECTIONED,
        "ingredient",
        "ingredients",
        "ingredient list",
        "ingredients list",
        "all ingredients",
        "main ingredients",
        "base ingredients",
        "other ingredients",
        "ingredients youll need",
        "ingredients you will need",
        "what youll need",
        "what you will need",
        "what you need",
        "youll need",
        "you will need",
        "recipe",
        "shopping list",
        "grocery list",
    }
)
# Requires whitespace after "for", so "Formaggio" and "Forcemeat" are safe.
_COMPONENT_FOR_PREFIX = re.compile(r"^for\s+(?:the\s+)?", re.IGNORECASE)
_COMPONENT_EDGE_PUNCT = " \t:;,.!-–—"  # noqa: RUF001 -- the en/em dashes are the point: they are stripped


def _component_key(label: str) -> str:
    """Folded form used only for comparison, never for storage."""
    folded = label.casefold().replace("’", "").replace("'", "")  # noqa: RUF001 -- both apostrophe forms occur in real headings
    return re.sub(r"[^a-z0-9]+", " ", folded).strip()


def normalize_component(raw: str | None) -> str | None:
    """The storage meaning of an extraction's `component`.

    The extraction schema requires a non-null string on every ingredient,
    because a nullable one is an escape hatch the 9B took on every row --
    see ExtractedIngredient.component. So an unsectioned recipe answers
    with the sentinel "main" rather than declining, and that sentinel
    stops here: NULL remains what the database means by "this recipe has
    no named parts", which is also what every row imported before the
    column existed honestly says.

    Two things beyond the sentinel mean the same thing and are treated the
    same way, both measured on the real corpus rather than imagined:

    A GENERIC HEADING is not a part. `INGREDIENTS YOU'LL NEED:` and plain
    `Ingredients` announce the list; `Brine`, `Crust` and `Filling` name a
    part of the dish. Only the second kind is worth storing, and the first
    kind is worse than nothing because component is half of how a reader
    tells two sections apart.

    `For the Crust` and `Crust` are the SAME part, written by two sources.
    The prefix is dropped so they land on one label. This is deliberately
    narrow -- it is the label instability that makes components hard to
    compare across recipes, and every additional guess here (case folding,
    singularising) risks mangling a real name like `Pico de Gallo`.

    Case- and whitespace-insensitive, since the sentinel comes back from
    a language model rather than from code. A genuine heading that reads
    "Main" is treated as unsectioned too, which is the right call: a
    recipe whose only part is called "Main" has no parts worth naming."""
    if raw is None:
        return None
    label = _COMPONENT_FOR_PREFIX.sub("", raw.strip()).strip(_COMPONENT_EDGE_PUNCT)
    if not label:
        return None
    if _component_key(label) in _COMPONENT_GENERIC:
        return None
    return label


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
                component=normalize_component(ing.get("component")),
            )
        )
    recipe.tags = resolve_tags(db, parsed.get("tags") or [])
    db.flush()
    return recipe


# --- AI-assisted import -------------------------------------------------
#
# Ollama is asked to return a single JSON object matching (most of)
# RecipeCreate's shape. Real model output often wraps that in prose,
# markdown fences, or a reasoning trace, so parsing is defensive -- see
# ai_json_extraction.extract_json_object (what _extract_json_object below
# delegates to).
#
# Written as numbered rules plus one worked example, matching
# RECEIPT_IMPORT_PROMPT (routers/inventory.py). Keep that shape if this
# is edited: a long prose-paragraph prompt raises the odds of a
# capable-but-not-huge local model bailing out early, and numbered rules
# cost fewer tokens per requirement stated. The requirements this prompt
# must keep expressing: unit fidelity (never convert, never guess), an
# ingredient name reused across sections split rather than merged (crust
# Rule 4 defines what a step BOUNDARY is, and that is also deliberate.
#
# Measured on the pie, 2026-08-06: dropping rule 3's example headings
# coincided with the model copying the source almost verbatim instead of
# paraphrasing it -- an improvement -- but it also started copying whole
# PARAGRAPHS as single steps. `Add butter and pulse until wet sand.
# Transfer to a 9 1/2" dish. Press crumbs firmly. Bake crust until
# fragrant, 20-25 minutes.` arrived as ONE entry, where the previous run
# had four.
#
# That is not cosmetic. B7.1 cook mode shows one entry at a time and B7.2
# parses durations out of entry text to offer a timer, so a bake buried
# mid-entry is a worse timer and a worse cook mode. Fidelity and
# granularity turned out to be in tension, so rule 4 now states the
# boundary rather than assuming "discrete step" is self-evident, and
# singles out timed actions because that is the case that actually costs
# the household something.
#
# Rules 3 and 4 name NO example section headings, and that is deliberate.
# They used to read `(Crust, Filling and Assembly, Topping)` and
# `(crust, filling, assembly, topping, etc.)`. On the Bon Appetit pie --
# whose ingredient list and method BOTH have exactly two headings, `Crust`
# and `Filling and Assembly` -- the model returned instructions labelled
# `Crust`, `Filling and Assembly` and `Topping`, in that order. The string
# `topping` occurs ZERO times in that source; counted, 2026-08-06. The
# first two examples were the recipe's real headings, so the model
# completed the list with the third.
#
# The ingredient path is immune to this because its components come from
# blocks of COPIED source lines (see extract_ingredients_two_pass); the
# instruction path has nothing holding it to the page, so the prompt is
# the only place the constraint can live. It now states the constraint
# instead of illustrating the field, and a test pins the absence.
#
# vs. filling, "divided" quantities), the per-ingredient `component`
# heading, the fixed tag vocabulary, and the tips/copyright-respect rule.
#
# `component` is stated here as well as in the schema because the schema
# makes it REQUIRED and NON-NULLABLE (see ExtractedIngredient) -- a field
# the grammar forces the model to fill but the prompt never defines gets
# filled with something invented. The two have to move together.
#
# {content} is filled via plain str.replace() at each call site
# (recipe_service._extract_via_ollama, routers/recipes.py's
# _run_text_extraction, and the image branch of
# parse_recipe_file_content), NOT `.format()`. That is load-bearing:
# this is a DB-backed, GUI-editable SystemPrompt
# (get_recipe_import_prompt, below), and `.format()` raises KeyError on
# any stray literal brace a household types -- which a JSON worked
# example is full of. `.replace()` has no such footgun.
RECIPE_IMPORT_PROMPT = """\
Task: extract a structured recipe from the following content, as a single JSON object.

SOURCE:
{content}

RULES:
1. Copy each ingredient's quantity and unit EXACTLY as written in the source -- never convert between units (e.g. "2 Tbsp." stays quantity 2 / unit "Tbsp.", never converted to a fraction of a cup or any other unit). Never guess a quantity or unit that isn't actually stated; leave both null rather than invent one.
2. If the same ingredient name (e.g. "sugar", "kosher salt") appears more than once in the source for a different part of the recipe (a crust vs. a filling, a marinade vs. a sauce, an ingredient list vs. a later "remaining X" reference), list each occurrence as its OWN separate ingredient entry with ONLY the quantity/unit/prep_note stated for THAT occurrence. Never merge, average, or carry a modifier like "divided" from one occurrence onto a different one.
3. Every ingredient carries a "component": the source's own heading for the part of the dish that ingredient belongs to, copied exactly as the source writes it. Use ONLY headings the source actually prints. If it prints two, there are exactly two -- inventing a third because it would be a natural name for those ingredients is an error. Use the value main when the recipe is a single dish with no named parts.
4. "instructions" is one array entry per DISCRETE STEP, in the order the source presents them, across EVERY labeled section -- not just the first one. A step is one action the cook finishes before starting the next. A source paragraph almost always holds several steps; never copy a whole paragraph across as one entry. Any action that carries a time or a temperature ("bake 20-25 minutes", "cook about 5 minutes", "chill overnight") is ALWAYS its own entry and is never buried in an entry that also does something else. Each entry carries the heading of the section it appears under, spelled identically to rule 3's, or the literal main when the recipe has no sections. Rule 3's constraint applies here too: never introduce a heading the source does not print.
5. "default_servings" is your best estimate if not stated, else a reasonable default like 4.
6. "tags" -- only short lowercase strings from this fixed set where applicable: breakfast, lunch, dinner, dessert, quick, portable, non_refrigerated, dutch_oven_only, backpacking, one_pot, make_ahead, freezer_friendly, kid_friendly (omit any that don't apply; add a new short tag only if clearly relevant and none of these fit). NEVER emit a tag claiming the dish is FREE OF something -- not gluten_free, dairy_free, nut_free, or any similar "free" or "safe for" tag. A recipe source cannot establish that an ingredient is absent, and this app decides those claims for itself.
7. "tips" -- short, GENUINELY USEFUL asides the source explicitly mentions that this shape has no other field for: ingredient substitutions, optional variations, make-ahead/storage notes, or equipment alternatives. Paraphrase each in your own words, never a long verbatim quote. Empty array if there's nothing like that.
8. Only extract factual/functional recipe information (what to buy, what to do, timing, substitutions). Do NOT reproduce the source's narrative prose, personal stories, advertisements, or other copyrightable writing -- summarize functionally instead of quoting at length.

EXAMPLE (a source line reused across two sections -- see rule 2):
Source: under the heading "Filling", the ingredient list says "3/4 cup plus 2 Tbsp. sugar, divided"; a later step says "fold in ... remaining 2 Tbsp. sugar".
Correct output includes BOTH as separate ingredient entries -- never one merged "3/4 cup plus 2 Tbsp." entry:
{"ingredient_name": "sugar", "quantity": 0.75, "unit": "cup", "prep_note": "divided", "component": "Filling"}
{"ingredient_name": "sugar", "quantity": 2, "unit": "Tbsp.", "prep_note": null, "component": "Filling"}

OUTPUT FORMAT: Respond with ONLY a JSON object -- no other text, no markdown fences. Exactly these keys:
{"title": string, "description": string or null, "default_servings": integer, "prep_time_minutes": integer or null, "cook_time_minutes": integer or null, "instructions": array of objects with "text" (string) and "component" (string, never null -- see rules 3 and 4), "ingredients": array of objects with "ingredient_name" (string), "quantity" (number or null), "unit" (string or null), "prep_note" (string or null), "component" (string, never null -- see rule 3), "nutrition": object with best-effort per-serving estimates as numbers or null: {NUTRITION_PROMPT_HINT}, "tags": array of short lowercase strings, "tips": array of strings}
""".replace("{NUTRITION_PROMPT_HINT}", NUTRITION_PROMPT_HINT)
# ^ the ONE remaining plain str.replace() at module-definition time,
# resolving the shared nutrition-key hint into the constant once -- the
# {content} token above is left alone here and filled in later, per-call,
# also via str.replace() (see this section's comment above for why
# .format() was dropped app-wide for this prompt).


# Constrained-decoding schema for every recipe extraction path (pasted
# text, PDF, photo, HTML with no JSON-LD, and the folder importer). The
# prompt's OUTPUT FORMAT section above is now a human-readable restatement
# of this -- the schema is what the model is actually held to. See
# app/schemas/ai_extraction.py for why this replaced prompt-only
# instructions.
RECIPE_SCHEMA = schema_of(ExtractedRecipe)

# A full recipe -- title, description, every ingredient, every instruction
# step, nutrition, tags and tips -- is one of the longest responses this
# app asks for. Reserved explicitly so a long source can never crowd the
# answer out of the context window (done_reason "length").
RECIPE_RESPONSE_TOKENS = 2500

# The recipe-scoped chat's shape: a conversational reply plus an optional
# full replacement recipe the user reviews before anything is saved.
RECIPE_EDIT_SCHEMA = schema_of(ExtractedRecipeEdit)


def get_recipe_import_prompt(db: Session) -> str:
    """Backlog B16.1 -- returns the household's custom override for the `recipe_import` SystemPrompt row
    when one exists and is marked active, else this module's own
    RECIPE_IMPORT_PROMPT default. Mirrors the existing main_chef/
    dietary_onboarding pattern exactly (same SystemPrompt table, same
    /api/system/prompts GET/PATCH endpoints, same Settings-page textarea)
    rather than inventing new plumbing -- unchecking "Active" in the
    Settings UI reverts to the shipped default without losing the
    household's draft edit, since the row's content isn't touched by
    toggling is_active off."""
    return ollama_client.get_active_prompt(db, "recipe_import") or RECIPE_IMPORT_PROMPT


def get_ingredient_lines_prompt(db: Session) -> str:
    """DB-override-with-fallback for INGREDIENT_LINES_PROMPT (pass 1 of
    ingredient extraction), same pattern as get_recipe_import_prompt."""
    return ollama_client.get_active_prompt(db, "ingredient_lines") or INGREDIENT_LINES_PROMPT


def parse_recipe_response(raw_text: str) -> dict | None:
    data = _extract_json_object(raw_text)
    if not data or not data.get("title"):
        return None
    return coerce_recipe_fields(data)


# --- An absence claim is not the model's to make -------------------------
#
# Rule 6 names a fixed tag vocabulary and, since 2026-08-07, forbids any
# "free of" tag outright. Neither is enforcement. Rule 6 also invites a
# new tag when nothing in the set fits, and NOTHING between the model and
# the database ever checked the answer: this function lowercased whatever
# arrived and `resolve_tags` created the row. Taking `gluten_free` out of
# a list the model was always free to depart from changed the odds and
# nothing else.
#
# Worth gating rather than merely asking, because this error is not
# symmetric. `allergen_service` is built to FLAG what it recognises, so a
# miss reads as clean -- an absence claim fails silently and in the
# dangerous direction. A tag wrongly saying a dish CONTAINS gluten costs
# the household a meal it would have enjoyed. The inverse costs a coeliac
# considerably more, and this household cooks gluten-free for a reason.
#
# Hand-entered tags are deliberately NOT gated, and the caller list is
# what makes that precise: every caller of `coerce_recipe_fields` is a
# model or a publisher asserting something about a document it read
# (recipe import, chat's recipe proposals, meal-plan generation's
# `new_recipe`). The API's own create/update path does not pass through
# here. A household tagging food it cooked itself is making a claim it is
# entitled to make, about a kitchen it can actually see.
#
# The JSON-LD path is gated too, and that is a decision rather than an
# oversight. A publisher's own `keywords: gluten free` describes their
# kitchen and their supply chain, not this one, and it arrives with no
# more checking behind it than the model's version.
#
# `freezer_friendly` and `free_range` are why these are anchored patterns
# and not a substring search for "free" -- the first would match on
# "free"+"zer" under a careless rule, and the second is a sourcing claim,
# not an absence one.
_ABSENCE_CLAIM_PATTERNS = [
    # gluten_free, dairy free, nut-free, allergen_free, sugar free
    re.compile(r"^.+[\s_-]free$"),
    # free_of_gluten, free-from-dairy
    re.compile(r"^free[\s_-](of|from)[\s_-].+$"),
    # no_gluten, without dairy
    re.compile(r"^(no|without)[\s_-].+$"),
    # celiac_friendly, coeliac safe -- an absence claim wearing another word
    re.compile(r"^.*(celiac|coeliac).*$"),
    # allergy_friendly
    re.compile(r"^allerg(y|en)[\s_-]friendly$"),
    # the abbreviations a model reaches for when told not to spell it out
    re.compile(r"^(gf|df|gfree|dfree)$"),
]


def split_absence_claim_tags(tags: list[str]) -> tuple[list[str], list[str]]:
    """Partition `tags` into (kept, dropped), dropping any tag that claims
    a dish is FREE OF something.

    Returns both halves rather than filtering in place so the caller can
    say what it threw away. A gate that silently eats a tag is how a
    household ends up believing the model never emitted one."""
    kept, dropped = [], []
    for tag in tags:
        target = kept
        for pattern in _ABSENCE_CLAIM_PATTERNS:
            if pattern.match(tag):
                target = dropped
                break
        target.append(tag)
    return kept, dropped


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
                # This rebuild is an allowlist, so a field the model was
                # just forced to emit dies here unless it is named. That
                # is how `component` reached a live run reporting "no
                # ingredient carries a component" while the grammar had
                # made a null one impossible -- the model sent it and this
                # function threw it away, three layers before the code
                # that stores it. Every consumer between the model and the
                # database has to copy the field, not just the last one.
                "component": normalize_component(ing.get("component")),
            }
        )

    instructions = normalize_instructions(data.get("instructions"))
    nutrition_raw = data.get("nutrition") or {}
    nutrition = {k: _safe_float(v) for k, v in nutrition_raw.items() if _safe_float(v) is not None}
    tags, dropped_tags = split_absence_claim_tags(
        [str(t).strip().lower() for t in (data.get("tags") or []) if str(t).strip()]
    )
    if dropped_tags:
        print(
            f"[recipe_tags] dropped {len(dropped_tags)} absence-claim tag(s): "
            f"{', '.join(sorted(dropped_tags))} -- see _ABSENCE_CLAIM_PATTERNS",
            flush=True,
        )
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


# --- Two page elements written through each other ------------------------
#
# Measured on the Bon Appetit pie, page 2 (2026-08-06). pdfplumber groups
# characters into lines by baseline and orders them by x, which is correct
# until two INDEPENDENT elements share a baseline. That page's
# subscription ad sits on top of the final instruction, and the two arrive
# shredded together, character by character:
#
#   and 1/4 tsp. salt just to combine. Usings uab lasrcgreip stipoono na,n
#   ddo lgloept asi xge FnReEroEu gsi aftms!ount of whipped cream in the
#
# That is `Using a large spoon, dollop a generous amount of whipped cream
# in the center` interleaved with `subscription and get six FREE gifts!`.
# `dollop` and `spoon` do not survive as words at all, so nothing
# downstream can recover them: the import rendered that step as `Pipe a
# mound of whipped cream`, a different technique needing different
# equipment, and that was the model doing its best with rubble.
#
# Ingredients were untouched because they are all on page 1 -- which is
# why this hid until the ingredients stopped being the problem.
#
# The two runs are trivially separable because they are set in different
# fonts. Separation is done by pdfplumber's own `filter` + `extract_text`
# and NOT by re-joining characters by hand: the page path already knows
# how to infer spaces from geometry, and a hand-rolled join loses every
# space on the line (measured, first attempt).
#
# The discriminator is INTERLEAVING, not merely mixing fonts. A bold word
# inside a sentence, or a table row whose cells are set differently, is a
# handful of contiguous runs; two overlapping elements alternate on nearly
# every character. Measured across both PDFs to hand -- 161 lines, 37 of
# them multi-font:
#
#     legitimate multi-font lines    2-5 alternations
#     the two overlaid lines         37 and 49
#
# 12 sits in a 32-wide empty band. The ratio is the second half of the
# test: an alternation COUNT alone would eventually fire on a long table
# row that changes font once per cell, so the alternation must also be
# dense. The overlaid lines change style every 2-3 characters (0.41,
# 0.33); no legitimate line measured here exceeds 0.12 at a comparable
# length.
#
# The overlay text is APPENDED, not discarded. It is page furniture and
# almost certainly noise, but "almost certainly" is not the standard this
# module holds itself to anywhere else -- see the pypdf-fallback comment
# below on why an empty result is never silently swallowed either.
#
# CAVEAT, stated because the corpus is thin: two PDFs is not a corpus, and
# extract_pdf_text is shared with bloodwork import, receipt import and
# knowledge files. So the guard is conservative and a page with no
# detected interleaving takes exactly the old path -- a test pins that the
# result is byte-identical in that case.
_OVERLAY_MIN_ALTERNATIONS = 12
_OVERLAY_MIN_ALTERNATION_RATIO = 0.25


def _char_style(obj) -> tuple:
    return (obj.get("fontname"), round(obj.get("size") or 0, 1))


def _interleaved_overlay_styles(page) -> set:
    """Character styles woven THROUGH another element's text on this page.

    Returns the styles belonging to the intruding element(s), or an empty
    set when the page has no line that alternates hard enough to be two
    things at once. The majority style on such a line is taken to be the
    text being written over; everything else on it is the overlay."""
    overlay: set = set()
    for line in page.extract_text_lines():
        chars = sorted(line.get("chars") or [], key=lambda c: c["x0"])
        styles = [_char_style(c) for c in chars]
        if len(set(styles)) < 2:
            continue
        alternations = 1 + sum(1 for a, b in pairwise(styles) if a != b)
        if alternations < _OVERLAY_MIN_ALTERNATIONS:
            continue
        if alternations / len(chars) < _OVERLAY_MIN_ALTERNATION_RATIO:
            continue
        counts = Counter(styles)
        written_over = counts.most_common(1)[0][0]
        overlay.update(style for style in counts if style != written_over)
    return overlay


def _page_text_unweaving_overlays(page) -> str:
    """One page's text, with any element written through another one pulled
    out of it and moved to the end rather than left shredded into it."""
    overlay = _interleaved_overlay_styles(page)
    if not overlay:
        return page.extract_text() or ""
    print(
        f"[recipe_import] page {page.page_number}: {len(overlay)} character style(s) found interleaved "
        f"through the text ({sorted(overlay)}). Separating them; the overlay is kept, at the end.",
        flush=True,
    )

    def is_overlay_char(obj) -> bool:
        return obj.get("object_type") == "char" and _char_style(obj) in overlay

    main = page.filter(lambda obj: not is_overlay_char(obj)).extract_text() or ""
    aside = page.filter(is_overlay_char).extract_text() or ""
    return "\n".join(part for part in (main, aside) if part)


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """PDF text, via pdfplumber, with pypdf kept only as a crash net.

    Measured on both browser print-to-PDF fixtures, 2026-08-06
    (scripts/compare_pdf_extractors.py):

                        chars   lines   longest line
        kimchi  pypdf   17443      29           7404
                plumber 17937     534             50
        pizza   pypdf   14012      56            897
                plumber 14325     345             67

    pypdf returns one line per LAYOUT BLOCK, which is why a 7,404-character
    "line" exists at all and why find_welded_run had to be written.
    pdfplumber returns the lines the page actually shows, and restores the
    spacing pypdf drops inside them: `320gCaputo` becomes `320g Caputo`,
    and the pizza's `+Chickpea flour` -- a sixth ingredient this app was
    silently storing as part of the fifth -- becomes its own line.

    It does not fix everything, and two things are worth not forgetting:
    a wrapped ingredient still arrives as `Korean red pepper` + `powder` on
    two lines, and the `fi` ligature still reads `Gsh`, because that glyph
    is mapped to `G` in the PDF's own font encoding. Two independent
    libraries returning the same wrong character from the same bytes is
    what proves it is the file rather than the reader.

    pypdf remains the fallback for an EXCEPTION only, never for an empty
    result: empty is meaningful here. A PDF with no text layer is a scan,
    and the callers read "" as the signal to try the vision path instead.
    Falling back on empty would turn one library's silence into the
    other's, and hide the scan."""
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            return "\n".join(_page_text_unweaving_overlays(page) for page in pdf.pages)
    except Exception as exc:
        print(f"[recipe_import] pdfplumber failed ({type(exc).__name__}: {exc}); falling back to pypdf.", flush=True)
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


# trafilatura's own accepted body-size window, read from its config rather
# than restated, so this cannot drift from the check that actually runs.
_TRAFILATURA_MIN_BYTES = int(trafilatura_config.DEFAULT_CONFIG.get("DEFAULT", "MIN_FILE_SIZE"))
_TRAFILATURA_MAX_BYTES = int(trafilatura_config.DEFAULT_CONFIG.get("DEFAULT", "MAX_FILE_SIZE"))

# A refusal, as opposed to a page that is simply not there.
_REFUSAL_STATUSES = {401, 402, 403, 405, 406, 418, 429, 451}


def _diagnose_failed_fetch(url: str) -> str:
    """Why `trafilatura.fetch_url` returned None.

    Measured need: the first real bookmarks run failed on 6 of 20 URLs and
    every one of them reported the same sentence -- "Could not download
    content from X" -- because `fetch_url` returns None and nothing else.
    A dead 2011 link and a live site refusing a non-browser client are
    indistinguishable in that message, and they call for opposite
    responses: one is attrition to accept, the other is a decision about
    how this app identifies itself. Guessing between them across 565
    bookmarks is not a thing worth doing.

    This re-requests through trafilatura's OWN `fetch_response`, not
    another HTTP client, so what gets diagnosed is the same request with
    the same user-agent and the same configuration -- a 403 observed
    through httpx would say nothing about what trafilatura was sent.

    Costs one extra request, and only ever on a URL that already failed."""
    try:
        response = trafilatura.fetch_response(url, decode=True)
    except Exception as exc:
        return f"the request itself raised {type(exc).__name__}: {exc}"

    if response is None:
        return "no response at all -- DNS failure, refused connection, TLS error, or timeout. Most likely a dead link."

    if response.status in _REFUSAL_STATUSES:
        return (
            f"the site answered HTTP {response.status}. That is a refusal rather than a missing page -- "
            "the server is rejecting this client, not saying the recipe is gone."
        )
    if response.status != 200:
        return f"the site answered HTTP {response.status}."

    size = len(response.html or response.data or "")
    if not _TRAFILATURA_MIN_BYTES <= size <= _TRAFILATURA_MAX_BYTES:
        return (
            f"the site answered HTTP 200 with {size} bytes, outside the "
            f"{_TRAFILATURA_MIN_BYTES}-{_TRAFILATURA_MAX_BYTES} byte window the extractor accepts."
        )
    return (
        f"the site answered HTTP 200 with {size} bytes on a second attempt, so the first was probably "
        "rate-limited or transient. Worth another run."
    )


def fetch_html(url: str) -> str:
    html = trafilatura.fetch_url(url)
    if not html:
        raise ValueError(f"Could not download {url}: {_diagnose_failed_fetch(url)}")
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


# --- Backlog B9.3: structured (schema.org JSON-LD) recipe import -------
#
# Most recipe sites publish a machine-readable `Recipe` JSON-LD block
# (Google requires it for rich-result eligibility, which is most of why
# it's so widespread) alongside the human-readable page. Parsing that
# directly is faster (no Ollama round trip), cheaper (no GPU time spent),
# and materially more accurate on ingredient quantities than asking a
# model to re-read prose and guess -- the source already states them as
# discrete facts, not something to be inferred. routers/recipes.py tries
# this FIRST for a URL import and only falls back to the existing
# Ollama-based extraction (RECIPE_IMPORT_PROMPT, above) when no usable
# JSON-LD Recipe block is found -- most sites publish one, but plenty of
# personal blogs/forums genuinely don't, so the fallback isn't a rare
# edge case to shrug off.
#
# Reuses lxml (already an indirect dependency -- trafilatura is built on
# it, confirmed importable) to find `<script type="application/ld+json">`
# blocks rather than a hand-rolled regex over raw HTML, which breaks on
# nested braces/quotes/CDATA in ways real-world markup actually produces.
# No new dependency either way.
#
# Output feeds directly into coerce_recipe_fields() (the same function
# recipe-text-import and meal-plan-generation's new_recipe both already
# use) -- this module returns the SAME pre-coercion shape (a dict with
# "title"/"ingredients"/etc., untyped/uncoerced) rather than duplicating
# coercion logic a second time.
#
# Known, stated limitation: ingredient quantity/unit parsing is a
# heuristic text parser (_parse_ingredient_line, below), not a guarantee
# -- schema.org's `recipeIngredient` is itself just a list of free-text
# strings with no structured quantity/unit (unlike, say, `prepTime`,
# which IS a real structured ISO 8601 duration). A line like
# "1 (15 oz) can black beans, drained and rinsed" will not split
# perfectly. This is an accepted tradeoff, not a silent one: like every
# other import path in this app (including the Ollama one it's replacing
# here), the result is a PREVIEW the user reviews/edits before anything
# saves, so an imperfect split is a minor annoyance to fix in the review
# step, never data corruption.

_UNICODE_FRACTIONS: dict[str, float] = {
    "¼": 0.25,
    "½": 0.5,
    "¾": 0.75,
    "⅓": 1 / 3,
    "⅔": 2 / 3,
    "⅕": 0.2,
    "⅖": 0.4,
    "⅗": 0.6,
    "⅘": 0.8,
    "⅙": 1 / 6,
    "⅚": 5 / 6,
    "⅛": 0.125,
    "⅜": 0.375,
    "⅝": 0.625,
    "⅞": 0.875,
}

# Count-style descriptors schema.org `recipeIngredient` strings commonly
# lead with that unit_conversion_service's registry doesn't cover (that
# module is scoped to real volume/mass conversion, not text parsing) --
# recognized here ONLY to decide "this word is the unit, not the start of
# the ingredient name". Maps both singular and plural spellings to a
# single canonical singular form (a hand-curated map, not naive "strip
# trailing s" stripping, since that breaks on "-es" plurals like
# "dashes"/"pinches").
_COUNT_UNIT_WORDS: dict[str, str] = {
    "clove": "clove",
    "cloves": "clove",
    "slice": "slice",
    "slices": "slice",
    "can": "can",
    "cans": "can",
    "package": "package",
    "packages": "package",
    "pkg": "package",
    "stick": "stick",
    "sticks": "stick",
    "bunch": "bunch",
    "bunches": "bunch",
    "head": "head",
    "heads": "head",
    "piece": "piece",
    "pieces": "piece",
    "pinch": "pinch",
    "pinches": "pinch",
    "dash": "dash",
    "dashes": "dash",
    "sprig": "sprig",
    "sprigs": "sprig",
    "large": "large",
    "medium": "medium",
    "small": "small",
    "whole": "whole",
    # Packaging words a recipe counts in. Without these the word lands in
    # the ingredient NAME, and ingredient name is this app's join key --
    # "envelope unflavored gelatin" matches nothing in inventory, on a
    # grocery line or in a price lookup, which is the silent-wrong-answer
    # class the audit calls out. Measured on the pie: "1 envelope
    # unflavored gelatin" was arriving with "envelope" glued to the name.
    "envelope": "envelope",
    "envelopes": "envelope",
    "packet": "packet",
    "packets": "packet",
    "jar": "jar",
    "jars": "jar",
    "bottle": "bottle",
    "bottles": "bottle",
    "box": "box",
    "boxes": "box",
    "bag": "bag",
    "bags": "bag",
    "container": "container",
    "containers": "container",
    "sheet": "sheet",
    "sheets": "sheet",
    "stalk": "stalk",
    "stalks": "stalk",
    "ear": "ear",
    "ears": "ear",
    "fillet": "fillet",
    "fillets": "fillet",
    "loaf": "loaf",
    "loaves": "loaf",
}

# A trailing parenthetical is a note about the ingredient, not part of its
# name: "unflavored gelatin (2 1/2 tsp.)", "pumpkin puree (from one 15-oz.
# can)". Leaving it in the name breaks matching for the same reason a unit
# word does, and the content is worth keeping, so it becomes a prep_note.
_TRAILING_PAREN_RE = re.compile(r"\s*\(([^()]*)\)\s*$")

_FRACTION_CHARS = "".join(_UNICODE_FRACTIONS)
# Order matters: the longest form has to win, or "1 1/2" matches the bare
# "1" alternative and silently becomes 1.0. The `\d+[FRAC]` alternative is
# for a mixed number written with a Unicode fraction and NO space, which
# is how pypdf renders "1 1/4 cups" out of a real recipe PDF -- it read as
# 1.0 before this, losing a quarter of the pumpkin.
_QTY_RE = re.compile(
    r"^\s*(\d+\s+\d+/\d+|\d+\s*[" + _FRACTION_CHARS + r"]|\d+/\d+|\d+\.\d+|\d+|[" + _FRACTION_CHARS + r"])\s*"
)

# "1-2 tablespoons", "2 to 3 cups", "3 - 4 tbsp". _QTY_RE matches the
# LOWER bound and stops, which left the rest of the range at the head of
# the remainder -- so the stored name became "-2 tablespoons melted fat of
# choice" and the unit was never read. Measured on a real batch
# (2026-08-07): six of nineteen suspect ingredient names were this, across
# five recipes.
#
# The upper bound is dropped rather than averaged, taking the lower one.
# That is already this project's convention for a range -- cookingText.js
# anchors a duration on its first bound and foodkeeper_service takes the
# sooner end of a shelf life, both for the same reason: the conservative
# end is the one that does not over-promise.
#
# `or` is deliberately NOT a range separator. "4 lamb shanks or 6 lamb
# shoulder shanks" is an alternative ingredient, not an upper bound, and
# it appears in this very batch.
_RANGE_TAIL_RE = re.compile(
    r"^\s*(?:-|\u2013|\u2014|to)\s*(?:\d+\s+\d+/\d+|\d+\s*["
    + _FRACTION_CHARS
    + r"]|\d+/\d+|\d+\.\d+|\d+|["
    + _FRACTION_CHARS
    + r"])\s*"
)

# A metric restatement of the amount just read -- "1/2 cup (100 g) red
# lentils", "1 tsp (5 mL) salt". It is the same quantity said twice, so it
# is not part of the food's name. Kept as a note rather than discarded,
# since it is the source's own wording.
_TRAILING_AMOUNT_PAREN_RE = re.compile(r"^\(\s*[\d./]+\s*[A-Za-z]+\s*\)\s*")

# "1 heaping tablespoon", "2 scant cups", "1 rounded tsp" -- a size
# adjective sitting between the number and the unit. Without this the unit
# is never found and the whole phrase lands in the name.
_MEASURE_ADJECTIVE_RE = re.compile(r"^(heaping|heaped|scant|rounded|generous|level|packed|slightly)\s+", re.IGNORECASE)


# "3/4 cup PLUS 2 Tbsp. sugar" is one source line describing two amounts
# of one ingredient. Summing them is wrong (the cook adds them at
# different times, and the source says "divided" precisely because of
# that) and dropping either is worse.
_COMPOUND_AMOUNT_RE = re.compile(r"^(?:plus|and)\s+", re.IGNORECASE)

# A leading qualifier in parentheses -- "3/4 (scant) cup" -- sits between
# the number and the unit, so unit detection has to see past it. It is
# real provenance and becomes a prep_note rather than being discarded.
_LEADING_PAREN_RE = re.compile(r"^\(([^)]*)\)\s*")


def _parse_quantity_token(token: str) -> float | None:
    token = token.strip()
    if token in _UNICODE_FRACTIONS:
        return _UNICODE_FRACTIONS[token]
    if token and token[-1] in _UNICODE_FRACTIONS:  # "1 1/4" as pypdf writes it: "1" + the glyph
        whole = _safe_float(token[:-1].strip())
        return (whole or 0.0) + _UNICODE_FRACTIONS[token[-1]]
    if " " in token:  # mixed number, e.g. "1 1/2"
        whole_str, frac_str = token.split(" ", 1)
        num, den = frac_str.split("/", 1)
        return _safe_float(whole_str) + (_safe_float(num) / _safe_float(den))
    if "/" in token:
        num, den = token.split("/", 1)
        denom = _safe_float(den)
        return (_safe_float(num) / denom) if denom else None
    return _safe_float(token)


def _take_unit(remainder: str) -> tuple[str | None, str]:
    """Pulls a leading unit word off `remainder`, returning it and what is
    left. Split out of _parse_ingredient_line so a compound amount can run
    it once per amount."""
    word_match = re.match(r"^([A-Za-z]+)\.?\b", remainder)
    if not word_match:
        return None, remainder
    candidate = word_match.group(1).lower()
    normalized = normalize_unit(candidate)
    if normalized in VOLUME_UNITS or normalized in MASS_UNITS:
        return normalized, remainder[word_match.end() :].strip()
    if candidate in _COUNT_UNIT_WORDS:
        return _COUNT_UNIT_WORDS[candidate], remainder[word_match.end() :].strip()
    return None, remainder


# Words that describe the STATE of a food rather than naming one. The
# comma split below reads a line as `<food>, <preparation>` -- correct for
# `1 onion, diced` -- but a source that writes `2 tbsp raw, local honey`
# means the opposite, and splitting there files the food as a prep note
# and leaves `raw` as the ingredient NAME.
#
# Measured 2026-08-07 on a saved recipe: `2 tbsp raw, local honey` was
# stored as name='raw', prep='local honey'. The detail page re-joins the
# two for display, so it reads correctly on screen and is wrong in the
# database -- which is why it survived several passes over that recipe.
#
# The name is this app's join key. `raw` is what inventory matching,
# grocery aggregation, nutrition resolution, the allergen warnings and
# the derived tags all see, so `1 cup raw, unsalted cashews` produced no
# tree-nut warning at all.
#
# Deliberately a short closed list of qualities, not an attempt to
# recognise food. A line whose entire head is qualities is not naming an
# ingredient yet, so there is nothing there to split off. Anything this
# list does not know keeps today's behaviour, and fails the way it
# already fails rather than a new way.
_QUALITY_WORDS = frozenset(
    [
        "raw",
        "fresh",
        "freshly",
        "dried",
        "dry",
        "frozen",
        "canned",
        "cooked",
        "uncooked",
        "ground",
        "whole",
        "halved",
        "unsalted",
        "salted",
        "sweetened",
        "unsweetened",
        "organic",
        "local",
        "plain",
        "pure",
        "natural",
        "chopped",
        "minced",
        "sliced",
        "diced",
        "grated",
        "shredded",
        "crushed",
        "toasted",
        "roasted",
        "warm",
        "cold",
        "chilled",
        "room",
        "hot",
        "lukewarm",
        "melted",
        "softened",
        "packed",
        "heaping",
        "level",
        "large",
        "medium",
        "small",
        "extra",
        "good",
        "ripe",
        "firm",
        "soft",
    ]
)


def _leads_with_only_qualities(text: str) -> bool:
    """True when everything before the first comma is quality words.

    `raw, local honey` -> True (nothing has been named yet)
    `onion, diced`     -> False (`onion` is a food)
    `red beets, peeled and chopped small` -> False
    """
    head = text.partition(",")[0]
    words = [w for w in re.split(r"[^a-z]+", head.lower()) if w]
    return bool(words) and all(word in _QUALITY_WORDS for word in words)


def parse_ingredient_line_amounts(line: str) -> list[dict]:
    """One source ingredient line -> one entry per amount it states.

    Usually one entry. A compound line ("3/4 cup plus 2 Tbsp. sugar,
    divided") yields two, sharing a name and a prep_note, because that is
    what the source describes: one ingredient added at two points. Summing
    to 1.75 cup loses the instruction, and keeping only the first loses a
    third of the sugar.

    Deliberately deterministic. Amount parsing is arithmetic over a fixed
    vocabulary of fractions and unit words -- Python does it exactly, and
    four live runs showed a 9B either refusing it (null on every row) or
    paying for it out of the ingredients it managed to list at all. The
    model's job is to find the lines; this function's job is to read
    them."""
    # Real pages use U+00A0 between a number and its unit, and every \s
    # and \b below is blind to it -- measured on this batch as
    # "figs\xa0soaked for 10 minutes". Normalised once, at the door.
    original = (line or "").replace("\u00a0", " ").strip()
    remainder = original
    amounts: list[tuple[float | None, str | None]] = []
    notes: list[str] = []

    while True:
        quantity = None
        m = _QTY_RE.match(remainder)
        if m:
            quantity = _parse_quantity_token(m.group(1))
            remainder = remainder[m.end() :].strip()

        # "1-2 tablespoons" -- take the lower bound and consume the rest
        # of the range, or the unit is never reached.
        if quantity is not None:
            span = _RANGE_TAIL_RE.match(remainder)
            if span:
                remainder = remainder[span.end() :].strip()

        paren = _LEADING_PAREN_RE.match(remainder)
        if paren:
            if paren.group(1).strip():
                notes.append(paren.group(1).strip())
            remainder = remainder[paren.end() :].strip()

        adjective = _MEASURE_ADJECTIVE_RE.match(remainder)
        if adjective:
            notes.append(adjective.group(1).lower())
            remainder = remainder[adjective.end() :].strip()

        unit, remainder = _take_unit(remainder)

        # "(100 g)" AFTER the unit is the same amount restated in metric.
        metric = _TRAILING_AMOUNT_PAREN_RE.match(remainder)
        if metric:
            notes.append(metric.group(0).strip().strip("()").strip())
            remainder = remainder[metric.end() :].strip()
        if quantity is not None or unit is not None:
            amounts.append((quantity, unit))

        # "... cup plus 2 Tbsp. sugar" -- another amount for the same
        # ingredient. Only continue when a number actually follows, so
        # "salt and pepper" is not mistaken for one.
        joiner = _COMPOUND_AMOUNT_RE.match(remainder)
        if not joiner or not _QTY_RE.match(remainder[joiner.end() :]):
            break
        remainder = remainder[joiner.end() :].strip()

    if remainder.lower().startswith("of "):
        remainder = remainder[3:].strip()

    if "," in remainder and not _leads_with_only_qualities(remainder):
        name_part, _, note_part = remainder.partition(",")
        remainder = name_part.strip()
        if note_part.strip():
            notes.append(note_part.strip())

    trailing = _TRAILING_PAREN_RE.search(remainder)
    if trailing and _TRAILING_PAREN_RE.sub("", remainder).strip(" ."):
        if trailing.group(1).strip():
            notes.append(trailing.group(1).strip())
        remainder = _TRAILING_PAREN_RE.sub("", remainder)

    name = remainder.strip(" .") or original
    prep_note = ", ".join(notes) or None
    if not amounts:
        amounts = [(None, None)]
    return [
        {"ingredient_name": name, "quantity": quantity, "unit": unit, "prep_note": prep_note}
        for quantity, unit in amounts
    ]


INGREDIENT_LINES_PROMPT = """\
Below is the text of one recipe. Find its INGREDIENT LIST and copy the lines out exactly as they appear.

An ingredient-list line is short and names one ingredient, nearly always starting with an amount ("2 Tbsp. sugar", "3 large egg yolks", "12 graham crackers"). A preparation step is a full sentence with a verb ("Add sugar and salt and pulse just to combine") -- those are not ingredient lines, however many ingredients they mention.

Copy each line character for character. Do not reword it, do not fix its spacing or punctuation, do not merge two lines, do not split one line, and do not convert anything. If you cannot copy a line exactly, leave it out.

Group the lines under the heading each block sits under in the source, copied as written ("Crust", "Filling and Assembly"). Use main when the list has no headings.

SOURCE:
{content}
"""

INGREDIENT_LINES_SCHEMA = schema_of(ExtractedIngredientLines)

# Pass 1 transcribes; it never reasons. 1200 tokens is far more than an
# ingredient list needs and far less than the full extraction reserves.
INGREDIENT_LINES_RESPONSE_TOKENS = 1200

# Below this share of pass 1's copied lines surviving verification,
# two-pass declines entirely rather than handing back what it salvaged.
# See the gate in extract_ingredients_two_pass for why partial is worse
# than nothing. 0.6 sits between the measured disaster (1/24 = 0.04) and
# every measured success (15/15, 10/10, 8/8, 6/6 = 1.00).
_TWO_PASS_MIN_COVERAGE = 0.6


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# A copied line this close to a real source line is a transcription slip,
# not a different line. Measured on the real fixture: genuine slips scored
# 0.938-0.987 and hallucinations 0.500-0.727, so 0.90 sits in a 0.21-wide
# empty band rather than being a round number someone liked.
_REPAIR_THRESHOLD = 0.90

# How many consecutive source lines one copied line may span.
#
# pdfplumber returns the lines the PAGE shows, which means an ingredient
# too long for the column arrives wrapped. Measured in
# `brussel_sprout_kimchi_pdfplumber.txt`:
#
#     74 | 4 tablespoons Korean red pepper
#     75 | powder
#     76 | 1 tablespoon Gsh sauce or shrimp sauce
#     77 | (I excluded this)
#
# The model copies what a reader sees -- one ingredient -- and sent
# `1 tablespoon Gsh sauce or shrimp sauce\n(I excluded this)`, which
# matched no single source line and was dropped. That is an ingredient
# deleted, which is worse than the welded name it replaced.
#
# find_welded_run solves the mirror case: MANY candidates inside ONE
# source line. This is ONE candidate across SEVERAL source lines, and the
# two are complementary rather than alternatives.
#
# Note this is a strict superset of the old behaviour, not a change to it:
# for any candidate shorter than its first source line, the prefix of the
# join IS the prefix of that line, so every previously-matching case
# matches identically. Only candidates too long for one line can reach the
# joined windows at all.
#
# 3 covers a two-line wrap with a line to spare. It is deliberately small:
# every extra line widens the text a hallucination could match against.
_MAX_WRAP_LINES = 3

# ...and only a candidate that STARTS LIKE AN INGREDIENT may use the wider
# window at all.
#
# Measured, by shipping it without this guard (batch run 2026-08-06):
# the kimchi imported 25 "ingredients", 17 of them method steps filed
# under a component called `Instructions`. The method wraps across lines
# exactly like an ingredient does, so joining made it verifiable, and the
# per-block coverage gate -- which had been dropping that block at 4 of 15
# -- suddenly passed it.
#
# The risk that was checked before shipping was hallucination, and the six
# measured phantoms were all still rejected. That check was sound and
# irrelevant: the damage came from text the source really does contain.
# Verification can never separate an ingredient line from a method line,
# because both are real; only the block gate can, and widening the match
# quietly disarmed it.
#
# Length cannot be the discriminator -- `B. Slice the brussel sprouts in
# half lengthwise` is 46 chars and the wrapped fish sauce is 55. What
# separates them is how they START. Every wrapped ingredient measured on
# both fixtures begins with an amount; the kimchi's method steps begin
# `A.`, `B.`, `C.`, and the pizza's prose begins `This recipe was...`.
#
# The cost, stated: an amountless ingredient that also wraps ("Kosher
# salt, freshly / ground") will not be rejoined. That is a name split in
# two, which the source at least still contains -- not seventeen phantom
# rows, and not a deleted ingredient.
_AMOUNT_START = re.compile(r"^\s*(\d|\d+/\d+|[¼-¾⅐-⅞])")


def reconcile_copied_lines(candidates: list[str], source: str) -> tuple[list[str], list[str]]:
    """Match each copied line to the source line it came from.

    Returns (accepted, rejected). **Every accepted line is the SOURCE's
    own text**, never the model's -- so nothing this returns can contain a
    character the source does not have, whatever the model actually sent.
    That property is what makes repairing safe: a too-generous threshold
    can at worst pick the wrong real line, and can never invent one.

    Matching is against the leading substring of each source line AND of
    each join of up to _MAX_WRAP_LINES consecutive lines, because an
    ingredient too long for the page column arrives wrapped -- see that
    constant. Not the whole line, because pypdf welds page furniture onto
    the last item of a
    block -- the real fixture ends `1/4 cup sour creamC o m p l e t e  y o
    u r  B o n  A p p e t i t`. Prefix rather than "appears anywhere",
    because a substring test accepts the method text: `2 Tbsp. graham
    cracker crumbs` IS in this source, inside `Set aside 2 Tbsp. graham
    cracker crumbs for serving`, and a phantom row for it appeared in
    every single live run.

    An exact prefix is accepted outright. A near miss above
    _REPAIR_THRESHOLD is REPAIRED to the source text rather than dropped,
    because dropping it is silent data loss: the first live two-pass run
    returned 12 of 16 ingredients, having transcribed `1/4` as `1/2` --
    adjacent Unicode codepoints -- on three lines. Those are not the model
    reading the wrong line, they are the model fumbling one glyph while
    copying the right one, and the source says what the answer should have
    been."""
    source_lines = [_normalize_for_match(line) for line in (source or "").splitlines() if line.strip()]
    accepted: list[str] = []
    rejected: list[str] = []
    for candidate in candidates:
        normalized = _normalize_for_match(candidate)
        if not normalized:
            continue
        best_text, best_ratio = None, 0.0
        wrap_lines = _MAX_WRAP_LINES if _AMOUNT_START.match(normalized) else 1
        for start in range(len(source_lines)):
            joined = source_lines[start]
            for extra in range(wrap_lines):
                if extra:
                    joined = f"{joined} {source_lines[start + extra]}"
                prefix = joined[: len(normalized)]
                ratio = difflib.SequenceMatcher(None, normalized, prefix).ratio()
                if ratio > best_ratio:
                    best_text, best_ratio = prefix, ratio
                # Joining further cannot help once the window is already
                # longer than what is being matched, and there is nothing
                # left to join at the end of the source.
                if len(joined) >= len(normalized) or start + extra + 1 >= len(source_lines):
                    break
        if best_text is not None and best_ratio >= _REPAIR_THRESHOLD:
            accepted.append(best_text)
        else:
            rejected.append(candidate)
    return accepted, rejected


def verify_copied_lines(candidates: list[str], source: str) -> list[str]:
    """Accepted lines only -- see reconcile_copied_lines for the rules and
    for the rejected half, which callers that report to a human want."""
    return reconcile_copied_lines(candidates, source)[0]


# --- Welded sources: when the extractor gives no line structure ---------
#
# Prefix matching above assumes the source puts each ingredient on its own
# line. That is a property of the EXTRACTOR AND THE DOCUMENT TOGETHER, not
# of the file type, and pypdf 5.0.1 produces both shapes:
#
#   publisher PDF (Bon Appetit pie)  -> one ingredient per line, 57 lines
#   browser print-to-PDF of a blog   -> the whole list welded into ONE line
#
# A real measured example, the entire kimchi ingredient list as pypdf
# returns it -- both components, 240 chars, no separators:
#
#   Ingredients2 1/2 lbs brussel sprouts1 medium daikon radish1 1/2
#   tablespoons diced ginger...Brine2 tablespoons sea salt4 cups water
#
# Against that, every correctly copied line scores 0.32-0.56 as a prefix
# and all of them are dropped -- measured, not estimated. The model had
# transcribed the list perfectly and verification destroyed it.
#
# The fallback below matches a block's lines IN ORDER as a forward-only
# chain inside ONE source line. Two structural guards, not numeric ones,
# keep it from re-opening the hole prefix matching exists to close:
#
#   MIN_RUN  a lone match is not a list. `2 Tbsp. graham cracker crumbs`
#            really is inside `Set aside 2 Tbsp. graham cracker crumbs for
#            serving` and scores 1.000 -- NO similarity threshold can ever
#            reject it, which is exactly why prefix was chosen. It is
#            rejected here because no second phantom sits next to it.
#   MAX_GAP  matches must be tightly packed. A welded list has gaps of 0;
#            words scattered through a paragraph of prose are not a list.
#
# Measured: kimchi 8/8 (gaps 0,0,0,0,0,22,0), pizza 6/6 (gaps 0,0,0,0,1),
# and 0 of the 6 known pie hallucinations accepted.
_WELD_SLACK = 6
_WELD_MIN_RUN = 2
_WELD_MAX_GAP = 40
_WELD_ANCHOR_WORD = re.compile(r"[A-Za-z]{4,}")


def _trim_to_alignment(normalized: str, window: str) -> tuple[str | None, float]:
    """Shrink `window` to the span that actually aligns with `normalized`.

    A fixed-length window cannot be right when the model's transcription
    and the source differ in length, and the error is not cosmetic: it
    took the next item's leading digit (`...red pepperpowder1`) and lost
    its own (` tablespoon Gsh sauce...`), which is a corrupted quantity on
    two ingredients."""
    blocks = [b for b in difflib.SequenceMatcher(None, normalized, window).get_matching_blocks() if b.size > 1]
    if not blocks:
        return None, 0.0
    trimmed = window[blocks[0].b : blocks[-1].b + blocks[-1].size]
    return trimmed, difflib.SequenceMatcher(None, normalized, trimmed).ratio()


def _walk_within_line(candidates: list[str], line: str) -> list[dict]:
    """Candidates matched in order, never backtracking, inside one line."""
    hits: list[dict] = []
    cursor = 0
    for candidate in candidates:
        normalized = _normalize_for_match(candidate)
        if not normalized:
            continue
        best_text, best_ratio, best_start, best_end = None, 0.0, -1, -1
        for word in sorted(_WELD_ANCHOR_WORD.findall(normalized), key=len, reverse=True)[:3]:
            offset = normalized.lower().find(word.lower())
            search_from = 0
            while True:
                found = line.lower().find(word.lower(), search_from)
                if found < 0:
                    break
                start = max(cursor, found - offset - _WELD_SLACK)
                window = line[start : start + len(normalized) + 2 * _WELD_SLACK]
                trimmed, ratio = _trim_to_alignment(normalized, window)
                if trimmed and ratio > best_ratio:
                    absolute = start + window.find(trimmed)
                    if absolute >= cursor:
                        best_text, best_ratio = trimmed, ratio
                        best_start, best_end = absolute, absolute + len(trimmed)
                search_from = found + 1
        if best_text is not None and best_ratio >= _REPAIR_THRESHOLD:
            hits.append({"text": best_text, "start": best_start, "end": best_end})
            cursor = best_end
    return hits


def find_welded_run(candidates: list[str], source: str) -> list[str]:
    """The copied block recovered from a source that has no line structure.

    Returns source-sliced text, same safety property as
    reconcile_copied_lines: nothing here contains a character the source
    does not have. Returns [] when the candidates do not form a tightly
    packed ordered run inside a single source line."""
    best: list[dict] = []
    for raw_line in (line for line in (source or "").splitlines() if line.strip()):
        hits = _walk_within_line(candidates, _normalize_for_match(raw_line))
        if len(hits) < _WELD_MIN_RUN:
            continue
        gaps = [hits[i + 1]["start"] - hits[i]["end"] for i in range(len(hits) - 1)]
        if gaps and max(gaps) > _WELD_MAX_GAP:
            continue
        if len(hits) > len(best):
            best = hits
    return [hit["text"] for hit in best]


def reconcile_block(candidates: list[str], source: str) -> tuple[list[str], list[str], str]:
    """One block reconciled against the source, by whichever strategy the
    evidence supports. Returns (accepted, rejected, strategy).

    Prefix ALWAYS runs first and wins ties. On a line-structured source it
    accepts everything, the welded walk never runs, and the pie's phantom
    row stays rejected exactly as before. The fallback is reached only
    where prefix has already failed, which is the source shape it was
    built for."""
    accepted, rejected = reconcile_copied_lines(candidates, source)
    if not rejected:
        return accepted, rejected, "prefix"
    welded = find_welded_run(candidates, source)
    if len(welded) > len(accepted):
        return welded, [], "welded"
    return accepted, rejected, "prefix"


# An ingredient is never NAMED after a span of time.
#
# Measured on the pizza, three runs running: pdfplumber gives
# `30 minutes hands-on effort` its own source line, immediately above the
# ingredient list. It starts with a number, it is real source text, and it
# verifies perfectly -- so it was stored as an ingredient called
# `minutes hands-on effort`, quantity 30. Verification cannot help here;
# the line IS in the source. Only what it says can distinguish it.
#
# The no-unit condition is not decoration. `Minute Rice` is a real
# product, and `1 cup Minute Rice` parses to a name beginning `Minute`.
# Requiring that no unit was parsed keeps every measured ingredient safe,
# because a recipe that means an ingredient gives it a measure.
_DURATION_WORDS = frozenset(
    {
        "second",
        "seconds",
        "minute",
        "minutes",
        "hour",
        "hours",
        "day",
        "days",
        "week",
        "weeks",
        "month",
        "months",
    }
)


def _names_a_duration(entry: dict) -> bool:
    if entry.get("unit"):
        return False
    first = (entry.get("ingredient_name") or "").strip().split()[:1]
    return bool(first) and first[0].strip(".,:;()").casefold() in _DURATION_WORDS


def normalize_instructions(raw) -> list[dict]:
    """Instruction steps as they are STORED: `{"component", "text"}`.

    Accepts what every caller actually has: bare strings (every recipe
    saved before components existed, and the JSON-LD import's flat list),
    dicts from the extraction schema or the API, or a mix. The column is
    not migrated, so this is the one place that has to know both shapes.

    The component goes through normalize_component, so the `main` sentinel
    and generic headings like `INGREDIENTS YOU'LL NEED:` become NULL here
    exactly as they do for ingredients -- the two lists are grouped by the
    same string in the UI, so they have to be normalised by the same
    rule."""
    steps: list[dict] = []
    for item in raw or []:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            component = normalize_component(item.get("component"))
        else:
            text = str(item).strip()
            component = None
        if text:
            steps.append({"component": component, "text": text})
    return steps


def instruction_texts(raw) -> list[str]:
    """Just the words, for prompts and anywhere a flat list is wanted."""
    return [step["text"] for step in normalize_instructions(raw)]


# A line that opens with a list bullet is an ITEM of the list, whatever
# else is true of it. A section heading is never bulleted -- that is what
# the bullet is for. The bullet is stripped BEFORE the amount is read,
# because it is the first thing _QTY_RE looks at.
#
# Two measured defects, one rule.
#
# 1. The pizza's `+ Chickpea flour or fine cornmeal` is a real sixth
#    ingredient with no amount, and the heading-split rule below deleted
#    it, because its block states amounts everywhere else. The plan
#    carried that as an open item on the grounds that relaxing "has no
#    amount" costs more than it pays -- which is true, and this does not
#    relax it. It uses a structural signal the source supplied, in the
#    same spirit as _AMOUNT_START: what separates these lines is how they
#    START.
#
# 2. Found by finally exercising trafilatura, 2026-08-07. It renders every
#    `<li>` as `- 1 cup stone-ground cornmeal`, so on EVERY non-JSON-LD
#    URL import, `_QTY_RE` failed at the bullet and the whole amount was
#    swallowed into the ingredient NAME: seven of seven nulls, named
#    `1 cup stone-ground cornmeal`. The name is this app's join key, so
#    inventory matching, grocery aggregation and nutrition resolution were
#    all broken on that path -- and the copied lines still verified
#    perfectly, so the review screen would have called it verified. It
#    was: verification checks that a line came from the page, not that
#    the arithmetic afterwards could read it.
#
# Hyphens and dashes count only when followed by whitespace, so a name
# beginning with one is untouched.
_LIST_BULLET = re.compile(r"^(?:[+*•‣▪]|[-–—](?=\s))\s*")  # noqa: RUF001 -- en/em dash bullets are what real pages use


def strip_list_bullet(line: str) -> tuple[str, bool]:
    """(line without its leading bullet, whether it had one)."""
    text = (line or "").strip()
    match = _LIST_BULLET.match(text)
    return (text[match.end() :].strip(), True) if match else (text, False)


def _split_headings_from_ingredients(entries: list[tuple[dict, bool]], component: str | None) -> list[dict]:
    """A welded source hides its section headings INSIDE the ingredient run.

    Measured on the kimchi file: the source line reads
    `...shrimp sauce(I excluded this)Brine2 tablespoons sea salt...`, so
    `Brine` is real source text, is copied correctly, verifies correctly,
    and then becomes an ingredient named "Brine" with no quantity. That is
    a junk row on the app's join key, which is the thing that has to stay
    clean (audit section 1, third architectural property).

    "Has no amount" cannot be the test on its own -- plenty of real
    recipes list `Kosher salt` with no amount and mean it. The test is
    whether THIS block states amounts at all: when most of its entries
    carry a quantity, one that carries neither quantity nor unit is not an
    ingredient. A block that states no amounts anywhere (blog and social
    recipes, very common) is left completely alone.

    A clean alphabetic label is PROMOTED to the component of everything
    after it, which is what the source meant by writing it -- the kimchi's
    salt and water really do belong to `Brine`. Anything else is dropped."""
    with_amounts = sum(1 for entry, _ in entries if entry["quantity"] is not None)
    if with_amounts <= len(entries) / 2:
        return [{**entry, "component": component} for entry, _ in entries]

    current = component
    kept: list[dict] = []
    for entry, was_bulleted in entries:
        if was_bulleted:
            # An item of the list. Not a heading, and not junk.
            kept.append({**entry, "component": current})
            continue
        if entry["quantity"] is None and not entry["unit"]:
            name = entry["ingredient_name"].strip()
            if name.replace(" ", "").isalpha() and len(name.split()) <= 3:
                current = normalize_component(name)
            continue
        kept.append({**entry, "component": current})
    return kept


# A continuation line starts lowercase, or with a bracket, and is short.
# A new ingredient starts with an amount; a heading starts capitalised.
_CONTINUATION_START = re.compile(r"^[a-z(\[]")
_CONTINUATION_MAX_WORDS = 3


def rejoin_wrapped_lines(text: str) -> str:
    """Put an ingredient the page wrapped back onto one line.

    pdfplumber returns the lines a page SHOWS, so an ingredient too long
    for the column arrives split. Measured in the kimchi fixture:

        4 tablespoons Korean red pepper
        powder
        1 tablespoon Gsh sauce or shrimp sauce
        (I excluded this)

    Every downstream attempt to cope with this has cost something. Joining
    at match time let method steps verify, because they wrap too. Leaving
    it alone truncated `Korean red pepper` and, when the model copied the
    fragment as its own line, promoted `powder` to a section HEADING --
    it is short, alphabetic and amountless, which is precisely what
    `Brine` looks like.

    Repairing it here instead means the model never sees the split, the
    matcher never sees it, and the heading rule never sees an orphan. One
    place, one rule, using the only fact that is actually available: two
    lines were adjacent in the source.

    The rule is deliberately narrow, because the failure it must not cause
    is welding a real amountless ingredient onto the line above it:

        2 cups flour
        Salt to taste        <- capitalised, so NOT joined

    A continuation starts lowercase or with a bracket, is at most
    _CONTINUATION_MAX_WORDS long, and follows a line that starts with an
    amount. `Salt to taste` fails the first test, `Brine` fails it too,
    and a following ingredient fails it by starting with a digit."""
    lines = (text or "").split("\n")
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if (
            out
            and stripped
            and _AMOUNT_START.match(out[-1])
            and _CONTINUATION_START.match(stripped)
            and len(stripped.split()) <= _CONTINUATION_MAX_WORDS
        ):
            out[-1] = f"{out[-1]} {stripped}"
            continue
        out.append(line)
    return "\n".join(out)


def dedupe_blocks(blocks: list) -> list[dict]:
    """Collapse blocks the model emitted more than once, verbatim.

    Measured: the pizza's pass 1 returned the same five-line `main` block
    fourteen times. Bounding the array (ExtractedIngredientLines.blocks)
    makes that terminate, but a terminated loop still arrives as fourteen
    blocks, and every one of them verifies -- so without this the app would
    store each ingredient once per repetition.

    Exact match on component AND lines. Two blocks that differ at all are
    both kept: a recipe legitimately having two sections named the same
    thing with the same contents is not a shape worth guessing at, while a
    verbatim repeat is a shape that has been observed."""
    seen: set[tuple] = set()
    unique: list[dict] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        lines = tuple(line for line in (block.get("lines") or []) if isinstance(line, str))
        key = (block.get("component"), lines)
        if key in seen:
            continue
        seen.add(key)
        unique.append(block)
    return unique


def extract_ingredients_two_pass(db: Session, content: str) -> list[dict]:
    """Ingredients for one recipe source, via transcribe-then-parse.

    Returns [] when the model gives nothing usable, which the caller reads
    as "keep whatever the single-call extraction produced" -- this is an
    improvement to one field, not a new dependency for the whole import.

    The model is asked only to locate and copy. Every line it returns is
    checked against the source (verify_copied_lines), and the amounts are
    then read by parse_ingredient_line_amounts, which is arithmetic and
    gets 16 entries out of these 15 lines exactly right where four live
    runs of the 9B got zero."""
    # Repaired ONCE, here, so that pass 1's prompt and the verification it
    # is checked against are the same text. Doing it at the extractor
    # would also change receipts, bloodwork and knowledge files, which
    # have their own line shapes and no measured need for this.
    content = rejoin_wrapped_lines(content)
    prompt_template = get_ingredient_lines_prompt(db)
    budget = ollama_client.content_char_budget(
        db,
        prompt_overhead_chars=len(prompt_template),
        response_reserve_tokens=INGREDIENT_LINES_RESPONSE_TOKENS,
    )
    raw, done_reason = ollama_client.chat_json_with_reason(
        db,
        [{"role": "user", "content": prompt_template.replace("{content}", content[:budget])}],
        schema=INGREDIENT_LINES_SCHEMA,
        model=ollama_client.get_extraction_model(db),
        response_tokens=INGREDIENT_LINES_RESPONSE_TOKENS,
    )
    if done_reason == "length":
        # Measured on a 24-page printed blog page: pass 1 ran to exactly
        # INGREDIENT_LINES_RESPONSE_TOKENS and its JSON was cut mid-array.
        # A truncated transcription is a partial one, and the whole point
        # of the completeness gate below is that partial is worse than
        # nothing -- so decline here too rather than salvage a fragment.
        print(
            "[recipe_import] two-pass DECLINED: pass 1 hit the response token cap "
            f"({INGREDIENT_LINES_RESPONSE_TOKENS}) and its output is truncated. "
            "Keeping the single-call ingredients.",
            flush=True,
        )
        return []
    data = _extract_json_object(raw)
    if not data:
        return []

    # The completeness gate, applied PER BLOCK.
    #
    # A block is one section of one ingredient list, and that is the unit
    # the gate has to judge. Measuring coverage across the whole response
    # averages unrelated things together, and it does real harm in both
    # directions -- measured, on the same file, one run apart:
    #
    #   run 1  1 of 24 lines verified, no gate. That single line replaced
    #          the entire single-call list. A one-ingredient kimchi.
    #   run 2  10 of 24 verified with a GLOBAL gate at 0.6 -> 0.417, so
    #          everything was refused, including a block that had verified
    #          completely. The model is not wrong to also copy a block
    #          this app then rejects; being punished for it is our bug.
    #
    # Per block, a section that verifies stands on its own and a section
    # that does not is dropped without taking its neighbours with it.
    result = ingredients_from_pass1_blocks(data.get("blocks") or [], content)
    if result.duplicate_blocks:
        print(
            f"[recipe_import] pass 1 repeated itself: {result.blocks_returned} block(s) returned, "
            f"{result.blocks_returned - result.duplicate_blocks} distinct. Duplicates collapsed.",
            flush=True,
        )
    for message in result.messages:
        print(f"[recipe_import] {message}", flush=True)
    if result.ingredients:
        print(
            f"[recipe_import] two-pass supplied {len(result.ingredients)} ingredient(s) from "
            f"{result.lines_verified} verified line(s) of {result.lines_returned} copied, "
            f"strategy={'+'.join(sorted(result.strategies))}",
            flush=True,
        )
    return result.ingredients


@dataclass
class Pass1Result:
    """Everything the block loop decided, for the two callers that need
    different parts of it."""

    ingredients: list[dict]
    lines_returned: int
    lines_verified: int
    blocks_returned: int
    duplicate_blocks: int
    blocks_dropped: int
    strategies: set
    # What the app prints. Returned rather than printed here so the batch
    # harness can render them as a table column instead of interleaving
    # them with its own output.
    messages: list


def split_welded_copied_lines(raw_lines: list[str]) -> list[str]:
    """Pass 1 sometimes copies a whole ingredient list as ONE array entry,
    newlines and all. Split those back into the lines they are.

    Measured on the first real bookmarks run (2026-08-07), which was also
    the first time trafilatura ran on real pages. Four recipes in one
    batch, each with its entire list welded into a single copied line:

        ['- 1 tablespoon butter for greasing the baking sheet\n
          - 1/4 cup buttermilk\n- 1/4 cup honey ... ']

    Verification then looks for that nine-line blob verbatim in the
    source, cannot find it, and drops it -- so the block scores 0 of 1
    verified, the coverage gate drops the WHOLE block, and the import
    silently falls back to the single call's unverified list. Every log
    line reads as the system working correctly, which it was; the input
    was one line that should have been nine.

    Why it appears on this path specifically: trafilatura renders every
    `<li>` as `- item` on consecutive lines with no blank line between,
    so the list LOOKS like one paragraph and the model copies it as one.

    **The constraint that makes this safe.** Splitting is a widening of
    what can verify, and this project has already measured what an
    unconstrained widening costs -- joining consecutive source lines
    disarmed the same gate and imported 17 method steps as ingredients.
    A method paragraph split on its newlines would produce lines that are
    all real source text and would all verify, which is precisely the
    failure that gate exists to prevent.

    So a blob is only split when EVERY line it produces starts with a
    bullet or an amount. That is the same discriminator `_AMOUNT_START`
    already provides for the joined window, and it is the thing method
    prose does not do. A blob that fails the test is passed through
    unchanged, fails verification exactly as it does today, and is dropped
    by the gate exactly as it is today -- this can only ever add lines
    that look like ingredients, never rescue a paragraph."""
    out: list[str] = []
    for line in raw_lines:
        if "\n" not in line:
            out.append(line)
            continue
        parts = [part.strip() for part in line.split("\n")]
        parts = [part for part in parts if part]
        if len(parts) > 1 and all(_looks_like_a_list_item(part) for part in parts):
            out.extend(parts)
        else:
            out.append(line)
    return out


def _looks_like_a_list_item(text: str) -> bool:
    """Starts with a bullet, or with an amount. Deliberately narrow -- see
    split_welded_copied_lines for why anything wider is unsafe."""
    bare, bulleted = strip_list_bullet(text)
    return bulleted or bool(_AMOUNT_START.match(bare))


def ingredients_from_pass1_blocks(raw_blocks: list, source: str) -> Pass1Result:
    """Pass 1's copied blocks -> stored ingredients, and the numbers that
    explain the decision.

    **This function exists to be called TWICE.** The app calls it after
    making the model call; `scripts/check_recipe_import_batch.py` calls it
    after making its own, because the harness needs pass 1's raw counts
    and cannot get them from a function that only returns ingredients.

    The harness used to re-implement this loop. It duplicated for a real
    reason -- calling the app's function AND pass 1 separately would mean
    three model calls per file where the app makes two, which on one
    worker thread makes a corpus run needlessly slow and misreports what
    an import costs. But a copy of a policy drifts from it, and this one
    did: it scored the pizza at six ingredients while the app stored five,
    and the 2026-08-07 bullet change broke its call signature outright.
    One function, two callers, no policy in the copy."""
    ingredients: list[dict] = []
    returned = verified = blocks_dropped = 0
    strategies: set[str] = set()
    messages: list[str] = []

    raw_blocks = [b for b in raw_blocks if isinstance(b, dict)]
    blocks = dedupe_blocks(raw_blocks)
    for block in blocks:
        component = normalize_component(block.get("component"))
        raw_lines = [line for line in (block.get("lines") or []) if isinstance(line, str)]
        # A whole list copied as one entry is nine ingredients, not one --
        # and left welded it scores 0 of 1 and takes the block with it.
        raw_lines = split_welded_copied_lines(raw_lines)
        if not raw_lines:
            continue
        kept, discarded, strategy = reconcile_block(raw_lines, source)
        returned += len(raw_lines)
        if discarded:
            messages.append(f"pass 1 returned {len(discarded)} line(s) not found in the source, dropped: {discarded}")
        if len(kept) / len(raw_lines) < _TWO_PASS_MIN_COVERAGE:
            blocks_dropped += 1
            messages.append(
                f"block {component or 'main'!r} DROPPED: {len(kept)} of {len(raw_lines)} copied line(s) "
                "verified. Not a section of an ingredient list."
            )
            continue
        verified += len(kept)
        strategies.add(strategy)
        # The bullet comes off before the amount is read -- see
        # _LIST_BULLET -- and whether there WAS one is carried alongside,
        # because it is the only thing separating an amountless list item
        # from a section heading.
        block_entries = []
        for line in kept:
            bare, bulleted = strip_list_bullet(line)
            for entry in parse_ingredient_line_amounts(bare):
                if entry["ingredient_name"] and not _names_a_duration(entry):
                    block_entries.append((entry, bulleted))
        ingredients.extend(_split_headings_from_ingredients(block_entries, component))

    return Pass1Result(
        ingredients=ingredients,
        lines_returned=returned,
        lines_verified=verified,
        blocks_returned=len(raw_blocks),
        duplicate_blocks=len(raw_blocks) - len(blocks),
        blocks_dropped=blocks_dropped,
        strategies=strategies,
        messages=messages,
    )


def _parse_ingredient_line(line: str) -> dict:
    """Splits one free-text `recipeIngredient` string into this app's
    ingredient shape. See this section's module-level docstring for the
    accuracy tradeoff -- this is best-effort, reviewed by the user before
    anything saves, same as the Ollama extraction path it's an
    alternative to.

    Single-dict view of parse_ingredient_line_amounts, kept because the
    JSON-LD and folder-import callers store one row per source line and a
    compound amount is a rarity there. New code should prefer the list
    form."""
    return parse_ingredient_line_amounts(line)[0]


_ISO8601_DURATION_RE = re.compile(r"^P(?:\d+D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")


def _parse_iso8601_duration_minutes(value) -> int | None:
    if not isinstance(value, str):
        return None
    m = _ISO8601_DURATION_RE.match(value.strip())
    if not m or not any(m.groups()):
        return None
    hours, minutes, _seconds = (int(g) if g else 0 for g in m.groups())
    return hours * 60 + minutes


def _first_number(value) -> float | None:
    """Pulls the first numeric token out of a schema.org value that's
    often free text alongside a number -- recipeYield ("4 servings"),
    a nutrition value ("270 kcal", "10 g"). Handles a bare number or a
    numeric string directly too."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"[\d.]+", str(value))
    return float(match.group(0)) if match else None


def _flatten_jsonld_instructions(value) -> list[str]:
    """recipeInstructions is one of: a single string (sometimes with
    steps separated by newlines), a list of strings, a list of HowToStep
    objects ({"@type": "HowToStep", "text": ...}), or nested HowToSection
    objects ({"@type": "HowToSection", "itemListElement": [...]})."""
    return [step["text"] for step in _jsonld_instruction_steps(value)]


def _jsonld_instruction_steps(value, component: str | None = None) -> list[dict]:
    """The same shapes, but keeping the HowToSection names.

    A HowToSection IS a component -- schema.org's `{"@type":
    "HowToSection", "name": "For the Crust", "itemListElement": [...]}` is
    the publisher saying which part of the dish these steps belong to.
    That name was being parsed and then thrown away, so a structured
    import produced a flat step list from a source that had told us how it
    was divided. Sections nest, so the label is carried down."""
    if value is None:
        return []
    if isinstance(value, str):
        return [{"component": component, "text": s.strip()} for s in re.split(r"\n+", value) if s.strip()]
    if isinstance(value, dict):
        value = [value]
    steps: list[dict] = []
    for item in value:
        if isinstance(item, str):
            if item.strip():
                steps.append({"component": component, "text": item.strip()})
        elif isinstance(item, dict):
            if "itemListElement" in item:  # HowToSection
                label = normalize_component(item.get("name")) or component
                steps.extend(_jsonld_instruction_steps(item["itemListElement"], label))
            elif item.get("text"):
                steps.append({"component": component, "text": str(item["text"]).strip()})
            elif item.get("name"):
                steps.append({"component": component, "text": str(item["name"]).strip()})
    return steps


def _author_name_from_jsonld(value) -> str | None:
    if isinstance(value, dict):
        return value.get("name") or None
    if isinstance(value, list) and value:
        return _author_name_from_jsonld(value[0])
    if isinstance(value, str):
        return value.strip() or None
    return None


def _image_url_from_jsonld(value) -> str | None:
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        return value.get("url") or None
    if isinstance(value, list) and value:
        return _image_url_from_jsonld(value[0])
    return None


# schema.org NutritionInformation property -> this app's nutrition dict
# key. Units are fixed by the schema.org property definitions themselves
# (calories in kcal, protein/fat/carb/fiber/sugar content in grams,
# sodium/cholesterol content in milligrams), so _first_number's leading-
# digits extraction is safe regardless of whether the source also wrote
# out a unit suffix like "10 g".
_JSONLD_NUTRITION_MAP: dict[str, str] = {
    "calories": "calories",
    "proteinContent": "protein_g",
    "carbohydrateContent": "carbs_g",
    "fatContent": "fat_g",
    "fiberContent": "fiber_g",
    "sodiumContent": "sodium_mg",
    "cholesterolContent": "cholesterol_mg",
    "saturatedFatContent": "saturated_fat_g",
    "sugarContent": "sugars_g",
}


def _nutrition_from_jsonld(value) -> dict:
    if not isinstance(value, dict):
        return {}
    nutrition = {}
    for schema_key, app_key in _JSONLD_NUTRITION_MAP.items():
        n = _first_number(value.get(schema_key))
        if n is not None:
            nutrition[app_key] = n
    return nutrition


def _iter_jsonld_nodes(data):
    """Flattens a parsed JSON-LD document's possible shapes (a single
    object, a list of objects, or a `{"@graph": [...]}` wrapper -- all
    three are common across real sites) into a flat stream of dict nodes
    to search for a Recipe type."""
    if isinstance(data, list):
        for item in data:
            yield from _iter_jsonld_nodes(item)
    elif isinstance(data, dict):
        if "@graph" in data and isinstance(data["@graph"], list):
            yield from _iter_jsonld_nodes(data["@graph"])
        else:
            yield data


def _is_recipe_node(node: dict) -> bool:
    node_type = node.get("@type")
    if isinstance(node_type, list):
        return any(str(t).lower() == "recipe" for t in node_type)
    return str(node_type).lower() == "recipe"


def _recipe_dict_from_jsonld_document(data) -> dict | None:
    """Shared core of extract_jsonld_recipe() and
    extract_jsonld_recipe_from_json() below -- given an already-parsed
    JSON-LD document (a dict/list, not raw text), finds the first usable
    Recipe node and converts it to a coerce_recipe_fields()-input-shaped
    dict. Split out so a raw uploaded .json file (backlog B9.2's export
    round trip -- see routers/recipes.py's import_recipe) can reuse the
    exact same node-search/field-mapping logic the HTML script-tag path
    already uses, rather than duplicating it or forcing a bare JSON
    upload through a fake HTML wrapper just to reuse extract_jsonld_recipe."""
    for node in _iter_jsonld_nodes(data):
        if not isinstance(node, dict) or not _is_recipe_node(node):
            continue
        title = node.get("name")
        if not title:
            continue  # a Recipe node with no name isn't usable -- keep looking
        ingredients = [
            _parse_ingredient_line(line)
            for line in (node.get("recipeIngredient") or node.get("ingredients") or [])
            if isinstance(line, str) and line.strip()
        ]
        return {
            "title": str(title).strip(),
            "description": node.get("description") or None,
            "default_servings": _first_number(node.get("recipeYield")),
            "prep_time_minutes": _parse_iso8601_duration_minutes(node.get("prepTime")),
            "cook_time_minutes": _parse_iso8601_duration_minutes(node.get("cookTime")),
            "instructions": _jsonld_instruction_steps(node.get("recipeInstructions")),
            "ingredients": ingredients,
            "nutrition": _nutrition_from_jsonld(node.get("nutrition")),
            "tags": [],  # no fixed vocabulary in JSON-LD -- left for the user to add on review, never guessed
            # schema.org has no standard "tips" property -- real-world
            # sites will never set this, but recipe_to_jsonld() (B9.2's
            # export) writes one under the namespaced "chef:tips" key, so
            # reading it back here is what makes a Chef-exported-then-
            # reimported recipe keep its tips rather than silently
            # dropping them on the round trip.
            "tips": [str(t).strip() for t in (node.get("chef:tips") or []) if isinstance(t, str) and t.strip()],
            # Not part of coerce_recipe_fields()'s output shape -- read
            # directly by routers/recipes.py before/instead of citation
            # fields it would otherwise only get from trafilatura's
            # metadata extraction.
            "_source_author": _author_name_from_jsonld(node.get("author")),
            "_image_url": _image_url_from_jsonld(node.get("image")),
        }
    return None


def extract_jsonld_recipe(html: str) -> dict | None:
    """Returns a coerce_recipe_fields()-input-shaped dict for the first
    schema.org Recipe block found in `html`'s `<script
    type="application/ld+json">` tags, or None if the page doesn't
    publish one (or it's malformed) -- callers fall back to the Ollama
    extraction path in that case, this never raises for "not found"."""
    try:
        tree = lxml.html.fromstring(html)
    except Exception:
        return None

    for script in tree.xpath('//script[@type="application/ld+json"]'):
        raw = script.text_content()
        if not raw or not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        result = _recipe_dict_from_jsonld_document(data)
        if result is not None:
            return result
    return None


def extract_jsonld_recipe_from_json(raw_json_text: str) -> dict | None:
    """Same output contract as extract_jsonld_recipe(), for a raw JSON
    document rather than one embedded in an HTML <script> tag -- what a
    recipe exported via recipe_to_jsonld() (backlog B9.2) looks like
    re-uploaded through the file-import path. Returns None (never
    raises) for unparseable JSON or JSON with no usable Recipe node, the
    same "not found, fall back" contract as extract_jsonld_recipe()."""
    try:
        data = json.loads(raw_json_text)
    except (json.JSONDecodeError, TypeError):
        return None
    return _recipe_dict_from_jsonld_document(data)


# --- Recipe -> schema.org JSON-LD (backlog B9.2: portable recipe export) -
#
# The reverse of extract_jsonld_recipe() above -- renders a Recipe row
# (plus its ingredients) as a schema.org Recipe document. Deliberately
# the SAME format the URL importer reads, not a Chef-specific export
# shape: a recipe exported here and later re-imported via "Import from
# URL"/file goes through extract_jsonld_recipe() -> coerce_recipe_fields()
# on the way back in, so this is a genuine round trip, not a one-way
# archive dump. (Full fidelity isn't guaranteed both directions --
# ingredient quantity/unit/prep_note gets flattened to one free-text
# line the same way every other JSON-LD-publishing recipe site's does,
# then re-parsed by the same heuristic _parse_ingredient_line() import
# already relies on -- but that's the real, existing contract of this
# format, not a gap specific to this export.)

# app nutrition key -> (schema.org property, display unit suffix). The
# inverse of _JSONLD_NUTRITION_MAP, with units restored since schema.org
# properties are typed as free text ("270 calories", "9 g", "500 mg") --
# _first_number's leading-digits extraction on the read side means the
# suffix is cosmetic/round-trip-safe either way, but omitting it would
# make the exported file look wrong to a human or another tool that reads
# it, so it's written out properly here.
_APP_NUTRITION_TO_JSONLD: dict[str, tuple[str, str]] = {
    "calories": ("calories", " calories"),
    "protein_g": ("proteinContent", " g"),
    "carbs_g": ("carbohydrateContent", " g"),
    "fat_g": ("fatContent", " g"),
    "fiber_g": ("fiberContent", " g"),
    "sodium_mg": ("sodiumContent", " mg"),
    "cholesterol_mg": ("cholesterolContent", " mg"),
    "saturated_fat_g": ("saturatedFatContent", " g"),
    "sugars_g": ("sugarContent", " g"),
}


def _minutes_to_iso8601_duration(minutes: int | None) -> str | None:
    """Inverse of _parse_iso8601_duration_minutes -- e.g. 90 -> 'PT1H30M'.
    Returns None for anything non-positive rather than emitting 'PT0M',
    matching extract_jsonld_recipe's own "absent means unknown" contract
    (a Recipe with no prepTime/cookTime published is common and correct
    JSON-LD, not an error)."""
    if minutes is None or minutes <= 0:
        return None
    hours, mins = divmod(int(minutes), 60)
    out = "PT"
    if hours:
        out += f"{hours}H"
    if mins or not hours:
        out += f"{mins}M"
    return out


def _format_quantity(quantity: float) -> str:
    """2.0 -> '2', 1.5 -> '1.5', 0.3333... -> '0.333' -- avoids both a
    misleading trailing '.0' on whole numbers and runaway float noise
    from prior unit-conversion arithmetic."""
    if quantity == int(quantity):
        return str(int(quantity))
    return f"{quantity:.3f}".rstrip("0").rstrip(".")


def _jsonld_ingredient_line(ing: RecipeIngredient) -> str:
    """Reconstructs one free-text ingredient line ('2 cups flour,
    sifted') from this app's structured quantity/unit/name/prep_note
    fields -- schema.org's recipeIngredient is itself just a list of
    free-text strings (see extract_jsonld_recipe's own docstring), so
    there is no structured shape to preserve beyond formatting it back
    into the same shape _parse_ingredient_line() already expects on
    import. Named distinctly from the pre-existing _format_ingredient_line
    (below, in the recipe-chat-context section) -- that one formats a
    dict for a chat prompt, this one formats an ORM RecipeIngredient for
    JSON-LD export; same-sounding job, different input shape, kept as
    two functions rather than one overloaded on argument type."""
    parts = []
    if ing.quantity is not None:
        parts.append(_format_quantity(ing.quantity))
    if ing.unit:
        parts.append(ing.unit)
    parts.append(ing.ingredient_name)
    line = " ".join(parts)
    if ing.prep_note:
        line += f", {ing.prep_note}"
    return line


def recipe_to_jsonld(recipe: Recipe, image_url: str | None = None) -> dict:
    """Renders `recipe` as a schema.org Recipe JSON-LD document for
    backlog B9.2's data-export goal. `image_url` is passed in by the
    caller (routers/recipes.py) rather than derived here, since building
    an absolute URL to this app's own /api/recipes/{id}/image endpoint
    needs the current request's base URL, which this service layer
    intentionally has no access to (see this file's module docstring --
    business logic, not request handling)."""
    doc: dict = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": recipe.title,
    }
    if recipe.description:
        doc["description"] = recipe.description
    doc["recipeYield"] = str(recipe.default_servings)
    prep_iso = _minutes_to_iso8601_duration(recipe.prep_time_minutes)
    if prep_iso:
        doc["prepTime"] = prep_iso
    cook_iso = _minutes_to_iso8601_duration(recipe.cook_time_minutes)
    if cook_iso:
        doc["cookTime"] = cook_iso
    if recipe.instructions:
        # schema.org's own answer to components is HowToSection, which is
        # what _flatten_jsonld_instructions already reads on the way IN.
        # Exporting sections means a Chef export re-imports with its
        # components intact, here or anywhere else that speaks schema.org.
        steps = normalize_instructions(recipe.instructions)
        if any(step["component"] for step in steps):
            sections: list[dict] = []
            for step in steps:
                label = step["component"]
                if not sections or sections[-1]["name"] != label:
                    sections.append({"@type": "HowToSection", "name": label, "itemListElement": []})
                sections[-1]["itemListElement"].append({"@type": "HowToStep", "text": step["text"]})
            doc["recipeInstructions"] = sections
        else:
            doc["recipeInstructions"] = [{"@type": "HowToStep", "text": step["text"]} for step in steps]
    if recipe.ingredients:
        doc["recipeIngredient"] = [_jsonld_ingredient_line(ing) for ing in recipe.ingredients]
    if recipe.nutrition:
        nutrition_doc = {"@type": "NutritionInformation"}
        for app_key, (schema_key, suffix) in _APP_NUTRITION_TO_JSONLD.items():
            value = recipe.nutrition.get(app_key)
            if value is not None:
                nutrition_doc[schema_key] = f"{_format_quantity(float(value))}{suffix}"
        if len(nutrition_doc) > 1:  # more than just @type
            doc["nutrition"] = nutrition_doc
    if recipe.source_url:
        doc["url"] = recipe.source_url
    if recipe.source_name:
        doc["publisher"] = {"@type": "Organization", "name": recipe.source_name}
    if recipe.source_author:
        doc["author"] = {"@type": "Person", "name": recipe.source_author}
    if image_url:
        doc["image"] = image_url
    if recipe.tags:
        doc["keywords"] = ", ".join(sorted(tag.name for tag in recipe.tags))
    if recipe.rating:
        doc["aggregateRating"] = {
            "@type": "AggregateRating",
            "ratingValue": recipe.rating,
            "ratingCount": 1,
            "bestRating": 5,
            "worstRating": 1,
        }
    if recipe.tips:
        # Not a real schema.org Recipe property -- schema.org has no
        # standard field for "tips/variations" -- so this rides along as
        # a namespaced extension property rather than being silently
        # dropped. A generic JSON-LD consumer ignores unknown properties
        # safely; Chef's own importer (extract_jsonld_recipe) doesn't
        # read this key today, a documented, deliberate limitation
        # rather than a bug (tips were never part of the import contract
        # either).
        doc["chef:tips"] = list(recipe.tips)
    return doc


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
    except Exception:
        return None


# --- Shared per-file import parsing ------------------------------------
#
# Backlog B13.1: pulled out of routers/
# recipes.py's import_recipe (where this branching logic used to live
# inline, only reachable via a single browser file upload) so the new
# folder-scan batch importer (recipe_folder_import_service.py) can parse
# each file it finds through the EXACT same logic a one-at-a-time upload
# already uses -- one code path for "what does Chef do with a recipe
# file," regardless of whether that file arrived via <input type=file>
# or was found sitting in a mounted folder.


def _extract_via_ollama(db: Session, content: str) -> str:
    """Sends extracted/plain text through the active recipe-import prompt
    (get_recipe_import_prompt -- the household's Settings-page override
    if set, else RECIPE_IMPORT_PROMPT) and returns the model's raw
    response text. Near-identical to routers/recipes.py's own
    `_run_text_extraction` (kept as two small, separate functions rather
    than one shared cross-module private import -- an underscore-prefixed
    function is module-internal by convention in this codebase; this is
    recipe_service.py's own copy for its own internal use, not exported).

    Content is capped by ollama_client.content_char_budget, which scales
    with the configured context window. This backs BOTH a single photo/PDF
    upload and every file recipe_folder_import_service.py processes --
    neither has JSON-LD available (that is a URL-only shortcut, see
    routers/recipes.py's import order), so this is always the messier,
    no-structured-data path."""
    prompt_template = get_recipe_import_prompt(db)
    budget = ollama_client.content_char_budget(
        db, prompt_overhead_chars=len(prompt_template), response_reserve_tokens=RECIPE_RESPONSE_TOKENS
    )
    prompt = prompt_template.replace("{content}", content[:budget])
    return ollama_client.chat_json(
        db,
        [{"role": "user", "content": prompt}],
        schema=RECIPE_SCHEMA,
        response_tokens=RECIPE_RESPONSE_TOKENS,
    )


def parse_recipe_file_content(db: Session, raw_bytes: bytes, filename: str, content_type: str = "") -> dict:
    """Determines how to extract a recipe from one file's raw bytes,
    based on its content type/extension, and returns everything the
    shared `finish_recipe_parse` tail step below needs:
        {"raw_output": str, "default_source": str, "citation": dict,
         "image_path": str | None, "jsonld_parsed": dict | None,
         "source_text": str | None}

    Mirrors, verbatim, the JSON/image/PDF/text branching that used to
    live directly inside routers/recipes.py's import_recipe -- see this
    section's module comment. The one genuinely NEW branch versus the
    original single-upload code is `.html`/`.htm` (see below): a browser
    file-picker upload of a raw saved-webpage HTML file was always an
    unusual thing for a human to do (URL import already covers "a recipe
    web page"), but a folder of recipes collected over the years
    plausibly has some saved-as-HTML files in it, so this batch path
    needed a real answer for that case -- the single-upload path gets it
    too now, for free, since both call this same function.

    Raises RuntimeError (never a bare exception, never returns silently)
    for "unsupported file type" -- every other failure mode (unreadable
    PDF, a photo/text the model can't parse) surfaces downstream, from
    `finish_recipe_parse` or from whatever raised inside Ollama/pypdf."""
    filename_lower = (filename or "").lower()
    citation: dict = {}
    image_path: str | None = None
    jsonld_parsed: dict | None = None
    # The raw text the model was shown, when there is any. finish_recipe_parse
    # needs it to verify pass 1 copied real lines. None for a photo (no text
    # layer to check against) and for JSON-LD (already structured).
    source_text: str | None = None

    if content_type in ("application/json", "application/ld+json") or filename_lower.endswith((".json", ".jsonld")):
        try:
            json_text = raw_bytes.decode("utf-8")
        except UnicodeDecodeError:
            json_text = ""
        if json_text:
            jsonld_parsed = extract_jsonld_recipe_from_json(json_text)

    if jsonld_parsed is not None:
        citation = {"source_author": jsonld_parsed.get("_source_author")}
        image_url = jsonld_parsed.get("_image_url")
        if image_url:
            fetched = fetch_image_bytes(image_url)
            if fetched:
                raw_image_bytes, image_content_type = fetched
                with contextlib.suppress(ValueError):
                    image_path = recipe_image_service.save_image(image_content_type, raw_image_bytes)
        raw_output = (
            "(parsed directly from the file's structured schema.org Recipe data -- Ollama was not used for this import)"
        )
        default_source = "import_file_jsonld"
    elif content_type.startswith("image/"):
        raw_output = ollama_client.describe_image_json(
            db,
            raw_bytes,
            get_recipe_import_prompt(db).replace("{content}", "[see attached photo]"),
            schema=RECIPE_SCHEMA,
            response_tokens=RECIPE_RESPONSE_TOKENS,
        )
        default_source = "import_image"
        with contextlib.suppress(ValueError):
            image_path = recipe_image_service.save_image(content_type, raw_bytes)
    elif content_type == "application/pdf" or filename_lower.endswith(".pdf"):
        pdf_text = extract_pdf_text(raw_bytes)
        if not pdf_text.strip():
            # A scanned or photographed PDF has no text layer, so pypdf
            # correctly returns nothing. Sending that empty string on as
            # the prompt's SOURCE block produced "Could not extract a
            # recipe from that input" -- indistinguishable, from the
            # user's side, from a genuine parse failure on a perfectly
            # readable file. The receipt importer already guards this;
            # the recipe importer did not.
            raise RuntimeError(
                "That PDF has no extractable text -- it looks like a scan or a photo saved as a PDF. "
                "Export or save a text-based PDF, or upload the page as an image instead so the "
                "vision model can read it."
            )
        source_text = pdf_text
        raw_output = _extract_via_ollama(db, pdf_text)
        default_source = "import_file"
    elif filename_lower.endswith((".html", ".htm")):
        # New for B13.1 -- tries the file's own schema.org JSON-LD first
        # (same B9.3 preference URL import already applies), falling
        # back to trafilatura's main-content extraction + the model,
        # same as every other text-bearing file below.
        html_text = raw_bytes.decode("utf-8", errors="ignore")
        jsonld_parsed = extract_jsonld_recipe(html_text)
        if jsonld_parsed is not None:
            citation = {"source_author": jsonld_parsed.get("_source_author")}
            image_url = jsonld_parsed.get("_image_url")
            if image_url:
                fetched = fetch_image_bytes(image_url)
                if fetched:
                    raw_image_bytes, image_content_type = fetched
                    with contextlib.suppress(ValueError):
                        image_path = recipe_image_service.save_image(image_content_type, raw_image_bytes)
            raw_output = (
                "(parsed directly from the file's structured schema.org Recipe data -- "
                "Ollama was not used for this import)"
            )
            default_source = "import_file_jsonld"
        else:
            page = extract_content_from_html(html_text)
            citation = {"source_name": page.get("sitename"), "source_author": page.get("author")}
            source_text = page.get("text") or ""
            raw_output = _extract_via_ollama(db, source_text)
            default_source = "import_file"
    else:
        try:
            text_content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("Unsupported file type for recipe import") from exc
        source_text = text_content
        raw_output = _extract_via_ollama(db, text_content)
        default_source = "import_file"

    return {
        "raw_output": raw_output,
        "default_source": default_source,
        "citation": citation,
        "image_path": image_path,
        "jsonld_parsed": jsonld_parsed,
        "source_text": source_text,
    }


# How the ingredients in a preview were actually arrived at, stated to the
# household instead of only to the container log.
#
# Recorded because the silence was measured. On 2026-08-06 a capture of
# THIS APP'S OWN REVIEW FORM was imported as if it were a recipe. Pass 1
# dutifully copied its rows -- `graham crackers 12 unit prep note X`,
# placeholders and delete button and all -- both blocks failed
# verification at 0 of 4 and 0 of 12, which is the correct answer because
# a form is not an ingredient list, and two-pass declined. The single
# call's unverified guesses were then presented in a review screen
# indistinguishable from a verified one, and the only record that anything
# had been refused was a print line nobody reads.
#
# The form's whole job is judging what the importer got right. It cannot
# do that without knowing whether any of it was checked.
#
# `_`-prefixed so RecipeCreate drops it exactly the way JSON-LD's own
# private keys are dropped (see coerce_recipe_fields); a caller that wants
# it pops it deliberately.
INGREDIENT_PROVENANCE_KEY = "_ingredient_provenance"

# Same `_` convention, same reason -- see unverified_instruction_facts.
INSTRUCTION_WARNINGS_KEY = "_instruction_warnings"


def _provenance(path: str, *, reason: str | None = None, verified: int | None = None, single_call: int | None = None):
    """One provenance record. `path` is how the stored ingredients were
    produced (`two_pass`, `jsonld`, `single_call`); `reason` says why
    verification did not happen when it did not. Counts are included
    because "6 verified against a single call of 12" is the shape of the
    judgement the gate made, and a human reviewing the rows deserves the
    same numbers it used."""
    return {"path": path, "reason": reason, "verified": verified, "single_call": single_call}


# --- Instructions: the one list nothing checked ---------------------------
#
# Ingredients are copied from the source and verified against it. The
# method is free prose the model writes, governed only by rule 8's
# instruction to summarise functionally -- so for the whole life of this
# importer, the half of a recipe that can actually ruin a dish was the
# unchecked half.
#
# Measured on the pie, 2026-08-06. The source says:
#
#     Stir gelatin, cinnamon, nutmeg, a scant 1/2 cup sugar, and 1/2 tsp.
#     salt in a small saucepan.        <- off heat, nothing liquid yet
#     ... whisk into sugar mixture. Cook over medium heat ... about 5
#     minutes.
#
# The import produced `in a small saucepan over medium heat until
# dissolved (do not boil)` as its own step, and then repeated the real
# cooking instruction two steps later. Followed literally that heats dry
# gelatin and sugar in an empty pan.
#
# Full verification is not available here and should not be pretended at:
# rule 8 REQUIRES paraphrase for copyright reasons, so a step legitimately
# does not appear in the source as text, and the ingredient path's
# copy-then-check trick cannot be reused.
#
# What CAN be checked is the part of a method that is not prose: its
# NUMBERS. A temperature or a duration is a fact with a standard written
# form, it is the class of error that actually burns dinner (a 350 oven
# for a 325 recipe, a 45-minute bake for a 20-25 minute one), and it is
# arithmetic to check. Technique drift -- `pipe` for `dollop` -- is not
# caught by this and is not claimed to be.
#
# Deliberately generous, because a false alarm on the review screen is
# worse than a missed one here: this is a prompt to look, not a gate. A
# number matches if the source states it ANYWHERE with the same unit, so
# narrowing `20-25 minutes` to `25 minutes` passes, and only a figure the
# source never gives at all is reported.
_TEMPERATURE_RE = re.compile(r"(\d{2,3})\s*(?:°|º|\bdegrees\b)", re.IGNORECASE)
_DURATION_RE = re.compile(
    r"(\d+(?:\s*[-–—]\s*\d+)?)\s*(second|minute|hour|day|week)s?\b",  # noqa: RUF001 -- ranges really are typeset with en/em dashes
    re.IGNORECASE,
)


def _temperatures(text: str) -> set:
    return {int(m.group(1)) for m in _TEMPERATURE_RE.finditer(text or "")}


def _durations(text: str) -> set:
    """{(number, unit)} -- a range contributes BOTH of its endpoints, so a
    step that narrows `20-25 minutes` to either number still matches."""
    found = set()
    for match in _DURATION_RE.finditer(text or ""):
        unit = match.group(2).lower()
        for part in re.split(r"[-–—]", match.group(1)):  # noqa: RUF001 -- same three dash characters as the pattern above
            part = part.strip()
            if part.isdigit():
                found.add((int(part), unit))
    return found


def unverified_instruction_facts(steps, source: str) -> list[str]:
    """Times and temperatures a step states that the source never does.

    One human-readable line per finding, for the review screen. Empty list
    when there is nothing to say, which is the normal case and the one
    worth keeping normal."""
    if not source:
        return []
    source_temps = _temperatures(source)
    source_durations = _durations(source)

    findings: list[str] = []
    for position, step in enumerate(normalize_instructions(steps), start=1):
        text = step["text"]
        for temperature in sorted(_temperatures(text) - source_temps):
            findings.append(f"Step {position} says {temperature}°, which the source never states.")
        for number, unit in sorted(_durations(text) - source_durations):
            plural = "" if number == 1 else "s"
            findings.append(f"Step {position} says {number} {unit}{plural}, which the source never states.")
    return findings


# The other half of the label instability the plan named as the trigger
# for reaching for a `RecipeComponent` table: `CRUST` and `Crust` are two
# different strings, so the UI -- which groups both lists by that string
# -- renders two sections where the source printed one.
#
# Case is NOT normalised to a house style, because rule 3 asks the model
# to copy the source's own heading and a recipe that shouts `CRUST` on the
# page is not wrong to shout it here. What is fixed is DISAGREEMENT within
# one recipe: the first spelling seen wins, and every later spelling that
# folds to the same key is rewritten to it.
#
# Ingredients are folded before instructions deliberately. An ingredient's
# component comes from a block of COPIED source lines; an instruction's
# comes from the model unaided. When the two disagree, the copied one is
# the one that saw the page.
def unify_component_case(ingredients: list[dict] | None, steps: list[dict] | None) -> None:
    """Rewrites both lists IN PLACE so labels that differ only by case,
    punctuation or a `For the ` prefix become one label."""
    canonical: dict[str, str] = {}
    for row in list(ingredients or []) + list(steps or []):
        label = row.get("component")
        if not label:
            continue
        key = _component_key(label)
        if key not in canonical:
            canonical[key] = label
        else:
            row["component"] = canonical[key]


def parse_recipe_from_url(db: Session, url: str) -> dict:
    """One URL -> the same shape `parse_recipe_file_content` returns, for
    `finish_recipe_parse` to finish.

    Extracted from `routers/recipes.py`'s import job for the same reason
    `parse_recipe_file_content` was: a second batch path now runs it (the
    bookmarks importer), and a copy of an import pipeline drifts from the
    original -- measured twice already in this project, most recently when
    the batch harness's copy of the block loop scored the pizza at six
    while the app stored five.

    JSON-LD FIRST, deliberately: a page publishing schema.org Recipe data
    is telling us its own quantities, which beats asking a 9B to re-read
    its prose, and it costs no GPU time at all. The model is the fallback,
    not the plan."""
    try:
        html = fetch_html(url)
    except Exception as exc:
        raise RuntimeError(f"Could not fetch that URL: {exc}") from exc

    jsonld_parsed = extract_jsonld_recipe(html)
    page = extract_content_from_html(html, url=url)
    if jsonld_parsed is None and not page.get("text"):
        raise RuntimeError("Could not extract readable content from that URL")

    citation = {
        "source_url": url,
        "source_name": page.get("sitename"),
        # JSON-LD's own author beats trafilatura's page-level byline
        # guess, which can pick up a site editor rather than the cook.
        "source_author": (jsonld_parsed or {}).get("_source_author") or page.get("author"),
    }

    image_path = None
    image_url = (jsonld_parsed or {}).get("_image_url") or page.get("image")
    if image_url:
        fetched = fetch_image_bytes(image_url)
        if fetched:
            raw_image_bytes, image_content_type = fetched
            with contextlib.suppress(ValueError):
                image_path = recipe_image_service.save_image(image_content_type, raw_image_bytes)

    if jsonld_parsed is not None:
        return {
            "raw_output": (
                "(parsed directly from the page's structured schema.org Recipe data -- "
                "Ollama was not used for this import)"
            ),
            "default_source": "import_url_jsonld",
            "citation": citation,
            "image_path": image_path,
            "jsonld_parsed": jsonld_parsed,
            "source_text": None,
        }

    source_text = page["text"]
    return {
        "raw_output": _extract_via_ollama(db, source_text),
        "default_source": "import_url",
        "citation": citation,
        "image_path": image_path,
        "jsonld_parsed": None,
        "source_text": source_text,
    }


def finish_recipe_parse(
    raw_output: str,
    default_source: str,
    citation: dict,
    image_path: str | None,
    jsonld_parsed: dict | None,
    db: Session | None = None,
    source_text: str | None = None,
) -> dict:
    """Shared tail step for every recipe-import path (URL, pasted text,
    single-file upload, and the B13.1 folder-scan batch importer): turns
    either the model's raw text output OR an already-structured JSON-LD
    dict into a final RecipeCreate-shaped dict, with source/citation/
    image_path folded in. Raises RuntimeError -- never returns None --
    when nothing could be extracted, since every caller treats "no
    recipe found in this input" as a reportable per-item failure, not a
    silent skip."""
    if jsonld_parsed is not None:
        parsed = coerce_recipe_fields({k: v for k, v in jsonld_parsed.items() if not k.startswith("_")})
    else:
        parsed = parse_recipe_response(raw_output)
    if parsed is None:
        raise RuntimeError("Could not extract a recipe from that input")

    # Ingredients come from the two-pass path when the raw source text is
    # available -- every text/PDF/photo import, but never JSON-LD, which
    # already carries a clean machine-readable list and needs no model.
    # Both arguments are optional so a caller that has no source text
    # (or no session) keeps exactly the previous behaviour rather than
    # failing; an empty result means pass 1 found nothing and the
    # single-call ingredients stand.
    if jsonld_parsed is not None:
        parsed[INGREDIENT_PROVENANCE_KEY] = _provenance("jsonld")
    elif db is None or not source_text:
        # A photo import has no text layer, so there is nothing a copied
        # line could be checked against. Unverified for a reason that is
        # not a failure, and the review form says so in those words.
        parsed[INGREDIENT_PROVENANCE_KEY] = _provenance(
            "single_call", reason="no_source_text", single_call=len(parsed.get("ingredients") or [])
        )
    else:
        two_pass = extract_ingredients_two_pass(db, source_text)
        single_call_count = len(parsed.get("ingredients") or [])
        # Second half of the completeness gate, and the only place both
        # counts are visible. Two-pass earns the replacement by being at
        # least comparably complete; it does not get to shrink a recipe.
        # `if two_pass:` alone -- truthiness -- let one verified line
        # replace a full ingredient list.
        if two_pass and (single_call_count == 0 or len(two_pass) >= single_call_count * _TWO_PASS_MIN_COVERAGE):
            parsed["ingredients"] = two_pass
            parsed[INGREDIENT_PROVENANCE_KEY] = _provenance(
                "two_pass", verified=len(two_pass), single_call=single_call_count
            )
        else:
            if two_pass:
                print(
                    f"[recipe_import] two-pass returned {len(two_pass)} ingredient(s) against the single call's "
                    f"{single_call_count}; keeping the single call's list.",
                    flush=True,
                )
            parsed[INGREDIENT_PROVENANCE_KEY] = _provenance(
                "single_call",
                reason="fewer_than_single_call" if two_pass else "nothing_verified",
                verified=len(two_pass),
                single_call=single_call_count,
            )

    # Labels that differ only by case are one label -- see
    # unify_component_case. Done after two-pass has settled the
    # ingredients, so the spelling that wins is the one copied from the
    # page rather than whichever list happened to be built first.
    parsed["instructions"] = normalize_instructions(parsed.get("instructions"))
    unify_component_case(parsed.get("ingredients"), parsed["instructions"])

    # The method's numbers, checked against the source -- see
    # unverified_instruction_facts. Never for JSON-LD: those instructions
    # are the publisher's own text, not a model's reading of it.
    parsed[INSTRUCTION_WARNINGS_KEY] = (
        unverified_instruction_facts(parsed.get("instructions"), source_text)
        if source_text and jsonld_parsed is None
        else []
    )

    parsed["source"] = default_source
    for key, value in citation.items():
        if value:
            parsed[key] = value
    if image_path:
        parsed["image_path"] = image_path
    return parsed


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
    ingredients_lines = "\n".join(_format_ingredient_line(ing) for ing in recipe_read.get("ingredients", []))
    # Grouped the way the recipe is written, so a question about "the
    # crust" reaches a model that can see which steps those are.
    instruction_lines: list[str] = []
    current: str | None = None
    for i, step in enumerate(normalize_instructions(recipe_read.get("instructions"))):
        if step["component"] != current:
            current = step["component"]
            if current:
                instruction_lines.append(f"[{current}]")
        instruction_lines.append(f"{i + 1}. {step['text']}")
    instructions_lines = "\n".join(instruction_lines)
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
# Lets the recipe-scoped chat propose an edit -- "take this imported
# recipe and make it gluten-free" -- rather than only answering
# questions. Every turn can still be a plain answer; this adds one
# optional field to the response shape. Same preview-then-confirm
# discipline as recipe import, vision intake and meal-plan generation:
# nothing is ever saved by chat code, only proposed.

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
integer or null, "instructions": array of objects with "text" and "component", "ingredients": array \
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


def get_recipe_modify_prompt(db: Session) -> str:
    """Backlog B16.1 -- same DB-override-with-fallback pattern as
    get_recipe_import_prompt above, for the recipe-scoped chat's "propose
    an edit" instructions. Never passed through `.format()` at any call
    site (it's f-string-concatenated into a chat message alongside the
    system prompt and recipe context instead -- see routers/recipes.py's
    chat_about_recipe), so, unlike the import prompt, there was never a
    brace-escaping footgun here to fix -- this getter exists purely to
    make the prompt GUI-editable, same as every other one in this
    file/router."""
    return ollama_client.get_active_prompt(db, "recipe_modify") or RECIPE_MODIFY_INSTRUCTIONS


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
    """Thin re-export of ai_json_extraction.extract_json_object, kept
    under this name/location since health_service.py, chat_service.py,
    and meal_plan_service.py all already import `_extract_json_object`
    from THIS module (recipe_service) -- changing the import path in all
    three would be pure churn for no behavior change. The real work is in
    ai_json_extraction.extract_json_object: a bare `json.loads` with a
    greedy `re.search` fallback has no defense against a `<think>`
    reasoning trace landing inline, and every recipe import, recipe-chat
    edit, health-metric parse and meal-plan generation funnels through
    here."""
    return extract_json_object(raw_text)


def _safe_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(value) -> int | None:
    f = _safe_float(value)
    return int(f) if f is not None else None
