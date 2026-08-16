"""A URL that failed is not fetched again next batch.

`already_imported_urls` only knew about SAVED recipes, so a 404, a 403, a
dead domain or a page with no ingredients left no trace and came back in
every future batch. Measured on a real 478-URL export: batch 1 attempted
40 and saved 21; batch 2 attempted 40 and saved 8, because roughly
nineteen of its attempts were batch 1's failures fetched again.

The compounding is the part that matters. Dead URLs sit EARLIER in the
file than the unreached ones, so every later batch would have been more
of them and less new work -- the run gets slower and less productive the
longer it goes, which is the opposite of what a resumable importer is
for.
"""

import pytest

from app.models import ImportSkip, Recipe
from app.services import bookmark_import_service as bis

GOOD = {
    "title": "Pie",
    "ingredients": [
        {"ingredient_name": "flour", "quantity": 1, "unit": "cup"},
        {"ingredient_name": "sugar", "quantity": 2, "unit": "tbsp"},
    ],
    "instructions": [{"text": "Bake it.", "component": "main"}],
}


def _bookmarks(urls):
    return [bis.Bookmark(url=u, title=u, folder_path="Recipes") for u in urls]


@pytest.fixture
def outcomes(monkeypatch):
    """Maps a URL to what parse_recipe_from_url should do: raise a
    RuntimeError with the given text, or return a recipe."""
    plan = {}
    attempted = []

    def fake_parse(_db, url):
        attempted.append(url)
        behaviour = plan.get(url, "ok")
        if behaviour != "ok":
            raise RuntimeError(behaviour)
        return {
            "raw_output": "",
            "default_source": "import_url_jsonld",
            "citation": {"source_url": url},
            "image_path": None,
            "jsonld_parsed": dict(GOOD),
            "source_text": None,
        }

    monkeypatch.setattr(bis.recipe_service, "parse_recipe_from_url", fake_parse)
    return plan, attempted


# --- what counts as permanent --------------------------------------------


@pytest.mark.parametrize(
    "reason",
    [
        "Could not download https://x/y: the site answered HTTP 404.",
        "Could not download https://x/y: the site answered HTTP 403. That is a refusal rather than a missing page",
        "Could not extract a recipe from that input",
        "No ingredients found -- this is probably an index or category page, not a recipe.",
        "One ingredient and no instructions -- this is probably a product page, not a recipe.",
    ],
)
def test_a_permanent_failure_is_remembered(db_session, reason):
    bis.record_failure(db_session, "https://example.com/x", reason)
    row = db_session.query(ImportSkip).one()
    assert row.permanent is True
    assert bis.known_bad_urls(db_session, ["https://example.com/x"]) == {"https://example.com/x"}


@pytest.mark.parametrize(
    "reason",
    [
        # A dead 2011 domain and this evening's flaky wifi produce the
        # same message, so this one has to stay retryable.
        "Could not download https://x/y: no response at all -- DNS failure, refused connection, TLS error",
        "Ollama did not respond within the configured timeout while running chat.",
        "Could not download https://x/y: the site answered HTTP 503.",
    ],
)
def test_a_transient_failure_is_recorded_but_still_retried(db_session, reason):
    bis.record_failure(db_session, "https://example.com/x", reason)
    assert db_session.query(ImportSkip).one().permanent is False
    assert bis.known_bad_urls(db_session, ["https://example.com/x"]) == set()


def test_a_repeat_failure_counts_up(db_session):
    for _ in range(3):
        bis.record_failure(db_session, "https://example.com/x", "no response at all")
    assert db_session.query(ImportSkip).one().attempts == 3


def test_a_classification_only_ever_hardens(db_session):
    """A page that 404s after a timeout has told us something the timeout
    did not."""
    bis.record_failure(db_session, "https://example.com/x", "no response at all")
    bis.record_failure(db_session, "https://example.com/x", "the site answered HTTP 404.")
    assert db_session.query(ImportSkip).one().permanent is True


def test_the_key_is_normalised_like_the_imported_check(db_session):
    """Both halves must agree about what "the same page" is, or a URL
    could be skipped as imported and retried as failed."""
    bis.record_failure(db_session, "http://www.example.com/x/", "HTTP 404")
    assert bis.known_bad_urls(db_session, ["https://example.com/x?utm_source=a"]) != set()


# --- the loop -------------------------------------------------------------


def test_a_second_batch_does_not_re_fetch_the_first_batch_failures(db_session, outcomes):
    """The measured behaviour, in miniature."""
    plan, attempted = outcomes
    urls = [f"https://example.com/r{i}" for i in range(6)]
    plan[urls[1]] = "Could not download: the site answered HTTP 404."
    plan[urls[3]] = "Could not extract a recipe from that input"

    first = bis.scan_and_parse(db_session, _bookmarks(urls), limit=6)
    assert len(attempted) == 6
    assert sum(1 for i in first["items"] if i["status"] == "ok") == 4

    attempted.clear()
    second = bis.scan_and_parse(db_session, _bookmarks(urls), limit=6)
    assert urls[1] not in attempted, "re-fetched a URL that already 404'd"
    assert urls[3] not in attempted
    assert second["known_bad"] == 2


def test_retry_failed_overrides_it(db_session, outcomes):
    """A permanent classification can be wrong, and the household should
    be able to say so without editing the database."""
    plan, attempted = outcomes
    url = "https://example.com/r0"
    plan[url] = "Could not download: the site answered HTTP 404."
    bis.scan_and_parse(db_session, _bookmarks([url]), limit=6)

    attempted.clear()
    bis.scan_and_parse(db_session, _bookmarks([url]), limit=6, retry_failed=True)
    assert attempted == [url]


def test_a_saved_recipe_is_not_recorded_as_a_failure(db_session, outcomes):
    bis.scan_and_parse(db_session, _bookmarks(["https://example.com/ok"]), limit=6)
    assert db_session.query(ImportSkip).count() == 0


def test_the_skip_does_not_survive_deleting_a_recipe(db_session, outcomes):
    """Deleting a recipe is a request to import the page again -- it must
    not leave a skip behind, or "clear the batch and retry" stops
    working."""
    url = "https://example.com/ok"
    bis.scan_and_parse(db_session, _bookmarks([url]), limit=6)
    db_session.add(Recipe(title="Pie", source_url=url, instructions=[], nutrition={}))
    db_session.commit()

    db_session.query(Recipe).delete()
    db_session.commit()
    assert bis.known_bad_urls(db_session, [url]) == set()


# --- the batch size is a setting -----------------------------------------


def test_the_default_is_forty(db_session):
    assert bis.configured_batch_size(db_session) == bis.MAX_URLS_PER_SCAN == 40


def test_a_household_can_raise_it(db_session):
    from app.services import settings_service

    settings_service.set_setting(db_session, "bookmark_scan_batch_size", "150")
    assert bis.configured_batch_size(db_session) == 150


@pytest.mark.parametrize("value", ["", "   ", "lots", "0", "-5", None])
def test_nonsense_falls_back_to_forty_not_to_unlimited(db_session, value):
    """A setting that silently means "unlimited" when someone clears the
    box is a trap, and an uncapped import is the accident the cap was
    added for."""
    from app.services import settings_service

    settings_service.set_setting(db_session, "bookmark_scan_batch_size", value)
    assert bis.configured_batch_size(db_session) == 40


def test_it_is_clamped(db_session):
    """The setting is for clearing a large export in fewer runs. It is not
    for turning one click into a thousand fetches."""
    from app.services import settings_service

    settings_service.set_setting(db_session, "bookmark_scan_batch_size", "99999")
    assert bis.configured_batch_size(db_session) == bis.MAX_SCAN_BATCH_CEILING


def test_the_scan_honours_it(db_session, outcomes, monkeypatch):
    from app.services import settings_service

    settings_service.set_setting(db_session, "bookmark_scan_batch_size", "3")
    urls = [f"https://example.com/r{i}" for i in range(10)]
    result = bis.scan_and_parse(db_session, _bookmarks(urls))
    assert len(result["items"]) == 3
    assert result["remaining"] == 7
