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
    # Free-text-ish goals/preferences, still fed to the LLM as prose --
    # e.g. ["gluten_free", "celiac", "vegetarian", "low_sodium"]. Kept
    # separate from restricted_allergens below: this field is
    # interpretive (the model reads it), that one is deterministic (code
    # checks it) -- see app/services/allergen_service.py's module
    # docstring for why nothing before backlog B3.1 could structurally
    # guarantee a generated plan or imported recipe avoided an allergen.
    dietary_restrictions: Mapped[list] = mapped_column(JSON, default=list)
    goals: Mapped[str | None] = mapped_column(Text, nullable=True)
    # how often an indulgent/treat meal is allowed in a generated plan
    indulgence_frequency: Mapped[str] = mapped_column(String(20), default="weekly")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Backlog B3.1 -- a fixed taxonomy (app/services/allergen_service.py's
    # ALLERGEN_CHOICES) the app can deterministically check ingredient
    # names against, rather than only ever trusting the LLM to have
    # honored dietary_restrictions' free text. List of canonical keys,
    # e.g. ["gluten", "milk"].
    restricted_allergens: Mapped[list] = mapped_column(JSON, default=list)
    # Backlog B3.2 -- None (no gluten restriction, or "gluten" isn't in
    # restricted_allergens) | "flexible" | "strict_gluten_only" |
    # "strict_no_cross_contact" (allergen_service.OBSERVANCE_LEVELS).
    # Only "strict_no_cross_contact" additionally surfaces the oats/
    # cross-contact warning -- the other two levels behave identically
    # to just having "gluten" restricted with no observance level set.
    gluten_observance_level: Mapped[str | None] = mapped_column(String(30), nullable=True)

    # Backlog B2.3 -- None (no preset selected, `goals` free text is all
    # generation gets) | a key from
    # app/services/dietary_pattern_service.DIETARY_PATTERN_KEYS (currently
    # just "portfolio_ldl"). A structured lever concrete generation
    # guidance can hang off of, distinct from `goals` in the same way
    # restricted_allergens is distinct from dietary_restrictions above:
    # this field is interpretive (the model still decides HOW to apply
    # it), but the guidance text itself is fixed and sourced, not
    # reinterpreted from scratch by the model on every run.
    dietary_pattern: Mapped[str | None] = mapped_column(String(30), nullable=True)


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
