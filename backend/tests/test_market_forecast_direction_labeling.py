from __future__ import annotations

from backend.app import market_forecast
from backend.app.market_forecast import ApprovedForecastArtifact


def resolve(
    *,
    up: float,
    down: float,
    timeout: float,
    threshold: float = 0.5,
    edge_gap: float = 0.1,
    buy_ev: float = 0.05,
    sell_ev: float = 0.05,
) -> str:
    return market_forecast.resolve_horizon_direction(
        buy_probability=up,
        sell_probability=down,
        timeout_probability=timeout,
        threshold=threshold,
        minimum_edge_gap=edge_gap,
        buy_expected_value=buy_ev,
        sell_expected_value=sell_ev,
    )


def test_timeout_dominant_forecast_direction_is_flat() -> None:
    assert resolve(up=0.399, down=0.028, timeout=0.573) == "flat"


def test_confirmed_bullish_forecast_direction_is_up() -> None:
    assert resolve(up=0.65, down=0.15, timeout=0.20, buy_ev=0.04) == "up"


def test_confirmed_bearish_forecast_direction_is_down() -> None:
    assert resolve(up=0.15, down=0.65, timeout=0.20, sell_ev=0.04) == "down"


def test_highest_buy_probability_below_threshold_still_predicts_up() -> None:
    assert resolve(up=0.49, down=0.20, timeout=0.31, threshold=0.5) == "up"


def test_required_edge_gap_not_met_still_predicts_probability_leader() -> None:
    assert resolve(up=0.58, down=0.51, timeout=0.01, threshold=0.5, edge_gap=0.1) == "up"


def test_non_positive_expected_value_does_not_change_market_behavior_direction() -> None:
    assert resolve(up=0.65, down=0.15, timeout=0.20, buy_ev=0.0) == "up"
    assert resolve(up=0.65, down=0.15, timeout=0.20, buy_ev=-0.01) == "up"
    assert resolve(up=0.15, down=0.65, timeout=0.20, sell_ev=0.0) == "down"
    assert resolve(up=0.15, down=0.65, timeout=0.20, sell_ev=-0.01) == "down"


def test_tiny_expected_movement_uses_forecast_only_neutral_band() -> None:
    assert market_forecast.forecast_expected_price_direction(0.001, 100.0) == "flat"
    assert market_forecast.forecast_expected_price_direction(-0.001, 100.0) == "flat"
    assert market_forecast.price_direction(0.001) == "up"
    assert market_forecast.price_direction(-0.001) == "down"


def test_future_price_prediction_keeps_expected_movement_separate_from_actionable_direction() -> None:
    prediction = market_forecast.forecast_future_price_prediction(
        {"algorithm": {}, "trend": {}, "mean_reversion": {}, "volatility": {"atr_1m": 0.02}, "regime": {}},
        100.0,
        probabilities={
            market_forecast.OUTCOME_TARGET: 0.399,
            market_forecast.OUTCOME_STOP: 0.028,
            market_forecast.OUTCOME_TIMEOUT: 0.573,
        },
        barriers={"targetDistance": 0.08, "stopDistance": 0.08, "atr5m": 0.10},
        market_regime={},
        actionable_direction="flat",
    )

    assert prediction["direction"] == "flat"
    assert prediction["predictedChange"] == prediction["predictedChangeDollars"]
    assert prediction["expectedPriceDirection"] in {"flat", "up", "down"}


def test_independent_horizons_can_return_flat_up_and_down_simultaneously(monkeypatch) -> None:
    artifact = ApprovedForecastArtifact(
        symbol="SPY",
        payload={
            "artifactId": "direction-label-test",
            "modelKind": "logistic",
            "approved": True,
            "threshold": 0.5,
            "horizonModels": {
                "5": {"approved": True, "threshold": 0.5, "testProbabilities": {market_forecast.OUTCOME_TARGET: 0.399, market_forecast.OUTCOME_STOP: 0.028, market_forecast.OUTCOME_TIMEOUT: 0.573}},
                "10": {"approved": True, "threshold": 0.5, "testProbabilities": {market_forecast.OUTCOME_TARGET: 0.65, market_forecast.OUTCOME_STOP: 0.15, market_forecast.OUTCOME_TIMEOUT: 0.20}},
                "15": {"approved": True, "threshold": 0.5, "testProbabilities": {market_forecast.OUTCOME_TARGET: 0.15, market_forecast.OUTCOME_STOP: 0.65, market_forecast.OUTCOME_TIMEOUT: 0.20}},
            },
        },
    )

    def fake_ensemble_probabilities(features, payload):
        return {"probabilities": payload["testProbabilities"], "modelCount": 1, "modelDisagreement": 0.0, "members": []}

    def fake_barriers(features, latest_close, *, artifact=None, horizon_minutes=5):
        return {
            "targetDistance": 1.0,
            "stopDistance": 1.0,
            "atr5m": 0.1,
            "minTargetPct": 0.0,
            "minStopPct": 0.0,
            "targetAtrMultiplier": 0.0,
            "stopAtrMultiplier": 0.0,
            "fixedTargetDollars": 1.0,
            "fixedStopDollars": 1.0,
        }

    monkeypatch.setattr(market_forecast, "ensemble_probabilities", fake_ensemble_probabilities)
    monkeypatch.setattr(market_forecast, "volatility_adjusted_barriers", fake_barriers)

    forecast = market_forecast.build_multi_horizon_forecast(
        artifact,
        {
            "features": {
                "algorithm": {},
                "trend": {},
                "mean_reversion": {},
                "volatility": {"atr_1m": 0.02, "realized_volatility": 0.0},
                "volume": {"relative_volume": 1.0},
                "microstructure": {"avg_spread": 0.0},
                "regime": {},
            },
            "latest": {"symbol": "SPY", "close": 100.0, "timestamp": "2026-07-23T13:35:00Z"},
        },
        execution_cost_inputs={"spread": 0.0, "slippage": 0.0, "fees": 0.0},
        primary_probabilities={},
        primary_barriers={},
        primary_market_regime={},
    )

    assert [row["predictedDirection"] for row in forecast["horizons"]] == ["flat", "up", "down"]
    assert [row["futurePricePrediction"]["direction"] for row in forecast["horizons"]] == ["flat", "up", "down"]


def test_no_trade_decision_stays_consistent_with_flat_horizon_card() -> None:
    probabilities = {
        market_forecast.OUTCOME_TARGET: 0.399,
        market_forecast.OUTCOME_STOP: 0.028,
        market_forecast.OUTCOME_TIMEOUT: 0.573,
    }
    advice = market_forecast.multi_horizon_position_advice(
        buy_probability=0.399,
        sell_probability=0.028,
        timeout_probability=0.573,
        buy_expected_value=0.01,
        sell_expected_value=-0.05,
        threshold=0.5,
        minimum_edge_gap=0.1,
    )
    decision = market_forecast.forecast_trade_decision(
        probabilities,
        buy_expected_value=0.01,
        sell_expected_value=-0.05,
        regime_allows=True,
        market_regime={},
        uncertainty={"modelDisagreement": 0.0, "modelCount": 1},
        features={"microstructure": {}, "volatility": {"atr_1m": 0.1}},
        base_threshold=0.5,
    )

    assert resolve(up=0.399, down=0.028, timeout=0.573, threshold=0.5, buy_ev=0.01, sell_ev=-0.05) == "flat"
    assert advice["newLongEntry"] == "WAIT"
    assert advice["flatMarket"] == "WAIT"
    assert decision["action"] == market_forecast.DECISION_NO_TRADE


def test_primary_no_trade_blocks_multi_horizon_new_entry_advice() -> None:
    horizons = [
        {
            "status": "ready",
            "predictedDirection": "up",
            "entryAuthorization": False,
            "advice": {
                "longPosition": "KEEP",
                "shortPosition": "CLOSE_REVIEW",
                "newLongEntry": "CONSIDER_AFTER_STRATEGY_SIGNAL",
                "newShortEntry": "WAIT",
                "flatMarket": "DIRECTIONAL_EDGE_PRESENT",
                "reasonCodes": ["ml_horizon_up_edge_confirmed"],
            },
        }
    ]

    gated = market_forecast.apply_primary_decision_gate_to_horizons(
        horizons,
        {"action": market_forecast.DECISION_NO_TRADE},
    )

    assert gated[0]["predictedDirection"] == "up"
    assert gated[0]["primaryDecisionGate"] == "PRIMARY_FORECAST_NO_TRADE"
    assert gated[0]["advice"]["newLongEntry"] == "WAIT"
    assert gated[0]["advice"]["directionalNewLongEntry"] == "CONSIDER_AFTER_STRATEGY_SIGNAL"
    assert "primary_forecast_no_trade_blocks_new_entries" in gated[0]["advice"]["reasonCodes"]
    assert horizons[0]["advice"]["newLongEntry"] == "CONSIDER_AFTER_STRATEGY_SIGNAL"
