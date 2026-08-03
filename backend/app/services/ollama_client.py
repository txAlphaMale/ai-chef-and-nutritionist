"""Wrapper around the `ollama` Python client, configured from DB-backed
settings (settings_service) rather than static env vars so the base URL,
models, context window and timeout can all be changed from the Settings
UI without a container rebuild.

Three properties this module is responsible for. Everything else in the
app depends on them holding:

1. **Bounded calls.** ollama-python defaults to no timeout at all, and
   this app runs every AI operation on a single serial worker thread
   (services/job_queue.py). An unbounded call therefore doesn't fail one
   feature, it wedges all of them. Every client here carries an explicit
   httpx timeout built from the `ollama_timeout_seconds` setting.

2. **Structurally valid output where the caller needs structure.**
   `chat_json()` passes a JSON Schema to Ollama's `format` parameter,
   which constrains decoding so the model physically cannot emit a token
   that violates the schema. This is the correct mechanism for every
   extraction path in this app; the text-scanning recovery in
   services/ai_json_extraction.py is a fallback for an Ollama server too
   old to support `format`, not the primary path.

3. **Reserved response space.** `num_ctx` covers prompt AND response.
   Callers state how much room the answer needs (`response_tokens`) and
   get a matching `num_predict` plus a content budget sized off what is
   left, so a long input can never silently crowd out the answer.

Two model-compatibility notes, both handled rather than assumed:
- Thinking-capable models (the household's default `qwen3.6:27b` is one)
  route their answer into `message.thinking` and leave `message.content`
  empty unless thinking is disabled. `think=False` is requested, and
  `extract_content()` falls back to the thinking field if content is
  empty anyway, so a server/template combination that ignores the flag
  degrades instead of returning nothing.
- Not every model accepts a `think` parameter. A server rejecting it is
  retried once without it rather than surfacing as a hard failure.
"""
from __future__ import annotations

import httpx
import ollama
from sqlalchemy.orm import Session

from app.models import SystemPrompt
from app.services import settings_service

# Deliberately conservative chars-per-token estimate. There is no real
# tokenizer available here (the model doing the tokenizing is whatever
# the household configured), so this is an approximation. Erring low --
# assuming fewer characters fit per token -- leaves headroom rather than
# risking the silent prompt truncation this budgeting exists to prevent.
_CHARS_PER_TOKEN = 3.5

# Connect/write/pool timeouts are always short: reaching the Ollama host
# is either fast or broken. Only the read timeout needs to accommodate a
# slow generation, and that one is user-configurable.
_CONNECT_TIMEOUT = 10.0
_WRITE_TIMEOUT = 60.0
_POOL_TIMEOUT = 10.0

_DEFAULT_TIMEOUT_SECONDS = 600.0
_MIN_TIMEOUT_SECONDS = 30.0


class OllamaTimeout(RuntimeError):
    """Raised when a generation exceeded `ollama_timeout_seconds`.

    A distinct type so callers (and the job worker) can report "the model
    took too long" rather than a generic connection error -- those need
    different advice from the user's point of view.
    """


def _timeout_seconds(db: Session) -> float:
    raw = settings_service.get_setting(db, "ollama_timeout_seconds")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = _DEFAULT_TIMEOUT_SECONDS
    return max(value, _MIN_TIMEOUT_SECONDS)


def _client(db: Session) -> ollama.Client:
    base_url = settings_service.get_setting(db, "ollama_base_url")
    timeout = httpx.Timeout(
        connect=_CONNECT_TIMEOUT,
        read=_timeout_seconds(db),
        write=_WRITE_TIMEOUT,
        pool=_POOL_TIMEOUT,
    )
    return ollama.Client(host=base_url, timeout=timeout)


def _num_ctx(db: Session) -> int:
    """Ollama falls back to a 2048-token context window when `num_ctx`
    isn't sent, and does NOT error when a prompt exceeds it -- it clips
    the overflow silently. Every call in this module sends it explicitly."""
    raw = settings_service.get_setting(db, "ollama_num_ctx")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = 8192
    return max(value, 2048)


def content_char_budget(db: Session, prompt_overhead_chars: int = 0, response_reserve_tokens: int = 1500) -> int:
    """How many characters of raw source content will fit in the context
    window alongside a prompt of `prompt_overhead_chars` and a response
    of `response_reserve_tokens`. Callers slice their content to this
    rather than a fixed cap, so usable length tracks whatever `num_ctx`
    the household actually configured."""
    overhead_tokens = (prompt_overhead_chars / _CHARS_PER_TOKEN) + response_reserve_tokens
    available_tokens = max(_num_ctx(db) - overhead_tokens, 500)  # never collapse to an unusably tiny budget
    return int(available_tokens * _CHARS_PER_TOKEN)


def fit_prompt(db: Session, prompt: str, response_tokens: int = 1500) -> tuple[str, bool]:
    """Last-line defence for an already-assembled prompt: returns
    `(prompt, was_truncated)`, trimming from the END if the whole thing
    plus its reserved response can't fit in the context window.

    Trimming from the end matters. Ollama clips from the FRONT, which is
    where the system prompt and output-format instructions live -- so an
    over-length prompt degrades into "the model was never told what to
    do." Cutting the tail (catalog listings, history, reference material)
    loses context but keeps the instructions intact.

    Callers that can drop specific low-value sections should do that
    first; this is the backstop for whatever they hand over."""
    budget_chars = int((_num_ctx(db) - response_tokens) * _CHARS_PER_TOKEN)
    if budget_chars <= 0 or len(prompt) <= budget_chars:
        return prompt, False
    marker = "\n\n[... context truncated to fit the configured context window ...]"
    return prompt[: max(budget_chars - len(marker), 0)] + marker, True


def get_active_prompt(db: Session, prompt_key: str) -> str | None:
    """e.g. prompt_key='main_chef' or 'dietary_onboarding' -- see
    app/seed.py for the seeded content."""
    row = db.query(SystemPrompt).filter_by(prompt_key=prompt_key, is_active=True).first()
    return row.content if row else None


def _log_call(label: str, base_url: str, model: str, num_ctx: int, prompt_chars: int, structured: bool) -> None:
    print(
        f"[ollama_client] -> {label} model={model!r} num_ctx={num_ctx} "
        f"prompt_chars={prompt_chars} structured={structured} base_url={base_url!r}",
        flush=True,
    )


def _log_response(label: str, response) -> None:
    """Logs the response SHAPE, never full content -- a reply can be long
    and can contain personal data (bloodwork, health metrics). A length
    plus a short preview is enough to tell "empty", "truncated" and
    "looks like real JSON" apart, which is what debugging actually needs.

    `done_reason` is the field worth watching: "length" means generation
    hit the token ceiling and the answer is cut off mid-structure."""
    if not hasattr(response, "get"):
        print(f"[ollama_client] <- {label} UNEXPECTED response type={type(response).__name__}", flush=True)
        return
    message = response.get("message") or {}
    content = (message.get("content") if hasattr(message, "get") else None) or ""
    thinking = (message.get("thinking") if hasattr(message, "get") else None) or ""
    preview = content[:200].replace("\n", " ")
    print(
        f"[ollama_client] <- {label} done={response.get('done')} done_reason={response.get('done_reason')!r} "
        f"eval_count={response.get('eval_count')} prompt_eval_count={response.get('prompt_eval_count')} "
        f"content_chars={len(content)} content_preview={preview!r} thinking_chars={len(thinking)}",
        flush=True,
    )


def extract_content(response) -> str:
    """Pulls the assistant's final answer out of a chat()/describe_image()
    response.

    Duck-typed on `.get()` rather than `isinstance(response, dict)`:
    ollama<0.4 returned plain dicts and ollama>=0.4 returns pydantic
    objects, and both support Mapping-style access. An isinstance check
    silently returns `str(<whole response object>)` on one of those.

    Falls back to `message.thinking` when `content` is empty. A
    thinking-capable model whose chat template ignores `think=False`
    routes its entire answer -- JSON included -- into that field, and
    returning it (for the JSON extractors downstream to work on) is
    strictly better than returning an empty string."""
    if not hasattr(response, "get"):
        return str(response)
    message = response.get("message") or {}
    if not hasattr(message, "get"):
        return ""
    content = message.get("content") or ""
    if content:
        return content
    return message.get("thinking") or ""


def _build_options(num_ctx: int, response_tokens: int | None, extra_options: dict | None) -> dict:
    options: dict = {"num_ctx": num_ctx}
    if response_tokens is not None:
        # Reserve generation headroom explicitly. Without this Ollama will
        # happily let a long prompt consume the whole window and then cut
        # the answer off mid-structure (done_reason="length").
        options["num_predict"] = response_tokens
    options.update(extra_options or {})
    return options


def _chat_raw(
    client: ollama.Client,
    label: str,
    *,
    model: str,
    messages: list[dict],
    options: dict,
    fmt=None,
) -> dict:
    """One Ollama chat call with the two compatibility retries this app
    needs, and nothing else.

    Retry 1: a server/model that rejects the `think` parameter outright
    (not every model advertises the capability) is retried once without
    it, rather than failing a user-visible operation over a flag whose
    only purpose is suppressing output nobody displays.

    Retry 2: a server too old to support schema-constrained `format` is
    retried once unconstrained -- at which point the caller's
    text-scanning fallback in ai_json_extraction.py takes over. That is
    exactly the degradation path that module was written for."""
    attempts: list[dict] = [{"think": False}, {}]
    last_exc: Exception | None = None
    for kwargs in attempts:
        try:
            return client.chat(model=model, messages=messages, options=options, format=fmt, **kwargs)
        except httpx.TimeoutException as exc:
            raise OllamaTimeout(
                f"Ollama did not respond within the configured timeout while running {label}. "
                "The model may still be loading, or may be too large for the available VRAM. "
                "Raise 'Ollama request timeout' in Settings, or try a smaller model."
            ) from exc
        except ollama.ResponseError as exc:
            message = str(exc).lower()
            if "think" in message and kwargs:
                print(f"[ollama_client] {label}: server rejected think parameter, retrying without it", flush=True)
                last_exc = exc
                continue
            if fmt is not None and ("format" in message or "schema" in message):
                print(f"[ollama_client] {label}: server rejected structured format, retrying unconstrained", flush=True)
                last_exc = exc
                fmt = None
                continue
            print(f"[ollama_client] {label} RESPONSE ERROR: {exc}", flush=True)
            raise
        except Exception as exc:  # noqa: BLE001 -- logged and re-raised, never swallowed
            print(f"[ollama_client] {label} EXCEPTION: {type(exc).__name__}: {exc}", flush=True)
            raise
    raise last_exc or RuntimeError(f"{label} failed with no usable Ollama call")


def chat(
    db: Session,
    messages: list[dict],
    model: str | None = None,
    extra_options: dict | None = None,
    response_tokens: int | None = None,
    response_schema: dict | None = None,
) -> dict:
    """messages: OpenAI/Ollama-style list of {"role", "content"} dicts.

    `response_schema` -- a JSON Schema dict (typically
    `SomeModel.model_json_schema()`) -- switches Ollama into constrained
    decoding for this call. Prefer `chat_json()` below, which pairs the
    schema with the right sampling settings.

    Connection/timeout errors propagate; callers decide how to surface
    them. A timeout arrives as OllamaTimeout with an actionable message."""
    client = _client(db)
    chat_model = model or settings_service.get_setting(db, "ollama_chat_model")
    num_ctx = _num_ctx(db)
    options = _build_options(num_ctx, response_tokens, extra_options)
    last_content = messages[-1].get("content", "") if messages else ""
    _log_call(
        "chat",
        settings_service.get_setting(db, "ollama_base_url"),
        chat_model,
        num_ctx,
        len(last_content),
        response_schema is not None,
    )
    response = _chat_raw(client, "chat", model=chat_model, messages=messages, options=options, fmt=response_schema)
    _log_response("chat", response)
    return response


# Sampling for extraction tasks. Deterministic on purpose.
#
# This replaces a previous config of temperature 0.7 / top_p 0.8 /
# presence_penalty 1.5, which were Qwen's published GENERAL CHAT
# parameters applied to a structured extraction job. The presence penalty
# in particular was actively harmful: it penalises tokens that have
# already appeared, and an extraction response is deliberately repetitive
# (the same JSON keys, units and category strings on every list element),
# so it pushed the model to stop emitting the repeated structure partway
# through a long list -- the exact "captured 4 of 8 receipt items"
# symptom it was introduced to fix. Ollama's own structured-output
# guidance is to use temperature 0.
EXTRACTION_OPTIONS: dict = {"temperature": 0.0, "top_p": 1.0}


def chat_json(
    db: Session,
    messages: list[dict],
    schema: dict,
    model: str | None = None,
    response_tokens: int = 2000,
    extra_options: dict | None = None,
) -> str:
    """The path every structured-extraction caller should use: constrained
    decoding against `schema`, deterministic sampling, reserved response
    space. Returns the raw response text -- the caller validates it
    against its own Pydantic model, so this function stays schema-agnostic.

    When the Ollama server supports `format`, the returned text is valid
    JSON matching the schema by construction. When it doesn't, this
    degrades to an unconstrained response and the caller's
    ai_json_extraction fallback handles it."""
    options = {**EXTRACTION_OPTIONS, **(extra_options or {})}
    response = chat(
        db,
        messages,
        model=model,
        extra_options=options,
        response_tokens=response_tokens,
        response_schema=schema,
    )
    return extract_content(response)


def describe_image(
    db: Session,
    image_bytes: bytes,
    prompt: str,
    model: str | None = None,
    extra_options: dict | None = None,
    response_tokens: int | None = None,
    response_schema: dict | None = None,
) -> dict:
    """Vision-model call for inventory photo intake, receipt photos, and
    recipe photo import. Vision models accept the same `format` parameter
    as text models, so `response_schema` works here too."""
    client = _client(db)
    vision_model = model or settings_service.get_setting(db, "ollama_vision_model")
    num_ctx = _num_ctx(db)
    options = _build_options(num_ctx, response_tokens, extra_options)
    _log_call(
        "describe_image",
        settings_service.get_setting(db, "ollama_base_url"),
        vision_model,
        num_ctx,
        len(prompt),
        response_schema is not None,
    )
    response = _chat_raw(
        client,
        "describe_image",
        model=vision_model,
        messages=[{"role": "user", "content": prompt, "images": [image_bytes]}],
        options=options,
        fmt=response_schema,
    )
    _log_response("describe_image", response)
    return response


def describe_image_json(
    db: Session,
    image_bytes: bytes,
    prompt: str,
    schema: dict,
    model: str | None = None,
    response_tokens: int = 2000,
    extra_options: dict | None = None,
) -> str:
    """Vision counterpart to `chat_json` -- see that function's docstring."""
    options = {**EXTRACTION_OPTIONS, **(extra_options or {})}
    response = describe_image(
        db,
        image_bytes,
        prompt,
        model=model,
        extra_options=options,
        response_tokens=response_tokens,
        response_schema=schema,
    )
    return extract_content(response)


def embed(db: Session, text: str, model: str | None = None) -> list[float]:
    """Embedding vector for a single string, used by knowledge_service to
    index knowledge files for retrieval. Connection errors propagate --
    callers decide how to degrade (knowledge_service skips a chunk rather
    than failing an entire reindex pass)."""
    client = _client(db)
    embed_model = model or settings_service.get_setting(db, "ollama_embed_model")
    try:
        response = client.embeddings(model=embed_model, prompt=text)
    except httpx.TimeoutException as exc:
        raise OllamaTimeout(
            "Ollama did not respond within the configured timeout while generating an embedding."
        ) from exc
    return list(response.get("embedding") or [])


def ping(db: Session) -> bool:
    """Best-effort reachability check for the configured Ollama host.
    Uses a short, fixed timeout rather than the generation timeout -- a
    status indicator that can hang for ten minutes is not a status
    indicator."""
    try:
        base_url = settings_service.get_setting(db, "ollama_base_url")
        ollama.Client(host=base_url, timeout=httpx.Timeout(5.0)).list()
        return True
    except Exception:  # noqa: BLE001 -- any failure means "not reachable", which is the answer
        return False
