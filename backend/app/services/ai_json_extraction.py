r"""Fallback JSON-from-model-text extraction.

READ THIS BEFORE EXTENDING ANYTHING HERE. This module is NOT the primary
path. Every structured AI call in the app passes a JSON Schema to
Ollama's `format` parameter (see app/schemas/ai_extraction.py and
ollama_client.chat_json), which constrains decoding so a malformed
response is unrepresentable rather than merely unlikely.

What remains here is the degradation path for an Ollama server too old to
support `format`, plus a cheap safety net for the handful of genuinely
conversational responses that are not schema-constrained.

**If a model is producing output this module has to rescue, constrain
that call site -- do not add another recovery heuristic here.** Three
successive rounds of exactly that (a greedy-regex fix, a reasoning-trace
stripper, a truncation salvage) each left the underlying feature still
failing, because the defect was in the generator and every fix was
applied to the parser.

Two things this has to survive, which is why it is not just json.loads:

- **Inline reasoning traces.** `ollama_client.chat` requests
  `think=False`, but not every server/template honours it, and a
  thinking-capable model routinely emits a scratch JSON draft mid-trace.
  A naive "first { to last }" scan spans from a brace inside that draft
  to the real answer's closing brace and yields garbage. `strip_reasoning`
  removes the trace first, and the scan below is string-aware bracket
  matching rather than a regex.
- **Truncated output.** A response clipped mid-object is salvaged to the
  last complete top-level field rather than discarded, so a partial
  answer degrades to a partial result instead of an empty one.

Both array and object shapes are handled here so every JSON-consuming
call site shares one defense, rather than half the app having it.
"""
from __future__ import annotations

import json
import re

_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_ORPHAN_THINK_CLOSE_RE = re.compile(r"^.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_reasoning(raw_text: str) -> str:
    """Removes a thinking/reasoning trace from model output before any
    JSON extraction is attempted. Two shapes are handled: a complete
    `<think>...</think>` block, and a bare trailing `</think>` with no
    opening tag (what you get when the chat template itself opens the
    tag, so the opener never appears in the returned content -- observed
    behavior, not a hypothetical, see inventory_service's original
    investigation)."""
    text = _THINK_BLOCK_RE.sub(" ", raw_text)
    if "</think>" in text.lower():
        text = _ORPHAN_THINK_CLOSE_RE.sub(" ", text)
    return text


def _scan_bracketed(text: str, start: int, open_char: str, close_char: str) -> tuple[int | None, list[tuple[int, int]]]:
    """Bracket-matching scan starting at `text[start] == open_char`,
    string-aware so brackets/braces inside string values (a free-text
    "confidence_note"/"description" field, say) don't throw off the depth
    count. Handles either shape (`[`/`]` for an array, `{`/`}` for an
    object) via the caller-supplied character pair, so the array and
    object extractors below share one implementation instead of two
    near-identical copies.

    Returns `(end_index_exclusive, top_level_spans)`:
      - `end_index_exclusive` is None if the bracket is never closed --
        i.e. generation was cut off mid-structure (the context/
        `num_predict` limit -- `done_reason` "length" in ollama_client's
        response log).
      - `top_level_spans` are the (start, end_exclusive) spans of every
        depth-1 `{...}` object found directly inside the structure --
        meaningful for an array (each element), and also collected for
        an object (each `{`/`[`-valued property) so a truncated OBJECT
        can still recover a nested object it fully contains even when
        the outer object itself never closes.
    """
    depth = 0
    in_string = False
    escaped = False
    object_start: int | None = None
    spans: list[tuple[int, int]] = []
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            if char == "{" and depth == 1 and object_start is None:
                object_start = index
            depth += 1
        elif char in "]}":
            depth -= 1
            if char == "}" and depth == 1 and object_start is not None:
                spans.append((object_start, index + 1))
                object_start = None
            if depth == 0:
                return index + 1, spans
    return None, spans


def _scan_top_level_pair_boundary(text: str, start: int) -> int | None:
    """Companion scan for OBJECT truncation salvage: walks `text[start] ==
    "{"` tracking depth-1 (i.e. this object's own top-level, not a nested
    value's) commas, string-aware exactly like `_scan_bracketed`. Returns
    the index of the LAST depth-1 comma seen before the text ends (or
    None if none was seen) -- the end of the last fully-formed "key":
    value pair, and therefore a safe point to truncate a cut-off object
    at and append a closing "}" to recover a partial-but-valid result
    (e.g. title/description/ingredients present but a long
    "instructions" array cut off mid-generation)."""
    depth = 0
    in_string = False
    escaped = False
    last_top_level_comma: int | None = None
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                return last_top_level_comma  # closed normally -- caller won't reach here needing salvage anyway
        elif char == "," and depth == 1:
            last_top_level_comma = index
    return last_top_level_comma


def _salvage_array_objects(text: str, spans: list[tuple[int, int]]) -> list:
    """Parses each individually-complete `{...}` element of an array that
    could not be parsed as a whole (truncated mid-generation, or a
    trailing comma). Elements that don't parse on their own are skipped
    rather than failing the batch -- a partial result is strictly better
    than the zero items a naive parse failure would return."""
    salvaged = []
    for object_start, object_end in spans:
        try:
            entry = json.loads(text[object_start:object_end])
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            salvaged.append(entry)
    return salvaged


def extract_json_array(raw_text: str) -> list:
    """Pulls the first genuinely-populated JSON array out of a model's
    raw answer -- reasoning trace stripped, string-aware bracket-matching
    (never a greedy regex, which a stray bracket anywhere else in the
    response -- trailing commentary, a lead-in sentence, a second array
    -- silently corrupts), with truncated-array salvage of whatever
    complete elements exist before a mid-generation cutoff. An `[]`
    embedded in prose is treated as a false start and skipped in favor of
    a later populated array; a response that IS just `[]` (parses
    strictly on the first attempt) is honored as a genuine "found
    nothing" answer."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        return []

    text = strip_reasoning(raw_text)

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
        # Ollama's `format` parameter takes an OBJECT schema -- a bare
        # top-level array is not portably supported across llama.cpp
        # grammar builds -- so every list-shaped extraction in this app is
        # now constrained to `{"items": [...]}` (see
        # app/schemas/ai_extraction.ExtractedInventoryList). Unwrapping it
        # here means callers keep receiving a plain list and none of them
        # had to change.
        if isinstance(parsed, dict):
            for key in ("items", "entries", "results", "data"):
                value = parsed.get(key)
                if isinstance(value, list):
                    return value
    except (json.JSONDecodeError, TypeError):
        pass

    for start in (index for index, char in enumerate(text) if char == "["):
        end, spans = _scan_bracketed(text, start, "[", "]")
        if end is not None:
            try:
                parsed = json.loads(text[start:end])
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                if parsed:
                    return parsed
                continue  # empty-in-prose is more often a false start -- keep looking
        salvaged = _salvage_array_objects(text, spans)
        if salvaged:
            return salvaged
    return []


def extract_json_object(raw_text: str) -> dict:
    """Pulls the first genuinely-populated JSON object out of a model's
    raw answer -- the object-shaped counterpart to `extract_json_array`
    above, and the direct fix for this module's own bug-fix note: no
    more greedy first-`{`-to-last-`}` regex. Tries a strict full-text
    parse first, then a bracket-matching scan of every `{` in the
    (reasoning-stripped) text, taking the first candidate that parses to
    a non-empty dict. If a candidate object never closes (truncated
    mid-generation), attempts one salvage: cut the text at the end of the
    last fully-formed top-level "key": value pair and close it with `}`
    -- recovering, say, a recipe's title/description/ingredients even
    when a long instructions list got cut off mid-array. Returns `{}`
    (never raises, never returns None) when nothing usable is found,
    matching every existing caller's "empty dict means nothing extracted"
    contract."""
    if not isinstance(raw_text, str) or not raw_text.strip():
        return {}

    text = strip_reasoning(raw_text)

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, TypeError):
        pass

    for start in (index for index, char in enumerate(text) if char == "{"):
        end, _spans = _scan_bracketed(text, start, "{", "}")
        if end is not None:
            try:
                parsed = json.loads(text[start:end])
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and parsed:
                return parsed
            continue  # empty-in-prose ("{}" as part of an unrelated example) -- keep looking

        salvage_point = _scan_top_level_pair_boundary(text, start)
        if salvage_point is not None:
            candidate = text[start:salvage_point] + "}"
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and parsed:
                return parsed
    return {}
