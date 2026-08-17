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
from app.models.log import AppLogEntry
from app.models.meal_plan import GroceryListItem, MealPlan, MealPlanEntry
from app.models.recipe import ImportSkip, MealTag, Recipe, RecipeIngredient
from app.models.settings import AppSetting, KnowledgeChunk, KnowledgeFile, SoundFile, SystemPrompt

__all__ = [
    "AppLogEntry",
    "AppSetting",
    "ChatMessage",
    "GroceryListItem",
    "HealthMetricEntry",
    "HouseholdMember",
    "HouseholdPreferences",
    "ImportSkip",
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
    "SoundFile",
    "SystemPrompt",
]
