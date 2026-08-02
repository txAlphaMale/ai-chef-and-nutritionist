"""Phase 10 -- a genuine cross-router end-to-end smoke test.

Every other test file in this suite (deliberately, per test_barcode_
lookup.py's/test_recipe_folder_import_confirm.py's own comments) calls
ONE router function in isolation against a plain db_session, no
TestClient -- appropriate for unit-level coverage, but it means nothing
committed actually exercises the realistic cross-router flow a household
lives day to day: set preferences -> add inventory -> add a recipe ->
build a meal plan -> confirm a meal (deducting inventory) -> log a health
metric. Each session's own manual "live curl against a running uvicorn"
pass has covered pieces of this ad hoc, but never as a single repeatable,
committed test. This fills that gap using the same "call the router
function directly with a plain Session" style the rest of the suite
already established -- no TestClient needed here either, since every
router function below only depends on a plain Session, not on any
HTTP-layer concern (auth, CORS, etc.) worth exercising through a real
request.

Deliberately does NOT touch anything that calls Ollama/Tavily/USDA/OSM --
this is a smoke test for the deterministic CRUD/business-logic backbone,
not a live-network integration test (no route reaches this sandbox
anyway, a standing constraint documented throughout this project)."""
from __future__ import annotations

from datetime import date, timedelta

from app.models import HouseholdPreferences
from app.routers.health import create_metric
from app.routers.household import create_member
from app.routers.inventory import create_inventory_item, list_inventory
from app.routers.meal_plan import confirm_meal_plan_entry, create_meal_plan
from app.routers.recipes import create_recipe
from app.schemas.health import HealthMetricEntryCreate
from app.schemas.household import HouseholdMemberCreate
from app.schemas.inventory import InventoryItemCreate
from app.schemas.meal_plan import MealPlanCreate, MealPlanEntryCreate
from app.schemas.recipe import RecipeCreate, RecipeIngredientBase


def test_full_household_flow_across_every_core_router(db_session):
    db = db_session

    # 1. Household preferences -- a plain row insert (update_preferences
    # 404s on an empty table by design, same as a truly fresh, unseeded
    # deployment would before app/seed.py's first run).
    db.add(HouseholdPreferences(household_size=2, dietary_restrictions=[], restricted_allergens=[]))
    db.commit()

    # 2. A household member (needed for BMI computation in step 6).
    member = create_member(HouseholdMemberCreate(name="Jason", age=45, height_cm=178), db)
    assert member.id is not None

    # 3. Inventory: two chicken breasts on hand.
    item = create_inventory_item(
        InventoryItemCreate(name="chicken breast", category="fridge", quantity=2, unit="lb"), db
    )
    assert item.id is not None

    # 4. A recipe using that ingredient.
    recipe = create_recipe(
        RecipeCreate(
            title="Simple Grilled Chicken",
            default_servings=2,
            instructions=["Season the chicken.", "Grill 6-8 minutes per side."],
            ingredients=[RecipeIngredientBase(ingredient_name="chicken breast", quantity=1, unit="lb")],
            tags=["quick", "gluten_free"],
        ),
        db,
    )
    assert recipe.id is not None

    # 5. A meal plan with one entry referencing that recipe.
    plan = create_meal_plan(
        MealPlanCreate(
            week_start_date=date.today(),
            entries=[MealPlanEntryCreate(day_of_week=0, meal_type="dinner", recipe_id=recipe.id, servings=2)],
        ),
        db,
    )
    assert plan.id is not None
    assert len(plan.entries) == 1
    entry_id = plan.entries[0].id

    # 6. Confirm the entry -- should deduct 1 lb of chicken breast from
    # inventory (the recipe's own ingredient quantity, at 2/2 servings
    # scale) without hitting the allergen 409 path (no restrictions set).
    confirmed = confirm_meal_plan_entry(plan.id, entry_id, db=db)
    assert confirmed.is_confirmed is True

    remaining = list_inventory(db=db)
    chicken = next(i for i in remaining if i.id == item.id)
    assert chicken.quantity == 1.0  # 2 lb on hand - 1 lb deducted

    # 7. Log a health metric for the member -- BMI should be computed
    # server-side from weight_kg + the member's height_cm set in step 2.
    metric = create_metric(
        HealthMetricEntryCreate(
            household_member_id=member.id,
            entry_date=date.today() - timedelta(days=1),
            weight_kg=79.4,
        ),
        db,
    )
    assert metric.bmi is not None
    assert 24.0 < metric.bmi < 26.0  # ~79.4kg / 1.78m^2 ~= 25.06
