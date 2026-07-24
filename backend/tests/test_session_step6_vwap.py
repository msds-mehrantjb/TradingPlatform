from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.app.algorithms.session import SessionConfig, analyze_vwap, classify_session, vwap_at


SESSION_START = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)


def test_session_step6_cumulative_vwap_uses_typical_price_volume() -> None:
    result = analyze_vwap([_bar(0, price=100, volume=10), _bar(1, price=102, volume=30)])

    assert result["status"] == "ready"
    assert result["metadata"]["priceConvention"] == "typical_price_x_volume"
    assert result["current"]["vwap"] == 101.5
    assert result["current"]["cumulativeVolume"] == 40


def test_session_step6_crossing_count_uses_close_minus_point_in_time_vwap() -> None:
    result = analyze_vwap([_bar(0, price=100), _bar(1, price=102), _bar(2, price=98), _bar(3, price=103)])

    assert result["current"]["crossingCount"] == 2
    assert result["current"]["crossingFrequencyPerHour"] == 30.0
    assert result["current"]["reclaimAbove"] is True


def test_session_step6_deadband_prevents_micro_crossings() -> None:
    config = SessionConfig(vwap_deadband_bps=5.0)

    result = analyze_vwap([_bar(0, price=100.00), _bar(1, price=100.03), _bar(2, price=99.99)], config=config)

    assert result["current"]["position"] == "neutral"
    assert result["current"]["crossingCount"] == 0
    assert result["current"]["timeAboveBars"] == 0
    assert result["current"]["timeBelowBars"] == 0


def test_session_step6_slope_uses_configurable_point_in_time_window() -> None:
    config = SessionConfig(vwap_slope_windows=(2,))

    result = analyze_vwap([_bar(0, price=100), _bar(1, price=101), _bar(2, price=102)], config=config)

    assert result["slopes"]["2"] == 0.01


def test_session_step6_acceptance_above_and_below_vwap() -> None:
    config = SessionConfig(vwap_acceptance_bars=2)

    above = analyze_vwap([_bar(0, price=100), _bar(1, price=102), _bar(2, price=103)], config=config)
    below = analyze_vwap([_bar(0, price=103), _bar(1, price=101), _bar(2, price=100)], config=config)

    assert above["current"]["acceptanceAbove"] is True
    assert above["current"]["acceptanceBelow"] is False
    assert below["current"]["acceptanceBelow"] is True
    assert below["current"]["acceptanceAbove"] is False


def test_session_step6_excursion_and_distance_units_are_exposed() -> None:
    result = analyze_vwap([_bar(0, price=100, high=100.2, low=99.8), _bar(1, price=102, high=102.2, low=101.8)])

    assert result["current"]["distanceDollars"] > 0
    assert result["current"]["distanceBps"] > 0
    assert result["current"]["distanceAtr"] > 0
    assert result["current"]["averageExcursion"] > 0


def test_session_step6_historical_snapshot_is_invariant_after_future_bars() -> None:
    first_six = [_bar(index, price=100 + index) for index in range(6)]
    full_stream = [*first_six, *[_bar(index, price=120 - index) for index in range(6, 12)]]
    cutoff = _timestamp(5)

    original = analyze_vwap(first_six)
    replayed = vwap_at(full_stream, cutoff)

    assert replayed["current"] == original["current"]
    assert replayed["history"] == original["history"]
    assert replayed["slopes"] == original["slopes"]


def test_session_step6_zero_volume_bar_does_not_move_existing_vwap() -> None:
    result = analyze_vwap([_bar(0, price=100, volume=10), _bar(1, price=110, volume=0)])

    assert result["status"] == "ready"
    assert result["current"]["vwap"] == 100
    assert result["current"]["cumulativeVolume"] == 10


def test_session_step6_all_zero_volume_is_not_ready() -> None:
    result = analyze_vwap([_bar(0, price=100, volume=0), _bar(1, price=101, volume=0)])

    assert result["status"] == "not_ready"
    assert result["current"] is None
    assert "session.vwap.no_cumulative_volume" in result["reasonCodes"]


def test_session_step6_missing_volume_is_invalid() -> None:
    result = analyze_vwap([_bar(0, price=100), {**_bar(1, price=101), "volume": None}])

    assert result["status"] == "invalid"
    assert result["current"] is None
    assert "session.vwap.volume_missing" in result["reasonCodes"]


def test_session_step6_classifier_exposes_vwap_feature_contract() -> None:
    classification = classify_session("SPY", [_bar(index, price=100 + index * 0.1) for index in range(20)])
    features = classification.evidence["vwapFeatures"]

    assert features["status"] == "ready"
    assert features["metadata"]["priceConvention"] == "typical_price_x_volume"
    assert classification.evidence["vwap"] == features["current"]["vwap"]
    assert classification.evidence["vwapCrosses"] == features["current"]["crossingCount"]
    assert "vwapDistanceBps" in classification.evidence


def _bar(
    index: int,
    *,
    price: float,
    volume: float = 10.0,
    high: float | None = None,
    low: float | None = None,
) -> dict[str, object]:
    return {
        "timestamp": _timestamp(index),
        "open": price,
        "high": price if high is None else high,
        "low": price if low is None else low,
        "close": price,
        "volume": volume,
    }


def _timestamp(index: int) -> str:
    return (SESSION_START + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
