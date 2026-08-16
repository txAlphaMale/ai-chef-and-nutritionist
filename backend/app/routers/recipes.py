"""Recipe CRUD, servings scaling, ratings/staple flag, and AI-assisted
import from pasted text, a PDF, or a photo.

As in inventory.py, static paths (/import) are declared before the
dynamic /{recipe_id} routes so FastAPI's route-matching order doesn't
swallow them.
"""

from __future__ import annotations

import json
import os
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session, selectinload

from app.database import SessionLocal, get_db
from app.models import Recipe, RecipeIngredient
from app.schemas.jobs import JobEnqueuedResponse
from app.schemas.recipe import (
    BookmarkFolder,
    BookmarkFoldersResponse,
    BulkDeleteRequest,
    BulkDeleteResult,
    DerivedTagRead,
    RecipeChatRequest,
    RecipeChatResponse,
    RecipeCreate,
    RecipeFolderImportConfirmRequest,
    RecipeFolderImportResponse,
    RecipeImportResponse,
    RecipeIngredientRead,
    RecipeRatingUpdate,
    RecipeRead,
    RecipeUpdate,
)
from app.services import (
    allergen_service,
    bookmark_import_service,
    cost_service,
    food_data_service,
    job_queue,
    ollama_client,
    recipe_folder_import_service,
    recipe_image_service,
    recipe_service,
    settings_service,
    smart_tag_service,
)

router = APIRouter(prefix="/api/recipes", tags=["recipes"])


def _to_read(
    recipe: Recipe,
    db: Session,
    servings_shown: int | None = None,
    unit_system: str = "original",
    restrictions: allergen_service.HouseholdRestrictions | None = None,
) -> RecipeRead:
    """Audit P1-9: pass `restrictions` when rendering MORE THAN ONE
    recipe in a request. Without it this re-reads household preferences
    per recipe, which on the Recipes list page meant one query per row on
    the page users open most."""
    servings_shown = servings_shown or recipe.default_servings
    ingredients = [
        {
            "id": ing.id,
            "ingredient_name": ing.ingredient_name,
            "quantity": ing.quantity,
            "unit": ing.unit,
            "prep_note": ing.prep_note,
            # Absent here until 2026-08-07, which is why a saved recipe's
            # ingredients rendered as one flat list while its INSTRUCTIONS
            # grouped correctly: the steps ride on a JSON column and are
            # returned whole, but every ingredient field this dict does
            # not name is dropped on the way out. The component was in the
            # database the whole time. Twin of the bug in
            # _apply_ingredients above -- one never wrote it, this never
            # read it, and each was found separately.
            "component": ing.component,
            "resolution_source": ing.resolution_source,
            "resolved_food_name": ing.resolved_food_name,
            "fdc_id": ing.fdc_id,
            "off_barcode": ing.off_barcode,
            "nutrition_per_100g": ing.nutrition_per_100g,
            "density_g_per_ml": ing.density_g_per_ml,
        }
        for ing in recipe.ingredients
    ]
    scaled = recipe_service.scale_ingredients(ingredients, recipe.default_servings, servings_shown)
    # Backlog B10.5 -- always routed through this same dict-based pipeline
    # now, even when servings_shown == default_servings (previously that
    # case validated straight from the ORM objects instead, which would
    # have skipped unit-system conversion entirely for the common
    # "viewing at default servings" case). `id` is threaded through
    # explicitly (see the dict above) since RecipeIngredientRead.id used
    # to come from the ORM object directly in that branch.
    display_ingredients = recipe_service.apply_display_unit_system(scaled, unit_system)
    restriction_check = allergen_service.check_household_restrictions(
        db, [ing.ingredient_name for ing in recipe.ingredients], restrictions
    )
    # Derived on every read rather than stored: editing an ingredient
    # changes what is true about the recipe, and a stored tag would not
    # notice. See smart_tag_service.
    derived_tags = smart_tag_service.derive_tags(
        [ing.ingredient_name for ing in recipe.ingredients],
        recipe.nutrition,
        recipe.nutrition_provenance,
    )
    return RecipeRead(
        id=recipe.id,
        title=recipe.title,
        description=recipe.description,
        default_servings=recipe.default_servings,
        prep_time_minutes=recipe.prep_time_minutes,
        cook_time_minutes=recipe.cook_time_minutes,
        instructions=recipe.instructions or [],
        nutrition=recipe.nutrition or {},
        nutrition_provenance=recipe.nutrition_provenance,
        derived_tags=[DerivedTagRead(tag=t.tag, basis=t.basis) for t in derived_tags],
        restriction_warnings=[vars(m) for m in restriction_check.matches],
        cross_contact_warnings=[vars(m) for m in restriction_check.cross_contact_matches],
        is_staple=recipe.is_staple,
        image_path=recipe.image_path,
        source_url=recipe.source_url,
        source_name=recipe.source_name,
        source_author=recipe.source_author,
        tips=recipe.tips or [],
        rating=recipe.rating,
        source=recipe.source,
        ingredients=[RecipeIngredientRead(**ing) for ing in display_ingredients],
        tags=[t.name for t in recipe.tags],
        servings_shown=servings_shown,
        parent_recipe_id=recipe.parent_recipe_id,
        variant_label=recipe.variant_label,
        parent_recipe_title=recipe.parent_recipe.title if recipe.parent_recipe else None,
        variant_count=len(recipe.variants),
        created_at=recipe.created_at,
        updated_at=recipe.updated_at,
    )


def _apply_ingredients(db: Session, recipe: Recipe, ingredients: list) -> None:
    recipe.ingredients.clear()
    for ing in ingredients:
        recipe.ingredients.append(
            RecipeIngredient(
                ingredient_name=ing.ingredient_name,
                quantity=ing.quantity,
                unit=ing.unit,
                prep_note=ing.prep_note,
                # RecipeIngredientBase has accepted `component` since the
                # column landed, but this function never wrote it -- so
                # every create and update through the API silently dropped
                # it, including the import preview->confirm path, which is
                # where a multi-part recipe actually arrives. Normalized
                # through the same sentinel rule as the extraction path so
                # "main" never reaches the database from either direction.
                component=recipe_service.normalize_component(ing.component),
            )
        )


@router.get("", response_model=list[RecipeRead])
def list_recipes(
    db: Session = Depends(get_db),
    is_staple: bool | None = None,
    tag: str | None = None,
    search: str | None = None,
):
    # Capstone review 2026-08-16: `_to_read` walks `recipe.ingredients` and
    # (via `recipe_service`/`smart_tag_service`) `recipe.tags` for every row.
    # Both are lazy relationships, so an un-hinted list query issued 2 extra
    # SELECTs per recipe -- invisible at 20 recipes, a real stall once a
    # bookmarks bulk-import puts hundreds in the catalog. Same class of
    # defect as audit item P1-9 (which fixed the per-row household-
    # preferences read), just on the relationships rather than a settings
    # lookup.
    query = db.query(Recipe).options(
        selectinload(Recipe.ingredients),
        selectinload(Recipe.tags),
    )
    if is_staple is not None:
        query = query.filter(Recipe.is_staple == is_staple)
    if search:
        query = query.filter(Recipe.title.ilike(f"%{search}%"))
    recipes = query.order_by(Recipe.title).all()
    if tag:
        recipes = [r for r in recipes if tag.lower() in {t.name for t in r.tags}]
    restrictions = allergen_service.load_household_restrictions(db)
    return [_to_read(r, db, restrictions=restrictions) for r in recipes]


@router.post("/import", response_model=JobEnqueuedResponse, status_code=202)
async def import_recipe(
    file: UploadFile | None = None,
    text: str | None = Form(None),
    url: str | None = Form(None),
):
    """Accepts `text`, an uploaded `file` (image, PDF, or plain text), OR a
    `url` -- exactly one -- extracts a structured recipe preview via
    Ollama, and returns it WITHOUT saving so the frontend can let the
    user review/edit before POSTing the confirmed result to
    POST /api/recipes. Citation info (source_url/source_name/
    source_author) is captured where available rather than discarded,
    per the project's copyright-respect requirement -- see
    recipe_service.RECIPE_IMPORT_PROMPT and extract_url_content().

    Also best-effort auto-captures a dish photo where the source plausibly
    has one: the uploaded photo itself for an image import, or a fetched
    og:image for a URL import (PDF/text imports have no plausible photo
    source, so this is skipped for those). The image is written to disk
    immediately (recipe_image_service.save_image) even though the recipe
    row itself doesn't exist until the user confirms via POST
    /api/recipes -- its storage path just rides along in the preview's
    `image_path` field, same as any other parsed field. A cancelled
    import leaves an orphaned file on disk; consistent with this app's
    existing local-single-user pragmatism elsewhere (e.g. inventory/
    knowledge-file deletes are best-effort too) rather than adding
    preview-session cleanup machinery for a low-stakes, low-volume case.

    The entire body below -- URL fetch, image fetch, PDF extraction, and
    the Ollama call -- runs inside a background job rather than in this
    `async def` handler. Not only for the Ollama call:
    `recipe_service.extract_url_content` (trafilatura.fetch_url) and
    `fetch_image_bytes` (a synchronous httpx.Client) are blocking network
    I/O too, and would freeze the event loop for their duration. Only the
    cheap "was anything provided
    at all" check and the async UploadFile.read() (which needs the
    request's own context) still happen before enqueueing; every other
    error that used to get its own HTTP status code (a bad URL, an
    unreadable file, a recipe the model couldn't extract) now surfaces
    uniformly as job status "error" with a message, since that
    distinction only mattered for synchronous HTTP semantics this
    endpoint no longer has."""
    if not (url or text or file is not None):
        raise HTTPException(status_code=400, detail="Provide one of `text`, `file`, or `url`")

    raw_bytes: bytes | None = None
    content_type = ""
    filename = ""
    if file is not None:
        raw_bytes = await file.read()
        content_type = file.content_type or ""
        filename = (file.filename or "").lower()

    def _run() -> dict:
        db = SessionLocal()
        try:
            citation: dict = {}
            image_path: str | None = None
            jsonld_parsed: dict | None = None
            # The text the model was shown, for two-pass ingredient
            # verification. Stays None for a photo import (no text layer
            # to check a copied line against) and for JSON-LD.
            source_text: str | None = None
            if url:
                # The whole URL pipeline -- fetch, JSON-LD first, citation,
                # dish photo, model fallback -- lives in recipe_service now,
                # because the bookmarks importer runs it too and a second
                # copy of an import pipeline drifts from the first.
                url_result = recipe_service.parse_recipe_from_url(db, url)
                raw_output = url_result["raw_output"]
                default_source = url_result["default_source"]
                citation = url_result["citation"]
                image_path = url_result["image_path"]
                jsonld_parsed = url_result["jsonld_parsed"]
                source_text = url_result["source_text"]
                # No model runs on the structured path, so it finishes in
                # about a second. Comparing it against imports that spend
                # two model calls is what made the progress badge read
                # "108s of ~1s typical".
                job_queue.set_estimate_key(
                    "recipe_import:structured" if jsonld_parsed is not None else "recipe_import:model"
                )
            elif text:
                source_text = text
                raw_output = _run_text_extraction(db, source_text)
                default_source = "import_text"
            else:
                # JSON/image/PDF/text/HTML branching lives in
                # recipe_service.parse_recipe_file_content so the
                # folder-scan batch importer
                # (recipe_folder_import_service.py) parses a file exactly
                # the way a single browser upload does.
                file_result = recipe_service.parse_recipe_file_content(db, raw_bytes, filename, content_type)
                raw_output = file_result["raw_output"]
                default_source = file_result["default_source"]
                citation = file_result["citation"]
                image_path = file_result["image_path"]
                jsonld_parsed = file_result["jsonld_parsed"]
                source_text = file_result["source_text"]

            # Backlog B13.1: this tail (raw model output or a structured
            # JSON-LD dict -> a final RecipeCreate-shaped dict, with
            # source/citation/image_path folded in) is likewise shared
            # with the folder-scan batch importer now -- see
            # recipe_service.finish_recipe_parse's docstring.
            parsed = recipe_service.finish_recipe_parse(
                raw_output,
                default_source,
                citation,
                image_path,
                jsonld_parsed,
                db=db,
                source_text=source_text,
            )

            # Backlog B3.1: check the parsed-but-not-yet-saved ingredients
            # against the household's current restrictions BEFORE the user
            # ever confirms this import -- a conflict should be visible in
            # the review step, not discovered later on the recipe's own page.
            restriction_check = allergen_service.check_household_restrictions(
                db, [ing.get("ingredient_name", "") for ing in parsed.get("ingredients", [])]
            )
            # Popped rather than left for RecipeCreate to ignore: this is
            # about the import, not about the recipe, and nothing should
            # be able to carry it into a saved row by accident.
            provenance = parsed.pop(recipe_service.INGREDIENT_PROVENANCE_KEY, None)
            instruction_warnings = parsed.pop(recipe_service.INSTRUCTION_WARNINGS_KEY, [])
            return RecipeImportResponse(
                recipe=RecipeCreate(**parsed),
                raw_model_output=raw_output,
                ingredient_provenance=provenance,
                instruction_warnings=instruction_warnings,
                restriction_warnings=[vars(m) for m in restriction_check.matches],
                cross_contact_warnings=[vars(m) for m in restriction_check.cross_contact_matches],
            ).model_dump()
        finally:
            db.close()

    # Bucketed by how much work the import will actually be, not by the
    # fact that it is an import -- see job_queue.enqueue's comment. A file
    # or pasted text always runs the model; a URL only does when the page
    # publishes no schema.org data, which _run discovers and corrects.
    job_id, created = job_queue.enqueue(
        "recipe_import",
        "Recipe import",
        _run,
        estimate_key="recipe_import:url" if url else "recipe_import:model",
    )
    return JobEnqueuedResponse(job_id=job_id, created=created)


def _run_text_extraction(db: Session, content: str) -> str:
    """Plain sync (no longer `async def`, backlog B11.1) -- always called
    from inside a job body on the background worker thread now, never
    awaited directly from a request handler.

    Content is capped by ollama_client.content_char_budget, which scales
    with the configured context window rather than a flat character
    count. This path needs the headroom most: the JSON-LD-first import
    order (B9.3) means this AI-prompt fallback only runs for sources with
    NO structured recipe data -- a photo, a PDF, pasted text, or a URL
    publishing no schema.org markup -- which are the messiest sources,
    where a scraped blog page's preamble prose runs long."""
    prompt_template = recipe_service.get_recipe_import_prompt(db)
    budget = ollama_client.content_char_budget(
        db,
        prompt_overhead_chars=len(prompt_template),
        response_reserve_tokens=recipe_service.RECIPE_RESPONSE_TOKENS,
    )
    prompt = prompt_template.replace("{content}", content[:budget])
    return ollama_client.chat_json(
        db,
        [{"role": "user", "content": prompt}],
        schema=recipe_service.RECIPE_SCHEMA,
        response_tokens=recipe_service.RECIPE_RESPONSE_TOKENS,
    )


@router.post("/import-folder/scan", response_model=JobEnqueuedResponse, status_code=202)
def scan_import_folder(db: Session = Depends(get_db)):
    """Backlog B13.1: scans the folder at
    `recipe_import_folder_path` (Settings > Integrations -- a Docker
    volume the household points at their OneDrive-synced folder, or any
    folder) and returns a PREVIEW of every recipe it could parse from the
    files found there. Nothing is written to the recipes table here --
    see RecipeFolderImportResponse's docstring for the review-then-
    confirm flow. Enqueued as a background job like every other Ollama-
    consuming batch operation (B11.1): a real recipe folder means many
    sequential model calls, one per file, easily minutes."""
    folder_path = settings_service.get_setting(db, "recipe_import_folder_path")
    if not folder_path:
        raise HTTPException(
            status_code=400,
            detail="Set a recipe import folder path in Settings (Integrations tab) first.",
        )

    def _run() -> dict:
        job_db = SessionLocal()
        try:
            result = recipe_folder_import_service.scan_and_parse(job_db, folder_path)
            return RecipeFolderImportResponse(**result).model_dump()
        finally:
            job_db.close()

    job_id, created = job_queue.enqueue(
        "recipe_folder_import", "Recipe folder import", _run, dedup_key="recipe_folder_import"
    )
    return JobEnqueuedResponse(job_id=job_id, created=created)


@router.post("/import-bookmarks/folders", response_model=BookmarkFoldersResponse)
async def list_bookmark_folders(file: UploadFile = File(...)):
    """The folders in an exported bookmarks file, with counts.

    Separate from the scan, and synchronous, because it costs nothing --
    no network, no model -- and picking a folder before spending GPU time
    on forty URLs is the whole point. Nothing here reads the household's
    data, so it takes no DB session."""
    raw = await file.read()
    try:
        html = raw.decode("utf-8", errors="replace")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="That file could not be read as text") from exc

    bookmarks = bookmark_import_service.parse_bookmarks(html)
    if not bookmarks:
        raise HTTPException(
            status_code=400,
            detail="No http(s) bookmarks found in that file. Export from your browser as HTML and try again.",
        )
    return BookmarkFoldersResponse(
        folders=[BookmarkFolder(**f) for f in bookmark_import_service.folder_summary(bookmarks)],
        total=len(bookmarks),
    )


@router.post("/import-bookmarks/scan", response_model=JobEnqueuedResponse, status_code=202)
async def scan_bookmarks(
    file: UploadFile = File(...),
    folder_path: str | None = Form(None),
    retry_failed: bool = Form(False),
    _db: Session = Depends(get_db),
):
    """Imports every bookmark in the chosen folder (and its subfolders),
    as a PREVIEW -- nothing is written until the household confirms
    through the existing /import-folder/confirm endpoint, which already
    takes a plain list of RecipeCreate.

    A background job for the same reason a single URL import is one: this
    is a fetch and up to two model calls per bookmark, forty times
    over."""
    raw = await file.read()
    html = raw.decode("utf-8", errors="replace")
    bookmarks = bookmark_import_service.select(
        bookmark_import_service.parse_bookmarks(html),
        (folder_path or "").strip() or None,
    )
    if not bookmarks:
        raise HTTPException(status_code=400, detail="No bookmarks to import in that folder")

    def _run() -> dict:
        db = SessionLocal()
        try:
            return bookmark_import_service.scan_and_parse(db, bookmarks, retry_failed=retry_failed)
        finally:
            db.close()

    # Almost every recipe site publishes schema.org data, so most of these
    # cost no model time at all -- but "almost" is not "all", and one
    # unmarked page in forty makes the whole run a model run.
    job_id, created = job_queue.enqueue(
        "recipe_bookmark_import", "Bookmark import", _run, estimate_key="recipe_import:model"
    )
    return JobEnqueuedResponse(job_id=job_id, created=created)


@router.post("/import-folder/confirm", response_model=list[RecipeRead])
def confirm_folder_import(payload: RecipeFolderImportConfirmRequest, db: Session = Depends(get_db)):
    """Bulk-creates recipes from a (user-reviewed/edited) folder-scan
    preview. Reuses the same per-recipe create logic as POST /api/recipes
    (Recipe row + ingredients + tag resolution + nutrition_provenance
    stamping), just looped -- there's no bulk-INSERT recipe primitive in
    this app, and a folder import is a low-frequency, one-off-per-scan
    operation, not worth adding one for."""
    created = []
    for recipe_in in payload.recipes:
        data = recipe_in.model_dump(exclude={"ingredients", "tags"})
        recipe = Recipe(**data)
        if recipe.nutrition:
            recipe.nutrition_provenance = "ai_estimated"
        db.add(recipe)
        db.flush()
        _apply_ingredients(db, recipe, recipe_in.ingredients)
        recipe.tags = recipe_service.resolve_tags(db, recipe_in.tags)
        created.append(recipe)
    db.commit()
    for recipe in created:
        db.refresh(recipe)
    restrictions = allergen_service.load_household_restrictions(db)
    return [_to_read(r, db, restrictions=restrictions) for r in created]


@router.get("/{recipe_id}", response_model=RecipeRead)
def get_recipe(
    recipe_id: int,
    servings: int | None = None,
    unit_system: str = "original",
    db: Session = Depends(get_db),
):
    """Backlog B10.5 -- `unit_system` ("original" | "metric" | "imperial"
    | "weight") is a per-view display transform only, applied after
    servings scaling; it never touches the persisted recipe. Weight mode
    can leave some ingredients unconverted (RecipeIngredientRead.
    display_unavailable=True) when no cached density is available for
    that specific ingredient -- run nutrition resolution first
    (POST /{recipe_id}/compute-nutrition or /resolve-nutrition) to
    populate what density data is available."""
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return _to_read(recipe, db, servings_shown=servings, unit_system=unit_system)


@router.get("/{recipe_id}/variants", response_model=list[RecipeRead])
def list_recipe_variants(recipe_id: int, db: Session = Depends(get_db)):
    """Children of this recipe (recipe.variants, via parent_recipe_id) --
    used by the detail page to list e.g. "Gluten-Free" / "Double Batch"
    siblings created from chat-proposed edits. Not the reverse direction
    (this recipe's own parent, if any) -- that's just parent_recipe_title
    on the recipe's own RecipeRead, no extra round-trip needed."""
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    restrictions = allergen_service.load_household_restrictions(db)
    return [_to_read(v, db, restrictions=restrictions) for v in recipe.variants]


@router.post("", response_model=RecipeRead, status_code=201)
def create_recipe(payload: RecipeCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"ingredients", "tags"})
    recipe = Recipe(**data)
    # Backlog B1.2: a nutrition dict arriving through the normal create
    # endpoint is always either an LLM's guess (import/generation/chat
    # already went through recipe_service.coerce_recipe_fields before the
    # frontend ever POSTs here) or a human typing numbers into RecipeForm
    # -- neither is "computed" (summed from a real USDA/OFF match), so
    # both land in the same honestly-unverified bucket. Only
    # POST /{id}/compute-nutrition (below) can ever set "computed"/"partial".
    if recipe.nutrition:
        recipe.nutrition_provenance = "ai_estimated"
    db.add(recipe)
    db.flush()
    _apply_ingredients(db, recipe, payload.ingredients)
    recipe.tags = recipe_service.resolve_tags(db, payload.tags)
    db.commit()
    db.refresh(recipe)
    return _to_read(recipe, db)


@router.patch("/{recipe_id}", response_model=RecipeRead)
def update_recipe(recipe_id: int, payload: RecipeUpdate, db: Session = Depends(get_db)):
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    updates = payload.model_dump(exclude_unset=True, exclude={"ingredients", "tags"})
    # Backlog B1.2: only demote a "computed"/"partial" recipe back to
    # "ai_estimated" if the incoming nutrition dict actually differs from
    # what's stored -- RecipeForm always resends the nutrition fields it
    # loaded, so an unrelated edit (title, rating, tags...) would
    # otherwise silently overwrite a real computed result's provenance
    # label on every save even though the numbers themselves didn't change.
    if "nutrition" in updates and updates["nutrition"] != (recipe.nutrition or {}):
        recipe.nutrition_provenance = "ai_estimated" if updates["nutrition"] else None
    for field, value in updates.items():
        setattr(recipe, field, value)
    if payload.ingredients is not None:
        _apply_ingredients(db, recipe, payload.ingredients)
    if payload.tags is not None:
        recipe.tags = recipe_service.resolve_tags(db, payload.tags)
    db.commit()
    db.refresh(recipe)
    return _to_read(recipe, db)


@router.post("/{recipe_id}/chat", response_model=JobEnqueuedResponse, status_code=202)
def chat_about_recipe(recipe_id: int, payload: RecipeChatRequest, db: Session = Depends(get_db)):
    """Ephemeral, recipe-scoped chat -- for things like "I'm out of buttermilk,
    what can I use instead?" while actually cooking. Deliberately NOT
    persisted to chat_messages (that's the Phase 7 persistent chat system,
    a separate concern); the client resends `history` each turn. The
    recipe's current ingredients/instructions/tips are injected as context
    so suggestions are grounded in the actual recipe, not a generic answer.

    Enqueues a background job rather than blocking on the Ollama call.
    A plain `def` here would run in FastAPI's threadpool and so would not
    freeze the event loop, but it would still hold a browser tab's
    request open for the whole generation, lose all state on navigation,
    and compete for this app's one GPU budget outside the queue that
    exists to serialize exactly that. The 404 check below
    still runs synchronously against the request's own `db` -- cheap,
    fast, no reason to make a bad recipe_id wait in line -- but the job
    body re-fetches the recipe fresh through its OWN session, since a
    SQLAlchemy ORM object is bound to the session that loaded it and
    isn't safe to hand across threads."""
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    servings = payload.servings or recipe.default_servings
    history = [{"role": m.role, "content": m.content} for m in payload.history]
    message = payload.message
    recipe_title = recipe.title

    def _run() -> dict:
        job_db = SessionLocal()
        try:
            job_recipe = job_db.get(Recipe, recipe_id)
            if job_recipe is None:
                raise RuntimeError("Recipe not found")
            read_view = _to_read(job_recipe, job_db, servings_shown=servings)
            system_prompt = ollama_client.get_active_prompt(job_db, "main_chef") or ""
            context = recipe_service.build_recipe_chat_context(read_view.model_dump())

            # RECIPE_MODIFY_INSTRUCTIONS upgrades this chat from
            # read-only Q&A to also being able to propose an edit -- e.g. "make this gluten-free" -- which the frontend
            # shows as a reviewable RecipeForm, exactly like a recipe
            # import preview, before anything is saved. See
            # recipe_service.py for the full writeup.
            messages = [
                {
                    "role": "system",
                    "content": f"{system_prompt}\n\n{context}\n\n{recipe_service.get_recipe_modify_prompt(job_db)}",
                }
            ]
            messages.extend(history)
            messages.append({"role": "user", "content": message})

            # Constrained decoding (see app/schemas/ai_extraction.py):
            # this response carries BOTH a conversational reply and an
            # optional whole-recipe replacement, which is precisely the
            # shape a free-form model is worst at emitting cleanly.
            raw_output = ollama_client.chat_json(
                job_db,
                messages,
                schema=recipe_service.RECIPE_EDIT_SCHEMA,
                response_tokens=recipe_service.RECIPE_RESPONSE_TOKENS,
                # A chat reply should read naturally; only the structure is
                # constrained, so a little sampling warmth is appropriate
                # here where it is not for pure extraction.
                extra_options={"temperature": 0.4},
            )
            parsed = recipe_service.parse_recipe_chat_response(raw_output)
            proposed = RecipeCreate(**parsed["proposed_recipe"]) if parsed["proposed_recipe"] else None
            return RecipeChatResponse(
                reply=parsed["reply"], proposed_recipe=proposed, variant_label=parsed["variant_label"]
            ).model_dump()
        finally:
            job_db.close()

    job_id, created = job_queue.enqueue("recipe_chat", f"Recipe chat: {recipe_title}", _run)
    return JobEnqueuedResponse(job_id=job_id, created=created)


@router.post("/{recipe_id}/image", response_model=RecipeRead)
async def upload_recipe_image(recipe_id: int, file: UploadFile, db: Session = Depends(get_db)):
    """Manual "upload a photo of the dish" -- used from the recipe form
    for both add and edit. Replaces any existing image (old file deleted
    after the new one is safely written and the DB row updated, so a
    failure partway through never leaves the recipe pointing at a
    half-written file)."""
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    raw_bytes = await file.read()
    try:
        new_path = recipe_image_service.save_image(file.content_type, raw_bytes)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    old_path = recipe.image_path
    recipe.image_path = new_path
    db.commit()
    db.refresh(recipe)
    if old_path and old_path != new_path:
        recipe_image_service.delete_image(old_path)
    return _to_read(recipe, db)


@router.delete("/{recipe_id}/image", response_model=RecipeRead)
def delete_recipe_image(recipe_id: int, db: Session = Depends(get_db)):
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    recipe_image_service.delete_image(recipe.image_path)
    recipe.image_path = None
    db.commit()
    db.refresh(recipe)
    return _to_read(recipe, db)


@router.get("/{recipe_id}/image")
def get_recipe_image(recipe_id: int, db: Session = Depends(get_db)):
    recipe = db.get(Recipe, recipe_id)
    if recipe is None or not recipe.image_path or not os.path.exists(recipe.image_path):
        raise HTTPException(status_code=404, detail="No image for this recipe")
    return FileResponse(recipe.image_path, media_type=recipe_image_service.guess_content_type(recipe.image_path))


def _recipe_image_url(request: Request, recipe: Recipe) -> str | None:
    if not recipe.image_path or not os.path.exists(recipe.image_path):
        return None
    return f"{str(request.base_url).rstrip('/')}/api/recipes/{recipe.id}/image"


@router.get("/{recipe_id}/export/jsonld")
def export_recipe_jsonld(recipe_id: int, request: Request, db: Session = Depends(get_db)):
    """Backlog B9.2: a single recipe as a schema.org Recipe JSON-LD
    document -- see recipe_service.recipe_to_jsonld's docstring for why
    this format specifically (it's what the URL/file importer already
    reads on the way back in). Returned as a downloadable file rather
    than a plain JSON API response so a browser click produces a real
    file, consistent with the .ics export's own Content-Disposition
    pattern (routers/meal_plan.py's get_meal_plan_calendar)."""
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    doc = recipe_service.recipe_to_jsonld(recipe, image_url=_recipe_image_url(request, recipe))
    slug = re.sub(r"[^a-z0-9]+", "-", recipe.title.lower()).strip("-") or "recipe"
    return Response(
        content=json.dumps(doc, indent=2),
        media_type="application/ld+json",
        headers={"Content-Disposition": f'attachment; filename="{slug}.json"'},
    )


@router.get("/export/jsonld")
def export_all_recipes_jsonld(request: Request, db: Session = Depends(get_db)):
    """Every recipe in the database as one schema.org JSON-LD document
    (an @graph array of Recipe nodes) -- the bulk counterpart to the
    single-recipe export above, and part of backlog B9.2's "recipe
    export in a portable format" goal. A two-segment path
    ('/export/jsonld'), so it never collides with the single-segment
    'GET /{recipe_id}' route above regardless of declaration order --
    FastAPI's path matching requires the same number of segments, and
    there's no existing 'GET /{recipe_id}/export' route in this router
    to shadow it either."""
    recipes = db.query(Recipe).options(selectinload(Recipe.ingredients), selectinload(Recipe.tags)).all()
    doc = {
        "@context": "https://schema.org",
        "@graph": [recipe_service.recipe_to_jsonld(r, image_url=_recipe_image_url(request, r)) for r in recipes],
    }
    return Response(
        content=json.dumps(doc, indent=2),
        media_type="application/ld+json",
        headers={"Content-Disposition": 'attachment; filename="chef-recipes-export.json"'},
    )


@router.post("/{recipe_id}/resolve-nutrition", response_model=RecipeRead)
def resolve_recipe_nutrition(recipe_id: int, force: bool = False, db: Session = Depends(get_db)):
    """Backlog B1.1: resolves every ingredient on this recipe against
    USDA FoodData Central / Open Food Facts and caches the result on each
    RecipeIngredient row. Explicit and on-demand (not triggered
    automatically by create/update) so recipe creation stays fast and
    doesn't depend on two external APIs being reachable. A previously
    "unresolved" ingredient is always retried (not sticky); `force=true`
    additionally re-resolves ingredients that already have a REAL cached
    match, e.g. after configuring a USDA key you want to prefer over an
    existing Open Food Facts match. Does NOT yet touch
    `Recipe.nutrition` itself or mark it computed-vs-estimated -- that
    summation-with-provenance step is B1.2, a separate pass."""
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    for ingredient in recipe.ingredients:
        food_data_service.resolve_and_cache_ingredient(db, ingredient, force=force)
    db.commit()
    db.refresh(recipe)
    return _to_read(recipe, db)


@router.post("/{recipe_id}/compute-nutrition", response_model=RecipeRead)
def compute_recipe_nutrition_endpoint(recipe_id: int, force: bool = False, db: Session = Depends(get_db)):
    """Backlog B1.2: the one-click "just fix my nutrition" action. Any
    ingredient never resolved before (resolution_source is None) is
    resolved first -- so this works standalone without a prior call to
    /resolve-nutrition -- but an ingredient already marked "unresolved"
    is deliberately NOT retried here unless `force=True`, matching
    resolve_and_cache_ingredient's own network-call discipline (retrying
    every unresolved ingredient's network lookup on every nutrition
    computation would be surprising for an endpoint whose name doesn't
    say "resolve"). `force=True` re-resolves everything, same as
    /resolve-nutrition's own force flag.

    Then sums via food_data_service.compute_recipe_nutrition() and
    persists both Recipe.nutrition and Recipe.nutrition_provenance --
    UNLESS the result is "ai_estimated", in which case only the
    provenance label is written (so an existing AI guess is preserved,
    just correctly labeled as unverified, rather than being blanked out
    because resolution didn't find anything to compute from)."""
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    for ingredient in recipe.ingredients:
        if force or ingredient.resolution_source is None:
            food_data_service.resolve_and_cache_ingredient(db, ingredient, force=force)
    db.flush()
    nutrition, provenance = food_data_service.compute_recipe_nutrition(recipe)
    if provenance != "ai_estimated":
        recipe.nutrition = nutrition
    recipe.nutrition_provenance = provenance
    db.commit()
    db.refresh(recipe)
    return _to_read(recipe, db)


@router.get("/{recipe_id}/cost")
def get_recipe_cost(recipe_id: int, db: Session = Depends(get_db)):
    """Backlog B6.1 -- cost per recipe and cost per serving, computed live
    from currently-tracked inventory unit_price data (see cost_service's
    module docstring for why this is never persisted the way nutrition
    is). Deliberately not a RecipeRead field: this changes every time
    inventory prices change, not just when the recipe itself is edited."""
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return cost_service.compute_recipe_cost(db, recipe)


@router.post("/{recipe_id}/rating", response_model=RecipeRead)
def rate_recipe(recipe_id: int, payload: RecipeRatingUpdate, db: Session = Depends(get_db)):
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    recipe.rating = payload.rating
    db.commit()
    db.refresh(recipe)
    return _to_read(recipe, db)


@router.post("/bulk-delete", response_model=BulkDeleteResult)
def bulk_delete_recipes(payload: BulkDeleteRequest, db: Session = Depends(get_db)):
    """Delete several recipes in one transaction.

    Added because clearing an import batch meant opening thirty recipe
    pages and pressing Delete on each. A bulk import needs a bulk undo --
    the household reviews forty rows at a time, and the first thing they
    want after a bad batch is all of it gone.

    POST rather than DELETE-with-a-body: a request body on DELETE is
    legal but poorly supported by intermediaries, and every other
    multi-item action in this router is already a POST.

    Missing ids are reported, not raised. A list built from a page the
    household was looking at can easily name a recipe deleted in another
    tab, and failing the whole call over one stale id would throw away
    twenty-nine legitimate deletions.

    **These URLs become importable again.** Deleting a recipe removes its
    source_url, which is what bookmark_import_service.already_imported_urls
    compares against -- so a deleted recipe reappears in the next scan of
    the same bookmarks file. That is the intended behaviour here (this
    endpoint exists to undo a batch and try again) and is NOT a way to
    permanently reject a URL."""
    requested = list(dict.fromkeys(payload.ids))
    recipes = db.query(Recipe).filter(Recipe.id.in_(requested)).all() if requested else []
    found = {recipe.id for recipe in recipes}

    # Collected before the rows go, and unlinked only after the commit
    # succeeds -- an image deleted ahead of a rolled-back transaction
    # leaves a recipe pointing at a file that is gone.
    image_paths = [recipe.image_path for recipe in recipes if recipe.image_path]
    for recipe in recipes:
        db.delete(recipe)
    db.commit()
    for path in image_paths:
        recipe_image_service.delete_image(path)

    return BulkDeleteResult(deleted=len(found), missing=sorted(set(requested) - found))


@router.delete("/{recipe_id}", status_code=204)
def delete_recipe(recipe_id: int, db: Session = Depends(get_db)):
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    image_path = recipe.image_path
    db.delete(recipe)
    db.commit()
    recipe_image_service.delete_image(image_path)
    return None
