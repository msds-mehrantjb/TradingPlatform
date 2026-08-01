"""Paper-readiness acceptance gates for the Meta-Strategy runtime."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID


META_STRATEGY_PAPER_READINESS_ACCEPTANCE_VERSION = "meta_strategy_paper_readiness_acceptance_v1"


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
    "META_STRATEGY_PAPER_READINESS_CRITERIA",
    "META_STRATEGY_PAPER_READINESS_TEST_IDS",
    "MetaStrategyPaperReadinessCriterion",
    "build_meta_strategy_paper_readiness_acceptance_report",
    "meta_strategy_paper_readiness_is_complete",
]
