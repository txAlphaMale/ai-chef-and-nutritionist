"""Run the real recipe-import extraction against a live Ollama and report
whether the three known Pumpkin Chiffon Pie defects are gone.

The backend test suite cannot do this: it has no model. This is the step
that actually proves the RECIPE_IMPORT_PROMPT rewrite works, so run it
after any change to that prompt or to ExtractedIngredient.

    docker compose cp backend/tests/fixtures/pumpkin_chiffon_pie_pypdf.txt chef:/tmp/pie.txt
    docker compose exec chef python scripts/check_recipe_import.py /tmp/pie.txt

The fixture is copied in rather than baked into the image on purpose:
test data does not belong in a production image, and `.dockerignore`
keeps `backend/tests` out of the build context entirely. The script
itself ships (`COPY backend/scripts ./scripts`) because it is an
operational tool, not a test.

Any source works -- a .pdf goes through the same extract_pdf_text() the
app uses, anything else is read as text:

    docker compose exec chef python scripts/check_recipe_import.py /tmp/other.pdf

The Pumpkin Chiffon Pie assertions run when the source is recognised by
content, not by filename, so a copied-in fixture is still checked. Exit
code is 0 only if every check passes, so this can gate a change.
"""

import sys
from pathlib import Path

# Invoked as a plain path (`python scripts/check_recipe_import.py`),
# Python puts THIS file's directory on sys.path -- not the working
# directory -- so `import app` fails even though /app is right there.
# Adding the parent explicitly makes both forms work: that one, and
# `python -m scripts.check_recipe_import` from /app.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import SessionLocal
from app.services import recipe_service

DEFAULT_FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "pumpkin_chiffon_pie_pypdf.txt"

# Identifies the pie by content, so the assertions below still run on a
# copy at any path. This exact string is an ingredient LINE in the real
# source -- see tests/test_recipe_components.py.
PIE_MARKER = "(scant) cup plus 2 Tbsp. sugar"


def load_source(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return recipe_service.extract_pdf_text(path.read_bytes())
    return path.read_text(encoding="utf-8")


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_FIXTURE
    if not path.exists():
        print(f"No such source: {path}\n")
        print("The fixture is not baked into the image. Copy it in first:")
        print("  docker compose cp backend/tests/fixtures/pumpkin_chiffon_pie_pypdf.txt chef:/tmp/pie.txt")
        print("  docker compose exec chef python scripts/check_recipe_import.py /tmp/pie.txt")
        return 2
    source = load_source(path)
    print(f"source: {path}  ({len(source)} chars)\n")

    db = SessionLocal()
    try:
        raw = recipe_service._extract_via_ollama(db, source)
    finally:
        db.close()

    parsed = recipe_service.parse_recipe_response(raw)
    if not parsed:
        print("FAIL: model returned nothing parseable\n")
        print(raw[:2000])
        return 1

    # RAW first, then coerced. A run that printed only the coerced result
    # reported "no ingredient carries a component" when the model had in
    # fact emitted every one and coerce_recipe_fields -- an allowlist
    # rebuild that had never been taught the field -- discarded them. The
    # table below could not tell "the model did not say it" apart from
    # "we threw it away", which is the single most expensive thing this
    # script can be vague about, since a live run is the scarce resource.
    raw_ingredients = (recipe_service._extract_json_object(raw) or {}).get("ingredients") or []
    print(f"title: {parsed.get('title')}")
    print(f"{len(raw_ingredients)} ingredients straight from the model:\n")
    for ing in raw_ingredients:
        if not isinstance(ing, dict):
            print(f"  !! not an object: {ing!r}")
            continue
        comp = ing.get("component")
        comp = "NULL" if comp is None else str(comp)
        note = f"  ({ing.get('prep_note')})" if ing.get("prep_note") else ""
        print(
            f"  [{comp:>22}] {ing.get('quantity')!s:>8} {ing.get('unit') or ''!s:<8} {ing.get('ingredient_name')}{note}"
        )
    print()

    ingredients = parsed.get("ingredients") or []
    lost = [
        key
        for key in ("component", "quantity", "unit", "prep_note")
        if any(i.get(key) is not None for i in raw_ingredients if isinstance(i, dict))
        and all(i.get(key) is None for i in ingredients)
    ]
    if lost:
        print(
            f"!! the model emitted {', '.join(lost)} and parsing dropped every one -- this is OUR bug, not the prompt's\n"
        )

    if PIE_MARKER not in source:
        print("Source is not the Pumpkin Chiffon Pie -- no assertions to run. Review the table above.")
        return 0

    failures = []

    def find(name, component=None, quantity=None, unit=None):
        return [
            i
            for i in ingredients
            if name in i["ingredient_name"].lower()
            and (component is None or (i.get("component") or "").lower().startswith(component))
            and (quantity is None or i.get("quantity") == quantity)
            and (unit is None or (i.get("unit") or "").lower().startswith(unit))
        ]

    # Rule numbers refer to RECIPE_IMPORT_PROMPT's numbered list. They had
    # drifted out of date; keep them honest or drop them.

    # 1. The crust's sugar is 2 Tbsp., not the method's "scant 1/2 cup".
    if not find("sugar", component="crust", quantity=2):
        failures.append("crust sugar is not 2 Tbsp. -- method text is still being mined (rule 1)")

    # 2. The filling's compound amount stays two entries, never summed to 1.75.
    filling_sugar = find("sugar", component="filling")
    if len(filling_sugar) != 2:
        failures.append(f"filling sugar should be 2 entries (3/4 cup + 2 Tbsp.), got {len(filling_sugar)} (rule 2)")
    if any(i.get("quantity") == 1.75 for i in filling_sugar):
        failures.append("filling sugar summed to 1.75 cup -- compound amount was merged (rule 2)")

    # 3. No ingredient invented from the preparation text.
    if find("crumb"):
        failures.append("a graham cracker CRUMBS row exists -- it is only in the prep text (rule 1)")

    # 4. Components were emitted at all. Checked against the RAW model
    # output, so this reports what the model did rather than what survived
    # our own parsing -- those were conflated once already.
    if not any(i.get("component") for i in raw_ingredients if isinstance(i, dict)):
        failures.append("the model emitted no component on any ingredient -- rule 3 produced nothing")

    # 5. Quantities decomposed into their own field rather than being left
    # inside the name ("unflavored gelatin (2 1/2 tsp.)"), which is what a
    # model does when it has lost the per-field template.
    if all(i.get("quantity") is None for i in ingredients):
        failures.append("every quantity is null -- the model stopped splitting fields, check the OUTPUT FORMAT line")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("PASS: every check clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
