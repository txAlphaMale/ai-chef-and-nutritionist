"""Unit tests for backlog B9.3's structured (schema.org JSON-LD) recipe
import helpers in app.services.recipe_service: the free-text ingredient-
line parser, ISO 8601 duration parsing, numeric extraction, instruction
flattening, and the end-to-end extract_jsonld_recipe() over small
hand-built HTML fixtures (a single Recipe object, an @graph-wrapped
document, a list containing a non-Recipe sibling type, and pages with no
usable JSON-LD at all)."""

from __future__ import annotations

import json

from app.services import recipe_service as rs

# --- _parse_quantity_token / _parse_ingredient_line ---------------------


def test_parse_ingredient_line_simple_quantity_and_volume_unit():
    result = rs._parse_ingredient_line("2 cups all-purpose flour")
    assert result == {"ingredient_name": "all-purpose flour", "quantity": 2.0, "unit": "cup", "prep_note": None}


def test_parse_ingredient_line_mixed_number():
    result = rs._parse_ingredient_line("1 1/2 tsp baking soda")
    assert result["quantity"] == 1.5
    assert result["unit"] == "tsp"
    assert result["ingredient_name"] == "baking soda"


def test_parse_ingredient_line_unicode_fraction():
    result = rs._parse_ingredient_line("½ cup sugar")
    assert result["quantity"] == 0.5
    assert result["unit"] == "cup"
    assert result["ingredient_name"] == "sugar"


def test_parse_ingredient_line_simple_fraction():
    result = rs._parse_ingredient_line("3/4 cup milk")
    assert result["quantity"] == 0.75
    assert result["unit"] == "cup"


def test_parse_ingredient_line_count_descriptor_unit():
    result = rs._parse_ingredient_line("3 large eggs")
    assert result["quantity"] == 3.0
    assert result["unit"] == "large"
    assert result["ingredient_name"] == "eggs"


def test_parse_ingredient_line_mass_unit_plural():
    result = rs._parse_ingredient_line("8 ounces cream cheese, softened")
    assert result["quantity"] == 8.0
    assert result["unit"] == "oz"
    assert result["ingredient_name"] == "cream cheese"
    assert result["prep_note"] == "softened"


def test_parse_ingredient_line_clove_unit_with_prep_note():
    result = rs._parse_ingredient_line("2 cloves garlic, minced")
    assert result["quantity"] == 2.0
    assert result["unit"] == "clove"
    assert result["ingredient_name"] == "garlic"
    assert result["prep_note"] == "minced"


def test_parse_ingredient_line_no_leading_quantity():
    result = rs._parse_ingredient_line("Salt and pepper to taste")
    assert result["quantity"] is None
    assert result["unit"] is None
    assert result["ingredient_name"] == "Salt and pepper to taste"
    assert result["prep_note"] is None


def test_parse_ingredient_line_unrecognized_leading_word_is_not_treated_as_a_unit():
    # "medium" IS recognized (count descriptor); a genuinely unknown word
    # like a brand/descriptor that isn't in the unit vocabulary should
    # stay part of the ingredient name instead of being silently dropped.
    result = rs._parse_ingredient_line("2 ripe bananas, mashed")
    assert result["quantity"] == 2.0
    assert result["unit"] is None
    assert result["ingredient_name"] == "ripe bananas"
    assert result["prep_note"] == "mashed"


def test_parse_ingredient_line_empty_string_falls_back_to_original():
    result = rs._parse_ingredient_line("   ")
    assert result["ingredient_name"] == ""
    assert result["quantity"] is None


# --- _parse_iso8601_duration_minutes ------------------------------------


def test_duration_hours_and_minutes():
    assert rs._parse_iso8601_duration_minutes("PT1H30M") == 90


def test_duration_minutes_only():
    assert rs._parse_iso8601_duration_minutes("PT15M") == 15


def test_duration_hours_only():
    assert rs._parse_iso8601_duration_minutes("PT1H") == 60


def test_duration_invalid_or_missing_returns_none():
    assert rs._parse_iso8601_duration_minutes(None) is None
    assert rs._parse_iso8601_duration_minutes("") is None
    assert rs._parse_iso8601_duration_minutes("not a duration") is None
    assert rs._parse_iso8601_duration_minutes(42) is None  # wrong type entirely


# --- _first_number -------------------------------------------------------


def test_first_number_extracts_from_text_with_units():
    assert rs._first_number("4 servings") == 4.0
    assert rs._first_number("270 kcal") == 270.0
    assert rs._first_number("10 g") == 10.0


def test_first_number_handles_bare_numeric_types():
    assert rs._first_number(4) == 4.0
    assert rs._first_number(4.5) == 4.5


def test_first_number_none_for_no_digits():
    assert rs._first_number("plenty") is None
    assert rs._first_number(None) is None


# --- _flatten_jsonld_instructions ----------------------------------------


def test_flatten_instructions_plain_string_with_newlines():
    assert rs._flatten_jsonld_instructions("Step one.\nStep two.\n\nStep three.") == [
        "Step one.",
        "Step two.",
        "Step three.",
    ]


def test_flatten_instructions_list_of_strings():
    assert rs._flatten_jsonld_instructions(["Do this.", "Then this."]) == ["Do this.", "Then this."]


def test_flatten_instructions_howto_step_objects():
    value = [
        {"@type": "HowToStep", "text": "Preheat the oven."},
        {"@type": "HowToStep", "text": "Mix the batter."},
    ]
    assert rs._flatten_jsonld_instructions(value) == ["Preheat the oven.", "Mix the batter."]


def test_flatten_instructions_nested_howto_sections():
    value = [
        {
            "@type": "HowToSection",
            "name": "Prep",
            "itemListElement": [{"@type": "HowToStep", "text": "Chop the onion."}],
        },
        {
            "@type": "HowToSection",
            "name": "Cook",
            "itemListElement": [{"@type": "HowToStep", "text": "Saute until soft."}],
        },
    ]
    assert rs._flatten_jsonld_instructions(value) == ["Chop the onion.", "Saute until soft."]


def test_flatten_instructions_none_returns_empty_list():
    assert rs._flatten_jsonld_instructions(None) == []


# --- _nutrition_from_jsonld -----------------------------------------------


def test_nutrition_from_jsonld_maps_known_keys():
    value = {
        "calories": "270 calories",
        "proteinContent": "10 g",
        "carbohydrateContent": "30 g",
        "fatContent": "8 g",
        "sodiumContent": "540 mg",
        "unknownField": "ignored",
    }
    result = rs._nutrition_from_jsonld(value)
    assert result == {
        "calories": 270.0,
        "protein_g": 10.0,
        "carbs_g": 30.0,
        "fat_g": 8.0,
        "sodium_mg": 540.0,
    }


def test_nutrition_from_jsonld_non_dict_returns_empty():
    assert rs._nutrition_from_jsonld(None) == {}
    assert rs._nutrition_from_jsonld("270 calories") == {}


# --- extract_jsonld_recipe (end to end over small HTML fixtures) --------


def _html_with_jsonld(payload) -> str:
    return f"""
    <html><head>
    <script type="application/ld+json">{json.dumps(payload)}</script>
    </head><body><p>Some page content.</p></body></html>
    """


_BASIC_RECIPE = {
    "@context": "https://schema.org",
    "@type": "Recipe",
    "name": "Test Chili",
    "description": "A hearty chili.",
    "recipeYield": "6 servings",
    "prepTime": "PT20M",
    "cookTime": "PT45M",
    "recipeIngredient": ["2 lb ground beef", "1 onion, diced", "2 cans (15 oz) black beans"],
    "recipeInstructions": [
        {"@type": "HowToStep", "text": "Brown the beef."},
        {"@type": "HowToStep", "text": "Add remaining ingredients and simmer."},
    ],
    "nutrition": {"@type": "NutritionInformation", "calories": "410 calories", "proteinContent": "28 g"},
    "author": {"@type": "Person", "name": "Jane Cook"},
    "image": "https://example.com/chili.jpg",
}


def test_extract_jsonld_recipe_basic_single_object():
    html = _html_with_jsonld(_BASIC_RECIPE)
    result = rs.extract_jsonld_recipe(html)
    assert result is not None
    assert result["title"] == "Test Chili"
    assert result["description"] == "A hearty chili."
    assert result["default_servings"] == 6.0
    assert result["prep_time_minutes"] == 20
    assert result["cook_time_minutes"] == 45
    # Steps now carry the section they belong to; this source has none.
    assert result["instructions"] == [
        {"component": None, "text": "Brown the beef."},
        {"component": None, "text": "Add remaining ingredients and simmer."},
    ]
    assert len(result["ingredients"]) == 3
    assert result["ingredients"][0] == {
        "ingredient_name": "ground beef",
        "quantity": 2.0,
        "unit": "lb",
        "prep_note": None,
    }
    assert result["nutrition"] == {"calories": 410.0, "protein_g": 28.0}
    assert result["_source_author"] == "Jane Cook"
    assert result["_image_url"] == "https://example.com/chili.jpg"


def test_extract_jsonld_recipe_handles_at_graph_wrapper():
    graph_doc = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "WebSite", "name": "Some Recipe Blog"},
            _BASIC_RECIPE,
        ],
    }
    html = _html_with_jsonld(graph_doc)
    result = rs.extract_jsonld_recipe(html)
    assert result is not None
    assert result["title"] == "Test Chili"


def test_extract_jsonld_recipe_handles_type_as_list():
    recipe_with_list_type = dict(_BASIC_RECIPE)
    recipe_with_list_type["@type"] = ["Recipe", "NewsArticle"]
    html = _html_with_jsonld(recipe_with_list_type)
    result = rs.extract_jsonld_recipe(html)
    assert result is not None
    assert result["title"] == "Test Chili"


def test_extract_jsonld_recipe_finds_recipe_among_sibling_array_entries():
    html = f"""
    <html><head>
    <script type="application/ld+json">{json.dumps({"@type": "BreadcrumbList", "itemListElement": []})}</script>
    <script type="application/ld+json">{json.dumps(_BASIC_RECIPE)}</script>
    </head><body></body></html>
    """
    result = rs.extract_jsonld_recipe(html)
    assert result is not None
    assert result["title"] == "Test Chili"


def test_extract_jsonld_recipe_returns_none_when_no_jsonld_present():
    html = "<html><head></head><body><p>No structured data here.</p></body></html>"
    assert rs.extract_jsonld_recipe(html) is None


def test_extract_jsonld_recipe_returns_none_for_malformed_json():
    html = """
    <html><head>
    <script type="application/ld+json">{ this is not valid json </script>
    </head><body></body></html>
    """
    assert rs.extract_jsonld_recipe(html) is None


def test_extract_jsonld_recipe_returns_none_when_recipe_node_has_no_name():
    nameless = dict(_BASIC_RECIPE)
    del nameless["name"]
    html = _html_with_jsonld(nameless)
    assert rs.extract_jsonld_recipe(html) is None


def test_extract_jsonld_recipe_instructions_as_plain_string():
    recipe = dict(_BASIC_RECIPE)
    recipe["recipeInstructions"] = "Brown the beef.\nSimmer for 30 minutes."
    html = _html_with_jsonld(recipe)
    result = rs.extract_jsonld_recipe(html)
    assert result["instructions"] == [
        {"component": None, "text": "Brown the beef."},
        {"component": None, "text": "Simmer for 30 minutes."},
    ]


def test_extract_jsonld_recipe_missing_optional_fields_degrade_gracefully():
    minimal = {"@type": "Recipe", "name": "Bare Minimum Recipe"}
    html = _html_with_jsonld(minimal)
    result = rs.extract_jsonld_recipe(html)
    assert result["title"] == "Bare Minimum Recipe"
    assert result["default_servings"] is None
    assert result["prep_time_minutes"] is None
    assert result["cook_time_minutes"] is None
    assert result["instructions"] == []
    assert result["ingredients"] == []
    assert result["nutrition"] == {}
    assert result["_source_author"] is None
    assert result["_image_url"] is None


# --- coerce_recipe_fields compatibility -----------------------------------
# extract_jsonld_recipe()'s output must round-trip cleanly through the
# SAME coercion function the Ollama import path uses -- this is the whole
# point of returning the same pre-coercion shape rather than a parallel one.


def test_extract_jsonld_recipe_output_coerces_cleanly():
    html = _html_with_jsonld(_BASIC_RECIPE)
    raw = rs.extract_jsonld_recipe(html)
    coerced = rs.coerce_recipe_fields({k: v for k, v in raw.items() if not k.startswith("_")})
    assert coerced["title"] == "Test Chili"
    assert coerced["default_servings"] == 6
    assert coerced["ingredients"][0]["ingredient_name"] == "ground beef"
    assert coerced["nutrition"]["calories"] == 410.0
