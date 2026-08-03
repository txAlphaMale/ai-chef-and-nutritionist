"""Backlog B9.5: an iCalendar (RFC 5545) feed of a weekly meal plan, so
it lands in whatever calendar app the household already uses (Google
Calendar, Apple Calendar, Outlook, etc. all support subscribing to a
plain .ics URL). Hand-built rather than adding a new dependency
(`icalendar`/`ics`) for a format this small and well-specified to get
right with a plain string builder -- consistent with this project's
existing preference for a stdlib-only implementation where one is
reasonably achievable (see e.g. TrendChart's inline-SVG charts avoiding
a JS charting library).

Live-generated on every request, same as cost_service.py and
diet_quality_service.py -- there is nothing to persist here, and
generating fresh each time is exactly what makes this endpoint a real
"feed": a calendar app that re-polls the URL later sees whatever the
plan looks like NOW (entries added, recipes swapped, slots skipped),
not a stale snapshot from whenever it was first added.

Date mapping: `MealPlanEntry.day_of_week` (0=Monday..6=Sunday, this
app's existing convention -- see meal_plan_service.DAY_NAMES) is applied
as an offset from `MealPlan.week_start_date`. This assumes
week_start_date IS the Monday of that week; nothing elsewhere in the app
enforces that today (a user could technically pass any date when
generating a plan), so a week_start_date that isn't actually a Monday
will shift every entry's calendar date by the same amount rather than
being auto-corrected -- a reasonable, honest behavior given the
ambiguity, not a bug to silently paper over.

Known simplification, stated plainly rather than hidden: meal times are
a fixed per-meal-type default (breakfast 08:00, lunch 12:00, dinner
18:00, snack 15:00, each a 45-minute block) since nothing in this app's
schema captures a household's actual meal times. A per-household
settings override is reasonable future work, not attempted here.

Event times are deliberately "floating" (no UTC designator, no TZID) --
a meal at 6pm should read as 6pm in whatever calendar app displays it,
not shift with a timezone conversion the way a scheduled meeting would.
Only DTSTAMP (when the feed was generated, a required VEVENT property
with real semantic meaning tied to generation time, not the meal itself)
is emitted as true UTC.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.models import MealPlan, MealPlanEntry
from app.models.base import utc_now

# Backlog B9.5's own stated simplification -- see module docstring.
MEAL_TYPE_TIMES: dict[str, tuple[int, int]] = {
    "breakfast": (8, 0),
    "lunch": (12, 0),
    "dinner": (18, 0),
    "snack": (15, 0),
}
DEFAULT_MEAL_TIME = (18, 0)  # unrecognized/future meal_type values fall back to dinner-time
EVENT_DURATION_MINUTES = 45

PRODID = "-//Chef//Meal Plan Export//EN"


def _escape_text(value: str) -> str:
    """RFC 5545 SS3.3.11 TEXT escaping -- backslash, semicolon, comma, and
    newline are the only characters that need it for the fields this
    module emits (SUMMARY/DESCRIPTION)."""
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold_line(line: str) -> str:
    """RFC 5545 SS3.1 line folding: no content line may be longer than 75
    octets; continuation lines start with a single space. Not folding
    long DESCRIPTION values is a real interop risk with strict parsers
    (Apple Calendar in particular), so this is done for real rather than
    skipped as an edge case."""
    if len(line.encode("utf-8")) <= 75:
        return line
    parts = []
    remaining = line
    limit = 75
    while len(remaining.encode("utf-8")) > limit:
        # Fold on a byte boundary that doesn't split a multi-byte UTF-8
        # character -- walk back from the limit until safe.
        cut = limit
        while cut > 0 and len(remaining[:cut].encode("utf-8")) > limit:
            cut -= 1
        parts.append(remaining[:cut])
        remaining = remaining[cut:]
        limit = 74  # continuation lines lose one column to the leading space
    parts.append(remaining)
    return "\r\n ".join(parts)


def _format_datetime(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def _entry_summary(entry: MealPlanEntry) -> str:
    if entry.is_eating_out:
        return "Eating out"
    if entry.recipe is not None:
        return entry.recipe.title
    if entry.notes:
        return entry.notes.strip()[:100]
    return f"{entry.meal_type.capitalize()} (unplanned)"


def _entry_description(entry: MealPlanEntry) -> str:
    lines = [f"{entry.meal_type.capitalize()} for {entry.servings} serving(s)."]
    if entry.recipe is not None and not entry.is_eating_out:
        lines.append(f"Recipe: {entry.recipe.title}")
    if entry.requested_tags:
        lines.append(f"Tags: {', '.join(entry.requested_tags)}")
    if entry.is_indulgence:
        lines.append("Flagged as this week's occasional indulgence.")
    if entry.notes:
        lines.append(entry.notes.strip())
    return "\n".join(lines)


def _entry_event_start(meal_plan: MealPlan, entry: MealPlanEntry) -> datetime:
    event_date = meal_plan.week_start_date + timedelta(days=entry.day_of_week)
    hour, minute = MEAL_TYPE_TIMES.get(entry.meal_type, DEFAULT_MEAL_TIME)
    return datetime(event_date.year, event_date.month, event_date.day, hour, minute)


def build_ics(meal_plan: MealPlan, now: datetime | None = None) -> str:
    """Builds the full .ics document text for a meal plan. Skipped
    entries (`is_skipped`) are excluded -- there's nothing to attend.
    `now` is injectable for deterministic tests; defaults to the real
    current time for the DTSTAMP every VEVENT requires."""
    stamp = _format_datetime(now or utc_now())

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_escape_text(f'Chef meal plan -- week of {meal_plan.week_start_date.isoformat()}')}",
    ]

    for entry in sorted(meal_plan.entries, key=lambda e: (e.day_of_week, e.meal_type)):
        if entry.is_skipped:
            continue
        start = _entry_event_start(meal_plan, entry)
        end = start + timedelta(minutes=EVENT_DURATION_MINUTES)
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:chef-mealplan-entry-{entry.id}@chef.local",
                f"DTSTAMP:{stamp}Z",
                f"DTSTART:{_format_datetime(start)}",
                f"DTEND:{_format_datetime(end)}",
                f"SUMMARY:{_escape_text(_entry_summary(entry))}",
                f"DESCRIPTION:{_escape_text(_entry_description(entry))}",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")
    # RFC 5545 requires CRLF line endings; fold each logical line first.
    return "\r\n".join(_fold_line(line) for line in lines) + "\r\n"
