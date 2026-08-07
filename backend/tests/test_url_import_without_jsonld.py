"""The trafilatura path, finally exercised.

The plan carried "trafilatura is untested" as an open item across four
attempts. It could not be tested with an ordinary saved recipe page,
because every real one publishes schema.org JSON-LD and the importer
prefers that -- so the fallback never ran, and nobody knew whether it
worked.

`no_jsonld_recipe_page.html` is CONSTRUCTED, and says so: a recipe page
with navigation, an ad, a long personal preamble, a footer, and no
JSON-LD anywhere. It is not a real page and does not prove behaviour on
a real messy blog. It does prove the path runs, strips the boilerplate,
keeps the recipe, and captures the citation metadata -- which is all
that was previously unknown.

Running it found a live defect on the first try. See
test_a_bulleted_list_still_gets_its_amounts_read below and _LIST_BULLET
in recipe_service: trafilatura renders every `<li>` as `- 1 cup flour`,
which _QTY_RE could not read past, so EVERY ingredient from EVERY
non-JSON-LD URL import arrived with a null quantity and the amount welded
into its name -- and the copied lines verified perfectly while it
happened.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app.services import recipe_service

PAGE = (Path(__file__).parent / "fixtures" / "no_jsonld_recipe_page.html").read_text(encoding="utf-8")
URL = "https://therustywhisk.example/cornbread"


def test_the_page_really_has_no_structured_data():
    """The control. If this ever starts returning a recipe, the fixture
    grew a JSON-LD block and every assertion below is testing the OTHER
    import path."""
    assert recipe_service.extract_jsonld_recipe(PAGE) is None


def test_boilerplate_is_stripped_and_the_recipe_is_kept():
    text = recipe_service.extract_content_from_html(PAGE, url=URL)["text"]

    assert "1 cup stone-ground cornmeal" in text
    assert "bake 22 minutes" in text
    assert "Buy our cast iron seasoning kit" not in text
    assert "Subscribe to our newsletter" not in text
    assert "Home" not in text.splitlines()[:3]


def test_the_citation_metadata_the_copyright_rule_depends_on_is_captured():
    """Rule 8 requires attribution rather than reproduction, so these
    three fields are not decoration."""
    result = recipe_service.extract_content_from_html(PAGE, url=URL)
    assert result["sitename"] == "The Rusty Whisk"
    assert result["author"] == "Marguerite Deane"
    assert result["image"] == "https://therustywhisk.example/img/cornbread.jpg"


@contextmanager
def _stub_pass1(lines):
    raw = json.dumps({"blocks": [{"component": "main", "lines": lines}]})
    with (
        patch.object(recipe_service, "get_ingredient_lines_prompt", return_value="{content}"),
        patch.object(recipe_service.ollama_client, "content_char_budget", return_value=100_000),
        patch.object(recipe_service.ollama_client, "get_extraction_model", return_value="stub"),
        patch.object(recipe_service.ollama_client, "chat_json_with_reason", return_value=(raw, "stop")),
    ):
        yield


def test_a_bulleted_page_imports_with_real_amounts_end_to_end():
    """The defect this file found, from HTML through to stored rows.

    Before the bullet fix this produced seven nulls named `1 cup
    stone-ground cornmeal` and friends -- the amount swallowed into the
    join key, on every URL import that was not JSON-LD."""
    text = recipe_service.extract_content_from_html(PAGE, url=URL)["text"]
    copied = [
        line
        for line in text.splitlines()
        if line.startswith("- ")
        and "oven" not in line
        and "Whisk" not in line
        and "Beat" not in line
        and "batter" not in line
    ]
    assert len(copied) == 7, copied

    with _stub_pass1(copied):
        got = recipe_service.extract_ingredients_two_pass(None, text)

    assert [(i["quantity"], i["unit"], i["ingredient_name"]) for i in got] == [
        (1.0, "cup", "stone-ground cornmeal"),
        (1.0, "cup", "all-purpose flour"),
        (2.0, "tsp", "baking powder"),
        (1.0, "tsp", "kosher salt"),
        (1.0, "cup", "buttermilk"),
        (2.0, "large", "eggs"),
        (6.0, "tbsp", "unsalted butter"),
    ]
    assert all(i["quantity"] is not None for i in got)
