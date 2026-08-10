import asyncio
import json
import time
import unittest
from datetime import timedelta
from pathlib import Path

from backend.app.algorithms.weighted_voting.acceptance_suite import (
    WEIGHTED_VOTING_SYSTEM_ACCEPTANCE_VERSION,
    build_weighted_voting_system_acceptance_report,
    weighted_voting_system_acceptance_is_complete,
)
from backend.app.algorithms.weighted_voting.broker_reconciliation import (
    WeightedVotingBrokerFillObservation,
    WeightedVotingBrokerPositionObservation,
    reconcile_weighted_voting_broker_observations,
)
from backend.app.algorithms.weighted_voting.catalog import WEIGHTED_VOTING_STRATEGY_CATALOG
from backend.app.algorithms.weighted_voting.position_manager import WeightedVotingPositionManagerService
from backend.app.algorithms.weighted_voting.runtime_supervisor import WeightedVotingEventBus, WeightedVotingRuntimeConfig, WeightedVotingRuntimeSupervisor
from backend.app.algorithms.weighted_voting.service import WeightedVotingService
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayBrokerAck, PaperGatewayFill, PaperOrderGateway
from backend.app.gates import GlobalGateResponse, apply_global_gate_response
from backend.tests.test_weighted_voting_runtime_supervisor import (
    AcceptedExecutionService,
    FakePaperBroker,
    MemoryStore,
    SESSION_OPEN,
    event_from_payload,
    evaluate_payload,
    enable_automatic_entries,
    global_proposal_for_context,
    seeded_inventory,
    validated_rollout_flags,
    validated_rollout_validation,
    weighted_voting_local_gateway,
)


ROOT = Path(__file__).resolve().parents[2]


ACCEPTANCE_STRATEGY_IDS = ("S2", "S3", "S4", "S5", "S6", "S7", "S8", "S9")


class EightSignalAcceptedExecutionService(WeightedVotingService):
    def evaluate_context(self, context, **_kwargs) -> dict:
        proposal = global_proposal_for_context(context)
        response = GlobalGateResponse(
            action="ALLOW",
            maximumAllowedQuantity=proposal.quantity,
            maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
            evaluatedAt=proposal.proposedAt,
            configurationHash="weighted_voting.acceptance.global_risk_allow",
        )
        application = apply_global_gate_response(proposal, response)
        signals = tuple(
            {
                "strategyId": strategy_id,
                "shadowRecordsOnly": False,
                "side": "BUY",
                "weight": round(1.0 / len(ACCEPTANCE_STRATEGY_IDS), 6),
                "reasonCodes": ("weighted_voting.acceptance.strategy_signal_evaluated",),
            }
            for strategy_id in ACCEPTANCE_STRATEGY_IDS
        )
        decision = {
            "algorithm_id": "weighted_voting",
            "decision_id": proposal.decisionId,
            "market_event_symbol": context.finalised_one_minute_market_snapshot.symbol,
            "settings_version": context.effective_settings.settings_version,
            "weight_version": context.active_weight_state.weight_version,
            "inventory_version": context.inventory_snapshot.snapshot_version,
            "signal_count": len(signals),
            "aggregation": {
                "candidate": "BUY",
                "score": 1.0,
                "validCandidate": True,
                "reasonCodes": ("weighted_voting.acceptance.weighted_aggregation_valid_candidate",),
            },
            "noTrade": False,
            "reason_codes": ("weighted_voting.acceptance.decision_complete",),
        }
        self.store.write_snapshot(f"weighted_voting.decisions.{proposal.decisionId}", decision)
        self.store.write_snapshot(
            f"weighted_voting.signals.{proposal.decisionId}",
            {
                "algorithm_id": "weighted_voting",
                "decision_id": proposal.decisionId,
                "signals": signals,
                "reason_codes": ("weighted_voting.acceptance.eight_strategy_signals_recorded",),
            },
        )
        return {
            "decision": {"decision_id": proposal.decisionId},
            "gateResult": {
                "permission_granted": True,
                "mode": "automatic",
                "reason_codes": ("weighted_voting.acceptance.local_gates_passed",),
                "explanation": "Deterministic acceptance path passed local gates.",
            },
            "globalOrderProposal": proposal.model_dump(mode="json"),
            "globalRiskResponse": response.model_dump(mode="json"),
            "globalGateApplication": application.model_dump(mode="json"),
            "signals": signals,
        }


class PartialThenFinalPaperBroker(FakePaperBroker):
    def __init__(self, *, partial_quantity: int = 1, fill_price: float = 102.0) -> None:
        super().__init__()
        self.partial_quantity = partial_quantity
        self.fill_price = fill_price
        self.acknowledged_client_order_ids: list[str] = []

    def submit_bracket_order(self, intent) -> PaperGatewayBrokerAck:
        self.submit_count += 1
        self.acknowledged_client_order_ids.append(intent.clientOrderId)
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
            filledQuantity=self.partial_quantity,
            averageFillPrice=self.fill_price,
            status="PARTIALLY_FILLED",
            filledAt=SESSION_OPEN + timedelta(minutes=95),
        )


class AcceptanceProtectionBroker:
    def __init__(self) -> None:
        self.protective_instructions = []
        self.exit_instructions = []

    def submit_protective_order(self, instruction) -> str:
        self.protective_instructions.append(instruction)
        return f"protective-{len(self.protective_instructions)}-{instruction.client_order_id}"

    def submit_exit_order(self, instruction) -> str:
        self.exit_instructions.append(instruction)
        return f"exit-{len(self.exit_instructions)}-{instruction.client_order_id}"


def acceptance_supervisor(store: MemoryStore, broker: FakePaperBroker) -> tuple[WeightedVotingRuntimeSupervisor, object]:
    inventory = seeded_inventory(store)
    supervisor = WeightedVotingRuntimeSupervisor(
        service=EightSignalAcceptedExecutionService(store=store),
        store=store,
        config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
        event_bus=WeightedVotingEventBus(maxsize=8),
        paper_gateway=weighted_voting_local_gateway(broker, store),
        inventory_repository=inventory,
        rollout_flags=validated_rollout_flags(),
        rollout_validation=validated_rollout_validation(),
    )
    return supervisor, inventory


class WeightedVotingAcceptanceSuiteTest(unittest.TestCase):
    def test_machine_readable_report_covers_every_requested_acceptance_requirement(self) -> None:
        report = build_weighted_voting_system_acceptance_report()

        self.assertEqual(report["algorithmId"], "weighted_voting")
        self.assertEqual(report["version"], WEIGHTED_VOTING_SYSTEM_ACCEPTANCE_VERSION)
        self.assertTrue(report["machineReadable"])
        self.assertTrue(report["complete"])
        self.assertTrue(weighted_voting_system_acceptance_is_complete())
        self.assertEqual(report["counts"], {"pass": 106, "fail": 0})
        self.assertEqual(report["requirementCount"], 106)
        self.assertEqual(report["blockingRequirementIds"], [])
        json.dumps(report, sort_keys=True)

    def test_acceptance_report_has_pass_fail_status_for_every_requirement_and_existing_evidence(self) -> None:
        report = build_weighted_voting_system_acceptance_report()
        requirement_ids = set()
        categories = set()

        for item in report["requirements"]:
            with self.subTest(requirement=item["requirementId"]):
                self.assertNotIn(item["requirementId"], requirement_ids)
                requirement_ids.add(item["requirementId"])
                categories.add(item["category"])
                self.assertIn(item["status"], {"pass", "fail"})
                self.assertTrue(item["evidence"])
                for evidence in item["evidence"]:
                    self.assertTrue((ROOT / evidence).exists(), evidence)

        self.assertEqual(
            categories,
            {
                "Isolation tests",
                "Background-runtime tests",
                "Inventory tests",
                "Settings tests",
                "Strategy tests",
                "Gate and sizing tests",
                "Execution tests",
                "Parity tests",
                "Performance tests",
            },
        )

    def test_strategy_acceptance_matrix_covers_every_active_or_shadow_strategy(self) -> None:
        report = build_weighted_voting_system_acceptance_report()
        requirement_ids = {item["requirementId"] for item in report["requirements"]}
        behaviors = {
            "warm_up",
            "data_readiness",
            "buy",
            "sell",
            "hold",
            "session_boundary",
            "invalidation",
            "stale_data",
            "malformed_data",
            "confidence_bounds",
            "no_future_candle_usage",
        }

        for entry in WEIGHTED_VOTING_STRATEGY_CATALOG:
            if entry.lifecycle not in {"active", "shadow"}:
                continue
            for behavior in behaviors:
                self.assertIn(f"strategy.{entry.strategy_id.lower()}.{behavior}", requirement_ids)

    def test_service_status_exposes_system_acceptance_report(self) -> None:
        status = WeightedVotingService(store=MemoryStore()).status()

        self.assertIn("systemAcceptance", status)
        self.assertEqual(status["systemAcceptance"]["version"], WEIGHTED_VOTING_SYSTEM_ACCEPTANCE_VERSION)
        self.assertTrue(status["systemAcceptance"]["complete"])
        self.assertEqual(status["systemAcceptance"]["counts"]["fail"], 0)

    def test_acceptance_latency_smoke_for_one_minute_workflow(self) -> None:
        store = MemoryStore()
        supervisor = WeightedVotingRuntimeSupervisor(
            service=WeightedVotingService(store=store),
            store=store,
            config=WeightedVotingRuntimeConfig(queue_maxsize=64, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
            event_bus=WeightedVotingEventBus(maxsize=64),
        )
        latencies_ms = []
        for offset in range(12):
            started = time.perf_counter()
            asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=offset))))
            latencies_ms.append((time.perf_counter() - started) * 1000)

        self.assertLess(max(latencies_ms), 60_000.0)
        self.assertLess(sum(latencies_ms) / len(latencies_ms), 5_000.0)
        self.assertFalse(any(key.startswith(("wca.", "meta_strategy.", "voting_ensemble.")) for key in store.snapshots))

    def test_acceptance_latency_smoke_for_decision_to_order_path(self) -> None:
        latencies_ms = []
        broker_submissions = 0
        for offset in range(5):
            store = MemoryStore()
            broker = FakePaperBroker()
            gateway = weighted_voting_local_gateway(broker, store)
            supervisor = WeightedVotingRuntimeSupervisor(
                service=AcceptedExecutionService(store=store),
                store=store,
                config=WeightedVotingRuntimeConfig(queue_maxsize=8, max_queue_lag_seconds=75, heartbeat_interval_seconds=999.0, maintenance_interval_seconds=999.0),
                event_bus=WeightedVotingEventBus(maxsize=8),
                paper_gateway=gateway,
                inventory_repository=seeded_inventory(store),
                rollout_flags=validated_rollout_flags(),
                rollout_validation=validated_rollout_validation(),
            )
            enable_automatic_entries(supervisor)
            asyncio.run(supervisor.process_finalised_bar_event(event_from_payload(evaluate_payload(offset_minutes=offset))))
            item = supervisor.execution_queue.get_nowait()
            started = time.perf_counter()
            supervisor.process_execution_queue_item(item)
            latencies_ms.append((time.perf_counter() - started) * 1000)
            broker_submissions += broker.submit_count

        self.assertEqual(broker_submissions, 5)
        self.assertLess(max(latencies_ms), 60_000.0)
        self.assertLess(sum(latencies_ms) / len(latencies_ms), 5_000.0)

    def test_acceptance_e2e_paper_on_submits_once_protects_fills_closes_and_replays_noop(self) -> None:
        store = MemoryStore()
        broker = PartialThenFinalPaperBroker(partial_quantity=1)
        supervisor, inventory = acceptance_supervisor(store, broker)
        supervisor.metrics.supervisor_started = True
        supervisor.reconcile_broker_inventory(startup=True, reason="weighted_voting.acceptance.startup_reconciliation")
        startup_snapshot = inventory.current_snapshot(now=SESSION_OPEN)

        self.assertGreater(startup_snapshot.allocated_capital, 0.0)
        self.assertTrue(broker.verify_paper_account())
        self.assertTrue(supervisor.metrics.inventory_reconciled)

        enable_automatic_entries(supervisor)
        event = event_from_payload(evaluate_payload(offset_minutes=80))
        self.assertTrue(asyncio.run(supervisor.publish_finalised_bar(event)))
        published_event = asyncio.run(supervisor.event_bus.next_event())

        record = asyncio.run(supervisor.process_finalised_bar_event(published_event))
        item = supervisor.execution_queue.get_nowait()
        risk_record = store.read_snapshot(f"weighted_voting.runtime.risk.decisions.{item.command.order_intent_id}")
        intent_record = store.read_snapshot(f"weighted_voting.runtime.order_intents.{item.command.order_intent_id}")
        outbox_ready = store.read_snapshot(f"weighted_voting.runtime.execution_outbox.{item.command.order_intent_id}")
        signals = store.read_snapshot(f"weighted_voting.signals.{item.command.decision_id}")["signals"]
        decision = store.read_snapshot(f"weighted_voting.decisions.{item.command.decision_id}")

        self.assertEqual(record["status"], "decision_persisted")
        self.assertEqual(len(signals), 8)
        self.assertTrue(decision["aggregation"]["validCandidate"])
        self.assertEqual(risk_record["status"], "approved_for_execution")
        self.assertEqual(risk_record["finalAllowedQuantity"], item.command.quantity)
        self.assertEqual(intent_record["status"], "EXECUTION_OUTBOX_READY_TO_SUBMIT")
        self.assertEqual(outbox_ready["status"], "READY_TO_SUBMIT")
        self.assertGreater(item.command.quantity, 0)

        execution = supervisor.process_execution_queue_item(item)
        outbox_after_partial = store.read_snapshot(f"weighted_voting.runtime.execution_outbox.{item.command.order_intent_id}")
        gateway_result = store.read_snapshot(f"weighted_voting.execution_gateway.automatic_result.{item.command.client_order_id}")
        submission = store.read_snapshot(f"weighted_voting.execution_gateway.submission.{item.command.client_order_id}")
        after_partial = inventory.current_snapshot(now=SESSION_OPEN + timedelta(minutes=95))
        partial_position = after_partial.open_positions[0]

        self.assertEqual(execution["status"], "submitted")
        self.assertEqual(broker.submit_count, 1)
        self.assertEqual(gateway_result["status"], "PARTIALLY_FILLED")
        self.assertEqual(gateway_result["brokerAck"]["status"], "ACCEPTED")
        self.assertEqual(submission["brokerStatus"], "ACCEPTED")
        self.assertEqual(outbox_after_partial["status"], "PARTIALLY_FILLED")
        self.assertEqual(partial_position.quantity, 1)
        self.assertTrue(all(position.algorithm_id == "weighted_voting" for position in after_partial.open_positions))

        protection_broker = AcceptanceProtectionBroker()
        manager = WeightedVotingPositionManagerService(store=store, inventory_repository=inventory, broker=protection_broker)
        protection = manager.protect_position_on_entry_fill(
            position=partial_position,
            effective_settings=supervisor._active_effective_settings(),
            entry_order_id=item.command.client_order_id,
            supporting_strategy_ids=ACCEPTANCE_STRATEGY_IDS,
            protected_at=SESSION_OPEN + timedelta(minutes=96),
        )

        self.assertEqual(protection.quantity, partial_position.quantity)
        self.assertEqual(len(protection_broker.protective_instructions), 1)
        self.assertLessEqual(protection.quantity, abs(partial_position.quantity))

        final_quantity = item.command.quantity - partial_position.quantity
        reconciliation = reconcile_weighted_voting_broker_observations(
            store=store,
            inventory_repository=inventory,
            fills=(
                WeightedVotingBrokerFillObservation(
                    fill_id="acceptance-final-fill",
                    client_order_id=item.command.client_order_id,
                    algorithm_id="weighted_voting",
                    symbol="SPY",
                    side="BUY",
                    quantity=final_quantity,
                    average_fill_price=103.0,
                    filled_at=SESSION_OPEN + timedelta(minutes=97),
                    broker_order_id=f"broker-{item.command.client_order_id}",
                ),
            ),
            positions=(
                WeightedVotingBrokerPositionObservation(
                    client_order_id=item.command.client_order_id,
                    algorithm_id="weighted_voting",
                    symbol="SPY",
                    quantity=item.command.quantity,
                    average_entry_price=102.6666666667,
                    observed_at=SESSION_OPEN + timedelta(minutes=97),
                    broker_position_id=f"broker-position-{item.command.client_order_id}",
                    unrealised_pnl=0.0,
                ),
            ),
            reconciled_at=SESSION_OPEN + timedelta(minutes=97),
        )
        after_final = inventory.current_snapshot(now=SESSION_OPEN + timedelta(minutes=97))
        final_position = after_final.open_positions[0]
        protection_refresh = manager.ensure_position_protection(
            position=final_position,
            effective_settings=supervisor._active_effective_settings(),
            entry_order_id=item.command.client_order_id,
            protected_at=SESSION_OPEN + timedelta(minutes=98),
            supporting_strategy_ids=ACCEPTANCE_STRATEGY_IDS,
        )

        self.assertTrue(reconciliation.inventory_reconciled)
        self.assertEqual(reconciliation.applied_fill_ids, ("acceptance-final-fill",))
        self.assertEqual(final_position.quantity, item.command.quantity)
        self.assertLessEqual(protection_refresh["protection"]["quantity"], abs(final_position.quantity))

        trade = manager.monitor_position(
            position=final_position,
            current_price=final_position.average_entry_price + 3.0,
            observed_at=SESSION_OPEN + timedelta(minutes=120),
            end_of_day=True,
        )
        closed_snapshot = inventory.current_snapshot(now=SESSION_OPEN + timedelta(minutes=120))

        self.assertIsNotNone(trade)
        self.assertEqual(closed_snapshot.open_positions, ())
        self.assertGreater(closed_snapshot.daily_trade_count, 0)
        self.assertGreater(closed_snapshot.realised_pnl, 0.0)
        self.assertTrue(any(key.startswith("weighted_voting.position_manager.trades.") for key in store.snapshots))
        self.assertTrue(any(key.startswith("weighted_voting.inventory.daily_ledgers.") for key in store.snapshots))
        self.assertTrue(any(key.startswith("weighted_voting.runtime.control_audit.") for key in store.snapshots))
        self.assertTrue(any(key.startswith("weighted_voting.runtime.events.") for key in store.snapshots))

        replay = asyncio.run(supervisor.process_finalised_bar_event(event))

        self.assertEqual(replay["status"], "duplicate_noop")
        self.assertEqual(broker.submit_count, 1)
        self.assertEqual(
            len([key for key in store.snapshots if key.startswith("weighted_voting.execution_gateway.automatic_result.")]),
            1,
        )

    def test_acceptance_e2e_paper_off_records_decision_and_audit_without_executable_intent(self) -> None:
        store = MemoryStore()
        broker = PartialThenFinalPaperBroker(partial_quantity=1)
        supervisor, _inventory = acceptance_supervisor(store, broker)
        supervisor.metrics.supervisor_started = True
        supervisor.metrics.inventory_reconciled = True
        supervisor.metrics.processing_lag_seconds = 0.0
        off_control = supervisor.update_runtime_control(
            paper_trading_enabled=False,
            automatic_entries_enabled=False,
            updated_by="weighted_voting.acceptance",
            reason="weighted_voting.acceptance.paper_toggle_off",
        )
        event = event_from_payload(evaluate_payload(offset_minutes=90))
        self.assertTrue(asyncio.run(supervisor.publish_finalised_bar(event)))
        published_event = asyncio.run(supervisor.event_bus.next_event())

        record = asyncio.run(supervisor.process_finalised_bar_event(published_event))
        decision_keys = [key for key in store.snapshots if key.startswith("weighted_voting.decisions.")]
        signal_record = store.read_snapshot(f"weighted_voting.signals.runtime-auto-decision")
        blocked_records = [
            value
            for key, value in store.snapshots.items()
            if key.startswith("weighted_voting.runtime.executions.blocked.")
        ]
        outbox_records = [
            value
            for key, value in store.snapshots.items()
            if key.startswith("weighted_voting.runtime.execution_outbox.") and not key.startswith("weighted_voting.runtime.execution_outbox.attempts.")
        ]
        executable_statuses = {"READY_TO_SUBMIT", "SUBMITTING", "ACKNOWLEDGED", "PARTIALLY_FILLED", "FILLED"}

        self.assertFalse(off_control["paper_trading_enabled"])
        self.assertFalse(off_control["automatic_entries_enabled"])
        self.assertEqual(record["status"], "decision_persisted")
        self.assertEqual(len(decision_keys), 1)
        self.assertEqual(len(signal_record["signals"]), 8)
        self.assertTrue(any(key.startswith("weighted_voting.runtime.control_audit.") for key in store.snapshots))
        self.assertTrue(blocked_records)
        self.assertTrue(any("weighted_voting.runtime.control.paper_trading_disabled" in tuple(record["reason_codes"]) for record in blocked_records))
        self.assertTrue(outbox_records)
        self.assertTrue(all(record["status"] not in executable_statuses for record in outbox_records))
        self.assertTrue(supervisor.execution_queue.empty())
        self.assertEqual(broker.submit_count, 0)
        self.assertFalse(any(key.startswith("weighted_voting.execution_gateway.automatic_result.") for key in store.snapshots))


if __name__ == "__main__":
    unittest.main()
