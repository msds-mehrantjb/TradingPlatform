from __future__ import annotations

from datetime import UTC, datetime

from backend.app.algorithms.regime.configuration import flatten_regime_trading_settings, validate_regime_settings, validate_regime_trading_settings_snapshot
from backend.app.algorithms.regime.contracts import (
    REGIME_ALGORITHM_ID,
    REGIME_ALGORITHM_VERSION,
    REGIME_STRATEGY_CATALOG_VERSION,
    RegimeCandle,
    RegimeDecision,
    RegimeHysteresisState,
    RegimeMarketSnapshot,
)
from backend.app.algorithms.regime.local_gates import evaluate_regime_local_risk
from backend.app.algorithms.regime.sizing import calculate_regime_position_size
from backend.tests.regime.fixtures.classification_cases import classification


NOW = datetime(2026, 7, 23, 15, 30, tzinfo=UTC)


def test_phase11_initial_automatic_paper_defaults_are_conservative() -> None:
    snapshot = validate_regime_trading_settings_snapshot().as_dict()
    flat = flatten_regime_trading_settings(snapshot)

    assert flat["symbolAllowlist"] == ["SPY"]
    assert flat["timeframe"] == "1Min"
    assert flat["paperOnly"] is True
    assert flat["shortEntriesEnabled"] is False
    assert flat["allowShortEntries"] is False
    assert flat["pyramidingEnabled"] is False
    assert flat["maxOpenRegimePositions"] == 1
    assert flat["maxEntriesPerDay"] == 3
    assert flat["regularHoursOnly"] is True
    assert flat["allowMarketEntryOrders"] is False
    assert flat["endOfDayFlattenEnabled"] is True
    assert flat["mandatoryStop"] is True
    assert flat["mandatoryMaxHoldingTime"] is True
    assert flat["baseRiskPercent"] <= 0.10
    assert flat["maxDailyLossPercent"] <= 0.50
    assert flat["maxPositionPercent"] <= 10.0


def test_phase11_sizing_sequence_uses_regime_settings_account_inventory_stop_confidence_and_liquidity() -> None:
    settings = _settings()
    decision = _decision(settings)
    sizing = calculate_regime_position_size(
        decision,
        _snapshot(volume=10_000, expected_fill_quantity=500),
        _account(equity=100_000, buying_power=100_000),
        _inventory(),
        {"entryCount": 0, "dailyLossPercent": 0.0},
    )

    labels = [cap["label"] for cap in sizing.quantity_caps]

    assert labels[:4] == [
        "regime_risk_based_quantity",
        "regime_capital_cap",
        "regime_liquidity_participation_cap",
        "regime_inventory_trade_count_cap",
    ]
    assert sizing.quantity == 40
    assert sizing.risk_dollars == 80.0
    assert sizing.stop_distance == 2.0
    assert sizing.stop_price == 98.0
    assert sizing.target_price == 103.0
    assert sizing.limiting_factor == "regime_risk_based_quantity"


def test_phase11_sizing_blocks_missing_trusted_account_or_inventory() -> None:
    settings = _settings()
    sizing = calculate_regime_position_size(_decision(settings), _snapshot(), {}, {}, {"entryCount": 0})

    assert sizing.quantity == 0
    assert "regime.sizing.account_snapshot_unavailable" in sizing.blockers
    assert "regime.sizing.account_equity_required" in sizing.blockers
    assert "regime.sizing.buying_power_required" in sizing.blockers
    assert "regime.sizing.inventory_snapshot_unavailable" in sizing.blockers


def test_phase11_sizing_blocks_duplicate_entry_order_and_second_position() -> None:
    settings = _settings()
    duplicate = calculate_regime_position_size(
        _decision(settings),
        _snapshot(),
        _account(),
        _inventory(open_order_quantity=5),
        {"entryCount": 0},
    )
    existing = calculate_regime_position_size(
        _decision(settings),
        _snapshot(),
        _account(),
        _inventory(quantity=10),
        {"entryCount": 0},
    )

    assert "regime.sizing.open_entry_order_exists" in duplicate.blockers
    assert "regime.sizing.existing_regime_position" in existing.blockers
    assert "regime.sizing.max_open_regime_positions" in existing.blockers


def test_phase11_local_risk_blocks_selling_more_than_regime_owned_long_quantity() -> None:
    result = evaluate_regime_local_risk(
        decision_id="regime-decision-sell",
        order_intent_id="regime-intent-sell",
        settings_version="regime-settings-v1",
        requested_quantity=15,
        entry_price=100.0,
        aggregation=_aggregation(),
        classification=classification(features={"expectedGrossEdgeBps": 100.0}),
        state=None,
        settings={**_settings(), "conservativeCostFallbackApproved": True},
        runtime_context={
            "side": "Sell",
            "positionEffect": "exit_long",
            "requireQuote": False,
            "accountSnapshot": _account(),
            "inventorySnapshot": _inventory(quantity=10),
            "dailyCounters": {"entryCount": 0, "consecutiveLosses": 0, "dailyLossPercent": 0.0, "strategyTradeCounts": {}, "familyTradeCounts": {}},
            "inventoryReconciled": True,
            "recoverySucceeded": True,
            "expectedGrossEdgeBps": 100.0,
        },
        evaluated_at=NOW,
    )

    assert result.passed is False
    assert "regime.local_risk.sell_quantity_exceeds_regime_long" in result.blockers


def test_phase11_local_risk_reduces_buying_power_by_regime_reserved_cash() -> None:
    result = evaluate_regime_local_risk(
        decision_id="regime-decision-reserve",
        order_intent_id="regime-intent-reserve",
        settings_version="regime-settings-v1",
        requested_quantity=10,
        entry_price=100.0,
        aggregation=_aggregation(),
        classification=classification(features={"expectedGrossEdgeBps": 100.0}),
        state=None,
        settings={**_settings(), "conservativeCostFallbackApproved": True},
        runtime_context={
            "requireQuote": False,
            "accountSnapshot": _account(equity=10_000, buying_power=1_000),
            "inventorySnapshot": _inventory(reserved_cash=500.0),
            "dailyCounters": {"entryCount": 0, "consecutiveLosses": 0, "dailyLossPercent": 0.0, "strategyTradeCounts": {}, "familyTradeCounts": {}},
            "inventoryReconciled": True,
            "recoverySucceeded": True,
            "expectedGrossEdgeBps": 100.0,
        },
        evaluated_at=NOW,
    )

    assert result.passed is True
    assert result.approvedQuantity == 5
    assert any(reduction["reasonCode"] == "regime.local_risk.reduce.buying_power" for reduction in result.reductions)


def _settings() -> dict:
    return validate_regime_settings(
        {
            "settingsVersion": "regime-settings-v1",
            "baseRiskPercent": 0.10,
            "maxPositionPercent": 10.0,
            "dailyAllocationPercent": 20.0,
            "maxAllowedShares": 1_000,
            "maxOrderNotionalDollars": 1_000_000.0,
            "maxPositionNotionalDollars": 1_000_000.0,
            "maxParticipationPercent": 0.02,
            "minimumStopDistancePercent": 0.05,
            "atrStopMultiplier": 2.0,
            "takeProfitR": 1.5,
            "maxEntriesPerDay": 3,
            "maxTradesPerDay": 3,
            "maxConsecutiveLosses": 3,
            "maxDailyLossPercent": 0.50,
            "minimumActiveStrategies": 1,
            "minimumIndependentFamilies": 1,
            "minimumWinningScore": 0.1,
            "minimumSignalEdge": 0.1,
            "minimumNetExpectedEdgeBps": 5.0,
            "maximumSlippageBps": 5.0,
            "maximumCostToEdgeRatio": 0.75,
            "estimatedFeesBps": 0.0,
            "adverseSelectionBufferBps": 0.0,
            "uncertaintyBufferBps": 0.0,
            "orderTimeToLiveSeconds": 60,
        }
    )


def _decision(settings: dict, *, signal: str = "Buy") -> RegimeDecision:
    return RegimeDecision(
        algorithm_id=REGIME_ALGORITHM_ID,
        algorithm_version=REGIME_ALGORITHM_VERSION,
        settings_version=str(settings["settingsVersion"]),
        strategy_catalog_version=REGIME_STRATEGY_CATALOG_VERSION,
        profile_version=str(settings["profileVersion"]),
        decision_id="regime-decision-sizing",
        symbol="SPY",
        signal=signal,
        aggregate_signal=signal,
        trade_allowed=True,
        trade_blockers=(),
        raw_classification=classification(features={"atr": 1.0, "expectedGrossEdgeBps": 100.0}),
        confirmed_state=RegimeHysteresisState(
            confirmed_regime="strong_uptrend",
            previous_regime=None,
            candidate_regime=None,
            candidate_confirmation_count=0,
            regime_start_time="2026-07-23T14:00:00Z",
            transition_confidence=0.8,
            transition_reason="test",
            regime_confidence=0.8,
        ),
        strategy_outputs=(),
        family_scores={"trend": 0.8},
        effective_settings={**settings, "familyAggregation": _aggregation()},
        score=0.8,
        confidence=0.8,
    )


def _snapshot(*, volume: int = 10_000, expected_fill_quantity: int = 500) -> RegimeMarketSnapshot:
    candles = (
        RegimeCandle(timestamp="2026-07-23T15:29:00Z", open=99.8, high=100.2, low=99.7, close=100.0, volume=volume),
    )
    return RegimeMarketSnapshot(
        symbol="SPY",
        candles=candles,
        one_minute_candles=candles,
        five_minute_candles=(),
        fifteen_minute_candles=(),
        context_feeds={"quoteFreshness": {"expectedFillQuantity": expected_fill_quantity}},
    )


def _account(*, equity: float = 100_000.0, buying_power: float = 100_000.0) -> dict:
    return {
        "sourceAuthority": "shared_backend_service",
        "equity": equity,
        "availableBuyingPower": buying_power,
        "buyingPower": buying_power,
        "buyingPowerCurrent": True,
        "accountSnapshotFresh": True,
    }


def _inventory(*, quantity: int = 0, open_order_quantity: int = 0, reserved_cash: float = 0.0) -> dict:
    return {
        "algorithmId": "regime",
        "symbol": "SPY",
        "quantity": quantity,
        "openOrderQuantity": open_order_quantity,
        "reservedCash": reserved_cash,
        "inventoryReconciled": True,
    }


def _aggregation() -> dict:
    return {
        "activeStrategyCount": 2,
        "activeFamilyCount": 2,
        "winningScore": 0.8,
        "winningEdge": 0.4,
        "expectedGrossEdgeBps": 100.0,
        "selectedStrategyByFamily": {"trend": {"strategyId": "moving_average_trend"}},
        "familyScores": {"trend": 0.8},
    }
