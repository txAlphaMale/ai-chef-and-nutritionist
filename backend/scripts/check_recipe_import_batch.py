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
    no_comp   how many carry no component
    src       B = two-pass supplied the ingredients (what you want)
              A = two-pass declined, single-call ingredients stand -- THE row to look at
              LD = schema.org data, two-pass skipped by design, not a failure
    how       which verification strategy accepted the lines:
              prefix = source puts each ingredient on its own line
              welded = source has no line structure (browser print-to-PDF,
                       pasted paragraph); recovered as an ordered run
              CUT    = pass 1 hit the response token cap, output truncated
              trailing ! = lines verified but the completeness gate
                       REFUSED to use them; single call stands
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
import re
import sys
from pathlib import Path

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
    no_amount_lines = 0
    if source and file_result["jsonld_parsed"] is None:
        raw, done_reason = recipe_service.ollama_client.chat_json_with_reason(
            db,
            [
                {
                    "role": "user",
                    "content": recipe_service.get_ingredient_lines_prompt(db).replace("{content}", source),
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
        for block in data.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            component = recipe_service.normalize_component(block.get("component"))
            lines = [ln for ln in (block.get("lines") or []) if isinstance(ln, str)]
            p1 += len(lines)
            accepted, _rejected, strategy = recipe_service.reconcile_block(lines, source)
            strategies.add(strategy)
            for line in accepted:
                kept += 1
                # A source line with no digit in it cannot yield a
                # quantity, so a null from it is the SOURCE saying
                # nothing, not this pipeline losing something. Without
                # this split, a correctly imported recipe that lists no
                # amounts (very common on blog/social recipes) reads as
                # a total failure in the null_q column.
                if not any(ch.isdigit() for ch in line):
                    no_amount_lines += 1
                for entry in recipe_service.parse_ingredient_line_amounts(line):
                    if entry["ingredient_name"]:
                        two_pass.append({**entry, "component": component})
        if strategies:
            row["how"] = "+".join(sorted(strategies))
        # Mirror the app's completeness gate exactly -- a partial
        # verification must not replace a fuller single-call list, and
        # this table is worthless if it reports a policy the app no
        # longer follows.
        covered = bool(p1) and kept / p1 >= recipe_service._TWO_PASS_MIN_COVERAGE
        single_call_count = len(parsed.get("ingredients") or [])
        big_enough = single_call_count == 0 or len(two_pass) >= single_call_count * (
            recipe_service._TWO_PASS_MIN_COVERAGE
        )
        if two_pass and covered and big_enough:
            parsed["ingredients"] = two_pass
        elif two_pass:
            row["how"] = f"{row['how']}!"  # verified something, refused to use it
    row["p1"], row["kept"] = p1, kept

    ingredients = parsed.get("ingredients") or []
    row["ingr"] = len(ingredients)
    row["null_q"] = sum(1 for i in ingredients if i.get("quantity") is None)
    # Nulls this pipeline cannot explain by the source stating no amount.
    row["lost_q"] = max(0, row["null_q"] - no_amount_lines)
    row["no_comp"] = sum(1 for i in ingredients if not i.get("component"))
    # LD is not a failure: a schema.org source already carries a clean
    # machine-readable ingredient list, so two-pass is skipped by design.
    # Without this it would show as a fallback and read like a defect.
    if file_result["jsonld_parsed"] is not None:
        row["src"] = "LD"
    else:
        row["src"] = "B" if kept else "A"
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
        f"{'file':<30} {'ingr':>4} {'null_q':>6} {'lost_q':>6} {'no_comp':>7} "
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
                    f"{row['no_comp']:>7} {row['src']:>3} {row['how']:>7} {row['p1']:>3} "
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
        print()
        print("  A fell-back or thin row is the one to open by hand:")
        print("    docker compose exec chef python scripts/check_recipe_import.py <that file>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
