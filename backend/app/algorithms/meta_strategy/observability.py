from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.paper_readiness import META_STRATEGY_PAPER_READINESS_TEST_IDS
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore
from backend.app.algorithms.meta_strategy.strategy_registry import ALL_META_STRATEGY_STRATEGIES


META_STRATEGY_OBSERVABILITY_VERSION = "meta_strategy_observability_v1"
META_STRATEGY_CONTROL_SNAPSHOT_PREFIX = "meta_strategy.controls."
META_STRATEGY_OPERATIONAL_CONTROLS: tuple[str, ...] = (
    "PAUSE_NEW_ENTRIES",
    "RESUME_NEW_ENTRIES",
    "EXIT_ONLY",
    "CANCEL_OPEN_META_ORDERS",
    "FLATTEN_META_INVENTORY",
    "DISABLE_ML_INFLUENCE",
    "DISABLE_DYNAMIC_OVERLAYS",
    "STOP_META_RUNTIME",
)
_LEGACY_CONTROL_ALIASES = {
    "pause_new_entries": "PAUSE_NEW_ENTRIES",
    "resume_new_entries": "RESUME_NEW_ENTRIES",
    "exit_only": "EXIT_ONLY",
    "cancel_open_meta_orders": "CANCEL_OPEN_META_ORDERS",
    "flatten_meta_inventory": "FLATTEN_META_INVENTORY",
    "disable_ml_influence": "DISABLE_ML_INFLUENCE",
    "disable_dynamic_overlays": "DISABLE_DYNAMIC_OVERLAYS",
    "stop_meta_runtime": "STOP_META_RUNTIME",
    "cancel_pending_jobs": "cancel_pending_jobs",
    "force_reconciliation": "force_reconciliation",
    "model_rollback": "model_rollback",
    "disable_strategy": "disable_strategy",
    "disable_symbol": "disable_symbol",
    "switch_active_strategies_to_shadow": "switch_active_strategies_to_shadow",
    "stop_execution_continue_decisions": "STOP_META_RUNTIME",
    "drain_and_stop_workers": "STOP_META_RUNTIME",
}

META_STRATEGY_RECOVERY_TEST_IDS: tuple[str, ...] = (
    "api_restart",
    "decision_worker_restart",
    "execution_worker_restart",
    "reconciliation_worker_restart",
    "database_temporary_unavailable",
    "broker_timeout",
    "duplicate_market_event",
    "duplicate_broker_event",
    "stale_lease",
    "dead_letter_recovery",
    "settings_rollback",
    "model_rollback",
    "full_inventory_rebuild",
)

META_STRATEGY_FINAL_DOD_IDS: tuple[str, ...] = (
    "no_inline_api_runtime_work",
    "paper_runtime_uses_durable_persistence",
    "paper_runtime_uses_real_paper_broker",
    "api_rejects_authoritative_state",
    "isolated_inventory_and_versioned_settings",
    "active_strategy_inputs_complete",
    "safety_gates_fail_closed",
    "finalised_bars_create_idempotent_decisions",
    "decision_outbox_atomic",
    "broker_submission_reconciliation_workers_separate",
    "duplicate_orders_and_fills_idempotent",
    "restart_recovery_tests_pass",
    "runtime_replay_backtest_parity",
    "cost_spread_slippage_included",
    "shadow_stability_passes",
    "paper_stability_reconciliation_passes",
    "ml_shadow_only_until_promotion_evidence",
    "live_execution_disabled",
)


@dataclass(frozen=True)
class MetaStrategyOperationalControlResult:
    algorithm_id: str
    control: str
    status: str
    evidence_id: str
    payload: dict[str, Any]
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": self.algorithm_id,
            "control": self.control,
            "status": self.status,
            "evidenceId": self.evidence_id,
            "payload": self.payload,
            "reasonCodes": self.reason_codes,
        }


def build_meta_strategy_observability_snapshot(
    *,
    job_repository: MetaStrategyJobRepository,
    inventory_repository: MetaStrategySqliteRepository,
    settings_store: MetaStrategySettingsStore,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = now or datetime.now(UTC)
    queue_status = job_repository.queue_status(now=current)
    operational_metrics = job_repository.operational_metrics(now=current)
    inventory = inventory_repository.current_inventory_snapshot()
    inventory_consistency = inventory_repository.check_inventory_consistency()
    settings = settings_store.get_active_settings()
    model = job_repository.active_model_pointer()
    gateway = job_repository.gateway_snapshots()
    controls = _control_snapshots(gateway)
    strategy_counts = _strategy_counts()
    required_metrics = _required_operational_metrics(
        job_repository=job_repository,
        base_metrics=operational_metrics,
        queue_status=queue_status,
        inventory=inventory,
        inventory_consistency=inventory_consistency,
        controls=controls,
        now=current,
    )
    return {
        "algorithmId": ALGORITHM_ID,
        "version": META_STRATEGY_OBSERVABILITY_VERSION,
        "asOf": current.isoformat(),
        "metrics": {
            **operational_metrics,
            **required_metrics,
            "workerHeartbeat": queue_status["workers"],
            "strategyActivationCounts": strategy_counts,
            "dailyPnl": inventory.realised_pnl,
            "reservedRisk": inventory.reserved_risk_dollars,
            "paperBrokerConnectivity": _paper_broker_connectivity(gateway),
            "settingsVersion": settings.settings_version,
            "modelVersion": model.get("modelArtifactId"),
        },
        "queueHealth": queue_status,
        "inventory": {
            "snapshot": _plain_inventory(inventory),
            "consistency": inventory_consistency,
        },
        "settings": {
            "activeSettingsVersion": settings.settings_version,
            "settingsHash": settings.settings_hash,
            "paperExecution": _plain_model(settings.paper_execution),
            "mlInference": _plain_model(settings.ml_inference),
            "dynamicOverlays": tuple(_plain_model(overlay) for overlay in settings.dynamic_overlays),
            "dynamicOverlayChanges": tuple(settings.dynamic_overlay_changes),
        },
        "model": {
            **model,
            "mlShadowOnly": True,
        },
        "controls": controls,
        "supportedControls": META_STRATEGY_OPERATIONAL_CONTROLS,
        "algorithmReadiness": _algorithm_readiness(operational_metrics, inventory_consistency, controls),
        "recoveryEvidence": _test_evidence(job_repository, META_STRATEGY_RECOVERY_TEST_IDS),
        "definitionOfDoneEvidence": _test_evidence(job_repository, META_STRATEGY_FINAL_DOD_IDS),
        "paperReadinessEvidence": _test_evidence(job_repository, META_STRATEGY_PAPER_READINESS_TEST_IDS),
        "paperStabilityEvidence": _paper_stability_evidence(job_repository),
        "liveExecutionEnabled": False,
    }


def apply_meta_strategy_operational_control(
    *,
    job_repository: MetaStrategyJobRepository,
    control: str,
    actor: str,
    reason: str,
    payload: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> MetaStrategyOperationalControlResult:
    current = now or datetime.now(UTC)
    body = dict(payload or {})
    canonical = _canonical_control(control)
    correlation_id = str(body.get("correlationId") or body.get("correlation_id") or f"meta_strategy.control.{canonical or control}.{current.isoformat()}")
    status = "RECORDED" if canonical else "REJECTED"
    result_payload: dict[str, Any] = {
        "actor": actor,
        "reason": reason,
        "control": canonical or control,
        "requestedControl": control,
        "requestedAt": current.isoformat(),
        "correlationId": correlation_id,
        **body,
    }
    if canonical is None:
        result_payload["state"] = {"supported": False, "supportedControls": META_STRATEGY_OPERATIONAL_CONTROLS}
    elif canonical == "cancel_pending_jobs":
        cancel = job_repository.cancel_pending_jobs(queue_name=_optional_str(body.get("queueName") or body.get("queue_name")), now=current)
        result_payload.update(cancel)
    elif canonical == "force_reconciliation":
        job = job_repository.enqueue_job(
            job_type="inventory_reconciliation",
            idempotency_key=f"meta_strategy.force_reconciliation.{body.get('idempotencyKey') or correlation_id}",
            payload={"source": "operational_control", "actor": actor, "reason": reason, "correlationId": correlation_id},
            now=current,
        )
        result_payload["jobId"] = job.job_id
    elif canonical == "model_rollback":
        result_payload.update(job_repository.rollback_active_model(actor=actor, reason=reason, now=current))
    elif canonical == "CANCEL_OPEN_META_ORDERS":
        result_payload["state"] = _control_state(canonical, body)
        result_payload["jobId"] = _enqueue_control_job(job_repository, "stale_order_handling", canonical, actor, reason, correlation_id, current).job_id
    elif canonical == "FLATTEN_META_INVENTORY":
        result_payload["state"] = _control_state(canonical, body)
        result_payload["jobId"] = _enqueue_control_job(job_repository, "position_management", canonical, actor, reason, correlation_id, current).job_id
    else:
        result_payload["state"] = _control_state(canonical, body)
    snapshot_key = f"{META_STRATEGY_CONTROL_SNAPSHOT_PREFIX}{canonical or control}"
    job_repository.write_gateway_snapshot(snapshot_key, result_payload, now=current)
    evidence = job_repository.record_operational_event(f"control.{canonical or control}", result_payload, status=status, correlation_id=correlation_id, now=current)
    if canonical and canonical != control:
        job_repository.record_operational_event(f"control.{control}", result_payload, status=status, correlation_id=correlation_id, now=current)
    return MetaStrategyOperationalControlResult(
        algorithm_id=ALGORITHM_ID,
        control=canonical or control,
        status=status,
        evidence_id=evidence["eventId"],
        payload=result_payload,
        reason_codes=(
            f"meta_strategy.controls.{str(canonical or control).lower()}.recorded"
            if status == "RECORDED"
            else "meta_strategy.controls.unsupported_control_rejected",
        ),
    )


def record_meta_strategy_test_evidence(
    *,
    job_repository: MetaStrategyJobRepository,
    test_id: str,
    passed: bool,
    command: str,
    evidence: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    return job_repository.record_operational_event(
        "test_result",
        {"testId": test_id, "passed": bool(passed), "command": command, "evidence": evidence},
        status="PASSED" if passed else "FAILED",
        correlation_id=test_id,
        now=now,
    )


def build_meta_strategy_evidence_acceptance_report(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    recovery = dict(snapshot.get("recoveryEvidence") or {})
    dod = dict(snapshot.get("definitionOfDoneEvidence") or {})
    metrics = dict(snapshot.get("metrics") or {})
    inventory = dict(snapshot.get("inventory") or {})
    consistency = dict(inventory.get("consistency") or {})
    paper_stability = dict(snapshot.get("paperStabilityEvidence") or {})
    items = [
        *_acceptance_items("Recovery", recovery),
        *_acceptance_items("Definition of done", dod),
        _health_item("worker_heartbeats_present", "Active worker heartbeats are present.", bool(metrics.get("workerHeartbeat")), metrics.get("workerHeartbeat")),
        _health_item("queue_health", "Queues have no dead-letter backlog.", int(metrics.get("jobDeadLetterCount") or 0) == 0, metrics.get("queueDepth")),
        _health_item("settings_version_present", "Active settings version is available.", bool(metrics.get("settingsVersion")), metrics.get("settingsVersion")),
        _health_item("model_shadow_mode", "ML remains shadow-only unless promotion evidence passes.", bool(dict(snapshot.get("model") or {}).get("mlShadowOnly")), snapshot.get("model")),
        _health_item(
            "reconciliation_state_clear",
            "Reconciliation has no unresolved inventory mismatch.",
            int(metrics.get("inventoryMismatchCount") or 0) == 0
            and (bool(consistency.get("consistent", False)) or consistency.get("derivedSnapshotId") == consistency.get("storedSnapshotId")),
            {"metrics": metrics.get("inventoryMismatchCount"), "consistency": consistency},
        ),
        _health_item("paper_stability_recorded", "Paper-stability evidence is recorded.", bool(paper_stability.get("latest")), paper_stability),
        _health_item("live_disabled", "Live execution remains disabled.", snapshot.get("liveExecutionEnabled") is False, False),
    ]
    blocking = [item for item in items if item["requiredForCompletion"] and item["status"] != "PASSED"]
    return {
        "algorithmId": ALGORITHM_ID,
        "version": "meta_strategy_final_acceptance_evidence_v1",
        "complete": not blocking,
        "paperReady": not blocking,
        "shadowStatus": "ACTIVE",
        "paperStatus": "BLOCKED" if blocking else "READY",
        "counts": {
            "PASSED": sum(1 for item in items if item["status"] == "PASSED"),
            "FAILED": sum(1 for item in items if item["status"] == "FAILED"),
        },
        "blockingStatements": [str(item["statement"]) for item in blocking],
        "passedControls": [item for item in items if item["status"] == "PASSED"],
        "failedControls": blocking,
        "items": items,
        "evidence": {
            "metrics": metrics,
            "settings": snapshot.get("settings"),
            "model": snapshot.get("model"),
            "paperStability": paper_stability,
        },
        "liveExecutionEnabled": False,
        "liveExecutionApprovalRequired": True,
    }


def _control_snapshots(gateway_snapshots: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    return {
        key.removeprefix(META_STRATEGY_CONTROL_SNAPSHOT_PREFIX): dict(value)
        for key, value in gateway_snapshots.items()
        if key.startswith(META_STRATEGY_CONTROL_SNAPSHOT_PREFIX)
    }


def _canonical_control(control: str) -> str | None:
    raw = str(control or "").strip()
    upper = raw.upper()
    if upper in META_STRATEGY_OPERATIONAL_CONTROLS:
        return upper
    return _LEGACY_CONTROL_ALIASES.get(raw.lower())


def _enqueue_control_job(
    job_repository: MetaStrategyJobRepository,
    job_type: str,
    control: str,
    actor: str,
    reason: str,
    correlation_id: str,
    now: datetime,
) -> Any:
    return job_repository.enqueue_job(
        job_type=job_type,
        idempotency_key=f"meta_strategy.control.{control}.{correlation_id}",
        payload={
            "source": "operational_control",
            "control": control,
            "actor": actor,
            "reason": reason,
            "correlationId": correlation_id,
        },
        now=now,
    )


def _control_state(control: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    if control == "PAUSE_NEW_ENTRIES":
        return {"newEntriesPaused": True, "riskReducingExitsAllowed": True}
    if control == "RESUME_NEW_ENTRIES":
        return {"newEntriesPaused": False, "exitOnly": False, "riskReducingExitsAllowed": True}
    if control == "EXIT_ONLY":
        return {"newEntriesPaused": True, "exitOnly": True, "riskReducingExitsAllowed": True}
    if control == "CANCEL_OPEN_META_ORDERS":
        return {"cancelOpenMetaOrdersRequested": True, "newEntriesPausedUntilResolved": True}
    if control == "FLATTEN_META_INVENTORY":
        return {"flattenMetaInventoryRequested": True, "exitOnly": True, "newEntriesPausedUntilFlat": True}
    if control == "DISABLE_ML_INFLUENCE":
        return {"mlInfluenceDisabled": True, "deterministicPathRequired": True}
    if control == "DISABLE_DYNAMIC_OVERLAYS":
        return {"dynamicOverlaysDisabled": True, "immutableBaselineOnly": True}
    if control == "STOP_META_RUNTIME":
        return {"runtimeStopRequested": True, "paperOrdersBlocked": True, "newEntriesPaused": True}
    if control == "disable_strategy":
        return {"strategyId": str(payload.get("strategyId") or payload.get("strategy_id") or ""), "mode": "DISABLED"}
    if control == "disable_symbol":
        return {"symbol": str(payload.get("symbol") or "").upper(), "mode": "DISABLED"}
    if control == "switch_active_strategies_to_shadow":
        return {"activeStrategiesMode": "SHADOW"}
    if control == "stop_execution_continue_decisions":
        return {"executionStopped": True, "decisionsContinue": True}
    if control == "drain_and_stop_workers":
        return {"drainRequested": True, "workersStopAfterDrain": True}
    return {"recorded": True}


def _required_operational_metrics(
    *,
    job_repository: MetaStrategyJobRepository,
    base_metrics: Mapping[str, Any],
    queue_status: Mapping[str, Any],
    inventory: Any,
    inventory_consistency: Mapping[str, Any],
    controls: Mapping[str, Any],
    now: datetime,
) -> dict[str, Any]:
    events = job_repository.operational_events(limit=1000)
    event_counts = _bar_event_counts(events)
    outbox = base_metrics.get("orderOutbox") if isinstance(base_metrics.get("orderOutbox"), Mapping) else {}
    total_outbox = sum(int(dict(item).get("count") or 0) for item in outbox.values() if isinstance(item, Mapping))
    rejected_orders = sum(int(dict(outbox.get(status) or {}).get("count") or 0) for status in ("REJECTED", "BROKER_REJECTED"))
    partial_orders = int(dict(outbox.get("PARTIALLY_FILLED") or {}).get("count") or 0)
    pnl = float(getattr(inventory, "realised_pnl", 0.0)) + float(getattr(inventory, "unrealised_pnl", 0.0))
    return {
        **event_counts,
        "queueLagSeconds": {
            queue: int(dict(status).get("lagSeconds") or 0)
            for queue, status in dict(queue_status.get("queues") or {}).items()
            if isinstance(status, Mapping)
        },
        "decisionCountsBySide": dict(base_metrics.get("decisionCountsByAction") or {"BUY": 0, "SELL": 0, "HOLD": 0, "BLOCKED": 0}),
        "noTradeReasons": dict(base_metrics.get("noTradeReasonCounts") or {}),
        "familyConflicts": dict(base_metrics.get("familyConflictCounts") or {}),
        "mlInferenceFailures": int(base_metrics.get("mlInferenceFailureCount") or 0),
        "oodRate": float(base_metrics.get("oodRate") or 0.0),
        "orderSubmissionLatency": base_metrics.get("brokerSubmissionLatencySeconds") or {"count": 0, "min": None, "max": None, "avg": None},
        "brokerRejectionRate": round(rejected_orders / total_outbox, 6) if total_outbox else 0.0,
        "partialFillRate": round(partial_orders / total_outbox, 6) if total_outbox else 0.0,
        "slippage": _slippage_summary(events),
        "inventoryMismatch": {
            "count": int(base_metrics.get("inventoryMismatchCount") or 0),
            "consistent": bool(inventory_consistency.get("consistent", False)),
        },
        "openRisk": float(getattr(inventory, "allocated_capital", 0.0)),
        "reservedRisk": float(getattr(inventory, "reserved_risk_dollars", 0.0)),
        "realizedPnl": float(getattr(inventory, "realised_pnl", 0.0)),
        "unrealizedPnl": float(getattr(inventory, "unrealised_pnl", 0.0)),
        "dailyDrawdown": max(0.0, -pnl),
        "restartFailures": _failure_count(events, "restart"),
        "reconciliationFailures": _failure_count(events, "reconciliation"),
        "runtimeStopRequested": bool(dict(controls.get("STOP_META_RUNTIME") or {}).get("state", {}).get("runtimeStopRequested")),
        "observedAt": now.isoformat(),
    }


def _bar_event_counts(events: tuple[dict[str, Any], ...]) -> dict[str, int]:
    finalized = 0
    duplicate = 0
    missing = 0
    for event in events:
        payload = dict(event.get("payload") or {})
        if event.get("eventType") == "finalised_candle_enqueued":
            if payload.get("duplicate") is True:
                duplicate += 1
            else:
                finalized += 1
        if event.get("eventType") == "finalised_candle_data_quality":
            status = str(payload.get("status") or event.get("status") or "").upper()
            if status not in {"OK", "VALID", "RECORDED"}:
                missing += 1
    return {
        "finalizedBarCount": finalized,
        "duplicateBarCount": duplicate,
        "missingBarCount": missing,
    }


def _slippage_summary(events: tuple[dict[str, Any], ...]) -> dict[str, float | int | None]:
    values = []
    for event in events:
        payload = dict(event.get("payload") or {})
        for key in ("slippageBps", "slippage_bps", "slippage"):
            if payload.get(key) is not None:
                values.append(float(payload[key]))
                break
    if not values:
        return {"count": 0, "min": None, "max": None, "avg": None}
    return {"count": len(values), "min": min(values), "max": max(values), "avg": sum(values) / len(values)}


def _failure_count(events: tuple[dict[str, Any], ...], fragment: str) -> int:
    return sum(1 for event in events if fragment in str(event.get("eventType") or "").lower() and str(event.get("status") or "").upper() in {"FAILED", "ERROR", "REJECTED"})


def _algorithm_readiness(base_metrics: Mapping[str, Any], inventory_consistency: Mapping[str, Any], controls: Mapping[str, Any]) -> dict[str, Any]:
    runtime_stopped = bool(dict(controls.get("STOP_META_RUNTIME") or {}).get("state", {}).get("runtimeStopRequested"))
    dead_letters = int(base_metrics.get("jobDeadLetterCount") or 0)
    mismatches = int(base_metrics.get("inventoryMismatchCount") or 0)
    consistent = bool(inventory_consistency.get("consistent", False)) or inventory_consistency.get("derivedSnapshotId") == inventory_consistency.get("storedSnapshotId")
    ready = not runtime_stopped and dead_letters == 0 and mismatches == 0 and consistent
    return {
        "algorithmId": ALGORITHM_ID,
        "readyToTrade": ready,
        "paperOrdersBlocked": not ready,
        "apiProcessHealthyDoesNotImplyReadiness": True,
        "reasonCodes": (
            "meta_strategy.readiness.ready"
            if ready
            else "meta_strategy.readiness.algorithm_specific_blocking_condition"
        ),
    }


def _paper_broker_connectivity(gateway_snapshots: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    status = dict(gateway_snapshots.get("paper_broker_connectivity") or {})
    if not status:
        return {"status": "UNKNOWN", "configured": False, "reasonCodes": ("meta_strategy.observability.paper_broker_connectivity_not_reported",)}
    return status


def _strategy_counts() -> dict[str, int]:
    return {
        "active": sum(1 for item in ALL_META_STRATEGY_STRATEGIES if item.mode == "ACTIVE"),
        "shadow": sum(1 for item in ALL_META_STRATEGY_STRATEGIES if item.mode == "SHADOW"),
        "disabled": sum(1 for item in ALL_META_STRATEGY_STRATEGIES if item.mode == "DISABLED"),
    }


def _test_evidence(job_repository: MetaStrategyJobRepository, required_ids: tuple[str, ...]) -> dict[str, Any]:
    events = job_repository.operational_events(event_type="test_result", limit=1000)
    by_id: dict[str, dict[str, Any]] = {}
    for event in reversed(events):
        payload = dict(event.get("payload") or {})
        test_id = str(payload.get("testId") or "")
        if test_id in required_ids:
            by_id[test_id] = {
                "passed": payload.get("passed") is True,
                "eventId": event["eventId"],
                "command": payload.get("command"),
                "evidence": payload.get("evidence"),
                "recordedAt": event["createdAt"],
            }
    return {
        test_id: by_id.get(test_id, {"passed": False, "eventId": None, "reason": "missing_evidence"})
        for test_id in required_ids
    }


def _paper_stability_evidence(job_repository: MetaStrategyJobRepository) -> dict[str, Any]:
    events = [
        event
        for event in job_repository.operational_events(event_type="test_result", limit=1000)
        if str(dict(event.get("payload") or {}).get("testId") or "") in {"paper_stability_reconciliation_passes", "shadow_stability_passes"}
        and dict(event.get("payload") or {}).get("passed") is True
    ]
    return {"latest": events[0] if events else None, "count": len(events)}


def _acceptance_items(category: str, evidence: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    return tuple(
        _health_item(str(test_id), str(test_id).replace("_", " ").capitalize(), bool(dict(item).get("passed")), item, category=category)
        for test_id, item in evidence.items()
    )


def _health_item(item_id: str, statement: str, passed: bool, evidence: Any, *, category: str = "Operational evidence") -> dict[str, Any]:
    return {
        "itemId": item_id,
        "category": category,
        "statement": statement,
        "status": "PASSED" if passed else "FAILED",
        "evidence": evidence,
        "requiredForCompletion": True,
    }


def _plain_inventory(value: Any) -> dict[str, Any]:
    if hasattr(value, "__dataclass_fields__"):
        data = {}
        for key in value.__dataclass_fields__:
            item = getattr(value, key)
            if isinstance(item, tuple):
                data[key] = [dict(getattr(row, "__dict__", row)) for row in item]
            else:
                data[key] = item
        return data
    return dict(value)


def _plain_model(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return dict(value)


def _optional_str(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)


__all__ = [
    "META_STRATEGY_FINAL_DOD_IDS",
    "META_STRATEGY_OBSERVABILITY_VERSION",
    "META_STRATEGY_OPERATIONAL_CONTROLS",
    "META_STRATEGY_RECOVERY_TEST_IDS",
    "MetaStrategyOperationalControlResult",
    "apply_meta_strategy_operational_control",
    "build_meta_strategy_evidence_acceptance_report",
    "build_meta_strategy_observability_snapshot",
    "record_meta_strategy_test_evidence",
]
