import unittest

from backend.app.algorithms.regime.classifier import _liquidity_axis, _liquidity_evidence, classify_market_regime
from backend.tests.regime.fixtures.market_snapshots import snapshot


FRESH_QUOTE = {
    "status": "fresh",
    "ageMs": 1000,
    "bid": 99.99,
    "ask": 100.01,
    "tradeCount": 80,
    "expectedFillQuantity": 100,
    "topOfBookDepth": 500,
}


class LiquidityAxisTest(unittest.TestCase):
    def test_complete_fresh_quote_supports_liquidity_states(self):
        self.assertEqual(_liquidity_axis(snapshot("up", context={"quoteFreshness": FRESH_QUOTE}), 1.0), "good")
        self.assertEqual(_liquidity_axis(snapshot("up", context={"quoteFreshness": FRESH_QUOTE}), 0.6), "acceptable")
        self.assertEqual(_liquidity_axis(snapshot("up", context={"quoteFreshness": FRESH_QUOTE}), 0.44), "poor")

    def test_missing_spread_or_bid_ask_fails_closed_as_unknown(self):
        market = snapshot("up", context={"quoteFreshness": {"status": "fresh", "ageMs": 1000}})

        evidence = _liquidity_evidence(market, 1.0)

        self.assertEqual(evidence["axis"], "unknown")
        self.assertTrue(evidence["blockNewEntries"])
        self.assertIn("bid", evidence["missingCriticalFields"])
        self.assertIn("ask", evidence["missingCriticalFields"])
        self.assertIn("spreadBps", evidence["missingCriticalFields"])

    def test_quote_age_over_maximum_fails_closed(self):
        market = snapshot("up", context={"quoteFreshness": {**FRESH_QUOTE, "ageMs": 16000}})

        evidence = _liquidity_evidence(market, 1.0)

        self.assertEqual(evidence["axis"], "unknown")
        self.assertTrue(evidence["blockNewEntries"])
        self.assertIn("regime.liquidity.quote_age_exceeded", evidence["reasonCodes"])

    def test_excessive_participation_or_depth_shortfall_blocks(self):
        market = snapshot(
            "up",
            context={
                "quoteFreshness": {
                    **FRESH_QUOTE,
                    "expectedFillQuantity": 30000,
                    "topOfBookDepth": 1000,
                }
            },
        )

        evidence = _liquidity_evidence(market, 1.0)

        self.assertEqual(evidence["axis"], "poor")
        self.assertTrue(evidence["blockNewEntries"])
        self.assertIn("regime.liquidity.participation_rate_too_high", evidence["reasonCodes"])

    def test_classifier_exposes_liquidity_evidence_and_blocks_missing_quote(self):
        classification = classify_market_regime(snapshot("up"))

        self.assertEqual(classification.axes.liquidity, "unknown")
        self.assertEqual(classification.raw_regime, "liquidity_stress")
        self.assertTrue(classification.evidence["liquidityEvidence"]["blockNewEntries"])
        self.assertIn("regime.safety.liquidity_fail_closed", classification.no_trade_reasons)


if __name__ == "__main__":
    unittest.main()
