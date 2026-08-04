"""What this build ships for each GUI-editable extraction prompt, and the
rule that tells a household's edit apart from a copy of a default.

`ollama_client.get_active_prompt` returns a `SystemPrompt` row when one
exists and the module constant is only the fallback, so a row is an
OVERRIDE: it beats whatever text a later release ships. That is sound
only if a row means somebody deliberately saved an edit.

Seeding a copy of each default into the table on first boot broke exactly
that. An untouched seeded row is byte-identical to a deliberate edit, so
every improved default shipped after an install's first boot was dead on
arrival for that install, and a prompt change could be measured against a
live model as "no better" when it had never executed at all.

The rule now:

* no row            -> use what this build ships; Settings shows that
                       text as placeholder, greyed, not as an edit
* row, is_active    -> a saved household edit, and it wins
* row, not active   -> a saved household draft, kept but not used

Nothing seeds these rows any more. A row exists only because someone
pressed Save.

`prune_unedited_prompt_rows` repairs installs created under the old
behaviour: content equal to a text this project shipped is not an edit,
so the row goes and the install lands in the same no-row state a fresh
install gets.

`_SHIPPED_SHA256` is a CLOSED set, not a growing one. Seeding existed
only between commit 1fd5b77 (which introduced it) and this change, so the
only texts a seeded row can hold are the ones live in that window. The
digests below were produced by resolving each constant's value at every
commit in `git log 1fd5b77^..HEAD` and keeping the ones that differ from
the current default; the current defaults are hashed at import time
rather than pinned, so editing a prompt constant never desynchronises
this file. Character lengths are recorded next to each digest so a wrong
entry is visible rather than silently inert.
"""

from __future__ import annotations

import hashlib

from sqlalchemy.orm import Session

from app.models import SystemPrompt
from app.routers.inventory import RECEIPT_IMPORT_PROMPT, VISION_PROMPT
from app.services.recipe_service import RECIPE_IMPORT_PROMPT, RECIPE_MODIFY_INSTRUCTIONS

# The prompts whose shipped text lives in code and is therefore safe to
# fall back to. `main_chef` and `dietary_onboarding` are deliberately NOT
# here: they have no code-level fallback (`get_active_prompt(...) or ""`),
# their row IS the value, and seed.py still creates them.
IMPORT_PROMPT_DEFAULTS: dict[str, str] = {
    "recipe_import": RECIPE_IMPORT_PROMPT,
    "recipe_modify": RECIPE_MODIFY_INSTRUCTIONS,
    "receipt_import": RECEIPT_IMPORT_PROMPT,
    "vision_intake": VISION_PROMPT,
}

# Texts this project shipped as a default at some point inside the
# seeding window, other than the current ones. See the module docstring
# for how this set is bounded and why it cannot grow.
_HISTORICAL_SHIPPED_SHA256: dict[str, set[str]] = {
    # the prompt rewrite introduced at 1660aa3 and reverted at abc621d, 4728 chars
    "recipe_import": {"23210b196c08e655b02f059c2317f52f2c4d2a4456a428621ac9b0012cffd4ff"},
    "recipe_modify": set(),
    # the text live at 1fd5b77, superseded at 759bb06, 2676 chars
    "receipt_import": {"567155fba97694e08385683fa4ba4ee5b68d5e1ac4a5da3c21106e1714dd2db7"},
    # the text live at 1fd5b77, superseded at 759bb06, 763 chars
    "vision_intake": {"c0ff52fbef773b28dabff47297b8f14a11c2133e0c12e6b8b64c9415f787ace5"},
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def is_shipped_default(prompt_key: str, content: str) -> bool:
    """True if `content` is character-identical to a text this project
    shipped as the default for `prompt_key`.

    Exact comparison on purpose. A near-match is an edit -- a household
    that changed one word meant to change one word -- and treating it as
    "close enough to a default" would throw that edit away."""
    default = IMPORT_PROMPT_DEFAULTS.get(prompt_key)
    if default is None:
        return False
    digest = _sha256(content)
    return digest == _sha256(default) or digest in _HISTORICAL_SHIPPED_SHA256.get(prompt_key, set())


def prune_unedited_prompt_rows(db: Session) -> list[str]:
    """Delete rows that only exist because an older build seeded them.

    Returns the keys pruned, so the caller can say so in the boot log --
    an install silently changing which prompt it runs is the failure this
    whole module exists to end, so it gets announced."""
    pruned = []
    for prompt_key in IMPORT_PROMPT_DEFAULTS:
        row = db.query(SystemPrompt).filter_by(prompt_key=prompt_key).first()
        if row is not None and is_shipped_default(prompt_key, row.content):
            db.delete(row)
            pruned.append(prompt_key)
    if pruned:
        db.commit()
    return pruned


def active_overrides(db: Session) -> list[str]:
    """Keys whose shipped default is currently being overridden by a
    household edit. Logged at boot next to the prune result: after this
    change, anything left here is a real edit, and a household reading
    "the new default did nothing" deserves to see why in the log."""
    rows = db.query(SystemPrompt).filter_by(is_active=True).all()
    return sorted(r.prompt_key for r in rows if r.prompt_key in IMPORT_PROMPT_DEFAULTS)
