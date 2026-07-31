"""Kitchen/equipment profiles -- lets the meal planner adapt to a home
kitchen, a camping trip, an RV, or a short-term rental with limited gear.
"""
from __future__ import annotations

from sqlalchemy import Boolean, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class KitchenProfile(Base, TimestampMixin):
    __tablename__ = "kitchen_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    # e.g. ["oven", "stovetop", "instant_pot", "grill", "dutch_oven", "microwave", "no_fridge"]
    equipment: Mapped[list] = mapped_column(JSON, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
