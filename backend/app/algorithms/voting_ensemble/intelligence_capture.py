from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from queue import Full, Queue
from threading import Event, Thread
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, field_validator

from backend.app.domain.models import DomainModel, _require_utc


VOTING_ENSEMBLE_CAPTURE_SCHEMA_VERSION = "voting_ensemble_intelligence_capture_v1"
VOTING_ENSEMBLE_CAPTURE_NAMESPACE = "voting_ensemble.intelligence_capture"
VOTING_ENSEMBLE_CAPTURE_OVERFLOW_POLICY = "drop_newest_optional_diagnostic"
VOTING_ENSEMBLE_OPERATIONAL_EVENT_TYPES = {
    "global_gate_decision",
    "local_gate_decision",
    "order_plan",
    "broker_event",
    "fill",
    "exit_decision",
    "final_trade_outcome",
    "worker_job_status",
    "error_recovery_event",
}


CAPTURE_TABLES: dict[str, str] = {
    "input_snapshot": "voting_ensemble_capture_input_snapshots",
    "resolved_settings": "voting_ensemble_capture_resolved_settings",
    "regime_state": "voting_ensemble_capture_regime_states",
    "directional_strategy_output": "voting_ensemble_capture_directional_strategy_outputs",
    "shadow_strategy_output": "voting_ensemble_capture_shadow_strategy_outputs",
    "context_output": "voting_ensemble_capture_context_outputs",
    "family_aggregate": "voting_ensemble_capture_family_aggregates",
    "ensemble_decision": "voting_ensemble_capture_ensemble_decisions",
    "global_gate_decision": "voting_ensemble_capture_global_gate_decisions",
    "local_gate_decision": "voting_ensemble_capture_local_gate_decisions",
    "cost_estimate": "voting_ensemble_capture_cost_estimates",
    "latency_measurement": "voting_ensemble_capture_latency_measurements",
    "risk_budget": "voting_ensemble_capture_risk_budgets",
    "order_plan": "voting_ensemble_capture_order_plans",
    "broker_event": "voting_ensemble_capture_broker_events",
    "fill": "voting_ensemble_capture_fills",
    "exit_decision": "voting_ensemble_capture_exit_decisions",
    "final_trade_outcome": "voting_ensemble_capture_trade_outcomes",
    "worker_job_status": "voting_ensemble_capture_worker_job_status",
    "error_recovery_event": "voting_ensemble_capture_error_recovery_events",
}


class VotingEnsembleCaptureRecord(DomainModel):
    captureSchemaVersion: str = VOTING_ENSEMBLE_CAPTURE_SCHEMA_VERSION
    algorithmId: Literal["voting_ensemble"] = "voting_ensemble"
    captureNamespace: Literal["voting_ensemble.intelligence_capture"] = VOTING_ENSEMBLE_CAPTURE_NAMESPACE
    recordId: str = Field(min_length=1)
    eventType: str = Field(min_length=1)
    tableName: str = Field(min_length=1)
    critical: bool
    correlationId: str = Field(min_length=1)
    jobId: str | None = None
    decisionId: str | None = None
    orderId: str | None = None
    settingsHash: str | None = None
    snapshotTimestamp: datetime | None = None
    createdAt: datetime
    payloadHash: str = Field(min_length=1)
    payload: dict[str, Any]
    reasonCodes: list[str] = Field(default_factory=list)

    @field_validator("createdAt", "snapshotTimestamp")
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None


class VotingEnsembleCaptureStore:
    def __init__(self, *, storage_path: str | Path | None = None) -> None:
        self.storagePath = Path(storage_path).resolve() if storage_path is not None else None
        self.records: list[VotingEnsembleCaptureRecord] = []

    def write(self, record: VotingEnsembleCaptureRecord) -> VotingEnsembleCaptureRecord:
        if not record.tableName.startswith("voting_ensemble_capture_"):
            raise ValueError("Voting Ensemble capture cannot write outside voting_ensemble_capture_* namespaces")
        self.records.append(record)
        if self.storagePath is not None:
            self.storagePath.parent.mkdir(parents=True, exist_ok=True)
            with self.storagePath.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record.model_dump(mode="json"), sort_keys=True) + "\n")
        return record

    def list_records(self, **filters: str | None) -> tuple[VotingEnsembleCaptureRecord, ...]:
        records = tuple(self.records)
        for field_name, expected in filters.items():
            if expected is None:
                continue
            records = tuple(record for record in records if getattr(record, field_name) == expected)
        return records

    def reconstruct_replay(
        self,
        *,
        correlationId: str | None = None,
        jobId: str | None = None,
        decisionId: str | None = None,
        orderId: str | None = None,
    ) -> dict[str, Any]:
        records = [
            record
            for record in self.records
            if (correlationId is None or record.correlationId == correlationId)
            and (jobId is None or record.jobId == jobId)
            and (decisionId is None or record.decisionId == decisionId)
            and (orderId is None or record.orderId == orderId)
        ]
        records.sort(key=lambda record: (record.snapshotTimestamp or record.createdAt, record.createdAt, record.recordId))
        return {
            "algorithmId": "voting_ensemble",
            "captureSchemaVersion": VOTING_ENSEMBLE_CAPTURE_SCHEMA_VERSION,
            "recordCount": len(records),
            "records": [record.model_dump(mode="json") for record in records],
            "byEventType": {event_type: [record.payload for record in records if record.eventType == event_type] for event_type in sorted({record.eventType for record in records})},
            "reasonCodes": ["voting_ensemble.capture.replay_reconstructed"],
        }


class VotingEnsembleCaptureWriter:
    def __init__(
        self,
        *,
        store: VotingEnsembleCaptureStore | None = None,
        max_queue_size: int = 1024,
        auto_start: bool = False,
    ) -> None:
        self.store = store or VotingEnsembleCaptureStore()
        self.queue: Queue[VotingEnsembleCaptureRecord] = Queue(maxsize=max_queue_size)
        self.overflowCount = 0
        self.overflowPolicy = VOTING_ENSEMBLE_CAPTURE_OVERFLOW_POLICY
        self._stop = Event()
        self._thread = Thread(target=self._run, name="voting-ensemble-capture-writer", daemon=True)
        if auto_start:
            self.start()

    def start(self) -> None:
        if not self._thread.is_alive():
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def publish(self, record: VotingEnsembleCaptureRecord) -> bool:
        if record.critical:
            self.store.write(record)
            return True
        try:
            self.queue.put_nowait(record)
            return True
        except Full:
            self.overflowCount += 1
            return False

    def publish_many(self, records: tuple[VotingEnsembleCaptureRecord, ...]) -> dict[str, Any]:
        accepted = 0
        dropped = 0
        for record in records:
            if self.publish(record):
                accepted += 1
            else:
                dropped += 1
        return {
            "accepted": accepted,
            "dropped": dropped,
            "overflowCount": self.overflowCount,
            "overflowPolicy": self.overflowPolicy,
            "reasonCodes": ["voting_ensemble.capture.publish_completed"],
        }

    def drain(self, *, limit: int = 1000) -> int:
        count = 0
        while count < limit:
            try:
                record = self.queue.get_nowait()
            except Exception:
                break
            self.store.write(record)
            self.queue.task_done()
            count += 1
        return count

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                record = self.queue.get(timeout=0.25)
            except Exception:
                continue
            self.store.write(record)
            self.queue.task_done()


def build_capture_record(
    *,
    event_type: str,
    payload: dict[str, Any],
    correlation_id: str,
    job_id: str | None = None,
    decision_id: str | None = None,
    order_id: str | None = None,
    settings_hash: str | None = None,
    snapshot_timestamp: datetime | None = None,
    critical: bool | None = None,
    reason_codes: list[str] | None = None,
) -> VotingEnsembleCaptureRecord:
    if event_type not in CAPTURE_TABLES:
        raise ValueError(f"Unsupported Voting Ensemble capture event type: {event_type}")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    payload_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    return VotingEnsembleCaptureRecord(
        recordId=f"ve-capture-{uuid4().hex[:16]}",
        eventType=event_type,
        tableName=CAPTURE_TABLES[event_type],
        critical=event_type in VOTING_ENSEMBLE_OPERATIONAL_EVENT_TYPES if critical is None else critical,
        correlationId=correlation_id,
        jobId=job_id,
        decisionId=decision_id,
        orderId=order_id,
        settingsHash=settings_hash,
        snapshotTimestamp=_utc(snapshot_timestamp) if snapshot_timestamp else None,
        createdAt=datetime.now(UTC),
        payloadHash=payload_hash,
        payload=payload,
        reasonCodes=reason_codes or ["voting_ensemble.capture.record_created"],
    )


def capture_voting_ensemble_evaluation(
    *,
    writer: VotingEnsembleCaptureWriter,
    snapshot: Any,
    settings: Any,
    regime_state: Any,
    directional_votes: tuple[Any, ...],
    shadow_directional_votes: tuple[Any, ...],
    context_signals: tuple[Any, ...],
    shadow_context_signals: tuple[Any, ...],
    decision: Any,
    upstream_global_gate: Any,
    local_gate: Any,
    execution_economics: dict[str, Any] | None,
    risk_budget: dict[str, Any] | None,
    response: dict[str, Any],
    job_id: str | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    resolved_correlation_id = correlation_id or str(response.get("correlationId") or getattr(snapshot, "snapshotHash", "voting-ensemble-correlation"))
    decision_id = str(getattr(decision, "decisionId", "") or response.get("decisionId") or "")
    snapshot_timestamp = getattr(snapshot, "evaluationTimestamp", None)
    settings_hash = getattr(settings, "configurationHash", None) or getattr(snapshot, "settingsHash", None)
    records: list[VotingEnsembleCaptureRecord] = [
        build_capture_record(event_type="input_snapshot", payload=_dump(snapshot), correlation_id=resolved_correlation_id, job_id=job_id, decision_id=decision_id, settings_hash=settings_hash, snapshot_timestamp=snapshot_timestamp),
        build_capture_record(event_type="resolved_settings", payload=_dump(settings), correlation_id=resolved_correlation_id, job_id=job_id, decision_id=decision_id, settings_hash=settings_hash, snapshot_timestamp=snapshot_timestamp),
        build_capture_record(event_type="regime_state", payload=_dump(regime_state), correlation_id=resolved_correlation_id, job_id=job_id, decision_id=decision_id, settings_hash=settings_hash, snapshot_timestamp=snapshot_timestamp),
        build_capture_record(event_type="family_aggregate", payload={"familyScores": response.get("family_scores"), "familySupport": response.get("family_support"), "familyOpposition": response.get("family_opposition")}, correlation_id=resolved_correlation_id, job_id=job_id, decision_id=decision_id, settings_hash=settings_hash, snapshot_timestamp=snapshot_timestamp),
        build_capture_record(event_type="ensemble_decision", payload=_dump(decision), correlation_id=resolved_correlation_id, job_id=job_id, decision_id=decision_id, settings_hash=settings_hash, snapshot_timestamp=snapshot_timestamp),
        build_capture_record(event_type="global_gate_decision", payload=_dump(upstream_global_gate) if upstream_global_gate else {"status": "not_provided"}, correlation_id=resolved_correlation_id, job_id=job_id, decision_id=decision_id, settings_hash=settings_hash, snapshot_timestamp=snapshot_timestamp),
        build_capture_record(event_type="local_gate_decision", payload=_dump(local_gate), correlation_id=resolved_correlation_id, job_id=job_id, decision_id=decision_id, settings_hash=settings_hash, snapshot_timestamp=snapshot_timestamp),
        build_capture_record(event_type="risk_budget", payload=risk_budget or {}, correlation_id=resolved_correlation_id, job_id=job_id, decision_id=decision_id, settings_hash=settings_hash, snapshot_timestamp=snapshot_timestamp),
    ]
    if execution_economics:
        records.append(build_capture_record(event_type="cost_estimate", payload=execution_economics, correlation_id=resolved_correlation_id, job_id=job_id, decision_id=decision_id, settings_hash=settings_hash, snapshot_timestamp=snapshot_timestamp))
        records.append(build_capture_record(event_type="latency_measurement", payload=execution_economics.get("latency") or {}, correlation_id=resolved_correlation_id, job_id=job_id, decision_id=decision_id, settings_hash=settings_hash, snapshot_timestamp=snapshot_timestamp))
    records.extend(
        build_capture_record(event_type="directional_strategy_output", payload=_dump(vote), correlation_id=resolved_correlation_id, job_id=job_id, decision_id=decision_id, settings_hash=settings_hash, snapshot_timestamp=snapshot_timestamp)
        for vote in directional_votes
    )
    records.extend(
        build_capture_record(event_type="shadow_strategy_output", payload=_dump(vote), correlation_id=resolved_correlation_id, job_id=job_id, decision_id=decision_id, settings_hash=settings_hash, snapshot_timestamp=snapshot_timestamp)
        for vote in shadow_directional_votes
    )
    records.extend(
        build_capture_record(event_type="context_output", payload=_dump(vote), correlation_id=resolved_correlation_id, job_id=job_id, decision_id=decision_id, settings_hash=settings_hash, snapshot_timestamp=snapshot_timestamp)
        for vote in (*context_signals, *shadow_context_signals)
    )
    return writer.publish_many(tuple(records))


def capture_operational_event(
    *,
    writer: VotingEnsembleCaptureWriter,
    event_type: str,
    payload: dict[str, Any],
    correlation_id: str,
    job_id: str | None = None,
    decision_id: str | None = None,
    order_id: str | None = None,
    settings_hash: str | None = None,
    snapshot_timestamp: datetime | None = None,
) -> VotingEnsembleCaptureRecord:
    record = build_capture_record(
        event_type=event_type,
        payload=payload,
        correlation_id=correlation_id,
        job_id=job_id,
        decision_id=decision_id,
        order_id=order_id,
        settings_hash=settings_hash,
        snapshot_timestamp=snapshot_timestamp,
        critical=True,
    )
    writer.publish(record)
    return record


def _dump(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_payload"):
        return value.to_payload()
    return {"value": str(value)}


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
