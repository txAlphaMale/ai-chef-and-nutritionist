"""Tests for the B5.2 prep-day/batch-cooking generation mode: gather_
generation_context() picking up an optional prep_day argument and build_
generation_prompt() actually including (or omitting) the resulting
instruction block in the literal generated prompt text -- same "assert on
the real generated string" discipline as the B2.3 dietary-pattern tests
(test_meal_plan_dietary_pattern.py).
"""
from __future__ import annotations

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
        "prep_day": None,
    }
    context.update(overrides)
    return context


# --- build_generation_prompt -----------------------------------------------


def test_prompt_omits_prep_day_section_by_default():
    prompt = meal_plan_service.build_generation_prompt(_base_context())
    assert "Prep-day" not in prompt
    assert "batch-cook" not in prompt


def test_prompt_includes_prep_day_section_when_set():
    prompt = meal_plan_service.build_generation_prompt(_base_context(prep_day=6))
    assert "Prep-day / batch-cooking mode is ON for Sunday" in prompt
    assert "batch-cook" in prompt
    assert "make_ahead" in prompt
    assert "already cooked from Sunday" in prompt


def test_prompt_names_the_correct_day_for_each_value():
    # Monday=0 .. Sunday=6, same convention MealPlanEntry.day_of_week and
    # DAY_NAMES already use everywhere else in this module.
    for day, name in enumerate(meal_plan_service.DAY_NAMES):
        prompt = meal_plan_service.build_generation_prompt(_base_context(prep_day=day))
        assert f"ON for {name}" in prompt


# --- gather_generation_context -----------------------------------------------


def test_gather_context_prep_day_defaults_to_none(db_session):
    context = meal_plan_service.gather_generation_context(
        db_session, household_size=2, meal_types=["dinner"], kitchen_profile_id=None, entry_guidance=[], notes=None
    )
    assert context["prep_day"] is None


def test_gather_context_prep_day_passthrough_reaches_prompt(db_session):
    context = meal_plan_service.gather_generation_context(
        db_session,
        household_size=2,
        meal_types=["dinner"],
        kitchen_profile_id=None,
        entry_guidance=[],
        notes=None,
        prep_day=2,
    )
    assert context["prep_day"] == 2

    prompt = meal_plan_service.build_generation_prompt(context)
    assert "ON for Wednesday" in prompt
