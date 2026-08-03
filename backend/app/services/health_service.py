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
# Retrieval-based: knowledge_service.search_knowledge embeds `query` and
# returns only the top-k most relevant chunks across the knowledge base,
# so grounding scales past a handful of short files rather than quietly
# truncating large or numerous ones out. See knowledge_service.py's module docstring for the full
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
    except Exception:
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

    Content is capped by ollama_client.content_char_budget, which scales
    with the configured context window."""
    budget = ollama_client.content_char_budget(
        db, prompt_overhead_chars=len(BLOODWORK_IMPORT_PROMPT), response_reserve_tokens=1500
    )
    prompt = BLOODWORK_IMPORT_PROMPT.format(content=content[:budget])
    response = ollama_client.chat(db, [{"role": "user", "content": prompt}])
    return ollama_client.extract_content(response)


# --- Wearable/health-platform import (backlog B8.2) -------------------
#
# Scoped to what could actually be VERIFIED rather than guessed, per
# this app's standing "never guess a format, or say so" discipline (the
# same discipline B10.3's Walmart research and B4.3's FoodKeeper sourcing
# both already document). Two paths:
#
# 1. Apple Health's `export.xml` (optionally inside the `export.zip` the
#    Health app produces via Share -> Export All Health Data) is a real,
#    stable, long-documented XML format -- HKQuantityTypeIdentifierBodyMass
#    and HKQuantityTypeIdentifierStepCount records, each with a `value`,
#    `unit`, and `startDate`. Parsed deterministically below, no AI call
#    needed at all for this path.
#
# 2. Everything else (a Google Health Connect export, a Withings CSV, a
#    third-party wearable-export tool's own file) is routed through the
#    SAME free-text Ollama extraction pipeline BLOODWORK_IMPORT_PROMPT
#    already established, via a sibling WEARABLE_IMPORT_PROMPT. This is a
#    deliberate choice, not a fallback of convenience: verified live
#    that Android Health Connect's own native
#    "export" is a proprietary backup ZIP meant for Health-Connect-to-
#    Health-Connect restore, not a documented, stable third-party-
#    parseable schema -- no official JSON/CSV export format exists to
#    build a deterministic parser against, the same class of gap this
#    app hit with Walmart's purchase history (B10.3). Rather than guess
#    at an undocumented format and silently misparse it, this reuses the
#    same free-text/CSV extraction path bloodwork import already proved
#    out -- a household exporting via a third-party Health Connect tool
#    (several of which produce CSV) or a Withings export gets a real,
#    working import path, just not a hand-built parser for a format that
#    was never actually verified to exist in a stable, public shape.

WEARABLE_IMPORT_PROMPT = """\
Extract daily body-weight and step-count entries from the content below \
(a Health Connect, Withings, or other fitness/health app export) and \
respond with ONLY a JSON object (no other text, no markdown fences) with \
a single key "entries": an array of objects, one per distinct calendar \
date found in the content. Each entry object has these keys, using null \
for anything not actually present -- NEVER invent a plausible-sounding \
number:
- "entry_date": string "YYYY-MM-DD", or null if genuinely not stated
- "weight_kg": body weight in KILOGRAMS -- convert from pounds if the \
source uses lbs (kg = lbs / 2.20462), or null
- "steps": integer step count for that date (if the source has multiple \
step readings for the same date, sum them into one daily total), or null

Content:
{content}"""


def extract_wearable_text(raw_bytes: bytes, filename: str, content_type: str) -> str:
    """Same plain-text extraction as extract_bloodwork_text, for the
    AI-extraction (non-Apple-Health) path -- PDF via pypdf, else decoded
    as UTF-8 text (covers CSV/JSON/plain-text exports alike, since the
    model reads the raw text either way)."""
    return extract_bloodwork_text(raw_bytes, filename, content_type)


def run_wearable_extraction(db: Session, content: str) -> str:
    budget = ollama_client.content_char_budget(
        db, prompt_overhead_chars=len(WEARABLE_IMPORT_PROMPT), response_reserve_tokens=1500
    )
    prompt = WEARABLE_IMPORT_PROMPT.format(content=content[:budget])
    response = ollama_client.chat(db, [{"role": "user", "content": prompt}])
    return ollama_client.extract_content(response)


def parse_wearable_ai_response(raw_text: str) -> list[dict]:
    """Same defensive-JSON-object-extraction discipline as
    parse_bloodwork_response. Drops entries with neither weight nor
    steps -- a date-only row with nothing else extracted isn't worth a
    preview row."""
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
            "steps": _safe_int(e.get("steps")),
        }
        if entry["weight_kg"] is not None or entry["steps"] is not None:
            entries.append(entry)
    return entries


# Apple HealthKit type identifiers this importer actually reads --
# deliberately a small, verified set (weight + steps, matching the
# backlog's own "weight and activity" framing) rather than attempting
# every HK type an export.xml can contain.
_APPLE_HEALTH_WEIGHT_TYPE = "HKQuantityTypeIdentifierBodyMass"
_APPLE_HEALTH_STEPS_TYPE = "HKQuantityTypeIdentifierStepCount"


def _apple_health_xml_bytes(raw_bytes: bytes, filename: str) -> bytes:
    """Apple's Health app exports a `export.zip` containing `export.xml`
    (usually nested under an `apple_health_export/` folder) -- but also
    accepts a raw, already-unzipped `export.xml` upload directly, since
    a household may have already extracted it. Returns the raw XML bytes
    either way."""
    filename_lower = (filename or "").lower()
    if filename_lower.endswith(".zip") or raw_bytes[:2] == b"PK":
        import zipfile
        from io import BytesIO

        with zipfile.ZipFile(BytesIO(raw_bytes)) as zf:
            xml_names = [n for n in zf.namelist() if n.lower().endswith("export.xml")]
            if not xml_names:
                raise ValueError("This .zip doesn't contain an export.xml -- is it a real Apple Health export?")
            # Prefer the shortest matching path -- Apple nests the real
            # export at "apple_health_export/export.xml"; a longer match
            # is more likely to be the separate, much larger
            # "export_cda.xml" clinical-records companion file, which
            # this importer doesn't parse.
            xml_names.sort(key=len)
            return zf.read(xml_names[0])
    return raw_bytes


def parse_apple_health_export(raw_bytes: bytes, filename: str) -> list[dict]:
    """Deterministic, no-Ollama parse of an Apple Health export.xml (or
    the export.zip containing one). Streams via `iterparse` and clears
    each `<Record>` element immediately after reading it -- a real
    multi-year export.xml can be hundreds of MB to multiple GB and
    holding the whole tree in memory (a plain `ET.parse`) is not viable
    on the modest hardware this app targets.

    Aggregation, per calendar day (the `startDate` attribute's first 10
    characters -- Apple's own "YYYY-MM-DD HH:MM:SS +ZZZZ" format, so a
    plain string slice is correct and doesn't need a timezone-aware
    datetime parse):
    - weight: the LAST sample of the day wins (a body-weight scale is
      typically stepped on once, at most a handful of times, per day --
      most-recent is a more sensible "the weight for that day" signal
      than an average across a handful of same-day readings).
    - steps: SUMMED across the day -- HealthKit records step counts as
      many small increments throughout the day (each walk/errand is its
      own sample), so summing is the only aggregation that produces a
      correct daily total; taking "the last one" would silently report
      only the day's final short walk.

    Unit handling: weight can be exported in "lb" or "kg" (whatever the
    device/Health app was set to at recording time, which can genuinely
    vary sample-to-sample if the household changed their unit
    preference partway through their Health history) -- converted to kg
    per-sample before aggregation, never assumed constant across the
    whole file."""
    import io
    from xml.etree import ElementTree as ET

    xml_bytes = _apple_health_xml_bytes(raw_bytes, filename)

    daily_weight_kg: dict[str, float] = {}
    daily_steps: dict[str, float] = {}

    with io.BytesIO(xml_bytes) as stream:
        for _event, elem in ET.iterparse(stream, events=("end",)):
            if elem.tag != "Record":
                continue
            record_type = elem.get("type")
            day = (elem.get("startDate") or "")[:10]
            if day and record_type == _APPLE_HEALTH_WEIGHT_TYPE:
                value = _safe_float(elem.get("value"))
                unit = (elem.get("unit") or "").strip().lower()
                if value is not None:
                    kg = value / 2.20462 if unit in ("lb", "lbs", "pound", "pounds") else value
                    daily_weight_kg[day] = kg  # last-wins: later records overwrite earlier same-day ones
            elif day and record_type == _APPLE_HEALTH_STEPS_TYPE:
                value = _safe_float(elem.get("value"))
                if value is not None:
                    daily_steps[day] = daily_steps.get(day, 0) + value
            elem.clear()  # bound memory use across a very large export

    all_days = sorted(set(daily_weight_kg) | set(daily_steps))
    return [
        {
            "entry_date": day,
            "weight_kg": round(daily_weight_kg[day], 2) if day in daily_weight_kg else None,
            "steps": round(daily_steps[day]) if day in daily_steps else None,
        }
        for day in all_days
    ]
