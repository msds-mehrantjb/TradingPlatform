"""Independent Meta-Strategy background worker entry points."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, time
from typing import Any

from backend.app.algorithms.meta_strategy.decision_worker import (
    MetaStrategyDecisionStateProvider,
    MetaStrategyFinalisedBarDecisionWorker as MetaStrategyDurableFinalisedBarDecisionWorker,
)
from backend.app.algorithms.meta_strategy.execution import (
    MetaStrategyPaperOrderReconciliationWorker,
    MetaStrategyPaperOrderSubmissionWorker,
    MetaStrategyStaleOrderCancellationWorker,
)
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository, MetaStrategyWorker
from backend.app.algorithms.meta_strategy.ownership import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.state_provider import MetaStrategyCandleStoreStateProvider
from backend.app.algorithms.meta_strategy.research_workers import (
    MetaStrategyBacktestingWorker as MetaStrategyResearchBacktestingWorker,
    MetaStrategyModelEvaluationWorker as MetaStrategyResearchModelEvaluationWorker,
    MetaStrategyPromotionWorker as MetaStrategyResearchPromotionWorker,
    MetaStrategyReplayWorker as MetaStrategyResearchReplayWorker,
    MetaStrategyReportingWorker as MetaStrategyResearchReportingWorker,
    MetaStrategyTrainingWorker as MetaStrategyResearchTrainingWorker,
)
from backend.app.algorithms.meta_strategy.exits import MetaStrategyExitCandle, MetaStrategyExitInputs, MetaStrategyPositionState
from backend.app.algorithms.meta_strategy.trade_management import manage_meta_strategy_trade


class MetaStrategyFinalisedBarDecisionWorker(MetaStrategyDurableFinalisedBarDecisionWorker):
    def __init__(
        self,
        *,
        repository: MetaStrategyJobRepository,
        state_provider: MetaStrategyDecisionStateProvider | None = None,
        worker_id: str = "meta_strategy.finalised_bar_decision_worker",
    ) -> None:
        super().__init__(
            repository=repository,
            state_provider=state_provider or MetaStrategyCandleStoreStateProvider(),
            worker_id=worker_id,
        )


class MetaStrategyOrderSubmissionWorker(MetaStrategyPaperOrderSubmissionWorker):
    pass


class MetaStrategyOrderReconciliationWorker(MetaStrategyPaperOrderReconciliationWorker):
    pass


class MetaStrategyStaleOrderHandlingWorker(MetaStrategyStaleOrderCancellationWorker):
    pass


class MetaStrategyInventoryReconciliationWorker(MetaStrategyWorker):
    def __init__(
        self,
        *,
        repository: MetaStrategyJobRepository,
        inventory_repository: MetaStrategySqliteRepository,
        worker_id: str = "meta_strategy.inventory_reconciliation_worker",
    ) -> None:
        super().__init__(repository=repository, queue_name="inventory_reconciliation", worker_id=worker_id)
        self.inventory_repository = inventory_repository

    def run_once(self, *, now: datetime | None = None, handler=None) -> dict[str, Any] | None:  # type: ignore[override]
        current = now or datetime.now(UTC)
        job = self.repository.claim_next_job(queue_name=self.queue_name, worker_id=self.worker_id, lease_seconds=self.lease_seconds, now=current)
        if job is None:
            self.repository.record_worker_heartbeat(worker_id=self.worker_id, queue_name=self.queue_name, now=current)
            return None
        try:
            payload = _job_payload(self.repository, job)
            mark_prices = _mark_prices(payload)
            self.repository.record_job_progress(job.job_id, worker_id=self.worker_id, status="RUNNING", progress_percent=0.0, payload={"checkpoint": "inventory_consistency"}, now=current)
            consistency = self.inventory_repository.check_inventory_consistency(mark_prices=mark_prices)
            snapshot = self.inventory_repository.current_inventory_snapshot(mark_prices=mark_prices)
            result = {
                "status": "INVENTORY_RECONCILED",
                "consistent": bool(consistency["consistent"]),
                "derivedSnapshotId": consistency["derivedSnapshotId"],
                "storedSnapshotId": consistency["storedSnapshotId"],
                "snapshotId": snapshot.snapshot_id,
                "openPositionCount": len(snapshot.open_positions),
                "reasonCodes": tuple(consistency["reasonCodes"]),
            }
            self.repository.record_job_progress(job.job_id, worker_id=self.worker_id, status="SUCCEEDED", progress_percent=100.0, payload=result, now=current)
            self.repository.complete_job(job.job_id, worker_id=self.worker_id, result=result, now=current)
            return result
        except Exception as exc:
            self.repository.record_job_progress(job.job_id, worker_id=self.worker_id, status="FAILED", progress_percent=100.0, payload={"errorCategory": type(exc).__name__}, now=current)
            self.repository.fail_job(job.job_id, worker_id=self.worker_id, error_category=type(exc).__name__, error_details=str(exc), now=current)
            return {"status": "FAILED", "reasonCodes": ("meta_strategy.inventory_reconciliation.failed",)}


class MetaStrategyPositionManagementWorker(MetaStrategyWorker):
    def __init__(
        self,
        *,
        repository: MetaStrategyJobRepository,
        inventory_repository: MetaStrategySqliteRepository,
        worker_id: str = "meta_strategy.position_management_worker",
    ) -> None:
        super().__init__(repository=repository, queue_name="position_management", worker_id=worker_id)
        self.inventory_repository = inventory_repository

    def run_once(self, *, now: datetime | None = None, handler=None) -> dict[str, Any] | None:  # type: ignore[override]
        current = now or datetime.now(UTC)
        job = self.repository.claim_next_job(queue_name=self.queue_name, worker_id=self.worker_id, lease_seconds=self.lease_seconds, now=current)
        if job is None:
            self.repository.record_worker_heartbeat(worker_id=self.worker_id, queue_name=self.queue_name, now=current)
            return None
        try:
            payload = _job_payload(self.repository, job)
            mark_prices = _mark_prices(payload)
            snapshot = self.inventory_repository.current_inventory_snapshot(mark_prices=mark_prices)
            requests = _position_management_requests(payload)
            explicit_decisions = _explicit_position_management_decisions(
                inventory_repository=self.inventory_repository,
                job=job,
                payload=payload,
                snapshot=snapshot,
                requests=requests,
                now=current,
            )
            managed = _manage_open_positions(
                repository=self.repository,
                inventory_repository=self.inventory_repository,
                job=job,
                payload=payload,
                snapshot=snapshot,
                mark_prices=mark_prices,
                now=current,
            )
            decisions = (*explicit_decisions, *managed["decisions"])
            status = "POSITION_MANAGEMENT_EVALUATED" if decisions else "POSITION_MANAGEMENT_AWAITING_EXIT_CONTEXT"
            checkpoint = self.inventory_repository.record_reconciliation_checkpoint(
                {
                    "algorithmId": "meta_strategy",
                    "algorithm_id": "meta_strategy",
                    "capitalPartitionId": payload.get("capitalPartitionId") or snapshot.capital_partition_id,
                    "capital_partition_id": payload.get("capitalPartitionId") or payload.get("capital_partition_id") or snapshot.capital_partition_id,
                    "settingsVersion": payload.get("settingsVersion") or snapshot.settings_version,
                    "settings_version": payload.get("settingsVersion") or payload.get("settings_version") or snapshot.settings_version,
                    "strategyCatalogVersion": payload.get("strategyCatalogVersion") or payload.get("strategy_catalog_version") or "meta_strategy_strategy_catalog_v1",
                    "strategy_catalog_version": payload.get("strategyCatalogVersion") or payload.get("strategy_catalog_version") or "meta_strategy_strategy_catalog_v1",
                    "featureSchemaVersion": payload.get("featureSchemaVersion") or payload.get("feature_schema_version") or "meta_strategy_feature_schema_v1",
                    "feature_schema_version": payload.get("featureSchemaVersion") or payload.get("feature_schema_version") or "meta_strategy_feature_schema_v1",
                    "modelVersion": payload.get("modelVersion") or payload.get("model_version") or "none",
                    "model_version": payload.get("modelVersion") or payload.get("model_version") or "none",
                    "correlationId": payload.get("correlationId") or f"{job.job_id}:position_management",
                    "correlation_id": payload.get("correlationId") or payload.get("correlation_id") or f"{job.job_id}:position_management",
                    "decisionId": payload.get("decisionId") or "position-management",
                    "decision_id": payload.get("decisionId") or payload.get("decision_id") or "position-management",
                    "jobId": job.job_id,
                    "job_id": job.job_id,
                    "eventId": payload.get("eventId") or "",
                    "event_id": payload.get("eventId") or payload.get("event_id") or "",
                    "symbol": str(payload.get("symbol") or "PORTFOLIO"),
                    "status": status,
                    "timestamp": current.isoformat(),
                    "payload": {
                        "snapshotId": snapshot.snapshot_id,
                        "openPositionCount": len(snapshot.open_positions),
                        "evaluatedExitCount": len(decisions),
                        "createdExitIntentCount": managed["createdExitIntentCount"],
                        "blockedEntrySymbols": managed["blockedEntrySymbols"],
                        "decisions": decisions,
                    },
                }
            )
            result = {
                "status": status,
                "snapshotId": snapshot.snapshot_id,
                "openPositionCount": len(snapshot.open_positions),
                "evaluatedExitCount": len(decisions),
                "createdExitIntentCount": managed["createdExitIntentCount"],
                "blockedEntrySymbols": managed["blockedEntrySymbols"],
                "checkpoint": checkpoint,
                "reasonCodes": (
                    "meta_strategy.position_management.evaluated"
                    if decisions
                    else "meta_strategy.position_management.exit_context_required"
                ),
            }
            self.repository.record_job_progress(job.job_id, worker_id=self.worker_id, status="SUCCEEDED", progress_percent=100.0, payload=result, now=current)
            self.repository.complete_job(job.job_id, worker_id=self.worker_id, result=result, now=current)
            return result
        except Exception as exc:
            self.repository.record_job_progress(job.job_id, worker_id=self.worker_id, status="FAILED", progress_percent=100.0, payload={"errorCategory": type(exc).__name__}, now=current)
            self.repository.fail_job(job.job_id, worker_id=self.worker_id, error_category=type(exc).__name__, error_details=str(exc), now=current)
            return {"status": "FAILED", "reasonCodes": ("meta_strategy.position_management.failed",)}


class MetaStrategyTrainingWorker(MetaStrategyResearchTrainingWorker):
    pass


class MetaStrategyBacktestingWorker(MetaStrategyResearchBacktestingWorker):
    pass


class MetaStrategyReplayWorker(MetaStrategyResearchReplayWorker):
    pass


class MetaStrategyModelEvaluationWorker(MetaStrategyResearchModelEvaluationWorker):
    pass


class MetaStrategyPromotionWorker(MetaStrategyResearchPromotionWorker):
    pass


class MetaStrategyReportingWorker(MetaStrategyResearchReportingWorker):
    pass


META_STRATEGY_WORKER_CLASSES = (
    MetaStrategyFinalisedBarDecisionWorker,
    MetaStrategyOrderSubmissionWorker,
    MetaStrategyOrderReconciliationWorker,
    MetaStrategyStaleOrderHandlingWorker,
    MetaStrategyInventoryReconciliationWorker,
    MetaStrategyPositionManagementWorker,
    MetaStrategyTrainingWorker,
    MetaStrategyBacktestingWorker,
    MetaStrategyReplayWorker,
    MetaStrategyModelEvaluationWorker,
    MetaStrategyPromotionWorker,
    MetaStrategyReportingWorker,
)


__all__ = [
    "META_STRATEGY_WORKER_CLASSES",
    "MetaStrategyBacktestingWorker",
    "MetaStrategyFinalisedBarDecisionWorker",
    "MetaStrategyInventoryReconciliationWorker",
    "MetaStrategyModelEvaluationWorker",
    "MetaStrategyOrderReconciliationWorker",
    "MetaStrategyOrderSubmissionWorker",
    "MetaStrategyPositionManagementWorker",
    "MetaStrategyPromotionWorker",
    "MetaStrategyReplayWorker",
    "MetaStrategyReportingWorker",
    "MetaStrategyStaleOrderHandlingWorker",
    "MetaStrategyTrainingWorker",
]


def _job_payload(repository: MetaStrategyJobRepository, job) -> dict[str, Any]:
    stored = repository.read_payload(job.payload_reference)
    payload = stored.get("payload")
    return dict(payload) if isinstance(payload, dict) else {}


def _mark_prices(payload: dict[str, Any]) -> dict[str, float]:
    raw = payload.get("markPrices") or payload.get("mark_prices") or {}
    if not isinstance(raw, dict):
        return {}
    return {str(symbol): float(price) for symbol, price in raw.items()}


def _position_management_requests(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = payload.get("positionManagementRequests") or payload.get("position_management_requests") or payload.get("exitInputs") or ()
    if isinstance(raw, dict):
        return (dict(raw),)
    if isinstance(raw, list | tuple):
        return tuple(dict(item) for item in raw if isinstance(item, dict))
    return ()


def _explicit_position_management_decisions(
    *,
    inventory_repository: MetaStrategySqliteRepository,
    job,
    payload: dict[str, Any],
    snapshot,
    requests: tuple[dict[str, Any], ...],
    now: datetime,
) -> tuple[dict[str, Any], ...]:
    if not requests:
        return ()
    decisions: list[dict[str, Any]] = []
    positions_by_id = {position.position_id: position for position in snapshot.open_positions}
    positions_by_symbol = {position.symbol.upper(): position for position in snapshot.open_positions}
    for index, request in enumerate(requests):
        requested_position = dict(request.get("position") or {})
        requested_position_id = str(
            requested_position.get("positionId")
            or requested_position.get("position_id")
            or request.get("positionId")
            or request.get("position_id")
            or ""
        )
        requested_symbol = str(requested_position.get("symbol") or request.get("symbol") or "").upper()
        observed_algorithm_id = (
            requested_position.get("algorithmId")
            or requested_position.get("algorithm_id")
            or request.get("algorithmId")
            or request.get("algorithm_id")
        )
        observed_capital_partition_id = (
            requested_position.get("capitalPartitionId")
            or requested_position.get("capital_partition_id")
            or request.get("capitalPartitionId")
            or request.get("capital_partition_id")
        )
        foreign_owner = observed_algorithm_id not in (None, "", ALGORITHM_ID) or observed_capital_partition_id not in (
            None,
            "",
            snapshot.capital_partition_id,
        )
        open_position = positions_by_id.get(requested_position_id) if requested_position_id else None
        if open_position is None and requested_symbol and not requested_position_id:
            open_position = positions_by_symbol.get(requested_symbol)
        if foreign_owner or open_position is None:
            reason = "FOREIGN_POSITION_MANAGEMENT_REQUEST" if foreign_owner else "UNOWNED_POSITION_MANAGEMENT_REQUEST"
            inventory_repository.record_quarantine(
                {
                    "algorithmId": ALGORITHM_ID,
                    "algorithm_id": ALGORITHM_ID,
                    "capitalPartitionId": snapshot.capital_partition_id,
                    "capital_partition_id": snapshot.capital_partition_id,
                    "settingsVersion": payload.get("settingsVersion") or snapshot.settings_version,
                    "settings_version": payload.get("settingsVersion") or payload.get("settings_version") or snapshot.settings_version,
                    "decisionId": payload.get("decisionId") or f"meta_strategy.position_management.explicit.{index}",
                    "decision_id": payload.get("decisionId") or payload.get("decision_id") or f"meta_strategy.position_management.explicit.{index}",
                    "jobId": getattr(job, "job_id", None) or payload.get("jobId") or payload.get("job_id") or "",
                    "job_id": getattr(job, "job_id", None) or payload.get("jobId") or payload.get("job_id") or "",
                    "eventId": payload.get("eventId") or payload.get("event_id") or f"explicit-position-management-{index}",
                    "event_id": payload.get("eventId") or payload.get("event_id") or f"explicit-position-management-{index}",
                    "symbol": requested_symbol or str(request.get("symbol") or "UNKNOWN"),
                    "status": "BLOCKED",
                    "timestamp": now.isoformat(),
                    "observedAlgorithmId": observed_algorithm_id,
                    "observedCapitalPartitionId": observed_capital_partition_id,
                    "requestedPositionId": requested_position_id,
                    "payload": {
                        "request": request,
                        "snapshotId": snapshot.snapshot_id,
                        "openPositionIds": tuple(sorted(positions_by_id)),
                        "openPositionSymbols": tuple(sorted(positions_by_symbol)),
                    },
                },
                reason=reason,
            )
            decisions.append(
                {
                    "positionId": requested_position_id,
                    "symbol": requested_symbol,
                    "action": "BLOCKED",
                    "reasonCodes": ("meta_strategy.position_management.foreign_or_unowned_position_request_rejected",),
                }
            )
            continue
        lifecycle = _position_lifecycle(inventory_repository, payload, open_position, snapshot)
        if lifecycle is None:
            decisions.append(
                {
                    "positionId": open_position.position_id,
                    "symbol": open_position.symbol.upper(),
                    "action": "BLOCKED",
                    "reasonCodes": ("meta_strategy.position_management.protective_state_missing",),
                }
            )
            continue
        explicit_payload = {**request, "position": _jsonable(lifecycle)}
        decisions.append(_jsonable(manage_meta_strategy_trade(_exit_inputs_from_payload(explicit_payload))))
    return tuple(decisions)


def _manage_open_positions(
    *,
    repository: MetaStrategyJobRepository,
    inventory_repository: MetaStrategySqliteRepository,
    job,
    payload: dict[str, Any],
    snapshot,
    mark_prices: dict[str, float],
    now: datetime,
) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    created = 0
    blocked_symbols: set[str] = set()
    candle_by_symbol = _candles_by_symbol(payload, now=now, mark_prices=mark_prices)
    for open_position in snapshot.open_positions:
        symbol = open_position.symbol.upper()
        candle = candle_by_symbol.get(symbol)
        lifecycle = _position_lifecycle(inventory_repository, payload, open_position, snapshot)
        if lifecycle is None:
            inventory_repository.record_quarantine(
                _lifecycle_payload(payload, snapshot, open_position, job=job, now=now, status="PROTECTIVE_STATE_MISSING", reason="PROTECTIVE_STATE_MISSING"),
                reason="PROTECTIVE_STATE_MISSING",
            )
            blocked_symbols.add(symbol)
            decisions.append({"positionId": open_position.position_id, "symbol": symbol, "action": "BLOCKED", "reasonCodes": ("meta_strategy.position_management.protective_state_missing",)})
            continue
        if candle is None:
            inventory_repository.record_position_lifecycle(
                _lifecycle_payload(payload, snapshot, open_position, job=job, now=now, status="AWAITING_FINALIZED_CANDLE", reason="AWAITING_FINALIZED_CANDLE", lifecycle=lifecycle)
            )
            blocked_symbols.add(symbol)
            decisions.append({"positionId": open_position.position_id, "symbol": symbol, "action": "HOLD", "reasonCodes": ("meta_strategy.position_management.finalized_candle_required",)})
            continue

        missing_protective = _protective_order_missing(payload, symbol=symbol, quantity=open_position.quantity, lifecycle=lifecycle)
        inputs = MetaStrategyExitInputs(
            position=lifecycle,
            candle=candle,
            session_end_exit=_session_end_exit_due(payload, now=now),
            event_risk_exit=bool(_symbol_flag(payload, "signalInvalidation", symbol) or _symbol_flag(payload, "signal_invalidation", symbol)),
            liquidity_emergency_exit=bool(payload.get("liquidityEmergencyExit") or payload.get("liquidity_emergency_exit") or missing_protective),
            global_emergency_exit=bool(payload.get("globalEmergencyExit") or payload.get("global_emergency_exit")),
            proposed_stop=_optional_float(payload.get("proposedStop") or payload.get("proposed_stop")),
            ml_delay_requested=bool(payload.get("mlDelayRequested") or payload.get("ml_delay_requested")),
            partial_exit_enabled=bool(payload.get("partialExitEnabled") if "partialExitEnabled" in payload else payload.get("partial_exit_enabled", True)),
            partial_exit_fraction=float(payload.get("partialExitFraction") or payload.get("partial_exit_fraction") or 0.5),
            partial_exit_trigger_r=float(payload.get("partialExitTriggerR") or payload.get("partial_exit_trigger_r") or 1.0),
        )
        if _symbol_flag(payload, "regimeInvalidation", symbol) or _symbol_flag(payload, "regime_invalidation", symbol):
            inputs = MetaStrategyExitInputs(**{**inputs.__dict__, "event_risk_exit": True})
        result = manage_meta_strategy_trade(inputs)
        decision = _jsonable(result)
        decisions.append(decision)
        inventory_repository.record_position_lifecycle(
            _lifecycle_payload(
                payload,
                snapshot,
                open_position,
                job=job,
                now=now,
                status=result.exit_decision.action,
                reason=result.exit_decision.exit_reason,
                lifecycle=result.exit_decision.updated_position,
                decision=decision,
            )
        )
        if result.exit_decision.action in {"EXIT", "PARTIAL_EXIT"}:
            if _exit_already_unresolved(repository, position_id=open_position.position_id):
                blocked_symbols.add(symbol)
                continue
            exit_intent = _exit_order_intent(payload, snapshot, open_position, result, job=job, now=now)
            repository.enqueue_position_exit_outbox(job=job, order_intent=exit_intent, now=now)
            inventory_repository.record_order_intent(exit_intent)
            blocked_symbols.add(symbol)
            created += 1
        if _partial_entry_remainder_should_cancel(payload, inventory_repository, open_position):
            repository.enqueue_job(
                job_type="stale_order_handling",
                idempotency_key=f"meta_strategy.position_management.cancel_entry_remainder.{open_position.position_id}.{job.job_id}",
                payload={
                    **_identity_payload(payload, snapshot, open_position, job=job, now=now),
                    "symbol": symbol,
                    "positionId": open_position.position_id,
                    "reasonCodes": ("meta_strategy.position_management.cancel_remaining_entry_quantity",),
                },
                now=now,
            )
    return {"decisions": tuple(decisions), "createdExitIntentCount": created, "blockedEntrySymbols": tuple(sorted(blocked_symbols))}


def _position_lifecycle(inventory_repository: MetaStrategySqliteRepository, payload: dict[str, Any], open_position, snapshot) -> MetaStrategyPositionState | None:
    override = _position_state_override(payload, open_position.position_id, open_position.symbol)
    if override:
        return _position_from_payload({**override, "quantity": open_position.quantity, "side": _exit_side(open_position.side), "entryPrice": open_position.average_price})
    latest = inventory_repository.latest_position_lifecycle(position_id=open_position.position_id, symbol=open_position.symbol)
    if latest is not None and isinstance(latest.get("payload"), dict):
        state = dict(latest["payload"].get("positionState") or latest["payload"])
        if state.get("protectiveStop") and state.get("profitTarget"):
            return _position_from_payload({**state, "quantity": open_position.quantity, "remainingQuantity": open_position.quantity})
    order_intent = _latest_entry_order_intent(inventory_repository, open_position)
    if order_intent is None:
        return None
    order_payload = dict(order_intent.get("payload") or {})
    stop = _optional_float(order_payload.get("stopPrice"))
    target = _optional_float(order_payload.get("targetPrice"))
    if stop is None or target is None:
        return None
    return MetaStrategyPositionState(
        position_id=open_position.position_id,
        symbol=open_position.symbol,
        side=_exit_side(open_position.side),
        original_quantity=max(1, int(round(open_position.quantity))),
        remaining_quantity=max(0, int(round(open_position.quantity))),
        entry_price=float(open_position.average_price),
        opened_at=_position_opened_at(snapshot, open_position.symbol),
        protective_stop=stop,
        profit_target=target,
        maximum_holding_minutes=int(order_payload.get("maximumHoldingMinutes") or order_payload.get("maximum_holding_minutes") or payload.get("maximumHoldingMinutes") or payload.get("maximum_holding_minutes") or 30),
        protective_order_quantity=int(order_payload.get("protectiveOrderQuantity") or round(open_position.quantity)),
        partial_exit_taken=bool(order_payload.get("partialExitTaken") or False),
    )


def _position_state_override(payload: dict[str, Any], position_id: str, symbol: str) -> dict[str, Any] | None:
    raw = payload.get("positionManagementStates") or payload.get("position_management_states") or {}
    if not isinstance(raw, dict):
        return None
    for key in (position_id, symbol.upper(), symbol):
        value = raw.get(key)
        if isinstance(value, dict):
            return dict(value)
    return None


def _latest_entry_order_intent(inventory_repository: MetaStrategySqliteRepository, open_position) -> dict[str, Any] | None:
    expected_side = "BUY" if open_position.side == "LONG" else "SELL"
    for record in inventory_repository.inventory_records("order_intents", limit=500):
        if str(record.get("symbol") or "").upper() != open_position.symbol.upper():
            continue
        if str(record.get("side") or "").upper() != expected_side:
            continue
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if str(payload.get("intent") or payload.get("orderIntentType") or "new_entry") != "new_entry":
            continue
        return record
    return None


def _position_opened_at(snapshot, symbol: str) -> datetime:
    candidates = [
        _parse_datetime(lot.opened_at)
        for lot in snapshot.open_lots
        if lot.symbol.upper() == symbol.upper()
    ]
    return min(candidates) if candidates else _parse_datetime(snapshot.created_at)


def _candles_by_symbol(payload: dict[str, Any], *, now: datetime, mark_prices: dict[str, float]) -> dict[str, MetaStrategyExitCandle]:
    candles: dict[str, MetaStrategyExitCandle] = {}
    raw = payload.get("candles") or payload.get("latestCandles") or payload.get("latest_candles") or {}
    if isinstance(raw, dict):
        for symbol, candle_payload in raw.items():
            if isinstance(candle_payload, dict):
                candles[str(symbol).upper()] = _candle_from_payload(candle_payload)
    single = payload.get("candle") or payload.get("latestCandle")
    if isinstance(single, dict):
        symbol = str(single.get("symbol") or payload.get("symbol") or "").upper()
        if symbol:
            candles[symbol] = _candle_from_payload(single)
    for symbol, price in mark_prices.items():
        candles.setdefault(
            symbol.upper(),
            MetaStrategyExitCandle(timestamp=now, open=float(price), high=float(price), low=float(price), close=float(price), volume=0.0),
        )
    return candles


def _lifecycle_payload(
    payload: dict[str, Any],
    snapshot,
    open_position,
    *,
    job=None,
    now: datetime,
    status: str,
    reason: str,
    lifecycle: MetaStrategyPositionState | None = None,
    decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = _identity_payload(payload, snapshot, open_position, job=job, now=now)
    position_state = _jsonable(lifecycle) if lifecycle is not None else {"positionId": open_position.position_id, "symbol": open_position.symbol, "quantity": open_position.quantity}
    return {
        **base,
        "positionId": open_position.position_id,
        "symbol": open_position.symbol,
        "side": _exit_side(open_position.side),
        "quantity": float(open_position.quantity),
        "price": float(open_position.market_price),
        "status": status,
        "exitReason": reason,
        "positionState": position_state,
        "entryBlockedWhileExitUnresolved": status in {"EXIT", "PARTIAL_EXIT", "PROTECTIVE_STATE_MISSING"},
        "timestamp": now.isoformat(),
        "payload": {
            "positionId": open_position.position_id,
            "snapshotId": snapshot.snapshot_id,
            "positionState": position_state,
            "exitReason": reason,
            "exitDecision": decision,
            "entryBlockedWhileExitUnresolved": status in {"EXIT", "PARTIAL_EXIT", "PROTECTIVE_STATE_MISSING"},
            "lifecycleAttribution": base,
        },
    }


def _identity_payload(payload: dict[str, Any], snapshot, open_position, *, job, now: datetime) -> dict[str, Any]:
    job_id = getattr(job, "job_id", None) or str(payload.get("jobId") or payload.get("job_id") or "")
    event_id = str(payload.get("eventId") or payload.get("event_id") or job_id)
    decision_id = str(payload.get("decisionId") or payload.get("decision_id") or f"meta_strategy.position_management.{open_position.position_id}")
    return {
        "algorithmId": "meta_strategy",
        "algorithm_id": "meta_strategy",
        "capitalPartitionId": str(payload.get("capitalPartitionId") or snapshot.capital_partition_id),
        "capital_partition_id": str(payload.get("capitalPartitionId") or payload.get("capital_partition_id") or snapshot.capital_partition_id),
        "settingsVersion": str(payload.get("settingsVersion") or snapshot.settings_version),
        "settings_version": str(payload.get("settingsVersion") or payload.get("settings_version") or snapshot.settings_version),
        "strategyCatalogVersion": str(payload.get("strategyCatalogVersion") or payload.get("strategy_catalog_version") or "meta_strategy_strategy_catalog_v1"),
        "strategy_catalog_version": str(payload.get("strategyCatalogVersion") or payload.get("strategy_catalog_version") or "meta_strategy_strategy_catalog_v1"),
        "featureSchemaVersion": str(payload.get("featureSchemaVersion") or payload.get("feature_schema_version") or "meta_strategy_feature_schema_v1"),
        "feature_schema_version": str(payload.get("featureSchemaVersion") or payload.get("feature_schema_version") or "meta_strategy_feature_schema_v1"),
        "modelVersion": str(payload.get("modelVersion") or payload.get("model_version") or "none"),
        "model_version": str(payload.get("modelVersion") or payload.get("model_version") or "none"),
        "decisionId": decision_id,
        "decision_id": decision_id,
        "jobId": job_id,
        "job_id": job_id,
        "eventId": event_id,
        "event_id": event_id,
        "correlationId": str(payload.get("correlationId") or payload.get("correlation_id") or f"{open_position.position_id}:{now.isoformat()}"),
        "correlation_id": str(payload.get("correlationId") or payload.get("correlation_id") or f"{open_position.position_id}:{now.isoformat()}"),
    }


def _exit_order_intent(payload: dict[str, Any], snapshot, open_position, result, *, job, now: datetime) -> dict[str, Any]:
    decision = result.exit_decision
    order_intent_id = f"meta_strategy.exit.{open_position.position_id}.{decision.exit_reason}"
    side = "SELL" if open_position.side == "LONG" else "BUY"
    identity = _identity_payload(payload, snapshot, open_position, job=job, now=now)
    reserved_risk = 0.0
    return {
        **identity,
        "intent": "end_of_day_liquidation" if decision.exit_reason == "SESSION_END" else "protective_exit",
        "orderIntentType": "protective_exit",
        "positionId": open_position.position_id,
        "mode": str(payload.get("mode") or "PAPER"),
        "orderIntentId": order_intent_id,
        "idempotencyKey": f"{job.idempotency_key}:position_exit:{order_intent_id}",
        "symbol": open_position.symbol,
        "side": side,
        "quantity": int(decision.exit_quantity),
        "limitPrice": float(decision.exit_price or open_position.market_price),
        "entryPrice": float(decision.exit_price or open_position.market_price),
        "stopPrice": None,
        "targetPrice": None,
        "reservedRiskDollars": reserved_risk,
        "createdAt": now.isoformat(),
        "timestamp": now.isoformat(),
        "exitReason": decision.exit_reason,
        "reasonCodes": tuple(result.reason_codes),
        "positionManagement": {
            "ownedBy": "MetaStrategyPositionManagementWorker",
            "decisionWorkerNewEntriesOnly": True,
            "avoidDuplicateExits": True,
            "preventNewEntryWhileExitUnresolved": True,
            "exitDecision": _jsonable(decision),
        },
    }


def _exit_already_unresolved(repository: MetaStrategyJobRepository, *, position_id: str) -> bool:
    prefix = f"meta_strategy.exit.{position_id}."
    for reason in ("PROTECTIVE_STOP", "PROFIT_TARGET", "MAXIMUM_HOLD", "SESSION_END", "EVENT_RISK", "LIQUIDITY_EMERGENCY", "GLOBAL_EMERGENCY", "PARTIAL_TARGET"):
        try:
            outbox = repository.outbox_for_order_intent(prefix + reason)
        except KeyError:
            continue
        if str(outbox.get("status") or "").upper() not in {"FILLED", "CANCELLED", "CANCELED", "EXPIRED", "REJECTED", "DEAD_LETTER"}:
            return True
    return False


def _partial_entry_remainder_should_cancel(payload: dict[str, Any], inventory_repository: MetaStrategySqliteRepository, open_position) -> bool:
    if not bool(payload.get("cancelRemainingEntryOnPartial") or payload.get("cancel_remaining_entry_on_partial")):
        return False
    order_intent = _latest_entry_order_intent(inventory_repository, open_position)
    if order_intent is None:
        return False
    requested_value = order_intent.get("quantity")
    if requested_value is None:
        payload = order_intent.get("payload") if isinstance(order_intent.get("payload"), dict) else {}
        requested_value = payload.get("quantity")
    requested = float(requested_value) if requested_value is not None else 0.0
    return requested > float(open_position.quantity)


def _protective_order_missing(payload: dict[str, Any], *, symbol: str, quantity: float, lifecycle: MetaStrategyPositionState) -> bool:
    protective = payload.get("protectiveOrders") or payload.get("protective_orders") or {}
    if isinstance(protective, dict) and protective:
        order = protective.get(symbol.upper()) or protective.get(symbol)
        if isinstance(order, dict):
            if str(order.get("status") or "").upper() in {"CANCELED", "CANCELLED", "REJECTED", "EXPIRED", "MISSING", "STALE"}:
                return True
            order_quantity = order.get("quantity")
            return (float(order_quantity) if order_quantity is not None else 0.0) + 1e-9 < float(quantity)
    return int(lifecycle.protective_order_quantity) < int(round(quantity))


def _session_end_exit_due(payload: dict[str, Any], *, now: datetime) -> bool:
    if bool(payload.get("sessionEndExit") or payload.get("session_end_exit") or payload.get("endOfDayExit") or payload.get("end_of_day_exit")):
        return True
    if bool(payload.get("noOvernight") or payload.get("no_overnight")) and _deadline_passed(payload, now=now):
        return True
    return _deadline_passed(payload, now=now)


def _deadline_passed(payload: dict[str, Any], *, now: datetime) -> bool:
    raw = payload.get("endOfDayExitAt") or payload.get("end_of_day_exit_at")
    if raw:
        return now >= _parse_datetime(raw)
    raw_time = payload.get("endOfDayExitTime") or payload.get("end_of_day_exit_time")
    if raw_time:
        parsed = time.fromisoformat(str(raw_time))
        return now.time() >= parsed
    return False


def _symbol_flag(payload: dict[str, Any], key: str, symbol: str) -> bool:
    value = payload.get(key)
    if isinstance(value, dict):
        return bool(value.get(symbol.upper()) or value.get(symbol))
    if isinstance(value, (list, tuple, set)):
        return symbol.upper() in {str(item).upper() for item in value}
    return bool(value)


def _exit_side(position_side: str) -> str:
    return "BUY" if str(position_side).upper() == "LONG" else "SELL"


def _exit_inputs_from_payload(payload: dict[str, Any]) -> MetaStrategyExitInputs:
    position = _position_from_payload(dict(payload.get("position") or {}))
    candle = _candle_from_payload(dict(payload.get("candle") or {}))
    return MetaStrategyExitInputs(
        position=position,
        candle=candle,
        session_end_exit=bool(payload.get("sessionEndExit") or payload.get("session_end_exit") or False),
        event_risk_exit=bool(payload.get("eventRiskExit") or payload.get("event_risk_exit") or False),
        liquidity_emergency_exit=bool(payload.get("liquidityEmergencyExit") or payload.get("liquidity_emergency_exit") or False),
        global_emergency_exit=bool(payload.get("globalEmergencyExit") or payload.get("global_emergency_exit") or False),
        proposed_stop=_optional_float(payload.get("proposedStop") if "proposedStop" in payload else payload.get("proposed_stop")),
        ml_delay_requested=bool(payload.get("mlDelayRequested") or payload.get("ml_delay_requested") or False),
        partial_exit_enabled=bool(payload.get("partialExitEnabled") if "partialExitEnabled" in payload else payload.get("partial_exit_enabled", True)),
        partial_exit_fraction=_float_value(payload.get("partialExitFraction") if payload.get("partialExitFraction") is not None else payload.get("partial_exit_fraction"), default=0.5),
        partial_exit_trigger_r=_float_value(payload.get("partialExitTriggerR") if payload.get("partialExitTriggerR") is not None else payload.get("partial_exit_trigger_r"), default=1.0),
    )


def _position_from_payload(payload: dict[str, Any]) -> MetaStrategyPositionState:
    return MetaStrategyPositionState(
        position_id=str(payload.get("positionId") or payload.get("position_id") or ""),
        symbol=str(payload.get("symbol") or ""),
        side=str(payload.get("side") or "BUY"),  # type: ignore[arg-type]
        original_quantity=_int_value(_first_present(payload, "originalQuantity", "original_quantity", "quantity")),
        remaining_quantity=_int_value(_first_present(payload, "remainingQuantity", "remaining_quantity", "quantity")),
        entry_price=_float_value(_first_present(payload, "entryPrice", "entry_price"), default=0.0),
        opened_at=_parse_datetime(payload.get("openedAt") or payload.get("opened_at")),
        protective_stop=_float_value(_first_present(payload, "protectiveStop", "protective_stop"), default=0.0),
        profit_target=_float_value(_first_present(payload, "profitTarget", "profit_target"), default=0.0),
        maximum_holding_minutes=_int_value(_first_present(payload, "maximumHoldingMinutes", "maximum_holding_minutes", default=1)),
        protective_order_quantity=_int_value(_first_present(payload, "protectiveOrderQuantity", "protective_order_quantity", "quantity")),
        partial_exit_taken=bool(payload.get("partialExitTaken") or payload.get("partial_exit_taken") or False),
    )


def _candle_from_payload(payload: dict[str, Any]) -> MetaStrategyExitCandle:
    return MetaStrategyExitCandle(
        timestamp=_parse_datetime(payload.get("timestamp") or payload.get("barEnd") or payload.get("bar_end")),
        open=float(payload.get("open") or payload.get("o") or 0.0),
        high=float(payload.get("high") or payload.get("h") or 0.0),
        low=float(payload.get("low") or payload.get("l") or 0.0),
        close=float(payload.get("close") or payload.get("c") or 0.0),
        volume=float(payload.get("volume") or payload.get("v") or 0.0),
    )


def _parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _float_value(value: Any, *, default: float) -> float:
    return float(value) if value is not None else float(default)


def _int_value(value: Any) -> int:
    return int(float(value) if value is not None else 0)


def _first_present(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if payload.get(key) is not None:
            return payload[key]
    return default


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    return value
