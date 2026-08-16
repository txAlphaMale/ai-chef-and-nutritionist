"""Amount ranges, metric restatements and non-breaking spaces.

All three found by `scripts/import_healthcheck.py` on a real batch of 21
recipes (2026-08-07), which reported 19 ingredient names containing a
digit. They were not one bug:

    1-2 tablespoons melted fat  -> name='-2 tablespoons melted fat...'  x6
    1/2 cup (100 g) lentils     -> name='(100 g) uncooked red lentils'  x4
    3 figs\\xa0soaked ...        -> the nbsp defeats every \\s and \\b     x2
    4 lamb shanks or 6 ...      -> legitimate, left alone               x3

The range case is the worst of them: `_QTY_RE` matched the lower bound
and stopped, so the rest of the range sat at the head of the remainder,
the unit was never read, and the leftover text became the ingredient
NAME -- which is this app's join key.
"""

import pytest

from app.services.recipe_service import parse_ingredient_line_amounts as parse


@pytest.mark.parametrize(
    ("line", "quantity", "unit", "name"),
    [
        # Verbatim from the batch.
        ("1-2 tablespoons melted fat of choice", 1.0, "tbsp", "melted fat of choice"),
        ("2-3 tbsp ghee", 2.0, "tbsp", "ghee"),
        ("3 - 4 tbsp water", 3.0, "tbsp", "water"),
        ("1/2 to 1 teaspoon fine sea salt", 0.5, "tsp", "fine sea salt"),
        ("2 to 3 cups flour", 2.0, "cup", "flour"),
        # En and em dashes, which is what a page that cares about
        # typography actually publishes.
        ("1–2 tsp vanilla", 1.0, "tsp", "vanilla"),
        ("1—2 tsp vanilla", 1.0, "tsp", "vanilla"),
    ],
)
def test_a_range_takes_the_lower_bound_and_consumes_the_rest(line, quantity, unit, name):
    entry = parse(line)[0]
    assert entry["quantity"] == quantity
    assert entry["unit"] == unit
    assert entry["ingredient_name"] == name


def test_or_is_not_a_range():
    """`4 lamb shanks or 6 lamb shoulder shanks` is an alternative
    ingredient, not an upper bound -- and it is in this very batch.
    Treating `or` as a range separator would silently delete the
    alternative."""
    entry = parse("4 lamb shanks or 6 lamb shoulder shanks")[0]
    assert entry["quantity"] == 4.0
    assert entry["ingredient_name"] == "lamb shanks or 6 lamb shoulder shanks"


@pytest.mark.parametrize(
    ("line", "name", "note"),
    [
        ("1/2 cup (100 g) uncooked red lentils", "uncooked red lentils", "100 g"),
        ("1/2 cup (125 mL) water", "water", "125 mL"),
        ("1 1/2 tsp (7.5 mL) garlic powder", "garlic powder", "7.5 mL"),
    ],
)
def test_a_metric_restatement_leaves_the_name(line, name, note):
    """The same amount said twice is not part of the food's name. Kept as
    a note rather than dropped -- it is the source's own wording."""
    entry = parse(line)[0]
    assert entry["ingredient_name"] == name
    assert note in entry["prep_note"]


def test_a_size_adjective_does_not_hide_the_unit():
    entry = parse("1 heaping tablespoon (20 mL) virgin coconut oil or olive oil")[0]
    assert entry["quantity"] == 1.0
    assert entry["unit"] == "tbsp"
    assert entry["ingredient_name"] == "virgin coconut oil or olive oil"
    assert "heaping" in entry["prep_note"]


def test_a_non_breaking_space_is_normalised():
    """Real pages put U+00A0 between a number and its unit, and every \\s
    and \\b in the parser is blind to it."""
    entry = parse("3 figs soaked for 10 minutes")[0]
    assert entry["quantity"] == 3.0
    assert " " not in entry["ingredient_name"]
    assert entry["ingredient_name"] == "figs soaked for 10 minutes"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        # The behaviours a range fix could plausibly have broken.
        ("1 onion, diced", [(1.0, None, "onion", "diced")]),
        ("2 tbsp raw, local honey", [(2.0, "tbsp", "raw, local honey", None)]),
        ("1 1/2 cups rolled oats", [(1.5, "cup", "rolled oats", None)]),
        ("3/4 cup plus 2 Tbsp. sugar", [(0.75, "cup", "sugar", None), (2.0, "tbsp", "sugar", None)]),
        # `large` is a count unit in _COUNT_UNIT_WORDS -- pre-existing,
        # unchanged, and pinned here so a later range change cannot
        # quietly alter it.
        ("2 large eggs", [(2.0, "large", "eggs", None)]),
    ],
)
def test_everything_else_parses_as_it_did(line, expected):
    got = [(e["quantity"], e["unit"], e["ingredient_name"], e["prep_note"]) for e in parse(line)]
    assert got == expected
