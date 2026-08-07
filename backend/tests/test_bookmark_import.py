"""Bulk import from a browser's exported bookmarks file.

The Netscape bookmark format is not valid HTML -- its `<p>` tags are never
closed and its `<DT>` elements do not nest the way the indentation
suggests -- so the folder structure has to be recovered from the tree a
lenient parser builds, not from the whitespace. The fixture below is a
real-shaped export: a toolbar folder, a nested Recipes folder with two
subfolders, a duplicate, a non-recipe bookmark and a `javascript:`
bookmarklet.
"""

from unittest.mock import patch

import pytest

from app.services import bookmark_import_service as bookmarks

EXPORT = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<META HTTP-EQUIV="Content-Type" CONTENT="text/html; charset=UTF-8">
<TITLE>Bookmarks</TITLE>
<H1>Bookmarks</H1>
<DL><p>
    <DT><H3 PERSONAL_TOOLBAR_FOLDER="true">Bookmarks bar</H3>
    <DL><p>
        <DT><A HREF="https://news.example/today">The News</A>
        <DT><H3>Recipes</H3>
        <DL><p>
            <DT><A HREF="https://example.com/pie">Pumpkin Chiffon Pie</A>
            <DT><H3>Desserts</H3>
            <DL><p>
                <DT><A HREF="https://example.com/brownies">Brownies</A>
                <DT><A HREF="https://example.com/brownies">Brownies again</A>
            </DL><p>
        </DL><p>
        <DT><A HREF="javascript:void(0)">A bookmarklet</A>
    </DL><p>
</DL><p>
"""


def test_folder_paths_come_from_the_tree_not_the_indentation():
    found = bookmarks.parse_bookmarks(EXPORT)
    assert {b.url: b.folder_path for b in found} == {
        "https://news.example/today": "Bookmarks bar",
        "https://example.com/pie": "Bookmarks bar/Recipes",
        "https://example.com/brownies": "Bookmarks bar/Recipes/Desserts",
    }


def test_a_url_bookmarked_twice_is_one_recipe():
    """The same recipe filed in two folders would otherwise import as two
    rows the household then merges by hand."""
    found = bookmarks.parse_bookmarks(EXPORT)
    assert [b.url for b in found].count("https://example.com/brownies") == 1
    assert next(b for b in found if "brownies" in b.url).title == "Brownies"


def test_bookmarklets_and_non_http_schemes_are_not_recipes():
    assert all(b.url.startswith(("http://", "https://")) for b in bookmarks.parse_bookmarks(EXPORT))


def test_only_folders_that_hold_bookmarks_are_offered():
    """A parent containing nothing but other folders is a tree node, not
    a choice."""
    summary = bookmarks.folder_summary(bookmarks.parse_bookmarks(EXPORT))
    assert summary == [
        {"path": "Bookmarks bar", "count": 1},
        {"path": "Bookmarks bar/Recipes", "count": 1},
        {"path": "Bookmarks bar/Recipes/Desserts", "count": 1},
    ]


def test_choosing_a_folder_includes_its_subfolders():
    """Picking `Recipes` and silently missing `Recipes/Desserts` is not
    what anyone means by picking a folder."""
    found = bookmarks.parse_bookmarks(EXPORT)
    assert {b.url for b in bookmarks.select(found, "Bookmarks bar/Recipes")} == {
        "https://example.com/pie",
        "https://example.com/brownies",
    }
    assert {b.url for b in bookmarks.select(found, "Bookmarks bar/Recipes/Desserts")} == {
        "https://example.com/brownies"
    }
    assert len(bookmarks.select(found, None)) == 3


def test_an_empty_or_junk_file_yields_nothing_rather_than_raising():
    assert bookmarks.parse_bookmarks("") == []
    assert bookmarks.parse_bookmarks("   ") == []
    assert bookmarks.parse_bookmarks("not html at all") == []


def _bookmark(url, folder=""):
    return bookmarks.Bookmark(url=url, title="T", folder_path=folder)


def test_one_dead_url_does_not_cost_the_household_the_other_imports(db_session):
    """A bookmarks folder holds shops, videos and dead links. A per-item
    failure is the normal case here, not an exception."""

    def fake_parse(_db, url):
        if "dead" in url:
            raise RuntimeError("Could not fetch that URL: 404")
        return {
            "raw_output": "{}",
            "default_source": "import_url_jsonld",
            "citation": {"source_url": url},
            "image_path": None,
            # A real ingredient, not a placeholder: an ingredient-less
            # parse now reports `empty` (an index or category page is
            # not a recipe), and this test is about dead URLs.
            "jsonld_parsed": {
                "title": "Pie",
                "ingredients": [{"ingredient_name": "flour", "quantity": 1, "unit": "cup"}],
                "instructions": [],
            },
            "source_text": None,
        }

    with patch.object(bookmarks.recipe_service, "parse_recipe_from_url", side_effect=fake_parse):
        result = bookmarks.scan_and_parse(
            db_session, [_bookmark("https://a/ok"), _bookmark("https://a/dead"), _bookmark("https://b/ok")]
        )

    assert [i["status"] for i in result["items"]] == ["ok", "error", "ok"]
    assert "404" in result["items"][1]["error"]
    assert result["items"][1]["recipe"] is None


def test_the_folder_name_becomes_a_tag_so_it_survives_the_import(db_session):
    def fake_parse(_db, url):
        return {
            "raw_output": "{}",
            "default_source": "import_url_jsonld",
            "citation": {"source_url": url},
            "image_path": None,
            "jsonld_parsed": {"title": "Pie", "ingredients": [], "instructions": [], "tags": ["quick"]},
            "source_text": None,
        }

    with patch.object(bookmarks.recipe_service, "parse_recipe_from_url", side_effect=fake_parse):
        result = bookmarks.scan_and_parse(db_session, [_bookmark("https://a/pie", "Recipes/Sunday Dinners")])

    assert "sunday_dinners" in result["items"][0]["recipe"]["tags"]
    assert "quick" in result["items"][0]["recipe"]["tags"]


def test_the_per_scan_cap_reports_what_it_skipped_rather_than_dropping_it(db_session):
    with patch.object(bookmarks.recipe_service, "parse_recipe_from_url", side_effect=RuntimeError("no")):
        result = bookmarks.scan_and_parse(db_session, [_bookmark(f"https://a/{i}") for i in range(5)], limit=2)

    assert len(result["items"]) == 2
    assert result["truncated"] is True
    assert [row[1] for row in result["skipped"]] == ["over the per-scan limit"] * 3


def test_the_import_preview_never_carries_internal_keys_into_a_saved_recipe(db_session):
    """`_ingredient_provenance` and `_instruction_warnings` are about the
    import, not the recipe. The single-import path pops them in the
    router; this path has no router to do it."""

    def fake_parse(_db, url):
        return {
            "raw_output": "{}",
            "default_source": "import_url_jsonld",
            "citation": {"source_url": url},
            "image_path": None,
            "jsonld_parsed": {"title": "Pie", "ingredients": [], "instructions": []},
            "source_text": None,
        }

    with patch.object(bookmarks.recipe_service, "parse_recipe_from_url", side_effect=fake_parse):
        result = bookmarks.scan_and_parse(db_session, [_bookmark("https://a/pie")])

    recipe = result["items"][0]["recipe"]
    assert not any(key.startswith("_") for key in recipe)
