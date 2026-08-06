"""Sources whose extractor gives no line structure, and the completeness gate.

Everything here is driven off REAL extractor output, checked in beside the
pie: `brussel_sprout_kimchi_pypdf.txt` and `leopard_crust_pizza_pypdf.txt`
are pypdf 5.0.1 over two browser print-to-PDF blog pages, produced the
same way the pie fixture was. No example below is invented.

The batch harness run that motivated this file (2026-08-04, four files):

    file                           ingr null_q no_comp src  p1 kept amounts
    Brussel Sprout Kimchi Fermenta    1      1       0   B  24    1       0
    GF Chicken Parm.txt              10     10       0   B  10   10       0
    Gluten-free pizza recipe- look    5      5       5   A   0    0       4
    Herb Slamon and Asparagus.json    8      1       8  LD   0    0       0

Two defects, in order of damage:

1. The kimchi row. Pass 1 copied 24 lines, ONE verified, and that one line
   replaced the entire single-call ingredient list -- `if two_pass:` gated
   on truthiness. A one-ingredient kimchi recipe, with nothing shown to
   the household. The plan claimed the failure mode was additive; it is
   additive only at exactly zero.

2. Why 23 were dropped. pypdf welded the whole list into one 240-char
   line, so prefix matching -- which requires a source line to START with
   the copied text -- scored 0.32-0.56 on lines the model had transcribed
   CORRECTLY. Same library and version as the pie, opposite shape, because
   one is a publisher PDF and these are printed web pages.

The pizza row is a third, independent failure (pass 1 hit its 1200-token
cap, `done_reason='length'`, truncated JSON, p1=0) and is covered by the
run test below only for the shape question, not the truncation.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app.services import recipe_service

FIXTURES = Path(__file__).parent / "fixtures"
PIE = FIXTURES / "pumpkin_chiffon_pie_pypdf.txt"
KIMCHI = FIXTURES / "brussel_sprout_kimchi_pypdf.txt"
PIZZA = FIXTURES / "leopard_crust_pizza_pypdf.txt"

# Exactly what the live 9B returned for the kimchi file, from the
# ollama_client log of the 2026-08-04 batch run.
KIMCHI_COPIED = [
    "2 1/2 lbs brussel sprouts",
    "1 medium daikon radish",
    "1 1/2 tablespoons diced ginger",
    "1 tablespoon diced garlic",
    "4 tablespoons Korean red pepper powder",
    "1 tablespoon fish sauce or shrimp sauce",
    "2 tablespoons sea salt",
    "4 cups water",
]

PIZZA_COPIED = [
    "320gCaputo Fioreglut flour (100%)",
    "2gInstant yeast (0.6%)",
    "256gWater, room temperature (80%)",
    "10gSalt (3%)",
    "16gExtra-virgin olive oil (5%)",
    "Chickpea flour or fine cornmeal",
]

# Every one of these was produced by the live model across the four
# measured pie runs. Reused from test_recipe_components.py deliberately:
# the welded fallback must not re-open the hole prefix matching closed.
PIE_HALLUCINATIONS = [
    "2 Tbsp. graham cracker crumbs",
    "a scant ½ cup sugar",
    "sour cream, remaining 2 Tbsp. sugar, and ¼ tsp. salt just to combine",
    "graham cracker crumbs (reserved)",
    "1 cup sugar",
    "2 cups whipped cream",
]


def test_the_kimchi_fixture_really_is_welded():
    """The premise. If pypdf ever starts splitting these, the fallback
    below is dead code and this test says so first."""
    source = KIMCHI.read_text(encoding="utf-8")
    lines = [line for line in source.splitlines() if line.strip()]
    assert len(lines) < 40, "17k chars in under 40 lines is the welded shape"
    assert max(len(line) for line in lines) > 5000

    welded = next(line for line in lines if "brussel sprouts" in line and "daikon" in line)
    # Both components and all eight ingredients, on one line, no separators.
    assert welded.startswith("Ingredients")
    assert "sprouts1 medium daikon" in welded
    assert "Brine2 tablespoons sea salt" in welded


def test_prefix_matching_rejects_correct_transcriptions_of_a_welded_source():
    """The measured failure, pinned so the fallback cannot be quietly
    removed as unnecessary. These eight lines are CORRECT."""
    source = KIMCHI.read_text(encoding="utf-8")
    accepted, rejected = recipe_service.reconcile_copied_lines(KIMCHI_COPIED, source)
    assert accepted == []
    assert rejected == KIMCHI_COPIED


def test_welded_run_recovers_the_whole_kimchi_list():
    source = KIMCHI.read_text(encoding="utf-8")
    recovered = recipe_service.find_welded_run(KIMCHI_COPIED, source)
    assert len(recovered) == 8
    assert recovered[0] == "2 1/2 lbs brussel sprouts"
    assert recovered[-1] == "4 cups water"


def test_welded_run_recovers_the_pizza_list_including_its_welded_units():
    """`320gCaputo Fioreglut flour` -- pypdf welds the unit onto the name
    here too, and the recovered text must be the source's, not tidied."""
    recovered = recipe_service.find_welded_run(PIZZA_COPIED, PIZZA.read_text(encoding="utf-8"))
    assert len(recovered) == 6
    assert recovered[0] == "320gCaputo Fioreglut flour (100%)"


def test_recovered_text_is_always_the_sources_own():
    """The safety property that makes any of this repairable: nothing
    returned can contain a character the source does not have. The model
    sent `fish sauce`; pypdf's ligature damage means the source says
    `Gsh sauce`, and the SOURCE wins."""
    source = KIMCHI.read_text(encoding="utf-8")
    normalized = recipe_service._normalize_for_match(source)
    for line in recipe_service.find_welded_run(KIMCHI_COPIED, source):
        assert line in normalized

    recovered = recipe_service.find_welded_run(KIMCHI_COPIED, source)
    assert any("Gsh sauce" in line for line in recovered), "source text, not the model's correction"
    assert not any("fish sauce" in line for line in recovered)


def test_alignment_trimming_does_not_steal_the_next_items_digits():
    """A fixed-length window took the next ingredient's leading `1` onto
    the end of the pepper line and lost the fish sauce's own. That is a
    corrupted quantity on two rows, not a cosmetic edge."""
    recovered = recipe_service.find_welded_run(KIMCHI_COPIED, KIMCHI.read_text(encoding="utf-8"))
    pepper = next(line for line in recovered if "pepper" in line)
    assert pepper == "4 tablespoons Korean red pepperpowder"
    assert not pepper.endswith("1")

    sauce = next(line for line in recovered if "sauce" in line)
    assert sauce.startswith("1 tablespoon")


def test_the_welded_fallback_rejects_every_known_pie_hallucination():
    """The guard is STRUCTURAL, and it has to be.

    `2 Tbsp. graham cracker crumbs` is genuinely inside `Set aside 2 Tbsp.
    graham cracker crumbs for serving` and scores 1.000 as a substring, so
    no similarity threshold can reject it. It is rejected because it does
    not sit in a tightly packed run with other matches -- one match is not
    a list."""
    assert recipe_service.find_welded_run(PIE_HALLUCINATIONS, PIE.read_text(encoding="utf-8")) == []


def test_prefix_wins_on_a_line_structured_source_so_the_pie_is_untouched():
    """The fallback is reached only where prefix has already failed."""
    source = PIE.read_text(encoding="utf-8")
    real = ["12 graham crackers", "¼ tsp. kosher salt", "¼ cup sour cream"]
    accepted, rejected, strategy = recipe_service.reconcile_block(real, source)
    assert strategy == "prefix"
    assert rejected == []
    assert len(accepted) == 3

    _, _, strategy = recipe_service.reconcile_block(PIE_HALLUCINATIONS, source)
    assert strategy == "prefix", "a block of pure phantoms must not fall through to the welded walk"


def test_reconcile_block_picks_the_welded_strategy_only_where_it_is_needed():
    accepted, rejected, strategy = recipe_service.reconcile_block(KIMCHI_COPIED, KIMCHI.read_text(encoding="utf-8"))
    assert strategy == "welded"
    assert len(accepted) == 8
    assert rejected == []


def test_a_partial_verification_is_refused_rather_than_returned():
    """The kimchi disaster, as a unit.

    1 of 24 lines verified. The old code returned that one ingredient and
    the caller replaced a full recipe with it. The gate's job is to make
    this indistinguishable from 'pass 1 found nothing', which the caller
    already handles correctly."""
    source = PIE.read_text(encoding="utf-8")
    mostly_junk = PIE_HALLUCINATIONS * 4 + ["12 graham crackers"]
    accepted, _, _ = recipe_service.reconcile_block(mostly_junk, source)
    coverage = len(accepted) / len(mostly_junk)
    assert coverage < recipe_service._TWO_PASS_MIN_COVERAGE, "this block must land on the declining side of the gate"


def test_the_coverage_threshold_sits_between_the_measured_disaster_and_every_success():
    """Not a round number someone liked. Measured coverages:
    kimchi-as-shipped 1/24 = 0.04; pie 15/15, chicken parm 10/10,
    kimchi-repaired 8/8, pizza 6/6 = 1.00."""
    assert 0.04 < recipe_service._TWO_PASS_MIN_COVERAGE < 1.0
    assert recipe_service._TWO_PASS_MIN_COVERAGE > 0.5


@contextmanager
def _stub_pass1(lines_by_component, done_reason="stop"):
    """Pass 1 stubbed at the model boundary, with the DB-backed prompt
    getter and the context budget stubbed too -- extract_ingredients_two_pass
    reads both before it ever calls Ollama."""
    blocks = [{"component": c, "lines": ls} for c, ls in lines_by_component]
    raw = json.dumps({"blocks": blocks})
    with (
        patch.object(recipe_service, "get_ingredient_lines_prompt", return_value="{content}"),
        patch.object(recipe_service.ollama_client, "content_char_budget", return_value=100_000),
        patch.object(recipe_service.ollama_client, "get_extraction_model", return_value="stub"),
        patch.object(recipe_service.ollama_client, "chat_json_with_reason", return_value=(raw, done_reason)),
    ):
        yield


def test_two_pass_declines_rather_than_returning_the_one_line_it_salvaged():
    """The kimchi disaster end to end, with the model stubbed.

    25 copied lines, one of which is real. Before the gate this returned
    that single ingredient and finish_recipe_parse installed it as the
    whole recipe."""
    source = PIE.read_text(encoding="utf-8")
    with _stub_pass1([("main", PIE_HALLUCINATIONS * 4 + ["12 graham crackers"])]):
        assert recipe_service.extract_ingredients_two_pass(None, source) == []


def test_two_pass_still_supplies_a_fully_verified_block():
    """The gate must not break the case two-pass exists for."""
    source = PIE.read_text(encoding="utf-8")
    with _stub_pass1([("Crust", ["12 graham crackers", "\u00bc tsp. kosher salt"])]):
        result = recipe_service.extract_ingredients_two_pass(None, source)
    assert [i["ingredient_name"] for i in result] == ["graham crackers", "kosher salt"]
    assert [i["component"] for i in result] == ["Crust", "Crust"]


def test_two_pass_recovers_a_welded_source_end_to_end():
    """Kimchi, through to parsed amounts -- the row that imported as a
    single ingredient."""
    source = KIMCHI.read_text(encoding="utf-8")
    with _stub_pass1([("main", KIMCHI_COPIED)]):
        result = recipe_service.extract_ingredients_two_pass(None, source)
    assert len(result) == 8
    assert [i["quantity"] for i in result] == [2.5, 1.0, 1.5, 1.0, 4.0, 1.0, 2.0, 4.0]
    assert result[0]["ingredient_name"] == "brussel sprouts"


def test_a_truncated_pass_1_declines_instead_of_salvaging_a_fragment():
    """The pizza row: done_reason='length' at the token cap. The JSON that
    comes back can be well formed and still be missing most of its array,
    so the text alone cannot reveal this."""
    source = KIMCHI.read_text(encoding="utf-8")
    with _stub_pass1([("main", KIMCHI_COPIED)], done_reason="length"):
        assert recipe_service.extract_ingredients_two_pass(None, source) == []


def test_a_verified_block_survives_a_junk_block_beside_it():
    """The gate's own regression, measured one run apart on the same file.

    A global coverage gate scored 10 verified of 24 copied = 0.417 and
    refused EVERYTHING, including a block that had verified completely.
    The model copying a block this app then rejects is not a reason to
    discard a section that checked out."""
    source = PIE.read_text(encoding="utf-8")
    with _stub_pass1(
        [
            ("Crust", ["12 graham crackers", "¼ tsp. kosher salt"]),
            ("main", PIE_HALLUCINATIONS * 3),
        ]
    ):
        result = recipe_service.extract_ingredients_two_pass(None, source)

    assert [i["ingredient_name"] for i in result] == ["graham crackers", "kosher salt"]
    assert {i["component"] for i in result} == {"Crust"}


def test_a_block_is_dropped_whole_rather_than_partially_kept():
    """Half a section is not a section. The lines that DO verify in a
    mostly-rejected block are the ones most likely to be method text that
    happens to appear in the source."""
    source = PIE.read_text(encoding="utf-8")
    with _stub_pass1([("main", PIE_HALLUCINATIONS * 3 + ["12 graham crackers"])]):
        assert recipe_service.extract_ingredients_two_pass(None, source) == []


def test_a_heading_welded_into_the_run_becomes_a_component_not_an_ingredient():
    """The kimchi source reads `...shrimp sauce(I excluded this)Brine2
    tablespoons sea salt...`, so `Brine` is real source text, copies
    correctly and verifies correctly -- and then became a ninth
    "ingredient" with no quantity, on the app's join key.

    Both `Brine` and `(I excluded this)` reproduce the observed live
    signature (9 kept lines, 9 ingredients, one null with no digits), so
    the fix has to handle either without knowing which occurred."""
    source = KIMCHI.read_text(encoding="utf-8")
    for stray in ("Brine", "(I excluded this)"):
        lines = [*KIMCHI_COPIED[:6], stray, *KIMCHI_COPIED[6:]]
        with _stub_pass1([("Ingredients", lines)]):
            result = recipe_service.extract_ingredients_two_pass(None, source)
        assert len(result) == 8, stray
        assert all(i["quantity"] is not None for i in result), stray
        assert not any(stray.strip("()") in i["ingredient_name"] for i in result), stray


def test_a_promoted_heading_carries_its_component_to_what_follows():
    """Dropping the row would be enough to stop the junk ingredient, but
    the source MEANT something by writing `Brine`: the salt and the water
    belong to it. This is the multi-component feature working on a source
    that has no line structure at all."""
    source = KIMCHI.read_text(encoding="utf-8")
    lines = [*KIMCHI_COPIED[:6], "Brine", *KIMCHI_COPIED[6:]]
    with _stub_pass1([("Ingredients", lines)]):
        result = recipe_service.extract_ingredients_two_pass(None, source)

    by_name = {i["ingredient_name"]: i["component"] for i in result}
    assert by_name["sea salt"] == "Brine"
    assert by_name["water"] == "Brine"
    # NOT "Ingredients". That heading announces the list rather than naming
    # a part, so normalize_component reads it as unsectioned -- which is
    # the honest description of this recipe: one named part, Brine, and a
    # main body. See test_recipe_components.py for the general rule.
    assert by_name["brussel sprouts"] is None


def test_a_source_that_states_no_amounts_is_left_completely_alone():
    """`GF Chicken Parm.txt` lists `Ground flaxseed`, `Kosher salt`,
    `Dried oregano` and means every one of them. "Has no amount" cannot
    be the heading test on its own, and this is the file that proves it --
    10 of 10 verified, and all 10 must survive."""
    entries = [
        {"ingredient_name": name, "quantity": None, "unit": None, "prep_note": None}
        for name in ("Ground flaxseed", "Kosher salt", "Dried oregano")
    ]
    kept = recipe_service._split_headings_from_ingredients(entries, "INGREDIENTS YOU'LL NEED:")
    assert len(kept) == 3
    assert [i["ingredient_name"] for i in kept] == ["Ground flaxseed", "Kosher salt", "Dried oregano"]
