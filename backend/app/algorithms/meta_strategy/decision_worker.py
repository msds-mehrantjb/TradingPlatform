"""Finalised-bar driven Meta-Strategy decision worker."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Protocol

from pydantic import BaseModel

from backend.app.algorithms.meta_strategy.ownership import META_STRATEGY_DEFAULT_CAPITAL_PARTITION
from backend.app.algorithms.meta_strategy.execution_pipeline import (
    MetaStrategyExecutionPipelineConfig,
    MetaStrategyExecutionPipelineRequest,
    MetaStrategyExecutionPipelineResult,
    run_meta_strategy_execution_pipeline,
)
from backend.app.algorithms.meta_strategy.global_risk_adapter import ReadOnlyMetaStrategyGlobalRiskAdapter
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyEventRecord, MetaStrategyJobRecord, MetaStrategyJobRepository, MetaStrategyWorker
from backend.app.algorithms.meta_strategy.market_snapshot import MetaStrategyMarketSnapshotRequest
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettings


@dataclass(frozen=True)
class MetaStrategyFinalisedBarDecisionEvent:
    event_id: str
    job_id: str
    mode: str
    symbol: str
    timeframe: str
    bar_end: datetime
    settings_version: str
    idempotency_key: str
    capital_partition_id: str = META_STRATEGY_DEFAULT_CAPITAL_PARTITION


@dataclass(frozen=True)
class MetaStrategyDecisionWorkerContext:
    event: MetaStrategyFinalisedBarDecisionEvent
    settings: MetaStrategySettings
    market_snapshot_request: MetaStrategyMarketSnapshotRequest
    inventory_snapshot: Mapping[str, Any]
    account_snapshot: Mapping[str, Any]
    global_risk_snapshot: Mapping[str, Any]
    event_state: Mapping[str, Any]
    operational_health: Mapping[str, Any]
    active_model_artifact: Mapping[str, Any] | None


class MetaStrategyDecisionStateProvider(Protocol):
    def load_context(self, event: MetaStrategyFinalisedBarDecisionEvent) -> MetaStrategyDecisionWorkerContext:
        ...


PipelineRunner = Callable[[MetaStrategyExecutionPipelineRequest, MetaStrategySettings, Mapping[str, Any] | None], MetaStrategyExecutionPipelineResult]
StartupReconstructor = Callable[[], Mapping[str, Any] | None]


class MetaStrategyFinalisedBarDecisionWorker(MetaStrategyWorker):
    def __init__(
        self,
        *,
        repository: MetaStrategyJobRepository,
        state_provider: MetaStrategyDecisionStateProvider,
        worker_id: str = "meta_strategy.finalised_bar_decision_worker",
        pipeline_runner: PipelineRunner | None = None,
        startup_reconstructor: StartupReconstructor | None = None,
    ) -> None:
        super().__init__(repository=repository, queue_name="finalised_bar_decisions", worker_id=worker_id)
        self.state_provider = state_provider
        self.pipeline_runner = pipeline_runner or _run_pipeline_without_broker
        self.startup_reconstructor = startup_reconstructor
        self.startup_reconstructed = startup_reconstructor is None

    def run_once(self, *, now: datetime | None = None, handler=None) -> MetaStrategyJobRecord | None:  # type: ignore[override]
        current = now or datetime.now(UTC)
        if self.shutdown_requested:
            self.repository.record_worker_heartbeat(worker_id=self.worker_id, queue_name=self.queue_name, now=current)
            return None
        if not self.startup_reconstructed:
            self._reconstruct_before_claim()
        job = self.repository.claim_next_job(queue_name=self.queue_name, worker_id=self.worker_id, lease_seconds=self.lease_seconds, now=current)
        if job is None:
            self.repository.record_worker_heartbeat(worker_id=self.worker_id, queue_name=self.queue_name, now=current)
            return None
        event_record: MetaStrategyEventRecord | None = None
        event: MetaStrategyFinalisedBarDecisionEvent | None = None
        try:
            event_record = self._event_for_job(job)
            event = self._decision_event(job, event_record)
            context = self.state_provider.load_context(event)
            started = datetime.now(UTC)
            realized_daily_pnl = _optional_float(context.inventory_snapshot, "realizedPnl", "realisedPnl", "realized_pnl", "realised_pnl")
            daily_trade_count = _optional_int(context.inventory_snapshot, "dailyTradeCount", "daily_trade_count")
            max_quote_age_seconds = _optional_int(context.operational_health, "maxQuoteAgeSeconds", "max_quote_age_seconds")
            result = self.pipeline_runner(
                MetaStrategyExecutionPipelineRequest(
                    mode=_pipeline_mode(event.mode),
                    snapshot_request=context.market_snapshot_request,
                    model_artifact=dict(context.active_model_artifact) if context.active_model_artifact else None,
                    settings_version=context.event.settings_version,
                    active_settings_version=str((context.event_state.get("sourceVersions") or {}).get("activeSettingsVersion") or ""),
                    inventory_snapshot=dict(context.inventory_snapshot),
                    reserved_risk_ledger=_reserved_risk_ledger(context.inventory_snapshot),
                    account_snapshot=dict(context.account_snapshot),
                    global_risk_snapshot=dict(context.global_risk_snapshot),
                    event_state=dict(context.event_state),
                    operational_health=dict(context.operational_health),
                    operational_controls=_mapping_or_empty(context.operational_health.get("operationalControls")),
                    runtime_health=_mapping_or_empty(context.operational_health.get("runtimeHealth")),
                    market_clock_state=_mapping_or_empty(context.operational_health.get("marketCalendar")),
                    paper_control_state=_mapping_or_empty(context.operational_health.get("paperControl")),
                    state_source_versions=_mapping_or_empty(context.event_state.get("sourceVersions")),
                    state_source_timestamps=_mapping_or_empty(context.event_state.get("sourceTimestamps")),
                    account_equity=_optional_float(context.account_snapshot, "accountEquity", "account_equity", "equity"),
                    available_buying_power=_optional_float(context.account_snapshot, "buyingPower", "buying_power"),
                    remaining_algorithm_risk=_remaining_algorithm_risk(context.inventory_snapshot),
                    global_available_risk=_optional_float(context.global_risk_snapshot, "availableRiskDollars", "available_risk_dollars"),
                    global_quantity_cap=_optional_int(context.global_risk_snapshot, "maxQuantity", "max_quantity", "globalQuantityCap", "global_quantity_cap"),
                    realized_daily_pnl=realized_daily_pnl if realized_daily_pnl is not None else 0.0,
                    daily_trade_count=daily_trade_count if daily_trade_count is not None else 0,
                    last_trade_at=_last_trade_at(context.inventory_snapshot),
                    paper_trading_permission=_paper_trading_allowed(context),
                    event_blackout=_event_blackout(context),
                    session_allowed=_session_allowed(context),
                    duplicate_order_intent_ids=_duplicate_order_intent_ids(context.inventory_snapshot),
                    existing_position_symbols=_existing_position_symbols(context.inventory_snapshot),
                    max_quote_age_seconds=max_quote_age_seconds if max_quote_age_seconds is not None else 60,
                ),
                context.settings,
                context.global_risk_snapshot,
            )
            latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
            data_age_seconds = int(max(0.0, (context.market_snapshot_request.decision_timestamp - event.bar_end).total_seconds()))
            queue_delay_ms = int(max(0.0, (current - _parse_dt(event_record.created_at)).total_seconds()) * 1000)
            persistence_started = perf_counter()
            decision_payload = _decision_payload(
                context=context,
                result=result,
                latency_ms=latency_ms,
                data_age_seconds=data_age_seconds,
                queue_delay_ms=queue_delay_ms,
                decision_persistence_time_ms=0,
            )
            persisted = self.repository.persist_decision_atomic(
                job=job,
                event=event_record,
                decision_id=result.snapshot.decision_id,
                payload=decision_payload,
                order_intent=_order_payload(result, context=context),
                now=current,
            )
            decision_payload["latencyMeasurements"]["decisionPersistenceTimeMs"] = int((perf_counter() - persistence_started) * 1000)
            self.repository.complete_job(job.job_id, worker_id=self.worker_id, result=persisted, now=current)
            return job
        except Exception as exc:
            if event_record is not None:
                try:
                    self.repository.record_finalized_candle_outcome(
                        event_id=event_record.event_id,
                        job_id=job.job_id,
                        decision_id="",
                        order_intent_id="",
                        client_order_id="",
                        symbol=event.symbol if event is not None else "",
                        bar_end=event.bar_end.isoformat() if event is not None else event_record.created_at,
                        outcome="NO_DECISION",
                        payload={
                            "eventId": event_record.event_id,
                            "jobId": job.job_id,
                            "errorCategory": type(exc).__name__,
                            "reasonCodes": ("meta_strategy.decision_worker.no_decision_recorded_after_failure",),
                        },
                        reason_codes=("meta_strategy.decision_worker.no_decision_recorded_after_failure",),
                        now=current,
                    )
                except Exception:
                    pass
            self.repository.fail_job(job.job_id, worker_id=self.worker_id, error_category=type(exc).__name__, error_details=str(exc), now=current)
            return job

    def _reconstruct_before_claim(self) -> None:
        if self.startup_reconstructor is not None:
            result = self.startup_reconstructor()
            if isinstance(result, Mapping) and str(result.get("status") or "OK").upper() not in {"OK", "SUCCEEDED", "READY"}:
                raise RuntimeError("meta_strategy.decision_worker.restart_reconstruction_failed")
        self.startup_reconstructed = True

    def _event_for_job(self, job: MetaStrategyJobRecord) -> MetaStrategyEventRecord:
        payload = self.repository.read_payload(job.payload_reference)
        event_id = str((payload.get("payload") or payload).get("eventId") or "")
        if not event_id:
            raise ValueError("finalised-bar decision job is missing eventId")
        return self.repository.event_by_id(event_id)

    def _decision_event(self, job: MetaStrategyJobRecord, event: MetaStrategyEventRecord) -> MetaStrategyFinalisedBarDecisionEvent:
        payload = self.repository.read_payload(event.payload_reference).get("payload") or {}
        return MetaStrategyFinalisedBarDecisionEvent(
            event_id=event.event_id,
            job_id=job.job_id,
            mode=str(payload["mode"]),
            symbol=str(payload["symbol"]).upper(),
            timeframe=str(payload["timeframe"]),
            bar_end=_parse_dt(str(payload["barEnd"])),
            settings_version=str(payload["settingsVersion"]),
            idempotency_key=job.idempotency_key,
            capital_partition_id=str(
                payload.get("capitalPartitionId")
                or payload.get("capital_partition_id")
                or META_STRATEGY_DEFAULT_CAPITAL_PARTITION
            ),
        )


def _run_pipeline_without_broker(
    request: MetaStrategyExecutionPipelineRequest,
    settings: MetaStrategySettings,
    global_risk_snapshot: Mapping[str, Any] | None,
) -> MetaStrategyExecutionPipelineResult:
    return run_meta_strategy_execution_pipeline(
        request,
        config=MetaStrategyExecutionPipelineConfig(submit_to_broker=False),
        config_settings=settings,
        global_risk_adapter=_global_risk_adapter_from_snapshot(global_risk_snapshot),
    )


def _global_risk_adapter_from_snapshot(global_risk_snapshot: Mapping[str, Any] | None) -> ReadOnlyMetaStrategyGlobalRiskAdapter:
    snapshot = dict(global_risk_snapshot or {})
    return ReadOnlyMetaStrategyGlobalRiskAdapter(
        reject=bool(snapshot.get("reject") or snapshot.get("rejected") or snapshot.get("tradingHalt") or snapshot.get("trading_halt")),
        max_quantity=_optional_int(snapshot, "maxQuantity", "max_quantity", "approvedQuantity", "approved_quantity", "globalQuantityCap", "global_quantity_cap"),
        available_risk_dollars=_optional_float(snapshot, "availableRiskDollars", "available_risk_dollars", "globalAvailableRisk", "global_available_risk"),
        stop_distance=_optional_float(snapshot, "stopDistance", "stop_distance"),
    )


def _pipeline_mode(mode: str) -> str:
    normalized = mode.upper()
    return normalized if normalized in {"PAPER", "SHADOW", "BACKTEST", "DAILY_REPLAY", "DIAGNOSTICS", "EVALUATION"} else "PAPER"


def _optional_int(payload: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        if payload.get(key) is not None:
            return int(payload[key])
    return None


def _optional_float(payload: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        if payload.get(key) is not None:
            return float(payload[key])
    return None


def _decision_payload(
    *,
    context: MetaStrategyDecisionWorkerContext,
    result: MetaStrategyExecutionPipelineResult,
    latency_ms: int,
    data_age_seconds: int,
    queue_delay_ms: int,
    decision_persistence_time_ms: int,
) -> dict[str, Any]:
    stage_durations = _stage_durations(result.stage_results)
    return {
        "algorithmId": "meta_strategy",
        "capitalPartitionId": context.event.capital_partition_id,
        "decisionId": result.snapshot.decision_id,
        "eventId": context.event.event_id,
        "jobId": context.event.job_id,
        "correlationId": context.event.idempotency_key,
        "mode": context.event.mode,
        "symbol": context.event.symbol,
        "barEnd": context.event.bar_end.isoformat(),
        "snapshotId": getattr(result.snapshot, "snapshot_id", result.snapshot.decision_id),
        "settingsVersion": result.settings_version,
        "strategyCatalogVersion": context.market_snapshot_request.strategy_catalog_version,
        "featureSchemaVersion": str((context.event_state or {}).get("featureSchemaVersion") or "meta_strategy_feature_schema_v1"),
        "effectiveSettingsHash": result.effective_settings_hash,
        "modelVersion": str((context.active_model_artifact or {}).get("modelVersion") or (context.active_model_artifact or {}).get("model_version") or "none"),
        "decisionStatus": "ORDER_PROPOSED" if result.order_intent is not None and result.final_valid else "HOLD_OR_BLOCKED",
        "reasonCodes": tuple(
            dict.fromkeys(
                (
                    *result.reason_codes,
                    *tuple(context.event_state.get("reasonCodes") or ()),
                    *tuple(context.operational_health.get("reasonCodes") or ()),
                )
            )
        ),
        "latencyMs": latency_ms,
        "dataAgeSeconds": data_age_seconds,
        "latencyMeasurements": {
            "queueDelayMs": queue_delay_ms,
            "snapshotBuildingTimeMs": stage_durations.get("market_snapshot", 0),
            "strategyEvaluationTimeMs": sum(
                stage_durations.get(stage, 0)
                for stage in ("strategies", "context_and_regime", "family_aggregation", "deterministic_candidate")
            ),
            "inferenceTimeMs": stage_durations.get("model_inference", 0),
            "decisionPersistenceTimeMs": decision_persistence_time_ms,
            "orderSubmissionTimeMs": None,
        },
        "authoritativeState": {
            "inventorySnapshot": dict(context.inventory_snapshot),
            "accountSnapshot": dict(context.account_snapshot),
            "globalRiskSnapshot": dict(context.global_risk_snapshot),
            "eventState": dict(context.event_state),
            "operationalHealth": dict(context.operational_health),
        },
        "sourceVersions": dict(context.event_state.get("sourceVersions") or {}),
        "sourceTimestamps": dict(context.event_state.get("sourceTimestamps") or {}),
        "stages": {
            "snapshot": _plain(result.stage_results.get("market_snapshot")),
            "strategyEvidence": _plain(result.stage_results.get("strategies")),
            "regime": _plain(result.stage_results.get("context_and_regime")),
            "safetyResult": _plain(result.stage_results.get("safety")),
            "aggregateCandidate": _plain(result.stage_results.get("family_aggregation")),
            "modelPrediction": _plain(result.stage_results.get("model_inference")),
            "decisionPolicy": _plain(result.stage_results.get("ml_decision_policy")),
            "localRisk": _plain(result.stage_results.get("local_gates")),
            "sizing": _plain(result.stage_results.get("sizing")),
            "tradeManagementDecision": {"status": "NO_POSITION_MANAGEMENT_IN_DECISION_WORKER", "reasonCodes": ("meta_strategy.trade_management.deferred",)},
            "orderProposal": _plain(result.stage_results.get("order_intent")),
        },
    }


def _stage_durations(stage_results: Mapping[str, Any]) -> dict[str, int]:
    durations: dict[str, int] = {}
    for stage, payload in stage_results.items():
        if isinstance(payload, Mapping):
            duration = payload.get("durationMs")
            durations[str(stage)] = int(duration) if duration is not None else 0
    return durations


def _order_payload(result: MetaStrategyExecutionPipelineResult, *, context: MetaStrategyDecisionWorkerContext) -> dict[str, Any] | None:
    if result.order_intent is None or not result.final_valid:
        return None
    payload = _plain(result.order_intent)
    payload["decisionId"] = result.snapshot.decision_id
    payload["settingsVersion"] = result.settings_version
    payload["effectiveSettingsHash"] = result.effective_settings_hash
    snapshot_timestamp = getattr(result.snapshot, "timestamp", None) or context.event.bar_end
    snapshot_quote = getattr(result.snapshot, "quote", None) or {}
    local_gates = getattr(result, "local_gates", None)
    payload["decisionTimestamp"] = snapshot_timestamp.isoformat()
    payload["quoteTimestamp"] = str((snapshot_quote or {}).get("timestamp") or snapshot_timestamp.isoformat())
    payload["localGatesPassed"] = bool(payload.get("localGatesPassed", getattr(local_gates, "passed", True)))
    payload["accountEquity"] = _optional_float(context.account_snapshot, "accountEquity", "account_equity", "equity")
    payload["buyingPower"] = _optional_float(context.account_snapshot, "buyingPower", "buying_power", "availableBuyingPower", "available_buying_power")
    payload["remainingAlgorithmRisk"] = _remaining_algorithm_risk(context.inventory_snapshot)
    payload["globalAvailableRisk"] = _optional_float(context.global_risk_snapshot, "availableRiskDollars", "available_risk_dollars", "globalAvailableRisk", "global_available_risk")
    payload["globalQuantityCap"] = _optional_int(context.global_risk_snapshot, "maxQuantity", "max_quantity", "globalQuantityCap", "global_quantity_cap")
    payload["reservedRiskDollars"] = _optional_float(payload, "reservedRiskDollars", "reserved_risk_dollars")
    return payload


def _remaining_algorithm_risk(inventory_snapshot: Mapping[str, Any]) -> float | None:
    return _optional_float(
        inventory_snapshot,
        "remainingAlgorithmRisk",
        "remaining_algorithm_risk",
        "remainingRiskDollars",
        "remaining_risk_dollars",
    )


def _reserved_risk_ledger(inventory_snapshot: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    rows = inventory_snapshot.get("reservedRiskLedger") or inventory_snapshot.get("riskReservations") or ()
    if not isinstance(rows, tuple | list):
        return ()
    return tuple(dict(row) for row in rows if isinstance(row, Mapping))


def _last_trade_at(inventory_snapshot: Mapping[str, Any]) -> datetime | None:
    explicit = inventory_snapshot.get("lastTradeAt") or inventory_snapshot.get("last_trade_at")
    if explicit:
        try:
            return _parse_dt(str(explicit))
        except ValueError:
            pass
    fills = inventory_snapshot.get("fills")
    if not isinstance(fills, tuple | list) or not fills:
        return None
    timestamps = []
    for fill in fills:
        if isinstance(fill, Mapping) and fill.get("timestamp"):
            try:
                timestamps.append(_parse_dt(str(fill["timestamp"])))
            except ValueError:
                continue
    return max(timestamps) if timestamps else None


def _mapping_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _paper_trading_allowed(context: MetaStrategyDecisionWorkerContext) -> bool:
    health = context.operational_health
    settings_allowed = bool(getattr(context.settings.paper_execution, "enabled", False))
    health_allowed = bool(health.get("tradingAllowed", health.get("trading_allowed", True)))
    global_allowed = not bool(context.global_risk_snapshot.get("reject") or context.global_risk_snapshot.get("tradingHalt"))
    return settings_allowed and health_allowed and global_allowed


def _event_blackout(context: MetaStrategyDecisionWorkerContext) -> bool:
    event_state = context.event_state
    return bool(
        event_state.get("active")
        or event_state.get("blackout")
        or event_state.get("eventBlackout")
        or str(event_state.get("dataQualityState") or "").upper() == "BLOCKED"
    )


def _session_allowed(context: MetaStrategyDecisionWorkerContext) -> bool:
    market_calendar = context.operational_health.get("marketCalendar")
    if isinstance(market_calendar, Mapping):
        return bool(market_calendar.get("isOpen", True))
    return True


def _duplicate_order_intent_ids(inventory_snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    rows = inventory_snapshot.get("pendingOrderIntents")
    if not isinstance(rows, tuple | list):
        return ()
    return tuple(str(row.get("orderIntentId") or row.get("order_intent_id")) for row in rows if isinstance(row, Mapping) and (row.get("orderIntentId") or row.get("order_intent_id")))


def _existing_position_symbols(inventory_snapshot: Mapping[str, Any]) -> tuple[str, ...]:
    rows = inventory_snapshot.get("positions") or inventory_snapshot.get("currentVirtualPositions")
    if not isinstance(rows, tuple | list):
        return ()
    return tuple(sorted({str(row.get("symbol") or "").upper() for row in rows if isinstance(row, Mapping) and row.get("symbol")}))


def _plain(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_plain(item) for item in value)
    return value


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


__all__ = [
    "MetaStrategyDecisionStateProvider",
    "MetaStrategyDecisionWorkerContext",
    "MetaStrategyFinalisedBarDecisionEvent",
    "MetaStrategyFinalisedBarDecisionWorker",
]
