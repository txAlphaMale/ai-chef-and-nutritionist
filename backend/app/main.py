"""Chef app API entrypoint.

Phase 1 added the data layer; Phase 2 adds DB-backed settings/secrets
and the Ollama/Tavily service wrappers, surfaced here through the
read-only /api/system/* router. Inventory (Phase 3), recipes (Phase 4),
meal planning (Phase 5), and health/knowledge tracking (Phase 6)
followed; the chat router lands in Phase 7 -- see PROJECT-PLAN.md.
"""
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import HouseholdPreferences
from app.routers import chat, health, household, inventory, kitchen, knowledge, meal_plan, recipes, system

app = FastAPI(title="Chef", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten once frontend origin is finalized
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router)
app.include_router(inventory.router)
app.include_router(recipes.router)
app.include_router(kitchen.router)
app.include_router(meal_plan.router)
app.include_router(household.router)
app.include_router(health.router)
app.include_router(knowledge.router)
app.include_router(chat.router)


@app.get("/health")
def health_check(db: Session = Depends(get_db)):
    prefs = db.query(HouseholdPreferences).first()
    return {
        "status": "ok",
        "household_size": prefs.household_size if prefs else None,
    }
