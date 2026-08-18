"""Age, from a birth date, in one place.

Author-requested 2026-08-18: the Health page collected a member's AGE as a
number. That is a value which is wrong the day after it is entered, and
nothing in the app would ever say so -- it silently skews every DRI target
computed from it, and those targets are the entire reason the field is
collected. A birth date is a fact that does not go stale.

`age` is kept on the model as a legacy fallback for members entered before
birth dates existed, so nobody's existing profile loses its targets on
upgrade. The UI no longer offers it. One rule, applied everywhere:
**birth_date wins; the stored age is only consulted when there is no birth
date.**
"""

from __future__ import annotations

from datetime import date


def age_from_birth_date(birth_date: date | None, today: date | None = None) -> int | None:
    """Completed years. `today` is injectable so tests are not a hostage to
    the clock, the same reason `dashboard_service.build_dashboard` takes one.

    Returns None for a missing date, and for a date in the FUTURE -- a
    negative age is not a number any caller here can do anything sensible
    with, and DRI computation asking for "age" should see "missing" rather
    than "-3".
    """
    if birth_date is None:
        return None
    today = today or date.today()
    if birth_date > today:
        return None

    years = today.year - birth_date.year
    # Subtract a year when this year's birthday has not happened yet.
    # Comparing (month, day) tuples handles 29 February without a special
    # case: in a non-leap year the birthday is treated as not-yet-reached
    # until 1 March, which is the more conservative of the two conventions
    # and matters not at all for a nutrient target.
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def effective_age(member, today: date | None = None) -> int | None:
    """The age to use for a `HouseholdMember`: computed from `birth_date`
    if there is one, else the legacy stored `age`.

    Takes the member duck-typed rather than importing the model, so this
    stays importable from anywhere without a circular import and is
    trivially testable with a stub.
    """
    computed = age_from_birth_date(getattr(member, "birth_date", None), today=today)
    if computed is not None:
        return computed
    return getattr(member, "age", None)
