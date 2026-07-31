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
    # Backlog B10.3 -- price actually paid for this quantity, as
    # purchased. Nullable: most intake sources have no price signal;
    # today only the order-history importer populates this.
    unit_price: float | None = None
    notes: str | None = None


class InventoryItemCreate(InventoryItemBase):
    source: str = "manual"  # manual|vision|chat|import_photo|import_pdf|import_text|import_order_history


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
    unit_price: float | None = None
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
    # Backlog B10.3 -- optional so this shape keeps working unchanged for
    # every intake source that has no price signal (vision, receipt/list
    # AI import); populated only by the order-history importer below.
    unit_price: float | None = None
    purchased_date: date | None = None


class VisionIntakeResponse(BaseModel):
    detected_items: list[VisionDetectedItem]
    raw_model_output: str = Field(..., description="Unparsed model response, for debugging/review")


class VisionIntakeConfirmRequest(BaseModel):
    items: list[InventoryItemCreate]


class InventoryImportResponse(BaseModel):
    """New intake source, added 2026-08-01 at the author's request:
    parses a receipt (photo or PDF) or a plain-text/file list of
    purchased items into the SAME preview shape vision-intake already
    established (`VisionDetectedItem`) -- deliberately reused rather
    than duplicated, since "one detected item, before the user reviews
    and confirms it" means the same thing regardless of which source
    produced it. Distinct from `/vision-intake` (a photo of what's
    CURRENTLY sitting in the pantry/fridge, not a purchase record) --
    both remain, serving genuinely different moments (a one-off pantry
    snapshot vs. recording what was just bought)."""

    detected_items: list[VisionDetectedItem]
    raw_model_output: str = Field(..., description="Unparsed model response, for debugging/review")
    source_type: str  # "photo" | "pdf" | "text" -- which input path produced this preview


class InventoryImportConfirmRequest(BaseModel):
    items: list[InventoryItemCreate]


class ColumnMapping(BaseModel):
    """Which column (by header text, not index -- headers are stable
    across re-exports of the same source even if column order isn't)
    supplies each inventory field. Any field left null means "no column
    maps to this" -- never guessed, always either the user's explicit
    choice or order_import_service's keyword-based suggestion, which the
    user can always override before parsing for real."""

    name_column: str | None = None
    quantity_column: str | None = None
    unit_column: str | None = None
    price_column: str | None = None
    date_column: str | None = None


class OrderImportPreviewResponse(BaseModel):
    """Backlog B10.3 (2026-08-01): result of parsing an uploaded order-
    history CSV/XLSX with a given (or auto-suggested) column mapping.
    `detected_items` is deliberately the SAME `VisionDetectedItem` shape
    the receipt/list AI import and pantry-photo vision intake already
    use -- the backlog explicitly calls for landing this in the same
    review-then-confirm screen, not a separate UI, and there's no reason
    for the preview shape to differ just because no AI call was involved
    this time."""

    headers: list[str]
    suggested_mapping: ColumnMapping
    mapping_used: ColumnMapping
    detected_items: list[VisionDetectedItem]
    row_count: int
    skipped_row_count: int = Field(
        ..., description="Rows with no usable name under the given mapping -- blank/footer/subtotal-style rows, most likely"
    )


class OrderImportProfileBase(BaseModel):
    name: str
    name_column: str | None = None
    quantity_column: str | None = None
    unit_column: str | None = None
    price_column: str | None = None
    date_column: str | None = None


class OrderImportProfileCreate(OrderImportProfileBase):
    pass


class OrderImportProfileUpdate(BaseModel):
    name: str | None = None
    name_column: str | None = None
    quantity_column: str | None = None
    unit_column: str | None = None
    price_column: str | None = None
    date_column: str | None = None


class OrderImportProfileRead(OrderImportProfileBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime


class InventoryDeductRequest(BaseModel):
    """Name-based deduction -- used by confirmed chat actions (Phase 7)
    and anywhere else "we used some of X" needs to resolve a name to a
    row without the caller knowing its id. See
    inventory_service.deduct_by_name for the matching logic."""

    ingredient_name: str
    quantity: float | None = None  # None means "deduct one unit"
    # Unit `quantity` is expressed in (backlog B5.3) -- converted against
    # the matched item's own unit before subtracting when both are known
    # and differ. Optional and backward compatible: omitting it keeps the
    # previous same-unit-assumed behavior.
    unit: str | None = None


class InventoryUpdateByNameRequest(BaseModel):
    """Name-based partial update -- used by confirmed chat actions for
    things like "mark the lentils as priority" or "we're out of milk"
    (set quantity) without the user/model needing to know the item's id.
    See inventory_service.update_by_name."""

    ingredient_name: str
    quantity: float | None = None
    unit: str | None = None
    category: str | None = None
    is_priority: bool | None = None
    priority_note: str | None = None
