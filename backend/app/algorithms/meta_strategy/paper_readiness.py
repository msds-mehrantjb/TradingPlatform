"""Paper-readiness acceptance gates for the Meta-Strategy runtime."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID


META_STRATEGY_PAPER_READINESS_ACCEPTANCE_VERSION = "meta_strategy_paper_readiness_acceptance_v1"
META_STRATEGY_PAPER_ENTRY_READINESS_VERSION = "meta_strategy_paper_entry_readiness_v1"
META_STRATEGY_MARKET_TIME_WORKERS: tuple[str, ...] = (
    "finalised_bar_decisions",
    "order_submission",
    "order_reconciliation",
    "stale_order_handling",
    "inventory_reconciliation",
    "position_management",
)
META_STRATEGY_MAX_ENTRY_QUEUE_LAG_SECONDS = 75
META_STRATEGY_MAX_ENTRY_DEAD_LETTERS = 0


@dataclass(frozen=True)
class MetaStrategyPaperReadinessCriterion:
    criterion_id: str
    statement: str
    evidence_id: str
    check: Callable[[Mapping[str, Any], Mapping[str, Any] | None], tuple[bool, Any]] | None = None


def build_meta_strategy_paper_readiness_acceptance_report(
    snapshot: Mapping[str, Any],
    runtime: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    evidence = dict(snapshot.get("paperReadinessEvidence") or {})
    items = []
    for criterion in META_STRATEGY_PAPER_READINESS_CRITERIA:
        evidence_item = dict(evidence.get(criterion.evidence_id) or {})
        evidence_passed = evidence_item.get("passed") is True
        runtime_passed = True
        runtime_evidence: Any = None
        if criterion.check is not None:
            runtime_passed, runtime_evidence = criterion.check(snapshot, runtime)
        passed = evidence_passed and runtime_passed
        items.append(
            {
                "itemId": criterion.criterion_id,
                "category": "Paper readiness",
                "statement": criterion.statement,
                "status": "PASSED" if passed else "FAILED",
                "requiredForCompletion": True,
                "evidenceId": criterion.evidence_id,
                "evidence": evidence_item or {"passed": False, "reason": "missing_evidence"},
                "runtimeEvidence": runtime_evidence,
            }
        )
    blocking = [item for item in items if item["status"] != "PASSED"]
    return {
        "algorithmId": ALGORITHM_ID,
        "version": META_STRATEGY_PAPER_READINESS_ACCEPTANCE_VERSION,
        "paperReady": not blocking,
        "paperStatus": "READY" if not blocking else "BLOCKED",
        "counts": {
            "PASSED": sum(1 for item in items if item["status"] == "PASSED"),
            "FAILED": len(blocking),
        },
        "blockingCriteria": [str(item["itemId"]) for item in blocking],
        "items": items,
        "liveExecutionEnabled": False,
    }


def meta_strategy_paper_readiness_is_complete(
    snapshot: Mapping[str, Any],
    runtime: Mapping[str, Any] | None = None,
) -> bool:
    return bool(build_meta_strategy_paper_readiness_acceptance_report(snapshot, runtime)["paperReady"])


def build_meta_strategy_paper_entry_readiness_prerequisites(
    snapshot: Mapping[str, Any],
    runtime: Mapping[str, Any] | None = None,
    paper_readiness: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    runtime_data = dict(runtime or {})
    runtime_prerequisites = dict(runtime_data.get("paperReadinessPrerequisites") or {})
    metrics = dict(snapshot.get("metrics") or {})
    settings = dict(snapshot.get("settings") or {})
    paper_execution = dict(settings.get("paperExecution") or {})
    queue_health = dict(snapshot.get("queueHealth") or {})
    queues = dict(queue_health.get("queues") or {})
    controls = dict(snapshot.get("controls") or {})
    algorithm_readiness = dict(snapshot.get("algorithmReadiness") or {})
    inventory = dict(snapshot.get("inventory") or {})
    inventory_snapshot = dict(inventory.get("snapshot") or {})
    consistency = dict(inventory.get("consistency") or {})
    broker = dict(metrics.get("paperBrokerConnectivity") or {})
    paper_control = _paper_control_state(controls)
    queue_lag_seconds = _queue_lag_seconds(runtime_data, queues)
    dead_letter_count = _dead_letter_count(runtime_data, queues, metrics)
    workers = dict(runtime_data.get("workers") or {})
    required_workers_healthy = _runtime_bool(runtime_prerequisites, runtime_data, "requiredWorkersHealthy", "marketWorkersHealthy")
    if required_workers_healthy is None and workers:
        required_workers_healthy = all(workers.get(queue) == "healthy" for queue in META_STRATEGY_MARKET_TIME_WORKERS)
    if required_workers_healthy is None:
        required_workers_healthy = False
    inventory_reconciled = _runtime_bool(runtime_prerequisites, runtime_data, "inventoryReconciliationCurrent")
    if inventory_reconciled is None:
        inventory_reconciled = bool(
            consistency.get("consistent") is True
            or (
                consistency.get("derivedSnapshotId") is not None
                and consistency.get("derivedSnapshotId") == consistency.get("storedSnapshotId")
            )
        )
    restart_state = dict(runtime_data.get("restartState") or {})
    restart_reconstructed = _runtime_bool(runtime_prerequisites, runtime_data, "restartReconstructionSucceeded")
    if restart_reconstructed is None:
        restart_reconstructed = restart_state.get("status") == "OK"
    required_acceptance_passed = _runtime_bool(runtime_prerequisites, runtime_data, "requiredAcceptanceTestsPassed")
    if required_acceptance_passed is None and paper_readiness is not None:
        required_acceptance_passed = paper_readiness.get("paperReady") is True and not tuple(paper_readiness.get("blockingCriteria") or ())
    inventory_repository_available = _runtime_bool(runtime_prerequisites, runtime_data, "inventoryRepositoryAvailable")
    if inventory_repository_available is None:
        inventory_repository_available = _meta_strategy_owned_inventory_snapshot(inventory_snapshot)
    inventory_consistency_passes = _runtime_bool(runtime_prerequisites, runtime_data, "inventoryConsistencyPasses")
    if inventory_consistency_passes is None:
        inventory_consistency_passes = bool(inventory_reconciled)
    allocated_capital_positive = _runtime_bool(runtime_prerequisites, runtime_data, "allocatedCapitalPositive")
    if allocated_capital_positive is None:
        allocated_capital_positive = _float_value(inventory_snapshot.get("allocated_capital", inventory_snapshot.get("allocatedCapital"))) > 0
    account_snapshot_meta_strategy_derived = _runtime_bool(runtime_prerequisites, runtime_data, "accountSnapshotMetaStrategyDerived")
    if account_snapshot_meta_strategy_derived is None:
        account_snapshot_meta_strategy_derived = _account_snapshot_meta_strategy_derived(runtime_data)
    risk_snapshot_meta_strategy_derived = _runtime_bool(runtime_prerequisites, runtime_data, "riskSnapshotMetaStrategyDerived")
    if risk_snapshot_meta_strategy_derived is None:
        risk_snapshot_meta_strategy_derived = _risk_snapshot_meta_strategy_derived(runtime_data)
    broker_paper_only = _runtime_bool(runtime_prerequisites, runtime_data, "brokerPaperOnly")
    if broker_paper_only is None:
        broker_paper_only = _paper_broker_is_paper_only(broker, runtime_data)
    paper_toggle_enabled = _runtime_bool(runtime_prerequisites, runtime_data, "paperToggleEnabled")
    if paper_toggle_enabled is None:
        paper_toggle_enabled = paper_control.get("newPaperEntriesEnabled") is True and paper_control.get("paperOnly") is True
    runtime_mode_paper = _runtime_bool(runtime_prerequisites, runtime_data, "runtimeModePaper", "paperMode")
    if runtime_mode_paper is None:
        runtime_mode_paper = str(runtime_data.get("mode") or "").upper() == "PAPER"
    live_trading_disabled = _runtime_bool(runtime_prerequisites, runtime_data, "liveTradingDisabled")
    if live_trading_disabled is None:
        live_trading_disabled = paper_control.get("liveExecutionEnabled") is False and runtime_data.get("liveTradingEnabled") is not True
    prerequisites = {
        "version": META_STRATEGY_PAPER_ENTRY_READINESS_VERSION,
        "durableDatabaseAvailable": _runtime_bool(runtime_prerequisites, runtime_data, "durableDatabaseAvailable") is not False,
        "inventoryRepositoryAvailable": bool(inventory_repository_available),
        "inventoryConsistencyPasses": bool(inventory_consistency_passes),
        "allocatedCapitalPositive": bool(allocated_capital_positive),
        "accountSnapshotMetaStrategyDerived": bool(account_snapshot_meta_strategy_derived),
        "riskSnapshotMetaStrategyDerived": bool(risk_snapshot_meta_strategy_derived),
        "activeSettingsPromotedForPaper": _runtime_bool(runtime_prerequisites, runtime_data, "activeSettingsPromotedForPaper")
        if _runtime_bool(runtime_prerequisites, runtime_data, "activeSettingsPromotedForPaper") is not None
        else (
            paper_execution.get("enabled") is True
            and str(paper_execution.get("executionMode") or paper_execution.get("execution_mode") or "").upper() == "PAPER"
        ),
        "paperBrokerVerified": _runtime_bool(runtime_prerequisites, runtime_data, "paperBrokerVerified")
        if _runtime_bool(runtime_prerequisites, runtime_data, "paperBrokerVerified") is not None
        else broker.get("verified") is True or str(broker.get("status") or "").upper() in {"OK", "CONNECTED", "VERIFIED"},
        "brokerPaperOnly": bool(broker_paper_only),
        "authoritativeMarketDataHealthy": _runtime_bool(runtime_prerequisites, runtime_data, "authoritativeMarketDataHealthy", "marketDataHealthy") is True,
        "marketClockHealthy": _runtime_bool(runtime_prerequisites, runtime_data, "marketClockHealthy") is True,
        "requiredWorkersHealthy": bool(required_workers_healthy),
        "queueLagBelowThreshold": bool(queue_lag_seconds is not None and queue_lag_seconds <= _entry_queue_lag_limit_seconds()),
        "deadLetterWithinThreshold": bool(dead_letter_count is not None and dead_letter_count <= _entry_dead_letter_limit()),
        "restartReconstructionSucceeded": bool(restart_reconstructed),
        "inventoryReconciliationCurrent": bool(inventory_reconciled),
        "globalRiskSourceCurrent": _runtime_bool(runtime_prerequisites, runtime_data, "globalRiskSourceCurrent") is True,
        "requiredAcceptanceTestsPassed": bool(required_acceptance_passed),
        "paperToggleEnabled": bool(paper_toggle_enabled),
        "runtimeModePaper": bool(runtime_mode_paper),
        "liveTradingDisabled": bool(live_trading_disabled),
        "queueLagSeconds": queue_lag_seconds,
        "deadLetterCount": dead_letter_count,
        "workers": workers,
        "restartState": restart_state,
        "evidence": {
            "runtimePrerequisites": runtime_prerequisites,
            "algorithmReadiness": algorithm_readiness,
            "inventorySnapshot": inventory_snapshot,
            "inventoryConsistency": consistency,
            "paperBrokerConnectivity": broker,
            "paperControl": paper_control,
            "queueHealth": queue_health,
        },
    }
    blocking = [
        key
        for key, value in prerequisites.items()
        if key
        in {
            "durableDatabaseAvailable",
            "inventoryRepositoryAvailable",
            "inventoryConsistencyPasses",
            "allocatedCapitalPositive",
            "accountSnapshotMetaStrategyDerived",
            "riskSnapshotMetaStrategyDerived",
            "activeSettingsPromotedForPaper",
            "paperBrokerVerified",
            "brokerPaperOnly",
            "authoritativeMarketDataHealthy",
            "marketClockHealthy",
            "requiredWorkersHealthy",
            "queueLagBelowThreshold",
            "deadLetterWithinThreshold",
            "restartReconstructionSucceeded",
            "inventoryReconciliationCurrent",
            "globalRiskSourceCurrent",
            "requiredAcceptanceTestsPassed",
            "paperToggleEnabled",
            "runtimeModePaper",
            "liveTradingDisabled",
        }
        and value is not True
    ]
    prerequisites["ready"] = not blocking
    prerequisites["blockingPrerequisites"] = tuple(blocking)
    return prerequisites


def _runtime_bool(prerequisites: Mapping[str, Any], runtime: Mapping[str, Any], *keys: str) -> bool | None:
    for key in keys:
        if prerequisites.get(key) is not None:
            return prerequisites.get(key) is True
        if runtime.get(key) is not None:
            return runtime.get(key) is True
    return None


def _paper_control_state(controls: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("AUTOMATIC_PAPER_TRADING", "automaticPaperTrading", "paperControl"):
        candidate = controls.get(key)
        if isinstance(candidate, Mapping):
            state = candidate.get("state")
            return dict(state) if isinstance(state, Mapping) else dict(candidate)
    return {}


def _meta_strategy_owned_inventory_snapshot(snapshot: Mapping[str, Any]) -> bool:
    return (
        snapshot.get("algorithm_id", snapshot.get("algorithmId")) == "meta_strategy"
        and snapshot.get("capital_partition_id", snapshot.get("capitalPartitionId")) == "meta_strategy.paper.default"
    )


def _account_snapshot_meta_strategy_derived(runtime: Mapping[str, Any]) -> bool:
    account = runtime.get("accountSnapshot") or runtime.get("account")
    if not isinstance(account, Mapping):
        return False
    return (
        account.get("algorithmId") == "meta_strategy"
        and account.get("capitalPartitionId") == "meta_strategy.paper.default"
        and account.get("accountAuthority") == "meta_strategy_inventory.current_inventory_snapshot"
    )


def _risk_snapshot_meta_strategy_derived(runtime: Mapping[str, Any]) -> bool:
    risk = runtime.get("riskSnapshot") or runtime.get("globalRiskSnapshot") or runtime.get("globalRisk")
    if not isinstance(risk, Mapping):
        return False
    return (
        risk.get("algorithmId") == "meta_strategy"
        and risk.get("capitalPartitionId") == "meta_strategy.paper.default"
        and risk.get("source") == "meta_strategy_local_settings_risk"
    )


def _paper_broker_is_paper_only(broker: Mapping[str, Any], runtime: Mapping[str, Any]) -> bool:
    mode = str(runtime.get("paperGatewayExecutionMode") or runtime.get("executionMode") or broker.get("executionMode") or "").upper()
    if mode in {"LOCAL_PAPER", "BROKER_PAPER", "LOCAL_LEDGER"}:
        return True
    return broker.get("paperOnly") is True or broker.get("liveExecutionEnabled") is False


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _entry_queue_lag_limit_seconds() -> int:
    return _env_non_negative_int("META_STRATEGY_QUEUE_LAG_THRESHOLD_SECONDS", META_STRATEGY_MAX_ENTRY_QUEUE_LAG_SECONDS)


def _entry_dead_letter_limit() -> int:
    return _env_non_negative_int("META_STRATEGY_DEAD_LETTER_THRESHOLD", META_STRATEGY_MAX_ENTRY_DEAD_LETTERS)


def _env_non_negative_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(0, value)


def _queue_lag_seconds(runtime: Mapping[str, Any], queues: Mapping[str, Any]) -> int | None:
    lag = runtime.get("queueLagSeconds")
    if isinstance(lag, Mapping) and lag:
        return max(int(value or 0) for value in lag.values())
    if isinstance(lag, (int, float)):
        return int(lag)
    values = []
    for data in queues.values():
        if isinstance(data, Mapping) and data.get("lagSeconds") is not None:
            values.append(int(data.get("lagSeconds") or 0))
    return max(values) if values else None


def _dead_letter_count(runtime: Mapping[str, Any], queues: Mapping[str, Any], metrics: Mapping[str, Any]) -> int | None:
    if runtime.get("deadLetterCount") is not None:
        return int(runtime.get("deadLetterCount") or 0)
    if metrics.get("jobDeadLetterCount") is not None:
        return int(metrics.get("jobDeadLetterCount") or 0)
    values = []
    for data in queues.values():
        if isinstance(data, Mapping) and data.get("deadLetter") is not None:
            values.append(int(data.get("deadLetter") or 0))
    return sum(values) if values else None


def _worker_healthy(queue_name: str) -> Callable[[Mapping[str, Any], Mapping[str, Any] | None], tuple[bool, Any]]:
    def check(_snapshot: Mapping[str, Any], runtime: Mapping[str, Any] | None) -> tuple[bool, Any]:
        workers = dict((runtime or {}).get("workers") or {})
        return workers.get(queue_name) == "healthy", {"worker": queue_name, "status": workers.get(queue_name)}

    return check


def _workers_healthy(*queue_names: str) -> Callable[[Mapping[str, Any], Mapping[str, Any] | None], tuple[bool, Any]]:
    def check(_snapshot: Mapping[str, Any], runtime: Mapping[str, Any] | None) -> tuple[bool, Any]:
        workers = dict((runtime or {}).get("workers") or {})
        statuses = {queue: workers.get(queue) for queue in queue_names}
        return all(status == "healthy" for status in statuses.values()), statuses

    return check


def _paper_runtime(_snapshot: Mapping[str, Any], runtime: Mapping[str, Any] | None) -> tuple[bool, Any]:
    data = dict(runtime or {})
    passed = data.get("mode") == "PAPER" and data.get("paperOrdersBlocked") is False and data.get("ready") is True
    return passed, {"mode": data.get("mode"), "ready": data.get("ready"), "paperOrdersBlocked": data.get("paperOrdersBlocked")}


def _synthetic_fills_disabled(snapshot: Mapping[str, Any], _runtime: Mapping[str, Any] | None) -> tuple[bool, Any]:
    paper_execution = dict(dict(snapshot.get("settings") or {}).get("paperExecution") or {})
    synthetic = paper_execution.get("syntheticImmediateFillsAllowed", paper_execution.get("synthetic_immediate_fills_allowed"))
    return synthetic is False, paper_execution


def _ml_disabled_or_shadow(snapshot: Mapping[str, Any], _runtime: Mapping[str, Any] | None) -> tuple[bool, Any]:
    settings = dict(snapshot.get("settings") or {})
    ml = dict(settings.get("mlInference") or {})
    model = dict(snapshot.get("model") or {})
    mode = str(ml.get("mode") or "").upper()
    return mode in {"DISABLED", "SHADOW"} and model.get("mlShadowOnly") is True, {"mlInference": ml, "model": model}


def _dynamic_overlays_not_risk_increasing(snapshot: Mapping[str, Any], _runtime: Mapping[str, Any] | None) -> tuple[bool, Any]:
    settings = dict(snapshot.get("settings") or {})
    overlays = tuple(settings.get("dynamicOverlays") or ())
    changes = tuple(settings.get("dynamicOverlayChanges") or ())
    overlay_safe = all(
        float(dict(overlay).get("riskMultiplier", 1.0)) <= 1.0
        and float(dict(overlay).get("positionSizeMultiplier", 1.0)) <= 1.0
        for overlay in overlays
        if isinstance(overlay, Mapping)
    )
    change_safe = all(
        str(dict(change).get("field") or "") not in {"local_risk.risk_percentage", "position_sizing.position_cap"}
        or float(dict(change).get("effectiveValue") or 0.0) <= float(dict(change).get("baselineValue") or 0.0)
        for change in changes
        if isinstance(change, Mapping)
    )
    return overlay_safe and change_safe, {"dynamicOverlays": overlays, "dynamicOverlayChanges": changes}


def _live_disabled(snapshot: Mapping[str, Any], runtime: Mapping[str, Any] | None) -> tuple[bool, Any]:
    return snapshot.get("liveExecutionEnabled") is False and dict(runtime or {}).get("mode") != "LIVE", {
        "snapshotLiveExecutionEnabled": snapshot.get("liveExecutionEnabled"),
        "runtimeMode": dict(runtime or {}).get("mode"),
    }


META_STRATEGY_PAPER_READINESS_CRITERIA: tuple[MetaStrategyPaperReadinessCriterion, ...] = (
    MetaStrategyPaperReadinessCriterion("finalized_spy_candle_exactly_one_event", "A finalized one-minute SPY candle automatically creates exactly one durable event.", "finalized_spy_candle_exactly_one_event"),
    MetaStrategyPaperReadinessCriterion("api_routes_enqueue_only", "No API request performs the heavy trading pipeline synchronously.", "api_routes_enqueue_only"),
    MetaStrategyPaperReadinessCriterion("unhandled_worker_jobs_fail_closed", "No worker can complete an unhandled job as successful.", "unhandled_worker_jobs_fail_closed"),
    MetaStrategyPaperReadinessCriterion("finalized_bar_worker_running", "The real finalized-bar worker is running.", "finalized_bar_worker_running", _worker_healthy("finalised_bar_decisions")),
    MetaStrategyPaperReadinessCriterion("paper_submission_worker_running", "The paper submission worker is running.", "paper_submission_worker_running", _worker_healthy("order_submission")),
    MetaStrategyPaperReadinessCriterion("reconciliation_and_stale_order_workers_running", "Reconciliation and stale-order workers are running.", "reconciliation_and_stale_order_workers_running", _workers_healthy("order_reconciliation", "stale_order_handling")),
    MetaStrategyPaperReadinessCriterion("position_management_worker_running", "The position-management worker is running.", "position_management_worker_running", _worker_healthy("position_management")),
    MetaStrategyPaperReadinessCriterion("alpaca_paper_endpoint_only", "PAPER mode uses the Alpaca paper endpoint only.", "alpaca_paper_endpoint_only", _paper_runtime),
    MetaStrategyPaperReadinessCriterion("missing_broker_configuration_blocks_startup", "Missing broker configuration blocks startup.", "missing_broker_configuration_blocks_startup"),
    MetaStrategyPaperReadinessCriterion("synthetic_fills_disabled", "Synthetic fills are disabled.", "synthetic_fills_disabled", _synthetic_fills_disabled),
    MetaStrategyPaperReadinessCriterion("decision_and_intent_before_submission", "A decision and order intent exist before every submitted order.", "decision_and_intent_before_submission"),
    MetaStrategyPaperReadinessCriterion("duplicate_processing_idempotent_order", "Duplicate processing cannot duplicate an order.", "duplicate_processing_idempotent_order"),
    MetaStrategyPaperReadinessCriterion("partial_fills_apply_once", "Partial fills update inventory exactly once.", "partial_fills_apply_once"),
    MetaStrategyPaperReadinessCriterion("inventory_isolation", "Meta-Strategy inventory is isolated from all sibling algorithms.", "inventory_isolation"),
    MetaStrategyPaperReadinessCriterion("caller_authoritative_state_rejected", "Caller-supplied inventory and settings are rejected.", "caller_authoritative_state_rejected"),
    MetaStrategyPaperReadinessCriterion("existing_position_management_complete", "Existing positions receive stop, target, timeout, invalidation, and end-of-day management.", "existing_position_management_complete"),
    MetaStrategyPaperReadinessCriterion("restart_reconstruction_and_broker_reconciliation", "Restart reconstruction and broker reconciliation pass.", "restart_reconstruction_and_broker_reconciliation"),
    MetaStrategyPaperReadinessCriterion("parity_same_decision_logic", "Backtest, replay, shadow, and paper use the same decision logic.", "parity_same_decision_logic"),
    MetaStrategyPaperReadinessCriterion("ml_disabled_or_shadow_only", "ML is disabled or shadow-only for the initial rollout.", "ml_disabled_or_shadow_only", _ml_disabled_or_shadow),
    MetaStrategyPaperReadinessCriterion("dynamic_overlays_do_not_increase_baseline_risk", "Dynamic overlays cannot increase risk above the initial baseline.", "dynamic_overlays_do_not_increase_baseline_risk", _dynamic_overlays_not_risk_increasing),
    MetaStrategyPaperReadinessCriterion("all_required_tests_pass", "All unit, integration, isolation, parity, failure-injection, and end-to-end tests pass.", "all_required_tests_pass"),
    MetaStrategyPaperReadinessCriterion("live_trading_disabled", "Live trading remains disabled.", "live_trading_disabled", _live_disabled),
)

META_STRATEGY_PAPER_READINESS_TEST_IDS: tuple[str, ...] = tuple(
    criterion.evidence_id for criterion in META_STRATEGY_PAPER_READINESS_CRITERIA
)


__all__ = [
    "META_STRATEGY_PAPER_READINESS_ACCEPTANCE_VERSION",
    "META_STRATEGY_PAPER_ENTRY_READINESS_VERSION",
    "META_STRATEGY_PAPER_READINESS_CRITERIA",
    "META_STRATEGY_PAPER_READINESS_TEST_IDS",
    "META_STRATEGY_MARKET_TIME_WORKERS",
    "MetaStrategyPaperReadinessCriterion",
    "build_meta_strategy_paper_entry_readiness_prerequisites",
    "build_meta_strategy_paper_readiness_acceptance_report",
    "meta_strategy_paper_readiness_is_complete",
]
