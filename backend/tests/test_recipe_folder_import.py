"""Tests for backlog B13.1's folder-scan batch recipe import
(app.services.recipe_folder_import_service): file discovery/filtering
(list_importable_files) against a real temp directory tree, and the
batch job body (scan_and_parse)'s per-file success/error handling.

recipe_service.parse_recipe_file_content/finish_recipe_parse are
monkeypatched at the boundary rather than exercised end-to-end here --
this project has never unit-tested the actual Ollama-calling AI-dispatch
path (no live Ollama reachable in this sandbox; see
test_inventory_import.py's own docstring for the same discipline), so
this file sticks to what recipe_folder_import_service itself is
responsible for: finding the right files, and turning per-file
success/failure into the right response shape."""

from __future__ import annotations

from app.services import recipe_folder_import_service as rfi

# --- list_importable_files ----------------------------------------------


def test_list_importable_files_returns_error_for_missing_folder(tmp_path):
    missing = tmp_path / "does-not-exist"
    result = rfi.list_importable_files(str(missing))
    assert result["error"] is not None
    assert result["files"] == []


def test_list_importable_files_returns_error_for_blank_path():
    result = rfi.list_importable_files("")
    assert result["error"] is not None


def test_list_importable_files_filters_by_extension(tmp_path):
    (tmp_path / "recipe1.txt").write_text("some text")
    (tmp_path / "recipe2.md").write_text("# markdown")
    (tmp_path / "recipe3.json").write_text("{}")
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8\xff")  # not a supported extension
    (tmp_path / "notes.docx").write_bytes(b"junk")

    result = rfi.list_importable_files(str(tmp_path))
    names = {p.split("/")[-1] for p in result["files"]}
    assert names == {"recipe1.txt", "recipe2.md", "recipe3.json"}
    assert result["error"] is None


def test_list_importable_files_skips_dotfiles_and_dot_directories(tmp_path):
    (tmp_path / "real.txt").write_text("a real recipe")
    (tmp_path / ".hidden.txt").write_text("should be skipped")
    dot_dir = tmp_path / ".onedrive-cache"
    dot_dir.mkdir()
    (dot_dir / "cached.txt").write_text("should be skipped too")

    result = rfi.list_importable_files(str(tmp_path))
    names = {p.split("/")[-1] for p in result["files"]}
    assert names == {"real.txt"}


def test_list_importable_files_recurses_into_subdirectories(tmp_path):
    subdir = tmp_path / "Soups"
    subdir.mkdir()
    (subdir / "chili.txt").write_text("chili recipe")
    (tmp_path / "top-level.txt").write_text("top level recipe")

    result = rfi.list_importable_files(str(tmp_path))
    names = {p.split("/")[-1] for p in result["files"]}
    assert names == {"chili.txt", "top-level.txt"}


def test_list_importable_files_skips_oversized_files(tmp_path, monkeypatch):
    monkeypatch.setattr(rfi, "MAX_FILE_SIZE_BYTES", 10)  # tiny, for a fast test
    (tmp_path / "small.txt").write_text("ok")
    (tmp_path / "big.txt").write_text("this text is definitely more than ten bytes long")

    result = rfi.list_importable_files(str(tmp_path))
    names = {p.split("/")[-1] for p in result["files"]}
    assert names == {"small.txt"}
    assert len(result["skipped"]) == 1
    assert result["skipped"][0][0].endswith("big.txt")
    assert "too large" in result["skipped"][0][1]


def test_list_importable_files_truncates_at_max_files(tmp_path, monkeypatch):
    monkeypatch.setattr(rfi, "MAX_FILES", 2)
    for i in range(5):
        (tmp_path / f"recipe{i}.txt").write_text(f"recipe {i}")

    result = rfi.list_importable_files(str(tmp_path))
    assert len(result["files"]) == 2
    assert result["truncated"] is True


# --- scan_and_parse -------------------------------------------------------


def test_scan_and_parse_reports_folder_level_error(tmp_path):
    result = rfi.scan_and_parse(db=None, folder_path=str(tmp_path / "nope"))
    assert result["error"] is not None
    assert result["items"] == []


def test_scan_and_parse_builds_ok_items_from_successful_files(tmp_path, monkeypatch):
    (tmp_path / "chili.txt").write_text("chili recipe text")

    def fake_parse_file_content(db, raw_bytes, filename, content_type=""):
        return {
            "raw_output": "raw model output",
            "default_source": "import_file",
            "citation": {},
            "image_path": None,
            "jsonld_parsed": None,
        }

    def fake_finish_parse(raw_output, default_source, citation, image_path, jsonld_parsed):
        return {"title": "Chili", "ingredients": [], "instructions": [], "source": default_source}

    monkeypatch.setattr(rfi.recipe_service, "parse_recipe_file_content", fake_parse_file_content)
    monkeypatch.setattr(rfi.recipe_service, "finish_recipe_parse", fake_finish_parse)

    result = rfi.scan_and_parse(db=None, folder_path=str(tmp_path))
    assert result["error"] is None
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert item["status"] == "ok"
    assert item["filename"] == "chili.txt"
    assert item["relative_path"] == "chili.txt"
    assert item["recipe"]["title"] == "Chili"
    assert item["error"] is None


def test_scan_and_parse_reports_relative_path_for_nested_files(tmp_path, monkeypatch):
    subdir = tmp_path / "Soups"
    subdir.mkdir()
    (subdir / "chili.txt").write_text("chili recipe text")

    monkeypatch.setattr(
        rfi.recipe_service,
        "parse_recipe_file_content",
        lambda db, raw_bytes, filename, content_type="": {
            "raw_output": "x",
            "default_source": "import_file",
            "citation": {},
            "image_path": None,
            "jsonld_parsed": None,
        },
    )
    monkeypatch.setattr(
        rfi.recipe_service, "finish_recipe_parse", lambda *a, **k: {"title": "Chili", "ingredients": []}
    )

    result = rfi.scan_and_parse(db=None, folder_path=str(tmp_path))
    assert result["items"][0]["relative_path"] == "Soups/chili.txt"


def test_scan_and_parse_isolates_one_files_failure_from_the_rest(tmp_path, monkeypatch):
    (tmp_path / "good.txt").write_text("a real recipe")
    (tmp_path / "bad.txt").write_text("not a recipe at all")

    def fake_parse_file_content(db, raw_bytes, filename, content_type=""):
        if filename == "bad.txt":
            raise RuntimeError("Could not extract a recipe from that input")
        return {
            "raw_output": "raw",
            "default_source": "import_file",
            "citation": {},
            "image_path": None,
            "jsonld_parsed": None,
        }

    monkeypatch.setattr(rfi.recipe_service, "parse_recipe_file_content", fake_parse_file_content)
    monkeypatch.setattr(
        rfi.recipe_service, "finish_recipe_parse", lambda *a, **k: {"title": "Good Recipe", "ingredients": []}
    )

    result = rfi.scan_and_parse(db=None, folder_path=str(tmp_path))
    by_name = {item["filename"]: item for item in result["items"]}
    assert by_name["good.txt"]["status"] == "ok"
    assert by_name["bad.txt"]["status"] == "error"
    assert "Could not extract" in by_name["bad.txt"]["error"]
    assert by_name["bad.txt"]["recipe"] is None
