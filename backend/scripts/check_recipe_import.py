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

    ingredients = parsed.get("ingredients") or []
    print(f"title: {parsed.get('title')}")
    print(f"{len(ingredients)} ingredients\n")
    for ing in ingredients:
        comp = ing.get("component") or "-"
        note = f"  ({ing['prep_note']})" if ing.get("prep_note") else ""
        print(f"  [{comp:>22}] {ing.get('quantity')!s:>8} {ing.get('unit') or ''!s:<8} {ing['ingredient_name']}{note}")
    print()

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

    # 1. The crust's sugar is 2 Tbsp., not the method's "scant 1/2 cup".
    if not find("sugar", component="crust", quantity=2):
        failures.append("crust sugar is not 2 Tbsp. -- method text is still being mined (rule 1)")

    # 2. The filling's compound amount stays two entries, never summed to 1.75.
    filling_sugar = find("sugar", component="filling")
    if len(filling_sugar) != 2:
        failures.append(f"filling sugar should be 2 entries (3/4 cup + 2 Tbsp.), got {len(filling_sugar)} (rule 4)")
    if any(i.get("quantity") == 1.75 for i in filling_sugar):
        failures.append("filling sugar summed to 1.75 cup -- compound amount was merged (rule 4)")

    # 3. No ingredient invented from the preparation text.
    if find("crumb"):
        failures.append("a graham cracker CRUMBS row exists -- it is only in the prep text (rule 1)")

    # 4. Components were emitted at all.
    if not any(i.get("component") for i in ingredients):
        failures.append("no ingredient carries a component -- rule 3 produced nothing")

    if failures:
        print("FAILURES:")
        for f in failures:
            print(f"  - {f}")
        return 1

    print("PASS: all four checks clean.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
