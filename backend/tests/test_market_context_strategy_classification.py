from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.app.market_context import compute_market_context


def test_market_context_strategy_fit_uses_correct_module_classifications() -> None:
    start = datetime(2026, 1, 5, 14, 30, tzinfo=UTC)
    daily = [_candle(start - timedelta(days=80 - index), 400 + index * 0.2) for index in range(80)]
    intraday = [_candle(start + timedelta(minutes=index), 420 + index * 0.03) for index in range(80)]

    context = compute_market_context("SPY", daily, intraday)
    strategies = {row["name"]: row for row in context["strategies"]}

    assert "Economic Event Reaction Strategy" not in strategies
    assert strategies["Economic Event Context"]["role"] == "context"
    assert "Bollinger Band Reversion" not in strategies
    assert "ATR Overextension Reversion" not in strategies
    assert strategies["Bollinger/ATR Reversion"]["role"] == "directional"
    assert "ADX Trend Strength Regime" not in strategies
    assert "ATR Volatility Regime" not in strategies
    assert strategies["ADX/ATR Regime Classifier"]["role"] == "regime"
    assert strategies["Cash / Avoid Trading Filter"]["role"] == "safety"
    assert strategies["Relative Strength vs QQQ/IWM"]["role"] == "context"
    assert strategies["Market Breadth Momentum"]["role"] == "context"
    assert "Ensemble Strategy Voting" not in strategies


def _candle(timestamp: datetime, close: float) -> dict[str, object]:
    return {
        "timestamp": timestamp.isoformat(),
        "open": close - 0.1,
        "high": close + 0.2,
        "low": close - 0.2,
        "close": close,
        "volume": 1_000_000,
        "trade_count": 1_000,
        "vwap": close,
    }
