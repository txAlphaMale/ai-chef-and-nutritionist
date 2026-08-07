"""Re-uploading the same bookmarks export continues where the last run
stopped, instead of re-attempting the same first forty forever.

Written against a real 565-URL export the household actually has. At a
40-URL cap that is fourteen runs, and before this the fourteenth run
would have been identical to the first -- same GPU minutes, and a second
copy of every recipe on confirm, because nothing checked whether a URL
had already been imported.
"""

import pytest

from app.models import Recipe
from app.services import bookmark_import_service as bis

# --- normalize_url is a comparison key, not a rewriter ---------------------


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # The pair that motivated folding the scheme: a bookmark saved in
        # 2013 and the same page today.
        ("http://example.com/pie", "https://example.com/pie"),
        ("https://www.example.com/pie", "https://example.com/pie"),
        ("https://EXAMPLE.com/pie", "https://example.com/pie"),
        ("https://example.com/pie/", "https://example.com/pie"),
        ("https://example.com/pie#ingredients", "https://example.com/pie"),
        ("https://example.com/pie?utm_source=newsletter", "https://example.com/pie"),
        ("https://example.com/pie?utm_medium=email&fbclid=abc", "https://example.com/pie"),
        ("https://example.com:443/pie", "https://example.com/pie"),
        # Query order is not identity.
        ("https://example.com/r?b=2&a=1", "https://example.com/r?a=1&b=2"),
    ],
)
def test_these_are_the_same_page(a, b):
    assert bis.normalize_url(a) == bis.normalize_url(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        # `?recipe=204` IS the page on plenty of older blogs -- dropping
        # the query would collapse a whole site into a single entry.
        ("https://example.com/?recipe=204", "https://example.com/?recipe=205"),
        ("https://example.com/pie", "https://example.com/cake"),
        ("https://example.com/pie", "https://other.com/pie"),
        # A subdomain is a different site, and this export has several.
        ("https://blog.example.com/pie", "https://example.com/pie"),
    ],
)
def test_these_are_different_pages(a, b):
    assert bis.normalize_url(a) != bis.normalize_url(b)


def test_normalize_url_survives_junk():
    for junk in ["", "   ", "not a url", "javascript:void(0)"]:
        bis.normalize_url(junk)  # must not raise


# --- matching against what is already saved -------------------------------


def _save(db, title, source_url):
    db.add(Recipe(title=title, source_url=source_url, instructions=[], nutrition={}))
    db.commit()


def test_already_imported_matches_cosmetic_differences(db_session):
    _save(db_session, "Pie", "https://www.example.com/pie/")
    found = bis.already_imported_urls(db_session, ["http://example.com/pie?utm_source=x", "https://example.com/cake"])
    assert found == {"http://example.com/pie?utm_source=x"}


def test_already_imported_ignores_recipes_with_no_source(db_session):
    _save(db_session, "Typed by hand", None)
    assert bis.already_imported_urls(db_session, ["https://example.com/pie"]) == set()


def test_already_imported_on_an_empty_catalog(db_session):
    assert bis.already_imported_urls(db_session, ["https://example.com/pie"]) == set()


# --- the resume loop ------------------------------------------------------


def _bookmarks(n, start=0):
    return [
        bis.Bookmark(url=f"https://example.com/r{i}", title=f"R{i}", folder_path="Recipes")
        for i in range(start, start + n)
    ]


@pytest.fixture
def no_network(monkeypatch):
    """Every URL 'imports' to a trivial recipe -- this suite is about which
    URLs are attempted, not about parsing."""
    attempted = []

    def fake_parse(db, url):
        attempted.append(url)
        return {
            "raw_output": "",
            "default_source": "import_url",
            "citation": {"source_url": url, "source_name": None, "source_author": None},
            "image_path": None,
            "jsonld_parsed": {"title": f"Recipe for {url}"},
            "source_text": None,
        }

    def fake_finish(*_args, **_kwargs):
        return {"title": "A recipe", "ingredients": [], "instructions": [], "tags": []}

    monkeypatch.setattr(bis.recipe_service, "parse_recipe_from_url", fake_parse)
    monkeypatch.setattr(bis.recipe_service, "finish_recipe_parse", fake_finish)
    return attempted


def test_a_second_run_takes_the_NEXT_batch(db_session, no_network):
    """The whole point. Run one imports 40; those get saved; run two must
    attempt 41-80, not 1-40 again."""
    bookmarks = _bookmarks(100)

    first = bis.scan_and_parse(db_session, bookmarks, limit=40)
    assert len(first["items"]) == 40
    assert first["already_imported"] == 0
    assert first["remaining"] == 60
    assert no_network[:1] == ["https://example.com/r0"]

    # The household confirms the first batch.
    for i in range(40):
        _save(db_session, f"R{i}", f"https://example.com/r{i}")

    no_network.clear()
    second = bis.scan_and_parse(db_session, bookmarks, limit=40)
    assert second["already_imported"] == 40
    assert second["remaining"] == 20
    assert [it["url"] for it in second["items"]] == [f"https://example.com/r{i}" for i in range(40, 80)]
    assert "https://example.com/r0" not in no_network, "re-fetched a URL already imported"


def test_the_cap_is_applied_AFTER_the_already_imported_filter(db_session, no_network):
    """If the order were reversed, run two would spend its whole budget
    rediscovering that run one was done and import nothing."""
    bookmarks = _bookmarks(50)
    for i in range(40):
        _save(db_session, f"R{i}", f"https://example.com/r{i}")

    result = bis.scan_and_parse(db_session, bookmarks, limit=40)
    assert len(result["items"]) == 10, "the cap ate slots on already-imported URLs"
    assert result["already_imported"] == 40
    assert result["remaining"] == 0
    assert result["truncated"] is False


def test_a_fully_imported_file_attempts_nothing(db_session, no_network):
    bookmarks = _bookmarks(5)
    for i in range(5):
        _save(db_session, f"R{i}", f"https://example.com/r{i}")

    result = bis.scan_and_parse(db_session, bookmarks, limit=40)
    assert result["items"] == []
    assert result["already_imported"] == 5
    assert result["remaining"] == 0
    assert no_network == [], "spent a fetch on a file with nothing new in it"


def test_remaining_and_skipped_agree(db_session, no_network):
    result = bis.scan_and_parse(db_session, _bookmarks(65), limit=40)
    assert result["remaining"] == 25
    assert len(result["skipped"]) == 25
    assert result["truncated"] is True
    assert all(reason == "over the per-scan limit" for _url, reason in result["skipped"])


def test_a_failed_item_is_not_treated_as_imported(db_session, monkeypatch):
    """A 404 leaves no recipe row, so the next run must try it again --
    otherwise a transient failure silently drops a bookmark forever."""

    def boom(_db, _url):
        raise RuntimeError("Could not fetch that URL: 404")

    monkeypatch.setattr(bis.recipe_service, "parse_recipe_from_url", boom)
    bookmarks = _bookmarks(3)

    first = bis.scan_and_parse(db_session, bookmarks, limit=40)
    assert all(it["status"] == "error" for it in first["items"])

    second = bis.scan_and_parse(db_session, bookmarks, limit=40)
    assert len(second["items"]) == 3
    assert second["already_imported"] == 0
