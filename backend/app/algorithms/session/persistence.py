"""Buffered, algorithm-owned persistence for Session classification decisions."""

from __future__ import annotations

from collections import deque
from datetime import UTC, date, datetime, time
from enum import Enum
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Literal, Protocol

from pydantic import Field, field_validator

from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig
from backend.app.algorithms.session.execution import SessionCandidateDecision, SessionOrderGateDecision
from backend.app.algorithms.session.models import SessionClassification
from backend.app.algorithms.session.profile import SessionProfile
from backend.app.algorithms.session.router import resolve_session_profile, session_route_permissions
from backend.app.algorithms.session.transition import SessionTransitionState
from backend.app.domain.models import DomainModel, _require_utc


SESSION_PERSISTENCE_VERSION = "session_persistence_v1"
SESSION_DECISION_RECORD_SCHEMA_VERSION = "session_decision_record_v1"
SESSION_PERSISTENCE_ROOT = Path("data") / "algorithms" / "session" / "decisions"
SessionOutputMode = Literal["shadow", "paper_affecting", "display_only"]
SessionPersistenceStatus = Literal["queued", "duplicate", "overflow_rejected", "persisted", "failed"]


class SessionDecisionPersistenceRecord(DomainModel):
    persistenceVersion: str = SESSION_PERSISTENCE_VERSION
    schemaVersion: str = SESSION_DECISION_RECORD_SCHEMA_VERSION
    recordId: str = Field(min_length=1)
    classificationId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    sessionDate: str | None
    marketEventTime: datetime | None
    featureSnapshotTime: datetime | None
    decisionTime: datetime
    validUntil: datetime
    phase: str
    behavior: str
    volatilityState: str
    liquidityState: str
    dataQualityState: str
    eventRiskState: str
    directionBias: str
    phaseConfidence: float = Field(ge=0, le=1)
    behaviorConfidence: float = Field(ge=0, le=1)
    volatilityConfidence: float = Field(ge=0, le=1)
    liquidityConfidence: float = Field(ge=0, le=1)
    dataQualityConfidence: float = Field(ge=0, le=1)
    overallConfidence: float = Field(ge=0, le=1)
    safetyBlockConfidence: float = Field(ge=0, le=1)
    reasonCodes: tuple[str, ...]
    featureSnapshotId: str = Field(min_length=1)
    featureSchemaVersion: str = Field(min_length=1)
    classifierVersion: str = Field(min_length=1)
    configVersion: str = Field(min_length=1)
    profileVersion: str = Field(min_length=1)
    profileId: str = Field(min_length=1)
    baselineArtifactId: str | None = None
    baselineVersion: str | None = None
    transitionState: dict[str, Any] = Field(default_factory=dict)
    strategyPermissions: dict[str, Any] = Field(default_factory=dict)
    safetyBlocks: dict[str, Any] = Field(default_factory=dict)
    expectedCostsAndEdge: dict[str, Any] | None = None
    outputMode: SessionOutputMode
    actualLaterOutcome: dict[str, Any] | None = None
    classificationProcessingLatencyMs: float | None = Field(default=None, ge=0)
    eventLagMs: float | None = Field(default=None, ge=0)
    persistedAt: datetime
    configurationHash: str = Field(min_length=1)

    @field_validator("marketEventTime", "featureSnapshotTime", "decisionTime", "validUntil", "persistedAt")
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None

    def deterministic_hash(self) -> str:
        return _hash_json(self.model_dump(mode="json", exclude={"persistedAt"}))


class SessionPersistenceEnqueueResult(DomainModel):
    status: SessionPersistenceStatus
    recordId: str
    queuedDepth: int = Field(ge=0)
    overflowCount: int = Field(ge=0)
    reasonCodes: tuple[str, ...]


class SessionPersistenceFlushResult(DomainModel):
    attempted: int = Field(ge=0)
    persisted: int = Field(ge=0)
    duplicates: int = Field(ge=0)
    failed: int = Field(ge=0)
    remainingQueued: int = Field(ge=0)
    overflowCount: int = Field(ge=0)
    failureReasons: tuple[str, ...] = ()


class SessionDecisionStore(Protocol):
    def write_record(self, record: SessionDecisionPersistenceRecord) -> SessionPersistenceStatus:
        ...

    def read_records(self, *, symbol: str | None = None, session_date: str | None = None) -> tuple[SessionDecisionPersistenceRecord, ...]:
        ...


class SessionDecisionJsonlStore:
    """JSONL-backed Session audit store with idempotent record IDs."""

    def __init__(self, *, root: Path | str = SESSION_PERSISTENCE_ROOT) -> None:
        self.root = Path(root)

    def write_record(self, record: SessionDecisionPersistenceRecord) -> SessionPersistenceStatus:
        path = self.record_path(symbol=record.symbol, session_date=record.sessionDate)
        path.parent.mkdir(parents=True, exist_ok=True)
        if record.recordId in self._record_ids(path):
            return "duplicate"
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(record.model_dump(mode="json"), sort_keys=True, separators=(",", ":")))
            handle.write("\n")
        return "persisted"

    def read_records(self, *, symbol: str | None = None, session_date: str | None = None) -> tuple[SessionDecisionPersistenceRecord, ...]:
        paths = self._candidate_paths(symbol=symbol, session_date=session_date)
        records: list[SessionDecisionPersistenceRecord] = []
        seen: set[str] = set()
        for path in paths:
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                record = SessionDecisionPersistenceRecord.model_validate(json.loads(line))
                if record.recordId in seen:
                    continue
                seen.add(record.recordId)
                records.append(record)
        return tuple(sorted(records, key=lambda item: (item.decisionTime, item.recordId)))

    def recover_records(self, *, symbol: str | None = None, session_date: str | None = None) -> tuple[SessionDecisionPersistenceRecord, ...]:
        return self.read_records(symbol=symbol, session_date=session_date)

    def record_path(self, *, symbol: str, session_date: str | None) -> Path:
        safe_symbol = _safe_path_component(symbol.upper())
        safe_session = _safe_path_component(session_date or "unknown_session")
        root = self.root.resolve()
        path = (root / safe_session / f"{safe_symbol}_session_decisions.jsonl").resolve()
        if root != path and root not in path.parents:
            raise ValueError("Session persistence path escaped storage root")
        return path

    def _candidate_paths(self, *, symbol: str | None, session_date: str | None) -> tuple[Path, ...]:
        if symbol and session_date:
            return (self.record_path(symbol=symbol, session_date=session_date),)
        if not self.root.exists():
            return ()
        pattern = f"{_safe_path_component(symbol.upper())}_session_decisions.jsonl" if symbol else "*_session_decisions.jsonl"
        roots = [self.root / _safe_path_component(session_date)] if session_date else sorted(path for path in self.root.iterdir() if path.is_dir())
        return tuple(path for root in roots for path in sorted(root.glob(pattern)))

    @staticmethod
    def _record_ids(path: Path) -> set[str]:
        if not path.exists():
            return set()
        ids: set[str] = set()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            record_id = payload.get("recordId")
            if record_id:
                ids.add(str(record_id))
        return ids


class BufferedSessionDecisionWriter:
    """Bounded buffer that keeps telemetry off the one-minute classification path."""

    def __init__(self, store: SessionDecisionStore, *, max_queue_size: int = 1_000) -> None:
        if max_queue_size <= 0:
            raise ValueError("max_queue_size must be positive")
        self.store = store
        self.max_queue_size = max_queue_size
        self._queue: deque[SessionDecisionPersistenceRecord] = deque()
        self._queued_ids: set[str] = set()
        self.overflow_count = 0
        self.failure_reasons: tuple[str, ...] = ()

    @property
    def queued_depth(self) -> int:
        return len(self._queue)

    def enqueue(self, record: SessionDecisionPersistenceRecord) -> SessionPersistenceEnqueueResult:
        if record.recordId in self._queued_ids:
            return SessionPersistenceEnqueueResult(
                status="duplicate",
                recordId=record.recordId,
                queuedDepth=len(self._queue),
                overflowCount=self.overflow_count,
                reasonCodes=("session.persistence.duplicate_in_buffer",),
            )
        if len(self._queue) >= self.max_queue_size:
            self.overflow_count += 1
            return SessionPersistenceEnqueueResult(
                status="overflow_rejected",
                recordId=record.recordId,
                queuedDepth=len(self._queue),
                overflowCount=self.overflow_count,
                reasonCodes=("session.persistence.queue_overflow_retry_required",),
            )
        self._queue.append(record)
        self._queued_ids.add(record.recordId)
        return SessionPersistenceEnqueueResult(
            status="queued",
            recordId=record.recordId,
            queuedDepth=len(self._queue),
            overflowCount=self.overflow_count,
            reasonCodes=("session.persistence.buffered_not_blocking_decision_path",),
        )

    def flush(self, *, limit: int | None = None) -> SessionPersistenceFlushResult:
        attempted = persisted = duplicates = failed = 0
        failures: list[str] = []
        max_items = len(self._queue) if limit is None else max(0, min(limit, len(self._queue)))
        for _ in range(max_items):
            record = self._queue[0]
            attempted += 1
            try:
                status = self.store.write_record(record)
            except Exception as exc:  # noqa: BLE001 - persistence failures must be isolated from trading.
                failed += 1
                failure = f"session.persistence.write_failed:{type(exc).__name__}"
                failures.append(failure)
                break
            self._queue.popleft()
            self._queued_ids.discard(record.recordId)
            if status == "duplicate":
                duplicates += 1
            else:
                persisted += 1
        self.failure_reasons = tuple(dict.fromkeys((*self.failure_reasons, *failures)))
        return SessionPersistenceFlushResult(
            attempted=attempted,
            persisted=persisted,
            duplicates=duplicates,
            failed=failed,
            remainingQueued=len(self._queue),
            overflowCount=self.overflow_count,
            failureReasons=tuple(dict.fromkeys(failures)),
        )


def build_session_decision_record(
    *,
    classification: SessionClassification,
    output_mode: SessionOutputMode,
    config: SessionConfig = DEFAULT_SESSION_CONFIG,
    profile: SessionProfile | None = None,
    transition_state: SessionTransitionState | dict[str, Any] | None = None,
    strategy_permissions: dict[str, Any] | None = None,
    candidate: SessionCandidateDecision | None = None,
    order_gate_decision: SessionOrderGateDecision | None = None,
    actual_later_outcome: dict[str, Any] | None = None,
    persisted_at: datetime | None = None,
) -> SessionDecisionPersistenceRecord:
    profile = profile or resolve_session_profile(classification, config=config)
    classification_id = _classification_id(classification)
    feature_snapshot_id = _feature_snapshot_id(classification)
    transition_payload = _transition_payload(transition_state)
    permissions = strategy_permissions or session_route_permissions(classification, config=config)
    safety_blocks = _safety_blocks(classification, profile)
    costs = _costs_and_edge(candidate, order_gate_decision)
    persisted = _require_utc(persisted_at or classification.decision_time)
    payload = {
        "classificationId": classification_id,
        "symbol": classification.symbol,
        "sessionDate": classification.session_date,
        "marketEventTime": classification.market_event_time,
        "featureSnapshotTime": classification.feature_snapshot_time,
        "decisionTime": classification.decision_time,
        "validUntil": classification.valid_until,
        "phase": classification.phase.value,
        "behavior": classification.behavior.value,
        "volatilityState": classification.volatility_state.value,
        "liquidityState": classification.liquidity_state.value,
        "dataQualityState": classification.data_quality_state.value,
        "eventRiskState": classification.event_risk_state.value,
        "directionBias": classification.direction_bias,
        "phaseConfidence": classification.phase_confidence,
        "behaviorConfidence": classification.behavior_confidence,
        "volatilityConfidence": classification.volatility_confidence,
        "liquidityConfidence": classification.liquidity_confidence,
        "dataQualityConfidence": classification.data_quality_confidence,
        "overallConfidence": classification.overall_confidence,
        "safetyBlockConfidence": classification.safety_block_confidence,
        "reasonCodes": tuple(classification.reason_codes),
        "featureSnapshotId": feature_snapshot_id,
        "featureSchemaVersion": classification.feature_schema_version,
        "classifierVersion": classification.classifier_version,
        "configVersion": config.config_version,
        "profileVersion": profile.profile_version,
        "profileId": profile.profile_id,
        "baselineArtifactId": _evidence_value(classification.evidence, "baselineArtifactId", "baseline_artifact_id"),
        "baselineVersion": _evidence_value(classification.evidence, "baselineVersion", "baseline_version") or config.baseline_version,
        "transitionState": transition_payload,
        "strategyPermissions": _jsonable(permissions),
        "safetyBlocks": safety_blocks,
        "expectedCostsAndEdge": costs,
        "outputMode": output_mode,
        "actualLaterOutcome": _jsonable(actual_later_outcome),
        "classificationProcessingLatencyMs": _latency_ms(classification.feature_snapshot_time, classification.decision_time),
        "eventLagMs": _latency_ms(classification.market_event_time, classification.decision_time),
        "persistedAt": persisted,
    }
    record_id = "session-decision-" + _hash_json(
        {
            "classificationId": classification_id,
            "featureSnapshotId": feature_snapshot_id,
            "outputMode": output_mode,
            "candidateHash": candidate.deterministic_hash() if candidate else None,
        }
    )
    return SessionDecisionPersistenceRecord(
        **payload,
        recordId=record_id,
        configurationHash=_hash_json({**_jsonable(payload), "recordId": record_id, "persistenceVersion": SESSION_PERSISTENCE_VERSION}),
    )


def _classification_id(classification: SessionClassification) -> str:
    candidate = _evidence_value(classification.evidence, "classificationId", "classification_id")
    return str(candidate) if candidate else f"session-classification-{classification.deterministic_hash()[:16]}"


def _feature_snapshot_id(classification: SessionClassification) -> str:
    candidate = _evidence_value(classification.evidence, "featureSnapshotId", "feature_snapshot_id")
    if candidate:
        return str(candidate)
    return "session-feature-" + _hash_json(
        {
            "symbol": classification.symbol,
            "marketEventTime": classification.market_event_time,
            "featureSnapshotTime": classification.feature_snapshot_time,
            "featureSchemaVersion": classification.feature_schema_version,
            "evidence": classification.evidence,
        }
    )


def _transition_payload(value: SessionTransitionState | dict[str, Any] | None) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, SessionTransitionState):
        return _jsonable(value.as_dict())
    return _jsonable(value)


def _safety_blocks(classification: SessionClassification, profile: SessionProfile) -> dict[str, Any]:
    return {
        "blockNewEntries": classification.block_new_entries or profile.block_new_entries,
        "classificationBlockNewEntries": classification.block_new_entries,
        "profileBlockNewEntries": profile.block_new_entries,
        "blockedStrategyFamilies": tuple(dict.fromkeys((*classification.blocked_strategy_families, *profile.blocked_strategy_families))),
        "safetyReasonCodes": tuple(code for code in classification.reason_codes if "BLOCK" in code.upper() or "STALE" in code.upper() or "LIQUIDITY" in code.upper()),
    }


def _costs_and_edge(candidate: SessionCandidateDecision | None, gate: SessionOrderGateDecision | None) -> dict[str, Any] | None:
    if candidate is None and gate is None:
        return None
    source = candidate or gate.candidate
    return {
        "originatingStrategyCandidateId": source.originatingStrategyCandidateId,
        "expectedGrossEdge": source.expectedGrossEdge,
        "spreadEstimate": source.spreadEstimate,
        "slippageEstimate": source.slippageEstimate,
        "fees": source.fees,
        "marketImpactEstimate": source.marketImpactEstimate,
        "adverseSelectionBuffer": source.adverseSelectionBuffer,
        "expectedNetEdge": source.expectedNetEdge,
        "fillProbability": source.fillProbability,
        "quantityCap": source.quantityCap,
        "gateStatus": gate.status if gate else None,
        "approvedQuantity": gate.approvedQuantity if gate else None,
        "gateReasonCodes": gate.reasonCodes if gate else (),
    }


def _latency_ms(start: datetime | None, end: datetime | None) -> float | None:
    if start is None or end is None:
        return None
    return max(0.0, round((end - start).total_seconds() * 1000.0, 3))


def _evidence_value(evidence: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in evidence and evidence[key] is not None:
            return evidence[key]
    baseline = evidence.get("baseline")
    if isinstance(baseline, dict):
        for key in keys:
            if key in baseline and baseline[key] is not None:
                return baseline[key]
    return None


def _safe_path_component(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("invalid Session persistence path component")
    return cleaned


def _hash_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


__all__ = [
    "SESSION_DECISION_RECORD_SCHEMA_VERSION",
    "SESSION_PERSISTENCE_ROOT",
    "SESSION_PERSISTENCE_VERSION",
    "BufferedSessionDecisionWriter",
    "SessionDecisionJsonlStore",
    "SessionDecisionPersistenceRecord",
    "SessionPersistenceEnqueueResult",
    "SessionPersistenceFlushResult",
    "build_session_decision_record",
]
