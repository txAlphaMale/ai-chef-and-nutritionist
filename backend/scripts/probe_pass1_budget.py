"""Why does pass 1 hit the response cap on one file, and what is it
spending the tokens on?

The Leopard Crust pizza has hit `done_reason='length'` at exactly
INGREDIENT_LINES_RESPONSE_TOKENS on three consecutive batch runs, with an
identical prompt every time.

Measured offline against the fixture: the whole ingredient list lives in
ONE source line, 477 chars at offset 1576, about 11% into the document.
The model's first five copied lines are correct. So it finds the list
immediately and then keeps going, and what it emits afterwards is the
question.

First live run of this probe (2026-08-06) settled half of it. At caps of
1200, 2400 and 3600 the response was cut off every time, and content_chars
came back 2910 / 5816 / 8739 -- 2.426 chars per token, three times over.
The model is not running slightly short of room; it does not converge. A
bigger cap buys proportionally more output and no ending.

That run also proved this script's first version useless: it asked
`_extract_json_object` for the blocks, got nothing (the JSON is cut
mid-array, which is the whole point), and printed "no blocks recovered"
instead of the text it exists to show. So the response is now salvaged
STRUCTURALLY -- every JSON string literal is pulled out in order, whether
or not the object ever closed -- and reported three ways:

  * where in the source each line came from. The real ingredient list is
    at offset 1576, the baker's-percentage worked example at 9250, and the
    reader comments past 10,900. Clustering says what the model was doing.
  * whether the line verifies against the source at all.
  * whether it has emitted the same line before. Perfectly linear output
    growth is also what a model stuck in a loop produces, and that is a
    different defect with a different fix.

    docker compose exec chef python scripts/probe_pass1_budget.py <file>
    docker compose exec chef python scripts/probe_pass1_budget.py <file> --caps 1200,2400
    docker compose exec chef python scripts/probe_pass1_budget.py <file> --dump /tmp/pass1.txt

Each cap costs one live model call. Caps much above ~4000 start eating
into the input budget on an 8192 context -- the script says so when the
source no longer fits, because a run that silently truncated the INPUT
would answer a different question than the one being asked.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.getLogger("pypdf").setLevel(logging.ERROR)

from app.database import SessionLocal
from app.services import ollama_client, recipe_service

# Every complete JSON string literal, in order. Deliberately tolerant of a
# document that never closed: this runs on text the JSON parser rejects.
_JSON_STRING = re.compile(r'"((?:[^"\\]|\\.)*)"')
_STRUCTURAL_KEYS = {"blocks", "component", "lines"}


def load_source(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return recipe_service.extract_pdf_text(path.read_bytes())
    return path.read_text(encoding="utf-8")


def salvage_blocks(raw: str) -> list[tuple[str | None, list[str]]]:
    """Blocks and lines out of a response that may have been cut anywhere.

    Walks the string literals in order and uses the schema's own key names
    as the state machine: the string after `component` is a heading, the
    strings after `lines` are lines, until the next `component`."""
    blocks: list[tuple[str | None, list[str]]] = []
    component: str | None = None
    lines: list[str] = []
    expecting = None
    for match in _JSON_STRING.finditer(raw):
        try:
            value = json.loads(f'"{match.group(1)}"')
        except ValueError:
            continue
        if value in _STRUCTURAL_KEYS:
            if value == "component":
                if lines or component is not None:
                    blocks.append((component, lines))
                component, lines = None, []
                expecting = "component"
            elif value == "lines":
                expecting = "lines"
            continue
        if expecting == "component":
            component = value
            expecting = None
        elif expecting == "lines":
            lines.append(value)
    if lines or component is not None:
        blocks.append((component, lines))
    return blocks


def run_one_cap(db, source: str, cap: int, dump: Path | None) -> None:
    template = recipe_service.get_ingredient_lines_prompt(db)
    budget = ollama_client.content_char_budget(db, prompt_overhead_chars=len(template), response_reserve_tokens=cap)
    content = source[:budget]

    print(f"\n{'=' * 78}")
    print(f"CAP {cap} tokens   input budget {budget} chars, source {len(source)} chars")
    if len(content) < len(source):
        print(f"  !! INPUT TRUNCATED by {len(source) - len(content)} chars -- this cap is too")
        print("     large for the context, and this run answers a different question.")
    print("=" * 78)

    raw, done_reason = ollama_client.chat_json_with_reason(
        db,
        [{"role": "user", "content": template.replace("{content}", content)}],
        schema=recipe_service.INGREDIENT_LINES_SCHEMA,
        model=ollama_client.get_extraction_model(db),
        response_tokens=cap,
    )
    print(f"  done_reason={done_reason!r} -> {'STOPPED ON ITS OWN' if done_reason != 'length' else 'CUT OFF'}")
    print(f"  parses as JSON: {'yes' if recipe_service._extract_json_object(raw) else 'NO -- salvaging structurally'}")
    if dump:
        target = dump.with_name(f"{dump.stem}.{cap}{dump.suffix or '.txt'}")
        target.write_text(raw, encoding="utf-8")
        print(f"  raw response written to {target}")

    blocks = salvage_blocks(raw)
    if not blocks:
        print("  nothing salvageable -- inspect the raw dump")
        return

    seen: Counter[str] = Counter()
    all_lines: list[str] = []
    for component, lines in blocks:
        if not lines:
            continue
        accepted, _rejected, strategy = recipe_service.reconcile_block(lines, source)
        coverage = len(accepted) / len(lines)
        gate = "KEPT" if coverage >= recipe_service._TWO_PASS_MIN_COVERAGE else "DROPPED"
        print(f"\n  block {component!r}: {len(lines)} lines, {len(accepted)} verified ({strategy}), {gate}")
        ok = set(accepted)
        for line in lines:
            where = source.find(line)
            seen[line] += 1
            all_lines.append(line)
            flag = "DUP" if seen[line] > 1 else ("ok " if line in ok else "-- ")
            at = f"@{where:<6}" if where >= 0 else "@absent"
            print(f"    {flag} {at} {line[:64]}")

    repeats = [(line, n) for line, n in seen.most_common() if n > 1]
    print(f"\n  {len(all_lines)} lines returned, {len(seen)} distinct")
    if repeats:
        print(f"  REPEATED {len(repeats)} line(s) -- the model is looping, not enumerating:")
        for line, n in repeats[:8]:
            print(f"    x{n:<3} {line[:60]}")
    located = [source.find(ln) for ln in seen if source.find(ln) >= 0]
    if located:
        print(f"  source offsets of verifiable lines: min {min(located)}, max {max(located)}")
        print("  (ingredient list ~1576, scaling example ~9250, reader comments 10900+)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file")
    ap.add_argument(
        "--caps",
        default=str(recipe_service.INGREDIENT_LINES_RESPONSE_TOKENS),
        help="comma-separated response token caps; one live model call each",
    )
    ap.add_argument("--dump", type=Path, default=None, help="write each raw response to this path")
    args = ap.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"No such source: {path}")
        return 2

    source = load_source(path)
    print(f"source: {path}  ({len(source)} chars, {len(source.splitlines())} lines)")

    db = SessionLocal()
    try:
        for cap in [int(c) for c in args.caps.split(",") if c.strip()]:
            run_one_cap(db, source, cap, args.dump)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
