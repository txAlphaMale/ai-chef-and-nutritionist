"""Response shapes for the Home dashboard (capstone review 2026-08-16,
backlog B24.3). See app/services/dashboard_service.py for why this is one
endpoint rather than five calls from the page."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class DashboardExpiringItem(BaseModel):
    id: int
    name: str
    category: str | None = None
    expiration_date: date | None = None
    # Negative when already past. Computed server-side against the same
    # `today` every other figure here uses, so the card cannot disagree
    # with itself across a timezone boundary.
    days_until: int | None = None


class DashboardInventory(BaseModel):
    total_items: int = 0
    expired: int = 0
    expiring_soon: int = 0
    within_days: int = 7
    soonest: list[DashboardExpiringItem] = Field(default_factory=list)


class DashboardPlanEntry(BaseModel):
    entry_id: int
    meal_type: str
    recipe_id: int | None = None
    recipe_title: str | None = None
    servings: int = 2
    is_confirmed: bool = False
    is_skipped: bool = False
    is_eating_out: bool = False


class DashboardMealPlan(BaseModel):
    plan_id: int | None = None
    week_start_date: date | None = None
    status: str | None = None
    # False when the most recent plan is not about the week we are in --
    # the card says so rather than presenting a stale week as tonight.
    is_current_week: bool = False
    today_entries: list[DashboardPlanEntry] = Field(default_factory=list)
    planned: int = 0
    confirmed: int = 0
    skipped: int = 0
    grocery_outstanding: int = 0


class DashboardRecipes(BaseModel):
    total: int = 0
    staples: int = 0


class DashboardMetric(BaseModel):
    value: float | None = None
    entry_date: date | None = None


class DashboardHealth(BaseModel):
    # Keyed by metric name. Each carries its OWN date: a lipid panel and a
    # weigh-in rarely happen on the same day, so a single "latest entry"
    # would blank the cholesterol every time somebody stepped on a scale.
    latest: dict[str, DashboardMetric] = Field(default_factory=dict)
    entry_count: int = 0


class DashboardRecalls(BaseModel):
    active: int = 0


class DashboardSetupItem(BaseModel):
    key: str
    label: str
    done: bool
    hint: str
    route: str


class DashboardResponse(BaseModel):
    today: date
    household_size: int = 2
    inventory: DashboardInventory
    meal_plan: DashboardMealPlan
    recipes: DashboardRecipes
    health: DashboardHealth
    recalls: DashboardRecalls
    setup: list[DashboardSetupItem] = Field(default_factory=list)
