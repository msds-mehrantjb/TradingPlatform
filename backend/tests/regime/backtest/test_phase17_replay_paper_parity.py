from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from backend.app.algorithms.regime.backtest.engine import REGIME_BACKTEST_PARITY_COMPONENTS, run_regime_backtest


def test_phase17_backtest_reports_paper_parity_components_and_allowed_adapters() -> None:
    result = run_regime_backtest({"symbol": "SPY", "candles": _candles(36)})
    components = {item["component"]: item for item in result["parity"]["components"]}

    assert result["parity"]["apiOrFrontendTradingAuthority"] is False
    assert result["parity"]["allowedAdapterDifferences"] == ("data", "clock", "broker_fill", "persistence")
    assert len(result["parity"]["components"]) == len(REGIME_BACKTEST_PARITY_COMPONENTS)
    assert components["market_snapshot_builder"]["paperImplementation"] == components["market_snapshot_builder"]["backtestImplementation"]
    assert components["cost_model_interface"]["paperImplementation"] == components["cost_model_interface"]["backtestImplementation"]
    assert components["exit_logic_trade_management"]["paperImplementation"] == components["exit_logic_trade_management"]["backtestImplementation"]
    assert components["broker_fill_execution_adapter"]["adapterBoundary"] == "allowed_difference"


def test_phase17_same_versions_and_data_produce_identical_replay_fingerprints() -> None:
    payload = {
        "symbol": "SPY",
        "candles": _candles(48),
        "marketModel": {"latencyBars": 1, "ttlBars": 2, "spreadBps": 2.0, "slippageBps": 1.0, "feePerShare": 0.001},
    }

    first = run_regime_backtest(payload)
    second = run_regime_backtest(payload)

    assert first["replay"]["deterministic"] is True
    assert first["replay"]["decisionHash"] == second["replay"]["decisionHash"]
    assert first["replay"]["tradeHash"] == second["replay"]["tradeHash"]
    assert first["replay"]["resultHash"] == second["replay"]["resultHash"]
    assert first["restartDeterminism"]["resultHash"] == second["restartDeterminism"]["resultHash"]
    assert first["dailySessionReplays"] == second["dailySessionReplays"]


def test_phase17_supplied_higher_timeframe_feed_cannot_change_replay_evidence() -> None:
    candles = _candles(40)
    malicious_future_five_minute = [
        {
            "timestamp": (datetime(2026, 7, 23, 19, 59, tzinfo=UTC)).isoformat().replace("+00:00", "Z"),
            "open": 1_000,
            "high": 2_000,
            "low": 999,
            "close": 1_800,
            "volume": 99_999_999,
        }
    ]

    clean = run_regime_backtest({"symbol": "SPY", "candles": candles})
    with_supplied_future = run_regime_backtest({"symbol": "SPY", "candles": candles, "fiveMinuteCandles": malicious_future_five_minute})

    assert clean["replay"]["decisionHash"] == with_supplied_future["replay"]["decisionHash"]
    assert clean["replay"]["resultHash"] == with_supplied_future["replay"]["resultHash"]
    assert all(decision["pointInTime"]["futureCandlesVisible"] == 0 for decision in with_supplied_future["decisions"])
    assert all(
        decision["pointInTime"]["higherTimeframePolicy"] == "derived_point_in_time_from_finalized_one_minute"
        for decision in with_supplied_future["decisions"]
        if decision["regime"] != "warmup"
    )


def test_phase17_backtest_exit_policy_flattens_at_configured_end_of_day_time() -> None:
    candles = [
        _candle("2026-07-23T19:54:00Z", 100.0),
        _candle("2026-07-23T19:55:00Z", 100.1),
    ]
    intent = {"symbol": "SPY", "side": "Buy", "quantity": 10, "entry_price": 100.1, "stop_price": 95.0, "target_price": 110.0}
    outputs = [
        _pipeline_output(intent=intent, valid=True, signal="Buy"),
        _pipeline_output(intent=None, valid=False, signal="Hold"),
    ]

    with patch("backend.app.algorithms.regime.backtest.engine.execute_regime_pipeline", side_effect=outputs):
        result = run_regime_backtest(
            {
                "symbol": "SPY",
                "candles": candles,
                "settings": {"exit_policy": {"endOfDayFlattenEnabled": True, "flattenTimeEt": "15:55"}},
                "marketModel": {"latencyBars": 0, "ttlBars": 1},
            }
        )

    assert len(result["trades"]) == 1
    assert result["trades"][0]["exitReason"] == "regime.exit.end_of_day_flatten"
    assert result["trades"][0]["exitAt"] == "2026-07-23T19:55:00Z"


def _pipeline_output(*, intent: dict | None, valid: bool, signal: str) -> dict:
    return {
        "decision": {
            "signal": signal,
            "confirmed_state": {"confirmed_regime": "strong_uptrend"},
            "strategy_outputs": [],
            "trade_blockers": (),
        },
        "orderIntent": intent,
        "orderValidation": {"valid": valid},
        "tradeManagement": {"action": "hold"},
    }


def _candles(count: int) -> list[dict[str, float | str]]:
    start = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)
    rows: list[dict[str, float | str]] = []
    price = 100.0
    for index in range(count):
        price += 0.05
        rows.append(_candle((start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"), price))
    return rows


def _candle(timestamp: str, price: float) -> dict[str, float | str]:
    return {
        "timestamp": timestamp,
        "open": price - 0.02,
        "high": price + 0.15,
        "low": price - 0.15,
        "close": price,
        "volume": 120_000,
        "bid": price - 0.01,
        "ask": price + 0.01,
    }
