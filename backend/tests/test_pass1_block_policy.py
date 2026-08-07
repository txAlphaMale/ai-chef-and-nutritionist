"""One block policy, two callers.

`scripts/check_recipe_import_batch.py` used to re-implement
`extract_ingredients_two_pass`'s block loop. It duplicated for a real
reason -- the harness needs pass 1's raw counts, and getting them from the
app's function plus a separate pass 1 would mean three model calls per
file where the app makes two -- but a copy of a policy drifts from it, and
this one did twice: it scored the pizza at six ingredients while the app
stored five, and the 2026-08-07 bullet change broke its call signature
outright, so it would have crashed on every file of the next corpus run.

The loop now lives in `ingredients_from_pass1_blocks` and both call it.
These tests pin the DECISIONS that used to exist in two places.
"""

from app.services import recipe_service

SOURCE = "\n".join(
    [
        "Ingredients",
        "Crust",
        "12 graham crackers",
        "2 Tbsp. sugar",
        "Preheat oven to 325 degrees and pulse the crackers until fine.",
        "Transfer to a dish and bake for 20 minutes until the edges colour.",
        "30 minutes hands-on effort",
        "+ Chickpea flour or fine cornmeal",
    ]
)


def _blocks(*groups):
    return [{"component": component, "lines": list(lines)} for component, lines in groups]


def test_a_verified_block_becomes_ingredients_with_read_amounts():
    result = recipe_service.ingredients_from_pass1_blocks(
        _blocks(("Crust", ["12 graham crackers", "2 Tbsp. sugar"])), SOURCE
    )
    assert [(i["quantity"], i["unit"], i["ingredient_name"]) for i in result.ingredients] == [
        (12.0, None, "graham crackers"),
        (2.0, "tbsp", "sugar"),
    ]
    assert (result.lines_returned, result.lines_verified, result.blocks_dropped) == (2, 2, 0)


def test_a_block_of_method_text_is_dropped_and_says_so():
    """Both lines are real source text and verify perfectly. Only the
    per-block coverage gate can tell they are not an ingredient list --
    and the harness has to report the same verdict, from the same code."""
    result = recipe_service.ingredients_from_pass1_blocks(
        _blocks(
            ("Crust", ["12 graham crackers", "2 Tbsp. sugar"]),
            (
                "Instructions",
                [
                    "Preheat oven to 325 degrees and pulse the crackers until fine.",
                    "Transfer to a dish and bake for 20 minutes until the edges colour.",
                    "This line is not in the source at all.",
                    "Nor is this one.",
                ],
            ),
        ),
        SOURCE,
    )
    assert result.blocks_dropped == 1
    assert [i["ingredient_name"] for i in result.ingredients] == ["graham crackers", "sugar"]
    assert any("DROPPED" in m for m in result.messages)


def test_a_repeated_block_is_counted_once():
    """The 9B loops. Fourteen copies of one block must not become fourteen
    copies of its ingredients, in the app OR in the harness's totals."""
    block = ("Crust", ["12 graham crackers", "2 Tbsp. sugar"])
    result = recipe_service.ingredients_from_pass1_blocks(_blocks(block, block, block), SOURCE)

    assert len(result.ingredients) == 2
    assert (result.blocks_returned, result.duplicate_blocks) == (3, 2)


def test_a_metadata_line_that_verifies_is_still_not_food():
    """`30 minutes hands-on effort` starts with a number and is really on
    the page. Verification cannot help; only what it says can."""
    result = recipe_service.ingredients_from_pass1_blocks(
        _blocks(("main", ["12 graham crackers", "2 Tbsp. sugar", "30 minutes hands-on effort"])), SOURCE
    )
    assert [i["ingredient_name"] for i in result.ingredients] == ["graham crackers", "sugar"]


def test_a_bulleted_amountless_ingredient_survives_the_heading_rule():
    """The pizza's sixth. This is the exact decision whose signature
    change broke the old copy."""
    result = recipe_service.ingredients_from_pass1_blocks(
        _blocks(("main", ["12 graham crackers", "2 Tbsp. sugar", "+ Chickpea flour or fine cornmeal"])), SOURCE
    )
    assert [i["ingredient_name"] for i in result.ingredients] == [
        "graham crackers",
        "sugar",
        "Chickpea flour or fine cornmeal",
    ]


def test_a_hallucinated_line_is_reported_not_stored():
    result = recipe_service.ingredients_from_pass1_blocks(
        _blocks(("Crust", ["12 graham crackers", "2 Tbsp. sugar", "1 cup unicorn tears"])), SOURCE
    )
    assert "unicorn" not in " ".join(i["ingredient_name"] for i in result.ingredients)
    assert any("not found in the source" in m for m in result.messages)


def test_nothing_usable_is_an_empty_result_not_a_crash():
    assert recipe_service.ingredients_from_pass1_blocks([], SOURCE).ingredients == []
    assert recipe_service.ingredients_from_pass1_blocks(["not a dict", 7], SOURCE).ingredients == []
    assert recipe_service.ingredients_from_pass1_blocks(_blocks(("main", [])), SOURCE).ingredients == []
