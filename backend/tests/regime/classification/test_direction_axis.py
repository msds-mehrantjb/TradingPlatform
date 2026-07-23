import unittest
from backend.app.algorithms.regime.classifier import _direction_axis
from backend.tests.regime.fixtures.market_snapshots import classified_snapshot

class DirectionAxisTest(unittest.TestCase):
    def test_direction_and_trend_strength_are_separate(self):
        self.assertEqual(_direction_axis(0.52, 0.70), "strong_up")
        self.assertEqual(_direction_axis(0.52, 0.40), "weak_up")
        self.assertEqual(_direction_axis(0.10, 0.90), "neutral")
        self.assertEqual(_direction_axis(-0.52, 0.40), "weak_down")
        self.assertEqual(_direction_axis(-0.52, 0.70), "strong_down")

    def test_classifier_exposes_reduced_direction_and_trend_strength_evidence(self):
        _, classification = classified_snapshot(
            "up",
            context={
                "qqqRelativeStrength": {"relativeToPrimaryPercent": 0.55},
                "iwmRelativeStrength": {"relativeToPrimaryPercent": 0.35},
            },
        )

        direction = classification.evidence["directionEvidence"]
        strength = classification.evidence["trendStrengthEvidence"]

        self.assertIn(classification.axes.direction, {"weak_up", "strong_up"})
        self.assertIn("ema20Slope", direction["components"])
        self.assertIn("ema50Slope", direction["components"])
        self.assertIn("vwapSlope", direction["components"])
        self.assertIn("vwapLocation", direction["components"])
        self.assertIn("marketStructure", direction["components"])
        self.assertNotIn("relativeStrength", direction["components"])
        self.assertIn("adx", strength["components"])
        self.assertIn("directionalMovementSpread", strength["components"])
        self.assertIn("efficiencyRatio", strength["components"])
        self.assertNotIn("momentumEvidence", classification.evidence)
        self.assertEqual(
            strength["rule"],
            "ADX, +DI/-DI spread, and efficiency ratio determine whether a directional move is strong or weak; they do not determine direction.",
        )
