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


# --- classes found on batch 4 of the real export -------------------------


def test_a_compound_amount_with_an_article_and_adjective():
    """`3 1/2 cups plus a scant 1/4 cup` -- the article and the size word
    sat between the joiner and the number, so the compound branch never
    saw a quantity and the whole tail became the name."""
    entries = parse("3 1/2 cups plus a scant 1/4 cup (455 grams) all-purpose or bread flour")
    assert [(e["quantity"], e["unit"]) for e in entries] == [(3.5, "cup"), (0.25, "cup")]
    assert {e["ingredient_name"] for e in entries} == {"all-purpose or bread flour"}


def test_a_range_inside_a_metric_parenthetical():
    entry = parse("2 teaspoons (6 to 7 grams) instant yeast")[0]
    assert entry["ingredient_name"] == "instant yeast"
    assert "6 to 7 grams" in entry["prep_note"]


@pytest.mark.parametrize(
    ("line", "name", "note"),
    [
        ("1 1/2 cups / 170g hazelnuts or walnuts", "hazelnuts or walnuts", "170g"),
        ("1/2 cup / 85g light brown sugar", "light brown sugar", "85g"),
    ],
)
def test_a_slash_metric_restatement(line, name, note):
    """How a British or dual-audience recipe writes the same amount
    twice. Same thing as the parenthetical form, handled the same way."""
    entry = parse(line)[0]
    assert entry["ingredient_name"] == name
    assert note in entry["prep_note"]


def test_a_fraction_is_not_mistaken_for_a_slash_restatement():
    """The guard on the rule above -- `1/2` must not be read as a slashed
    amount."""
    entry = parse("1/2 cup sugar")[0]
    assert entry["quantity"] == 0.5
    assert entry["ingredient_name"] == "sugar"


# --- a bare ordinal is not an ingredient ---------------------------------


def test_a_numbered_method_list_does_not_become_ingredients():
    """Measured: a page's numbered METHOD list arrived as eight
    ingredients named `1.` through `6.`."""
    from app.services import recipe_service as rs

    coerced = rs.coerce_recipe_fields(
        {
            "title": "Salad Dressings",
            "ingredients": [
                {"ingredient_name": "1."},
                {"ingredient_name": "2."},
                {"ingredient_name": "-"},
                {"ingredient_name": "olive oil", "quantity": 1, "unit": "cup"},
            ],
        }
    )
    assert [i["ingredient_name"] for i in coerced["ingredients"]] == ["olive oil"]


def test_a_name_that_merely_starts_with_a_digit_survives():
    """The weakest possible test on purpose -- one letter anywhere. It
    must not mistake a real ingredient for a number, which is the
    direction that loses data."""
    from app.services import recipe_service as rs

    assert rs.names_a_food("2% milk")
    assert rs.names_a_food("28-oz can tomatoes")
    assert not rs.names_a_food("1.")
    assert not rs.names_a_food("")
