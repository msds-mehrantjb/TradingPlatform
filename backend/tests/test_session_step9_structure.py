from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.app.algorithms.session import SessionBehavior, analyze_opening_ranges, analyze_session_structure, analyze_vwap, classify_session
from backend.app.market_context import compute_market_context


SESSION_START = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)


def test_session_step9_trend_up_has_swing_and_path_evidence() -> None:
    result = analyze_session_structure(_fixture("trend_up"), _context(_fixture("trend_up")))

    assert result["behavior"] == "trend_up"
    assert result["swing"]["higherHighs"] is True
    assert result["swing"]["higherLows"] is True
    assert "session.structure.behavior.trend_up" in result["reasonCodes"]


def test_session_step9_trend_down_has_swing_and_path_evidence() -> None:
    result = analyze_session_structure(_fixture("trend_down"), _context(_fixture("trend_down")))

    assert result["behavior"] == "trend_down"
    assert result["swing"]["lowerHighs"] is True
    assert result["swing"]["lowerLows"] is True
    assert "session.structure.behavior.trend_down" in result["reasonCodes"]


def test_session_step9_balanced_range_stays_balanced_without_single_candle_claim() -> None:
    result = analyze_session_structure(_fixture("balanced_range"), _context(_fixture("balanced_range")))

    assert result["behavior"] == "balanced_range"
    assert "session.structure.behavior.balanced_range" in result["reasonCodes"]
    assert result["breakout"]["validBreakout"] is False


def test_session_step9_mean_reversion_uses_repeated_failed_acceptance() -> None:
    candles = _fixture("failed_breakout")
    context = _context(candles)
    context["openingRanges"]["breakouts"]["OR15"]["failedBreakout"] = True

    result = analyze_session_structure(candles, context)

    assert result["behavior"] == "mean_reverting"
    assert result["auction"]["repeatedFailedAcceptance"] is True


def test_session_step9_choppy_whipsaw_uses_overlap_and_vwap_frequency() -> None:
    candles = _fixture("choppy")
    context = _context(candles)

    result = analyze_session_structure(candles, context)

    assert result["behavior"] == "choppy"
    assert result["trendChop"]["overlapRatio"] >= 0.6
    assert result["trendChop"]["vwapCrossingFrequencyPerHour"] >= 5


def test_session_step9_valid_breakout_requires_acceptance_and_volume() -> None:
    candles = _fixture("valid_breakout")
    result = analyze_session_structure(candles, _context(candles))

    assert result["behavior"] == "valid_breakout_up"
    assert result["breakout"]["validBreakout"] is True
    assert result["breakout"]["volumeConfirmation"] is True
    assert result["breakout"]["barsSinceBreakout"] is not None


def test_session_step9_failed_breakout_and_liquidity_sweep_are_explicit() -> None:
    candles = _fixture("failed_breakout")
    result = analyze_session_structure(candles, _context(candles))

    assert result["behavior"] == "failed_breakout_up"
    assert result["breakout"]["failedBreakout"] is True
    assert result["auction"]["openingRangeRejection"] is True


def test_session_step9_reversal_uses_change_of_character() -> None:
    result = analyze_session_structure(_fixture("reversal"), _context(_fixture("reversal")))

    assert result["behavior"] == "reversal_up"
    assert result["swing"]["changeOfCharacter"] == "up"


def test_session_step9_shallow_pullback_is_valid() -> None:
    result = analyze_session_structure(_fixture("shallow_pullback"), _context(_fixture("shallow_pullback")))

    assert result["pullback"]["shallowValid"] is True
    assert result["pullback"]["originProtected"] is True
    assert result["pullback"]["volumeContraction"] is True
    assert result["pullback"]["depthFraction"] <= 0.38


def test_session_step9_deep_invalid_pullback_is_flagged() -> None:
    result = analyze_session_structure(_fixture("deep_pullback"), _context(_fixture("deep_pullback")))

    assert result["pullback"]["deepInvalid"] is True
    assert "session.structure.pullback.deep_invalid" in result["pullback"]["reasonCodes"]


def test_session_step9_classifier_uses_structure_behavior_evidence() -> None:
    candles = [_with_quote(candle) for candle in _fixture("valid_breakout")]

    classification = classify_session("SPY", candles)

    assert classification.behavior == SessionBehavior.BREAKOUT_UP
    assert classification.evidence["structureEvidence"]["breakout"]["validBreakout"] is True
    assert "session.structure.behavior.valid_breakout" in classification.reason_codes


def test_session_step9_legacy_signals_have_pullback_and_same_time_volume_values() -> None:
    context = compute_market_context("SPY", [], [_with_quote(candle) for candle in _fixture("shallow_pullback")])

    signals = {signal["name"]: signal["value"] for signal in context["session"]["signals"]}

    assert signals["Pullback depth"] != "NA"
    assert signals["Same-time volume avg"] == "not-ready"


def _context(candles: list[dict[str, object]]) -> dict[str, object]:
    opening = analyze_opening_ranges(candles)
    vwap = analyze_vwap(candles)
    return {
        "openingRanges": opening,
        "vwapFeatures": vwap,
        "participationEvidence": {"oneMinuteRelativeVolume": 1.5},
        "vwapCrossingFrequencyPerHour": (vwap.get("current") or {}).get("crossingFrequencyPerHour") or 0,
    }


def _fixture(name: str) -> list[dict[str, object]]:
    if name == "trend_up":
        prices = [100, 101, 100.6, 102, 101.3, 103, 102.2, 104, 103.1, 105, 104.2, 106, 105.2, 107, 108]
        return [_bar(index, price, width=0.40, volume=1000) for index, price in enumerate(prices)]
    if name == "trend_down":
        prices = [108, 107, 107.4, 106, 106.7, 105, 105.8, 104, 104.8, 103, 103.9, 102, 102.7, 101, 100]
        return [_bar(index, price, width=0.40, volume=1000) for index, price in enumerate(prices)]
    if name == "balanced_range":
        prices = [100, 100.4, 99.9, 100.2, 99.8, 100.3, 99.9, 100.1, 99.7, 100.2, 99.9, 100.1]
        return [_bar(index, price, width=0.50, volume=1000) for index, price in enumerate(prices)]
    if name == "choppy":
        prices = [100, 101, 99, 101.1, 98.9, 101, 99, 101.2, 98.8, 101, 99, 100.8]
        return [_bar(index, price, width=2.4, volume=1000) for index, price in enumerate(prices)]
    if name == "valid_breakout":
        prices = [100, 100.1, 99.9, 100.0, 100.05, 100.7, 101.0, 101.2, 101.35, 101.55, 101.8, 102.0]
        return [_bar(index, price, width=0.25, volume=3000 if index >= 5 else 1000) for index, price in enumerate(prices)]
    if name == "failed_breakout":
        prices = [100, 100.1, 99.9, 100.0, 100.05, 100.8, 100.0, 99.8, 99.7, 99.6, 99.7, 99.5]
        bars = [_bar(index, price, width=0.25, volume=1500) for index, price in enumerate(prices)]
        bars[5]["high"] = 101.0
        return bars
    if name == "reversal":
        prices = [105, 104, 104.5, 103, 103.6, 102, 102.8, 101.8, 102.4, 103.2, 104.0, 105.2, 106.0]
        return [_bar(index, price, width=0.45, volume=1000) for index, price in enumerate(prices)]
    if name == "shallow_pullback":
        prices = [100, 101, 102, 103, 104, 105, 104.6, 104.3, 104.7, 105.3, 105.8, 106.2]
        return [_bar(index, price, width=0.35, volume=2000 if index <= 5 else 900) for index, price in enumerate(prices)]
    if name == "deep_pullback":
        prices = [100, 101, 102, 103, 104, 105, 103.5, 102.0, 101.0, 100.5, 100.2, 100.1]
        return [_bar(index, price, width=0.35, volume=1500) for index, price in enumerate(prices)]
    raise AssertionError(name)


def _bar(index: int, close: float, *, width: float, volume: float) -> dict[str, object]:
    timestamp = SESSION_START + timedelta(minutes=index)
    return {
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "open": close,
        "high": close + width / 2,
        "low": close - width / 2,
        "close": close,
        "volume": volume,
    }


def _with_quote(candle: dict[str, object]) -> dict[str, object]:
    return {
        **candle,
        "bestBid": float(candle["close"]) - 0.01,
        "bestAsk": float(candle["close"]) + 0.01,
        "bidSize": 1000,
        "askSize": 1000,
        "quoteTimestamp": candle["timestamp"],
        "tradeCount": 100,
    }
