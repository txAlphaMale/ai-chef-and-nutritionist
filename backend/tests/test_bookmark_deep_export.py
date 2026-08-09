"""A real bookmarks export is deeper than libxml2 will parse.

Found 2026-08-07, on the author's own export, by comparing what the UI
reported against what the file contains:

    860 anchors and 6 folders in the file
    250 anchors and 2 folders reached by the parser
    max element depth exactly 255

libxml2 refuses to nest deeper than 256 elements. This format's `<DT>`
tags are never closed, so a recovering parser nests EVERY bookmark one
level deeper than the last and walks straight into that wall. Past it,
parsing stops -- no exception, no warning, and the UI reported the
truncated count as though it were the file's contents. More than half the
household's bookmarks were silently absent and two entire folders had
vanished.

**Every existing test passed.** The hand-written fixture and the 20-URL
trial file were far too shallow to reach depth 256. So the fixture here is
generated deep on purpose: a test that cannot reach the limit cannot
guard the limit.
"""

import lxml.html
import pytest

from app.services import bookmark_import_service as bis

# Comfortably past libxml2's 256-element ceiling. 249 is roughly where the
# author's real file died, so a fixture at 400 fails loudly on a
# regression rather than marginally.
DEEP = 400


def _export(count, folder="Recipes"):
    """A Netscape bookmark file shaped like a browser's -- unclosed <p>,
    unclosed <DT>, which is what produces the runaway nesting."""
    rows = "\n".join(
        f'        <DT><A HREF="https://example.com/r{i}" ADD_DATE="1300000000">Recipe {i}</A>' for i in range(count)
    )
    return (
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n"
        "<TITLE>Bookmarks</TITLE>\n<H1>Bookmarks</H1>\n"
        "<DL><p>\n"
        "    <DT><H3>Bookmarks bar</H3>\n"
        "    <DL><p>\n"
        f"        <DT><H3>{folder}</H3>\n"
        "        <DL><p>\n"
        f"{rows}\n"
        "        </DL><p>\n"
        "    </DL><p>\n"
        "</DL><p>\n"
    )


def test_the_fixture_really_is_deep_enough_to_trip_the_limit():
    """Guards the guard. If a future lxml stops nesting this way, this
    test file quietly stops testing anything, and the assertion below
    would pass for the wrong reason."""
    tree = lxml.html.fromstring(_export(DEEP))
    truncated = len(list(tree.iter("a")))
    assert truncated < DEEP, (
        f"the default parser read all {DEEP} anchors, so this fixture no longer reproduces the defect"
    )


def test_every_bookmark_in_a_deep_export_is_parsed():
    bookmarks = bis.parse_bookmarks(_export(DEEP))
    assert len(bookmarks) == DEEP


def test_folders_survive_a_deep_export():
    """The folders went missing along with the bookmarks -- the <H3>s were
    past the truncation point too, so everything left collapsed into one
    unnamed path."""
    bookmarks = bis.parse_bookmarks(_export(DEEP, folder="Desserts"))
    assert {b.folder_path for b in bookmarks} == {"Bookmarks bar/Desserts"}


def test_a_deep_export_still_selects_by_folder():
    bookmarks = bis.parse_bookmarks(_export(DEEP, folder="Recipes"))
    assert len(bis.select(bookmarks, "Bookmarks bar/Recipes")) == DEEP
    assert bis.select(bookmarks, "Bookmarks bar/Nope") == []


# --- in-file duplicates are compared the same way saved ones are ----------


def test_two_bookmarks_of_one_page_collapse():
    """Measured on the real export: the same Mountain Rose Herbs page
    appears twice, identical but for a `#gel` fragment, and BOTH were
    imported and both arrived pre-ticked -- two copies of one recipe to
    merge by hand, which is what this function's docstring promises not to
    do. `already_imported_urls` compared normalized URLs across runs; the
    in-file check compared raw ones, so a duplicate survived inside a
    single run and not between two."""
    html = (
        "<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<DL><p>\n"
        "    <DT><H3>Recipes</H3>\n    <DL><p>\n"
        '        <DT><A HREF="https://blog.example.com/energy-gel?utm_source=news">Gel</A>\n'
        '        <DT><A HREF="https://blog.example.com/energy-gel?utm_source=news#gel">Gel again</A>\n'
        '        <DT><A HREF="http://www.blog.example.com/energy-gel/">Gel, old bookmark</A>\n'
        '        <DT><A HREF="https://blog.example.com/energy-bars">Bars</A>\n'
        "    </DL><p>\n</DL><p>\n"
    )
    bookmarks = bis.parse_bookmarks(html)
    assert len(bookmarks) == 2, [b.url for b in bookmarks]
    # First occurrence wins, and it keeps its ORIGINAL url -- the
    # normalized form is a comparison key, never what gets fetched.
    assert bookmarks[0].url == "https://blog.example.com/energy-gel?utm_source=news"


@pytest.mark.parametrize("scheme", ["javascript:void(0)", "data:text/html,x", "place:sort=8"])
def test_non_http_schemes_are_still_dropped(scheme):
    html = f'<DL><p><DT><H3>R</H3><DL><p><DT><A HREF="{scheme}">x</A></DL><p></DL><p>'
    assert bis.parse_bookmarks(html) == []


# --- the folder tag has to describe the recipe, not the app --------------


def test_a_descriptive_folder_becomes_a_tag(db_session, monkeypatch):
    """`Desserts` is why the folder tag exists at all -- a dessert stays
    findable as one after the import."""
    _stub_parse(monkeypatch)
    bookmarks = [bis.Bookmark(url="https://example.com/a", title="A", folder_path="Bookmarks bar/Desserts")]
    result = bis.scan_and_parse(db_session, bookmarks)
    assert "desserts" in result["items"][0]["recipe"]["tags"]


@pytest.mark.parametrize("folder", ["Recipes", "recipes", "Cooking", "Food", "Bookmarks bar", "To Try"])
def test_a_folder_that_only_says_this_is_a_recipe_does_not(db_session, monkeypatch, folder):
    """Measured on the author's first real import: all 21 saved recipes
    carried `recipes`, and it sorted to the TOP of the tag filter because
    that panel ranks by count. A tag every row carries is not a facet."""
    _stub_parse(monkeypatch)
    bookmarks = [bis.Bookmark(url="https://example.com/a", title="A", folder_path=f"Bookmarks bar/{folder}")]
    result = bis.scan_and_parse(db_session, bookmarks)
    assert result["items"][0]["recipe"]["tags"] == ["quick"], "an uninformative folder tag was added"


def _stub_parse(monkeypatch):
    def fake_parse(_db, url):
        return {
            "raw_output": "",
            "default_source": "import_url_jsonld",
            "citation": {"source_url": url},
            "image_path": None,
            "jsonld_parsed": {
                "title": "Thing",
                "ingredients": [{"ingredient_name": "flour", "quantity": 1, "unit": "cup"}],
                "instructions": [],
                "tags": ["quick"],
            },
            "source_text": None,
        }

    monkeypatch.setattr(bis.recipe_service, "parse_recipe_from_url", fake_parse)
