"""Capstone review 2026-08-16, backlog B24.1 -- the Recipes LIST shape.

`GET /api/recipes` returned a full `RecipeRead` per recipe: every
ingredient row including its `nutrition_per_100g` dict, every instruction
step, tips, source metadata, per-ingredient resolution provenance. Measured
against the live deployment, 603 KB for 129 recipes, for a page that
renders a title, some chips and a thumbnail.

The tests that matter here are the two that assert what is ABSENT. A test
that only checked the fields the page uses would have passed against the
old behaviour just as happily, since the old shape was a superset.
"""

from app.models import MealTag, Recipe, RecipeIngredient
from app.routers.recipes import _to_list_read
from app.schemas.recipe import RecipeListRead
from app.services import allergen_service

NUTRITION_PER_100G = {"calories": 52.0, "protein_g": 0.3, "carbs_g": 14.0}


def _make_recipe(db, title="Test Recipe", ingredients=("flour", "water"), tags=("dinner",)):
    recipe = Recipe(
        title=title,
        description="A description.",
        default_servings=4,
        prep_time_minutes=10,
        cook_time_minutes=20,
        instructions=[{"component": None, "text": "Do the thing."}],
        nutrition={"calories": 400},
        nutrition_provenance="computed",
        is_staple=True,
        rating=4,
        source="import_url",
        source_url="https://example.com/recipe",
        tips=["A tip."],
    )
    db.add(recipe)
    db.flush()
    for name in ingredients:
        db.add(
            RecipeIngredient(
                recipe_id=recipe.id,
                ingredient_name=name,
                quantity=1.0,
                unit="cup",
                resolution_source="usda",
                resolved_food_name=f"Resolved {name}",
                fdc_id=12345,
                nutrition_per_100g=NUTRITION_PER_100G,
            )
        )
    for tag_name in tags:
        tag = db.query(MealTag).filter_by(name=tag_name).first() or MealTag(name=tag_name)
        db.add(tag)
        db.flush()
        recipe.tags.append(tag)
    db.commit()
    db.refresh(recipe)
    return recipe


def test_list_shape_omits_ingredients_and_instructions(db_session):
    """The defect itself. Both of these were the bulk of the 603 KB."""
    fields = RecipeListRead.model_fields
    assert "ingredients" not in fields
    assert "instructions" not in fields
    assert "tips" not in fields
    assert "nutrition" not in fields
    assert "description" not in fields
    assert "source_url" not in fields


def test_serialized_list_row_contains_no_per_ingredient_nutrition(db_session):
    """Asserts against the serialized JSON, not just the model fields --
    a nested blob leaking through a field that was kept would not show up
    in a field-name check."""
    recipe = _make_recipe(db_session)
    restrictions = allergen_service.load_household_restrictions(db_session)

    payload = _to_list_read(recipe, restrictions).model_dump_json()

    assert "nutrition_per_100g" not in payload
    assert "Resolved flour" not in payload
    assert "Do the thing." not in payload


def test_list_shape_keeps_every_field_the_page_renders(db_session):
    """The other half: slimming must not remove something the list draws.
    These eight are what RecipesPage.jsx actually reads."""
    recipe = _make_recipe(db_session)
    restrictions = allergen_service.load_household_restrictions(db_session)

    row = _to_list_read(recipe, restrictions)

    assert row.id == recipe.id
    assert row.title == "Test Recipe"
    assert row.default_servings == 4
    assert row.is_staple is True
    assert row.rating == 4
    assert row.tags == ["dinner"]
    assert isinstance(row.derived_tags, list)
    assert row.has_restriction_conflict is False


def test_derived_tags_are_still_computed_for_the_list(db_session):
    """SmartTags are derived per read rather than stored, so the slim path
    has to keep doing the work -- it only skips SERIALIZING the ingredients
    it derives them from."""
    recipe = _make_recipe(db_session, ingredients=("raw cashews", "coconut milk"))
    restrictions = allergen_service.load_household_restrictions(db_session)

    row = _to_list_read(recipe, restrictions)

    assert row.derived_tags, "expected at least one derived tag from cashews"
    assert all(d.basis for d in row.derived_tags), "every chip must carry its evidence"


def test_restriction_conflict_is_flagged_but_never_asserted_as_absent(db_session):
    """The flag direction matters more than the flag. See
    project_chef_safety_claims: this app flags what it finds and never
    certifies what it did not find."""
    from app.models import HouseholdPreferences

    db_session.add(HouseholdPreferences(household_size=2, restricted_allergens=["wheat"]))
    db_session.commit()
    restrictions = allergen_service.load_household_restrictions(db_session)

    conflicting = _make_recipe(db_session, title="Wheat Bread", ingredients=("wheat flour", "water"))
    clean = _make_recipe(db_session, title="Rice Bowl", ingredients=("rice", "water"))

    assert _to_list_read(conflicting, restrictions).has_restriction_conflict is True
    # False here means "nothing matched", and the field name says conflict
    # rather than `is_safe` precisely so this can never be read as a
    # clearance by a caller or by the UI.
    assert _to_list_read(clean, restrictions).has_restriction_conflict is False
    assert "is_safe" not in RecipeListRead.model_fields
    assert "restriction_free" not in RecipeListRead.model_fields


def test_the_list_row_is_dramatically_smaller_than_the_full_row(db_session):
    """A regression guard with a number in it. If someone adds ingredients
    back to the list shape, this fails and says why."""
    from app.routers.recipes import _to_read

    recipe = _make_recipe(db_session, ingredients=tuple(f"ingredient {i}" for i in range(12)))
    restrictions = allergen_service.load_household_restrictions(db_session)

    full = len(_to_read(recipe, db_session, restrictions=restrictions).model_dump_json())
    slim = len(_to_list_read(recipe, restrictions).model_dump_json())

    assert slim * 5 < full, f"list row is {slim} bytes vs {full} full -- the slimming has regressed"


# --- The endpoint itself, not just the serializer -------------------------
#
# The first version of this file tested `_to_list_read` only, and a control
# run proved that was not enough: reverting `list_recipes` to the old
# `_to_read` call left every test above passing, because none of them went
# through the endpoint. These two close that gap.


def test_the_list_endpoint_actually_returns_the_slim_shape(db_session):
    from app.routers.recipes import list_recipes

    _make_recipe(db_session, title="Endpoint Recipe", ingredients=("flour", "sugar"))

    rows = list_recipes(db=db_session)

    assert rows, "expected the endpoint to return the recipe"
    assert all(isinstance(row, RecipeListRead) for row in rows)
    assert not hasattr(rows[0], "ingredients")
    assert not hasattr(rows[0], "instructions")


def test_the_declared_response_model_matches_what_is_returned(db_session):
    """A response_model wider than the return value silently re-adds the
    fields as nulls; narrower, and FastAPI strips data the page needs.
    Either way the two must agree."""
    from app.main import app

    route = next(
        r for r in app.routes if getattr(r, "path", None) == "/api/recipes" and "GET" in getattr(r, "methods", set())
    )
    assert route.response_model == list[RecipeListRead]
