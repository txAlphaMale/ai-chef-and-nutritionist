"""JSON Schemas handed to Ollama's `format` parameter for constrained
decoding.

Why these exist. Every structured AI response in this app used to be
free-form text that a bespoke parser then tried to rescue -- strip a
`<think>` block, scan for balanced brackets, salvage a truncated object
by cutting at the last top-level comma. That approach cannot be made
reliable, because the failure is in the sampler, not the prompt: a small
local model asked politely for "ONLY a JSON object" will sometimes emit
something else, and no amount of prompt rewriting changes that. Three
separate rounds of prompt tuning on the recipe importer are the evidence.

Ollama supports passing a JSON Schema to `format`, which constrains
decoding token by token. A response that violates the schema is not
unlikely -- it is unrepresentable. Combined with `temperature: 0` (see
ollama_client.EXTRACTION_OPTIONS) this removes the entire class of bug.

Design rules for the models below, learned from what the old prompts
actually got wrong:

- **Mirror the prompt exactly.** The schema and the prompt's stated
  output shape must not drift; the schema is now the authority and the
  prompt's format section is a human-readable restatement of it.
- **Nullable, not optional.** Constrained decoding fills required fields.
  Making a field `X | None` and required means the model must emit
  something -- possibly `null` -- rather than silently omitting a key,
  which is what produced half-populated results before.
- **Enums for closed sets.** Category and tag vocabularies are enforced
  here rather than hoped for in prose, so `inventory_service`'s
  "if category not in CATEGORY_VALUES: category = 'other'" fallback stops
  being load-bearing.
- **No `additionalProperties: false`.** Some Ollama/llama.cpp grammar
  builds handle strict object closure poorly; extra keys are harmless
  because every consumer reads by name.

These are deliberately separate from the app's own request/response
schemas. A recipe as the model is asked to produce it and a recipe as the
API accepts it are different contracts, and coupling them means a future
API field change silently alters what the model is constrained to emit.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Kept in sync with inventory_service.CATEGORY_VALUES.
InventoryCategory = Literal["pantry", "fridge", "freezer", "produce", "spice", "other"]


class ExtractedIngredient(BaseModel):
    ingredient_name: str
    quantity: float | None = Field(default=None)
    unit: str | None = Field(default=None)
    prep_note: str | None = Field(default=None)


class ExtractedNutrition(BaseModel):
    """Per-serving estimates. Every key is nullable because a source
    frequently states none of them, and a model forced to produce a number
    it has no basis for will invent one -- which is exactly the
    hallucinated-nutrition problem B1.1/B1.2 exist to contain.

    Key set mirrors food_data_service.NUTRITION_PROMPT_HINT."""

    calories: float | None = None
    protein_g: float | None = None
    carbs_g: float | None = None
    fat_g: float | None = None
    fiber_g: float | None = None
    sodium_mg: float | None = None
    cholesterol_mg: float | None = None
    saturated_fat_g: float | None = None
    sugars_g: float | None = None


class ExtractedRecipe(BaseModel):
    title: str
    description: str | None = None
    default_servings: int | None = None
    prep_time_minutes: int | None = None
    cook_time_minutes: int | None = None
    instructions: list[str] = Field(default_factory=list)
    ingredients: list[ExtractedIngredient] = Field(default_factory=list)
    nutrition: ExtractedNutrition | None = None
    tags: list[str] = Field(default_factory=list)
    tips: list[str] = Field(default_factory=list)


class ExtractedInventoryItem(BaseModel):
    """One detected item from a pantry photo, a receipt, or a pasted list.

    `estimated_quantity` is how many were purchased/seen. Package size
    descriptors ("14 oz can", "6 count") belong in `unit`, not here --
    conflating the two was the original B4.5 bug, and stating it in the
    schema as well as the prompt gives the constraint two chances to hold.
    """

    name: str
    estimated_quantity: float | None = None
    unit: str | None = None
    category: InventoryCategory = "other"
    estimated_expiration_days: int | None = None
    purchased_date: str | None = Field(default=None, description="YYYY-MM-DD")
    unit_price: float | None = None
    confidence_note: str | None = None


class ExtractedInventoryList(BaseModel):
    """Ollama's `format` takes an object schema; a bare top-level array is
    not reliably supported across builds. Wrapping the array in a single
    `items` key is the portable form, and callers unwrap it."""

    items: list[ExtractedInventoryItem] = Field(default_factory=list)


class ExtractedMealPlanEntry(BaseModel):
    day_of_week: int = Field(ge=0, le=6, description="0=Monday .. 6=Sunday")
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"]
    recipe_id: int | None = None
    new_recipe: ExtractedRecipe | None = None
    servings: int | None = None
    requested_tags: list[str] = Field(default_factory=list)
    is_indulgence: bool = False
    notes: str | None = None


class ExtractedMealPlan(BaseModel):
    entries: list[ExtractedMealPlanEntry] = Field(default_factory=list)


class ExtractedRecipeEdit(BaseModel):
    """The recipe-scoped chat's response: a conversational reply, plus an
    optional whole-recipe replacement the user reviews before anything is
    saved."""

    reply: str
    proposed_recipe: ExtractedRecipe | None = None
    variant_label: str | None = None


def schema_of(model: type[BaseModel]) -> dict:
    """`format`-ready JSON Schema for a model.

    Pydantic emits `$defs` + `$ref` for nested models. Ollama's grammar
    builder handles internal refs, but flattening removes a compatibility
    variable across llama.cpp versions for no cost, so nested definitions
    are inlined here."""
    return _inline_refs(model.model_json_schema())


def _inline_refs(schema: dict) -> dict:
    defs = schema.pop("$defs", {})
    if not defs:
        return schema

    def resolve(node, seen: frozenset[str] = frozenset()):
        # Guarded by the set of $ref names already being expanded on this
        # branch, not by nesting depth. A depth cap looks safer but isn't:
        # ExtractedMealPlan nests four models deep (plan -> entry ->
        # recipe -> nutrition) through anyOf/items wrappers, so any cap low
        # enough to stop a cycle also silently leaves real $refs unresolved
        # -- which is worse than the problem, because the schema still
        # *looks* fine. Tracking names stops a genuine cycle exactly, and
        # lets legitimate nesting go as deep as it needs to.
        if isinstance(node, list):
            return [resolve(entry, seen) for entry in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            name = ref.split("/")[-1]
            target = defs.get(name)
            if target is not None and name not in seen:
                merged = {**resolve(target, seen | {name})}
                # Preserve sibling keys (description, default) that sat
                # alongside the $ref.
                merged.update({k: resolve(v, seen) for k, v in node.items() if k != "$ref"})
                return merged
            # A self-referential model would land here. Nothing in this
            # module is, but degrade to a permissive object rather than
            # emitting a dangling $ref the grammar builder can't follow.
            return {"type": "object"}
        return {key: resolve(value, seen) for key, value in node.items()}

    return resolve(schema)
