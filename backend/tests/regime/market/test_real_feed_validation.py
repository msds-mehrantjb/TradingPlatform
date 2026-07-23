import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.regime.feed_validation import (
    REGIME_REAL_FEED_VALIDATION_VERSION,
    RealFeedValidationPolicy,
    validate_real_quote_trade_feeds,
)
from backend.app.algorithms.regime.volatility_calibration import INACTIVE_UNTIL_LIVE_PAPER_TRADING


START = datetime(2026, 7, 23, 13, 30, tzinfo=UTC)


class RealQuoteTradeFeedValidationTest(unittest.TestCase):
    def test_clean_live_paper_feed_is_inactive_by_default(self) -> None:
        report = validate_real_quote_trade_feeds(
            quotes=_quotes(),
            trades=_trades(),
            fills=_fills(),
            source_mode="live_paper",
            observed_at=(START + timedelta(seconds=22)).isoformat(),
            generated_at="2026-07-23T00:00:00+00:00",
        )

        self.assertEqual(report["validationVersion"], REGIME_REAL_FEED_VALIDATION_VERSION)
        self.assertEqual(report["validationStatus"], INACTIVE_UNTIL_LIVE_PAPER_TRADING)
        self.assertTrue(report["diagnosticPassed"])
        self.assertFalse(report["validationAppliedToLivePaperTrading"])
        self.assertTrue(report["liquidityFeed"]["passed"])
        self.assertTrue(report["tradeFeed"]["passed"])
        self.assertTrue(report["executionQuality"]["passed"])

    def test_clean_live_paper_feed_can_be_explicitly_applied_later(self) -> None:
        report = validate_real_quote_trade_feeds(
            quotes=_quotes(),
            trades=_trades(),
            fills=_fills(),
            source_mode="live_paper_shadow",
            observed_at=(START + timedelta(seconds=22)).isoformat(),
            allow_inactive=True,
            generated_at="2026-07-23T00:00:00+00:00",
        )

        self.assertEqual(report["validationStatus"], "pass")
        self.assertTrue(report["validationAppliedToLivePaperTrading"])
        self.assertGreaterEqual(report["executionQuality"]["fillRate"], 0.70)
        self.assertLess(report["executionQuality"]["averageSignedSlippageBps"], 4.0)

    def test_crossed_quotes_and_outside_nbbo_trades_fail_closed(self) -> None:
        bad_quotes = _quotes()
        bad_quotes[5] = {**bad_quotes[5], "bid": 100.10, "ask": 100.05}
        bad_trades = _trades()
        bad_trades[3] = {**bad_trades[3], "price": 100.50}

        report = validate_real_quote_trade_feeds(
            quotes=bad_quotes,
            trades=bad_trades,
            fills=_fills(),
            source_mode="live_paper",
            observed_at=(START + timedelta(seconds=22)).isoformat(),
            allow_inactive=True,
            policy=RealFeedValidationPolicy(maximum_locked_or_crossed_quote_rate=0.0, maximum_trade_outside_nbbo_rate=0.0),
            generated_at="2026-07-23T00:00:00+00:00",
        )

        self.assertEqual(report["validationStatus"], "fail")
        self.assertIn("regime.feed_validation.locked_or_crossed_quotes", report["reasonCodes"])
        self.assertIn("regime.feed_validation.trade_prices_outside_nbbo", report["reasonCodes"])

    def test_missing_arrival_quote_and_poor_fill_lifecycle_fail_execution_quality(self) -> None:
        fills = [
            {
                "side": "buy",
                "orderSubmissionTimestamp": (START - timedelta(seconds=10)).isoformat(),
                "fillTimestamp": (START + timedelta(seconds=5)).isoformat(),
                "submittedQuantity": 100,
                "filledQuantity": 20,
                "averageFillPrice": 100.20,
            },
            {
                "side": "buy",
                "orderSubmissionTimestamp": (START + timedelta(seconds=15)).isoformat(),
                "submittedQuantity": 100,
                "filledQuantity": 0,
            },
        ]

        report = validate_real_quote_trade_feeds(
            quotes=_quotes(),
            trades=_trades(),
            fills=fills,
            source_mode="live_paper",
            observed_at=(START + timedelta(seconds=22)).isoformat(),
            allow_inactive=True,
            generated_at="2026-07-23T00:00:00+00:00",
        )

        self.assertEqual(report["validationStatus"], "fail")
        self.assertIn("regime.feed_validation.fill_lacks_arrival_quote", report["reasonCodes"])
        self.assertIn("regime.feed_validation.fill_rate_too_low", report["reasonCodes"])
        self.assertIn("regime.feed_validation.partial_fill_rate_too_high", report["reasonCodes"])

    def test_offline_diagnostic_source_cannot_be_used_as_live_paper_proof(self) -> None:
        report = validate_real_quote_trade_feeds(
            quotes=_quotes(),
            trades=_trades(),
            fills=_fills(),
            source_mode="backtest",
            observed_at=(START + timedelta(seconds=22)).isoformat(),
            allow_inactive=True,
            generated_at="2026-07-23T00:00:00+00:00",
        )

        self.assertEqual(report["validationStatus"], "fail")
        self.assertFalse(report["sourceReady"])
        self.assertIn("regime.feed_validation.live_paper_source_required", report["reasonCodes"])


def _quotes() -> list[dict[str, float | str]]:
    rows = []
    for index in range(25):
        mid = 100.00 + (index * 0.001)
        rows.append(
            {
                "timestamp": (START + timedelta(seconds=index)).isoformat().replace("+00:00", "Z"),
                "bid": mid - 0.01,
                "ask": mid + 0.01,
                "bidSize": 1_000,
                "askSize": 1_000,
            }
        )
    return rows


def _trades() -> list[dict[str, float | str]]:
    return [
        {
            "timestamp": (START + timedelta(seconds=index + 1, milliseconds=100)).isoformat().replace("+00:00", "Z"),
            "price": 100.00 + (index * 0.001),
            "size": 100,
        }
        for index in range(15)
    ]


def _fills() -> list[dict[str, float | str]]:
    return [
        {
            "side": "buy",
            "orderSubmissionTimestamp": (START + timedelta(seconds=3)).isoformat(),
            "fillTimestamp": (START + timedelta(seconds=3, milliseconds=600)).isoformat(),
            "submittedQuantity": 100,
            "filledQuantity": 100,
            "averageFillPrice": 100.018,
        },
        {
            "side": "sell",
            "orderSubmissionTimestamp": (START + timedelta(seconds=8)).isoformat(),
            "fillTimestamp": (START + timedelta(seconds=8, milliseconds=500)).isoformat(),
            "submittedQuantity": 100,
            "filledQuantity": 100,
            "averageFillPrice": 99.998,
        },
    ]


if __name__ == "__main__":
    unittest.main()
