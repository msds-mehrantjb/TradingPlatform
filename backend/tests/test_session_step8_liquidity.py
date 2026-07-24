from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.app.algorithms.session import DataQualityState, LiquidityState, SessionConfig, analyze_session_liquidity, classify_session


DECISION_TIME = datetime(2026, 7, 23, 13, 40, tzinfo=UTC)


def test_session_step8_normal_quote_is_healthy() -> None:
    result = analyze_session_liquidity(_quote(), decision_time=DECISION_TIME)

    assert result["liquidityState"] == LiquidityState.HEALTHY.value
    assert result["dataQualityState"] == DataQualityState.READY.value
    assert result["blockNewEntries"] is False
    assert result["spreadDollars"] == pytest.approx(0.01)
    assert result["spreadBasisPoints"] > 0
    assert result["midpoint"] == 100.005
    assert result["topOfBookImbalance"] == 0
    assert result["estimatedFillProbability"] is not None


def test_session_step8_wide_spread_is_stressed_and_blocks() -> None:
    config = SessionConfig(maximum_constrained_spread_bps=20.0)
    result = analyze_session_liquidity(_quote(bestAsk=101.00), decision_time=DECISION_TIME, config=config)

    assert result["liquidityState"] == LiquidityState.STRESSED.value
    assert result["blockNewEntries"] is True
    assert "session.liquidity.spread_stressed" in result["reasonCodes"]


def test_session_step8_stale_quote_blocks() -> None:
    result = analyze_session_liquidity(_quote(quoteTimestamp=(DECISION_TIME - timedelta(seconds=6)).isoformat()), decision_time=DECISION_TIME)

    assert result["liquidityState"] == LiquidityState.STALE.value
    assert result["dataQualityState"] == DataQualityState.STALE.value
    assert result["blockNewEntries"] is True
    assert "session.liquidity.quote_stale" in result["reasonCodes"]


def test_session_step8_missing_quote_is_unknown_not_healthy() -> None:
    result = analyze_session_liquidity(None, decision_time=DECISION_TIME)

    assert result["liquidityState"] == LiquidityState.UNKNOWN.value
    assert result["blockNewEntries"] is True
    assert result["spreadBasisPoints"] is None
    assert "session.liquidity.quote_missing" in result["reasonCodes"]


def test_session_step8_locked_or_crossed_market_is_invalid_and_blocks() -> None:
    locked = analyze_session_liquidity(_quote(bestAsk=100.00), decision_time=DECISION_TIME)
    crossed = analyze_session_liquidity(_quote(bestAsk=99.99), decision_time=DECISION_TIME)

    assert locked["dataQualityState"] == DataQualityState.INVALID.value
    assert locked["blockNewEntries"] is True
    assert crossed["dataQualityState"] == DataQualityState.INVALID.value
    assert crossed["blockNewEntries"] is True
    assert "session.liquidity.invalid_or_crossed_market" in crossed["reasonCodes"]


def test_session_step8_thin_displayed_size_is_constrained() -> None:
    result = analyze_session_liquidity(_quote(bidSize=50, askSize=40), decision_time=DECISION_TIME)

    assert result["liquidityState"] == LiquidityState.CONSTRAINED.value
    assert result["blockNewEntries"] is False
    assert "session.liquidity.thin_top_of_book" in result["reasonCodes"]


def test_session_step8_excessive_order_participation_blocks() -> None:
    result = analyze_session_liquidity(_quote(volume=1_000, intendedOrderQuantity=200), decision_time=DECISION_TIME)

    assert result["estimatedParticipationRate"] == 0.2
    assert result["liquidityState"] == LiquidityState.STRESSED.value
    assert result["blockNewEntries"] is True
    assert "session.liquidity.participation_too_high" in result["reasonCodes"]


def test_session_step8_invalid_units_are_invalid_data() -> None:
    result = analyze_session_liquidity(_quote(bidSize=-1), decision_time=DECISION_TIME)

    assert result["dataQualityState"] == DataQualityState.INVALID.value
    assert result["blockNewEntries"] is True
    assert "session.liquidity.invalid_units" in result["reasonCodes"]


def test_session_step8_slippage_and_partial_fill_evidence_are_reported() -> None:
    result = analyze_session_liquidity(_quote(recentEstimatedSlippageBps=2, recentRealizedSlippageBps=8, partialFillRate=0.25), decision_time=DECISION_TIME)

    assert result["realizedVsEstimatedSlippageBps"] == 6
    assert result["partialFillRate"] == 0.25
    assert result["liquidityState"] == LiquidityState.CONSTRAINED.value
    assert "session.liquidity.slippage_error_constrained" in result["reasonCodes"]


def test_session_step8_classifier_uses_authoritative_liquidity_evidence() -> None:
    candles = [_candle(index) for index in range(10)]
    candles[-1].update(_quote(bestAsk=101.00))

    classification = classify_session("SPY", candles, config=SessionConfig(maximum_constrained_spread_bps=20.0))

    assert classification.liquidity_state == LiquidityState.STRESSED
    assert classification.block_new_entries is True
    assert classification.evidence["liquidityEvidence"]["spreadBasisPoints"] > 20
    assert classification.evidence["liquidityStress"] == "Active"


def test_session_step8_unknown_liquidity_cannot_be_healthy_in_classifier() -> None:
    classification = classify_session("SPY", [_candle(index) for index in range(10)])

    assert classification.liquidity_state == LiquidityState.UNKNOWN
    assert classification.block_new_entries is True
    assert classification.evidence["liquidityEvidence"]["liquidityState"] == "unknown"
    assert classification.evidence["liquidityStress"] == "unknown"


def _quote(**overrides: object) -> dict[str, object]:
    quote = {
        "bestBid": 100.00,
        "bestAsk": 100.01,
        "bidSize": 1_000,
        "askSize": 1_000,
        "quoteTimestamp": (DECISION_TIME - timedelta(milliseconds=250)).isoformat(),
        "latestTradeTimestamp": (DECISION_TIME - timedelta(milliseconds=100)).isoformat(),
        "volume": 10_000,
        "barDollarVolume": 1_000_000,
        "tradeCount": 120,
        "intendedOrderQuantity": 100,
    }
    quote.update(overrides)
    return quote


def _candle(index: int) -> dict[str, object]:
    timestamp = datetime(2026, 7, 23, 13, 30, tzinfo=UTC) + timedelta(minutes=index)
    price = 100 + index * 0.01
    return {
        "timestamp": timestamp.isoformat().replace("+00:00", "Z"),
        "open": price,
        "high": price + 0.1,
        "low": price - 0.1,
        "close": price,
        "volume": 10_000,
    }
