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
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import GroceryListItem, HouseholdMember, MealPlan, MealPlanEntry, Recipe
from app.schemas.jobs import JobEnqueuedResponse
from app.schemas.meal_plan import (
    DietQualityScoreResponse,
    GroceryListItemCreate,
    GroceryListItemRead,
    GroceryListItemUpdate,
    MealPlanCreate,
    MealPlanEntryConfirmRequest,
    MealPlanEntryRead,
    MealPlanEntryRecipeSummary,
    MealPlanEntryUpdate,
    MealPlanGenerateRequest,
    MealPlanGenerateResponse,
    MealPlanNutritionSummary,
    MealPlanRead,
    MealPlanUpdate,
    MemberDailyTarget,
)
from app.services import (
    allergen_service,
    calendar_export_service,
    cost_service,
    diet_quality_service,
    dri_service,
    google_calendar_service,
    inventory_service,
    job_queue,
    meal_plan_service,
    ollama_client,
    recipe_service,
)

router = APIRouter(prefix="/api/meal-plans", tags=["meal-plans"])


# Backlog B12.1 -- every meal-plan/entry mutation that can change what's
# on the calendar (a new plan, a swapped recipe/servings/meal-type, a
# skip, a whole-plan delete) routes through one of these three tiny
# helpers rather than duplicating the "is sync even on" check and job-
# queue closure shape at each call site. The is_sync_enabled(db) check
# runs on the REQUEST-scoped session (cheap, no network call) so the
# overwhelmingly common case -- sync never configured -- costs one extra
# settings read per mutation, not a job-queue round trip. Mirrors the
# job-queue closure pattern every other job-enqueuing endpoint in this
# app already uses: close over plain ids/values, never the request-scoped
# `db` Session, and open a fresh SessionLocal() inside the closure.
def _enqueue_plan_sync(db: Session, plan_id: int) -> None:
    if not google_calendar_service.is_sync_enabled(db):
        return

    def _run() -> dict:
        db2 = SessionLocal()
        try:
            plan = db2.get(MealPlan, plan_id)
            if plan is not None:
                google_calendar_service.sync_meal_plan(db2, plan)
        finally:
            db2.close()
        return {}

    job_queue.enqueue("google_calendar_sync", f"Sync meal plan {plan_id} to Google Calendar", _run)


def _enqueue_entry_sync(db: Session, entry_id: int) -> None:
    if not google_calendar_service.is_sync_enabled(db):
        return

    def _run() -> dict:
        db2 = SessionLocal()
        try:
            entry = db2.get(MealPlanEntry, entry_id)
            if entry is not None:
                google_calendar_service.sync_entry(db2, entry)
                db2.commit()
        finally:
            db2.close()
        return {}

    job_queue.enqueue("google_calendar_sync", f"Sync meal plan entry {entry_id} to Google Calendar", _run)


def _enqueue_event_cleanup(db: Session, event_ids: list[str]) -> None:
    if not event_ids or not google_calendar_service.is_sync_enabled(db):
        return

    def _run() -> dict:
        db2 = SessionLocal()
        try:
            for event_id in event_ids:
                google_calendar_service.delete_event(db2, event_id)
        finally:
            db2.close()
        return {}

    job_queue.enqueue("google_calendar_sync", f"Remove {len(event_ids)} calendar event(s) for a deleted plan", _run)


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
        is_eating_out=entry.is_eating_out,
        is_confirmed=entry.is_confirmed,
        is_skipped=entry.is_skipped,
        notes=entry.notes,
        # Backlog B5.1 -- learned from B10.1's own documented bug (both
        # create_meal_plan and this same function once omitted
        # is_eating_out here, silently reporting false over the API even
        # though the DB column held the correct value) not to trust
        # "it's on MealPlanEntryBase so it must already round-trip" --
        # this manual constructor needs every field named explicitly.
        leftover_of_entry_id=entry.leftover_of_entry_id,
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
                # Backlog B5.4 -- category was always available on the
                # computed dict (subtract_inventory sets it), just never
                # carried through to the persisted row.
                category=item.get("category"),
                source="auto",
            )
        )
    db.commit()


@router.post("/generate", response_model=JobEnqueuedResponse, status_code=202)
def generate_meal_plan(payload: MealPlanGenerateRequest):
    """AI-assisted draft for a full week -- returns a MealPlanCreate-
    shaped PREVIEW, nothing is persisted here. The user reviews/edits
    (swap a recipe, adjust servings, fill in a slot the model left
    empty) then POSTs the confirmed result to POST /api/meal-plans.

    Backlog B11.1 (2026-08-01): enqueues a background job instead of
    blocking on the Ollama call. This endpoint was already a plain `def`
    (never froze the whole app's event loop the way the `async def`
    import endpoints did), but it still held one browser tab's request
    open for the full generation, lost all state on navigation, and
    didn't share this app's one GPU budget with any other AI feature --
    so it now goes through the same shared queue, per the 2026-08-01
    "everything, unified" scope decision (see PROJECT-PLAN.md)."""
    meal_types = [m.strip().lower() for m in (payload.meal_types or ["dinner"]) if m.strip()]
    if not meal_types:
        meal_types = ["dinner"]
    entry_guidance = [g.model_dump() for g in payload.entry_guidance]

    def _run() -> dict:
        db = SessionLocal()
        try:
            context = meal_plan_service.gather_generation_context(
                db,
                household_size=payload.household_size,
                meal_types=meal_types,
                kitchen_profile_id=payload.kitchen_profile_id,
                entry_guidance=entry_guidance,
                notes=payload.notes,
                prep_day=payload.prep_day,
            )
            prompt = meal_plan_service.build_generation_prompt(context)
            system_prompt = ollama_client.get_active_prompt(db, "main_chef") or ""
            response = ollama_client.chat(
                db, [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
            )
            raw_output = ollama_client.extract_content(response)

            entries = meal_plan_service.parse_meal_plan_response(raw_output)
            if not entries:
                raise RuntimeError("Could not extract a meal plan from the model's response")
            catalog_ids = {r["id"] for r in context["recipe_catalog"]}
            entries = meal_plan_service.validate_entries_against_catalog(entries, catalog_ids)
            entries = meal_plan_service.attach_restriction_warnings(db, entries)

            plan = MealPlanCreate(
                week_start_date=payload.week_start_date,
                household_size_snapshot=context["household_size"],
                kitchen_profile_id=context["kitchen_profile_id"],
                status="draft",
                entries=entries,
            )
            return MealPlanGenerateResponse(plan=plan, raw_model_output=raw_output).model_dump(mode="json")
        finally:
            db.close()

    job_id, created = job_queue.enqueue("meal_plan_generate", "Meal plan generation", _run)
    return JobEnqueuedResponse(job_id=job_id, created=created)


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
                is_eating_out=entry_in.is_eating_out,
                notes=entry_in.notes,
            )
        )
    db.commit()
    db.refresh(plan)

    _persist_grocery_list(db, plan)
    db.refresh(plan)
    _enqueue_plan_sync(db, plan.id)
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
    event_ids = [e.google_event_id for e in plan.entries if e.google_event_id]
    db.delete(plan)
    db.commit()
    _enqueue_event_cleanup(db, event_ids)
    return None


@router.patch("/{plan_id}/entries/{entry_id}", response_model=MealPlanEntryRead)
def update_meal_plan_entry(plan_id: int, entry_id: int, payload: MealPlanEntryUpdate, db: Session = Depends(get_db)):
    """Backlog B5.1: the mechanism for linking (or, by sending
    `leftover_of_entry_id: null`, unlinking) a leftover entry -- see
    MealPlanEntry.leftover_of_entry_id's model docstring. Validated here
    rather than left to the database's bare FK constraint (SQLite doesn't
    enforce those by default in this setup, same caveat already
    documented elsewhere in this codebase): the referenced entry must
    exist, must belong to THIS plan (a leftover link across two
    different weeks' plans would be a nonsensical grocery/inventory
    shortcut), and an entry cannot be marked as its own leftover."""
    entry = db.get(MealPlanEntry, entry_id)
    if entry is None or entry.meal_plan_id != plan_id:
        raise HTTPException(status_code=404, detail="Meal plan entry not found")

    updates = payload.model_dump(exclude_unset=True)
    if "leftover_of_entry_id" in updates and updates["leftover_of_entry_id"] is not None:
        origin_id = updates["leftover_of_entry_id"]
        if origin_id == entry_id:
            raise HTTPException(status_code=400, detail="An entry cannot be marked as leftovers of itself")
        origin = db.get(MealPlanEntry, origin_id)
        if origin is None or origin.meal_plan_id != plan_id:
            raise HTTPException(
                status_code=400, detail="leftover_of_entry_id must reference another entry in this same plan"
            )

    for field, value in updates.items():
        setattr(entry, field, value)
    db.commit()
    db.refresh(entry)
    # Manual edits (swapping a recipe, changing servings) can change what
    # the plan needs to buy, so keep the auto grocery list in sync.
    _persist_grocery_list(db, entry.meal_plan)
    db.refresh(entry)
    _enqueue_entry_sync(db, entry.id)
    return _to_entry_read(entry)


@router.post("/{plan_id}/entries/{entry_id}/confirm", response_model=MealPlanEntryRead)
def confirm_meal_plan_entry(
    plan_id: int,
    entry_id: int,
    payload: MealPlanEntryConfirmRequest = MealPlanEntryConfirmRequest(),
    db: Session = Depends(get_db),
):
    """Marks a meal as actually made and deducts its ingredients (scaled
    to the entry's servings) from inventory -- the same deduction
    primitive used elsewhere (inventory_service.deduct_by_name), applied
    once per ingredient. Best-effort: an ingredient with no inventory
    match is simply skipped rather than failing the whole confirmation.

    Backlog B3.1: re-runs the deterministic allergen check at confirm
    time (not just at generation-preview time -- restrictions or the
    plan itself may have changed in the days between generating a plan
    and actually cooking from it). A hard allergen match 409s with the
    match details instead of confirming, unless the caller explicitly
    sets acknowledge_restriction_conflict=true on a follow-up request --
    see MealPlanEntryConfirmRequest's docstring. Cross-contact warnings
    never block on their own, only surfaced for the frontend to display.

    Backlog B5.1: an entry marked `leftover_of_entry_id` skips BOTH the
    allergen re-check and the inventory deduction below -- the origin
    entry's own confirm already ran both against the same recipe, scaled
    to the combined servings across the whole cook event. Deducting
    again here (or re-flagging an allergen match already surfaced once)
    would double-count inventory usage for a meal that was never
    separately cooked. The entry still gets marked `is_confirmed=True`
    for tracking purposes -- it just doesn't touch inventory."""
    entry = db.get(MealPlanEntry, entry_id)
    if entry is None or entry.meal_plan_id != plan_id:
        raise HTTPException(status_code=404, detail="Meal plan entry not found")
    if entry.is_confirmed:
        raise HTTPException(status_code=400, detail="Entry is already confirmed")
    if entry.is_skipped:
        raise HTTPException(status_code=400, detail="Cannot confirm a skipped entry")

    if entry.recipe_id is not None and entry.leftover_of_entry_id is None:
        recipe = db.get(Recipe, entry.recipe_id)
        if recipe is not None:
            ingredient_names = [i.ingredient_name for i in recipe.ingredients]
            restriction_check = allergen_service.check_household_restrictions(db, ingredient_names)
            if restriction_check.has_conflict and not payload.acknowledge_restriction_conflict:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": (
                            "This recipe contains a restricted allergen. Resend with "
                            "acknowledge_restriction_conflict=true to confirm anyway."
                        ),
                        **restriction_check.to_dict(),
                    },
                )
            base_ingredients = [
                {"ingredient_name": i.ingredient_name, "quantity": i.quantity, "unit": i.unit}
                for i in recipe.ingredients
            ]
            scaled = recipe_service.scale_ingredients(base_ingredients, recipe.default_servings, entry.servings)
            for ing in scaled:
                inventory_service.deduct_by_name(db, ing["ingredient_name"], ing.get("quantity"), ing.get("unit"))

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
    _enqueue_entry_sync(db, entry.id)  # removes this entry's calendar event, if any
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


@router.get("/{plan_id}/grocery-list/cost")
def get_grocery_list_cost(plan_id: int, db: Session = Depends(get_db)):
    """Backlog B6.1 -- projected spend across this plan's still-unpurchased
    grocery-list items, computed live from currently-tracked inventory
    unit_price data (see cost_service's module docstring). Already-
    purchased items are excluded -- they're spent money, not a
    projection of what's left to buy."""
    plan = db.get(MealPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Meal plan not found")
    items = db.query(GroceryListItem).filter_by(meal_plan_id=plan_id).all()
    return cost_service.compute_grocery_list_cost(db, items)


@router.get("/{plan_id}/nutrition-summary", response_model=MealPlanNutritionSummary)
def get_meal_plan_nutrition_summary(plan_id: int, db: Session = Depends(get_db)):
    """Backlog B1.4 -- per-day/week nutrition totals (meal_plan_service.
    compute_nutrition_summary) alongside every household member's
    DRI-derived daily target (dri_service.compute_member_daily_targets),
    so the totals have something concrete to compare against instead of
    just being logged. Member targets are returned even for members
    missing the data to compute one (weight/height/age) -- with
    `missing_fields` naming exactly what's absent -- rather than
    silently omitting that member from the response."""
    plan = db.get(MealPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Meal plan not found")

    summary = meal_plan_service.compute_nutrition_summary(plan)

    member_targets = []
    for member in db.query(HouseholdMember).order_by(HouseholdMember.name).all():
        targets, missing = dri_service.compute_member_daily_targets(db, member)
        member_targets.append(
            MemberDailyTarget(member_id=member.id, name=member.name, daily_targets=targets, missing_fields=missing)
        )

    return MealPlanNutritionSummary(
        days=summary["days"], week_totals=summary["week_totals"], member_targets=member_targets
    )


@router.get("/{plan_id}/diet-quality-score", response_model=DietQualityScoreResponse)
def get_meal_plan_diet_quality_score(plan_id: int, db: Session = Depends(get_db)):
    """Backlog B2.2 -- an HEI-2020-inspired diet-quality estimate
    (diet_quality_service.compute_diet_quality_score) over this plan's
    non-skipped, recipe-assigned entries. See that module's docstring
    for exactly which of the 13 real HEI-2020 components this can and
    cannot score with this app's current data, and why the adequacy
    components are an approximation rather than a true USDA Food
    Patterns Equivalents lookup -- `methodology` on the response repeats
    the short version so the caveat travels with the number."""
    plan = db.get(MealPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Meal plan not found")
    return diet_quality_service.compute_diet_quality_score(plan)


@router.get("/{plan_id}/calendar.ics")
def get_meal_plan_calendar(plan_id: int, db: Session = Depends(get_db)):
    """Backlog B9.5 -- an iCalendar feed of this plan's non-skipped
    entries (calendar_export_service.build_ics), regenerated fresh on
    every request so a calendar app that re-polls this URL later sees
    the plan's current state, not a stale one-time snapshot.

    Known limitation, stated here rather than silently: if the optional
    session-cookie auth gate (B10.2) is enabled, this path sits behind
    `auth_gate` like every other `/api/*` route -- but a calendar app
    subscribing to a plain .ics URL has no way to complete a login flow
    or hold a session cookie. With the auth gate on, this endpoint is
    only usable for a manual download from an already-authenticated
    browser tab, not as a live-subscribed feed in a calendar app. Fixing
    that (e.g. a per-plan share token) is reasonable future work, not
    attempted in this pass."""
    plan = db.get(MealPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Meal plan not found")
    ics_text = calendar_export_service.build_ics(plan)
    return Response(
        content=ics_text,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="chef-meal-plan-{plan.week_start_date}.ics"'},
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
    data = payload.model_dump()
    # Backlog B5.4 -- a manually-typed item has no recipe/inventory
    # context to draw a category from, so apply the same best-effort
    # keyword guess auto-generated lines get, rather than leaving every
    # hand-added item permanently uncategorized. The user can always
    # correct it afterward (PATCH already supports category).
    if not data.get("category"):
        data["category"] = meal_plan_service.guess_grocery_category(data["ingredient_name"])
    item = GroceryListItem(meal_plan_id=plan_id, source="manual", **data)
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
