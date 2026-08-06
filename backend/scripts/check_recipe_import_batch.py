"""Run the real import over a FOLDER of recipes and report one table.

check_recipe_import.py proves one file in detail. This proves a corpus,
which is the thing that actually matters: the pie passes, but it is one
recipe, one PDF and one text extractor, and the whole two-pass design
rests on an assumption that has only been tested against it -- that a
source's ingredient lines are their own lines. That holds for pypdf
output. Whether it holds for trafilatura's HTML extraction, for text
pasted as a paragraph, or for a two-column PDF pypdf interleaves, is
unknown, and when it does not hold the failure is SILENT: verification
rejects everything, two-pass returns nothing, and the single-call
ingredients stand with their null quantities.

So this reports, per file, which pass supplied the answer and what was
lost on the way, rather than a pass/fail. Read the columns:

    ingr      ingredients in the final result
    null_q    how many have no quantity
    lost_q    nulls NOT explained by the source stating no amount -- the
              real defect count. A blog recipe that lists "Kosher salt"
              with no amount is correctly imported with a null, and
              reading null_q alone calls that a total failure.
    no_comp   how many carry no component. NOT a defect count on its own:
              an unsectioned recipe correctly stores NULL on every row.
    bad_c     how many carry a component that reads like a HEADING rather
              than a part of the dish. no_comp answers "is a label there",
              which stopped being the interesting question once labels
              were reliably populated -- a recipe filed entirely under
              `INGREDIENTS YOU'LL NEED:` scored no_comp=0 and looked
              clean. Every label is also printed verbatim below the
              table, because a count is what hid this in the first place.
    src       B = two-pass supplied the ingredients (what you want)
              A = two-pass declined, single-call ingredients stand -- THE row to look at
              LD = schema.org data, two-pass skipped by design, not a failure
    how       which verification strategy accepted the lines:
              prefix = source puts each ingredient on its own line
              welded = source has no line structure (browser print-to-PDF,
                       pasted paragraph); recovered as an ordered run
              CUT    = pass 1 hit the response token cap, output truncated
              -Nb    = N whole blocks dropped: too few of their lines
                       verified for them to be an ingredient list
              trailing ! = lines verified but two-pass was still refused
                       because it came back thinner than the single call
    p1        lines pass 1 returned
    kept      of those, how many matched a real source line
    amounts   source lines that LOOK like ingredient lines (start with an
              amount). A rough count, deliberately not used for
              extraction -- brittle segmentation is exactly what two-pass
              avoids -- but a fine smoke alarm: kept far below amounts
              means pass 1 missed most of the list.

A row with src=A is the interesting one. It means two-pass declined and
nothing told the user.

    docker compose exec chef python scripts/check_recipe_import_batch.py /recipes
    docker compose exec chef python scripts/check_recipe_import_batch.py /recipes --limit 5

Each file costs two live model calls -- the same two a real import makes --
so start with --limit on a big folder.
"""

import argparse
import logging
import re
import sys
from pathlib import Path

# pypdf logs `Ignoring wrong pointing object ...` at WARNING for every
# malformed object in a browser print-to-PDF, which is most of them. They
# are not errors, they interleave between the table header and the first
# row, and they make the table hard to read. Real failures still surface.
logging.getLogger("pypdf").setLevel(logging.ERROR)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.services import recipe_service
from app.services.recipe_folder_import_service import SUPPORTED_EXTENSIONS, _guess_content_type

# A line starting with a number, a fraction, or a Unicode vulgar fraction.
# Used ONLY to say "the source appears to contain about this many
# ingredient lines" -- never to extract one.
_LOOKS_LIKE_AMOUNT = re.compile(r"^\s*(\d|\d+/\d+|[¼-¾⅐-⅞])")


def looks_like_ingredient_lines(source: str) -> int:
    return sum(1 for line in (source or "").splitlines() if _LOOKS_LIKE_AMOUNT.match(line))


# Words that mean a label is announcing a list or a section of the METHOD
# rather than naming a part of the dish. `normalize_component` already
# drops the variants that have actually been measured; this exists to
# catch the next one, which is the whole reason the column is here --
# `INGREDIENTS YOU'LL NEED:` sat on ten ingredients for four runs while
# `no_comp=0` reported it as a clean result.
_SUSPECT_COMPONENT_WORDS = (
    "ingredient",
    "youll need",
    "you will need",
    "what you",
    "shopping",
    "grocery",
    "instruction",
    "direction",
    "method",
    "step",
    "note",
)


def suspect_component(label: str) -> bool:
    """Is this a heading that should never have become a component?

    A part of a dish is named in a word or three -- Crust, Filling, Brine,
    Pico de Gallo. Anything long, or anything that talks about ingredients
    or instructions instead of being one, is a heading that leaked."""
    key = recipe_service._component_key(label)
    if not key:
        return True
    if any(word in key for word in _SUSPECT_COMPONENT_WORDS):
        return True
    # A section heading is written as one: `Brine`, `Crust`, `Filling`.
    # An all-lowercase label is usually a fragment that got mistaken for a
    # heading -- `powder`, the tail of a wrapped ingredient, was stored as
    # a component and this column did not flag it.
    if not any(ch.isupper() for ch in label):
        return True
    return len(key.split()) > 4 or len(label) > 40


def check_one(db, path: Path) -> dict:
    row = {"file": path.name, "error": None}
    file_result = recipe_service.parse_recipe_file_content(
        db, path.read_bytes(), path.name, _guess_content_type(path.suffix.lower())
    )
    source = file_result.get("source_text") or ""
    row["amounts"] = looks_like_ingredient_lines(source)

    parsed = recipe_service.parse_recipe_response(file_result["raw_output"])
    if file_result["jsonld_parsed"] is not None:
        parsed = recipe_service.coerce_recipe_fields(
            {k: v for k, v in file_result["jsonld_parsed"].items() if not k.startswith("_")}
        )
    if parsed is None:
        raise RuntimeError("nothing parseable from the single call")
    row["title"] = (parsed.get("title") or "")[:34]

    # Deliberately NOT finish_recipe_parse: that runs two-pass internally,
    # and this needs pass 1's own numbers too. Calling both would make
    # three model calls per file where the app makes two, which on the one
    # worker thread would make a 20-file corpus needlessly slow and
    # misreport the cost of an import.
    p1 = kept = 0
    row["how"] = "-"
    used_two_pass = False
    # extract_ingredients_two_pass repairs wrapped lines before it prompts,
    # and is verified against the SAME repaired text. The single call is
    # not, in the app either, so only this half rejoins. Without it this
    # table scored the pizza at 6 ingredients while the app stored 5.
    two_pass_source = recipe_service.rejoin_wrapped_lines(source)
    if source and file_result["jsonld_parsed"] is None:
        raw, done_reason = recipe_service.ollama_client.chat_json_with_reason(
            db,
            [
                {
                    "role": "user",
                    "content": recipe_service.get_ingredient_lines_prompt(db).replace("{content}", two_pass_source),
                }
            ],
            schema=recipe_service.INGREDIENT_LINES_SCHEMA,
            model=recipe_service.ollama_client.get_extraction_model(db),
            response_tokens=recipe_service.INGREDIENT_LINES_RESPONSE_TOKENS,
        )
        data = {} if done_reason == "length" else (recipe_service._extract_json_object(raw) or {})
        if done_reason == "length":
            row["how"] = "CUT"
        two_pass = []
        strategies = set()
        blocks_dropped = 0
        # Mirrors the app: a looping pass 1 returns the same block many
        # times, and counting it many times would overstate p1 and the
        # ingredient count alike.
        for block in recipe_service.dedupe_blocks(data.get("blocks") or []):
            component = recipe_service.normalize_component(block.get("component"))
            lines = [ln for ln in (block.get("lines") or []) if isinstance(ln, str)]
            if not lines:
                continue
            p1 += len(lines)
            accepted, _rejected, strategy = recipe_service.reconcile_block(lines, two_pass_source)
            # Same per-block gate the app applies. A table that reports a
            # policy the app no longer follows is worse than no table.
            if len(accepted) / len(lines) < recipe_service._TWO_PASS_MIN_COVERAGE:
                blocks_dropped += 1
                continue
            strategies.add(strategy)
            kept += len(accepted)
            block_entries = [
                entry
                for line in accepted
                for entry in recipe_service.parse_ingredient_line_amounts(line)
                # Mirrors the app: a metadata line like `30 minutes
                # hands-on effort` verifies perfectly and is not food.
                if entry["ingredient_name"] and not recipe_service._names_a_duration(entry)
            ]
            # Headings welded into the run are source text and verify
            # correctly, so they have to be split out here too or this
            # table reports one more ingredient than the app stores.
            two_pass.extend(recipe_service._split_headings_from_ingredients(block_entries, component))
        if strategies:
            row["how"] = "+".join(sorted(strategies))
        if blocks_dropped:
            row["how"] = f"{row['how']}-{blocks_dropped}b"
        single_call_count = len(parsed.get("ingredients") or [])
        big_enough = single_call_count == 0 or len(two_pass) >= single_call_count * (
            recipe_service._TWO_PASS_MIN_COVERAGE
        )
        if two_pass and big_enough:
            parsed["ingredients"] = two_pass
            used_two_pass = True
        elif two_pass:
            row["how"] = f"{row['how']}!"  # verified lines, refused to use them
    row["p1"], row["kept"] = p1, kept

    ingredients = parsed.get("ingredients") or []
    row["ingr"] = len(ingredients)
    row["null_q"] = sum(1 for i in ingredients if i.get("quantity") is None)
    # A null this pipeline can EXPLAIN: the ingredient text came from the
    # SOURCE and carries no digit anywhere, so the source stated no amount
    # and null is the correct reading -- "Kosher salt", "Salt and black
    # pepper to taste".
    #
    # The "from the source" half is not a technicality. Two-pass text is
    # verified source text and JSON-LD strings ARE the source, so a
    # missing digit there means the source had none. Single-call text is
    # authored by the model, and a model that loses an amount loses its
    # digits with it: the pizza row reported all 5 nulls as explained on a
    # source whose own amounts column said 4. Where the text is not the
    # source's, unexplained is the honest reading.
    source_derived = used_two_pass or file_result["jsonld_parsed"] is not None
    row["lost_q"] = sum(
        1
        for i in ingredients
        if i.get("quantity") is None
        and (
            not source_derived
            or any(
                ch.isdigit() for ch in " ".join(str(i.get(k) or "") for k in ("ingredient_name", "unit", "prep_note"))
            )
        )
    )
    row["no_comp"] = sum(1 for i in ingredients if not i.get("component"))
    # no_comp counts whether a component is PRESENT. It cannot tell a part
    # of the dish from a heading, so it reported a clean 0 on a recipe
    # whose every ingredient was filed under `INGREDIENTS YOU'LL NEED:`.
    # These two report what the labels actually SAY.
    row["labels"] = sorted({str(i["component"]) for i in ingredients if i.get("component")})
    row["bad_c"] = sum(1 for i in ingredients if i.get("component") and suspect_component(str(i["component"])))
    # LD is not a failure: a schema.org source already carries a clean
    # machine-readable ingredient list, so two-pass is skipped by design.
    # Without this it would show as a fallback and read like a defect.
    if file_result["jsonld_parsed"] is not None:
        row["src"] = "LD"
    else:
        # Whether two-pass ACTUALLY supplied the ingredients, not whether
        # it verified something. Those came apart the moment the gate
        # landed: a row read "src=B ... how=prefix+welded!", claiming
        # two-pass supplied a list it had just been refused.
        row["src"] = "B" if used_two_pass else "A"
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder")
    ap.add_argument("--limit", type=int, default=0, help="stop after N files (each costs two model calls)")
    args = ap.parse_args()

    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"Not a folder: {folder}")
        return 2

    files = sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS)
    if args.limit:
        files = files[: args.limit]
    if not files:
        print(f"No importable files under {folder} (looking for {', '.join(sorted(SUPPORTED_EXTENSIONS))})")
        return 2

    print(f"{len(files)} file(s) under {folder}\n")
    header = (
        f"{'file':<30} {'ingr':>4} {'null_q':>6} {'lost_q':>6} {'no_comp':>7} {'bad_c':>5} "
        f"{'src':>3} {'how':>7} {'p1':>3} {'kept':>4} {'amounts':>7}"
    )
    print(header)
    print("-" * len(header))

    rows = []
    db = SessionLocal()
    try:
        for path in files:
            try:
                row = check_one(db, path)
            except Exception as exc:  # a bad file must not end the run
                row = {"file": path.name, "error": str(exc)[:60]}
            rows.append(row)
            if row.get("error"):
                print(f"{row['file'][:30]:<30} {'ERROR':>4} {row['error']}")
            else:
                print(
                    f"{row['file'][:30]:<30} {row['ingr']:>4} {row['null_q']:>6} {row['lost_q']:>6} "
                    f"{row['no_comp']:>7} {row['bad_c']:>5} {row['src']:>3} {row['how']:>7} {row['p1']:>3} "
                    f"{row['kept']:>4} {row['amounts']:>7}"
                )
    finally:
        db.close()

    ok = [r for r in rows if not r.get("error")]
    print()
    print(f"  files parsed          : {len(ok)}/{len(rows)}")
    if ok:
        fell_back = [r for r in ok if r["src"] == "A"]
        jsonld = [r for r in ok if r["src"] == "LD"]
        with_nulls = [r for r in ok if r.get("lost_q")]
        thin = [r for r in ok if r["amounts"] and r["kept"] < r["amounts"] * 0.6]
        print(
            f"  two-pass supplied     : {len(ok) - len(fell_back) - len(jsonld)}/{len(ok) - len(jsonld)}"
            + (f"   (+{len(jsonld)} schema.org, skipped by design)" if jsonld else "")
        )
        print(
            f"  fell back to pass A   : {len(fell_back)}"
            + (f"  <- {[r['file'] for r in fell_back]}" if fell_back else "")
        )
        print(
            f"  unexplained null qty  : {len(with_nulls)}"
            + (f"  <- {[r['file'] for r in with_nulls]}" if with_nulls else "")
        )
        print(f"  pass 1 looks thin     : {len(thin)}" + (f"  <- {[r['file'] for r in thin]}" if thin else ""))
        bad_labels = [r for r in ok if r.get("bad_c")]
        print(
            f"  suspect components    : {len(bad_labels)}"
            + (f"  <- {[r['file'] for r in bad_labels]}" if bad_labels else "")
        )
        # Printed in full, not counted. A count is what let
        # `INGREDIENTS YOU'LL NEED:` pass review four runs running.
        labelled = [r for r in ok if r.get("labels")]
        if labelled:
            print()
            print("  components stored, verbatim:")
            for r in labelled:
                for label in r["labels"]:
                    mark = "   <-- SUSPECT" if suspect_component(label) else ""
                    print(f"    {r['file'][:30]:<30} {label}{mark}")
        print()
        print("  A fell-back or thin row is the one to open by hand:")
        print("    docker compose exec chef python scripts/check_recipe_import.py <that file>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
