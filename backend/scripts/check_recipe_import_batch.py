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
    null_q    how many have no quantity -- the headline defect
    no_comp   how many carry no component
    src       A = single call only (two-pass gave nothing), B = two-pass
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

Each file costs two live model calls, so start with --limit.
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

    parsed = recipe_service.finish_recipe_parse(
        file_result["raw_output"],
        file_result["default_source"],
        file_result["citation"],
        file_result["image_path"],
        file_result["jsonld_parsed"],
        db=db,
        source_text=source,
    )
    row["title"] = (parsed.get("title") or "")[:34]

    # Re-run pass 1 alone to report what it found vs what survived. This
    # costs a second call on the ingredient prompt, which is the cheap one
    # (roughly a fifth of the main call), and it is the only way to tell
    # "pass 1 found nothing" from "pass 1 found things we rejected".
    p1 = kept = 0
    if source and file_result["jsonld_parsed"] is None:
        raw = recipe_service.ollama_client.chat_json(
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
        data = recipe_service._extract_json_object(raw) or {}
        lines = [ln for b in (data.get("blocks") or []) if isinstance(b, dict) for ln in (b.get("lines") or [])]
        p1 = len(lines)
        kept = len(recipe_service.verify_copied_lines(lines, source))
    row["p1"], row["kept"] = p1, kept

    ingredients = parsed.get("ingredients") or []
    row["ingr"] = len(ingredients)
    row["null_q"] = sum(1 for i in ingredients if i.get("quantity") is None)
    row["no_comp"] = sum(1 for i in ingredients if not i.get("component"))
    # If two-pass supplied the answer, its count equals what survived
    # verification (plus any compound line split in two).
    row["src"] = "B" if kept and row["ingr"] >= kept else "A"
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
    header = f"{'file':<30} {'ingr':>4} {'null_q':>6} {'no_comp':>7} {'src':>3} {'p1':>3} {'kept':>4} {'amounts':>7}"
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
                    f"{row['file'][:30]:<30} {row['ingr']:>4} {row['null_q']:>6} {row['no_comp']:>7} "
                    f"{row['src']:>3} {row['p1']:>3} {row['kept']:>4} {row['amounts']:>7}"
                )
    finally:
        db.close()

    ok = [r for r in rows if not r.get("error")]
    print()
    print(f"  files parsed          : {len(ok)}/{len(rows)}")
    if ok:
        fell_back = [r for r in ok if r["src"] == "A"]
        with_nulls = [r for r in ok if r["null_q"]]
        thin = [r for r in ok if r["amounts"] and r["kept"] < r["amounts"] * 0.6]
        print(f"  two-pass supplied     : {len(ok) - len(fell_back)}/{len(ok)}")
        print(
            f"  fell back to pass A   : {len(fell_back)}"
            + (f"  <- {[r['file'] for r in fell_back]}" if fell_back else "")
        )
        print(
            f"  any null quantity     : {len(with_nulls)}"
            + (f"  <- {[r['file'] for r in with_nulls]}" if with_nulls else "")
        )
        print(f"  pass 1 looks thin     : {len(thin)}" + (f"  <- {[r['file'] for r in thin]}" if thin else ""))
        print()
        print("  A fell-back or thin row is the one to open by hand:")
        print("    docker compose exec chef python scripts/check_recipe_import.py <that file>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
