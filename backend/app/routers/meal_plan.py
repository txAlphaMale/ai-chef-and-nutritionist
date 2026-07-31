"""Weekly meal-plan CRUD, AI-assisted generation (preview-then-confirm,
same pattern as recipe import in routers/recipes.py), per-entry
confirm/skip (confirming deducts the recipe's ingredients from
inventory, scaled to the entry's servings), and the derived grocery
list (planned ingredients minus what's already on hand).

Route ordering matters -- the static /generate path is declared before
the dynamic /{plan_id} routes so FastAPI's route-matching order doesn't
swallow it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import GroceryListItem, MealPlan, MealPlanEntry, Recipe
from app.schemas.meal_plan import (
    GroceryListItemCreate,
    GroceryListItemRead,
    GroceryListItemUpdate,
    MealPlanCreate,
    MealPlanEntryRead,
    MealPlanEntryRecipeSummary,
    MealPlanEntryUpdate,
    MealPlanGenerateRequest,
    MealPlanGenerateResponse,
    MealPlanRead,
    MealPlanUpdate,
)
from app.services import inventory_service, meal_plan_service, ollama_client, recipe_service

router = APIRouter(prefix="/api/meal-plans", tags=["meal-plans"])


def _to_entry_read(entry: MealPlanEntry) -> MealPlanEntryRead:
    recipe_summary = None
    if entry.recipe is not None:
        recipe_summary = MealPlanEntryRecipeSummary(
            id=entry.recipe.id,
            title=entry.recipe.title,
            default_servings=entry.recipe.default_servings,
            is_staple=entry.recipe.is_staple,
            tags=[t.name for t in entry.recipe.tags],
        )
    return MealPlanEntryRead(
        id=entry.id,
        meal_plan_id=entry.meal_plan_id,
        day_of_week=entry.day_of_week,
        meal_type=entry.meal_type,
        recipe_id=entry.recipe_id,
        recipe=recipe_summary,
        servings=entry.servings,
        requested_tags=entry.requested_tags or [],
        is_indulgence=entry.is_indulgence,
        is_confirmed=entry.is_confirmed,
        is_skipped=entry.is_skipped,
        notes=entry.notes,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


def _to_plan_read(plan: MealPlan) -> MealPlanRead:
    ordered = sorted(plan.entries, key=lambda e: (e.day_of_week, e.meal_type))
    return MealPlanRead(
        id=plan.id,
        week_start_date=plan.week_start_date,
        household_size_snapshot=plan.household_size_snapshot,
        kitchen_profile_id=plan.kitchen_profile_id,
        status=plan.status,
        entries=[_to_entry_read(e) for e in ordered],
        created_at=plan.created_at,
        updated_at=plan.updated_at,
    )


def _persist_grocery_list(db: Session, plan: MealPlan) -> None:
    """Replaces this plan's auto-generated grocery items with a fresh
    computation; manually-added items (source="manual") are left alone."""
    db.query(GroceryListItem).filter_by(meal_plan_id=plan.id, source="auto").delete()
    for item in meal_plan_service.compute_grocery_list(db, plan):
        db.add(
            GroceryListItem(
                meal_plan_id=plan.id,
                ingredient_name=item["ingredient_name"],
                quantity=item["quantity"],
                unit=item["unit"],
                source="auto",
            )
        )
    db.commit()


@router.post("/generate", response_model=MealPlanGenerateResponse)
def generate_meal_plan(payload: MealPlanGenerateRequest, db: Session = Depends(get_db)):
    """AI-assisted draft for a full week -- returns a MealPlanCreate-
    shaped PREVIEW, nothing is persisted here. The user reviews/edits
    (swap a recipe, adjust servings, fill in a slot the model left
    empty) then POSTs the confirmed result to POST /api/meal-plans."""
    meal_types = [m.strip().lower() for m in (payload.meal_types or ["dinner"]) if m.strip()]
    if not meal_types:
        meal_types = ["dinner"]

    context = meal_plan_service.gather_generation_context(
        db,
        household_size=payload.household_size,
        meal_types=meal_types,
        kitchen_profile_id=payload.kitchen_profile_id,
        entry_guidance=[g.model_dump() for g in payload.entry_guidance],
        notes=payload.notes,
    )
    prompt = meal_plan_service.build_generation_prompt(context)
    system_prompt = ollama_client.get_active_prompt(db, "main_chef") or ""
    try:
        response = ollama_client.chat(
            db, [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Ollama request failed: {exc}") from exc
    raw_output = response.get("message", {}).get("content", "") if isinstance(response, dict) else str(response)

    entries = meal_plan_service.parse_meal_plan_response(raw_output)
    if not entries:
        raise HTTPException(status_code=422, detail="Could not extract a meal plan from the model's response")
    catalog_ids = {r["id"] for r in context["recipe_catalog"]}
    entries = meal_plan_service.validate_entries_against_catalog(entries, catalog_ids)

    plan = MealPlanCreate(
        week_start_date=payload.week_start_date,
        household_size_snapshot=context["household_size"],
        kitchen_profile_id=context["kitchen_profile_id"],
        status="draft",
        entries=entries,
    )
    return MealPlanGenerateResponse(plan=plan, raw_model_output=raw_output)


@router.get("", response_model=list[MealPlanRead])
def list_meal_plans(status: str | None = None, db: Session = Depends(get_db)):
    query = db.query(MealPlan)
    if status:
        query = query.filter(MealPlan.status == status)
    plans = query.order_by(MealPlan.week_start_date.desc()).all()
    return [_to_plan_read(p) for p in plans]


@router.post("", response_model=MealPlanRead, status_code=201)
def create_meal_plan(payload: MealPlanCreate, db: Session = Depends(get_db)):
    """Persists a plan -- typically the (possibly user-edited) result of
    POST /generate, but manual plans work too. Any entry with a
    new_recipe and no recipe_id gets that recipe created first (source=
    "ai_generated") so the entry can reference it. The grocery list is
    computed and persisted immediately after."""
    plan = MealPlan(
        week_start_date=payload.week_start_date,
        household_size_snapshot=payload.household_size_snapshot,
        kitchen_profile_id=payload.kitchen_profile_id,
        status=payload.status,
    )
    db.add(plan)
    db.flush()

    for entry_in in payload.entries:
        recipe_id = entry_in.recipe_id
        if recipe_id is None and entry_in.new_recipe is not None:
            new_recipe = recipe_service.create_recipe_from_parsed(
                db, entry_in.new_recipe.model_dump(), source="ai_generated"
            )
            recipe_id = new_recipe.id
        plan.entries.append(
            MealPlanEntry(
                day_of_week=entry_in.day_of_week,
                meal_type=entry_in.meal_type,
                recipe_id=recipe_id,
                servings=entry_in.servings,
                requested_tags=entry_in.requested_tags,
                is_indulgence=entry_in.is_indulgence,
                notes=entry_in.notes,
            )
        )
    db.commit()
    db.refresh(plan)

    _persist_grocery_list(db, plan)
    db.refresh(plan)
    return _to_plan_read(plan)


@router.get("/{plan_id}", response_model=MealPlanRead)
def get_meal_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(MealPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Meal plan not found")
    return _to_plan_read(plan)


@router.patch("/{plan_id}", response_model=MealPlanRead)
def update_meal_plan(plan_id: int, payload: MealPlanUpdate, db: Session = Depends(get_db)):
    plan = db.get(MealPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Meal plan not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(plan, field, value)
    db.commit()
    db.refresh(plan)
    return _to_plan_read(plan)


@router.delete("/{plan_id}", status_code=204)
def delete_meal_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(MealPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Meal plan not found")
    db.delete(plan)
    db.commit()
    return None


@router.patch("/{plan_id}/entries/{entry_id}", response_model=MealPlanEntryRead)
def update_meal_plan_entry(plan_id: int, entry_id: int, payload: MealPlanEntryUpdate, db: Session = Depends(get_db)):
    entry = db.get(MealPlanEntry, entry_id)
    if entry is None or entry.meal_plan_id != plan_id:
        raise HTTPException(status_code=404, detail="Meal plan entry not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(entry, field, value)
    db.commit()
    db.refresh(entry)
    # Manual edits (swapping a recipe, changing servings) can change what
    # the plan needs to buy, so keep the auto grocery list in sync.
    _persist_grocery_list(db, entry.meal_plan)
    db.refresh(entry)
    return _to_entry_read(entry)


@router.post("/{plan_id}/entries/{entry_id}/confirm", response_model=MealPlanEntryRead)
def confirm_meal_plan_entry(plan_id: int, entry_id: int, db: Session = Depends(get_db)):
    """Marks a meal as actually made and deducts its ingredients (scaled
    to the entry's servings) from inventory -- the same deduction
    primitive used elsewhere (inventory_service.deduct_by_name), applied
    once per ingredient. Best-effort: an ingredient with no inventory
    match is simply skipped rather than failing the whole confirmation."""
    entry = db.get(MealPlanEntry, entry_id)
    if entry is None or entry.meal_plan_id != plan_id:
        raise HTTPException(status_code=404, detail="Meal plan entry not found")
    if entry.is_confirmed:
        raise HTTPException(status_code=400, detail="Entry is already confirmed")
    if entry.is_skipped:
        raise HTTPException(status_code=400, detail="Cannot confirm a skipped entry")

    if entry.recipe_id is not None:
        recipe = db.get(Recipe, entry.recipe_id)
        if recipe is not None:
            base_ingredients = [
                {"ingredient_name": i.ingredient_name, "quantity": i.quantity, "unit": i.unit}
                for i in recipe.ingredients
            ]
            scaled = recipe_service.scale_ingredients(base_ingredients, recipe.default_servings, entry.servings)
            for ing in scaled:
                inventory_service.deduct_by_name(db, ing["ingredient_name"], ing.get("quantity"))

    entry.is_confirmed = True
    db.commit()
    db.refresh(entry)
    return _to_entry_read(entry)


@router.post("/{plan_id}/entries/{entry_id}/skip", response_model=MealPlanEntryRead)
def skip_meal_plan_entry(plan_id: int, entry_id: int, db: Session = Depends(get_db)):
    entry = db.get(MealPlanEntry, entry_id)
    if entry is None or entry.meal_plan_id != plan_id:
        raise HTTPException(status_code=404, detail="Meal plan entry not found")
    if entry.is_confirmed:
        raise HTTPException(status_code=400, detail="Cannot skip an already-confirmed entry")
    entry.is_skipped = True
    db.commit()
    db.refresh(entry)
    _persist_grocery_list(db, entry.meal_plan)
    db.refresh(entry)
    return _to_entry_read(entry)


@router.get("/{plan_id}/grocery-list", response_model=list[GroceryListItemRead])
def get_grocery_list(plan_id: int, db: Session = Depends(get_db)):
    plan = db.get(MealPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Meal plan not found")
    return (
        db.query(GroceryListItem)
        .filter_by(meal_plan_id=plan_id)
        .order_by(GroceryListItem.is_purchased, GroceryListItem.ingredient_name)
        .all()
    )


@router.post("/{plan_id}/grocery-list/regenerate", response_model=list[GroceryListItemRead])
def regenerate_grocery_list(plan_id: int, db: Session = Depends(get_db)):
    """Recomputes the auto portion of the grocery list -- useful after
    inventory changes (e.g. a shopping trip already covered part of it)
    without losing manually-added items."""
    plan = db.get(MealPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Meal plan not found")
    _persist_grocery_list(db, plan)
    db.refresh(plan)
    return (
        db.query(GroceryListItem)
        .filter_by(meal_plan_id=plan_id)
        .order_by(GroceryListItem.is_purchased, GroceryListItem.ingredient_name)
        .all()
    )


@router.post("/{plan_id}/grocery-list", response_model=GroceryListItemRead, status_code=201)
def add_grocery_list_item(plan_id: int, payload: GroceryListItemCreate, db: Session = Depends(get_db)):
    plan = db.get(MealPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Meal plan not found")
    item = GroceryListItem(meal_plan_id=plan_id, source="manual", **payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/{plan_id}/grocery-list/{item_id}", response_model=GroceryListItemRead)
def update_grocery_list_item(plan_id: int, item_id: int, payload: GroceryListItemUpdate, db: Session = Depends(get_db)):
    item = db.get(GroceryListItem, item_id)
    if item is None or item.meal_plan_id != plan_id:
        raise HTTPException(status_code=404, detail="Grocery list item not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{plan_id}/grocery-list/{item_id}", status_code=204)
def delete_grocery_list_item(plan_id: int, item_id: int, db: Session = Depends(get_db)):
    item = db.get(GroceryListItem, item_id)
    if item is None or item.meal_plan_id != plan_id:
        raise HTTPException(status_code=404, detail="Grocery list item not found")
    db.delete(item)
    db.commit()
    return None
