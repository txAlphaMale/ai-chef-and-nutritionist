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
/order-import, /order-import/profiles, /deduct, /update-by-name) are
declared before the dynamic /{item_id} routes to avoid being swallowed
by them.

A third intake source lives here too (backlog B10.3, 2026-08-01): a
generic order-history CSV/XLSX importer (/order-import,
/order-import/profiles). Unlike the receipt/list import above, this one
is pure deterministic parsing with no AI call -- see
order_import_service.py's module docstring for why no AI is needed and
why no pre-built "Walmart" column profile ships. It still lands in the
same VisionDetectedItem preview shape and reuses /import/confirm's
bulk-create, per the backlog's explicit "same review screen, not a
separate UI" guidance.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import InventoryItem, OrderImportProfile
from app.schemas.inventory import (
    BarcodeLookupResponse,
    ColumnMapping,
    ExpiringDigestResponse,
    InventoryDeductRequest,
    InventoryImportConfirmRequest,
    InventoryImportResponse,
    InventoryItemCreate,
    InventoryItemRead,
    InventoryItemUpdate,
    InventoryUpdateByNameRequest,
    OrderImportPreviewResponse,
    OrderImportProfileCreate,
    OrderImportProfileRead,
    OrderImportProfileUpdate,
    PrioritySuggestion,
    RecallStatusResponse,
    ShelfLifeSuggestionResponse,
    VisionIntakeConfirmRequest,
    VisionIntakeResponse,
)
from app.schemas.jobs import JobEnqueuedResponse
from app.services import (
    food_data_service,
    foodkeeper_service,
    inventory_service,
    job_queue,
    meal_plan_service,
    ollama_client,
    order_import_service,
    recall_service,
    recipe_service,
)

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
pasted. Today's date is {today}. Here is the content to parse:

{content}

Extract every actual food/grocery line item that was purchased or listed. \
SKIP anything that is not itself a purchasable item: subtotals, tax, \
total, tender/change amounts, coupons, loyalty/rewards messages, store \
name/address/phone, and cashier/register/date-time header lines.

This is a FOOD inventory, not a general purchase log -- a real store \
receipt (Walmart, Target, a grocery store, etc.) very often mixes food in \
with household and personal items on the SAME receipt. You MUST also SKIP \
every non-food line item, including but not limited to: household/\
cleaning supplies (paper towels, lint rollers, laundry detergent, trash \
bags), personal care/hygiene/beauty products (soap, cotton swabs, \
floss, shampoo, conditioner, lotion), over-the-counter medication/\
vitamins/supplements (probiotics, pain relievers, allergy pills), pet \
food/litter/supplies, baby items that aren't food (diapers, wipes), \
clothing, electronics, toys, and office/school supplies. When genuinely \
unsure whether something is food (e.g. a pet treat, a protein bar that \
could read as either food or supplement), include it but say so plainly \
in "confidence_note" rather than silently guessing either way. It is \
far better to skip a real food item by mistake (the user can add it \
manually) than to fill a food-inventory list with lint rollers and cat \
litter.

Receipt item names are frequently abbreviated by point-of-sale systems \
(e.g. "ORG BANANA", "GV 2% MLK GAL"). Expand these into a normal, \
readable food name when you are reasonably confident what it means (e.g. \
"Organic bananas", "Great Value 2% milk, 1 gallon"). If an abbreviation \
is genuinely ambiguous, keep your best-guess name but say so in \
"confidence_note" -- never silently invent a specific brand or variety \
you are not reasonably sure of.

Look for a single order/transaction date printed once near the top of the \
receipt or order confirmation (e.g. "Jul 30, 2026 order", "Order Date:", \
a header timestamp) -- that ONE date applies to every item in this list, \
since a receipt records a single purchase event. If the printed date has \
no year (common on register receipts), assume the most recent occurrence \
of that month/day on or before today's date given above, not a future \
date. If genuinely no date is printed anywhere, use null -- never guess \
a date from nothing.

Getting "estimated_quantity" and "unit" right requires distinguishing TWO \
different numbers that often both appear on the same line, and this is a \
common mistake to avoid: (1) how many of that item/package were actually \
PURCHASED -- usually shown as an explicit "Qty"/quantity next to the \
price (default to 1 if the source shows no explicit purchase quantity for \
a line), and (2) a size/count descriptor that is part of the PRODUCT'S \
OWN NAME, describing what's inside a single package (e.g. "6 Count", "24 \
oz", "4 Pack", "300 Count"). "estimated_quantity" is ALWAYS the first one \
(how many were purchased) -- NEVER the second. The same "never invent a \
conversion" principle applies here as everywhere else in this app: a \
"6 Count" hot-dog package is 1 purchased item, not 6, exactly like a \
"500 g" bag is 1 item, not 500. If the product's own name states a size/\
count descriptor, put that in "unit" instead (e.g. "8 oz bag", "14 oz \
can", "300 count", "4-pack of 8 fl oz bottles") so the size information \
is captured, not discarded -- just never let it overwrite the purchased \
quantity. When nothing more specific is available, "count" is a \
reasonable default unit rather than leaving it null.

If a per-line price is printed (the item's own price, not a subtotal/tax/ \
total), extract it into "unit_price" as a plain number with no currency \
symbol -- this field name means the price paid for that line's WHOLE \
purchased quantity as printed (e.g. "Qty 2 ... $6.96" means \
"unit_price": 6.96, covering both), not a re-derived per-single-unit \
price. Use null if no price is printed for that line.

Respond with ONLY a JSON array (no other text, no markdown fences) -- if \
truly nothing on this receipt/list is food, respond with an empty array \
`[]`, never prose explaining why. Each element of the array is an object \
with these keys:
- "name": string, the food item's name
- "estimated_quantity": number or null -- see the purchased-quantity-vs-\
package-size guidance above
- "unit": string or null (e.g. "count", "lbs", "oz", "gallon", "8 oz \
bag") -- see the guidance above
- "category": one of "pantry", "fridge", "freezer", "produce", "spice", \
"other"
- "estimated_expiration_days": integer number of days from today this \
item is typically still good for, or null if you can't estimate -- this \
is always a category-based estimate, never something printed on the \
source
- "purchased_date": string "YYYY-MM-DD" or null -- see the date guidance \
above; the SAME value for every item on one receipt
- "unit_price": number or null -- see the price guidance above
- "confidence_note": a short string noting any uncertainty (e.g. an \
ambiguous abbreviation, or "included despite being borderline food"), or \
null
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


@router.get("/expiring-digest", response_model=ExpiringDigestResponse)
def expiring_digest(within_days: int = 7, db: Session = Depends(get_db)):
    """Backlog B4.4 (via B10.2) -- backs the persistent app-shell banner
    (ExpiringDigestBanner.jsx), not just the Inventory page's own passive
    display, per the backlog's explicit "reach out" framing."""
    return inventory_service.get_expiring_digest(db, within_days=within_days)


@router.get("/barcode-lookup", response_model=BarcodeLookupResponse)
def barcode_lookup(barcode: str):
    """Backlog B4.1 (author-requested 2026-08-01): looks up a scanned
    barcode against Open Food Facts and returns a prefilled item preview.
    Deliberately a plain sync `def`, not a job_queue job -- a single OFF
    HTTP lookup is one fast round trip (REQUEST_TIMEOUT_SECONDS=8 in
    food_data_service.py), nothing like the tens-of-seconds Ollama calls
    B11.1's job-queue rule exists for, and FastAPI already runs plain
    `def` handlers in a threadpool so this doesn't block the event loop
    either. See BarcodeLookupResponse's docstring for why there's no
    separate confirm endpoint: a barcode scan is always exactly one item,
    so the frontend (BarcodeScanner.jsx) lets the user review/edit this
    preview, then POSTs a normal InventoryItemCreate (source="barcode")
    straight to POST /api/inventory."""
    barcode = (barcode or "").strip()
    if not barcode:
        raise HTTPException(status_code=400, detail="barcode is required")

    product = food_data_service.get_off_product(barcode)
    if product is None:
        return BarcodeLookupResponse(barcode=barcode, found=False)

    name = (product.get("product_name") or product.get("generic_name") or "").strip() or None
    brand = (product.get("brands") or "").strip() or None
    quantity_text = (product.get("quantity") or "").strip() or None
    image_url = product.get("image_front_url") or product.get("image_url") or None
    category = meal_plan_service.guess_grocery_category(name) if name else None

    return BarcodeLookupResponse(
        barcode=barcode,
        found=True,
        name=name,
        brand=brand,
        quantity_text=quantity_text,
        category=category or "other",
        image_url=image_url,
        confidence_note=None if name else "Found on Open Food Facts, but that record has no product name -- fill it in manually.",
    )


@router.get("/shelf-life-suggestion", response_model=ShelfLifeSuggestionResponse)
def shelf_life_suggestion(name: str, category: str = "pantry", purchased_date: str | None = None):
    """Backlog B4.3 (2026-08-01): auto-suggests an expiration date from
    the shipped USDA FoodKeeper catalog (foodkeeper_service) so the
    household doesn't have to know or look up a shelf life themselves --
    the frontend calls this as the item name is typed on the inventory
    add form and, when a suggestion comes back, prefills the (still
    freely editable) expiration-date field. Deliberately a plain sync
    `def` with no DB access at all -- the FoodKeeper catalog is a fixed,
    in-memory-cached CSV (see foodkeeper_service._load_entries), so this
    is a pure, fast lookup, not something that needs job_queue's
    background-job treatment.

    `purchased_date` is accepted as a plain `YYYY-MM-DD` string (not a
    typed `date`) since the frontend calls this from a partially-filled
    form where the purchased-date field may be empty or not yet a valid
    date -- an unparseable or missing value just falls back to today,
    same as foodkeeper_service.suggest_expiration_date's own default."""
    name = (name or "").strip()
    if not name:
        return ShelfLifeSuggestionResponse(found=False)
    parsed_purchased_date = None
    if purchased_date:
        try:
            from datetime import date as _date

            parsed_purchased_date = _date.fromisoformat(purchased_date)
        except ValueError:
            pass
    result = foodkeeper_service.suggest_expiration_date(name, category, parsed_purchased_date)
    if result is None:
        return ShelfLifeSuggestionResponse(found=False)
    return ShelfLifeSuggestionResponse(found=True, **result)


@router.get("/recalls", response_model=RecallStatusResponse)
def get_recalls(db: Session = Depends(get_db)):
    """Backlog B3.3 -- fast, DB-only read of currently active (not
    dismissed) recall alerts, backing RecallBanner.jsx. Deliberately
    never makes a live FSIS/openFDA call itself (that's slow and
    network-bound -- see recall_service.check_inventory_for_recalls's
    docstring); instead, when a check is due (recall_service.is_check_due),
    it enqueues one in the background via the same job queue every other
    external-API-consuming feature uses, and returns immediately with
    whatever's already cached. `check_due` in the response lets the
    frontend know a fresh check was just kicked off, without blocking
    this request on it."""
    check_due = recall_service.is_check_due(db)
    if check_due:
        job_queue.enqueue("recall_check", "Recall check", _recall_check_job, dedup_key="recall_check")
    state = recall_service.get_check_state(db)
    return RecallStatusResponse(
        alerts=recall_service.list_active_alerts(db),
        last_checked_at=state.last_checked_at,
        check_due=check_due,
    )


def _recall_check_job() -> dict:
    db = SessionLocal()
    try:
        return recall_service.check_inventory_for_recalls(db)
    finally:
        db.close()


@router.post("/recalls/check", response_model=JobEnqueuedResponse, status_code=202)
def trigger_recall_check(db: Session = Depends(get_db)):
    """The explicit "Check for recalls now" button -- bypasses the
    throttle interval (force=True) since the household asked for this
    specifically, unlike get_recalls's automatic background trigger."""

    def _forced_job() -> dict:
        job_db = SessionLocal()
        try:
            return recall_service.check_inventory_for_recalls(job_db, force=True)
        finally:
            job_db.close()

    job_id, created = job_queue.enqueue("recall_check", "Recall check", _forced_job, dedup_key="recall_check")
    return JobEnqueuedResponse(job_id=job_id, created=created)


@router.post("/recalls/{alert_id}/dismiss", response_model=RecallStatusResponse)
def dismiss_recall_alert(alert_id: int, db: Session = Depends(get_db)):
    alert = recall_service.dismiss_alert(db, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="Recall alert not found")
    state = recall_service.get_check_state(db)
    return RecallStatusResponse(
        alerts=recall_service.list_active_alerts(db),
        last_checked_at=state.last_checked_at,
        check_due=recall_service.is_check_due(db),
    )


@router.post("/vision-intake", response_model=JobEnqueuedResponse, status_code=202)
async def vision_intake(file: UploadFile):
    """Backlog B11.1 (2026-08-01): analyzes an uploaded photo with the
    configured Ollama vision model and returns a PREVIEW of detected
    items -- nothing is written to inventory here. The user reviews/
    edits the preview client-side, then POSTs the confirmed list to
    /vision-intake/confirm.

    Enqueues a background job instead of blocking on the vision model
    (which can run tens of seconds to a couple of minutes on this app's
    target hardware) -- returns immediately with a job_id the frontend
    polls via GET /api/jobs/{job_id}. See job_queue.py's module docstring
    for why: this endpoint used to be `async def` calling the Ollama
    client's synchronous, blocking HTTP client directly, which froze the
    entire app (not just this request) for the whole duration of the
    call. The file is read here (UploadFile.read() needs the request's
    own async context) before handing the raw bytes to the job body,
    which opens its own DB session -- never the request-scoped one,
    which isn't safe to share across threads."""
    image_bytes = await file.read()

    def _run() -> dict:
        db = SessionLocal()
        try:
            response = ollama_client.describe_image(db, image_bytes, VISION_PROMPT)
            raw_output = ollama_client.extract_content(response)
            detected = inventory_service.parse_vision_response(raw_output)
            return VisionIntakeResponse(detected_items=detected, raw_model_output=raw_output).model_dump()
        finally:
            db.close()

    job_id, created = job_queue.enqueue("vision_intake", "Vision intake (pantry photo)", _run)
    return JobEnqueuedResponse(job_id=job_id, created=created)


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


def _receipt_text_extraction(db: Session, content: str) -> str:
    """Chat-based extraction for the text/PDF receipt-import paths --
    mirrors routers/recipes.py's _run_text_extraction (same content-
    budget convention, same response-unwrapping idiom), kept as its own
    function here rather than imported from recipes.py since the two
    routers shouldn't depend on each other for something this small.

    Bug fix (2026-08-02, author-reported follow-up): this used to hard-
    cap content at a flat `content[:8000]` regardless of the actual
    configured context window -- now scales via
    ollama_client.content_char_budget (see its docstring). A receipt can
    legitimately have dozens of line items, each needing real JSON
    response space (name/quantity/unit/category/dates/price/confidence
    per item), so this uses a generous response reserve.

    Plain sync (no longer `async def`, backlog B11.1, 2026-08-01) -- every
    caller now runs this from inside a job body on the background worker
    thread, never from a request handler awaiting it directly, so there's
    nothing left to await here."""
    budget = ollama_client.content_char_budget(
        db, prompt_overhead_chars=len(RECEIPT_IMPORT_PROMPT), response_reserve_tokens=3000
    )
    print(
        f"[inventory._receipt_text_extraction] extracted_content_chars={len(content)} "
        f"budget_chars={budget} truncated={len(content) > budget}",
        flush=True,
    )
    prompt = RECEIPT_IMPORT_PROMPT.format(content=content[:budget], today=date.today().isoformat())
    response = ollama_client.chat(db, [{"role": "user", "content": prompt}])
    raw_output = ollama_client.extract_content(response)
    print(f"[inventory._receipt_text_extraction] raw_output_chars={len(raw_output)}", flush=True)
    return raw_output


def _inventory_import_job(source_type: str, extractor) -> dict:
    """Shared job body for every import_inventory branch below --
    `extractor` is a zero-arg callable (a closure over whatever raw bytes/
    text that branch already read) that returns the model's raw text
    output; this opens its own DB session (background worker thread,
    never the request's own session), parses it the same way regardless
    of which branch produced it, and returns the exact JSON shape
    InventoryImportResponse used to return synchronously -- so it can be
    handed straight to InventoryImportResponse(**result) once the job
    finishes without the frontend needing to know anything changed."""
    db = SessionLocal()
    try:
        raw_output = extractor(db)
        detected = inventory_service.parse_vision_response(raw_output)
        print(
            f"[inventory._inventory_import_job] source_type={source_type!r} "
            f"raw_output_chars={len(raw_output)} detected_items={len(detected)}",
            flush=True,
        )
        return InventoryImportResponse(detected_items=detected, raw_model_output=raw_output, source_type=source_type).model_dump()
    finally:
        db.close()


@router.post("/import", response_model=JobEnqueuedResponse, status_code=202)
async def import_inventory(
    file: UploadFile | None = None,
    text: str | None = Form(None),
):
    """Backlog B4.2 (author-requested 2026-08-01): accepts a receipt
    PHOTO, a receipt PDF, an uploaded plain-text file, OR pasted `text`
    -- exactly one -- and returns a PREVIEW of detected items, same
    preview-then-confirm discipline as every other AI-assisted intake in
    this app. Nothing is written to inventory here; the user reviews/
    edits client-side and POSTs the confirmed list to /import/confirm.

    Backlog B11.1 (2026-08-01): enqueues a background job for the
    Ollama-calling part instead of blocking on it -- see job_queue.py's
    module docstring and vision_intake's docstring above for why. Fast,
    Ollama-independent validation (missing input, an image-only PDF with
    no text layer, an undecodable file) still happens HERE, synchronously,
    so a doomed request fails immediately with a clear 400 rather than
    occupying a queue slot behind other work only to fail once it's
    finally run."""
    if file is None and not text:
        raise HTTPException(status_code=400, detail="Provide a file or pasted text.")

    if file is not None:
        raw_bytes = await file.read()
        content_type = file.content_type or ""
        filename = (file.filename or "").lower()

        if content_type.startswith("image/"):
            prompt = RECEIPT_IMPORT_PROMPT.format(
                content="[see attached photo of a receipt]", today=date.today().isoformat()
            )
            source_type = "photo"

            def extractor(db: Session) -> str:
                response = ollama_client.describe_image(db, raw_bytes, prompt)
                return ollama_client.extract_content(response)

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
            source_type = "pdf"

            def extractor(db: Session) -> str:
                return _receipt_text_extraction(db, extracted)

        else:
            try:
                decoded = raw_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(
                    status_code=400, detail="Could not read this file as text. Upload a photo, PDF, or plain-text file."
                )
            source_type = "text"

            def extractor(db: Session) -> str:
                return _receipt_text_extraction(db, decoded)

    else:
        source_type = "text"

        def extractor(db: Session) -> str:
            return _receipt_text_extraction(db, text)

    job_id, created = job_queue.enqueue(
        "inventory_import", "Receipt/list import", lambda: _inventory_import_job(source_type, extractor)
    )
    return JobEnqueuedResponse(job_id=job_id, created=created)


@router.post("/import/confirm", response_model=list[InventoryItemRead])
def confirm_inventory_import(payload: InventoryImportConfirmRequest, db: Session = Depends(get_db)):
    """Bulk-creates inventory rows from a (user-reviewed/edited) receipt/
    list import preview -- identical mechanics to /vision-intake/confirm,
    see _bulk_create_items above."""
    return _bulk_create_items(db, payload.items)


@router.get("/order-import/profiles", response_model=list[OrderImportProfileRead])
def list_order_import_profiles(db: Session = Depends(get_db)):
    return db.query(OrderImportProfile).order_by(OrderImportProfile.name).all()


@router.post("/order-import/profiles", response_model=OrderImportProfileRead, status_code=201)
def create_order_import_profile(payload: OrderImportProfileCreate, db: Session = Depends(get_db)):
    if db.query(OrderImportProfile).filter(OrderImportProfile.name == payload.name).first():
        raise HTTPException(status_code=409, detail=f'A profile named "{payload.name}" already exists.')
    profile = OrderImportProfile(**payload.model_dump())
    db.add(profile)
    db.commit()
    db.refresh(profile)
    return profile


@router.patch("/order-import/profiles/{profile_id}", response_model=OrderImportProfileRead)
def update_order_import_profile(profile_id: int, payload: OrderImportProfileUpdate, db: Session = Depends(get_db)):
    profile = db.get(OrderImportProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Import profile not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    db.commit()
    db.refresh(profile)
    return profile


@router.delete("/order-import/profiles/{profile_id}", status_code=204)
def delete_order_import_profile(profile_id: int, db: Session = Depends(get_db)):
    profile = db.get(OrderImportProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Import profile not found")
    db.delete(profile)
    db.commit()
    return None


@router.post("/order-import", response_model=OrderImportPreviewResponse)
async def order_import(
    file: UploadFile,
    db: Session = Depends(get_db),
    name_column: str | None = Form(None),
    quantity_column: str | None = Form(None),
    unit_column: str | None = Form(None),
    price_column: str | None = Form(None),
    date_column: str | None = Form(None),
    profile_id: int | None = Form(None),
):
    """Backlog B10.3: parses an uploaded order-history CSV/XLSX and
    returns a PREVIEW, same discipline as every other intake source --
    nothing is written to inventory here. No AI call is involved (see
    order_import_service.py) -- this is deterministic column parsing.

    Column-mapping precedence: an explicit `*_column` form field wins if
    given; else a saved `profile_id`'s mapping is used; else this call
    returns the module's best-guess `suggested_mapping` and applies THAT
    (so a first-time upload still produces a usable preview, not an
    empty one) -- the frontend shows both the applied mapping and the
    detected items together, so the user can see the guess and re-POST
    with corrected `*_column` fields (optionally saving a profile) if
    it's wrong. Re-uploading the same file each time a mapping is
    adjusted is deliberate and consistent with how every other
    preview-then-confirm flow in this app already works (nothing is
    cached server-side between preview attempts)."""
    raw_bytes = await file.read()
    try:
        headers, rows = order_import_service.parse_tabular_file(raw_bytes, file.filename or "", file.content_type or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not headers:
        raise HTTPException(status_code=400, detail="Could not find any columns in this file -- is it empty?")

    suggested = order_import_service.guess_column_mapping(headers)

    profile_mapping = None
    if profile_id is not None:
        profile = db.get(OrderImportProfile, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="Import profile not found")
        profile_mapping = ColumnMapping(
            name_column=profile.name_column,
            quantity_column=profile.quantity_column,
            unit_column=profile.unit_column,
            price_column=profile.price_column,
            date_column=profile.date_column,
        )

    mapping_used = ColumnMapping(
        name_column=name_column or (profile_mapping.name_column if profile_mapping else suggested.name_column),
        quantity_column=quantity_column
        or (profile_mapping.quantity_column if profile_mapping else suggested.quantity_column),
        unit_column=unit_column or (profile_mapping.unit_column if profile_mapping else suggested.unit_column),
        price_column=price_column or (profile_mapping.price_column if profile_mapping else suggested.price_column),
        date_column=date_column or (profile_mapping.date_column if profile_mapping else suggested.date_column),
    )

    detected, skipped = order_import_service.apply_mapping(headers, rows, mapping_used)
    return OrderImportPreviewResponse(
        headers=headers,
        suggested_mapping=suggested,
        mapping_used=mapping_used,
        detected_items=detected,
        row_count=len(rows),
        skipped_row_count=skipped,
    )


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
