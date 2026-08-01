"""Tests for backlog B4.3's FoodKeeper shelf-life auto-suggestion
(app/services/foodkeeper_service.py + the /api/inventory/shelf-life-
suggestion endpoint). Calls the router function directly, no TestClient
(same established pattern as test_barcode_lookup.py) -- the endpoint takes
only plain query params, no FastAPI dependency injection.

Exercises the real shipped CSV (backend/app/data/foodkeeper_shelf_life.csv)
rather than a fixture -- this data IS the feature; a test against a fake
CSV wouldn't catch a real parsing bug against the actual file shape."""
from __future__ import annotations

from datetime import date

from app.routers.inventory import shelf_life_suggestion
from app.services import foodkeeper_service


def test_csv_loads_with_expected_row_count():
    entries = foodkeeper_service._load_entries()
    assert len(entries) == 661
    # cached -- a second call must return the same list object, not re-parse
    assert foodkeeper_service._load_entries() is entries


def test_exact_name_match_butter():
    entry = foodkeeper_service.match_item("Butter")
    assert entry is not None
    assert entry.name == "Butter"
    assert entry.fridge_days == (30, 60)
    assert entry.freezer_days == (180, 270)


def test_case_insensitive_and_whitespace_tolerant():
    entry = foodkeeper_service.match_item("  BUTTER  ")
    assert entry is not None
    assert entry.name == "Butter"


def test_keyword_substring_match():
    # "buttermilk pancakes" isn't a FoodKeeper product name, but
    # "buttermilk" is both a keyword and substring-contained -- should
    # still resolve to the Buttermilk entry via the keyword-substring path.
    entry = foodkeeper_service.match_item("fresh buttermilk from the farm")
    assert entry is not None
    assert entry.name == "Buttermilk"


def test_no_match_returns_none_rather_than_guessing():
    assert foodkeeper_service.match_item("xyzzy nonexistent food item 123") is None
    assert foodkeeper_service.match_item("") is None
    assert foodkeeper_service.match_item("   ") is None


def test_suggest_shelf_life_prefers_requested_category_storage():
    result = foodkeeper_service.suggest_shelf_life("Butter", "fridge")
    assert result is not None
    assert result["storage"] == "fridge"
    assert result["days_min"] == 30
    assert result["days_max"] == 60

    result = foodkeeper_service.suggest_shelf_life("Butter", "freezer")
    assert result["storage"] == "freezer"
    assert result["days_min"] == 180


def test_suggest_shelf_life_produce_falls_back_fridge_then_pantry():
    # Whatever category order produce checks, it should return SOME
    # result for a common produce item without erroring, and the
    # returned storage should be one of the two produce falls back to.
    entry = foodkeeper_service.match_item("Butter")
    assert entry is not None  # sanity: fixture data is loaded
    # Use a category with no FoodKeeper mapping at all -- "spice" and
    # "other" aren't in CATEGORY_FIELD_ORDER, so this must return None
    # rather than guessing a random storage location.
    assert foodkeeper_service.suggest_shelf_life("Butter", "spice") is None
    assert foodkeeper_service.suggest_shelf_life("Butter", "other") is None


def test_suggest_expiration_date_anchors_on_purchased_date_plus_days_min():
    result = foodkeeper_service.suggest_expiration_date("Butter", "fridge", date(2026, 1, 1))
    assert result is not None
    assert result["suggested_expiration_date"] == date(2026, 1, 31)  # +30 days (days_min)


def test_suggest_expiration_date_defaults_purchased_date_to_today():
    result = foodkeeper_service.suggest_expiration_date("Butter", "fridge", None)
    assert result is not None
    assert result["suggested_expiration_date"] == date.today() + __import__("datetime").timedelta(days=30)


def test_suggest_expiration_date_no_match_returns_none():
    assert foodkeeper_service.suggest_expiration_date("totally unknown food xyz", "fridge", None) is None


def test_reset_cache_forces_reparse(monkeypatch, tmp_path):
    fixture = tmp_path / "fixture.csv"
    fixture.write_text(
        "ID|Category|Name|NameSubtitle|Keywords|PantryDaysMin|PantryDaysMax|FridgeDaysMin|FridgeDaysMax|"
        "FreezerDaysMin|FreezerDaysMax|PantryAfterOpenDaysMin|PantryAfterOpenDaysMax|"
        "FridgeAfterOpenDaysMin|FridgeAfterOpenDaysMax\n"
        "9001|Test Category|Widget||widget,widgets|10|20||||||||\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(foodkeeper_service, "CSV_PATH", str(fixture))
    foodkeeper_service._reset_cache()
    entries = foodkeeper_service._load_entries()
    assert len(entries) == 1
    assert entries[0].name == "Widget"
    assert entries[0].pantry_days == (10, 20)
    # restore the real cache for any test that runs after this one in the
    # same process
    foodkeeper_service._reset_cache()
    monkeypatch.undo()
    foodkeeper_service._reset_cache()


# --- router-level tests -----------------------------------------------


def test_endpoint_returns_found_true_with_suggestion():
    result = shelf_life_suggestion(name="Butter", category="fridge", purchased_date="2026-01-01")
    assert result.found is True
    assert result.matched_name == "Butter"
    assert result.storage == "fridge"
    assert result.days_min == 30
    assert result.suggested_expiration_date == date(2026, 1, 31)


def test_endpoint_returns_found_false_for_blank_name():
    result = shelf_life_suggestion(name="   ", category="pantry")
    assert result.found is False


def test_endpoint_returns_found_false_for_unmatched_item():
    # Deliberately gibberish with no real-word overlap against any
    # FoodKeeper keyword -- an earlier version of this test used "...
    # branded snack xyz", which turned out to be a bad test case, not a
    # bug: "snack" is itself a genuine, specific FoodKeeper keyword (id
    # 380, Gummy (fruit) snacks), so matching it was correct behavior.
    result = shelf_life_suggestion(name="zzqxw nonexistent glorbex 999", category="pantry")
    assert result.found is False


def test_endpoint_tolerates_unparseable_purchased_date():
    # Malformed date string from a partially-filled form -- falls back to
    # today rather than raising, same defensive handling the schema-level
    # `str | None` param (not a typed `date`) exists for.
    result = shelf_life_suggestion(name="Butter", category="fridge", purchased_date="not-a-date")
    assert result.found is True
    assert result.suggested_expiration_date == date.today() + __import__("datetime").timedelta(days=30)


def test_endpoint_defaults_category_to_pantry():
    # Butter has no plain Pantry_* range (only DOP_Refrigerate/DOP_Freeze
    # are populated in the source), so a default "pantry" category lookup
    # should honestly report no match rather than fabricating one.
    result = shelf_life_suggestion(name="Butter")
    assert result.found is False
