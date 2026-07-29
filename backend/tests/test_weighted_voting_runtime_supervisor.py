import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.dynamic_settings import DynamicSettingsResolver, resolve_effective_settings
from backend.app.algorithms.weighted_voting.market_condition import classify_market_condition
from backend.app.algorithms.weighted_voting.market_snapshot import build_weighted_voting_market_snapshot
from backend.app.algorithms.weighted_voting.inventory import WeightedVotingInventoryEventType, WeightedVotingInventoryRepository
from backend.app.algorithms.weighted_voting.rollout import WeightedVotingRolloutFlags, WeightedVotingRolloutValidation
from backend.app.algorithms.weighted_voting.runtime_supervisor import (
    WeightedVotingEventBus,
    WeightedVotingFinalisedBarEvent,
    WeightedVotingRuntimeConfig,
    WeightedVotingRuntimeSupervisor,
    runtime_supervisor_status,
    weighted_voting_bar_event_idempotency_key,
)
from backend.app.algorithms.weighted_voting.runtime_context import WeightedVotingStaticAccountPort, WeightedVotingStaticGlobalRiskPort
from backend.app.algorithms.weighted_voting.persistence import WEIGHTED_VOTING_SETTINGS_KEY, persist_effective_settings
from backend.app.algorithms.weighted_voting.service import WeightedVotingService
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway
from backend.app.gates import GlobalGateResponse, GlobalOrderProposal, apply_global_gate_response


SESSION_OPEN = datetime(2026, 7, 14, 13, 30, tzinfo=timezone.utc)
MAIN_PATH = Path(__file__).parents[1] / "app" / "main.py"


class WeightedVotingRuntimeSupervisorTest(unittest.TestCase):
    def test_supervisor_contract_declares_workers_and_backend_startup(self) -> None:
        status = runtime_supervisor_status()
        main_source = MAIN_PATH.read_text(encoding="utf-8")

        self.assertTrue(status["startsWithBackend"])
        self.assertFalse(status["dashboardRequired"])
        self.assertIn("WeightedVotingDecisionWorker", status["workers"])
        self.assertIn("await get_weighted_voting_runtime_supervisor().start()", main_source)
        self.assertIn("await get_weighted_voting_runtime_supervisor().shutdown()", main_source)

    def test_finalised_bar_event_automatically_persists_one_decision(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)

        record = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload())))

        self.assertEqual(record["status"], "decision_persisted")
        self.assertEqual(len([key for key in store.snapshots if key.startswith("weighted_voting.decisions.")]), 1)
        self.assertTrue(any(key.startswith("weighted_voting.runtime.checkpoints.SPY") for key in store.snapshots))
        self.assertEqual(supervisor.health()["persistedDecisions"], 1)

    def test_runtime_builds_full_context_from_completed_bar_event(self) -> None:
        store = MemoryStore()
        inventory = seeded_inventory(store)
        payload = evaluate_payload()
        payload["five_minute_candles"] = [
            {
                "timestamp": payload["candles"][index]["timestamp"],
                "open": payload["candles"][index - 4]["open"],
                "high": max(row["high"] for row in payload["candles"][index - 4 : index + 1]),
                "low": min(row["low"] for row in payload["candles"][index - 4 : index + 1]),
                "close": payload["candles"][index]["close"],
                "volume": sum(row["volume"] for row in payload["candles"][index - 4 : index + 1]),
                "finalized": True,
            }
            for index in range(4, len(payload["candles"]), 5)
        ]
        snapshot = build_weighted_voting_market_snapshot(payload)
        service = WeightedVotingService(store=store)
        weight_state = service.active_weight_state()
        condition = classify_market_condition(snapshot)
        effective = DynamicSettingsResolver().resolve(condition, timestamp=snapshot.data_timestamp)
        supervisor = WeightedVotingRuntimeSupervisor(
            service=service,
            store=store,
            inventory_repository=inventory,
            account_port=WeightedVotingStaticAccountPort(account_equity=100000.0, broker_buying_power=75000.0, source_id="weighted_voting.test.account_port"),
            global_risk_port=WeightedVotingStaticGlobalRiskPort(global_available_risk=1000.0, global_max_shares=100, gate_response=None, source_id="weighted_voting.test.global_risk_port"),
            config=WeightedVotingRuntimeConfig(heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
        )

        context = supervisor.build_runtime_context_from_finalised_bar(
            snapshot=snapshot,
            active_weight_state=weight_state,
            effective_settings=effective,
            market_condition=condition,
            observed_at=snapshot.data_timestamp,
            event_payload=payload,
        )

        self.assertEqual(context.mode, "production")
        self.assertEqual(len(context.finalised_one_minute_market_snapshot.one_minute_candles), len(payload["candles"]))
        self.assertGreaterEqual(len(context.five_minute_candles), 1)
        self.assertEqual(context.inventory_snapshot.algorithm_id, "weighted_voting")
        self.assertEqual(context.read_only_account_equity, 100000.0)
        self.assertEqual(context.read_only_broker_buying_power, 75000.0)
        self.assertEqual(context.global_risk_state.global_available_risk, 1000.0)
        self.assertEqual(context.global_risk_state.global_max_shares, 100)
        self.assertEqual(context.algorithm_daily_pnl, 0.0)
        self.assertEqual(context.effective_settings.settings_version, effective.settings_version)
        self.assertTrue(any(key.startswith("weighted_voting.runtime.contexts.") for key in store.snapshots))

    def test_finalised_bar_event_uses_stable_settings_not_one_minute_payload_settings(self) -> None:
        store = MemoryStore()
        stable_settings = resolve_effective_settings(
            dynamic_values={"slippage_allowance_per_share": 0.02, "maximum_shares": 7},
            baseline_config=WeightedVotingConfig(),
            source_evidence=("weighted_voting.test.stable_settings_version",),
        )
        persist_effective_settings(store, stable_settings)
        supervisor = supervisor_for(store)
        first_payload = evaluate_payload()
        first_payload["settingsVersion"] = "one-minute-settings-should-be-ignored"
        first_payload["effective_settings"] = {"settings_version": "bar-derived-settings", "maximum_shares": 999999}
        first_payload["slippage_per_share"] = 12.34
        first_payload["fee_per_share"] = 56.78
        second_payload = evaluate_payload(offset_minutes=5)
        second_payload["settingsVersion"] = "different-one-minute-settings-should-still-be-ignored"
        second_payload["effective_settings"] = {"settings_version": "second-bar-derived-settings", "maximum_shares": 1}
        second_payload["slippage_per_share"] = 87.65
        second_payload["fee_per_share"] = 43.21

        first = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(first_payload)))
        second = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(second_payload)))

        self.assertEqual(first["status"], "decision_persisted")
        self.assertEqual(second["status"], "decision_persisted")
        self.assertEqual(store.read_snapshot(WEIGHTED_VOTING_SETTINGS_KEY)["settings_version"], stable_settings.settings_version)
        context_records = [
            snapshot
            for key, snapshot in store.snapshots.items()
            if key.startswith("weighted_voting.runtime.contexts.")
        ]
        self.assertEqual(len(context_records), 2)
        self.assertEqual({record["settings_version"] for record in context_records}, {stable_settings.settings_version})
        self.assertEqual({record["estimated_slippage"] for record in context_records}, {stable_settings.slippage_allowance_per_share})
        self.assertEqual({record["estimated_fees"] for record in context_records}, {WeightedVotingConfig().fee_per_share})
        proposal_records = [
            snapshot
            for key, snapshot in store.snapshots.items()
            if key.startswith("weighted_voting.order_proposals.")
        ]
        self.assertEqual(len(proposal_records), 2)
        self.assertEqual({record["settings_version"] for record in proposal_records}, {stable_settings.settings_version})

    def test_missing_effective_settings_are_bootstrapped_once_and_reused_across_bar_events(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)

        first = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload())))
        bootstrapped_version = store.read_snapshot(WEIGHTED_VOTING_SETTINGS_KEY)["settings_version"]
        second = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=5))))

        self.assertEqual(first["status"], "decision_persisted")
        self.assertEqual(second["status"], "decision_persisted")
        self.assertEqual(store.read_snapshot(WEIGHTED_VOTING_SETTINGS_KEY)["settings_version"], bootstrapped_version)
        context_versions = {
            snapshot["settings_version"]
            for key, snapshot in store.snapshots.items()
            if key.startswith("weighted_voting.runtime.contexts.")
        }
        self.assertEqual(context_versions, {bootstrapped_version})

    def test_duplicate_bar_events_produce_only_one_decision(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)
        event = event_from_payload(evaluate_payload())

        first = asyncio.run(supervisor.process_finalised_bar_event(event))
        second = asyncio.run(supervisor.process_finalised_bar_event(event))

        self.assertEqual(first["status"], "decision_persisted")
        self.assertEqual(second["status"], "duplicate_noop")
        self.assertEqual(len([key for key in store.snapshots if key.startswith("weighted_voting.decisions.")]), 1)
        self.assertEqual(supervisor.health()["duplicateEvents"], 1)

    def test_restart_recovery_resumes_from_last_checkpoint(self) -> None:
        store = MemoryStore()
        first_supervisor = supervisor_for(store)
        event = event_from_payload(evaluate_payload())
        asyncio.run(first_supervisor.process_finalised_bar_event(event))

        recovered = supervisor_for(store)
        recovered.recover_from_checkpoints()

        self.assertEqual(recovered.health()["lastEventTimestampBySymbol"]["SPY"], event.finalised_candle_timestamp.isoformat())
        self.assertTrue(recovered.health()["lastCheckpointBySymbol"]["SPY"])

    def test_out_of_order_events_are_rejected_without_replay_recovery(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=10))))

        out_of_order = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=0))))

        self.assertEqual(out_of_order["status"], "rejected_out_of_order")
        self.assertEqual(len([key for key in store.snapshots if key.startswith("weighted_voting.decisions.")]), 1)
        self.assertEqual(supervisor.health()["outOfOrderEvents"], 1)

    def test_stale_queued_event_cannot_create_order_or_decision(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store, max_queue_lag_seconds=1)
        stale_event = event_from_payload(evaluate_payload(), published_at=SESSION_OPEN)

        record = asyncio.run(supervisor.process_finalised_bar_event(stale_event))

        self.assertEqual(record["status"], "stale_no_order")
        self.assertEqual(len([key for key in store.snapshots if key.startswith("weighted_voting.decisions.")]), 0)
        self.assertEqual(len([key for key in store.snapshots if key.startswith("weighted_voting.order_proposals.")]), 0)
        self.assertTrue(supervisor.health()["automaticOrderCreationPaused"])

    def test_incomplete_one_minute_bar_event_cannot_create_decision(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)
        payload = evaluate_payload()
        payload["candles"][-1]["finalized"] = False
        event = WeightedVotingFinalisedBarEvent(
            algorithm_id="weighted_voting",
            symbol="SPY",
            finalised_candle_timestamp=datetime.fromisoformat(payload["data_timestamp"]),
            data_manifest_hash="incomplete-candle-manifest",
            market_payload=payload,
            published_at=datetime.now(timezone.utc),
        )

        record = asyncio.run(supervisor.process_finalised_bar_event(event))

        self.assertEqual(record["status"], "runtime_exception_safe_degradation")
        self.assertEqual(len([key for key in store.snapshots if key.startswith("weighted_voting.decisions.")]), 0)
        self.assertTrue(supervisor.health()["automaticOrderCreationPaused"])
        self.assertTrue(supervisor.health()["recoveryRequired"])
        self.assertIn("completed bars", record["error"])

    def test_bounded_queue_applies_backpressure(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store, queue_maxsize=1)

        first = asyncio.run(supervisor.publish_finalised_bar(event_from_payload(evaluate_payload())))
        second = asyncio.run(supervisor.publish_finalised_bar(event_from_payload(evaluate_payload(offset_minutes=1))))

        self.assertTrue(first)
        self.assertFalse(second)
        self.assertEqual(supervisor.health()["rejectedEvents"], 1)

    def test_idempotency_key_contains_required_runtime_versions(self) -> None:
        key = weighted_voting_bar_event_idempotency_key(
            symbol="SPY",
            finalised_candle_timestamp=SESSION_OPEN,
            data_manifest_hash="manifest",
            settings_version="settings",
            weight_version="weights",
        )

        self.assertTrue(key.startswith("weighted_voting.runtime.idempotency."))
        self.assertEqual(key, weighted_voting_bar_event_idempotency_key(symbol="SPY", finalised_candle_timestamp=SESSION_OPEN, data_manifest_hash="manifest", settings_version="settings", weight_version="weights"))
        self.assertNotEqual(key, weighted_voting_bar_event_idempotency_key(symbol="SPY", finalised_candle_timestamp=SESSION_OPEN, data_manifest_hash="manifest2", settings_version="settings", weight_version="weights"))

    def test_accepted_finalised_bar_decision_can_reach_paper_gateway_through_execution_queue(self) -> None:
        store = MemoryStore()
        broker = FakePaperBroker()
        gateway = PaperOrderGateway(broker, store)
        inventory = seeded_inventory(store)
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=gateway,
            inventory_repository=inventory,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )
        enable_automatic_entries(supervisor)

        record = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload())))
        item = supervisor.execution_queue.get_nowait()
        execution_record = supervisor.process_execution_queue_item(item)

        self.assertEqual(record["status"], "decision_persisted")
        self.assertEqual(execution_record["status"], "submitted")
        self.assertEqual(broker.submit_count, 1)
        self.assertEqual(supervisor.health()["submittedOrders"], 1)

    def test_unreconciled_inventory_blocks_new_entries_before_execution_queue(self) -> None:
        store = MemoryStore()
        gateway = PaperOrderGateway(UnreconciledPaperBroker(), store)
        inventory = seeded_inventory(store)
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
            paper_gateway=gateway,
            inventory_repository=inventory,
            rollout_flags=validated_rollout_flags(),
            rollout_validation=validated_rollout_validation(),
        )

        supervisor.reconcile_broker_inventory(startup=True)
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload())))

        self.assertTrue(supervisor.health()["entryCreationPausedForReconciliation"])
        self.assertTrue(supervisor.execution_queue.empty())
        self.assertTrue(any("reconciliation_blocks_new_entries" in str(value) for value in store.snapshots.values()))

    def test_health_exposes_operational_status_and_metrics_without_mutating_store(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload())))
        before = dict(store.snapshots)

        health = supervisor.health()
        after = dict(store.snapshots)

        operational = health["operationalStatus"]
        metrics = health["metrics"]
        self.assertEqual(before, after)
        self.assertIn("workerState", operational)
        self.assertIn("lastFinalisedBarReceived", operational)
        self.assertIn("lastBarProcessed", operational)
        self.assertIn("processingLagSeconds", operational)
        self.assertIn("lastDecision", operational)
        self.assertIn("lastGlobalRiskResponse", operational)
        self.assertIn("openPositions", operational)
        self.assertIn("pendingOrders", operational)
        self.assertIn("inventoryVersion", operational)
        self.assertIn("dailyTradeCount", operational)
        self.assertIn("dailyPnL", operational)
        self.assertIn("remainingDailyRisk", operational)
        self.assertIn("automaticSubmissionRolloutState", operational)
        self.assertIn("decisionLatencyMs", metrics)
        self.assertIn("brokerLatencyMs", metrics)
        self.assertIn("eventBacklog", metrics)
        self.assertIn("gateRejectionCounts", metrics)
        self.assertIn("strategyOpportunityCounts", metrics)
        self.assertIn("proposedVsAllowedQuantity", metrics)
        self.assertIn("reconciliationDiscrepancies", metrics)

    def test_pause_new_entries_keeps_position_protection_active_and_audits(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)

        audit = supervisor.pause_new_entries(actor="ops-user", reason="weighted_voting.test.pause_entries")
        health = supervisor.health()

        self.assertEqual(audit["actor"], "ops-user")
        self.assertEqual(audit["action"], "pause_new_entries")
        self.assertFalse(health["paused"])
        self.assertTrue(health["automaticOrderCreationPaused"])
        self.assertTrue(health["riskReducingExitsAllowed"])
        self.assertEqual(health["operationalStatus"]["pauseReason"], "weighted_voting.test.pause_entries")
        self.assertTrue(any(key.startswith("weighted_voting.runtime.admin_audit.") for key in store.snapshots))

    def test_automatic_entry_pause_blocks_execution_queue_but_keeps_shadow_decision(self) -> None:
        store = MemoryStore()
        supervisor = WeightedVotingRuntimeSupervisor(
            service=AcceptedExecutionService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=8),
        )
        supervisor.pause_new_entries(actor="dashboard", reason="weighted_voting.test.global_paper_off")

        record = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=20))))

        self.assertEqual(record["status"], "decision_persisted")
        self.assertEqual(supervisor.execution_queue.qsize(), 0)
        self.assertEqual(supervisor.health()["rejectedExecutionEvents"], 1)
        blocked = [value for key, value in store.snapshots.items() if key.startswith("weighted_voting.runtime.executions.blocked.")]
        self.assertEqual(blocked[0]["status"], "automatic_order_creation_paused")
        self.assertIn("weighted_voting.runtime.automatic_entries_paused", blocked[0]["reason_codes"])

    def test_all_admin_state_changes_capture_actor_prior_and_new_state(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)

        supervisor.pause(actor="ops-user", reason="weighted_voting.test.pause_runtime")
        supervisor.resume(actor="ops-user", reason="weighted_voting.test.resume_runtime")
        supervisor.resume_new_entries(actor="ops-user", reason="weighted_voting.test.resume_entries", validation_passed=True)
        supervisor.set_strategy_runtime_state("S3", "disabled", actor="ops-user", reason="weighted_voting.test.disable_strategy")
        supervisor.emergency_flatten(actor="ops-user", reason="weighted_voting.test.emergency_flatten")
        audits = [value for key, value in store.snapshots.items() if key.startswith("weighted_voting.runtime.admin_audit.")]

        self.assertGreaterEqual(len(audits), 5)
        self.assertTrue(all(item["actor"] == "ops-user" for item in audits))
        self.assertTrue(all("priorState" in item and "newState" in item and "recordedAt" in item for item in audits))
        self.assertEqual(store.read_snapshot("weighted_voting.runtime.strategy_controls.S3")["runtimeState"], "disabled")
        self.assertTrue(any(key.startswith("weighted_voting.runtime.emergency_flatten.") for key in store.snapshots))

    def test_force_reconciliation_control_makes_failure_visible_and_audited(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)

        audit = supervisor.force_reconciliation(actor="ops-user", reason="weighted_voting.test.force_reconciliation")
        health = supervisor.health()

        self.assertEqual(audit["action"], "force_reconciliation")
        self.assertTrue(health["entryCreationPausedForReconciliation"])
        self.assertTrue(health["automaticOrderCreationPaused"])
        self.assertEqual(health["operationalStatus"]["lastReconciliation"]["status"], "unavailable")
        self.assertTrue(any(key.startswith("weighted_voting.runtime.admin_audit.") for key in store.snapshots))

    def test_fault_injection_recovery_blocks_new_entries_for_crash_points_without_duplicates(self) -> None:
        cases = (
            ("decision_before_risk_response", seed_decision_without_risk, "decision_before_risk_response"),
            ("risk_approval_before_broker_submission", seed_queued_order_without_submission, "risk_approval_before_broker_submission"),
            ("submission_before_local_acknowledgement", seed_submitted_lifecycle_without_ack, "submission_or_acknowledgement_incomplete"),
            ("restart_during_partial_fill", seed_partial_fill_lifecycle, "submission_or_acknowledgement_incomplete"),
            ("fill_before_inventory_update", seed_filled_result_without_reconciliation, "fill_before_inventory_update"),
            ("protective_orders_being_created", seed_unprotected_position, "protective_orders_being_created"),
        )
        for boundary, seed, expected_boundary in cases:
            with self.subTest(boundary=boundary):
                store = MemoryStore()
                item = seed(store)
                broker = FakePaperBroker()
                recovered = WeightedVotingRuntimeSupervisor(
                    service=AcceptedExecutionService(store=store),
                    store=store,
                    config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
                    event_bus=WeightedVotingEventBus(maxsize=8),
                    paper_gateway=PaperOrderGateway(broker, store),
                    inventory_repository=WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=25_000.0),
                    rollout_flags=validated_rollout_flags(),
                    rollout_validation=validated_rollout_validation(),
                )

                state = recovered.perform_recovery_safety_check(reason=f"weighted_voting.test.{boundary}")
                if item is not None:
                    execution_record = recovered.process_execution_queue_item(item)
                    self.assertEqual(execution_record["status"], "recovery_blocked")

                self.assertTrue(state["recoveryRequired"])
                self.assertTrue(recovered.health()["automaticOrderCreationPaused"])
                self.assertEqual(broker.submit_count, 0)
                self.assertTrue(any(item["boundary"] == expected_boundary for item in state["unresolvedBoundaries"]))

    def test_fault_injection_degradation_boundaries_fail_closed(self) -> None:
        stale_market = evaluate_payload()
        stale_market["data_freshness_seconds"] = 999.0
        stale_quote = evaluate_payload(offset_minutes=1)
        stale_quote["quote_timestamp"] = (SESSION_OPEN - timedelta(minutes=15)).isoformat()
        future_payload = evaluate_payload(offset_minutes=2)
        future_payload["data_timestamp"] = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        future_payload["candles"][-1]["timestamp"] = future_payload["data_timestamp"]

        for boundary, payload in (
            ("stale_market_data_feed", stale_market),
            ("stale_quote_feed", stale_quote),
            ("clock_skew_future_bar", future_payload),
        ):
            with self.subTest(boundary=boundary):
                store = MemoryStore()
                supervisor = supervisor_for(store)
                record = asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(payload)))

                self.assertEqual(record["status"], "safe_degradation_no_order")
                self.assertTrue(supervisor.health()["automaticOrderCreationPaused"])
                self.assertTrue(any(boundary in code for code in record["reason_codes"]))

        store = MemoryStore()
        supervisor = WeightedVotingRuntimeSupervisor(service=GlobalRiskOutageService(store=store), store=store, config=WeightedVotingRuntimeConfig(heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0), event_bus=WeightedVotingEventBus(maxsize=8))
        asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=3))))
        self.assertTrue(supervisor.health()["automaticOrderCreationPaused"])
        self.assertTrue(supervisor.health()["recoveryRequired"])

        store = MemoryStore()
        item = seed_queued_order_without_submission(store)
        supervisor = supervisor_for(store)
        broker_record = supervisor.process_execution_queue_item(item)
        self.assertEqual(broker_record["status"], "gateway_unavailable")
        self.assertTrue(supervisor.health()["automaticOrderCreationPaused"])

        outage_store = FailingWriteStore()
        outage_supervisor = supervisor_for(outage_store)
        outage_record = asyncio.run(outage_supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=4))))
        self.assertEqual(outage_record["status"], "runtime_exception_safe_degradation")
        self.assertTrue(outage_supervisor.health()["recoveryRequired"])

    def test_corrupt_authoritative_snapshots_are_quarantined_and_last_approved_records_restored(self) -> None:
        store = MemoryStore()
        service = WeightedVotingService(store=store)
        approved_settings = service.get_config()["configuration"]
        approved_weights = service.weights_active()["weightState"]
        store.write_snapshot("weighted_voting.settings.last_approved", approved_settings)
        store.write_snapshot("weighted_voting.weights.last_approved", approved_weights)
        store.write_snapshot("weighted_voting.settings.effective", {"algorithm_id": "weighted_voting", "settings_version": ""})
        store.write_snapshot("weighted_voting.weights.active", {"algorithm_id": "weighted_voting", "strategy_weights": {"S2": 2.0}})
        store.write_snapshot("weighted_voting.inventory.snapshot.current", {"algorithm_id": "weighted_voting", "snapshot_version": "bad"})
        supervisor = supervisor_for(store)

        state = supervisor.perform_recovery_safety_check(reason="weighted_voting.test.corruption")

        self.assertTrue(state["recoveryRequired"])
        self.assertGreaterEqual(len(state["quarantinedSnapshots"]), 3)
        self.assertEqual(store.read_snapshot("weighted_voting.settings.effective"), approved_settings)
        self.assertEqual(store.read_snapshot("weighted_voting.weights.active"), approved_weights)
        self.assertTrue(any(key.startswith("weighted_voting.runtime.quarantine.") for key in store.snapshots))

    def test_inventory_conflict_event_backlog_and_duplicate_out_of_order_events_fail_closed(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store, queue_maxsize=1)
        asyncio.run(supervisor.publish_finalised_bar(event_from_payload(evaluate_payload(offset_minutes=5))))
        state = supervisor.perform_recovery_safety_check(reason="weighted_voting.test.event_backlog")
        self.assertTrue(any(item["boundary"] == "event_backlog" for item in state["unresolvedBoundaries"]))
        self.assertTrue(supervisor.health()["automaticOrderCreationPaused"])

        duplicate_store = MemoryStore()
        duplicate_supervisor = supervisor_for(duplicate_store)
        event = event_from_payload(evaluate_payload(offset_minutes=6))
        first = asyncio.run(duplicate_supervisor.process_finalised_bar_event(event))
        second = asyncio.run(duplicate_supervisor.process_finalised_bar_event(event))
        older = asyncio.run(duplicate_supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=5))))
        self.assertEqual(first["status"], "decision_persisted")
        self.assertEqual(second["status"], "duplicate_noop")
        self.assertEqual(older["status"], "rejected_out_of_order")
        self.assertEqual(len([key for key in duplicate_store.snapshots if key.startswith("weighted_voting.decisions.")]), 1)

    def test_circuit_breaker_requires_healthy_state_check_before_auto_submission_resumes(self) -> None:
        store = MemoryStore()
        supervisor = supervisor_for(store)
        supervisor.metrics.inventory_reconciled = True
        supervisor.metrics.worker_failures["WeightedVotingDecisionWorker"] = 3
        supervisor.metrics.circuit_breaker_open = True
        supervisor.metrics.automatic_order_creation_paused = True

        rejected = supervisor.resume_new_entries(actor="ops-user", reason="weighted_voting.test.circuit_breaker", validation_passed=True)
        self.assertTrue(rejected["newState"]["automaticOrderCreationPaused"])
        self.assertTrue(supervisor.health()["circuitBreakerOpen"])

        supervisor.metrics.circuit_breaker_open = False
        supervisor.metrics.worker_failures.clear()
        supervisor.metrics.last_error = None
        supervisor.metrics.inventory_reconciled = True
        supervisor.metrics.entry_creation_paused_for_reconciliation = False
        accepted = supervisor.resume_new_entries(actor="ops-user", reason="weighted_voting.test.healthy_resume", validation_passed=True)
        self.assertFalse(accepted["newState"]["automaticOrderCreationPaused"])
        self.assertFalse(supervisor.health()["automaticOrderCreationPaused"])


def supervisor_for(store: "MemoryStore", *, queue_maxsize: int = 8, max_queue_lag_seconds: int = 75) -> WeightedVotingRuntimeSupervisor:
    return WeightedVotingRuntimeSupervisor(
        service=WeightedVotingService(store=store),
        store=store,
        config=WeightedVotingRuntimeConfig(queue_maxsize=queue_maxsize, max_queue_lag_seconds=max_queue_lag_seconds, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
        event_bus=WeightedVotingEventBus(maxsize=queue_maxsize),
    )


def event_from_payload(payload: dict, *, published_at: datetime | None = None) -> WeightedVotingFinalisedBarEvent:
    snapshot = build_weighted_voting_market_snapshot(payload)
    return WeightedVotingFinalisedBarEvent(
        algorithm_id="weighted_voting",
        symbol=snapshot.symbol,
        finalised_candle_timestamp=snapshot.data_timestamp,
        data_manifest_hash=snapshot.data_manifest_hash,
        market_payload=payload,
        published_at=published_at or datetime.now(timezone.utc),
    )


def evaluate_payload(*, offset_minutes: int = 0) -> dict:
    rows = []
    start = SESSION_OPEN + timedelta(minutes=offset_minutes)
    for index in range(95):
        base = 100.0 + index * 0.03
        rows.append(
            {
                "timestamp": (start + timedelta(minutes=index)).isoformat(),
                "open": base,
                "high": base + 0.45,
                "low": base - 0.18,
                "close": base + 0.08,
                "volume": 200000 if index != 5 else 5000,
            }
        )
    return {
        "symbol": "SPY",
        "data_timestamp": rows[-1]["timestamp"],
        "candles": rows,
        "bid": rows[-1]["close"] - 0.01,
        "ask": rows[-1]["close"] + 0.01,
        "session_phase": "morning",
        "data_freshness_seconds": 0.0,
    }


def global_proposal_for_snapshot(payload: dict) -> GlobalOrderProposal:
    snapshot = build_weighted_voting_market_snapshot(payload)
    return global_proposal_for_market_snapshot(snapshot)


def global_proposal_for_context(context) -> GlobalOrderProposal:
    return global_proposal_for_market_snapshot(context.finalised_one_minute_market_snapshot)


def global_proposal_for_market_snapshot(snapshot) -> GlobalOrderProposal:
    close = snapshot.one_minute_candles[-1].close
    return GlobalOrderProposal(
        algorithmId="weighted_voting",
        capitalPartitionId="weighted_voting.paper.default",
        decisionId="runtime-auto-decision",
        orderIntentId="runtime-auto-intent",
        intent="new_entry",
        symbol=snapshot.symbol,
        side="BUY",
        quantity=3,
        triggerPrice=close,
        limitPrice=close,
        stopPrice=close - 1.0,
        targetPrice=close + 2.0,
        plannedRiskDollars=50.0,
        settingsSnapshot={"settings_version": "runtime-test"},
        entryFormula={"kind": "limit"},
        stopFormula={"kind": "structural"},
        targetFormula={"kind": "r_multiple"},
        strategyStateHash="runtime-strategy-state",
        proposedAt=snapshot.data_timestamp,
        sessionDate=snapshot.data_timestamp.date(),
        configurationHash="runtime-auto-config",
    )


def validated_rollout_flags() -> WeightedVotingRolloutFlags:
    return WeightedVotingRolloutFlags(
        v2_enabled=True,
        shadow_mode=False,
        dynamic_reduction_enabled=True,
        dynamic_increase_enabled=True,
        auto_submit_enabled=True,
    )


def validated_rollout_validation() -> WeightedVotingRolloutValidation:
    return WeightedVotingRolloutValidation(
        backend_shadow_passed=True,
        shadow_comparison_passed=True,
        static_equal_weights_passed=True,
        performance_weights_validated=True,
        dynamic_reduction_validated=True,
        dynamic_entry_exit_validated=True,
        dynamic_increase_validated=True,
        manual_paper_submission_validated=True,
        tests_passed=True,
        paper_validations_passed=True,
        live_trading_enabled=False,
    )


def enable_automatic_entries(supervisor: WeightedVotingRuntimeSupervisor) -> None:
    supervisor.metrics.inventory_reconciled = True
    supervisor.resume_new_entries(
        actor="weighted_voting.test",
        reason="weighted_voting.test.enable_automatic_entries",
        validation_passed=True,
    )
    assert not supervisor.health()["automaticOrderCreationPaused"]


def seeded_inventory(store: "MemoryStore") -> WeightedVotingInventoryRepository:
    inventory = WeightedVotingInventoryRepository(store, symbol="SPY", allocated_capital=25_000.0)
    inventory.initialize_session(
        session_date=SESSION_OPEN.date(),
        allocated_capital=25_000.0,
        cash_available=25_000.0,
        occurred_at=SESSION_OPEN,
        expected_snapshot_version=0,
        event_id="runtime-session-start",
    )
    return inventory


def seed_decision_without_risk(store: "MemoryStore"):
    store.write_snapshot(
        "weighted_voting.decisions.crash-decision",
        {
            "algorithm_id": "weighted_voting",
            "decision_id": "crash-decision",
            "status": "persisted_before_risk_response",
            "reason_codes": ("weighted_voting.test.crash.decision_before_risk",),
        },
    )
    return None


def seed_queued_order_without_submission(store: "MemoryStore"):
    supervisor = WeightedVotingRuntimeSupervisor(
        service=AcceptedExecutionService(store=store),
        store=store,
        config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
        event_bus=WeightedVotingEventBus(maxsize=8),
        rollout_flags=validated_rollout_flags(),
        rollout_validation=validated_rollout_validation(),
    )
    enable_automatic_entries(supervisor)
    asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=20))))
    return supervisor.execution_queue.get_nowait()


def seed_submitted_lifecycle_without_ack(store: "MemoryStore"):
    item = seed_queued_order_without_submission(store)
    store.write_snapshot(
        f"weighted_voting.execution_gateway.lifecycle.{item.command.client_order_id}.latest",
        {
            "algorithmId": "weighted_voting",
            "clientOrderId": item.command.client_order_id,
            "orderIntentId": item.command.order_intent_id,
            "decisionId": item.command.decision_id,
            "status": "SUBMITTED",
            "recordedAt": SESSION_OPEN.isoformat(),
            "reasonCodes": ("weighted_voting.test.crash.submitted_before_ack",),
        },
    )
    return item


def seed_partial_fill_lifecycle(store: "MemoryStore"):
    item = seed_queued_order_without_submission(store)
    store.write_snapshot(
        f"weighted_voting.execution_gateway.lifecycle.{item.command.client_order_id}.latest",
        {
            "algorithmId": "weighted_voting",
            "clientOrderId": item.command.client_order_id,
            "orderIntentId": item.command.order_intent_id,
            "decisionId": item.command.decision_id,
            "status": "PARTIALLY_FILLED",
            "recordedAt": SESSION_OPEN.isoformat(),
            "reasonCodes": ("weighted_voting.test.crash.partial_fill",),
        },
    )
    return item


def seed_filled_result_without_reconciliation(store: "MemoryStore"):
    item = seed_queued_order_without_submission(store)
    store.write_snapshot(
        f"weighted_voting.execution_gateway.automatic_result.{item.command.client_order_id}",
        {
            "algorithmId": "weighted_voting",
            "orderIntentId": item.command.order_intent_id,
            "decisionId": item.command.decision_id,
            "clientOrderId": item.command.client_order_id,
            "mode": "automatic",
            "submitted": True,
            "duplicate": False,
            "status": "FILLED",
            "fill": {
                "clientOrderId": item.command.client_order_id,
                "algorithmId": "weighted_voting",
                "orderIntentId": item.command.order_intent_id,
                "symbol": "SPY",
                "side": "BUY",
                "filledQuantity": 2,
                "averageFillPrice": 101.0,
                "status": "FILLED",
                "filledAt": SESSION_OPEN.isoformat(),
            },
            "reasonCodes": ("weighted_voting.test.crash.fill_before_inventory",),
        },
    )
    return item


def seed_unprotected_position(store: "MemoryStore"):
    inventory = seeded_inventory(store)
    snapshot = inventory.current_snapshot(now=SESSION_OPEN)
    inventory.append_event(
        event_id="runtime-test-unprotected-fill",
        event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
        payload={
            "algorithm_id": "weighted_voting",
            "position_id": "weighted_voting.position.SPY.unprotected",
            "symbol": "SPY",
            "side": "LONG",
            "quantity": 3,
            "average_entry_price": 100.0,
            "opened_at": SESSION_OPEN.isoformat(),
            "decision_id": "unprotected-decision",
            "order_intent_id": "unprotected-intent",
            "client_order_id": "unprotected-client",
            "source": "weighted_voting.test.unprotected_fill",
        },
        occurred_at=SESSION_OPEN,
        expected_snapshot_version=snapshot.snapshot_version,
    )
    return None


class AcceptedExecutionService(WeightedVotingService):
    def evaluate_context(self, context, **_kwargs) -> dict:
        proposal = global_proposal_for_context(context)
        response = GlobalGateResponse(
            action="ALLOW",
            maximumAllowedQuantity=proposal.quantity,
            maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
            evaluatedAt=proposal.proposedAt,
            configurationHash="runtime-global-risk",
        )
        application = apply_global_gate_response(proposal, response)
        return {
            "decision": {"decision_id": proposal.decisionId},
            "gateResult": {
                "permission_granted": True,
                "mode": "automatic",
                "reason_codes": (),
                "explanation": "Synthetic accepted runtime gate result.",
            },
            "globalOrderProposal": proposal.model_dump(mode="json"),
            "globalRiskResponse": response.model_dump(mode="json"),
            "globalGateApplication": application.model_dump(mode="json"),
            "signals": (
                {"strategyId": "S2", "shadowRecordsOnly": False, "side": "BUY"},
                {"strategyId": "S3", "shadowRecordsOnly": True, "side": "HOLD"},
            ),
        }


class GlobalRiskOutageService(WeightedVotingService):
    def evaluate_context(self, context, **_kwargs) -> dict:
        proposal = global_proposal_for_context(context)
        return {
            "decision": {"decision_id": proposal.decisionId},
            "gateResult": {
                "permission_granted": True,
                "mode": "automatic",
                "reason_codes": ("weighted_voting.test.global_risk_outage",),
                "explanation": "Synthetic missing global-risk response.",
            },
            "globalOrderProposal": proposal.model_dump(mode="json"),
        }


class FakePaperBroker:
    def __init__(self) -> None:
        self.submit_count = 0

    def verify_paper_account(self) -> bool:
        return True

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        return PaperGatewayBrokerAck(
            clientOrderId=intent.clientOrderId,
            brokerOrderId=f"broker-{intent.clientOrderId}",
            status="ACCEPTED",
            acceptedAt=SESSION_OPEN,
        )

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill:
        return PaperGatewayFill(
            clientOrderId=client_order_id,
            algorithmId="weighted_voting",
            orderIntentId="runtime-auto-intent",
            symbol="SPY",
            side=Signal.BUY,
            filledQuantity=3,
            averageFillPrice=102.0,
            status="FILLED",
            filledAt=SESSION_OPEN,
        )

    def cancel_order(self, client_order_id: str) -> bool:
        return True

    def refresh_positions(self) -> list[dict]:
        return []


class UnreconciledPaperBroker(FakePaperBroker):
    def refresh_positions(self) -> list[dict]:
        return [
            {
                "positionId": "unknown-weighted-position",
                "clientOrderId": "unknown-weighted-client",
                "algorithmId": "weighted_voting",
                "symbol": "SPY",
                "quantity": 5,
                "averageEntryPrice": 100.0,
            }
        ]


class MemoryStore:
    def __init__(self) -> None:
        self.snapshots: dict[str, dict] = {}

    def read_snapshot(self, key: str) -> dict:
        if key not in self.snapshots:
            raise KeyError(key)
        return self.snapshots[key]

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        self.snapshots[key] = snapshot


class FailingWriteStore(MemoryStore):
    def write_snapshot(self, key: str, snapshot: dict) -> None:
        raise RuntimeError(f"simulated persistence outage for {key}")


if __name__ == "__main__":
    unittest.main()
