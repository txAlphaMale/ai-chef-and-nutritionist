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
    household_member_id: Mapped[int | None] = mapped_column(ForeignKey("household_members.id"), nullable=True)
    entry_date: Mapped[date] = mapped_column(Date, index=True)

    weight_kg: Mapped[float | None] = mapped_column(Float, nullable=True)
    bmi: Mapped[float | None] = mapped_column(Float, nullable=True)  # computed at write time
    # Backlog B8.2: the "activity" half of "weight and
    # activity" wearable import. Deliberately just a daily step total,
    # not a fuller activity model (active minutes, workout sessions,
    # heart rate) -- this table's grain is one row per (member,
    # entry_date), built for occasional point readings (see B8.3's own
    # already-written note on this), and a single daily integer fits
    # that shape without pretending to be a real time-series store.
    # Denser wearable signals (a CGM feed, minute-by-minute heart rate)
    # remain B8.3's explicitly deferred, larger schema question.
    steps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ldl_mg_dl: Mapped[float | None] = mapped_column(Float, nullable=True)
    hdl_mg_dl: Mapped[float | None] = mapped_column(Float, nullable=True)
    total_cholesterol_mg_dl: Mapped[float | None] = mapped_column(Float, nullable=True)
    triglycerides_mg_dl: Mapped[float | None] = mapped_column(Float, nullable=True)
    blood_pressure_systolic: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blood_pressure_diastolic: Mapped[int | None] = mapped_column(Integer, nullable=True)
    blood_glucose_mg_dl: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Backlog B18.1 (2026-08-18). The 2026 ACC/AHA multi-society
    # dyslipidemia guideline names apolipoprotein B and lipoprotein(a) as
    # measurements that change risk assessment, and HbA1c and waist
    # circumference are the two other things a household working on LDL is
    # routinely handed by a doctor. None of them had anywhere to go, in an
    # app whose stated purpose is LDL reduction -- so they were transcribed
    # nowhere and trended never.
    apob_mg_dl: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Lp(a) is reported in BOTH mg/dL and nmol/L, and the two are NOT
    # reliably interconvertible: the conversion depends on apo(a) isoform
    # size, which varies between people, so any fixed factor is an
    # approximation that different labs and guidelines disagree about.
    # Storing a bare number would silently mix two scales in one trend
    # line. The unit rides WITH the value, and the UI shows it -- same
    # discipline as nutrition provenance: carry what you know, do not
    # convert what you cannot.
    lpa_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    lpa_unit: Mapped[str | None] = mapped_column(String(10), nullable=True)  # mg_dl | nmol_l

    hba1c_percent: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Waist circumference, cm. The 2025 Lancet Commission on clinical
    # obesity moved diagnosis off BMI alone toward BMI plus an
    # anthropometric measure; waist-to-height is computed from this and the
    # member's height (see health_service.waist_to_height_ratio).
    waist_cm: Mapped[float | None] = mapped_column(Float, nullable=True)

    source: Mapped[str] = mapped_column(String(20), default="manual")  # manual|import
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
