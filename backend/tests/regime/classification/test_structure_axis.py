import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.regime.classifier import _structure_axis, _structure_evidence, classify_market_regime
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.tests.regime.fixtures.market_snapshots import snapshot


FRESH_QUOTE = {"status": "fresh", "ageMs": 1000, "bid": 99.99, "ask": 100.01, "tradeCount": 80, "expectedFillQuantity": 100}


def regular_candles(closes: list[float]) -> list[dict]:
    start = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)
    rows = []
    for index, close in enumerate(closes):
        timestamp = (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        rows.append(
            {
                "timestamp": timestamp,
                "open": close - 0.04,
                "high": close + 0.08,
                "low": close - 0.08,
                "close": close,
                "volume": 120000,
                "vwap": 100.0,
            }
        )
    return rows


class StructureAxisTest(unittest.TestCase):
    def test_trend_range_and_breakout_states_remain_supported(self):
        self.assertIn(_structure_axis(snapshot("up"), 5, 0), {"trend", "breakout", "valid_breakout"})
        self.assertIn(_structure_axis(snapshot("flat"), 2, 2), {"range", "mixed"})
        self.assertIn(_structure_axis(snapshot("down"), 0, 5), {"trend", "breakout", "valid_breakout"})

    def test_confirmed_opening_range_breakout_uses_reference_level(self):
        candles = regular_candles([100.0 + index * 0.01 for index in range(35)])
        candles[-1] = {**candles[-1], "open": 100.95, "high": 101.25, "low": 100.90, "close": 101.20}
        market = build_regime_market_snapshot(
            {
                "symbol": "SPY",
                "primaryCandles": candles,
                "contextFeeds": {"marketStructureLevels": {"openingRangeHigh": 101.0, "openingRangeLow": 99.5}},
            }
        )

        evidence = _structure_evidence(market, 4, 0, computed_vwap=100.0, directional_efficiency=0.7)

        self.assertEqual(evidence["axis"], "opening_range_breakout")
        self.assertTrue(evidence["confirmedBreak"])
        self.assertEqual(evidence["activeReferenceLevel"]["type"], "opening_range_high")

    def test_liquidity_sweep_requires_failed_acceptance_at_reference_level(self):
        candles = regular_candles([100.0 + index * 0.01 for index in range(40)])
        candles[-1] = {**candles[-1], "open": 101.08, "high": 101.42, "low": 100.72, "close": 100.86}
        market = build_regime_market_snapshot(
            {
                "symbol": "SPY",
                "primaryCandles": candles,
                "contextFeeds": {"marketStructureLevels": {"priorDayHigh": 101.0}},
            }
        )

        evidence = _structure_evidence(market, 1, 1, computed_vwap=100.0, directional_efficiency=0.25)

        self.assertEqual(evidence["axis"], "liquidity_sweep")
        self.assertTrue(evidence["failedAcceptance"])
        self.assertTrue(evidence["rejectionCandle"]["isRejection"])
        self.assertEqual(evidence["activeReferenceLevel"]["type"], "prior_day_high")

    def test_rejection_candle_alone_does_not_create_reversal(self):
        candles = regular_candles([100.0 + index * 0.01 for index in range(40)])
        candles[-1] = {**candles[-1], "open": 99.70, "high": 99.95, "low": 99.20, "close": 99.90}
        market = build_regime_market_snapshot({"symbol": "SPY", "primaryCandles": candles})

        evidence = _structure_evidence(market, 1, 1, computed_vwap=100.0, directional_efficiency=0.25)

        self.assertTrue(evidence["rejectionCandle"]["isRejection"])
        self.assertNotIn(evidence["axis"], {"reversal", "liquidity_sweep", "failed_breakout"})

    def test_choppy_mixed_structure_is_reachable(self):
        candles = regular_candles([100.10, 99.90, 100.08, 99.92, 100.06, 99.94, 100.04, 99.96, 100.03, 99.97])
        market = build_regime_market_snapshot({"symbol": "SPY", "primaryCandles": candles})

        evidence = _structure_evidence(market, 1, 1, computed_vwap=100.0, directional_efficiency=0.12)

        self.assertEqual(evidence["axis"], "mixed")
        self.assertGreaterEqual(evidence["vwapCrossingFrequency"], 3)

    def test_classifier_exposes_structure_evidence_and_choppy_composite(self):
        candles = regular_candles([100.10, 99.90, 100.08, 99.92, 100.06, 99.94, 100.04, 99.96, 100.03, 99.97])
        market = build_regime_market_snapshot({"symbol": "SPY", "primaryCandles": candles, "contextFeeds": {"quoteFreshness": FRESH_QUOTE}})

        classification = classify_market_regime(market)

        self.assertIn("structureEvidence", classification.evidence)
        self.assertEqual(classification.axes.structure, "mixed")
        self.assertEqual(classification.raw_regime, "choppy_mixed")


if __name__ == "__main__":
    unittest.main()
