"""The method's numbers, checked against the source.

Instructions were the unchecked half of every import: ingredients get
copied and verified, the method is prose the model writes. Measured on the
pie, 2026-08-06 -- the source stirs gelatin, spices, sugar and salt in a
saucepan OFF heat, and the import produced `in a small saucepan over
medium heat until dissolved`, then repeated the real cooking step two
entries later. Followed literally that heats dry sugar in an empty pan.

This does NOT catch that. Rule 8 requires paraphrase, so a step legitimately
is not in the source as text and the copy-then-check trick cannot be
reused. What it catches is the class that is not prose -- a temperature or
a duration the source never gives -- which is the class that burns dinner
and the class that is arithmetic to check.
"""

from pathlib import Path

from app.services.recipe_service import unverified_instruction_facts

PIE = (Path(__file__).parent / "fixtures" / "pumpkin_chiffon_pie_pdfplumber.txt").read_text(encoding="utf-8")

# Verbatim from the 2026-08-06 import that produced them.
REAL_STEPS = [
    {
        "component": "Crust",
        "text": "Preheat oven to 325°. Pulse graham crackers in a food processor until broken down into fine crumbs (you should have about 2 cups).",
    },
    {"component": "Crust", "text": "Bake crust until fragrant and edges just start to take on color, 20–25 minutes."},
    {
        "component": "Filling and Assembly",
        "text": "Cook over medium heat, stirring frequently, until mixture begins to thicken, about 5 minutes.",
    },
    {"component": "Filling and Assembly", "text": "Transfer to a large bowl and chill until cool, about 10 minutes."},
    {"component": "Filling and Assembly", "text": "Continue to beat until stiff peaks form, 5–7 minutes."},
]


def test_a_faithful_import_raises_nothing():
    """The normal case, and the one worth keeping quiet. Every figure in
    these five real steps is stated on the page."""
    assert unverified_instruction_facts(REAL_STEPS, PIE) == []


def test_an_invented_oven_temperature_is_reported():
    steps = [{"component": None, "text": "Preheat oven to 350° and bake the crust."}]
    findings = unverified_instruction_facts(steps, PIE)
    assert findings == ["Step 1 says 350°, which the source never states."]


def test_an_invented_duration_is_reported():
    steps = [{"component": None, "text": "Bake crust until fragrant, 45 minutes."}]
    assert unverified_instruction_facts(steps, PIE) == ["Step 1 says 45 minutes, which the source never states."]


def test_narrowing_a_stated_range_is_not_a_finding():
    """The source says 20-25 minutes. A step saying either endpoint is
    reading the page, not inventing. A false alarm on the review screen
    costs more than a missed one, so ranges match on both ends."""
    for text in ("Bake 20 minutes.", "Bake 25 minutes.", "Bake 20–25 minutes.", "Bake 20-25 minutes."):
        assert unverified_instruction_facts([{"component": None, "text": text}], PIE) == [], text


def test_the_step_number_points_at_the_step():
    steps = [
        {"component": None, "text": "Preheat oven to 325°."},
        {"component": None, "text": "Rest 3 hours."},
    ]
    assert unverified_instruction_facts(steps, PIE) == ["Step 2 says 3 hours, which the source never states."]


def test_bare_strings_are_accepted_like_everywhere_else():
    """Older recipes and the JSON-LD path store plain strings. This reads
    both shapes because normalize_instructions does."""
    assert unverified_instruction_facts(["Preheat oven to 500°."], PIE) == [
        "Step 1 says 500°, which the source never states."
    ]


def test_no_source_means_no_claims():
    """A photo import has no text layer. Silence is the honest answer, not
    a clean bill of health invented from nothing."""
    assert unverified_instruction_facts(REAL_STEPS, "") == []
