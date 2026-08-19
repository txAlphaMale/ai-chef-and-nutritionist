"""Pydantic request/response models for health-metric trend tracking.
BMI is deliberately excluded from the writable fields -- it's always
computed server-side from weight_kg + the member's height_cm (see
routers/health.py) rather than trusted from the client."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict


class HealthMetricEntryBase(BaseModel):
    entry_date: date
    weight_kg: float | None = None
    # Backlog B8.2 -- daily step total; see HealthMetricEntry's own
    # docstring comment for why this stays a single integer, not a
    # fuller activity model.
    steps: int | None = None
    ldl_mg_dl: float | None = None
    hdl_mg_dl: float | None = None
    total_cholesterol_mg_dl: float | None = None
    triglycerides_mg_dl: float | None = None
    blood_pressure_systolic: int | None = None
    blood_pressure_diastolic: int | None = None
    blood_glucose_mg_dl: float | None = None
    # Backlog B18.1 -- see app/models/health.py for why Lp(a) carries its
    # own unit instead of being normalized to one scale.
    apob_mg_dl: float | None = None
    lpa_value: float | None = None
    lpa_unit: str | None = None  # mg_dl | nmol_l
    hba1c_percent: float | None = None
    waist_cm: float | None = None
    notes: str | None = None


class HealthMetricEntryCreate(HealthMetricEntryBase):
    household_member_id: int | None = None
    source: str = "manual"  # manual|import


class HealthMetricEntryUpdate(BaseModel):
    entry_date: date | None = None
    household_member_id: int | None = None
    weight_kg: float | None = None
    steps: int | None = None
    ldl_mg_dl: float | None = None
    hdl_mg_dl: float | None = None
    total_cholesterol_mg_dl: float | None = None
    triglycerides_mg_dl: float | None = None
    blood_pressure_systolic: int | None = None
    blood_pressure_diastolic: int | None = None
    blood_glucose_mg_dl: float | None = None
    # Backlog B18.1 -- see app/models/health.py for why Lp(a) carries its
    # own unit instead of being normalized to one scale.
    apob_mg_dl: float | None = None
    lpa_value: float | None = None
    lpa_unit: str | None = None  # mg_dl | nmol_l
    hba1c_percent: float | None = None
    waist_cm: float | None = None
    notes: str | None = None


class HealthMetricEntryRead(HealthMetricEntryBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    household_member_id: int | None = None
    bmi: float | None = None
    source: str


class MetricTrend(BaseModel):
    latest_value: float
    latest_date: date
    baseline_value: float
    baseline_date: date
    delta: float
    days_span: int


class HealthTrendsResponse(BaseModel):
    household_member_id: int
    weight_kg: MetricTrend | None = None
    bmi: MetricTrend | None = None
    ldl_mg_dl: MetricTrend | None = None
    hdl_mg_dl: MetricTrend | None = None
    total_cholesterol_mg_dl: MetricTrend | None = None


# --- Bloodwork import (backlog B8.1) ---------------------------------------


class HealthBloodworkEntryPreview(BaseModel):
    """One extracted-but-unconfirmed row. `entry_date` is a plain string
    (not `date`) and every numeric field stays nullable/unvalidated on
    purpose -- this is a preview for the user to review/edit/reject, not
    a write; the real validation happens at POST /api/health/metrics
    once the user confirms a row, same preview-then-confirm discipline
    as recipe import."""

    entry_date: str | None = None
    weight_kg: float | None = None
    ldl_mg_dl: float | None = None
    hdl_mg_dl: float | None = None
    total_cholesterol_mg_dl: float | None = None
    triglycerides_mg_dl: float | None = None
    blood_pressure_systolic: int | None = None
    blood_pressure_diastolic: int | None = None
    blood_glucose_mg_dl: float | None = None
    # Backlog B18.1 -- see app/models/health.py for why Lp(a) carries its
    # own unit instead of being normalized to one scale.
    apob_mg_dl: float | None = None
    lpa_value: float | None = None
    lpa_unit: str | None = None  # mg_dl | nmol_l
    hba1c_percent: float | None = None
    waist_cm: float | None = None


class HealthBloodworkImportResponse(BaseModel):
    entries: list[HealthBloodworkEntryPreview]
    raw_model_output: str


# --- Wearable/health-platform import (backlog B8.2) ------------------------


class HealthWearableEntryPreview(BaseModel):
    """One extracted-but-unconfirmed daily row from an Apple Health /
    Health Connect / other wearable export -- same preview-then-confirm
    discipline as HealthBloodworkEntryPreview above (and reuses the same
    POST /api/health/metrics confirm endpoint), just a narrower field
    set (weight + steps, not lab values)."""

    entry_date: str | None = None
    weight_kg: float | None = None
    steps: int | None = None


class HealthWearableImportResponse(BaseModel):
    entries: list[HealthWearableEntryPreview]
    # "apple_health" (deterministic XML parse) | "ai_extracted" (any
    # other file, extracted via the same free-text Ollama pipeline
    # bloodwork import uses) -- see health_service.py's module comment
    # for why Health Connect specifically has no native path here.
    source_detail: str
    raw_model_output: str | None = None
