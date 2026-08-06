"""An ingredient too long for the column, wrapped across source lines.

pdfplumber replaced pypdf as the primary reader on 2026-08-06 because
pypdf returns one line per LAYOUT BLOCK -- a 7,404-character "line" on the
kimchi -- while pdfplumber returns the lines the page actually shows. That
fixed real defects (`320gCaputo` -> `320g Caputo`, and the pizza's
`+ Chickpea flour` became its own line instead of being welded onto the
olive oil) and immediately created this one.

`brussel_sprout_kimchi_pdfplumber.txt`, lines 74-77:

    4 tablespoons Korean red pepper
    powder
    1 tablespoon Gsh sauce or shrimp sauce
    (I excluded this)

The model copies what a reader sees. The live run sent
`1 tablespoon Gsh sauce or shrimp sauce\\n(I excluded this)` as ONE line,
it matched no single source line, and it was dropped -- so the fish sauce
disappeared from the recipe entirely, and `Korean red pepper` was stored
without its `powder`. Deleting an ingredient is worse than the welded
name the swap was meant to fix.

`reconcile_copied_lines` now prefix-matches against joins of consecutive
source lines. `find_welded_run` handles the mirror case -- many candidates
inside one source line -- and the two are complementary.

The widened window is the risk this file exists to pin: more text to match
against is more text a hallucination could match against. The pie's six
measured hallucinations are re-checked here against the NEW matcher, not
just against the old one in test_recipe_components.py.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app.services import recipe_service

FIXTURES = Path(__file__).parent / "fixtures"
PIE = FIXTURES / "pumpkin_chiffon_pie_pypdf.txt"
KIMCHI_PLUMBER = FIXTURES / "brussel_sprout_kimchi_pdfplumber.txt"
PIZZA_PLUMBER = FIXTURES / "leopard_crust_pizza_pdfplumber.txt"

# Exactly what the live 9B sent for the kimchi, wraps and all, from the
# check_recipe_import.py run of 2026-08-06.
KIMCHI_COPIED = [
    "2 1/2 lbs brussel sprouts",
    "1 medium daikon radish",
    "1 1/2 tablespoons diced ginger",
    "1 tablespoon diced garlic",
    "4 tablespoons Korean red pepper powder",
    "1 tablespoon Gsh sauce or shrimp sauce\n(I excluded this)",
    "Brine",
    "2 tablespoons sea salt",
    "4 cups water",
]

# Method steps the same live run copied into the same response. They wrap
# across source lines exactly like the ingredients do, which is precisely
# why they are dangerous, and none of them may ever verify.
KIMCHI_METHOD = [
    "A. Rinse and gently clean the brussel\nsprouts, daikon, and ginger",
    "B. Slice the brussel sprouts in half\nlengthwise",
    'C. Cut the daikon into disks approx ⅛"\nthick. If your diakon is particularly\n'
    "fat then cut lengthwise in half or\nquarters Grst",
    "D. Dissolve the sea salt in the water to\nmake a brine",
    "H. In a large bowl, combine the garlic\nand ginger with the drained veggies\n"
    "and korean chili powder and toss\n(this is where you would also add\n"
    "gsh sauce or shrimp sauce if you\nwish)",
]

# Every one of these was produced by the live model across the four
# measured pie runs, and every one must stay rejected.
PIE_HALLUCINATIONS = [
    "2 Tbsp. graham cracker crumbs",
    "a scant ½ cup sugar",
    "sour cream, remaining 2 Tbsp. sugar, and ¼ tsp. salt just to combine",
    "graham cracker crumbs (reserved)",
    "1 cup sugar",
    "2 cups whipped cream",
]


@contextmanager
def _stub_pass1(blocks, done_reason="stop"):
    raw = json.dumps({"blocks": blocks})
    with (
        patch.object(recipe_service, "get_ingredient_lines_prompt", return_value="{content}"),
        patch.object(recipe_service.ollama_client, "content_char_budget", return_value=100_000),
        patch.object(recipe_service.ollama_client, "get_extraction_model", return_value="stub"),
        patch.object(recipe_service.ollama_client, "chat_json_with_reason", return_value=(raw, done_reason)),
    ):
        yield


def test_the_fixture_really_is_wrapped():
    """The premise. If the extractor ever stops splitting these, the join
    below is dead code and this test says so first."""
    lines = KIMCHI_PLUMBER.read_text(encoding="utf-8").split("\n")
    assert "4 tablespoons Korean red pepper" in lines
    assert "powder" in lines
    assert "1 tablespoon Gsh sauce or shrimp sauce" in lines
    assert "(I excluded this)" in lines
    # And no single line carries the whole ingredient.
    assert not any("Korean red pepper powder" in line for line in lines)


def test_a_line_wrapped_in_the_source_verifies():
    source = KIMCHI_PLUMBER.read_text(encoding="utf-8")
    accepted, rejected = recipe_service.reconcile_copied_lines(KIMCHI_COPIED, source)
    assert rejected == []
    assert "4 tablespoons Korean red pepper powder" in accepted
    assert "1 tablespoon Gsh sauce or shrimp sauce (I excluded this)" in accepted


def test_method_steps_never_reach_the_wider_window():
    """The regression this guard exists for, measured in production.

    Shipped without it, the kimchi imported 25 "ingredients" -- 17 of them
    method steps, filed under a component called `Instructions`. The
    method wraps across lines exactly like an ingredient does, so joining
    made it verifiable, and the per-block gate that had been dropping that
    block at 4 of 15 suddenly passed it.

    Hallucination was the risk that got checked before shipping, and the
    check was sound and irrelevant: the damage came from text the source
    really does contain. Only how a line STARTS separates the two, since
    `B. Slice the brussel sprouts in half lengthwise` is 46 chars and the
    wrapped fish sauce is 55."""
    source = KIMCHI_PLUMBER.read_text(encoding="utf-8")
    accepted, rejected = recipe_service.reconcile_copied_lines(KIMCHI_METHOD, source)
    assert accepted == []
    assert len(rejected) == len(KIMCHI_METHOD)


def test_the_wider_window_does_not_let_the_pie_hallucinations_in():
    """The cost of matching against joined lines, measured rather than
    assumed. All six must still be rejected."""
    source = PIE.read_text(encoding="utf-8")
    accepted, rejected = recipe_service.reconcile_copied_lines(PIE_HALLUCINATIONS, source)
    assert accepted == []
    assert len(rejected) == len(PIE_HALLUCINATIONS)


def test_short_candidates_are_matched_exactly_as_before():
    """A candidate shorter than its source line can never reach a joined
    window: the prefix of the join IS the prefix of the first line. This
    change is a superset of the old behaviour, not a replacement."""
    source = PIE.read_text(encoding="utf-8")
    accepted, _ = recipe_service.reconcile_copied_lines(["12 graham crackers", "¼ tsp. kosher salt"], source)
    assert accepted == ["12 graham crackers", "¼ tsp. kosher salt"]


def test_a_wrap_is_bounded_and_cannot_swallow_the_rest_of_the_page():
    """Joining is capped, or a long enough candidate would match an
    arbitrary run of unrelated text."""
    assert 1 < recipe_service._MAX_WRAP_LINES <= 4


def test_the_wrapped_kimchi_imports_completely_end_to_end():
    """The row the swap regressed: 8 ingredients, fish sauce present, and
    the pepper keeping its powder."""
    source = KIMCHI_PLUMBER.read_text(encoding="utf-8")
    with _stub_pass1([{"component": "Ingredients", "lines": KIMCHI_COPIED}]):
        result = recipe_service.extract_ingredients_two_pass(None, source)

    names = [i["ingredient_name"] for i in result]
    assert len(result) == 8, names
    assert any("Korean red pepper powder" in name for name in names), names
    assert any("sauce or shrimp sauce" in name for name in names), names
    by_name = {i["ingredient_name"]: i["component"] for i in result}
    assert by_name["sea salt"] == "Brine"
    assert by_name["water"] == "Brine"
