from __future__ import annotations

import hashlib
import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from time import perf_counter
from typing import Any

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.market_clock import read_market_clock_snapshot
from backend.app.algorithms.meta_strategy.observability import (
    META_STRATEGY_AUTOMATIC_PAPER_CONTROL_KEY,
    build_meta_strategy_evidence_acceptance_report,
    build_meta_strategy_observability_snapshot,
)
from backend.app.algorithms.meta_strategy.ownership import META_STRATEGY_DEFAULT_CAPITAL_PARTITION
from backend.app.algorithms.meta_strategy.paper_readiness import (
    build_meta_strategy_paper_entry_readiness_prerequisites,
    build_meta_strategy_paper_readiness_acceptance_report,
)
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.algorithms.meta_strategy.settings import MetaStrategySettingsStore
from backend.app.algorithms.meta_strategy.versions import (
    META_STRATEGY_FEATURE_SCHEMA_VERSION,
    META_STRATEGY_MODEL_VERSION,
    META_STRATEGY_STRATEGY_CATALOG_VERSION,
)
from backend.app.domain.models import Signal
from backend.app.execution import PaperOrderGateway, PaperOrderGatewayResult
from backend.app.gates import GlobalGateResponse, GlobalOrderProposal, apply_global_gate_response


META_STRATEGY_PAPER_EXECUTION_VERSION = "meta_strategy_paper_execution_v1"
META_STRATEGY_EXECUTION_GUARD_VERSION = "meta_strategy_execution_guard_v1"
META_STRATEGY_MAX_INTENT_AGE_SECONDS = 300
META_STRATEGY_MAX_DECISION_AGE_SECONDS = 300
META_STRATEGY_MAX_QUOTE_AGE_SECONDS = 60
META_STRATEGY_MAX_GLOBAL_RISK_AGE_SECONDS = 30
_ACTIVE_OUTBOX_STATUSES = frozenset({"PENDING", "RETRY", "SUBMITTING", "SUBMITTED", "ACKNOWLEDGED", "OPEN", "PARTIALLY_FILLED", "CANCEL_PENDING", "REPLACED", "RECONCILIATION_REQUIRED"})


@dataclass(frozen=True)
class MetaStrategyExecutionGuardResult:
    algorithm_id: str
    capital_partition_id: str
    order_intent_id: str
    intent_type: str
    allowed: bool
    policy: str
    evaluated_at: str
    reason_codes: tuple[str, ...]
    evidence: dict[str, Any]
    guard_version: str = META_STRATEGY_EXECUTION_GUARD_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "guardVersion": self.guard_version,
            "algorithmId": self.algorithm_id,
            "capitalPartitionId": self.capital_partition_id,
            "orderIntentId": self.order_intent_id,
            "intentType": self.intent_type,
            "allowed": self.allowed,
            "policy": self.policy,
            "evaluatedAt": self.evaluated_at,
            "reasonCodes": self.reason_codes,
            "evidence": self.evidence,
        }


class MetaStrategyPaperOrderSubmissionWorker:
    def __init__(
        self,
        *,
        repository: MetaStrategyJobRepository,
        inventory_repository: MetaStrategySqliteRepository,
        paper_gateway: PaperOrderGateway,
        global_risk_source: Any | None = None,
        settings_store: MetaStrategySettingsStore | None = None,
        runtime_readiness_source: Any | None = None,
        readiness_report_source: Any | None = None,
        market_clock_source: Any | None = None,
        worker_id: str = "meta_strategy.paper_order_submission_worker",
        lease_seconds: int = 300,
    ) -> None:
        self.repository = repository
        self.inventory_repository = inventory_repository
        self.paper_gateway = paper_gateway
        self.global_risk_source = global_risk_source
        self.settings_store = settings_store
        self.runtime_readiness_source = runtime_readiness_source
        self.readiness_report_source = readiness_report_source
        self.market_clock_source = market_clock_source
        self.worker_id = worker_id
        self.lease_seconds = lease_seconds

    def run_once(self, *, now: datetime | None = None) -> dict[str, Any] | None:
        evaluated_at = _as_utc(now or datetime.now(UTC))
        outbox = self.repository.claim_next_execution_outbox(worker_id=self.worker_id, lease_seconds=self.lease_seconds, now=evaluated_at)
        if outbox is None:
            return None
        return submit_meta_strategy_outbox_record(
            repository=self.repository,
            inventory_repository=self.inventory_repository,
            paper_gateway=self.paper_gateway,
            outbox_record=outbox,
            global_risk_source=self.global_risk_source,
            settings_store=self.settings_store,
            runtime_readiness_source=self.runtime_readiness_source,
            readiness_report_source=self.readiness_report_source,
            market_clock_source=self.market_clock_source,
            evaluated_at=evaluated_at,
        )


class MetaStrategyPaperOrderReconciliationWorker:
    def __init__(
        self,
        *,
        repository: MetaStrategyJobRepository,
        inventory_repository: MetaStrategySqliteRepository,
        paper_gateway: PaperOrderGateway,
        worker_id: str = "meta_strategy.paper_order_reconciliation_worker",
    ) -> None:
        self.repository = repository
        self.inventory_repository = inventory_repository
        self.paper_gateway = paper_gateway
        self.worker_id = worker_id

    def run_once(self, *, now: datetime | None = None) -> dict[str, Any]:
        return reconcile_meta_strategy_paper_orders(
            repository=self.repository,
            inventory_repository=self.inventory_repository,
            paper_gateway=self.paper_gateway,
            reconciled_at=_as_utc(now or datetime.now(UTC)),
        )


class MetaStrategyStaleOrderCancellationWorker:
    def __init__(
        self,
        *,
        repository: MetaStrategyJobRepository,
        inventory_repository: MetaStrategySqliteRepository,
        paper_gateway: PaperOrderGateway,
        worker_id: str = "meta_strategy.stale_order_cancellation_worker",
        stale_seconds: int = 300,
    ) -> None:
        self.repository = repository
        self.inventory_repository = inventory_repository
        self.paper_gateway = paper_gateway
        self.worker_id = worker_id
        self.stale_seconds = stale_seconds

    def run_once(self, *, now: datetime | None = None) -> dict[str, Any]:
        evaluated_at = _as_utc(now or datetime.now(UTC))
        return cancel_stale_meta_strategy_paper_orders(
            repository=self.repository,
            inventory_repository=self.inventory_repository,
            paper_gateway=self.paper_gateway,
            evaluated_at=evaluated_at,
            stale_seconds=self.stale_seconds,
        )


def submit_meta_strategy_outbox_record(
    *,
    repository: MetaStrategyJobRepository,
    inventory_repository: MetaStrategySqliteRepository,
    paper_gateway: PaperOrderGateway,
    outbox_record: Mapping[str, Any],
    global_risk_source: Any | None = None,
    settings_store: MetaStrategySettingsStore | None = None,
    runtime_readiness_source: Any | None = None,
    readiness_report_source: Any | None = None,
    market_clock_source: Any | None = None,
    evaluated_at: datetime,
) -> dict[str, Any]:
    payload = _outbox_payload(outbox_record)
    outbox_id = str(outbox_record["outboxId"])
    if str(outbox_record.get("algorithmId")) != ALGORITHM_ID or str(payload.get("algorithmId")) != ALGORITHM_ID:
        return _reject(repository, inventory_repository, outbox_id, payload, "meta_strategy.execution.foreign_outbox_rejected", evaluated_at)
    if str(payload.get("mode") or payload.get("executionMode") or "PAPER").upper() != "PAPER":
        return _reject(repository, inventory_repository, outbox_id, payload, "meta_strategy.execution.paper_mode_required", evaluated_at)
    if not str(payload.get("settingsVersion") or outbox_record.get("settingsVersion") or ""):
        return _reject(repository, inventory_repository, outbox_id, payload, "meta_strategy.execution.settings_version_required", evaluated_at)

    client_order_id = deterministic_meta_strategy_client_order_id(payload)
    payload = {**payload, "clientOrderId": client_order_id}
    proposal = build_meta_strategy_global_order_proposal(payload, evaluated_at=evaluated_at)
    proposal_intent = str(proposal.intent)
    if proposal_intent != "new_entry":
        payload = _with_reason_codes(payload, "meta_strategy.paper_control.protective_exit_allowed")
    response = _global_risk_response(global_risk_source, proposal, evaluated_at=evaluated_at)
    application = apply_global_gate_response(proposal, response)
    guard = evaluate_meta_strategy_execution_guard(
        repository=repository,
        inventory_repository=inventory_repository,
        paper_gateway=paper_gateway,
        proposal=proposal,
        payload=payload,
        global_response=response,
        global_application=application,
        settings_store=settings_store,
        runtime_readiness_source=runtime_readiness_source,
        readiness_report_source=readiness_report_source,
        market_clock_source=market_clock_source,
        evaluated_at=evaluated_at,
    )
    if not guard.allowed:
        return _reject(
            repository,
            inventory_repository,
            outbox_id,
            {**payload, "reservedRiskDollars": 0.0, "executionGuard": guard.to_dict()},
            guard.reason_codes,
            evaluated_at,
        )

    requested_reserved = _first_number(payload, "reservedRiskDollars", "reserved_risk_dollars", "riskDollars", "risk_dollars")
    reserved = min(
        requested_reserved if requested_reserved is not None else proposal.plannedRiskDollars,
        application.maximumAdditionalRiskDollars,
    )
    atomic_persistence = dict(payload.get("atomicPersistence") or payload.get("atomic_persistence") or {})
    if proposal_intent == "new_entry" and not bool(atomic_persistence.get("riskReservationPersisted")):
        return _reject(
            repository,
            inventory_repository,
            outbox_id,
            {**payload, "reservedRiskDollars": 0.0},
            "meta_strategy.atomic_persistence.risk_reservation_required_before_submission",
            evaluated_at,
        )
    intent_payload = {
        **payload,
        **_identity_envelope(payload),
        "quantity": application.globallyAllowedQuantity,
        "reservedRiskDollars": reserved,
        "clientOrderId": client_order_id,
        "globalApplication": application.model_dump(mode="json"),
        "executionGuard": guard.to_dict(),
        "timestamp": evaluated_at.isoformat(),
    }
    if proposal_intent == "new_entry":
        inventory_repository.adjust_reserved_risk(intent_payload, target_reserved_risk=reserved, reason="EXECUTION_GUARD_ADJUSTMENT")
    else:
        inventory_repository.record_order_intent(intent_payload)
    repository.update_execution_outbox(
        outbox_id,
        status="SUBMITTING",
        payload=_with_reason_codes(intent_payload, "meta_strategy.execution.order_intent_persisted_before_broker"),
        client_order_id=intent_payload["clientOrderId"],
        now=evaluated_at,
    )
    submission_started = perf_counter()
    try:
        gateway_result = paper_gateway.submit(
            proposal=proposal,
            global_application=application,
            local_gate_passed=guard.allowed,
            mode="automatic",
            evaluated_at=evaluated_at,
        )
        order_submission_time_ms = int((perf_counter() - submission_started) * 1000)
    except TimeoutError as exc:
        order_submission_time_ms = int((perf_counter() - submission_started) * 1000)
        repository.update_execution_outbox(
            outbox_id,
            status="RECONCILIATION_REQUIRED",
            payload={
                **intent_payload,
                "reasonCodes": ["meta_strategy.execution.broker_timeout_no_fill_assumed"],
                "latencyMeasurements": {**dict(intent_payload.get("latencyMeasurements") or {}), "orderSubmissionTimeMs": order_submission_time_ms},
            },
            client_order_id=intent_payload["clientOrderId"],
            retryable=False,
            error_category="TimeoutError",
            error_details=str(exc),
            now=evaluated_at,
        )
        return {
            "status": "RECONCILIATION_REQUIRED",
            "submitted": False,
            "latencyMeasurements": {"orderSubmissionTimeMs": order_submission_time_ms},
        "reasonCodes": ("meta_strategy.execution.broker_timeout_no_fill_assumed",),
        }
    except Exception as exc:
        order_submission_time_ms = int((perf_counter() - submission_started) * 1000)
        repository.update_execution_outbox(
            outbox_id,
            status="RECONCILIATION_REQUIRED",
            payload={
                **intent_payload,
                "reasonCodes": ["meta_strategy.execution.unknown_broker_outcome_reconciliation_required"],
                "latencyMeasurements": {**dict(intent_payload.get("latencyMeasurements") or {}), "orderSubmissionTimeMs": order_submission_time_ms},
            },
            client_order_id=intent_payload["clientOrderId"],
            retryable=False,
            error_category=type(exc).__name__,
            error_details=str(exc),
            now=evaluated_at,
        )
        return {
            "status": "RECONCILIATION_REQUIRED",
            "submitted": False,
            "latencyMeasurements": {"orderSubmissionTimeMs": order_submission_time_ms},
            "reasonCodes": ("meta_strategy.execution.unknown_broker_outcome_reconciliation_required",),
        }

    result_payload = gateway_result.model_dump(mode="json")
    status = _outbox_status_from_gateway(gateway_result)
    broker_order_id = gateway_result.brokerAck.brokerOrderId if gateway_result.brokerAck else None
    inventory_repository.record_order_status(
        {
            **intent_payload,
            "brokerOrderId": broker_order_id or "",
            "orderStatus": "REJECTED" if status == "REJECTED" else "ACCEPTED",
            "status": "REJECTED" if status == "REJECTED" else "ACCEPTED",
            "timestamp": evaluated_at.isoformat(),
        }
    )
    _enqueue_position_management_after_execution(repository, intent_payload, trigger=f"order_status_{status.lower()}", now=evaluated_at)
    repository.update_execution_outbox(
        outbox_id,
        status=status,
        payload={
            **intent_payload,
            "gatewayResult": result_payload,
            "reasonCodes": list(dict.fromkeys((*tuple(intent_payload.get("reasonCodes") or ()), *tuple(gateway_result.reasonCodes)))),
            "latencyMeasurements": {**dict(intent_payload.get("latencyMeasurements") or {}), "orderSubmissionTimeMs": order_submission_time_ms},
        },
        client_order_id=gateway_result.clientOrderId,
        broker_order_id=broker_order_id,
        now=evaluated_at,
    )
    if gateway_result.fill and gateway_result.fill.filledQuantity > 0:
        _apply_fill_event(repository, inventory_repository, outbox_id, _fill_event_with_order_context(intent_payload, gateway_result.fill.model_dump(mode="json")), evaluated_at=evaluated_at)
    return {
        "status": status,
        "submitted": gateway_result.submitted,
        "latencyMeasurements": {"orderSubmissionTimeMs": order_submission_time_ms},
        "reasonCodes": tuple(gateway_result.reasonCodes),
    }


def reconcile_meta_strategy_paper_orders(
    *,
    repository: MetaStrategyJobRepository,
    inventory_repository: MetaStrategySqliteRepository,
    paper_gateway: PaperOrderGateway,
    reconciled_at: datetime,
) -> dict[str, Any]:
    processed = 0
    duplicate = 0
    quarantined = 0
    events = _broker_events(paper_gateway)
    if not events:
        paper_gateway.recover_from_restart(evaluated_at=reconciled_at)
    for event in events:
        event = dict(event)
        outbox = _outbox_for_event(repository, event)
        if outbox is not None:
            event = _event_with_known_outbox_ownership(event, outbox)
        recorded = repository.record_broker_event(event, now=reconciled_at)
        if recorded["status"] == "QUARANTINED":
            inventory_repository.record_foreign_ownership_quarantine(
                {**event, "timestamp": str(event.get("timestamp") or reconciled_at.isoformat())},
                reason="BROKER_EVENT_FOREIGN_ALGORITHM",
            )
            quarantined += 1
            continue
        if recorded["duplicate"]:
            duplicate += 1
            continue
        processed += 1
        if outbox is None:
            quarantined += 1
            repository.record_reconciliation_evidence(
                "BROKER_ORDER_MISSING_LOCALLY",
                event,
                client_order_id=str(event.get("clientOrderId") or ""),
                broker_order_id=str(event.get("brokerOrderId") or ""),
                order_intent_id=str(event.get("orderIntentId") or ""),
                status="QUARANTINED",
                now=reconciled_at,
            )
            continue
        event_outcome = _apply_broker_event(repository, inventory_repository, outbox, event, reconciled_at=reconciled_at)
        if event_outcome == "QUARANTINED":
            quarantined += 1
        elif event_outcome == "DUPLICATE_FILL_IGNORED":
            duplicate += 1
    recovery = paper_gateway.recover_from_restart(evaluated_at=reconciled_at)
    if recovery.get("orphanPositionsDetected"):
        quarantined += len(recovery["orphanPositionsDetected"])
        repository.record_reconciliation_evidence("UNEXPECTED_PAPER_POSITION", recovery, status="QUARANTINED", now=reconciled_at)
    return {
        "status": "OK",
        "processed": processed,
        "duplicates": duplicate,
        "quarantined": quarantined,
        "reasonCodes": ("meta_strategy.execution.reconciliation_completed",),
    }


def cancel_stale_meta_strategy_paper_orders(
    *,
    repository: MetaStrategyJobRepository,
    inventory_repository: MetaStrategySqliteRepository,
    paper_gateway: PaperOrderGateway,
    evaluated_at: datetime,
    stale_seconds: int = 300,
) -> dict[str, Any]:
    reconciliation = reconcile_meta_strategy_paper_orders(
        repository=repository,
        inventory_repository=inventory_repository,
        paper_gateway=paper_gateway,
        reconciled_at=evaluated_at,
    )
    stale = repository.stale_execution_outbox_records(now=evaluated_at, stale_seconds=stale_seconds)
    cancelled = 0
    for outbox in stale:
        client_order_id = str(outbox.get("clientOrderId") or "")
        if not client_order_id:
            continue
        payload = dict(outbox["payload"])
        replacement = _try_replace_stale_order(paper_gateway, outbox, payload)
        if replacement is not None:
            replaced_payload = {
                **payload,
                "replacementCount": int(payload.get("replacementCount") or 0) + 1,
                "replacementEvent": replacement,
                "reasonCodes": ["meta_strategy.execution.stale_order_replaced"],
            }
            repository.update_execution_outbox(
                outbox["outboxId"],
                status="REPLACED",
                payload=replaced_payload,
                client_order_id=str(replacement.get("clientOrderId") or client_order_id),
                broker_order_id=str(replacement.get("brokerOrderId") or outbox.get("brokerOrderId") or ""),
                now=evaluated_at,
            )
            inventory_repository.record_order_status({**replaced_payload, "clientOrderId": client_order_id, "orderStatus": "REPLACED", "status": "REPLACED", "timestamp": evaluated_at.isoformat()})
            repository.record_reconciliation_evidence("STALE_ORDER_REPLACEMENT", replaced_payload, client_order_id=client_order_id, order_intent_id=str(outbox["orderIntentId"]), status="REPLACED", now=evaluated_at)
            continue
        pending_payload = {**payload, "reasonCodes": ["meta_strategy.execution.stale_order_cancel_pending"]}
        repository.update_execution_outbox(outbox["outboxId"], status="CANCEL_PENDING", payload=pending_payload, now=evaluated_at)
        repository.record_reconciliation_evidence("STALE_ORDER_CANCEL_PENDING", pending_payload, client_order_id=client_order_id, order_intent_id=str(outbox["orderIntentId"]), status="CANCEL_PENDING", now=evaluated_at)
        ok = paper_gateway.broker.cancel_order(client_order_id)
        status = "CANCELLED" if ok else "RECONCILIATION_REQUIRED"
        cancelled_payload = {**payload, "reasonCodes": ["meta_strategy.execution.stale_order_cancelled" if ok else "meta_strategy.execution.stale_order_cancel_unknown"]}
        repository.update_execution_outbox(outbox["outboxId"], status=status, payload=cancelled_payload, now=evaluated_at)
        inventory_repository.record_order_status({**cancelled_payload, "clientOrderId": client_order_id, "orderStatus": "CANCELLED" if ok else "UNKNOWN", "status": "CANCELLED" if ok else "UNKNOWN", "timestamp": evaluated_at.isoformat()})
        repository.record_reconciliation_evidence("STALE_ORDER_CANCELLATION", cancelled_payload, client_order_id=client_order_id, order_intent_id=str(outbox["orderIntentId"]), status=status, now=evaluated_at)
        cancelled += 1 if ok else 0
    return {"status": "OK", "cancelled": cancelled, "reconciliation": reconciliation, "reasonCodes": ("meta_strategy.execution.stale_order_cancellation_completed",)}


def build_meta_strategy_global_order_proposal(payload: Mapping[str, Any], *, evaluated_at: datetime) -> GlobalOrderProposal:
    envelope = _identity_envelope(payload)
    side = _signal(payload.get("side"))
    quantity_value = payload.get("quantity")
    quantity = max(0, int(quantity_value) if quantity_value is not None else 0)
    settings_version = envelope["settingsVersion"]
    effective_settings_hash = envelope["effectiveSettingsHash"]
    proposed_at = _as_utc(_parse_datetime(payload.get("createdAt") or payload.get("timestamp"), evaluated_at))
    price = _positive_float(payload.get("limitPrice") or payload.get("entryPrice") or payload.get("price") or 0.01)
    stop = _optional_positive(payload.get("stopPrice"))
    target = _optional_positive(payload.get("targetPrice"))
    client_order_id = str(payload.get("clientOrderId") or deterministic_meta_strategy_client_order_id(payload))
    order_type = _order_type(payload)
    time_in_force = str(payload.get("timeInForce") or payload.get("time_in_force") or payload.get("timeInForcePolicy") or "DAY").upper()
    stop_limit_price = _optional_positive(payload.get("stopLimitPrice") or payload.get("stop_limit_price"))
    replacement_count = int(payload.get("replacementCount") or payload.get("replacement_count") or 0)
    maximum_order_age_seconds = int(payload.get("maximumOrderAgeSeconds") or payload.get("maxOrderAgeSeconds") or payload.get("staleAfterSeconds") or 300)
    maximum_replacement_count = int(payload.get("maximumReplacementCount") or payload.get("maxReplacementCount") or 0)
    proposal_hash = _hash_json(
        {
            "algorithmId": ALGORITHM_ID,
            "decisionId": payload.get("decisionId"),
            "orderIntentId": payload.get("orderIntentId"),
            "settingsVersion": settings_version,
            "effectiveSettingsHash": effective_settings_hash,
            "strategyCatalogVersion": envelope["strategyCatalogVersion"],
            "featureSchemaVersion": envelope["featureSchemaVersion"],
            "modelVersion": envelope["modelVersion"],
            "quantity": quantity,
            "price": price,
            "stop": stop,
            "target": target,
        }
    )
    return GlobalOrderProposal(
        algorithmId=ALGORITHM_ID,
        capitalPartitionId=envelope["capitalPartitionId"],
        decisionId=envelope["decisionId"],
        orderIntentId=envelope["orderIntentId"],
        intent=_proposal_intent(payload),
        symbol=str(payload.get("symbol") or "UNKNOWN").upper(),
        side=side,
        quantity=quantity,
        triggerPrice=price,
        limitPrice=price,
        stopPrice=stop,
        targetPrice=target,
        plannedRiskDollars=(
            _first_number(payload, "reservedRiskDollars", "reserved_risk_dollars", "riskDollars", "risk_dollars")
            if _first_number(payload, "reservedRiskDollars", "reserved_risk_dollars", "riskDollars", "risk_dollars") is not None
            else 0.0
        ),
        settingsSnapshot={
            "settingsVersion": settings_version,
            "effectiveSettingsHash": effective_settings_hash,
            "strategyCatalogVersion": envelope["strategyCatalogVersion"],
            "featureSchemaVersion": envelope["featureSchemaVersion"],
            "modelVersion": envelope["modelVersion"],
            "correlationId": envelope["correlationId"],
            "paperOnly": True,
            "clientOrderId": client_order_id,
            "orderType": order_type,
            "timeInForce": time_in_force,
            "stopLimitPrice": stop_limit_price,
            "cancelAndReplaceEnabled": bool(payload.get("cancelAndReplaceEnabled") or payload.get("cancel_and_replace_enabled") or False),
            "maximumOrderAgeSeconds": maximum_order_age_seconds,
            "maximumReplacementCount": maximum_replacement_count,
            "replacementCount": replacement_count,
            "protectiveExitEscalationPolicy": str(payload.get("protectiveExitEscalationPolicy") or payload.get("protective_exit_escalation_policy") or "CANCEL_AND_MARKETABLE_LIMIT"),
        },
        entryFormula={"kind": "bracket_limit", "orderType": order_type, "timeInForce": time_in_force},
        stopFormula={"stopPrice": stop, "stopLimitPrice": stop_limit_price, "policy": "protective_stop"},
        targetFormula={"targetPrice": target, "orderType": "LIMIT", "policy": "limit_target"},
        strategyStateHash=proposal_hash,
        proposedAt=proposed_at,
        sessionDate=proposed_at.date(),
        configurationHash=proposal_hash,
    )


def deterministic_meta_strategy_client_order_id(payload: Mapping[str, Any]) -> str:
    partition = str(payload.get("capitalPartitionId") or payload.get("capital_partition_id") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION)
    stable = {
        "algorithmId": ALGORITHM_ID,
        "capitalPartitionId": partition,
        "decisionId": payload.get("decisionId"),
        "orderIntentId": payload.get("orderIntentId") or payload.get("order_intent_id"),
        "symbol": str(payload.get("symbol") or "").upper(),
        "side": str(payload.get("side") or "").upper(),
    }
    partition_slug = "".join(ch if ch.isalnum() else "-" for ch in partition.lower()).strip("-")
    return "meta-strategy-" + partition_slug[:24] + "-" + hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:18]


def _apply_broker_event(
    repository: MetaStrategyJobRepository,
    inventory_repository: MetaStrategySqliteRepository,
    outbox: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    reconciled_at: datetime,
) -> str:
    status = str(event.get("status") or "").upper()
    if status in {"ACCEPTED", "PENDING"}:
        mapped = "ACKNOWLEDGED"
    elif status == "OPEN":
        mapped = "OPEN"
    elif status in {"PARTIAL_FILL", "PARTIALLY_FILLED"}:
        mapped = "PARTIALLY_FILLED"
    elif status in {"FILL", "FILLED"}:
        mapped = "FILLED"
    elif status in {"CANCELED", "CANCELLED"}:
        mapped = "CANCELLED"
    elif status == "EXPIRED":
        mapped = "EXPIRED"
    elif status == "REJECTED":
        mapped = "REJECTED"
    elif status == "REPLACED":
        mapped = "REPLACED"
    else:
        mapped = "RECONCILIATION_REQUIRED"
    ownership_reason = _broker_event_ownership_conflict(outbox, event)
    if ownership_reason is not None:
        quarantined_payload = {
            **dict(outbox["payload"]),
            **dict(event),
            "algorithmId": event.get("algorithmId") or event.get("algorithm_id") or ALGORITHM_ID,
            "capitalPartitionId": event.get("capitalPartitionId") or event.get("capital_partition_id") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
            "orderIntentId": str(event.get("orderIntentId") or outbox.get("orderIntentId") or outbox["payload"].get("orderIntentId") or ""),
            "clientOrderId": str(event.get("clientOrderId") or outbox.get("clientOrderId") or outbox["payload"].get("clientOrderId") or ""),
            "brokerOrderId": str(event.get("brokerOrderId") or outbox.get("brokerOrderId") or outbox["payload"].get("brokerOrderId") or ""),
            "brokerFillId": str(event.get("brokerFillId") or event.get("fillId") or event.get("brokerEventId") or ""),
            "timestamp": reconciled_at.isoformat(),
        }
        inventory_repository.record_foreign_ownership_quarantine(quarantined_payload, reason=ownership_reason)
        repository.record_reconciliation_evidence(
            "BROKER_EVENT_OWNERSHIP_CONFLICT",
            quarantined_payload,
            client_order_id=str(quarantined_payload.get("clientOrderId") or ""),
            broker_order_id=str(quarantined_payload.get("brokerOrderId") or ""),
            order_intent_id=str(quarantined_payload.get("orderIntentId") or ""),
            status="QUARANTINED",
            now=reconciled_at,
        )
        return "QUARANTINED"
    fill_event_payload = _fill_event_with_order_context(outbox["payload"], event)
    if mapped in {"PARTIALLY_FILLED", "FILLED"}:
        _, fill_rejection_reason = _meta_strategy_fill_inventory_payload(fill_event_payload, evaluated_at=reconciled_at)
        if fill_rejection_reason is not None:
            _quarantine_rejected_fill(
                repository,
                inventory_repository,
                fill_event_payload,
                reason=fill_rejection_reason,
                evaluated_at=reconciled_at,
            )
            repository.record_reconciliation_evidence(
                "BROKER_EVENT_FILL_REJECTED",
                fill_event_payload,
                client_order_id=str(fill_event_payload.get("clientOrderId") or ""),
                broker_order_id=str(fill_event_payload.get("brokerOrderId") or ""),
                order_intent_id=str(fill_event_payload.get("orderIntentId") or ""),
                status="QUARANTINED",
                now=reconciled_at,
            )
            return "QUARANTINED"
    payload = {**outbox["payload"], **_identity_envelope(outbox["payload"]), "brokerEvent": dict(event), "reasonCodes": [f"meta_strategy.execution.broker_event_{mapped.lower()}"]}
    repository.update_execution_outbox(
        str(outbox["outboxId"]),
        status=mapped,
        payload=payload,
        client_order_id=str(event.get("clientOrderId") or outbox.get("clientOrderId") or ""),
        broker_order_id=str(event.get("brokerOrderId") or outbox.get("brokerOrderId") or ""),
        now=reconciled_at,
    )
    fill_outcome = None
    if mapped in {"PARTIALLY_FILLED", "FILLED"}:
        fill_outcome = _apply_fill_event(repository, inventory_repository, str(outbox["outboxId"]), fill_event_payload, evaluated_at=reconciled_at)
    if mapped in {"CANCELLED", "EXPIRED", "REJECTED"}:
        inventory_repository.record_order_status(
            {
                **payload,
                **_identity_envelope(payload),
                "orderStatus": mapped,
                "status": mapped,
                "clientOrderId": str(event.get("clientOrderId") or outbox.get("clientOrderId") or ""),
                "brokerOrderId": str(event.get("brokerOrderId") or outbox.get("brokerOrderId") or ""),
                "timestamp": reconciled_at.isoformat(),
            }
        )
    if fill_outcome != "DUPLICATE_FILL_IGNORED" and mapped in {"PARTIALLY_FILLED", "FILLED", "CANCELLED", "EXPIRED", "REJECTED", "REPLACED", "RECONCILIATION_REQUIRED"}:
        _enqueue_position_management_after_execution(repository, payload, trigger=f"broker_event_{mapped.lower()}", now=reconciled_at)
    repository.record_reconciliation_evidence("BROKER_EVENT_RECONCILED", payload, client_order_id=str(event.get("clientOrderId") or ""), broker_order_id=str(event.get("brokerOrderId") or ""), order_intent_id=str(event.get("orderIntentId") or ""), status=mapped, now=reconciled_at)
    return fill_outcome or mapped


def _broker_event_ownership_conflict(outbox: Mapping[str, Any], event: Mapping[str, Any]) -> str | None:
    payload = dict(outbox.get("payload") or {})
    observed_algorithm_id = event.get("algorithmId") or event.get("algorithm_id")
    if observed_algorithm_id is not None and str(observed_algorithm_id) != ALGORITHM_ID:
        return "BROKER_EVENT_FOREIGN_ALGORITHM"
    observed_partition_id = event.get("capitalPartitionId") or event.get("capital_partition_id")
    outbox_partition_id = str(outbox.get("capitalPartitionId") or payload.get("capitalPartitionId") or payload.get("capital_partition_id") or "")
    if observed_partition_id is not None and str(observed_partition_id) != outbox_partition_id:
        return "BROKER_EVENT_FOREIGN_PARTITION"
    observed_order_intent_id = str(event.get("orderIntentId") or "")
    if observed_order_intent_id and observed_order_intent_id != str(outbox.get("orderIntentId") or payload.get("orderIntentId") or ""):
        return "BROKER_EVENT_ORDER_INTENT_MISMATCH"
    observed_client_order_id = str(event.get("clientOrderId") or "")
    if observed_client_order_id and observed_client_order_id != str(outbox.get("clientOrderId") or payload.get("clientOrderId") or ""):
        return "BROKER_EVENT_CLIENT_ORDER_MISMATCH"
    return None


def _fill_event_with_order_context(order_payload: Mapping[str, Any], event: Mapping[str, Any]) -> dict[str, Any]:
    context = _identity_envelope(order_payload)
    return {**context, **dict(event)}


def _apply_fill_event(
    repository: MetaStrategyJobRepository,
    inventory_repository: MetaStrategySqliteRepository,
    outbox_id: str,
    event: Mapping[str, Any],
    *,
    evaluated_at: datetime,
) -> str:
    fill_payload, fill_rejection_reason = _meta_strategy_fill_inventory_payload(event, evaluated_at=evaluated_at)
    if fill_rejection_reason is not None:
        _quarantine_rejected_fill(
            repository,
            inventory_repository,
            event,
            reason=fill_rejection_reason,
            evaluated_at=evaluated_at,
        )
        return "QUARANTINED"
    result = inventory_repository.ingest_broker_fill(fill_payload)
    if result.get("status") == "DUPLICATE_IGNORED":
        repository.record_reconciliation_evidence(
            "FILL_DUPLICATE_IGNORED",
            fill_payload,
            client_order_id=str(fill_payload.get("clientOrderId") or ""),
            broker_order_id=str(fill_payload.get("brokerOrderId") or ""),
            order_intent_id=str(fill_payload.get("orderIntentId") or ""),
            status="DUPLICATE_IGNORED",
            now=evaluated_at,
        )
        return "DUPLICATE_FILL_IGNORED"
    _enqueue_position_management_after_execution(repository, fill_payload, trigger="broker_fill", now=evaluated_at)
    repository.record_reconciliation_evidence(
        "FILL_APPLIED_TO_INVENTORY",
        fill_payload,
        client_order_id=str(fill_payload.get("clientOrderId") or ""),
        broker_order_id=str(fill_payload.get("brokerOrderId") or ""),
        order_intent_id=str(fill_payload.get("orderIntentId") or ""),
        status="FILLED",
        now=evaluated_at,
    )
    return "FILL_APPLIED"


def _meta_strategy_fill_inventory_payload(event: Mapping[str, Any], *, evaluated_at: datetime) -> tuple[dict[str, Any], str | None]:
    ownership_reason = _fill_event_ownership_conflict(event)
    if ownership_reason is not None:
        return {}, ownership_reason
    fill_id = _required_string(event, "brokerFillId", "broker_fill_id", "fillId", "fill_id")
    if not fill_id:
        return {}, "FILL_MALFORMED_MISSING_BROKER_FILL_ID"
    broker_order_id = _required_string(event, "brokerOrderId", "broker_order_id")
    order_intent_id = _required_string(event, "orderIntentId", "order_intent_id")
    client_order_id = _required_string(event, "clientOrderId", "client_order_id")
    symbol = _required_string(event, "symbol", "ticker").upper()
    side = _required_string(event, "side").upper()
    timestamp = _required_string(event, "timestamp", "filledAt", "filled_at")
    quantity = _positive_number(event, "filledQuantity", "filled_quantity", "quantity")
    price = _positive_number(event, "averageFillPrice", "average_fill_price", "fillPrice", "fill_price", "price")
    missing = []
    if not order_intent_id:
        missing.append("orderIntentId")
    if not client_order_id:
        missing.append("clientOrderId")
    if not broker_order_id:
        missing.append("brokerOrderId")
    if not symbol:
        missing.append("symbol")
    if side not in {"BUY", "SELL"}:
        missing.append("side")
    if quantity is None:
        missing.append("quantity")
    if price is None:
        missing.append("price")
    if not timestamp or _parse_datetime_or_none(timestamp) is None:
        missing.append("timestamp")
    if missing:
        return {}, "FILL_MALFORMED_MISSING_" + "_".join(item.upper() for item in missing)
    capital_partition_id = _required_string(event, "capitalPartitionId", "capital_partition_id")
    settings_version = str(event.get("settingsVersion") or event.get("settings_version") or "")
    effective_settings_hash = str(event.get("effectiveSettingsHash") or event.get("effective_settings_hash") or "")
    strategy_catalog_version = str(event.get("strategyCatalogVersion") or event.get("strategy_catalog_version") or META_STRATEGY_STRATEGY_CATALOG_VERSION)
    feature_schema_version = str(event.get("featureSchemaVersion") or event.get("feature_schema_version") or META_STRATEGY_FEATURE_SCHEMA_VERSION)
    model_version = str(event.get("modelVersion") or event.get("model_version") or META_STRATEGY_MODEL_VERSION)
    decision_id = str(event.get("decisionId") or event.get("decision_id") or "")
    job_id = str(event.get("jobId") or event.get("job_id") or "")
    event_id = str(event.get("brokerEventId") or event.get("broker_event_id") or event.get("eventId") or event.get("event_id") or fill_id)
    correlation_id = str(event.get("correlationId") or event.get("correlation_id") or order_intent_id or decision_id or fill_id)
    return {
        "algorithmId": ALGORITHM_ID,
        "algorithm_id": ALGORITHM_ID,
        "capitalPartitionId": capital_partition_id,
        "capital_partition_id": capital_partition_id,
        "settingsVersion": settings_version,
        "settings_version": settings_version,
        "effectiveSettingsHash": effective_settings_hash,
        "effective_settings_hash": effective_settings_hash,
        "strategyCatalogVersion": strategy_catalog_version,
        "strategy_catalog_version": strategy_catalog_version,
        "featureSchemaVersion": feature_schema_version,
        "feature_schema_version": feature_schema_version,
        "modelVersion": model_version,
        "model_version": model_version,
        "decisionId": decision_id,
        "decision_id": decision_id,
        "jobId": job_id,
        "job_id": job_id,
        "eventId": event_id,
        "event_id": event_id,
        "orderIntentId": order_intent_id,
        "clientOrderId": client_order_id,
        "brokerOrderId": broker_order_id,
        "brokerFillId": fill_id,
        "correlationId": correlation_id,
        "correlation_id": correlation_id,
        "symbol": symbol,
        "side": side,
        "filledQuantity": quantity,
        "quantity": quantity,
        "fillPrice": price,
        "price": price,
        "timestamp": timestamp,
    }, None


def _quarantine_rejected_fill(
    repository: MetaStrategyJobRepository,
    inventory_repository: MetaStrategySqliteRepository,
    event: Mapping[str, Any],
    *,
    reason: str,
    evaluated_at: datetime,
) -> None:
    payload = {
        **dict(event),
        "brokerFillId": _required_string(event, "brokerFillId", "broker_fill_id", "fillId", "fill_id"),
        "timestamp": str(event.get("timestamp") or event.get("filledAt") or evaluated_at.isoformat()),
    }
    if reason.startswith("FILL_FOREIGN_"):
        inventory_repository.record_foreign_ownership_quarantine(payload, reason=reason)
        evidence_type = "FILL_OWNERSHIP_CONFLICT"
    else:
        inventory_repository.record_quarantine(
            {
                **payload,
                "algorithmId": ALGORITHM_ID,
                "capitalPartitionId": META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
            },
            reason=reason,
        )
        evidence_type = "FILL_MALFORMED_QUARANTINED"
    repository.record_reconciliation_evidence(
        evidence_type,
        payload,
        client_order_id=str(event.get("clientOrderId") or ""),
        broker_order_id=str(event.get("brokerOrderId") or ""),
        order_intent_id=str(event.get("orderIntentId") or ""),
        status="QUARANTINED",
        now=evaluated_at,
    )


def _required_string(payload: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value) != "":
            return str(value)
    return ""


def _positive_number(payload: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = payload.get(key)
        if value is None or value == "":
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if math.isfinite(number) and number > 0.0:
            return number
        return None
    return None


def _parse_datetime_or_none(value: Any) -> datetime | None:
    try:
        return _as_utc(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _fill_event_ownership_conflict(event: Mapping[str, Any]) -> str | None:
    observed_algorithm_id = _required_string(event, "algorithmId", "algorithm_id")
    if not observed_algorithm_id:
        return "FILL_MALFORMED_MISSING_ALGORITHM_ID"
    if observed_algorithm_id != ALGORITHM_ID:
        return "FILL_FOREIGN_ALGORITHM"
    observed_partition_id = _required_string(event, "capitalPartitionId", "capital_partition_id")
    if not observed_partition_id:
        return "FILL_MALFORMED_MISSING_CAPITAL_PARTITION_ID"
    if observed_partition_id != META_STRATEGY_DEFAULT_CAPITAL_PARTITION:
        return "FILL_FOREIGN_PARTITION"
    return None


def _global_risk_response(source: Any | None, proposal: GlobalOrderProposal, *, evaluated_at: datetime) -> GlobalGateResponse:
    if source is not None and hasattr(source, "approve_order"):
        response = source.approve_order(proposal)
        if isinstance(response, GlobalGateResponse):
            return response
    raise RuntimeError("meta_strategy.execution.global_risk_source_required")


def evaluate_meta_strategy_execution_guard(
    *,
    repository: MetaStrategyJobRepository,
    inventory_repository: MetaStrategySqliteRepository,
    paper_gateway: PaperOrderGateway,
    proposal: GlobalOrderProposal,
    payload: Mapping[str, Any],
    global_response: GlobalGateResponse,
    global_application: Any,
    settings_store: MetaStrategySettingsStore | None,
    runtime_readiness_source: Any | None,
    readiness_report_source: Any | None,
    market_clock_source: Any | None,
    evaluated_at: datetime,
) -> MetaStrategyExecutionGuardResult:
    intent_type = str(proposal.intent)
    if intent_type != "new_entry":
        return _protective_order_guard(
            repository=repository,
            inventory_repository=inventory_repository,
            paper_gateway=paper_gateway,
            proposal=proposal,
            payload=payload,
            evaluated_at=evaluated_at,
        )

    reasons: list[str] = []
    evidence: dict[str, Any] = {}
    if str(proposal.algorithmId) != ALGORITHM_ID or str(payload.get("algorithmId")) != ALGORITHM_ID:
        reasons.append("meta_strategy.execution_guard.wrong_algorithm")
    if str(proposal.capitalPartitionId) != META_STRATEGY_DEFAULT_CAPITAL_PARTITION:
        reasons.append("meta_strategy.execution_guard.wrong_capital_partition")

    runtime = _runtime_readiness(runtime_readiness_source, repository)
    evidence["runtimeSupervisor"] = dict(runtime or {})
    if not runtime:
        reasons.append("meta_strategy.execution.runtime_supervisor_unavailable")
    else:
        if runtime.get("enabled") is not True:
            reasons.append("meta_strategy.execution_guard.runtime_disabled")
        if str(runtime.get("mode") or "").upper() != "PAPER":
            reasons.append("meta_strategy.execution_guard.runtime_not_paper")
        if runtime.get("liveTradingEnabled") is True or runtime.get("live_trading_enabled") is True:
            reasons.append("meta_strategy.execution_guard.live_trading_enabled")
        if runtime.get("ready") is not True:
            reasons.append("meta_strategy.execution_guard.runtime_not_ready")
        if runtime.get("paperOrdersBlocked") is True:
            reasons.append("meta_strategy.execution_guard.runtime_blocks_paper_orders")

    readiness = _readiness_report(
        repository=repository,
        inventory_repository=inventory_repository,
        settings_store=settings_store,
        runtime=runtime,
        readiness_report_source=readiness_report_source,
    )
    evidence["readiness"] = dict(readiness or {})
    if not readiness:
        reasons.append("meta_strategy.execution.readiness_report_unavailable")
    else:
        if str(readiness.get("status") or "").upper() != "OK":
            reasons.append("meta_strategy.execution_guard.readiness_status_not_ok")
        if readiness.get("complete") is not True:
            reasons.append("meta_strategy.execution_guard.readiness_incomplete")
        if readiness.get("paperReady") is not True:
            reasons.append("meta_strategy.execution_guard.paper_readiness_incomplete")
        reasons.extend(_readiness_prerequisite_reasons(readiness, runtime))

    control = _automatic_paper_control(repository)
    evidence["paperControl"] = dict(control or {})
    if not control:
        reasons.append("meta_strategy.paper_control.state_unavailable")
    elif _automatic_paper_control_on(control) is not True:
        reasons.append("meta_strategy.paper_control.new_entries_disabled")
        reasons.append("meta_strategy.paper_control.new_entry_blocked_before_submission")

    control_states = _operational_control_states(repository)
    evidence["operationalControls"] = control_states
    if _control_active(control_states, "PAUSE_NEW_ENTRIES", "newEntriesPaused"):
        reasons.append("meta_strategy.execution_guard.pause_new_entries_active")
    if _control_active(control_states, "EXIT_ONLY", "exitOnly"):
        reasons.append("meta_strategy.execution_guard.exit_only_active")
    if _control_active(control_states, "STOP_META_RUNTIME", "runtimeStopRequested") or _control_active(control_states, "STOP_META_RUNTIME", "paperOrdersBlocked"):
        reasons.append("meta_strategy.execution_guard.emergency_stop_active")

    clock = _market_clock_snapshot(market_clock_source or getattr(paper_gateway, "broker", None), evaluated_at=evaluated_at)
    evidence["authoritativeMarketClock"] = dict(clock or {})
    if not clock:
        reasons.append("meta_strategy.execution.authoritative_market_clock_unavailable")
    elif clock.get("canAuthorizeNewEntries") is not True:
        if clock.get("fresh") is not True:
            reasons.append("meta_strategy.execution_guard.market_clock_stale")
        if clock.get("authoritativeReadOnly") is not True:
            reasons.append("meta_strategy.execution_guard.market_clock_not_authoritative")
        if _market_clock_contradictory(clock):
            reasons.append("meta_strategy.execution.market_clock_contradictory")
        if _market_clock_open(clock) is not True:
            reasons.append("meta_strategy.execution.market_closed")
            reasons.append("meta_strategy.execution_guard.market_closed")

    settings = _settings_for_payload(settings_store, payload)
    active_settings = _active_settings(settings_store)
    evidence["settings"] = {
        "orderSettingsVersion": str(payload.get("settingsVersion") or payload.get("settings_version") or ""),
        "activeSettingsVersion": getattr(active_settings, "settings_version", None),
        "orderSettingsLoaded": settings is not None,
    }
    if settings is None or active_settings is None:
        reasons.append("meta_strategy.execution.authoritative_settings_unavailable")
    else:
        if active_settings.settings_version != str(payload.get("settingsVersion") or payload.get("settings_version") or ""):
            reasons.append("meta_strategy.execution_guard.settings_version_changed")
        paper_settings = settings.paper_execution
        if paper_settings.execution_mode != "PAPER" or paper_settings.enabled is not True:
            reasons.append("meta_strategy.execution_guard.settings_not_promoted_for_paper")
        if settings.status not in {"ACTIVE", "EFFECTIVE"}:
            reasons.append("meta_strategy.execution_guard.settings_not_active")
        _append_early_close_reason(reasons, clock, settings, evaluated_at)

    quote_age_limit = _env_non_negative_int("META_STRATEGY_QUOTE_FRESHNESS_LIMIT_SECONDS", META_STRATEGY_MAX_QUOTE_AGE_SECONDS)
    decision_age_limit = _env_non_negative_int("META_STRATEGY_DECISION_MAX_AGE_SECONDS", META_STRATEGY_MAX_DECISION_AGE_SECONDS)
    intent_age_limit = _env_non_negative_int("META_STRATEGY_ORDER_INTENT_MAX_AGE_SECONDS", META_STRATEGY_MAX_INTENT_AGE_SECONDS)
    global_risk_age_limit = _env_non_negative_int("META_STRATEGY_GLOBAL_RISK_FRESHNESS_LIMIT_SECONDS", META_STRATEGY_MAX_GLOBAL_RISK_AGE_SECONDS)
    evidence["freshnessLimitsSeconds"] = {
        "quote": quote_age_limit,
        "decision": decision_age_limit,
        "orderIntent": intent_age_limit,
        "globalRiskApproval": global_risk_age_limit,
    }
    quote_age = _quote_age_seconds(payload, evaluated_at)
    evidence["quoteAgeSeconds"] = quote_age
    if quote_age is None or quote_age > quote_age_limit:
        reasons.append("meta_strategy.execution_guard.quote_stale")
    decision_age = _age_seconds(payload.get("decisionTimestamp") or payload.get("decision_timestamp") or payload.get("barEnd"), evaluated_at)
    intent_age = _age_seconds(payload.get("createdAt") or payload.get("created_at") or payload.get("timestamp"), evaluated_at)
    evidence["decisionAgeSeconds"] = decision_age
    evidence["intentAgeSeconds"] = intent_age
    if decision_age is None or decision_age > decision_age_limit:
        reasons.append("meta_strategy.execution_guard.decision_stale")
    if intent_age is None or intent_age > intent_age_limit:
        reasons.append("meta_strategy.execution_guard.intent_stale")
    risk_age = max(0.0, (evaluated_at - _as_utc(global_response.evaluatedAt)).total_seconds())
    evidence["globalRiskApprovalAgeSeconds"] = risk_age
    if risk_age > global_risk_age_limit:
        reasons.append("meta_strategy.execution_guard.global_risk_approval_stale")

    if _truth(payload, "localGatesPassed", "local_gates_passed") is not True:
        reasons.append("meta_strategy.execution_guard.local_gates_not_passed")
    if global_application.globallyAllowedQuantity <= 0 or global_application.action in {"REJECT_NEW_ENTRY", "EXIT_ONLY", "EMERGENCY_LIQUIDATE"}:
        reasons.append("meta_strategy.execution.global_risk_rejected")

    inventory = inventory_repository.current_inventory_snapshot()
    account_equity = _account_equity(payload, paper_gateway, evaluated_at=evaluated_at)
    buying_power = _buying_power(payload, paper_gateway, evaluated_at=evaluated_at)
    remaining_risk = _remaining_algorithm_risk(payload)
    global_available_risk = float(global_response.maximumAdditionalRiskDollars)
    global_quantity_cap = int(global_response.maximumAllowedQuantity)
    reserved_risk = _first_number(payload, "reservedRiskDollars", "reserved_risk_dollars")
    _validate_required_non_negative(
        account_equity,
        reasons,
        unavailable_reason="meta_strategy.sizing.account_equity_unavailable",
        zero_reason="meta_strategy.sizing.zero_account_equity",
    )
    _validate_required_non_negative(
        buying_power,
        reasons,
        unavailable_reason="meta_strategy.sizing.buying_power_unavailable",
        zero_reason="meta_strategy.sizing.zero_buying_power",
    )
    _validate_required_non_negative(
        remaining_risk,
        reasons,
        unavailable_reason="meta_strategy.sizing.algorithm_risk_unavailable",
        zero_reason="meta_strategy.sizing.zero_algorithm_risk",
    )
    _validate_required_non_negative(
        global_available_risk,
        reasons,
        unavailable_reason="meta_strategy.sizing.global_risk_unavailable",
        zero_reason="meta_strategy.sizing.zero_global_risk",
    )
    _validate_required_non_negative(global_quantity_cap, reasons, unavailable_reason="", zero_reason="")
    _validate_required_non_negative(
        reserved_risk,
        reasons,
        unavailable_reason="meta_strategy.sizing.reserved_risk_unavailable",
        zero_reason="",
        zero_allowed=True,
    )
    evidence["inventory"] = {
        "remainingAlgorithmRisk": remaining_risk,
        "reservedRiskDollars": inventory.reserved_risk_dollars,
        "openPositions": tuple(position.__dict__ for position in inventory.open_positions),
    }
    evidence["sizingInputs"] = {
        "accountEquity": account_equity,
        "buyingPower": buying_power,
        "remainingAlgorithmRisk": remaining_risk,
        "globalAvailableRisk": global_available_risk,
        "globalQuantityCap": global_quantity_cap,
        "reservedRiskDollars": reserved_risk,
    }
    if global_available_risk <= 0.0:
        reasons.append("meta_strategy.execution_guard.zero_global_risk")
    if buying_power is not None and buying_power <= 0.0:
        reasons.append("meta_strategy.execution_guard.zero_buying_power")
    if remaining_risk is not None and remaining_risk <= 0.0:
        reasons.append("meta_strategy.execution_guard.zero_algorithm_risk")
    if global_application.globallyAllowedQuantity <= 0:
        reasons.append("meta_strategy.execution_guard.zero_approved_quantity")
    if _duplicate_client_order_id(repository, str(payload.get("clientOrderId") or "")):
        reasons.append("meta_strategy.execution_guard.duplicate_client_order_id")
    position_policy = _meta_strategy_position_entry_policy(inventory, proposal, settings)
    evidence["metaStrategyPositionPolicy"] = position_policy
    if position_policy["blocked"]:
        reasons.append(str(position_policy["reasonCode"]))
        if position_policy["reasonCode"] == "meta_strategy.execution_guard.existing_meta_strategy_position":
            reasons.append("meta_strategy.execution_guard.existing_position")
    if _has_open_entry_order(repository, proposal, current_order_intent_id=proposal.orderIntentId):
        reasons.append("meta_strategy.execution_guard.existing_open_entry_order")
    if _symbol_allowed(settings, proposal) is not True:
        reasons.append("meta_strategy.execution_guard.symbol_not_permitted")
    if _broker_account_verified(paper_gateway) is not True:
        reasons.append("meta_strategy.execution_guard.paper_account_unverified")

    allowed = not reasons
    if allowed:
        reasons.append("meta_strategy.execution_guard.new_entry_allowed")
    return MetaStrategyExecutionGuardResult(
        algorithm_id=ALGORITHM_ID,
        capital_partition_id=str(proposal.capitalPartitionId),
        order_intent_id=str(proposal.orderIntentId),
        intent_type=intent_type,
        allowed=allowed,
        policy="new_entry_execution_boundary",
        evaluated_at=evaluated_at.isoformat(),
        reason_codes=tuple(dict.fromkeys(reasons)),
        evidence=evidence,
    )


def _protective_order_guard(
    *,
    repository: MetaStrategyJobRepository,
    inventory_repository: MetaStrategySqliteRepository,
    paper_gateway: PaperOrderGateway,
    proposal: GlobalOrderProposal,
    payload: Mapping[str, Any],
    evaluated_at: datetime,
) -> MetaStrategyExecutionGuardResult:
    reasons: list[str] = []
    inventory = inventory_repository.current_inventory_snapshot()
    if str(proposal.algorithmId) != ALGORITHM_ID or str(payload.get("algorithmId")) != ALGORITHM_ID:
        reasons.append("meta_strategy.execution_guard.wrong_algorithm")
    if str(proposal.capitalPartitionId) != META_STRATEGY_DEFAULT_CAPITAL_PARTITION:
        reasons.append("meta_strategy.execution_guard.wrong_capital_partition")
    if _broker_account_verified(paper_gateway) is not True:
        reasons.append("meta_strategy.execution_guard.paper_account_unverified")
    if not _order_reduces_exposure(inventory, proposal):
        reasons.append("meta_strategy.execution_guard.protective_order_not_risk_reducing")
    allowed = not reasons
    reasons.append("meta_strategy.paper_control.protective_exit_allowed" if allowed else "meta_strategy.execution_guard.protective_exit_blocked")
    return MetaStrategyExecutionGuardResult(
        algorithm_id=ALGORITHM_ID,
        capital_partition_id=str(proposal.capitalPartitionId),
        order_intent_id=str(proposal.orderIntentId),
        intent_type=str(proposal.intent),
        allowed=allowed,
        policy="protective_risk_reducing",
        evaluated_at=evaluated_at.isoformat(),
        reason_codes=tuple(dict.fromkeys(reasons)),
        evidence={
            "inventory": {"openPositions": tuple(position.__dict__ for position in inventory.open_positions)},
            "brokerPaperAccountVerified": _broker_account_verified(paper_gateway),
        },
    )


def _active_settings(settings_store: MetaStrategySettingsStore | None) -> Any | None:
    if settings_store is None:
        return None
    try:
        return settings_store.get_active_settings()
    except Exception:
        return None


def _operational_control_states(repository: MetaStrategyJobRepository) -> dict[str, dict[str, Any]]:
    snapshots = repository.gateway_snapshots()
    states: dict[str, dict[str, Any]] = {}
    for name in ("PAUSE_NEW_ENTRIES", "EXIT_ONLY", "STOP_META_RUNTIME"):
        payload = snapshots.get(f"meta_strategy.controls.{name}") or {}
        state = payload.get("state") if isinstance(payload.get("state"), Mapping) else payload
        states[name] = dict(state) if isinstance(state, Mapping) else {}
    return states


def _control_active(states: Mapping[str, Mapping[str, Any]], control: str, field: str) -> bool:
    return states.get(control, {}).get(field) is True


def _append_early_close_reason(reasons: list[str], clock: Mapping[str, Any] | None, settings: Any | None, evaluated_at: datetime) -> None:
    if not clock or settings is None:
        return
    next_close = _first_datetime(clock, "nextClose", "next_close", "nextMarketClose", "next_market_close")
    if next_close is None:
        return
    seconds_to_close = (next_close - evaluated_at).total_seconds()
    cutoff_minutes = int(getattr(settings.position_management, "no_new_entry_minutes_before_close", 0) or 0)
    if seconds_to_close <= 0 or seconds_to_close <= cutoff_minutes * 60:
        reasons.append("meta_strategy.execution_guard.early_close_boundary")


def _quote_age_seconds(payload: Mapping[str, Any], evaluated_at: datetime) -> float | None:
    quote = payload.get("quote") if isinstance(payload.get("quote"), Mapping) else {}
    timestamp = _first_datetime(
        {**dict(quote), **dict(payload)},
        "quoteTimestamp",
        "quote_timestamp",
        "nbboTimestamp",
        "nbbo_timestamp",
        "marketDataTimestamp",
        "market_data_timestamp",
    )
    if timestamp is None:
        return None
    return max(0.0, (evaluated_at - timestamp).total_seconds())


def _age_seconds(value: Any, evaluated_at: datetime) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, (evaluated_at - _as_utc(_parse_datetime(value, evaluated_at))).total_seconds())
    except Exception:
        return None


def _first_datetime(payload: Mapping[str, Any], *keys: str) -> datetime | None:
    for key in keys:
        if payload.get(key) is None:
            continue
        try:
            return _as_utc(_parse_datetime(payload[key], datetime.now(UTC)))
        except Exception:
            continue
    return None


def _truth(payload: Mapping[str, Any], *keys: str) -> bool | None:
    for key in keys:
        if key in payload:
            return payload[key] is True
    return None


def _account_equity(payload: Mapping[str, Any], paper_gateway: PaperOrderGateway, *, evaluated_at: datetime) -> float | None:
    return _account_value(
        payload,
        paper_gateway,
        evaluated_at=evaluated_at,
        keys=("accountEquity", "account_equity", "equity"),
    )


def _buying_power(payload: Mapping[str, Any], paper_gateway: PaperOrderGateway, *, evaluated_at: datetime) -> float | None:
    return _account_value(
        payload,
        paper_gateway,
        evaluated_at=evaluated_at,
        keys=("buyingPower", "buying_power", "availableBuyingPower", "available_buying_power"),
    )


def _account_value(
    payload: Mapping[str, Any],
    paper_gateway: PaperOrderGateway,
    *,
    evaluated_at: datetime,
    keys: tuple[str, ...],
) -> float | None:
    account = payload.get("accountSnapshot") if isinstance(payload.get("accountSnapshot"), Mapping) else {}
    value = _first_number({**dict(account), **dict(payload)}, *keys)
    if value is not None:
        return value
    broker = getattr(paper_gateway, "broker", None)
    for method_name in ("read_account_snapshot", "get_account", "account_snapshot"):
        method = getattr(broker, method_name, None)
        if not callable(method):
            continue
        try:
            result = method(at=evaluated_at)
        except TypeError:
            result = method()
        if isinstance(result, Mapping):
            value = _first_number(result, *keys)
            if value is not None:
                return value
    return None


def _first_number(payload: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        if payload.get(key) is None:
            continue
        try:
            return float(payload[key])
        except (TypeError, ValueError):
            continue
    return None


def _remaining_algorithm_risk(payload: Mapping[str, Any]) -> float | None:
    return _first_number(
        payload,
        "remainingAlgorithmRisk",
        "remaining_algorithm_risk",
        "remainingRiskDollars",
        "remaining_risk_dollars",
    )


def _validate_required_non_negative(
    value: float | int | None,
    reasons: list[str],
    *,
    unavailable_reason: str,
    zero_reason: str,
    zero_allowed: bool = False,
) -> None:
    if value is None:
        if unavailable_reason:
            reasons.append(unavailable_reason)
        return
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        reasons.append("meta_strategy.sizing.non_finite_input")
        return
    if not math.isfinite(numeric) or numeric < 0:
        reasons.append("meta_strategy.sizing.non_finite_input")
    elif numeric == 0.0 and not zero_allowed and zero_reason:
        reasons.append(zero_reason)


def _duplicate_client_order_id(repository: MetaStrategyJobRepository, client_order_id: str) -> bool:
    if not client_order_id:
        return True
    try:
        repository.read_gateway_snapshot(f"paper_order_gateway.client_order.{client_order_id}")
        return True
    except KeyError:
        return False


def _has_conflicting_position(inventory: Any, proposal: GlobalOrderProposal, settings: Any | None = None) -> bool:
    return bool(_meta_strategy_position_entry_policy(inventory, proposal, settings)["blocked"])


def _meta_strategy_position_entry_policy(inventory: Any, proposal: GlobalOrderProposal, settings: Any | None) -> dict[str, Any]:
    symbol = str(proposal.symbol).upper()
    position = _owned_open_position_for_symbol(inventory, symbol)
    pyramiding_enabled = settings is not None and getattr(settings.position_management, "one_position_per_symbol", True) is not True
    if position is None:
        return {
            "source": "meta_strategy_repository.current_inventory_snapshot",
            "symbol": symbol,
            "ownedPositionFound": False,
            "pyramidingEnabled": pyramiding_enabled,
            "action": "ALLOW_NEW_META_STRATEGY_ENTRY",
            "blocked": False,
            "reasonCode": "meta_strategy.execution_guard.no_owned_position",
        }
    side = proposal.side.value if hasattr(proposal.side, "value") else str(proposal.side)
    position_side = str(position.side).upper()
    same_direction = (position_side == "LONG" and side.upper() == "BUY") or (position_side == "SHORT" and side.upper() == "SELL")
    if not pyramiding_enabled:
        return {
            "source": "meta_strategy_repository.current_inventory_snapshot",
            "symbol": symbol,
            "ownedPositionFound": True,
            "ownedPositionSide": position_side,
            "ownedPositionQuantity": float(position.quantity),
            "pyramidingEnabled": False,
            "action": "BLOCK_DUPLICATE_META_STRATEGY_ENTRY",
            "blocked": True,
            "reasonCode": "meta_strategy.execution_guard.existing_meta_strategy_position",
        }
    if not same_direction:
        return {
            "source": "meta_strategy_repository.current_inventory_snapshot",
            "symbol": symbol,
            "ownedPositionFound": True,
            "ownedPositionSide": position_side,
            "ownedPositionQuantity": float(position.quantity),
            "pyramidingEnabled": True,
            "action": "BLOCK_META_STRATEGY_REVERSAL_ENTRY",
            "blocked": True,
            "reasonCode": "meta_strategy.execution_guard.owned_position_reversal_rejected",
        }
    return {
        "source": "meta_strategy_repository.current_inventory_snapshot",
        "symbol": symbol,
        "ownedPositionFound": True,
        "ownedPositionSide": position_side,
        "ownedPositionQuantity": float(position.quantity),
        "pyramidingEnabled": True,
        "action": "ALLOW_ADD_TO_META_STRATEGY_POSITION",
        "blocked": False,
        "reasonCode": "meta_strategy.execution_guard.add_to_owned_position_allowed",
    }


def _owned_open_position_for_symbol(inventory: Any, symbol: str) -> Any | None:
    for position in getattr(inventory, "open_positions", ()):
        if str(position.symbol).upper() == symbol and float(position.quantity) > 0:
            return position
    return None


def _has_open_entry_order(repository: MetaStrategyJobRepository, proposal: GlobalOrderProposal, *, current_order_intent_id: str) -> bool:
    symbol = str(proposal.symbol).upper()
    for outbox in repository.submitted_execution_outbox_records():
        payload = outbox.get("payload") if isinstance(outbox.get("payload"), Mapping) else {}
        if str(outbox.get("orderIntentId") or payload.get("orderIntentId") or "") == current_order_intent_id:
            continue
        if str(outbox.get("status") or "").upper() not in _ACTIVE_OUTBOX_STATUSES:
            continue
        if _proposal_intent(payload) != "new_entry":
            continue
        if str(payload.get("symbol") or "").upper() == symbol:
            return True
    return False


def _symbol_allowed(settings: Any | None, proposal: GlobalOrderProposal) -> bool:
    if settings is None:
        return False
    side = proposal.side.value if hasattr(proposal.side, "value") else str(proposal.side)
    if side.upper() == "BUY":
        return getattr(settings.local_risk, "allow_long", False) is True
    return getattr(settings.local_risk, "allow_short", False) is True


def _broker_account_verified(paper_gateway: PaperOrderGateway) -> bool:
    broker = getattr(paper_gateway, "broker", None)
    verify = getattr(broker, "verify_paper_account", None)
    if not callable(verify):
        return False
    try:
        return verify() is True and getattr(broker, "paper_endpoint", False) is True
    except Exception:
        return False


def _order_reduces_exposure(inventory: Any, proposal: GlobalOrderProposal) -> bool:
    symbol = str(proposal.symbol).upper()
    quantity = int(proposal.quantity)
    if quantity <= 0:
        return False
    side = proposal.side.value if hasattr(proposal.side, "value") else str(proposal.side)
    for position in inventory.open_positions:
        if str(position.symbol).upper() != symbol:
            continue
        current_qty = float(position.quantity)
        if current_qty <= 0:
            continue
        position_side = str(position.side).upper()
        if position_side == "LONG" and side.upper() == "SELL" and quantity <= current_qty:
            return True
        if position_side == "SHORT" and side.upper() == "BUY" and quantity <= current_qty:
            return True
    return False


def _automatic_entry_activation_blockers(
    *,
    repository: MetaStrategyJobRepository,
    inventory_repository: MetaStrategySqliteRepository,
    paper_gateway: PaperOrderGateway,
    proposal: GlobalOrderProposal,
    payload: Mapping[str, Any],
    settings_store: MetaStrategySettingsStore | None,
    runtime_readiness_source: Any | None,
    readiness_report_source: Any | None,
    market_clock_source: Any | None,
    evaluated_at: datetime,
) -> tuple[str, ...]:
    if str(proposal.intent) != "new_entry":
        return ()

    blockers: list[str] = []
    runtime = _runtime_readiness(runtime_readiness_source, repository)
    if not runtime:
        blockers.append("meta_strategy.execution.runtime_supervisor_unavailable")
    else:
        if runtime.get("enabled") is not True:
            blockers.append("meta_strategy.execution.runtime_not_enabled")
        if str(runtime.get("mode") or "").upper() != "PAPER":
            blockers.append("meta_strategy.execution.paper_runtime_mode_required")
        if runtime.get("ready") is not True:
            blockers.append("meta_strategy.execution.runtime_supervisor_not_ready")
        if runtime.get("paperOrdersBlocked") is True:
            blockers.append("meta_strategy.execution.runtime_blocks_paper_orders")

    settings = _settings_for_payload(settings_store, payload)
    if settings is None:
        blockers.append("meta_strategy.execution.authoritative_settings_unavailable")
    else:
        paper_settings = settings.paper_execution
        if paper_settings.execution_mode != "PAPER":
            blockers.append("meta_strategy.execution.paper_settings_mode_required")
        if paper_settings.enabled is not True:
            blockers.append("meta_strategy.execution.paper_settings_off")

    control = _automatic_paper_control(repository)
    if not control:
        blockers.append("meta_strategy.paper_control.state_unavailable")
    elif _automatic_paper_control_on(control) is not True:
        blockers.append("meta_strategy.paper_control.new_entries_disabled")
        blockers.append("meta_strategy.paper_control.new_entry_blocked_before_submission")

    clock = _market_clock_snapshot(market_clock_source or getattr(paper_gateway, "broker", None), evaluated_at=evaluated_at)
    if not clock:
        blockers.append("meta_strategy.execution.authoritative_market_clock_unavailable")
    elif clock.get("canAuthorizeNewEntries") is not True:
        if clock.get("fresh") is not True:
            blockers.append("meta_strategy.execution_guard.market_clock_stale")
        if clock.get("authoritativeReadOnly") is not True:
            blockers.append("meta_strategy.execution_guard.market_clock_not_authoritative")
        if _market_clock_contradictory(clock):
            blockers.append("meta_strategy.execution.market_clock_contradictory")
        if _market_clock_open(clock) is not True:
            blockers.append("meta_strategy.execution.market_closed")

    readiness = _readiness_report(
        repository=repository,
        inventory_repository=inventory_repository,
        settings_store=settings_store,
        runtime=runtime,
        readiness_report_source=readiness_report_source,
    )
    if not readiness:
        blockers.append("meta_strategy.execution.readiness_report_unavailable")
    elif readiness.get("complete") is not True or readiness.get("paperReady") is not True:
        blockers.append("meta_strategy.execution.readiness_report_incomplete")
    else:
        blockers.extend(_readiness_prerequisite_reasons(readiness, runtime))

    return tuple(dict.fromkeys(blockers))


def _runtime_readiness(source: Any | None, repository: MetaStrategyJobRepository) -> dict[str, Any] | None:
    result = _call_mapping_source(source, "readiness_status", "load_snapshot", "runtime_readiness")
    if result is None:
        try:
            result = repository.read_gateway_snapshot("meta_strategy.runtime.readiness")
        except KeyError:
            return None
    return result


def _settings_for_payload(settings_store: MetaStrategySettingsStore | None, payload: Mapping[str, Any]) -> Any | None:
    if settings_store is None:
        return None
    settings_version = str(payload.get("settingsVersion") or payload.get("settings_version") or "")
    try:
        return settings_store.get_settings(settings_version) if settings_version else settings_store.get_active_settings()
    except Exception:
        return None


def _automatic_paper_control(repository: MetaStrategyJobRepository) -> dict[str, Any] | None:
    durable = repository.read_paper_trading_control()
    if durable is not None:
        return durable.to_dict()
    try:
        return repository.read_gateway_snapshot(META_STRATEGY_AUTOMATIC_PAPER_CONTROL_KEY)
    except KeyError:
        return None


def _automatic_paper_control_on(control: Mapping[str, Any]) -> bool:
    state = control.get("state") if isinstance(control.get("state"), Mapping) else control
    return bool(
        isinstance(state, Mapping)
        and (state.get("newPaperEntriesEnabled") is True or state.get("automaticPaperTradingEnabled") is True)
        and state.get("paperEntriesAllowed") is True
        and state.get("liveExecutionEnabled") is not True
    )


def _market_clock_snapshot(source: Any | None, *, evaluated_at: datetime) -> dict[str, Any] | None:
    try:
        snapshot = read_market_clock_snapshot(
            source,
            evaluated_at=evaluated_at,
            max_age_seconds=_env_non_negative_int("META_STRATEGY_MARKET_CLOCK_FRESHNESS_LIMIT_SECONDS", 30),
        )
    except Exception:
        return None
    return snapshot.as_dict() if snapshot is not None else None


def _market_clock_open(clock: Mapping[str, Any]) -> bool | None:
    for key in ("isOpen", "is_open", "marketOpen", "market_open"):
        if key in clock:
            return bool(clock[key])
    status = str(clock.get("status") or clock.get("state") or "").lower()
    if status in {"open", "regular", "regular_session"}:
        return True
    if status in {"closed", "pre_market", "post_market", "halted"}:
        return False
    return None


def _market_clock_contradictory(clock: Mapping[str, Any]) -> bool:
    explicit = _market_clock_open(clock)
    status = str(clock.get("status") or clock.get("state") or "").lower()
    if explicit is True and status in {"closed", "halted"}:
        return True
    if explicit is False and status in {"open", "regular", "regular_session"}:
        return True
    return False


def _env_non_negative_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(0, value)


def _readiness_prerequisite_reasons(readiness: Mapping[str, Any], runtime: Mapping[str, Any] | None) -> tuple[str, ...]:
    prerequisites = _readiness_prerequisites(readiness)
    runtime_snapshot = _mapping_or_empty(readiness.get("runtimeSupervisor")) or dict(runtime or {})
    shadow_paper = _mapping_or_empty(readiness.get("currentShadowPaperStatus"))
    reasons: list[str] = []
    if runtime_snapshot.get("ready") is not True:
        reasons.append("meta_strategy.readiness.runtime_supervisor_not_ready")
    if shadow_paper.get("paperOrdersBlocked") is not False:
        reasons.append("meta_strategy.readiness.paper_orders_blocked")
    checks = (
        ("durableDatabaseAvailable", "meta_strategy.readiness.database_unavailable"),
        ("inventoryRepositoryAvailable", "meta_strategy.readiness.inventory_repository_unavailable"),
        ("inventoryConsistencyPasses", "meta_strategy.readiness.inventory_consistency_failed"),
        ("allocatedCapitalPositive", "meta_strategy.readiness.allocated_capital_missing"),
        ("accountSnapshotMetaStrategyDerived", "meta_strategy.readiness.account_snapshot_not_meta_strategy_inventory"),
        ("riskSnapshotMetaStrategyDerived", "meta_strategy.readiness.risk_snapshot_not_meta_strategy_inventory"),
        ("activeSettingsPromotedForPaper", "meta_strategy.readiness.settings_not_promoted_for_paper"),
        ("paperBrokerVerified", "meta_strategy.readiness.paper_broker_unverified"),
        ("brokerPaperOnly", "meta_strategy.readiness.broker_not_paper_only"),
        ("authoritativeMarketDataHealthy", "meta_strategy.readiness.market_data_unhealthy"),
        ("marketClockHealthy", "meta_strategy.readiness.market_clock_unhealthy"),
        ("requiredWorkersHealthy", "meta_strategy.readiness.worker_unhealthy"),
        ("queueLagBelowThreshold", "meta_strategy.readiness.queue_lag_exceeded"),
        ("deadLetterWithinThreshold", "meta_strategy.readiness.dead_letter_threshold_exceeded"),
        ("restartReconstructionSucceeded", "meta_strategy.readiness.restart_reconstruction_failed"),
        ("inventoryReconciliationCurrent", "meta_strategy.readiness.inventory_reconciliation_stale"),
        ("globalRiskSourceCurrent", "meta_strategy.readiness.global_risk_stale"),
        ("requiredAcceptanceTestsPassed", "meta_strategy.readiness.acceptance_evidence_missing_or_failed"),
        ("paperToggleEnabled", "meta_strategy.readiness.paper_toggle_disabled"),
        ("runtimeModePaper", "meta_strategy.readiness.runtime_mode_not_paper"),
        ("liveTradingDisabled", "meta_strategy.readiness.live_trading_enabled"),
    )
    for field, reason in checks:
        if _readiness_prerequisite_value(readiness, prerequisites, field) is not True:
            reasons.append(reason)
    return tuple(dict.fromkeys(reasons))


def _readiness_prerequisites(readiness: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("paperEntryReadinessPrerequisites", "operationalPrerequisites", "executionPrerequisites", "prerequisites"):
        value = readiness.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _readiness_prerequisite_value(readiness: Mapping[str, Any], prerequisites: Mapping[str, Any], field: str) -> bool | None:
    if prerequisites.get(field) is not None:
        return prerequisites.get(field) is True
    if readiness.get(field) is not None:
        return readiness.get(field) is True
    return None


def _mapping_or_empty(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _readiness_report(
    *,
    repository: MetaStrategyJobRepository,
    inventory_repository: MetaStrategySqliteRepository,
    settings_store: MetaStrategySettingsStore | None,
    runtime: Mapping[str, Any] | None,
    readiness_report_source: Any | None,
) -> dict[str, Any] | None:
    report = _call_mapping_source(readiness_report_source, "readiness_report", "load_snapshot")
    if report is not None:
        payload = report.get("payload") if isinstance(report.get("payload"), Mapping) else report
        return dict(payload) if isinstance(payload, Mapping) else None
    if settings_store is None:
        try:
            return repository.read_gateway_snapshot("meta_strategy.readiness.report")
        except KeyError:
            return None
    try:
        snapshot = build_meta_strategy_observability_snapshot(
            job_repository=repository,
            inventory_repository=inventory_repository,
            settings_store=settings_store,
        )
        report = build_meta_strategy_evidence_acceptance_report(snapshot)
        paper_readiness = build_meta_strategy_paper_readiness_acceptance_report(snapshot, runtime)
        entry_prerequisites = build_meta_strategy_paper_entry_readiness_prerequisites(snapshot, runtime, paper_readiness)
    except Exception:
        return None
    return {
        **report,
        "complete": bool(report.get("complete") and paper_readiness.get("paperReady") and entry_prerequisites.get("ready")),
        "paperReady": bool(paper_readiness.get("paperReady") and entry_prerequisites.get("ready")),
        "paperReadinessAcceptance": paper_readiness,
        "paperEntryReadinessPrerequisites": entry_prerequisites,
        "operationalPrerequisites": entry_prerequisites,
        "runtimeSupervisor": dict(runtime or {}),
        "currentShadowPaperStatus": {
            "paperOrdersBlocked": dict(runtime or {}).get("paperOrdersBlocked") is True or entry_prerequisites.get("ready") is not True,
            "liveExecutionEnabled": False,
        },
    }


def _call_mapping_source(source: Any | None, *method_names: str) -> dict[str, Any] | None:
    if source is None:
        return None
    if callable(source):
        value = source()
        return dict(value) if isinstance(value, Mapping) else None
    for method_name in method_names:
        method = getattr(source, method_name, None)
        if callable(method):
            value = method()
            return dict(value) if isinstance(value, Mapping) else None
    return None


def _broker_events(paper_gateway: PaperOrderGateway) -> tuple[dict[str, Any], ...]:
    broker = paper_gateway.broker
    if hasattr(broker, "list_order_events"):
        return tuple(dict(event) for event in broker.list_order_events())
    return ()


def _enqueue_position_management_after_execution(repository: MetaStrategyJobRepository, payload: Mapping[str, Any], *, trigger: str, now: datetime) -> None:
    envelope = _identity_envelope(payload)
    symbol = str(payload.get("symbol") or "PORTFOLIO").upper()
    event_id = str(payload.get("brokerEventId") or payload.get("eventId") or payload.get("event_id") or trigger)
    mark_price = float(payload.get("averageFillPrice") or payload.get("fillPrice") or payload.get("limitPrice") or payload.get("entryPrice") or 0.0)
    repository.enqueue_job(
        job_type="position_management",
        idempotency_key=f"meta_strategy.position_management.{trigger}.{envelope['capitalPartitionId']}.{symbol}.{event_id}",
        payload={
            **envelope,
            "trigger": trigger,
            "symbol": symbol,
            "mode": str(payload.get("mode") or "PAPER"),
            "markPrices": {symbol: mark_price} if symbol != "PORTFOLIO" and mark_price > 0.0 else {},
            "sourceOrderIntentId": str(payload.get("orderIntentId") or ""),
            "sourceClientOrderId": str(payload.get("clientOrderId") or ""),
            "sourceBrokerOrderId": str(payload.get("brokerOrderId") or ""),
            "sourceBrokerEventId": event_id,
        },
        now=now,
    )


def _try_replace_stale_order(paper_gateway: PaperOrderGateway, outbox: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any] | None:
    if not bool(payload.get("cancelAndReplaceEnabled") or payload.get("cancel_and_replace_enabled") or False):
        return None
    replacement_count = int(payload.get("replacementCount") or payload.get("replacement_count") or 0)
    maximum_replacements = int(payload.get("maximumReplacementCount") or payload.get("maxReplacementCount") or 0)
    if replacement_count >= maximum_replacements:
        return None
    replace_order = getattr(paper_gateway.broker, "replace_order", None)
    if not callable(replace_order):
        return None
    broker_order_id = str(outbox.get("brokerOrderId") or payload.get("brokerOrderId") or "")
    if not broker_order_id:
        return None
    replacement_client_order_id = f"{str(outbox.get('clientOrderId') or payload.get('clientOrderId'))[:116]}-r{replacement_count + 1}"
    return replace_order(
        broker_order_id,
        quantity=(int(payload["quantity"]) if payload.get("quantity") is not None else None),
        limit_price=_optional_positive(payload.get("replacementLimitPrice") or payload.get("limitPrice")),
        stop_price=_optional_positive(payload.get("replacementStopPrice") or payload.get("stopPrice")),
        client_order_id=replacement_client_order_id,
    )


def _outbox_for_event(repository: MetaStrategyJobRepository, event: Mapping[str, Any]) -> dict[str, Any] | None:
    order_intent_id = str(event.get("orderIntentId") or "")
    if order_intent_id:
        try:
            return repository.outbox_for_order_intent(order_intent_id)
        except KeyError:
            pass
    client_order_id = str(event.get("clientOrderId") or "")
    submitted = repository.submitted_execution_outbox_records()
    for outbox in submitted:
        if client_order_id and str(outbox.get("clientOrderId") or "") == client_order_id:
            return outbox
    broker_order_id = str(event.get("brokerOrderId") or "")
    for outbox in submitted:
        if broker_order_id and str(outbox.get("brokerOrderId") or "") == broker_order_id:
            return outbox
    return None


def _event_with_known_outbox_ownership(event: Mapping[str, Any], outbox: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(outbox.get("payload") or {})
    return {
        **dict(event),
        "algorithmId": event.get("algorithmId") or event.get("algorithm_id") or ALGORITHM_ID,
        "capitalPartitionId": event.get("capitalPartitionId") or event.get("capital_partition_id") or outbox.get("capitalPartitionId") or payload.get("capitalPartitionId") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
        "orderIntentId": event.get("orderIntentId") or outbox.get("orderIntentId") or payload.get("orderIntentId") or "",
        "clientOrderId": event.get("clientOrderId") or outbox.get("clientOrderId") or payload.get("clientOrderId") or "",
        "brokerOrderId": event.get("brokerOrderId") or outbox.get("brokerOrderId") or payload.get("brokerOrderId") or "",
    }


def _reject(
    repository: MetaStrategyJobRepository,
    inventory_repository: MetaStrategySqliteRepository,
    outbox_id: str,
    payload: Mapping[str, Any],
    reason_code: str | tuple[str, ...],
    evaluated_at: datetime,
) -> dict[str, Any]:
    reason_codes = (reason_code,) if isinstance(reason_code, str) else tuple(reason_code)
    release_payload = {
        **dict(payload),
        "algorithmId": ALGORITHM_ID,
        "algorithm_id": ALGORITHM_ID,
        "capitalPartitionId": META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
        "capital_partition_id": META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
        "timestamp": evaluated_at.isoformat(),
    }
    if str(payload.get("algorithmId") or payload.get("algorithm_id") or ALGORITHM_ID) == ALGORITHM_ID and str(payload.get("capitalPartitionId") or payload.get("capital_partition_id") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION) == META_STRATEGY_DEFAULT_CAPITAL_PARTITION:
        inventory_repository.record_order_status({**dict(payload), "orderStatus": "REJECTED", "status": "REJECTED", "timestamp": evaluated_at.isoformat()})
    elif str(payload.get("orderIntentId") or payload.get("order_intent_id") or ""):
        inventory_repository.adjust_reserved_risk(release_payload, target_reserved_risk=0.0, reason="REJECTED_CORRUPTED_META_STRATEGY_OUTBOX")
    repository.update_execution_outbox(outbox_id, status="REJECTED", payload={**dict(payload), "reasonCodes": list(reason_codes)}, now=evaluated_at)
    return {"status": "REJECTED", "submitted": False, "reasonCodes": reason_codes}


def _with_reason_codes(payload: Mapping[str, Any], *reason_codes: str) -> dict[str, Any]:
    existing = tuple(str(code) for code in payload.get("reasonCodes") or payload.get("reason_codes") or ())
    return {**dict(payload), "reasonCodes": list(dict.fromkeys((*existing, *reason_codes)))}


def _outbox_payload(outbox_record: Mapping[str, Any]) -> dict[str, Any]:
    payload = outbox_record.get("payload")
    return dict(payload) if isinstance(payload, Mapping) else {}


def _identity_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    capital_partition_id = str(payload.get("capitalPartitionId") or payload.get("capital_partition_id") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION)
    settings_version = str(payload.get("settingsVersion") or payload.get("settings_version") or "")
    effective_settings_hash = str(payload.get("effectiveSettingsHash") or payload.get("effective_settings_hash") or "")
    strategy_catalog_version = str(payload.get("strategyCatalogVersion") or payload.get("strategy_catalog_version") or META_STRATEGY_STRATEGY_CATALOG_VERSION)
    feature_schema_version = str(payload.get("featureSchemaVersion") or payload.get("feature_schema_version") or META_STRATEGY_FEATURE_SCHEMA_VERSION)
    model_version = str(payload.get("modelVersion") or payload.get("model_version") or META_STRATEGY_MODEL_VERSION)
    decision_id = str(payload.get("decisionId") or payload.get("decision_id") or "")
    job_id = str(payload.get("jobId") or payload.get("job_id") or "")
    event_id = str(payload.get("eventId") or payload.get("event_id") or "")
    order_intent_id = str(payload.get("orderIntentId") or payload.get("order_intent_id") or "")
    correlation_id = str(payload.get("correlationId") or payload.get("correlation_id") or order_intent_id or decision_id or event_id or job_id)
    return {
        "algorithmId": ALGORITHM_ID,
        "algorithm_id": ALGORITHM_ID,
        "capitalPartitionId": capital_partition_id,
        "capital_partition_id": capital_partition_id,
        "settingsVersion": settings_version,
        "settings_version": settings_version,
        "effectiveSettingsHash": effective_settings_hash,
        "effective_settings_hash": effective_settings_hash,
        "strategyCatalogVersion": strategy_catalog_version,
        "strategy_catalog_version": strategy_catalog_version,
        "featureSchemaVersion": feature_schema_version,
        "feature_schema_version": feature_schema_version,
        "modelVersion": model_version,
        "model_version": model_version,
        "decisionId": decision_id,
        "decision_id": decision_id,
        "jobId": job_id,
        "job_id": job_id,
        "eventId": event_id,
        "event_id": event_id,
        "orderIntentId": order_intent_id,
        "order_intent_id": order_intent_id,
        "correlationId": correlation_id,
        "correlation_id": correlation_id,
    }


def _outbox_status_from_gateway(result: PaperOrderGatewayResult) -> str:
    if result.duplicate:
        return "ACKNOWLEDGED"
    if result.status == "ACCEPTED":
        return "ACKNOWLEDGED"
    if result.status == "PARTIALLY_FILLED":
        return "PARTIALLY_FILLED"
    if result.status == "FILLED":
        return "FILLED"
    if result.status in {"CANCELED", "CANCELLED"}:
        return "CANCELLED"
    if result.status in {"REJECTED", "NOT_SUBMITTED"}:
        return "REJECTED"
    return "SUBMITTED" if result.submitted else "RECONCILIATION_REQUIRED"


def _signal(value: Any) -> Signal:
    side = str(value or "BUY").upper()
    return Signal.SELL if side in {"SELL", "SHORT"} else Signal.BUY


def _order_type(payload: Mapping[str, Any]) -> str:
    normalized = str(payload.get("orderType") or payload.get("order_type") or "").upper()
    if normalized in {"MARKET", "LIMIT", "STOP", "STOP_LIMIT", "MARKETABLE_LIMIT"}:
        return normalized
    return "MARKETABLE_LIMIT" if payload.get("limitPrice") else "MARKET"


def _proposal_intent(payload: Mapping[str, Any]) -> str:
    normalized = str(payload.get("intent") or payload.get("orderIntentType") or payload.get("order_intent_type") or "new_entry").lower()
    if normalized in {"protective_exit", "risk_reducing", "end_of_day_liquidation", "reconciliation"}:
        return normalized
    return "new_entry"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_datetime(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return fallback


def _positive_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = 0.01
    return max(0.01, parsed)


def _optional_positive(value: Any) -> float | None:
    if value is None:
        return None
    parsed = _positive_float(value)
    return parsed if parsed > 0 else None


def _hash_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:24]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


__all__ = [
    "META_STRATEGY_PAPER_EXECUTION_VERSION",
    "MetaStrategyPaperOrderReconciliationWorker",
    "MetaStrategyPaperOrderSubmissionWorker",
    "MetaStrategyStaleOrderCancellationWorker",
    "build_meta_strategy_global_order_proposal",
    "cancel_stale_meta_strategy_paper_orders",
    "deterministic_meta_strategy_client_order_id",
    "reconcile_meta_strategy_paper_orders",
    "submit_meta_strategy_outbox_record",
]
