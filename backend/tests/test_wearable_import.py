"""Tests for backlog B8.2's wearable/health-platform import:
health_service.parse_apple_health_export (deterministic Apple Health
export.xml/.zip parsing) and parse_wearable_ai_response (the AI-
extraction fallback for any other export format, sharing
parse_bloodwork_response's own defensive-parsing discipline).
"""

from __future__ import annotations

import io
import zipfile

import pytest

from app.services import health_service


def _apple_health_xml(records: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<HealthData locale="en_US">\n' + records + "\n</HealthData>\n"
    ).encode("utf-8")


def test_parses_weight_records_taking_last_sample_of_day():
    xml = _apple_health_xml(
        '<Record type="HKQuantityTypeIdentifierBodyMass" sourceName="Health" unit="lb" '
        'value="180.0" startDate="2026-07-15 07:00:00 -0500" endDate="2026-07-15 07:00:00 -0500"/>\n'
        '<Record type="HKQuantityTypeIdentifierBodyMass" sourceName="Health" unit="lb" '
        'value="178.5" startDate="2026-07-15 19:00:00 -0500" endDate="2026-07-15 19:00:00 -0500"/>\n'
    )
    entries = health_service.parse_apple_health_export(xml, "export.xml")
    assert len(entries) == 1
    assert entries[0]["entry_date"] == "2026-07-15"
    # 178.5 lb (the LATER same-day sample) -> kg, not the morning reading.
    assert abs(entries[0]["weight_kg"] - 178.5 / 2.20462) < 0.01


def test_parses_weight_records_already_in_kg():
    xml = _apple_health_xml(
        '<Record type="HKQuantityTypeIdentifierBodyMass" unit="kg" value="81.2" '
        'startDate="2026-07-20 08:00:00 -0500" endDate="2026-07-20 08:00:00 -0500"/>\n'
    )
    entries = health_service.parse_apple_health_export(xml, "export.xml")
    assert entries[0]["weight_kg"] == 81.2


def test_sums_step_records_across_the_same_day():
    xml = _apple_health_xml(
        '<Record type="HKQuantityTypeIdentifierStepCount" unit="count" value="500" '
        'startDate="2026-07-15 07:00:00 -0500" endDate="2026-07-15 07:05:00 -0500"/>\n'
        '<Record type="HKQuantityTypeIdentifierStepCount" unit="count" value="3200" '
        'startDate="2026-07-15 12:00:00 -0500" endDate="2026-07-15 12:30:00 -0500"/>\n'
        '<Record type="HKQuantityTypeIdentifierStepCount" unit="count" value="1000" '
        'startDate="2026-07-16 08:00:00 -0500" endDate="2026-07-16 08:10:00 -0500"/>\n'
    )
    entries = health_service.parse_apple_health_export(xml, "export.xml")
    by_date = {e["entry_date"]: e["steps"] for e in entries}
    assert by_date["2026-07-15"] == 3700
    assert by_date["2026-07-16"] == 1000


def test_ignores_unrelated_record_types():
    xml = _apple_health_xml(
        '<Record type="HKQuantityTypeIdentifierHeartRate" unit="count/min" value="72" '
        'startDate="2026-07-15 07:00:00 -0500" endDate="2026-07-15 07:00:00 -0500"/>\n'
        '<Record type="HKQuantityTypeIdentifierBodyMass" unit="lb" value="180.0" '
        'startDate="2026-07-15 07:00:00 -0500" endDate="2026-07-15 07:00:00 -0500"/>\n'
    )
    entries = health_service.parse_apple_health_export(xml, "export.xml")
    assert len(entries) == 1
    assert entries[0]["weight_kg"] is not None


def test_accepts_export_zip_and_finds_export_xml():
    xml_bytes = _apple_health_xml(
        '<Record type="HKQuantityTypeIdentifierBodyMass" unit="kg" value="80.0" '
        'startDate="2026-07-15 07:00:00 -0500" endDate="2026-07-15 07:00:00 -0500"/>\n'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("apple_health_export/export.xml", xml_bytes)
        zf.writestr(
            "apple_health_export/export_cda.xml", b"<HealthData></HealthData>"
        )  # longer name, should be skipped
    entries = health_service.parse_apple_health_export(buf.getvalue(), "export.zip")
    assert len(entries) == 1
    assert entries[0]["weight_kg"] == 80.0


def test_zip_without_export_xml_raises_readable_error():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("some_other_file.txt", b"not a health export")
    with pytest.raises(ValueError, match=r"export\.xml"):
        health_service.parse_apple_health_export(buf.getvalue(), "export.zip")


def test_empty_export_returns_no_entries():
    xml = _apple_health_xml("")
    assert health_service.parse_apple_health_export(xml, "export.xml") == []


# --- AI-extraction fallback path (non-Apple-Health files) -------------


def test_parse_wearable_ai_response_extracts_weight_and_steps():
    raw = '{"entries": [{"entry_date": "2026-07-15", "weight_kg": 79.5, "steps": 8342}]}'
    entries = health_service.parse_wearable_ai_response(raw)
    assert entries == [{"entry_date": "2026-07-15", "weight_kg": 79.5, "steps": 8342}]


def test_parse_wearable_ai_response_drops_entries_with_neither_field():
    raw = '{"entries": [{"entry_date": "2026-07-15"}, {"entry_date": "2026-07-16", "steps": 100}]}'
    entries = health_service.parse_wearable_ai_response(raw)
    assert len(entries) == 1
    assert entries[0]["entry_date"] == "2026-07-16"


def test_parse_wearable_ai_response_handles_garbage_input():
    assert health_service.parse_wearable_ai_response("not json at all") == []
    assert health_service.parse_wearable_ai_response("") == []
