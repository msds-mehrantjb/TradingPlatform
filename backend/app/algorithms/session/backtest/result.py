"""Result contracts for Session runtime parity."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field, field_validator

from backend.app.domain.models import DomainModel, _require_utc


SESSION_RUNTIME_DECISION_SCHEMA_VERSION = "session_runtime_decision_snapshot_v1"
SESSION_RUNTIME_PARITY_RESULT_VERSION = "session_runtime_parity_result_v1"


class SessionRuntimeDecisionSnapshot(DomainModel):
    schemaVersion: str = SESSION_RUNTIME_DECISION_SCHEMA_VERSION
    mode: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    timestamp: datetime
    classificationId: str = Field(min_length=1)
    classification: dict[str, Any]
    transitionState: dict[str, Any]
    transitionReason: str | None
    profile: dict[str, Any]
    routePermissions: dict[str, Any]
    blockNewEntries: bool
    orderGate: dict[str, Any] | None = None
    outputMode: str = Field(min_length=1)
    decisionHash: str = Field(min_length=1)

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    def parity_payload(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json", exclude={"mode", "outputMode", "decisionHash"})
        runtime = ((payload.get("classification") or {}).get("evidence") or {}).get("runtime")
        if isinstance(runtime, dict):
            runtime.pop("runtimeMode", None)
        return payload


class SessionRuntimeParityResult(DomainModel):
    parityVersion: str = SESSION_RUNTIME_PARITY_RESULT_VERSION
    modes: tuple[str, ...]
    identical: bool
    comparedTimestamps: tuple[str, ...]
    mismatchCount: int = Field(ge=0)
    mismatches: tuple[dict[str, Any], ...]
    reasonCodes: tuple[str, ...]


def compare_session_runtime_parity(*mode_results: tuple[SessionRuntimeDecisionSnapshot, ...]) -> SessionRuntimeParityResult:
    if not mode_results:
        return SessionRuntimeParityResult(
            modes=(),
            identical=True,
            comparedTimestamps=(),
            mismatchCount=0,
            mismatches=(),
            reasonCodes=("session.parity.no_modes",),
        )
    mode_names = tuple(result[0].mode if result else f"mode_{index}" for index, result in enumerate(mode_results))
    by_mode = [
        {snapshot.timestamp.isoformat(): snapshot.parity_payload() for snapshot in result}
        for result in mode_results
    ]
    common = sorted(set.intersection(*(set(mapping) for mapping in by_mode))) if by_mode else []
    mismatches: list[dict[str, Any]] = []
    for timestamp in common:
        first = by_mode[0][timestamp]
        for mode, mapping in zip(mode_names[1:], by_mode[1:]):
            if mapping[timestamp] != first:
                mismatches.append({"timestamp": timestamp, "mode": mode, "reason": "session.parity.payload_mismatch"})
    lengths = {mode_names[index]: len(result) for index, result in enumerate(mode_results)}
    if len(set(lengths.values())) > 1:
        mismatches.append({"timestamp": None, "mode": "all", "reason": "session.parity.decision_count_mismatch", "counts": lengths})
    return SessionRuntimeParityResult(
        modes=mode_names,
        identical=not mismatches,
        comparedTimestamps=tuple(common),
        mismatchCount=len(mismatches),
        mismatches=tuple(mismatches),
        reasonCodes=("session.parity.identical_authoritative_runtime",) if not mismatches else ("session.parity.mismatch",),
    )


__all__ = [
    "SESSION_RUNTIME_DECISION_SCHEMA_VERSION",
    "SESSION_RUNTIME_PARITY_RESULT_VERSION",
    "SessionRuntimeDecisionSnapshot",
    "SessionRuntimeParityResult",
    "compare_session_runtime_parity",
]
