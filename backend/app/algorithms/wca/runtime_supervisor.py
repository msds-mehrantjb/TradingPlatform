"""Standalone WCA background runtime supervisor and logical workers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from backend.app.algorithms.wca.broker_reconciliation import reconcile_wca_broker
from backend.app.algorithms.wca.contracts import WcaDecision, WcaLatencyTimestamps
from backend.app.algorithms.wca.execution_pipeline import WcaExecutionPipelineInput, run_wca_paper_pipeline_adapter
from backend.app.algorithms.wca.paper_broker import WcaDeterministicPaperBroker, WcaPaperBrokerOutboxAdapter, build_wca_paper_broker_request
from backend.app.algorithms.wca.position_management import manage_wca_position
from backend.app.algorithms.wca.repository import WcaSqliteRepository
from backend.app.algorithms.wca.runtime_commands import WcaRuntimeCommand, WcaRuntimeCommandType, runtime_command
from backend.app.algorithms.wca.runtime_events import WcaFinalizedBarEvent
from backend.app.algorithms.wca.runtime_health import WcaRuntimeHealthSnapshot, healthy_runtime_snapshot
from backend.app.algorithms.wca.runtime_repository import WcaRuntimeRepository
from backend.app.execution import BrokerFillUpdate
from backend.app.gates import BrokerAccountSnapshot


WCA_RUNTIME_SUPERVISOR_VERSION = "wca_background_runtime_supervisor_v1"
WCA_RUNTIME_REQUIRES_OS_PROCESS = True
WCA_RUNTIME_WORKERS = (
    "finalised_bar_consumer",
    "decision_worker",
    "position_and_protective_exit_worker",
    "global_risk_request_worker",
    "execution_outbox_worker",
    "broker_reconciliation_worker",
    "recovery_worker",
    "heartbeat_and_health_worker",
    "end_of_session_worker",
)


@dataclass(frozen=True)
class WcaRuntimeSettings:
    account_id: str = "paper"
    symbol: str = "SPY"
    max_event_queue_depth: int = 200
    max_command_queue_depth: int = 500
    max_event_age_seconds: int = 300
    max_lag_seconds: int = 120
    lease_seconds: int = 30
    poll_seconds: float = 1.0


class WcaRuntimeSupervisor:
    def __init__(
        self,
        *,
        repository: WcaSqliteRepository | None = None,
        runtime_repository: WcaRuntimeRepository | None = None,
        settings: WcaRuntimeSettings | None = None,
        owner_id: str | None = None,
    ) -> None:
        self.repository = repository or WcaSqliteRepository()
        self.runtime_repository = runtime_repository or WcaRuntimeRepository(self.repository)
        self.settings = settings or WcaRuntimeSettings()
        self.owner_id = owner_id or f"wca-runtime-{uuid4().hex}"
        self.recovery_state = "not_started"
        self.workers = (
            RecoveryWorker(self),
            FinalizedBarConsumerWorker(self),
            DecisionWorker(self),
            PositionProtectiveExitWorker(self),
            GlobalRiskRequestWorker(self),
            ExecutionOutboxWorker(self),
            BrokerReconciliationWorker(self),
            EndOfSessionWorker(self),
            HeartbeatHealthWorker(self),
        )

    def publish_finalized_bar_event(self, event: WcaFinalizedBarEvent):
        return self.runtime_repository.publish_finalized_bar_event(
            event,
            account_id=self.settings.account_id,
            max_queue_depth=self.settings.max_event_queue_depth,
            max_event_age_seconds=self.settings.max_event_age_seconds,
        )

    def run_once(self) -> dict[str, Any]:
        results: dict[str, Any] = {"runtimeVersion": WCA_RUNTIME_SUPERVISOR_VERSION, "ownerId": self.owner_id, "workers": {}}
        for worker in self.workers:
            results["workers"][worker.worker_name] = worker.run_once()
        return results

    def run_forever(self, *, max_iterations: int | None = None) -> None:
        iterations = 0
        while max_iterations is None or iterations < max_iterations:
            self.run_once()
            iterations += 1
            time.sleep(self.settings.poll_seconds)

    def health_snapshot(self, *, paused_new_entries: bool, reason_codes: tuple[str, ...]) -> WcaRuntimeHealthSnapshot:
        depths = self.runtime_repository.queue_depths()
        last_bar = self.runtime_repository.last_processed_bar(symbol=self.settings.symbol)
        lag_seconds = max(0.0, (_utc_now() - last_bar).total_seconds()) if last_bar else 0.0
        return healthy_runtime_snapshot(
            queue_depth=depths["events"],
            command_depth=depths["commands"],
            last_processed_bar=last_bar,
            lag_seconds=lag_seconds,
            last_decision_id=self.runtime_repository.last_decision_id(),
            recovery_state=self.recovery_state,
            paused_new_entries=paused_new_entries,
            reason_codes=reason_codes,
        )


class RuntimeWorker:
    worker_name = "runtime_worker"

    def __init__(self, supervisor: WcaRuntimeSupervisor) -> None:
        self.supervisor = supervisor

    @property
    def runtime_repository(self) -> WcaRuntimeRepository:
        return self.supervisor.runtime_repository

    @property
    def repository(self) -> WcaSqliteRepository:
        return self.supervisor.repository


class FinalizedBarConsumerWorker(RuntimeWorker):
    worker_name = "finalised_bar_consumer"

    def run_once(self) -> dict[str, Any]:
        event = self.runtime_repository.claim_next_event(owner_id=self.supervisor.owner_id, lease_seconds=self.supervisor.settings.lease_seconds)
        if event is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.finalized_bar_consumer.idle"]}
        command = WcaRuntimeCommand.from_finalized_bar_event(event, account_id=self.supervisor.settings.account_id)
        result = self.runtime_repository.enqueue_command(command, max_queue_depth=self.supervisor.settings.max_command_queue_depth)
        if result.accepted:
            self.runtime_repository.mark_event_decision_queued(event.event_id, command_id=command.command_id)
        return {"status": result.status, "eventId": event.event_id, "commandId": command.command_id, "reasonCodes": list(result.reason_codes)}


class DecisionWorker(RuntimeWorker):
    worker_name = "decision_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(WcaRuntimeCommandType.FINALIZED_BAR_DECISION, owner_id=self.supervisor.owner_id, lease_seconds=self.supervisor.settings.lease_seconds)
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.decision_worker.idle"]}
        event = WcaFinalizedBarEvent.model_validate(command.payload["event"])
        if not self.runtime_repository.acquire_symbol_lease(symbol=event.symbol, owner_id=self.supervisor.owner_id, ttl_seconds=self.supervisor.settings.lease_seconds):
            self.runtime_repository.block_command(command.command_id, reason_codes=("wca.runtime.symbol_lease_unavailable",))
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": ["wca.runtime.symbol_lease_unavailable"]}
        try:
            result = self._process_decision(command, event)
            self.runtime_repository.complete_command(command.command_id)
            return result
        except Exception as exc:
            self.runtime_repository.fail_command(command.command_id, reason_codes=("wca.runtime.decision_worker.failed", type(exc).__name__))
            raise
        finally:
            self.runtime_repository.release_symbol_lease(symbol=event.symbol, owner_id=self.supervisor.owner_id)

    def _process_decision(self, command: WcaRuntimeCommand, event: WcaFinalizedBarEvent) -> dict[str, Any]:
        configuration = self.repository.read_active_configuration()
        weights = self.repository.read_active_weights(as_of=event.finalized_candle_timestamp)
        if configuration is None or weights is None:
            self.runtime_repository.block_command(command.command_id, reason_codes=("wca.runtime.fail_closed.configuration_or_weights_missing",))
            self.runtime_repository.write_runtime_health(WcaRuntimeHealthSnapshot(status="starting_fail_closed", reason_codes=("wca.runtime.fail_closed.configuration_or_weights_missing",)))
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": ["wca.runtime.fail_closed.configuration_or_weights_missing"]}
        if self.repository.reconciliation_blocks_new_entries(account_id=self.supervisor.settings.account_id, symbol=event.symbol):
            self.runtime_repository.block_command(command.command_id, reason_codes=("wca.runtime.fail_closed.reconciliation_blocks_entries",))
            self.runtime_repository.enqueue_command(
                runtime_command(WcaRuntimeCommandType.POSITION_PROTECTIVE_EXIT, event_id=event.event_id, decision_id=command.decision_id, run_id=command.run_id, reason_codes=("wca.runtime.protective_management.continues",)),
                max_queue_depth=self.supervisor.settings.max_command_queue_depth,
            )
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": ["wca.runtime.fail_closed.reconciliation_blocks_entries"]}
        if event.snapshot is None:
            self.runtime_repository.block_command(command.command_id, reason_codes=("wca.runtime.snapshot_missing",))
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": ["wca.runtime.snapshot_missing"]}
        lag_seconds = max(0.0, (_utc_now() - event.finalized_candle_timestamp.astimezone(timezone.utc)).total_seconds())
        pause_new_entries = lag_seconds > self.supervisor.settings.max_lag_seconds
        pipeline_result = run_wca_paper_pipeline_adapter(
            WcaExecutionPipelineInput(
                run_id=command.run_id,
                decision_id=command.decision_id,
                order_intent_id=f"wca-intent-{event.event_id}",
                snapshot=event.snapshot,
                configuration_version=configuration.configuration_version,
                runtime_mode="automatic_paper",
                configuration=configuration,
                weight_snapshot=weights,
                calibration_tables=self.repository.read_active_confidence_calibrations(symbol=event.symbol, as_of=event.finalized_candle_timestamp),
                global_gate_quantity_cap=0 if pause_new_entries else 2_147_483_647,
                latency_timestamps=WcaLatencyTimestamps(
                    bar_finalization=event.finalized_candle_timestamp,
                    event_publication=event.publication_timestamp,
                    event_receipt=_utc_now(),
                    snapshot_completion=event.snapshot.decision_timestamp,
                ),
            )
        )
        decision = pipeline_result.decision
        self.repository.write_decision_snapshot(decision, run_id=command.run_id)
        self.runtime_repository.complete_event_and_checkpoint(event, decision_id=decision.decision_id, run_id=command.run_id)
        self._enqueue_downstream_commands(event, command, decision)
        health = self.supervisor.health_snapshot(
            paused_new_entries=pause_new_entries,
            reason_codes=("wca.runtime.lag_entry_pause",) if pause_new_entries else ("wca.runtime.healthy",),
        )
        self.runtime_repository.write_runtime_health(health)
        return {"status": "completed", "commandId": command.command_id, "decisionId": decision.decision_id, "pausedNewEntries": pause_new_entries}

    def _enqueue_downstream_commands(self, event: WcaFinalizedBarEvent, command: WcaRuntimeCommand, decision: WcaDecision) -> None:
        latest_close = decision.market_snapshot.candles[-1].close if decision.market_snapshot.candles else None
        common = {"event_id": event.event_id, "account_id": command.account_id, "decision_id": decision.decision_id, "run_id": command.run_id, "symbol": event.symbol}
        position_payload = {**common, "mark_price": latest_close, "data_timestamp": decision.data_timestamp.isoformat()}
        self.runtime_repository.enqueue_command(
            runtime_command(WcaRuntimeCommandType.POSITION_PROTECTIVE_EXIT, payload=position_payload, **common),
            max_queue_depth=self.supervisor.settings.max_command_queue_depth,
        )
        self.runtime_repository.enqueue_command(
            runtime_command(WcaRuntimeCommandType.BROKER_RECONCILIATION, payload=common, priority=80, **common),
            max_queue_depth=self.supervisor.settings.max_command_queue_depth,
        )
        if decision.global_gate_result is not None:
            self.runtime_repository.enqueue_command(
                runtime_command(WcaRuntimeCommandType.GLOBAL_RISK_REQUEST, payload={"decision": decision.model_dump(mode="json"), **common}, priority=30, **common),
                max_queue_depth=self.supervisor.settings.max_command_queue_depth,
            )
        if decision.proposed_order is not None:
            self.runtime_repository.enqueue_command(
                runtime_command(WcaRuntimeCommandType.EXECUTION_OUTBOX, payload={"decision": decision.model_dump(mode="json"), **common}, priority=20, **common),
                max_queue_depth=self.supervisor.settings.max_command_queue_depth,
            )


class PositionProtectiveExitWorker(RuntimeWorker):
    worker_name = "position_and_protective_exit_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(WcaRuntimeCommandType.POSITION_PROTECTIVE_EXIT, owner_id=self.supervisor.owner_id)
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.protective_exit_worker.idle"]}
        mark_price = command.payload.get("mark_price")
        if mark_price is None:
            self.runtime_repository.complete_command(command.command_id, reason_codes=("wca.runtime.protective_exit_management.no_mark_price",))
            return {"status": "completed", "commandId": command.command_id, "reasonCodes": ["wca.runtime.protective_exit_management.no_mark_price"]}
        position = manage_wca_position(
            repository=self.repository,
            account_id=command.account_id,
            symbol=command.symbol,
            mark_price=float(mark_price),
            evaluated_at=_utc_now(),
            emergency_exit=bool(command.payload.get("emergency_exit", False)),
            global_emergency_risk_reduction=bool(command.payload.get("global_emergency_risk_reduction", False)),
        )
        self.runtime_repository.complete_command(command.command_id, reason_codes=("wca.runtime.protective_exit_management.completed", *position.reason_codes))
        return {
            "status": "completed",
            "commandId": command.command_id,
            "openQuantity": position.open_quantity,
            "pendingExitOrders": len(position.pending_exit_orders),
            "circuitBreakerOpen": position.circuit_breaker_open,
            "reasonCodes": ["wca.runtime.protective_exit_management.completed", *position.reason_codes],
        }


class GlobalRiskRequestWorker(RuntimeWorker):
    worker_name = "global_risk_request_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(WcaRuntimeCommandType.GLOBAL_RISK_REQUEST, owner_id=self.supervisor.owner_id)
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.global_risk_worker.idle"]}
        self.runtime_repository.complete_command(command.command_id, reason_codes=("wca.runtime.global_risk_response.already_persisted_with_decision",))
        return {"status": "completed", "commandId": command.command_id, "reasonCodes": ["wca.runtime.global_risk_response.already_persisted_with_decision"]}


class ExecutionOutboxWorker(RuntimeWorker):
    worker_name = "execution_outbox_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(WcaRuntimeCommandType.EXECUTION_OUTBOX, owner_id=self.supervisor.owner_id)
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.execution_outbox_worker.idle"]}
        decision = WcaDecision.model_validate(command.payload["decision"])
        if decision.proposed_order is None:
            self.runtime_repository.block_command(command.command_id, reason_codes=("wca.runtime.execution_outbox.no_order",))
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": ["wca.runtime.execution_outbox.no_order"]}
        idempotency_key = f"wca-runtime-outbox-{decision.proposed_order.order_intent_id}"
        proposed = decision.proposed_order.model_copy(update={"idempotency_key": decision.proposed_order.idempotency_key or idempotency_key, "account_id": command.account_id})
        decision = decision.model_copy(update={"proposed_order": proposed})
        request = build_wca_paper_broker_request(proposed)
        reservation = self.repository.reserve_decision_order_and_outbox(
            decision,
            run_id=command.run_id,
            account_id=command.account_id,
            idempotency_key=proposed.idempotency_key or idempotency_key,
            client_order_id=request.client_order_id,
            request_payload=request.model_dump(mode="json"),
        )
        submission = WcaPaperBrokerOutboxAdapter().process_next_outbox(self.repository, WcaDeterministicPaperBroker(), owner_id=self.supervisor.owner_id)
        self.runtime_repository.complete_command(command.command_id, reason_codes=("wca.runtime.execution_outbox.created", *submission.reason_codes))
        return {
            "status": "completed",
            "commandId": command.command_id,
            "outboxId": reservation.outbox_id,
            "orderState": str(submission.state),
            "submitted": submission.submitted,
            "reasonCodes": ["wca.runtime.execution_outbox.created", *submission.reason_codes],
        }


class BrokerReconciliationWorker(RuntimeWorker):
    worker_name = "broker_reconciliation_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(WcaRuntimeCommandType.BROKER_RECONCILIATION, owner_id=self.supervisor.owner_id)
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.broker_reconciliation_worker.idle"]}
        result = reconcile_wca_broker(
            repository=self.repository,
            broker=_RuntimeEmptyPaperBroker(account_id=command.account_id),
            account_id=command.account_id,
            evaluated_at=_utc_now(),
        )
        self.runtime_repository.complete_command(command.command_id, reason_codes=("wca.runtime.broker_reconciliation.completed", *result.reason_codes))
        return {"status": "completed", "commandId": command.command_id, "reasonCodes": ["wca.runtime.broker_reconciliation.completed", *result.reason_codes]}


class RecoveryWorker(RuntimeWorker):
    worker_name = "recovery_worker"

    def run_once(self) -> dict[str, Any]:
        recovered = self.runtime_repository.recover_expired_work()
        self.supervisor.recovery_state = "completed"
        return {"status": "completed", **recovered, "reasonCodes": ["wca.runtime.recovery.completed"]}


class HeartbeatHealthWorker(RuntimeWorker):
    worker_name = "heartbeat_and_health_worker"

    def run_once(self) -> dict[str, Any]:
        config_ready = self.repository.read_active_configuration() is not None and self.repository.read_active_weights() is not None
        recon_block = self.repository.reconciliation_blocks_new_entries(account_id=self.supervisor.settings.account_id, symbol=self.supervisor.settings.symbol)
        circuit_breaker = self.repository.wca_position_circuit_breaker_open(account_id=self.supervisor.settings.account_id, symbol=self.supervisor.settings.symbol)
        last_bar = self.runtime_repository.last_processed_bar(symbol=self.supervisor.settings.symbol)
        lag_pause = last_bar is not None and (_utc_now() - last_bar).total_seconds() > self.supervisor.settings.max_lag_seconds
        paused = (not config_ready) or recon_block or lag_pause or circuit_breaker
        reason = "wca.runtime.healthy"
        if lag_pause:
            reason = "wca.runtime.lag_entry_pause"
        elif recon_block:
            reason = "wca.runtime.reconciliation_blocks_entries"
        elif circuit_breaker:
            reason = "wca.runtime.position_circuit_breaker"
        elif not config_ready:
            reason = "wca.runtime.starting_fail_closed"
        health = self.supervisor.health_snapshot(paused_new_entries=paused, reason_codes=(reason,))
        self.runtime_repository.write_runtime_health(health)
        return {"status": "completed", "pausedNewEntries": paused, "reasonCodes": [reason]}


class EndOfSessionWorker(RuntimeWorker):
    worker_name = "end_of_session_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(WcaRuntimeCommandType.END_OF_SESSION, owner_id=self.supervisor.owner_id)
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.end_of_session_worker.idle"]}
        self.runtime_repository.complete_command(command.command_id, reason_codes=("wca.runtime.end_of_session.completed",))
        return {"status": "completed", "commandId": command.command_id, "reasonCodes": ["wca.runtime.end_of_session.completed"]}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class _RuntimeEmptyPaperBroker:
    def __init__(self, *, account_id: str) -> None:
        self.account_id = account_id

    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        now = _utc_now()
        return BrokerAccountSnapshot(
            accountId=self.account_id,
            equity=0,
            buyingPower=0,
            realizedPnlToday=0,
            positions=[],
            pendingOrders=[],
            partiallyFilledOrders=[],
            observedAt=now,
            sessionDate=now.date(),
            sourceAuthority="unknown",
        )

    def refresh_order(self, client_order_id: str) -> BrokerFillUpdate | None:
        return None


__all__ = [
    "WCA_RUNTIME_REQUIRES_OS_PROCESS",
    "WCA_RUNTIME_SUPERVISOR_VERSION",
    "WCA_RUNTIME_WORKERS",
    "WcaRuntimeSettings",
    "WcaRuntimeSupervisor",
]
