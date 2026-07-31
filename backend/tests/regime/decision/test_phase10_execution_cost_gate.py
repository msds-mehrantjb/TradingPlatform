from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.app.algorithms.regime.configuration import (
    validate_regime_settings,
    validate_regime_trading_settings_snapshot,
)
from backend.app.algorithms.regime.execution_cost_adapter import (
    estimate_regime_execution_cost,
    evaluate_regime_execution_cost_gate,
)
from backend.app.algorithms.regime.local_gates import evaluate_regime_local_risk
from backend.tests.regime.fixtures.classification_cases import classification


NOW = datetime(2026, 7, 23, 15, 30, tzinfo=UTC)


class FakeExecutionCostModel:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {}
        self.calls: list[dict] = []

    def estimate(self, *, symbol: str, side: str, order_type: str, feature_snapshot: dict, conservative_fallback: dict) -> dict:
        self.calls.append(
            {
                "symbol": symbol,
                "side": side,
                "orderType": order_type,
                "featureSnapshot": dict(feature_snapshot),
            }
        )
        return {**conservative_fallback, **self.payload}


def test_execution_cost_estimate_calculates_required_net_edge_formula_with_fallback() -> None:
    settings = _settings(
        conservativeCostFallbackApproved=True,
        maximumSlippageBps=10.0,
        maximumCostToEdgeRatio=0.75,
        minimumNetExpectedEdgeBps=5.0,
        estimatedFeesBps=0.5,
        estimatedRegulatoryFeesBps=0.2,
        marketImpactBps=1.0,
        adverseSelectionBufferBps=1.5,
        uncertaintyBufferBps=2.0,
    )
    estimate = estimate_regime_execution_cost(
        symbol="SPY",
        side="Buy",
        order_type="limit",
        entry_price=100.0,
        quantity=10,
        expected_gross_edge_bps=20.0,
        classification=classification(features={"spreadBps": 8.0}),
        settings=settings,
        runtime_context={"spreadBps": 8.0, "expectedSlippageBps": 3.0},
        model_service=FakeExecutionCostModel({"status": "CONSERVATIVE_FALLBACK_MODEL_INACTIVE", "modelApplied": False}),
        evaluated_at=NOW,
    )

    assert estimate.expected_net_edge_bps == pytest.approx(7.8)
    assert estimate.total_cost_bps == pytest.approx(12.2)
    assert estimate.cost_to_edge_ratio == pytest.approx(0.61)
    assert estimate.unavailable is False
    assert evaluate_regime_execution_cost_gate(estimate, settings)["passed"] is True


def test_execution_cost_gate_fails_closed_when_model_unavailable_and_fallback_not_approved() -> None:
    settings = _settings(conservativeCostFallbackApproved=False)
    estimate = estimate_regime_execution_cost(
        symbol="SPY",
        side="Buy",
        order_type="limit",
        entry_price=100.0,
        quantity=10,
        expected_gross_edge_bps=100.0,
        classification=classification(features={"spreadBps": 2.0}),
        settings=settings,
        runtime_context={"spreadBps": 2.0},
        model_service=FakeExecutionCostModel({"status": "CONSERVATIVE_FALLBACK_MODEL_INACTIVE", "modelApplied": False}),
        evaluated_at=NOW,
    )

    gate = evaluate_regime_execution_cost_gate(estimate, settings)

    assert gate["passed"] is False
    assert "regime.execution_cost.model_unavailable_fallback_not_approved" in gate["reasonCodes"]


@pytest.mark.parametrize(
    ("settings_update", "gross_edge_bps", "expected_reason"),
    [
        ({"conservativeCostFallbackApproved": True, "minimumNetExpectedEdgeBps": 1.0}, 2.0, "regime.execution_cost.net_edge_nonpositive"),
        ({"conservativeCostFallbackApproved": True, "minimumNetExpectedEdgeBps": 12.0}, 20.0, "regime.execution_cost.net_edge_below_threshold"),
        ({"conservativeCostFallbackApproved": True, "maximumCostToEdgeRatio": 0.20}, 20.0, "regime.execution_cost.cost_to_edge_ratio_exceeded"),
        ({"conservativeCostFallbackApproved": True, "maximumSlippageBps": 1.0}, 20.0, "regime.execution_cost.slippage_limit_exceeded"),
    ],
)
def test_execution_cost_gate_blocks_net_edge_ratio_and_slippage_failures(settings_update: dict, gross_edge_bps: float, expected_reason: str) -> None:
    settings_payload = {
        "estimatedFeesBps": 0.5,
        "estimatedRegulatoryFeesBps": 0.2,
        "marketImpactBps": 1.0,
        "adverseSelectionBufferBps": 1.5,
        "uncertaintyBufferBps": 2.0,
        "maximumSlippageBps": 10.0,
        **settings_update,
    }
    settings = _settings(**settings_payload)
    estimate = estimate_regime_execution_cost(
        symbol="SPY",
        side="Buy",
        order_type="limit",
        entry_price=100.0,
        quantity=10,
        expected_gross_edge_bps=gross_edge_bps,
        classification=classification(features={"spreadBps": 8.0}),
        settings=settings,
        runtime_context={"spreadBps": 8.0, "expectedSlippageBps": 3.0},
        model_service=FakeExecutionCostModel({"status": "CONSERVATIVE_FALLBACK_MODEL_INACTIVE", "modelApplied": False}),
        evaluated_at=NOW,
    )

    gate = evaluate_regime_execution_cost_gate(estimate, settings)

    assert gate["passed"] is False
    assert expected_reason in gate["reasonCodes"]


def test_execution_cost_gate_blocks_stale_active_model() -> None:
    settings = _settings(conservativeCostFallbackApproved=True, maximumCostModelAgeSeconds=30)
    estimate = estimate_regime_execution_cost(
        symbol="SPY",
        side="Buy",
        order_type="limit",
        entry_price=100.0,
        quantity=10,
        expected_gross_edge_bps=100.0,
        classification=classification(features={"spreadBps": 2.0}),
        settings=settings,
        runtime_context={"spreadBps": 2.0},
        model_service=FakeExecutionCostModel(
            {
                "status": "ACTIVE_MODEL",
                "modelApplied": True,
                "artifactId": "cost-model-active",
                "modelAgeSeconds": 31,
                "reasonCodes": ("execution_cost_model.active_artifact_applied",),
            }
        ),
        evaluated_at=NOW,
    )

    gate = evaluate_regime_execution_cost_gate(estimate, settings)

    assert gate["passed"] is False
    assert "regime.execution_cost.model_stale" in gate["reasonCodes"]


def test_local_risk_persists_complete_execution_cost_estimate_and_gate_result() -> None:
    settings = _settings(
        conservativeCostFallbackApproved=True,
        minimumActiveStrategies=1,
        minimumIndependentFamilies=1,
        minimumWinningScore=0.1,
        minimumSignalEdge=0.1,
        minimumNetExpectedEdgeBps=5.0,
        maximumCostToEdgeRatio=0.75,
        maximumSlippageBps=5.0,
        uncertaintyBufferBps=0.5,
        estimatedFeesBps=0.1,
        adverseSelectionBufferBps=0.1,
    )
    result = evaluate_regime_local_risk(
        decision_id="regime-decision-cost",
        order_intent_id="regime-intent-cost",
        settings_version="regime-settings-cost",
        requested_quantity=10,
        entry_price=100.0,
        aggregation={
            "activeStrategyCount": 2,
            "activeFamilyCount": 2,
            "winningScore": 0.8,
            "winningEdge": 0.4,
            "expectedGrossEdgeBps": 100.0,
            "selectedStrategyByFamily": {"trend": {"strategyId": "moving_average_trend"}},
            "familyScores": {"trend": 0.8},
        },
        classification=classification(features={"spreadBps": 2.0}),
        state=None,
        settings=settings,
        runtime_context={
            "quoteFreshness": {"status": "fresh", "ageMs": 100, "bid": 99.99, "ask": 100.01, "spreadBps": 2.0, "expectedFillQuantity": 10_000},
            "accountSnapshot": {"sourceAuthority": "shared_backend_service", "equity": 100_000.0, "availableBuyingPower": 100_000.0},
            "inventorySnapshot": {"algorithmId": "regime", "symbol": "SPY", "quantity": 0, "openOrderQuantity": 0, "reservedCash": 0.0},
            "dailyCounters": {"tradeCount": 0, "consecutiveLosses": 0, "dailyLossPercent": 0.0, "strategyTradeCounts": {}, "familyTradeCounts": {}},
            "inventoryReconciled": True,
            "recoverySucceeded": True,
        },
        evaluated_at=NOW,
    )

    assert result.passed is True
    assert result.details["executionCostGate"]["passed"] is True
    estimate = result.details["executionCostEstimate"]
    assert estimate["expectedNetEdgeBps"] > 0
    assert estimate["modelVersion"]
    assert estimate["modelStatus"]
    assert estimate["conservativeFallbackApproved"] is True


def test_dynamic_overlay_cannot_relax_phase10_cost_safety_bounds() -> None:
    with pytest.raises(ValueError, match="maximumCostToEdgeRatio cannot exceed baseline"):
        validate_regime_trading_settings_snapshot(
            {
                "execution": {"maximumCostToEdgeRatio": 0.5},
                "dynamic_profiles": {"overlays": {"strong_uptrend": {"maximumCostToEdgeRatio": 0.6}}},
            }
        )
    with pytest.raises(ValueError, match="minimumNetExpectedEdgeBps cannot reduce baseline edge"):
        validate_regime_trading_settings_snapshot(
            {
                "family_aggregation": {"minimumNetExpectedEdgeBps": 5.0},
                "dynamic_profiles": {"overlays": {"strong_uptrend": {"minimumNetExpectedEdgeBps": 4.0}}},
            }
        )
    with pytest.raises(ValueError, match="conservativeCostFallbackApproved cannot enable fallback beyond baseline"):
        validate_regime_trading_settings_snapshot(
            {
                "execution": {"conservativeCostFallbackApproved": False},
                "dynamic_profiles": {"overlays": {"strong_uptrend": {"conservativeCostFallbackApproved": True}}},
            }
        )


def _settings(**overrides) -> dict:
    return validate_regime_settings(
        {
            "settingsVersion": "regime-settings-phase10",
            "maxAllowedShares": 1_000,
            "maxOrderNotionalDollars": 1_000_000.0,
            "maxPositionNotionalDollars": 1_000_000.0,
            "maxParticipationPercent": 1.0,
            "maxTradesPerDay": 10,
            "maxConsecutiveLosses": 10,
            "maxDailyLossPercent": 10.0,
            "entryCutoffTimeEt": "15:30",
            "orderTimeToLiveSeconds": 60,
            **overrides,
        }
    )
