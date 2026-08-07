"""Bulk recipe import from a browser's exported bookmarks file.

Every browser exports the same thing -- the Netscape bookmark format,
unchanged since 1996 -- so one parser covers Chrome, Firefox, Edge and
Safari without sniffing which produced the file:

    <DL><p>
        <DT><H3>Recipes</H3>
        <DL><p>
            <DT><H3>Desserts</H3>
            <DL><p><DT><A HREF="https://...">Pumpkin Chiffon Pie</A>
        </DL><p>
    </DL><p>

It is not valid HTML -- those `<p>` tags are never closed and the `<DT>`
elements never nest the way the indentation suggests -- which is why this
parses with lxml rather than a strict parser: lxml recovers the tree a
browser would build, and the FOLDER STRUCTURE is recovered from that tree
rather than from the indentation, which is cosmetic.

Each URL is then imported through `recipe_service.parse_recipe_from_url`
-- the exact same path a single URL import takes, not a copy of it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import lxml.html
from sqlalchemy.orm import Session

from app.models import Recipe
from app.services import recipe_service

# Bookmarks folders hold bookmarks, not only recipes, and a fetch plus up
# to two model calls per URL is minutes of GPU time. The cap is a
# guardrail against pointing this at a 500-bookmark bar by accident, not
# a judgement about how many recipes a household may have -- the response
# says plainly how many were skipped and why.
MAX_URLS_PER_SCAN = 40

# javascript: and data: bookmarks are real and are not recipes.
ALLOWED_SCHEMES = ("http://", "https://")


# Query parameters that identify how somebody ARRIVED at a page rather
# than which page it is. Two bookmarks of the same recipe saved from a
# newsletter and from a search are the same recipe, and importing it
# twice because one carries `utm_source` is exactly the kind of duplicate
# a household then has to merge by hand.
_TRACKING_PARAMS = re.compile(r"^(utm_[a-z_]+|fbclid|gclid|mc_[a-z]+|ref|ref_src|igshid|_ga)$", re.IGNORECASE)


def normalize_url(url: str) -> str:
    """A comparison key for "is this the same page", NOT a rewritten URL.

    Deliberately conservative -- it lowercases the scheme and host, drops
    the fragment, strips a trailing slash and removes tracking
    parameters, and touches nothing else. Query strings are KEPT and
    sorted, because `?recipe=204` is the page on plenty of older sites and
    dropping it would collapse a whole blog into one entry.

    http and https are folded together: a bookmark saved in 2013 and the
    same page today differ by exactly that, and they are not two recipes.
    What is normalized here is only ever compared, never stored or
    fetched -- the original URL is what gets imported and cited."""
    try:
        parts = urlsplit((url or "").strip())
    except ValueError:
        return (url or "").strip().lower()
    if not parts.netloc:
        return (url or "").strip().lower()

    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    # Default ports carry no meaning.
    host = re.sub(r":(80|443)$", "", host)

    path = parts.path.rstrip("/") or "/"
    query = urlencode(
        sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING_PARAMS.match(k))
    )
    # Scheme folded to https so http/https variants compare equal.
    return urlunsplit(("https", host, path, query, ""))


def already_imported_urls(db: Session, urls: list[str]) -> set[str]:
    """Which of `urls` this household has already saved a recipe for.

    Compared on `normalize_url`, and the comparison runs over every
    recipe that HAS a source_url rather than as a SQL `IN` on the raw
    strings -- the whole point is that the stored string and the
    bookmarked string differ cosmetically, so matching them in SQL would
    find only the easy cases."""
    stored = {
        normalize_url(source_url)
        for (source_url,) in db.query(Recipe.source_url).filter(Recipe.source_url.isnot(None)).all()
        if source_url
    }
    return {url for url in urls if normalize_url(url) in stored}


@dataclass
class Bookmark:
    url: str
    title: str
    folder_path: str  # "Recipes/Desserts", or "" at the top level


def _folder_of(anchor) -> str:
    """The folder path an <A> sits in, read from the tree.

    Each nesting level is a <DL> whose name is the <H3> immediately
    before it. Walking ancestors and reading each one's heading is
    stable against the format's unclosed tags in a way that counting
    indentation is not."""
    names: list[str] = []
    for element in anchor.iterancestors():
        if element.tag != "dl":
            continue
        heading = element.getprevious()
        # lxml may leave the H3 as the DL's previous sibling, or inside
        # the DT that precedes it -- both shapes occur in real exports.
        if heading is not None and heading.tag == "h3":
            names.append((heading.text_content() or "").strip())
        elif heading is not None:
            found = heading.find(".//h3")
            if found is not None:
                names.append((found.text_content() or "").strip())
    return "/".join(name for name in reversed(names) if name)


def parse_bookmarks(html: str) -> list[Bookmark]:
    """Every http(s) bookmark in the file, with the folder it sits in.

    Deduplicated on URL, keeping the first occurrence: the same recipe
    filed in two folders is one recipe, and importing it twice would
    create two rows the household then has to merge by hand."""
    if not (html or "").strip():
        return []
    tree = lxml.html.fromstring(html)

    seen: set[str] = set()
    bookmarks: list[Bookmark] = []
    for anchor in tree.iter("a"):
        url = (anchor.get("href") or "").strip()
        if not url.lower().startswith(ALLOWED_SCHEMES) or url in seen:
            continue
        seen.add(url)
        bookmarks.append(
            Bookmark(
                url=url,
                title=(anchor.text_content() or "").strip() or url,
                folder_path=_folder_of(anchor),
            )
        )
    return bookmarks


def folder_summary(bookmarks: list[Bookmark]) -> list[dict]:
    """Every folder that directly contains at least one bookmark, with its
    count -- what the household picks from before spending any GPU time.

    Only folders that hold bookmarks are listed. A parent that contains
    nothing but other folders is a tree node, not a choice."""
    counts: dict[str, int] = {}
    for bookmark in bookmarks:
        counts[bookmark.folder_path] = counts.get(bookmark.folder_path, 0) + 1
    return [{"path": path, "count": count} for path, count in sorted(counts.items())]


def select(bookmarks: list[Bookmark], folder_path: str | None) -> list[Bookmark]:
    """`None` means everything. A chosen folder includes its
    SUBFOLDERS -- picking `Recipes` and silently missing
    `Recipes/Desserts` is not what anyone means by picking a folder."""
    if folder_path is None:
        return list(bookmarks)
    prefix = folder_path.rstrip("/")
    return [b for b in bookmarks if b.folder_path == prefix or b.folder_path.startswith(f"{prefix}/")]


def scan_and_parse(db: Session, bookmarks: list[Bookmark], limit: int = MAX_URLS_PER_SCAN) -> dict:
    """Imports each bookmark, one at a time, and never raises for one bad
    URL.

    A bookmarks folder is full of things that are not recipes -- a
    technique video, a shop, a dead link -- so a per-item failure is the
    NORMAL case here, not an exception. Each one is reported with its
    reason and the household skips it in review; one 404 must not cost
    them the other thirty-nine imports.

    **Anything already imported is dropped BEFORE the cap is applied**,
    and that ordering is the whole feature. A 565-bookmark export cannot
    go through a 40-URL scan in one run, and re-uploading it used to
    re-attempt the same first forty forever -- the same GPU minutes, and,
    since nothing checked, a second copy of every recipe on confirm. Now
    each run takes the next forty it has not seen, so uploading the same
    file repeatedly walks the whole export. Filtering AFTER the cap would
    have looked identical on run one and then spent run two rediscovering
    that run one was done.

    `remaining` is what makes that loop legible: how many unimported URLs
    are still waiting once this batch is taken."""
    known = already_imported_urls(db, [b.url for b in bookmarks])
    fresh = [b for b in bookmarks if b.url not in known]

    attempted = fresh[:limit]
    skipped = [[b.url, "over the per-scan limit"] for b in fresh[limit:]]

    items = []
    for bookmark in attempted:
        item = {
            "url": bookmark.url,
            "title": bookmark.title,
            "folder_path": bookmark.folder_path,
            "status": "ok",
            "recipe": None,
            "error": None,
        }
        try:
            url_result = recipe_service.parse_recipe_from_url(db, bookmark.url)
            parsed = recipe_service.finish_recipe_parse(
                url_result["raw_output"],
                url_result["default_source"],
                url_result["citation"],
                url_result["image_path"],
                url_result["jsonld_parsed"],
                db=db,
                source_text=url_result["source_text"],
            )
            parsed.pop(recipe_service.INGREDIENT_PROVENANCE_KEY, None)
            parsed.pop(recipe_service.INSTRUCTION_WARNINGS_KEY, None)
            # The bookmark's own folder becomes a tag, so a "Desserts"
            # folder is still findable as one after the import. The
            # bookmark TITLE is deliberately not used as the recipe title
            # -- browsers store whatever the page's <title> said, which is
            # routinely "Best Ever Pie Recipe (SO EASY!) - My Blog".
            if bookmark.folder_path:
                leaf = bookmark.folder_path.rsplit("/", 1)[-1].strip().lower().replace(" ", "_")
                if leaf:
                    parsed["tags"] = sorted({*(parsed.get("tags") or []), leaf})
            item["recipe"] = parsed
        except Exception as exc:
            item["status"] = "error"
            item["error"] = str(exc)[:300]
        items.append(item)

    return {
        "items": items,
        "skipped": skipped,
        "truncated": bool(skipped),
        # Reported rather than silently absent: a household that uploads a
        # 565-URL file and gets 40 rows back deserves to know the other
        # 525 were accounted for, and which way.
        "already_imported": len(known),
        "remaining": len(fresh) - len(attempted),
    }
