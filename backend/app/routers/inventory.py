"""Pantry/fridge/freezer/produce/spice inventory: CRUD, urgency-ranked
suggestions for the meal planner, AI vision photo intake (what's
CURRENTLY in the pantry/fridge), and AI receipt/list import (2026-08-01,
author-requested -- what was just PURCHASED, from a receipt photo/PDF or
a plain-text/file list). The two intake sources are deliberately kept
separate rather than merged into one endpoint: they answer different
questions ("what do I have" vs. "what did I just buy") and need
different prompts (a pantry photo just names visible items; a receipt
has to skip subtotal/tax/tender lines and expand POS abbreviations) --
but both land in the SAME preview-then-confirm shape
(VisionDetectedItem/InventoryItemCreate) since "one detected item before
the user reviews and confirms it" means the same thing regardless of
source.

Route ordering matters here -- FastAPI matches path operations in
declaration order, so the static paths (/priority-suggestions,
/vision-intake, /vision-intake/confirm, /import, /import/confirm,
/deduct, /update-by-name) are declared before the dynamic /{item_id}
routes to avoid being swallowed by them.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import InventoryItem
from app.schemas.inventory import (
    InventoryDeductRequest,
    InventoryImportConfirmRequest,
    InventoryImportResponse,
    InventoryItemCreate,
    InventoryItemRead,
    InventoryItemUpdate,
    InventoryUpdateByNameRequest,
    PrioritySuggestion,
    VisionIntakeConfirmRequest,
    VisionIntakeResponse,
)
from app.services import inventory_service, ollama_client, recipe_service

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

VISION_PROMPT = """\
Look at this photo of food items and identify each distinct item you can see.
Respond with ONLY a JSON array (no other text, no markdown fences) where each \
element is an object with these keys:
- "name": string, the food item's name
- "estimated_quantity": number or null
- "unit": string or null (e.g. "count", "lbs", "oz", "cans")
- "category": one of "pantry", "fridge", "freezer", "produce", "spice", "other"
- "estimated_expiration_days": integer number of days from today this item is \
likely still good for, or null if you can't estimate
- "confidence_note": a short string noting any uncertainty, or null

Example: [{"name": "milk", "estimated_quantity": 1, "unit": "gallon", \
"category": "fridge", "estimated_expiration_days": 7, "confidence_note": null}]
"""

# Backlog B4.2 (author-requested 2026-08-01) -- shares VisionDetectedItem's
# exact output shape with VISION_PROMPT above (same keys), so
# inventory_service.parse_vision_response works unchanged for this
# source too -- only the prompt differs. Uses the same "{content}"
# .format() placeholder trick as recipe_service.RECIPE_IMPORT_PROMPT: for
# a photo, the caller substitutes a short description instead of real
# text (see import_inventory below) rather than needing a second prompt
# template. Deliberately has NO inline JSON example (unlike VISION_PROMPT
# above, which is never passed through .format() so literal braces are
# safe there) -- an example containing literal `{`/`}` would need
# doubling to survive .format(), and the bullet list alone is unambiguous
# without one.
RECEIPT_IMPORT_PROMPT = """\
You are extracting grocery/food items from either a photo of a paper \
receipt, a PDF receipt, or a plain-text list of items someone typed or \
pasted. Here is the content to parse:

{content}

Extract every actual food/grocery line item that was purchased or listed. \
SKIP anything that is not itself a purchasable item: subtotals, tax, \
total, tender/change amounts, coupons, loyalty/rewards messages, store \
name/address/phone, and cashier/register/date-time header lines.

Receipt item names are frequently abbreviated by point-of-sale systems \
(e.g. "ORG BANANA", "GV 2% MLK GAL"). Expand these into a normal, \
readable food name when you are reasonably confident what it means (e.g. \
"Organic bananas", "Great Value 2% milk, 1 gallon"). If an abbreviation \
is genuinely ambiguous, keep your best-guess name but say so in \
"confidence_note" -- never silently invent a specific brand or variety \
you are not reasonably sure of.

Respond with ONLY a JSON array (no other text, no markdown fences) where \
each element is an object with these keys:
- "name": string, the food item's name
- "estimated_quantity": number or null (default to 1 if the source shows \
no explicit quantity for a line item)
- "unit": string or null (e.g. "count", "lbs", "oz", "gallon")
- "category": one of "pantry", "fridge", "freezer", "produce", "spice", \
"other"
- "estimated_expiration_days": integer number of days from today this \
item is typically still good for, or null if you can't estimate -- \
receipts/lists never print an expiration date, so this is always a \
category-based estimate, not something read off the source
- "confidence_note": a short string noting any uncertainty (e.g. an \
ambiguous abbreviation), or null
"""


@router.get("", response_model=list[InventoryItemRead])
def list_inventory(
    db: Session = Depends(get_db),
    category: str | None = None,
    is_priority: bool | None = None,
    search: str | None = None,
):
    query = db.query(InventoryItem)
    if category:
        query = query.filter(InventoryItem.category == category)
    if is_priority is not None:
        query = query.filter(InventoryItem.is_priority == is_priority)
    if search:
        query = query.filter(InventoryItem.name.ilike(f"%{search}%"))
    return query.order_by(InventoryItem.name).all()


@router.get("/priority-suggestions", response_model=list[PrioritySuggestion])
def priority_suggestions(limit: int = 10, db: Session = Depends(get_db)):
    scored = inventory_service.get_priority_suggestions(db, limit=limit)
    return [
        PrioritySuggestion(item=item, urgency_score=score, reasons=reasons)
        for item, score, reasons in scored
    ]


@router.post("/vision-intake", response_model=VisionIntakeResponse)
async def vision_intake(file: UploadFile, db: Session = Depends(get_db)):
    """Analyzes an uploaded photo with the configured Ollama vision model
    and returns a PREVIEW of detected items -- nothing is written to
    inventory here. The user reviews/edits the preview client-side, then
    POSTs the confirmed list to /vision-intake/confirm."""
    image_bytes = await file.read()
    try:
        response = ollama_client.describe_image(db, image_bytes, VISION_PROMPT)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Ollama vision request failed: {exc}") from exc

    raw_output = response.get("message", {}).get("content", "") if isinstance(response, dict) else str(response)
    detected = inventory_service.parse_vision_response(raw_output)
    return VisionIntakeResponse(detected_items=detected, raw_model_output=raw_output)


@router.post("/vision-intake/confirm", response_model=list[InventoryItemRead])
def confirm_vision_intake(payload: VisionIntakeConfirmRequest, db: Session = Depends(get_db)):
    """Bulk-creates inventory rows from a (user-reviewed/edited) vision
    intake result. Each item's `source` should already be "vision" per
    InventoryItemCreate's default, but callers may override it."""
    return _bulk_create_items(db, payload.items)


def _bulk_create_items(db: Session, items_in: list[InventoryItemCreate]) -> list[InventoryItem]:
    """Shared by both bulk-confirm endpoints (/vision-intake/confirm and
    /import/confirm) -- identical logic, kept in one place rather than
    duplicated across the two intake sources."""
    created = []
    for item_in in items_in:
        item = InventoryItem(**item_in.model_dump())
        db.add(item)
        created.append(item)
    db.commit()
    for item in created:
        db.refresh(item)
    return created


async def _receipt_text_extraction(db: Session, content: str) -> str:
    """Chat-based extraction for the text/PDF receipt-import paths --
    mirrors routers/recipes.py's _run_text_extraction (same 8000-char
    truncation convention, same response-unwrapping idiom), kept as its
    own function here rather than imported from recipes.py since the two
    routers shouldn't depend on each other for something this small."""
    prompt = RECEIPT_IMPORT_PROMPT.format(content=content[:8000])
    try:
        response = ollama_client.chat(db, [{"role": "user", "content": prompt}])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Ollama request failed: {exc}") from exc
    return response.get("message", {}).get("content", "") if isinstance(response, dict) else str(response)


@router.post("/import", response_model=InventoryImportResponse)
async def import_inventory(
    db: Session = Depends(get_db),
    file: UploadFile | None = None,
    text: str | None = Form(None),
):
    """Backlog B4.2 (author-requested 2026-08-01): accepts a receipt
    PHOTO, a receipt PDF, an uploaded plain-text file, OR pasted `text`
    -- exactly one -- and returns a PREVIEW of detected items, same
    preview-then-confirm discipline as every other AI-assisted intake in
    this app. Nothing is written to inventory here; the user reviews/
    edits client-side and POSTs the confirmed list to /import/confirm."""
    if file is None and not text:
        raise HTTPException(status_code=400, detail="Provide a file or pasted text.")

    if file is not None:
        raw_bytes = await file.read()
        content_type = file.content_type or ""
        filename = (file.filename or "").lower()

        if content_type.startswith("image/"):
            try:
                response = ollama_client.describe_image(
                    db, raw_bytes, RECEIPT_IMPORT_PROMPT.format(content="[see attached photo of a receipt]")
                )
            except Exception as exc:  # noqa: BLE001
                raise HTTPException(status_code=502, detail=f"Ollama vision request failed: {exc}") from exc
            raw_output = response.get("message", {}).get("content", "") if isinstance(response, dict) else str(response)
            source_type = "photo"
        elif content_type == "application/pdf" or filename.endswith(".pdf"):
            extracted = recipe_service.extract_pdf_text(raw_bytes)
            if not extracted.strip():
                # A common real failure mode for receipts specifically:
                # a phone "scan to PDF" app often produces an image-only
                # PDF with no text layer at all, which pypdf correctly
                # reports as empty rather than this app guessing at
                # content that isn't there. Told plainly rather than
                # silently sending near-nothing to the model and getting
                # back a near-empty/garbage preview.
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "This PDF has no extractable text -- it looks like a scanned image with no text "
                        "layer. Try uploading it as a photo/image instead."
                    ),
                )
            raw_output = await _receipt_text_extraction(db, extracted)
            source_type = "pdf"
        else:
            try:
                decoded = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(
                    status_code=400, detail="Could not read this file as text. Upload a photo, PDF, or plain-text file."
                )
            raw_output = await _receipt_text_extraction(db, decoded)
            source_type = "text"
    else:
        raw_output = await _receipt_text_extraction(db, text)
        source_type = "text"

    detected = inventory_service.parse_vision_response(raw_output)
    return InventoryImportResponse(detected_items=detected, raw_model_output=raw_output, source_type=source_type)


@router.post("/import/confirm", response_model=list[InventoryItemRead])
def confirm_inventory_import(payload: InventoryImportConfirmRequest, db: Session = Depends(get_db)):
    """Bulk-creates inventory rows from a (user-reviewed/edited) receipt/
    list import preview -- identical mechanics to /vision-intake/confirm,
    see _bulk_create_items above."""
    return _bulk_create_items(db, payload.items)


@router.post("/deduct", response_model=InventoryItemRead)
def deduct_inventory(payload: InventoryDeductRequest, db: Session = Depends(get_db)):
    """Name-based deduction -- the confirm step for a chat-proposed
    inventory_deduct action (Phase 7), or anywhere else a natural-
    language ingredient name needs to resolve to a row. 404 if nothing
    matches closely enough (see inventory_service.find_by_name)."""
    item = inventory_service.deduct_by_name(db, payload.ingredient_name, payload.quantity, payload.unit)
    if item is None:
        raise HTTPException(status_code=404, detail=f'No inventory item matching "{payload.ingredient_name}"')
    return item


@router.post("/update-by-name", response_model=InventoryItemRead)
def update_inventory_by_name(payload: InventoryUpdateByNameRequest, db: Session = Depends(get_db)):
    """Name-based partial update -- the confirm step for a chat-proposed
    inventory_update action (Phase 7), e.g. "mark the lentils as
    priority" or "we're out of milk" (set quantity to 0)."""
    updates = payload.model_dump(exclude={"ingredient_name"}, exclude_unset=True)
    item = inventory_service.update_by_name(db, payload.ingredient_name, **updates)
    if item is None:
        raise HTTPException(status_code=404, detail=f'No inventory item matching "{payload.ingredient_name}"')
    return item


@router.get("/{item_id}", response_model=InventoryItemRead)
def get_inventory_item(item_id: int, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    return item


@router.post("", response_model=InventoryItemRead, status_code=201)
def create_inventory_item(payload: InventoryItemCreate, db: Session = Depends(get_db)):
    item = InventoryItem(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


@router.patch("/{item_id}", response_model=InventoryItemRead)
def update_inventory_item(item_id: int, payload: InventoryItemUpdate, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    db.commit()
    db.refresh(item)
    return item


@router.delete("/{item_id}", status_code=204)
def delete_inventory_item(item_id: int, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    db.delete(item)
    db.commit()
    return None
