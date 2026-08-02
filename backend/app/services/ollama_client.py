"""Thin wrapper around the `ollama` Python client, configured from
DB-backed settings (settings_service) rather than static env vars, so
the base URL/models can be changed from the Settings UI (Phase 8)
without a container rebuild."""
from __future__ import annotations

import ollama
from sqlalchemy.orm import Session

from app.models import SystemPrompt
from app.services import settings_service


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


def chat(db: Session, messages: list[dict], model: str | None = None) -> dict:
    """messages: OpenAI/Ollama-style list of {"role", "content"} dicts.
    Returns the raw Ollama response dict. Connection errors propagate --
    callers (chat endpoint, Phase 7) decide how to surface a friendly
    "Ollama unreachable" message."""
    client = _client(db)
    chat_model = model or settings_service.get_setting(db, "ollama_chat_model")
    return client.chat(model=chat_model, messages=messages, options={"num_ctx": _num_ctx(db)})


def describe_image(db: Session, image_bytes: bytes, prompt: str, model: str | None = None) -> dict:
    """For inventory photo intake (Phase 3): send an image to the
    configured vision model with a text prompt asking it to identify
    food items and, where visible, quantity/expiration."""
    client = _client(db)
    vision_model = model or settings_service.get_setting(db, "ollama_vision_model")
    return client.chat(
        model=vision_model,
        messages=[{"role": "user", "content": prompt, "images": [image_bytes]}],
        options={"num_ctx": _num_ctx(db)},
    )


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
