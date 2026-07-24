from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from math import ceil
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EVENT_CONTEXT_VALIDATION_VERSION = "economic_event_context_validation_v1"
EventValidationMode = Literal["historical_replay", "walk_forward", "holdout", "shadow", "paper"]


class EventReplayDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    eventId: str = Field(min_length=1)
    eventType: str = Field(min_length=1)
    eventCycleId: str = Field(min_length=1)
    eventTime: datetime
    providerTimestamp: datetime
    receivedAt: datetime
    decisionTime: datetime
    mode: EventValidationMode
    selectedWindowMinutes: int = Field(ge=0)
    grossEdgeBps: float
    expectedCostBps: float = Field(ge=0)
    realizedNetEdgeBps: float
    latencyMs: float = Field(ge=0)
    costEstimateErrorBps: float = Field(ge=0)
    feedHealthy: bool
    orderSubmitted: bool = False
    releaseValuesVisibleBeforeRelease: bool = False
    operationalError: bool = False
    reasonCodes: tuple[str, ...] = ()

    @field_validator("eventTime", "providerTimestamp", "receivedAt", "decisionTime")
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware UTC")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def replay_must_be_point_in_time(self) -> "EventReplayDecision":
        if self.decisionTime < self.providerTimestamp:
            raise ValueError("decision time cannot be before provider timestamp")
        if self.receivedAt < self.providerTimestamp:
            raise ValueError("receipt timestamp cannot be before provider timestamp")
        return self


class EventTypeWindowCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    eventType: str
    selectedWindowMinutes: int = Field(ge=0)
    calibrationRows: int = Field(ge=0)
    averageNetEdgeBps: float
    selectedUsingHoldout: bool = False
    reasonCodes: tuple[str, ...]


class EventPerformanceSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rowCount: int = Field(ge=0)
    grossEdgeBps: float
    expectedCostBps: float
    realizedNetEdgeBps: float
    feedStability: float = Field(ge=0, le=1)
    averageLatencyMs: float
    p95LatencyMs: float
    averageCostEstimateErrorBps: float
    operationalErrorCount: int = Field(ge=0)
    orderSubmissionCount: int = Field(ge=0)
    eventCycleCount: int = Field(ge=0)
    distinctEventTypes: int = Field(ge=0)


class EventValidationThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    minimumWalkForwardNetEdgeBps: float = 0.25
    minimumHoldoutNetEdgeBps: float = 0.25
    maximumP95LatencyMs: float = 750.0
    maximumAverageCostErrorBps: float = 1.5
    minimumFeedStability: float = 0.98
    minimumShadowDecisions: int = 20
    minimumPaperEventCycles: int = 3
    minimumDistinctPaperEventTypes: int = 2
    maximumOperationalErrors: int = 0


class EventValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    validationVersion: str
    generatedAt: datetime
    calibrationByEventType: tuple[EventTypeWindowCalibration, ...]
    walkForward: EventPerformanceSummary
    untouchedHoldout: EventPerformanceSummary
    shadow: EventPerformanceSummary
    paper: EventPerformanceSummary
    pointInTimePassed: bool
    grossVersusNetMeasured: bool
    shadowModeSubmittedOrders: bool
    reasonCodes: tuple[str, ...]

    @field_validator("generatedAt")
    @classmethod
    def generated_at_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("generatedAt must be timezone-aware UTC")
        return value.astimezone(UTC)


class EventPromotionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    promoted: bool
    targetMode: Literal["shadow", "paper_veto_reduce_only"]
    policyVersion: str
    reasonCodes: tuple[str, ...]
    frontendSuppliedEvidenceRejected: bool


def build_event_validation_report(
    decisions: list[EventReplayDecision | dict[str, Any]],
    *,
    calibration_cutoff: datetime,
    holdout_start: datetime,
    generated_at: datetime | None = None,
) -> EventValidationReport:
    rows = [_coerce_decision(item) for item in decisions]
    calibration_rows = [row for row in rows if row.decisionTime < calibration_cutoff and row.mode in {"historical_replay", "walk_forward"}]
    walk_forward_rows = [row for row in rows if calibration_cutoff <= row.decisionTime < holdout_start and row.mode in {"historical_replay", "walk_forward"}]
    holdout_rows = [row for row in rows if row.decisionTime >= holdout_start and row.mode == "holdout"]
    shadow_rows = [row for row in rows if row.mode == "shadow"]
    paper_rows = [row for row in rows if row.mode == "paper"]
    point_in_time_passed = all(not row.releaseValuesVisibleBeforeRelease and row.decisionTime >= row.providerTimestamp for row in rows)
    shadow_submitted = any(row.orderSubmitted for row in shadow_rows)
    reasons = ["event.validation.backend_authoritative"]
    if not point_in_time_passed:
        reasons.append("event.validation.point_in_time_failed")
    if shadow_submitted:
        reasons.append("event.validation.shadow_order_submission_detected")
    return EventValidationReport(
        validationVersion=EVENT_CONTEXT_VALIDATION_VERSION,
        generatedAt=generated_at or datetime.now(tz=UTC),
        calibrationByEventType=tuple(calibrate_event_windows_by_type(calibration_rows)),
        walkForward=_summary(walk_forward_rows),
        untouchedHoldout=_summary(holdout_rows),
        shadow=_summary(shadow_rows),
        paper=_summary(paper_rows),
        pointInTimePassed=point_in_time_passed,
        grossVersusNetMeasured=all(row.grossEdgeBps != row.realizedNetEdgeBps or row.expectedCostBps > 0 for row in rows),
        shadowModeSubmittedOrders=shadow_submitted,
        reasonCodes=tuple(reasons),
    )


def calibrate_event_windows_by_type(rows: list[EventReplayDecision]) -> list[EventTypeWindowCalibration]:
    grouped: dict[str, list[EventReplayDecision]] = defaultdict(list)
    for row in rows:
        grouped[row.eventType].append(row)
    results: list[EventTypeWindowCalibration] = []
    for event_type, event_rows in sorted(grouped.items()):
        by_window: dict[int, list[EventReplayDecision]] = defaultdict(list)
        for row in event_rows:
            by_window[row.selectedWindowMinutes].append(row)
        selected_window, selected_rows = max(
            by_window.items(),
            key=lambda item: (_mean([row.realizedNetEdgeBps for row in item[1]]), len(item[1]), -item[0]),
        )
        results.append(
            EventTypeWindowCalibration(
                eventType=event_type,
                selectedWindowMinutes=selected_window,
                calibrationRows=len(selected_rows),
                averageNetEdgeBps=round(_mean([row.realizedNetEdgeBps for row in selected_rows]), 6),
                selectedUsingHoldout=False,
                reasonCodes=("event.validation.calibrated_by_event_type", "event.validation.holdout_excluded_from_calibration"),
            )
        )
    return results


def evaluate_event_promotion_policy(
    report: EventValidationReport | dict[str, Any] | None,
    *,
    thresholds: EventValidationThresholds | None = None,
    frontend_supplied_evidence: dict[str, Any] | None = None,
) -> EventPromotionDecision:
    limits = thresholds or EventValidationThresholds()
    reasons: list[str] = []
    frontend_rejected = frontend_supplied_evidence is not None
    if frontend_rejected:
        reasons.append("event.promotion.frontend_supplied_evidence_rejected")
    if report is None:
        reasons.append("event.promotion.backend_validation_report_required")
        return _promotion(False, reasons, frontend_rejected)
    current_report = report if isinstance(report, EventValidationReport) else EventValidationReport.model_validate(report)
    if not current_report.pointInTimePassed:
        reasons.append("event.promotion.point_in_time_replay_required")
    if not current_report.grossVersusNetMeasured:
        reasons.append("event.promotion.gross_vs_net_edge_required")
    if current_report.shadowModeSubmittedOrders:
        reasons.append("event.promotion.shadow_must_not_submit_orders")
    if any(item.selectedUsingHoldout for item in current_report.calibrationByEventType):
        reasons.append("event.promotion.holdout_must_not_calibrate_windows")
    if current_report.walkForward.realizedNetEdgeBps < limits.minimumWalkForwardNetEdgeBps:
        reasons.append("event.promotion.walk_forward_net_edge_too_low")
    if current_report.untouchedHoldout.realizedNetEdgeBps < limits.minimumHoldoutNetEdgeBps:
        reasons.append("event.promotion.holdout_net_edge_too_low")
    if current_report.shadow.rowCount < limits.minimumShadowDecisions:
        reasons.append("event.promotion.shadow_decisions_required")
    if current_report.paper.eventCycleCount < limits.minimumPaperEventCycles:
        reasons.append("event.promotion.paper_event_cycles_required")
    if current_report.paper.distinctEventTypes < limits.minimumDistinctPaperEventTypes:
        reasons.append("event.promotion.paper_distinct_event_types_required")
    for label, summary in (("walk_forward", current_report.walkForward), ("holdout", current_report.untouchedHoldout), ("shadow", current_report.shadow), ("paper", current_report.paper)):
        if summary.p95LatencyMs > limits.maximumP95LatencyMs:
            reasons.append(f"event.promotion.{label}_latency_too_high")
        if summary.averageCostEstimateErrorBps > limits.maximumAverageCostErrorBps:
            reasons.append(f"event.promotion.{label}_cost_error_too_high")
        if summary.feedStability < limits.minimumFeedStability:
            reasons.append(f"event.promotion.{label}_feed_stability_too_low")
        if summary.operationalErrorCount > limits.maximumOperationalErrors:
            reasons.append(f"event.promotion.{label}_operational_errors_present")
    return _promotion(not reasons, reasons or ["event.promotion.paper_veto_reduce_only_allowed"], frontend_rejected)


def _summary(rows: list[EventReplayDecision]) -> EventPerformanceSummary:
    latencies = sorted(row.latencyMs for row in rows)
    p95_index = max(0, min(len(latencies) - 1, ceil(len(latencies) * 0.95) - 1)) if latencies else 0
    return EventPerformanceSummary(
        rowCount=len(rows),
        grossEdgeBps=round(_mean([row.grossEdgeBps for row in rows]), 6),
        expectedCostBps=round(_mean([row.expectedCostBps for row in rows]), 6),
        realizedNetEdgeBps=round(_mean([row.realizedNetEdgeBps for row in rows]), 6),
        feedStability=round((sum(1 for row in rows if row.feedHealthy) / len(rows)) if rows else 0.0, 6),
        averageLatencyMs=round(_mean([row.latencyMs for row in rows]), 6),
        p95LatencyMs=round(latencies[p95_index] if latencies else 0.0, 6),
        averageCostEstimateErrorBps=round(_mean([row.costEstimateErrorBps for row in rows]), 6),
        operationalErrorCount=sum(1 for row in rows if row.operationalError),
        orderSubmissionCount=sum(1 for row in rows if row.orderSubmitted),
        eventCycleCount=len({row.eventCycleId for row in rows}),
        distinctEventTypes=len({row.eventType for row in rows}),
    )


def _coerce_decision(item: EventReplayDecision | dict[str, Any]) -> EventReplayDecision:
    return item if isinstance(item, EventReplayDecision) else EventReplayDecision.model_validate(item)


def _mean(values: list[float]) -> float:
    return mean(values) if values else 0.0


def _promotion(promoted: bool, reasons: list[str], frontend_rejected: bool) -> EventPromotionDecision:
    return EventPromotionDecision(
        promoted=promoted,
        targetMode="paper_veto_reduce_only" if promoted else "shadow",
        policyVersion=EVENT_CONTEXT_VALIDATION_VERSION,
        reasonCodes=tuple(dict.fromkeys(reasons)),
        frontendSuppliedEvidenceRejected=frontend_rejected,
    )


__all__ = [
    "EVENT_CONTEXT_VALIDATION_VERSION",
    "EventPromotionDecision",
    "EventReplayDecision",
    "EventPerformanceSummary",
    "EventTypeWindowCalibration",
    "EventValidationReport",
    "EventValidationThresholds",
    "build_event_validation_report",
    "calibrate_event_windows_by_type",
    "evaluate_event_promotion_policy",
]
