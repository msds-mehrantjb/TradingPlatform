"""Operational diagnostics for persisted Session decisions."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from pydantic import Field

from backend.app.algorithms.session.persistence import SessionDecisionPersistenceRecord
from backend.app.domain.models import DomainModel


SESSION_DIAGNOSTICS_VERSION = "session_diagnostics_v1"


class SessionOperationalMetrics(DomainModel):
    diagnosticsVersion: str = SESSION_DIAGNOSTICS_VERSION
    recordCount: int = Field(ge=0)
    averageClassificationProcessingLatencyMs: float | None = Field(default=None, ge=0)
    averageEventLagMs: float | None = Field(default=None, ge=0)
    missingBarRate: float = Field(ge=0, le=1)
    staleQuoteRate: float = Field(ge=0, le=1)
    unknownStateOccupancy: float = Field(ge=0, le=1)
    behaviorOccupancy: dict[str, float]
    transitionCount: int = Field(ge=0)
    transitionReversalRate: float = Field(ge=0, le=1)
    averageDwellTimeSeconds: float | None = Field(default=None, ge=0)
    blockedEntryCountByReason: dict[str, int]
    estimatedVersusRealizedCosts: dict[str, Any]
    paperReplayDivergence: dict[str, Any]
    reasonCodes: tuple[str, ...]


def build_session_operational_metrics(records: tuple[SessionDecisionPersistenceRecord, ...] | list[SessionDecisionPersistenceRecord]) -> SessionOperationalMetrics:
    ordered = tuple(sorted(records, key=lambda item: (item.symbol, item.sessionDate or "", item.decisionTime, item.recordId)))
    count = len(ordered)
    if count == 0:
        return SessionOperationalMetrics(
            recordCount=0,
            averageClassificationProcessingLatencyMs=None,
            averageEventLagMs=None,
            missingBarRate=0.0,
            staleQuoteRate=0.0,
            unknownStateOccupancy=0.0,
            behaviorOccupancy={},
            transitionCount=0,
            transitionReversalRate=0.0,
            averageDwellTimeSeconds=None,
            blockedEntryCountByReason={},
            estimatedVersusRealizedCosts={"sampleCount": 0, "averageError": None, "averageAbsoluteError": None},
            paperReplayDivergence={"sampleCount": 0, "divergenceCount": 0, "divergenceRate": 0.0},
            reasonCodes=("session.diagnostics.no_records",),
        )

    behavior_counts = Counter(record.behavior for record in ordered)
    unknown_count = sum(
        1
        for record in ordered
        if "unknown" in {record.phase, record.behavior, record.volatilityState, record.liquidityState, record.dataQualityState, record.eventRiskState}
    )
    missing_bar_count = sum(1 for record in ordered if _missing_bar_evidence(record))
    stale_quote_count = sum(1 for record in ordered if record.liquidityState == "stale" or any("QUOTE_STALE" in code.upper() or "quote_stale" in code for code in record.reasonCodes))
    blocked_counts: Counter[str] = Counter()
    for record in ordered:
        if bool(record.safetyBlocks.get("blockNewEntries")):
            for reason in record.reasonCodes or ("session.diagnostics.blocked_without_reason",):
                blocked_counts[str(reason)] += 1

    transitions, reversals, dwell_seconds = _transition_metrics(ordered)
    costs = _cost_metrics(ordered)
    divergence = _paper_replay_divergence(ordered)

    return SessionOperationalMetrics(
        recordCount=count,
        averageClassificationProcessingLatencyMs=_average(record.classificationProcessingLatencyMs for record in ordered),
        averageEventLagMs=_average(record.eventLagMs for record in ordered),
        missingBarRate=round(missing_bar_count / count, 6),
        staleQuoteRate=round(stale_quote_count / count, 6),
        unknownStateOccupancy=round(unknown_count / count, 6),
        behaviorOccupancy={behavior: round(total / count, 6) for behavior, total in sorted(behavior_counts.items())},
        transitionCount=transitions,
        transitionReversalRate=0.0 if transitions == 0 else round(reversals / transitions, 6),
        averageDwellTimeSeconds=_average(dwell_seconds),
        blockedEntryCountByReason=dict(sorted(blocked_counts.items())),
        estimatedVersusRealizedCosts=costs,
        paperReplayDivergence=divergence,
        reasonCodes=("session.diagnostics.operational_metrics",),
    )


def _missing_bar_evidence(record: SessionDecisionPersistenceRecord) -> bool:
    evidence = record.transitionState.get("evidence") if isinstance(record.transitionState, dict) else None
    candidates = [
        record.safetyBlocks.get("missingBarCount"),
        record.strategyPermissions.get("missingBarCount"),
        evidence.get("missingBarCount") if isinstance(evidence, dict) else None,
    ]
    if any(isinstance(value, (int, float)) and value > 0 for value in candidates):
        return True
    return any("MISSING_BAR" in code.upper() or "GAP" in code.upper() for code in record.reasonCodes)


def _transition_metrics(records: tuple[SessionDecisionPersistenceRecord, ...]) -> tuple[int, int, list[float]]:
    transitions = 0
    reversals = 0
    dwell_seconds: list[float] = []
    by_stream: dict[tuple[str, str | None], list[SessionDecisionPersistenceRecord]] = defaultdict(list)
    for record in records:
        by_stream[(record.symbol, record.sessionDate)].append(record)
    for stream in by_stream.values():
        previous = None
        previous_transition_time: datetime | None = None
        previous_direction: str | None = None
        for record in stream:
            if previous is not None and record.behavior != previous.behavior:
                transitions += 1
                if previous_transition_time is not None:
                    dwell_seconds.append(max(0.0, (record.decisionTime - previous_transition_time).total_seconds()))
                current_direction = _direction_family(record.behavior)
                if previous_direction and current_direction and previous_direction != current_direction:
                    reversals += 1
                previous_direction = current_direction or previous_direction
                previous_transition_time = record.decisionTime
            elif previous is None:
                previous_direction = _direction_family(record.behavior)
                previous_transition_time = record.decisionTime
            previous = record
    return transitions, reversals, dwell_seconds


def _cost_metrics(records: tuple[SessionDecisionPersistenceRecord, ...]) -> dict[str, Any]:
    errors: list[float] = []
    for record in records:
        actual = record.actualLaterOutcome or {}
        realized = _float_or_none(actual.get("realizedCost") or actual.get("realized_cost"))
        estimated = _float_or_none(actual.get("estimatedCost") or actual.get("estimated_cost"))
        if estimated is None and record.expectedCostsAndEdge:
            estimated = sum(
                float(record.expectedCostsAndEdge.get(key) or 0.0)
                for key in ("spreadEstimate", "slippageEstimate", "fees", "marketImpactEstimate", "adverseSelectionBuffer")
            )
        if estimated is None or realized is None:
            continue
        errors.append(realized - estimated)
    return {
        "sampleCount": len(errors),
        "averageError": _average(errors),
        "averageAbsoluteError": _average(abs(value) for value in errors),
    }


def _paper_replay_divergence(records: tuple[SessionDecisionPersistenceRecord, ...]) -> dict[str, Any]:
    samples = 0
    divergences = 0
    for record in records:
        actual = record.actualLaterOutcome or {}
        paper = actual.get("paperDecisionHash") or actual.get("paper_decision_hash")
        replay = actual.get("replayDecisionHash") or actual.get("replay_decision_hash")
        if paper is None or replay is None:
            continue
        samples += 1
        divergences += int(str(paper) != str(replay))
    return {
        "sampleCount": samples,
        "divergenceCount": divergences,
        "divergenceRate": 0.0 if samples == 0 else round(divergences / samples, 6),
    }


def _direction_family(behavior: str) -> str | None:
    if behavior.endswith("_up") or behavior in {"breakout_up", "trend_up", "reversal_up"}:
        return "up"
    if behavior.endswith("_down") or behavior in {"breakout_down", "trend_down", "reversal_down"}:
        return "down"
    return None


def _average(values) -> float | None:
    clean = [float(value) for value in values if value is not None]
    if not clean:
        return None
    return round(sum(clean) / len(clean), 6)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


__all__ = ["SESSION_DIAGNOSTICS_VERSION", "SessionOperationalMetrics", "build_session_operational_metrics"]
