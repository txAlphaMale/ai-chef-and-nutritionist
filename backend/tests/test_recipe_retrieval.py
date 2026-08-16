"""Capstone review 2026-08-16 -- recipe retrieval for AI context.

The defect these tests exist to hold closed: `chat_service.build_chat_context`
gave the model 200 recipe TITLES, ordered alphabetically, with no indication
that the list was partial. Two consequences, one test each below:

  * a recipe filed past the alphabetical cut-off was invisible, and the
    model had no way to know, so "we have no recipe for that" was an
    answer it could give confidently and wrongly;
  * "what can I make with the chicken that expires Thursday" was being
    answered from recipe names, because no ingredient ever reached the
    prompt.

Every assertion here is written to FAIL against the pre-change behaviour,
not merely to pass against the new one.
"""

from app.models import MealTag, Recipe, RecipeIngredient
from app.services import chat_service, recipe_service


def _make_recipe(db, title, ingredients=(), tags=(), *, is_staple=False, rating=None, servings=2):
    recipe = Recipe(title=title, default_servings=servings, is_staple=is_staple, rating=rating)
    db.add(recipe)
    db.flush()
    for name in ingredients:
        db.add(RecipeIngredient(recipe_id=recipe.id, ingredient_name=name))
    for tag_name in tags:
        tag = db.query(MealTag).filter_by(name=tag_name).first()
        if tag is None:
            tag = MealTag(name=tag_name)
            db.add(tag)
            db.flush()
        recipe.tags.append(tag)
    db.commit()
    db.refresh(recipe)
    return recipe


# --- Pure helpers ---------------------------------------------------------


def test_tokens_drop_cooking_filler_words():
    tokens = recipe_service._retrieval_tokens("What can I make for dinner with the chicken?")
    assert "chicken" in tokens
    # "make", "dinner", "with", "what", "can" are all in nearly every
    # cooking question and would match nearly every recipe.
    assert "make" not in tokens
    assert "dinner" not in tokens
    assert "with" not in tokens


def test_tokens_include_a_singular_form_so_plurals_match():
    # The allergen matcher learned this the hard way: recipes are written
    # in the plural ("2 cups cashews") and questions are asked either way.
    tokens = recipe_service._retrieval_tokens("do we have anything with tomatoes")
    assert "tomatoe" in tokens or "tomato" in tokens
    assert "tomatoes" in tokens


def test_empty_and_greeting_queries_produce_no_search():
    assert recipe_service._retrieval_tokens("") == []
    assert recipe_service._retrieval_tokens(None) == []
    # "the best one" is entirely filler -- nothing worth a table scan.
    assert recipe_service._retrieval_tokens("what is the best one for me") == []


def test_title_match_outscores_an_ingredient_only_match():
    title_hit = recipe_service.score_recipe_against_tokens(
        "Chicken Soup", [], ["water", "salt"], ["chicken"]
    )
    ingredient_hit = recipe_service.score_recipe_against_tokens(
        "Grandma's Sunday Supper", [], ["chicken thighs"], ["chicken"]
    )
    assert title_hit > ingredient_hit > 0


def test_score_is_zero_when_nothing_matches():
    assert recipe_service.score_recipe_against_tokens("Beef Stew", ["dinner"], ["beef"], ["salmon"]) == 0


# --- Retrieval against a database ----------------------------------------


def test_retrieval_finds_a_recipe_by_ingredient_its_title_never_mentions(db_session):
    """The question this app exists to answer. "Zucchini Boats" does not
    contain the word "sausage" anywhere in its title, so no title-only
    catalog could ever surface it for "what can I do with the sausage"."""
    _make_recipe(db_session, "Zucchini Boats", ["italian sausage", "zucchini", "parmesan"])
    _make_recipe(db_session, "Apple Crisp", ["apples", "oats", "butter"])

    found = recipe_service.find_relevant_recipes(db_session, "what can I do with the sausage")

    assert [r.title for r in found] == ["Zucchini Boats"]


def test_retrieval_reaches_past_the_alphabetical_catalog_cutoff(db_session):
    """The truncation bug, reproduced. 250 filler recipes named so they
    sort BEFORE the real answer means the old 200-title alphabetical
    catalog could not contain it at any size."""
    for i in range(250):
        _make_recipe(db_session, f"Aaa Filler Recipe {i:03d}", ["water"])
    _make_recipe(db_session, "Zesty Salmon Traybake", ["salmon fillets", "lemon", "dill"])

    catalog = chat_service.build_chat_context(db_session, query="any salmon ideas?")["recipe_catalog"]
    assert len(catalog) == chat_service.RECIPE_CATALOG_LIMIT
    assert "Zesty Salmon Traybake" not in [r["title"] for r in catalog], (
        "guard on the premise: the salmon recipe must NOT be in the truncated catalog, "
        "otherwise this test proves nothing about retrieval"
    )

    relevant = chat_service.build_chat_context(db_session, query="any salmon ideas?")["relevant_recipes"]
    assert [r["title"] for r in relevant] == ["Zesty Salmon Traybake"]


def test_staples_and_ratings_break_ties_ahead_of_alphabetical_order(db_session):
    _make_recipe(db_session, "Apple A", ["apples"], rating=1)
    _make_recipe(db_session, "Apple B", ["apples"], is_staple=True)
    _make_recipe(db_session, "Apple C", ["apples"], rating=5)

    found = recipe_service.find_relevant_recipes(db_session, "apples")

    # All three score identically on keywords, so the tiebreak decides:
    # staple first, then rating, then title.
    assert [r.title for r in found] == ["Apple B", "Apple C", "Apple A"]


def test_retrieval_returns_nothing_for_a_greeting(db_session):
    _make_recipe(db_session, "Chicken Soup", ["chicken"])
    assert recipe_service.find_relevant_recipes(db_session, "hey there") == []


def test_summary_carries_ingredients_and_reports_truncation(db_session):
    recipe = _make_recipe(db_session, "Big Bake", [f"ingredient {i}" for i in range(25)])
    summary = recipe_service.summarize_recipe_for_context(recipe, max_ingredients=20)

    assert len(summary["ingredients"]) == 20
    assert summary["ingredients_truncated"] == 5
    # Instructions are deliberately absent -- six recipes' worth of method
    # text would dominate the context window.
    assert "instructions" not in summary


# --- The prompt the model actually receives -------------------------------


def test_prompt_states_that_a_truncated_catalog_is_partial(db_session):
    for i in range(chat_service.RECIPE_CATALOG_LIMIT + 5):
        _make_recipe(db_session, f"Recipe {i:03d}", ["water"])

    context = chat_service.build_chat_context(db_session, query="chicken")
    prompt = chat_service.build_chat_system_prompt("BASE", context)

    assert "PARTIAL" in prompt
    assert f"of {chat_service.RECIPE_CATALOG_LIMIT + 5}" in prompt


def test_prompt_says_nothing_about_partiality_when_the_catalog_is_complete(db_session):
    _make_recipe(db_session, "Only Recipe", ["water"])

    prompt = chat_service.build_chat_system_prompt(
        "BASE", chat_service.build_chat_context(db_session, query="chicken")
    )

    assert "PARTIAL" not in prompt


def test_retrieved_ingredients_reach_the_generated_prompt_text(db_session):
    """Asserts against the literal prompt string, not just the context
    dict -- the same discipline the health/knowledge grounding work used,
    because a context key nothing interpolates is grounding that never
    happened."""
    _make_recipe(db_session, "Zucchini Boats", ["italian sausage", "zucchini", "parmesan"])

    context = chat_service.build_chat_context(db_session, query="what can I do with the sausage")
    prompt = chat_service.build_chat_system_prompt("BASE", context)

    assert "Zucchini Boats" in prompt
    assert "italian sausage" in prompt
    assert "parmesan" in prompt


def test_no_relevant_recipe_block_when_retrieval_finds_nothing(db_session):
    _make_recipe(db_session, "Chicken Soup", ["chicken"])

    prompt = chat_service.build_chat_system_prompt(
        "BASE", chat_service.build_chat_context(db_session, query="hello")
    )

    assert "look relevant to THIS message" not in prompt
