"""Kitchen/equipment profile CRUD -- lets the household describe what
gear is available (a full home kitchen vs. a camping trip, RV, or
short-term rental with limited equipment) so meal-plan generation
(routers/meal_plan.py) can adapt accordingly. Only one profile is
"active" at a time; setting one active clears the flag on the others,
same singleton-ish pattern used for HouseholdPreferences.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import KitchenProfile
from app.schemas.kitchen import KitchenProfileCreate, KitchenProfileRead, KitchenProfileUpdate

router = APIRouter(prefix="/api/kitchen-profiles", tags=["kitchen-profiles"])


def _deactivate_others(db: Session, keep_id: int | None) -> None:
    query = db.query(KitchenProfile).filter(KitchenProfile.is_active.is_(True))
    if keep_id is not None:
        query = query.filter(KitchenProfile.id != keep_id)
    for other in query.all():
        other.is_active = False


@router.get("", response_model=list[KitchenProfileRead])
def list_kitchen_profiles(db: Session = Depends(get_db)):
    return db.query(KitchenProfile).order_by(KitchenProfile.name).all()


@router.post("", response_model=KitchenProfileRead, status_code=201)
def create_kitchen_profile(payload: KitchenProfileCreate, db: Session = Depends(get_db)):
    profile = KitchenProfile(**payload.model_dump())
    db.add(profile)
    db.flush()
    if profile.is_active:
        _deactivate_others(db, profile.id)
    db.commit()
    db.refresh(profile)
    return profile


@router.patch("/{profile_id}", response_model=KitchenProfileRead)
def update_kitchen_profile(profile_id: int, payload: KitchenProfileUpdate, db: Session = Depends(get_db)):
    profile = db.get(KitchenProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Kitchen profile not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)
    if payload.is_active:
        _deactivate_others(db, profile.id)
    db.commit()
    db.refresh(profile)
    return profile


@router.delete("/{profile_id}", status_code=204)
def delete_kitchen_profile(profile_id: int, db: Session = Depends(get_db)):
    profile = db.get(KitchenProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="Kitchen profile not found")
    db.delete(profile)
    db.commit()
    return None
