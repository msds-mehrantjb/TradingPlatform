from __future__ import annotations

import time
import unittest
from datetime import UTC, datetime

import backend.app.algorithms.voting_ensemble.service as service_module
from backend.app.algorithms.voting_ensemble.execution_adapter import VotingEnsembleExecutionAdapter
from backend.app.algorithms.voting_ensemble.intelligence_capture import (
    VOTING_ENSEMBLE_CAPTURE_NAMESPACE,
    VOTING_ENSEMBLE_CAPTURE_OVERFLOW_POLICY,
    VotingEnsembleCaptureStore,
    VotingEnsembleCaptureWriter,
    build_capture_record,
    capture_operational_event,
)
from backend.app.algorithms.voting_ensemble.runtime.commands import manual_evaluation_command
from backend.app.algorithms.voting_ensemble.runtime.status_store import VotingEnsembleStatusStore
from backend.app.execution.broker_reconciliation import BrokerFillUpdate
from backend.tests.test_voting_ensemble_execution_adapter import FakeVotingEnsembleBroker, order_plan
from backend.tests.test_voting_ensemble_local_gates import evaluate_service_candidate
from backend.tests.test_voting_ensemble_snapshot import candles, snapshot_payload


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


class VotingEnsembleIntelligenceCaptureTest(unittest.TestCase):
    def test_optional_capture_uses_bounded_overflow_but_critical_records_are_durable(self) -> None:
        store = VotingEnsembleCaptureStore()
        writer = VotingEnsembleCaptureWriter(store=store, max_queue_size=1, auto_start=False)

        first_optional = build_capture_record(
            event_type="input_snapshot",
            payload={"snapshot": 1},
            correlation_id="corr-1",
            settings_hash="settings",
            snapshot_timestamp=NOW,
        )
        second_optional = build_capture_record(
            event_type="resolved_settings",
            payload={"settings": 1},
            correlation_id="corr-1",
            settings_hash="settings",
            snapshot_timestamp=NOW,
        )
        critical = build_capture_record(
            event_type="local_gate_decision",
            payload={"eligible": False},
            correlation_id="corr-1",
            settings_hash="settings",
            snapshot_timestamp=NOW,
        )

        self.assertTrue(writer.publish(first_optional))
        self.assertFalse(writer.publish(second_optional))
        self.assertTrue(writer.publish(critical))

        self.assertEqual(writer.overflowPolicy, VOTING_ENSEMBLE_CAPTURE_OVERFLOW_POLICY)
        self.assertEqual(writer.overflowCount, 1)
        self.assertEqual(store.list_records(eventType="local_gate_decision")[0].payload, {"eligible": False})
        self.assertEqual(writer.drain(), 1)
        self.assertEqual(len(store.list_records(correlationId="corr-1")), 2)

    def test_service_evaluation_publishes_capture_without_waiting_for_optional_diagnostics(self) -> None:
        store = VotingEnsembleCaptureStore()
        writer = VotingEnsembleCaptureWriter(store=store, max_queue_size=1, auto_start=False)
        original_writer = service_module.CAPTURE_WRITER
        service_module.CAPTURE_WRITER = writer
        try:
            started = time.perf_counter()
            result = evaluate_service_candidate({"predictedGrossEdgeDollars": 0.75, "currentOneMinuteVolume": 100000})
            elapsed = time.perf_counter() - started
        finally:
            service_module.CAPTURE_WRITER = original_writer

        self.assertEqual(result["final_signal"], "Buy")
        self.assertLess(elapsed, 1.0)
        self.assertGreater(writer.overflowCount, 0)
        self.assertGreaterEqual(len(store.list_records(eventType="global_gate_decision")), 1)
        self.assertGreaterEqual(len(store.list_records(eventType="local_gate_decision")), 1)
        writer.drain()
        self.assertTrue(all(record.captureNamespace == VOTING_ENSEMBLE_CAPTURE_NAMESPACE for record in store.records))
        self.assertTrue(all(record.tableName.startswith("voting_ensemble_capture_") for record in store.records))

    def test_pre_gate_block_persists_operational_gate_capture(self) -> None:
        store = VotingEnsembleCaptureStore()
        writer = VotingEnsembleCaptureWriter(store=store, max_queue_size=1, auto_start=False)
        original_writer = service_module.CAPTURE_WRITER
        service_module.CAPTURE_WRITER = writer
        payload = snapshot_payload(candles(30))
        payload["market_context"]["operationalHealthSnapshot"]["feedDegraded"] = True
        try:
            result = service_module.VotingEnsembleService().evaluate(payload)
        finally:
            service_module.CAPTURE_WRITER = original_writer

        self.assertEqual(result["final_signal"], "Hold")
        self.assertTrue(result["safety_gate_failed"])
        local_gate_records = store.list_records(eventType="local_gate_decision")
        self.assertEqual(len(local_gate_records), 1)
        self.assertFalse(local_gate_records[0].payload["eligible"])
        self.assertIn("voting_ensemble.local_gate.feed_degradation", local_gate_records[0].payload["reasonCodes"])

    def test_execution_adapter_captures_order_broker_and_fill_events_for_replay(self) -> None:
        store = VotingEnsembleCaptureStore()
        writer = VotingEnsembleCaptureWriter(store=store, auto_start=False)
        fill = BrokerFillUpdate(
            clientOrderId="placeholder",
            filledQuantity=4,
            averageFillPrice=100.01,
            status="PARTIALLY_FILLED",
            updatedAt=NOW,
        )
        adapter = VotingEnsembleExecutionAdapter(capture_writer=writer)

        result = adapter.submit_order_once(
            orderPlan=order_plan(quantity=10),
            broker=FakeVotingEnsembleBroker(fill=fill),
            idempotencyKey="decision-1",
            evaluatedAt=NOW,
        )
        replay = store.reconstruct_replay(correlationId="decision-1", orderId=result.clientOrderId)

        self.assertEqual(result.status, "PARTIALLY_FILLED")
        self.assertIn("order_plan", replay["byEventType"])
        self.assertIn("broker_event", replay["byEventType"])
        self.assertIn("fill", replay["byEventType"])
        self.assertEqual(replay["byEventType"]["fill"][0]["filledQuantity"], 4)

    def test_runtime_status_store_captures_worker_job_status_transitions(self) -> None:
        store = VotingEnsembleCaptureStore()
        writer = VotingEnsembleCaptureWriter(store=store, auto_start=False)
        status_store = VotingEnsembleStatusStore(capture_writer=writer)
        command = manual_evaluation_command(
            {"symbol": "SPY", "data_timestamp": NOW.isoformat()},
            correlation_id="job-corr",
            settings_hash="settings-hash",
        )

        status_store.persist_queued(command)
        status_store.mark_running(command)
        status_store.complete(command, {"algorithmId": "voting_ensemble", "decision": {"final_signal": "Hold"}})

        statuses = [record.payload["status"] for record in store.list_records(eventType="worker_job_status")]
        self.assertEqual(statuses, ["queued", "running", "completed"])
        self.assertTrue(all(record.jobId == command.jobId for record in store.list_records(eventType="worker_job_status")))

    def test_complete_trade_reconstruction_uses_immutable_voting_ensemble_events(self) -> None:
        store = VotingEnsembleCaptureStore()
        writer = VotingEnsembleCaptureWriter(store=store, auto_start=False)
        common = {
            "writer": writer,
            "correlation_id": "trade-corr",
            "job_id": "job-1",
            "decision_id": "decision-1",
            "order_id": "ve-order-1",
            "settings_hash": "settings-hash",
            "snapshot_timestamp": NOW,
        }
        for event_type, payload in (
            ("order_plan", {"quantity": 10, "side": "Buy"}),
            ("broker_event", {"status": "ACCEPTED"}),
            ("fill", {"filledQuantity": 10, "averageFillPrice": 100.02}),
            ("exit_decision", {"reason": "target"}),
            ("final_trade_outcome", {"realizedPnl": 15.25}),
        ):
            capture_operational_event(event_type=event_type, payload=payload, **common)

        replay = store.reconstruct_replay(correlationId="trade-corr")

        self.assertEqual(replay["recordCount"], 5)
        self.assertEqual(replay["algorithmId"], "voting_ensemble")
        self.assertIn("final_trade_outcome", replay["byEventType"])
        self.assertEqual(replay["byEventType"]["final_trade_outcome"][0]["realizedPnl"], 15.25)

    def test_capture_store_rejects_non_voting_ensemble_table_names(self) -> None:
        store = VotingEnsembleCaptureStore()
        record = build_capture_record(
            event_type="input_snapshot",
            payload={"symbol": "SPY"},
            correlation_id="corr-1",
            snapshot_timestamp=NOW,
        )
        bad_record = record.model_copy(update={"tableName": "weighted_voting_capture_input_snapshots"})

        with self.assertRaises(ValueError):
            store.write(bad_record)


class VotingEnsembleCaptureMemoryBoundTest(unittest.TestCase):
    """The in-memory capture store is bounded.

    Every evaluation publishes about twenty-five records, one of them a dump of the whole
    input snapshot with every candle in it. Unbounded, that grew by about a megabyte per
    bar and exhausted a 32 GB machine inside a multi-session replay. The newest records
    stay, the oldest go, and the store counts what it evicted so the loss is visible.
    """

    def test_store_keeps_the_newest_records_and_counts_evictions(self) -> None:
        store = VotingEnsembleCaptureStore(max_records=3)
        for index in range(5):
            store.write(
                build_capture_record(
                    event_type="input_snapshot",
                    payload={"index": index},
                    correlation_id=f"corr-{index}",
                    settings_hash="settings",
                    snapshot_timestamp=NOW,
                )
            )

        self.assertEqual(len(store.records), 3)
        self.assertEqual([record.payload["index"] for record in store.records], [2, 3, 4])
        self.assertEqual(store.evictedRecordCount, 2)
        self.assertEqual(len(store.list_records(correlationId="corr-4")), 1)
        self.assertEqual(len(store.list_records(correlationId="corr-0")), 0)

    def test_default_bound_is_finite(self) -> None:
        self.assertGreater(VotingEnsembleCaptureStore().maxRecords, 0)
        with self.assertRaises(ValueError):
            VotingEnsembleCaptureStore(max_records=0)


if __name__ == "__main__":
    unittest.main()
