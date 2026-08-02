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


# ---- ROOT CAUSE FIX (2026-08-02): real docker logs the author provided
# proved a chat() call succeeded (done_reason/eval_count present -- tokens
# WERE generated) yet message.content came back empty. Per Ollama's own
# docs (docs.ollama.com/capabilities/thinking, fetched live), "Thinking is
# enabled by default in the CLI and API for supported models", and this
# app's default `ollama_chat_model` (qwen3.5:9b) is a Qwen 3-family
# thinking model -- so its answer was being routed into a separate
# message.thinking field the app never read, leaving content empty. The
# previously-pinned ollama==0.3.3 client predated the `think` parameter
# needed to turn that off. These lock down the two-part fix: think=False
# is now sent on every call, and extract_content()/the logging correctly
# surface a thinking-only response instead of treating it as silently
# empty. ---------------------------------------------------------------


def test_chat_passes_think_false_to_ollama_client(db_session):
    mock_client = MagicMock()
    mock_client.chat.return_value = {"message": {"content": "ok"}}
    with patch("app.services.ollama_client.ollama.Client", return_value=mock_client):
        ollama_client.chat(db_session, [{"role": "user", "content": "hi"}])
    _, kwargs = mock_client.chat.call_args
    assert kwargs["think"] is False


def test_describe_image_passes_think_false_to_ollama_client(db_session):
    mock_client = MagicMock()
    mock_client.chat.return_value = {"message": {"content": "ok"}}
    with patch("app.services.ollama_client.ollama.Client", return_value=mock_client):
        ollama_client.describe_image(db_session, b"fake-bytes", "describe this")
    _, kwargs = mock_client.chat.call_args
    assert kwargs["think"] is False


class _FakeSubscriptableMessage:
    """Stands in for ollama-python>=0.4's pydantic Message/ChatResponse
    objects without requiring the real pydantic dependency chain in this
    narrow unit test -- only implements the one thing extract_content/
    _log_response actually rely on (.get()), same as the real
    SubscriptableBaseModel does (confirmed by reading ollama-python's
    _types.py directly)."""

    def __init__(self, **fields):
        self._fields = fields

    def get(self, key, default=None):
        return self._fields.get(key, default)


def test_extract_content_handles_plain_dict_response():
    response = {"message": {"content": "hello"}}
    assert ollama_client.extract_content(response) == "hello"


def test_extract_content_handles_pydantic_style_response():
    # The exact shape ollama>=0.4 actually returns -- NOT a dict, so the
    # old `isinstance(response, dict)` guard this app used to copy-paste
    # at every call site would have silently fallen through to
    # `str(response)` here instead of returning "hello".
    response = _FakeSubscriptableMessage(message=_FakeSubscriptableMessage(content="hello"))
    assert ollama_client.extract_content(response) == "hello"


def test_extract_content_returns_empty_string_not_none_when_content_missing():
    response = _FakeSubscriptableMessage(message=_FakeSubscriptableMessage())
    assert ollama_client.extract_content(response) == ""


def test_extract_content_falls_back_to_str_for_a_totally_unexpected_shape():
    assert ollama_client.extract_content("not a response object at all") == "not a response object at all"


def test_chat_merges_extra_options_with_num_ctx(db_session):
    # Author follow-up (2026-08-02): a real receipt with 8 food items
    # only came back with 4 even with think=False -- verified the full
    # text genuinely reached the model (not a truncation bug), so
    # extra_options exists to let a caller request e.g. a lower
    # temperature for more complete list extraction. Must be MERGED with
    # num_ctx, not replace it.
    mock_client = MagicMock()
    mock_client.chat.return_value = {"message": {"content": "ok"}}
    with patch("app.services.ollama_client.ollama.Client", return_value=mock_client):
        ollama_client.chat(
            db_session, [{"role": "user", "content": "hi"}], extra_options={"temperature": 0.1}
        )
    _, kwargs = mock_client.chat.call_args
    assert kwargs["options"] == {"num_ctx": 8192, "temperature": 0.1}


def test_describe_image_merges_extra_options_with_num_ctx(db_session):
    mock_client = MagicMock()
    mock_client.chat.return_value = {"message": {"content": "ok"}}
    with patch("app.services.ollama_client.ollama.Client", return_value=mock_client):
        ollama_client.describe_image(
            db_session, b"fake-bytes", "describe this", extra_options={"temperature": 0.1}
        )
    _, kwargs = mock_client.chat.call_args
    assert kwargs["options"] == {"num_ctx": 8192, "temperature": 0.1}


def test_chat_without_extra_options_only_sends_num_ctx(db_session):
    # Default behavior for every OTHER caller (meal planning, recipe
    # generation, chat) must stay exactly as before -- no unexpected keys
    # sneaking into `options` for callers that don't ask for them.
    mock_client = MagicMock()
    mock_client.chat.return_value = {"message": {"content": "ok"}}
    with patch("app.services.ollama_client.ollama.Client", return_value=mock_client):
        ollama_client.chat(db_session, [{"role": "user", "content": "hi"}])
    _, kwargs = mock_client.chat.call_args
    assert kwargs["options"] == {"num_ctx": 8192}


def test_chat_logs_thinking_chars_when_answer_is_routed_to_thinking_not_content(db_session, capsys):
    # The exact bug this whole investigation uncovered: a thinking model
    # can return a fully successful response (done=True, eval_count>0)
    # with message.content empty because the answer landed in
    # message.thinking instead. Must be clearly distinguishable in the
    # logs from a genuinely empty/failed response.
    mock_client = MagicMock()
    mock_client.chat.return_value = {
        "message": {"content": "", "thinking": "Let me think about this receipt..." * 5},
        "done": True,
        "done_reason": "stop",
        "eval_count": 342,
        "prompt_eval_count": 900,
    }
    with patch("app.services.ollama_client.ollama.Client", return_value=mock_client):
        ollama_client.chat(db_session, [{"role": "user", "content": "hi"}])
    out = capsys.readouterr().out
    assert "content_chars=0" in out
    assert "thinking_chars=170" in out  # len("Let me think about this receipt..." * 5)
    assert "done_reason='stop'" in out
    assert "eval_count=342" in out


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


# ---- diagnostic print logging (2026-08-02, author-reported: "you've
# tried and failed three times... what do you need from me?") ----------
#
# This module had ZERO logging anywhere before this -- every prior round
# of debugging a live "0 items" report had to be reasoned about from
# outside this sandbox (no live Ollama reachable here) with no way to see
# what Ollama actually received or returned. These lock down that the new
# print()-based diagnostics (chosen over `logging` to match this
# codebase's own established docker-logs-visible convention, see
# run_server.py) actually fire with the right, useful content -- so the
# NEXT real import attempt's `docker compose logs backend` output is
# guaranteed to show real evidence instead of nothing.


def test_chat_logs_request_and_response_details(db_session, capsys):
    settings_service.set_setting(db_session, "ollama_num_ctx", "8192")
    mock_client = MagicMock()
    mock_client.chat.return_value = {"message": {"content": "hello world"}, "done": True}
    with patch("app.services.ollama_client.ollama.Client", return_value=mock_client):
        ollama_client.chat(db_session, [{"role": "user", "content": "x" * 500}], model="test-model")
    out = capsys.readouterr().out
    assert "model='test-model'" in out
    assert "num_ctx=8192" in out
    assert "prompt_chars=500" in out
    assert "content_chars=11" in out  # len("hello world")
    assert "hello world" in out


def test_chat_logs_empty_content_clearly_rather_than_hiding_it(db_session, capsys):
    # The exact symptom under investigation: Ollama call succeeds (no
    # exception), but message.content comes back empty. This must be
    # loudly, unambiguously visible in the logs, not indistinguishable
    # from a normal short response.
    mock_client = MagicMock()
    mock_client.chat.return_value = {"message": {"content": ""}}
    with patch("app.services.ollama_client.ollama.Client", return_value=mock_client):
        ollama_client.chat(db_session, [{"role": "user", "content": "hi"}])
    out = capsys.readouterr().out
    assert "content_chars=0" in out


def test_chat_logs_and_reraises_on_exception(db_session, capsys):
    mock_client = MagicMock()
    mock_client.chat.side_effect = RuntimeError("connection refused")
    with patch("app.services.ollama_client.ollama.Client", return_value=mock_client):
        try:
            ollama_client.chat(db_session, [{"role": "user", "content": "hi"}])
            assert False, "expected the exception to propagate"
        except RuntimeError:
            pass
    out = capsys.readouterr().out
    assert "EXCEPTION" in out
    assert "connection refused" in out


def test_chat_logs_an_unexpected_non_dict_response_without_crashing(db_session, capsys):
    # Guards the logging code itself against the exact "what if the
    # response isn't shaped how we assumed" scenario this whole
    # investigation is about -- must never raise while trying to log.
    mock_client = MagicMock()
    mock_client.chat.return_value = "not a dict at all"
    with patch("app.services.ollama_client.ollama.Client", return_value=mock_client):
        result = ollama_client.chat(db_session, [{"role": "user", "content": "hi"}])
    assert result == "not a dict at all"
    out = capsys.readouterr().out
    assert "UNEXPECTED response type=str" in out


def test_describe_image_logs_request_and_response_details(db_session, capsys):
    mock_client = MagicMock()
    mock_client.chat.return_value = {"message": {"content": "[]"}}
    with patch("app.services.ollama_client.ollama.Client", return_value=mock_client):
        ollama_client.describe_image(db_session, b"fake-bytes", "describe this photo", model="test-vision-model")
    out = capsys.readouterr().out
    assert "describe_image" in out
    assert "model='test-vision-model'" in out
