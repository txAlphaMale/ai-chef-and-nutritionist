"""Recipe CRUD, servings scaling, ratings/staple flag, and AI-assisted
import from pasted text, a PDF, or a photo.

As in inventory.py, static paths (/import) are declared before the
dynamic /{recipe_id} routes so FastAPI's route-matching order doesn't
swallow them.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Recipe, RecipeIngredient
from app.schemas.recipe import (
    RecipeCreate,
    RecipeImportResponse,
    RecipeIngredientRead,
    RecipeRatingUpdate,
    RecipeRead,
    RecipeUpdate,
)
from app.services import ollama_client, recipe_service

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
        rating=recipe.rating,
        source=recipe.source,
        ingredients=[RecipeIngredientRead(**ing) for ing in scaled]
        if servings_shown != recipe.default_servings
        else [RecipeIngredientRead.model_validate(ing) for ing in recipe.ingredients],
        tags=[t.name for t in recipe.tags],
        servings_shown=servings_shown,
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
):
    """Accepts EITHER a `text` form field OR an uploaded file (image, PDF,
    or plain text), extracts a structured recipe preview via Ollama, and
    returns it WITHOUT saving -- the frontend lets the user review/edit,
    then POSTs the confirmed result to POST /api/recipes."""
    if text:
        content_text = text
        raw_output = await _run_text_extraction(db, content_text)
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
        elif content_type == "application/pdf" or file.filename.lower().endswith(".pdf"):
            pdf_text = recipe_service.extract_pdf_text(raw_bytes)
            raw_output = await _run_text_extraction(db, pdf_text)
        else:
            try:
                text_content = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(status_code=400, detail="Unsupported file type for recipe import")
            raw_output = await _run_text_extraction(db, text_content)
    else:
        raise HTTPException(status_code=400, detail="Provide either `text` or `file`")

    parsed = recipe_service.parse_recipe_response(raw_output)
    if parsed is None:
        raise HTTPException(status_code=422, detail="Could not extract a recipe from that input")
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
    db.delete(recipe)
    db.commit()
    return None
