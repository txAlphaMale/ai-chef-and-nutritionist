"""Pydantic request/response models for household-wide preferences and
individual member profiles."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class HouseholdPreferencesUpdate(BaseModel):
    household_size: int | None = None
    dietary_restrictions: list[str] | None = None
    goals: str | None = None
    indulgence_frequency: str | None = None
    notes: str | None = None


class HouseholdPreferencesRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    household_size: int
    dietary_restrictions: list[str] = Field(default_factory=list)
    goals: str | None = None
    indulgence_frequency: str
    notes: str | None = None
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
