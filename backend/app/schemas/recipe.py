"""Pydantic request/response models for the recipe API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.allergen import RestrictionMatchRead


class InstructionStep(BaseModel):
    """One instruction, and the part of the dish it belongs to.

    Stored in the recipe's JSON `instructions` column as an object rather
    than a bare string, so a multi-component recipe can show the crust's
    steps under the crust and the filling's under the filling -- which is
    how the source wrote them and how anyone cooks from them.

    A BARE STRING IS STILL ACCEPTED and read as an unsectioned step. That
    is not politeness: every recipe stored before this change is a list of
    strings, the JSON column is not migrated, and an API client that sends
    the old shape is not wrong. Tolerating it here means one coercion in
    one place instead of a migration plus a flag day."""

    component: str | None = None
    text: str

    @field_validator("text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return (value or "").strip()

    @classmethod
    def coerce(cls, raw) -> list[InstructionStep]:
        """A stored or submitted instructions value, whatever shape it is."""
        steps: list[InstructionStep] = []
        for item in raw or []:
            if isinstance(item, InstructionStep):
                step = item
            elif isinstance(item, dict):
                text = str(item.get("text") or "").strip()
                step = cls(component=item.get("component"), text=text)
            else:
                step = cls(component=None, text=str(item).strip())
            if step.text:
                steps.append(step)
        return steps


class RecipeIngredientBase(BaseModel):
    ingredient_name: str
    quantity: float | None = None
    unit: str | None = None
    prep_note: str | None = None
    # The source's own section heading for multi-component dishes
    # ("Crust", "Filling and Assembly"); None for a single-component
    # recipe. Accepted on create/update because it is authored data, not
    # a derived field -- unlike the resolution columns below. The UI
    # groups by this only when at least one line carries a label.
    component: str | None = None


class RecipeIngredientRead(RecipeIngredientBase):
    model_config = ConfigDict(from_attributes=True)
    # The underlying RecipeIngredient row's id -- present even when the
    # displayed quantity/unit have been scaled (servings) or converted
    # (B10.5 unit_system) for this view, since scaling/converting never
    # creates or removes a row, only changes what's rendered. None only
    # for a genuinely unpersisted ingredient (e.g. an AI-proposed new
    # recipe in a not-yet-saved import/generation preview).
    id: int | None = None
    # Food-database resolution (B1.1) -- output-only, never accepted on
    # create/update (see RecipeIngredientBase). None/unset means "never
    # attempted"; "unresolved" means "tried, no match in either source."
    resolution_source: str | None = None  # usda | off | unresolved | None
    resolved_food_name: str | None = None
    fdc_id: int | None = None
    off_barcode: str | None = None
    nutrition_per_100g: dict | None = None
    # Backlog B10.5 -- True when a requested `unit_system` (metric/
    # imperial/weight) could NOT be honored for this specific ingredient
    # (only possible for weight mode on a volume-quantity ingredient with
    # no cached density) -- quantity/unit are left in the recipe's
    # original values in that case, not a guess. Always False for
    # unit_system="original" (nothing was attempted) and for count-based
    # ingredients (nothing to convert, not a failure).
    display_unavailable: bool = False


class RecipeBase(BaseModel):
    title: str
    description: str | None = None
    default_servings: int = 2
    prep_time_minutes: int | None = None
    cook_time_minutes: int | None = None
    instructions: list[InstructionStep] = Field(default_factory=list)

    @field_validator("instructions", mode="before")
    @classmethod
    def _accept_plain_strings(cls, value):
        return InstructionStep.coerce(value)
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
    source: str = "manual"  # manual|import_file|import_file_jsonld|import_image|import_text|import_url|import_url_jsonld|ai_generated
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
    instructions: list[InstructionStep] | None = None

    @field_validator("instructions", mode="before")
    @classmethod
    def _accept_plain_strings_on_patch(cls, value):
        # None means "not provided" on a PATCH and must stay None; only an
        # actual list is coerced.
        return value if value is None else InstructionStep.coerce(value)

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
    # Backlog B3.1/B3.2 -- computed fresh on every read against the
    # household's CURRENT restricted_allergens/gluten_observance_level
    # (see routers/recipes.py's _to_read), not persisted -- restrictions
    # can change at any time and a recipe saved before that change should
    # still show a warning, not a stale/missing one. `matches` are hard
    # allergen hits; `cross_contact_warnings` are the softer B3.2 oats-
    # style warning, only ever non-empty at the strict_no_cross_contact
    # observance level.
    restriction_warnings: list[RestrictionMatchRead] = Field(default_factory=list)
    cross_contact_warnings: list[RestrictionMatchRead] = Field(default_factory=list)
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


class IngredientProvenance(BaseModel):
    """How the previewed ingredients were produced -- see
    recipe_service.INGREDIENT_PROVENANCE_KEY for why this is on the wire
    at all rather than only in the log.

    `path`   two_pass | jsonld | single_call
    `reason` why verification did not happen, when path is single_call:
             no_source_text (a photo -- nothing to check a copy against),
             nothing_verified (no block survived the coverage gate), or
             fewer_than_single_call (a block did, but too few lines to
             earn the replacement).
    """

    path: str
    reason: str | None = None
    verified: int | None = None
    single_call: int | None = None


class RecipeImportResponse(BaseModel):
    recipe: RecipeCreate
    raw_model_output: str
    # Null only for a response built by an older build; every import path
    # sets one now.
    ingredient_provenance: IngredientProvenance | None = None
    # Backlog B3.1 -- checked against the parsed-but-not-yet-saved
    # ingredients so a conflict is visible in the review step, before the
    # user ever confirms the import (see routers/recipes.py's import_recipe).
    restriction_warnings: list[RestrictionMatchRead] = Field(default_factory=list)
    cross_contact_warnings: list[RestrictionMatchRead] = Field(default_factory=list)


class RecipeFolderScanItem(BaseModel):
    """One file's outcome from a backlog B13.1 folder-scan batch import
    (see recipe_folder_import_service.scan_and_parse). `recipe` is a
    preview only -- nothing is saved to the recipes table until the user
    reviews and POSTs the confirmed subset to
    POST /api/recipes/import-folder/confirm."""

    filename: str
    relative_path: str
    status: str  # "ok" | "error"
    recipe: RecipeCreate | None = None
    error: str | None = None


class RecipeFolderImportResponse(BaseModel):
    """Result of scanning `recipe_import_folder_path` (Settings >
    Integrations) for recipe files. Nothing is saved yet -- the frontend
    reviews/edits/unchecks `items` then POSTs the confirmed subset."""

    items: list[RecipeFolderScanItem]
    skipped: list[list[str]] = Field(
        default_factory=list,
        description="[[path, reason], ...] -- files found but not attempted (too large, or over the file-count cap)",
    )
    truncated: bool = False
    scanned_folder: str
    error: str | None = Field(
        None, description="Set when the configured folder itself couldn't be scanned (missing, or not a directory)"
    )


class RecipeFolderImportConfirmRequest(BaseModel):
    recipes: list[RecipeCreate]


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
