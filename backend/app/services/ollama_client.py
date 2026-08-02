"""Thin wrapper around the `ollama` Python client, configured from
DB-backed settings (settings_service) rather than static env vars, so
the base URL/models can be changed from the Settings UI (Phase 8)
without a container rebuild."""
from __future__ import annotations

import ollama
from sqlalchemy.orm import Session

from app.models import SystemPrompt
from app.services import settings_service

# Diagnostic visibility (2026-08-02, author-reported): this module had
# ZERO logging anywhere in it before this -- every prior round of
# debugging a "receipt import returns 0 items" report had to be done by
# reasoning about prompt/token math from OUTSIDE this sandbox (no live
# Ollama reachable here), with no way to see what Ollama actually
# received or returned for a real, live call. `print(..., flush=True)`
# is used rather than the stdlib `logging` module to match this
# codebase's own established convention for anything that must reliably
# reach `docker compose logs` (see run_server.py) -- avoids any risk of
# uvicorn's own logging config swallowing a `logging.getLogger()` call
# that isn't wired into its dictConfig. Always-on, not gated behind a
# debug flag: one line per AI call is cheap, and the alternative (no
# visibility at all into the single most failure-prone part of this app)
# is worse than a little log noise.
#
# ROOT CAUSE FOUND (2026-08-02, confirmed against real ground-truth
# `docker compose logs` output the author provided after the diagnostic
# logging above shipped, plus Ollama's own docs and the ollama-python
# source, not assumed): the log showed a chat() call completing with NO
# exception, full normal response metadata (done_reason, eval_count,
# prompt_eval_count all present -- proof tokens WERE generated), yet
# message.content was an empty string. Per docs.ollama.com/capabilities/
# thinking: "Thinking is enabled by default in the CLI and API for
# supported models," and Qwen 3 (this app's default `ollama_chat_model`,
# `qwen3.5:9b`, is that family) is one of the listed supported models.
# When thinking is on, the model's entire response -- including a JSON-
# extraction answer -- can be routed into a separate `message.thinking`
# field, leaving `message.content` empty exactly as observed. The
# previously-pinned `ollama==0.3.3` client predates the `think` request
# parameter needed to turn this off (confirmed via `inspect.getsource`
# against that exact installed version earlier this session), so this
# app had no way to disable it. Fixed by bumping to `ollama==0.6.2`
# (confirmed current via `pip index versions ollama`) and passing
# `think=False` on every chat()/describe_image() call below -- this app
# has no UI for showing a reasoning trace to the user, so there's no
# reason to pay the extra generation time/tokens for it, and disabling
# it removes the entire failure mode.
#
# IMPORTANT SIDE EFFECT of the version bump, fixed in the same pass: on
# ollama==0.3.3, `client.chat()` returned a plain dict (confirmed via
# `inspect.getsource` on `Client._request()` -> `cls(**response.json())`
# called with `cls=dict`-like passthrough). On ollama>=0.4, responses are
# pydantic `ChatResponse`/`Message` objects (confirmed by reading
# ollama-python's `_types.py` directly). Every one of this app's ~13
# Ollama call sites (routers/inventory.py, routers/recipes.py,
# routers/chat.py, routers/health.py, routers/meal_plan.py,
# services/recipe_service.py, services/health_service.py) used a
# copy-pasted `response.get("message", {}).get("content", "") if
# isinstance(response, dict) else str(response)` -- a check that would
# have started silently returning `str(<the whole pydantic object>)`
# instead of the real answer at every single one of them the moment this
# version bump landed, since `isinstance(ChatResponse(...), dict)` is
# False. `extract_content()` below replaces all of those with one
# duck-typed helper (both dict and ollama-python's SubscriptableBaseModel
# support `.get()`, confirmed by reading `_types.py`), tested once here
# instead of copy-pasted and untested at 13 call sites.


def _client(db: Session) -> ollama.Client:
    base_url = settings_service.get_setting(db, "ollama_base_url")
    return ollama.Client(host=base_url)


def _num_ctx(db: Session) -> int:
    """Bug fix (2026-08-02, author-reported): every call in this module
    used to omit `options`, so Ollama silently fell back to its own
    default context window (2048 tokens) with no error whatsoever when a
    prompt exceeded it -- just quietly clipped content off. This was
    traced as the likely real cause of a receipt-import PDF suddenly
    returning zero items right after RECEIPT_IMPORT_PROMPT grew
    substantially longer (adding purchase-date/price/quantity-vs-unit
    guidance): the prompt template alone is now ~1300+ tokens before even
    adding the receipt's own text, comfortably capable of pushing a
    typical import past 2048 with the actual content silently dropped.
    Reads the new `ollama_num_ctx` setting (default 8192, GUI-editable in
    Settings) rather than hardcoding a value, since the right number
    depends on the user's model/VRAM budget."""
    raw = settings_service.get_setting(db, "ollama_num_ctx")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 8192
    return max(value, 2048)


# Rough, deliberately conservative chars-per-token estimate for English
# prose -- there's no real tokenizer available here (the model doing the
# actual tokenizing is whatever the user configured, possibly not even
# downloaded on this machine), so this is an approximation, not a
# guarantee. Erring toward "fewer chars per token" (i.e. UNDER-estimating
# how much text fits) is the safe direction: it leaves extra headroom
# rather than risking the exact silent-truncation bug this exists to fix.
_CHARS_PER_TOKEN = 3.5


def content_char_budget(db: Session, prompt_overhead_chars: int = 0, response_reserve_tokens: int = 1500) -> int:
    """Author-reported follow-up (2026-08-02) to the num_ctx fix above:
    every AI-import prompt in this app (receipt, recipe, bloodwork) used
    to hard-cap its raw extracted content at a flat `content[:8000]`
    regardless of the actual configured context window -- a number that,
    not coincidentally, works out to roughly Ollama's OLD 2048-token
    default (8000 chars / ~4 chars-per-token). Now that num_ctx is
    configurable (default 8192, 4x higher), that flat 8000-char cap is
    needlessly conservative AND, for the specific case the author asked
    about, backwards: it was never actually sized off the context window
    at all, just a leftover guess, and recipe imports are exactly the
    case most likely to need MORE room than a receipt -- a recipe blog
    page without schema.org JSON-LD markup (the messiest source, and the
    only one that reaches this AI-prompt path at all -- see B9.3's
    JSON-LD-first import order) commonly runs long on its own SEO/life-
    story prose even after trafilatura's extraction. Replaces every
    hardcoded `content[:8000]` in this app with a call to this function,
    so the usable content length scales with whatever num_ctx the user
    has actually configured, always leaving room for the surrounding
    prompt's own instructions (`prompt_overhead_chars`) and the model's
    response (`response_reserve_tokens` -- higher for a prompt whose
    response can itself be long, e.g. a big multi-item receipt or a
    detailed recipe with many ingredients/steps)."""
    overhead_tokens = (prompt_overhead_chars / _CHARS_PER_TOKEN) + response_reserve_tokens
    available_tokens = max(_num_ctx(db) - overhead_tokens, 500)  # never collapse to an unusably tiny budget
    return int(available_tokens * _CHARS_PER_TOKEN)


def get_active_prompt(db: Session, prompt_key: str) -> str | None:
    """e.g. prompt_key='main_chef' or 'dietary_onboarding' -- see
    app/seed.py for the seeded content."""
    row = db.query(SystemPrompt).filter_by(prompt_key=prompt_key, is_active=True).first()
    return row.content if row else None


def _log_call(label: str, base_url: str, model: str, num_ctx: int, prompt_chars: int) -> None:
    print(
        f"[ollama_client] -> {label} model={model!r} num_ctx={num_ctx} "
        f"prompt_chars={prompt_chars} base_url={base_url!r}",
        flush=True,
    )


def _log_response(label: str, response) -> None:
    """Logs enough of the response shape to answer, from a real live
    call, the exact question every prior debugging round could only
    guess at: did Ollama return the expected message/content shape at
    all, and if so, was "content" actually empty? Duck-typed on `.get()`
    rather than `isinstance(response, dict)` so this works unchanged for
    both the plain dicts the previously-pinned ollama==0.3.3 client
    returned and the pydantic ChatResponse/Message objects ollama>=0.4
    returns (both support Mapping-style .get() -- see the module
    docstring's SIDE EFFECT note). Also surfaces done_reason, eval_count,
    prompt_eval_count, and -- the specific field that explained this
    session's live "0 items" report -- message.thinking's length, so a
    thinking-capable model routing its answer there instead of into
    content is immediately visible instead of looking identical to a
    genuinely empty response. Never logs full content/thinking text
    (could be long/contain personal data, e.g. bloodwork) -- a length
    plus a short preview is enough to tell "empty," "truncated-looking,"
    or "looks like real JSON" apart."""
    if not hasattr(response, "get"):
        print(f"[ollama_client] <- {label} UNEXPECTED response type={type(response).__name__}: {response!r:.300}", flush=True)
        return
    message = response.get("message") or {}
    content = (message.get("content") if hasattr(message, "get") else None) or ""
    thinking = (message.get("thinking") if hasattr(message, "get") else None) or ""
    content_preview = content[:300].replace("\n", " ")
    thinking_preview = thinking[:200].replace("\n", " ")
    print(
        f"[ollama_client] <- {label} done={response.get('done')} done_reason={response.get('done_reason')!r} "
        f"eval_count={response.get('eval_count')} prompt_eval_count={response.get('prompt_eval_count')} "
        f"content_chars={len(content)} content_preview={content_preview!r} "
        f"thinking_chars={len(thinking)} thinking_preview={thinking_preview!r}",
        flush=True,
    )


def extract_content(response) -> str:
    """Pulls the assistant's final answer out of a chat()/describe_image()
    response. Duck-typed on `.get()` (see module docstring's SIDE EFFECT
    note) rather than `isinstance(response, dict)` -- the exact check
    every one of this app's Ollama call sites used to copy-paste inline,
    which would have silently started returning `str(<the whole pydantic
    response object>)` instead of the real content at all ~13 of them the
    moment ollama>=0.4 (needed for the `think` parameter, see chat()/
    describe_image() below) started returning pydantic objects instead of
    plain dicts. Centralized here and tested once instead."""
    if not hasattr(response, "get"):
        return str(response)
    message = response.get("message") or {}
    content = message.get("content") if hasattr(message, "get") else None
    return content or ""


def chat(
    db: Session, messages: list[dict], model: str | None = None, extra_options: dict | None = None
) -> dict:
    """messages: OpenAI/Ollama-style list of {"role", "content"} dicts.
    Returns the raw Ollama response dict. Connection errors propagate --
    callers (chat endpoint, Phase 7) decide how to surface a friendly
    "Ollama unreachable" message.

    `extra_options` (2026-08-02, author-reported follow-up: a receipt
    import with `think=False` now correctly populates content, but only
    captured 4 of 8 real food items from a real 15-line receipt --
    verified by extracting that exact PDF's text locally and confirming
    ALL 8 items were genuinely present in what was sent to the model, so
    this is not a truncation/budget bug, it's the model itself stopping
    partway through a long list. Merged into the num_ctx options dict
    (never silently overwritten -- `extra_options` wins on key
    collision) so a caller doing extraction over a long, repetitive list
    can request e.g. a low `temperature` for more complete, less
    "creative" recall, without changing the default behavior for
    every other caller (meal planning genuinely benefits from some
    creativity; extraction does not)."""
    client = _client(db)
    chat_model = model or settings_service.get_setting(db, "ollama_chat_model")
    num_ctx = _num_ctx(db)
    options = {"num_ctx": num_ctx, **(extra_options or {})}
    last_content = messages[-1].get("content", "") if messages else ""
    _log_call("chat", settings_service.get_setting(db, "ollama_base_url"), chat_model, num_ctx, len(last_content))
    try:
        # think=False (2026-08-02, root cause above): this app never
        # displays a reasoning trace, so there's no reason to let a
        # thinking-capable model (e.g. the default qwen3.5:9b) spend
        # generation time/tokens on one -- and leaving thinking on is
        # exactly what caused message.content to come back empty.
        response = client.chat(model=chat_model, messages=messages, options=options, think=False)
    except Exception as exc:
        print(f"[ollama_client] chat EXCEPTION: {type(exc).__name__}: {exc}", flush=True)
        raise
    _log_response("chat", response)
    return response


def describe_image(
    db: Session, image_bytes: bytes, prompt: str, model: str | None = None, extra_options: dict | None = None
) -> dict:
    """For inventory photo intake (Phase 3): send an image to the
    configured vision model with a text prompt asking it to identify
    food items and, where visible, quantity/expiration. `extra_options`
    -- see chat()'s docstring."""
    client = _client(db)
    vision_model = model or settings_service.get_setting(db, "ollama_vision_model")
    num_ctx = _num_ctx(db)
    options = {"num_ctx": num_ctx, **(extra_options or {})}
    _log_call("describe_image", settings_service.get_setting(db, "ollama_base_url"), vision_model, num_ctx, len(prompt))
    try:
        # think=False -- see chat() above, same reasoning applies to the
        # vision model.
        response = client.chat(
            model=vision_model,
            messages=[{"role": "user", "content": prompt, "images": [image_bytes]}],
            options=options,
            think=False,
        )
    except Exception as exc:
        print(f"[ollama_client] describe_image EXCEPTION: {type(exc).__name__}: {exc}", flush=True)
        raise
    _log_response("describe_image", response)
    return response


def embed(db: Session, text: str, model: str | None = None) -> list[float]:
    """Embedding vector for a single string of text, via Ollama's
    /api/embeddings (the `ollama` package's Client.embeddings wraps this
    directly). Used by knowledge_service.py to chunk/embed knowledge
    files for real retrieval instead of always injecting a whole file's
    text into every prompt. Connection errors propagate, same convention
    as chat()/describe_image() -- callers decide how to degrade (e.g.
    knowledge_service skips a chunk on failure rather than failing an
    entire reindex pass)."""
    client = _client(db)
    embed_model = model or settings_service.get_setting(db, "ollama_embed_model")
    response = client.embeddings(model=embed_model, prompt=text)
    return list(response.get("embedding") or [])


def ping(db: Session) -> bool:
    """Best-effort reachability check for the configured Ollama host."""
    try:
        _client(db).list()
        return True
    except Exception:
        return False
