"""Unit tests for the B2.3 dietary-pattern preset registry
(app.services.dietary_pattern_service)."""

from __future__ import annotations

from app.services import dietary_pattern_service


def test_portfolio_ldl_is_a_registered_pattern():
    assert "portfolio_ldl" in dietary_pattern_service.DIETARY_PATTERN_KEYS
    keys = [p["key"] for p in dietary_pattern_service.DIETARY_PATTERNS]
    assert keys == sorted(set(keys)) or len(keys) == len(set(keys))  # no duplicate keys


def test_every_registered_pattern_has_guidance():
    # Registry and guidance dict must not drift apart -- every key in
    # DIETARY_PATTERNS should resolve to real, non-empty guidance text.
    for pattern in dietary_pattern_service.DIETARY_PATTERNS:
        guidance = dietary_pattern_service.get_pattern_guidance(pattern["key"])
        assert guidance, f"{pattern['key']} has no guidance text"


def test_get_pattern_guidance_none_for_no_selection():
    assert dietary_pattern_service.get_pattern_guidance(None) is None
    assert dietary_pattern_service.get_pattern_guidance("") is None


def test_get_pattern_guidance_none_for_unrecognized_key():
    # Defensive: a stale value left over from a removed preset should
    # degrade to "no guidance," not raise.
    assert dietary_pattern_service.get_pattern_guidance("some_removed_preset") is None


def test_portfolio_ldl_guidance_names_the_four_components():
    guidance = dietary_pattern_service.get_pattern_guidance("portfolio_ldl")
    for term in ("soluble fiber", "sterols", "Soy protein", "Tree nuts"):
        assert term in guidance
