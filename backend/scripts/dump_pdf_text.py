"""Write what pdfplumber extracts from a PDF, as a test fixture.

The checked-in fixtures are pypdf output, and pypdf is no longer the
primary reader. Every offline test of the wrapped-line problem needs the
text the app now actually sees, and that text can only come from the real
PDFs, which live on the author's machine rather than in the repo.

Mirrors the pdfplumber branch of recipe_service.extract_pdf_text exactly
-- same call, same join -- but imports NOTHING from the app, so it runs in
a throwaway container without the app's dependency tree:

    docker run --rm -v /home/comfyui/recipe-smoke-test:/pdfs:ro \
      -v /mnt/c/Users/JBentley/Claude/Projects/chef/backend:/src -w /src \
      python:3.12-slim \
      bash -c "pip install -q pdfplumber && \
               python scripts/dump_pdf_text.py /pdfs/*.pdf --out tests/fixtures"

Note the mount: it writes into the WINDOWS checkout, which is where edits
are made and where commits are pushed from. Writing into the WSL clone
would put fixtures on the side of the fence that only ever pulls.

The pypdf fixtures are NOT replaced. They stay as the regression case for
a source with no line structure at all -- that shape still occurs, it is
what find_welded_run exists for, and losing the only example of it to a
library swap would be a bad trade.
"""

from __future__ import annotations

import argparse
import io
import logging
import re
from pathlib import Path

logging.getLogger("pdfminer").setLevel(logging.ERROR)


def slug(name: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", name.lower())).strip("_")[:44]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", type=Path, required=True, help="directory to write fixtures into")
    args = ap.parse_args()

    try:
        import pdfplumber
    except ImportError:
        print("pdfplumber is not installed -- see the docstring at the top of this file.")
        return 2

    args.out.mkdir(parents=True, exist_ok=True)
    for raw in args.files:
        path = Path(raw)
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        with pdfplumber.open(io.BytesIO(path.read_bytes())) as pdf:
            text = "\n".join(page.extract_text() or "" for page in pdf.pages)
        target = args.out / f"{slug(path.stem)}_pdfplumber.txt"
        target.write_text(text, encoding="utf-8")
        lines = text.split("\n")
        print(f"{target}  ({len(text)} chars, {len(lines)} lines, longest {max(map(len, lines), default=0)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
