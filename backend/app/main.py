"""Chef app API entrypoint.

Phase 0: health check only. Phase 1+ wires in the real routers
(inventory, recipes, meal plans, chat, settings) as they're built --
see PROJECT-PLAN.md for the phase order.
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings

app = FastAPI(title="Chef", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten once frontend origin is finalized
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "household_size": settings.household_size,
        "ollama_base_url": settings.ollama_base_url,
    }
