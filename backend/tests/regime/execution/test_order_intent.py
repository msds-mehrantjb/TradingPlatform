import unittest

from backend.app.algorithms.regime.decision_engine import calculate_regime_decision
from backend.app.algorithms.regime.order_intent import build_regime_order_intent
from backend.app.algorithms.regime.sizing import calculate_regime_position_size
from backend.tests.regime.fixtures.market_snapshots import snapshot


PERMISSIVE_SETTINGS = {
    "minimumWinningScore": 0,
    "minimumSignalEdge": 0,
    "minimumActiveStrategies": 1,
    "minimumIndependentFamilies": 1,
    "minimumRegimeConfidence": 0,
}


class OrderIntentTest(unittest.TestCase):
    def test_order_intent_preserves_regime_attribution(self):
        market = snapshot("up")
        decision = calculate_regime_decision(market, settings=PERMISSIVE_SETTINGS)
        sizing = calculate_regime_position_size(decision, market, {"availableBuyingPower": 25_000, "remainingAlgorithmRiskDollars": 500})
        intent = build_regime_order_intent(decision, sizing)

        if decision.signal == "Hold" or sizing.quantity <= 0:
            self.assertIsNone(intent)
            return
        self.assertEqual(intent.algorithm_id, "regime")
        self.assertEqual(intent.decision_id, decision.decision_id)
        self.assertTrue(intent.order_intent_id.startswith("regime-intent-"))
        self.assertEqual(intent.regime, decision.confirmed_state.confirmed_regime)

    def test_same_decision_and_size_are_idempotent(self):
        market = snapshot("up")
        decision = calculate_regime_decision(market, settings=PERMISSIVE_SETTINGS)
        sizing = calculate_regime_position_size(decision, market, {"availableBuyingPower": 25_000, "remainingAlgorithmRiskDollars": 500})

        self.assertEqual(build_regime_order_intent(decision, sizing), build_regime_order_intent(decision, sizing))

