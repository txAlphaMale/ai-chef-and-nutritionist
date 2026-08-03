"""Ingredient identity resolution -- the app's join key, made explicit.

Audit finding P1-5. Inventory rows, recipe ingredients, grocery lines and
price lookups are all reconciled by matching a free-text ingredient name
against a free-text inventory name. Before this module existed, every one
of those call sites did the same thing: an exact case-insensitive compare,
then `ILIKE %name%` -- raw substring, in *either* direction at two of them
-- and took the first row the database happened to return.

That is wrong in three distinct ways, all of them silent:

- Substring matching ignores word boundaries. `"egg"` matched
  `"eggplant"`, `"oil"` matched `"oil-packed tuna"`. The match is not
  merely imprecise, it is a different food.
- Substring matching ignores what the extra words MEAN. `"chicken"`
  matched `"chicken broth"`; broth is not chicken, and deducting a
  recipe's chicken from a carton of stock corrupts both numbers.
- `inventory_items.name` has no unique constraint (deliberately -- two
  cartons of milk with different expiration dates are two legitimate
  rows), so "the first row" was whichever rowid came back first. Which
  row got decremented was undefined and could differ between calls.

This module replaces all of that with one resolution layer:

1. **Normalisation** (`normalize_name`) -- case, accents, punctuation and
   whitespace folded; conservative singularisation; and a second "core"
   form with preparation words removed, kept alongside the full form
   rather than replacing it.
2. **Word-boundary token matching** -- names are compared as token
   sequences, never as raw substrings. `"egg"` and `"eggplant"` share no
   token, so they do not match at all. This is the single change that
   removes the whole class of defect above.
3. **Transformation head-nouns** -- a curated list of words that mean
   "this is a product DERIVED from the other ingredient, not the
   ingredient itself" (broth, oil, flour, milk, extract, ...). When such a
   word appears in one name and not the other, the pair is BLOCKED
   outright rather than scored low, because the two names denote
   different foods no matter how much text they share. The list is
   curated and therefore non-exhaustive; it is also user-extendable from
   the Settings page (`ingredient_transformation_words`) precisely
   because no fixed list can be complete.
4. **Longest-match-wins with head agreement** -- among candidates that
   survive, a more specific name that ends in the same head noun
   ("olive oil" for a query of "oil") outranks a partial overlap that
   does not ("oil-packed tuna"). English food compounds are head-final,
   so the last token carries the identity and a token subset that is not
   a suffix is a weaker signal, not an equal one.
5. **Confidence, reported rather than hidden** -- every match carries a
   score, a band, and a human-readable reason. Call sites choose their
   own threshold based on what a wrong answer costs there (see
   `THRESHOLD_*` below), and anything under the threshold is returned as
   a ranked suggestion for the user to confirm instead of being applied
   silently.
6. **A persisted alias table** (`IngredientAlias`) -- when the user does
   confirm a match the matcher could not make on its own, that correction
   is remembered and applied at full confidence from then on. This is the
   escape hatch that makes the conservative thresholds above liveable:
   the matcher never has to guess twice about the same name.

What this module deliberately does NOT do: fuzzy/edit-distance matching
(Levenshtein, trigram, phonetic). Those trade a bounded, explainable
failure mode -- "no confident match, here are the candidates" -- for an
unbounded one, where a typo threshold that fixes `"tomatos"` also makes
`"cumin"` match `"cumin seed"` and `"butter"` match `"batter"`. Given
that a wrong answer here silently corrupts inventory counts and drops
items off a grocery list, refusing to match is the better failure.

Nothing here is AI-driven or network-backed: it is deterministic, pure
where it can be, and directly unit-testable -- the same discipline
allergen_service already follows, and for the same reason (a silent wrong
answer is worse than a visible non-answer).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

# --- Confidence bands ---------------------------------------------------
#
# A float score plus a discrete band. The band is what the API and UI
# speak in; the score is what ranking uses and what a test pins.

CONFIDENCE_EXACT = "exact"
CONFIDENCE_HIGH = "high"
CONFIDENCE_MEDIUM = "medium"
CONFIDENCE_LOW = "low"
CONFIDENCE_NONE = "none"

SCORE_EXACT = 0.98
SCORE_HIGH = 0.75
SCORE_MEDIUM = 0.50
SCORE_LOW = 0.25

# Below this, a pair is not reported as a candidate at all. A shared
# token that carries almost none of either name (e.g. "sauce" in
# "cranberry sauce" vs "soy sauce") is noise, not a suggestion.
SCORE_FLOOR = SCORE_LOW

# --- Per-call-site thresholds -------------------------------------------
#
# The correct threshold is a function of what a WRONG match costs at that
# call site, not of how confident the matcher usually is. These three
# constants are the whole policy, in one place, so it can be read and
# argued with rather than being re-decided ad hoc at each caller.

# Writes to the database (inventory deduction, name-based update). A wrong
# match here changes a stored number and there is no undo. Anything short
# of a high-confidence match refuses and asks -- see
# inventory_service.resolve_for_write.
THRESHOLD_DESTRUCTIVE = SCORE_HIGH

# Reconciling a grocery line against stock, and looking up a price. A
# wrong match here produces a visibly wrong list or a wrong dollar figure
# that the user reads before acting on it, so a medium-confidence match is
# acceptable when it is reported as such.
THRESHOLD_ADVISORY = SCORE_MEDIUM

# A pantry-staple hit REMOVES a line from the grocery list entirely --
# the failure mode is "you did not buy dinner", with nothing on screen to
# notice. Held to the same bar as a database write.
THRESHOLD_SUPPRESSING = SCORE_HIGH


# --- Normalisation ------------------------------------------------------

# Tokens that carry no identity and only add noise to overlap ratios.
_STRUCTURAL_STOPWORDS = frozenset(
    {"a", "an", "the", "of", "and", "or", "with", "in", "for", "to", "into", "plus", "some", "any"}
)

# Preparation, presentation and grading words. Removed only when building
# the *core* form, which is scored at a lower tier than the full form --
# so stripping one of these can promote a match that would otherwise be
# missed, but can never override a better full-form match.
#
# Curated, not exhaustive, and deliberately conservative about words that
# change what the food IS rather than how it was cut or graded. "ground"
# is a notable exclusion: "ground cinnamon" and "cinnamon" should match
# (and do, via the head-noun suffix rule below, without needing this
# list), but "ground beef" and "beef" are not interchangeable for either
# a recipe or an inventory count.
_PREPARATION_WORDS = frozenset(
    {
        # knife work / physical prep
        "chopped",
        "diced",
        "minced",
        "sliced",
        "shredded",
        "grated",
        "crushed",
        "mashed",
        "cubed",
        "julienned",
        "halved",
        "quartered",
        "peeled",
        "seeded",
        "cored",
        "pitted",
        "trimmed",
        "deboned",
        "shelled",
        "husked",
        "zested",
        "beaten",
        "whisked",
        "sifted",
        # state at time of use
        "melted",
        "softened",
        "thawed",
        "defrosted",
        "drained",
        "rinsed",
        "washed",
        "packed",
        "rounded",
        "heaping",
        "level",
        "warmed",
        "chilled",
        "cooled",
        # adverbs that only ever qualify one of the above
        "finely",
        "roughly",
        "thinly",
        "thickly",
        "coarsely",
        "freshly",
        "lightly",
        "well",
        "very",
        "approximately",
        "about",
        # size / grade / marketing qualifiers
        "large",
        "small",
        "medium",
        "extra",
        "jumbo",
        "baby",
        "mini",
        "fresh",
        "ripe",
        "organic",
        "natural",
        "premium",
        "quality",
        "grade",
        "boneless",
        "skinless",
        # recipe-list bookkeeping that survives parsing
        "divided",
        "optional",
        "needed",
        "taste",
        "garnish",
        "serving",
        "topping",
        "room",
        "temperature",
    }
)

# Words whose presence means "a product MADE FROM the other thing", not
# "a more specific kind of the other thing". When one name has one of
# these and the other does not, the two names denote different foods and
# the pair is blocked -- no score, no suggestion, no silent deduction.
#
# This is the list that stops "chicken" deducting from "chicken broth"
# and "almond" from "almond milk". It is hand-curated and therefore
# incomplete by construction, which is exactly why
# `load_transformation_words` lets the household edit it from Settings
# rather than shipping it as a fixed fact.
#
# Its failure mode is safe in both directions: a word that SHOULD be here
# and is not leaves a pair scored as a low-confidence suggestion (visible,
# confirmable) rather than auto-applied; a word wrongly added here
# produces "no match, please pick one" rather than a wrong match.
DEFAULT_TRANSFORMATION_WORDS: tuple[str, ...] = (
    "broth",
    "stock",
    "bouillon",
    "consomme",
    "soup",
    "sauce",
    "gravy",
    "paste",
    "puree",
    "concentrate",
    "extract",
    "essence",
    "oil",
    "butter",
    "margarine",
    "shortening",
    "lard",
    "milk",
    "cream",
    "creamer",
    "yogurt",
    "cheese",
    "flour",
    "meal",
    "starch",
    "powder",
    "granules",
    "juice",
    "cider",
    "nectar",
    "syrup",
    "jam",
    "jelly",
    "preserves",
    "marmalade",
    "vinegar",
    "wine",
    "liqueur",
    "seasoning",
    "rub",
    "marinade",
    "dressing",
    "dip",
    "spread",
    "chips",
    "crisps",
    "crackers",
    "jerky",
    "sausage",
    "bacon",
    "ham",
    "salami",
    "pepperoni",
    "bread",
    "roll",
    "bun",
    "tortilla",
    "pasta",
    "noodles",
    "cereal",
    "granola",
    "bar",
    "water",
    "tea",
    "coffee",
    "smoothie",
    "shake",
)

DEFAULT_TRANSFORMATION_SET: frozenset[str] = frozenset(DEFAULT_TRANSFORMATION_WORDS)

# Plurals that the rules below get wrong, and singulars that only LOOK
# plural. Both lists are short by design -- every entry is a word this
# app has a concrete reason to handle, not a general-purpose inflection
# dictionary.
_IRREGULAR_PLURALS: dict[str, str] = {
    "leaves": "leaf",
    "loaves": "loaf",
    "halves": "half",
    "knives": "knife",
    "calves": "calf",
    "shelves": "shelf",
    "geese": "goose",
    "teeth": "tooth",
    "feet": "foot",
    "mice": "mouse",
    "children": "child",
    "people": "person",
}

# Words ending in -s (or worse, -sses) that are already singular. Without
# this, "molasses" singularises to "molass" and stops matching itself.
_NEVER_SINGULARIZE = frozenset(
    {
        "molasses",
        "asparagus",
        "hummus",
        "couscous",
        "grits",
        "chives",
        "watercress",
        "cress",
        "swiss",
        "bass",
        "haddock",
        "plaice",
        "salmon",
        "series",
        "species",
        "gas",
        "grass",
        "glass",
        "class",
        "mass",
        "cous",
        "brussels",
        "greens",
        "oats",
        "beans",
        "peas",
        "lentils",
        "sprouts",
        "noodles",
        "crackers",
        "chips",
    }
)


def singularize(word: str) -> str:
    """Conservative English singularisation for food words.

    Deliberately rule-based and short rather than a full inflection
    library: the input vocabulary here is food nouns, the rules that
    matter are few, and every one of them is directly testable. Anything
    the rules would get wrong lives in `_IRREGULAR_PLURALS` or
    `_NEVER_SINGULARIZE` as an explicit entry rather than being papered
    over with a fuzzier rule.

    Note the interaction with `_NEVER_SINGULARIZE`: several entries there
    (beans, lentils, oats, greens) are words whose plural form is how the
    ingredient is normally written. Folding them to a singular is not
    wrong exactly, but it is churn -- both the recipe and the inventory
    row will spell it the same way, so leaving them alone keeps the
    normalised forms identical without a rule having to fire at all."""
    if not word:
        return word
    if word in _NEVER_SINGULARIZE:
        return word
    if word in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[word]
    if len(word) <= 3 or not word.endswith("s"):
        return word
    if word.endswith("ss") or word.endswith("us") or word.endswith("is"):
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"  # berries -> berry
    if word.endswith("sses"):
        return word[:-2]  # glasses -> glass
    if word.endswith(("ches", "shes", "xes", "zes")):
        return word[:-2]  # peaches -> peach, dishes -> dish, boxes -> box
    if word.endswith("oes") and len(word) > 4:
        return word[:-2]  # tomatoes -> tomato, potatoes -> potato
    return word[:-1]


_NON_WORD = re.compile(r"[^0-9a-z]+")


def _fold(text: str) -> str:
    """Lowercase, strip accents, and reduce anything that is not a letter
    or digit to a single space. Accent folding matters for real data:
    "jalapeño" and "jalapeno" are the same pepper, and which spelling
    arrives depends on whether the row came from a keyboard, an OCR pass
    or a model's output."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_WORD.sub(" ", without_accents).strip()


@dataclass(frozen=True)
class NormalizedName:
    """Both forms of a normalised name, kept together.

    `tokens` is the full normalised token sequence; `core_tokens` is the
    same sequence with structural and preparation words removed. Scoring
    consults the full form first and only falls back to the core form, so
    dropping a preparation word can rescue a match that would otherwise
    be missed but can never outrank a match that needed no rescuing."""

    raw: str
    normalized: str
    tokens: tuple[str, ...]
    core_tokens: tuple[str, ...]

    @property
    def is_empty(self) -> bool:
        return not self.tokens

    @property
    def head(self) -> str | None:
        """The identity-bearing token. English food compounds are
        head-final -- "olive oil" is an oil, "oil-packed tuna" is a tuna
        -- so the last core token is what the name is actually naming."""
        return self.core_tokens[-1] if self.core_tokens else None


def normalize_name(name: str | None) -> NormalizedName:
    """Folds a free-text ingredient or inventory name into comparable
    token sequences. Never raises; an empty or punctuation-only input
    produces an empty NormalizedName that matches nothing."""
    raw = (name or "").strip()
    folded = _fold(raw)
    if not folded:
        return NormalizedName(raw=raw, normalized="", tokens=(), core_tokens=())

    tokens = tuple(singularize(word) for word in folded.split() if word not in _STRUCTURAL_STOPWORDS)
    # Bare numbers are dropped from the core form as well as preparation
    # words. A quantity that leaked through ingredient parsing ("2 large
    # ripe avocados") is not part of what the ingredient IS, but it does
    # add a token, and token count is what the suffix rung uses to decide
    # which of two names is the more specific one -- so leaving it in
    # made "2 large ripe avocados" look MORE specific than "avocado" and
    # docked the match for it. Dropped only when a non-numeric token
    # survives, so a name that is somehow all digits still matches itself.
    core = tuple(word for word in tokens if word not in _PREPARATION_WORDS)
    non_numeric = tuple(word for word in core if not word.isdigit())
    core = non_numeric or core
    # If a name is made ENTIRELY of preparation words ("chopped fresh"),
    # keep the full token list as its own core rather than leaving it
    # empty -- an empty core would match every other empty core.
    return NormalizedName(raw=raw, normalized=" ".join(tokens), tokens=tokens, core_tokens=core or tokens)


# --- Scoring ------------------------------------------------------------


@dataclass(frozen=True)
class MatchScore:
    score: float
    reason: str
    blocked_by: str | None = None

    @property
    def confidence(self) -> str:
        return confidence_band(self.score)


def confidence_band(score: float) -> str:
    if score >= SCORE_EXACT:
        return CONFIDENCE_EXACT
    if score >= SCORE_HIGH:
        return CONFIDENCE_HIGH
    if score >= SCORE_MEDIUM:
        return CONFIDENCE_MEDIUM
    if score >= SCORE_LOW:
        return CONFIDENCE_LOW
    return CONFIDENCE_NONE


NO_MATCH = MatchScore(score=0.0, reason="no shared words")


def _is_suffix(shorter: Sequence[str], longer: Sequence[str]) -> bool:
    """True if `shorter` is a contiguous suffix of `longer`. A suffix
    match is the narrowing case -- "oil" inside "olive oil", "tomato"
    inside "roma tomato" -- where the longer name is a specific kind of
    the shorter one. A non-suffix subset ("oil" inside "oil-packed tuna")
    is a different relationship entirely and is scored far lower."""
    return len(shorter) <= len(longer) and tuple(longer[len(longer) - len(shorter) :]) == tuple(shorter)


def score_names(
    query: NormalizedName,
    candidate: NormalizedName,
    transformation_words: frozenset[str] | None = None,
) -> MatchScore:
    """Scores one query/candidate pair. Pure, deterministic, no I/O.

    The ladder, highest rung first. Each rung is a strictly stronger
    statement about identity than the one below it, which is what makes
    "prefer the best match" meaningful rather than arbitrary:

      1.00  identical after normalisation
      0.98  identical token sequences (differed only in punctuation,
            accents, plural forms or word order noise)
      0.90  identical once preparation words are dropped
      0.70-0.95  one name is a suffix of the other -- same head noun,
            one is a more specific kind of it
      0.25-0.50  token overlap without head agreement -- shares words but
            names a different food; a suggestion, never an application
      0.00  no shared tokens, or blocked by a transformation word

    Blocking is checked before any of it: a transformation word present
    on one side and absent on the other short-circuits to zero regardless
    of how much other text the two names share."""
    words = DEFAULT_TRANSFORMATION_SET if transformation_words is None else transformation_words

    if query.is_empty or candidate.is_empty:
        return NO_MATCH

    if query.normalized == candidate.normalized:
        return MatchScore(1.0, "exact name match")

    q_tokens, c_tokens = set(query.tokens), set(candidate.tokens)
    if q_tokens == c_tokens:
        return MatchScore(0.98, "same words after normalising spelling and plurals")

    # Blocking: a transformation word on exactly one side.
    #
    # Checked against the FULL token sets, not the core ones, and before
    # any scoring rung -- "chicken" vs "chicken broth" must not be
    # rescued by the fact that it is otherwise a clean suffix match.
    only_in_candidate = c_tokens - q_tokens
    only_in_query = q_tokens - c_tokens
    for token in sorted(only_in_candidate | only_in_query):
        if token in words:
            side = candidate.raw if token in only_in_candidate else query.raw
            return MatchScore(
                0.0,
                f"{candidate.raw!r} and {query.raw!r} are not the same ingredient",
                blocked_by=(
                    f"{token!r} in {side!r} makes it a product made FROM the other ingredient, "
                    f"not the ingredient itself"
                ),
            )

    q_core, c_core = query.core_tokens, candidate.core_tokens
    if set(q_core) == set(c_core):
        dropped = sorted((q_tokens | c_tokens) - (set(q_core) & set(c_core)))
        return MatchScore(0.90, f"same ingredient once preparation words are ignored ({', '.join(dropped)})")

    shared = set(q_core) & set(c_core)
    if not shared:
        return NO_MATCH

    longer, shorter = (q_core, c_core) if len(q_core) >= len(c_core) else (c_core, q_core)
    overlap = len(shared) / len(longer)

    if set(shorter).issubset(set(longer)) and _is_suffix(shorter, longer):
        # Same head noun; the longer name is a more specific kind of the
        # shorter one. Full credit when the QUERY is the general one (a
        # recipe asking for "oil", a row of "olive oil" -- the row
        # satisfies the request). Slightly discounted the other way round
        # (a recipe asking for "olive oil" against a generic "oil" row),
        # because a generic row may or may not be the specific thing
        # asked for and the user should get a say.
        base = 0.70 + 0.25 * overlap
        query_is_general = len(q_core) <= len(c_core)
        score = base if query_is_general else base * 0.90
        direction = "more specific than" if query_is_general else "more general than"
        reason = f"{candidate.raw!r} is {direction} {query.raw!r}, same base ingredient"

        # One exception, and it is the reason this rung is not simply
        # "suffix match wins": when the shared head noun is itself a
        # transformation word, the qualifier in front of it may name the
        # SOURCE rather than a variety. "olive oil" is oil, but "peanut
        # butter" is not butter and "almond milk" is not milk -- same
        # grammar, opposite answers, and nothing in the strings tells
        # them apart. So the whole family is capped just below the
        # high-confidence threshold: advisory call sites still use it,
        # destructive ones ask once, and the answer is remembered as an
        # alias so it is never asked twice.
        #
        # Purely numeric qualifiers are exempt -- "2% milk" and "1% milk"
        # are grades of milk, not a different substance, and a digit
        # cannot be naming a source ingredient.
        head = longer[-1]
        extra = [t for t in longer if t not in set(shorter)]
        if head in words and any(not token.isdigit() for token in extra):
            capped = min(score, SCORE_HIGH - 0.05)
            if capped < score:
                return MatchScore(
                    round(capped, 4),
                    f"{reason}, but {head!r} products are often made FROM their qualifier "
                    f"rather than being a variety of it -- worth confirming",
                )
        return MatchScore(round(score, 4), reason)

    # Shares words but not a head noun: "chicken" vs "chicken breast",
    # "oil" vs "oil-packed tuna", "red bell pepper" vs "green bell
    # pepper". Structurally these are all the same case -- an attributive
    # compound whose head differs -- and no amount of string analysis
    # separates the reasonable one from the absurd one without knowing
    # what the foods are. So all three are reported as suggestions at low
    # confidence and none of them is ever applied on its own. Confirming
    # one writes an alias, and it is never asked again.
    score = 0.30 + 0.20 * overlap
    return MatchScore(
        round(score, 4),
        f"{candidate.raw!r} shares {', '.join(sorted(shared))} with {query.raw!r} but names a different food",
    )


# --- Ranking ------------------------------------------------------------


@dataclass
class Candidate:
    """One scored candidate. `payload` is whatever the caller passed in
    (an InventoryItem, a staple string) -- this module never needs to know
    what it is, which is what lets the same ranking serve inventory rows,
    pantry staples and priced rows alike."""

    name: str
    score: float
    confidence: str
    reason: str
    payload: Any = None
    blocked_by: str | None = None
    tiebreak: tuple = field(default=(), repr=False)


def rank_candidates(
    name: str,
    candidates: Iterable[tuple[str, Any]],
    transformation_words: frozenset[str] | None = None,
    tiebreak_key=None,
    limit: int | None = None,
) -> list[Candidate]:
    """Scores every candidate and returns those at or above SCORE_FLOOR,
    best first.

    `tiebreak_key(payload) -> tuple` decides the order of candidates that
    score *identically* -- which is the case the old code handled by
    taking whatever row the database returned first. Callers supply a
    tiebreak that means something for their use (see
    `inventory_tiebreak`); with none supplied, candidates keep their input
    order, which at least makes the result reproducible."""
    query = normalize_name(name)
    if query.is_empty:
        return []

    scored: list[Candidate] = []
    for index, (candidate_name, payload) in enumerate(candidates):
        result = score_names(query, normalize_name(candidate_name), transformation_words)
        if result.score < SCORE_FLOOR:
            continue
        tiebreak = tiebreak_key(payload) if tiebreak_key is not None else ()
        scored.append(
            Candidate(
                name=candidate_name,
                score=result.score,
                confidence=result.confidence,
                reason=result.reason,
                payload=payload,
                blocked_by=result.blocked_by,
                tiebreak=(*tiebreak, index),
            )
        )

    scored.sort(key=lambda c: (-c.score, c.tiebreak))
    return scored[:limit] if limit else scored


def best_match(
    name: str,
    candidates: Iterable[tuple[str, Any]],
    minimum_score: float,
    transformation_words: frozenset[str] | None = None,
    tiebreak_key=None,
) -> tuple[Candidate | None, list[Candidate]]:
    """Returns `(match, ranked)` where `match` is the top candidate only
    if it clears `minimum_score`, and `ranked` is always the full ranked
    list.

    Returning both is the point. A caller that gets `None` still has
    something specific to show the user -- "no confident match, did you
    mean one of these three?" -- instead of the bare 404 the old
    `find_by_name` produced, which told the user nothing about why."""
    ranked = rank_candidates(name, candidates, transformation_words, tiebreak_key)
    if ranked and ranked[0].score >= minimum_score:
        return ranked[0], ranked
    return None, ranked


# --- DB-backed resolution ----------------------------------------------
#
# Everything above this line is pure and testable without a database.
# Everything below it is the thin layer that feeds real inventory rows,
# the household's alias table and the household's edited transformation
# word list into those functions.

MAX_ALIAS_HOPS = 4


def load_transformation_words(db: Session) -> frozenset[str]:
    """The effective transformation-word list: the household's edited
    value from Settings if they have one, otherwise the curated default.

    Replaces rather than extends the default, and the Settings field is
    seeded with the default's own contents -- so "add a word" is an edit
    to a visible list rather than an invisible second list layered on
    top, and clearing the field restores the shipped default. Same
    override shape the editable system prompts already use."""
    from app.services import settings_service

    raw = settings_service.get_setting(db, "ingredient_transformation_words")
    words = {w.strip().lower() for w in (raw or "").replace("\n", ",").split(",") if w.strip()}
    return frozenset(words) if words else DEFAULT_TRANSFORMATION_SET


def inventory_tiebreak(item) -> tuple:
    """Ordering among inventory rows that score IDENTICALLY.

    The old code took whatever row came back first, which with no unique
    constraint on `inventory_items.name` meant the choice between two
    cartons of milk was undefined and could differ between calls. This
    makes it both deterministic and useful:

    1. Rows with stock on hand before empty ones -- deducting from a row
       already at zero accomplishes nothing and leaves the real stock
       untouched.
    2. Soonest expiration first. This is first-expired-first-out, the
       standard rotation practice, and it is also the behaviour the rest
       of this app already argues for: `compute_urgency` scores expiring
       items up precisely so they get used before they are thrown away.
    3. Oldest purchase first, then lowest id -- both pure tie-breakers,
       present so the result is fully determined rather than
       nearly-determined.

    A null expiration or purchase date sorts last within its group: an
    unknown date is not evidence of freshness."""
    from datetime import date as _date

    far_future = _date.max
    return (
        0 if (item.quantity or 0) > 0 else 1,
        item.expiration_date or far_future,
        item.purchased_date or far_future,
        item.id or 0,
    )


@dataclass
class Resolution:
    """The full result of resolving one free-text name against inventory.

    Carries the ranked alternatives even on success, so a UI can show
    "matched X (high confidence) -- not right?" without a second request,
    and carries `blocked` explanations so a user who expected a match can
    see WHY the matcher refused instead of being told only that nothing
    was found."""

    query: str
    normalized: str
    item: Any | None = None
    score: float = 0.0
    confidence: str = CONFIDENCE_NONE
    reason: str = ""
    via_alias: bool = False
    candidates: list[Candidate] = field(default_factory=list)
    blocked: list[Candidate] = field(default_factory=list)

    @property
    def matched(self) -> bool:
        return self.item is not None

    @property
    def needs_confirmation(self) -> bool:
        """True when the matcher found plausible candidates but none it
        was confident enough to apply. This is the state that should
        produce a question in the UI, and it is deliberately distinct
        from "nothing resembling this exists" -- the two want different
        messages and different next actions."""
        return self.item is None and bool(self.candidates)


def _lookup_alias(db: Session, normalized: str):
    from app.models import IngredientAlias

    if not normalized:
        return None
    return db.query(IngredientAlias).filter(IngredientAlias.alias_normalized == normalized).first()


def resolve(
    db: Session,
    name: str,
    minimum_score: float = THRESHOLD_DESTRUCTIVE,
    items: Sequence[Any] | None = None,
) -> Resolution:
    """Resolves a free-text ingredient name to an inventory row.

    Order of operations:

    1. Follow the alias chain, if any. An alias rewrites the query to its
       canonical name and resolution continues from there, so aliases
       compose and keep working when the row they were first taught on is
       used up and re-bought. Chains are bounded by MAX_ALIAS_HOPS and
       guarded against cycles -- a household editing aliases by hand can
       trivially create "a -> b, b -> a", and that must not hang a
       request.
    2. If the alias pinned a specific `inventory_item_id` and that row
       still exists, return it directly at full confidence. The user
       already answered this exact question.
    3. Otherwise score every inventory row (see `score_names`) and take
       the best one that clears `minimum_score`.

    `minimum_score` is the caller's own policy, not a global constant --
    see the THRESHOLD_* constants for what each call site uses and why.
    `items` lets a caller that has already loaded inventory pass it in
    rather than paying for a second query."""
    from app.models import InventoryItem

    original_query = (name or "").strip()
    query_name = original_query
    via_alias = False
    pinned_item = None

    seen: set[str] = set()
    for _ in range(MAX_ALIAS_HOPS):
        normalized = normalize_name(query_name).normalized
        if not normalized or normalized in seen:
            break
        seen.add(normalized)
        alias = _lookup_alias(db, normalized)
        if alias is None:
            break
        via_alias = True
        if alias.inventory_item_id is not None:
            pinned_item = db.get(InventoryItem, alias.inventory_item_id)
        query_name = alias.canonical_name

    resolved_norm = normalize_name(query_name)

    if pinned_item is not None:
        return Resolution(
            query=original_query,
            normalized=resolved_norm.normalized,
            item=pinned_item,
            score=1.0,
            confidence=CONFIDENCE_EXACT,
            reason=f"you previously confirmed {original_query!r} means {pinned_item.name!r}",
            via_alias=True,
        )

    rows = list(items) if items is not None else db.query(InventoryItem).all()
    words = load_transformation_words(db)
    ranked = rank_candidates(
        query_name,
        [(row.name, row) for row in rows],
        transformation_words=words,
        tiebreak_key=inventory_tiebreak,
    )

    # Blocked pairs score 0 and so never appear in `ranked`; re-score the
    # rows that share a word with the query purely to explain the refusal.
    blocked: list[Candidate] = []
    for row in rows:
        result = score_names(resolved_norm, normalize_name(row.name), words)
        if result.blocked_by:
            blocked.append(
                Candidate(
                    name=row.name,
                    score=0.0,
                    confidence=CONFIDENCE_NONE,
                    reason=result.reason,
                    payload=row,
                    blocked_by=result.blocked_by,
                )
            )

    top = ranked[0] if ranked else None
    if top is not None and top.score >= minimum_score:
        reason = top.reason
        if via_alias:
            reason = f"{original_query!r} is an alias for {query_name!r}; {reason}"
        return Resolution(
            query=original_query,
            normalized=resolved_norm.normalized,
            item=top.payload,
            score=top.score,
            confidence=top.confidence,
            reason=reason,
            via_alias=via_alias,
            candidates=ranked,
            blocked=blocked,
        )

    return Resolution(
        query=original_query,
        normalized=resolved_norm.normalized,
        via_alias=via_alias,
        candidates=ranked,
        blocked=blocked,
    )


def remember_alias(
    db: Session,
    alias_text: str,
    canonical_name: str,
    inventory_item_id: int | None = None,
    note: str | None = None,
):
    """Upserts one alias. Refuses a self-alias (a name pointing at its own
    normalised form), which would be a no-op row that only makes the
    Settings list harder to read, and refuses an empty target.

    Does NOT attempt to detect longer cycles at write time -- `resolve`
    already bounds and de-duplicates chain traversal, and rejecting a
    write because it would close a loop is a worse experience than
    following the loop safely and letting the user see both rows in the
    alias list."""
    from app.models import IngredientAlias

    alias_text = (alias_text or "").strip()
    canonical_name = (canonical_name or "").strip()
    normalized = normalize_name(alias_text).normalized
    canonical_normalized = normalize_name(canonical_name).normalized
    if not normalized or not canonical_normalized:
        raise ValueError("Both the alias and the ingredient it points at must be non-empty")
    if normalized == canonical_normalized and inventory_item_id is None:
        raise ValueError(f"{alias_text!r} already resolves to {canonical_name!r} without an alias")

    row = _lookup_alias(db, normalized)
    if row is None:
        row = IngredientAlias(alias_normalized=normalized, source="user")
        db.add(row)
    row.alias_text = alias_text
    row.canonical_name = canonical_name
    row.inventory_item_id = inventory_item_id
    row.note = note
    db.commit()
    db.refresh(row)
    return row


def forget_alias(db: Session, alias_id: int) -> bool:
    from app.models import IngredientAlias

    row = db.get(IngredientAlias, alias_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def list_aliases(db: Session) -> list:
    from app.models import IngredientAlias

    return db.query(IngredientAlias).order_by(IngredientAlias.alias_text).all()
