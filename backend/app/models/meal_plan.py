"""Weekly meal plans, their per-meal entries, and the derived grocery list."""
from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin


class MealPlan(Base, TimestampMixin):
    __tablename__ = "meal_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    week_start_date: Mapped[date] = mapped_column(Date, index=True)
    household_size_snapshot: Mapped[int] = mapped_column(Integer, default=2)
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft|active|archived
    kitchen_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("kitchen_profiles.id"), nullable=True
    )

    entries: Mapped[list["MealPlanEntry"]] = relationship(
        back_populates="meal_plan", cascade="all, delete-orphan"
    )


class MealPlanEntry(Base, TimestampMixin):
    __tablename__ = "meal_plan_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    meal_plan_id: Mapped[int] = mapped_column(ForeignKey("meal_plans.id"))
    day_of_week: Mapped[int] = mapped_column(Integer)  # 0=Monday .. 6=Sunday
    meal_type: Mapped[str] = mapped_column(String(20), default="dinner")  # breakfast|lunch|dinner|snack
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipes.id"), nullable=True)
    servings: Mapped[int] = mapped_column(Integer, default=2)

    # Guiding constraints this slot was planned against, e.g. ["quick"] or
    # ["portable", "non_refrigerated"] for a picnic -- either requested by
    # the user before generation or reflecting the tags of the chosen
    # recipe. Purely descriptive; doesn't restrict manual edits.
    requested_tags: Mapped[list] = mapped_column(JSON, default=list)
    # Marks this slot as the week's occasional indulgence (see
    # HouseholdPreferences.indulgence_frequency) rather than a fully
    # "balanced" meal -- lets the UI/nutrition views distinguish it.
    is_indulgence: Mapped[bool] = mapped_column(Boolean, default=False)

    # Confirming a meal was made triggers ingredient deduction from inventory
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_skipped: Mapped[bool] = mapped_column(Boolean, default=False)
    # Backlog B10.1 (2026-08-01) -- marks a slot as "we're eating out"
    # rather than an unplanned/empty one. Purely descriptive: a
    # recipe-less entry (recipe_id=None) ALREADY confirms without any
    # inventory deduction attempt and is ALREADY excluded from grocery-
    # list aggregation and the nutrition summary (both loops in
    # meal_plan_service.py skip on `entry.recipe is None` -- verified by
    # reading them before adding this, rather than assuming new
    # exclusion logic was needed). This flag exists only so the UI can
    # render "🍽️ eating out" instead of a blank cell for a slot that
    # was deliberately left recipe-less, not accidentally forgotten.
    is_eating_out: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Backlog B5.1 (2026-08-01) -- "leftovers welcome" per the project
    # brief, and the stated problem this solves: a Sunday cook that makes
    # enough for Sunday dinner AND Monday lunch previously had no way to
    # represent that without either double-buying/double-deducting for
    # Monday's slot, or leaving Monday recipe-less and losing its
    # nutrition/display info entirely. Set this on the LATER entry
    # (Monday lunch) pointing back at the entry whose cook event it's
    # drawing from (Sunday dinner) -- see meal_plan_service.
    # compute_grocery_list (skips a leftover entry's own ingredient
    # contribution, since the origin entry's grocery need already covers
    # the combined total) and routers/meal_plan.py's confirm_meal_plan_
    # entry (skips inventory deduction on a leftover entry's confirm, for
    # the same reason -- deduct once, at the origin, not twice).
    # Deliberately a single nullable self-FK, not a "cook batch" grouping
    # table: the backlog's own text describes exactly a two-entry
    # relationship ("a Sunday cook can legitimately fill Monday lunch"),
    # and a chain of these (Monday lunch leftover-of Sunday dinner,
    # Tuesday lunch also leftover-of Sunday dinner) already covers "one
    # cook event feeds several slots" without needing a grouping concept.
    # Not cascade-deleted on the origin's deletion -- a dangling leftover
    # link just means grocery aggregation resumes counting that entry's
    # own ingredients normally, which is the safe default (a household
    # deleting an entry that had leftovers linked to it should not
    # silently make a grocery-list shortfall appear elsewhere), not a
    # data-integrity break.
    leftover_of_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("meal_plan_entries.id"), nullable=True
    )

    # Backlog B12.1 (2026-08-01) -- the Google Calendar event id this
    # entry currently corresponds to in the household's dedicated "Chef
    # Meal Plan" calendar, or None if it has never been pushed (sync is
    # off, was off when this entry was created, or the push simply
    # hasn't run yet). Lets google_calendar_service tell "create a new
    # event" from "update/delete this existing one" apart per entry,
    # and clean up a stale event when an entry is skipped or its plan is
    # deleted, rather than orphaning events in the user's calendar.
    # Deliberately not a FK/relationship -- it's an opaque id from an
    # external system, same treatment as any other third-party
    # reference this app doesn't otherwise model.
    google_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    meal_plan: Mapped["MealPlan"] = relationship(back_populates="entries")
    recipe: Mapped["Recipe | None"] = relationship()
    leftover_entries: Mapped[list["MealPlanEntry"]] = relationship(
        "MealPlanEntry",
        backref=backref("leftover_of_entry", remote_side="MealPlanEntry.id"),
        foreign_keys=[leftover_of_entry_id],
    )


class GroceryListItem(Base, TimestampMixin):
    __tablename__ = "grocery_list_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    meal_plan_id: Mapped[int | None] = mapped_column(ForeignKey("meal_plans.id"), nullable=True)
    ingredient_name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # Mirrors InventoryItem's category values (pantry/fridge/freezer/
    # produce/spice/other) so a future UI can group the list by aisle;
    # nullable since auto-generated items don't always have a confident
    # category guess.
    category: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_purchased: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(20), default="auto")  # auto|manual

    # --- Reconciliation provenance (2026-08-03) ------------------------
    #
    # Why this line reads the way it does. `subtract_inventory` has
    # produced all three of these for a while; none of them were
    # persisted, so the explanation was computed and then thrown away one
    # function call later and the user never saw any of it.
    #
    # `needs_review` is audit P1-4's output: the recipe's unit and the
    # matched row's unit are not convertible, so the line kept its FULL
    # quantity rather than being reduced by a number that would have been
    # wrong. Without this column the user just sees a line for something
    # they know they have, with no reason given.
    #
    # `matched_item_name` / `match_confidence` are audit P1-5's: which
    # inventory row this line was reconciled against, and how sure the
    # resolver was. Reported even on a confident match -- the user is
    # about to shop from this list, and "we took 1 lb off because you
    # already have X" is only checkable if X is named.
    #
    # All nullable and all ignored by every calculation: this is
    # explanation, never input.
    needs_review: Mapped[str | None] = mapped_column(Text, nullable=True)
    matched_item_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    match_confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)
