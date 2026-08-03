"""Pydantic request/response models for the inventory API."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class InventoryItemBase(BaseModel):
    name: str
    category: str = "pantry"  # pantry|fridge|freezer|produce|spice|other
    # Quantity model redesign (2026-08-02, author-requested) -- see
    # app/models/inventory.py's InventoryItem docstring for the full
    # rationale. `quantity` is CURRENT ON-HAND, in `unit`; `unit` should
    # be a real convertible measurement unit (oz/lb/g/kg/ml/l/cup/tbsp/
    # tsp/count), not a compound "8 oz bag"-style string. `unit` is kept
    # a free string (not a hard enum) at the API layer, same soft-
    # validation posture `category` already has -- the frontend offers a
    # curated dropdown, but this layer doesn't hard-reject anything, so a
    # household with an unusual unit isn't locked out.
    quantity: float = 1.0
    unit: str | None = None
    # Immutable purchase-time snapshot of `quantity` -- "volume at time
    # of purchase" vs. `quantity`'s "volume on hand". Optional here: the
    # create endpoint defaults it to the row's own `quantity` when the
    # caller doesn't supply one, since at creation time on-hand IS what
    # was just purchased. See cost_service.py for why this exists (it's
    # the stable denominator for $/unit math, instead of the live,
    # depleting `quantity`).
    purchased_quantity: float | None = None
    # Purely descriptive package metadata -- never read by deduction or
    # cost math. See app/services/package_parsing.py.
    package_quantity: float | None = None
    package_count: float | None = None
    package_descriptor: str | None = None
    location: str | None = None
    purchased_date: date | None = None
    expiration_date: date | None = None
    last_used_date: date | None = None
    is_priority: bool = False
    priority_note: str | None = None
    # Backlog B10.3 -- price actually paid for this quantity, as
    # purchased. Nullable: many intake sources (a manual add, a bare
    # pantry photo) genuinely have no price signal. Populated by the
    # order-history importer, and, since 2026-08-02, the receipt/list AI
    # import too when a per-line price is printed on the source.
    unit_price: float | None = None
    notes: str | None = None


class InventoryItemCreate(InventoryItemBase):
    source: str = "manual"  # manual|vision|chat|import_photo|import_pdf|import_text|import_order_history|barcode


class InventoryItemUpdate(BaseModel):
    """All fields optional -- PATCH semantics."""

    name: str | None = None
    category: str | None = None
    quantity: float | None = None
    unit: str | None = None
    purchased_quantity: float | None = None
    package_quantity: float | None = None
    package_count: float | None = None
    package_descriptor: str | None = None
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


class ExpiringDigestResponse(BaseModel):
    """Backlog B4.4 (via B10.2) -- the in-app expiration digest. See
    inventory_service.get_expiring_digest's docstring for the full
    rationale and what was deliberately not built (push/email)."""

    expired: list[InventoryItemRead]
    expiring_soon: list[InventoryItemRead]
    within_days: int


class RecallAlertRead(BaseModel):
    """Backlog B3.3 -- one persisted recall match. See
    app.models.inventory.RecallAlert and app.services.recall_service for
    the full matching/persistence rationale."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str  # fsis | openfda
    external_id: str
    matched_item_name: str
    title: str
    reason: str | None = None
    classification: str | None = None
    status: str | None = None
    recall_date: date | None = None
    states: str | None = None
    summary: str | None = None
    is_dismissed: bool


class RecallStatusResponse(BaseModel):
    """GET /api/inventory/recalls -- fast, DB-only. `check_due` tells the
    frontend whether a background refresh was just kicked off (see
    routers/inventory.py's get_recalls)."""

    alerts: list[RecallAlertRead]
    last_checked_at: datetime | None
    check_due: bool


class ShelfLifeSuggestionResponse(BaseModel):
    """Backlog B4.3 (2026-08-01): result of GET /api/inventory/
    shelf-life-suggestion?name=X&category=Y&purchased_date=Z against the
    shipped USDA FoodKeeper catalog (foodkeeper_service). `found=False`
    means no confident FoodKeeper match (an unusual/branded item name, or
    a category FoodKeeper doesn't map cleanly onto -- see
    foodkeeper_service.CATEGORY_FIELD_ORDER) -- the frontend should treat
    this the same as "no suggestion available," not an error, and just
    leave the expiration-date field for the household to fill in
    themselves as always. Always an estimate, never authoritative -- see
    the module docstring's food-safety framing."""

    found: bool
    matched_name: str | None = None
    foodkeeper_id: int | None = None
    storage: str | None = None  # pantry|fridge|freezer -- which FoodKeeper range this came from
    days_min: int | None = None
    days_max: int | None = None
    suggested_expiration_date: date | None = None


class BarcodeLookupResponse(BaseModel):
    """Backlog B4.1 (2026-08-01): result of GET /api/inventory/barcode-
    lookup?barcode=X against Open Food Facts (food_data_service.
    get_off_product). `found=False` means the barcode isn't in OFF's
    database (common for local/store-brand/less common products -- OFF
    is crowd-sourced, not exhaustive) -- the frontend scanner should fall
    back to a manual-entry form pre-filled with nothing but the scanned
    barcode itself in that case, not treat it as an error. There's no
    separate confirm endpoint for this source: unlike the batch vision/
    receipt intakes, a barcode scan is always exactly one item, so the
    frontend just lets the user review/edit this preview and POSTs a
    normal InventoryItemCreate (source="barcode") straight to
    POST /api/inventory."""

    barcode: str
    found: bool
    name: str | None = None
    brand: str | None = None
    # Open Food Facts' own free-text package-size field (e.g. "500 g",
    # "12 x 355 ml") -- kept verbatim for reference/display alongside the
    # structured fields below.
    #
    # Revised 2026-08-02 (superseding this field's prior "never parsed"
    # docstring): this string IS now parsed, via
    # app/services/package_parsing.py -- what changed is not the "never
    # invent a conversion" discipline (still true: no unit-family
    # crossing, no density guessing happens here), but this app's own
    # data model. `quantity`/`unit` used to mean "1 of this package";
    # they now mean "how much, in a real measurement unit, is on hand"
    # (see InventoryItem's own docstring) -- so extracting "500" and "g"
    # out of "500 g" is a plain number+unit read, not a conversion, and
    # is exactly what the new model wants. `estimated_quantity`/`unit`
    # below reflect that parse when it succeeds (falling back to the
    # prior 1/"count" default when OFF's text doesn't contain a
    # recognizable leading measurement, e.g. a blank field or "12
    # pieces").
    quantity_text: str | None = None
    estimated_quantity: float = 1
    unit: str = "count"
    package_quantity: float | None = None
    package_count: float | None = None
    package_descriptor: str | None = None
    category: str | None = None
    image_url: str | None = None
    confidence_note: str | None = None


class VisionDetectedItem(BaseModel):
    """One item as detected from a photo, before the user reviews/edits
    and confirms it into real inventory rows."""

    name: str
    estimated_quantity: float | None = None
    unit: str | None = None
    # Package/measurement split (2026-08-02) -- populated whenever
    # inventory_service.parse_vision_response (or order_import_service.
    # apply_mapping) could split the source's unit text via
    # package_parsing.parse_package_text; null whenever it couldn't, in
    # which case `unit`/`estimated_quantity` behave exactly as before
    # this field existed (freeform unit text, quantity = how many
    # purchased). See InventoryItem's own docstring for the full model.
    package_quantity: float | None = None
    package_count: float | None = None
    package_descriptor: str | None = None
    category: str = "other"
    expiration_date: date | None = None
    confidence_note: str | None = None
    # Backlog B10.3 -- optional so this shape keeps working unchanged for
    # intake sources with no price/date signal at all (a bare pantry
    # photo has neither). Populated by the order-history importer, and,
    # since 2026-08-02, by the receipt/list AI import too whenever the
    # source actually prints a price and/or a purchase date -- see
    # RECEIPT_IMPORT_PROMPT and inventory_service.parse_vision_response.
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
        ...,
        description="Rows with no usable name under the given mapping -- blank/footer/subtotal-style rows, most likely",
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
    # Audit P1-5. The user's answer to a disambiguation prompt: apply the
    # deduction to THIS row and skip name matching entirely. Set by the
    # frontend after the 409 below offered a candidate list.
    item_id: int | None = None
    # Persist that answer as an alias, so the same name resolves straight
    # to the same item next time and the question is asked exactly once.
    # Only meaningful together with `item_id`.
    remember_alias: bool = False


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
    # Same disambiguation escape hatch as InventoryDeductRequest above.
    item_id: int | None = None
    remember_alias: bool = False


# --- Audit P1-5: ingredient resolution ---------------------------------


class IngredientMatchCandidate(BaseModel):
    """One scored inventory row from the resolver, with the reasoning
    attached. `reason` is meant to be shown to the user verbatim -- the
    whole point of this layer is that a match is explainable, so a
    candidate list that only shows names would throw away the useful
    half."""

    item_id: int | None = None
    name: str
    score: float
    confidence: str  # exact | high | medium | low
    reason: str
    quantity: float | None = None
    unit: str | None = None
    category: str | None = None
    expiration_date: date | None = None
    # Set only on entries in `blocked_candidates`: why this row was ruled
    # out despite sharing words with the query.
    blocked_by: str | None = None


class IngredientResolutionResponse(BaseModel):
    """What `GET /api/inventory/resolve` returns, and the body of the 409
    the write endpoints raise when they will not guess.

    `matched` false with a non-empty `candidates` is the "which did you
    mean?" state; `matched` false with an empty `candidates` is "nothing
    like this is tracked". `blocked_candidates` exists so a user who
    expected a match can see that e.g. "chicken broth" was deliberately
    excluded rather than overlooked -- an unexplained non-match invites
    the same bug report twice."""

    query: str
    normalized: str
    matched: bool
    match: IngredientMatchCandidate | None = None
    via_alias: bool = False
    threshold: float
    candidates: list[IngredientMatchCandidate] = Field(default_factory=list)
    blocked_candidates: list[IngredientMatchCandidate] = Field(default_factory=list)
    message: str | None = None


class IngredientAliasCreate(BaseModel):
    """Teach the resolver that one name means one ingredient. Written
    either from the Settings alias manager or automatically when a user
    answers a disambiguation prompt with `remember_alias`."""

    alias_text: str
    canonical_name: str
    inventory_item_id: int | None = None
    note: str | None = None


class IngredientAliasRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    alias_text: str
    alias_normalized: str
    canonical_name: str
    inventory_item_id: int | None = None
    source: str
    note: str | None = None
    created_at: datetime
