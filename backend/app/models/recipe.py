"""Recipes, their ingredients, and the tag system used for meal-planning
filters (quick, portable, non-refrigerated, dutch-oven-only, etc.)."""
from __future__ import annotations

from sqlalchemy import Boolean, Column, ForeignKey, Integer, JSON, String, Table, Text
from sqlalchemy.orm import Mapped, backref, mapped_column, relationship

from app.database import Base
from app.models.base import TimestampMixin

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

    # Per-serving nutrition estimate: calories, protein_g, carbs_g, fat_g,
    # fiber_g, sodium_mg, cholesterol_mg -- keys are informal by design so
    # the AI-grounded estimator (Phase 2+) can extend this without a migration.
    nutrition: Mapped[dict] = mapped_column(JSON, default=dict)

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

    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )
    tags: Mapped[list["MealTag"]] = relationship(secondary=recipe_tag_links)
    variants: Mapped[list["Recipe"]] = relationship(
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

    recipe: Mapped["Recipe"] = relationship(back_populates="ingredients")
