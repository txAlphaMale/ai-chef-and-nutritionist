"""Recipes, their ingredients, and the tag system used for meal-planning
filters (quick, portable, non-refrigerated, dutch-oven-only, etc.)."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, Float, ForeignKey, Integer, String, Table, Text
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin, UtcDateTime

recipe_tag_links = Table(
    "recipe_tag_links",
    Base.metadata,
    Column("recipe_id", ForeignKey("recipes.id"), primary_key=True),
    Column("tag_id", ForeignKey("meal_tags.id"), primary_key=True),
)


class MealTag(Base):
    __tablename__ = "meal_tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    # e.g. quick, portable, non_refrigerated, dutch_oven_only, backpacking,
    # one_pot, make_ahead, freezer_friendly, kid_friendly, gluten_free
    name: Mapped[str] = mapped_column(String(50), unique=True)


class Recipe(Base, TimestampMixin):
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Scaling base -- UI scales ingredient quantities relative to this.
    # Defaults to household size at creation time, editable per-recipe.
    default_servings: Mapped[int] = mapped_column(Integer, default=2)

    prep_time_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cook_time_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Ordered list of step strings
    instructions: Mapped[list] = mapped_column(JSON, default=list)

    # Per-serving nutrition estimate -- keys are app.services.food_data_
    # service.NUTRITION_KEYS (calories, protein_g, carbs_g, fat_g, fiber_g,
    # sodium_mg, cholesterol_mg, saturated_fat_g, sugars_g), informal by
    # design so a future addition doesn't need a migration.
    nutrition: Mapped[dict] = mapped_column(JSON, default=dict)
    # Backlog B1.2: "computed" (every ingredient with a stated quantity
    # summed from a real USDA/OFF match), "partial" (some but not all
    # ingredients contributed -- an honest undercount, not a full total),
    # "ai_estimated" (no ingredient contributed real data -- this is
    # whatever the LLM guessed, or what a human typed into the form), or
    # None (legacy row from before this column existed / never touched).
    # Set by app.services.food_data_service.compute_recipe_nutrition() and
    # by routers/recipes.py's create/update handlers -- never accepted
    # directly from the client (see RecipeRead vs. RecipeBase/RecipeCreate/
    # RecipeUpdate in schemas/recipe.py), so a client can't fake "computed".
    nutrition_provenance: Mapped[str | None] = mapped_column(String(20), nullable=True)

    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5
    is_staple: Mapped[bool] = mapped_column(Boolean, default=False)

    # manual | import_file | import_image | import_text | import_url | ai_generated
    source: Mapped[str] = mapped_column(String(20), default="manual")
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Citation info, captured on import where available -- respects the
    # original source rather than stripping attribution. source_url is
    # only set for import_url; source_name/source_author may come from
    # any import method that states them (e.g. a PDF's title page, a
    # webpage's byline/site name).
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    source_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_author: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Helpful extras worth keeping from an imported source that aren't
    # part of the core recipe structure -- variations, substitution
    # suggestions, optional modifications -- as opposed to the ads,
    # stories, and other boilerplate that import parsing discards.
    tips: Mapped[list] = mapped_column(JSON, default=list)

    # Recipe variants (added 2026-07-31, "commit an AI-modified recipe"
    # backlog request): a variant is just a normal Recipe row that
    # happens to point back at the recipe it was derived from -- e.g.
    # asking chat to "make this gluten-free" and choosing "save as a new
    # variant" creates a full, independent Recipe with this set, rather
    # than mutating the original. Deliberately NOT cascade-deleted: a
    # variant should survive its parent being deleted (it's a real,
    # independent recipe by that point, not a diff/patch), so this is a
    # plain nullable FK with no ORM delete cascade -- same "SQLite
    # doesn't enforce FK constraints by default in this setup" tradeoff
    # already accepted elsewhere (see health_service.py's known
    # limitation on HealthMetricEntry.household_member_id).
    parent_recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipes.id"), nullable=True)
    # Short freeform label for what's different, e.g. "Gluten-Free",
    # "Low-Sodium" -- shown next to the title, and by the AI when
    # proposing a variant so the user can tell it apart from siblings.
    variant_label: Mapped[str | None] = mapped_column(String(100), nullable=True)

    ingredients: Mapped[list[RecipeIngredient]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )
    tags: Mapped[list[MealTag]] = relationship(secondary=recipe_tag_links)
    variants: Mapped[list[Recipe]] = relationship(
        "Recipe",
        backref=backref("parent_recipe", remote_side="Recipe.id"),
        foreign_keys=[parent_recipe_id],
    )


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    ingredient_name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[float | None] = mapped_column(nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prep_note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    # Food-database resolution (backlog B1.1, added 2026-07-31) -- see
    # app/services/food_data_service.py's module docstring for the full
    # design. Populated once, on demand (not automatically on save), by
    # resolve_and_cache_ingredient(); resolution_source distinguishes
    # "usda" / "off" / "unresolved" (tried, no match) from None (never
    # attempted), so a caller can tell "we looked and found nothing" from
    # "nobody's asked yet" without an extra round-trip.
    fdc_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    off_barcode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resolved_food_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    resolution_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # Cached per-100g snapshot at resolution time -- avoids re-hitting
    # USDA/OFF on every recipe view; a future "refresh stale resolutions"
    # pass can use resolved_at to decide what's worth re-fetching.
    nutrition_per_100g: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)

    # Backlog B10.5 -- implied density (grams per mL), parsed from USDA's
    # foodPortions data at the same resolution pass as nutrition_per_100g
    # (see food_data_service._parse_usda_density). Lets unit_conversion_
    # service.convert() bridge volume<->mass for THIS ingredient (the
    # "weight mode" unit-system toggle) without ever guessing a generic
    # density -- None means "unknown," which the display layer must
    # treat as unavailable, not a missing food match (a resolved
    # ingredient can have real nutrition data and still have no density,
    # e.g. if USDA/OFF never reported a volume-unit portion for it).
    density_g_per_ml: Mapped[float | None] = mapped_column(Float, nullable=True)

    recipe: Mapped[Recipe] = relationship(back_populates="ingredients")
