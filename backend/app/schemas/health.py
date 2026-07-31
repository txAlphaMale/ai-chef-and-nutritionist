"""Pydantic request/response models for health-metric trend tracking.
BMI is deliberately excluded from the writable fields -- it's always
computed server-side from weight_kg + the member's height_cm (see
routers/health.py) rather than trusted from the client."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class HealthMetricEntryBase(BaseModel):
    entry_date: date
    weight_kg: float | None = None
    ldl_mg_dl: float | None = None
    hdl_mg_dl: float | None = None
    total_cholesterol_mg_dl: float | None = None
    triglycerides_mg_dl: float | None = None
    blood_pressure_systolic: int | None = None
    blood_pressure_diastolic: int | None = None
    blood_glucose_mg_dl: float | None = None
    notes: str | None = None


class HealthMetricEntryCreate(HealthMetricEntryBase):
    household_member_id: int | None = None
    source: str = "manual"  # manual|import


class HealthMetricEntryUpdate(BaseModel):
    entry_date: date | None = None
    household_member_id: int | None = None
    weight_kg: float | None = None
    ldl_mg_dl: float | None = None
    hdl_mg_dl: float | None = None
    total_cholesterol_mg_dl: float | None = None
    triglycerides_mg_dl: float | None = None
    blood_pressure_systolic: int | None = None
    blood_pressure_diastolic: int | None = None
    blood_glucose_mg_dl: float | None = None
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
