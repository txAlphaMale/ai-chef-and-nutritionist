"""Pantry/fridge/freezer/produce/spice inventory: CRUD, urgency-ranked
suggestions for the meal planner, and three intake paths.

The intake paths are kept separate rather than merged into one endpoint
because they answer different questions and need different handling:

- AI vision photo intake -- what is CURRENTLY in the pantry/fridge.
- AI receipt/list import -- what was just PURCHASED, from a receipt
  photo/PDF or a plain-text/file list. Its prompt has to skip
  subtotal/tax/tender lines and expand POS abbreviations, which a pantry
  photo prompt has no reason to do.
- Order-history CSV/XLSX import (B10.3) -- pure deterministic parsing,
  no AI call. See order_import_service.py for why no AI is needed and
  why no pre-built "Walmart" column profile ships.

All three land in the same preview-then-confirm shape
(VisionDetectedItem/InventoryItemCreate) and share /import/confirm's
bulk-create, so there is one review screen rather than three.

Route ordering matters -- FastAPI matches path operations in declaration
order, so the static paths (/priority-suggestions, /vision-intake,
/vision-intake/confirm, /import, /import/confirm, /order-import,
/order-import/profiles, /deduct, /update-by-name) are declared before
the dynamic /{item_id} routes to avoid being swallowed by them.
"""

from __future__ import annotations

import contextlib
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import InventoryItem, OrderImportProfile
from app.schemas.ai_extraction import ExtractedInventoryList, schema_of
from app.schemas.inventory import (
    BarcodeLookupResponse,
    ColumnMapping,
    ExpiringDigestResponse,
    IngredientAliasCreate,
    IngredientAliasRead,
    IngredientMatchCandidate,
    IngredientResolutionResponse,
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
    ingredient_resolution_service,
    inventory_service,
    job_queue,
    log_service,
    meal_plan_service,
    ollama_client,
    order_import_service,
    package_parsing,
    recall_service,
    recipe_service,
)

router = APIRouter(prefix="/api/inventory", tags=["inventory"])

VISION_PROMPT = """\
Look at this photo of food items and identify each distinct item you can see.
Respond with ONLY a JSON object of the form {"items": [ ... ]}, where each \
element of "items" is an object with these keys:
- "name": string, the food item's name
- "estimated_quantity": number or null
- "unit": string or null (e.g. "count", "lbs", "oz", "cans")
- "category": one of "pantry", "fridge", "freezer", "produce", "spice", "other"
- "estimated_expiration_days": integer number of days from today this item is \
likely still good for, or null if you can't estimate
- "confidence_note": a short string noting any uncertainty, or null

Example: {"items": [{"name": "milk", "estimated_quantity": 1, "unit": "gallon", \
"category": "fridge", "estimated_expiration_days": 7, "confidence_note": null}]}
"""

# Shares VisionDetectedItem's output shape with VISION_PROMPT above, so
# inventory_service.parse_vision_response works unchanged for both -- only
# the prompt text differs.
#
# Placeholders are filled with plain str.replace(), not .format(). That is
# load-bearing: this prompt is a DB-backed, GUI-editable SystemPrompt, and
# .format() raises KeyError on any stray literal brace a household happens
# to type. A worked example containing JSON is exactly the kind of text
# that would trip it.
#
# Two things about the prompt's shape are deliberate and worth keeping if
# it is ever edited again. Numbered rules rather than prose: the previous
# prose version grew long enough that the model took its own "nothing
# found" escape hatch instantly (done_reason='stop', eval_count=2) on a
# perfectly valid receipt -- length itself was the failure. And one
# concrete worked example mapping a real source line to its exact output
# object, which is a stronger format signal to a model than another
# paragraph of description.
RECEIPT_IMPORT_PROMPT = """\
Task: extract every human-food/grocery item purchased on this receipt, PDF \
order, or list. Today's date: {today}.

SOURCE:
{content}

RULES:
1. One JSON object per purchased line. If the same product name appears on \
more than one line, each line is still a separate purchase -- never merge \
them into one object.
2. Skip these line types entirely: subtotal, tax, total, tender/change, \
coupon/loyalty, and store/cashier/address header lines.
3. Skip non-food purchases: household/cleaning supplies, personal care/\
beauty products, medicine/vitamins/supplements, pet food/litter/supplies, \
non-food baby items (diapers, wipes), clothing, electronics, toys, office \
supplies. If genuinely unsure whether something is food, include it and \
explain why in "confidence_note" rather than guessing either way.
4. Expand abbreviated point-of-sale names into normal readable names when \
confident (e.g. "ORG BANANA" -> "Organic bananas"); note real ambiguity in \
"confidence_note" instead of inventing a brand or variety you aren't sure of.
5. "estimated_quantity" is how many were purchased (e.g. an explicit \
"Qty 2"), default 1 if none is shown. NEVER use a size/count descriptor \
that's part of the product's own name (e.g. "6 Count", "24 oz", "4 Pack") \
as the quantity -- put that descriptor in "unit" instead (e.g. "8 oz bag").
6. "unit_price" is the line's own printed price for its whole purchased \
quantity as printed, or null if none is printed.
7. "purchased_date" is the single order/transaction date printed once near \
the top (e.g. "Jul 30, 2026 order"), formatted "YYYY-MM-DD", the SAME value \
for every item on this receipt. If the printed date has no year, use the \
most recent past occurrence of that month/day relative to today. Use null \
only if no date is printed anywhere.

EXAMPLE:
Source line: "Progresso Gluten Free Chicken Soup, 14 oz. Qty 2 $6.96" (order \
dated "Jul 30, 2026 order")
Correct output object: {"name": "Progresso Gluten Free Chicken Soup", \
"estimated_quantity": 2, "unit": "14 oz can", "category": "pantry", \
"estimated_expiration_days": 365, "purchased_date": "2026-07-30", \
"unit_price": 6.96, "confidence_note": null}

OUTPUT FORMAT: Respond with ONLY a JSON object of the form {"items": [ ... ]} \
-- no other text, no markdown fences. An empty "items" array only if this \
source genuinely contains zero food/grocery items. Each element of "items" is \
an object with exactly these keys:
{"name": string, "estimated_quantity": number or null, "unit": string or \
null, "category": one of "pantry"/"fridge"/"freezer"/"produce"/"spice"/\
"other", "estimated_expiration_days": integer or null, "purchased_date": \
"YYYY-MM-DD" or null, "unit_price": number or null, "confidence_note": \
string or null}
"""


def get_receipt_import_prompt(db: Session) -> str:
    """Backlog B16.1 -- DB-override-with-fallback for RECEIPT_IMPORT_PROMPT,
    same pattern as recipe_service.get_recipe_import_prompt. Filled in
    per-call via str.replace("{content}", ...).replace("{today}", ...) at
    every call site below -- never `.format()`, see this section's module
    comment for why."""
    return ollama_client.get_active_prompt(db, "receipt_import") or RECEIPT_IMPORT_PROMPT


def get_vision_prompt(db: Session) -> str:
    """Backlog B16.1 -- DB-override-with-fallback for VISION_PROMPT (the
    pantry/fridge photo-intake prompt, no placeholders to fill)."""
    return ollama_client.get_active_prompt(db, "vision_intake") or VISION_PROMPT


# Constrained-decoding schema for every inventory-extraction path: pantry
# photo, receipt photo, receipt PDF, and a pasted item list. All four
# produce the same shape, so they share one schema -- only the prompt
# differs. See app/schemas/ai_extraction.py for why this replaced
# prompt-only "respond with ONLY a JSON array" instructions.
INVENTORY_SCHEMA = schema_of(ExtractedInventoryList)

# A long grocery receipt is the biggest list this app extracts -- 30+
# objects, each with eight fields. Reserved explicitly so the answer can
# never be squeezed out of the context window by the source text.
INVENTORY_RESPONSE_TOKENS = 3000

# Sampling is now ollama_client.EXTRACTION_OPTIONS (temperature 0), applied
# by chat_json/describe_image_json.
#
# What was here before: temperature 0.7, top_p 0.8, top_k 20, and
# presence_penalty 1.5 -- Qwen's published GENERAL CHAT parameters, applied
# to a deterministic extraction job. The presence penalty was the damaging
# one. It penalises tokens that have already appeared, and a receipt
# extraction response is deliberately repetitive: the same eight JSON keys,
# the same category strings, the same units, on every single element. A
# strong presence penalty therefore pushes the model to stop emitting that
# repeated structure partway down a long list -- which is exactly the
# "captured 4 of 8 real items from a 15-line receipt" symptom it was added
# while trying to fix. Ollama's own structured-output guidance is
# temperature 0 and no penalties.


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
    return [PrioritySuggestion(item=item, urgency_score=score, reasons=reasons) for item, score, reasons in scored]


@router.get("/expiring-digest", response_model=ExpiringDigestResponse)
def expiring_digest(within_days: int = 7, db: Session = Depends(get_db)):
    """Backlog B4.4 (via B10.2) -- backs the persistent app-shell banner
    (ExpiringDigestBanner.jsx), not just the Inventory page's own passive
    display, per the backlog's explicit "reach out" framing."""
    return inventory_service.get_expiring_digest(db, within_days=within_days)


@router.get("/barcode-lookup", response_model=BarcodeLookupResponse)
def barcode_lookup(barcode: str):
    """Backlog B4.1: looks up a scanned
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

    # See BarcodeLookupResponse's docstring for why quantity_text is
    # parsed rather than left display-only.
    estimated_quantity = 1.0
    unit = "count"
    package_quantity = None
    package_count = None
    package_descriptor = None
    parsed_package = package_parsing.parse_package_text(quantity_text)
    if parsed_package is not None:
        unit = parsed_package.unit
        package_quantity = parsed_package.package_quantity
        package_count = parsed_package.package_count
        package_descriptor = parsed_package.package_descriptor
        estimated_quantity = package_count * package_quantity

    return BarcodeLookupResponse(
        barcode=barcode,
        found=True,
        name=name,
        brand=brand,
        quantity_text=quantity_text,
        estimated_quantity=estimated_quantity,
        unit=unit,
        package_quantity=package_quantity,
        package_count=package_count,
        package_descriptor=package_descriptor,
        category=category or "other",
        image_url=image_url,
        nova_group=food_data_service.parse_off_nova_group(product),
        nutriscore_grade=food_data_service.parse_off_nutriscore_grade(product),
        confidence_note=None
        if name
        else "Found on Open Food Facts, but that record has no product name -- fill it in manually.",
    )


@router.get("/shelf-life-suggestion", response_model=ShelfLifeSuggestionResponse)
def shelf_life_suggestion(name: str, category: str = "pantry", purchased_date: str | None = None):
    """Backlog B4.3: auto-suggests an expiration date from
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
    """Analyzes an uploaded photo with the
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
            raw_output = ollama_client.describe_image_json(
                db,
                image_bytes,
                get_vision_prompt(db),
                schema=INVENTORY_SCHEMA,
                response_tokens=INVENTORY_RESPONSE_TOKENS,
            )
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

    Content is capped by ollama_client.content_char_budget (see its
    docstring), which scales with the configured context window rather
    than a flat character count. A receipt can legitimately have dozens
    of line items, each needing real JSON response space, so this uses a
    generous response reserve.

    Plain sync, not `async def` -- every caller runs this from inside a
    job body on the background worker thread, never from a request
    handler awaiting it, so there is nothing to await."""
    prompt_template = get_receipt_import_prompt(db)
    budget = ollama_client.content_char_budget(
        db, prompt_overhead_chars=len(prompt_template), response_reserve_tokens=INVENTORY_RESPONSE_TOKENS
    )
    log_service.debug(
        "inventory.receipt_import",
        f"extracted_content_chars={len(content)} budget_chars={budget} truncated={len(content) > budget}",
    )
    prompt = prompt_template.replace("{content}", content[:budget]).replace("{today}", date.today().isoformat())
    raw_output = ollama_client.chat_json(
        db,
        [{"role": "user", "content": prompt}],
        schema=INVENTORY_SCHEMA,
        response_tokens=INVENTORY_RESPONSE_TOKENS,
    )
    log_service.debug("inventory.receipt_import", f"raw_output_chars={len(raw_output)}")
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
        log_service.info(
            "inventory.import",
            f"source_type={source_type!r} raw_output_chars={len(raw_output)} detected_items={len(detected)}",
        )
        if raw_output and not detected:
            # A non-empty response that parses to zero items looks
            # identical in the UI to "this receipt genuinely had no food
            # on it". ollama_client logs the first 300 chars; the END is
            # what separates the two shapes that matter -- cut off
            # mid-array by the context/num_predict limit (no closing
            # "]", done_reason "length") versus complete JSON wrapped in
            # prose. Logged only on the zero-items path, so a normal
            # import adds no noise.
            head = raw_output[:500].replace("\n", " ")
            tail = raw_output[-300:].replace("\n", " ")
            log_service.error(
                "inventory.import",
                f"ZERO ITEMS from a non-empty response -- head={head!r} tail={tail!r}",
            )
        return InventoryImportResponse(
            detected_items=detected, raw_model_output=raw_output, source_type=source_type
        ).model_dump()
    finally:
        db.close()


@router.post("/import", response_model=JobEnqueuedResponse, status_code=202)
async def import_inventory(
    file: UploadFile | None = None,
    text: str | None = Form(None),
):
    """Backlog B4.2: accepts a receipt
    PHOTO, a receipt PDF, an uploaded plain-text file, OR pasted `text`
    -- exactly one -- and returns a PREVIEW of detected items, same
    preview-then-confirm discipline as every other AI-assisted intake in
    this app. Nothing is written to inventory here; the user reviews/
    edits client-side and POSTs the confirmed list to /import/confirm.

    Enqueues a background job for the
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
            source_type = "photo"

            def extractor(db: Session) -> str:
                # Prompt resolution happens inside the job-body
                # closure: this handler has no `db` session of its own
                # (the session opens inside the job body, on the worker
                # thread), and get_receipt_import_prompt needs one to
                # check for a household override.
                prompt = (
                    get_receipt_import_prompt(db)
                    .replace("{content}", "[see attached photo of a receipt]")
                    .replace("{today}", date.today().isoformat())
                )
                return ollama_client.describe_image_json(
                    db,
                    raw_bytes,
                    prompt,
                    schema=INVENTORY_SCHEMA,
                    response_tokens=INVENTORY_RESPONSE_TOKENS,
                )

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
            except UnicodeDecodeError as exc:
                raise HTTPException(
                    status_code=400, detail="Could not read this file as text. Upload a photo, PDF, or plain-text file."
                ) from exc
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


# --- Audit P1-5: ingredient resolution ---------------------------------
#
# Ingredient identity is this app's join key and it is free text, so it
# is never matched by raw substring: that scores "egg" against "eggplant"
# and leaves ties between identically-named rows undefined. The matcher
# lives in ingredient_resolution_service; these endpoints are the surface
# that lets the user see what it decided and correct it once.


def _candidate_read(candidate) -> IngredientMatchCandidate:
    item = candidate.payload
    return IngredientMatchCandidate(
        item_id=getattr(item, "id", None),
        name=candidate.name,
        score=candidate.score,
        confidence=candidate.confidence,
        reason=candidate.reason,
        quantity=getattr(item, "quantity", None),
        unit=getattr(item, "unit", None),
        category=getattr(item, "category", None),
        expiration_date=getattr(item, "expiration_date", None),
        blocked_by=candidate.blocked_by,
    )


def _resolution_read(resolution, threshold: float, message: str | None = None) -> IngredientResolutionResponse:
    match = None
    if resolution.item is not None:
        match = IngredientMatchCandidate(
            item_id=resolution.item.id,
            name=resolution.item.name,
            score=resolution.score,
            confidence=resolution.confidence,
            reason=resolution.reason,
            quantity=resolution.item.quantity,
            unit=resolution.item.unit,
            category=resolution.item.category,
            expiration_date=resolution.item.expiration_date,
        )
    return IngredientResolutionResponse(
        query=resolution.query,
        normalized=resolution.normalized,
        matched=resolution.matched,
        match=match,
        via_alias=resolution.via_alias,
        threshold=threshold,
        candidates=[_candidate_read(c) for c in resolution.candidates],
        blocked_candidates=[_candidate_read(c) for c in resolution.blocked],
        message=message,
    )


@router.get("/resolve", response_model=IngredientResolutionResponse)
def resolve_ingredient_name(name: str, db: Session = Depends(get_db)):
    """What does this free-text ingredient name resolve to, and how sure
    is the app? Read-only -- nothing is written, nothing is deducted.

    Exists so the frontend can show a match and its confidence BEFORE the
    user commits to an action, and so a household can investigate a
    surprising grocery-list or cost result without having to reason about
    the matcher from the outside."""
    resolution = inventory_service.resolve_for_write(db, name)
    return _resolution_read(resolution, ingredient_resolution_service.THRESHOLD_DESTRUCTIVE)


@router.get("/aliases", response_model=list[IngredientAliasRead])
def list_ingredient_aliases(db: Session = Depends(get_db)):
    return ingredient_resolution_service.list_aliases(db)


@router.post("/aliases", response_model=IngredientAliasRead, status_code=201)
def create_ingredient_alias(payload: IngredientAliasCreate, db: Session = Depends(get_db)):
    """Teach the resolver that one name means one ingredient. Upserts on
    the normalised alias, so re-teaching a name updates the existing entry
    rather than accumulating near-duplicate rows."""
    if payload.inventory_item_id is not None and db.get(InventoryItem, payload.inventory_item_id) is None:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    try:
        return ingredient_resolution_service.remember_alias(
            db,
            alias_text=payload.alias_text,
            canonical_name=payload.canonical_name,
            inventory_item_id=payload.inventory_item_id,
            note=payload.note,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/aliases/{alias_id}", status_code=204)
def delete_ingredient_alias(alias_id: int, db: Session = Depends(get_db)):
    if not ingredient_resolution_service.forget_alias(db, alias_id):
        raise HTTPException(status_code=404, detail="Alias not found")


def _explicit_item(payload, db: Session) -> InventoryItem | None:
    """Resolves an explicit `item_id` on a write request and, when asked,
    remembers it as an alias. This is the path a disambiguation answer
    takes: the user has already told us which row they meant, so the
    matcher is bypassed entirely rather than re-run and second-guessed."""
    if payload.item_id is None:
        return None
    item = db.get(InventoryItem, payload.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    if payload.remember_alias:
        # The name already resolves to this item without help -- nothing
        # to remember, and not a reason to fail the write the user asked
        # for.
        with contextlib.suppress(ValueError):
            ingredient_resolution_service.remember_alias(
                db,
                alias_text=payload.ingredient_name,
                canonical_name=item.name,
                inventory_item_id=item.id,
                note="Saved from a disambiguation prompt",
            )
    return item


@router.post("/deduct", response_model=InventoryItemRead)
def deduct_inventory(payload: InventoryDeductRequest, db: Session = Depends(get_db)):
    """Name-based deduction -- the confirm step for a chat-proposed
    inventory_deduct action (Phase 7), or anywhere else a natural-
    language ingredient name needs to resolve to a row.

    Three outcomes, deliberately distinct (audit P1-5):
    - 200: deducted, or found-but-not-deducted on an unconvertible unit
      (P1-4 -- the item is returned either way, `last_used_date` stamped).
    - 409: the name resembles something in inventory but not confidently
      enough to write to it. NOTHING is modified. The body is an
      IngredientResolutionResponse with ranked candidates; re-send with
      `item_id` (and `remember_alias: true`) to apply the user's choice.
    - 404: nothing in inventory resembles this name at all."""
    item = _explicit_item(payload, db)
    if item is not None:
        outcome = inventory_service.deduct_item(db, item, payload.quantity, payload.unit)
        return outcome.item

    outcome = inventory_service.deduct_by_name(db, payload.ingredient_name, payload.quantity, payload.unit)
    if outcome.status == inventory_service.DEDUCT_AMBIGUOUS:
        raise HTTPException(
            status_code=409,
            detail=_resolution_read(
                outcome.resolution,
                ingredient_resolution_service.THRESHOLD_DESTRUCTIVE,
                outcome.message,
            ).model_dump(mode="json"),
        )
    if outcome.status == inventory_service.DEDUCT_NO_MATCH:
        raise HTTPException(status_code=404, detail=outcome.message)
    return outcome.item


@router.post("/update-by-name", response_model=InventoryItemRead)
def update_inventory_by_name(payload: InventoryUpdateByNameRequest, db: Session = Depends(get_db)):
    """Name-based partial update -- the confirm step for a chat-proposed
    inventory_update action (Phase 7), e.g. "mark the lentils as
    priority" or "we're out of milk" (set quantity to 0).

    Same three-outcome contract as /deduct above, and for the same reason:
    "we're out of milk" zeroing the wrong row is the same class of silent
    corruption as deducting from it."""
    updates = payload.model_dump(exclude={"ingredient_name", "item_id", "remember_alias"}, exclude_unset=True)
    item = _explicit_item(payload, db)
    if item is not None:
        return inventory_service.update_item(db, item, **updates)

    resolution = inventory_service.resolve_for_write(db, payload.ingredient_name)
    if resolution.item is None:
        if resolution.needs_confirmation:
            raise HTTPException(
                status_code=409,
                detail=_resolution_read(
                    resolution,
                    ingredient_resolution_service.THRESHOLD_DESTRUCTIVE,
                    f"Not confident enough to update an inventory item for "
                    f"{payload.ingredient_name!r}. Confirm which item you meant.",
                ).model_dump(mode="json"),
            )
        raise HTTPException(status_code=404, detail=f'No inventory item matching "{payload.ingredient_name}"')
    return inventory_service.update_item(db, resolution.item, **updates)


@router.get("/{item_id}", response_model=InventoryItemRead)
def get_inventory_item(item_id: int, db: Session = Depends(get_db)):
    item = db.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Inventory item not found")
    return item


@router.post("", response_model=InventoryItemRead, status_code=201)
def create_inventory_item(payload: InventoryItemCreate, db: Session = Depends(get_db)):
    data = payload.model_dump()
    # `purchased_quantity` is the immutable "on hand at purchase time"
    # snapshot cost_service divides by -- see InventoryItem's docstring.
    # At creation, on-hand IS what was just purchased, so default it to
    # the row's initial `quantity` unless the caller said otherwise.
    if data.get("purchased_quantity") is None:
        data["purchased_quantity"] = data.get("quantity")
    item = InventoryItem(**data)
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
