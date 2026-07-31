"""Pantry/fridge/freezer/produce/spice inventory: CRUD, urgency-ranked
suggestions for the meal planner, and AI vision photo intake.

Route ordering matters here -- FastAPI matches path operations in
declaration order, so the static paths (/priority-suggestions,
/vision-intake, /vision-intake/confirm) are declared before the
dynamic /{item_id} routes to avoid being swallowed by them.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import InventoryItem
from app.schemas.inventory import (
    InventoryItemCreate,
    InventoryItemRead,
    InventoryItemUpdate,
    PrioritySuggestion,
    VisionIntakeConfirmRequest,
    VisionIntakeResponse,
)
from app.services import inventory_service, ollama_client

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
    created = []
    for item_in in payload.items:
        item = InventoryItem(**item_in.model_dump())
        db.add(item)
        created.append(item)
    db.commit()
    for item in created:
        db.refresh(item)
    return created


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
