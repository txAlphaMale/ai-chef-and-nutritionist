"""`2 tbsp raw, local honey` was stored as name='raw'.

The comma split reads an ingredient line as `<food>, <preparation>`,
which is right for `1 onion, diced`. A source that writes
`<quality>, <food>` means the opposite, so the food was filed as a prep
note and a bare adjective became the ingredient NAME.

It hid well: the detail page re-joins name and prep note for display, so
the page read "2 tbsp raw, local honey" while the database held 'raw'.
It took a `repr()` query against the container to see it.

The name is this app's join key. Inventory matching, grocery
aggregation, nutrition resolution, the allergen warnings and the derived
tags all read it -- so `1 cup raw, unsalted cashews` produced no tree-nut
warning at all, which is the same safety failure as the plural-keyword
bug from a different direction.

The tests that matter most are the ones below asserting the split STILL
happens, because narrowing it too far would break every ordinary prep
note in the corpus.
"""

import pytest

from app.services import allergen_service
from app.services import smart_tag_service as sts
from app.services.recipe_service import parse_ingredient_line_amounts as parse


@pytest.mark.parametrize(
    ("line", "name"),
    [
        # The measured case, verbatim from recipe 12.
        ("2 tbsp raw, local honey", "raw, local honey"),
        ("1 cup raw, unsalted cashews", "raw, unsalted cashews"),
        ("2 tbsp fresh, chopped parsley", "fresh, chopped parsley"),
        ("1 cup frozen, chopped spinach", "frozen, chopped spinach"),
        ("2 cups dried, sliced mushrooms", "dried, sliced mushrooms"),
    ],
)
def test_a_quality_word_head_is_not_a_name(line, name):
    assert parse(line)[0]["ingredient_name"] == name
    assert parse(line)[0]["prep_note"] is None


@pytest.mark.parametrize(
    ("line", "name", "prep"),
    [
        ("1 onion, diced", "onion", "diced"),
        ("2 cups flour, sifted", "flour", "sifted"),
        ("2 tbsp butter, melted", "butter", "melted"),
        ("3 organic red beets, peeled and chopped small", "organic red beets", "peeled and chopped small"),
        # `whole` IS a quality word, but `milk` is not -- the head names a
        # food, so this splits exactly as it always did.
        ("1 cup whole milk, warmed", "whole milk", "warmed"),
        ("salt and pepper, to taste", "salt and pepper", "to taste"),
    ],
)
def test_an_ordinary_prep_note_still_splits(line, name, prep):
    entry = parse(line)[0]
    assert entry["ingredient_name"] == name
    assert entry["prep_note"] == prep


def test_the_honey_is_now_visible_to_the_derived_tags():
    """End to end through what surfaced it."""
    names = [parse(line)[0]["ingredient_name"] for line in ["3 organic red beets", "2 tbsp raw, local honey"]]
    assert "contains_animal_products" in [t.tag for t in sts.derive_tags(names, {}, None)]


def test_the_cashews_would_now_warn_a_nut_allergic_household():
    """The half that matters more than a tag. `raw, unsalted cashews` is
    an ordinary way to write a shopping line, and it was invisible."""
    name = parse("1 cup raw, unsalted cashews")[0]["ingredient_name"]
    assert allergen_service.find_allergen_matches([name], ["tree_nuts"])


# --- a comma inside brackets is not the prep-note comma ------------------


@pytest.mark.parametrize(
    ("line", "name", "note"),
    [
        # Measured on a real batch: this stored the name as
        # `jalapeno peppers (or 1 serrano pepper` -- an unclosed bracket in
        # the join key, with `seeded)` as the note.
        ("2 jalapeno peppers (or 1 serrano pepper, seeded)", "jalapeno peppers", "or 1 serrano pepper, seeded"),
        ("1 onion (about 2 cups, chopped)", "onion", "about 2 cups, chopped"),
        ("1 cup nuts (walnuts, pecans, or almonds)", "nuts", "walnuts, pecans, or almonds"),
    ],
)
def test_a_comma_inside_brackets_does_not_split(line, name, note):
    entry = parse(line)[0]
    assert entry["ingredient_name"] == name
    assert entry["prep_note"] == note


def test_a_comma_after_the_brackets_still_splits():
    entry = parse("1 onion (peeled), diced")[0]
    assert entry["ingredient_name"] == "onion"
    assert "diced" in entry["prep_note"]
    assert "peeled" in entry["prep_note"]


def test_an_unclosed_bracket_does_not_swallow_the_rest_of_the_line():
    """Real pages do publish mismatched brackets. Depth must not go
    negative or a stray `)` would start splitting at commas again."""
    entry = parse("1 onion) something, diced")[0]
    assert entry["prep_note"] == "diced"
