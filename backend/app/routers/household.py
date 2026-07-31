"""Household-wide preferences (a singleton row seeded in app/seed.py)
and individual member profiles (age/height/sex/activity level) --
household_size/dietary_restrictions/goals feed meal-plan generation
(meal_plan_service.py), and member height feeds BMI calculation when
logging health metrics (routers/health.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import HouseholdMember, HouseholdPreferences
from app.schemas.household import (
    HouseholdMemberCreate,
    HouseholdMemberRead,
    HouseholdMemberUpdate,
    HouseholdPreferencesRead,
    HouseholdPreferencesUpdate,
)

router = APIRouter(prefix="/api/household", tags=["household"])


@router.get("/preferences", response_model=HouseholdPreferencesRead)
def get_preferences(db: Session = Depends(get_db)):
    prefs = db.query(HouseholdPreferences).first()
    if prefs is None:
        # Should always exist after app/seed.py runs, but don't 500 if
        # someone hits this against an unseeded DB.
        raise HTTPException(status_code=404, detail="Household preferences not yet initialized")
    return prefs


@router.patch("/preferences", response_model=HouseholdPreferencesRead)
def update_preferences(payload: HouseholdPreferencesUpdate, db: Session = Depends(get_db)):
    prefs = db.query(HouseholdPreferences).first()
    if prefs is None:
        raise HTTPException(status_code=404, detail="Household preferences not yet initialized")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(prefs, field, value)
    db.commit()
    db.refresh(prefs)
    return prefs


@router.get("/members", response_model=list[HouseholdMemberRead])
def list_members(db: Session = Depends(get_db)):
    return db.query(HouseholdMember).order_by(HouseholdMember.name).all()


@router.post("/members", response_model=HouseholdMemberRead, status_code=201)
def create_member(payload: HouseholdMemberCreate, db: Session = Depends(get_db)):
    member = HouseholdMember(**payload.model_dump())
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


@router.patch("/members/{member_id}", response_model=HouseholdMemberRead)
def update_member(member_id: int, payload: HouseholdMemberUpdate, db: Session = Depends(get_db)):
    member = db.get(HouseholdMember, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Household member not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(member, field, value)
    db.commit()
    db.refresh(member)
    return member


@router.delete("/members/{member_id}", status_code=204)
def delete_member(member_id: int, db: Session = Depends(get_db)):
    member = db.get(HouseholdMember, member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Household member not found")
    db.delete(member)
    db.commit()
    return None
