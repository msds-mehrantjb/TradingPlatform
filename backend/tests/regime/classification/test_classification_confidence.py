import unittest

from backend.app.algorithms.regime.classifier import _confidence, classify_market_regime
from backend.tests.regime.fixtures.market_snapshots import snapshot


FRESH_QUOTE = {
    "status": "fresh",
    "ageMs": 1000,
    "bid": 99.99,
    "ask": 100.01,
    "tradeCount": 80,
    "expectedFillQuantity": 100,
}


class ClassificationConfidenceTest(unittest.TestCase):
    def test_legacy_confidence_no_longer_boosts_no_trade_reasons(self):
        blocked = _confidence(0, 0, (), ("regime.safety.event_blackout",))

        self.assertLess(blocked, 0.7)
        self.assertLessEqual(_confidence(99, 0, (), ()), 1.0)

    def test_classifier_exposes_axis_and_composite_confidence(self):
        classification = classify_market_regime(
            snapshot(
                "up",
                context={
                    "quoteFreshness": FRESH_QUOTE,
                    "scheduledEconomicEvent": {"state": "none"},
                    "intradayVolatilityBaseline": {
                        "calibrationStatus": "ready",
                        "atrPercentile": 0.45,
                        "realizedVolatilityPercentile": 0.48,
                        "currentRangeVsExpected": 1.0,
                        "sampleSize": 80,
                    },
                },
            )
        )

        confidence = classification.evidence["confidenceEvidence"]

        self.assertIn("directionConfidence", confidence)
        self.assertIn("volatilityConfidence", confidence)
        self.assertIn("structureConfidence", confidence)
        self.assertIn("liquidityConfidence", confidence)
        self.assertIn("eventConfidence", confidence)
        self.assertIn("compositeConfidence", confidence)
        self.assertEqual(classification.confidence, confidence["compositeConfidence"])
        self.assertEqual(classification.features["compositeConfidence"], confidence["compositeConfidence"])
        self.assertEqual(confidence["safetyBlockConfidence"], 0.0)

    def test_safety_block_confidence_is_separate_from_classification_confidence(self):
        classification = classify_market_regime(
            snapshot(
                "up",
                context={
                    "quoteFreshness": {"status": "unknown"},
                    "scheduledEconomicEvent": {"state": "none"},
                },
            )
        )

        confidence = classification.evidence["confidenceEvidence"]

        self.assertEqual(classification.raw_regime, "liquidity_stress")
        self.assertEqual(confidence["liquidityConfidence"], 0.25)
        self.assertEqual(confidence["compositeConfidence"], 0.25)
        self.assertGreater(confidence["safetyBlockConfidence"], confidence["compositeConfidence"])
        self.assertIn("regime.safety.liquidity_fail_closed", classification.no_trade_reasons)


if __name__ == "__main__":
    unittest.main()
