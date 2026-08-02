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
