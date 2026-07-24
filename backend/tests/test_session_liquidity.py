from __future__ import annotations

from datetime import timedelta

from backend.app.algorithms.session import DataQualityState, LiquidityState, analyze_session_liquidity
from session_test_fixtures import NOW, with_quote, golden_candles


def test_session_liquidity_missing_quote_is_unknown_and_blocks() -> None:
    result = analyze_session_liquidity(None, decision_time=NOW)

    assert result["liquidityState"] == LiquidityState.UNKNOWN.value
    assert result["spreadBasisPoints"] is None
    assert result["blockNewEntries"] is True


def test_session_liquidity_validates_basis_point_and_percentage_units() -> None:
    quote = with_quote(golden_candles("balanced_range")[0])
    result = analyze_session_liquidity(quote, decision_time=NOW)

    assert 0 < result["spreadBasisPoints"] < 5
    assert 0 <= result["estimatedParticipationRate"] <= 1
    assert 0 <= result["estimatedFillProbability"] <= 1


def test_session_liquidity_stale_and_crossed_markets_fail_closed() -> None:
    stale_quote = {**with_quote(golden_candles("balanced_range")[0]), "quoteTimestamp": (NOW - timedelta(seconds=10)).isoformat()}
    crossed_quote = {**with_quote(golden_candles("balanced_range")[0]), "bestAsk": 99.0}

    stale = analyze_session_liquidity(stale_quote, decision_time=NOW)
    crossed = analyze_session_liquidity(crossed_quote, decision_time=NOW)

    assert stale["liquidityState"] == LiquidityState.STALE.value
    assert crossed["dataQualityState"] == DataQualityState.INVALID.value
    assert stale["blockNewEntries"] is True
    assert crossed["blockNewEntries"] is True
