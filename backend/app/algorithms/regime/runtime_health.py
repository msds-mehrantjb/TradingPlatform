"""Regime runtime health and status models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


REGIME_RUNTIME_HEALTH_VERSION = "regime_runtime_health_v1"
REGIME_HEALTH_COMPONENTS = (
    "market_event_ingestion",
    "settings_repository",
    "runtime_state",
    "decision_worker",
    "local_risk",
    "global_risk_connection",
    "execution_outbox",
    "paper_broker",
    "order_reconciliation",
    "position_reconciliation",
    "backtest_worker",
    "database",
)


@dataclass
class RegimeRuntimeMetrics:
    supervisor_started: bool = False
    paused: bool = False
    emergency_flatten_requested: bool = False
    entry_creation_paused_for_reconciliation: bool = True
    inventory_reconciled: bool = False
    recovery_succeeded: bool = False
    risk_reducing_exits_allowed: bool = True
    persistence_available: bool = True
    settings_available: bool = True
    checkpoint_consistent: bool = True
    quarantined: bool = False
    queue_lag_block_active: bool = False
    queue_depth: int = 0
    command_queue_depth: int = 0
    queue_lag_seconds: float | None = None
    latest_event_age_seconds: float | None = None
    decision_latency_ms: float | None = None
    classifier_latency_ms: float | None = None
    strategy_latency_ms: float | None = None
    risk_service_latency_ms: float | None = None
    broker_latency_ms: float | None = None
    processed_events: int = 0
    duplicate_events: int = 0
    stale_events: int = 0
    out_of_order_events: int = 0
    rejected_events: int = 0
    persisted_events: int = 0
    persisted_decisions: int = 0
    decision_counts: dict[str, int] = field(default_factory=lambda: {"total": 0})
    signal_counts: dict[str, int] = field(default_factory=lambda: {"Buy": 0, "Sell": 0, "Hold": 0})
    blockers_by_reason: dict[str, int] = field(default_factory=dict)
    regime_occupancy: dict[str, int] = field(default_factory=dict)
    strategy_opportunities: dict[str, int] = field(default_factory=dict)
    strategy_signals: dict[str, dict[str, int]] = field(default_factory=dict)
    family_contributions: dict[str, float] = field(default_factory=dict)
    proposed_quantity_total: int = 0
    approved_quantity_total: int = 0
    proposed_vs_approved_quantity: dict[str, int] = field(default_factory=lambda: {"proposed": 0, "approved": 0, "reduced": 0, "rejected": 0})
    enqueued_orders: int = 0
    submitted_orders: int = 0
    acknowledged_orders: int = 0
    filled_orders: int = 0
    rejected_orders: int = 0
    order_status_counts: dict[str, int] = field(default_factory=dict)
    fill_quality: dict[str, float] = field(default_factory=lambda: {"fillCount": 0, "partialFillCount": 0, "averageSlippageBps": 0.0})
    slippage: dict[str, float] = field(default_factory=lambda: {"totalBps": 0.0, "averageBps": 0.0, "sampleCount": 0})
    reconciliation_discrepancies: int = 0
    open_positions: int = 0
    protected_positions_managed_during_entry_pause: int = 0
    recovered_outbox_records: int = 0
    abandoned_leases_detected: int = 0
    last_event_id: str | None = None
    last_decision_id: str | None = None
    last_checkpoint: dict[str, Any] | None = None
    latest_decision: dict[str, Any] | None = None
    latest_event: dict[str, Any] | None = None
    latest_command: dict[str, Any] | None = None
    latest_recovery: dict[str, Any] = field(default_factory=dict)
    latest_reconciliation: dict[str, Any] | None = None
    alert_conditions: list[dict[str, Any]] = field(default_factory=list)
    disabled_strategy_ids: list[str] = field(default_factory=list)
    active_settings_version: str | None = None
    processing_lag_seconds: float | None = None
    last_processed_bar_by_instance_symbol: dict[str, str] = field(default_factory=dict)
    worker_status: dict[str, str] = field(default_factory=dict)
    pause_reason: str | None = None
    last_error: str | None = None
    entry_block_reason_codes: list[str] = field(default_factory=list)
    component_health: dict[str, dict[str, Any]] = field(default_factory=lambda: _initial_component_health())

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def health_from_metrics(metrics: RegimeRuntimeMetrics) -> dict[str, Any]:
    metrics.alert_conditions = alert_conditions_from_metrics(metrics)
    unhealthy_components = {
        name: health
        for name, health in metrics.component_health.items()
        if str(health.get("status")) == "unhealthy"
    }
    healthy = metrics.supervisor_started and not unhealthy_components and not metrics.quarantined and (metrics.recovery_succeeded or metrics.entry_creation_paused_for_reconciliation)
    return {
        "algorithmId": "regime",
        "healthVersion": REGIME_RUNTIME_HEALTH_VERSION,
        "healthy": healthy,
        "failClosed": metrics.entry_creation_paused_for_reconciliation and not metrics.recovery_succeeded,
        "newEntriesBlocked": bool(metrics.entry_block_reason_codes or metrics.entry_creation_paused_for_reconciliation or metrics.paused),
        "entryBlockReasonCodes": list(metrics.entry_block_reason_codes),
        "quarantined": metrics.quarantined,
        "persistenceAvailable": metrics.persistence_available,
        "settingsAvailable": metrics.settings_available,
        "checkpointConsistent": metrics.checkpoint_consistent,
        "queueLagBlockActive": metrics.queue_lag_block_active,
        "queueDepth": metrics.queue_depth,
        "commandQueueDepth": metrics.command_queue_depth,
        "inventoryReconciled": metrics.inventory_reconciled,
        "recoverySucceeded": metrics.recovery_succeeded,
        "lastError": metrics.last_error,
        "componentHealth": {name: dict(value) for name, value in metrics.component_health.items()},
        "unhealthyComponents": sorted(unhealthy_components),
        "workerStatus": dict(metrics.worker_status),
        "alerts": list(metrics.alert_conditions),
    }


def mark_component_health(
    metrics: RegimeRuntimeMetrics,
    component: str,
    status: str,
    *,
    reason_codes: tuple[str, ...] = (),
    error: str | None = None,
    retry_count: int | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    if component not in REGIME_HEALTH_COMPONENTS:
        raise ValueError(f"Unknown Regime health component: {component}")
    previous = metrics.component_health.get(component, {})
    metrics.component_health[component] = {
        "component": component,
        "status": status,
        "reasonCodes": list(reason_codes),
        "lastError": error,
        "lastCheckedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "retryCount": int(retry_count if retry_count is not None else previous.get("retryCount") or 0),
        "details": dict(details or {}),
    }
    if status == "unhealthy" and error:
        metrics.last_error = error


def observe_decision_result(metrics: RegimeRuntimeMetrics, result: dict[str, Any], *, decision_latency_ms: float | None = None, event_age_seconds: float | None = None) -> None:
    decision = result.get("decision") if isinstance(result.get("decision"), dict) else {}
    signal = str(decision.get("signal") or result.get("signal") or "Hold")
    if signal not in metrics.signal_counts:
        metrics.signal_counts[signal] = 0
    metrics.signal_counts[signal] += 1
    metrics.decision_counts["total"] = int(metrics.decision_counts.get("total") or 0) + 1
    if decision_latency_ms is not None:
        metrics.decision_latency_ms = float(decision_latency_ms)
    if event_age_seconds is not None:
        metrics.latest_event_age_seconds = float(event_age_seconds)
    timings = result.get("runtimeTiming") if isinstance(result.get("runtimeTiming"), dict) else {}
    metrics.classifier_latency_ms = _number(timings.get("classifierLatencyMs"), metrics.classifier_latency_ms)
    metrics.strategy_latency_ms = _number(timings.get("strategyLatencyMs"), metrics.strategy_latency_ms)
    metrics.risk_service_latency_ms = _number(timings.get("riskServiceLatencyMs"), metrics.risk_service_latency_ms)
    confirmed = decision.get("confirmed_state") if isinstance(decision.get("confirmed_state"), dict) else {}
    regime = str(confirmed.get("confirmed_regime") or "unknown")
    metrics.regime_occupancy[regime] = int(metrics.regime_occupancy.get(regime) or 0) + 1
    for blocker in decision.get("trade_blockers") or ():
        key = str(blocker)
        metrics.blockers_by_reason[key] = int(metrics.blockers_by_reason.get(key) or 0) + 1
    for output in decision.get("strategy_outputs") or ():
        if not isinstance(output, dict):
            continue
        strategy_id = str(output.get("strategy_id") or output.get("strategyId") or "unknown")
        if output.get("eligible"):
            metrics.strategy_opportunities[strategy_id] = int(metrics.strategy_opportunities.get(strategy_id) or 0) + 1
        strategy_signals = metrics.strategy_signals.setdefault(strategy_id, {"Buy": 0, "Sell": 0, "Hold": 0})
        strategy_signal = str(output.get("signal") or "Hold")
        strategy_signals[strategy_signal] = int(strategy_signals.get(strategy_signal) or 0) + 1
    family = result.get("familyAggregation") if isinstance(result.get("familyAggregation"), dict) else {}
    scores = family.get("familyScores") if isinstance(family.get("familyScores"), dict) else decision.get("family_scores")
    if isinstance(scores, dict):
        for family_id, contribution in scores.items():
            metrics.family_contributions[str(family_id)] = float(contribution or 0.0)
    proposal = result.get("orderProposal") if isinstance(result.get("orderProposal"), dict) else {}
    approval = result.get("globalRiskApproval") if isinstance(result.get("globalRiskApproval"), dict) else {}
    proposed = int(proposal.get("quantity") or 0)
    approved = int(approval.get("approved_quantity") or approval.get("approvedQuantity") or proposed or 0)
    if proposed:
        metrics.proposed_quantity_total += proposed
        metrics.approved_quantity_total += max(0, approved)
        metrics.proposed_vs_approved_quantity = {
            "proposed": metrics.proposed_quantity_total,
            "approved": metrics.approved_quantity_total,
            "reduced": metrics.proposed_quantity_total - metrics.approved_quantity_total,
            "rejected": int(metrics.proposed_vs_approved_quantity.get("rejected") or 0) + (1 if approval.get("rejected") else 0),
        }


def observe_execution_result(metrics: RegimeRuntimeMetrics, result: dict[str, Any]) -> None:
    status = str(result.get("status") or result.get("processingStatus") or "unknown")
    metrics.order_status_counts[status] = int(metrics.order_status_counts.get(status) or 0) + 1
    latency = result.get("latency") if isinstance(result.get("latency"), dict) else {}
    broker_latency = latency.get("submitToAckMs") or latency.get("submissionToFillLatencyMs")
    if broker_latency is not None:
        metrics.broker_latency_ms = float(broker_latency)
    slippage_bps = _number(result.get("slippageBps") or result.get("averageSlippageBps"), None)
    if slippage_bps is not None:
        samples = int(metrics.slippage.get("sampleCount") or 0) + 1
        total = float(metrics.slippage.get("totalBps") or 0.0) + slippage_bps
        metrics.slippage = {"sampleCount": samples, "totalBps": total, "averageBps": total / samples}
        metrics.fill_quality["averageSlippageBps"] = total / samples
    if status in {"filled", "partially_filled"}:
        metrics.fill_quality["fillCount"] = float(metrics.fill_quality.get("fillCount") or 0) + 1
    if status == "partially_filled":
        metrics.fill_quality["partialFillCount"] = float(metrics.fill_quality.get("partialFillCount") or 0) + 1


def operational_snapshot_from_metrics(metrics: RegimeRuntimeMetrics) -> dict[str, Any]:
    metrics.alert_conditions = alert_conditions_from_metrics(metrics)
    return {
        "algorithmId": "regime",
        "healthVersion": REGIME_RUNTIME_HEALTH_VERSION,
        "supervisorHealth": health_from_metrics(metrics),
        "componentHealth": {name: dict(value) for name, value in metrics.component_health.items()},
        "workerHealth": dict(metrics.worker_status),
        "queueDepth": metrics.queue_depth,
        "queueLagSeconds": metrics.queue_lag_seconds if metrics.queue_lag_seconds is not None else metrics.processing_lag_seconds,
        "eventAgeSeconds": metrics.latest_event_age_seconds,
        "latency": {
            "decisionMs": metrics.decision_latency_ms,
            "classifierMs": metrics.classifier_latency_ms,
            "strategyMs": metrics.strategy_latency_ms,
            "riskServiceMs": metrics.risk_service_latency_ms,
            "brokerMs": metrics.broker_latency_ms,
        },
        "decisionCounts": dict(metrics.decision_counts),
        "signalCounts": dict(metrics.signal_counts),
        "blockersByReason": dict(metrics.blockers_by_reason),
        "regimeOccupancy": dict(metrics.regime_occupancy),
        "strategyOpportunities": dict(metrics.strategy_opportunities),
        "strategySignals": {key: dict(value) for key, value in metrics.strategy_signals.items()},
        "familyContributions": dict(metrics.family_contributions),
        "proposedVsApprovedQuantity": dict(metrics.proposed_vs_approved_quantity),
        "orderStatusCounts": dict(metrics.order_status_counts),
        "fillQuality": dict(metrics.fill_quality),
        "slippage": dict(metrics.slippage),
        "reconciliationDiscrepancies": metrics.reconciliation_discrepancies,
        "recoveryState": dict(metrics.latest_recovery),
        "lastCompletedCheckpoint": metrics.last_checkpoint,
        "latestDecision": metrics.latest_decision,
        "disabledStrategyIds": list(metrics.disabled_strategy_ids),
        "activeSettingsVersion": metrics.active_settings_version,
        "alerts": list(metrics.alert_conditions),
        "operatorsCanExplainNoTrade": True,
        "settingsAndEvidenceAvailablePerDecision": metrics.latest_decision is not None,
    }


def alert_conditions_from_metrics(metrics: RegimeRuntimeMetrics) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []
    if metrics.stale_events or metrics.out_of_order_events:
        alerts.append(_alert("regime.alert.missed_bars", "warning", {"staleEvents": metrics.stale_events, "outOfOrderEvents": metrics.out_of_order_events}))
    if metrics.rejected_events:
        alerts.append(_alert("regime.alert.queue_overflow", "critical" if metrics.queue_depth else "warning", {"rejectedEvents": metrics.rejected_events}))
    if not metrics.settings_available:
        alerts.append(_alert("regime.alert.stale_settings", "critical", {}))
    if not metrics.persistence_available:
        alerts.append(_alert("regime.alert.persistence_failure", "critical", {}))
    for name, health in metrics.component_health.items():
        if str(health.get("status")) == "unhealthy":
            alerts.append(_alert(f"regime.alert.component_unhealthy.{name}", "critical", dict(health)))
    if metrics.broker_latency_ms is None and metrics.submitted_orders:
        alerts.append(_alert("regime.alert.broker_disconnection", "critical", {"submittedOrders": metrics.submitted_orders}))
    if metrics.reconciliation_discrepancies:
        alerts.append(_alert("regime.alert.unresolved_position_discrepancy", "critical", {"count": metrics.reconciliation_discrepancies}))
    if metrics.decision_latency_ms is not None and metrics.decision_latency_ms > 1_500:
        alerts.append(_alert("regime.alert.excessive_decision_latency", "warning", {"decisionLatencyMs": metrics.decision_latency_ms}))
    if metrics.rejected_orders >= 3:
        alerts.append(_alert("regime.alert.repeated_order_rejection", "warning", {"rejectedOrders": metrics.rejected_orders}))
    if any("daily_loss" in reason for reason in metrics.blockers_by_reason):
        alerts.append(_alert("regime.alert.daily_loss_limit", "critical", {"blockers": dict(metrics.blockers_by_reason)}))
    if any("circuit_breaker" in reason for reason in metrics.blockers_by_reason):
        alerts.append(_alert("regime.alert.circuit_breaker_activation", "critical", {"blockers": dict(metrics.blockers_by_reason)}))
    return alerts


def _alert(code: str, severity: str, details: dict[str, Any]) -> dict[str, Any]:
    return {"algorithmId": "regime", "code": code, "severity": severity, "details": details, "detectedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}


def _number(value: Any, fallback: float | None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


__all__ = [
    "REGIME_HEALTH_COMPONENTS",
    "REGIME_RUNTIME_HEALTH_VERSION",
    "RegimeRuntimeMetrics",
    "alert_conditions_from_metrics",
    "health_from_metrics",
    "mark_component_health",
    "observe_decision_result",
    "observe_execution_result",
    "operational_snapshot_from_metrics",
]


def _initial_component_health() -> dict[str, dict[str, Any]]:
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        component: {
            "component": component,
            "status": "unknown",
            "reasonCodes": ["regime.health.component.not_checked"],
            "lastError": None,
            "lastCheckedAt": now,
            "retryCount": 0,
            "details": {},
        }
        for component in REGIME_HEALTH_COMPONENTS
    }
