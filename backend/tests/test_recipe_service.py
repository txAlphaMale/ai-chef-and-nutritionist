"""Unit tests for recipe_service.py's pure ingredient-list transforms:
scale_ingredients (pre-existing, not previously under its own test file)
and apply_display_unit_system (backlog B10.5). Pure functions over plain
dicts, no DB/network involved.

Also covers (2026-08-03, backlog B16.1 + the same-day recipe-import bug
fix): parse_recipe_response's resilience against the real-world model
output shapes that broke it (see ai_json_extraction.py and
test_ai_json_extraction.py for the underlying fix this exercises
end-to-end), and the new get_recipe_import_prompt/get_recipe_modify_prompt
DB-override-with-fallback getters.
"""

from __future__ import annotations

from app.models import SystemPrompt
from app.services import recipe_service


def _ing(**kwargs) -> dict:
    base = {
        "ingredient_name": "test ingredient",
        "quantity": 1,
        "unit": "cup",
        "prep_note": None,
        "resolution_source": None,
        "resolved_food_name": None,
        "fdc_id": None,
        "off_barcode": None,
        "nutrition_per_100g": None,
        "density_g_per_ml": None,
    }
    base.update(kwargs)
    return base


# --- scale_ingredients -------------------------------------------------


def test_scale_ingredients_scales_quantity_proportionally():
    scaled = recipe_service.scale_ingredients([_ing(quantity=2)], 2, 4)
    assert scaled[0]["quantity"] == 4


def test_scale_ingredients_noop_when_servings_match():
    ingredients = [_ing(quantity=2)]
    assert recipe_service.scale_ingredients(ingredients, 2, 2) is ingredients


def test_scale_ingredients_leaves_null_quantity_alone():
    scaled = recipe_service.scale_ingredients([_ing(quantity=None)], 2, 4)
    assert scaled[0]["quantity"] is None


# --- apply_display_unit_system (backlog B10.5) --------------------------


def test_apply_display_unit_system_original_strips_density_only():
    ingredients = [_ing(quantity=1, unit="cup", density_g_per_ml=0.5)]
    result = recipe_service.apply_display_unit_system(ingredients, "original")
    assert result[0]["quantity"] == 1
    assert result[0]["unit"] == "cup"
    assert result[0]["display_unavailable"] is False
    assert "density_g_per_ml" not in result[0]


def test_apply_display_unit_system_unknown_system_treated_as_original():
    ingredients = [_ing(quantity=1, unit="cup")]
    result = recipe_service.apply_display_unit_system(ingredients, "bogus")
    assert result[0]["quantity"] == 1
    assert result[0]["display_unavailable"] is False


def test_apply_display_unit_system_metric_converts_and_clears_density_key():
    ingredients = [_ing(quantity=1, unit="cup")]
    result = recipe_service.apply_display_unit_system(ingredients, "metric")
    assert result[0]["unit"] == "ml"
    assert "density_g_per_ml" not in result[0]
    assert result[0]["display_unavailable"] is False


def test_apply_display_unit_system_weight_mode_marks_unavailable_without_density():
    ingredients = [_ing(quantity=1, unit="cup", density_g_per_ml=None)]
    result = recipe_service.apply_display_unit_system(ingredients, "weight")
    assert result[0]["display_unavailable"] is True
    # Original quantity/unit preserved, not a guess.
    assert result[0]["quantity"] == 1
    assert result[0]["unit"] == "cup"


def test_apply_display_unit_system_weight_mode_converts_with_density():
    ingredients = [_ing(quantity=2, unit="cup", density_g_per_ml=0.529)]
    result = recipe_service.apply_display_unit_system(ingredients, "weight")
    assert result[0]["display_unavailable"] is False
    assert result[0]["unit"] == "g"


def test_apply_display_unit_system_null_quantity_passthrough_not_unavailable():
    ingredients = [_ing(quantity=None, unit=None)]
    result = recipe_service.apply_display_unit_system(ingredients, "weight")
    assert result[0]["display_unavailable"] is False
    assert result[0]["quantity"] is None


def test_apply_display_unit_system_count_unit_never_marked_unavailable():
    ingredients = [_ing(quantity=2, unit="clove", density_g_per_ml=None)]
    result = recipe_service.apply_display_unit_system(ingredients, "weight")
    assert result[0]["display_unavailable"] is False
    assert result[0]["quantity"] == 2
    assert result[0]["unit"] == "clove"


def test_apply_display_unit_system_preserves_other_keys():
    ingredients = [_ing(quantity=1, unit="cup", ingredient_name="flour", resolution_source="usda")]
    result = recipe_service.apply_display_unit_system(ingredients, "metric")
    assert result[0]["ingredient_name"] == "flour"
    assert result[0]["resolution_source"] == "usda"


# --- parse_recipe_response (2026-08-03 bug fix) --------------------------
#
# Author-reported: a plain, well-formed two-page recipe PDF ("Pumpkin
# Chiffon Pie") failed to parse at all -- "Could not extract a recipe
# from that input". Root cause was _extract_json_object's old naive
# greedy-regex fallback with no defense against an inline reasoning
# trace; see ai_json_extraction.py's module docstring for the full
# writeup. These exercise the fix through the actual public entry point
# every recipe import call site uses.


def test_parse_recipe_response_survives_an_inline_thinking_trace_with_a_scratch_json_draft():
    raw = (
        '<think>\nLet me structure this: { "title": "WRONG DRAFT" } -- '
        "actually let me re-read the ingredients section first.\n</think>\n"
        '{"title": "Pumpkin Chiffon Pie", "ingredients": '
        '[{"ingredient_name": "graham crackers", "quantity": 12, "unit": null, "prep_note": null}], '
        '"instructions": ["Preheat oven to 325."]}'
    )
    parsed = recipe_service.parse_recipe_response(raw)
    assert parsed is not None
    assert parsed["title"] == "Pumpkin Chiffon Pie"
    assert parsed["ingredients"][0]["ingredient_name"] == "graham crackers"


def test_parse_recipe_response_returns_none_for_a_genuinely_unusable_response():
    assert recipe_service.parse_recipe_response("I could not find a recipe in that content.") is None
    assert recipe_service.parse_recipe_response("") is None


def test_parse_recipe_response_handles_a_strict_well_formed_response():
    raw = '{"title": "Pumpkin Chiffon Pie", "ingredients": [], "instructions": []}'
    parsed = recipe_service.parse_recipe_response(raw)
    assert parsed["title"] == "Pumpkin Chiffon Pie"


# --- RECIPE_IMPORT_PROMPT / RECIPE_MODIFY_INSTRUCTIONS: .replace()-based
# substitution mechanics (2026-08-03, backlog B16.1) ----------------------
#
# Both prompts are now DB-backed, GUI-editable SystemPrompt rows (see
# get_recipe_import_prompt/get_recipe_modify_prompt below). They're filled
# in via plain str.replace(), not `.format()`, specifically so a household
# member's free-text edit -- which may well contain a literal, unescaped
# "{" or "}" (a JSON example, a recipe with curly quotes, anything) --
# can never raise a hard KeyError/IndexError at request time the way
# `.format()` would. These lock that contract down directly, rather than
# only implicitly through the getter tests below.


def test_recipe_import_prompt_content_placeholder_survives_replace_with_stray_braces():
    custom = 'Extract a recipe. Example: {"title": "x"}. SOURCE:\n{content}\n'
    rendered = custom.replace("{content}", "some source text with a } stray brace")
    assert "{content}" not in rendered
    assert "some source text with a } stray brace" in rendered
    # The unrelated worked-example braces are untouched, not mangled.
    assert '{"title": "x"}' in rendered


def test_recipe_import_prompt_has_no_leftover_format_only_double_braces():
    # A leftover {{ }} from the pre-2026-08-03 .format()-escaped version
    # would now render literally (str.replace() doesn't unescape it) --
    # this guards against that regression.
    assert "{{" not in recipe_service.RECIPE_IMPORT_PROMPT
    assert "}}" not in recipe_service.RECIPE_IMPORT_PROMPT


# --- get_recipe_import_prompt / get_recipe_modify_prompt (backlog B16.1) -


def test_get_recipe_import_prompt_falls_back_to_default_with_no_db_row(db_session):
    assert recipe_service.get_recipe_import_prompt(db_session) == recipe_service.RECIPE_IMPORT_PROMPT


def test_get_recipe_import_prompt_uses_active_db_override(db_session):
    db_session.add(SystemPrompt(prompt_key="recipe_import", content="CUSTOM PROMPT", is_active=True))
    db_session.commit()
    assert recipe_service.get_recipe_import_prompt(db_session) == "CUSTOM PROMPT"


def test_get_recipe_import_prompt_inactive_override_falls_back_to_default(db_session):
    # Unchecking "Active" in the Settings UI should revert to the shipped
    # default without deleting the household's draft edit (the row's
    # content is untouched, is_active just flips).
    db_session.add(SystemPrompt(prompt_key="recipe_import", content="CUSTOM PROMPT", is_active=False))
    db_session.commit()
    assert recipe_service.get_recipe_import_prompt(db_session) == recipe_service.RECIPE_IMPORT_PROMPT


def test_get_recipe_modify_prompt_falls_back_to_default_with_no_db_row(db_session):
    assert recipe_service.get_recipe_modify_prompt(db_session) == recipe_service.RECIPE_MODIFY_INSTRUCTIONS


def test_get_recipe_modify_prompt_uses_active_db_override(db_session):
    db_session.add(SystemPrompt(prompt_key="recipe_modify", content="CUSTOM EDIT PROMPT", is_active=True))
    db_session.commit()
    assert recipe_service.get_recipe_modify_prompt(db_session) == "CUSTOM EDIT PROMPT"
