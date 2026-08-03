from __future__ import annotations

import asyncio
import sqlite3
import unittest
from datetime import UTC, datetime, timedelta

from backend.app.algorithms.wca.runtime_publisher import WcaFinalizedOneMinuteEventPublisher, WcaFinalizedOneMinutePollConfig
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeRepository
from backend.app.algorithms.wca.runtime_supervisor import WcaRuntimeSettings, WcaRuntimeSupervisor
from backend.tests.test_wca_phase2_runtime_state import seeded_repository


class WcaPhase3FinalizedEventPublisherTests(unittest.TestCase):
    def test_one_finalized_candle_creates_one_effective_wca_evaluation(self) -> None:
        repository, snapshot = seeded_repository()
        runtime_repository = WcaRuntimeRepository(repository)
        publication = publish(snapshot, runtime_repository)
        supervisor = supervisor_for(repository, runtime_repository)

        result = supervisor.run_once()

        self.assertTrue(publication.accepted)
        self.assertEqual(result["workers"]["decision_worker"]["status"], "completed")
        self.assertEqual(count_rows(repository, "wca_decisions"), 1)
        self.assertEqual(event_status(repository, publication.event.event_id), "completed")

    def test_replaying_same_event_creates_no_duplicate_decision_or_order(self) -> None:
        repository, snapshot = seeded_repository()
        runtime_repository = WcaRuntimeRepository(repository)
        first = publish(snapshot, runtime_repository)
        supervisor_for(repository, runtime_repository).run_once()
        decision_count = count_rows(repository, "wca_decisions")
        outbox_count = count_rows(repository, "wca_execution_outbox")

        replay = publish(snapshot, runtime_repository)
        supervisor_for(repository, runtime_repository).run_once()

        self.assertTrue(first.accepted)
        self.assertFalse(replay.accepted)
        self.assertEqual(replay.status, "duplicate")
        self.assertEqual(count_rows(repository, "wca_decisions"), decision_count)
        self.assertEqual(count_rows(repository, "wca_execution_outbox"), outbox_count)

    def test_unfinished_candles_are_rejected(self) -> None:
        repository, snapshot = seeded_repository()
        runtime_repository = WcaRuntimeRepository(repository)
        publisher = WcaFinalizedOneMinuteEventPublisher(runtime_repository)
        result = publisher.publish_completed_candle(
            candles=snapshot.candles,
            finalized_candle_timestamp=snapshot.data_timestamp,
            quote=snapshot.quote,
            publication_timestamp=snapshot.data_timestamp - timedelta(seconds=1),
            market_data_source="phase3-feed",
        )

        self.assertFalse(result.accepted)
        self.assertIn("wca.market_event.unfinished_candle", result.reason_codes)
        self.assertEqual(count_rows(repository, "wca_runtime_event_queue"), 0)

    def test_out_of_order_candles_are_handled_deterministically(self) -> None:
        repository, snapshot = seeded_repository()
        runtime_repository = WcaRuntimeRepository(repository)
        latest = publish(snapshot, runtime_repository)
        older_timestamp = snapshot.data_timestamp - timedelta(minutes=1)
        older = publish(snapshot, runtime_repository, finalized_timestamp=older_timestamp, source="phase3-feed-older")

        self.assertTrue(latest.accepted)
        self.assertFalse(older.accepted)
        self.assertEqual(older.status, "rejected")
        self.assertIn("wca.runtime.event.out_of_order", older.reason_codes)

    def test_missing_candle_history_blocks_entries(self) -> None:
        repository, snapshot = seeded_repository()
        runtime_repository = WcaRuntimeRepository(repository)
        short_snapshot = snapshot.model_copy(update={"candles": snapshot.candles[-5:]})
        publication = publish(short_snapshot, runtime_repository)

        supervisor_for(repository, runtime_repository).run_once()
        decision = latest_decision(repository)

        self.assertTrue(publication.accepted)
        self.assertEqual(publication.event.data_readiness_result, "BLOCKED")
        self.assertIn("wca.market_event.core_spy_history_insufficient", publication.event.missing_input_reason_codes)
        self.assertEqual(decision["aggregation"]["post_local_gate_decision"], "HOLD")
        self.assertIn("wca.hard_filter.invalid_or_stale_data", str(decision["hard_filter_results"]))

    def test_no_future_candle_enters_feature_snapshot(self) -> None:
        repository, snapshot = seeded_repository()
        runtime_repository = WcaRuntimeRepository(repository)
        future = snapshot.candles[-1].model_copy(update={"timestamp": snapshot.data_timestamp + timedelta(minutes=1)})
        publication = publish(snapshot.model_copy(update={"candles": (*snapshot.candles, future)}), runtime_repository)

        max_timestamp = max(candle.timestamp for candle in publication.event.snapshot.candles)
        self.assertLessEqual(max_timestamp, snapshot.data_timestamp)
        self.assertNotIn(future.timestamp, {candle.timestamp for candle in publication.event.snapshot.candles})

    def test_api_ui_refreshes_cannot_create_wca_evaluations(self) -> None:
        repository, snapshot = seeded_repository()
        runtime_repository = WcaRuntimeRepository(repository)
        result = publish(snapshot, runtime_repository, triggered_by="api_refresh")

        supervisor_for(repository, runtime_repository).run_once()

        self.assertFalse(result.accepted)
        self.assertIn("wca.market_event.publisher.background_only", result.reason_codes)
        self.assertEqual(count_rows(repository, "wca_runtime_event_queue"), 0)
        self.assertEqual(count_rows(repository, "wca_decisions"), 0)

    def test_events_remain_processable_after_worker_restart(self) -> None:
        repository, snapshot = seeded_repository()
        runtime_repository = WcaRuntimeRepository(repository)
        publication = publish(snapshot, runtime_repository)
        restarted_repository = type(repository)(f"sqlite:///{repository.path}")
        restarted_runtime_repository = WcaRuntimeRepository(restarted_repository)

        result = supervisor_for(restarted_repository, restarted_runtime_repository).run_once()

        self.assertTrue(publication.accepted)
        self.assertEqual(result["workers"]["decision_worker"]["status"], "completed")
        self.assertEqual(count_rows(restarted_repository, "wca_decisions"), 1)

    def test_market_event_contains_no_account_or_inventory_authority(self) -> None:
        repository, snapshot = seeded_repository()
        publication = publish(snapshot, WcaRuntimeRepository(repository))
        payload = publication.event.model_dump(mode="json")

        self.assertEqual(payload["algorithm_id"], "wca")
        self.assertEqual(payload["symbol"], "SPY")
        self.assertEqual(payload["timeframe"], "1Min")
        self.assertNotIn("buying_power", str(payload).lower())
        self.assertNotIn("account_id", str(payload).lower())
        self.assertNotIn("position_quantity", str(payload).lower())


def test_background_poller_publishes_each_missing_completed_minute_in_order() -> None:
    repository, _ = seeded_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    now = datetime(2026, 7, 20, 15, 5, 15, tzinfo=UTC)
    rows = candle_rows(datetime(2026, 7, 20, 13, 25, tzinfo=UTC), 100)
    client = FakeWcaMarketDataClient(rows={"SPY": rows, "QQQ": rows, "IWM": rows}, quote_time=now - timedelta(seconds=1))
    store = FakeWcaCandleStore()
    publisher = WcaFinalizedOneMinuteEventPublisher(
        runtime_repository,
        market_data_client=client,
        candle_store=store,
        config=WcaFinalizedOneMinutePollConfig(fetch_limit=120, max_event_age_seconds=300),
    )
    seed_previous_event(runtime_repository, rows[-4]["timestamp"], rows)

    result = asyncio.run(publisher.poll_once(now=now, market_is_open=True))

    accepted_events = [publication.event for publication in result.publications if publication.accepted]
    assert result.status == "published"
    assert [event.finalized_candle_timestamp for event in accepted_events] == [
        timestamp(rows[-3]["timestamp"]),
        timestamp(rows[-2]["timestamp"]),
        timestamp(rows[-1]["timestamp"]),
    ]
    assert all(event.snapshot.decision_timestamp == now for event in accepted_events)
    assert all("SPY_5Min" in event.snapshot.external_market_data for event in accepted_events)
    assert count_rows(repository, "wca_runtime_event_queue") == 4


def test_background_poller_suppresses_duplicates_after_restart_and_never_publishes_forming_candle() -> None:
    repository, _ = seeded_repository()
    runtime_repository = WcaRuntimeRepository(repository)
    now = datetime(2026, 7, 20, 15, 5, 15, tzinfo=UTC)
    rows = candle_rows(datetime(2026, 7, 20, 13, 30, tzinfo=UTC), 96)
    forming_close = rows[-1]["close"] + 1
    rows.append({**rows[-1], "timestamp": "2026-07-20T15:05:00Z", "close": forming_close, "high": forming_close + 0.5})
    client = FakeWcaMarketDataClient(rows={"SPY": rows, "QQQ": rows, "IWM": rows}, quote_time=now - timedelta(seconds=1))
    store = FakeWcaCandleStore()
    publisher = WcaFinalizedOneMinuteEventPublisher(
        runtime_repository,
        market_data_client=client,
        candle_store=store,
        config=WcaFinalizedOneMinutePollConfig(fetch_limit=120, max_event_age_seconds=300),
    )

    first = asyncio.run(publisher.poll_once(now=now, market_is_open=True))
    restarted = WcaFinalizedOneMinuteEventPublisher(
        WcaRuntimeRepository(type(repository)(f"sqlite:///{repository.path}")),
        market_data_client=client,
        candle_store=store,
        config=WcaFinalizedOneMinutePollConfig(fetch_limit=120, max_event_age_seconds=300),
    )
    second = asyncio.run(restarted.poll_once(now=now + timedelta(seconds=10), market_is_open=True))

    accepted = [publication.event for publication in first.publications if publication.accepted]
    assert accepted
    assert all(event.finalized_candle_timestamp < datetime(2026, 7, 20, 15, 5, tzinfo=UTC) for event in accepted)
    assert second.status == "idle"
    assert "wca.market_event.no_new_finalized_candle" in second.reason_codes
    assert count_rows(repository, "wca_runtime_event_queue") == len({event.event_id for event in accepted})


def publish(snapshot, runtime_repository: WcaRuntimeRepository, *, finalized_timestamp=None, source: str = "phase3-feed", triggered_by: str = "background_publisher"):
    finalized = finalized_timestamp or snapshot.data_timestamp
    quote = snapshot.quote.model_copy(update={"timestamp": finalized}) if snapshot.quote is not None else None
    publisher = WcaFinalizedOneMinuteEventPublisher(runtime_repository)
    return publisher.publish_completed_candle(
        candles=snapshot.candles,
        finalized_candle_timestamp=finalized,
        quote=quote,
        publication_timestamp=finalized + timedelta(seconds=1),
        market_data_source=source,
        triggered_by=triggered_by,
        external_market_data={"QQQ": snapshot.candles, "IWM": snapshot.candles},
        external_input_timestamps={"QQQ": finalized, "IWM": finalized},
        market_breadth_inputs={"advance_decline_ratio": 1.0},
    )


def supervisor_for(repository, runtime_repository):
    return WcaRuntimeSupervisor(
        repository=repository,
        runtime_repository=runtime_repository,
        settings=WcaRuntimeSettings(max_lag_seconds=99_999_999, max_state_age_seconds=120),
        owner_id="phase3-worker",
    )


def count_rows(repository, table: str) -> int:
    with sqlite3.connect(repository.path) as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def event_status(repository, event_id: str) -> str:
    with sqlite3.connect(repository.path) as conn:
        return str(conn.execute("SELECT status FROM wca_runtime_event_queue WHERE event_id = ?", (event_id,)).fetchone()[0])


def latest_decision(repository) -> dict:
    import json

    with sqlite3.connect(repository.path) as conn:
        row = conn.execute("SELECT payload_json FROM wca_decisions ORDER BY created_at DESC LIMIT 1").fetchone()
    assert row is not None
    return json.loads(row[0])


def seed_previous_event(runtime_repository: WcaRuntimeRepository, finalized_timestamp: str, rows: list[dict]) -> None:
    prior = timestamp(finalized_timestamp)
    publisher = WcaFinalizedOneMinuteEventPublisher(runtime_repository)
    result = publisher.publish_completed_candle(
        candles=tuple(candle_from_row(row) for row in rows if timestamp(row["timestamp"]) <= prior),
        finalized_candle_timestamp=prior,
        quote=None,
        publication_timestamp=prior + timedelta(seconds=1),
        market_data_source="alpaca:iex",
        external_market_data={"QQQ": tuple(candle_from_row(row) for row in rows if timestamp(row["timestamp"]) <= prior), "IWM": tuple(candle_from_row(row) for row in rows if timestamp(row["timestamp"]) <= prior)},
        external_input_timestamps={"QQQ": prior, "IWM": prior},
        market_breadth_inputs={"qqq_iwm_relative_strength": 1.0},
        max_event_age_seconds=999_999,
    )
    assert result.accepted


def candle_rows(start: datetime, count: int) -> list[dict]:
    return [
        {
            "provider": "alpaca",
            "feed": "iex",
            "symbol": "SPY",
            "timeframe": "1Min",
            "timestamp": (start + timedelta(minutes=index)).isoformat().replace("+00:00", "Z"),
            "open": 100 + index * 0.01,
            "high": 100.5 + index * 0.01,
            "low": 99.5 + index * 0.01,
            "close": 100.1 + index * 0.01,
            "volume": 1000 + index,
            "trade_count": 10,
            "vwap": 100.05 + index * 0.01,
        }
        for index in range(count)
    ]


def candle_from_row(row: dict):
    from backend.app.algorithms.wca.contracts import WcaCandle

    return WcaCandle(
        timestamp=timestamp(row["timestamp"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        vwap=float(row["vwap"]),
    )


def timestamp(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


class FakeWcaMarketDataClient:
    def __init__(self, *, rows: dict[str, list[dict]], quote_time: datetime) -> None:
        self.rows = rows
        self.quote_time = quote_time

    async def get_bars(self, *, symbol: str, timeframe: str, feed: str, limit: int, start: str | None, end: str | None, sort: str):
        selected = list(self.rows.get(symbol.upper(), ()))
        if end is not None:
            end_at = timestamp(end)
            selected = [row for row in selected if timestamp(row["timestamp"]) <= end_at]
        return selected[-limit:]

    async def get_latest_quote(self, *, symbol: str, feed: str):
        return {"symbol": symbol, "bid": 100, "ask": 100.02, "quoteTimestamp": self.quote_time.isoformat().replace("+00:00", "Z")}


class FakeWcaCandleStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str, str], dict[str, dict]] = {}

    def upsert_many(self, candles: list[dict]) -> None:
        for row in candles:
            key = (str(row["symbol"]).upper(), str(row["timeframe"]), str(row["feed"]))
            self.rows.setdefault(key, {})[str(row["timestamp"])] = dict(row)

    def latest(self, *, symbol: str, timeframe: str, feed: str, limit: int):
        rows = list(self.rows.get((symbol.upper(), timeframe, feed), {}).values())
        return sorted(rows, key=lambda row: row["timestamp"])[-limit:]

    def latest_until(self, *, symbol: str, timeframe: str, feed: str, limit: int, end: str):
        end_at = timestamp(end)
        rows = [row for row in self.rows.get((symbol.upper(), timeframe, feed), {}).values() if timestamp(row["timestamp"]) <= end_at]
        return sorted(rows, key=lambda row: row["timestamp"])[-limit:]


if __name__ == "__main__":
    unittest.main()
