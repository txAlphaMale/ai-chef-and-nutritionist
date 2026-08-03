"""Pydantic request/response models for the meal-plan API: AI-assisted
weekly generation (preview-then-confirm, same pattern as recipe import),
manual plan/entry CRUD, and the derived grocery list."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.allergen import RestrictionMatchRead
from app.schemas.recipe import RecipeIngredientBase


class EntryGuidance(BaseModel):
    """Optional per-slot steering before generation, e.g. "Saturday
    lunch needs to be portable for a picnic" or "Wednesday dinner
    should be quick"."""

    day_of_week: int = Field(ge=0, le=6)
    meal_type: str = "dinner"
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None


class MealPlanGenerateRequest(BaseModel):
    week_start_date: date
    household_size: int | None = None
    # Which meal slots to plan per day -- defaults to dinner-only, the
    # core "weekly meal plan" ask; add "breakfast"/"lunch"/"snack" to
    # plan those too.
    meal_types: list[str] = Field(default_factory=lambda: ["dinner"])
    kitchen_profile_id: int | None = None
    entry_guidance: list[EntryGuidance] = Field(default_factory=list)
    notes: str | None = None  # free-text steering, e.g. "going camping this weekend"
    # Backlog B5.2 -- prep-day / batch-cooking mode. When set (0-6,
    # Mon-Sun), the generation prompt is instructed to cluster cooking
    # effort: batch-cook a few reusable base components on this day, then
    # prefer fast-assembly recipes on other days that explicitly reuse
    # them. See meal_plan_service._format_prep_day_section for the exact
    # instruction text and its module docstring for why this shipped as a
    # generation-prompt heuristic rather than a new persisted ingredient-
    # sharing/inventory-linking model (a much bigger change the backlog
    # text didn't actually ask for).
    prep_day: int | None = Field(None, ge=0, le=6)


class NewRecipeInput(BaseModel):
    """Shape of an AI-proposed brand-new recipe for a meal-plan slot the
    existing catalog can't fill -- mirrors recipe_service.coerce_recipe_
    fields()'s output. Narrower than RecipeCreate: no source citation or
    staple/photo fields, since those aren't meaningful for a recipe with
    no external source that hasn't been reviewed/rated yet."""

    title: str
    description: str | None = None
    default_servings: int = 2
    prep_time_minutes: int | None = None
    cook_time_minutes: int | None = None
    instructions: list[str] = Field(default_factory=list)
    ingredients: list[RecipeIngredientBase] = Field(default_factory=list)
    nutrition: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    tips: list[str] = Field(default_factory=list)


class MealPlanEntryBase(BaseModel):
    day_of_week: int = Field(ge=0, le=6)
    meal_type: str = "dinner"
    servings: int = 2
    requested_tags: list[str] = Field(default_factory=list)
    is_indulgence: bool = False
    # Backlog B10.1 -- see MealPlanEntry.is_eating_out's model docstring.
    is_eating_out: bool = False
    notes: str | None = None
    # Backlog B5.1 -- see MealPlanEntry.leftover_of_entry_id's model
    # docstring. Set via PATCH /entries/{entry_id} after both entries
    # already exist (a brand-new entry created alongside its own plan has
    # no sibling id to reference yet) -- see update_meal_plan_entry's
    # validation in routers/meal_plan.py.
    leftover_of_entry_id: int | None = None


class MealPlanEntryCreate(MealPlanEntryBase):
    recipe_id: int | None = None
    # Set when recipe_id is null and this slot should create a brand-new
    # recipe on plan confirmation (see routers/meal_plan.py's create_meal_plan).
    new_recipe: NewRecipeInput | None = None
    # Backlog B3.1 -- informational only, attached during generation
    # preview (meal_plan_service.attach_restriction_warnings) so a
    # conflict is visible before the plan is ever saved. Harmless if
    # echoed back on the actual POST /api/meal-plans create call --
    # create_meal_plan doesn't read these fields, they're just along for
    # the ride on the reviewed-and-resubmitted preview payload.
    restriction_warnings: list[RestrictionMatchRead] = Field(default_factory=list)
    cross_contact_warnings: list[RestrictionMatchRead] = Field(default_factory=list)


class MealPlanEntryConfirmRequest(BaseModel):
    """Backlog B3.1: confirming an entry runs the deterministic allergen
    check against the actual recipe being confirmed (not just at
    generation-preview time -- a plan can sit around for days before a
    meal is actually made, during which household restrictions could
    change). A hard match blocks the confirm with a 409 UNLESS this flag
    is explicitly set, giving the household a real (not just cosmetic)
    speed bump before deducting inventory for a meal that conflicts with
    a stated restriction -- while still leaving a human able to say "I
    know, do it anyway" for a false positive or a one-off exception."""

    acknowledge_restriction_conflict: bool = False


class MealPlanEntryUpdate(BaseModel):
    """PATCH semantics -- all optional. Confirming/skipping a meal goes
    through the dedicated /confirm and /skip actions instead, since
    confirming has the side effect of deducting inventory."""

    meal_type: str | None = None
    recipe_id: int | None = None
    servings: int | None = None
    requested_tags: list[str] | None = None
    is_indulgence: bool | None = None
    is_eating_out: bool | None = None
    notes: str | None = None
    # Backlog B5.1 -- the actual mechanism for linking/unlinking a
    # leftover entry; validated in routers/meal_plan.py's update_meal_
    # plan_entry (must reference a different entry in the SAME plan, and
    # explicitly set to None to unlink -- see that endpoint's docstring).
    leftover_of_entry_id: int | None = None


class MealPlanEntryRecipeSummary(BaseModel):
    """Lightweight nested recipe info on a meal-plan entry -- enough for
    the weekly grid UI without a second round-trip per slot."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    default_servings: int
    is_staple: bool
    tags: list[str] = Field(default_factory=list)


class InventoryDeductionNote(BaseModel):
    """One ingredient's outcome from confirming a meal as cooked.

    Audit P1-5. Confirming a meal deducts every ingredient from
    inventory, and that used to be silently best-effort: an ingredient
    that matched nothing was skipped with no trace, and one that matched
    the WRONG row was deducted with no trace either. Now that the matcher
    refuses low-confidence matches rather than guessing, the refusals
    have to be visible or the household would just see their inventory
    quietly failing to go down.

    `status` mirrors inventory_service's DEDUCT_* vocabulary: applied,
    ambiguous (candidates found, nothing written), no_match, or
    unit_mismatch (row found and marked used, quantity left alone
    because the units are not convertible -- audit P1-4)."""

    ingredient_name: str
    status: str
    matched_item_name: str | None = None
    message: str | None = None
    candidate_names: list[str] = Field(default_factory=list)


class MealPlanEntryRead(MealPlanEntryBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    meal_plan_id: int
    recipe_id: int | None = None
    recipe: MealPlanEntryRecipeSummary | None = None
    is_confirmed: bool
    is_skipped: bool
    created_at: datetime
    updated_at: datetime
    # Populated only by the confirm endpoint; every other read of an
    # entry leaves it empty. Not persisted -- it describes what one
    # confirmation did, not a property of the entry.
    inventory_deductions: list[InventoryDeductionNote] = Field(default_factory=list)


class MealPlanBase(BaseModel):
    week_start_date: date
    household_size_snapshot: int = 2
    kitchen_profile_id: int | None = None


class MealPlanCreate(MealPlanBase):
    status: str = "draft"  # draft|active|archived
    entries: list[MealPlanEntryCreate] = Field(default_factory=list)


class MealPlanUpdate(BaseModel):
    status: str | None = None
    kitchen_profile_id: int | None = None


class MealPlanRead(MealPlanBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    entries: list[MealPlanEntryRead]
    created_at: datetime
    updated_at: datetime


class MealPlanGenerateResponse(BaseModel):
    plan: MealPlanCreate
    raw_model_output: str


class DayNutritionTotals(BaseModel):
    """Backlog B1.4 -- one day's summed per-serving nutrition across its
    non-skipped, recipe-assigned entries. `entry_count` is every such
    entry; `contributing_entry_count` is how many actually had nutrition
    data to add in -- the two can differ (e.g. a recipe that's never had
    "Compute from ingredients" run and has no AI estimate either), and
    showing both rather than just a silent total is the same "be honest
    about partial data" discipline as B1.2's computed/partial/
    ai_estimated provenance."""

    day_of_week: int
    entry_count: int
    contributing_entry_count: int
    totals: dict[str, float] = Field(default_factory=dict)


class MemberDailyTarget(BaseModel):
    """A household member's DRI-derived daily target (dri_service.py),
    or None with `missing_fields` naming exactly what's absent (weight/
    height/age) if there isn't enough data to compute one -- never a
    guessed number."""

    member_id: int
    name: str
    daily_targets: dict[str, float] | None = None
    missing_fields: list[str] = Field(default_factory=list)


class MealPlanNutritionSummary(BaseModel):
    days: list[DayNutritionTotals] = Field(default_factory=list)
    week_totals: dict[str, float] = Field(default_factory=dict)
    member_targets: list[MemberDailyTarget] = Field(default_factory=list)


class DietQualityComponent(BaseModel):
    """One HEI-2020 component's score, or a null score with `computable`
    false -- see diet_quality_service.py's module docstring for exactly
    which components that applies to and why."""

    key: str
    label: str
    max_points: int
    points: float | None = None
    value: float | None = None
    unit: str
    computable: bool


class DietQualityUnscoredComponent(BaseModel):
    key: str
    label: str
    max_points: int
    reason: str


class DietQualityScoreTotal(BaseModel):
    points: float
    max_points: int
    percent: float | None = None


class DietQualityScoreResponse(BaseModel):
    """Backlog B2.2 -- an HEI-2020-inspired diet-quality estimate, never
    the certified clinical index; `methodology` states that caveat on
    every response, not just in code comments a caller might not read."""

    computed: bool
    reason: str | None = None
    contributing_entries: int = 0
    total_entries: int = 0
    total_calories: float | None = None
    score: DietQualityScoreTotal | None = None
    components: list[DietQualityComponent] = Field(default_factory=list)
    unscored_components: list[DietQualityUnscoredComponent] = Field(default_factory=list)
    methodology: str | None = None


class GroceryListItemBase(BaseModel):
    ingredient_name: str
    quantity: float | None = None
    unit: str | None = None
    category: str | None = None


class GroceryListItemCreate(GroceryListItemBase):
    pass


class GroceryListItemUpdate(BaseModel):
    ingredient_name: str | None = None
    quantity: float | None = None
    unit: str | None = None
    category: str | None = None
    is_purchased: bool | None = None


class GroceryListItemRead(GroceryListItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    meal_plan_id: int | None = None
    is_purchased: bool
    source: str  # auto|manual
    # Why this line reads the way it does -- see GroceryListItem's model
    # docstring. Explanation only; nothing computes from these.
    needs_review: str | None = None
    matched_item_name: str | None = None
    match_confidence: str | None = None
