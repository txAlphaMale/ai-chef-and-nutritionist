"""Health-metric entries (weight, BMI, cholesterol, blood pressure,
glucose) and trend computation over them -- the "monitor trends ...
influenced by diet" piece of the project brief. BMI is always computed
server-side from weight_kg + the member's height_cm rather than trusted
from the client, so it stays consistent even if the member's height is
updated later.

Route ordering matters -- the static /trends path is declared before
the dynamic /{entry_id} routes so FastAPI's route-matching order
doesn't swallow it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.models import HealthMetricEntry, HouseholdMember
from app.schemas.health import (
    HealthBloodworkImportResponse,
    HealthMetricEntryCreate,
    HealthMetricEntryRead,
    HealthMetricEntryUpdate,
    HealthTrendsResponse,
    HealthWearableImportResponse,
    MetricTrend,
)
from app.schemas.jobs import JobEnqueuedResponse
from app.services import health_service, job_queue, ollama_client

router = APIRouter(prefix="/api/health", tags=["health"])

TREND_FIELDS = [
    "weight_kg",
    "bmi",
    "ldl_mg_dl",
    "hdl_mg_dl",
    "total_cholesterol_mg_dl",
    # Backlog B18.1. Lp(a) is deliberately absent: it is reported in two
    # non-interconvertible units, so a single trend line over it could be
    # mixing scales. It is shown as a value with its unit instead.
    "apob_mg_dl",
    "hba1c_percent",
    "waist_cm",
]


def _compute_bmi_for_entry(db: Session, household_member_id: int | None, weight_kg: float | None) -> float | None:
    if weight_kg is None or household_member_id is None:
        return None
    member = db.get(HouseholdMember, household_member_id)
    if member is None or member.height_cm is None:
        return None
    return health_service.compute_bmi(weight_kg, member.height_cm)


@router.get("/metrics", response_model=list[HealthMetricEntryRead])
def list_metrics(household_member_id: int | None = None, limit: int = 200, db: Session = Depends(get_db)):
    query = db.query(HealthMetricEntry)
    if household_member_id is not None:
        query = query.filter(HealthMetricEntry.household_member_id == household_member_id)
    return query.order_by(HealthMetricEntry.entry_date.desc()).limit(limit).all()


@router.post("/import", response_model=JobEnqueuedResponse, status_code=202)
async def import_bloodwork(file: UploadFile | None = None, text: str | None = Form(None)):
    """Backlog B8.1 -- accepts `text` (pasted lab values) OR an uploaded
    `file` (a lab-report PDF, a CSV/text export, or a photo of a printed
    report), extracts a bloodwork PREVIEW via Ollama, and returns it
    WITHOUT saving -- same preview-then-confirm shape as recipe import.
    The frontend lets the user review/edit each extracted row, then
    POSTs the ones they want to keep to the existing POST /metrics
    endpoint (source="import"), so BMI computation and validation stay
    on the one real code path rather than being duplicated here.

    Runs through the shared background job queue (B11.1) like every
    other AI-consuming endpoint in this app -- the Ollama call (and, for
    a PDF, the synchronous pypdf text extraction) would otherwise block
    this whole app's single event loop for the call's full duration, the
    exact bug class B11.1 fixed everywhere else."""
    if not (text or file is not None):
        raise HTTPException(status_code=400, detail="Provide `text` or `file`")

    raw_bytes: bytes | None = None
    content_type = ""
    filename = ""
    if file is not None:
        raw_bytes = await file.read()
        content_type = file.content_type or ""
        filename = (file.filename or "").lower()

    def _run() -> dict:
        db = SessionLocal()
        try:
            if text:
                content = text
            elif content_type.startswith("image/"):
                response = ollama_client.describe_image(
                    db,
                    raw_bytes,
                    health_service.BLOODWORK_IMPORT_PROMPT.format(content="[see attached photo]"),
                )
                raw_output = ollama_client.extract_content(response)
                entries = health_service.parse_bloodwork_response(raw_output)
                return HealthBloodworkImportResponse(entries=entries, raw_model_output=raw_output).model_dump()
            else:
                content = health_service.extract_bloodwork_text(raw_bytes, filename, content_type)
                if not content.strip():
                    raise RuntimeError("Could not read any text from that file")

            raw_output = health_service.run_bloodwork_extraction(db, content)
            entries = health_service.parse_bloodwork_response(raw_output)
            return HealthBloodworkImportResponse(entries=entries, raw_model_output=raw_output).model_dump()
        finally:
            db.close()

    job_id, created = job_queue.enqueue("bloodwork_import", "Bloodwork import", _run)
    return JobEnqueuedResponse(job_id=job_id, created=created)


@router.post("/import-wearable", response_model=JobEnqueuedResponse, status_code=202)
async def import_wearable(file: UploadFile):
    """Backlog B8.2 -- accepts an Apple Health export (`export.xml` or
    the `export.zip` the Health app produces) for a deterministic,
    no-AI parse, or any other file (a Health Connect export, a Withings
    CSV, etc.) for the same free-text Ollama extraction pipeline
    bloodwork import uses. See health_service.py's module comment above
    parse_apple_health_export for why Health Connect specifically has no
    dedicated deterministic path here.

    Runs through the shared background job queue (B11.1) even for the
    Apple Health path, which makes no Ollama call at all -- a real
    multi-year export.xml can take real wall-clock time to stream-parse,
    and this app's own standing rule is "anything that isn't instant
    gets a visible busy indicator," not just "anything that calls
    Ollama."
    """
    raw_bytes = await file.read()
    filename = (file.filename or "").lower()
    content_type = file.content_type or ""
    is_apple_health = filename.endswith(".xml") or filename.endswith(".zip") or raw_bytes[:2] == b"PK"

    def _run() -> dict:
        if is_apple_health:
            try:
                entries = health_service.parse_apple_health_export(raw_bytes, filename)
            except ValueError as e:
                raise RuntimeError(str(e)) from e
            return HealthWearableImportResponse(
                entries=entries, source_detail="apple_health", raw_model_output=None
            ).model_dump()

        db = SessionLocal()
        try:
            content = health_service.extract_wearable_text(raw_bytes, filename, content_type)
            if not content.strip():
                raise RuntimeError("Could not read any text from that file")
            raw_output = health_service.run_wearable_extraction(db, content)
            entries = health_service.parse_wearable_ai_response(raw_output)
            return HealthWearableImportResponse(
                entries=entries, source_detail="ai_extracted", raw_model_output=raw_output
            ).model_dump()
        finally:
            db.close()

    job_id, created = job_queue.enqueue("wearable_import", "Wearable data import", _run)
    return JobEnqueuedResponse(job_id=job_id, created=created)


@router.get("/trends", response_model=HealthTrendsResponse)
def get_trends(household_member_id: int, window_days: int = 90, db: Session = Depends(get_db)):
    member = db.get(HouseholdMember, household_member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Household member not found")

    entries = (
        db.query(HealthMetricEntry)
        .filter_by(household_member_id=household_member_id)
        .order_by(HealthMetricEntry.entry_date.desc())
        .all()
    )
    entry_dicts = [
        {
            "entry_date": e.entry_date,
            "weight_kg": e.weight_kg,
            "bmi": e.bmi,
            "ldl_mg_dl": e.ldl_mg_dl,
            "hdl_mg_dl": e.hdl_mg_dl,
            "total_cholesterol_mg_dl": e.total_cholesterol_mg_dl,
            "apob_mg_dl": e.apob_mg_dl,
            "hba1c_percent": e.hba1c_percent,
            "waist_cm": e.waist_cm,
        }
        for e in entries
    ]
    trends = {
        field: health_service.compute_metric_trend(entry_dicts, field, window_days=window_days)
        for field in TREND_FIELDS
    }
    return HealthTrendsResponse(
        household_member_id=household_member_id,
        **{field: MetricTrend(**trend) if trend else None for field, trend in trends.items()},
    )


@router.post("/metrics", response_model=HealthMetricEntryRead, status_code=201)
def create_metric(payload: HealthMetricEntryCreate, db: Session = Depends(get_db)):
    data = payload.model_dump()
    data["bmi"] = _compute_bmi_for_entry(db, data.get("household_member_id"), data.get("weight_kg"))
    entry = HealthMetricEntry(**data)
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


@router.patch("/metrics/{entry_id}", response_model=HealthMetricEntryRead)
def update_metric(entry_id: int, payload: HealthMetricEntryUpdate, db: Session = Depends(get_db)):
    entry = db.get(HealthMetricEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Health metric entry not found")
    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(entry, field, value)
    # Recompute BMI if weight or the member association changed.
    if "weight_kg" in updates or "household_member_id" in updates:
        entry.bmi = _compute_bmi_for_entry(db, entry.household_member_id, entry.weight_kg)
    db.commit()
    db.refresh(entry)
    return entry


@router.delete("/metrics/{entry_id}", status_code=204)
def delete_metric(entry_id: int, db: Session = Depends(get_db)):
    entry = db.get(HealthMetricEntry, entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="Health metric entry not found")
    db.delete(entry)
    db.commit()
    return None
