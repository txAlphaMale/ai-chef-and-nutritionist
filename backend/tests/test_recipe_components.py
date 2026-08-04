"""Multi-component recipes: a dish built from named parts.

The failure these tests exist to prevent is the Pumpkin Chiffon Pie
import, whose real pypdf output is checked in at
tests/fixtures/pumpkin_chiffon_pie_pypdf.txt. Three things went wrong and
each has a test here:

  - the crust's "2 Tbsp. sugar" came back as 0.5 cup, taken from a
    sentence in the crust's method rather than from the ingredient line;
  - the filling's "3/4 cup plus 2 Tbsp." collapsed into a single
    1.75 cup entry;
  - a "graham cracker crumbs, 2 Tbsp" ingredient appeared that is in
    NEITHER ingredient list -- it exists only in the preparation text.

The first and third are prompt behaviour and can only be verified against
a live model (see test_fixture_shape_the_prompt_must_survive for the
structural facts about the source that the prompt has to handle). What is
tested here without a model is everything downstream: that a component
survives a round trip, and that quantity math aggregates ACROSS
components rather than grouping by them.
"""

from pathlib import Path

import pytest

from app.models.recipe import Recipe, RecipeIngredient
from app.routers.recipes import _apply_ingredients
from app.schemas.ai_extraction import (
    COMPONENT_UNSECTIONED,
    ExtractedIngredient,
    ExtractedRecipe,
    schema_of,
)
from app.schemas.recipe import RecipeIngredientBase
from app.services import meal_plan_service, recipe_service

FIXTURE = Path(__file__).parent / "fixtures" / "pumpkin_chiffon_pie_pypdf.txt"


def test_component_round_trips_through_create(db_session):
    parsed = {
        "title": "Pumpkin Chiffon Pie",
        "ingredients": [
            {"ingredient_name": "sugar", "quantity": 2, "unit": "Tbsp.", "component": "Crust"},
            {
                "ingredient_name": "sugar",
                "quantity": 0.75,
                "unit": "cup",
                "component": "Filling and Assembly",
                "prep_note": "scant, divided",
            },
        ],
    }
    recipe = recipe_service.create_recipe_from_parsed(db_session, parsed, source="import_file")
    db_session.flush()

    got = {(i.component, i.quantity, i.unit) for i in recipe.ingredients}
    assert got == {
        ("Crust", 2.0, "Tbsp."),
        ("Filling and Assembly", 0.75, "cup"),
    }


def test_component_defaults_to_none(db_session):
    """A single-component recipe has no component, and nothing invents one.

    None is the honest value for every recipe imported before the column
    existed; the migration deliberately backfills nothing.
    """
    parsed = {
        "title": "Toast",
        "ingredients": [{"ingredient_name": "bread", "quantity": 2, "unit": "slices"}],
    }
    recipe = recipe_service.create_recipe_from_parsed(db_session, parsed, source="manual")
    db_session.flush()
    assert [i.component for i in recipe.ingredients] == [None]


def test_the_extraction_schema_leaves_the_model_no_way_to_decline(db_session):
    """`component` must be required AND non-nullable in the grammar.

    Both weaker forms were tried against the live 9B and both produced a
    component on zero ingredients: `default=None` kept the field out of
    `required` entirely, and `str | None` kept it in `required` but let
    `null` satisfy it. This asserts the grammar handed to Ollama, which is
    the only thing the model is actually constrained by.
    """
    schema = schema_of(ExtractedRecipe)
    ingredient = schema["properties"]["ingredients"]["items"]

    component = ingredient["properties"]["component"]
    assert "component" in ingredient["required"]
    assert component.get("type") == "string", "a nullable component is an escape hatch the 9B takes on every row"
    # A nullable field reaches the grammar as anyOf[string, null], which
    # is exactly the shape that let the model answer null on every row.
    assert "anyOf" not in component

    # Contrast: the fields that genuinely may have no answer keep their
    # null branch. This is a targeted exception, not a change of rule.
    assert "anyOf" in ingredient["properties"]["quantity"]


def test_the_prompt_defines_the_field_the_schema_forces(db_session):
    """A field the grammar compels and the prompt never mentions gets
    filled with something invented. These two drift apart silently, so
    the link is pinned rather than trusted."""
    prompt = recipe_service.RECIPE_IMPORT_PROMPT

    assert '"component"' in prompt
    assert COMPONENT_UNSECTIONED in prompt


def test_the_prompt_does_not_forbid_what_the_grammar_requires():
    """Rule 1 once said "copy the quantity EXACTLY as written" and "leave
    it null rather than invent one", while the grammar types quantity as
    a number. Eight of the fifteen amounts in the real pie source are
    Unicode vulgar fractions, which a JSON number cannot hold -- so on
    those rows the two instructions were mutually unsatisfiable and rule 1
    said which way to resolve it. The live model answered null on all
    sixteen ingredients, twice.

    A stated fraction written as a decimal is the same amount, so the rule
    now says so. This pins the resolution, not the wording: if a future
    edit reinstates "exactly as written" without permitting the decimal
    form, the contradiction is back.
    """
    prompt = recipe_service.RECIPE_IMPORT_PROMPT

    # One worked fraction, not five. The first version of this fix spelled
    # out ¼/½/¾/1¼/1/3 and their decimals; that run converted a fraction
    # correctly and returned 5 of 16 ingredients instead of 16, at
    # temperature 0. The permission is what the model needed; the drill
    # was what it cost.
    assert "write a fraction as its decimal" in prompt
    assert "0.75" in prompt

    # Unit fidelity is the part of rule 1 that was always right and must
    # survive any rewording -- converting Tbsp. to cups is still wrong.
    assert "without changing the unit" in prompt
    # The escape hatch is conditional on the source stating nothing, so a
    # stated-but-fractional amount has no null to fall back to.
    assert "states no amount" in prompt


def test_rule_1_stays_short():
    """Every character in this prompt is paid for out of the same budget.

    Three live runs at temperature 0 tracked prompt length against
    completeness: 3654 chars extracted all 16 ingredients, 4047 extracted
    5. Rule 1 is the first thing the model reads after the source and the
    easiest place to overspend, so its length is pinned rather than left
    to judgement. Raising this ceiling means re-measuring, not editing the
    number.
    """
    rule_1 = next(line for line in recipe_service.RECIPE_IMPORT_PROMPT.splitlines() if line.startswith("1. "))

    assert len(rule_1) <= 260, (
        f"rule 1 is {len(rule_1)} chars -- it was 380 before any of this and cost completeness at 600"
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Crust", "Crust"),
        ("  Filling and Assembly  ", "Filling and Assembly"),
        ("main", None),
        ("Main", None),
        ("MAIN", None),
        ("  main  ", None),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_the_unsectioned_sentinel_never_reaches_the_database(raw, expected):
    """ "main" exists only to deny the model a null. NULL stays the
    database's word for "no named parts", which is also what every row
    imported before the column existed says."""
    assert recipe_service.normalize_component(raw) == expected


def test_the_sentinel_is_stripped_on_the_extraction_path(db_session):
    parsed = {
        "title": "Toast",
        "ingredients": [{"ingredient_name": "bread", "quantity": 2, "component": COMPONENT_UNSECTIONED}],
    }
    recipe = recipe_service.create_recipe_from_parsed(db_session, parsed, source="import_file")
    db_session.flush()

    assert [i.component for i in recipe.ingredients] == [None]


def test_coercion_carries_component_through():
    """The drop that made the first live measurement worthless.

    coerce_recipe_fields rebuilds every ingredient as an allowlist of
    named keys, and it had never been taught `component`. So the grammar
    forced the model to emit one, the model did, and this function threw
    all sixteen away before anything downstream saw them -- and the run
    was reported as "rule 3 produced nothing", blaming the prompt for a
    bug three layers below it.
    """
    coerced = recipe_service.coerce_recipe_fields(
        {
            "title": "Pumpkin Chiffon Pie",
            "ingredients": [
                {"ingredient_name": "sugar", "quantity": 2, "unit": "Tbsp.", "component": "Crust"},
                {"ingredient_name": "sugar", "quantity": 0.75, "unit": "cup", "component": "Filling and Assembly"},
            ],
        }
    )

    assert [i["component"] for i in coerced["ingredients"]] == ["Crust", "Filling and Assembly"]


def test_coercion_applies_the_unsectioned_sentinel():
    coerced = recipe_service.coerce_recipe_fields(
        {
            "title": "Toast",
            "ingredients": [{"ingredient_name": "bread", "component": COMPONENT_UNSECTIONED}],
        }
    )

    assert coerced["ingredients"][0]["component"] is None


def test_every_extraction_field_survives_coercion():
    """Generalizes the two tests above: any field the grammar can emit
    must be represented downstream, or the model is being compelled to
    produce something this app immediately discards. Driven off the schema
    so adding a field to ExtractedIngredient without teaching the
    allowlist about it fails here rather than in a live run.
    """
    coerced = recipe_service.coerce_recipe_fields(
        {
            "title": "Pie",
            "ingredients": [
                {
                    "ingredient_name": "sugar",
                    "quantity": 2,
                    "unit": "Tbsp.",
                    "prep_note": "divided",
                    "component": "Crust",
                }
            ],
        }
    )

    missing = set(ExtractedIngredient.model_fields) - set(coerced["ingredients"][0])
    assert not missing, f"coerce_recipe_fields silently drops {sorted(missing)}"


def test_the_api_create_path_persists_component(db_session):
    """The regression this catches: RecipeIngredientBase accepted
    `component` from the day the column landed, but _apply_ingredients
    never wrote it -- so every create and update through the API dropped
    it, including the import preview->confirm path, which is exactly where
    a multi-part recipe arrives.
    """
    recipe = Recipe(title="Pumpkin Chiffon Pie", default_servings=8)
    db_session.add(recipe)
    db_session.flush()

    _apply_ingredients(
        db_session,
        recipe,
        [
            RecipeIngredientBase(ingredient_name="sugar", quantity=2, unit="Tbsp.", component="Crust"),
            RecipeIngredientBase(ingredient_name="sugar", quantity=0.75, unit="cup", component="Filling"),
            RecipeIngredientBase(ingredient_name="salt", quantity=1, unit="tsp.", component=COMPONENT_UNSECTIONED),
        ],
    )
    db_session.flush()

    assert {(i.ingredient_name, i.component) for i in recipe.ingredients} == {
        ("sugar", "Crust"),
        ("sugar", "Filling"),
        ("salt", None),
    }


def test_grocery_merges_across_components():
    """The load-bearing one.

    2 Tbsp. of sugar in the crust and 2 Tbsp. in the filling are two real
    uses of one pantry item -- the household buys sugar once. So
    aggregation must ignore `component` entirely.

    This currently passes for a structural reason rather than a deliberate
    one: aggregate_ingredients rebuilds each dict with only
    ingredient_name/quantity/unit, so `component` is dropped on the way
    in. That makes it exactly the kind of correctness a later refactor
    could destroy while looking like an improvement -- preserving extra
    keys through the merge would silently split one shopping line in two.
    Hence a test rather than a comment.
    """
    merged = meal_plan_service.aggregate_ingredients(
        [
            [
                {"ingredient_name": "sugar", "quantity": 2, "unit": "Tbsp.", "component": "Crust"},
                {
                    "ingredient_name": "sugar",
                    "quantity": 2,
                    "unit": "Tbsp.",
                    "component": "Filling and Assembly",
                },
            ]
        ]
    )

    sugar = [m for m in merged if m["ingredient_name"] == "sugar"]
    assert len(sugar) == 1, f"component split one shopping line into {len(sugar)}"
    assert sugar[0]["quantity"] == 4
    assert sugar[0]["unit"] == "Tbsp."
    assert "component" not in sugar[0]


def test_scaling_preserves_component():
    """Scaling touches quantity only; the component label rides through."""
    scaled = recipe_service.scale_ingredients(
        [{"ingredient_name": "sugar", "quantity": 2, "unit": "Tbsp.", "component": "Crust"}],
        from_servings=2,
        to_servings=4,
    )
    assert scaled[0]["quantity"] == 4
    assert scaled[0]["component"] == "Crust"


def test_fixture_shape_the_prompt_must_survive():
    """Pins the properties of the real source that made this hard.

    Not a test of our code -- a test that the fixture still exhibits the
    conditions the import prompt was rewritten against. If a future pypdf
    bump reorders the extraction, the prompt's rule 1 ("position does not
    matter, line shape does") may be solving a problem that no longer
    exists in this form, and whoever sees this fail should re-read the
    rule before deleting the test.
    """
    text = FIXTURE.read_text(encoding="utf-8")

    # The method text arrives BEFORE the ingredient list. This is why a
    # model reading linearly binds "crust sugar" to the method's
    # "a scant 1/2 cup sugar" before it ever reaches "2 Tbsp. sugar".
    assert text.index("12 graham crackers") > text.index("Set aside 2 Tbsp")

    # Section headings appear twice -- once over prep, once over
    # ingredients -- so a heading name alone cannot identify the list.
    assert text.count("Filling and Assembly") >= 2

    # "scant" is genuinely on the ingredient line, so recording it as a
    # prep_note is correct provenance, not leakage from the method.
    assert "(scant) cup plus 2 Tbsp. sugar" in text

    # The crumbs quantity exists ONLY in prose. Any ingredient row for it
    # is fabricated.
    assert "2 Tbsp. graham cracker crumbs" in text
    assert "graham cracker crumbs\n" not in text
