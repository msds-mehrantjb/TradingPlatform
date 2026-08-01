from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from backend.app.algorithms.meta_strategy.finalized_candle_producer import (
    MetaStrategyFinalizedCandleProducer,
    MetaStrategyFinalizedCandleProducerConfig,
)
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore, build_meta_strategy_settings
from backend.app.database import CandleStore


NOW = datetime(2026, 1, 5, 15, 45, 10, tzinfo=UTC)


class MetaStrategyFinalizedCandleProducerTest(unittest.IsolatedAsyncioTestCase):
    async def test_finalized_one_minute_candle_enqueues_exactly_one_durable_event_and_job(self) -> None:
        producer, jobs, store = producer_fixture(candles=fixture_candles(count=30, end=NOW.replace(second=0)))

        first = await producer.process_symbol("SPY", now=NOW)
        duplicate = await producer.process_symbol("SPY", now=NOW + timedelta(seconds=1))

        self.assertEqual(first.status, "ENQUEUED")
        self.assertEqual(duplicate.status, "DUPLICATE")
        self.assertEqual(duplicate.job_id, first.job_id)
        self.assertEqual(jobs.queue_status(queue_name="finalised_bar_decisions", now=NOW)["queues"]["finalised_bar_decisions"]["pending"], 1)
        self.assertEqual(jobs.queue_status(queue_name="position_management", now=NOW)["queues"]["position_management"]["pending"], 1)
        job = jobs.read_job(first.job_id)
        self.assertIn("meta_strategy:meta_strategy.paper.default:SPY:1m:2026-01-05T15:45:00+00:00:", job.idempotency_key)
        payload = jobs.read_payload(jobs.event_by_id(first.event_id).payload_reference)["payload"]
        self.assertEqual(payload["latencyMeasurements"]["candleFinalizationDelayMs"], 10000)
        self.assertEqual(payload["higherTimeframePolicy"], "derived_point_in_time_from_finalized_one_minute")
        self.assertIsNotNone(payload["derivedHigherTimeframes"]["fiveMinute"])
        self.assertIsNotNone(payload["derivedHigherTimeframes"]["fifteenMinute"])
        self.assertEqual(len(store.latest(symbol="SPY", timeframe="5Min", feed="iex", limit=10)), 6)
        self.assertEqual(len(store.latest(symbol="SPY", timeframe="15Min", feed="iex", limit=10)), 2)

    async def test_sequence_gap_records_data_quality_and_does_not_enqueue_decision_job(self) -> None:
        rows = fixture_candles(count=30, end=NOW.replace(second=0))
        rows.pop(20)
        producer, jobs, _store = producer_fixture(candles=rows)

        result = await producer.process_symbol("SPY", now=NOW)

        self.assertEqual(result.status, "BLOCKED")
        self.assertIn("meta_strategy.candle.sequence_gap", result.reason_codes)
        self.assertEqual(jobs.queue_status(queue_name="finalised_bar_decisions", now=NOW)["queues"]["finalised_bar_decisions"]["pending"], 0)
        quality_events = jobs.operational_events(event_type="finalised_candle_data_quality", limit=5)
        self.assertTrue(any(event["status"] == "SEQUENCE_GAP" for event in quality_events))

    async def test_partial_future_or_stale_candles_do_not_enqueue(self) -> None:
        stale_now = NOW + timedelta(minutes=10)
        producer, jobs, _store = producer_fixture(candles=fixture_candles(count=30, end=NOW.replace(second=0), finalized=False))
        partial = await producer.process_symbol("SPY", now=NOW)

        stale_producer, stale_jobs, _stale_store = producer_fixture(candles=fixture_candles(count=30, end=NOW.replace(second=0)))
        stale = await stale_producer.process_symbol("SPY", now=stale_now)

        self.assertEqual(partial.status, "BLOCKED")
        self.assertEqual(stale.status, "BLOCKED")
        self.assertIn("meta_strategy.candle.stale", stale.reason_codes)
        self.assertEqual(jobs.queue_status(queue_name="finalised_bar_decisions", now=NOW)["queues"]["finalised_bar_decisions"]["pending"], 0)
        self.assertEqual(stale_jobs.queue_status(queue_name="finalised_bar_decisions", now=stale_now)["queues"]["finalised_bar_decisions"]["pending"], 0)


def producer_fixture(*, candles: list[dict]):
    database_url = f"sqlite:///{temp_db_path()}"
    jobs = MetaStrategyJobRepository(database_url)
    settings = MetaStrategySettingsStore(temp_db_path(prefix="meta-strategy-candle-settings"))
    baseline = settings.create_baseline(build_meta_strategy_settings(settings_version=f"settings-{uuid4().hex}"), actor="test")
    settings.activate_settings(baseline.settings_version, actor="test")
    store = CandleStore(SimpleNamespace(database_url=database_url))
    producer = MetaStrategyFinalizedCandleProducer(
        market_data_client=StaticMarketDataClient(candles),
        candle_store=store,
        job_repository=jobs,
        settings_store=settings,
        config=MetaStrategyFinalizedCandleProducerConfig(warmup_bars=30, fetch_limit=60, mode="PAPER"),
    )
    return producer, jobs, store


class StaticMarketDataClient:
    def __init__(self, candles: list[dict]) -> None:
        self.candles = candles

    async def get_bars(self, *, symbol: str, timeframe: str, feed: str, limit: int, start: str | None, end: str | None, sort: str):
        return list(self.candles[-limit:])


def fixture_candles(*, count: int, end: datetime, finalized: bool = True) -> list[dict]:
    rows = []
    for index in range(count):
        timestamp = end - timedelta(minutes=count - index - 1)
        rows.append(
            {
                "provider": "fixture",
                "feed": "iex",
                "symbol": "SPY",
                "timeframe": "1Min",
                "timestamp": timestamp.isoformat(),
                "open": 100.0 + index * 0.01,
                "high": 100.2 + index * 0.01,
                "low": 99.9 + index * 0.01,
                "close": 100.1 + index * 0.01,
                "volume": 1000 + index,
                "trade_count": 10,
                "vwap": 100.05 + index * 0.01,
                "finalized": finalized,
            }
        )
    return rows


def temp_db_path(*, prefix: str = "meta-strategy-candle-producer") -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"{prefix}-{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
