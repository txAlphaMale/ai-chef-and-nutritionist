"""Shared schema for the B3.1/B3.2 deterministic allergen/restriction
check -- lives in its own module (rather than inside schemas/recipe.py or
schemas/meal_plan.py) since both of those, plus schemas/household.py,
need it and neither should import from the other."""
from __future__ import annotations

from pydantic import BaseModel


class RestrictionMatchRead(BaseModel):
    # For a cross-contact match this is the synthetic key
    # "gluten_cross_contact", not one of the real ALLERGEN_CHOICES keys --
    # see allergen_service.find_cross_contact_matches.
    allergen: str
    ingredient_name: str
    matched_keyword: str
