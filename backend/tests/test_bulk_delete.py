"""Deleting a batch of recipes in one action.

Added because clearing a bad import meant opening thirty recipe pages and
pressing Delete on each. A bulk import needs a bulk undo -- the household
reviews forty rows at a time, and the first thing they want after a bad
batch is all of it gone.
"""

import pytest
from pydantic import ValidationError

from app.models import Recipe
from app.routers.recipes import bulk_delete_recipes
from app.schemas.recipe import BulkDeleteRequest
from app.services import bookmark_import_service as bis


def _make(db, title, source_url=None):
    recipe = Recipe(title=title, source_url=source_url, instructions=[], nutrition={})
    db.add(recipe)
    db.commit()
    return recipe.id


def test_deletes_every_id_given(db_session):
    ids = [_make(db_session, f"R{i}") for i in range(4)]
    result = bulk_delete_recipes(BulkDeleteRequest(ids=ids), db_session)
    assert result.deleted == 4
    assert result.missing == []
    assert db_session.query(Recipe).count() == 0


def test_leaves_everything_else_alone(db_session):
    doomed = [_make(db_session, "gone one"), _make(db_session, "gone two")]
    kept = _make(db_session, "keep me")
    bulk_delete_recipes(BulkDeleteRequest(ids=doomed), db_session)
    assert [r.id for r in db_session.query(Recipe).all()] == [kept]


def test_a_stale_id_is_reported_not_raised(db_session):
    """A list built from a page the household was looking at can easily
    name a recipe deleted in another tab. Failing the whole call over one
    stale id would throw away every legitimate deletion in it."""
    alive = _make(db_session, "alive")
    result = bulk_delete_recipes(BulkDeleteRequest(ids=[alive, 999_999]), db_session)
    assert result.deleted == 1
    assert result.missing == [999_999]
    assert db_session.query(Recipe).count() == 0


def test_a_repeated_id_counts_once(db_session):
    alive = _make(db_session, "alive")
    assert bulk_delete_recipes(BulkDeleteRequest(ids=[alive, alive]), db_session).deleted == 1


def test_an_empty_list_is_refused():
    """Nothing good comes of a delete call that names nothing."""
    with pytest.raises(ValidationError):
        BulkDeleteRequest(ids=[])


def test_the_list_is_bounded():
    """This is the most destructive call in the API, and an unbounded list
    is how a typo becomes a catalog."""
    with pytest.raises(ValidationError):
        BulkDeleteRequest(ids=list(range(501)))


def test_the_urls_become_importable_again(db_session):
    """Deliberate, and the reason this is NOT a way to reject a URL:
    already_imported_urls compares against Recipe.source_url, so a deleted
    recipe reappears in the next scan of the same bookmarks file. That is
    exactly what makes "clear the batch and try again" work, and exactly
    why a permanent skip list has to be a separate thing."""
    url = "https://example.com/pie"
    recipe_id = _make(db_session, "Pie", source_url=url)
    assert bis.already_imported_urls(db_session, [url]) == {url}

    bulk_delete_recipes(BulkDeleteRequest(ids=[recipe_id]), db_session)
    assert bis.already_imported_urls(db_session, [url]) == set()
