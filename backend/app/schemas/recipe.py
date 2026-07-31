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
    # Food-database resolution (B1.1) -- output-only, never accepted on
    # create/update (see RecipeIngredientBase). None/unset means "never
    # attempted"; "unresolved" means "tried, no match in either source."
    resolution_source: str | None = None  # usda | off | unresolved | None
    resolved_food_name: str | None = None
    fdc_id: int | None = None
    off_barcode: str | None = None
    nutrition_per_100g: dict | None = None


class RecipeBase(BaseModel):
    title: str
    description: str | None = None
    default_servings: int = 2
    prep_time_minutes: int | None = None
    cook_time_minutes: int | None = None
    instructions: list[str] = Field(default_factory=list)
    # Per-serving estimate -- keys are app.services.food_data_service.
    # NUTRITION_KEYS (calories/protein_g/carbs_g/fat_g/fiber_g/sodium_mg/
    # cholesterol_mg/saturated_fat_g/sugars_g), informal by design, see
    # the Recipe model. Whether this dict is real (summed from resolved
    # ingredients) or a guess is RecipeRead.nutrition_provenance below --
    # deliberately NOT on this base class, since a client should never be
    # able to just assert "computed" on create/update (see RecipeRead).
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
    # Recipe variants -- see the Recipe model for the full writeup. Set
    # together when an AI-proposed edit is saved as "a new variant"
    # rather than overwriting the recipe it came from.
    parent_recipe_id: int | None = None
    variant_label: str | None = None


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
    parent_recipe_id: int | None = None
    variant_label: str | None = None
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
    # Backlog B1.2 -- output-only, same "never accepted from the client"
    # discipline as RecipeIngredientRead.resolution_source above. "computed"
    # / "partial" / "ai_estimated" / None (legacy row, never computed).
    nutrition_provenance: str | None = None
    ingredients: list[RecipeIngredientRead]
    tags: list[str]
    servings_shown: int = Field(default=0, description="Servings these ingredient quantities are scaled to")
    # Computed in routers/recipes.py's _to_read() (a join / relationship
    # length, not directly derivable via from_attributes) -- lets the
    # detail page show "Variant of X" and a variants list without extra
    # round-trips for the common case.
    parent_recipe_title: str | None = None
    variant_count: int = 0
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
    # Set only when the user's message implied an edit (e.g. "make this
    # gluten-free") rather than a question -- a full recipe reflecting
    # the requested change, in the same shape recipe import returns, for
    # the frontend to show in a review step (reusing RecipeForm) before
    # anything is saved. variant_label is the AI's short suggested label
    # (e.g. "Gluten-Free") for the "save as a new variant" path -- see
    # recipe_service.RECIPE_CHAT_MODIFY_INSTRUCTIONS.
    proposed_recipe: RecipeCreate | None = None
    variant_label: str | None = None
