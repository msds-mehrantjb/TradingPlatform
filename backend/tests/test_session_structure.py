from __future__ import annotations

import pytest

from backend.app.algorithms.session import analyze_opening_ranges, analyze_session_structure, analyze_vwap
from session_test_fixtures import golden_candles


@pytest.mark.parametrize(
    ("fixture", "expected_behavior", "reason"),
    (
        ("strong_morning_trend", "trend_up", "session.structure.behavior.trend_up"),
        ("balanced_range", "balanced_range", "session.structure.behavior.balanced_range"),
        ("choppy_vwap_rotation", "choppy", "session.structure.behavior.choppy"),
        ("or_breakout_acceptance", "valid_breakout_up", "session.structure.behavior.valid_breakout"),
        ("failed_or_breakout", "failed_breakout_up", "session.structure.behavior.failed_breakout"),
    ),
)
def test_session_structure_golden_patterns_have_reason_codes(fixture: str, expected_behavior: str, reason: str) -> None:
    candles = golden_candles(fixture)
    result = analyze_session_structure(candles, _context(candles))

    assert result["behavior"] == expected_behavior
    assert reason in result["reasonCodes"]


def test_session_structure_no_behavior_from_single_unconfirmed_candle() -> None:
    result = analyze_session_structure(golden_candles("or_breakout_acceptance")[:1], _context(golden_candles("or_breakout_acceptance")[:1]))

    assert result["behavior"] in {"unknown", "balanced_range"}
    assert result["breakout"]["validBreakout"] is False


def _context(candles: list[dict[str, object]]) -> dict[str, object]:
    vwap = analyze_vwap(candles)
    return {
        "openingRanges": analyze_opening_ranges(candles),
        "vwapFeatures": vwap,
        "participationEvidence": {"oneMinuteRelativeVolume": 1.5},
        "vwapCrossingFrequencyPerHour": (vwap.get("current") or {}).get("crossingFrequencyPerHour") or 0,
    }
