"""Allergen keywords have to match the plural, because recipes are written
in the plural.

Found 2026-08-07 on a real import: `Homemade Adaptogenic Energy Bars`
opens with `1 lb raw organic cashews` and produced NO tree-nut match at
all. `_build_pattern` emitted `\b(?:almond|cashew|...)\b`, and the
trailing `\b` requires a non-word character after the keyword -- so every
singular keyword in the list was invisible in its plural form.

It surfaced through a missing `contains_nuts` derived tag, which is the
cosmetic half. The same function backs `check_household_restrictions`:
a household with a tree-nut restriction would have been shown no warning
on a recipe whose first ingredient is a pound of cashews.

Why it survived this long: `peanuts` and `macadamia nuts` are listed in
their plural form already, so the cases anyone would think to try by hand
worked.
"""

import pytest

from app.services import allergen_service as a
from app.services import smart_tag_service as sts


@pytest.mark.parametrize(
    ("name", "allergen"),
    [
        # The measured case, verbatim from the import.
        ("raw organic cashews", "tree_nuts"),
        ("2 cups almonds", "tree_nuts"),
        ("chopped walnuts", "tree_nuts"),
        ("pecans, toasted", "tree_nuts"),
        ("pistachios", "tree_nuts"),
        ("hazelnuts", "tree_nuts"),
        ("shelled peanuts", "peanuts"),
        ("2 large eggs", "eggs"),
        ("anchovies", "fish"),
        ("prawns", "shellfish"),
        ("sesame seeds", "sesame"),
        ("soybeans", "soybeans"),
    ],
)
def test_a_plural_ingredient_still_matches(name, allergen):
    assert a.find_allergen_matches([name], [allergen]), f"{name!r} did not match {allergen}"


@pytest.mark.parametrize(
    ("name", "allergen"),
    [
        ("cashew", "tree_nuts"),
        ("almond flour", "tree_nuts"),
        ("macadamia nuts", "tree_nuts"),
        ("peanut butter", "peanuts"),
    ],
)
def test_the_singular_and_already_plural_forms_still_match(name, allergen):
    """Guards the regression the fix could have introduced -- the working
    cases are why this went unnoticed and must keep working."""
    assert a.find_allergen_matches([name], [allergen])


@pytest.mark.parametrize(
    ("name", "allergen"),
    [
        # A word that merely CONTAINS a keyword is not a match.
        ("coconut milk", "tree_nuts"),
        ("nutmeg", "tree_nuts"),
        # An explicit disclaimer still suppresses.
        ("nut-free spread", "tree_nuts"),
        ("gluten free flour", "gluten"),
    ],
)
def test_what_must_not_match_still_does_not(name, allergen):
    assert not a.find_allergen_matches([name], [allergen])


def test_the_energy_bars_derive_contains_nuts():
    """End to end through the thing that surfaced it."""
    bars = [
        "raw organic cashews",
        "organic powdered sugar",
        "water",
        "organic cardamom powder",
        "organic maca powder",
        "organic extra virgin olive oil",
    ]
    assert "contains_nuts" in [t.tag for t in sts.derive_tags(bars, {}, None)]


def test_the_household_would_now_be_warned():
    """The half that actually matters. This is the call the restriction
    warnings on every recipe page are built from."""
    matches = a.find_allergen_matches(["1 lb raw organic cashews"], ["tree_nuts"])
    assert matches and matches[0].matched_keyword.startswith("cashew")
