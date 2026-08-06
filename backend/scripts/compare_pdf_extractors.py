"""Does a different PDF library fix the ingredient names pypdf welds?

Three measured defects, all inside an ingredient NAME, which is the app's
join key, and all of them invisible to verification because the extracted
source really does say them:

    Gsh sauce                   the `fi` ligature mapped to `G`
    Korean red pepperpowder     a line wrap joined without a space
    oil (5%)+Chickpea flour     an inline `+` separator not broken out

The common cause is the extractor, not the model, the prompt or the
schema, so the first thing worth knowing is whether another extractor
simply does not have the problem. That is a measurement, not a redesign,
and it needs no model calls at all.

pdfplumber is the candidate rather than PyMuPDF: this repo is MIT,
pdfplumber is MIT, and PyMuPDF is AGPL with a separate commercial
licence, which is not a reasonable thing to hand to someone cloning a home
meal-planner. If pdfplumber turns out to fix these, the swap is a one-line
change in extract_pdf_text; if it does not, the finding is that this is
inherent to the PDFs (browser print-to-PDF of a web page) and the remedy
has to be normalisation with all the risk that carries.

Deliberately imports NOTHING from the app -- pdfplumber is not, and should
not yet be, a dependency of this image. Run it in a throwaway container:

    docker run --rm -v /home/comfyui/recipe-smoke-test:/pdfs:ro \
      -v ~/projects/chef/backend:/src -w /src python:3.12-slim \
      bash -c "pip install -q pypdf==5.0.1 pdfplumber && \
               python scripts/compare_pdf_extractors.py /pdfs/*.pdf"

For each PDF it reports the shape of what each library returns, then
checks the specific strings above -- the BROKEN form and the form the
source visually shows -- so the answer is a table of facts rather than an
impression of the text.
"""

from __future__ import annotations

import argparse
import io
import logging
from pathlib import Path

logging.getLogger("pypdf").setLevel(logging.ERROR)
logging.getLogger("pdfminer").setLevel(logging.ERROR)

# (label, anchor to locate in whatever the library returned, the broken
# form pypdf produces there).
#
# An anchor plus a window rather than a present/absent pair: "is the fixed
# form present" needs a guess about what the fix would look like, and a
# guess that matches ordinary prose answers yes for the wrong reason.
# `Grst` -> `first` did exactly that -- "first" appears in both files as a
# normal word, so the column read FIXED on text that was still broken.
# Printing what each library actually returned needs no guess.
PROBES = [
    ("kimchi ligature", "sauce or shrimp sauce", "Gsh"),
    # NOT "red pepper": that matches a reader comment before it matches the
    # ingredient line. NOT "cantly slow": pypdf welds that pair too, so the
    # anchor described text no extractor produces.
    ("kimchi line wrap", "Korean red pepper", "pepperpowder"),
    ("kimchi ligature 2", "refrigerate to", "signiGcantly"),
    ("pizza + separator", "Chickpea flour or fine cornmeal", "(5%)+Chickpea"),
    ("pizza weld", "Fioreglut flour (100%)", "320gCaputo"),
]
WINDOW = 44


def with_pypdf(data: bytes) -> str:
    from pypdf import PdfReader

    # Mirrors recipe_service.extract_pdf_text exactly.
    reader = PdfReader(io.BytesIO(data))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def with_pdfplumber(data: bytes) -> str:
    import pdfplumber

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join(page.extract_text() or "" for page in pdf.pages)


def describe(name: str, text: str) -> None:
    lines = text.split("\n")
    longest = max((len(ln) for ln in lines), default=0)
    print(f"  {name:<12} {len(text):>7} chars  {len(lines):>4} lines  longest line {longest:>5}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+")
    ap.add_argument("--show", type=int, default=0, help="print the first N chars of each extraction")
    args = ap.parse_args()

    try:
        import pdfplumber  # noqa: F401
    except ImportError:
        print("pdfplumber is not installed. This script is meant to run in a throwaway")
        print("container -- see the docstring at the top of this file.")
        return 2

    for raw in args.files:
        path = Path(raw)
        if not path.is_file() or path.suffix.lower() != ".pdf":
            continue
        data = path.read_bytes()
        print(f"\n{'=' * 74}\n{path.name}\n{'=' * 74}")

        results: dict[str, str] = {}
        for label, fn in (("pypdf", with_pypdf), ("pdfplumber", with_pdfplumber)):
            try:
                results[label] = fn(data)
            except Exception as exc:
                print(f"  {label:<12} FAILED: {type(exc).__name__}: {exc}")
        for label, text in results.items():
            describe(label, text)

        for label, anchor, broken in PROBES:
            if not any(anchor in text for text in results.values()):
                continue  # a probe from the other file
            print(f"\n  {label}   (broken form: {broken!r})")
            for lib, text in results.items():
                at = text.find(anchor)
                if at < 0:
                    print(f"    {lib:<11} anchor NOT FOUND -- the text may be shaped differently")
                    continue
                window = text[max(0, at - WINDOW) : at + len(anchor) + 12]
                window = window.replace("\n", "\\n")
                verdict = "STILL BROKEN" if broken in text else "changed"
                print(f"    {lib:<11} {verdict:<12} ...{window}...")

        if args.show:
            for label, text in results.items():
                print(f"\n  --- {label}, first {args.show} chars ---")
                print(text[: args.show])

    print("\nProbes whose anchor is absent from a file are skipped: the kimchi probes")
    print("say nothing about the pizza and vice versa. 'changed' means only that the")
    print("broken form is gone -- read the window to see what replaced it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
