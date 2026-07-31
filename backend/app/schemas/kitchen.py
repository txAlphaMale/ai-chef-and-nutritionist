"""Pydantic request/response models for kitchen/equipment profiles --
lets the meal planner adapt to a home kitchen, camping trip, RV, or
short-term rental with limited gear (see KitchenProfile model)."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class KitchenProfileBase(BaseModel):
    name: str
    equipment: list[str] = Field(default_factory=list)
    notes: str | None = None


class KitchenProfileCreate(KitchenProfileBase):
    is_active: bool = False


class KitchenProfileUpdate(BaseModel):
    name: str | None = None
    equipment: list[str] | None = None
    is_active: bool | None = None
    notes: str | None = None


class KitchenProfileRead(KitchenProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
