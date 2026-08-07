"""The import tag gate: a model or a publisher may not assert that a dish
is free of something.

Written after measuring that removing `gluten_free` from the import
prompt's rule 6 vocabulary did not actually stop the model emitting it --
rule 6 invites a new tag when nothing in its set fits, and neither
`coerce_recipe_fields` nor `resolve_tags` ever checked what arrived. See
the `_ABSENCE_CLAIM_PATTERNS` comment in recipe_service for why this
error is not symmetric.

The false-positive cases below matter as much as the true positives. A
gate that eats `freezer_friendly` because the word starts with "free" is
a worse bug than the one it was written to fix, since it fails on every
import of a legitimately tagged recipe rather than occasionally.
"""

import pytest

from app.services import recipe_service as rs

# --- what must be dropped -------------------------------------------------


@pytest.mark.parametrize(
    "tag",
    [
        "gluten_free",
        "gluten free",
        "gluten-free",
        "dairy_free",
        "nut free",
        "egg-free",
        "soy_free",
        "grain_free",
        "sugar_free",
        "lactose free",
        "allergen_free",
        "free_of_gluten",
        "free-from-dairy",
        "no_gluten",
        "without dairy",
        "celiac_friendly",
        "celiac safe",
        "coeliac_friendly",
        "allergy_friendly",
        "allergen-friendly",
        "gf",
        "df",
        "gfree",
    ],
)
def test_absence_claims_are_dropped(tag):
    kept, dropped = rs.split_absence_claim_tags([tag])
    assert kept == []
    assert dropped == [tag]


# --- what must survive ----------------------------------------------------


@pytest.mark.parametrize(
    "tag",
    [
        # the whole reason this is an anchored match and not a substring
        # search: "freezer" contains "free".
        "freezer_friendly",
        # a sourcing claim, not an absence claim -- "free" leads here
        "free_range",
        "kid_friendly",
        "quick",
        "portable",
        "non_refrigerated",
        "dutch_oven_only",
        "backpacking",
        "one_pot",
        "make_ahead",
        # the meal types added to rule 6 alongside this gate
        "breakfast",
        "lunch",
        "dinner",
        "dessert",
    ],
)
def test_legitimate_tags_survive(tag):
    kept, dropped = rs.split_absence_claim_tags([tag])
    assert kept == [tag]
    assert dropped == []


def test_every_seeded_default_tag_survives_except_gluten_free():
    """The seeded vocabulary is the app's own list. If this gate would eat
    one of them on import, the two are contradicting each other.

    `gluten_free` is the deliberate exception: it stays seeded so the
    HOUSEHOLD can apply it by hand, and is refused only when a model or a
    publisher is the one asserting it."""
    from app.seed import DEFAULT_TAGS

    kept, dropped = rs.split_absence_claim_tags(list(DEFAULT_TAGS))
    assert dropped == ["gluten_free"]
    assert "gluten_free" not in kept
    assert set(kept) == set(DEFAULT_TAGS) - {"gluten_free"}


def test_partition_is_lossless_and_order_preserving():
    tags = ["breakfast", "gluten_free", "quick", "df", "freezer_friendly"]
    kept, dropped = rs.split_absence_claim_tags(tags)
    assert kept == ["breakfast", "quick", "freezer_friendly"]
    assert dropped == ["gluten_free", "df"]
    assert len(kept) + len(dropped) == len(tags)


def test_empty_input():
    assert rs.split_absence_claim_tags([]) == ([], [])


# --- through the function every model path actually calls -----------------


def _minimal(**extra):
    return {"title": "Test Recipe", "ingredients": [], "instructions": [], **extra}


def test_coerce_recipe_fields_drops_the_claim():
    """This is the shared tail for recipe import, chat's recipe proposals
    and meal-plan generation's `new_recipe` -- gating here covers all
    three, and covers none of the API's own create/update path."""
    coerced = rs.coerce_recipe_fields(_minimal(tags=["quick", "gluten_free", "dinner"]))
    assert coerced["tags"] == ["quick", "dinner"]


def test_coerce_recipe_fields_gates_the_jsonld_path_too():
    """A publisher's own schema.org keywords describe their kitchen, not
    this household's. Deliberate, not an oversight -- see the comment on
    _ABSENCE_CLAIM_PATTERNS."""
    coerced = rs.coerce_recipe_fields(_minimal(tags=["Gluten Free", "Dessert"]))
    assert coerced["tags"] == ["dessert"]


def test_coerce_recipe_fields_still_lowercases_and_strips():
    coerced = rs.coerce_recipe_fields(_minimal(tags=["  QUICK  ", "Make_Ahead", ""]))
    assert coerced["tags"] == ["quick", "make_ahead"]


def test_coerce_recipe_fields_with_no_tags():
    assert rs.coerce_recipe_fields(_minimal())["tags"] == []


def test_the_gate_says_what_it_dropped(capsys):
    """A gate that silently eats a tag is how someone concludes the model
    never emitted one."""
    rs.coerce_recipe_fields(_minimal(tags=["gluten_free", "quick"]))
    out = capsys.readouterr().out
    assert "[recipe_tags]" in out
    assert "gluten_free" in out


def test_no_log_line_when_nothing_was_dropped(capsys):
    rs.coerce_recipe_fields(_minimal(tags=["quick"]))
    assert "[recipe_tags]" not in capsys.readouterr().out


# --- the prompt and the gate must agree -----------------------------------


def test_rule_6_no_longer_offers_gluten_free():
    """Pins the prompt change this gate was written to back up. If someone
    puts it back in the vocabulary, the gate would silently refuse every
    tag the prompt asked for -- a contradiction worth failing on."""
    assert "kid_friendly, gluten_free" not in rs.RECIPE_IMPORT_PROMPT
    assert "NEVER emit a tag claiming the dish is FREE OF something" in rs.RECIPE_IMPORT_PROMPT


def test_rule_6_offers_the_meal_types():
    for meal_type in ("breakfast", "lunch", "dinner", "dessert"):
        assert meal_type in rs.RECIPE_IMPORT_PROMPT


def test_no_tag_rule_6_offers_would_be_eaten_by_the_gate():
    """The prompt's vocabulary and the gate are two lists that must not
    contradict each other. Parsed out of the prompt text rather than
    duplicated here, so editing rule 6 is what this test reads."""
    line = next(ln for ln in rs.RECIPE_IMPORT_PROMPT.splitlines() if ln.startswith('6. "tags"'))
    vocabulary = line.split("where applicable:")[1].split("(omit")[0]
    offered = [t.strip() for t in vocabulary.split(",") if t.strip()]
    assert len(offered) >= 13, f"parsed only {offered} out of rule 6 -- the format changed"
    _kept, dropped = rs.split_absence_claim_tags(offered)
    assert dropped == [], f"rule 6 offers tags this gate refuses: {dropped}"
