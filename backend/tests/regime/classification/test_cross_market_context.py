import unittest

from backend.tests.regime.fixtures.market_snapshots import classified_snapshot


READY_CRITICAL_FEEDS = {
    "quoteFreshness": {"status": "fresh", "ageMs": 1000, "bid": 99.99, "ask": 100.01, "tradeCount": 80, "expectedFillQuantity": 100},
    "scheduledEconomicEvent": {"state": "none"},
}


class CrossMarketContextTest(unittest.TestCase):
    def test_broad_market_confirmation_raises_confidence_without_overriding_spy_direction(self):
        _, classification = classified_snapshot(
            "up",
            context={
                **READY_CRITICAL_FEEDS,
                "qqqRelativeStrength": {"relativeToPrimaryPercent": 0.55},
                "iwmRelativeStrength": {"relativeToPrimaryPercent": 0.45},
                "marketBreadth": {"advanceDeclineRatio": 1.35},
                "vix": {"state": "normal"},
                "vix1d": {"state": "normal"},
                "esFutures": {"changePercent": 0.30},
            },
        )

        context = classification.evidence["crossMarketContextEvidence"]
        self.assertEqual(context["label"], "broad_market_trend")
        self.assertGreater(context["confidenceAdjustment"], 0)
        self.assertIn(classification.axes.direction, {"weak_up", "strong_up"})

    def test_risk_off_or_unsupported_context_reduces_confidence(self):
        _, classification = classified_snapshot(
            "up",
            context={
                **READY_CRITICAL_FEEDS,
                "qqqRelativeStrength": {"relativeToPrimaryPercent": -0.35},
                "iwmRelativeStrength": {"relativeToPrimaryPercent": -0.45},
                "marketBreadth": {"advanceDeclineRatio": 0.65},
                "vix": {"state": "stress"},
                "vix1d": {"state": "elevated"},
                "esFutures": {"changePercent": -0.35},
            },
        )

        context = classification.evidence["crossMarketContextEvidence"]
        self.assertEqual(context["label"], "risk_off_divergence")
        self.assertLess(context["confidenceAdjustment"], 0)
        self.assertIn("regime.context.risk_off_divergence", context["reasonCodes"])

    def test_missing_optional_context_reduces_confidence_but_does_not_block(self):
        _, classification = classified_snapshot("up", context=READY_CRITICAL_FEEDS)

        context = classification.evidence["crossMarketContextEvidence"]
        self.assertIn("regime.context.optional_feeds_missing", context["reasonCodes"])
        self.assertLess(context["confidenceAdjustment"], 0)
        self.assertNotIn("regime.safety.missing_quote_freshness", classification.no_trade_reasons)
        self.assertNotIn("regime.safety.missing_event_state", classification.no_trade_reasons)


if __name__ == "__main__":
    unittest.main()
