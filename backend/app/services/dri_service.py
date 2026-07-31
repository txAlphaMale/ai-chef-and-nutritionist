"""Backlog B1.4 -- per-member daily nutrient targets (Dietary Reference
Intake-style), computed from age/sex/height/weight/activity_level. Every
one of those fields already exists on HouseholdMember/HealthMetricEntry
(Phase 6) and was, before this module, never actually used for anything
-- logged into a chart nobody could act on. This is the piece that turns
those numbers into something a meal-plan nutrition roll-up
(meal_plan_service.compute_nutrition_summary) can compare against.

Values used here are sourced from published guidance, not invented, and
are noted per-field since these figures genuinely differ across editions
and it matters which one this app is claiming to follow (checked live,
2026-08-01, rather than assumed from training data -- the 2025-2030
Dietary Guidelines for Americans were released in January 2026 and are
current as of this writing):

- Calories: Mifflin-St Jeor BMR x an activity-level multiplier (a
  decades-standard equation + PAL-category combination; not itself a
  DGA/DRI figure, just the least-bad widely-used estimate of energy
  need without indirect calorimetry).
- Protein: the 2025-2030 DGA raised its protein guidance to a
  1.2-1.6 g/kg body weight/day range -- a genuinely debated increase
  from the older, decades-standard 0.8 g/kg RDA (which remains valid as
  a deficiency-prevention FLOOR, just isn't what current DGA guidance
  recommends as a target anymore). This module uses 1.2 g/kg -- the low
  end of the new range -- since the stated household context here is
  sedentary; the higher end of that range is more often justified for
  strength-training/athletic populations this household isn't.
- Carbohydrate/fat: IOM Acceptable Macronutrient Distribution Ranges
  (AMDR), stable for over a decade and not revised by the 2025-2030 DGA:
  carbs 45-65% of calories, fat 20-35%. Midpoints used (55%, 27.5%).
- Saturated fat: DGA cap, unchanged in the 2025-2030 edition (despite
  that edition's separate, debated endorsement of full-fat dairy/red
  meat/butter elsewhere) -- less than 10% of calories.
- Added sugars: DGA cap, unchanged -- less than 10% of calories.
- Sodium: DGA cap, unchanged -- 2,300 mg/day flat, not calorie-scaled.
- Fiber: IOM DRI Adequate Intake, age/sex-banded rather than calorie-
  derived -- 38g/day for men <=50, 30g/day for men >50; 25g/day for
  women <=50, 21g/day for women >50.
- Cholesterol: the DGA has published no specific numeric cap since 2015
  ("as low as possible while consuming a healthy eating pattern");
  300 mg/day is used here as a documented, conservative REFERENCE value
  historically common in clinical guidance (relevant given this
  project's stated LDL-reduction goal) -- flagged in the field name/
  description as a reference, not presented as a current official
  DRI/DGA figure the way sodium/saturated-fat/added-sugar caps are.

None of this is medical advice -- it is a transparent, assumption-stated
planning target, same "informational, not diagnostic" framing already
used for BMI category in health_service.py. A household member with
different, clinician-provided targets should treat this as a rough
default, not an override.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import HealthMetricEntry, HouseholdMember

ACTIVITY_MULTIPLIERS: dict[str, float] = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "active": 1.725,
}
DEFAULT_ACTIVITY_MULTIPLIER = ACTIVITY_MULTIPLIERS["sedentary"]

PROTEIN_G_PER_KG = 1.2
CARB_CALORIE_FRACTION = 0.55
FAT_CALORIE_FRACTION = 0.275
SATURATED_FAT_CALORIE_FRACTION = 0.10
ADDED_SUGAR_CALORIE_FRACTION = 0.10
SODIUM_MG_CAP = 2300
CHOLESTEROL_MG_REFERENCE = 300  # documented reference value, not a current official DRI/DGA cap -- see module docstring

FIBER_TARGETS_G: dict[tuple[str, bool], float] = {
    ("male", True): 38,  # age <= 50
    ("male", False): 30,  # age > 50
    ("female", True): 25,
    ("female", False): 21,
}


def compute_bmr_mifflin_st_jeor(weight_kg: float, height_cm: float, age: int, sex: str | None) -> float:
    """Mifflin-St Jeor basal metabolic rate. `sex` of "male"/"female"
    uses the standard offset; anything else (unspecified/other) uses the
    midpoint of the two offsets rather than guessing a sex -- a
    documented approximation, not a claim that BMR doesn't vary by sex
    for reasons unrelated to the male/female offset itself."""
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    if sex == "male":
        return base + 5
    if sex == "female":
        return base - 161
    return base - 78  # midpoint of +5 and -161


def _fiber_target_g(sex: str | None, age: int | None) -> float:
    sex_key = sex if sex in ("male", "female") else "female"
    is_younger = age is None or age <= 50
    return FIBER_TARGETS_G[(sex_key, is_younger)]


def compute_daily_targets(
    *,
    weight_kg: float | None,
    height_cm: float | None,
    age: int | None,
    sex: str | None,
    activity_level: str | None,
) -> dict[str, float] | None:
    """Returns None -- never a guessed number -- if weight, height, or
    age is missing; BMR has no meaningful fallback for any of the three.
    Sex and activity_level DO have documented fallbacks (see
    compute_bmr_mifflin_st_jeor and DEFAULT_ACTIVITY_MULTIPLIER) since
    "unspecified" is itself a meaningful, non-guessed state for those
    two, unlike a missing weight/height/age which has no safe default at
    all."""
    if weight_kg is None or height_cm is None or age is None:
        return None

    bmr = compute_bmr_mifflin_st_jeor(weight_kg, height_cm, age, sex)
    multiplier = ACTIVITY_MULTIPLIERS.get(activity_level, DEFAULT_ACTIVITY_MULTIPLIER)
    calories = bmr * multiplier

    return {
        "calories": round(calories),
        "protein_g": round(weight_kg * PROTEIN_G_PER_KG, 1),
        "carbs_g": round((calories * CARB_CALORIE_FRACTION) / 4, 1),
        "fat_g": round((calories * FAT_CALORIE_FRACTION) / 9, 1),
        "saturated_fat_g": round((calories * SATURATED_FAT_CALORIE_FRACTION) / 9, 1),
        "fiber_g": _fiber_target_g(sex, age),
        "sugars_g": round((calories * ADDED_SUGAR_CALORIE_FRACTION) / 4, 1),
        "sodium_mg": SODIUM_MG_CAP,
        "cholesterol_mg": CHOLESTEROL_MG_REFERENCE,
    }


def get_latest_weight_kg(db: Session, member_id: int) -> float | None:
    entry = (
        db.query(HealthMetricEntry)
        .filter(HealthMetricEntry.household_member_id == member_id, HealthMetricEntry.weight_kg.isnot(None))
        .order_by(HealthMetricEntry.entry_date.desc())
        .first()
    )
    return entry.weight_kg if entry else None


def compute_member_daily_targets(db: Session, member: HouseholdMember) -> tuple[dict[str, float] | None, list[str]]:
    """Returns (targets, missing_fields). `missing_fields` names exactly
    what's absent (e.g. ["weight"]) rather than the caller having to
    infer why targets came back None -- so the UI can say "log a weight
    entry to see a target for Jason" instead of just hiding the row."""
    weight_kg = get_latest_weight_kg(db, member.id)
    missing = []
    if weight_kg is None:
        missing.append("weight")
    if member.height_cm is None:
        missing.append("height")
    if member.age is None:
        missing.append("age")
    if missing:
        return None, missing
    targets = compute_daily_targets(
        weight_kg=weight_kg,
        height_cm=member.height_cm,
        age=member.age,
        sex=member.sex,
        activity_level=member.activity_level,
    )
    return targets, []
