"""Tests for the B2.3 dietary-pattern wiring into meal-plan generation:
gather_generation_context() picking up HouseholdPreferences.dietary_pattern
and build_generation_prompt() actually including (or omitting) the
resulting guidance block in the literal generated prompt text -- the same
"assert on the real generated string, not just that a context dict was
built" discipline the Phase 6 health/knowledge context wiring used.
"""

from __future__ import annotations

from app.models import HouseholdPreferences
from app.services import meal_plan_service


def _base_context(**overrides):
    context = {
        "household_size": 2,
        "dietary_restrictions": [],
        "goals": None,
        "dietary_pattern_guidance": None,
        "indulgence_frequency": "weekly",
        "kitchen_name": "Home Kitchen",
        "equipment": ["oven", "stovetop"],
        "priority_ingredients": [],
        "recipe_catalog": [],
        "meal_types_requested": ["dinner"],
        "slots": [{"day_of_week": 0, "meal_type": "dinner", "tags": [], "notes": None}],
        "notes": None,
        "health_summary": None,
        "knowledge_context": None,
    }
    context.update(overrides)
    return context


# --- build_generation_prompt -----------------------------------------------


def test_prompt_omits_pattern_section_when_no_pattern_selected():
    prompt = meal_plan_service.build_generation_prompt(_base_context())
    assert "Portfolio" not in prompt
    assert "soluble fiber" not in prompt


def test_prompt_includes_portfolio_guidance_when_selected():
    from app.services import dietary_pattern_service

    guidance = dietary_pattern_service.get_pattern_guidance("portfolio_ldl")
    prompt = meal_plan_service.build_generation_prompt(_base_context(dietary_pattern_guidance=guidance))
    assert "soluble fiber" in prompt
    assert "Soy protein" in prompt
    assert "Tree nuts" in prompt
    assert guidance in prompt


# --- gather_generation_context -----------------------------------------------


def test_gather_context_no_guidance_when_no_household_row(db_session):
    # No HouseholdPreferences row at all (unseeded DB) -- should degrade
    # gracefully, same as every other household-derived field here.
    context = meal_plan_service.gather_generation_context(
        db_session, household_size=2, meal_types=["dinner"], kitchen_profile_id=None, entry_guidance=[], notes=None
    )
    assert context["dietary_pattern_guidance"] is None


def test_gather_context_no_guidance_when_pattern_not_selected(db_session):
    db_session.add(HouseholdPreferences(household_size=2, dietary_restrictions=[], dietary_pattern=None))
    db_session.commit()

    context = meal_plan_service.gather_generation_context(
        db_session, household_size=None, meal_types=["dinner"], kitchen_profile_id=None, entry_guidance=[], notes=None
    )
    assert context["dietary_pattern_guidance"] is None


def test_gather_context_includes_guidance_when_portfolio_selected(db_session):
    db_session.add(HouseholdPreferences(household_size=2, dietary_restrictions=[], dietary_pattern="portfolio_ldl"))
    db_session.commit()

    context = meal_plan_service.gather_generation_context(
        db_session, household_size=None, meal_types=["dinner"], kitchen_profile_id=None, entry_guidance=[], notes=None
    )
    assert context["dietary_pattern_guidance"] is not None
    assert "soluble fiber" in context["dietary_pattern_guidance"]

    # And it actually reaches the literal prompt text end to end, not
    # just the intermediate context dict.
    prompt = meal_plan_service.build_generation_prompt(context)
    assert "soluble fiber" in prompt
