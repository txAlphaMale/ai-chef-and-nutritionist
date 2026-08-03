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
    KnowledgeFile,
    MealTag,
    SystemPrompt,
)
from app.routers.inventory import RECEIPT_IMPORT_PROMPT, VISION_PROMPT
from app.services import knowledge_service, settings_service
from app.services.recipe_service import RECIPE_IMPORT_PROMPT, RECIPE_MODIFY_INSTRUCTIONS

# Backlog B2.1 (2026-08-01): bundled, repo-shipped reference documents so
# every external user starts with SOME grounding for the "grounded in
# nutritionist knowledge" requirement, instead of an empty knowledge
# corpus. Seeded INACTIVE (is_active=False) -- a household must
# explicitly enable each one from the Knowledge Files UI, since these are
# generic references, not this household's own dietary needs. Sourced
# from federal-agency (public-domain) pages plus one clearly-labeled
# original research synthesis; see each file's own License/provenance
# note and PROJECT-PLAN.md's B2.1 notes section for what was
# investigated and deliberately left out (DRI numeric tables -- NASEM
# copyright, not a federal work).
DEFAULT_KNOWLEDGE_FILES_DIR = os.path.join(os.path.dirname(__file__), "data", "default_knowledge")

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

DEFAULT_KNOWLEDGE_FILE_DESCRIPTIONS = {
    "dietary_guidelines_2025_2030.md": (
        "Bundled default (repo-shipped, inactive until enabled). "
        "USDA/HHS Dietary Guidelines for Americans 2025-2030 summary -- "
        "public domain federal source."
    ),
    "dash_eating_pattern.md": (
        "Bundled default (repo-shipped, inactive until enabled). "
        "NIH/NHLBI DASH eating plan summary -- public domain federal source."
    ),
    "portfolio_diet_ldl_cholesterol.md": (
        "Bundled default (repo-shipped, inactive until enabled). "
        "Original synthesis of published research on the Portfolio diet "
        "for LDL cholesterol reduction -- not a government source, see "
        "the file's own citations."
    ),
    "fda_major_food_allergens.md": (
        "Bundled default (repo-shipped, inactive until enabled). "
        "FDA FALCPA/FASTER Act nine major food allergens reference -- "
        "public domain federal source."
    ),
    "niaid_food_allergy_diagnosis_management.md": (
        "Bundled default (repo-shipped, inactive until enabled). "
        "NIAID guidelines for diagnosis and management of food allergy -- "
        "public domain federal source."
    ),
}


def seed_default_knowledge_files(db) -> list[str]:
    """Registers each bundled file under DEFAULT_KNOWLEDGE_FILES_DIR as an
    inactive-by-default KnowledgeFile row, copying it into the live
    knowledge_service storage dir via the same save_file()/extract_text()
    path a normal upload uses (so a bundled file is indistinguishable from
    a user upload once created -- same storage convention, same indexing
    path once activated). Matches on filename, so re-running never
    duplicates a row already present -- covers both "seed ran before" and
    "the user already uploaded a same-named file themselves" the same
    way. Deliberately does NOT index (embed) these at seed time: they are
    inactive by default, and knowledge_service.ensure_indexed/
    search_knowledge already skip inactive files and lazily index on
    first use once a household enables one -- no live Ollama needed just
    to seed a fresh database. Returns the list of filenames actually
    added (empty on a re-run against an already-seeded DB), useful for
    tests and for a future "what did seeding just do" log line."""
    if not os.path.isdir(DEFAULT_KNOWLEDGE_FILES_DIR):
        return []

    added: list[str] = []
    for filename in sorted(os.listdir(DEFAULT_KNOWLEDGE_FILES_DIR)):
        if not filename.endswith(".md"):
            continue
        if db.query(KnowledgeFile).filter_by(filename=filename).first():
            continue

        source_path = os.path.join(DEFAULT_KNOWLEDGE_FILES_DIR, filename)
        with open(source_path, "rb") as f:
            raw_bytes = f.read()

        storage_path = knowledge_service.save_file(filename, raw_bytes)
        content = knowledge_service.extract_text(filename, "text/markdown", raw_bytes)

        db.add(
            KnowledgeFile(
                filename=filename,
                storage_path=storage_path,
                content_type="text/markdown",
                description=DEFAULT_KNOWLEDGE_FILE_DESCRIPTIONS.get(
                    filename, "Bundled default (repo-shipped, inactive until enabled)."
                ),
                content=content,
                is_active=False,
            )
        )
        added.append(filename)

    if added:
        db.commit()
    return added


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

        # Backlog B16.1 (author-requested 2026-08-03): the AI import/
        # extraction prompts -- previously hardcoded Python constants a
        # household could only change by editing code and rebuilding the
        # container -- are now SystemPrompt rows too, seeded with the
        # exact same text the code otherwise falls back to (see each of
        # recipe_service.get_recipe_import_prompt/get_recipe_modify_prompt
        # and routers/inventory.py's get_receipt_import_prompt/
        # get_vision_prompt: "DB row if present and active, else this
        # module's own constant"). Seeding these means a fresh install's
        # Settings page shows the REAL prompt text ready to tweak, not an
        # empty box -- and unlike main_chef/dietary_onboarding, unchecking
        # "Active" on one of these has a precise, safe meaning: revert to
        # the shipped default without losing the household's draft edit.
        for prompt_key, default_content in (
            ("recipe_import", RECIPE_IMPORT_PROMPT),
            ("recipe_modify", RECIPE_MODIFY_INSTRUCTIONS),
            ("receipt_import", RECEIPT_IMPORT_PROMPT),
            ("vision_intake", VISION_PROMPT),
        ):
            if not db.query(SystemPrompt).filter_by(prompt_key=prompt_key).first():
                db.add(SystemPrompt(prompt_key=prompt_key, content=default_content, is_active=True))

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

        added_knowledge = seed_default_knowledge_files(db)
        if added_knowledge:
            print(f"Seeded {len(added_knowledge)} default knowledge file(s) (inactive): {', '.join(added_knowledge)}")

        print("Seed complete.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
