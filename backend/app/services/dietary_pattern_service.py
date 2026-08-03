"""Backlog B2.3: a selectable, structured dietary-goal preset that
concretely biases meal-plan generation, rather than leaving a cholesterol
goal as free text (`HouseholdPreferences.goals`) for the model to
interpret differently every week.

Why this exists: `goals` already lets a household type "reduce LDL
cholesterol," and the model reads it -- but nothing in the generation
prompt gives it a concrete lever to pull, so the actual ingredient
choices it makes to satisfy that goal vary run to run and are never
checkable. This module is a small, server-driven registry (same pattern
as `allergen_service.ALLERGEN_CHOICES`/`OBSERVANCE_LEVELS`) mapping a
preset key to a specific block of generation guidance, so a household
selects a pattern once and every subsequent generation gets the same
concrete instructions.

Only one preset is implemented: `portfolio_ldl`, matching the author's
own stated LDL-reduction goal and this app's own
`default_knowledge/portfolio_diet_ldl_cholesterol.md` reference file
(B2.1) -- the two are meant to be read together, not duplicated content.
DASH and Mediterranean were both named as candidate patterns in the
original B2 backlog text; this module's registry shape supports adding
either later (a new dict entry plus its own guidance string), but doing
so is deliberately left as future work rather than built speculatively
here -- Portfolio is the pattern the household's own stated goal
actually calls for.
"""

from __future__ import annotations

DIETARY_PATTERNS: list[dict] = [
    {
        "key": "portfolio_ldl",
        "label": "Portfolio diet (LDL cholesterol reduction)",
        "description": (
            "Biases ingredient selection toward the Portfolio diet's four "
            "cholesterol-lowering components: viscous/soluble fiber, plant "
            "sterols, soy protein, and nuts. See the bundled "
            "portfolio_diet_ldl_cholesterol.md knowledge file for the "
            "research this is based on."
        ),
    },
]
DIETARY_PATTERN_KEYS: frozenset[str] = frozenset(p["key"] for p in DIETARY_PATTERNS)

# Keyed the same as DIETARY_PATTERNS so a future second preset is just
# another dict entry here, not a structural change.
_PATTERN_GUIDANCE: dict[str, str] = {
    "portfolio_ldl": (
        "This household follows a Portfolio-diet-inspired approach to "
        "reduce LDL cholesterol. Across the week (not necessarily every "
        "single meal), work in a sensible mix of:\n"
        "- Viscous/soluble fiber: oats (certified gluten-free if the "
        "household restricts gluten), barley (only if gluten is not "
        "restricted), psyllium, eggplant, okra, or legumes (beans, "
        "lentils, chickpeas).\n"
        "- Plant sterols: foods naturally containing them (vegetable "
        "oils, nuts, seeds) or a sterol-fortified product only if one is "
        "already in the household's inventory or recipe catalog -- do "
        "not invent a fortified product that doesn't exist.\n"
        "- Soy protein: tofu, soy milk, edamame, or similar, used in "
        "place of some animal protein on at least a couple of meals.\n"
        "- Tree nuts: almonds, walnuts, or similar as a snack or recipe "
        "component, in place of less healthy fat sources.\n"
        "Favor lower-saturated-fat preparations generally. Do not treat "
        "this as a rule that every meal must contain all four components "
        "-- that isn't how the pattern works and produces an unrealistic, "
        "repetitive plan. Never claim a specific numeric cholesterol "
        "outcome; this is a dietary pattern, not a guaranteed result."
    ),
}


def get_pattern_guidance(pattern_key: str | None) -> str | None:
    """Returns the generation-prompt guidance block for a pattern key, or
    None for no pattern selected / an unrecognized key (defensive -- the
    API layer validates against DIETARY_PATTERN_KEYS, but a stale value
    left over from a removed pattern should degrade to "no guidance"
    rather than erroring generation)."""
    if not pattern_key:
        return None
    return _PATTERN_GUIDANCE.get(pattern_key)
