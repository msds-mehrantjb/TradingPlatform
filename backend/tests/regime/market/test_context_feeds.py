from __future__ import annotations

import unittest

from backend.app.algorithms.regime.context_feeds import (
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
        self.assertEqual(feeds["esFutures"]["trend"], "unknown")
        self.assertEqual(feeds["scheduledEconomicEvent"]["state"], "unknown")

    def test_quote_freshness_accepts_quotes_and_age_aliases(self) -> None:
        fresh = adapt_quote_freshness({"quote_age_ms": 5000, "bid": 99.95, "ask": 100.05})
        stale = adapt_quote_freshness({"ageMs": 20000, "spreadBps": 12})

        self.assertEqual(fresh["status"], "fresh")
        self.assertAlmostEqual(fresh["spreadPercent"], 0.001)
        self.assertEqual(stale["status"], "stale")
        self.assertAlmostEqual(stale["spreadPercent"], 0.0012)

    def test_breadth_relative_strength_vix_es_and_event_state_are_operational(self) -> None:
        self.assertEqual(adapt_relative_strength({"relative_to_primary_percent": 0.4}, default_key="qqqRelativeStrength")["state"], "outperforming")
        self.assertEqual(adapt_relative_strength({"changePercent": -0.5}, default_key="iwmRelativeStrength")["state"], "underperforming")
        self.assertEqual(adapt_market_breadth({"advance_decline_ratio": 1.35})["state"], "positive")
        self.assertEqual(adapt_vix_context({"value": 24})["state"], "elevated")
        self.assertEqual(adapt_es_futures_context({"change_percent": -0.25})["trend"], "down")
        self.assertEqual(adapt_scheduled_event_state({"minutes_until_event": 10})["state"], "soon")

    def test_market_snapshot_uses_dedicated_context_feed_adapter(self) -> None:
        snapshot = build_regime_market_snapshot(
            {
                "symbol": "SPY",
                "primaryCandles": candles(20),
                "contextFeeds": {
                    "quote_freshness": {"age_ms": 4000, "spread_bps": 8},
                    "breadth": {"advance_decline_ratio": 0.7},
                    "vix": {"last": 31},
                    "es": {"percent_change": 0.2},
                    "eventState": {"minutesToEvent": 45},
                },
            }
        )

        self.assertEqual(snapshot.context_feeds["quoteFreshness"]["status"], "fresh")
        self.assertEqual(snapshot.context_feeds["marketBreadth"]["state"], "negative")
        self.assertEqual(snapshot.context_feeds["vix"]["state"], "stress")
        self.assertEqual(snapshot.context_feeds["esFutures"]["trend"], "up")
        self.assertEqual(snapshot.context_feeds["scheduledEconomicEvent"]["state"], "elevated")


if __name__ == "__main__":
    unittest.main()
