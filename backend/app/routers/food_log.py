"""The food log: what was actually eaten (backlog B17.1).

Route ordering matters -- the static /summary path is declared before the
dynamic /{entry_id} routes so FastAPI's matching order does not swallow
it, the same reason health.py declares /trends first.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import FoodLogEntry, HouseholdMember, InventoryItem, Recipe
from app.models.base import utc_now
from app.schemas.food_log import (
    FoodLogDaySummary,
    FoodLogEntryCreate,
    FoodLogEntryRead,
    FoodLogEntryUpdate,
)
from app.services import food_log_service

router = APIRouter(prefix="/api/food-log", tags=["food-log"])

# How far back the list and summary reach by default. A fortnight covers
# "how did last week go" -- the question the log exists to answer -- and
# the adherence view (B17.4) works a week at a time. A household wanting
# more passes ?days=; there is no cap, because this table grows at a few
# rows a day and the honest answer to "show me everything" is everything.
DEFAULT_WINDOW_DAYS = 14


def _window_start(days: int) -> datetime:
    return utc_now() - timedelta(days=days)


@router.get("/summary", response_model=list[FoodLogDaySummary])
def food_log_summary(
    days: int = DEFAULT_WINDOW_DAYS,
    # Minutes EAST of UTC, i.e. `-new Date().getTimezoneOffset()` in the
    # browser. Required for the day grouping to be right: timestamps are
    # stored in UTC, and "which day did I eat that" is a question about
    # the eater's wall clock. Without it a 7pm dinner in Texas lands on
    # the following day. Bounded to real-world offsets so a malformed
    # value cannot shift the whole history.
    tz_offset_minutes: int = Query(0, ge=-840, le=840),
    member_id: int | None = None,
    db: Session = Depends(get_db),
):
    """Per-day totals, newest first.

    Each day reports `unquantified_entries` alongside its totals, and that
    field is not optional decoration -- a day whose two logged meals both
    carry no nutrition has totals of zero, and a caller showing only the
    totals would render the least-known day as the best one."""
    query = db.query(FoodLogEntry).filter(FoodLogEntry.eaten_at >= _window_start(days))
    if member_id is not None:
        query = query.filter(FoodLogEntry.member_id == member_id)
    return food_log_service.summarize_days(query.all(), tz_offset_minutes=tz_offset_minutes)


@router.get("", response_model=list[FoodLogEntryRead])
def list_food_log(
    days: int = DEFAULT_WINDOW_DAYS,
    member_id: int | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(FoodLogEntry).filter(FoodLogEntry.eaten_at >= _window_start(days))
    if member_id is not None:
        query = query.filter(FoodLogEntry.member_id == member_id)
    return query.order_by(FoodLogEntry.eaten_at.desc()).all()


@router.post("", response_model=FoodLogEntryRead, status_code=201)
def create_food_log_entry(payload: FoodLogEntryCreate, db: Session = Depends(get_db)):
    """Log a meal by hand.

    Nutrition is never accepted from the caller. Either the entry names a
    recipe -- in which case the server reads that recipe's per-serving
    figures and scales them itself, through the one function allowed to do
    that multiplication -- or the entry carries no nutrition and says so
    with a NULL provenance. A client cannot assert "computed" over numbers
    nothing computed.
    """
    if payload.member_id is not None and db.get(HouseholdMember, payload.member_id) is None:
        raise HTTPException(status_code=404, detail="Household member not found")
    if payload.inventory_item_id is not None and db.get(InventoryItem, payload.inventory_item_id) is None:
        raise HTTPException(status_code=404, detail="Inventory item not found")

    eaten_at = payload.eaten_at
    if eaten_at is not None and eaten_at.tzinfo is None:
        # A client that sent a bare local timestamp gets it read as UTC,
        # matching UtcDateTime's own rule rather than inventing a second
        # convention here.
        eaten_at = eaten_at.replace(tzinfo=timezone.utc)

    if payload.recipe_id is not None:
        recipe = db.get(Recipe, payload.recipe_id)
        if recipe is None:
            raise HTTPException(status_code=404, detail="Recipe not found")
        entry = food_log_service.log_from_recipe(
            db,
            recipe=recipe,
            servings=payload.servings,
            meal_type=payload.meal_type,
            source=payload.source,
            eaten_at=eaten_at,
            member_id=payload.member_id,
            notes=payload.notes,
        )
        # A description typed alongside a recipe wins over the recipe
        # title: "Sunday roast, no potatoes" is more use in a history than
        # the recipe's own name, and the person typing it knew that.
        if (payload.description or "").strip():
            entry.description = payload.description.strip()
    else:
        entry = FoodLogEntry(
            member_id=payload.member_id,
            eaten_at=eaten_at or utc_now(),
            meal_type=payload.meal_type,
            source=payload.source,
            description=payload.description.strip(),
            inventory_item_id=payload.inventory_item_id,
            servings=payload.servings,
            # No recipe means no nutrition, and NULL provenance says
            # exactly that. B17.3's text/photo capture is what will fill
            # these in; guessing them from a description would be the
            # invention this app's provenance labelling exists to stop.
            nutrition={},
            nutrition_provenance=None,
            notes=payload.notes,
        )
        db.add(entry)

    db.commit()
    db.refresh(entry)
    return entry


@router.patch("/{entry_id}", response_model=FoodLogEntryRead)
def update_food_log_entry(entry_id: int, payload: FoodLogEntryUpdate, db: Session = Depends(get_db)):
    """Correct a logged meal.

    Changing `servings` re-scales the nutrition from the linked recipe
    rather than scaling the stored total, because the stored total is a
    rounded product and re-scaling a rounded number compounds the error
    every time somebody edits. An entry with no recipe has nothing to
    re-derive from, so its (absent) nutrition stays absent.
    """
    entry = db.get(FoodLogEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Food log entry not found")

    data = payload.model_dump(exclude_unset=True)
    if "member_id" in data and data["member_id"] is not None:
        if db.get(HouseholdMember, data["member_id"]) is None:
            raise HTTPException(status_code=404, detail="Household member not found")
    if "servings" in data and data["servings"] is not None and data["servings"] <= 0:
        raise HTTPException(status_code=400, detail="servings must be greater than zero")
    if "description" in data and not (data["description"] or "").strip():
        raise HTTPException(status_code=400, detail="description cannot be blank")

    for field, value in data.items():
        if field == "eaten_at" and value is not None and value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        setattr(entry, field, value)

    if "servings" in data and entry.recipe_id is not None:
        recipe = db.get(Recipe, entry.recipe_id)
        if recipe is not None:
            entry.nutrition = food_log_service.scale_nutrition(recipe.nutrition, entry.servings)
            entry.nutrition_provenance = recipe.nutrition_provenance if recipe.nutrition else None

    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/{entry_id}", status_code=204)
def delete_food_log_entry(entry_id: int, db: Session = Depends(get_db)):
    """Remove a logged meal -- a mis-tap, or a plan slot confirmed by
    accident. Deleting the row also frees its `meal_plan_entry_id`, so a
    slot whose automatic log was deleted can be logged again by hand."""
    entry = db.get(FoodLogEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Food log entry not found")
    db.delete(entry)
    db.commit()
    return None
