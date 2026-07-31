"""Regime runtime health and status models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


REGIME_RUNTIME_HEALTH_VERSION = "regime_runtime_health_v1"
REGIME_HEALTH_COMPONENTS = (
    "runtime_supervisor",
    "market_event_ingestion",
    "settings_repository",
    "runtime_state",
    "strategy_registry",
    "decision_worker",
    "local_risk",
    "global_risk_connection",
    "risk_reservations",
    "execution_outbox",
    "paper_broker",
    "broker_connectivity",
    "inventory",
    "order_reconciliation",
    "position_reconciliation",
    "backtest_worker",
    "database",
)


@dataclass
class RegimeRuntimeMetrics:
    supervisor_started: bool = False
    supervisor_heartbeat_at: str | None = None
    paused: bool = False
    kill_switch_active: bool = False
    kill_switch_reason: str | None = None
    kill_switch_actor: str | None = None
    kill_switch_activated_at: str | None = None
    kill_switch_state_version: int = 0
    pending_entry_orders_cancel_requested: int = 0
    emergency_flatten_requested: bool = False
    entry_creation_paused_for_reconciliation: bool = True
    inventory_reconciled: bool = False
    inventory_available: bool = True
    recovery_succeeded: bool = False
    broker_paper_mode_verified: bool = False
    broker_connectivity_ok: bool = False
    current_rollout_stage: str = "decision_shadow"
    rollout_stage_version: int = 0
    rollout_stage_policy: dict[str, Any] = field(default_factory=dict)
    simulated_execution_active: bool = False
    strategy_registry_valid: bool = True
    outbox_stuck: bool = False
    risk_reservations_consistent: bool = True
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
    gap_events: int = 0
    stale_events: int = 0
    missing_bar_count: int = 0
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
    last_received_bar: dict[str, Any] | None = None
    last_finalized_bar: dict[str, Any] | None = None
    last_processed_bar: dict[str, Any] | None = None
    latest_command: dict[str, Any] | None = None
    latest_recovery: dict[str, Any] = field(default_factory=dict)
    latest_reconciliation: dict[str, Any] | None = None
    alert_conditions: list[dict[str, Any]] = field(default_factory=list)
    disabled_strategy_ids: list[str] = field(default_factory=list)
    active_settings_version: str | None = None
    current_strategy_routing: dict[str, Any] = field(default_factory=dict)
    current_inventory: dict[str, Any] = field(default_factory=dict)
    open_orders: list[dict[str, Any]] = field(default_factory=list)
    risk_reservations: list[dict[str, Any]] = field(default_factory=list)
    outbox_status: dict[str, Any] = field(default_factory=dict)
    reconciliation_status: dict[str, Any] = field(default_factory=dict)
    broker_connectivity: dict[str, Any] = field(default_factory=dict)
    daily_regime_pnl: float = 0.0
    daily_trade_count: int = 0
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
    _derive_required_component_health(metrics)
    metrics.alert_conditions = alert_conditions_from_metrics(metrics)
    unhealthy_components = {
        name: health
        for name, health in metrics.component_health.items()
        if str(health.get("status")) == "unhealthy"
    }
    healthy = metrics.supervisor_started and not unhealthy_components and not metrics.quarantined and not metrics.kill_switch_active
    return {
        "algorithmId": "regime",
        "healthVersion": REGIME_RUNTIME_HEALTH_VERSION,
        "healthy": healthy,
        "failClosed": metrics.entry_creation_paused_for_reconciliation and not metrics.recovery_succeeded,
        "newEntriesBlocked": bool(metrics.entry_block_reason_codes or metrics.entry_creation_paused_for_reconciliation or metrics.paused or metrics.kill_switch_active),
        "entryBlockReasonCodes": list(metrics.entry_block_reason_codes),
        "killSwitch": kill_switch_status_from_metrics(metrics),
        "quarantined": metrics.quarantined,
        "persistenceAvailable": metrics.persistence_available,
        "settingsAvailable": metrics.settings_available,
        "inventoryAvailable": metrics.inventory_available,
        "brokerPaperModeVerified": metrics.broker_paper_mode_verified,
        "brokerConnectivityOk": metrics.broker_connectivity_ok,
        "strategyRegistryValid": metrics.strategy_registry_valid,
        "outboxStuck": metrics.outbox_stuck,
        "riskReservationsConsistent": metrics.risk_reservations_consistent,
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
    _derive_required_component_health(metrics)
    metrics.alert_conditions = alert_conditions_from_metrics(metrics)
    return {
        "algorithmId": "regime",
        "healthVersion": REGIME_RUNTIME_HEALTH_VERSION,
        "telemetryVersion": "regime_operational_telemetry_v1",
        "supervisorHeartbeat": {
            "started": metrics.supervisor_started,
            "heartbeatAt": metrics.supervisor_heartbeat_at,
            "workerStatus": dict(metrics.worker_status),
        },
        "lastReceivedBar": metrics.last_received_bar,
        "lastFinalizedBar": metrics.last_finalized_bar,
        "lastProcessedBar": metrics.last_processed_bar,
        "supervisorHealth": health_from_metrics(metrics),
        "componentHealth": {name: dict(value) for name, value in metrics.component_health.items()},
        "workerHealth": dict(metrics.worker_status),
        "queueDepth": metrics.queue_depth,
        "queueLagSeconds": metrics.queue_lag_seconds if metrics.queue_lag_seconds is not None else metrics.processing_lag_seconds,
        "queueLag": {"seconds": metrics.queue_lag_seconds if metrics.queue_lag_seconds is not None else metrics.processing_lag_seconds, "blockActive": metrics.queue_lag_block_active},
        "eventAgeSeconds": metrics.latest_event_age_seconds,
        "processingLatency": {"decisionMs": metrics.decision_latency_ms},
        "latency": {
            "decisionMs": metrics.decision_latency_ms,
            "classifierMs": metrics.classifier_latency_ms,
            "strategyMs": metrics.strategy_latency_ms,
            "riskServiceMs": metrics.risk_service_latency_ms,
            "brokerMs": metrics.broker_latency_ms,
        },
        "decisionCounts": dict(metrics.decision_counts),
        "duplicateBarCount": metrics.duplicate_events,
        "missingBarCount": metrics.missing_bar_count,
        "staleDataState": {
            "staleEvents": metrics.stale_events,
            "queueLagBlockActive": metrics.queue_lag_block_active,
            "latestEventAgeSeconds": metrics.latest_event_age_seconds,
        },
        "signalCounts": dict(metrics.signal_counts),
        "blockersByReason": dict(metrics.blockers_by_reason),
        "entryBlockers": list(metrics.entry_block_reason_codes),
        "regimeOccupancy": dict(metrics.regime_occupancy),
        "currentConfirmedRegime": _current_regime(metrics),
        "currentStrategyRouting": dict(metrics.current_strategy_routing),
        "strategyOpportunities": dict(metrics.strategy_opportunities),
        "strategySignals": {key: dict(value) for key, value in metrics.strategy_signals.items()},
        "familyContributions": dict(metrics.family_contributions),
        "proposedVsApprovedQuantity": dict(metrics.proposed_vs_approved_quantity),
        "orderStatusCounts": dict(metrics.order_status_counts),
        "outboxStatus": dict(metrics.outbox_status),
        "openOrders": list(metrics.open_orders),
        "riskReservations": list(metrics.risk_reservations),
        "fillQuality": dict(metrics.fill_quality),
        "slippage": dict(metrics.slippage),
        "currentInventory": dict(metrics.current_inventory),
        "reconciliationDiscrepancies": metrics.reconciliation_discrepancies,
        "reconciliationStatus": dict(metrics.reconciliation_status or (metrics.latest_reconciliation or {})),
        "brokerConnectivity": dict(metrics.broker_connectivity),
        "dailyRegimePnl": metrics.daily_regime_pnl,
        "dailyTradeCount": metrics.daily_trade_count,
        "killSwitch": kill_switch_status_from_metrics(metrics),
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
    if not metrics.supervisor_started:
        alerts.append(_alert("regime.alert.supervisor_stopped", "critical", {}))
    if metrics.stale_events or metrics.out_of_order_events:
        alerts.append(_alert("regime.alert.missed_bars", "warning", {"staleEvents": metrics.stale_events, "outOfOrderEvents": metrics.out_of_order_events}))
    if metrics.rejected_events:
        alerts.append(_alert("regime.alert.queue_overflow", "critical" if metrics.queue_depth else "warning", {"rejectedEvents": metrics.rejected_events}))
    if not metrics.settings_available:
        alerts.append(_alert("regime.alert.stale_settings", "critical", {}))
    if not metrics.inventory_available:
        alerts.append(_alert("regime.alert.inventory_unavailable", "critical", {}))
    if metrics.kill_switch_active:
        alerts.append(_alert("regime.alert.kill_switch_active", "critical", kill_switch_status_from_metrics(metrics)))
    if not metrics.broker_paper_mode_verified:
        alerts.append(_alert("regime.alert.paper_broker_not_verified", "critical", dict(metrics.broker_connectivity)))
    if not metrics.strategy_registry_valid:
        alerts.append(_alert("regime.alert.strategy_registry_invalid", "critical", {}))
    if metrics.outbox_stuck:
        alerts.append(_alert("regime.alert.execution_outbox_stuck", "critical", dict(metrics.outbox_status)))
    if not metrics.risk_reservations_consistent:
        alerts.append(_alert("regime.alert.risk_reservation_inconsistent", "critical", {"riskReservations": list(metrics.risk_reservations)}))
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


def kill_switch_status_from_metrics(metrics: RegimeRuntimeMetrics) -> dict[str, Any]:
    return {
        "algorithmId": "regime",
        "active": metrics.kill_switch_active,
        "reason": metrics.kill_switch_reason,
        "actor": metrics.kill_switch_actor,
        "activatedAt": metrics.kill_switch_activated_at,
        "stateVersion": metrics.kill_switch_state_version,
        "blocksNewEntries": metrics.kill_switch_active,
        "riskReducingExitsAllowed": metrics.risk_reducing_exits_allowed,
        "pendingEntryOrdersCancelRequested": metrics.pending_entry_orders_cancel_requested,
    }


def _derive_required_component_health(metrics: RegimeRuntimeMetrics) -> None:
    checks = {
        "runtime_supervisor": (metrics.supervisor_started, "regime.health.supervisor.stopped"),
        "market_event_ingestion": (not (metrics.stale_events or metrics.queue_lag_block_active), "regime.health.market_data.stale"),
        "settings_repository": (metrics.settings_available, "regime.health.settings.unavailable"),
        "inventory": (metrics.inventory_available and metrics.inventory_reconciled, "regime.health.inventory.unavailable_or_unreconciled"),
        "paper_broker": (metrics.broker_paper_mode_verified, "regime.health.paper_broker.not_verified"),
        "broker_connectivity": (metrics.broker_connectivity_ok or not metrics.submitted_orders, "regime.health.broker_connectivity.unhealthy"),
        "strategy_registry": (metrics.strategy_registry_valid, "regime.health.strategy_registry.invalid"),
        "execution_outbox": (not metrics.outbox_stuck, "regime.health.execution_outbox.stuck"),
        "risk_reservations": (metrics.risk_reservations_consistent, "regime.health.risk_reservations.inconsistent"),
        "order_reconciliation": (not metrics.reconciliation_discrepancies, "regime.health.reconciliation.unresolved"),
        "position_reconciliation": (not metrics.reconciliation_discrepancies and metrics.inventory_reconciled, "regime.health.reconciliation.unresolved"),
    }
    for component, (ok, reason) in checks.items():
        current = metrics.component_health.get(component, {})
        if ok or str(current.get("status")) == "unhealthy":
            continue
        metrics.component_health[component] = {
            "component": component,
            "status": "unhealthy",
            "reasonCodes": [reason],
            "lastError": None,
            "lastCheckedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "retryCount": int(current.get("retryCount") or 0),
            "details": dict(current.get("details") or {}),
        }


def _current_regime(metrics: RegimeRuntimeMetrics) -> str:
    latest = metrics.latest_decision if isinstance(metrics.latest_decision, dict) else {}
    decision = latest.get("decision") if isinstance(latest.get("decision"), dict) else {}
    confirmed = decision.get("confirmed_state") if isinstance(decision.get("confirmed_state"), dict) else {}
    return str(confirmed.get("confirmed_regime") or "unknown")


__all__ = [
    "REGIME_HEALTH_COMPONENTS",
    "REGIME_RUNTIME_HEALTH_VERSION",
    "RegimeRuntimeMetrics",
    "alert_conditions_from_metrics",
    "health_from_metrics",
    "mark_component_health",
    "kill_switch_status_from_metrics",
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
