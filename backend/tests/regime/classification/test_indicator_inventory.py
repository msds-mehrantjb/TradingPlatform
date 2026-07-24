import unittest

from backend.app.algorithms.regime.classifier import classify_market_regime
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
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

    def test_market_open_values_are_not_marked_ready_without_warmup(self):
        classification = classify_market_regime(
            build_regime_market_snapshot(
                {
                    "symbol": "SPY",
                    "primaryCandles": [
                        {
                            "timestamp": "2026-07-23T13:30:00Z",
                            "open": 100.0,
                            "high": 100.15,
                            "low": 99.95,
                            "close": 100.05,
                            "volume": 120000,
                        }
                    ],
                    "contextFeeds": {
                        "quoteFreshness": {
                            "status": "fresh",
                            "ageMs": 500,
                            "bid": 100.04,
                            "ask": 100.06,
                            "tradeCount": 200,
                            "expectedFillQuantity": 100,
                        },
                        "scheduledEconomicEvent": {"state": "none"},
                    },
                }
            )
        )

        readiness = classification.evidence["indicatorReadiness"]["indicators"]

        self.assertIsNotNone(classification.features["vwap"])
        self.assertTrue(readiness["vwap"]["dataReady"])
        self.assertFalse(readiness["ema20"]["dataReady"])
        self.assertFalse(readiness["ema50"]["dataReady"])
        self.assertFalse(readiness["atr"]["dataReady"])
        self.assertFalse(readiness["adx"]["dataReady"])
        self.assertFalse(readiness["realizedVolatility"]["dataReady"])
        self.assertFalse(readiness["volatilityPercentiles"]["dataReady"])
        self.assertFalse(readiness["structure"]["componentReadiness"]["openingRange"]["dataReady"])
        self.assertIn("ema20", classification.missing_inputs)
        self.assertIn("adx", classification.missing_inputs)
        self.assertIn("realizedVolatility", classification.missing_inputs)


if __name__ == "__main__":
    unittest.main()
