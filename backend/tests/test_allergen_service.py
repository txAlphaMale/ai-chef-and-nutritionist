"""Unit tests for the B3.1/B3.2 deterministic allergen/restriction check
(app/services/allergen_service.py).

Pure-function tests for find_allergen_matches/find_cross_contact_matches/
check_ingredients need no DB session at all. check_household_restrictions
is exercised against a real (but unpersisted -- no db.add/commit needed
since it only reads) HouseholdPreferences row via the db_session fixture.
"""

from __future__ import annotations

from app.models import HouseholdPreferences
from app.services import allergen_service as ag

# --- find_allergen_matches ------------------------------------------------


def test_find_allergen_matches_basic_hit():
    matches = ag.find_allergen_matches(["all-purpose flour", "salt"], ["wheat"])
    assert len(matches) == 1
    assert matches[0].allergen == "wheat"
    assert matches[0].ingredient_name == "all-purpose flour"
    assert matches[0].matched_keyword == "flour"


def test_find_allergen_matches_no_hit_when_allergen_not_restricted():
    matches = ag.find_allergen_matches(["wheat flour"], ["milk"])
    assert matches == []


def test_find_allergen_matches_case_insensitive():
    matches = ag.find_allergen_matches(["WHOLE MILK"], ["milk"])
    assert len(matches) == 1


def test_find_allergen_matches_multiple_allergens_multiple_ingredients():
    matches = ag.find_allergen_matches(["butter", "peanut butter", "rice"], ["milk", "peanuts"])
    allergens_hit = {m.allergen for m in matches}
    assert allergens_hit == {"milk", "peanuts"}
    # "butter" matches milk in both ingredient strings that contain it
    ingredient_names_hit = {m.ingredient_name for m in matches}
    assert "rice" not in ingredient_names_hit


def test_find_allergen_matches_gluten_superset_catches_non_wheat_grains():
    matches = ag.find_allergen_matches(["barley malt syrup"], ["gluten"])
    matched_keywords = {m.matched_keyword for m in matches}
    assert "barley" in matched_keywords
    assert "malt" in matched_keywords


def test_find_allergen_matches_word_boundary_avoids_compound_false_positive():
    # "eggplant" should NOT match "egg" -- word-boundary anchored.
    matches = ag.find_allergen_matches(["eggplant"], ["eggs"])
    assert matches == []


def test_find_allergen_matches_unknown_allergen_key_ignored():
    matches = ag.find_allergen_matches(["wheat flour"], ["not_a_real_allergen"])
    assert matches == []


def test_find_allergen_matches_empty_ingredient_name_skipped():
    matches = ag.find_allergen_matches(["", None, "milk"], ["milk"])
    assert len(matches) == 1
    assert matches[0].ingredient_name == "milk"


# --- "-free" negation ------------------------------------------------------


def test_negation_suppresses_gluten_free_flour():
    matches = ag.find_allergen_matches(["gluten-free flour"], ["gluten"])
    assert matches == []


def test_negation_suppresses_dairy_free_but_not_wheat_in_same_name():
    # "dairy-free" should suppress a milk match, but the ingredient still
    # legitimately contains wheat and should be flagged for that allergen.
    matches = ag.find_allergen_matches(["dairy-free wheat cracker"], ["milk", "wheat"])
    allergens_hit = {m.allergen for m in matches}
    assert allergens_hit == {"wheat"}


def test_negation_handles_no_hyphen_and_no_space_spelling():
    assert ag.find_allergen_matches(["glutenfree bread"], ["gluten"]) == []
    assert ag.find_allergen_matches(["gluten free bread"], ["gluten"]) == []


def test_negation_does_not_suppress_a_different_allergen():
    # "peanut-free" shouldn't accidentally suppress a real tree_nuts hit.
    matches = ag.find_allergen_matches(["peanut-free almond butter"], ["peanuts", "tree_nuts"])
    allergens_hit = {m.allergen for m in matches}
    assert allergens_hit == {"tree_nuts"}


# --- find_cross_contact_matches (B3.2) -------------------------------------


def test_cross_contact_requires_strict_no_cross_contact_level():
    matches = ag.find_cross_contact_matches(["oats"], ["gluten"], gluten_observance_level="flexible")
    assert matches == []
    matches = ag.find_cross_contact_matches(["oats"], ["gluten"], gluten_observance_level="strict_gluten_only")
    assert matches == []


def test_cross_contact_requires_gluten_actually_restricted():
    matches = ag.find_cross_contact_matches(["oats"], ["milk"], gluten_observance_level="strict_no_cross_contact")
    assert matches == []


def test_cross_contact_hit_when_both_conditions_met():
    matches = ag.find_cross_contact_matches(
        ["rolled oats"], ["gluten"], gluten_observance_level="strict_no_cross_contact"
    )
    assert len(matches) == 1
    assert matches[0].allergen == "gluten_cross_contact"
    assert matches[0].ingredient_name == "rolled oats"


def test_cross_contact_respects_free_negation():
    matches = ag.find_cross_contact_matches(
        ["gluten-free oats"], ["gluten"], gluten_observance_level="strict_no_cross_contact"
    )
    assert matches == []


# --- check_ingredients (combined) ------------------------------------------


def test_check_ingredients_empty_restrictions_returns_empty_result():
    result = ag.check_ingredients(["wheat flour", "oats"], [])
    assert result.matches == []
    assert result.cross_contact_matches == []
    assert result.has_conflict is False


def test_check_ingredients_combines_both_categories():
    result = ag.check_ingredients(
        ["wheat flour", "rolled oats"], ["gluten"], gluten_observance_level="strict_no_cross_contact"
    )
    # "wheat flour" legitimately hits both the "wheat" and "flour"
    # keywords -- two matches, one ingredient, both correct.
    assert {m.ingredient_name for m in result.matches} == {"wheat flour"}
    assert len(result.cross_contact_matches) == 1
    assert result.has_conflict is True


def test_check_ingredients_cross_contact_alone_does_not_set_has_conflict():
    result = ag.check_ingredients(["rolled oats"], ["gluten"], gluten_observance_level="strict_no_cross_contact")
    assert result.matches == []
    assert len(result.cross_contact_matches) == 1
    assert result.has_conflict is False


def test_restriction_check_result_to_dict_shape():
    result = ag.check_ingredients(["wheat flour"], ["gluten"])
    d = result.to_dict()
    assert set(d.keys()) == {"matches", "cross_contact_matches"}
    assert d["matches"][0]["allergen"] == "gluten"
    assert d["matches"][0]["ingredient_name"] == "wheat flour"


# --- check_household_restrictions (DB-backed convenience wrapper) --------


def test_check_household_restrictions_no_preferences_row_returns_empty(db_session):
    result = ag.check_household_restrictions(db_session, ["wheat flour"])
    assert result.has_conflict is False


def test_check_household_restrictions_uses_persisted_preferences(db_session):
    prefs = HouseholdPreferences(restricted_allergens=["gluten"], gluten_observance_level="strict_no_cross_contact")
    db_session.add(prefs)
    db_session.commit()

    result = ag.check_household_restrictions(db_session, ["wheat flour", "rolled oats"])
    assert result.has_conflict is True
    assert len(result.cross_contact_matches) == 1


def test_check_household_restrictions_empty_allergen_list_is_no_conflict(db_session):
    prefs = HouseholdPreferences(restricted_allergens=[])
    db_session.add(prefs)
    db_session.commit()

    result = ag.check_household_restrictions(db_session, ["wheat flour", "milk"])
    assert result.has_conflict is False


# --- Taxonomy sanity --------------------------------------------------------


def test_allergen_choices_keys_match_keyword_dict():
    assert set(ag.ALLERGEN_KEYWORDS.keys()) == ag.ALLERGEN_KEYS


def test_observance_levels_keys_are_the_documented_three():
    assert {"flexible", "strict_gluten_only", "strict_no_cross_contact"} == ag.OBSERVANCE_LEVEL_KEYS
