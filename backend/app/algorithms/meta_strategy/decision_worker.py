"""Finalised-bar driven Meta-Strategy decision worker."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel

from backend.app.algorithms.meta_strategy.execution_pipeline import (
    MetaStrategyExecutionPipelineConfig,
    MetaStrategyExecutionPipelineRequest,
    MetaStrategyExecutionPipelineResult,
    run_meta_strategy_execution_pipeline,
)
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
        try:
            event_record = self._event_for_job(job)
            event = self._decision_event(job, event_record)
            context = self.state_provider.load_context(event)
            started = datetime.now(UTC)
            result = self.pipeline_runner(
                MetaStrategyExecutionPipelineRequest(
                    mode=_pipeline_mode(event.mode),
                    snapshot_request=context.market_snapshot_request,
                    model_artifact=dict(context.active_model_artifact) if context.active_model_artifact else None,
                ),
                context.settings,
                context.global_risk_snapshot,
            )
            latency_ms = int((datetime.now(UTC) - started).total_seconds() * 1000)
            data_age_seconds = int(max(0.0, (context.market_snapshot_request.decision_timestamp - event.bar_end).total_seconds()))
            persisted = self.repository.persist_decision_atomic(
                job=job,
                event=event_record,
                decision_id=result.snapshot.decision_id,
                payload=_decision_payload(
                    context=context,
                    result=result,
                    latency_ms=latency_ms,
                    data_age_seconds=data_age_seconds,
                ),
                order_intent=_order_payload(result),
                now=current,
            )
            self.repository.complete_job(job.job_id, worker_id=self.worker_id, result=persisted, now=current)
            return job
        except Exception as exc:
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
        global_risk_adapter=None,
    )


def _pipeline_mode(mode: str) -> str:
    normalized = mode.upper()
    return normalized if normalized in {"PAPER", "SHADOW", "BACKTEST", "DAILY_REPLAY", "DIAGNOSTICS", "EVALUATION"} else "PAPER"


def _decision_payload(
    *,
    context: MetaStrategyDecisionWorkerContext,
    result: MetaStrategyExecutionPipelineResult,
    latency_ms: int,
    data_age_seconds: int,
) -> dict[str, Any]:
    return {
        "algorithmId": "meta_strategy",
        "decisionId": result.snapshot.decision_id,
        "eventId": context.event.event_id,
        "jobId": context.event.job_id,
        "symbol": context.event.symbol,
        "barEnd": context.event.bar_end.isoformat(),
        "settingsVersion": result.settings_version,
        "effectiveSettingsHash": result.effective_settings_hash,
        "modelVersion": str((context.active_model_artifact or {}).get("modelVersion") or (context.active_model_artifact or {}).get("model_version") or "none"),
        "decisionStatus": "ORDER_PROPOSED" if result.order_intent is not None and result.final_valid else "HOLD_OR_BLOCKED",
        "reasonCodes": result.reason_codes,
        "latencyMs": latency_ms,
        "dataAgeSeconds": data_age_seconds,
        "authoritativeState": {
            "inventorySnapshot": dict(context.inventory_snapshot),
            "accountSnapshot": dict(context.account_snapshot),
            "globalRiskSnapshot": dict(context.global_risk_snapshot),
            "eventState": dict(context.event_state),
            "operationalHealth": dict(context.operational_health),
        },
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


def _order_payload(result: MetaStrategyExecutionPipelineResult) -> dict[str, Any] | None:
    if result.order_intent is None or not result.final_valid:
        return None
    payload = _plain(result.order_intent)
    payload["decisionId"] = result.snapshot.decision_id
    payload["settingsVersion"] = result.settings_version
    payload["effectiveSettingsHash"] = result.effective_settings_hash
    return payload


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
