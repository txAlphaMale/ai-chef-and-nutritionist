"""Instructions belonging to a part of the dish.

Ingredients have carried a `component` since 2026-08-03, so an imported
Pumpkin Chiffon Pie knew which of its ingredients were the crust's and
which were the filling's. Instructions did not: they were `list[str]` in
the model, the API schema and the extraction schema, so the steps arrived
as one undifferentiated run of 22 and the recipe page showed them that
way.

Two things had to be true at once, and this file pins both:

  * A step can say which part it belongs to.
  * Every recipe already saved -- a plain list of strings in a JSON
    column that is NOT migrated -- still reads correctly, from the API,
    the UI, and the export.

The second is why nothing here asserts that storage is uniform. It is
not, deliberately: coercion on read costs one function, a migration costs
a flag day and cannot be undone if it goes wrong.
"""

import pytest

from app.schemas.recipe import InstructionStep, RecipeBase
from app.services import recipe_service as rs


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # The shape every pre-existing recipe is stored in.
        (["Preheat.", "Bake."], [{"component": None, "text": "Preheat."}, {"component": None, "text": "Bake."}]),
        # The shape the extraction schema now returns.
        (
            [{"component": "Crust", "text": "Press into the tin."}],
            [{"component": "Crust", "text": "Press into the tin."}],
        ),
        # The unsectioned sentinel never reaches storage, exactly as it
        # does not for ingredients.
        ([{"component": "main", "text": "Mix."}], [{"component": None, "text": "Mix."}]),
        # A generic heading is not a part of the dish -- same rule, same
        # function, because the UI groups both lists by this string.
        ([{"component": "INGREDIENTS YOU'LL NEED:", "text": "Mix."}], [{"component": None, "text": "Mix."}]),
        # `For the Crust` and `Crust` are one part written two ways.
        ([{"component": "For the Crust", "text": "Press."}], [{"component": "Crust", "text": "Press."}]),
        # A mixed list is what an edited older recipe looks like.
        (
            ["Old step", {"component": "Filling", "text": "Whisk."}],
            [{"component": None, "text": "Old step"}, {"component": "Filling", "text": "Whisk."}],
        ),
        # Blank steps are dropped rather than stored as empty rows.
        ([{"component": "Crust", "text": "   "}, "", "  "], []),
        (None, []),
    ],
)
def test_stored_steps_normalise_whatever_shape_they_arrive_in(raw, expected):
    assert rs.normalize_instructions(raw) == expected


def test_the_api_schema_accepts_the_old_shape():
    """An API client sending a list of strings is not wrong -- it is what
    this app itself sent until today."""
    recipe = RecipeBase(title="Toast", instructions=["Toast the bread.", "Butter it."])
    assert recipe.instructions == [
        InstructionStep(component=None, text="Toast the bread."),
        InstructionStep(component=None, text="Butter it."),
    ]


def test_instruction_texts_are_available_flat_for_prompts():
    steps = [{"component": "Crust", "text": "Press."}, "Chill."]
    assert rs.instruction_texts(steps) == ["Press.", "Chill."]


def test_jsonld_sections_become_components():
    """schema.org already had the answer. A HowToSection IS a component,
    and its name was being parsed and then discarded, so a structured
    import flattened a source that had told us how it was divided."""
    value = [
        {
            "@type": "HowToSection",
            "name": "For the Crust",
            "itemListElement": [
                {"@type": "HowToStep", "text": "Crush the crackers."},
                {"@type": "HowToStep", "text": "Bake 20 minutes."},
            ],
        },
        {
            "@type": "HowToSection",
            "name": "Filling and Assembly",
            "itemListElement": [{"@type": "HowToStep", "text": "Whisk the yolks."}],
        },
    ]
    assert rs._jsonld_instruction_steps(value) == [
        {"component": "Crust", "text": "Crush the crackers."},
        {"component": "Crust", "text": "Bake 20 minutes."},
        {"component": "Filling and Assembly", "text": "Whisk the yolks."},
    ]


def test_the_flat_jsonld_helper_still_returns_plain_text():
    """Kept as the texts-only view so its own tests, and any caller that
    only wants words, are unaffected by the shape change."""
    value = [{"@type": "HowToSection", "name": "Crust", "itemListElement": ["Press."]}]
    assert rs._flatten_jsonld_instructions(value) == ["Press."]


def test_what_the_export_writes_is_what_the_import_reads_back():
    """A component that survives export but not re-import is not
    preserved, so the two schema.org-facing shapes are checked against
    each other rather than each on its own."""
    exported_sections = [
        {"@type": "HowToSection", "name": "Crust", "itemListElement": [{"@type": "HowToStep", "text": "Press."}]},
        {"@type": "HowToSection", "name": "Filling", "itemListElement": [{"@type": "HowToStep", "text": "Whisk."}]},
    ]
    assert rs._jsonld_instruction_steps(exported_sections) == [
        {"component": "Crust", "text": "Press."},
        {"component": "Filling", "text": "Whisk."},
    ]

    # An unsectioned recipe exports flat HowToSteps and reads back flat --
    # no phantom component invented on the way through.
    exported_flat = [{"@type": "HowToStep", "text": "Press."}, {"@type": "HowToStep", "text": "Whisk."}]
    assert rs._jsonld_instruction_steps(exported_flat) == rs.normalize_instructions(["Press.", "Whisk."])


def test_chat_context_labels_each_run_of_steps():
    """The recipe-scoped chat has to be able to answer "can I make the
    crust ahead?", which means the prompt has to say which steps those
    are."""
    context = rs.build_recipe_chat_context(
        {
            "title": "Pie",
            "servings_shown": 8,
            "ingredients": [],
            "instructions": [
                {"component": "Crust", "text": "Press."},
                {"component": "Crust", "text": "Bake."},
                {"component": "Filling", "text": "Whisk."},
            ],
        }
    )
    assert "[Crust]\n1. Press.\n2. Bake.\n[Filling]\n3. Whisk." in context


def test_chat_context_says_nothing_extra_for_an_unsectioned_recipe():
    context = rs.build_recipe_chat_context(
        {"title": "Toast", "servings_shown": 2, "ingredients": [], "instructions": ["Toast it.", "Eat it."]}
    )
    assert "1. Toast it.\n2. Eat it." in context
    assert "[" not in context.split("Instructions:")[1]
