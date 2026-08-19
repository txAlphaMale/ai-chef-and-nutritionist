

# --- B18.1: ApoB, Lp(a), HbA1c, waist (2026-08-18) ------------------------


def test_lpa_units_are_recognised_in_the_shapes_labs_actually_print():
    from app.services.health_service import normalize_lpa_unit

    assert normalize_lpa_unit("mg/dL") == "mg_dl"
    assert normalize_lpa_unit("MG_DL") == "mg_dl"
    assert normalize_lpa_unit("nmol/L") == "nmol_l"
    assert normalize_lpa_unit("nmol_l") == "nmol_l"


def test_an_unrecognised_lpa_unit_is_none_rather_than_a_guess():
    from app.services.health_service import normalize_lpa_unit

    assert normalize_lpa_unit("mmol/L") is None
    assert normalize_lpa_unit("") is None
    assert normalize_lpa_unit(None) is None


def test_an_lpa_number_with_no_unit_is_discarded_not_stored():
    """The scale matters more than the number here. mg/dL and nmol/L differ
    by roughly 2.5x and are not reliably interconvertible, so an unlabelled
    value would silently corrupt whatever trend it joined."""
    from app.services.health_service import parse_bloodwork_response

    entries = parse_bloodwork_response(
        '{"entries": [{"entry_date": "2026-08-18", "lpa_value": 75, "lpa_unit": "furlongs", "ldl_mg_dl": 130}]}'
    )

    assert len(entries) == 1
    assert entries[0]["lpa_value"] is None
    assert entries[0]["ldl_mg_dl"] == 130, "the rest of the panel survives"


def test_an_lpa_value_keeps_its_unit_when_the_lab_gave_one():
    from app.services.health_service import parse_bloodwork_response

    entries = parse_bloodwork_response(
        '{"entries": [{"entry_date": "2026-08-18", "lpa_value": 75, "lpa_unit": "nmol/L"}]}'
    )

    assert (entries[0]["lpa_value"], entries[0]["lpa_unit"]) == (75.0, "nmol_l")


def test_a_unit_with_no_number_is_not_a_result():
    """An entry carrying only `lpa_unit` must still be dropped as empty."""
    from app.services.health_service import parse_bloodwork_response

    assert parse_bloodwork_response('{"entries": [{"entry_date": "2026-08-18", "lpa_unit": "mg/dL"}]}') == []


def test_waist_to_height_is_a_ratio_and_nothing_else():
    """Deliberately no category and no threshold: interpreting it is a
    clinician's job, and this app does not hand out risk labels."""
    from app.services.health_service import waist_to_height_ratio

    assert waist_to_height_ratio(94.0, 180.0) == 0.522
    assert waist_to_height_ratio(None, 180.0) is None
    assert waist_to_height_ratio(94.0, None) is None
    assert waist_to_height_ratio(94.0, 0) is None


def test_the_health_line_reports_the_latest_of_each_metric_not_the_latest_entry(db_session):
    """The defect this fixes fed the MEAL PLAN. Logging a weight this
    morning used to hand the generation prompt a health line with no
    cholesterol in it, because a lipid panel and a weigh-in are almost
    never the same row -- so the planner steered for a household whose LDL
    it could not see."""
    from datetime import date

    from app.models import HealthMetricEntry, HouseholdMember
    from app.services import health_service

    member = HouseholdMember(name="Jason", height_cm=180.0)
    db_session.add(member)
    db_session.flush()
    db_session.add(HealthMetricEntry(household_member_id=member.id, entry_date=date(2026, 5, 1), ldl_mg_dl=142.0))
    db_session.add(HealthMetricEntry(household_member_id=member.id, entry_date=date(2026, 8, 17), weight_kg=92.0))
    db_session.commit()

    summary = health_service.build_health_context_summary(db_session)

    assert "weight 92.0kg" in summary
    assert "LDL 142.0 mg/dL" in summary, "the older panel must survive a newer weigh-in"


def test_the_new_biomarkers_reach_the_meal_plan_grounding(db_session):
    from datetime import date

    from app.models import HealthMetricEntry, HouseholdMember
    from app.services import health_service

    member = HouseholdMember(name="Jason", height_cm=180.0)
    db_session.add(member)
    db_session.flush()
    db_session.add(
        HealthMetricEntry(
            household_member_id=member.id,
            entry_date=date(2026, 8, 18),
            apob_mg_dl=105.0,
            lpa_value=75.0,
            lpa_unit="nmol_l",
            hba1c_percent=5.8,
            waist_cm=94.0,
        )
    )
    db_session.commit()

    summary = health_service.build_health_context_summary(db_session)

    assert "ApoB 105.0 mg/dL" in summary
    assert "Lp(a) 75.0 nmol/L" in summary, "the unit must travel with the number"
    assert "HbA1c 5.8%" in summary
    assert "waist 94.0cm" in summary
    assert "waist-to-height 0.522" in summary
