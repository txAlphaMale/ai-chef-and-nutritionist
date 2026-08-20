"""Pydantic request/response models for the food log (backlog B17.1)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, model_validator


class FoodLogEntryCreate(BaseModel):
    """A manually logged meal.

    `nutrition` and `nutrition_provenance` are deliberately NOT accepted
    from the client, the same posture `RecipeCreate` takes: a caller must
    not be able to assert "computed" over numbers nothing computed. A
    manual entry either references a recipe -- in which case the server
    derives and scales the nutrition itself -- or it carries no nutrition
    and says so.
    """

    description: str | None = None
    meal_type: str = "dinner"
    source: str = "manual"
    eaten_at: datetime | None = None
    member_id: int | None = None
    recipe_id: int | None = None
    inventory_item_id: int | None = None
    servings: float = 1.0
    notes: str | None = None

    @model_validator(mode="after")
    def _needs_something_to_identify_the_meal(self) -> "FoodLogEntryCreate":
        """Either a recipe or a description. A row with neither says only
        that eating occurred, which is not a fact anybody can read back or
        act on -- and it would show in the history as a blank line."""
        if self.recipe_id is None and not (self.description or "").strip():
            raise ValueError("a food log entry needs either a recipe_id or a description")
        if self.servings is not None and self.servings <= 0:
            raise ValueError("servings must be greater than zero")
        return self


class FoodLogEntryUpdate(BaseModel):
    """PATCH semantics. Nutrition is absent here for the same reason it is
    absent from create, and `meal_plan_entry_id` is absent because an
    automatic row's link to its plan slot is not something a client should
    be able to rewrite -- B17.4's adherence view is built on that link."""

    description: str | None = None
    meal_type: str | None = None
    eaten_at: datetime | None = None
    member_id: int | None = None
    servings: float | None = None
    notes: str | None = None


class FoodLogEntryRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    member_id: int | None = None
    eaten_at: datetime
    meal_type: str
    source: str
    description: str
    recipe_id: int | None = None
    inventory_item_id: int | None = None
    meal_plan_entry_id: int | None = None
    servings: float
    # Absolute for this entry, already multiplied by `servings` -- not
    # per-serving. See food_log_service for why that multiplication lives
    # in exactly one place.
    nutrition: dict = {}
    # NULL means this entry carries no nutrition at all. Any consumer
    # summing these must count the NULLs and report them, never treat
    # them as zero.
    nutrition_provenance: str | None = None
    notes: str | None = None
    created_at: datetime


class FoodLogDaySummary(BaseModel):
    """One day's totals, with the honesty fields attached rather than
    optional.

    `unquantified_entries` is not a diagnostic. It is the denominator: a
    day with two logged meals and no nutrition on either has totals of
    zero, and a UI that showed only the totals would render the least-
    known day as the best one. Every consumer of this shape is expected
    to display the count.
    """

    date: str  # ISO calendar date, in the household's own timezone offset
    entry_count: int
    unquantified_entries: int
    nutrition: dict = {}
    # The weakest provenance among the entries that DID contribute, since
    # a total is only as trustworthy as its worst input: "partial" beats
    # "computed" and "ai_estimated" beats both. NULL when nothing
    # contributed.
    nutrition_provenance: str | None = None
