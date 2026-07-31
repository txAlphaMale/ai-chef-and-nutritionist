"""Weekly meal plans, their per-meal entries, and the derived grocery list."""
from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    meal_plan: Mapped["MealPlan"] = relationship(back_populates="entries")
    recipe: Mapped["Recipe | None"] = relationship()


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
