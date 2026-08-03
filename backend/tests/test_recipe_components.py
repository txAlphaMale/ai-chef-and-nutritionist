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

from app.models.recipe import Recipe, RecipeIngredient
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
