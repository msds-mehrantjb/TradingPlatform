"""Regression cover for the snapshot point-in-time cutoff.

The automatic snapshot fixes its bar-arrival time, then loads SPY, QQQ, IWM and every
breadth component before finally fetching the live quote and last trade. In production
that build took 11-12 seconds, so the quote always carried a timestamp later than the
bar-arrival time it was validated against. Both the producer's own check and the
snapshot builder's zero-tolerance check then flagged legitimately fresh market data as
future-dated, and every single evaluation fail-closed with:

    future_spy_nbbo_timestamp
    voting_ensemble.automatic_snapshot.future_spy_quote
    voting_ensemble.automatic_snapshot.future_spy_last_trade

The cutoff is now the latest moment any input was observed, so a slow build no longer
invalidates its own market data. These tests pin that, and pin that the feed-sanity
guard it replaced still rejects genuinely future-dated quotes.
"""

from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.algorithms.voting_ensemble.finalized_bar_producer import (
    VotingEnsembleAutomaticEvaluationPayloadBuilder,
    VotingEnsembleAutomaticSnapshotError,
)

from test_voting_ensemble_runtime_supervisor import (  # type: ignore[import-not-found]
    MemoryCandleStore,
    finalized_bar_evaluation_command,
    finalized_market_event_from_candle,
    stored_candle,
)


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
BREADTH = ["QQQ", "IWM", "XLK", "XLF", "XLY", "XLP", "XLV", "XLI", "XLE", "XLB", "XLU", "XLRE", "XLC"]

# How far after bar arrival the market-data fetch lands. Production measured 11-12s.
SLOW_BUILD_SECONDS = 12


def iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


class SnapshotObservationCutoffTest(unittest.TestCase):
    def build_payload(self, *, quote_offset: int, trade_offset: int, receipt_offset: int | None = None) -> dict[str, Any]:
        receipt = receipt_offset if receipt_offset is not None else max(quote_offset, trade_offset)
        store = MemoryCandleStore()
        for symbol in ["SPY", *BREADTH]:
            store.upsert_many(
                [stored_candle(NOW - timedelta(minutes=15 - i), symbol=symbol, close=100.0 + i * 0.01) for i in range(15)]
            )
        event = finalized_market_event_from_candle(
            stored_candle(NOW - timedelta(minutes=1)),
            sequence=1,
            received_at=NOW + timedelta(seconds=3),
            finalized_at=NOW + timedelta(seconds=2),
            source_authority="backend.test.finalized_bar",
        )
        command = finalized_bar_evaluation_command(
            {"marketEvent": event.snapshot()},
            symbol="SPY",
            bar_end_timestamp=event.barEndTimestamp,
            settings_hash="settings-a",
            deadline_seconds=20,
        )
        builder = VotingEnsembleAutomaticEvaluationPayloadBuilder(
            candle_store=store,
            control_snapshot_provider=lambda: {
                "requestedPaperTradingEnabled": True,
                "effectivePaperTradingEnabled": True,
                "newEntriesEnabled": True,
                "liveTradingEnabled": False,
                "reasonCodes": ["test.backend_control_allowed"],
            },
            paper_inventory_provider=lambda: {
                "orders": [],
                "fills": [],
                "positions": [],
                "account": {
                    "accountId": "voting_ensemble.paper.default.account",
                    "capitalPartitionId": "voting_ensemble.paper.default",
                    "equity": 100000.0,
                    "buyingPower": 100000.0,
                    "realizedPnlToday": 0.0,
                    "unrealizedPnlToday": 0.0,
                    "dailyNetPnlAfterExitCosts": 0.0,
                    "intradayEquityHigh": 100000.0,
                    "drawdownPercent": 0.0,
                    "openPositionNotional": 0.0,
                    "totalOpenRiskPercent": 0.0,
                    "tradesToday": 0,
                    "sessionDate": NOW.date().isoformat(),
                    "observedAt": iso(NOW + timedelta(seconds=2)),
                    "sourceAuthority": "voting_ensemble_local_paper_account",
                },
            },
            market_status_provider=lambda: {"isOpen": True, "status": "open", "timestamp": iso(NOW)},
            account_snapshot_provider=lambda: {
                "accountId": "broker",
                "equity": 100000.0,
                "buyingPower": 100000.0,
                "observedAt": iso(NOW + timedelta(seconds=2)),
                "sourceAuthority": "broker",
            },
            quote_provider=lambda **_: {
                "provider": "alpaca",
                "feed": "iex",
                "symbol": "SPY",
                "bid": 100.0,
                "ask": 100.01,
                "bidSize": 10,
                "askSize": 12,
                "quoteTimestamp": iso(NOW + timedelta(seconds=quote_offset)),
                "marketDataReceiptTimestamp": iso(NOW + timedelta(seconds=receipt)),
            },
            last_trade_provider=lambda **_: {
                "provider": "alpaca",
                "feed": "iex",
                "symbol": "SPY",
                "price": 100.005,
                "size": 100,
                "tradeTimestamp": iso(NOW + timedelta(seconds=trade_offset)),
                "marketDataReceiptTimestamp": iso(NOW + timedelta(seconds=receipt)),
            },
        )
        return builder.build(command)

    def test_slow_build_no_longer_invalidates_its_own_market_data(self) -> None:
        """The production failure: quote fetched 12s after bar arrival."""
        payload = self.build_payload(quote_offset=SLOW_BUILD_SECONDS, trade_offset=SLOW_BUILD_SECONDS)

        self.assertIsNotNone(payload.get("nbbo"), "nbbo was dropped as future-dated")

    def test_cutoff_advances_to_the_latest_observation(self) -> None:
        payload = self.build_payload(quote_offset=SLOW_BUILD_SECONDS, trade_offset=SLOW_BUILD_SECONDS)

        self.assertEqual(payload["data_timestamp"], iso(NOW + timedelta(seconds=SLOW_BUILD_SECONDS)))

    def test_fast_build_keeps_the_bar_arrival_cutoff(self) -> None:
        """When market data predates bar arrival, the cutoff must not move backwards."""
        payload = self.build_payload(quote_offset=1, trade_offset=1, receipt_offset=2)

        self.assertEqual(payload["data_timestamp"], iso(NOW + timedelta(seconds=3)))

    def test_quote_dated_after_its_own_receipt_is_still_rejected(self) -> None:
        """Feed-sanity guard: a quote cannot legitimately predate its own arrival."""
        with self.assertRaises(VotingEnsembleAutomaticSnapshotError) as caught:
            self.build_payload(quote_offset=120, trade_offset=5, receipt_offset=5)

        codes = " ".join(caught.exception.reason_codes)
        self.assertIn("future_spy_quote", codes)


if __name__ == "__main__":
    unittest.main()
