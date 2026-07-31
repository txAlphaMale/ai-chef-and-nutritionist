"""Recipes, their ingredients, and the tag system used for meal-planning
filters (quick, portable, non-refrigerated, dutch-oven-only, etc.)."""
from __future__ import annotations

from sqlalchemy import Boolean, Column, ForeignKey, Integer, JSON, String, Table, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

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

    # manual | import_file | import_image | import_text | ai_generated
    source: Mapped[str] = mapped_column(String(20), default="manual")
    image_path: Mapped[str | None] = mapped_column(String(500), nullable=True)

    ingredients: Mapped[list["RecipeIngredient"]] = relationship(
        back_populates="recipe", cascade="all, delete-orphan"
    )
    tags: Mapped[list["MealTag"]] = relationship(secondary=recipe_tag_links)


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"

    id: Mapped[int] = mapped_column(primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    ingredient_name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[float | None] = mapped_column(nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    prep_note: Mapped[str | None] = mapped_column(String(200), nullable=True)

    recipe: Mapped["Recipe"] = relationship(back_populates="ingredients")
