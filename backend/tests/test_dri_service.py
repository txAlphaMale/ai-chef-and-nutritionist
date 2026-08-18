"""Unit tests for the B1.4 DRI target service (app/services/dri_service.py).

compute_bmr_mifflin_st_jeor/compute_daily_targets are pure functions, no
DB needed. get_latest_weight_kg/compute_member_daily_targets touch the
DB (a member's latest HealthMetricEntry), so those use db_session.
"""

from __future__ import annotations

from datetime import date

from app.models import HealthMetricEntry, HouseholdMember
from app.services import dri_service as dri

# --- compute_bmr_mifflin_st_jeor --------------------------------------------


def test_bmr_male_matches_hand_calculation():
    # 10*80 + 6.25*180 - 5*30 + 5 = 800 + 1125 - 150 + 5 = 1780
    assert dri.compute_bmr_mifflin_st_jeor(80, 180, 30, "male") == 1780


def test_bmr_female_matches_hand_calculation():
    # 10*65 + 6.25*165 - 5*40 - 161 = 650 + 1031.25 - 200 - 161 = 1320.25
    assert dri.compute_bmr_mifflin_st_jeor(65, 165, 40, "female") == 1320.25


def test_bmr_unspecified_sex_uses_midpoint_offset():
    base = 10 * 70 + 6.25 * 170 - 5 * 35
    assert dri.compute_bmr_mifflin_st_jeor(70, 170, 35, None) == base - 78
    assert dri.compute_bmr_mifflin_st_jeor(70, 170, 35, "other") == base - 78


# --- compute_daily_targets ---------------------------------------------------


def test_compute_daily_targets_returns_none_without_weight():
    assert (
        dri.compute_daily_targets(weight_kg=None, height_cm=170, age=40, sex="male", activity_level="sedentary") is None
    )


def test_compute_daily_targets_returns_none_without_height():
    assert (
        dri.compute_daily_targets(weight_kg=80, height_cm=None, age=40, sex="male", activity_level="sedentary") is None
    )


def test_compute_daily_targets_returns_none_without_age():
    assert (
        dri.compute_daily_targets(weight_kg=80, height_cm=170, age=None, sex="male", activity_level="sedentary") is None
    )


def test_compute_daily_targets_sedentary_male_hand_checked():
    targets = dri.compute_daily_targets(weight_kg=80, height_cm=180, age=30, sex="male", activity_level="sedentary")
    bmr = 1780
    calories = bmr * 1.2  # sedentary multiplier
    assert targets["calories"] == round(calories)
    assert targets["protein_g"] == round(80 * 1.2, 1)
    assert targets["carbs_g"] == round((calories * 0.55) / 4, 1)
    assert targets["fat_g"] == round((calories * 0.275) / 9, 1)
    assert targets["saturated_fat_g"] == round((calories * 0.10) / 9, 1)
    assert targets["sugars_g"] == round((calories * 0.10) / 4, 1)
    assert targets["sodium_mg"] == 2300
    assert targets["cholesterol_mg"] == 300
    assert targets["fiber_g"] == 38  # male, <=50


def test_compute_daily_targets_unknown_activity_level_falls_back_to_sedentary():
    with_sedentary = dri.compute_daily_targets(
        weight_kg=80, height_cm=180, age=30, sex="male", activity_level="sedentary"
    )
    with_unknown = dri.compute_daily_targets(weight_kg=80, height_cm=180, age=30, sex="male", activity_level="bogus")
    assert with_sedentary["calories"] == with_unknown["calories"]


def test_compute_daily_targets_active_multiplier_increases_calories():
    sedentary = dri.compute_daily_targets(weight_kg=80, height_cm=180, age=30, sex="male", activity_level="sedentary")
    active = dri.compute_daily_targets(weight_kg=80, height_cm=180, age=30, sex="male", activity_level="active")
    assert active["calories"] > sedentary["calories"]


# --- fiber age/sex banding ---------------------------------------------------


def test_fiber_target_male_over_50_is_lower():
    younger = dri.compute_daily_targets(weight_kg=80, height_cm=180, age=45, sex="male", activity_level="sedentary")
    older = dri.compute_daily_targets(weight_kg=80, height_cm=180, age=55, sex="male", activity_level="sedentary")
    assert younger["fiber_g"] == 38
    assert older["fiber_g"] == 30


def test_fiber_target_female_over_50_is_lower():
    younger = dri.compute_daily_targets(weight_kg=65, height_cm=165, age=45, sex="female", activity_level="sedentary")
    older = dri.compute_daily_targets(weight_kg=65, height_cm=165, age=55, sex="female", activity_level="sedentary")
    assert younger["fiber_g"] == 25
    assert older["fiber_g"] == 21


def test_fiber_target_unspecified_sex_defaults_to_female_band():
    targets = dri.compute_daily_targets(weight_kg=70, height_cm=170, age=30, sex=None, activity_level="sedentary")
    assert targets["fiber_g"] == 25


# --- get_latest_weight_kg / compute_member_daily_targets (DB-backed) -------


def test_get_latest_weight_kg_returns_most_recent_entry(db_session):
    member = HouseholdMember(name="Jason", age=45, height_cm=178, sex="male", activity_level="sedentary")
    db_session.add(member)
    db_session.commit()

    db_session.add_all(
        [
            HealthMetricEntry(household_member_id=member.id, entry_date=date(2026, 1, 1), weight_kg=95.0),
            HealthMetricEntry(household_member_id=member.id, entry_date=date(2026, 6, 1), weight_kg=90.0),
        ]
    )
    db_session.commit()

    assert dri.get_latest_weight_kg(db_session, member.id) == 90.0


def test_get_latest_weight_kg_none_when_no_entries(db_session):
    member = HouseholdMember(name="Jason")
    db_session.add(member)
    db_session.commit()
    assert dri.get_latest_weight_kg(db_session, member.id) is None


def test_get_latest_weight_kg_skips_entries_with_no_weight(db_session):
    member = HouseholdMember(name="Jason")
    db_session.add(member)
    db_session.commit()
    db_session.add(
        HealthMetricEntry(household_member_id=member.id, entry_date=date(2026, 6, 1), weight_kg=None, ldl_mg_dl=110)
    )
    db_session.commit()
    assert dri.get_latest_weight_kg(db_session, member.id) is None


def test_compute_member_daily_targets_full_data(db_session):
    member = HouseholdMember(name="Jason", age=45, height_cm=178, sex="male", activity_level="sedentary")
    db_session.add(member)
    db_session.commit()
    db_session.add(HealthMetricEntry(household_member_id=member.id, entry_date=date(2026, 6, 1), weight_kg=95.0))
    db_session.commit()

    targets, missing = dri.compute_member_daily_targets(db_session, member)
    assert missing == []
    assert targets is not None
    assert targets["calories"] > 0


def test_compute_member_daily_targets_missing_weight_reports_it(db_session):
    member = HouseholdMember(name="Jason", age=45, height_cm=178, sex="male")
    db_session.add(member)
    db_session.commit()

    targets, missing = dri.compute_member_daily_targets(db_session, member)
    assert targets is None
    assert missing == ["weight"]


def test_compute_member_daily_targets_reports_every_missing_field(db_session):
    member = HouseholdMember(name="Jason")
    db_session.add(member)
    db_session.commit()

    targets, missing = dri.compute_member_daily_targets(db_session, member)
    assert targets is None
    assert set(missing) == {"weight", "height", "age"}


# --- Age from a birth date (author-requested 2026-08-18) ------------------


def test_age_is_computed_from_a_birth_date():
    from datetime import date

    from app.services.household_age import age_from_birth_date

    born = date(1977, 6, 15)
    assert age_from_birth_date(born, today=date(2026, 6, 14)) == 48, "day before the birthday"
    assert age_from_birth_date(born, today=date(2026, 6, 15)) == 49, "on the birthday"
    assert age_from_birth_date(born, today=date(2026, 6, 16)) == 49


def test_a_leap_day_birthday_does_not_crash_in_a_common_year():
    from datetime import date

    from app.services.household_age import age_from_birth_date

    born = date(2000, 2, 29)
    # Treated as not-yet-reached until 1 March in a non-leap year: the
    # conservative convention, and immaterial to a nutrient target.
    assert age_from_birth_date(born, today=date(2026, 2, 28)) == 25
    assert age_from_birth_date(born, today=date(2026, 3, 1)) == 26


def test_a_future_or_missing_birth_date_is_no_age_rather_than_a_negative_one():
    from datetime import date

    from app.services.household_age import age_from_birth_date

    assert age_from_birth_date(None) is None
    assert age_from_birth_date(date(2030, 1, 1), today=date(2026, 1, 1)) is None


def test_birth_date_wins_over_a_legacy_stored_age():
    from datetime import date

    from app.services.household_age import effective_age

    class Stub:
        birth_date = date(1977, 6, 15)
        age = 12  # stale, entered years ago

    assert effective_age(Stub(), today=date(2026, 8, 18)) == 49


def test_a_member_with_no_birth_date_still_uses_the_legacy_age(db_session):
    """Nobody's existing profile loses its DRI targets on upgrade."""
    from app.services.household_age import effective_age

    class Stub:
        birth_date = None
        age = 51

    assert effective_age(Stub()) == 51


def test_dri_targets_work_from_a_birth_date_alone(db_session):
    """The end of the chain that matters: a member entered the NEW way, with
    no legacy age at all, must still produce targets rather than reporting
    'age' as missing."""
    from datetime import date

    from app.models import HealthMetricEntry, HouseholdMember
    from app.services import dri_service

    member = HouseholdMember(name="Birthdate Only", birth_date=date(1977, 6, 15), height_cm=180.0, sex="male")
    db_session.add(member)
    db_session.flush()
    db_session.add(HealthMetricEntry(household_member_id=member.id, entry_date=date.today(), weight_kg=92.0))
    db_session.commit()

    targets, missing = dri_service.compute_member_daily_targets(db_session, member)

    assert missing == []
    assert targets is not None
