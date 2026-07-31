"""Pydantic request/response models for the recipe API."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RecipeIngredientBase(BaseModel):
    ingredient_name: str
    quantity: float | None = None
    unit: str | None = None
    prep_note: str | None = None


class RecipeIngredientRead(RecipeIngredientBase):
    model_config = ConfigDict(from_attributes=True)
    # None for a scaled (non-persisted) ingredient view -- see
    # routers/recipes.py's _to_read(), which returns scaled quantities
    # without corresponding DB rows when servings != default_servings.
    id: int | None = None


class RecipeBase(BaseModel):
    title: str
    description: str | None = None
    default_servings: int = 2
    prep_time_minutes: int | None = None
    cook_time_minutes: int | None = None
    instructions: list[str] = Field(default_factory=list)
    # Per-serving estimate: calories, protein_g, carbs_g, fat_g, fiber_g,
    # sodium_mg, cholesterol_mg -- keys are informal, see Recipe model.
    nutrition: dict = Field(default_factory=dict)
    is_staple: bool = False
    image_path: str | None = None
    # Citation info, captured on import where available -- see Recipe model.
    source_url: str | None = None
    source_name: str | None = None
    source_author: str | None = None
    # Variations/substitutions/optional modifications worth keeping from
    # an imported source, distinct from the ads/stories/boilerplate that
    # import parsing discards.
    tips: list[str] = Field(default_factory=list)


class RecipeCreate(RecipeBase):
    source: str = "manual"  # manual|import_file|import_image|import_text|import_url|ai_generated
    ingredients: list[RecipeIngredientBase] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)  # tag names; unknown ones are created


class RecipeUpdate(BaseModel):
    """All fields optional -- PATCH semantics. Providing `ingredients` or
    `tags` replaces the whole list (no partial list-item patching)."""

    title: str | None = None
    description: str | None = None
    default_servings: int | None = None
    prep_time_minutes: int | None = None
    cook_time_minutes: int | None = None
    instructions: list[str] | None = None
    nutrition: dict | None = None
    is_staple: bool | None = None
    image_path: str | None = None
    source_url: str | None = None
    source_name: str | None = None
    source_author: str | None = None
    tips: list[str] | None = None
    ingredients: list[RecipeIngredientBase] | None = None
    tags: list[str] | None = None


class RecipeRatingUpdate(BaseModel):
    rating: int

    @field_validator("rating")
    @classmethod
    def rating_in_range(cls, v: int) -> int:
        if not 1 <= v <= 5:
            raise ValueError("rating must be between 1 and 5")
        return v


class RecipeRead(RecipeBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    rating: int | None = None
    source: str
    ingredients: list[RecipeIngredientRead]
    tags: list[str]
    servings_shown: int = Field(default=0, description="Servings these ingredient quantities are scaled to")
    created_at: datetime
    updated_at: datetime


class RecipeImportResponse(BaseModel):
    recipe: RecipeCreate
    raw_model_output: str


class RecipeChatMessage(BaseModel):
    role: str  # user|assistant
    content: str


class RecipeChatRequest(BaseModel):
    message: str
    # Client-held conversation so far -- this chat is deliberately
    # EPHEMERAL (not persisted to chat_messages, unlike the Phase 7
    # persistent chat system), so the frontend resends history each turn.
    history: list[RecipeChatMessage] = Field(default_factory=list)
    servings: int | None = None  # scale ingredient context to what the user is actually cooking


class RecipeChatResponse(BaseModel):
    reply: str
