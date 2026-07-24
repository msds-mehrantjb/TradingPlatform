from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.app.market_context import SESSION_SIGNAL_NAMES, _failed_breakouts, _liquidity_stress, _volume_pace, compute_market_context


SESSION_START = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("bar_count", "session_label", "event_label"),
    (
        (0, "Session Building", "No Event Confirmed"),
        (5, "Session Building", "Gap-Down Open"),
        (9, "Session Building", "Gap-Down Open"),
        (10, "Balanced Session", "Gap-Down Open"),
        (15, "Balanced Session", "Gap-Down Open"),
        (30, "Balanced Session", "Opening Range Breakout Up"),
        (390, "Trend Day Up", "Opening Range Breakout Up"),
    ),
)
def test_session_step1_baseline_labels_and_signal_fields(bar_count: int, session_label: str, event_label: str) -> None:
    context = compute_market_context("SPY", _daily_bars(), [_intraday_candle(index) for index in range(bar_count)])

    assert context["session"]["label"] == session_label
    assert context["event"]["label"] == event_label
    assert [signal["name"] for signal in context["session"]["signals"]] == SESSION_SIGNAL_NAMES
    assert len(context["event"]["signals"]) == 13


def test_session_step1_zero_bars_are_not_ready() -> None:
    context = compute_market_context("SPY", _daily_bars(), [])

    assert context["session"]["label"] == "Session Building"
    assert context["event"]["label"] == "No Event Confirmed"
    assert all(signal["status"] == "na" for signal in context["session"]["signals"])
    assert _signal_value(context["session"], "Liquidity stress") == "NA"


def test_session_step1_ten_bars_do_not_raise_when_vwap_slope_is_not_ready() -> None:
    context = compute_market_context("SPY", _daily_bars(), [_intraday_candle(index) for index in range(10)])

    assert context["session"]["label"] == "Balanced Session"
    assert _signal_value(context["session"], "VWAP slope") == "NA"
    assert _signal_value(context["session"], "Liquidity stress") == "unknown"
    assert _signal_status(context["session"], "Liquidity stress") == "na"


def test_session_step1_zero_volume_does_not_fabricate_vwap_or_liquidity() -> None:
    context = compute_market_context("SPY", _daily_bars(), [_intraday_candle(index, volume=0) for index in range(30)])

    assert _signal_value(context["session"], "VWAP") == "NA"
    assert _signal_value(context["session"], "Volume pace vs session avg") == "NA"
    assert _signal_value(context["session"], "Liquidity stress") == "unknown"
    assert _signal_status(context["session"], "Liquidity stress") == "na"


def test_session_step1_missing_optional_quote_fields_produce_unknown_liquidity() -> None:
    candles = [_intraday_candle(index) for index in range(30)]

    context = compute_market_context("SPY", _daily_bars(), candles)

    assert "bid" not in candles[-1]
    assert "ask" not in candles[-1]
    assert _signal_value(context["session"], "Liquidity stress") == "unknown"
    assert _signal_value(context["event"], "Liquidity stress") == "unknown"


def test_session_step1_quote_fields_can_mark_liquidity_inactive_or_active() -> None:
    inactive = compute_market_context(
        "SPY",
        _daily_bars(),
        [_intraday_candle(index, bid=100 + index * 0.02, ask=100 + index * 0.02 + 0.01, quoteAgeMs=250) for index in range(30)],
    )
    active = compute_market_context(
        "SPY",
        _daily_bars(),
        [_intraday_candle(index, bid=100 + index * 0.02, ask=100 + index * 0.02 + 0.50, quoteAgeMs=250) for index in range(30)],
    )

    assert _signal_value(inactive["session"], "Liquidity stress") == "Inactive"
    assert _signal_value(active["session"], "Liquidity stress") == "Active"


def test_session_step1_duplicate_timestamps_are_characterized_without_crashing() -> None:
    candles = [_intraday_candle(index) for index in range(10)]
    candles.append(_intraday_candle(9))

    context = compute_market_context("SPY", _daily_bars(), candles)

    assert context["session"]["label"] == "Balanced Session"
    assert context["session"]["candleWindow"]["count"] == 11
    assert context["session"]["candleWindow"]["start"] == _timestamp(0)
    assert context["session"]["candleWindow"]["end"] == _timestamp(9)


def test_session_step1_out_of_order_input_is_sorted_before_baseline_calculation() -> None:
    context = compute_market_context("SPY", _daily_bars(), [_intraday_candle(index) for index in reversed(range(30))])

    assert context["session"]["candleWindow"]["count"] == 30
    assert context["session"]["candleWindow"]["start"] == _timestamp(0)
    assert context["session"]["candleWindow"]["end"] == _timestamp(29)


def test_session_step1_failed_breakouts_use_only_current_window() -> None:
    first_15 = compute_market_context("SPY", _daily_bars(), [_intraday_candle(index) for index in range(15)])
    full_30 = compute_market_context("SPY", _daily_bars(), [_intraday_candle(index) for index in range(30)])

    assert _signal_value(first_15["session"], "Failed breakouts") == "0"
    assert _signal_value(full_30["session"], "Failed breakouts") == "0"


def test_session_step1_temporary_helpers_fail_closed_or_point_in_time() -> None:
    assert _volume_pace([0.0] * 30) is None
    assert _volume_pace([100.0] * 19) is None
    assert _liquidity_stress([_intraday_candle(index) for index in range(30)]) == "unknown"

    current_window = [_intraday_candle(index) for index in range(15)]
    future_failure = {
        **_intraday_candle(15),
        "high": 101.5,
        "close": 100.1,
    }
    opening_high = max(float(candle["high"]) for candle in current_window)
    opening_low = min(float(candle["low"]) for candle in current_window)

    assert _failed_breakouts(current_window, opening_high, opening_low) == "0"
    assert _failed_breakouts([*current_window, future_failure], opening_high, opening_low) == "1"


def _daily_bars(count: int = 80) -> list[dict[str, object]]:
    return [
        {
            **_intraday_candle(index, volume=1_000_000),
            "timestamp": (SESSION_START - timedelta(days=count - index)).isoformat().replace("+00:00", "Z"),
        }
        for index in range(count)
    ]


def _intraday_candle(index: int, *, volume: int = 100_000, **extra: object) -> dict[str, object]:
    close = 100 + index * 0.02
    candle = {
        "timestamp": _timestamp(index),
        "open": close - 0.03,
        "high": close + 0.08,
        "low": close - 0.07,
        "close": close,
        "volume": volume,
    }
    candle.update(extra)
    return candle


def _timestamp(index: int) -> str:
    return (SESSION_START + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")


def _signal_value(layer: dict, name: str) -> str:
    return next(signal["value"] for signal in layer["signals"] if signal["name"] == name)


def _signal_status(layer: dict, name: str) -> str:
    return next(signal["status"] for signal in layer["signals"] if signal["name"] == name)
