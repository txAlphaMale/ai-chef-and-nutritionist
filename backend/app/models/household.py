"""Household-wide preferences and individual member profiles.

HouseholdPreferences is a singleton-style table (expect id=1) holding the
settings the app brief calls out as user-customizable: household size,
dietary restrictions/goals, indulgence cadence. Seed defaults are
intentionally generic (see app/seed.py) since this repo is meant to be
pulled down and run by other households, not just the original author's.
"""
from __future__ import annotations

from sqlalchemy import Float, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class HouseholdPreferences(Base, TimestampMixin):
    __tablename__ = "household_preferences"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_size: Mapped[int] = mapped_column(Integer, default=2)
    # e.g. ["gluten_free", "celiac", "vegetarian", "low_sodium"]
    dietary_restrictions: Mapped[list] = mapped_column(JSON, default=list)
    goals: Mapped[str | None] = mapped_column(Text, nullable=True)
    # how often an indulgent/treat meal is allowed in a generated plan
    indulgence_frequency: Mapped[str] = mapped_column(String(20), default="weekly")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class HouseholdMember(Base, TimestampMixin):
    __tablename__ = "household_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    sex: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # sedentary | light | moderate | active
    activity_level: Mapped[str | None] = mapped_column(String(30), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
