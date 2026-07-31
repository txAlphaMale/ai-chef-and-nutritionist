"""Recipe CRUD, servings scaling, ratings/staple flag, and AI-assisted
import from pasted text, a PDF, or a photo.

As in inventory.py, static paths (/import) are declared before the
dynamic /{recipe_id} routes so FastAPI's route-matching order doesn't
swallow them.
"""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Recipe, RecipeIngredient
from app.schemas.recipe import (
    RecipeChatRequest,
    RecipeChatResponse,
    RecipeCreate,
    RecipeImportResponse,
    RecipeIngredientRead,
    RecipeRatingUpdate,
    RecipeRead,
    RecipeUpdate,
)
from app.services import ollama_client, recipe_image_service, recipe_service

router = APIRouter(prefix="/api/recipes", tags=["recipes"])


def _to_read(recipe: Recipe, servings_shown: int | None = None) -> RecipeRead:
    servings_shown = servings_shown or recipe.default_servings
    ingredients = [
        {
            "ingredient_name": ing.ingredient_name,
            "quantity": ing.quantity,
            "unit": ing.unit,
            "prep_note": ing.prep_note,
        }
        for ing in recipe.ingredients
    ]
    scaled = recipe_service.scale_ingredients(ingredients, recipe.default_servings, servings_shown)
    return RecipeRead(
        id=recipe.id,
        title=recipe.title,
        description=recipe.description,
        default_servings=recipe.default_servings,
        prep_time_minutes=recipe.prep_time_minutes,
        cook_time_minutes=recipe.cook_time_minutes,
        instructions=recipe.instructions or [],
        nutrition=recipe.nutrition or {},
        is_staple=recipe.is_staple,
        image_path=recipe.image_path,
        source_url=recipe.source_url,
        source_name=recipe.source_name,
        source_author=recipe.source_author,
        tips=recipe.tips or [],
        rating=recipe.rating,
        source=recipe.source,
        ingredients=[RecipeIngredientRead(**ing) for ing in scaled]
        if servings_shown != recipe.default_servings
        else [RecipeIngredientRead.model_validate(ing) for ing in recipe.ingredients],
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
            )
        )


@router.get("", response_model=list[RecipeRead])
def list_recipes(
    db: Session = Depends(get_db),
    is_staple: bool | None = None,
    tag: str | None = None,
    search: str | None = None,
):
    query = db.query(Recipe)
    if is_staple is not None:
        query = query.filter(Recipe.is_staple == is_staple)
    if search:
        query = query.filter(Recipe.title.ilike(f"%{search}%"))
    recipes = query.order_by(Recipe.title).all()
    if tag:
        recipes = [r for r in recipes if tag.lower() in {t.name for t in r.tags}]
    return [_to_read(r) for r in recipes]


@router.post("/import", response_model=RecipeImportResponse)
async def import_recipe(
    db: Session = Depends(get_db),
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
    preview-session cleanup machinery for a low-stakes, low-volume case."""
    citation: dict = {}
    image_path: str | None = None
    if url:
        try:
            page = recipe_service.extract_url_content(url)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=502, detail=f"Could not fetch or parse that URL: {exc}") from exc
        if not page.get("text"):
            raise HTTPException(status_code=422, detail="Could not extract readable content from that URL")
        citation = {"source_url": url, "source_name": page.get("sitename"), "source_author": page.get("author")}
        if page.get("image"):
            fetched = recipe_service.fetch_image_bytes(page["image"])
            if fetched:
                raw_image_bytes, image_content_type = fetched
                try:
                    image_path = recipe_image_service.save_image(image_content_type, raw_image_bytes)
                except ValueError:
                    pass  # unsupported content type -- skip, not fatal to the import
        raw_output = await _run_text_extraction(db, page["text"])
        default_source = "import_url"
    elif text:
        raw_output = await _run_text_extraction(db, text)
        default_source = "import_text"
    elif file is not None:
        raw_bytes = await file.read()
        content_type = file.content_type or ""
        if content_type.startswith("image/"):
            try:
                response = ollama_client.describe_image(
                    db, raw_bytes, recipe_service.RECIPE_IMPORT_PROMPT.format(content="[see attached photo]")
                )
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"Ollama vision request failed: {exc}") from exc
            raw_output = response.get("message", {}).get("content", "") if isinstance(response, dict) else str(response)
            default_source = "import_image"
            try:
                image_path = recipe_image_service.save_image(content_type, raw_bytes)
            except ValueError:
                pass  # unsupported content type -- skip, not fatal to the import
        elif content_type == "application/pdf" or file.filename.lower().endswith(".pdf"):
            pdf_text = recipe_service.extract_pdf_text(raw_bytes)
            raw_output = await _run_text_extraction(db, pdf_text)
            default_source = "import_file"
        else:
            try:
                text_content = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(status_code=400, detail="Unsupported file type for recipe import")
            raw_output = await _run_text_extraction(db, text_content)
            default_source = "import_file"
    else:
        raise HTTPException(status_code=400, detail="Provide one of `text`, `file`, or `url`")

    parsed = recipe_service.parse_recipe_response(raw_output)
    if parsed is None:
        raise HTTPException(status_code=422, detail="Could not extract a recipe from that input")
    parsed["source"] = default_source
    for key, value in citation.items():
        if value:
            parsed[key] = value
    if image_path:
        parsed["image_path"] = image_path
    return RecipeImportResponse(recipe=RecipeCreate(**parsed), raw_model_output=raw_output)


async def _run_text_extraction(db: Session, content: str) -> str:
    prompt = recipe_service.RECIPE_IMPORT_PROMPT.format(content=content[:8000])
    try:
        response = ollama_client.chat(db, [{"role": "user", "content": prompt}])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Ollama request failed: {exc}") from exc
    return response.get("message", {}).get("content", "") if isinstance(response, dict) else str(response)


@router.get("/{recipe_id}", response_model=RecipeRead)
def get_recipe(recipe_id: int, servings: int | None = None, db: Session = Depends(get_db)):
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return _to_read(recipe, servings_shown=servings)


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
    return [_to_read(v) for v in recipe.variants]


@router.post("", response_model=RecipeRead, status_code=201)
def create_recipe(payload: RecipeCreate, db: Session = Depends(get_db)):
    data = payload.model_dump(exclude={"ingredients", "tags"})
    recipe = Recipe(**data)
    db.add(recipe)
    db.flush()
    _apply_ingredients(db, recipe, payload.ingredients)
    recipe.tags = recipe_service.resolve_tags(db, payload.tags)
    db.commit()
    db.refresh(recipe)
    return _to_read(recipe)


@router.patch("/{recipe_id}", response_model=RecipeRead)
def update_recipe(recipe_id: int, payload: RecipeUpdate, db: Session = Depends(get_db)):
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    updates = payload.model_dump(exclude_unset=True, exclude={"ingredients", "tags"})
    for field, value in updates.items():
        setattr(recipe, field, value)
    if payload.ingredients is not None:
        _apply_ingredients(db, recipe, payload.ingredients)
    if payload.tags is not None:
        recipe.tags = recipe_service.resolve_tags(db, payload.tags)
    db.commit()
    db.refresh(recipe)
    return _to_read(recipe)


@router.post("/{recipe_id}/chat", response_model=RecipeChatResponse)
def chat_about_recipe(recipe_id: int, payload: RecipeChatRequest, db: Session = Depends(get_db)):
    """Ephemeral, recipe-scoped chat -- for things like "I'm out of buttermilk,
    what can I use instead?" while actually cooking. Deliberately NOT
    persisted to chat_messages (that's the Phase 7 persistent chat system,
    a separate concern); the client resends `history` each turn. The
    recipe's current ingredients/instructions/tips are injected as context
    so suggestions are grounded in the actual recipe, not a generic answer."""
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")

    servings = payload.servings or recipe.default_servings
    read_view = _to_read(recipe, servings_shown=servings)
    system_prompt = ollama_client.get_active_prompt(db, "main_chef") or ""
    context = recipe_service.build_recipe_chat_context(read_view.model_dump())

    # RECIPE_MODIFY_INSTRUCTIONS (added 2026-07-31) upgrades this chat
    # from read-only Q&A to also being able to propose an edit -- e.g.
    # "make this gluten-free" -- which the frontend shows as a reviewable
    # RecipeForm, exactly like a recipe import preview, before anything
    # is saved. See recipe_service.py for the full writeup.
    messages = [
        {
            "role": "system",
            "content": f"{system_prompt}\n\n{context}\n\n{recipe_service.RECIPE_MODIFY_INSTRUCTIONS}",
        }
    ]
    for m in payload.history:
        messages.append({"role": m.role, "content": m.content})
    messages.append({"role": "user", "content": payload.message})

    try:
        response = ollama_client.chat(db, messages)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Ollama request failed: {exc}") from exc
    raw_output = response.get("message", {}).get("content", "") if isinstance(response, dict) else str(response)
    parsed = recipe_service.parse_recipe_chat_response(raw_output)
    proposed = RecipeCreate(**parsed["proposed_recipe"]) if parsed["proposed_recipe"] else None
    return RecipeChatResponse(reply=parsed["reply"], proposed_recipe=proposed, variant_label=parsed["variant_label"])


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
    return _to_read(recipe)


@router.delete("/{recipe_id}/image", response_model=RecipeRead)
def delete_recipe_image(recipe_id: int, db: Session = Depends(get_db)):
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    recipe_image_service.delete_image(recipe.image_path)
    recipe.image_path = None
    db.commit()
    db.refresh(recipe)
    return _to_read(recipe)


@router.get("/{recipe_id}/image")
def get_recipe_image(recipe_id: int, db: Session = Depends(get_db)):
    recipe = db.get(Recipe, recipe_id)
    if recipe is None or not recipe.image_path or not os.path.exists(recipe.image_path):
        raise HTTPException(status_code=404, detail="No image for this recipe")
    return FileResponse(recipe.image_path, media_type=recipe_image_service.guess_content_type(recipe.image_path))


@router.post("/{recipe_id}/rating", response_model=RecipeRead)
def rate_recipe(recipe_id: int, payload: RecipeRatingUpdate, db: Session = Depends(get_db)):
    recipe = db.get(Recipe, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    recipe.rating = payload.rating
    db.commit()
    db.refresh(recipe)
    return _to_read(recipe)


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
