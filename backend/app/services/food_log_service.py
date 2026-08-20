"""Writing and reading the food log (backlog B17.1).

Everything that creates a log row goes through here rather than building a
`FoodLogEntry` inline, for one reason: **the per-serving to total
conversion has to happen in exactly one place.**

`Recipe.nutrition` is stored PER SERVING -- `food_data_service.
compute_recipe_nutrition` divides its ingredient totals by
`default_servings` before returning. A log row records what was actually
eaten, so it holds the ABSOLUTE amount, already multiplied by the servings
consumed. Those two facts are one multiplication apart, and a caller that
forgot it would write a plausible-looking number that is wrong by a factor
of two to six with nothing on screen to reveal it. So no caller does the
multiplication.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import FoodLogEntry, MealPlanEntry, Recipe
from app.models.base import utc_now

# Provenance labels, shared with Recipe.nutrition_provenance. NULL is a
# fifth state and means "no nutrition on this entry at all" -- see the
# model's own comment for why that must never be read as zero.
PROVENANCE_COMPUTED = "computed"
PROVENANCE_PARTIAL = "partial"
PROVENANCE_AI_ESTIMATED = "ai_estimated"


def scale_nutrition(per_serving: dict | None, servings: float | None) -> dict:
    """Per-serving nutrition -> the total for `servings` servings.

    Returns {} for missing/zero/negative servings or missing nutrition
    rather than guessing a portion: a log row with no numbers is honest,
    and one with numbers derived from an assumed serving count is not.
    Non-numeric values are dropped individually, so one bad key in an AI
    estimate cannot discard the rest of the dict.
    """
    if not per_serving or servings is None or servings <= 0:
        return {}
    total: dict[str, float] = {}
    for key, value in per_serving.items():
        try:
            total[key] = round(float(value) * float(servings), 1)
        except (TypeError, ValueError):
            continue
    return total


def log_from_recipe(
    db: Session,
    *,
    recipe: Recipe,
    servings: float,
    meal_type: str,
    source: str,
    eaten_at: datetime | None = None,
    member_id: int | None = None,
    meal_plan_entry_id: int | None = None,
    notes: str | None = None,
) -> FoodLogEntry:
    """One log row for a recipe that was eaten. Does NOT commit -- callers
    own the transaction, the same convention every other service in this
    app follows.

    The recipe's provenance label is carried straight through. Multiplying
    a per-serving figure by a serving count does not make it more or less
    trustworthy than it already was, so a "partial" recipe produces a
    "partial" log row -- it does not get promoted by arriving here, and it
    does not get demoted either.
    """
    entry = FoodLogEntry(
        member_id=member_id,
        eaten_at=eaten_at or utc_now(),
        meal_type=meal_type,
        source=source,
        # Snapshotted, not joined: the log has to survive this recipe
        # being renamed or deleted.
        description=recipe.title,
        recipe_id=recipe.id,
        meal_plan_entry_id=meal_plan_entry_id,
        servings=servings,
        nutrition=scale_nutrition(recipe.nutrition, servings),
        # A recipe with no nutrition at all yields NULL provenance, not a
        # false "ai_estimated" -- nothing estimated anything.
        nutrition_provenance=recipe.nutrition_provenance if recipe.nutrition else None,
        notes=notes,
    )
    db.add(entry)
    return entry


def log_for_confirmed_plan_entry(db: Session, entry: MealPlanEntry) -> FoodLogEntry | None:
    """The automatic half of B17.1: confirming a planned meal records that
    it was eaten, so the plan path stays one click rather than two.

    Returns None, without raising, in the three cases where there is
    nothing honest to write:

    * **The slot has no recipe.** An empty or eating-out slot confirms
      with no recipe attached, and inventing "you ate something" from a
      confirmation of nothing would put a phantom meal in the history.
      B17.3 gives eating-out entries a way to carry a real estimate; until
      then, silence is the correct output.
    * **The recipe row is gone.** Same reasoning.
    * **This slot already has a log row.** The confirm endpoint refuses to
      confirm twice, so this is unreachable today -- it is here because
      the invariant belongs next to the write, not in whichever caller
      happens to enforce it now. The unique index on
      `meal_plan_entry_id` is the third line of defence.

    A LEFTOVER entry (`leftover_of_entry_id` set) IS logged, deliberately,
    even though it deducts no inventory. The origin entry's confirm
    already deducted the ingredients for the whole cook event, so
    deducting again would double-count the pantry -- but the leftovers
    were genuinely eaten on a different day, and a consumption log that
    skipped them would undercount intake for that day and overcount it for
    the day the batch was cooked. Inventory and intake are different
    questions and this is the point where they diverge.
    """
    if entry.recipe_id is None:
        return None
    recipe = db.get(Recipe, entry.recipe_id)
    if recipe is None:
        return None
    existing = db.query(FoodLogEntry).filter_by(meal_plan_entry_id=entry.id).first()
    if existing is not None:
        return None

    return log_from_recipe(
        db,
        recipe=recipe,
        servings=float(entry.servings or 1),
        meal_type=entry.meal_type,
        source="meal_plan",
        # The plan says which day the slot is for, not which minute it was
        # eaten. `utc_now()` is used rather than reconstructing a datetime
        # from week_start_date + day_of_week, because confirming is an act
        # that happens when the meal happens -- and a reconstructed
        # midnight would land the meal on the wrong calendar day for
        # anyone west of UTC, which is the same class of bug the frontend
        # datetime helpers were written to kill.
        eaten_at=utc_now(),
        meal_plan_entry_id=entry.id,
    )


# --- Daily roll-up -------------------------------------------------------
#
# B17.2 re-points the existing nutrient roll-up and diet-quality score at
# this data; the grouping below is the piece both of them need first.

# Weakest wins. A total is exactly as trustworthy as its worst input, so a
# day containing one AI-estimated meal is an AI-estimated day however many
# computed ones sit beside it. Ordered worst-first.
_PROVENANCE_RANK = {PROVENANCE_AI_ESTIMATED: 0, PROVENANCE_PARTIAL: 1, PROVENANCE_COMPUTED: 2}


def weakest_provenance(labels) -> str | None:
    """The least trustworthy label present, or None if none are."""
    known = [x for x in labels if x in _PROVENANCE_RANK]
    if not known:
        return None
    return min(known, key=lambda x: _PROVENANCE_RANK[x])


def summarize_days(entries, tz_offset_minutes: int = 0) -> list[dict]:
    """Group log entries into per-day totals, newest day first.

    **`tz_offset_minutes` is required to be correct and defaults to UTC.**
    Timestamps are stored in UTC, and "which day did I eat that" is a
    question about the eater's wall clock. Grouping in UTC puts a 7pm
    dinner in Texas on the following day, which would misattribute one
    meal in three for this household and make every daily total wrong in
    both directions at once. The frontend passes
    `-new Date().getTimezoneOffset()`; the default is UTC because a
    caller that says nothing gets the unshifted truth rather than a
    guessed locale.

    Every returned day carries `unquantified_entries`. See
    FoodLogDaySummary for why that is a required output and not a
    diagnostic.
    """
    shift = timedelta(minutes=tz_offset_minutes)
    days: dict[str, dict] = {}

    for entry in entries:
        local_date = (entry.eaten_at + shift).date().isoformat()
        day = days.setdefault(
            local_date,
            {"date": local_date, "entry_count": 0, "unquantified_entries": 0, "nutrition": {}, "_labels": []},
        )
        day["entry_count"] += 1
        if not entry.nutrition:
            # No numbers on this entry. Counted, never summed as zero.
            day["unquantified_entries"] += 1
            continue
        day["_labels"].append(entry.nutrition_provenance)
        for key, value in entry.nutrition.items():
            try:
                day["nutrition"][key] = round(day["nutrition"].get(key, 0.0) + float(value), 1)
            except (TypeError, ValueError):
                continue

    out = []
    for day in days.values():
        day["nutrition_provenance"] = weakest_provenance(day.pop("_labels"))
        out.append(day)
    return sorted(out, key=lambda d: d["date"], reverse=True)
