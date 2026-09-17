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
    def build_payload(
        self,
        *,
        quote_offset: float,
        trade_offset: float,
        receipt_offset: float | None = None,
        minutes_missing: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        receipt = receipt_offset if receipt_offset is not None else max(quote_offset, trade_offset)
        store = MemoryCandleStore()
        for symbol in ["SPY", *BREADTH]:
            # Trailing minutes with no bar, as IEX leaves them when a symbol does not trade.
            missing = (minutes_missing or {}).get(symbol, 0)
            store.upsert_many(
                [stored_candle(NOW - timedelta(minutes=15 - i), symbol=symbol, close=100.0 + i * 0.01) for i in range(15 - missing)]
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

    def test_exchange_clock_slightly_ahead_of_the_local_clock_is_accepted(self) -> None:
        """A local clock 0.9 s slow made every fresh quote look future-dated."""
        payload = self.build_payload(quote_offset=5.5, trade_offset=5.5, receipt_offset=4)

        self.assertIsNotNone(payload.get("nbbo"), "nbbo was dropped as future-dated")
        self.assertEqual(payload["market_context"]["automaticRuntimeSnapshot"]["dataReadiness"]["ready"], True)

    def test_exchange_clock_far_ahead_of_the_local_clock_is_still_rejected(self) -> None:
        with self.assertRaises(VotingEnsembleAutomaticSnapshotError) as caught:
            self.build_payload(quote_offset=7, trade_offset=7, receipt_offset=4)

        self.assertIn("future_spy_quote", " ".join(caught.exception.reason_codes))

    def test_auxiliary_stream_without_a_bar_this_minute_is_accepted(self) -> None:
        """IEX leaves minutes empty for thinly traded sector ETFs."""
        payload = self.build_payload(quote_offset=3, trade_offset=3, minutes_missing={"XLRE": 1, "QQQ": 1})

        self.assertIsNotNone(payload.get("nbbo"))

    def test_index_stream_older_than_the_age_limit_still_fails_closed(self) -> None:
        with self.assertRaises(VotingEnsembleAutomaticSnapshotError) as caught:
            self.build_payload(quote_offset=3, trade_offset=3, minutes_missing={"QQQ": 2})

        self.assertIn("voting_ensemble.automatic_snapshot.qqq_stale_or_unsynchronized", caught.exception.reason_codes)

    def test_stale_sector_etfs_are_left_out_of_breadth(self) -> None:
        """Eight of eleven fresh sector ETFs meets the breadth strategy's own coverage floor."""
        stale = {"XLRE": 2, "XLC": 3, "XLB": 4}
        payload = self.build_payload(quote_offset=3, trade_offset=3, minutes_missing=stale)

        components = payload["breadth_components"]
        self.assertEqual(len(components), 8)
        self.assertFalse(set(stale) & set(components))
        self.assertEqual(payload["external_breadth_feed"]["componentCount"], 8)

    def test_too_few_fresh_sector_etfs_fails_closed(self) -> None:
        stale = {"XLRE": 2, "XLC": 3, "XLB": 4, "XLV": 2}
        with self.assertRaises(VotingEnsembleAutomaticSnapshotError) as caught:
            self.build_payload(quote_offset=3, trade_offset=3, minutes_missing=stale)

        codes = caught.exception.reason_codes
        self.assertIn("voting_ensemble.automatic_snapshot.breadth_coverage_insufficient", codes)
        for symbol in stale:
            self.assertIn(f"voting_ensemble.automatic_snapshot.{symbol.lower()}_stale_or_unsynchronized", codes)

    def test_spy_last_trade_twenty_seconds_old_is_accepted(self) -> None:
        """IEX can go more than ten seconds without a SPY print while the quote is fresh."""
        payload = self.build_payload(quote_offset=3, trade_offset=-17, receipt_offset=3)

        self.assertIsNotNone(payload.get("nbbo"))

    def test_spy_last_trade_older_than_thirty_seconds_still_fails_closed(self) -> None:
        with self.assertRaises(VotingEnsembleAutomaticSnapshotError) as caught:
            self.build_payload(quote_offset=3, trade_offset=-30, receipt_offset=3)

        self.assertIn("voting_ensemble.automatic_snapshot.stale_spy_last_trade", caught.exception.reason_codes)

    def test_the_primary_symbol_still_needs_this_exact_minute(self) -> None:
        with self.assertRaises(VotingEnsembleAutomaticSnapshotError) as caught:
            self.build_payload(quote_offset=3, trade_offset=3, minutes_missing={"SPY": 1})

        self.assertIn(
            "voting_ensemble.automatic_snapshot.spy_finalized_one_minute_candle_missing_or_unsynchronized",
            caught.exception.reason_codes,
        )


if __name__ == "__main__":
    unittest.main()
