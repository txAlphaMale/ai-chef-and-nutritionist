"""Tests for the 2026-08-02 author-reported bug: ollama_client's chat()/
describe_image() never passed a `num_ctx` option, so Ollama silently fell
back to its own conservative 2048-token default and clipped any prompt
past that with zero error -- traced as the likely real cause of a receipt
import suddenly returning 0 items once RECEIPT_IMPORT_PROMPT grew
substantially longer. Mocks `ollama.Client` (never a real network call,
same standing constraint as every other Ollama-touching test in this
project) to verify the actual call arguments, not just that no exception
is raised."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services import ollama_client, settings_service


def test_ollama_num_ctx_setting_has_a_sane_default(db_session):
    # No row seeded -- get_setting must fall back to the spec's own
    # default rather than returning None/crashing.
    assert settings_service.get_setting(db_session, "ollama_num_ctx") == "8192"


def test_num_ctx_uses_configured_value(db_session):
    settings_service.set_setting(db_session, "ollama_num_ctx", "16384")
    assert ollama_client._num_ctx(db_session) == 16384


def test_num_ctx_falls_back_to_8192_on_garbage_value(db_session):
    settings_service.set_setting(db_session, "ollama_num_ctx", "not-a-number")
    assert ollama_client._num_ctx(db_session) == 8192


def test_num_ctx_never_goes_below_2048(db_session):
    # A user setting this too low would silently reintroduce the exact
    # bug this fix exists for -- floor it at Ollama's own default instead
    # of trusting an accidental "0" or "100" through.
    settings_service.set_setting(db_session, "ollama_num_ctx", "512")
    assert ollama_client._num_ctx(db_session) == 2048


def test_chat_passes_num_ctx_option_to_ollama_client(db_session):
    settings_service.set_setting(db_session, "ollama_num_ctx", "8192")
    mock_client = MagicMock()
    mock_client.chat.return_value = {"message": {"content": "ok"}}
    with patch("app.services.ollama_client.ollama.Client", return_value=mock_client):
        ollama_client.chat(db_session, [{"role": "user", "content": "hi"}], model="test-model")
    mock_client.chat.assert_called_once()
    _, kwargs = mock_client.chat.call_args
    assert kwargs["options"] == {"num_ctx": 8192}
    assert kwargs["model"] == "test-model"


def test_describe_image_passes_num_ctx_option_to_ollama_client(db_session):
    settings_service.set_setting(db_session, "ollama_num_ctx", "12000")
    mock_client = MagicMock()
    mock_client.chat.return_value = {"message": {"content": "ok"}}
    with patch("app.services.ollama_client.ollama.Client", return_value=mock_client):
        ollama_client.describe_image(db_session, b"fake-bytes", "describe this", model="test-vision-model")
    mock_client.chat.assert_called_once()
    _, kwargs = mock_client.chat.call_args
    assert kwargs["options"] == {"num_ctx": 12000}
    assert kwargs["model"] == "test-vision-model"


# ---- content_char_budget (2026-08-02, author follow-up: "will this be a
# problem for recipe files, which can be larger than receipts?") --------
#
# Every AI-import prompt used to hard-cap raw content at a flat
# `content[:8000]` with no relationship to the actual configured context
# window at all. These lock down that the budget now scales with
# `ollama_num_ctx` instead of staying pinned to that old flat number.


def test_content_char_budget_scales_up_with_a_larger_num_ctx(db_session):
    settings_service.set_setting(db_session, "ollama_num_ctx", "8192")
    small = ollama_client.content_char_budget(db_session)
    settings_service.set_setting(db_session, "ollama_num_ctx", "32768")
    large = ollama_client.content_char_budget(db_session)
    assert large > small
    # Quadrupling num_ctx should roughly quadruple the usable content
    # budget too, not just add a flat amount -- confirms this is a real
    # proportional calculation, not two hardcoded constants.
    assert large > small * 3


def test_content_char_budget_at_default_exceeds_the_old_flat_8000_cap(db_session):
    # The whole point of this fix: at the new 8192 default, a receipt or
    # recipe should get noticeably MORE usable content than the old flat
    # 8000-char cap ever allowed, not less.
    budget = ollama_client.content_char_budget(db_session, prompt_overhead_chars=1800, response_reserve_tokens=2000)
    assert budget > 8000


def test_content_char_budget_reserves_room_for_prompt_overhead_and_response(db_session):
    settings_service.set_setting(db_session, "ollama_num_ctx", "8192")
    no_overhead = ollama_client.content_char_budget(db_session, prompt_overhead_chars=0, response_reserve_tokens=0)
    with_overhead = ollama_client.content_char_budget(
        db_session, prompt_overhead_chars=4000, response_reserve_tokens=3000
    )
    assert with_overhead < no_overhead


def test_content_char_budget_never_collapses_to_an_unusably_tiny_value(db_session):
    # An enormous prompt/response reserve (or a very low num_ctx) must
    # never drive the usable content budget to zero or negative -- floor
    # it at something still workably small rather than truncating to
    # nothing, which would just reproduce the original silent-empty-
    # result bug in a different shape.
    settings_service.set_setting(db_session, "ollama_num_ctx", "2048")
    budget = ollama_client.content_char_budget(db_session, prompt_overhead_chars=50000, response_reserve_tokens=50000)
    assert budget > 0
