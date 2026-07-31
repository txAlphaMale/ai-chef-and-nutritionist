"""Populate a fresh database with generic, non-personal defaults.

Deliberately generic: this repo is meant for other households to pull
down and run, so defaults are neutral (household_size=2, no dietary
restrictions pre-filled) rather than the original author's specific
gluten-free/cholesterol-focused profile. Each household configures its
own preferences through the onboarding flow / Settings GUI (Phase 2/8).

Settings (Ollama config, Tavily key) are stored in the DB via
settings_service/AppSetting, not read from .env at runtime -- .env is
only consulted here, once, as an OPTIONAL convenience so a value already
present in a fresh .env doesn't have to be retyped into the Settings UI
after first boot. Secret settings are encrypted before being written
(see app/services/secrets_crypto.py). Re-running this script never
overwrites a value the user has already set, in .env or in the UI.

Run with: python -m app.seed
"""
import os

from app.database import Base, SessionLocal, engine
from app.models import (
    AppSetting,
    HouseholdPreferences,
    KitchenProfile,
    MealTag,
    SystemPrompt,
)
from app.services import settings_service

MAIN_CHEF_PROMPT = """\
You are a world-class culinary chef and nutritionist. You are responsible \
for this household's pantry and ingredient inventory, expiration tracking, \
and building a balanced weekly meal plan and grocery list for a household \
of {household_size} people.

Rules you always follow:
- Check current inventory before proposing or changing a meal plan.
- Prefer ingredients that are close to expiration or have gone unused for \
a long time; also favor any ingredient the user has flagged as a \
priority to use up, without using it in every single meal.
- Meals should be balanced and nutritious overall, but the week may \
include one occasional indulgence.
- Respect the household's stated dietary restrictions and goals at all times.
- Take the kitchen/equipment profile currently in use into account (e.g. \
a full home kitchen vs. a camping trip, RV, or short-term rental with \
limited gear).
- Before writing to inventory, the meal plan, or the grocery list, \
confirm the action with the user unless they've explicitly asked you to \
just do it.
- Every recipe you produce includes: ingredients, step-by-step \
instructions, estimated prep time, estimated total time, nutritional \
details, and an estimated calorie count, scaled to the requested serving size.
"""

DIETARY_ONBOARDING_PROMPT = """\
Before I build your first meal plan, I'd like to understand your \
household's needs. Please tell me:
1. Any allergies, intolerances, or dietary restrictions (e.g. gluten-free, \
celiac-friendly, vegetarian, low-sodium).
2. Any health goals I should steer meals toward (e.g. reducing LDL \
cholesterol, weight management, more protein).
3. Typical activity level (sedentary, lightly active, moderately active, \
very active).
4. Preferences around prep time, leftovers, and how adventurous you want \
meals to be.
5. Anything you're currently trying to use up, or ingredients you'd like \
featured more often.
You can update any of this later from Settings or just by telling me in chat.
"""

DEFAULT_TAGS = [
    "quick",
    "portable",
    "non_refrigerated",
    "dutch_oven_only",
    "backpacking",
    "one_pot",
    "make_ahead",
    "freezer_friendly",
    "kid_friendly",
    "gluten_free",
]

DEFAULT_KITCHEN_EQUIPMENT = [
    "oven",
    "stovetop",
    "microwave",
    "refrigerator",
    "freezer",
    "instant_pot",
    "slow_cooker",
    "blender",
]

def seed() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if not db.query(HouseholdPreferences).first():
            db.add(HouseholdPreferences(household_size=2, dietary_restrictions=[], goals=None))

        if not db.query(KitchenProfile).filter_by(name="Home Kitchen").first():
            db.add(
                KitchenProfile(
                    name="Home Kitchen",
                    equipment=DEFAULT_KITCHEN_EQUIPMENT,
                    is_active=True,
                )
            )

        for name in DEFAULT_TAGS:
            if not db.query(MealTag).filter_by(name=name).first():
                db.add(MealTag(name=name))

        if not db.query(SystemPrompt).filter_by(prompt_key="main_chef").first():
            db.add(SystemPrompt(prompt_key="main_chef", content=MAIN_CHEF_PROMPT, is_active=True))

        if not db.query(SystemPrompt).filter_by(prompt_key="dietary_onboarding").first():
            db.add(
                SystemPrompt(
                    prompt_key="dietary_onboarding",
                    content=DIETARY_ONBOARDING_PROMPT,
                    is_active=True,
                )
            )

        db.commit()

        # Settings: only create a row if one doesn't exist yet -- never
        # clobber a value already set via .env or a previous Settings UI
        # edit. Check row existence directly (not get_setting(), which
        # falls back to the spec default and would look "already set").
        # set_setting() handles encryption for secret specs.
        for spec in settings_service.SETTING_SPECS:
            if db.query(AppSetting).filter_by(key=spec.key).first():
                continue
            seed_value = os.environ.get(spec.env_fallback, "") if spec.env_fallback else ""
            settings_service.set_setting(db, spec.key, seed_value or spec.default)

        print("Seed complete.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
