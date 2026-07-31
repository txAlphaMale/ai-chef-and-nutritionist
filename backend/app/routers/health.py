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

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import HealthMetricEntry, HouseholdMember
from app.schemas.health import (
    HealthMetricEntryCreate,
    HealthMetricEntryRead,
    HealthMetricEntryUpdate,
    HealthTrendsResponse,
    MetricTrend,
)
from app.services import health_service

router = APIRouter(prefix="/api/health", tags=["health"])

TREND_FIELDS = ["weight_kg", "bmi", "ldl_mg_dl", "hdl_mg_dl", "total_cholesterol_mg_dl"]


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
