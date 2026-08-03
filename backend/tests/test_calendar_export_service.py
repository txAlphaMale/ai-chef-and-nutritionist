"""Unit tests for backlog B9.5's iCalendar export
(app.services.calendar_export_service.build_ics): correct RFC 5545
structure, day-of-week -> calendar-date mapping, skip/eating-out/
unplanned-slot summary handling, text escaping, and line folding. Pure
in-memory MealPlan/MealPlanEntry/Recipe objects, same pattern as
test_meal_plan_nutrition_summary.py -- no DB needed since this module
only reads attributes off objects it's handed.
"""
from __future__ import annotations

from datetime import date, datetime

from app.models import MealPlan, MealPlanEntry, Recipe
from app.services import calendar_export_service as cal


def _plan(week_start_date, entries):
    plan = MealPlan(week_start_date=week_start_date)
    plan.entries = entries
    return plan


def _entry(day_of_week, meal_type="dinner", recipe=None, **kwargs):
    entry = MealPlanEntry(id=kwargs.pop("id", 1), day_of_week=day_of_week, meal_type=meal_type, servings=2, **kwargs)
    entry.recipe = recipe
    return entry


def _recipe(title):
    return Recipe(title=title, default_servings=2)


NOW = datetime(2026, 8, 1, 12, 0, 0)


def test_wraps_in_vcalendar_with_required_properties():
    plan = _plan(date(2026, 8, 3), [])
    ics = cal.build_ics(plan, now=NOW)
    assert ics.startswith("BEGIN:VCALENDAR\r\n")
    assert ics.rstrip("\r\n").endswith("END:VCALENDAR")
    assert "VERSION:2.0\r\n" in ics
    assert "PRODID:" in ics


def test_skipped_entry_produces_no_vevent():
    plan = _plan(date(2026, 8, 3), [_entry(0, id=1, is_skipped=True)])
    ics = cal.build_ics(plan, now=NOW)
    assert "BEGIN:VEVENT" not in ics


def test_normal_entry_maps_day_of_week_and_meal_type_to_correct_datetime():
    # week_start_date is a Monday (2026-08-03); day_of_week=2 -> Wednesday
    # 2026-08-05; meal_type="lunch" -> 12:00 per MEAL_TYPE_TIMES.
    recipe = _recipe("Test Lunch")
    plan = _plan(date(2026, 8, 3), [_entry(2, meal_type="lunch", recipe=recipe, id=42)])
    ics = cal.build_ics(plan, now=NOW)
    assert "DTSTART:20260805T120000\r\n" in ics
    assert "DTEND:20260805T124500\r\n" in ics  # 45-minute default duration
    assert "UID:chef-mealplan-entry-42@chef.local\r\n" in ics
    assert "SUMMARY:Test Lunch\r\n" in ics


def test_each_meal_type_has_its_own_default_time():
    plan = _plan(
        date(2026, 8, 3),
        [
            _entry(0, meal_type="breakfast", recipe=_recipe("B"), id=1),
            _entry(0, meal_type="lunch", recipe=_recipe("L"), id=2),
            _entry(0, meal_type="dinner", recipe=_recipe("D"), id=3),
            _entry(0, meal_type="snack", recipe=_recipe("S"), id=4),
        ],
    )
    ics = cal.build_ics(plan, now=NOW)
    assert "DTSTART:20260803T080000" in ics
    assert "DTSTART:20260803T120000" in ics
    assert "DTSTART:20260803T180000" in ics
    assert "DTSTART:20260803T150000" in ics


def test_eating_out_entry_summary_ignores_any_recipe():
    plan = _plan(date(2026, 8, 3), [_entry(0, recipe=_recipe("Should not show"), is_eating_out=True, id=1)])
    ics = cal.build_ics(plan, now=NOW)
    assert "SUMMARY:Eating out\r\n" in ics
    assert "Should not show" not in ics


def test_unplanned_entry_with_no_recipe_and_no_notes_gets_a_fallback_summary():
    plan = _plan(date(2026, 8, 3), [_entry(0, meal_type="snack", id=1)])
    ics = cal.build_ics(plan, now=NOW)
    assert "SUMMARY:Snack (unplanned)\r\n" in ics


def test_special_characters_are_escaped_in_summary():
    plan = _plan(date(2026, 8, 3), [_entry(0, recipe=_recipe("Mac, Cheese; Bacon"), id=1)])
    ics = cal.build_ics(plan, now=NOW)
    assert "SUMMARY:Mac\\, Cheese\\; Bacon\r\n" in ics


def test_long_description_is_folded_with_continuation_space():
    long_notes = "x" * 200
    plan = _plan(date(2026, 8, 3), [_entry(0, recipe=_recipe("R"), notes=long_notes, id=1)])
    ics = cal.build_ics(plan, now=NOW)
    # Every raw physical line (split on the real CRLF used for folding)
    # must be within the 75-octet content-line limit -- the folded
    # continuation lines start with a single space per RFC 5545 3.1.
    description_block = [line for line in ics.split("\r\n") if line.startswith("DESCRIPTION:") or (line.startswith(" ") and "x" * 10 in line)]
    assert len(description_block) > 1  # actually folded into multiple physical lines
    for line in ics.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75


def test_dtstamp_is_utc_with_z_suffix():
    plan = _plan(date(2026, 8, 3), [_entry(0, recipe=_recipe("R"), id=1)])
    ics = cal.build_ics(plan, now=NOW)
    assert "DTSTAMP:20260801T120000Z\r\n" in ics
