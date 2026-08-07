"""`CRUST` and `Crust` are one section, not two.

The UI groups ingredients AND instructions by the component string, so
two spellings of one heading render as two sections. The plan carried
this as the remaining half of the label instability that would justify
reaching for a full `RecipeComponent` table.

Case is deliberately NOT normalised to a house style: rule 3 asks the
model to copy the source's own heading, and a page that shouts `CRUST` is
not wrong to shout it here. Only DISAGREEMENT inside one recipe is fixed.
"""

from app.services.recipe_service import unify_component_case


def _ing(name, component):
    return {"ingredient_name": name, "quantity": None, "unit": None, "prep_note": None, "component": component}


def test_the_first_spelling_wins_within_one_recipe():
    ingredients = [_ing("graham crackers", "Crust"), _ing("sugar", "CRUST")]
    unify_component_case(ingredients, [])
    assert [i["component"] for i in ingredients] == ["Crust", "Crust"]


def test_the_ingredients_spelling_beats_the_instructions_one():
    """An ingredient's component came from copied source lines; an
    instruction's came from the model unaided. The copied one saw the
    page."""
    ingredients = [_ing("sugar", "Filling and Assembly")]
    steps = [{"component": "FILLING AND ASSEMBLY", "text": "Whisk."}]
    unify_component_case(ingredients, steps)
    assert steps[0]["component"] == "Filling and Assembly"


def test_the_for_the_prefix_is_someone_elses_job_and_still_gets_done():
    """`For the Crust` is stripped by normalize_component on the way in,
    long before this function sees it -- checked here so the division of
    labour is written down rather than rediscovered. What arrives here
    already reads `Crust`, and only the CASE is left to reconcile."""
    from app.services.recipe_service import normalize_component

    ingredients = [_ing("butter", normalize_component("Crust")), _ing("flour", normalize_component("FOR THE CRUST"))]
    unify_component_case(ingredients, [])
    assert [i["component"] for i in ingredients] == ["Crust", "Crust"]


def test_genuinely_different_sections_are_left_alone():
    ingredients = [_ing("butter", "Crust"), _ing("cream", "Topping")]
    unify_component_case(ingredients, [])
    assert [i["component"] for i in ingredients] == ["Crust", "Topping"]


def test_null_components_are_untouched():
    ingredients = [_ing("salt", None), _ing("butter", "Crust")]
    unify_component_case(ingredients, [])
    assert [i["component"] for i in ingredients] == [None, "Crust"]


def test_empty_lists_do_not_raise():
    unify_component_case(None, None)
    unify_component_case([], [])
