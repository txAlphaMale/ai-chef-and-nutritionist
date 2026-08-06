"""Real source text that verifies perfectly and is not an ingredient.

This is its own failure class, distinct from hallucination and distinct
from a wrapped line, and it only became visible once pdfplumber started
returning the lines a page actually shows. `leopard_crust_pizza_pdfplumber.txt`
lines 42-52:

    30 minutes hands-on effort          <- metadata
    Prepare dough 1-3 days ahead        <- metadata
    This recipe was developed to scale  <- prose
    Ingredients for 2 dough balls (300g each)
    320g Caputo Fioreglut flour (100%)  <- the list starts here
    ...
    + Chickpea flour or fine cornmeal

Pass 1 copies the metadata along with the list, because it sits directly
above it and looks the part. Verification cannot object -- the lines ARE
in the source -- and the per-block coverage gate cannot either, because
most of the block really is ingredients. Only what a line SAYS is left.

`30 minutes hands-on effort` was imported three runs running as an
ingredient named `minutes hands-on effort`, quantity 30.
"""

import pytest

from app.services import recipe_service


@pytest.mark.parametrize(
    ("entry", "dropped"),
    [
        # Measured, live, three consecutive batch runs.
        ({"ingredient_name": "minutes hands-on effort", "unit": None, "quantity": 30.0}, True),
        ({"ingredient_name": "hours to ferment", "unit": None, "quantity": 16.0}, True),
        # `Minute Rice` is a real product, and this is why the rule
        # requires that no unit was parsed: a recipe that means an
        # ingredient gives it a measure.
        ({"ingredient_name": "Minute Rice", "unit": "cup", "quantity": 1.0}, False),
        ({"ingredient_name": "day-old baguette", "unit": None, "quantity": 1.0}, False),
        ({"ingredient_name": "Caputo Fioreglut flour", "unit": "g", "quantity": 320.0}, False),
        # Amountless and unitless, but a real ingredient. The duration rule
        # must not be the thing that removes it.
        ({"ingredient_name": "Chickpea flour or fine cornmeal", "unit": None, "quantity": None}, False),
        ({"ingredient_name": "", "unit": None, "quantity": None}, False),
    ],
)
def test_an_ingredient_is_never_named_after_a_span_of_time(entry, dropped):
    assert recipe_service._names_a_duration(entry) is dropped


def test_the_pizza_metadata_line_is_in_the_fixture():
    """The premise, so this rule cannot quietly become dead code."""
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "leopard_crust_pizza_pdfplumber.txt"
    lines = fixture.read_text(encoding="utf-8").split("\n")
    assert "30 minutes hands-on effort" in lines
    assert "+ Chickpea flour or fine cornmeal" in lines
