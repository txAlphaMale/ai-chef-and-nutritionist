"""Derived tags, and the reason they are all phrased as "contains".

The first version of this module derived `gluten_free` from the ABSENCE
of an allergen match. Its first run returned `gluten_free` for a
graham-cracker pie crust and `dairy_free` for a caesar salad containing
parmesan -- because `allergen_service` is built to flag what it
RECOGNISES, so its misses are false negatives, and inverting it turns
every miss into a false SAFETY claim.

Both of those cases are pinned below, in their corrected form. A match is
evidence; a non-match is not.
"""

import pytest

from app.services.smart_tag_service import TRUSTED_NUTRITION_PROVENANCE, derive_tags

PIE = ["graham crackers", "sugar", "unsalted butter", "unflavored gelatin", "egg yolks", "whole milk", "heavy cream"]
CAESAR = ["romaine", "parmesan", "worcestershire sauce", "olive oil"]
LENTIL_SOUP = ["red lentils", "carrot", "onion", "olive oil", "vegetable stock"]


def _tags(names, nutrition=None, provenance=None):
    return {t.tag for t in derive_tags(names, nutrition or {}, provenance)}


def test_no_tag_ever_claims_a_recipe_is_free_of_anything():
    """The guard on the whole design. If a `*_free` tag ever appears
    here, the safety inversion has come back."""
    for names in (PIE, CAESAR, LENTIL_SOUP):
        assert not any(tag.endswith("_free") for tag in _tags(names)), names


def test_the_lentil_soup_gets_no_tags_rather_than_a_clean_bill_of_health():
    """It really is vegan and gluten-free. The app still says nothing,
    because "we found nothing" is not the same as "there is nothing"."""
    assert _tags(LENTIL_SOUP) == set()


def test_parmesan_is_dairy_even_though_the_allergen_matcher_misses_it():
    """The caesar-salad case. allergen_service's keyword list is tuned for
    warnings and does not know parmesan is milk; the dairy word list here
    supplements it, which strengthens a positive claim rather than
    weakening one."""
    assert "contains_dairy" in _tags(CAESAR)


def test_worcestershire_is_fish_and_meat():
    tags = _tags(CAESAR)
    assert "contains_meat" in tags and "contains_animal_products" in tags


def test_gelatin_is_an_animal_product_but_calling_it_meat_would_be_wrong():
    """Calling a panna cotta "contains meat" is how a household stops
    trusting the tags."""
    tags = _tags(["unflavored gelatin", "sugar", "water"])
    assert tags == {"contains_animal_products"}


@pytest.mark.parametrize(
    "name",
    ["almond milk", "coconut milk", "oat milk", "soy milk", "vegan butter", "cashew cream"],
)
def test_a_plant_version_of_a_dairy_word_is_not_dairy(name):
    assert "contains_dairy" not in _tags([name, "rolled oats"])


def test_a_plant_qualifier_after_the_dairy_word_does_not_excuse_it():
    """`milk chocolate almonds` is dairy. The qualifier has to come
    first."""
    assert "contains_dairy" in _tags(["milk chocolate almonds"])


def test_nutrition_tags_need_figures_that_were_computed_not_guessed():
    """The pie's AI estimate read 380mg of cholesterol against a real
    ~115. A `heart_healthy` tag off a number that wrong is worse than no
    tag."""
    lean = {"carbs_g": 6, "sodium_mg": 95, "saturated_fat_g": 3.1, "cholesterol_mg": 54}

    for provenance in TRUSTED_NUTRITION_PROVENANCE:
        tags = _tags(["salmon fillet", "asparagus"], lean, provenance)
        assert {"keto", "low_carb", "low_sodium", "heart_healthy"} <= tags, provenance

    for provenance in ("ai_estimated", None, "", "guessed"):
        tags = _tags(["salmon fillet", "asparagus"], lean, provenance)
        assert not tags & {"keto", "low_carb", "low_sodium", "heart_healthy"}, provenance


def test_a_threshold_is_only_met_when_the_figure_is_actually_under_it():
    rich = {"carbs_g": 62, "sodium_mg": 890, "saturated_fat_g": 14, "cholesterol_mg": 210}
    assert not _tags(["rice", "oil"], rich, "computed") & {"keto", "low_carb", "low_sodium", "heart_healthy"}


def test_a_missing_figure_never_earns_its_tag():
    """Absent is not zero. A recipe with no sodium figure is not low
    sodium."""
    assert "low_sodium" not in _tags(["rice"], {"carbs_g": 5}, "computed")
    assert "heart_healthy" not in _tags(["rice"], {"saturated_fat_g": 1}, "computed")


def test_a_junk_nutrition_value_is_ignored_rather_than_raising():
    assert _tags(["rice"], {"carbs_g": "not a number", "sodium_mg": None}, "computed") == set()


def test_every_tag_carries_the_evidence_it_was_derived_from():
    """A tag that cannot explain itself is a tag nobody should filter a
    diet by."""
    for tag in derive_tags(PIE, {}, None):
        assert tag.basis.strip(), tag.tag


def test_an_empty_recipe_derives_nothing():
    assert derive_tags([], {}, "computed") == []
    assert derive_tags(None, None, None) == []


def test_derived_tags_reach_the_api_and_are_not_the_editable_ones(db_session):
    """Two lists, deliberately: `tags` is what the household and the model
    can write, `derived_tags` is what the app worked out and nobody can
    type."""
    from app.models import Recipe, RecipeIngredient
    from app.routers.recipes import _to_read

    recipe = Recipe(title="Pie", default_servings=8, instructions=[], nutrition={}, nutrition_provenance=None)
    recipe.ingredients = [RecipeIngredient(ingredient_name=name, quantity=1) for name in PIE]
    db_session.add(recipe)
    db_session.commit()
    db_session.refresh(recipe)

    read = _to_read(recipe, db_session)
    derived = {t.tag for t in read.derived_tags}

    assert "contains_dairy" in derived and "contains_egg" in derived
    assert not any(t.endswith("_free") for t in derived)
    assert read.tags == []


def test_a_recipe_whose_nutrition_was_computed_gets_its_threshold_tags(db_session):
    from app.models import Recipe, RecipeIngredient
    from app.routers.recipes import _to_read

    recipe = Recipe(
        title="Salmon",
        default_servings=2,
        instructions=[],
        nutrition={"carbs_g": 6, "sodium_mg": 95, "saturated_fat_g": 3.1, "cholesterol_mg": 54},
        nutrition_provenance="computed",
    )
    recipe.ingredients = [RecipeIngredient(ingredient_name="salmon fillet", quantity=2, unit="fillet")]
    db_session.add(recipe)
    db_session.commit()
    db_session.refresh(recipe)

    assert {"keto", "low_sodium", "heart_healthy"} <= {t.tag for t in _to_read(recipe, db_session).derived_tags}
