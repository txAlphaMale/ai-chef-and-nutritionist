"""Unit tests for backlog B9.2's recipe export
(recipe_service.recipe_to_jsonld, the reverse of B9.3's
extract_jsonld_recipe) and the file-upload re-import path
(recipe_service.extract_jsonld_recipe_from_json), including a genuine
round trip through both directions against a real Recipe row."""
from __future__ import annotations

import json

from app.models import MealTag, Recipe, RecipeIngredient
from app.services import recipe_service as rs


def _build_recipe(db_session, **overrides) -> Recipe:
    defaults = dict(
        title="Test Skillet Chicken",
        description="A quick weeknight skillet.",
        default_servings=4,
        prep_time_minutes=15,
        cook_time_minutes=95,
        instructions=["Season the chicken.", "Sear both sides.", "Simmer until done."],
        nutrition={
            "calories": 420.0,
            "protein_g": 35.0,
            "carbs_g": 10.0,
            "fat_g": 22.0,
            "fiber_g": 2.0,
            "sodium_mg": 480.0,
            "cholesterol_mg": 110.0,
            "saturated_fat_g": 6.0,
            "sugars_g": 3.0,
        },
        rating=4,
        source_url="https://example.com/skillet-chicken",
        source_name="Example Kitchen",
        source_author="Jamie Cook",
        tips=["Great with rice.", "Freezes well."],
    )
    defaults.update(overrides)
    recipe = Recipe(**defaults)
    recipe.ingredients = [
        RecipeIngredient(ingredient_name="chicken thighs", quantity=2, unit="lb", prep_note="boneless"),
        RecipeIngredient(ingredient_name="olive oil", quantity=2, unit="tbsp", prep_note=None),
        RecipeIngredient(ingredient_name="garlic", quantity=3, unit="clove", prep_note="minced"),
    ]
    db_session.add(recipe)
    db_session.commit()
    db_session.refresh(recipe)
    return recipe


# --- recipe_to_jsonld ------------------------------------------------------


def test_recipe_to_jsonld_basic_fields(db_session):
    recipe = _build_recipe(db_session)
    doc = rs.recipe_to_jsonld(recipe)
    assert doc["@context"] == "https://schema.org"
    assert doc["@type"] == "Recipe"
    assert doc["name"] == "Test Skillet Chicken"
    assert doc["description"] == "A quick weeknight skillet."
    assert doc["recipeYield"] == "4"
    assert doc["url"] == "https://example.com/skillet-chicken"
    assert doc["author"] == {"@type": "Person", "name": "Jamie Cook"}
    assert doc["publisher"] == {"@type": "Organization", "name": "Example Kitchen"}


def test_recipe_to_jsonld_durations():
    assert rs._minutes_to_iso8601_duration(90) == "PT1H30M"
    assert rs._minutes_to_iso8601_duration(45) == "PT45M"
    assert rs._minutes_to_iso8601_duration(60) == "PT1H"
    assert rs._minutes_to_iso8601_duration(0) is None
    assert rs._minutes_to_iso8601_duration(None) is None


def test_recipe_to_jsonld_prep_cook_time(db_session):
    recipe = _build_recipe(db_session, prep_time_minutes=15, cook_time_minutes=95)
    doc = rs.recipe_to_jsonld(recipe)
    assert doc["prepTime"] == "PT15M"
    assert doc["cookTime"] == "PT1H35M"


def test_recipe_to_jsonld_omits_missing_prep_cook_time(db_session):
    recipe = _build_recipe(db_session, prep_time_minutes=None, cook_time_minutes=None)
    doc = rs.recipe_to_jsonld(recipe)
    assert "prepTime" not in doc
    assert "cookTime" not in doc


def test_recipe_to_jsonld_instructions_as_howto_steps(db_session):
    recipe = _build_recipe(db_session)
    doc = rs.recipe_to_jsonld(recipe)
    assert doc["recipeInstructions"] == [
        {"@type": "HowToStep", "text": "Season the chicken."},
        {"@type": "HowToStep", "text": "Sear both sides."},
        {"@type": "HowToStep", "text": "Simmer until done."},
    ]


def test_recipe_to_jsonld_ingredient_lines(db_session):
    recipe = _build_recipe(db_session)
    doc = rs.recipe_to_jsonld(recipe)
    assert doc["recipeIngredient"] == [
        "2 lb chicken thighs, boneless",
        "2 tbsp olive oil",
        "3 clove garlic, minced",
    ]


def test_format_quantity_whole_and_fractional():
    assert rs._format_quantity(2.0) == "2"
    assert rs._format_quantity(1.5) == "1.5"
    assert abs(float(rs._format_quantity(0.333333)) - 0.333) < 1e-6


def test_recipe_to_jsonld_nutrition_units(db_session):
    recipe = _build_recipe(db_session)
    doc = rs.recipe_to_jsonld(recipe)
    nutrition = doc["nutrition"]
    assert nutrition["@type"] == "NutritionInformation"
    assert nutrition["calories"] == "420 calories"
    assert nutrition["proteinContent"] == "35 g"
    assert nutrition["sodiumContent"] == "480 mg"


def test_recipe_to_jsonld_omits_nutrition_when_empty(db_session):
    recipe = _build_recipe(db_session, nutrition={})
    doc = rs.recipe_to_jsonld(recipe)
    assert "nutrition" not in doc


def test_recipe_to_jsonld_rating(db_session):
    recipe = _build_recipe(db_session, rating=5)
    doc = rs.recipe_to_jsonld(recipe)
    assert doc["aggregateRating"] == {
        "@type": "AggregateRating",
        "ratingValue": 5,
        "ratingCount": 1,
        "bestRating": 5,
        "worstRating": 1,
    }


def test_recipe_to_jsonld_omits_rating_when_none(db_session):
    recipe = _build_recipe(db_session, rating=None)
    doc = rs.recipe_to_jsonld(recipe)
    assert "aggregateRating" not in doc


def test_recipe_to_jsonld_tags_as_keywords(db_session):
    recipe = _build_recipe(db_session)
    tag_a = MealTag(name="quick")
    tag_b = MealTag(name="gluten_free")
    db_session.add_all([tag_a, tag_b])
    db_session.commit()
    recipe.tags.append(tag_a)
    recipe.tags.append(tag_b)
    db_session.commit()
    doc = rs.recipe_to_jsonld(recipe)
    assert doc["keywords"] == "gluten_free, quick"  # sorted


def test_recipe_to_jsonld_tips_as_namespaced_extension(db_session):
    recipe = _build_recipe(db_session)
    doc = rs.recipe_to_jsonld(recipe)
    assert doc["chef:tips"] == ["Great with rice.", "Freezes well."]


def test_recipe_to_jsonld_image_url_passthrough(db_session):
    recipe = _build_recipe(db_session)
    doc = rs.recipe_to_jsonld(recipe, image_url="http://localhost:8095/api/recipes/1/image")
    assert doc["image"] == "http://localhost:8095/api/recipes/1/image"


def test_recipe_to_jsonld_no_image_when_none_passed(db_session):
    recipe = _build_recipe(db_session)
    doc = rs.recipe_to_jsonld(recipe)
    assert "image" not in doc


# --- extract_jsonld_recipe_from_json (file-upload re-import path) ---------


def test_extract_jsonld_recipe_from_json_valid_document():
    doc = {
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": "Round Trip Soup",
        "recipeYield": "6",
        "prepTime": "PT10M",
        "cookTime": "PT30M",
        "recipeIngredient": ["2 cups broth", "1 onion, diced"],
        "recipeInstructions": [{"@type": "HowToStep", "text": "Simmer everything."}],
    }
    result = rs.extract_jsonld_recipe_from_json(json.dumps(doc))
    assert result is not None
    assert result["title"] == "Round Trip Soup"
    assert result["default_servings"] == 6
    assert result["prep_time_minutes"] == 10
    assert result["cook_time_minutes"] == 30
    assert result["instructions"] == ["Simmer everything."]
    assert len(result["ingredients"]) == 2


def test_extract_jsonld_recipe_from_json_malformed_json_returns_none():
    assert rs.extract_jsonld_recipe_from_json("{not valid json") is None


def test_extract_jsonld_recipe_from_json_no_recipe_node_returns_none():
    assert rs.extract_jsonld_recipe_from_json(json.dumps({"@type": "BreadcrumbList"})) is None


def test_extract_jsonld_recipe_from_json_reads_chef_tips_extension():
    doc = {"@type": "Recipe", "name": "X", "chef:tips": ["Tip one", "Tip two"]}
    result = rs.extract_jsonld_recipe_from_json(json.dumps(doc))
    assert result["tips"] == ["Tip one", "Tip two"]


# --- full round trip: recipe_to_jsonld -> extract_jsonld_recipe_from_json -
# -> coerce_recipe_fields, the exact sequence routers/recipes.py's
# file-upload import branch runs on a re-uploaded export.


def test_full_export_then_reimport_round_trip(db_session):
    original = _build_recipe(db_session)
    exported_doc = rs.recipe_to_jsonld(original)
    exported_json = json.dumps(exported_doc)

    parsed = rs.extract_jsonld_recipe_from_json(exported_json)
    assert parsed is not None
    coerced = rs.coerce_recipe_fields({k: v for k, v in parsed.items() if not k.startswith("_")})

    assert coerced["title"] == original.title
    assert coerced["default_servings"] == original.default_servings
    assert coerced["instructions"] == original.instructions
    assert coerced["tips"] == original.tips
    assert len(coerced["ingredients"]) == len(original.ingredients)
    reimported_names = {ing["ingredient_name"] for ing in coerced["ingredients"]}
    original_names = {ing.ingredient_name for ing in original.ingredients}
    assert reimported_names == original_names
    # Nutrition round-trips through free-text formatting and back --
    # exact float equality isn't guaranteed (schema.org properties are
    # free text, "420 calories" -> 420.0 is exact here since the
    # original values were already whole numbers, which _format_quantity
    # preserves losslessly).
    assert coerced["nutrition"]["calories"] == original.nutrition["calories"]
    assert coerced["nutrition"]["protein_g"] == original.nutrition["protein_g"]
