"""Audit P1-5: the ingredient resolution layer.

Ingredient identity is this app's join key across inventory, recipes,
grocery lines and price lookups, and it is free text. Before this layer
existed every one of those call sites did `ILIKE %name%` in one or both
directions and took whichever row came back first.

The first section below pins the exact wrong answers that produced --
each test names the old behaviour it replaces, so a future session can
tell whether a change is reintroducing something already understood
rather than fixing something new.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.models import IngredientAlias, InventoryItem
from app.services import ingredient_resolution_service as irs
from app.services import inventory_service


def _score(query: str, candidate: str) -> float:
    return irs.score_names(irs.normalize_name(query), irs.normalize_name(candidate)).score


def _item(db, name, quantity=5.0, unit="lb", **kwargs):
    item = InventoryItem(name=name, quantity=quantity, unit=unit, category="pantry", **kwargs)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


# --- The specific wrong matches the audit named -------------------------


def test_egg_does_not_match_eggplant():
    """The headline case. `ILIKE %egg%` matched "eggplant" because
    substring matching has no concept of a word boundary. These two names
    share no TOKEN, so they now score zero -- not "low", zero."""
    assert _score("egg", "eggplant") == 0.0
    assert _score("eggs", "eggplant") == 0.0
    assert _score("eggplant", "egg") == 0.0


def test_oil_does_not_match_oil_packed_tuna():
    """Shares a whole token, so token matching alone would still fire.
    What stops it is head agreement: "oil-packed tuna" is a tuna, and a
    token subset that is not a suffix is a weak signal, not an equal one.
    Lands in the low band -- visible as a suggestion, never applied."""
    score = _score("oil", "oil-packed tuna")
    assert 0 < score < irs.THRESHOLD_ADVISORY
    assert irs.confidence_band(score) == irs.CONFIDENCE_LOW


def test_chicken_does_not_match_chicken_broth():
    """Blocked outright rather than scored low. "broth" is a
    transformation word: a broth is made FROM chicken and is not chicken,
    so no amount of shared text should let a recipe's chicken be deducted
    from a carton of stock."""
    result = irs.score_names(irs.normalize_name("chicken"), irs.normalize_name("chicken broth"))
    assert result.score == 0.0
    assert result.blocked_by is not None
    assert "broth" in result.blocked_by


def test_blocking_is_symmetric():
    """Which side the transformation word is on cannot matter -- the two
    names denote different foods either way."""
    assert _score("almond", "almond milk") == 0.0
    assert _score("almond milk", "almond") == 0.0
    assert _score("peanut butter", "peanut") == 0.0


# --- What must still match ----------------------------------------------


@pytest.mark.parametrize(
    "query,candidate",
    [
        ("salt", "kosher salt"),
        ("tomato", "roma tomatoes"),
        ("lentils", "red lentils"),
        ("sugar", "brown sugar"),
        ("cinnamon", "ground cinnamon"),
        ("milk", "2% milk"),
    ],
)
def test_a_more_specific_name_still_matches_confidently(query, candidate):
    """Conservative must not mean useless. A candidate that ends in the
    query's own head noun is a variety of it, and still clears the bar
    for a database write."""
    assert _score(query, candidate) >= irs.THRESHOLD_DESTRUCTIVE


def test_preparation_words_are_ignored():
    assert _score("tomatoes, chopped", "tomato") >= irs.THRESHOLD_DESTRUCTIVE
    assert _score("chicken breast, boneless skinless", "chicken breast") >= irs.THRESHOLD_DESTRUCTIVE
    assert _score("2 large ripe avocados", "avocado") >= irs.THRESHOLD_DESTRUCTIVE


def test_accents_and_punctuation_fold():
    assert _score("jalapeño", "jalapeno") == 1.0
    assert _score("all-purpose flour", "all purpose flour") == 1.0


def test_plurals_fold_without_mangling_words_that_only_look_plural():
    assert irs.singularize("berries") == "berry"
    assert irs.singularize("tomatoes") == "tomato"
    assert irs.singularize("leaves") == "leaf"
    assert irs.singularize("dishes") == "dish"
    assert irs.singularize("glasses") == "glass"
    # These would singularise to "molass", "asparagu" and "hummu" under
    # the generic rules, and then stop matching themselves.
    assert irs.singularize("molasses") == "molasses"
    assert irs.singularize("asparagus") == "asparagus"
    assert irs.singularize("hummus") == "hummus"
    assert _score("molasses", "molasses") == 1.0


def test_a_source_qualifier_on_a_transformation_word_is_only_a_suggestion():
    """ "olive oil" is oil but "peanut butter" is not butter -- identical
    grammar, opposite answers, and nothing in the strings separates them.
    So the whole family sits just under the write threshold: usable for a
    grocery list, a question before a deduction."""
    for query, candidate in [("oil", "olive oil"), ("butter", "peanut butter"), ("flour", "almond flour")]:
        score = _score(query, candidate)
        assert irs.THRESHOLD_ADVISORY <= score < irs.THRESHOLD_DESTRUCTIVE


# --- Ranking: longest match wins, ties are decided on purpose ------------


def test_longest_match_wins_over_first_row():
    """The old code took the first row the database returned. Here the
    generic row is created first and the specific one second, so "first"
    and "best" disagree -- which is the whole point."""
    ranked = irs.rank_candidates("olive oil", [("oil", "generic"), ("olive oil", "specific")])
    assert ranked[0].payload == "specific"
    assert ranked[0].score > ranked[1].score


def test_identical_names_are_ordered_by_expiration_not_rowid(db_session):
    """`inventory_items.name` has no unique constraint -- two cartons of
    milk with different dates are two legitimate rows -- so which one a
    deduction hits used to be undefined. It is now first-expired-first-
    out, matching what compute_urgency already argues the app should
    prefer."""
    later = _item(db_session, "milk", 1.0, "l", expiration_date=date(2026, 9, 1))
    sooner = _item(db_session, "milk", 1.0, "l", expiration_date=date(2026, 8, 5))
    assert later.id < sooner.id  # the soonest-expiring row is NOT the first row

    resolution = irs.resolve(db_session, "milk")
    assert resolution.item.id == sooner.id


def test_an_empty_row_loses_to_one_with_stock(db_session):
    """Deducting from a row already at zero accomplishes nothing and
    leaves the real stock untouched."""
    empty = _item(db_session, "rice", 0.0, "cup", expiration_date=date(2026, 8, 5))
    stocked = _item(db_session, "rice", 4.0, "cup", expiration_date=date(2026, 12, 1))
    assert empty.id < stocked.id

    assert irs.resolve(db_session, "rice").item.id == stocked.id


# --- Confidence policy per call site ------------------------------------


def test_destructive_call_sites_refuse_a_low_confidence_match(db_session):
    """Nothing is written, and the caller gets candidates to ask with --
    not a bare "not found", which tells the user nothing about why."""
    _item(db_session, "chicken breast", 2.0, "lb")

    outcome = inventory_service.deduct_by_name(db_session, "chicken", 1.0, "lb")
    assert outcome.status == inventory_service.DEDUCT_AMBIGUOUS
    assert outcome.item is None
    assert [c.name for c in outcome.resolution.candidates] == ["chicken breast"]
    # The row is untouched.
    assert db_session.query(InventoryItem).one().quantity == 2.0


def test_a_blocked_row_is_reported_as_blocked_not_merely_absent(db_session):
    """An unexplained non-match invites the same bug report twice."""
    _item(db_session, "chicken broth", 2.0, "l")

    resolution = irs.resolve(db_session, "chicken")
    assert resolution.item is None
    assert resolution.candidates == []
    assert len(resolution.blocked) == 1
    assert "broth" in resolution.blocked[0].blocked_by


def test_advisory_call_sites_accept_a_medium_confidence_match(db_session):
    """A grocery list is read by a human before anyone acts on it, so it
    runs at a lower bar than a database write -- deliberately, and the
    matched name rides along so the reduction is checkable."""
    from app.services import meal_plan_service

    inventory = [InventoryItem(name="olive oil", quantity=1.0, unit="cup", category="pantry")]
    result = meal_plan_service.subtract_inventory(
        [{"ingredient_name": "oil", "quantity": 3.0, "unit": "cup"}], inventory
    )
    assert result[0]["quantity"] == 2.0
    assert result[0]["matched_item_name"] == "olive oil"
    assert result[0]["match_confidence"] == irs.CONFIDENCE_MEDIUM


def test_advisory_call_sites_still_refuse_a_low_confidence_match(db_session):
    """ "oil" against "oil-packed tuna" is below even the advisory bar, so
    the line keeps its full quantity. Buying oil you did not need is
    recoverable; not buying it is not."""
    from app.services import meal_plan_service

    inventory = [InventoryItem(name="oil-packed tuna", quantity=4.0, unit="cup", category="pantry")]
    result = meal_plan_service.subtract_inventory(
        [{"ingredient_name": "oil", "quantity": 3.0, "unit": "cup"}], inventory
    )
    assert result[0]["quantity"] == 3.0
    assert "matched_item_name" not in result[0]


# --- Aliases ------------------------------------------------------------


def test_an_alias_resolves_a_name_the_matcher_refuses(db_session):
    """The escape hatch that makes the conservative thresholds liveable:
    the user is asked once, and never again."""
    breast = _item(db_session, "chicken breast", 2.0, "lb")
    assert irs.resolve(db_session, "chicken").item is None

    irs.remember_alias(db_session, "chicken", "chicken breast", inventory_item_id=breast.id)

    resolution = irs.resolve(db_session, "chicken")
    assert resolution.item.id == breast.id
    assert resolution.confidence == irs.CONFIDENCE_EXACT
    assert resolution.via_alias is True


def test_an_alias_survives_the_row_it_was_taught_on_being_replaced(db_session):
    """Groceries get used up and re-bought constantly. An alias that only
    pointed at a row id would rot within one shopping cycle, so aliases
    resolve to a NAME and the name is then matched normally."""
    old_row = _item(db_session, "chicken breast", 2.0, "lb")
    irs.remember_alias(db_session, "chicken", "chicken breast", inventory_item_id=old_row.id)

    db_session.delete(old_row)
    db_session.commit()
    new_row = _item(db_session, "chicken breast", 3.0, "lb")

    resolution = irs.resolve(db_session, "chicken")
    assert resolution.item.id == new_row.id
    assert resolution.via_alias is True


def test_an_alias_is_matched_on_its_normalised_form(db_session):
    """ "Chopped Tomatoes", "chopped tomato" and "CHOPPED TOMATOES" are
    one alias, not three rows."""
    _item(db_session, "san marzano", 2.0, "can")
    irs.remember_alias(db_session, "Chopped Tomatoes", "san marzano")

    for spelling in ["chopped tomatoes", "CHOPPED TOMATO", "  Chopped  Tomatoes  "]:
        assert irs.resolve(db_session, spelling).item is not None


def test_teaching_the_same_alias_twice_updates_it(db_session):
    _item(db_session, "great northern beans", 2.0, "can")
    _item(db_session, "cannellini beans", 2.0, "can")
    irs.remember_alias(db_session, "white beans", "great northern beans")
    irs.remember_alias(db_session, "white beans", "cannellini beans")

    assert db_session.query(IngredientAlias).count() == 1
    assert irs.resolve(db_session, "white beans").item.name == "cannellini beans"


def test_an_alias_cycle_terminates(db_session):
    """A household editing aliases by hand can trivially write "a -> b,
    b -> a". That must not hang a request."""
    irs.remember_alias(db_session, "aubergine", "eggplant")
    irs.remember_alias(db_session, "eggplant", "aubergine")

    resolution = irs.resolve(db_session, "aubergine")  # must return, not spin
    assert resolution.item is None


def test_a_self_alias_is_rejected(db_session):
    with pytest.raises(ValueError):
        irs.remember_alias(db_session, "Tomatoes", "tomato")


# --- The user-editable transformation word list -------------------------


def test_adding_a_word_blocks_a_pair_the_default_list_misses(db_session):
    """No curated list can be complete. A household that finds a pair the
    matcher wrongly treats as one ingredient can add the word themselves
    rather than filing a bug and waiting."""
    from app.services import settings_service

    assert _score("chicken", "chicken feed") > 0  # scored, if only as a suggestion

    settings_service.set_setting(db_session, "ingredient_transformation_words", "feed, broth")
    words = irs.load_transformation_words(db_session)
    result = irs.score_names(irs.normalize_name("chicken"), irs.normalize_name("chicken feed"), words)
    assert result.score == 0.0
    assert "feed" in result.blocked_by


def test_removing_a_word_unblocks_a_pair_the_household_wants_matched(db_session):
    """The other direction, and the one that matters more in practice:
    the list erring toward over-blocking is safe by design, and this is
    how a household undoes it for their own kitchen."""
    from app.services import settings_service

    assert _score("chicken", "chicken broth") == 0.0

    settings_service.set_setting(db_session, "ingredient_transformation_words", "oil, milk")
    words = irs.load_transformation_words(db_session)
    result = irs.score_names(irs.normalize_name("chicken"), irs.normalize_name("chicken broth"), words)
    assert result.blocked_by is None
    assert result.score > 0


def test_clearing_the_transformation_setting_restores_the_default(db_session):
    from app.services import settings_service

    settings_service.set_setting(db_session, "ingredient_transformation_words", "")
    assert irs.load_transformation_words(db_session) == irs.DEFAULT_TRANSFORMATION_SET


# --- The API contract ---------------------------------------------------
#
# Called as plain functions against a Session, the same style the rest of
# this suite uses (see test_end_to_end_smoke.py's note on why no
# TestClient) -- nothing here depends on an HTTP-layer concern.


def test_an_ambiguous_deduct_returns_409_with_candidates_and_changes_nothing(db_session):
    """The contract the frontend picker is built against. A 409 is a
    question, not a failure -- distinguishable from the 404 that means
    "nothing like this exists", which needs a different message and a
    different next action."""
    from fastapi import HTTPException

    from app.routers.inventory import deduct_inventory
    from app.schemas.inventory import InventoryDeductRequest

    _item(db_session, "chicken breast", 2.0, "lb")

    with pytest.raises(HTTPException) as exc:
        deduct_inventory(InventoryDeductRequest(ingredient_name="chicken", quantity=1.0, unit="lb"), db_session)

    assert exc.value.status_code == 409
    assert [c["name"] for c in exc.value.detail["candidates"]] == ["chicken breast"]
    assert exc.value.detail["matched"] is False
    assert db_session.query(InventoryItem).one().quantity == 2.0  # untouched


def test_answering_the_question_applies_the_deduction_and_remembers_it(db_session):
    """The round trip the picker performs: re-send with `item_id` to
    apply the user's explicit choice, plus `remember_alias` so the same
    name goes straight through next time."""
    from app.routers.inventory import deduct_inventory
    from app.schemas.inventory import InventoryDeductRequest

    breast = _item(db_session, "chicken breast", 2.0, "lb")

    result = deduct_inventory(
        InventoryDeductRequest(
            ingredient_name="chicken", quantity=1.0, unit="lb", item_id=breast.id, remember_alias=True
        ),
        db_session,
    )
    assert result.quantity == 1.0

    # Asked once. The second time, no 409.
    again = deduct_inventory(InventoryDeductRequest(ingredient_name="chicken", quantity=1.0, unit="lb"), db_session)
    assert again.id == breast.id
    assert again.quantity == 0.0


def test_a_name_matching_nothing_is_a_404_not_a_409(db_session):
    from fastapi import HTTPException

    from app.routers.inventory import deduct_inventory
    from app.schemas.inventory import InventoryDeductRequest

    _item(db_session, "rice", 2.0, "cup")

    with pytest.raises(HTTPException) as exc:
        deduct_inventory(InventoryDeductRequest(ingredient_name="saffron"), db_session)
    assert exc.value.status_code == 404


def test_confirming_a_meal_reports_what_it_could_not_deduct(db_session):
    """Confirming a meal is still best-effort -- one unresolvable
    ingredient must not fail the whole confirmation -- but best-effort now
    means "tells you what it could not do". It used to skip silently, so a
    household just saw inventory quietly failing to go down."""
    from app.routers.meal_plan import confirm_meal_plan_entry, create_meal_plan
    from app.routers.recipes import create_recipe
    from app.schemas.meal_plan import MealPlanCreate, MealPlanEntryCreate
    from app.schemas.recipe import RecipeCreate, RecipeIngredientBase

    _item(db_session, "chicken breast", 5.0, "lb")

    recipe = create_recipe(
        RecipeCreate(
            title="Roast chicken",
            default_servings=2,
            ingredients=[RecipeIngredientBase(ingredient_name="chicken", quantity=1.0, unit="lb")],
        ),
        db_session,
    )
    plan = create_meal_plan(
        MealPlanCreate(
            week_start_date=date(2026, 8, 10),
            entries=[MealPlanEntryCreate(day_of_week=0, meal_type="dinner", recipe_id=recipe.id, servings=2)],
        ),
        db_session,
    )
    entry = plan.entries[0]

    result = confirm_meal_plan_entry(plan.id, entry.id, db=db_session)

    assert result.is_confirmed is True  # the confirmation itself still succeeds
    assert len(result.inventory_deductions) == 1
    note = result.inventory_deductions[0]
    assert note.ingredient_name == "chicken"
    assert note.status == inventory_service.DEDUCT_AMBIGUOUS
    assert note.candidate_names == ["chicken breast"]
    assert db_session.query(InventoryItem).one().quantity == 5.0  # nothing guessed


def test_the_grocery_list_persists_why_a_line_reads_the_way_it_does(db_session):
    """`needs_review` and the matched-item fields were computed by
    subtract_inventory and then discarded before reaching a persisted row,
    so no user had ever seen any of them."""
    from app.models import GroceryListItem
    from app.routers.meal_plan import create_meal_plan
    from app.routers.recipes import create_recipe
    from app.schemas.meal_plan import MealPlanCreate, MealPlanEntryCreate
    from app.schemas.recipe import RecipeCreate, RecipeIngredientBase

    _item(db_session, "olive oil", 1.0, "cup")
    recipe = create_recipe(
        RecipeCreate(
            title="Dressing",
            default_servings=2,
            ingredients=[RecipeIngredientBase(ingredient_name="oil", quantity=3.0, unit="cup")],
        ),
        db_session,
    )
    create_meal_plan(
        MealPlanCreate(
            week_start_date=date(2026, 8, 17),
            entries=[MealPlanEntryCreate(day_of_week=0, meal_type="dinner", recipe_id=recipe.id, servings=2)],
        ),
        db_session,
    )

    line = db_session.query(GroceryListItem).filter_by(ingredient_name="oil").one()
    assert line.quantity == 2.0
    assert line.matched_item_name == "olive oil"
    assert line.match_confidence == irs.CONFIDENCE_MEDIUM
