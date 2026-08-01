"""Tests for backlog B13.1's POST /api/recipes/import-folder/confirm
(app/routers/recipes.py's confirm_folder_import) -- the bulk-create step
after a household reviews a folder-scan preview. Calls the router
function directly against a real (temp-file SQLite) db_session, same
pattern this repo already avoids TestClient for (see
test_barcode_lookup.py) -- confirm_folder_import takes no FastAPI
dependency injection beyond a plain Session, so no app/client needed."""
from __future__ import annotations

from app.models import Recipe
from app.routers.recipes import confirm_folder_import
from app.schemas.recipe import RecipeCreate, RecipeFolderImportConfirmRequest


def test_confirm_folder_import_creates_multiple_recipes(db_session):
    payload = RecipeFolderImportConfirmRequest(
        recipes=[
            RecipeCreate(
                title="Chili",
                source="import_file",
                ingredients=[{"ingredient_name": "ground beef", "quantity": 1, "unit": "lb"}],
                tags=["dinner"],
            ),
            RecipeCreate(
                title="Banana Bread",
                source="import_file_jsonld",
                ingredients=[{"ingredient_name": "bananas", "quantity": 3, "unit": "count"}],
                tags=["baking"],
            ),
        ]
    )
    result = confirm_folder_import(payload, db_session)
    assert len(result) == 2
    titles = {r.title for r in result}
    assert titles == {"Chili", "Banana Bread"}
    assert db_session.query(Recipe).count() == 2

    chili = next(r for r in result if r.title == "Chili")
    assert chili.source == "import_file"
    assert len(chili.ingredients) == 1
    assert chili.ingredients[0].ingredient_name == "ground beef"
    assert chili.tags == ["dinner"]


def test_confirm_folder_import_stamps_ai_estimated_provenance_when_nutrition_present(db_session):
    payload = RecipeFolderImportConfirmRequest(
        recipes=[
            RecipeCreate(
                title="Oatmeal",
                source="import_file",
                nutrition={"calories": 150},
                ingredients=[{"ingredient_name": "oats", "quantity": 1, "unit": "cup"}],
            )
        ]
    )
    result = confirm_folder_import(payload, db_session)
    assert result[0].nutrition_provenance == "ai_estimated"


def test_confirm_folder_import_handles_empty_list(db_session):
    result = confirm_folder_import(RecipeFolderImportConfirmRequest(recipes=[]), db_session)
    assert result == []
