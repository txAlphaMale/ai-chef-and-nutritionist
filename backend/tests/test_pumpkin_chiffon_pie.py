"""The pie, offline, against the text production actually reads.

`pumpkin_chiffon_pie_pypdf.txt` was the only pie fixture, and pypdf has
not been the primary reader since 2026-08-06. The plan carried this as an
open item in exactly those words: the offline assertions exercised a shape
the app no longer sees. `pumpkin_chiffon_pie_pdfplumber.txt` closes it.

It is `extract_pdf_text`'s OUTPUT, not raw pdfplumber's -- so it includes
the un-weaving of the ad drawn over the last instruction. That distinction
is the whole reason the fixture could not be produced by
`scripts/dump_pdf_text.py`, which imports nothing from the app and
therefore cannot know about the repair (see that script's docstring).

The pypdf fixture stays. It is the regression case for a source with no
line structure, which is what find_welded_run exists for, and losing the
only example of that shape to a library swap would be a bad trade.

Why this recipe and no other: the rest of the corpus proves an import is
PLAUSIBLE. Only the pie proves one is RIGHT -- it is the recipe whose
answer is known line by line, including the compound `3/4 cup plus
2 Tbsp.` that must stay two entries and the crust sugar that must not
inherit the filling's amount.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app.services import recipe_service

PIE = Path(__file__).parent / "fixtures" / "pumpkin_chiffon_pie_pdfplumber.txt"

# (component, quantity, unit, name, prep_note) -- hand-checked against the
# printed page, not against what the code happens to return.
EXPECTED = [
    ("Crust", 12.0, None, "graham crackers", None),
    ("Crust", 2.0, "tbsp", "sugar", None),
    ("Crust", 0.25, "tsp", "kosher salt", None),
    ("Crust", 6.0, "tbsp", "unsalted butter", "melted, slightly cooled"),
    ("Filling and Assembly", 1.0, "envelope", "unflavored gelatin", "2½ tsp."),
    ("Filling and Assembly", 1.0, "tsp", "ground cinnamon", None),
    ("Filling and Assembly", 0.25, "tsp", "ground nutmeg", None),
    ("Filling and Assembly", 0.75, "cup", "sugar", "scant, divided"),
    ("Filling and Assembly", 2.0, "tbsp", "sugar", "scant, divided"),
    ("Filling and Assembly", 0.75, "tsp", "kosher salt", "divided"),
    ("Filling and Assembly", 3.0, "large", "egg yolks", None),
    ("Filling and Assembly", 0.75, "cup", "whole milk", None),
    ("Filling and Assembly", 1.25, "cup", "unsweetened pumpkin purée", "from one 15-oz. can"),
    ("Filling and Assembly", 3.0, "large", "egg whites", None),
    ("Filling and Assembly", 0.75, "cup", "heavy cream", None),
    ("Filling and Assembly", 0.25, "cup", "sour cream", None),
]


def _source() -> str:
    return PIE.read_text(encoding="utf-8")


def _ingredient_blocks() -> list[tuple[str, list[str]]]:
    """The ingredient list, read out of the fixture rather than pasted in,
    so the blocks pass 1 is stubbed with are the source's real lines."""
    lines = [line.strip() for line in _source().splitlines()]
    region = lines[lines.index("Crust") : lines.index("Recipe Preparation")]
    blocks: list[tuple[str, list[str]]] = []
    for line in region:
        if not line:
            continue
        if line in ("Crust", "Filling and Assembly"):
            blocks.append((line, []))
        elif blocks:
            blocks[-1][1].append(line)
    return blocks


@contextmanager
def _stub_pass1(blocks):
    raw = json.dumps({"blocks": [{"component": c, "lines": ls} for c, ls in blocks]})
    with (
        patch.object(recipe_service, "get_ingredient_lines_prompt", return_value="{content}"),
        patch.object(recipe_service.ollama_client, "content_char_budget", return_value=100_000),
        patch.object(recipe_service.ollama_client, "get_extraction_model", return_value="stub"),
        patch.object(recipe_service.ollama_client, "chat_json_with_reason", return_value=(raw, "stop")),
    ):
        yield


def test_the_fixture_is_the_repaired_shape_not_the_raw_one():
    """If this fails, the fixture was regenerated with raw pdfplumber and
    every assertion below is testing the wrong document."""
    source = _source()
    assert "Using a large spoon, dollop a generous amount" in source
    assert "lasrcgreip" not in source


def test_fifteen_source_lines_yield_the_sixteen_right_ingredients():
    blocks = _ingredient_blocks()
    assert [len(lines) for _, lines in blocks] == [4, 11], "fixture's ingredient region changed shape"

    with _stub_pass1(blocks):
        got = recipe_service.extract_ingredients_two_pass(None, _source())

    assert [(i["component"], i["quantity"], i["unit"], i["ingredient_name"], i["prep_note"]) for i in got] == EXPECTED


def test_the_compound_amount_stays_two_entries_and_is_never_summed():
    """`3/4 (scant) cup plus 2 Tbsp. sugar, divided` is one ingredient
    added at two points. Summing it to 1.75 cup loses the instruction;
    keeping only the first loses a third of the sugar."""
    with _stub_pass1(_ingredient_blocks()):
        got = recipe_service.extract_ingredients_two_pass(None, _source())

    filling_sugar = [i for i in got if i["ingredient_name"] == "sugar" and i["component"] != "Crust"]
    assert [(i["quantity"], i["unit"]) for i in filling_sugar] == [(0.75, "cup"), (2.0, "tbsp")]


def test_the_crust_sugar_does_not_inherit_the_fillings_amount():
    """The original bug that started multi-component support: the crust's
    `2 Tbsp. sugar` came through as `0.5 cup`, because nothing
    distinguished it from the filling's sugar."""
    with _stub_pass1(_ingredient_blocks()):
        got = recipe_service.extract_ingredients_two_pass(None, _source())

    crust_sugar = [i for i in got if i["ingredient_name"] == "sugar" and i["component"] == "Crust"]
    assert [(i["quantity"], i["unit"], i["prep_note"]) for i in crust_sugar] == [(2.0, "tbsp", None)]
