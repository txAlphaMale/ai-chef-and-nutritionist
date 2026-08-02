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
from app.services import knowledge_service, ollama_client
from app.services.recipe_service import _extract_json_object, _safe_float, _safe_int, extract_pdf_text

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


# --- Bloodwork import (backlog B8.1) ---------------------------------------
#
# Metrics were previously only enterable one field at a time via the manual
# form, which the backlog text correctly called out as unrealistic to keep
# up with -- nobody types in six numbers from a lab report every quarter.
# Same architecture as every other unstructured-document import in this app
# (recipe text/PDF/photo, inventory vision-intake, receipt OCR): extract
# text (or hand a photo straight to the vision model), ask Ollama for
# strict JSON, defensively parse whatever comes back, and return a PREVIEW
# for the user to review/edit -- nothing is written to health_metric_entries
# here. The frontend confirms each accepted row through the existing
# POST /api/health/metrics endpoint, same "preview then reuse the real
# create endpoint" discipline as recipe import and the dining-out
# send-to-meal-plan flow, so BMI still gets computed the one existing way
# rather than duplicating that logic.
#
# Deliberately NOT a per-lab column-mapping profile system the way B10.3's
# order-history importer is: a grocery receipt's column layout is far more
# consistent within one retailer than lab report WORDING is even within one
# lab (LDL alone shows up as "LDL", "LDL Cholesterol", "LDL Cholesterol
# Calc", "LDL-C", etc., often on the same page as an HDL/total/triglyceride
# panel with its own separate wording quirks) -- free-text LLM extraction
# is the better-fitting tool here, the same conclusion recipe/receipt
# import already reached for their own free-text sources.
#
# Unit conversion is asked of the model directly in the prompt (lbs->kg,
# mmol/L->mg/dL) rather than attempted as a separate deterministic pass
# afterward, since which unit a given lab report used has to be read from
# the same unstructured text being parsed anyway. This is honestly a
# weaker guarantee than the deterministic unit_conversion_service used
# elsewhere in this app (B5.3/B10.5) -- flagged plainly in the prompt's own
# "ai_estimated"-equivalent framing below and in HealthPage.jsx's import
# panel copy, not silently presented as exact.

BLOODWORK_IMPORT_PROMPT = """\
Extract lab/bloodwork results from the content below and respond with \
ONLY a JSON object (no other text, no markdown fences) with a single key \
"entries": an array of objects, one per distinct draw/report date found \
in the content (usually just one). Each entry object has these keys, \
using null for anything not actually present in the content -- NEVER \
invent a plausible-sounding number, and NEVER report a "normal reference \
range" value as if it were the patient's actual result:
- "entry_date": string "YYYY-MM-DD" (the date the sample was collected \
or the report was issued), or null if genuinely not stated
- "weight_kg": body weight in KILOGRAMS -- convert from pounds if the \
source uses lbs (kg = lbs / 2.20462), or null
- "ldl_mg_dl": LDL cholesterol in mg/dL -- convert from mmol/L if needed \
(mg/dL = mmol/L * 38.67), or null
- "hdl_mg_dl": HDL cholesterol in mg/dL (same mmol/L conversion if \
needed), or null
- "total_cholesterol_mg_dl": total cholesterol in mg/dL (same \
conversion), or null
- "triglycerides_mg_dl": triglycerides in mg/dL -- convert from mmol/L \
if needed (mg/dL = mmol/L * 88.57), or null
- "blood_pressure_systolic": integer mmHg, or null
- "blood_pressure_diastolic": integer mmHg, or null
- "blood_glucose_mg_dl": blood glucose in mg/dL -- convert from mmol/L \
if needed (mg/dL = mmol/L * 18.02), or null

Content:
{content}"""


def extract_bloodwork_text(raw_bytes: bytes, filename: str, content_type: str) -> str:
    """Plain-text extraction for the two non-image bloodwork import file
    types (PDF lab reports, CSV/plain-text exports) -- mirrors recipe_
    service.extract_pdf_text's usage exactly. Image files are handled
    separately by the router via ollama_client.describe_image, same
    split as recipe import's photo branch, since a vision call needs the
    raw bytes directly rather than an extracted-text intermediate."""
    filename_lower = (filename or "").lower()
    if content_type == "application/pdf" or filename_lower.endswith(".pdf"):
        return extract_pdf_text(raw_bytes)
    try:
        return raw_bytes.decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 -- defensive; decode() with errors="replace" shouldn't raise
        return ""


def _parse_entry_date(value) -> str | None:
    """Returns an ISO "YYYY-MM-DD" string if `value` parses as one,
    else None -- never raises. Kept as a string (not a `date`) here since
    this is a JSON-preview response, not a DB write; routers/health.py's
    existing HealthMetricEntryCreate does the real date validation at
    confirm time."""
    if not value or not isinstance(value, str):
        return None
    from datetime import datetime as _datetime

    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%d/%m/%Y"):
        try:
            return _datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


BLOODWORK_FIELDS = [
    "weight_kg",
    "ldl_mg_dl",
    "hdl_mg_dl",
    "total_cholesterol_mg_dl",
    "triglycerides_mg_dl",
    "blood_glucose_mg_dl",
]


def parse_bloodwork_response(raw_text: str) -> list[dict]:
    """Defensively extracts the entries array from raw model output.
    Drops any entry with zero actual metric values -- a common
    real-world model failure mode is a well-formed but entirely-null
    object when the source genuinely had nothing extractable, and a
    date-only/empty row isn't worth showing the user a preview row for."""
    data = _extract_json_object(raw_text)
    entries_raw = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries_raw, list):
        return []

    entries = []
    for e in entries_raw:
        if not isinstance(e, dict):
            continue
        entry = {
            "entry_date": _parse_entry_date(e.get("entry_date")),
            "weight_kg": _safe_float(e.get("weight_kg")),
            "ldl_mg_dl": _safe_float(e.get("ldl_mg_dl")),
            "hdl_mg_dl": _safe_float(e.get("hdl_mg_dl")),
            "total_cholesterol_mg_dl": _safe_float(e.get("total_cholesterol_mg_dl")),
            "triglycerides_mg_dl": _safe_float(e.get("triglycerides_mg_dl")),
            "blood_pressure_systolic": _safe_int(e.get("blood_pressure_systolic")),
            "blood_pressure_diastolic": _safe_int(e.get("blood_pressure_diastolic")),
            "blood_glucose_mg_dl": _safe_float(e.get("blood_glucose_mg_dl")),
        }
        has_any_value = any(entry[f] is not None for f in BLOODWORK_FIELDS) or (
            entry["blood_pressure_systolic"] is not None and entry["blood_pressure_diastolic"] is not None
        )
        if has_any_value:
            entries.append(entry)
    return entries


def run_bloodwork_extraction(db: Session, content: str) -> str:
    """The one Ollama-calling step -- always invoked from inside a
    background job body (job_queue, backlog B11.1), never directly from
    a request handler, same discipline as every other AI-consuming
    endpoint in this app since that backlog item.

    Bug fix (2026-08-02, author-reported follow-up): used to hard-cap
    content at a flat `content[:8000]` -- see ollama_client.
    content_char_budget's docstring."""
    budget = ollama_client.content_char_budget(
        db, prompt_overhead_chars=len(BLOODWORK_IMPORT_PROMPT), response_reserve_tokens=1500
    )
    prompt = BLOODWORK_IMPORT_PROMPT.format(content=content[:budget])
    response = ollama_client.chat(db, [{"role": "user", "content": prompt}])
    return ollama_client.extract_content(response)
