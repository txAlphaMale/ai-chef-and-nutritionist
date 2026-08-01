"""Import every model so Base.metadata (and Alembic autogenerate) sees
the full schema, and so relationship() string references resolve."""
from app.models.inventory import InventoryItem, OrderImportProfile, RecallAlert, RecallCheckState
from app.models.kitchen import KitchenProfile
from app.models.recipe import MealTag, Recipe, RecipeIngredient
from app.models.meal_plan import GroceryListItem, MealPlan, MealPlanEntry
from app.models.household import HouseholdMember, HouseholdPreferences
from app.models.health import HealthMetricEntry
from app.models.chat import ChatMessage
from app.models.settings import AppSetting, KnowledgeChunk, KnowledgeFile, SystemPrompt

__all__ = [
    "InventoryItem",
    "OrderImportProfile",
    "RecallAlert",
    "RecallCheckState",
    "KitchenProfile",
    "MealTag",
    "Recipe",
    "RecipeIngredient",
    "GroceryListItem",
    "MealPlan",
    "MealPlanEntry",
    "HouseholdMember",
    "HouseholdPreferences",
    "HealthMetricEntry",
    "ChatMessage",
    "AppSetting",
    "KnowledgeFile",
    "KnowledgeChunk",
    "SystemPrompt",
]
