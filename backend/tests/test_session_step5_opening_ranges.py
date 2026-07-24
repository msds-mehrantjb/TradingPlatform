from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.app.algorithms.session import SessionConfig, analyze_opening_ranges, classify_session


SESSION_START = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)
EARLY_CLOSE_START = datetime(2026, 11, 27, 14, 30, tzinfo=UTC)


def test_session_step5_exact_5_15_30_minute_completion() -> None:
    at_or5 = analyze_opening_ranges([_bar(index) for index in range(6)])
    at_or15 = analyze_opening_ranges([_bar(index) for index in range(16)])
    at_or30 = analyze_opening_ranges([_bar(index) for index in range(31)])

    assert at_or5["references"]["OR5"]["status"] == "complete"
    assert at_or5["references"]["OR5"]["completionTimestamp"] == "2026-07-23T13:35:00+00:00"
    assert at_or5["references"]["OR5"]["barsExpected"] == 5
    assert at_or5["references"]["OR5"]["barsObserved"] == 5
    assert at_or5["references"]["OR15"]["status"] == "building"

    assert at_or15["references"]["OR15"]["status"] == "complete"
    assert at_or15["references"]["OR15"]["completionTimestamp"] == "2026-07-23T13:45:00+00:00"
    assert at_or30["references"]["OR30"]["status"] == "complete"
    assert at_or30["references"]["OR30"]["completionTimestamp"] == "2026-07-23T14:00:00+00:00"


def test_session_step5_premarket_bars_are_excluded_from_reference_windows() -> None:
    premarket = [_bar_at(datetime(2026, 7, 23, 12, 0, tzinfo=UTC) + timedelta(minutes=index), high=110, low=90) for index in range(10)]
    regular = [_bar(index) for index in range(6)]

    result = analyze_opening_ranges([*premarket, *regular])

    assert result["references"]["OR5"]["status"] == "complete"
    assert result["references"]["OR5"]["high"] < 110
    assert result["references"]["OR5"]["low"] > 90
    assert result["references"]["OR5"]["barsObserved"] == 5


def test_session_step5_current_bar_is_excluded_from_completed_reference() -> None:
    candles = [_bar(index, high=100.20, low=99.90, close=100.0) for index in range(5)]
    candles.append(_bar(5, high=105.0, low=100.10, close=104.0))

    result = analyze_opening_ranges(candles)

    assert result["references"]["OR5"]["high"] == 100.20
    assert result["breakouts"]["OR5"]["direction"] == "up"
    assert result["breakouts"]["OR5"]["closeBeyondRange"] is True


def test_session_step5_before_or5_complete_reports_opening_drive_not_breakout() -> None:
    result = analyze_opening_ranges([_bar(index, close=100 + index * 0.10) for index in range(4)])

    assert result["references"]["OR5"]["status"] == "building"
    assert result["openingDrive"]["status"] == "building"
    assert result["openingDrive"]["direction"] == "up"
    assert result["breakouts"]["OR5"]["status"] == "building"


def test_session_step5_breakout_after_completion_exposes_distance() -> None:
    result = analyze_opening_ranges([*[_bar(index, high=100.20, low=99.90, close=100.0) for index in range(5)], _bar(5, high=100.80, low=100.21, close=100.60)])
    breakout = result["breakouts"]["OR5"]

    assert breakout["direction"] == "up"
    assert breakout["closeBeyondRange"] is True
    assert breakout["distanceFromRangeAmount"] > 0
    assert breakout["distanceFromRangeBps"] > 0
    assert breakout["accepted"] is False


def test_session_step5_wick_only_false_break_is_not_accepted() -> None:
    result = analyze_opening_ranges([*[_bar(index, high=100.20, low=99.90, close=100.0) for index in range(5)], _bar(5, high=100.80, low=100.00, close=100.10)])
    breakout = result["breakouts"]["OR5"]

    assert breakout["direction"] == "up"
    assert breakout["wickBeyondRange"] is True
    assert breakout["closeBeyondRange"] is False
    assert breakout["accepted"] is False
    assert breakout["failedBreakout"] is False


def test_session_step5_close_and_accept_break_uses_configurable_bars() -> None:
    config = SessionConfig(opening_range_acceptance_bars=2)
    candles = [
        *[_bar(index, high=100.20, low=99.90, close=100.0) for index in range(5)],
        _bar(5, high=100.80, low=100.21, close=100.50),
        _bar(6, high=100.90, low=100.30, close=100.70),
    ]

    breakout = analyze_opening_ranges(candles, config=config)["breakouts"]["OR5"]

    assert breakout["closeBeyondRange"] is True
    assert breakout["accepted"] is True
    assert breakout["acceptanceBarsObserved"] == 2


def test_session_step5_failed_breakout_requires_rejection_back_inside() -> None:
    candles = [
        *[_bar(index, high=100.20, low=99.90, close=100.0) for index in range(5)],
        _bar(5, high=100.80, low=100.21, close=100.50),
        _bar(6, high=100.60, low=99.95, close=100.10),
    ]

    breakout = analyze_opening_ranges(candles)["breakouts"]["OR5"]

    assert breakout["rejectionBackInside"] is True
    assert breakout["failedBreakout"] is True
    assert "session.opening_range.or5.failed_breakout" in breakout["reasonCodes"]


def test_session_step5_missing_reference_bar_invalidates_completed_window() -> None:
    result = analyze_opening_ranges([_bar(0), _bar(1), _bar(3), _bar(4), _bar(5)])

    assert result["references"]["OR5"]["status"] == "invalid"
    assert result["references"]["OR5"]["missingBarCount"] == 1
    assert result["references"]["OR5"]["missingBars"] == ("2026-07-23T13:32:00+00:00",)
    assert result["breakouts"]["OR5"]["status"] == "invalid"


def test_session_step5_early_close_keeps_opening_range_windows_intact() -> None:
    result = analyze_opening_ranges([_bar(index, start=EARLY_CLOSE_START) for index in range(31)])

    assert result["references"]["OR5"]["status"] == "complete"
    assert result["references"]["OR15"]["status"] == "complete"
    assert result["references"]["OR30"]["status"] == "complete"
    assert result["references"]["OR30"]["completionTimestamp"] == "2026-11-27T15:00:00+00:00"


def test_session_step5_classifier_exposes_independent_opening_range_identities() -> None:
    classification = classify_session("SPY", [_bar(index) for index in range(31)])
    opening_ranges = classification.evidence["openingRanges"]

    assert set(opening_ranges["references"]) == {"OR5", "OR15", "OR30"}
    assert opening_ranges["references"]["OR5"]["completionTimestamp"] != opening_ranges["references"]["OR15"]["completionTimestamp"]
    assert opening_ranges["references"]["OR15"]["completionTimestamp"] != opening_ranges["references"]["OR30"]["completionTimestamp"]
    assert classification.evidence["openingRange5m"] != "NA"
    assert classification.evidence["openingRange15m"] != "NA"
    assert classification.evidence["openingRange30m"] != "NA"


def _bar(index: int, *, start: datetime = SESSION_START, high: float | None = None, low: float | None = None, close: float | None = None) -> dict[str, object]:
    timestamp = start + timedelta(minutes=index)
    base = 100 + index * 0.01
    close_value = base if close is None else close
    return _bar_at(
        timestamp,
        high=base + 0.10 if high is None else high,
        low=base - 0.10 if low is None else low,
        close=close_value,
    )


def _bar_at(timestamp: datetime, *, high: float, low: float, close: float | None = None) -> dict[str, object]:
    close_value = (high + low) / 2 if close is None else close
    return {
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "open": close_value,
        "high": high,
        "low": low,
        "close": close_value,
        "volume": 100_000,
    }
