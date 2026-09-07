from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from backend.app.algorithms.voting_ensemble.finalized_bar_producer import (
    VotingEnsembleFinalizedBarEventStore,
    VotingEnsembleFinalizedBarProducer,
    VotingEnsembleFinalizedBarProducerConfig,
)


BAR_END = datetime(2026, 9, 8, 14, 0, tzinfo=UTC)


def candles(count: int, *, symbol: str) -> list[dict]:
    rows = []
    for index in range(count):
        start = BAR_END - timedelta(minutes=count - index)
        rows.append(
            {
                "symbol": symbol,
                "timeframe": "1Min",
                "feed": "iex",
                "timestamp": start.isoformat().replace("+00:00", "Z"),
                "open": 100.0,
                "high": 100.5,
                "low": 99.5,
                "close": 100.2,
                "volume": 1000,
            }
        )
    return rows


class RecordingClient:
    """Counts requests and records the order and concurrency they arrive in."""

    def __init__(self, *, delay: float = 0.0) -> None:
        self.delay = delay
        self.order: list[str] = []
        self.calls: list[str] = []
        self.in_flight = 0
        self.peak_in_flight = 0

    async def get_bars(self, *, symbol, timeframe, feed, limit, start, end, sort):
        self.calls.append(symbol)
        self.in_flight += 1
        self.peak_in_flight = max(self.peak_in_flight, self.in_flight)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            self.order.append(symbol)
            return candles(60, symbol=symbol)
        finally:
            self.in_flight -= 1


class CollectingStore:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def upsert_many(self, rows):
        self.rows.extend(rows)

    def range(self, **kwargs):
        return []


def producer(client, *, store_path, config=None, published=None):
    def publish(event, settings_hash, deadline_seconds):
        if published is not None:
            published.append(event)
        return {"accepted": True, "jobId": "job-1", "deduplicated": False, "reasonCodes": []}

    return VotingEnsembleFinalizedBarProducer(
        market_data_client=client,
        candle_store=CollectingStore(),
        publish_event=publish,
        # An explicit path: the default is the live runtime event store, which would make
        # these tests share deduplication state with each other and with the running app.
        event_store=VotingEnsembleFinalizedBarEventStore(store_path),
        config=config or VotingEnsembleFinalizedBarProducerConfig(),
    )


class IntakeLatencyTest(unittest.TestCase):
    """The bar has twenty seconds from its close; the intake must not spend them.

    The auxiliary streams were fetched one after another and the primary symbol last, so
    the deadline clock started on a bar that was already several seconds old.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.store_path = Path(self._tmp.name) / "finalized_bar_events.json"

    def test_the_primary_symbol_is_not_queued_behind_the_auxiliary_sweep(self) -> None:
        client = RecordingClient(delay=0.02)
        result = asyncio.run(producer(client, store_path=self.store_path).poll_once(now=BAR_END + timedelta(seconds=3)))

        self.assertEqual(len(result), 1)
        # Every symbol this poll needed went out together, so the primary is not last.
        self.assertGreater(client.peak_in_flight, 1)
        self.assertIn("SPY", client.calls)

    def test_a_poll_fetches_each_symbol_once(self) -> None:
        client = RecordingClient()
        asyncio.run(producer(client, store_path=self.store_path).poll_once(now=BAR_END + timedelta(seconds=3)))

        self.assertEqual(len(client.calls), len(set(client.calls)))

    def test_auxiliary_streams_are_not_refetched_on_every_poll(self) -> None:
        client = RecordingClient()
        instance = producer(client, store_path=self.store_path)
        started = BAR_END + timedelta(seconds=3)

        asyncio.run(instance.poll_once(now=started))
        first = len(client.calls)
        asyncio.run(instance.poll_once(now=started + timedelta(seconds=2)))
        second = len(client.calls) - first

        # First poll warms all fourteen; the next one costs a single request.
        self.assertEqual(first, 1 + len(instance.config.auxiliary_symbols))
        self.assertEqual(second, 1)
        self.assertEqual(client.calls[first:], ["SPY"])

    def test_auxiliary_streams_refresh_once_their_cadence_elapses(self) -> None:
        client = RecordingClient()
        instance = producer(client, store_path=self.store_path)
        started = BAR_END + timedelta(seconds=3)

        asyncio.run(instance.poll_once(now=started))
        before = len(client.calls)
        due = started + timedelta(seconds=instance.config.auxiliary_refresh_seconds)
        asyncio.run(instance.poll_once(now=due))

        self.assertEqual(len(client.calls) - before, 1 + len(instance.config.auxiliary_symbols))

    def test_a_failed_auxiliary_fetch_does_not_stop_the_bar(self) -> None:
        class Flaky(RecordingClient):
            async def get_bars(self, *, symbol, **kwargs):
                if symbol != "SPY":
                    raise RuntimeError("auxiliary feed down")
                return await super().get_bars(symbol=symbol, **kwargs)

        published: list = []
        result = asyncio.run(producer(Flaky(), store_path=self.store_path, published=published).poll_once(now=BAR_END + timedelta(seconds=3)))

        self.assertEqual(result[0]["status"], "enqueued")
        self.assertEqual(len(published), 1)

    def test_the_deadline_and_finalization_delay_are_unchanged(self) -> None:
        # The fix is the intake, not the deadline. If these move, the argument changes.
        config = VotingEnsembleFinalizedBarProducerConfig()
        self.assertEqual(config.decision_deadline_seconds, 20)
        self.assertEqual(config.finalization_delay_seconds, 2)
        self.assertLessEqual(config.poll_seconds, 2.0)


if __name__ == "__main__":
    unittest.main()
