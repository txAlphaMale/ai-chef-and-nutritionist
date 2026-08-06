"""Why does pass 1 hit the response cap on one file, and what is it
spending the tokens on?

The Leopard Crust pizza has hit `done_reason='length'` at exactly
INGREDIENT_LINES_RESPONSE_TOKENS on three consecutive batch runs, with an
identical prompt every time. The plan has been calling the input the
suspect -- 14k chars of a 24-page printed blog post to find ~400 chars of
ingredients in -- but that was a theory, and the same shape of theory has
been wrong twice before on this project.

Measured offline against the fixture, before writing this: the whole
ingredient list lives in ONE source line, 477 chars at offset 1576, about
11% into the document. The model's first five copied lines are correct. So
it finds the list immediately and then keeps going, and what it emits for
the remaining ~2700 characters is the thing nobody has looked at.

This looks. For each response cap it reports whether generation stopped on
its own, every line the model returned, whether that line verifies against
the source, and WHERE in the source it came from. The offset is the
diagnostic: the real list sits near 1576, the baker's-percentage scaling
example near 9250, and the reader comments past 10900. A run that wanders
into the comments is a different problem from one that merely needs room.

    docker compose exec chef python scripts/probe_pass1_budget.py /tmp/corpus/pizza.pdf
    docker compose exec chef python scripts/probe_pass1_budget.py <file> --caps 1200,2400

Each cap costs one live model call. Caps much above ~4000 start eating
into the input budget on an 8192 context -- the script says so when the
source no longer fits, because a run that silently truncated the INPUT
would answer a different question than the one being asked.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.getLogger("pypdf").setLevel(logging.ERROR)

from app.database import SessionLocal
from app.services import ollama_client, recipe_service


def load_source(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return recipe_service.extract_pdf_text(path.read_bytes())
    return path.read_text(encoding="utf-8")


def run_one_cap(db, source: str, cap: int) -> None:
    template = recipe_service.get_ingredient_lines_prompt(db)
    budget = ollama_client.content_char_budget(
        db, prompt_overhead_chars=len(template), response_reserve_tokens=cap
    )
    content = source[:budget]
    truncated_input = len(content) < len(source)

    print(f"\n{'=' * 78}")
    print(f"CAP {cap} tokens   input budget {budget} chars, source {len(source)} chars")
    if truncated_input:
        print(f"  !! INPUT TRUNCATED by {len(source) - len(content)} chars -- this cap")
        print("     is too large for the context, and the run below is answering a")
        print("     different question. Lower the cap or raise num_ctx.")
    print("=" * 78)

    raw, done_reason = ollama_client.chat_json_with_reason(
        db,
        [{"role": "user", "content": template.replace("{content}", content)}],
        schema=recipe_service.INGREDIENT_LINES_SCHEMA,
        model=ollama_client.get_extraction_model(db),
        response_tokens=cap,
    )
    stopped_on_its_own = done_reason != "length"
    print(f"  done_reason={done_reason!r}  -> {'STOPPED ON ITS OWN' if stopped_on_its_own else 'CUT OFF'}")

    data = recipe_service._extract_json_object(raw) or {}
    blocks = [b for b in (data.get("blocks") or []) if isinstance(b, dict)]
    if not blocks:
        print("  no blocks recovered from the response")
        return

    total_lines = total_verified = 0
    for block in blocks:
        lines = [ln for ln in (block.get("lines") or []) if isinstance(ln, str)]
        if not lines:
            continue
        accepted, _rejected, strategy = recipe_service.reconcile_block(lines, source)
        coverage = len(accepted) / len(lines)
        gate = "KEPT" if coverage >= recipe_service._TWO_PASS_MIN_COVERAGE else "DROPPED"
        total_lines += len(lines)
        total_verified += len(accepted)
        print(
            f"\n  block {str(block.get('component'))!r}: {len(lines)} lines, "
            f"{len(accepted)} verified ({strategy}), coverage {coverage:.2f} -> {gate}"
        )
        ok = set(accepted)
        for line in lines:
            where = source.find(line)
            mark = "ok " if line in ok else "-- "
            at = f"@{where:<6}" if where >= 0 else "@absent"
            print(f"    {mark}{at} {line[:66]}")

    print(f"\n  totals: {total_lines} lines returned, {total_verified} verified")
    print("  Read the offsets: the ingredient list, the scaling example and the")
    print("  reader comments are far apart in the source, so where the lines came")
    print("  from says what the model was actually doing with the tokens.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file")
    ap.add_argument(
        "--caps",
        default=f"{recipe_service.INGREDIENT_LINES_RESPONSE_TOKENS},2400,3600",
        help="comma-separated response token caps; one live model call each",
    )
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"No such source: {path}")
        return 2

    source = load_source(path)
    print(f"source: {path}  ({len(source)} chars, {len(source.splitlines())} lines)")

    caps = [int(c) for c in args.caps.split(",") if c.strip()]
    db = SessionLocal()
    try:
        for cap in caps:
            run_one_cap(db, source, cap)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
