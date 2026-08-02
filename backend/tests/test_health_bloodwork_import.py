"""Tests for the B8.1 bloodwork import parsing/extraction helpers --
same "pure function, no DB/Ollama" test style as recipe_service's own
parse_recipe_response tests. The router's job-queue wiring itself isn't
exercised here (no live Ollama reaches this sandbox, same standing
constraint as every other AI-consuming endpoint in this project); what's
tested is the defensive parsing that has to handle real, imperfect model
output.
"""
from __future__ import annotations

import json

from app.services import health_service


# --- extract_bloodwork_text -------------------------------------------------


def test_extract_bloodwork_text_plain_text():
    raw = b"LDL: 110 mg/dL\nHDL: 55 mg/dL\n"
    assert health_service.extract_bloodwork_text(raw, "labs.txt", "text/plain") == raw.decode("utf-8")


def test_extract_bloodwork_text_csv_by_extension():
    raw = b"test,value,unit\nLDL,110,mg/dL\n"
    result = health_service.extract_bloodwork_text(raw, "export.csv", "")
    assert "LDL" in result and "110" in result


def test_extract_bloodwork_text_pdf_dispatches_to_pypdf(monkeypatch):
    called = {}

    def fake_extract_pdf_text(pdf_bytes):
        called["bytes"] = pdf_bytes
        return "extracted pdf text"

    monkeypatch.setattr(health_service, "extract_pdf_text", fake_extract_pdf_text)
    result = health_service.extract_bloodwork_text(b"%PDF-1.4 fake", "report.pdf", "application/pdf")
    assert result == "extracted pdf text"
    assert called["bytes"] == b"%PDF-1.4 fake"


def test_extract_bloodwork_text_handles_bad_decode_gracefully():
    # Non-UTF8 bytes with no recognizable PDF marker -- errors="replace"
    # means this never raises, just produces replacement characters.
    raw = b"\xff\xfe\x00\x01"
    result = health_service.extract_bloodwork_text(raw, "weird.bin", "")
    assert isinstance(result, str)


# --- parse_bloodwork_response ------------------------------------------------


def _wrap(entries):
    return json.dumps({"entries": entries})


def test_parse_strict_json_single_entry():
    raw = _wrap(
        [
            {
                "entry_date": "2026-07-15",
                "weight_kg": 90.5,
                "ldl_mg_dl": 130,
                "hdl_mg_dl": 45,
                "total_cholesterol_mg_dl": 210,
                "triglycerides_mg_dl": 150,
                "blood_pressure_systolic": 128,
                "blood_pressure_diastolic": 82,
                "blood_glucose_mg_dl": 95,
            }
        ]
    )
    entries = health_service.parse_bloodwork_response(raw)
    assert len(entries) == 1
    e = entries[0]
    assert e["entry_date"] == "2026-07-15"
    assert e["ldl_mg_dl"] == 130.0
    assert e["blood_pressure_systolic"] == 128


def test_parse_markdown_wrapped_json():
    raw = "Here are the results:\n```json\n" + _wrap([{"ldl_mg_dl": 100}]) + "\n```"
    entries = health_service.parse_bloodwork_response(raw)
    assert len(entries) == 1
    assert entries[0]["ldl_mg_dl"] == 100.0


def test_parse_multiple_entries_multiple_draws():
    raw = _wrap(
        [
            {"entry_date": "2026-01-10", "ldl_mg_dl": 140},
            {"entry_date": "2026-04-10", "ldl_mg_dl": 125},
        ]
    )
    entries = health_service.parse_bloodwork_response(raw)
    assert len(entries) == 2
    assert entries[0]["entry_date"] == "2026-01-10"
    assert entries[1]["entry_date"] == "2026-04-10"


def test_parse_drops_entries_with_no_actual_values():
    raw = _wrap([{"entry_date": "2026-01-10"}, {"ldl_mg_dl": 110}])
    entries = health_service.parse_bloodwork_response(raw)
    # The date-only row (no metrics at all) is dropped; the metric-bearing
    # row survives even with no date.
    assert len(entries) == 1
    assert entries[0]["ldl_mg_dl"] == 110.0


def test_parse_keeps_blood_pressure_only_entry():
    raw = _wrap([{"blood_pressure_systolic": 130, "blood_pressure_diastolic": 85}])
    entries = health_service.parse_bloodwork_response(raw)
    assert len(entries) == 1
    assert entries[0]["blood_pressure_systolic"] == 130


def test_parse_garbage_input_returns_empty_list():
    assert health_service.parse_bloodwork_response("not json at all") == []
    assert health_service.parse_bloodwork_response("") == []


def test_parse_non_list_entries_key_returns_empty_list():
    assert health_service.parse_bloodwork_response(json.dumps({"entries": "not a list"})) == []


def test_parse_skips_non_dict_items_in_entries_list():
    raw = json.dumps({"entries": ["garbage", {"ldl_mg_dl": 120}]})
    entries = health_service.parse_bloodwork_response(raw)
    assert len(entries) == 1
    assert entries[0]["ldl_mg_dl"] == 120.0


# --- date parsing (via parse_bloodwork_response's entry_date handling) -----


def test_parse_various_date_formats():
    for date_str, expected in [
        ("2026-07-15", "2026-07-15"),
        ("07/15/2026", "2026-07-15"),
        ("07-15-2026", "2026-07-15"),
    ]:
        raw = _wrap([{"entry_date": date_str, "ldl_mg_dl": 100}])
        entries = health_service.parse_bloodwork_response(raw)
        assert entries[0]["entry_date"] == expected, f"failed for input {date_str}"


def test_parse_unrecognized_date_format_becomes_none_not_a_crash():
    raw = _wrap([{"entry_date": "sometime last spring", "ldl_mg_dl": 100}])
    entries = health_service.parse_bloodwork_response(raw)
    assert entries[0]["entry_date"] is None
    assert entries[0]["ldl_mg_dl"] == 100.0
