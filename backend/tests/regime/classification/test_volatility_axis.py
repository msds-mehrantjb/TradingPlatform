import unittest
from backend.app.algorithms.regime.classifier import _volatility_axis
from backend.tests.regime.fixtures.market_snapshots import classified_snapshot

class VolatilityAxisTest(unittest.TestCase):
    def test_all_volatility_states_and_boundaries_use_intraday_percentiles(self):
        self.assertEqual(_volatility_axis({"atrPercentile": 0.24, "realizedVolatilityPercentile": 0.34}), "compressed")
        self.assertEqual(_volatility_axis({"atrPercentile": 0.26, "realizedVolatilityPercentile": 0.34}), "normal")
        self.assertEqual(_volatility_axis({"atrPercentile": 0.75, "realizedVolatilityPercentile": 0.75}), "expanded")
        self.assertEqual(_volatility_axis({"atrPercentile": 0.97, "realizedVolatilityPercentile": 0.75}), "extreme")

    def test_range_vs_expected_confirms_transition_during_percentile_disagreement(self):
        self.assertEqual(_volatility_axis({"atrPercentile": 0.80, "realizedVolatilityPercentile": 0.40, "currentRangeVsExpected": 1.0}), "normal")
        self.assertEqual(_volatility_axis({"atrPercentile": 0.80, "realizedVolatilityPercentile": 0.40, "currentRangeVsExpected": 1.6}), "expanded")
        self.assertEqual(_volatility_axis({"atrPercentile": 0.98, "realizedVolatilityPercentile": 0.40, "currentRangeVsExpected": 3.1}), "extreme")
        self.assertEqual(_volatility_axis({"atrPercentile": None, "realizedVolatilityPercentile": None}), "normal")

    def test_classifier_uses_context_feed_same_minute_percentiles(self):
        _, classification = classified_snapshot(
            "up",
            context={
                "intradayVolatilityBaseline": {
                    "calibrationStatus": "ready",
                    "atrPercentile": 0.78,
                    "realizedVolatilityPercentile": 0.42,
                    "currentRangeVsExpected": 1.1,
                    "currentVolumeVsExpected": 1.2,
                    "sampleSize": 90,
                }
            },
        )

        self.assertEqual(classification.axes.volatility, "normal")
        self.assertEqual(classification.evidence["volatilityEvidence"]["agreement"], "atr_rv_disagreement")
        self.assertIn(
            "regime.volatility.atr_rv_direction_disagreement",
            classification.evidence["volatilityEvidence"]["disagreementReasonCodes"],
        )

    def test_classifier_uses_range_confirmed_same_minute_transition(self):
        _, classification = classified_snapshot(
            "up",
            context={
                "intradayVolatilityBaseline": {
                    "calibrationStatus": "ready",
                    "atrPercentile": 0.78,
                    "realizedVolatilityPercentile": 0.42,
                    "currentRangeVsExpected": 1.7,
                    "currentVolumeVsExpected": 1.2,
                    "sampleSize": 90,
                }
            },
        )

        self.assertEqual(classification.axes.volatility, "expanded")
        self.assertEqual(classification.features["atrPercentile"], 0.78)
        self.assertEqual(classification.evidence["volatilityEvidence"]["agreement"], "range_confirmed_transition")
        self.assertEqual(classification.evidence["volatilityEvidence"]["calibrationStatus"], "ready")
        self.assertEqual(
            classification.evidence["volatilityEvidence"]["policy"]["calibrationRequirement"],
            "Thresholds must be calibrated through historical regime occupancy and out-of-sample results.",
        )
