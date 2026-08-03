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
    package_parsing,
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
# source too -- only the prompt differs. Uses the same "{content}"/
# "{today}" str.replace() placeholder trick as
# recipe_service.RECIPE_IMPORT_PROMPT: for a photo, the caller substitutes
# a short description instead of real text (see import_inventory below)
# rather than needing a second prompt template.
#
# Substitution mechanics (2026-08-03, same pass as recipe_service.
# RECIPE_IMPORT_PROMPT's rewrite, backlog B16.1): this prompt is now a
# DB-backed, GUI-editable SystemPrompt (get_receipt_import_prompt, below),
# so {content}/{today} are filled via plain str.replace() at each call
# site rather than `.format()` -- the worked EXAMPLE below used to need
# its literal `{`/`}` doubled to `{{`/`}}` to survive `.format()`; that
# doubling is gone now, since .replace() has no such escaping rule and a
# household member editing this in a textarea has no reason to know one
# exists. `.format()`'s KeyError-on-a-stray-brace failure mode is exactly
# the kind of code-only footgun a GUI-editable prompt cannot carry.
# REWRITTEN FROM SCRATCH (2026-08-02, author-directed full reset): four
# rounds of patching the previous prompt (see git history on this file)
# chased symptoms one at a time -- date/price fields, quantity-vs-unit
# conflation, anti-truncation wording, anti-merge wording -- each patch
# making the prompt longer, until a live A/B test against the author's
# real Ollama container proved the accumulated LENGTH itself was
# overwhelming a 9B model into bailing out to a literal `[]` rather than
# attempting the task (see PROJECT-PLAN.md's session log for that
# investigation's full detail: `done_reason='stop'`, `eval_count=2` --
# the model took its own documented "nothing found" escape hatch almost
# instantly, on a fully correct, untruncated input). Trimming that same
# prose-paragraph prompt bought partial headroom but was still patching
# the same design. This version is a genuine redesign, not another
# trim: numbered rules instead of flowing prose (more scannable, more
# token-efficient per requirement stated), and -- notably absent from
# every earlier version despite four rounds of edits -- one concrete
# worked example showing a real source line mapped to its exact output
# object, since a single good example is generally a stronger format/
# behavior signal for a model than another paragraph of abstract
# description. Rendered against the author's real 15-line, 8-food-item
# Walmart receipt this comes to ~4090 chars / ~1169 tokens, well under
# half of what the prompt that caused the bailout used for the exact
# same content (7723 chars / ~2207 tokens) -- see
# _RECEIPT_EXTRA_OPTIONS's docstring below for the accompanying model
# swap. Every functional requirement from the prior version is still
# here (non-food exclusion categories, abbreviation expansion, quantity-
# vs-package-size, unit_price semantics, purchased_date semantics) --
# reworded and reorganized, not dropped.
RECEIPT_IMPORT_PROMPT = """\
Task: extract every human-food/grocery item purchased on this receipt, PDF \
order, or list, as a JSON array. Today's date: {today}.

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

OUTPUT FORMAT: Respond with ONLY a JSON array -- no other text, no markdown \
fences. An empty array `[]` only if this source genuinely contains zero \
food/grocery items. Each element is an object with exactly these keys:
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

# Model swap (2026-08-02, author-directed): the author pointed out this
# whole investigation had narrowed to "make qwen3.5:9b work" instead of
# asking whether it was the right model for the job, and separately
# corrected an assumption that any replacement had to fit an 11GB single
# GPU -- the author's hardware is TWO GTX 1080 Tis (22GB combined), and
# Ollama splits a model across multiple visible GPUs automatically when
# it doesn't fit on one. The author's actual locally-pulled model list
# (`docker exec ollama ollama list`) includes `qwen3.6:27b` (17GB) --
# same lab/family as the model already confirmed (via this whole
# session's investigation) to correctly follow this prompt's structure
# and correctly honor `think=False` when the prompt is short enough not
# to trigger a bailout, just 3x the parameters, and already pulled (no
# download needed). Comfortably fits the real 22GB budget with room for
# KV cache, unlike a same-single-GPU-constrained option would have.
# `ollama_chat_model`'s default is changed to this below (settings_
# service.py) rather than only overridden here, since the author's
# intent is an app-wide model swap, not a receipt-import-only patch --
# meal planning, recipe generation, and chat all benefit from the same
# larger-capacity model for the identical reason (more complex prompts
# than a 9B model reliably executes).
#
# `_RECEIPT_EXTRA_OPTIONS` keeps Qwen's own documented non-thinking/
# general-task sampling recommendation (`temperature=0.7, top_p=0.8,
# top_k=20, presence_penalty=1.5` -- Hugging Face model card,
# Qwen/Qwen3.5-9B, verified live and cross-checked against a discussion
# thread comparing two sections of that same README against each
# other). This is a same-family EXTRAPOLATION to qwen3.6, not a value
# independently verified against qwen3.6's own documentation -- the
# diagnostic logging already wired into ollama_client.py (done_reason,
# eval_count, thinking_chars) will make it immediately visible in
# `docker compose logs` if qwen3.6 needs different sampling than 3.5 did.
_RECEIPT_EXTRA_OPTIONS = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.5}


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

    # Package/measurement split (2026-08-02) -- see BarcodeLookupResponse's
    # own docstring for why this now parses quantity_text instead of
    # leaving it display-only.
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
            response = ollama_client.describe_image(db, image_bytes, get_vision_prompt(db))
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
    prompt_template = get_receipt_import_prompt(db)
    budget = ollama_client.content_char_budget(
        db, prompt_overhead_chars=len(prompt_template), response_reserve_tokens=3000
    )
    print(
        f"[inventory._receipt_text_extraction] extracted_content_chars={len(content)} "
        f"budget_chars={budget} truncated={len(content) > budget}",
        flush=True,
    )
    prompt = prompt_template.replace("{content}", content[:budget]).replace("{today}", date.today().isoformat())
    response = ollama_client.chat(db, [{"role": "user", "content": prompt}], extra_options=_RECEIPT_EXTRA_OPTIONS)
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
        if raw_output and not detected:
            # The exact author-reported case that the existing logging
            # above could NOT explain on its own (2026-08-02): a
            # non-empty model response that nonetheless parses to zero
            # items -- which looks identical in the UI to "this receipt
            # genuinely had no food on it". ollama_client's own response
            # log already shows the first 300 chars of content; what it
            # can't show is the END of the response, and the END is what
            # distinguishes the two failure shapes that matter here: a
            # response cut off mid-array by the context/num_predict limit
            # (no closing "]", done_reason "length") versus a complete
            # response whose JSON is simply surrounded by prose. Logged
            # ONLY on the zero-items path, so a normal successful import
            # adds no extra noise.
            head = raw_output[:500].replace("\n", " ")
            tail = raw_output[-300:].replace("\n", " ")
            print(
                f"[inventory._inventory_import_job] ZERO ITEMS from a non-empty response -- "
                f"head={head!r} tail={tail!r}",
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
            source_type = "photo"

            def extractor(db: Session) -> str:
                # Prompt resolution moved inside the job-body closure
                # (2026-08-03, backlog B16.1) -- this handler has no `db`
                # session of its own (B11.1's pattern: the DB session only
                # opens inside the job body, on the worker thread), and
                # get_receipt_import_prompt needs one to check for a
                # household override.
                prompt = get_receipt_import_prompt(db).replace(
                    "{content}", "[see attached photo of a receipt]"
                ).replace("{today}", date.today().isoformat())
                response = ollama_client.describe_image(db, raw_bytes, prompt, extra_options=_RECEIPT_EXTRA_OPTIONS)
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
    data = payload.model_dump()
    # Quantity model redesign (2026-08-02): `purchased_quantity` is the
    # immutable "on hand at purchase time" snapshot cost_service divides
    # by -- see InventoryItem's own docstring. At creation time, on-hand
    # IS whatever was just purchased, so default it to the row's own
    # initial `quantity` whenever the caller didn't explicitly supply a
    # different value (every existing caller -- the manual add form, the
    # barcode/vision/receipt/order-import confirm flows -- can keep
    # sending a plain InventoryItemCreate with no changes needed).
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
