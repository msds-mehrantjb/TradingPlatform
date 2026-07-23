import unittest

from backend.tests.regime.fixtures.market_snapshots import classified_snapshot


class IndicatorInventoryTest(unittest.TestCase):
    def test_authoritative_classifier_uses_reduced_non_duplicative_inventory(self):
        _, classification = classified_snapshot(
            "up",
            context={
                "quoteFreshness": {"status": "fresh", "ageMs": 1000, "bid": 99.99, "ask": 100.01, "tradeCount": 100, "expectedFillQuantity": 100},
                "scheduledEconomicEvent": {"state": "none"},
                "qqqRelativeStrength": {"relativeToPrimaryPercent": 0.30},
                "iwmRelativeStrength": {"relativeToPrimaryPercent": 0.20},
                "marketBreadth": {"advanceDeclineRatio": 1.25},
            },
        )

        direction = classification.evidence["directionEvidence"]["components"]
        strength = classification.evidence["trendStrengthEvidence"]["components"]
        volatility = classification.evidence["volatilityEvidence"]
        structure = classification.evidence["structureEvidence"]
        liquidity = classification.evidence["liquidityEvidence"]
        market_context = classification.evidence["crossMarketContextEvidence"]["components"]
        session = classification.evidence["sessionEvidence"]
        event = classification.evidence["eventEvidence"]

        self.assertEqual(set(direction), {"ema20Slope", "ema50Slope", "vwapSlope", "vwapLocation", "marketStructure"})
        self.assertEqual(set(strength), {"adx", "directionalMovementSpread", "efficiencyRatio"})
        self.assertIn("atrPercent", volatility)
        self.assertIn("atrPercentile", volatility)
        self.assertIn("realizedVolatilityPercentile", volatility)
        self.assertIn("higherHighsHigherLows", structure)
        self.assertIn("breakOfStructure", structure)
        self.assertIn("changeOfCharacter", structure)
        self.assertIn("retestOutcome", structure)
        self.assertIn("rejectionCandle", structure)
        self.assertIn("spreadBps", liquidity)
        self.assertIn("quoteAgeMs", liquidity)
        self.assertIn("relativeOneMinuteVolume", liquidity)
        self.assertIn("tradeRatePerSecond", liquidity)
        self.assertIn("participationRate", liquidity)
        self.assertIn("spyVsQqqRelativeStrength", market_context)
        self.assertIn("spyVsIwmRelativeStrength", market_context)
        self.assertIn("marketBreadthScore", market_context)
        self.assertIn("timestampEt", session)
        self.assertIn("eventType", event)
        self.assertIn("newEntriesBlocked", event)

    def test_removed_correlated_indicators_are_not_authoritative_classifier_outputs(self):
        _, classification = classified_snapshot("up")

        self.assertNotIn("rsi", classification.features)
        self.assertNotIn("macdHistogram", classification.features)
        self.assertNotIn("rateOfChange", classification.features)
        self.assertNotIn("momentumEvidence", classification.evidence)
        self.assertNotIn("exhaustionEvidence", classification.evidence)


if __name__ == "__main__":
    unittest.main()
