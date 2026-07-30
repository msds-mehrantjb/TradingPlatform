"""Standalone WCA background runtime supervisor and logical workers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from backend.app.algorithms.wca.broker_reconciliation import reconcile_wca_broker
from backend.app.algorithms.wca.alpaca_paper_broker import WcaAlpacaPaperBroker, WcaAlpacaPaperBrokerConfigurationError
from backend.app.algorithms.wca.contracts import (
    GlobalGateResult,
    WcaAggregationResult,
    WcaDecision,
    WcaEvaluationStatus,
    WcaGateStatus,
    WcaLatencyTimestamps,
    WcaLocalGateResult,
    WcaMarketStatus,
    WcaOrderStatus,
    WcaOrderValidationContext,
    WcaRuntimeMode,
    WcaSide,
    WcaSizingResult,
    coerce_wca_runtime_mode,
)
from backend.app.algorithms.wca.execution_pipeline import WcaExecutionPipelineInput, run_wca_paper_pipeline_adapter
from backend.app.algorithms.wca.paper_account import validate_wca_automatic_paper_account
from backend.app.algorithms.wca.paper_broker import WcaPaperBrokerOrderRequest, WcaPaperBrokerOutboxAdapter, WcaPaperBrokerTimeout, build_wca_paper_broker_request
from backend.app.algorithms.wca.position_management import manage_wca_position
from backend.app.algorithms.wca.market_calendar import WcaMarketCalendar
from backend.app.algorithms.wca.repository import WcaInventoryLedgerEvent, WcaSqliteRepository
from backend.app.algorithms.wca.runtime_state import WcaAuthoritativeRuntimeState, load_wca_authoritative_runtime_state
from backend.app.algorithms.wca.runtime_commands import WcaRuntimeCommand, WcaRuntimeCommandType, runtime_command
from backend.app.algorithms.wca.runtime_events import WcaFinalizedBarEvent
from backend.app.algorithms.wca.runtime_health import WcaRuntimeHealthSnapshot, critical_health_reason_codes, healthy_runtime_snapshot
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
    runtime_mode: WcaRuntimeMode | str = WcaRuntimeMode.AUTOMATIC_PAPER
    max_event_queue_depth: int = 200
    max_command_queue_depth: int = 500
    max_event_age_seconds: int = 300
    max_state_age_seconds: int = 120
    max_finalized_bar_age_seconds: int | None = None
    max_quote_age_seconds: int = 15
    max_authoritative_account_state_age_seconds: int | None = None
    max_reconciliation_age_seconds: int = 120
    max_queue_delay_seconds: int = 20
    max_clock_skew_seconds: int = 2
    max_worker_heartbeat_age_seconds: int = 60
    max_lag_seconds: int = 120
    lease_seconds: int = 30
    poll_seconds: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "runtime_mode", coerce_wca_runtime_mode(self.runtime_mode))
        if self.max_finalized_bar_age_seconds is None:
            object.__setattr__(self, "max_finalized_bar_age_seconds", self.max_lag_seconds)
        if self.max_authoritative_account_state_age_seconds is None:
            object.__setattr__(self, "max_authoritative_account_state_age_seconds", self.max_state_age_seconds)


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
            ConfigurationActivationWorker(self),
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
        prior = self.runtime_repository.read_latest_runtime_health()
        prior_critical = _prior_critical_health_reasons(prior)
        prior_pause = prior is not None and prior.paused_new_entries
        prior_pause = prior is not None and prior.paused_new_entries and not _clean_reconciliation_clears_prior_pause(prior, reason_codes)
        if (prior_critical or prior_pause) and not paused_new_entries and all(code in {"wca.runtime.healthy", "wca.runtime.broker_reconciliation.clean"} or code.startswith("wca.broker_reconciliation.") for code in reason_codes):
            paused_new_entries = True
            reason_codes = prior.reason_codes if prior is not None else prior_critical
        depths = self.runtime_repository.queue_depths()
        ages = self.runtime_repository.queue_ages()
        last_bar = self.runtime_repository.last_processed_bar(symbol=self.settings.symbol)
        lag_seconds = max(0.0, (_utc_now() - last_bar).total_seconds()) if last_bar else 0.0
        return healthy_runtime_snapshot(
            queue_depth=depths["events"],
            command_depth=depths["commands"],
            max_queue_age_seconds=ages["maximum"],
            last_processed_bar=last_bar,
            lag_seconds=lag_seconds,
            last_decision_id=self.runtime_repository.last_decision_id(),
            recovery_state=self.recovery_state,
            paused_new_entries=paused_new_entries,
            reason_codes=reason_codes,
            latency_summary=self.runtime_repository.read_latency_summaries(account_id=self.settings.account_id, symbol=self.settings.symbol),
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
            reasons = (
                "wca.runtime.fail_closed.configuration_or_weights_missing",
                "wca.runtime.health.configuration_ready" if configuration is None else "wca.runtime.health.weight_calibration_ready",
            )
            self.runtime_repository.block_command(command.command_id, reason_codes=tuple(dict.fromkeys(reasons)))
            self.runtime_repository.enqueue_command(
                runtime_command(WcaRuntimeCommandType.POSITION_PROTECTIVE_EXIT, event_id=event.event_id, decision_id=command.decision_id, run_id=command.run_id, reason_codes=("wca.runtime.protective_management.continues",)),
                max_queue_depth=self.supervisor.settings.max_command_queue_depth,
            )
            self.runtime_repository.write_runtime_health(WcaRuntimeHealthSnapshot(status="starting_fail_closed", paused_new_entries=True, protective_management_active=True, configuration_ready=configuration is not None, weight_calibration_ready=weights is not None, reason_codes=tuple(dict.fromkeys(reasons))))
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": ["wca.runtime.fail_closed.configuration_or_weights_missing"]}
        if event.snapshot is None:
            self.runtime_repository.block_command(command.command_id, reason_codes=("wca.runtime.snapshot_missing",))
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": ["wca.runtime.snapshot_missing"]}
        state = load_wca_authoritative_runtime_state(
            self.repository,
            broker_account_id=self.supervisor.settings.account_id,
            symbol=event.symbol,
            state_timestamp=event.finalized_candle_timestamp,
            maximum_permitted_state_age_seconds=int(self.supervisor.settings.max_authoritative_account_state_age_seconds or self.supervisor.settings.max_state_age_seconds),
        )
        if not state.fresh or not state.account_wide_entry_permission:
            decision = self._hold_decision(command, event, state, configuration_hash=configuration.content_hash, weight_version=weights.weight_version)
            self.repository.write_decision_snapshot(decision, run_id=command.run_id)
            self.runtime_repository.complete_event_and_checkpoint(event, decision_id=decision.decision_id, run_id=command.run_id)
            self._enqueue_downstream_commands(event, command, decision)
            self.runtime_repository.write_runtime_health(
                self.supervisor.health_snapshot(
                    paused_new_entries=True,
                    reason_codes=("wca.runtime.fail_closed.authoritative_state_blocked", *state.reason_codes),
                )
            )
            return {
                "status": "blocked",
                "commandId": command.command_id,
                "decisionId": decision.decision_id,
                "authoritativeStateHash": state.state_hash,
                "reasonCodes": ["wca.runtime.fail_closed.authoritative_state_blocked", *state.reason_codes],
            }
        entry_health = _evaluate_entry_health(
            self.supervisor,
            event=event,
            state=state,
            configuration_ready=True,
            weight_calibration_ready=True,
        )
        health_block_reasons = critical_health_reason_codes(entry_health)
        if self.repository.reconciliation_blocks_new_entries(account_id=self.supervisor.settings.account_id, symbol=event.symbol):
            self.runtime_repository.block_command(command.command_id, reason_codes=("wca.runtime.fail_closed.reconciliation_blocks_entries",))
            self.runtime_repository.enqueue_command(
                runtime_command(WcaRuntimeCommandType.POSITION_PROTECTIVE_EXIT, event_id=event.event_id, decision_id=command.decision_id, run_id=command.run_id, reason_codes=("wca.runtime.protective_management.continues",)),
                max_queue_depth=self.supervisor.settings.max_command_queue_depth,
            )
            return {"status": "blocked", "commandId": command.command_id, "reasonCodes": ["wca.runtime.fail_closed.reconciliation_blocks_entries"]}
        lag_seconds = max(0.0, (_utc_now() - event.finalized_candle_timestamp.astimezone(timezone.utc)).total_seconds())
        freshness_reasons = _entry_freshness_reason_codes(self.supervisor, event=event, state=state, lag_seconds=lag_seconds)
        pause_new_entries = bool(freshness_reasons or health_block_reasons)
        entry_block_reasons = tuple(dict.fromkeys((*freshness_reasons, *health_block_reasons)))
        now = _utc_now()
        pipeline_result = run_wca_paper_pipeline_adapter(
            WcaExecutionPipelineInput(
                run_id=command.run_id,
                decision_id=command.decision_id,
                order_intent_id=f"wca-intent-{event.event_id}",
                snapshot=event.snapshot,
                configuration_version=configuration.configuration_version,
                runtime_mode=self.supervisor.settings.runtime_mode,
                configuration=configuration,
                weight_snapshot=weights,
                calibration_tables=self.repository.read_active_confidence_calibrations(symbol=event.symbol, as_of=event.finalized_candle_timestamp),
                account_id=state.broker_account_id,
                trades_today=_required_int(state.daily_trade_count, "daily_trade_count"),
                open_position=state.to_open_position(),
                realized_daily_loss=_required_float(state.daily_loss, "daily_loss"),
                account_equity=_required_float(state.equity, "equity"),
                available_buying_power=_required_float(state.buying_power, "buying_power"),
                remaining_allocated_risk_budget=state.remaining_portfolio_risk,
                global_gate_quantity_cap=0 if pause_new_entries else state.maximum_approved_quantity,
                approved_risk_budget=state.remaining_portfolio_risk,
                total_account_exposure_snapshot=state.global_risk.get("riskState", {}),
                current_wca_attributed_exposure=(state.current_quantity or 0) * (state.average_entry_price or 0.0),
                authoritative_state_version=state.state_version,
                authoritative_state_hash=state.state_hash,
                authoritative_state_reason_codes=state.reason_codes,
                latency_timestamps=WcaLatencyTimestamps(
                    candle_open=event.snapshot.candles[-1].timestamp if event.snapshot.candles else event.finalized_candle_timestamp,
                    candle_close=event.finalized_candle_timestamp,
                    bar_finalization=event.finalized_candle_timestamp,
                    event_publication=event.publication_timestamp,
                    event_queue_enqueued=event.publication_timestamp,
                    event_receipt=now,
                    event_claimed=now,
                    decision_start=now,
                    snapshot_construction_start=event.publication_timestamp,
                    snapshot_completion=event.snapshot.decision_timestamp,
                ),
            )
        )
        decision = pipeline_result.decision
        self.repository.write_decision_snapshot(decision, run_id=command.run_id)
        self.runtime_repository.record_latency_snapshot(decision.latency, account_id=command.account_id, symbol=event.symbol, timestamp=decision.decision_timestamp)
        self.runtime_repository.complete_event_and_checkpoint(event, decision_id=decision.decision_id, run_id=command.run_id)
        self._enqueue_downstream_commands(event, command, decision)
        health = entry_health.model_copy(
            update={
                "status": "protective_only" if pause_new_entries else "healthy",
                "paused_new_entries": pause_new_entries,
                "last_decision_id": decision.decision_id,
                "reason_codes": entry_block_reasons or ("wca.runtime.healthy",),
                "latency_summary": self.runtime_repository.read_latency_summaries(account_id=command.account_id, symbol=event.symbol),
            }
        )
        self.runtime_repository.write_runtime_health(health)
        return {"status": "completed", "commandId": command.command_id, "decisionId": decision.decision_id, "pausedNewEntries": pause_new_entries, "reasonCodes": list(entry_block_reasons or ("wca.runtime.healthy",))}

    def _hold_decision(self, command: WcaRuntimeCommand, event: WcaFinalizedBarEvent, state: WcaAuthoritativeRuntimeState, *, configuration_hash: str, weight_version: str) -> WcaDecision:
        if event.snapshot is None:
            raise ValueError("cannot persist WCA runtime-state HOLD without a market snapshot")
        reason_codes = ("wca.runtime.authoritative_state_unavailable_hold", *state.reason_codes)
        market_status = WcaMarketStatus(
            status=WcaEvaluationStatus.DEGRADED,
            input_timestamp=event.snapshot.data_timestamp,
            reason_codes=reason_codes,
            explanation="WCA automatic decision held because authoritative runtime state was unavailable, stale, inconsistent, or not entry-permitted.",
        )
        hold_gate = WcaLocalGateResult(
            gate_id="authoritative_runtime_state",
            status=WcaGateStatus.FAIL,
            blocks_entry=True,
            reason_code="wca.runtime.authoritative_state_blocked",
            detail="Authoritative runtime state blocks new WCA entries.",
            evaluated_value=state.freshness_result,
            required_value="PASS",
            reason_codes=reason_codes,
            explanation="WCA automatic entries require fresh broker, inventory, daily-state, and reconciliation state.",
        )
        aggregation = WcaAggregationResult(
            signal=WcaSide.HOLD,
            decision_label="HOLD",
            pre_gate_decision=WcaSide.HOLD,
            post_local_gate_decision=WcaSide.HOLD,
            buy_score=0,
            sell_score=0,
            net_score=0,
            active_weight=0,
            normalized_net_score=0,
            active_strategy_count=0,
            winner_edge=0,
            buy_agreement=0,
            sell_agreement=0,
            buy_average_confidence=0,
            sell_average_confidence=0,
            strategy_evaluations=(),
            reason_codes=reason_codes,
        )
        sizing = WcaSizingResult(
            final_quantity=0,
            risk_dollars=0,
            stop_distance=0,
            shares_by_risk=0,
            shares_by_order=0,
            shares_by_capital=0,
            shares_by_buying_power=0,
            shares_by_liquidity=0,
            limiting_factor="authoritative_runtime_state",
            blocked_reason="wca.runtime.authoritative_state_blocked",
            side=WcaSide.HOLD,
            reason_codes=reason_codes,
        )
        gate = GlobalGateResult(
            status=WcaGateStatus.FAIL,
            proposed_quantity=0,
            allowed_quantity=0,
            entry_permitted=False,
            risk_reducing_exit_permitted=state.account_wide_exit_permission,
            reason_codes=reason_codes,
            explanation="Global entry permission is denied until authoritative WCA runtime state is fresh.",
        )
        decision = WcaDecision(
            decision_id=command.decision_id,
            configuration_version=state.wca_configuration_version,
            configuration_hash=configuration_hash,
            weight_version=weight_version,
            data_timestamp=event.snapshot.data_timestamp,
            decision_timestamp=event.snapshot.decision_timestamp,
            market_snapshot=event.snapshot,
            market_status=market_status,
            runtime_mode=self.supervisor.settings.runtime_mode,
            called_module_versions={"authoritative_runtime_state": state.state_version},
            hard_filter_results=(hold_gate,),
            aggregation=aggregation,
            local_gates=(hold_gate,),
            sizing=sizing,
            global_gate_result=gate,
            authoritative_state_version=state.state_version,
            authoritative_state_hash=state.state_hash,
            authoritative_state_reason_codes=state.reason_codes,
            reason_codes=reason_codes,
        )
        return decision.model_copy(update={"decision_hash": decision.deterministic_hash()})

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
        paper_account = validate_wca_automatic_paper_account(account_id=command.account_id)
        if not paper_account.verified:
            self.runtime_repository.block_command(command.command_id, reason_codes=("wca.runtime.execution_outbox.automatic_paper_account_blocked", *paper_account.reason_codes))
            self.runtime_repository.write_runtime_health(
                self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=("wca.runtime.execution_outbox.automatic_paper_account_blocked", *paper_account.reason_codes))
            )
            return {
                "status": "blocked",
                "commandId": command.command_id,
                "submitted": False,
                "reasonCodes": ["wca.runtime.execution_outbox.automatic_paper_account_blocked", *paper_account.reason_codes],
            }
        reservation = self.repository.reserve_decision_order_and_outbox(
            decision,
            run_id=command.run_id,
            account_id=command.account_id,
            idempotency_key=proposed.idempotency_key or idempotency_key,
            client_order_id=request.client_order_id,
            request_payload=request.model_dump(mode="json"),
            final_validation_context=_runtime_order_validation_context(command, decision, request, automatic_paper_enabled=paper_account.verified),
        )
        broker: WcaAlpacaPaperBroker | None = None
        try:
            broker = WcaAlpacaPaperBroker.from_env(account_id=command.account_id)
            verified, broker_reason_codes = broker.verify_account_and_endpoint_identity()
            if not verified:
                reasons = ("wca.runtime.execution_outbox.alpaca_paper_broker_blocked", *broker_reason_codes)
                self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
                self.runtime_repository.write_runtime_health(
                    self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons)
                )
                return {
                    "status": "blocked",
                    "commandId": command.command_id,
                    "outboxId": reservation.outbox_id,
                    "submitted": False,
                    "reasonCodes": list(reasons),
                }
            submission = WcaPaperBrokerOutboxAdapter().process_next_outbox(self.repository, broker, owner_id=self.supervisor.owner_id)
        except WcaAlpacaPaperBrokerConfigurationError as exc:
            reason_text = str(exc)
            broker_reason_codes = tuple(code for code in reason_text.split(";") if code)
            reasons = ("wca.runtime.execution_outbox.alpaca_paper_broker_blocked", *broker_reason_codes)
            self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(
                self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons)
            )
            return {
                "status": "blocked",
                "commandId": command.command_id,
                "outboxId": reservation.outbox_id,
                "submitted": False,
                "reasonCodes": list(reasons),
            }
        finally:
            if broker is not None:
                broker.close()
        self.runtime_repository.complete_command(command.command_id, reason_codes=("wca.runtime.execution_outbox.created", *submission.reason_codes))
        self.runtime_repository.enqueue_command(
            runtime_command(
                WcaRuntimeCommandType.BROKER_RECONCILIATION,
                account_id=command.account_id,
                symbol=command.symbol,
                decision_id=decision.decision_id,
                run_id=command.run_id,
                payload={"after": "submission", "outbox_id": reservation.outbox_id, "submitted": submission.submitted},
                priority=30,
                reason_codes=("wca.runtime.reconciliation.scheduled_after_submission",),
            ),
            max_queue_depth=self.supervisor.settings.max_command_queue_depth,
        )
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
            if self.repository.reconciliation_blocks_new_entries(account_id=self.supervisor.settings.account_id, symbol=self.supervisor.settings.symbol):
                paper_account = validate_wca_automatic_paper_account(account_id=self.supervisor.settings.account_id)
                if paper_account.verified:
                    return self._run_reconciliation(None, account_id=self.supervisor.settings.account_id)
            return {"status": "idle", "reasonCodes": ["wca.runtime.broker_reconciliation_worker.idle"]}
        return self._run_reconciliation(command, account_id=command.account_id)

    def _run_reconciliation(self, command: WcaRuntimeCommand | None, *, account_id: str) -> dict[str, Any]:
        broker: WcaAlpacaPaperBroker | None = None
        try:
            broker = WcaAlpacaPaperBroker.from_env(account_id=account_id)
            verified, broker_reason_codes = broker.verify_account_and_endpoint_identity()
            if not verified:
                reasons = ("wca.runtime.broker_reconciliation.alpaca_paper_broker_blocked", *broker_reason_codes)
                if command is not None:
                    self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
                self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons))
                return {"status": "blocked", "commandId": command.command_id if command is not None else None, "reasonCodes": list(reasons)}
            result = reconcile_wca_broker(
                repository=self.repository,
                broker=broker,
                account_id=account_id,
                evaluated_at=_utc_now(),
            )
        except WcaAlpacaPaperBrokerConfigurationError as exc:
            broker_reason_codes = tuple(code for code in str(exc).split(";") if code)
            reasons = ("wca.runtime.broker_reconciliation.alpaca_paper_broker_blocked", *broker_reason_codes)
            if command is not None:
                self.runtime_repository.block_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons))
            return {"status": "blocked", "commandId": command.command_id if command is not None else None, "reasonCodes": list(reasons)}
        finally:
            if broker is not None:
                broker.close()
        if result.discrepancies:
            self.runtime_repository.write_runtime_health(
                self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=("wca.runtime.broker_reconciliation.discrepancies_block_entries", *result.reason_codes))
            )
        else:
            self.runtime_repository.write_runtime_health(
                self.supervisor.health_snapshot(paused_new_entries=False, reason_codes=("wca.runtime.broker_reconciliation.clean", *result.reason_codes))
            )
        if command is None:
            return {"status": "completed", "commandId": None, "reasonCodes": ["wca.runtime.broker_reconciliation.startup_completed", *result.reason_codes]}
        self.runtime_repository.complete_command(command.command_id, reason_codes=("wca.runtime.broker_reconciliation.completed", *result.reason_codes))
        return {"status": "completed", "commandId": command.command_id, "reasonCodes": ["wca.runtime.broker_reconciliation.completed", *result.reason_codes]}


class RecoveryWorker(RuntimeWorker):
    worker_name = "recovery_worker"

    def run_once(self) -> dict[str, Any]:
        recovered = self.runtime_repository.recover_expired_work()
        self.supervisor.recovery_state = "completed"
        if recovered.get("commands_requeued", 0) or recovered.get("events_requeued", 0):
            self.runtime_repository.enqueue_command(
                runtime_command(
                    WcaRuntimeCommandType.BROKER_RECONCILIATION,
                    account_id=self.supervisor.settings.account_id,
                    symbol=self.supervisor.settings.symbol,
                    payload={"after": "worker_recovery", **recovered},
                    priority=5,
                    reason_codes=("wca.runtime.reconciliation.scheduled_after_worker_recovery",),
                ),
                max_queue_depth=self.supervisor.settings.max_command_queue_depth,
            )
        return {"status": "completed", **recovered, "reasonCodes": ["wca.runtime.recovery.completed"]}


class ConfigurationActivationWorker(RuntimeWorker):
    worker_name = "configuration_activation_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(WcaRuntimeCommandType.CONFIGURATION_ACTIVATION, owner_id=self.supervisor.owner_id)
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.configuration_activation_worker.idle"]}
        version = str(command.payload.get("configuration_version") or command.payload.get("configurationVersion") or command.decision_id)
        boundary = _payload_datetime(command.payload.get("candle_timestamp") or command.payload.get("finalized_candle_timestamp") or command.payload.get("activation_timestamp")) or _utc_now()
        try:
            activated = self.repository.activate_configuration_version_at_candle_boundary(version, candle_timestamp=boundary)
        except Exception as exc:
            reasons = ("wca.runtime.configuration_activation.failed", type(exc).__name__)
            self.runtime_repository.fail_command(command.command_id, reason_codes=reasons)
            self.runtime_repository.write_runtime_health(self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=reasons))
            return {"status": "failed", "commandId": command.command_id, "reasonCodes": list(reasons)}
        reasons = ("wca.runtime.configuration_activation.applied_at_candle_boundary",)
        self.runtime_repository.complete_command(command.command_id, reason_codes=reasons)
        return {
            "status": "completed",
            "commandId": command.command_id,
            "configurationVersion": activated.configuration_version,
            "activationTimestamp": activated.activation_timestamp.isoformat() if activated.activation_timestamp else "",
            "reasonCodes": list(reasons),
        }


class HeartbeatHealthWorker(RuntimeWorker):
    worker_name = "heartbeat_and_health_worker"

    def run_once(self) -> dict[str, Any]:
        prior = self.runtime_repository.read_latest_runtime_health()
        prior_critical = _prior_critical_health_reasons(prior)
        prior_pause = prior is not None and prior.paused_new_entries
        config_ready = self.repository.read_active_configuration() is not None and self.repository.read_active_weights() is not None
        recon_block = self.repository.reconciliation_blocks_new_entries(account_id=self.supervisor.settings.account_id, symbol=self.supervisor.settings.symbol)
        circuit_breaker = self.repository.wca_position_circuit_breaker_open(account_id=self.supervisor.settings.account_id, symbol=self.supervisor.settings.symbol)
        last_bar = self.runtime_repository.last_processed_bar(symbol=self.supervisor.settings.symbol)
        lag_pause = last_bar is not None and (_utc_now() - last_bar).total_seconds() > int(self.supervisor.settings.max_finalized_bar_age_seconds or self.supervisor.settings.max_lag_seconds)
        paused = (not config_ready) or recon_block or lag_pause or circuit_breaker or bool(prior_critical) or prior_pause
        reason = "wca.runtime.healthy"
        if prior_critical or prior_pause:
            reason = (prior_critical or prior.reason_codes if prior is not None else ("wca.runtime.health.prior_pause",))[0]
        elif lag_pause:
            reason = "wca.runtime.lag_entry_pause"
        elif recon_block:
            reason = "wca.runtime.reconciliation_blocks_entries"
        elif circuit_breaker:
            reason = "wca.runtime.position_circuit_breaker"
        elif not config_ready:
            reason = "wca.runtime.starting_fail_closed"
        if prior is not None and (prior_critical or prior_pause):
            now = _utc_now()
            heartbeats = {**prior.worker_heartbeats, self.worker_name: now}
            health = prior.model_copy(
                update={
                    "heartbeat_at": now,
                    "status": "protective_only",
                    "paused_new_entries": True,
                    "protective_management_active": True,
                    "worker_heartbeats": heartbeats,
                    "reason_codes": prior.reason_codes,
                    "latency_summary": self.runtime_repository.read_latency_summaries(account_id=self.supervisor.settings.account_id, symbol=self.supervisor.settings.symbol),
                }
            )
        else:
            health = self.supervisor.health_snapshot(paused_new_entries=paused, reason_codes=(reason,))
        self.runtime_repository.write_runtime_health(health)
        return {"status": "completed", "pausedNewEntries": paused, "reasonCodes": [reason]}


class EndOfSessionWorker(RuntimeWorker):
    worker_name = "end_of_session_worker"

    def run_once(self) -> dict[str, Any]:
        command = self.runtime_repository.claim_next_command(WcaRuntimeCommandType.END_OF_SESSION, owner_id=self.supervisor.owner_id)
        if command is None:
            return {"status": "idle", "reasonCodes": ["wca.runtime.end_of_session_worker.idle"]}
        broker: WcaAlpacaPaperBroker | None = None
        try:
            broker = WcaAlpacaPaperBroker.from_env(account_id=command.account_id)
            verified, broker_reason_codes = broker.verify_account_and_endpoint_identity()
            if not verified:
                reasons = ("wca.runtime.end_of_session.alpaca_paper_broker_blocked", *broker_reason_codes)
                return _fail_end_of_session(self.supervisor, command, reasons, evidence={"stage": "broker_identity"})
            result = process_wca_end_of_session(
                repository=self.repository,
                runtime_repository=self.runtime_repository,
                broker=broker,
                command=command,
                max_queue_depth=self.supervisor.settings.max_command_queue_depth,
                now=_payload_datetime(command.payload.get("evaluated_at")) or _utc_now(),
            )
        except WcaAlpacaPaperBrokerConfigurationError as exc:
            reasons = ("wca.runtime.end_of_session.alpaca_paper_broker_blocked", *(code for code in str(exc).split(";") if code))
            return _fail_end_of_session(self.supervisor, command, reasons, evidence={"stage": "broker_configuration"})
        finally:
            if broker is not None:
                broker.close()
        if result["verified"]:
            self.runtime_repository.complete_command(command.command_id, reason_codes=tuple(result["reasonCodes"]))
            self.runtime_repository.write_runtime_health(
                self.supervisor.health_snapshot(paused_new_entries=True, reason_codes=tuple(result["reasonCodes"]))
            )
            return {"status": "completed", "commandId": command.command_id, **result}
        return _fail_end_of_session(self.supervisor, command, tuple(result["reasonCodes"]), evidence=result)


def process_wca_end_of_session(
    *,
    repository: WcaSqliteRepository,
    runtime_repository: WcaRuntimeRepository,
    broker: Any,
    command: WcaRuntimeCommand,
    max_queue_depth: int,
    now: datetime,
    calendar: WcaMarketCalendar | None = None,
) -> dict[str, Any]:
    evaluated = now.astimezone(timezone.utc)
    market_calendar = calendar or WcaMarketCalendar()
    session = market_calendar.session_for(evaluated)
    config = repository.read_active_configuration()
    cutoff_minutes = config.execution.entry_cutoff_minutes if config is not None else 15 * 60 + 30
    if session is None:
        cutoff_reached = True
    else:
        local_evaluated = evaluated.astimezone(session.market_close.tzinfo)
        cutoff_reached = (local_evaluated.hour * 60 + local_evaluated.minute) >= cutoff_minutes or market_calendar.should_flatten(evaluated, buffer_minutes=5)
    reasons: list[str] = ["wca.runtime.end_of_session.started", "wca.runtime.end_of_session.entries_paused"]
    evidence: dict[str, Any] = {
        "command_id": command.command_id,
        "account_id": command.account_id,
        "symbol": command.symbol,
        "evaluated_at": evaluated.isoformat(),
        "session_date": session.session_date.isoformat() if session is not None else evaluated.date().isoformat(),
        "early_close": bool(session and session.is_early_close),
        "market_holiday_or_closed": session is None,
        "entry_cutoff_minutes": cutoff_minutes,
        "entry_cutoff_reached": cutoff_reached,
    }
    _enqueue_reconciliation(runtime_repository, command, max_queue_depth=max_queue_depth, marker="before_end_of_session_flatten", priority=1)
    first_reconciliation = reconcile_wca_broker(repository=repository, broker=broker, account_id=command.account_id, evaluated_at=evaluated)
    evidence["first_reconciliation_id"] = first_reconciliation.reconciliation_id
    evidence["first_discrepancies"] = [row.model_dump(mode="json") for row in first_reconciliation.discrepancies]
    try:
        cancelled = broker.cancel_all_wca_entry_orders() if hasattr(broker, "cancel_all_wca_entry_orders") else ()
    except WcaPaperBrokerTimeout as exc:
        reasons.append("wca.runtime.end_of_session.entry_cancel_timeout")
        evidence["cancel_error"] = str(exc)
        cancelled = ()
    except Exception as exc:
        reasons.append("wca.runtime.end_of_session.entry_cancel_failed")
        evidence["cancel_error"] = str(exc)
        cancelled = ()
    evidence["cancelled_entry_orders"] = len(cancelled or ())
    _mark_local_entry_orders_cancelled(repository, account_id=command.account_id, symbol=command.symbol, evidence={"phase": "end_of_session"})
    processed_fills = _process_observed_fills(repository, broker)
    evidence["processed_fills"] = processed_fills
    flattened = _flatten_local_wca_position(repository, broker, command=command, evaluated_at=evaluated)
    evidence["flatten"] = flattened
    if flattened["status"] == "filled":
        _record_end_of_session_flatten_event(repository, command=command, evaluated_at=evaluated, flatten=flattened)
    if flattened["status"] == "rejected":
        reasons.append("wca.runtime.end_of_session.flatten_rejected")
    elif flattened["status"] == "timeout":
        reasons.append("wca.runtime.end_of_session.flatten_timeout")
    elif flattened["status"] == "failed":
        reasons.append("wca.runtime.end_of_session.flatten_failed")
    elif flattened["status"] == "already_flat":
        reasons.append("wca.runtime.end_of_session.already_flat")
    else:
        reasons.append("wca.runtime.end_of_session.flatten_submitted")
    second_reconciliation = reconcile_wca_broker(repository=repository, broker=broker, account_id=command.account_id, evaluated_at=evaluated + timedelta(microseconds=1))
    evidence["final_reconciliation_id"] = second_reconciliation.reconciliation_id
    evidence["final_discrepancies"] = [row.model_dump(mode="json") for row in second_reconciliation.discrepancies]
    verification = _verify_end_of_session(repository, broker, command=command)
    evidence["verification"] = verification
    if verification["verified"]:
        _record_end_of_session_evidence(repository, command=command, evaluated_at=evaluated, evidence=evidence, verified=True)
        reasons.append("wca.runtime.end_of_session.verified_flat")
        reasons.append("wca.runtime.end_of_session.completed")
        return {"verified": True, "reasonCodes": reasons, "evidence": evidence}
    reasons.extend(verification["blocking_reasons"])
    reasons.append("wca.runtime.end_of_session.failed")
    _record_end_of_session_evidence(repository, command=command, evaluated_at=evaluated, evidence=evidence, verified=False)
    _enqueue_reconciliation(runtime_repository, command, max_queue_depth=max_queue_depth, marker="after_failed_end_of_session", priority=1)
    return {"verified": False, "reasonCodes": tuple(dict.fromkeys(reasons)), "evidence": evidence}


def _process_observed_fills(repository: WcaSqliteRepository, broker: Any) -> int:
    if not hasattr(broker, "read_fills_and_activities"):
        return 0
    records = {record.client_order_id: record for record in repository.list_execution_outbox_records()}
    processed = 0
    for fill in broker.read_fills_and_activities(after=_utc_now() - timedelta(days=1)):
        record = records.get(fill.client_order_id)
        if record is None or fill.filled_quantity <= 0:
            continue
        payload = {
            "fill": fill.model_dump(mode="json") if hasattr(fill, "model_dump") else {},
            "client_order_id": fill.client_order_id,
            "entry_price": fill.average_fill_price or record.proposed_order.limit_price or record.proposed_order.trigger_price,
            "stop_price": record.proposed_order.stop_price,
            "target_price": record.proposed_order.target_price,
            "opened_at": fill.filled_at.astimezone(timezone.utc).isoformat(),
            "remaining_quantity": fill.remaining_quantity,
            "position_effect": "exit" if _record_is_risk_reducing_exit(record) else "entry",
        }
        if repository.apply_fill_and_update_position(
            record.decision.model_copy(update={"proposed_order": record.proposed_order}),
            fill_id=fill.fill_id,
            account_id=record.account_id,
            quantity=fill.filled_quantity,
            broker_order_id=fill.broker_order_id,
            payload=payload,
        ):
            processed += 1
            target_status = WcaOrderStatus.FILLED if fill.remaining_quantity == 0 else WcaOrderStatus.PARTIALLY_FILLED
            try:
                repository.update_execution_outbox_state(outbox_id=record.outbox_id, status=target_status, response_payload={"end_of_session_fill": payload})
            except ValueError:
                pass
    return processed


def _flatten_local_wca_position(repository: WcaSqliteRepository, broker: Any, *, command: WcaRuntimeCommand, evaluated_at: datetime) -> dict[str, Any]:
    lots = repository.list_open_wca_lots(account_id=command.account_id, symbol=command.symbol)
    quantity = sum(int(lot["quantity"]) for lot in lots)
    if quantity <= 0:
        return {"status": "already_flat", "closed_quantity": 0}
    side = lots[0]["side"]
    client_order_id = f"wca-eos-{command.account_id}-{command.symbol}-{command.command_id}"[:48]
    try:
        ack = broker.close_or_reduce_wca_position(symbol=command.symbol, quantity=quantity, side=side, client_order_id=client_order_id)
    except WcaPaperBrokerTimeout as exc:
        return {"status": "timeout", "closed_quantity": 0, "error": str(exc)}
    except Exception as exc:
        return {"status": "failed", "closed_quantity": 0, "error": str(exc)}
    if ack.status == "REJECTED":
        return {"status": "rejected", "closed_quantity": 0, "broker_order_id": ack.broker_order_id, "message": ack.message}
    fill_quantity = ack.fill.filled_quantity if ack.fill is not None else 0
    fill_price = ack.fill.average_fill_price if ack.fill is not None and ack.fill.average_fill_price is not None else _average_mark_from_lots(lots)
    if fill_quantity > 0:
        repository.close_wca_attributed_position_quantity(
            account_id=command.account_id,
            symbol=command.symbol,
            quantity=min(quantity, fill_quantity),
            exit_price=fill_price,
            exit_reason="end_of_session_flatten",
            evaluated_at=evaluated_at,
        )
    return {
        "status": "submitted" if fill_quantity < quantity else "filled",
        "closed_quantity": fill_quantity,
        "remaining_quantity": max(0, quantity - fill_quantity),
        "fill_price": fill_price,
        "broker_order_id": ack.broker_order_id,
        "client_order_id": ack.client_order_id,
    }


def _verify_end_of_session(repository: WcaSqliteRepository, broker: Any, *, command: WcaRuntimeCommand) -> dict[str, Any]:
    local_quantity = repository.open_wca_position_quantity(account_id=command.account_id, symbol=command.symbol)
    projection = repository.read_inventory_projection(algorithm_id="wca", broker_account_id=command.account_id, symbol=command.symbol)
    snapshot = broker.refresh_account_snapshot()
    broker_quantity = sum(_signed_broker_quantity(position.side, position.quantity) for position in snapshot.positions if position.symbol.upper() == command.symbol.upper() and (position.algorithmId == "wca" or position.positionOwner == "wca"))
    orders = [*snapshot.pendingOrders, *snapshot.partiallyFilledOrders]
    entry_orders = [order for order in orders if _broker_order_is_entry(order)]
    orphan_protection = [order for order in orders if _broker_order_is_protective(order) and local_quantity == 0 and broker_quantity == 0]
    local_entry_orders = [record for record in repository.list_execution_outbox_records(account_id=command.account_id) if record.symbol == command.symbol and _record_is_open_entry(record)]
    if local_quantity == 0 and broker_quantity == 0 and projection.open_quantity == 0 and not entry_orders and not local_entry_orders and not orphan_protection:
        _release_reserved_risk_for_eos(repository, command=command)
        projection = repository.read_inventory_projection(algorithm_id="wca", broker_account_id=command.account_id, symbol=command.symbol)
    blocking: list[str] = []
    if local_quantity != 0 or broker_quantity != 0 or projection.open_quantity != 0:
        blocking.append("wca.runtime.end_of_session.position_not_flat")
    if entry_orders or local_entry_orders:
        blocking.append("wca.runtime.end_of_session.entry_orders_remain")
    if orphan_protection:
        blocking.append("wca.runtime.end_of_session.orphan_protective_orders")
    if projection.reserved_risk > 0:
        blocking.append("wca.runtime.end_of_session.unreleased_risk_reservation")
    return {
        "verified": not blocking,
        "blocking_reasons": tuple(blocking),
        "local_quantity": local_quantity,
        "broker_quantity": broker_quantity,
        "projection_quantity": projection.open_quantity,
        "reserved_risk": projection.reserved_risk,
        "broker_entry_orders": len(entry_orders),
        "local_entry_orders": len(local_entry_orders),
        "orphan_protective_orders": len(orphan_protection),
    }


def _mark_local_entry_orders_cancelled(repository: WcaSqliteRepository, *, account_id: str, symbol: str, evidence: dict[str, Any]) -> int:
    cancelled = 0
    for record in repository.list_execution_outbox_records(account_id=account_id):
        if record.symbol != symbol or not _record_is_open_entry(record):
            continue
        try:
            repository.update_execution_outbox_state(outbox_id=record.outbox_id, status=WcaOrderStatus.CANCELLED, response_payload=evidence)
        except ValueError:
            try:
                repository.update_execution_outbox_state(outbox_id=record.outbox_id, status=WcaOrderStatus.RECONCILING, response_payload=evidence)
            except ValueError:
                continue
        repository.record_order_terminal_inventory_event(
            record.decision.model_copy(update={"proposed_order": record.proposed_order}),
            account_id=record.account_id,
            client_order_id=record.client_order_id,
            event_type="ORDER_CANCELLED",
            event_timestamp=_utc_now(),
            payload={"reason": "end_of_session_entry_cancel", **evidence},
        )
        cancelled += 1
    return cancelled


def _release_reserved_risk_for_eos(repository: WcaSqliteRepository, *, command: WcaRuntimeCommand) -> None:
    projection = repository.read_inventory_projection(algorithm_id="wca", broker_account_id=command.account_id, symbol=command.symbol)
    if projection.reserved_risk <= 0:
        return
    now = _utc_now().isoformat()
    repository.record_inventory_event(
        WcaInventoryLedgerEvent(
            inventory_event_id=f"wca-eos-risk-released-{command.command_id}",
            event_type="RISK_RELEASED",
            broker_account_id=command.account_id,
            symbol=command.symbol,
            event_timestamp=now,
            trade_date=now[:10],
            reserved_risk=projection.reserved_risk,
            source_authority="wca_end_of_session",
            configuration_version="wca_end_of_session",
            decision_id=command.decision_id or command.command_id,
            run_id=command.run_id,
            payload={"reason": "end_of_session_final_risk_release"},
        )
    )


def _record_end_of_session_flatten_event(repository: WcaSqliteRepository, *, command: WcaRuntimeCommand, evaluated_at: datetime, flatten: dict[str, Any]) -> None:
    closed_quantity = int(flatten.get("closed_quantity") or 0)
    repository.record_inventory_event(
        WcaInventoryLedgerEvent(
            inventory_event_id=f"wca-eos-flatten-{command.command_id}",
            event_type="END_OF_SESSION_FLATTEN",
            broker_account_id=command.account_id,
            symbol=command.symbol,
            event_timestamp=evaluated_at.isoformat(),
            trade_date=evaluated_at.date().isoformat(),
            side="SELL",
            quantity=closed_quantity,
            filled_quantity=closed_quantity,
            fill_price=float(flatten.get("fill_price") or 0),
            source_authority="wca_end_of_session",
            configuration_version="wca_end_of_session",
            decision_id=command.decision_id or command.command_id,
            run_id=command.run_id,
            payload={"reason": "end_of_session_flatten", **flatten},
        )
    )


def _record_end_of_session_evidence(repository: WcaSqliteRepository, *, command: WcaRuntimeCommand, evaluated_at: datetime, evidence: dict[str, Any], verified: bool) -> None:
    flatten = evidence.get("flatten") if isinstance(evidence.get("flatten"), dict) else {}
    payload = {**evidence, "verified": verified, "critical": not verified, "circuit_breaker_state": "closed" if verified else "open"}
    repository.record_inventory_event(
        WcaInventoryLedgerEvent(
            inventory_event_id=f"wca-eos-evidence-{command.command_id}",
            event_type="RECONCILIATION_CORRECTION",
            broker_account_id=command.account_id,
            symbol=command.symbol,
            event_timestamp=evaluated_at.isoformat(),
            trade_date=evaluated_at.date().isoformat(),
            side="SELL",
            quantity=int(flatten.get("closed_quantity") or 0),
            filled_quantity=int(flatten.get("closed_quantity") or 0),
            fill_price=float(flatten.get("fill_price") or 0),
            source_authority="wca_end_of_session",
            configuration_version="wca_end_of_session",
            decision_id=command.decision_id or command.command_id,
            run_id=command.run_id,
            payload=payload,
        )
    )


def _enqueue_reconciliation(runtime_repository: WcaRuntimeRepository, command: WcaRuntimeCommand, *, max_queue_depth: int, marker: str, priority: int) -> None:
    runtime_repository.enqueue_command(
        runtime_command(
            WcaRuntimeCommandType.BROKER_RECONCILIATION,
            account_id=command.account_id,
            symbol=command.symbol,
            decision_id=command.decision_id,
            run_id=command.run_id,
            payload={marker: True, "source_command_id": command.command_id},
            priority=priority,
            reason_codes=(f"wca.runtime.reconciliation.scheduled_{marker}",),
        ),
        max_queue_depth=max_queue_depth,
    )


def _fail_end_of_session(supervisor: WcaRuntimeSupervisor, command: WcaRuntimeCommand, reasons: tuple[str, ...], *, evidence: dict[str, Any]) -> dict[str, Any]:
    reason_codes = tuple(dict.fromkeys((*reasons, "wca.runtime.end_of_session.critical_health", "wca.runtime.end_of_session.failed")))
    supervisor.runtime_repository.fail_command(command.command_id, reason_codes=reason_codes)
    supervisor.runtime_repository.write_runtime_health(
        WcaRuntimeHealthSnapshot(
            health_id="wca-runtime-health",
            account_id=command.account_id,
            symbol=command.symbol,
            status="critical",
            paused_new_entries=True,
            protective_management_active=True,
            reason_codes=reason_codes,
            recovery_state=supervisor.recovery_state,
            last_decision_id=command.decision_id,
        )
    )
    _enqueue_reconciliation(supervisor.runtime_repository, command, max_queue_depth=supervisor.settings.max_command_queue_depth, marker="after_failed_end_of_session", priority=1)
    return {"status": "failed", "commandId": command.command_id, "verified": False, "reasonCodes": list(reason_codes), "evidence": evidence}


def _evaluate_entry_health(
    supervisor: WcaRuntimeSupervisor,
    *,
    event: WcaFinalizedBarEvent,
    state: WcaAuthoritativeRuntimeState,
    configuration_ready: bool,
    weight_calibration_ready: bool,
) -> WcaRuntimeHealthSnapshot:
    now = _utc_now()
    depths = supervisor.runtime_repository.queue_depths()
    ages = supervisor.runtime_repository.queue_ages(now=now)
    prior = supervisor.runtime_repository.read_latest_runtime_health()
    heartbeat_ok = _worker_heartbeats_fresh(prior, now=now, maximum_age_seconds=supervisor.settings.max_worker_heartbeat_age_seconds)
    reconciliation_age = _latest_reconciliation_age_seconds(supervisor.repository, account_id=supervisor.settings.account_id, symbol=event.symbol, now=now)
    clock_skew = prior.clock_skew_seconds if prior is not None else 0.0
    health_checks = {
        "worker_heartbeat": heartbeat_ok,
        "queue_depth": depths["events"] <= supervisor.settings.max_event_queue_depth and depths["commands"] <= supervisor.settings.max_command_queue_depth,
        "queue_age": ages["maximum"] <= supervisor.settings.max_queue_delay_seconds,
        "database_available": supervisor.runtime_repository.database_available(),
        "broker_available": prior.broker_available if prior is not None else True,
        "market_data_available": (event.snapshot is None or event.snapshot.data_ready) and (prior.market_data_available if prior is not None else True),
        "clock_skew": abs(clock_skew) <= supervisor.settings.max_clock_skew_seconds,
        "reconciliation_fresh": reconciliation_age is None or reconciliation_age <= supervisor.settings.max_reconciliation_age_seconds,
        "unprotected_position_clear": not (prior.unprotected_position if prior is not None else False),
        "duplicate_order_evidence_clear": not (prior.duplicate_order_evidence if prior is not None else False),
        "configuration_ready": configuration_ready and (prior.configuration_ready if prior is not None else True),
        "weight_calibration_ready": weight_calibration_ready and (prior.weight_calibration_ready if prior is not None else True),
        "circuit_breaker_closed": state.circuit_breaker_state != "open" and not (prior.circuit_breaker_open if prior is not None else False),
    }
    reason_codes = tuple(f"wca.runtime.health.{name}" for name, passed in health_checks.items() if not passed)
    return healthy_runtime_snapshot(
        queue_depth=depths["events"],
        command_depth=depths["commands"],
        max_queue_age_seconds=ages["maximum"],
        last_processed_bar=supervisor.runtime_repository.last_processed_bar(symbol=event.symbol),
        lag_seconds=max(0.0, (now - event.finalized_candle_timestamp.astimezone(timezone.utc)).total_seconds()),
        last_decision_id=supervisor.runtime_repository.last_decision_id(),
        recovery_state=supervisor.recovery_state,
        paused_new_entries=bool(reason_codes),
        reason_codes=reason_codes or ("wca.runtime.health.ok",),
        worker_heartbeats=prior.worker_heartbeats if prior is not None else {},
        database_available=health_checks["database_available"],
        broker_available=health_checks["broker_available"],
        market_data_available=health_checks["market_data_available"],
        clock_skew_seconds=abs(clock_skew),
        reconciliation_age_seconds=reconciliation_age,
        unprotected_position=not health_checks["unprotected_position_clear"],
        duplicate_order_evidence=not health_checks["duplicate_order_evidence_clear"],
        configuration_ready=health_checks["configuration_ready"],
        weight_calibration_ready=health_checks["weight_calibration_ready"],
        circuit_breaker_open=not health_checks["circuit_breaker_closed"],
        latency_summary=supervisor.runtime_repository.read_latency_summaries(account_id=supervisor.settings.account_id, symbol=event.symbol),
        health_checks=health_checks,
    )


def _prior_critical_health_reasons(health: WcaRuntimeHealthSnapshot | None) -> tuple[str, ...]:
    return tuple(
        code
        for code in (health.reason_codes if health is not None else ())
        if code.startswith("wca.runtime.health.") and code != "wca.runtime.health.ok"
    )


def _clean_reconciliation_clears_prior_pause(health: WcaRuntimeHealthSnapshot | None, new_reason_codes: tuple[str, ...]) -> bool:
    if health is None or "wca.runtime.broker_reconciliation.clean" not in new_reason_codes:
        return False
    return any("reconciliation" in code for code in health.reason_codes)


def _entry_freshness_reason_codes(
    supervisor: WcaRuntimeSupervisor,
    *,
    event: WcaFinalizedBarEvent,
    state: WcaAuthoritativeRuntimeState,
    lag_seconds: float,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if lag_seconds > int(supervisor.settings.max_finalized_bar_age_seconds or supervisor.settings.max_lag_seconds):
        reasons.append("wca.runtime.health.finalized_bar_age_exceeded")
    if event.snapshot is not None and event.snapshot.quote is not None:
        quote_age = max(0.0, (_utc_now() - event.snapshot.quote.timestamp.astimezone(timezone.utc)).total_seconds())
        if quote_age > supervisor.settings.max_quote_age_seconds:
            reasons.append("wca.runtime.health.quote_age_exceeded")
    if state.broker_observation_timestamp is not None:
        account_age = max(0.0, (_utc_now() - state.broker_observation_timestamp.astimezone(timezone.utc)).total_seconds())
        if account_age > int(supervisor.settings.max_authoritative_account_state_age_seconds or supervisor.settings.max_state_age_seconds):
            reasons.append("wca.runtime.health.authoritative_account_state_age_exceeded")
    return tuple(dict.fromkeys(reasons))


def _worker_heartbeats_fresh(health: WcaRuntimeHealthSnapshot | None, *, now: datetime, maximum_age_seconds: int) -> bool:
    if health is None or not health.worker_heartbeats:
        return True
    return all((now - heartbeat.astimezone(timezone.utc)).total_seconds() <= maximum_age_seconds for heartbeat in health.worker_heartbeats.values())


def _latest_reconciliation_age_seconds(repository: WcaSqliteRepository, *, account_id: str, symbol: str, now: datetime) -> float | None:
    with repository.connect() as conn:
        row = conn.execute(
            """
            SELECT timestamp
            FROM wca_broker_reconciliations
            WHERE algorithm_id = 'wca' AND account_id = ? AND symbol = ?
            ORDER BY timestamp DESC, created_at DESC
            LIMIT 1
            """,
            (account_id, symbol),
        ).fetchone()
    if row is None:
        return None
    return max(0.0, (now - _payload_datetime(row["timestamp"])).total_seconds())


def _record_is_open_entry(record: Any) -> bool:
    return record.status not in {
        WcaOrderStatus.FILLED.value,
        WcaOrderStatus.CANCELLED.value,
        WcaOrderStatus.REJECTED.value,
        WcaOrderStatus.RECONCILED.value,
        WcaOrderStatus.DEAD_LETTER.value,
        WcaOrderStatus.EXPIRED.value,
    } and not _record_is_risk_reducing_exit(record)


def _record_is_risk_reducing_exit(record: Any) -> bool:
    codes = tuple(str(code) for code in record.proposed_order.reason_codes)
    return any("risk_reducing_exit" in code or ".exit" in code for code in codes)


def _broker_order_is_entry(order: Any) -> bool:
    return not _broker_order_is_protective(order) and str(getattr(order, "symbol", "")).upper() == "SPY"


def _broker_order_is_protective(order: Any) -> bool:
    order_type = str(getattr(order, "orderType", "") or "").upper()
    client_order_id = str(getattr(order, "clientOrderId", "") or "").lower()
    return order_type in {"STOP", "STOP_LIMIT", "TRAILING_STOP"} or getattr(order, "exitOwner", None) == "wca" or "-exit-" in client_order_id or "-protection-" in client_order_id


def _signed_broker_quantity(side: Any, quantity: int) -> int:
    return int(quantity) if _side_text(side) == "BUY" else -int(quantity)


def _side_text(side: Any) -> str:
    return side.value if hasattr(side, "value") else str(side)


def _average_mark_from_lots(lots: tuple[dict[str, Any], ...]) -> float:
    quantity = sum(int(lot["quantity"]) for lot in lots)
    if quantity <= 0:
        return 0.01
    return max(0.01, sum(float(lot["entry_price"]) * int(lot["quantity"]) for lot in lots) / quantity)


def _payload_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _required_float(value: float | None, field_name: str) -> float:
    if value is None:
        raise ValueError(f"WCA authoritative runtime state missing {field_name}")
    return float(value)


def _required_int(value: int | None, field_name: str) -> int:
    if value is None:
        raise ValueError(f"WCA authoritative runtime state missing {field_name}")
    return int(value)


def _runtime_order_validation_context(
    command: WcaRuntimeCommand,
    decision: WcaDecision,
    request: WcaPaperBrokerOrderRequest,
    *,
    automatic_paper_enabled: bool,
) -> WcaOrderValidationContext:
    quote_required = decision.proposed_order is not None and not _is_risk_reducing_exit(decision)
    return WcaOrderValidationContext(
        evaluation_timestamp=decision.decision_timestamp,
        paper_only_mode=True,
        account_id=command.account_id,
        broker_endpoint="paper",
        runtime_mode=decision.runtime_mode,
        requires_executable_paper_stage=True,
        automatic_paper_enabled=automatic_paper_enabled,
        market_is_open=True,
        allowed_session_window=True,
        candle_freshness_seconds=120,
        data_ready=decision.market_snapshot.data_ready,
        inventory_consistent="wca.runtime_state.fresh" in decision.authoritative_state_reason_codes or not decision.authoritative_state_reason_codes,
        max_approved_quantity=decision.global_gate_result.allowed_quantity if decision.global_gate_result is not None else None,
        order_type=request.order_type,
        time_in_force=request.time_in_force,
        protective_exit_plan_present=decision.proposed_order is None or (decision.proposed_order.stop_price is not None and decision.proposed_order.target_price is not None),
        current_position_quantity=0,
        current_position_side=None,
        position_owned_by_wca=True,
        quote_freshness_seconds=15 if quote_required else None,
        available_buying_power=None,
        account_equity=None,
        max_position_value=None,
        max_spread_percent=decision.effective_settings.final_max_spread_percent if decision.effective_settings is not None else None,
        average_one_minute_volume=None,
        max_participation_percent=decision.effective_settings.final_max_participation_percent if decision.effective_settings is not None else None,
        expected_net_edge=decision.cost_estimate.conservative_net_edge_per_share if decision.cost_estimate is not None else None,
        minimum_net_edge=decision.effective_settings.final_minimum_net_edge_per_share if decision.effective_settings is not None else 0,
        idempotency_required=True,
        new_entry_permitted=decision.global_gate_result.entry_permitted if decision.global_gate_result is not None else True,
        risk_reducing_exit_permitted=decision.global_gate_result.risk_reducing_exit_permitted if decision.global_gate_result is not None else True,
        is_risk_reducing_exit=not quote_required,
    )


def _is_risk_reducing_exit(decision: WcaDecision) -> bool:
    return decision.global_gate_result is not None and not decision.global_gate_result.entry_permitted and decision.global_gate_result.risk_reducing_exit_permitted


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
