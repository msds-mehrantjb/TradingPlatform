from __future__ import annotations

import unittest

from backend.app.algorithms.regime.contracts import RegimeStrategyEvaluation
from backend.app.algorithms.regime.decision_engine import calculate_regime_decision
from backend.app.algorithms.regime.family_aggregation import aggregate_directional_strategies, apply_confirmation_layer, apply_safety_layer
from backend.app.algorithms.regime.local_gates import evaluate_regime_local_gates, evaluate_regime_local_risk
from backend.tests.regime.fixtures.classification_cases import classification
from backend.tests.regime.fixtures.market_snapshots import snapshot


def _directional(strategy_id: str, family: str, signal: str = "Buy", confidence: float = 0.8, edge: float = 12.0) -> RegimeStrategyEvaluation:
    return RegimeStrategyEvaluation(
        strategy_id=strategy_id,
        name=strategy_id,
        family=family,
        role="directional",
        signal=signal,
        confidence=confidence,
        weight=1.0,
        eligible=True,
        reason="regime.test.directional",
        expected_gross_edge_bps=edge,
        evidence={"strategyHealth": "healthy"},
    )


class Phase9AggregationLayersTest(unittest.TestCase):
    def test_directional_aggregation_uses_only_active_independent_direction(self):
        outputs = (
            _directional("trend_a", "trend", "Buy", edge=20.0),
            _directional("trend_b", "trend", "Buy", edge=8.0),
            RegimeStrategyEvaluation("confirm", "confirm", "confirmation", "confirmation", "Sell", 1.0, 1.0, True, "bad"),
            RegimeStrategyEvaluation("shadow", "shadow", "breakout", "directional", "Sell", 1.0, 1.0, False, "shadow", lifecycle_status="shadow"),
        )

        aggregation = aggregate_directional_strategies(outputs, {"minimumIndependentFamilies": 1})

        self.assertEqual(aggregation["signal"], "Buy")
        self.assertEqual(aggregation["activeFamilyCount"], 1)
        self.assertEqual(aggregation["selectedStrategyByFamily"], {"trend": "trend_a"})
        self.assertIn("regime.family_aggregation.correlated_family_collapsed:trend", aggregation["correlationCollisionReasonCodes"])
        self.assertEqual(aggregation["expectedGrossEdgeBps"], 20.0)

    def test_confirmation_and_safety_layers_cannot_create_direction(self):
        hold = aggregate_directional_strategies((), {"minimumIndependentFamilies": 1})
        confirmation = (
            RegimeStrategyEvaluation("volume_confirmation", "Volume", "confirmation", "confirmation", "Buy", 1.0, 0.0, True, "regime.test.invalid_direction"),
        )

        confirmed = apply_confirmation_layer(hold, confirmation, settings={"maximumConfirmationAdjustment": 0.08})
        self.assertEqual(confirmed["signal"], "Hold")
        self.assertIn("regime.confirmation.direction_creation_rejected:volume_confirmation", confirmed["confirmationBlockers"])

        safe = apply_safety_layer(confirmed, blockers=("regime.safety.event_blackout",), runtime_context={"duplicateOrderIntent": True})
        self.assertEqual(safe["signal"], "Hold")
        self.assertIn("regime.safety.event_blackout", safe["safetyBlockers"])
        self.assertIn("regime.safety.duplicate_order", safe["safetyBlockers"])

    def test_vote_margin_is_not_used_as_economic_expected_edge(self):
        aggregation = {
            "activeStrategyCount": 3,
            "activeFamilyCount": 2,
            "winningScore": 0.95,
            "winningEdge": 0.95,
            "abstentionRate": 0,
            "expectedGrossEdgeBps": 0.0,
        }

        blockers = evaluate_regime_local_gates(
            aggregation,
            classification(confidence=0.9),
            None,
            {
                "settingsVersion": "test",
                "minimumActiveStrategies": 1,
                "minimumIndependentFamilies": 1,
                "minimumWinningScore": 0.1,
                "minimumSignalEdge": 0.1,
                "minimumNetExpectedEdge": 0.02,
                "maximumAbstentionRate": 1.0,
                "maxAllowedShares": 10,
                "maxOrderNotionalDollars": 10_000,
                "maxTradesPerDay": 10,
                "maxConsecutiveLosses": 10,
            },
        )

        self.assertIn("regime.local_gate.minimum_net_expected_edge", blockers)

        risk = evaluate_regime_local_risk(
            decision_id="d",
            order_intent_id="o",
            settings_version="s",
            requested_quantity=1,
            entry_price=100.0,
            aggregation=aggregation,
            classification=classification(confidence=0.9),
            state=None,
            settings={
                "minimumNetExpectedEdgeBps": 1.0,
                "maxAllowedShares": 10,
                "maxOrderNotionalDollars": 10_000,
                "maxTradesPerDay": 10,
                "maxConsecutiveLosses": 10,
                "orderTimeToLiveSeconds": 60,
            },
            runtime_context={"requireQuote": False, "requireBuyingPower": False},
        )
        self.assertEqual(risk.estimatedGrossEdge, 0.0)
        self.assertIn("regime.local_risk.minimum_expected_net_edge", risk.blockers)

    def test_decision_snapshot_exposes_separate_aggregation_layers(self):
        decision = calculate_regime_decision(snapshot("up", count=120), settings={"minimumIndependentFamilies": 99})

        self.assertIn("directionalAggregation", decision.effective_settings)
        self.assertIn("confirmationLayer", decision.effective_settings)
        self.assertIn("safetyLayer", decision.effective_settings)
        self.assertEqual(decision.signal, "Hold")
        self.assertIn("regime.directional.minimum_independent_strategies_not_met", decision.effective_settings["directionalAggregation"]["thresholdReasonCodes"])


if __name__ == "__main__":
    unittest.main()
