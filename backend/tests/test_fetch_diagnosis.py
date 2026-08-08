"""Why a URL fetch failed, instead of one sentence for every cause.

The first real bookmarks run (2026-08-07) failed on 6 of 20 URLs, and all
six said "Could not download content from X" -- because
`trafilatura.fetch_url` returns None and nothing else. A dead 2011 link
and a live site refusing a non-browser client are indistinguishable in
that message, and they call for opposite responses: one is attrition to
accept, the other is a decision about how this app identifies itself.
Across 565 bookmarks, guessing between them is not worth doing.

No network here. `fetch_response` is stubbed, which is the point -- these
tests pin the WORDING and the branching, and the wording is the whole
deliverable.
"""

from unittest.mock import patch

import pytest

from app.services import recipe_service as rs


class _Response:
    """The shape trafilatura.fetch_response returns."""

    def __init__(self, status, html=""):
        self.status = status
        self.html = html
        self.data = html.encode() if html else b""


def _diagnose(response=None, raises=None):
    target = "app.services.recipe_service.trafilatura.fetch_response"
    if raises is not None:
        with patch(target, side_effect=raises):
            return rs._diagnose_failed_fetch("https://example.com/x")
    with patch(target, return_value=response):
        return rs._diagnose_failed_fetch("https://example.com/x")


def test_no_response_reads_as_a_dead_link():
    message = _diagnose(response=None)
    assert "no response at all" in message
    assert "dead link" in message


@pytest.mark.parametrize("status", [401, 403, 429, 451])
def test_a_refusal_is_named_as_a_refusal(status):
    """403 is the case that matters. thespruce.com is a live site with the
    recipe still on it; calling that "could not download" sends someone
    looking for a broken link that is not broken."""
    message = _diagnose(_Response(status))
    assert f"HTTP {status}" in message
    assert "refusal" in message
    assert "rejecting this client" in message


@pytest.mark.parametrize("status", [404, 410, 500, 503])
def test_an_ordinary_error_is_just_reported(status):
    message = _diagnose(_Response(status))
    assert f"HTTP {status}" in message
    assert "refusal" not in message


def test_a_body_too_small_says_so():
    """trafilatura rejects on size AFTER a successful 200, which otherwise
    looks identical to every other failure."""
    message = _diagnose(_Response(200, "hi"))
    assert "HTTP 200" in message
    assert "outside" in message
    assert str(rs._TRAFILATURA_MIN_BYTES) in message


def test_a_200_of_reasonable_size_points_at_a_transient_failure():
    """If the retry works, the first attempt was rate-limited or flaky --
    which is worth knowing, because it is the one cause that a later run
    fixes by itself."""
    message = _diagnose(_Response(200, "<html>" + "x" * 500 + "</html>"))
    assert "second attempt" in message
    assert "Worth another run" in message


def test_diagnosis_never_raises_over_the_failure_it_explains():
    message = _diagnose(raises=OSError("connection reset"))
    assert "OSError" in message
    assert "connection reset" in message


# --- through fetch_html ---------------------------------------------------


def test_fetch_html_carries_the_reason_into_its_error():
    with (
        patch("app.services.recipe_service.trafilatura.fetch_url", return_value=None),
        patch("app.services.recipe_service.trafilatura.fetch_response", return_value=_Response(403)),
        pytest.raises(ValueError) as caught,
    ):
        rs.fetch_html("https://example.com/x")
    assert "https://example.com/x" in str(caught.value)
    assert "HTTP 403" in str(caught.value)


def test_a_successful_fetch_does_not_diagnose_anything():
    """The diagnosis costs a second request. It must only ever happen on a
    URL that already failed -- 565 bookmarks is not the place to double
    every fetch."""
    with (
        patch("app.services.recipe_service.trafilatura.fetch_url", return_value="<html>ok</html>"),
        patch("app.services.recipe_service.trafilatura.fetch_response") as diagnosed,
    ):
        assert rs.fetch_html("https://example.com/x") == "<html>ok</html>"
    diagnosed.assert_not_called()


def test_the_size_window_comes_from_trafilatura_not_from_us():
    """Restating the bounds would let them drift from the check that
    actually runs."""
    assert rs._TRAFILATURA_MIN_BYTES == 10
    assert rs._TRAFILATURA_MAX_BYTES == 20000000
