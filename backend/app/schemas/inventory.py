"""Pydantic request/response models for the inventory API."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class InventoryItemBase(BaseModel):
    name: str
    category: str = "pantry"  # pantry|fridge|freezer|produce|spice|other
    quantity: float = 1.0
    unit: str | None = None
    location: str | None = None
    purchased_date: date | None = None
    expiration_date: date | None = None
    last_used_date: date | None = None
    is_priority: bool = False
    priority_note: str | None = None
    notes: str | None = None


class InventoryItemCreate(InventoryItemBase):
    source: str = "manual"  # manual|vision|chat


class InventoryItemUpdate(BaseModel):
    """All fields optional -- PATCH semantics."""

    name: str | None = None
    category: str | None = None
    quantity: float | None = None
    unit: str | None = None
    location: str | None = None
    purchased_date: date | None = None
    expiration_date: date | None = None
    last_used_date: date | None = None
    is_priority: bool | None = None
    priority_note: str | None = None
    notes: str | None = None


class InventoryItemRead(InventoryItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    created_at: datetime
    updated_at: datetime


class PrioritySuggestion(BaseModel):
    item: InventoryItemRead
    urgency_score: float
    reasons: list[str]


class VisionDetectedItem(BaseModel):
    """One item as detected from a photo, before the user reviews/edits
    and confirms it into real inventory rows."""

    name: str
    estimated_quantity: float | None = None
    unit: str | None = None
    category: str = "other"
    expiration_date: date | None = None
    confidence_note: str | None = None


class VisionIntakeResponse(BaseModel):
    detected_items: list[VisionDetectedItem]
    raw_model_output: str = Field(..., description="Unparsed model response, for debugging/review")


class VisionIntakeConfirmRequest(BaseModel):
    items: list[InventoryItemCreate]
