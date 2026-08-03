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
    assert dri.compute_daily_targets(weight_kg=None, height_cm=170, age=40, sex="male", activity_level="sedentary") is None


def test_compute_daily_targets_returns_none_without_height():
    assert dri.compute_daily_targets(weight_kg=80, height_cm=None, age=40, sex="male", activity_level="sedentary") is None


def test_compute_daily_targets_returns_none_without_age():
    assert dri.compute_daily_targets(weight_kg=80, height_cm=170, age=None, sex="male", activity_level="sedentary") is None


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
    with_sedentary = dri.compute_daily_targets(weight_kg=80, height_cm=180, age=30, sex="male", activity_level="sedentary")
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
    db_session.add(HealthMetricEntry(household_member_id=member.id, entry_date=date(2026, 6, 1), weight_kg=None, ldl_mg_dl=110))
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
