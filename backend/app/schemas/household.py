"""Pydantic request/response models for household-wide preferences and
individual member profiles."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.allergen_service import ALLERGEN_KEYS, OBSERVANCE_LEVEL_KEYS
from app.services.dietary_pattern_service import DIETARY_PATTERN_KEYS


class HouseholdPreferencesUpdate(BaseModel):
    household_size: int | None = None
    dietary_restrictions: list[str] | None = None
    goals: str | None = None
    indulgence_frequency: str | None = None
    notes: str | None = None
    # Backlog B3.1/B3.2 -- validated against the fixed taxonomy so an
    # unrecognized key can't silently do nothing (allergen_service's
    # matching functions already ignore unknown keys defensively, but
    # rejecting them here at the API boundary catches a typo immediately
    # instead of it quietly never matching anything).
    restricted_allergens: list[str] | None = None
    gluten_observance_level: str | None = None
    # Backlog B2.3 -- same "validate against the registry" discipline as
    # the two fields above, so a typo'd/removed preset key fails loudly
    # at the API boundary instead of silently generating with no guidance.
    dietary_pattern: str | None = None
    # Backlog B5.5 -- free text, no fixed taxonomy to validate against
    # (see the model column's own docstring for why).
    pantry_staples: list[str] | None = None

    @field_validator("restricted_allergens")
    @classmethod
    def _validate_allergens(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        unknown = set(v) - ALLERGEN_KEYS
        if unknown:
            raise ValueError(f"Unknown allergen key(s): {', '.join(sorted(unknown))}")
        return v

    @field_validator("gluten_observance_level")
    @classmethod
    def _validate_observance_level(cls, v: str | None) -> str | None:
        if v is not None and v not in OBSERVANCE_LEVEL_KEYS:
            raise ValueError(f"Unknown gluten observance level: {v}")
        return v

    @field_validator("dietary_pattern")
    @classmethod
    def _validate_dietary_pattern(cls, v: str | None) -> str | None:
        if v is not None and v not in DIETARY_PATTERN_KEYS:
            raise ValueError(f"Unknown dietary pattern: {v}")
        return v


class HouseholdPreferencesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    household_size: int
    dietary_restrictions: list[str] = Field(default_factory=list)
    goals: str | None = None
    indulgence_frequency: str
    notes: str | None = None
    restricted_allergens: list[str] = Field(default_factory=list)
    gluten_observance_level: str | None = None
    dietary_pattern: str | None = None
    pantry_staples: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class HouseholdMemberBase(BaseModel):
    name: str
    age: int | None = None
    height_cm: float | None = None
    sex: str | None = None
    activity_level: str | None = None  # sedentary | light | moderate | active
    notes: str | None = None


class HouseholdMemberCreate(HouseholdMemberBase):
    pass


class HouseholdMemberUpdate(BaseModel):
    name: str | None = None
    age: int | None = None
    height_cm: float | None = None
    sex: str | None = None
    activity_level: str | None = None
    notes: str | None = None


class HouseholdMemberRead(HouseholdMemberBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime
