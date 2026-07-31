"""Trend tracking for weight, cholesterol, and other diet-influenced
bloodwork metrics, tied to a household member."""
from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin


class HealthMetricEntry(Base, TimestampMixin):
    __tablename__ = "health_metric_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    household_member_id: Mapped[int | None] = mapped_column(
        ForeignKey("household_members.id"), nullable=True
    )
    entry_date: Mapped[date] = mapped_column(Date, index=True)

    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    bmi: Mapped[float | None] = mapped_column(Float, nullable=True)  # computed at write time
    ldl_mg_dl: Mapped[float | None] = mapped_column(Float, nullable=True)
    hdl_mg_dl: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_cholesterol_mg_dl: Mapped[float | None] = mapped_column(Float, nullable=True)
    triglycerides_mg_dl: Mapped[float | None] = mapped_column(Float, nullable=True)
    blood_pressure_systolic: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blood_pressure_diastolic: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blood_glucose_mg_dl: Mapped[float | None] = mapped_column(Float, nullable=True)

    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual|import
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
