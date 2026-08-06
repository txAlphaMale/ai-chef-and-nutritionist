"""Pass 1 finishing its work and then not stopping.

Measured live on the Leopard Crust pizza, 2026-08-06, via
`scripts/probe_pass1_budget.py`:

    CAP 1200   done_reason='length'  eval_count=1200  content_chars=2910
    CAP 2400   done_reason='length'  eval_count=2400  content_chars=5816
    CAP 3600   done_reason='length'  eval_count=3600  content_chars=8739

    block 'main': 5 lines, 5 verified (welded), KEPT      <- x14
    66 lines returned, 5 distinct
    source offsets of verifiable lines: min 1759, max 1859

Three theories died there. The model does not fail to find the ingredient
list -- it copies all five lines correctly in its first ~110 tokens. It
does not wander into the baker's-percentage worked example at offset 9250
or the 30 reader comments past 10,900 -- every line it returned came from
between 1759 and 1859. And it is not short of room -- output length
tracked the cap at a constant 2.43 chars per token across three runs, so a
larger cap buys proportionally more repetition and no ending.

It emits the correct answer and then repeats the whole block thirteen more
times until something stops it.

Two changes answer that, and both are needed:

  * `ExtractedIngredientLines.blocks` is bounded (maxItems), so the
    grammar reaches a point where the closing bracket is the only legal
    token. An unbounded array never gives the decoder that.
  * `dedupe_blocks` collapses verbatim repeats, because a bounded loop is
    still a loop. Without it the same five ingredients would be stored
    once per repetition -- the bound would convert a visible failure into
    a silent one, which is the trade this project keeps refusing.

The five lines below are exactly what the live model returned, and they
sit in the checked-in fixture at exactly the offsets the probe reported.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app.schemas.ai_extraction import ExtractedIngredientLines, schema_of
from app.services import recipe_service

FIXTURES = Path(__file__).parent / "fixtures"
PIZZA = FIXTURES / "leopard_crust_pizza_pypdf.txt"

PIZZA_LOOPED = [
    "320gCaputo Fioreglut flour (100%)",
    "2gInstant yeast (0.6%)",
    "256gWater, room temperature (80%)",
    "10gSalt (3%)",
    "16gExtra-virgin olive oil (5%)+Chickpea flour or fine cornmeal",
]

# Observed repetition count: 14 copies of the block at a 1200-token cap.
LIVE_REPEATS = 14


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


def test_the_blocks_array_is_bounded_so_the_grammar_can_close_it():
    """The schema actually sent to Ollama, not the pydantic model: an
    unbounded array is why the decoder never had to stop."""
    schema = schema_of(ExtractedIngredientLines)
    blocks = schema["properties"]["blocks"]
    assert blocks["type"] == "array"
    assert "maxItems" in blocks, "an unbounded array gives the grammar no reason to close"
    # Above any real recipe, below llama.cpp's repetition threshold.
    assert 2 < blocks["maxItems"] <= 16


def test_a_verbatim_repeat_is_collapsed():
    block = {"component": "main", "lines": list(PIZZA_LOOPED)}
    assert len(recipe_service.dedupe_blocks([block] * LIVE_REPEATS)) == 1


def test_blocks_that_differ_at_all_are_both_kept():
    """Only an exact repeat is a loop. A real multi-part recipe must
    survive, including two sections that share a heading."""
    a = {"component": "Crust", "lines": ["12 graham crackers"]}
    b = {"component": "Filling", "lines": ["12 graham crackers"]}
    c = {"component": "Crust", "lines": ["12 graham crackers", "1 tsp. salt"]}
    assert len(recipe_service.dedupe_blocks([a, b, c])) == 3


def test_non_dict_entries_are_skipped_rather_than_crashing():
    block = {"component": "main", "lines": ["12 graham crackers"]}
    assert recipe_service.dedupe_blocks(["nonsense", None, block, block]) == [block]


def test_a_looping_response_stores_each_ingredient_once():
    """End to end on the real fixture: fourteen identical blocks must
    produce exactly what one block produces."""
    source = PIZZA.read_text(encoding="utf-8")
    block = {"component": "main", "lines": list(PIZZA_LOOPED)}

    with _stub_pass1([block]):
        once = recipe_service.extract_ingredients_two_pass(None, source)
    with _stub_pass1([block] * LIVE_REPEATS):
        looped = recipe_service.extract_ingredients_two_pass(None, source)

    # The premise: these lines verify against this source. If this fails,
    # the fixture and the measured live run have diverged.
    assert once, "the measured pizza lines no longer verify against the fixture"
    assert looped == once
    names = [i["ingredient_name"] for i in looped]
    assert len(names) == len(set(names)), f"an ingredient was stored more than once: {names}"
