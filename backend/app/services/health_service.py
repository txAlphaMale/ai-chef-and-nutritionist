"""Health/nutrition business logic: BMI calculation and categorization,
trend computation over HealthMetricEntry history, and building the
health/knowledge-file grounding text injected into meal-plan generation
(meal_plan_service.py) so plans can steer toward healthy numbers without
the household having to spell that out every time.

Pure/testable functions (compute_bmi, bmi_category, compute_metric_trend,
format_member_health_line, _format_knowledge_excerpts) are kept separate
from the DB-touching wrappers around them, same pattern as inventory_
service.py, recipe_service.py, and meal_plan_service.py.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.models import HealthMetricEntry, HouseholdMember
from app.services import knowledge_service

# --- BMI -----------------------------------------------------------------


def compute_bmi(weight_kg: float | None, height_cm: float | None) -> float | None:
    if not weight_kg or not height_cm or height_cm <= 0:
        return None
    height_m = height_cm / 100
    return round(weight_kg / (height_m**2), 1)


def bmi_category(bmi: float) -> str:
    """Standard WHO adult BMI bands. Informational only -- not medical
    advice; surfaced in the UI/AI grounding as a rough signal, not a
    diagnosis."""
    if bmi < 18.5:
        return "underweight"
    if bmi < 25:
        return "normal"
    if bmi < 30:
        return "overweight"
    return "obese"


# --- Trend computation -----------------------------------------------------


def compute_metric_trend(entries: list[dict], field: str, window_days: int = 90) -> dict | None:
    """entries: list of dicts with an "entry_date" (date) key and the
    metric field, in any order. Compares the most recent data point
    against the oldest data point within `window_days` of it (falling
    back to the oldest data point overall if none fall inside the
    window) -- e.g. "down 3kg over the last 62 days". Returns None if
    fewer than 2 non-null points exist for this field."""
    points = [(e["entry_date"], e[field]) for e in entries if e.get(field) is not None]
    if len(points) < 2:
        return None
    points.sort(key=lambda p: p[0])  # ascending by date
    latest_date, latest_value = points[-1]
    cutoff = latest_date - timedelta(days=window_days)
    candidates = [p for p in points[:-1] if p[0] >= cutoff]
    baseline_date, baseline_value = candidates[0] if candidates else points[0]
    return {
        "latest_value": latest_value,
        "latest_date": latest_date,
        "baseline_value": baseline_value,
        "baseline_date": baseline_date,
        "delta": round(latest_value - baseline_value, 2),
        "days_span": (latest_date - baseline_date).days,
    }


# --- Meal-plan grounding: household health context ------------------------
#
# Consumed by meal_plan_service.gather_generation_context() so generation
# can favor lower-cholesterol, appropriately-portioned meals when a
# member's numbers suggest it, without the household having to restate
# their goals in every generation request.


def format_member_health_line(name: str, latest: dict, weight_trend: dict | None) -> str:
    parts = []
    if latest.get("weight_kg") is not None:
        parts.append(f"weight {latest['weight_kg']}kg")
    if latest.get("bmi") is not None:
        parts.append(f"BMI {latest['bmi']} ({bmi_category(latest['bmi'])})")
    if latest.get("ldl_mg_dl") is not None:
        parts.append(f"LDL {latest['ldl_mg_dl']} mg/dL")
    if latest.get("hdl_mg_dl") is not None:
        parts.append(f"HDL {latest['hdl_mg_dl']} mg/dL")
    if latest.get("total_cholesterol_mg_dl") is not None:
        parts.append(f"total cholesterol {latest['total_cholesterol_mg_dl']} mg/dL")
    if not parts:
        return ""

    line = f"{name}: " + ", ".join(parts)
    if weight_trend and weight_trend["delta"] != 0:
        direction = "down" if weight_trend["delta"] < 0 else "up"
        line += f"; weight {direction} {abs(weight_trend['delta'])}kg over the last {weight_trend['days_span']} days"
    return line


def build_health_context_summary(db: Session) -> str:
    members = db.query(HouseholdMember).all()
    lines = []
    for member in members:
        entries = (
            db.query(HealthMetricEntry)
            .filter_by(household_member_id=member.id)
            .order_by(HealthMetricEntry.entry_date.desc())
            .all()
        )
        if not entries:
            continue
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
        latest = entry_dicts[0]
        weight_trend = compute_metric_trend(entry_dicts, "weight_kg")
        line = format_member_health_line(member.name, latest, weight_trend)
        if line:
            lines.append(line)
    return "\n".join(lines)


# --- Meal-plan/chat grounding: nutritionist knowledge files ----------------
#
# Upgraded 2026-07-31 from "concatenate every active file, truncated to a
# combined character budget" to real retrieval: knowledge_service.
# search_knowledge embeds `query` and returns only the top-k most
# relevant chunks across the knowledge base, so grounding scales past a
# handful of short files instead of quietly truncating large/numerous
# ones out. See knowledge_service.py's module docstring for the full
# design writeup (chunking, embedding, retrieval, and what was
# deliberately NOT ported from the Fiduciary project this was modeled
# on). `query` should be whatever text best represents what the caller
# actually needs grounded -- meal_plan_service builds one from household
# dietary restrictions/goals; chat_service uses the user's actual
# message, the more natural fit for per-turn retrieval.


def _format_knowledge_results(results: list[dict]) -> str:
    if not results:
        return ""
    return "\n\n".join(f"[{r['source']}]\n{r['text']}" for r in results)


def build_knowledge_context(db: Session, query: str, k: int = 4) -> str:
    results = knowledge_service.search_knowledge(db, query, k=k)
    return _format_knowledge_results(results)
