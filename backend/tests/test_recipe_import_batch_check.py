"""The batch import harness's one piece of real logic.

scripts/check_recipe_import_batch.py runs the live import over a folder
and reports which pass supplied each answer. Almost all of it is I/O and
formatting, but the "does this source even look like it has an ingredient
list" counter is a judgement call, and it is the thing that would flag the
failure mode two-pass has never been tested against.

That failure is SILENT by construction: verification matches a copied line
against the START of a source line, so a source whose ingredients are not
on their own lines rejects everything, two-pass returns nothing, and the
single-call ingredients stand with their null quantities. The counter is
the smoke alarm. It is deliberately never used to EXTRACT -- brittle
segmentation across PDF, HTML, photo and pasted text is exactly what
two-pass exists to avoid -- but a false alarm only costs a second look.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).parent.parent / "scripts" / "check_recipe_import_batch.py"
_spec = importlib.util.spec_from_file_location("check_recipe_import_batch", _SCRIPT)
batch = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(batch)

FIXTURE = Path(__file__).parent / "fixtures" / "pumpkin_chiffon_pie_pypdf.txt"


def test_the_real_pie_source_reads_as_line_per_ingredient():
    """15 ingredient lines plus a few prose lines that begin with a
    number. An over-count is fine; this is a floor check, not a parser."""
    count = batch.looks_like_ingredient_lines(FIXTURE.read_text(encoding="utf-8"))

    assert count >= 15, "the source two-pass was designed against must read as one ingredient per line"


def test_a_paragraph_style_source_raises_the_alarm():
    """The case two-pass has never been tested on, and the reason this
    harness exists. Ingredients run inline inside a sentence, so no line
    starts with an amount, nothing can match the start of a source line,
    and the import falls back silently."""
    source = "Paragraph Style Salad\n\nIngredients: 2 cups spinach, 1/4 cup walnuts, 3 Tbsp. olive oil. Toss.\n"

    assert batch.looks_like_ingredient_lines(source) == 0


def test_it_counts_unicode_and_slash_fractions_not_just_digits():
    source = "12 graham crackers\n¼ tsp. kosher salt\n1/2 cup milk\nPreheat the oven to 325.\n"

    # Three ingredient lines; the instruction sentence starts with a word.
    assert batch.looks_like_ingredient_lines(source) == 3
