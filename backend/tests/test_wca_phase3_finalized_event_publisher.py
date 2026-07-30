from __future__ import annotations

import sqlite3
import unittest
from datetime import timedelta

from backend.app.algorithms.wca.runtime_publisher import WcaFinalizedOneMinuteEventPublisher
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


if __name__ == "__main__":
    unittest.main()
