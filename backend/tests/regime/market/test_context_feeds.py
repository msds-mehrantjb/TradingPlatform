from __future__ import annotations

import unittest

from backend.app.algorithms.regime.context_feeds import (
    adapt_intraday_volatility_baseline,
    adapt_es_futures_context,
    adapt_market_breadth,
    adapt_quote_freshness,
    adapt_relative_strength,
    adapt_scheduled_event_state,
    adapt_vix_context,
    build_regime_context_feeds,
)
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot
from backend.tests.regime.fixtures.candles import candles


class RegimeContextFeedAdapterTest(unittest.TestCase):
    def test_missing_feeds_are_explicit_unknowns(self) -> None:
        feeds = build_regime_context_feeds({})

        self.assertEqual(feeds["quoteFreshness"]["status"], "unknown")
        self.assertEqual(feeds["qqqRelativeStrength"]["state"], "unknown")
        self.assertEqual(feeds["iwmRelativeStrength"]["state"], "unknown")
        self.assertEqual(feeds["marketBreadth"]["state"], "unknown")
        self.assertEqual(feeds["vix"]["state"], "unknown")
        self.assertEqual(feeds["vix1d"]["state"], "unknown")
        self.assertEqual(feeds["esFutures"]["trend"], "unknown")
        self.assertEqual(feeds["scheduledEconomicEvent"]["state"], "unknown")
        self.assertEqual(feeds["intradayVolatilityBaseline"]["calibrationStatus"], "missing")
        self.assertIsNone(feeds["quoteFreshness"]["bid"])
        self.assertIsNone(feeds["quoteFreshness"]["ask"])
        self.assertIsNone(feeds["quoteFreshness"]["spreadBps"])

    def test_quote_freshness_accepts_quotes_and_age_aliases(self) -> None:
        fresh = adapt_quote_freshness({"quote_age_ms": 5000, "bid": 99.95, "ask": 100.05})
        stale = adapt_quote_freshness({"ageMs": 20000, "spreadBps": 12})

        self.assertEqual(fresh["status"], "fresh")
        self.assertAlmostEqual(fresh["spreadPercent"], 0.001)
        self.assertAlmostEqual(fresh["spreadBps"], 10.0)
        self.assertEqual(stale["status"], "stale")
        self.assertAlmostEqual(stale["spreadPercent"], 0.0012)
        self.assertAlmostEqual(stale["spreadBps"], 12.0)
        self.assertEqual(fresh["unitConvention"]["spreadPercent"], "decimal_ratio")

    def test_breadth_relative_strength_vix_es_and_event_state_are_operational(self) -> None:
        self.assertEqual(adapt_relative_strength({"relative_to_primary_percent": 0.4}, default_key="qqqRelativeStrength")["state"], "outperforming")
        self.assertEqual(adapt_relative_strength({"changePercent": -0.5}, default_key="iwmRelativeStrength")["state"], "underperforming")
        self.assertEqual(adapt_market_breadth({"advance_decline_ratio": 1.35})["state"], "positive")
        self.assertEqual(adapt_vix_context({"value": 24})["state"], "elevated")
        self.assertEqual(adapt_es_futures_context({"change_percent": -0.25})["trend"], "down")
        event = adapt_scheduled_event_state({"minutes_until_event": 10, "event_name": "FOMC rate decision"})
        self.assertEqual(event["state"], "soon")
        self.assertEqual(event["eventType"], "fomc")

    def test_intraday_volatility_baseline_accepts_percentile_and_expected_ratio_aliases(self) -> None:
        baseline = adapt_intraday_volatility_baseline(
            {
                "atr_percentile": 97,
                "realized_volatility_percentile": 0.82,
                "range_vs_expected": 1.7,
                "volume_vs_expected": 1.4,
                "expected_range": 0.42,
                "expected_volume": 250000,
                "samples": 120,
            }
        )

        self.assertEqual(baseline["calibrationStatus"], "ready")
        self.assertEqual(baseline["atrPercentile"], 0.97)
        self.assertEqual(baseline["realizedVolatilityPercentile"], 0.82)
        self.assertEqual(baseline["currentRangeVsExpected"], 1.7)
        self.assertEqual(baseline["currentVolumeVsExpected"], 1.4)
        self.assertEqual(baseline["sampleSize"], 120)

    def test_market_snapshot_uses_dedicated_context_feed_adapter(self) -> None:
        snapshot = build_regime_market_snapshot(
            {
                "symbol": "SPY",
                "primaryCandles": candles(20),
                "contextFeeds": {
                    "quote_freshness": {"age_ms": 4000, "spread_bps": 8},
                    "breadth": {"advance_decline_ratio": 0.7},
                    "vix": {"last": 31},
                    "vix1d": {"value": 19},
                    "es": {"percent_change": 0.2},
                    "eventState": {"minutesToEvent": 45},
                    "volatility_baseline": {"atrPercentile": 0.8, "realizedVolatilityPercentile": 0.55, "sampleSize": 80},
                },
            }
        )

        self.assertEqual(snapshot.context_feeds["quoteFreshness"]["status"], "fresh")
        self.assertEqual(snapshot.context_feeds["marketBreadth"]["state"], "negative")
        self.assertEqual(snapshot.context_feeds["vix"]["state"], "stress")
        self.assertEqual(snapshot.context_feeds["vix1d"]["state"], "normal")
        self.assertEqual(snapshot.context_feeds["esFutures"]["trend"], "up")
        self.assertEqual(snapshot.context_feeds["scheduledEconomicEvent"]["state"], "elevated")
        self.assertEqual(snapshot.context_feeds["intradayVolatilityBaseline"]["atrPercentile"], 0.8)


if __name__ == "__main__":
    unittest.main()
