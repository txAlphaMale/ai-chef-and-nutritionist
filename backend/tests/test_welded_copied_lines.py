"""Pass 1 copying a whole ingredient list as ONE array entry.

Found on 2026-08-07, on the first bookmarks run against real pages --
which was also the first time trafilatura had ever run on one. Four
recipes in a single batch of twenty arrived like this:

    ['- 1 tablespoon butter for greasing the baking sheet\\n
      - 1/4 cup buttermilk\\n- 1/4 cup honey ... ']

Verification looked for that nine-line blob verbatim in the source,
could not find it, and dropped it. The block then scored 0 of 1 verified,
the coverage gate dropped the whole block -- correctly, by its own rule --
and the import fell back to the single call's unverified list. Every log
line read as the system working. The input was one line that should have
been nine.

The constraint is the interesting half. Splitting widens what can verify,
and an unconstrained widening has already cost this project 17 method
steps imported as ingredients (2026-08-06). So a blob is split only when
EVERY line it produces starts with a bullet or an amount, which is what
method prose does not do. The refusal cases below matter more than the
acceptance ones.
"""

import pytest

from app.services import recipe_service as rs

# The Sage Oatcakes list, verbatim from the container log.
OATCAKES = (
    "- 1 tablespoon butter for greasing the baking sheet\n"
    "- 1/4 cup buttermilk\n"
    "- 1/4 cup honey (warm temperature and liquid)\n"
    "- 1 1/2 cups rolled oats (not soaked; avoid quick or instant oats)\n"
    "- 1 cup all-purpose flour (gluten free if desired)\n"
    "- 1 teaspoon finely ground dried sage leaves\n"
    "- 1/2 teaspoon baking soda\n"
    "- 1/4 teaspoon salt\n"
    "- 1/2 cup chilled butter, cut into pieces"
)

# The taco seasoning list, verbatim -- no bullets, amounts only.
TACO = (
    "1/2 cup chili powder\n"
    "1/4 cup onion powder\n"
    "1/8 cup ground cumin\n"
    "1 tablespoon garlic powder\n"
    "1 tablespoon paprika\n"
    "1 tablespoon sea salt"
)


def test_a_bulleted_list_is_split_into_its_lines():
    out = rs.split_welded_copied_lines([OATCAKES])
    assert len(out) == 9
    assert out[0] == "- 1 tablespoon butter for greasing the baking sheet"
    assert out[-1] == "- 1/2 cup chilled butter, cut into pieces"


def test_an_unbulleted_amount_list_is_split_too():
    out = rs.split_welded_copied_lines([TACO])
    assert len(out) == 6
    assert out[0] == "1/2 cup chili powder"


def test_ordinary_single_lines_are_untouched():
    lines = ["1 cup flour", "- 2 eggs", "Brine"]
    assert rs.split_welded_copied_lines(lines) == lines


def test_blank_lines_inside_a_blob_do_not_defeat_the_split():
    out = rs.split_welded_copied_lines(["- 1 cup flour\n\n- 2 eggs\n"])
    assert out == ["- 1 cup flour", "- 2 eggs"]


# --- the refusals, which are the point ------------------------------------


def test_a_method_paragraph_is_NOT_split():
    """The 2026-08-06 failure, in this new shape. Every one of these lines
    is real source text and would verify individually, so splitting would
    hand the gate a full-coverage block of method steps."""
    method = (
        "Preheat the oven to 350 degrees.\n"
        "Add butter and pulse until the mixture resembles coarse meal.\n"
        "Transfer to a 9 1/2 inch dish and press evenly.\n"
        "Bake until fragrant, 20-25 minutes."
    )
    assert rs.split_welded_copied_lines([method]) == [method]


def test_a_mostly_ingredient_blob_with_one_prose_line_is_NOT_split():
    """All-or-nothing on purpose. One line that is not a list item means
    this was never a clean list, and a partial split is how method text
    rides in on an ingredient block's coverage."""
    mixed = "- 1 cup flour\n- 2 eggs\nMix everything together until smooth."
    assert rs.split_welded_copied_lines([mixed]) == [mixed]


def test_a_heading_inside_a_blob_prevents_the_split():
    assert rs.split_welded_copied_lines(["Crust\n- 1 cup flour\n- 2 eggs"]) == ["Crust\n- 1 cup flour\n- 2 eggs"]


def test_a_single_line_blob_is_returned_untouched():
    """One line plus a trailing newline is not a welded list. The line is
    handed back BYTE-IDENTICAL rather than tidied: this function's only
    job is splitting, and a function that also quietly rewrites its input
    is one whose output nobody can reason about."""
    assert rs.split_welded_copied_lines(["1 cup flour\n"]) == ["1 cup flour\n"]


def test_empty_and_garbage():
    assert rs.split_welded_copied_lines([]) == []
    assert rs.split_welded_copied_lines(["\n\n"]) == ["\n\n"]


# --- end to end through the block policy ----------------------------------


def test_the_oatcakes_block_now_survives_the_coverage_gate():
    """Before the split this scored 0 of 1 and the gate dropped the whole
    block. It is the same gate and the same source; only the shape of
    what pass 1 handed it has changed."""
    source = OATCAKES.replace("- ", "")
    result = rs.ingredients_from_pass1_blocks([{"component": "main", "lines": [OATCAKES]}], source)

    assert result.blocks_dropped == 0, "the gate still dropped the block"
    assert result.lines_verified == 9
    assert len(result.ingredients) == 9
    names = [i["ingredient_name"] for i in result.ingredients]
    assert any("buttermilk" in n for n in names)
    # The bullet must still come off before the amount is read -- an
    # ingredient NAME containing a digit is the 2026-08-07 bullet bug.
    assert not any(any(ch.isdigit() for ch in n) for n in names), f"an amount leaked into a name: {names}"


def test_a_welded_method_block_is_still_dropped():
    """The other half. If this ever passes the gate, the split has become
    the thing it was written not to be."""
    method = "Preheat the oven to 350 degrees.\nBake until fragrant, 20-25 minutes."
    result = rs.ingredients_from_pass1_blocks([{"component": "main", "lines": [method]}], method)
    assert result.blocks_dropped == 1
    assert result.ingredients == []
