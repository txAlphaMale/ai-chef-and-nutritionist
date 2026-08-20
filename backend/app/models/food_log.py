"""What was actually eaten, as opposed to what was planned.

Backlog B17.1. Until this table existed, every nutrition figure in the app
described a *plan*: the weekly roll-up (B1.4), the diet-quality
approximation (B2.2) and the health-trend feedback loop (B6) all read the
meal plan and reported it as though it were intake. A plan is a statement
of intent. Somebody who generated a plan on Monday and ate out three times
would have seen a tidy week of numbers that described nothing that
happened. This is the largest functional gap the capstone benchmark found
against the commercial services, and it is the input every one of those
three features actually wanted.

**A log row is a claim that food was eaten, and nothing more.** It carries
nutrition when nutrition is knowable and says so when it is not (see
`nutrition_provenance` below) -- the same rule `Recipe.nutrition` follows,
for the same reason: a roll-up that silently treats "unknown" as "zero"
reads as a good week.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.base import TimestampMixin, UtcDateTime, utc_now

# How an entry got here. Deliberately a documented string rather than a
# DB enum, matching InventoryItem.source -- the set grows (B17.3 adds
# text and photo capture) and a migration per new intake route would be
# friction with no safety benefit at this scale.
#
#   meal_plan  -- written automatically when a MealPlanEntry is confirmed
#   manual     -- typed into the food-log form
#   dining_out -- an eating-out entry, which may carry only an estimate
#   text       -- B17.3, free-text description parsed by the LLM
#   photo      -- B17.3, a plate photo through the existing vision path
FOOD_LOG_SOURCES = ("meal_plan", "manual", "dining_out", "text", "photo")


class FoodLogEntry(Base, TimestampMixin):
    __tablename__ = "food_log_entries"

    id: Mapped[int] = mapped_column(primary_key=True)

    # NULL means "the household", not "unknown". A meal plan is generated
    # for a household and its entries name no member, so every
    # auto-logged row is household-level by construction; forcing a
    # member here would mean inventing an attribution the plan never
    # made. B17.2's roll-up therefore has to handle both, and B17.4's
    # adherence view is household-level for the same reason.
    #
    # SET NULL rather than CASCADE: removing a person from the household
    # should not delete the record of meals that were eaten. The row
    # becomes household-level, which is a demotion of detail rather than
    # a loss of the fact.
    member_id: Mapped[int | None] = mapped_column(
        ForeignKey("household_members.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # When the food was EATEN, which is not when the row was written --
    # `created_at` from TimestampMixin already records the latter, and
    # the two differ every time somebody logs yesterday's dinner. Every
    # roll-up must group by this one.
    eaten_at: Mapped[datetime] = mapped_column(UtcDateTime, default=utc_now, index=True)

    # breakfast|lunch|dinner|snack -- same vocabulary as MealPlanEntry.
    meal_type: Mapped[str] = mapped_column(String(20), default="dinner")
    source: Mapped[str] = mapped_column(String(20), default="manual")

    # Always populated, for every source. A log the household cannot read
    # back in plain words is a log nobody checks, and for a manual or
    # eating-out entry this is the only description there will ever be.
    # For a recipe-backed entry it is the recipe title AS COOKED, kept
    # verbatim so the history survives the recipe being renamed, edited
    # into something else, or deleted.
    description: Mapped[str] = mapped_column(Text)

    # Provenance links, all optional and all SET NULL. A log entry is a
    # historical fact; deleting the recipe it came from must not rewrite
    # what somebody ate. `description` and `nutrition` above are the
    # durable copies, which is why they are stored rather than joined.
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipes.id", ondelete="SET NULL"), nullable=True)
    inventory_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_items.id", ondelete="SET NULL"), nullable=True
    )
    # The plan slot this row came from, when it came from one. Not in the
    # backlog's field list, but B17.4's whole job is planned-vs-logged and
    # that comparison needs the join. It also carries the no-double-log
    # invariant: a unique index means one confirmed slot can produce at
    # most one automatic row, whatever future code does to the confirm
    # path. NULLs are exempt from uniqueness in SQLite, which is exactly
    # right -- manual rows all carry NULL here.
    meal_plan_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("meal_plan_entries.id", ondelete="SET NULL"), nullable=True, unique=True
    )

    # How much was eaten, in the recipe's own serving unit. Float rather
    # than int (MealPlanEntry.servings is an int) because half a portion
    # is a normal thing to eat and a log that cannot express it would be
    # rounded by the person filling it in.
    servings: Mapped[float] = mapped_column(Float, default=1.0)

    # Absolute nutrition for THIS ENTRY -- already multiplied by
    # `servings`, not per-serving. `Recipe.nutrition` is per-serving (see
    # food_data_service.compute_recipe_nutrition, which divides by
    # default_servings); the log stores the total actually consumed,
    # because that is what a daily roll-up sums and doing the
    # multiplication once here beats every reader remembering to do it.
    nutrition: Mapped[dict] = mapped_column(JSON, default=dict)

    # The same four-value vocabulary Recipe.nutrition_provenance uses --
    # "computed", "partial", "ai_estimated" -- plus NULL, which here
    # means something specific and load-bearing: **this entry has no
    # nutrition at all.** "I ate at my sister's" is a real log entry with
    # a real date and no numbers behind it.
    #
    # B17.2's roll-up must report the count of NULL-provenance entries as
    # an explicit denominator rather than summing around them. Treating
    # an unquantified meal as zero calories would make the least-known
    # week look like the best one, which is the exact failure mode this
    # app's nutrition provenance labelling exists to prevent.
    nutrition_provenance: Mapped[str | None] = mapped_column(String(20), nullable=True)

    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
