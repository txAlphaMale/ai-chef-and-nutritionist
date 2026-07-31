"""Pantry / fridge / freezer / produce / spice inventory tracking."""
from __future__ import annotations

from datetime import date

from sqlalchemy import Boolean, Date, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class InventoryItem(Base, TimestampMixin):
    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True)
    # pantry | fridge | freezer | produce | spice | other
    category: Mapped[str] = mapped_column(String(50), default="pantry")
    quantity: Mapped[float] = mapped_column(Float, default=1.0)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    location: Mapped[str | None] = mapped_column(String(100), nullable=True)

    purchased_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_used_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    # "use this up" boost -- e.g. 5 lbs of lentils the household wants
    # worked into meals more often (but not constantly)
    is_priority: Mapped[bool] = mapped_column(Boolean, default=False)
    priority_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # manual | vision | chat -- how the item entered inventory
    source: Mapped[str] = mapped_column(String(20), default="manual")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
