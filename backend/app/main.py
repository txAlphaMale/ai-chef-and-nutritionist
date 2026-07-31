"""Chef app API entrypoint.

Phase 1 added the data layer; Phase 2 adds DB-backed settings/secrets
and the Ollama/Tavily service wrappers, surfaced here through the
read-only /api/system/* router. Inventory, recipe, meal-plan, and chat
routers land in their respective phases -- see PROJECT-PLAN.md.
"""
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import HouseholdPreferences
from app.routers import inventory, recipes, system

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


@app.get("/health")
def health(db: Session = Depends(get_db)):
    prefs = db.query(HouseholdPreferences).first()
    return {
        "status": "ok",
        "household_size": prefs.household_size if prefs else None,
    }
