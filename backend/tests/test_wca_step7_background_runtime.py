from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from backend.app.algorithms.wca.configuration import default_wca_configuration
from backend.app.algorithms.wca.contracts import WcaBrokerReconciliationResult
from backend.app.algorithms.wca.repository import WcaInventoryLedgerEvent, WcaSqliteRepository
from backend.app.algorithms.wca.runtime_events import WcaFinalizedBarEvent
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeRepository
from backend.app.algorithms.wca.runtime_supervisor import WCA_RUNTIME_REQUIRES_OS_PROCESS, WCA_RUNTIME_WORKERS, WcaRuntimeSettings, WcaRuntimeSupervisor
from backend.app.algorithms.wca.weights import baseline_weight_snapshot
from backend.app.gates import BrokerAccountSnapshot
from backend.tests.test_wca_step5_production_pipeline import market_snapshot
from backend.tests.test_wca_step6_inventory_persistence import decision_with_order


class WcaStep7BackgroundRuntimeTests(unittest.TestCase):
    def test_runtime_entrypoint_runs_as_standalone_module_process(self) -> None:
        db_path = temp_db_path()
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "backend.app.algorithms.wca.runtime_main",
                "--once",
                "--database-url",
                f"sqlite:///{db_path}",
                "--owner-id",
                "step7-process",
            ],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertIn("wca_background_runtime_supervisor_v1", payload["runtimeVersion"])
        self.assertTrue(WCA_RUNTIME_REQUIRES_OS_PROCESS)
        runtime_main_source = Path("backend/app/algorithms/wca/runtime_main.py").read_text(encoding="utf-8")
        self.assertNotIn("FastAPI", runtime_main_source)
        self.assertNotIn("backend.app.main", runtime_main_source)

    def test_runtime_declares_all_logical_workers(self) -> None:
        self.assertEqual(
            WCA_RUNTIME_WORKERS,
            (
                "finalised_bar_consumer",
                "decision_worker",
                "position_and_protective_exit_worker",
                "global_risk_request_worker",
                "execution_outbox_worker",
                "broker_reconciliation_worker",
                "recovery_worker",
                "heartbeat_and_health_worker",
                "end_of_session_worker",
            ),
        )

    def test_finalized_bar_queue_rejects_duplicate_stale_incomplete_and_out_of_order_events(self) -> None:
        runtime_repository = WcaRuntimeRepository(WcaSqliteRepository(f"sqlite:///{temp_db_path()}"))
        snapshot = market_snapshot()
        now = snapshot.decision_timestamp + timedelta(seconds=1)
        event = finalized_event("event-1", snapshot=snapshot, publication_offset_seconds=1)

        accepted = runtime_repository.publish_finalized_bar_event(event, now=now)
        duplicate = runtime_repository.publish_finalized_bar_event(event, now=now)
        older = runtime_repository.publish_finalized_bar_event(finalized_event("event-older", snapshot=snapshot, bar_offset_minutes=-1, publication_offset_seconds=2), now=now)
        stale_event = finalized_event("event-stale", snapshot=snapshot, bar_offset_minutes=10, publication_offset_seconds=11)
        stale = runtime_repository.publish_finalized_bar_event(stale_event, now=stale_event.publication_timestamp + timedelta(seconds=600), max_event_age_seconds=300)

        self.assertTrue(accepted.accepted)
        self.assertFalse(duplicate.accepted)
        self.assertIn("wca.runtime.event.duplicate", duplicate.reason_codes)
        self.assertFalse(older.accepted)
        self.assertIn("wca.runtime.event.out_of_order", older.reason_codes)
        self.assertFalse(stale.accepted)
        self.assertIn("wca.runtime.event.stale", stale.reason_codes)
        with self.assertRaises(ValueError):
            finalized_event("event-incomplete", snapshot=snapshot, is_finalized=False)

    def test_finalized_bar_queue_backpressure_rejects_new_events_when_full(self) -> None:
        runtime_repository = WcaRuntimeRepository(WcaSqliteRepository(f"sqlite:///{temp_db_path()}"))
        snapshot = market_snapshot()
        now = snapshot.decision_timestamp + timedelta(seconds=1)

        accepted = runtime_repository.publish_finalized_bar_event(finalized_event("event-cap-1", snapshot=snapshot), now=now, max_queue_depth=1)
        blocked = runtime_repository.publish_finalized_bar_event(finalized_event("event-cap-2", snapshot=snapshot, bar_offset_minutes=1), now=now + timedelta(minutes=1), max_queue_depth=1)

        self.assertTrue(accepted.accepted)
        self.assertFalse(blocked.accepted)
        self.assertEqual(blocked.status, "backpressure")
        self.assertIn("wca.runtime.backpressure.event_queue_full", blocked.reason_codes)

    def test_runtime_processes_each_bar_once_and_checkpoints_after_decision_persistence(self) -> None:
        repository = seeded_repository()
        runtime_repository = WcaRuntimeRepository(repository)
        snapshot = market_snapshot()
        event = finalized_event("event-process-once", snapshot=snapshot)
        runtime_repository.publish_finalized_bar_event(event, now=snapshot.decision_timestamp + timedelta(seconds=1))
        supervisor = WcaRuntimeSupervisor(
            repository=repository,
            runtime_repository=runtime_repository,
            settings=WcaRuntimeSettings(max_lag_seconds=99_999_999),
            owner_id="step7-worker",
        )
        fake_decision = decision_with_order("runtime-decision", "runtime-intent", "runtime-idempotency")

        with patch.dict("os.environ", {}, clear=True), patch(
            "backend.app.algorithms.wca.runtime_supervisor.run_wca_paper_pipeline_adapter",
            return_value=type("PipelineResult", (), {"decision": fake_decision})(),
        ):
            first = supervisor.run_once()
            second = supervisor.run_once()

        self.assertEqual(first["workers"]["decision_worker"]["status"], "completed")
        self.assertEqual(first["workers"]["execution_outbox_worker"]["status"], "blocked")
        self.assertIn(
            "wca.paper_account.automatic_paper_disabled",
            first["workers"]["execution_outbox_worker"]["reasonCodes"],
        )
        self.assertEqual(second["workers"]["finalised_bar_consumer"]["status"], "idle")
        with sqlite3.connect(repository.path) as conn:
            event_rows = conn.execute("SELECT status, decision_id FROM wca_runtime_event_queue WHERE event_id = ?", (event.event_id,)).fetchall()
            decision_count = conn.execute("SELECT COUNT(*) FROM wca_decisions WHERE decision_id = ?", (fake_decision.decision_id,)).fetchone()[0]
            checkpoint_count = conn.execute("SELECT COUNT(*) FROM wca_runtime_checkpoints WHERE checkpoint_key = ?", (event.checkpoint_key,)).fetchone()[0]
            outbox_count = conn.execute("SELECT COUNT(*) FROM wca_execution_outbox").fetchone()[0]
            broker_count = conn.execute("SELECT COUNT(*) FROM wca_broker_orders").fetchone()[0]

        self.assertEqual(event_rows, [("completed", fake_decision.decision_id)])
        self.assertEqual(decision_count, 1)
        self.assertEqual(checkpoint_count, 1)
        self.assertEqual(outbox_count, 0)
        self.assertEqual(broker_count, 0)

    def test_lag_pauses_new_entries_while_protective_management_continues(self) -> None:
        repository = seeded_repository()
        runtime_repository = WcaRuntimeRepository(repository)
        snapshot = market_snapshot()
        event = finalized_event("event-lag", snapshot=snapshot)
        runtime_repository.publish_finalized_bar_event(event, now=snapshot.decision_timestamp + timedelta(seconds=1))
        supervisor = WcaRuntimeSupervisor(
            repository=repository,
            runtime_repository=runtime_repository,
            settings=WcaRuntimeSettings(max_lag_seconds=1),
            owner_id="step7-lag",
        )
        fake_decision = decision_with_order("lag-decision", "lag-intent", "lag-idempotency")
        calls = []

        def fake_pipeline(pipeline_input):
            calls.append(pipeline_input)
            return type("PipelineResult", (), {"decision": fake_decision})()

        with patch("backend.app.algorithms.wca.runtime_supervisor.run_wca_paper_pipeline_adapter", side_effect=fake_pipeline):
            result = supervisor.run_once()

        self.assertEqual(calls[0].global_gate_quantity_cap, 0)
        self.assertTrue(result["workers"]["decision_worker"]["pausedNewEntries"])
        self.assertEqual(result["workers"]["position_and_protective_exit_worker"]["status"], "completed")
        latest_health = runtime_repository.read_latest_runtime_health()
        self.assertIsNotNone(latest_health)
        self.assertTrue(latest_health.paused_new_entries)
        self.assertTrue(latest_health.protective_management_active)

    def test_recovery_worker_requeues_expired_event_and_command_leases(self) -> None:
        runtime_repository = WcaRuntimeRepository(WcaSqliteRepository(f"sqlite:///{temp_db_path()}"))
        snapshot = market_snapshot()
        event = finalized_event("event-recovery", snapshot=snapshot)
        runtime_repository.publish_finalized_bar_event(event, now=snapshot.decision_timestamp + timedelta(seconds=1))
        claimed_event = runtime_repository.claim_next_event(owner_id="old-owner", lease_seconds=1)
        command = runtime_repository.claim_next_command
        self.assertIsNotNone(claimed_event)
        recovered = runtime_repository.recover_expired_work(now=datetime.now(timezone.utc) + timedelta(seconds=120))

        self.assertEqual(recovered["events_requeued"], 1)
        self.assertEqual(command.__name__, "claim_next_command")
        with sqlite3.connect(runtime_repository.path) as conn:
            status = conn.execute("SELECT status FROM wca_runtime_event_queue WHERE event_id = ?", (event.event_id,)).fetchone()[0]
        self.assertEqual(status, "queued")

    def test_wca_runtime_modules_do_not_import_sibling_runtime_or_mutable_stores(self) -> None:
        violations: list[str] = []
        for path in Path("backend/app/algorithms/wca").glob("runtime*.py"):
            source = path.read_text(encoding="utf-8")
            for forbidden in (
                "backend.app.algorithms.weighted_voting",
                "backend.app.algorithms.voting_ensemble",
                "backend.app.algorithms.regime",
                "backend.app.algorithms.session",
                "backend.app.algorithms.meta_strategy",
            ):
                if forbidden in source:
                    violations.append(f"{path}: imports {forbidden}")

        self.assertEqual(violations, [])


def seeded_repository() -> WcaSqliteRepository:
    repository = WcaSqliteRepository(f"sqlite:///{temp_db_path()}")
    configuration = default_wca_configuration()
    snapshot = market_snapshot()
    repository.initialize_defaults(
        symbol="SPY",
        configuration=configuration.model_dump(mode="json"),
        weight_snapshot=baseline_weight_snapshot(cutoff=snapshot.decision_timestamp, weight_version="step7.weights.v1"),
        engine_version="step7-test",
    )
    timestamp = snapshot.decision_timestamp.astimezone(timezone.utc)
    repository.record_inventory_event(
        WcaInventoryLedgerEvent(
            inventory_event_id=f"step7-runtime-reset-{uuid4().hex}",
            event_type="DAILY_STATE_RESET",
            broker_account_id="paper",
            symbol="SPY",
            event_timestamp=(timestamp - timedelta(seconds=5)).isoformat(),
            trade_date=timestamp.date().isoformat(),
            reconciliation_watermark="step7-reconciled",
        )
    )
    repository.write_broker_account_snapshot(
        BrokerAccountSnapshot(
            accountId="paper",
            equity=100_000,
            buyingPower=100_000,
            realizedPnlToday=0,
            positions=[],
            pendingOrders=[],
            partiallyFilledOrders=[],
            observedAt=timestamp - timedelta(seconds=1),
            sessionDate=timestamp.date(),
            sourceAuthority="broker",
            positionsReconciled=True,
            openOrdersReconciled=True,
        ),
        cash=100_000,
        configuration_version=configuration.configuration_version,
        run_id="step7-runtime-state",
    )
    repository.write_broker_reconciliation(
        WcaBrokerReconciliationResult(
            reconciliation_id=f"step7-clean-reconciliation-{uuid4().hex}",
            account_id="paper",
            evaluated_at=timestamp - timedelta(seconds=1),
            intents_checked=0,
            broker_open_orders_checked=0,
            broker_positions_checked=0,
            discrepancies=(),
            hard_operational_warning=False,
            reason_codes=("wca.broker_reconciliation.clean",),
        )
    )
    return repository


def finalized_event(
    event_id: str,
    *,
    snapshot,
    bar_offset_minutes: int = 0,
    publication_offset_seconds: int = 1,
    is_finalized: bool = True,
) -> WcaFinalizedBarEvent:
    finalized_at = snapshot.decision_timestamp + timedelta(minutes=bar_offset_minutes)
    event_snapshot = snapshot.model_copy(
        update={
            "data_timestamp": finalized_at,
            "decision_timestamp": finalized_at,
        }
    )
    return WcaFinalizedBarEvent(
        event_id=event_id,
        symbol="SPY",
        finalized_candle_timestamp=finalized_at,
        data_manifest_hash=f"manifest-{event_id}",
        publication_timestamp=finalized_at + timedelta(seconds=publication_offset_seconds),
        source="test.completed_bar_publisher",
        replay_or_recovery=False,
        is_finalized=is_finalized,
        snapshot=event_snapshot,
    )


def temp_db_path() -> Path:
    root = Path.cwd() / "data" / "test_tmp"
    root.mkdir(exist_ok=True)
    return root / f"wca-step7-{uuid4().hex}.sqlite"


if __name__ == "__main__":
    unittest.main()
