from __future__ import annotations

from datetime import timedelta

from backend.app.algorithms.session import analyze_vwap
from session_test_fixtures import golden_candles


def test_session_vwap_is_point_in_time_and_future_append_invariant() -> None:
    bars = golden_candles("strong_morning_trend")
    future = _future_bars_after(bars, golden_candles("afternoon_expansion"))
    first = analyze_vwap(bars)
    extended = analyze_vwap([*bars, *future])

    assert first["history"][5] == extended["history"][5]
    assert first["metadata"]["priceConvention"] == "typical_price_x_volume"


def test_session_vwap_crossing_count_uses_deadband() -> None:
    quiet = analyze_vwap(golden_candles("midday_compression"))
    choppy = analyze_vwap(golden_candles("choppy_vwap_rotation"))

    assert quiet["current"]["crossingFrequencyPerHour"] < choppy["current"]["crossingFrequencyPerHour"]
    assert choppy["current"]["crossingCount"] >= 3


def test_session_vwap_zero_volume_returns_not_ready_not_zero_vwap() -> None:
    bars = [{**bar, "volume": 0} for bar in golden_candles("balanced_range")]

    result = analyze_vwap(bars)

    assert result["status"] == "not_ready"
    assert result["current"] is None


def _future_bars_after(anchor: list[dict[str, object]], future: list[dict[str, object]]) -> list[dict[str, object]]:
    from backend.app.algorithms.session.calendar import parse_session_timestamp_utc

    last = parse_session_timestamp_utc(str(anchor[-1]["timestamp"]))
    return [
        {
            **bar,
            "timestamp": (last + timedelta(minutes=index + 1)).isoformat().replace("+00:00", "Z"),
        }
        for index, bar in enumerate(future)
    ]
