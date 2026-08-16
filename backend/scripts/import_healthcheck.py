"""A text health report on what the bookmarks importer actually stored.

Written 2026-08-07 after a run of import defects that were each invisible
in the UI and obvious in the data: an ingredient named `raw` (the comma
split filing the food as a prep note), an amount swallowed into a name
(the bullet bug), a product page saved as a recipe, and one page imported
twice under two URLs.

Every check below corresponds to one of those, and none of them could
have been found by looking at the app. The recipe page re-joins name and
prep note for display, so `name='raw', prep='local honey'` reads on
screen as "2 tbsp raw, local honey" -- correct-looking, and wrong in the
database. It took a `repr()` query to see it.

Read-only. Run it after confirming a batch:

    docker compose exec -T chef python /app/scripts/import_healthcheck.py
"""

import re
import sqlite3
from collections import Counter

DB_PATH = "/app/data/chef.db"

# Mirrors recipe_service._QUALITY_WORDS. Duplicated rather than imported
# so this stays a plain sqlite reader that runs even when the app package
# will not import -- which is exactly the situation where someone most
# wants to look at the data.
QUALITY = {
    "raw", "fresh", "freshly", "dried", "dry", "frozen", "canned", "cooked", "uncooked",
    "ground", "whole", "halved", "unsalted", "salted", "sweetened", "unsweetened",
    "organic", "local", "plain", "pure", "natural", "chopped", "minced", "sliced",
    "diced", "grated", "shredded", "crushed", "toasted", "roasted", "warm", "cold",
    "chilled", "room", "hot", "lukewarm", "melted", "softened", "packed", "heaping",
    "level", "large", "medium", "small", "extra", "good", "ripe", "firm", "soft",
}  # fmt: skip

MAX_SHOWN = 60


def words(text):
    return [w for w in re.split(r"[^a-z]+", (text or "").lower()) if w]


def normalise_url(url):
    """Rough equivalent of bookmark_import_service.normalize_url -- enough
    to spot two rows that are the same page, not enough to rely on for
    anything else."""
    if not url:
        return ""
    url = re.sub(r"#.*$", "", url)
    url = re.sub(r"[?&](utm_[a-z_]+|fbclid|gclid|_hs[a-z]+|ref|uid|uatoken|uaexptime)=[^&]*", "", url)
    return re.sub(r"^https?://(www\.)?", "", url).rstrip("/").lower()


def report(title, lines):
    print(f"\n--- {title}: {len(lines)}")
    for line in lines[:MAX_SHOWN]:
        print("   ", line)
    if len(lines) > MAX_SHOWN:
        print(f"    ... and {len(lines) - MAX_SHOWN} more")


connection = sqlite3.connect(DB_PATH)
connection.row_factory = sqlite3.Row

recipes = list(connection.execute("SELECT id, title, source, source_url, default_servings FROM recipes ORDER BY id"))
ingredients = list(
    connection.execute("SELECT recipe_id, ingredient_name, quantity, unit, prep_note FROM recipe_ingredients")
)

by_recipe = {}
for row in ingredients:
    by_recipe.setdefault(row["recipe_id"], []).append(row)

print("=" * 72)
print(f"RECIPES: {len(recipes)}     INGREDIENT ROWS: {len(ingredients)}")
print("by source:", dict(Counter(r["source"] for r in recipes)))
print("=" * 72)

# 1. The comma-split defect: a name made only of quality words means the
#    food itself was filed as a prep note.
report(
    "QUALITY-WORD NAMES (want 0 -- the comma-split bug)",
    [
        f"r{row['recipe_id']:<4} name={row['ingredient_name']!r:<28} prep={row['prep_note']!r}"
        for row in ingredients
        if words(row["ingredient_name"]) and all(w in QUALITY for w in words(row["ingredient_name"]))
    ],
)

# 2. The bullet defect: an amount swallowed into the name. A few real
#    names carry a digit ("2% milk"), so this is a look-at-these list
#    rather than a count that must be zero.
report(
    "NAMES CONTAINING A DIGIT (mostly want 0 -- the bullet bug)",
    [
        f"r{row['recipe_id']:<4} name={row['ingredient_name']!r}"
        for row in ingredients
        if row["ingredient_name"] and re.search(r"\d", row["ingredient_name"])
    ],
)

# 3. Product pages and listings that got saved anyway.
report(
    "RECIPES WITH <2 INGREDIENTS (product page?)",
    [
        f"r{r['id']:<4} {(r['title'] or '')[:45]:<45} ingredients={len(by_recipe.get(r['id'], []))}"
        for r in recipes
        if len(by_recipe.get(r["id"], [])) < 2
    ],
)

# 3b. Pages that parsed cleanly and are not recipes. Curation, not a
#     defect -- these are FLAGGED for a person, never acted on. A recipe
#     with no stated amounts is a real thing (this catalog has several),
#     and so is a one-ingredient one, so no single signal is enough to
#     say "delete this". Two or more is worth a look.
LOOKS_LIKE_AN_ARTICLE = re.compile(
    r"\b(guide|collection|analysis|uses? for|substitutions?|selection|tips?|"
    r"\d+\s+(ways?|steps?|things|recipes|flavors)|how to make perfect|what (is|are)|"
    r"method|blend[s]?\b)",
    re.IGNORECASE,
)

suspects = []
for r in recipes:
    rows = by_recipe.get(r["id"], [])
    signals = []
    if rows and all(i["quantity"] is None for i in rows):
        signals.append("no amounts at all")
    if LOOKS_LIKE_AN_ARTICLE.search(r["title"] or ""):
        signals.append("title reads like an article")
    if (r["default_servings"] or 0) > 50:
        signals.append(f"servings={r['default_servings']}")
    if len(rows) > 20:
        signals.append(f"{len(rows)} ingredients")
    if len(signals) >= 2:
        suspects.append(f"r{r['id']:<4} {(r['title'] or '')[:44]:<44} {'; '.join(signals)}")

report("MAYBE NOT RECIPES -- your call, nothing is changed here", suspects)

# 4. Duplicates that got past dedup -- by title, and by the URL they came
#    from, which is the one that would indicate a real regression.
titles = Counter((r["title"] or "").strip().lower() for r in recipes)
report("DUPLICATE TITLES", [f"{title[:50]:<50} x{n}" for title, n in titles.items() if n > 1])

urls = Counter(normalise_url(r["source_url"]) for r in recipes if r["source_url"])
report("DUPLICATE SOURCE URLS", [f"{url[:60]:<60} x{n}" for url, n in urls.items() if n > 1])

# 5. Everything, one line each, so the shape of a batch is readable at a
#    glance and anything odd stands out without a query of its own.
print("\n" + "=" * 72)
print("ALL RECIPES")
print("=" * 72)
for r in recipes:
    rows = by_recipe.get(r["id"], [])
    without_quantity = sum(1 for i in rows if i["quantity"] is None)
    tags = [
        t[0]
        for t in connection.execute(
            "SELECT m.name FROM meal_tags m JOIN recipe_tag_links l ON l.tag_id = m.id WHERE l.recipe_id = ?",
            (r["id"],),
        )
    ]
    print(
        f"r{r['id']:<4} {(r['title'] or '')[:46]:<46} "
        f"ing={len(rows):<3} noqty={without_quantity:<3} serv={r['default_servings']!s:<4} "
        f"{r['source']:<18} {','.join(tags)}"
    )
