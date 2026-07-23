import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.regime.classifier import classify_market_regime
from backend.app.algorithms.regime.market_snapshot import build_regime_market_snapshot


def july_weekday_candles() -> list[dict]:
    rows = []
    price = 100.0
    start = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)
    for index in range(70):
        price += 0.05
        timestamp = (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z")
        rows.append(
            {
                "timestamp": timestamp,
                "open": price - 0.02,
                "high": price + 0.05,
                "low": price - 0.05,
                "close": price,
                "volume": 120000,
            }
        )
    return rows


class SessionCalendarEvidenceTest(unittest.TestCase):
    def test_classifier_exposes_dst_aware_exchange_calendar_evidence(self):
        market = build_regime_market_snapshot({"symbol": "SPY", "primaryCandles": july_weekday_candles()})
        classification = classify_market_regime(market)

        session = classification.evidence["sessionEvidence"]
        self.assertEqual(classification.axes.session, "midday")
        self.assertEqual(session["calendar"], "NYSE/Nasdaq DST-aware calendar")
        self.assertEqual(session["sessionDate"], "2026-07-23")
        self.assertEqual(session["reason"], "regular_exchange_session")


if __name__ == "__main__":
    unittest.main()
