from __future__ import annotations

from datetime import timedelta

from backend.app.algorithms.session import analyze_opening_ranges
from session_test_fixtures import SESSION_START, golden_candles


def test_session_opening_range_tracks_or5_or15_or30_independently() -> None:
    result = analyze_opening_ranges(_continuous_bars(31))

    assert result["references"]["OR5"]["status"] == "complete"
    assert result["references"]["OR15"]["status"] == "complete"
    assert result["references"]["OR30"]["status"] == "complete"
    assert result["references"]["OR5"]["completionTimestamp"] != result["references"]["OR15"]["completionTimestamp"]


def test_session_opening_range_flags_breakout_acceptance_and_failed_breakout() -> None:
    accepted = analyze_opening_ranges(golden_candles("or_breakout_acceptance"))
    failed = analyze_opening_ranges(golden_candles("failed_or_breakout")[:8])

    assert accepted["breakouts"]["OR5"]["closeBeyondRange"] is True
    assert accepted["breakouts"]["OR5"]["accepted"] is True
    assert failed["breakouts"]["OR5"]["failedBreakout"] is True
    assert failed["breakouts"]["OR5"]["rejectionBackInside"] is True


def test_session_opening_range_missing_reference_bar_degrades_not_silently_ignored() -> None:
    bars = golden_candles("or_breakout_acceptance")
    del bars[2]

    result = analyze_opening_ranges(bars)

    assert result["references"]["OR5"]["status"] != "complete"
    assert result["references"]["OR5"]["missingBarCount"] >= 1


def _continuous_bars(length: int) -> list[dict[str, object]]:
    return [
        {
            "timestamp": (SESSION_START + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
            "open": 100 + index * 0.02,
            "high": 100 + index * 0.02 + 0.1,
            "low": 100 + index * 0.02 - 0.1,
            "close": 100 + index * 0.02,
            "volume": 100_000,
        }
        for index in range(length)
    ]
