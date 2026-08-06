"""Multi-component recipes: a dish built from named parts.

The failure these tests exist to prevent is the Pumpkin Chiffon Pie
import, whose real pypdf output is checked in at
tests/fixtures/pumpkin_chiffon_pie_pypdf.txt. Three things went wrong and
each has a test here:

  - the crust's "2 Tbsp. sugar" came back as 0.5 cup, taken from a
    sentence in the crust's method rather than from the ingredient line;
  - the filling's "3/4 cup plus 2 Tbsp." collapsed into a single
    1.75 cup entry;
  - a "graham cracker crumbs, 2 Tbsp" ingredient appeared that is in
    NEITHER ingredient list -- it exists only in the preparation text.

The first and third are prompt behaviour and can only be verified against
a live model (see test_fixture_shape_the_prompt_must_survive for the
structural facts about the source that the prompt has to handle). What is
tested here without a model is everything downstream: that a component
survives a round trip, and that quantity math aggregates ACROSS
components rather than grouping by them.
"""

from pathlib import Path

import pytest

from app.models.recipe import Recipe, RecipeIngredient
from app.routers.recipes import _apply_ingredients
from app.schemas.ai_extraction import (
    COMPONENT_UNSECTIONED,
    ExtractedIngredient,
    ExtractedRecipe,
    schema_of,
)
from app.schemas.recipe import RecipeIngredientBase
from app.services import meal_plan_service, recipe_service

FIXTURE = Path(__file__).parent / "fixtures" / "pumpkin_chiffon_pie_pypdf.txt"


def test_component_round_trips_through_create(db_session):
    parsed = {
        "title": "Pumpkin Chiffon Pie",
        "ingredients": [
            {"ingredient_name": "sugar", "quantity": 2, "unit": "Tbsp.", "component": "Crust"},
            {
                "ingredient_name": "sugar",
                "quantity": 0.75,
                "unit": "cup",
                "component": "Filling and Assembly",
                "prep_note": "scant, divided",
            },
        ],
    }
    recipe = recipe_service.create_recipe_from_parsed(db_session, parsed, source="import_file")
    db_session.flush()

    got = {(i.component, i.quantity, i.unit) for i in recipe.ingredients}
    assert got == {
        ("Crust", 2.0, "Tbsp."),
        ("Filling and Assembly", 0.75, "cup"),
    }


def test_component_defaults_to_none(db_session):
    """A single-component recipe has no component, and nothing invents one.

    None is the honest value for every recipe imported before the column
    existed; the migration deliberately backfills nothing.
    """
    parsed = {
        "title": "Toast",
        "ingredients": [{"ingredient_name": "bread", "quantity": 2, "unit": "slices"}],
    }
    recipe = recipe_service.create_recipe_from_parsed(db_session, parsed, source="manual")
    db_session.flush()
    assert [i.component for i in recipe.ingredients] == [None]


def test_the_extraction_schema_leaves_the_model_no_way_to_decline(db_session):
    """`component` must be required AND non-nullable in the grammar.

    Both weaker forms were tried against the live 9B and both produced a
    component on zero ingredients: `default=None` kept the field out of
    `required` entirely, and `str | None` kept it in `required` but let
    `null` satisfy it. This asserts the grammar handed to Ollama, which is
    the only thing the model is actually constrained by.
    """
    schema = schema_of(ExtractedRecipe)
    ingredient = schema["properties"]["ingredients"]["items"]

    component = ingredient["properties"]["component"]
    assert "component" in ingredient["required"]
    assert component.get("type") == "string", "a nullable component is an escape hatch the 9B takes on every row"
    # A nullable field reaches the grammar as anyOf[string, null], which
    # is exactly the shape that let the model answer null on every row.
    assert "anyOf" not in component

    # Contrast: the fields that genuinely may have no answer keep their
    # null branch. This is a targeted exception, not a change of rule.
    assert "anyOf" in ingredient["properties"]["quantity"]


def test_the_prompt_defines_the_field_the_schema_forces(db_session):
    """A field the grammar compels and the prompt never mentions gets
    filled with something invented. These two drift apart silently, so
    the link is pinned rather than trusted."""
    prompt = recipe_service.RECIPE_IMPORT_PROMPT

    assert '"component"' in prompt
    assert COMPONENT_UNSECTIONED in prompt


def test_rule_1_is_the_configuration_that_actually_measured_best():
    """Rule 1 is back to forbidding conversion, deliberately, and this
    test exists to stop someone "fixing" it again on reasoning alone.

    The reasoning for changing it was sound and the outcome was worse.
    Rule 1 tells the model to copy the quantity EXACTLY and to leave it
    null rather than invent one, while the grammar types quantity as a
    number -- and eight of the fifteen amounts in the real pie source are
    Unicode vulgar fractions a JSON number cannot hold. That contradiction
    is real. Permitting the decimal form fixed it and cost the extraction:

        rule 1 forbids conversion, 380 chars -> 16 of 16 ingredients, null quantities
        rule 1 permits conversion, 600 chars ->  5 of 16 ingredients, one real quantity
        rule 1 permits conversion, 247 chars ->  4 of 16 ingredients, null quantities

    All four runs at temperature 0 with deterministic sampling, so these
    are measurements. Length was the obvious explanation and it is wrong:
    3654 chars returned 16 and 3671 chars returned 4. What tracks is
    whether rule 1 asks for per-row conversion work, and `eval_count` fell
    every time it did (1126 -> 842 -> 773) -- the model generated less and
    stopped earlier.

    Caveat kept honest: the EXAMPLE note changed alongside rule 1 in both
    permissive runs, so those two are confounded and neither was isolated.

    The conclusion is not "this wording is right". It is that components
    and quantities are mutually exclusive on this model at this prompt
    size, which is a capacity limit and not a wording problem. This is the
    better half of a bad trade, held only until two-pass extraction lands.
    """
    prompt = recipe_service.RECIPE_IMPORT_PROMPT
    rule_1 = next(line for line in prompt.splitlines() if line.startswith("1. "))

    assert "EXACTLY as written" in rule_1
    assert "leave both null rather than invent one" in rule_1
    # Unit fidelity is the part of rule 1 that was never in question.
    assert "never convert between units" in rule_1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Crust", "Crust"),
        ("  Filling and Assembly  ", "Filling and Assembly"),
        ("main", None),
        ("Main", None),
        ("MAIN", None),
        ("  main  ", None),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_the_unsectioned_sentinel_never_reaches_the_database(raw, expected):
    """ "main" exists only to deny the model a null. NULL stays the
    database's word for "no named parts", which is also what every row
    imported before the column existed says."""
    assert recipe_service.normalize_component(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Measured, not imagined: this heading was stored as the component
        # on all ten ingredients of a single-component recipe, and the
        # batch harness reported no_comp=0 and called it clean.
        ("INGREDIENTS YOU'LL NEED:", None),
        ("INGREDIENTS YOU’LL NEED:", None),
        ("Ingredients", None),
        ("ingredients:", None),
        ("Ingredient List", None),
        ("What You Need", None),
        ("For the ingredients", None),
        (":", None),
        # Real parts are untouched, including the ones a cleverer
        # normalisation would mangle.
        ("Brine", "Brine"),
        ("Crust", "Crust"),
        ("Pico de Gallo", "Pico de Gallo"),
        ("Filling and Assembly", "Filling and Assembly"),
        # The same part written two ways lands on one label, because
        # component is how a reader tells two sections apart and how
        # anything downstream compares them across recipes.
        ("For the Crust", "Crust"),
        ("For the Filling:", "Filling"),
        ("For Serving", "Serving"),
        # "for" is only a prefix when a word follows it.
        ("Formaggio", "Formaggio"),
        ("Forcemeat", "Forcemeat"),
    ],
)
def test_a_heading_that_announces_the_list_is_not_a_part_of_the_dish(raw, expected):
    """`Brine`, `Crust` and `Filling` name a part. `Ingredients` and
    `INGREDIENTS YOU'LL NEED:` announce the list, and storing them puts
    noise on the column that exists to distinguish sections -- worse than
    the NULL an unsectioned recipe honestly deserves."""
    assert recipe_service.normalize_component(raw) == expected


def test_the_sentinel_is_stripped_on_the_extraction_path(db_session):
    parsed = {
        "title": "Toast",
        "ingredients": [{"ingredient_name": "bread", "quantity": 2, "component": COMPONENT_UNSECTIONED}],
    }
    recipe = recipe_service.create_recipe_from_parsed(db_session, parsed, source="import_file")
    db_session.flush()

    assert [i.component for i in recipe.ingredients] == [None]


def test_coercion_carries_component_through():
    """The drop that made the first live measurement worthless.

    coerce_recipe_fields rebuilds every ingredient as an allowlist of
    named keys, and it had never been taught `component`. So the grammar
    forced the model to emit one, the model did, and this function threw
    all sixteen away before anything downstream saw them -- and the run
    was reported as "rule 3 produced nothing", blaming the prompt for a
    bug three layers below it.
    """
    coerced = recipe_service.coerce_recipe_fields(
        {
            "title": "Pumpkin Chiffon Pie",
            "ingredients": [
                {"ingredient_name": "sugar", "quantity": 2, "unit": "Tbsp.", "component": "Crust"},
                {"ingredient_name": "sugar", "quantity": 0.75, "unit": "cup", "component": "Filling and Assembly"},
            ],
        }
    )

    assert [i["component"] for i in coerced["ingredients"]] == ["Crust", "Filling and Assembly"]


def test_coercion_applies_the_unsectioned_sentinel():
    coerced = recipe_service.coerce_recipe_fields(
        {
            "title": "Toast",
            "ingredients": [{"ingredient_name": "bread", "component": COMPONENT_UNSECTIONED}],
        }
    )

    assert coerced["ingredients"][0]["component"] is None


def test_every_extraction_field_survives_coercion():
    """Generalizes the two tests above: any field the grammar can emit
    must be represented downstream, or the model is being compelled to
    produce something this app immediately discards. Driven off the schema
    so adding a field to ExtractedIngredient without teaching the
    allowlist about it fails here rather than in a live run.
    """
    coerced = recipe_service.coerce_recipe_fields(
        {
            "title": "Pie",
            "ingredients": [
                {
                    "ingredient_name": "sugar",
                    "quantity": 2,
                    "unit": "Tbsp.",
                    "prep_note": "divided",
                    "component": "Crust",
                }
            ],
        }
    )

    missing = set(ExtractedIngredient.model_fields) - set(coerced["ingredients"][0])
    assert not missing, f"coerce_recipe_fields silently drops {sorted(missing)}"


def test_the_api_create_path_persists_component(db_session):
    """The regression this catches: RecipeIngredientBase accepted
    `component` from the day the column landed, but _apply_ingredients
    never wrote it -- so every create and update through the API dropped
    it, including the import preview->confirm path, which is exactly where
    a multi-part recipe arrives.
    """
    recipe = Recipe(title="Pumpkin Chiffon Pie", default_servings=8)
    db_session.add(recipe)
    db_session.flush()

    _apply_ingredients(
        db_session,
        recipe,
        [
            RecipeIngredientBase(ingredient_name="sugar", quantity=2, unit="Tbsp.", component="Crust"),
            RecipeIngredientBase(ingredient_name="sugar", quantity=0.75, unit="cup", component="Filling"),
            RecipeIngredientBase(ingredient_name="salt", quantity=1, unit="tsp.", component=COMPONENT_UNSECTIONED),
        ],
    )
    db_session.flush()

    assert {(i.ingredient_name, i.component) for i in recipe.ingredients} == {
        ("sugar", "Crust"),
        ("sugar", "Filling"),
        ("salt", None),
    }


def test_grocery_merges_across_components():
    """The load-bearing one.

    2 Tbsp. of sugar in the crust and 2 Tbsp. in the filling are two real
    uses of one pantry item -- the household buys sugar once. So
    aggregation must ignore `component` entirely.

    This currently passes for a structural reason rather than a deliberate
    one: aggregate_ingredients rebuilds each dict with only
    ingredient_name/quantity/unit, so `component` is dropped on the way
    in. That makes it exactly the kind of correctness a later refactor
    could destroy while looking like an improvement -- preserving extra
    keys through the merge would silently split one shopping line in two.
    Hence a test rather than a comment.
    """
    merged = meal_plan_service.aggregate_ingredients(
        [
            [
                {"ingredient_name": "sugar", "quantity": 2, "unit": "Tbsp.", "component": "Crust"},
                {
                    "ingredient_name": "sugar",
                    "quantity": 2,
                    "unit": "Tbsp.",
                    "component": "Filling and Assembly",
                },
            ]
        ]
    )

    sugar = [m for m in merged if m["ingredient_name"] == "sugar"]
    assert len(sugar) == 1, f"component split one shopping line into {len(sugar)}"
    assert sugar[0]["quantity"] == 4
    assert sugar[0]["unit"] == "Tbsp."
    assert "component" not in sugar[0]


def test_scaling_preserves_component():
    """Scaling touches quantity only; the component label rides through."""
    scaled = recipe_service.scale_ingredients(
        [{"ingredient_name": "sugar", "quantity": 2, "unit": "Tbsp.", "component": "Crust"}],
        from_servings=2,
        to_servings=4,
    )
    assert scaled[0]["quantity"] == 4
    assert scaled[0]["component"] == "Crust"


def test_fixture_shape_the_prompt_must_survive():
    """Pins the properties of the real source that made this hard.

    Not a test of our code -- a test that the fixture still exhibits the
    conditions the import prompt was rewritten against. If a future pypdf
    bump reorders the extraction, the prompt's rule 1 ("position does not
    matter, line shape does") may be solving a problem that no longer
    exists in this form, and whoever sees this fail should re-read the
    rule before deleting the test.
    """
    text = FIXTURE.read_text(encoding="utf-8")

    # The method text arrives BEFORE the ingredient list. This is why a
    # model reading linearly binds "crust sugar" to the method's
    # "a scant 1/2 cup sugar" before it ever reaches "2 Tbsp. sugar".
    assert text.index("12 graham crackers") > text.index("Set aside 2 Tbsp")

    # Section headings appear twice -- once over prep, once over
    # ingredients -- so a heading name alone cannot identify the list.
    assert text.count("Filling and Assembly") >= 2

    # "scant" is genuinely on the ingredient line, so recording it as a
    # prep_note is correct provenance, not leakage from the method.
    assert "(scant) cup plus 2 Tbsp. sugar" in text

    # The crumbs quantity exists ONLY in prose. Any ingredient row for it
    # is fabricated.
    assert "2 Tbsp. graham cracker crumbs" in text
    assert "graham cracker crumbs\n" not in text


# --- Two-pass ingredient extraction -----------------------------------
#
# The single-call extraction was measured four times against the live 9B
# and never got all four checks. Each requirement it won cost one it had
# already met: components arrived and every quantity went null, quantities
# arrived and eleven of sixteen ingredients vanished. Two-pass splits the
# job at the seam -- the model LOCATES and COPIES, Python READS -- so the
# tests below can prove the reading half without a model at all.


def _pie_ingredient_lines() -> dict[str, list[str]]:
    """The pie's real ingredient blocks, exactly as they appear in the
    checked-in pypdf output. This is what pass 1 is asked to return."""
    text = FIXTURE.read_text(encoding="utf-8")
    block = text.split("overmix.Crust")[1].split("C o m p l e t e")[0]
    blocks: dict[str, list[str]] = {"Crust": []}
    current = "Crust"
    for raw in block.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "Filling and Assembly":
            current = line
            blocks[current] = []
            continue
        blocks[current].append(line)
    return blocks


def test_pass_1_lines_are_the_real_source_lines():
    blocks = _pie_ingredient_lines()
    assert list(blocks) == ["Crust", "Filling and Assembly"]
    assert len(blocks["Crust"]) == 4
    assert len(blocks["Filling and Assembly"]) == 11


def test_verification_accepts_every_real_line_and_rejects_every_hallucination():
    """The check that replaces "never take an ingredient from a sentence".

    Three prompt rewrites tried to forbid method-mining and all three
    failed, because a negative constraint is the first thing a small model
    drops. A copied line either is the start of a real source line or it
    is not, and that is decidable here.

    The rejected list is not invented: every entry is something the live
    model actually produced across the four measured runs.
    """
    source = FIXTURE.read_text(encoding="utf-8")
    real = [line for lines in _pie_ingredient_lines().values() for line in lines]

    hallucinated = [
        # In the prep text ("Set aside 2 Tbsp. graham cracker crumbs for
        # serving"), never in an ingredient list. Appeared in every run.
        "2 Tbsp. graham cracker crumbs",
        # The method's sugar, which became the crust's 0.5 cup.
        "a scant ½ cup sugar",
        # A whole method sentence returned as an ingredient name.
        "sour cream, remaining 2 Tbsp. sugar, and ¼ tsp. salt just to combine",
        # Reworded rather than copied.
        "graham cracker crumbs (reserved)",
        "1 cup sugar",
    ]

    assert recipe_service.verify_copied_lines(real, source) == real
    assert recipe_service.verify_copied_lines(hallucinated, source) == []


def test_verification_keeps_a_line_pypdf_welded_page_furniture_onto():
    """The real fixture ends the filling block with
    "1/4 cup sour creamC o m p l e t e  y o u r  B o n  A p p e t i t".
    An equality test would drop that ingredient; prefix matching keeps it.
    """
    source = FIXTURE.read_text(encoding="utf-8")
    assert recipe_service.verify_copied_lines(["¼ cup sour cream"], source) == ["¼ cup sour cream"]


def test_deterministic_parse_gets_every_amount_the_live_model_could_not():
    """The whole point, provable without Ollama.

    Four live runs produced a null quantity on every ingredient. These are
    the same fifteen lines, read by arithmetic instead.
    """
    blocks = _pie_ingredient_lines()
    parsed = [
        entry
        for component, lines in blocks.items()
        for line in lines
        for entry in recipe_service.parse_ingredient_line_amounts(line)
    ]

    assert all(e["quantity"] is not None for e in parsed), "a null quantity is the failure this replaces"
    assert len(parsed) == 16, "15 source lines, one of which states two amounts"


def test_the_four_live_checks_pass_on_deterministically_parsed_lines():
    """The four assertions check_recipe_import.py runs against a live
    model, run here against the two-pass result. Every one of them failed
    on every live run of the single-call extraction.
    """
    ingredients = []
    for component, lines in _pie_ingredient_lines().items():
        for line in lines:
            for entry in recipe_service.parse_ingredient_line_amounts(line):
                ingredients.append({**entry, "component": component})

    def find(name, component=None, quantity=None, unit=None):
        return [
            i
            for i in ingredients
            if name in i["ingredient_name"].lower()
            and (component is None or (i.get("component") or "").lower().startswith(component))
            and (quantity is None or i["quantity"] == quantity)
            and (unit is None or (i["unit"] or "").lower().startswith(unit))
        ]

    # 1. The crust's sugar is 2 Tbsp., not the method's "scant 1/2 cup".
    assert find("sugar", component="crust", quantity=2), "crust sugar came from the method text"

    # 2. The filling's compound amount stays two entries, never summed.
    filling_sugar = find("sugar", component="filling")
    assert len(filling_sugar) == 2, f"expected 3/4 cup and 2 Tbsp. separately, got {len(filling_sugar)}"
    assert not any(i["quantity"] == 1.75 for i in filling_sugar), "compound amount was merged"

    # 3. Nothing invented from the preparation text.
    assert not find("crumb"), "a graham cracker crumbs row exists and is only in the prep text"

    # 4. Every ingredient carries a component.
    assert all(i["component"] for i in ingredients)


def test_the_mixed_unicode_fraction_pypdf_emits():
    """ "1 1/4 cups" arrives from pypdf as "1" followed by the ¼ glyph with
    no space. It parsed as 1.0 before, quietly losing a quarter of the
    pumpkin -- the kind of wrong that never looks wrong."""
    assert recipe_service.parse_ingredient_line_amounts("1¼ cups pumpkin purée")[0]["quantity"] == 1.25


def test_a_compound_line_is_split_not_summed():
    entries = recipe_service.parse_ingredient_line_amounts("¾ (scant) cup plus 2 Tbsp. sugar , divided")

    assert [(e["quantity"], e["unit"]) for e in entries] == [(0.75, "cup"), (2.0, "tbsp")]
    assert all(e["ingredient_name"] == "sugar" for e in entries)
    # "scant" is genuinely on the ingredient line, so it is provenance.
    assert all("scant" in (e["prep_note"] or "") for e in entries)


def test_and_is_only_a_compound_joiner_when_a_number_follows():
    """ "salt and pepper" is one ingredient with a two-word name, not two
    amounts. The joiner only continues when a quantity actually follows."""
    entries = recipe_service.parse_ingredient_line_amounts("1 tsp. salt and pepper")

    assert len(entries) == 1
    assert entries[0]["ingredient_name"] == "salt and pepper"


def test_a_one_glyph_transcription_slip_is_repaired_not_dropped():
    """The first live two-pass run returned 12 of 16 ingredients.

    Pass 1 had transcribed the source's 1/4 as 1/2 -- U+00BC and U+00BD,
    adjacent codepoints -- on three lines. Verification correctly refused
    them, and they then vanished, which is silent data loss: a household
    reviewing the import sees a plausible ingredient list with the salt
    and the nutmeg simply absent.

    The source says what the answer should have been, so it is used.
    """
    source = FIXTURE.read_text(encoding="utf-8")
    slips = ["½ tsp. kosher salt", "½ tsp. ground nutmeg", "½ cup sour cream"]

    accepted, rejected = recipe_service.reconcile_copied_lines(slips, source)

    assert rejected == []
    assert accepted == ["¼ tsp. kosher salt", "¼ tsp. ground nutmeg", "¼ cup sour cream"]


def test_repair_can_never_introduce_text_the_source_does_not_have():
    """The property that makes repairing safe at all.

    Everything accepted is the SOURCE's own characters, never the model's,
    so a too-generous threshold can at worst pick the wrong real line -- it
    can never invent one. Without this, "repair" would just be a second
    place for the model to hallucinate.
    """
    source = FIXTURE.read_text(encoding="utf-8")
    normalized_source = " ".join(source.split())

    accepted, _ = recipe_service.reconcile_copied_lines(
        ["½ tsp. kosher salt", "12 graham crackers", "¾ cup whole milk"], source
    )

    assert accepted
    for line in accepted:
        assert line in normalized_source


def test_repair_does_not_rescue_a_hallucination():
    """The threshold sits in measured empty space, not on a round number.

    On the real fixture, transcription slips scored 0.938-0.987 against
    their true line and these scored 0.500-0.727 -- every one of them
    something the live model actually produced.
    """
    source = FIXTURE.read_text(encoding="utf-8")
    hallucinated = [
        "2 Tbsp. graham cracker crumbs",
        "a scant ½ cup sugar",
        "graham cracker crumbs (reserved)",
        "sour cream, remaining 2 Tbsp. sugar, and ¼ tsp. salt just to combine",
        "2 cups whipped cream",
    ]

    accepted, rejected = recipe_service.reconcile_copied_lines(hallucinated, source)

    assert accepted == []
    assert rejected == hallucinated


def test_repair_recovers_the_full_ingredient_list_from_a_slipped_transcription():
    """End to end: what the live run should have produced.

    Pass 1's actual output, with the three glyph slips it really made,
    yields all 16 entries once repaired.
    """
    source = FIXTURE.read_text(encoding="utf-8")
    blocks = _pie_ingredient_lines()
    slipped = {
        "¼ tsp. kosher salt": "½ tsp. kosher salt",
        "¼ tsp. ground nutmeg": "½ tsp. ground nutmeg",
        "¼ cup sour cream": "½ cup sour cream",
    }

    entries = []
    for component, lines in blocks.items():
        as_sent = [slipped.get(line, line) for line in lines]
        for line in recipe_service.verify_copied_lines(as_sent, source):
            for entry in recipe_service.parse_ingredient_line_amounts(line):
                entries.append({**entry, "component": component})

    assert len(entries) == 16, "a repaired transcription must lose nothing"
    assert all(e["quantity"] is not None for e in entries)
    salt = [e for e in entries if e["ingredient_name"] == "kosher salt" and e["component"] == "Crust"]
    assert salt and salt[0]["quantity"] == 0.25, "the slipped 1/4 tsp. must come back as 0.25, not 0.5"


def test_a_packaging_word_becomes_the_unit_not_part_of_the_name():
    """Ingredient name is this app's join key -- inventory rows, grocery
    lines and price lookups are all reconciled by string match, which the
    audit names as the source of every silent-wrong-answer bug in the data
    layer. "envelope unflavored gelatin" matches nothing anywhere, so the
    packaging word has to land in `unit` where it belongs.
    """
    entry = recipe_service.parse_ingredient_line_amounts("1 envelope unflavored gelatin (2½ tsp.)")[0]

    assert entry["quantity"] == 1.0
    assert entry["unit"] == "envelope"
    assert entry["ingredient_name"] == "unflavored gelatin"
    # The secondary measure is kept -- it is real provenance, just not part
    # of the name.
    assert entry["prep_note"] == "2½ tsp."


def test_a_trailing_parenthetical_is_a_note_not_part_of_the_name():
    entry = recipe_service.parse_ingredient_line_amounts("1¼ cups unsweetened pumpkin purée (from one 15-oz. can)")[0]

    assert entry["ingredient_name"] == "unsweetened pumpkin purée"
    assert entry["prep_note"] == "from one 15-oz. can"


def test_a_parenthetical_is_kept_when_it_is_the_whole_name():
    """Stripping has to stop short of leaving nothing behind."""
    entry = recipe_service.parse_ingredient_line_amounts("2 (whatever)")[0]

    assert entry["ingredient_name"]


def test_the_prompt_names_no_example_section_headings():
    """Measured 2026-08-06, on the pie.

    Rules 3 and 4 used to illustrate `component` with
    `(Crust, Filling and Assembly, Topping)`. The pie's ingredient list
    and its method each have exactly TWO headings, and they are the first
    two of those three. The import came back with instructions labelled
    `Crust`, `Filling and Assembly` and `Topping` -- and `topping` does
    not occur anywhere in that source, not once.

    An example that happens to match the document in front of the model
    is not an example, it is a suggestion. Rules 3 and 4 state the
    constraint now. Do not put example headings back."""
    prompt = recipe_service.RECIPE_IMPORT_PROMPT
    rules = prompt[prompt.index("RULES:") : prompt.index("EXAMPLE (")]

    assert "opping" not in rules
    # ...and the constraint the examples were replaced with is present.
    assert "ONLY headings the source actually prints" in rules
    assert "never introduce a heading the source does not print" in rules
