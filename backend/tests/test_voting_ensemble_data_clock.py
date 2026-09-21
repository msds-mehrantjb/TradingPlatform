"""The Voting Ensemble live pipeline runs on a data clock.

Alpaca's free plan serves full-market data fifteen minutes late. Paper trading on it
means every decision, gate, fill and session event is measured against a clock that is
that far behind the wall, while the gates keep their live tolerances. These tests pin the
clock itself, the as-of market data, the single-request intake, and the session handling
that a multi-day paper run depends on.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.app.algorithms.voting_ensemble import data_clock
from backend.app.algorithms.voting_ensemble.finalized_bar_producer import (
    MULTI_SYMBOL_POLL_LOOKBACK,
    MULTI_SYMBOL_WARM_UP_LOOKBACK,
    VotingEnsembleFinalizedBarEventStore,
    VotingEnsembleFinalizedBarProducer,
    VotingEnsembleFinalizedBarProducerConfig,
)
from backend.app.algorithms.voting_ensemble.local_paper_account import trading_session_date
from backend.app.algorithms.voting_ensemble.market_data import VotingEnsembleDelayedMarketDataClient
from backend.app.algorithms.voting_ensemble.paper_execution import (
    VotingEnsemblePaperExecutionQueue,
    VotingEnsemblePaperExecutionRepository,
    VotingEnsemblePaperExecutionRuntime,
)
from backend.app.algorithms.voting_ensemble.runtime.commands import finalized_bar_evaluation_command
from backend.app.algorithms.voting_ensemble.runtime_supervisor import VotingEnsembleRuntimeSupervisor
from backend.app.domain.models import Signal
from backend.tests.test_voting_ensemble_automatic_paper_execution import NOW, local_engine_intent, seed_local_quote
from backend.tests.test_voting_ensemble_local_paper_exits import engine_with_repository, open_long, order

DELAY = {data_clock.DATA_DELAY_ENV: "960", data_clock.MARKET_DATA_FEED_ENV: "sip"}
NO_DELAY = {data_clock.DATA_DELAY_ENV: "0", data_clock.MARKET_DATA_FEED_ENV: ""}
NO_FEES = {
    "VOTING_ENSEMBLE_LOCAL_PAPER_INITIAL_CASH": "100000",
    "VOTING_ENSEMBLE_LOCAL_PAPER_FEE_PER_SHARE": "0",
    "VOTING_ENSEMBLE_LOCAL_PAPER_FLAT_FEE_PER_FILL": "0",
}


class DataClockTest(unittest.TestCase):
    def test_the_default_is_the_live_clock_and_feed(self) -> None:
        with patch.dict("os.environ", NO_DELAY):
            self.assertFalse(data_clock.delayed())
            self.assertLess(abs((data_clock.data_now() - datetime.now(UTC)).total_seconds()), 1)
            self.assertEqual(data_clock.market_data_feed(), "iex")

    def test_a_configured_delay_moves_the_clock_back(self) -> None:
        with patch.dict("os.environ", DELAY):
            self.assertTrue(data_clock.delayed())
            lag = (datetime.now(UTC) - data_clock.data_now()).total_seconds()
            self.assertAlmostEqual(lag, 960, delta=1)
            self.assertEqual(data_clock.market_data_feed(), "sip")

    def test_a_malformed_or_negative_delay_is_no_delay(self) -> None:
        for raw in ("soon", "-60"):
            with patch.dict("os.environ", {data_clock.DATA_DELAY_ENV: raw}):
                self.assertFalse(data_clock.delayed())

    def test_command_deadlines_are_on_the_data_clock(self) -> None:
        # The worker expires a command whose deadline has passed on the data clock; if the
        # command were stamped on the wall clock, every delayed bar would look fresh
        # forever, and the other way round every bar would expire on arrival.
        with patch.dict("os.environ", DELAY):
            command = finalized_bar_evaluation_command(
                {},
                symbol="SPY",
                bar_end_timestamp=NOW,
                settings_hash="settings",
                deadline_seconds=20,
            )
        lag = (datetime.now(UTC) - command.createdAt).total_seconds()
        self.assertAlmostEqual(lag, 960, delta=2)
        self.assertEqual(command.deadlineAt - command.createdAt, timedelta(seconds=20))


class AsOfMarketDataTest(unittest.TestCase):
    def client(self) -> tuple[VotingEnsembleDelayedMarketDataClient, SimpleNamespace]:
        settings = SimpleNamespace(has_alpaca_credentials=True, alpaca_data_base_url="https://data.example", alpaca_key_id="k", alpaca_secret_key="s")
        alpaca = SimpleNamespace(
            settings=settings,
            get_latest_quote_sync=lambda **_: {"source": "live-quote"},
            get_latest_trade_sync=lambda **_: {"source": "live-trade"},
        )
        return VotingEnsembleDelayedMarketDataClient(alpaca), alpaca

    def test_without_delay_the_live_endpoints_are_used(self) -> None:
        client, _ = self.client()
        with patch.dict("os.environ", NO_DELAY):
            self.assertEqual(client.get_latest_quote_sync(symbol="SPY", feed="iex"), {"source": "live-quote"})
            self.assertEqual(client.get_latest_trade_sync(symbol="SPY", feed="iex"), {"source": "live-trade"})

    def test_with_delay_the_quote_is_the_last_one_at_the_data_clock(self) -> None:
        client, _ = self.client()
        seen: dict = {}

        def as_of(path, key, *, feed, at):
            seen.update(path=path, key=key, feed=feed, at=at)
            return {"t": (at - timedelta(seconds=1)).isoformat(), "bp": 100.0, "ap": 100.02, "bs": 3, "as": 4}

        with patch.dict("os.environ", DELAY), patch.object(client, "_latest_as_of", side_effect=as_of):
            quote = client.get_latest_quote_sync(symbol="SPY", feed="sip")

        self.assertEqual(seen["path"], "stocks/SPY/quotes")
        self.assertEqual(seen["feed"], "sip")
        self.assertAlmostEqual((datetime.now(UTC) - seen["at"]).total_seconds(), 960, delta=2)
        assert quote is not None
        receipt = datetime.fromisoformat(quote["marketDataReceiptTimestamp"].replace("Z", "+00:00"))
        quoted = datetime.fromisoformat(quote["quoteTimestamp"].replace("Z", "+00:00"))
        # Receipt is on the data clock, and the quote is never after its own receipt.
        self.assertEqual(receipt, seen["at"])
        self.assertLessEqual(quoted, receipt)
        self.assertEqual(quote["bid"], 100.0)
        self.assertEqual(quote["source"], "alpaca_quote_as_of_data_clock")

    def test_with_delay_the_trade_is_the_last_one_at_the_data_clock(self) -> None:
        client, _ = self.client()
        with patch.dict("os.environ", DELAY), patch.object(
            client,
            "_latest_as_of",
            side_effect=lambda path, key, *, feed, at: {"t": at.isoformat(), "p": 100.01, "s": 50} if key == "trades" else None,
        ):
            trade = client.get_latest_trade_sync(symbol="SPY", feed="sip")
        assert trade is not None
        self.assertEqual(trade["price"], 100.01)
        self.assertEqual(trade["tradeTimestamp"], trade["marketDataReceiptTimestamp"])

    def test_no_quote_at_the_data_clock_is_none(self) -> None:
        client, _ = self.client()
        with patch.dict("os.environ", DELAY), patch.object(client, "_latest_as_of", return_value=None):
            self.assertIsNone(client.get_latest_quote_sync(symbol="SPY", feed="sip"))


BAR_END = datetime(2026, 9, 17, 14, 0, tzinfo=UTC)


def bars(symbol: str, count: int, *, end: datetime) -> list[dict]:
    return [
        {
            "provider": "alpaca",
            "feed": "sip",
            "symbol": symbol,
            "timeframe": "1Min",
            "timestamp": (end - timedelta(minutes=count - index)).isoformat().replace("+00:00", "Z"),
            "open": 100.0,
            "high": 100.5,
            "low": 99.5,
            "close": 100.2,
            "volume": 1000,
        }
        for index in range(count)
    ]


class MultiSymbolClient:
    def __init__(self) -> None:
        self.requests: list[dict] = []

    async def get_bars(self, **kwargs):  # pragma: no cover - must not be used
        raise AssertionError("a multi-symbol client must not be polled symbol by symbol")

    async def get_bars_multi(self, *, symbols, timeframe, feed, start, end):
        self.requests.append({"symbols": list(symbols), "feed": feed, "start": start, "end": end})
        return {symbol: bars(symbol, 5, end=BAR_END) for symbol in symbols}


class CollectingStore:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def upsert_many(self, rows):
        self.rows.extend(rows)


class SingleRequestIntakeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.client = MultiSymbolClient()
        self.store = CollectingStore()
        self.published: list = []
        self.producer = VotingEnsembleFinalizedBarProducer(
            market_data_client=self.client,
            candle_store=self.store,
            publish_event=lambda event, settings_hash, deadline: self.published.append(event) or {"accepted": True, "jobId": "job", "reasonCodes": []},
            event_store=VotingEnsembleFinalizedBarEventStore(Path(self._tmp.name) / "events.json"),
            config=VotingEnsembleFinalizedBarProducerConfig(feed="sip"),
        )

    def test_every_stream_comes_in_one_request_per_poll(self) -> None:
        now = BAR_END + timedelta(seconds=3)
        result = asyncio.run(self.producer.poll_once(now=now))

        self.assertEqual(len(self.client.requests), 1)
        request = self.client.requests[0]
        self.assertEqual(request["symbols"][0], "SPY")
        self.assertEqual(set(request["symbols"][1:]), set(self.producer.config.auxiliary_symbols))
        self.assertEqual(request["feed"], "sip")
        self.assertEqual(request["end"], now)
        self.assertEqual(result[0]["status"], "enqueued")
        self.assertEqual(len(self.published), 1)
        self.assertEqual({row["symbol"] for row in self.store.rows}, {"SPY", *self.producer.config.auxiliary_symbols})

    def test_the_first_poll_warms_history_and_later_polls_fetch_minutes(self) -> None:
        now = BAR_END + timedelta(seconds=3)
        asyncio.run(self.producer.poll_once(now=now))
        asyncio.run(self.producer.poll_once(now=now + timedelta(seconds=2)))

        self.assertEqual(self.client.requests[0]["start"], now - MULTI_SYMBOL_WARM_UP_LOOKBACK)
        self.assertEqual(self.client.requests[1]["start"], now + timedelta(seconds=2) - MULTI_SYMBOL_POLL_LOOKBACK)

    def test_a_failed_request_falls_back_to_the_candle_store(self) -> None:
        async def failing(**kwargs):
            raise RuntimeError("429 too many requests")

        self.client.get_bars_multi = failing
        self.store.latest_until = lambda **kwargs: bars("SPY", 5, end=BAR_END)
        result = asyncio.run(self.producer.poll_once(now=BAR_END + timedelta(seconds=3)))

        self.assertEqual(result[0]["status"], "enqueued")


class TradingSessionTest(unittest.TestCase):
    def test_session_dates_are_new_york_dates(self) -> None:
        # 01:30 UTC is still the previous evening in New York.
        self.assertEqual(trading_session_date(datetime(2026, 9, 18, 1, 30, tzinfo=UTC)), date(2026, 9, 17))
        self.assertEqual(trading_session_date(datetime(2026, 9, 17, 14, 0, tzinfo=UTC)), date(2026, 9, 17))

    def test_a_new_session_starts_daily_pnl_and_the_intraday_high_from_zero(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_long(engine, repository, quantity=10)
            # Take profit at the target: +15 realized on day one.
            seed_local_quote(repository, bid=101.5, ask=101.55, bid_size=10, observed_at=NOW + timedelta(minutes=5))
            engine.refresh_order("long-entry-target")
            day_one = repository.local_account_snapshot(observed_at=NOW + timedelta(minutes=5))

            next_day = NOW + timedelta(days=1)
            rolled = repository.inventory_ledger.roll_trading_session(observed_at=next_day)
            again = repository.inventory_ledger.roll_trading_session(observed_at=next_day + timedelta(minutes=1))

        self.assertEqual(day_one["realizedPnlToday"], 15.0)
        self.assertEqual(day_one["tradesToday"], 1)
        assert rolled is not None
        self.assertEqual(rolled["sessionDate"], trading_session_date(next_day).isoformat())
        self.assertEqual(rolled["realizedPnl"], 15.0)
        self.assertEqual(rolled["realizedPnlToday"], 0.0)
        self.assertEqual(rolled["dailyNetPnl"], 0.0)
        self.assertEqual(rolled["tradesToday"], 0)
        self.assertEqual(rolled["intradayEquityHigh"], rolled["equity"])
        self.assertEqual(rolled["drawdownDollars"], 0.0)
        self.assertIsNone(again, "a session rolls once")

    def test_a_fill_on_the_new_session_counts_only_that_session(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_long(engine, repository, quantity=10)
            seed_local_quote(repository, bid=101.5, ask=101.55, bid_size=10, observed_at=NOW + timedelta(minutes=5))
            engine.refresh_order("long-entry-target")

            next_day = NOW + timedelta(days=1)
            seed_local_quote(repository, bid=100.0, ask=100.0, ask_size=10, observed_at=next_day)
            intent = local_engine_intent(client_order_id="day-two", quantity=10, limit_price=100.0)
            intent.createdAt = next_day
            ack = engine.submit_order(intent)
            self.assertEqual(ack.status, "OPEN")
            engine.refresh_order("day-two")
            seed_local_quote(repository, bid=99.0, ask=99.05, bid_size=10, observed_at=next_day + timedelta(minutes=5))
            engine.refresh_order("day-two-stop")
            account = repository.local_account_snapshot(observed_at=next_day + timedelta(minutes=5))

        self.assertEqual(account["realizedPnl"], 5.0)
        self.assertEqual(account["realizedPnlToday"], -10.0)
        self.assertEqual(account["tradesToday"], 1)


class LocalFillTimeTest(unittest.TestCase):
    def test_a_stop_is_stamped_when_it_fills_not_when_the_entry_was_placed(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            engine, repository = engine_with_repository()
            open_long(engine, repository, quantity=3)
            later = NOW + timedelta(hours=2)
            seed_local_quote(repository, bid=99.0, ask=99.05, bid_size=3, observed_at=later)
            fill = engine.refresh_order("long-entry-stop")
            entry = [item for item in repository.inventory_snapshot()["fills"] if item["clientOrderId"] == "long-entry"][0]

        assert fill is not None
        self.assertEqual(fill.filledAt, later)
        self.assertEqual(entry["filledAt"], NOW.isoformat().replace("+00:00", "Z"))


class EndOfDayTest(unittest.TestCase):
    def runtime(self) -> tuple[VotingEnsemblePaperExecutionRuntime, VotingEnsemblePaperExecutionRepository]:
        repository = VotingEnsemblePaperExecutionRepository()
        runtime = VotingEnsemblePaperExecutionRuntime(repository=repository, queue=VotingEnsemblePaperExecutionQueue(), auto_start=False)
        repository.local_account_snapshot(observed_at=NOW)
        return runtime, repository

    def test_the_supervisor_records_the_clock_that_flattening_reads(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            runtime, repository = self.runtime()
            repository.inventory_ledger.apply_fill(
                client_order_id="eod-entry",
                order_intent_id="intent-eod-entry",
                symbol="SPY",
                side=Signal.BUY,
                requested_quantity=3,
                fill_price=100.0,
                filled_at=NOW,
            )
            close = NOW + timedelta(minutes=4)
            seed_local_quote(repository, bid=100.5, ask=100.55, bid_size=3, observed_at=NOW)
            supervisor = SimpleNamespace(
                paper_execution_runtime=runtime,
                market_clock_provider=lambda: {"isOpen": True, "nextClose": close.isoformat().replace("+00:00", "Z")},
            )

            VotingEnsembleRuntimeSupervisor._record_local_market_clock(supervisor, NOW)
            result = runtime.run_local_position_order_maintenance(evaluated_at=NOW)
            inventory = runtime.inventory_snapshot()

        self.assertEqual(result["eodExitsSubmitted"], 1)
        self.assertEqual(inventory["positions"], [])

    def test_the_end_of_day_entry_block_lifts_once_the_window_has_passed(self) -> None:
        with patch.dict("os.environ", NO_FEES):
            runtime, repository = self.runtime()
            runtime.update_local_market_clock({"nextClose": (NOW + timedelta(minutes=4)).isoformat().replace("+00:00", "Z")}, observed_at=NOW)
            runtime.run_local_position_order_maintenance(evaluated_at=NOW)
            blocked = repository.read_snapshot("local_entry_control.end_of_day")["newEntriesAllowed"]

            tomorrow_close = NOW + timedelta(days=1, hours=1)
            runtime.update_local_market_clock({"nextClose": tomorrow_close.isoformat().replace("+00:00", "Z")}, observed_at=NOW + timedelta(hours=18))
            runtime.run_local_position_order_maintenance(evaluated_at=NOW + timedelta(hours=18))
            lifted = repository.read_snapshot("local_entry_control.end_of_day")["newEntriesAllowed"]

        self.assertFalse(blocked)
        self.assertTrue(lifted)


class DataClockMarketCalendarTest(unittest.TestCase):
    def test_a_delayed_clock_reads_the_exchange_calendar_at_the_data_clock(self) -> None:
        supervisor = SimpleNamespace(settings=SimpleNamespace(has_alpaca_credentials=True))
        wall = datetime(2026, 9, 17, 20, 10, tzinfo=UTC)  # 16:10 New York: the real session is over
        with patch.dict("os.environ", DELAY), patch.object(data_clock, "datetime") as clock:
            clock.now.return_value = wall
            status = VotingEnsembleRuntimeSupervisor._default_market_clock(supervisor)

        # The data clock is at 15:54 New York, so its session is still open.
        self.assertTrue(status["isOpen"])
        self.assertEqual(status["nextClose"], "2026-09-17T20:00:00Z")
        self.assertEqual(status["sourceAuthority"], "voting_ensemble.data_clock_calendar")


if __name__ == "__main__":
    unittest.main()
