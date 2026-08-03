"""Import every model so Base.metadata (and Alembic autogenerate) sees
the full schema, and so relationship() string references resolve."""

from app.models.chat import ChatMessage
from app.models.health import HealthMetricEntry
from app.models.household import HouseholdMember, HouseholdPreferences
from app.models.inventory import (
    IngredientAlias,
    InventoryItem,
    OrderImportProfile,
    RecallAlert,
    RecallCheckState,
)
from app.models.kitchen import KitchenProfile
from app.models.meal_plan import GroceryListItem, MealPlan, MealPlanEntry
from app.models.recipe import MealTag, Recipe, RecipeIngredient
from app.models.settings import AppSetting, KnowledgeChunk, KnowledgeFile, SystemPrompt

__all__ = [
    "AppSetting",
    "ChatMessage",
    "GroceryListItem",
    "HealthMetricEntry",
    "HouseholdMember",
    "HouseholdPreferences",
    "IngredientAlias",
    "InventoryItem",
    "KitchenProfile",
    "KnowledgeChunk",
    "KnowledgeFile",
    "MealPlan",
    "MealPlanEntry",
    "MealTag",
    "OrderImportProfile",
    "RecallAlert",
    "RecallCheckState",
    "Recipe",
    "RecipeIngredient",
    "SystemPrompt",
]
