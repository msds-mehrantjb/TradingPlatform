from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.jobs import MetaStrategyJobRepository
from backend.app.algorithms.meta_strategy.ownership import META_STRATEGY_DEFAULT_CAPITAL_PARTITION
from backend.app.algorithms.meta_strategy.repository import MetaStrategySqliteRepository
from backend.app.domain.models import Signal
from backend.app.execution import PaperOrderGateway, PaperOrderGatewayResult
from backend.app.gates import GlobalGateResponse, GlobalOrderProposal, apply_global_gate_response


META_STRATEGY_PAPER_EXECUTION_VERSION = "meta_strategy_paper_execution_v1"


class MetaStrategyPaperOrderSubmissionWorker:
    def __init__(
        self,
        *,
        repository: MetaStrategyJobRepository,
        inventory_repository: MetaStrategySqliteRepository,
        paper_gateway: PaperOrderGateway,
        global_risk_source: Any | None = None,
        worker_id: str = "meta_strategy.paper_order_submission_worker",
        lease_seconds: int = 300,
    ) -> None:
        self.repository = repository
        self.inventory_repository = inventory_repository
        self.paper_gateway = paper_gateway
        self.global_risk_source = global_risk_source
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

    proposal = build_meta_strategy_global_order_proposal(payload, evaluated_at=evaluated_at)
    response = _global_risk_response(global_risk_source, proposal, evaluated_at=evaluated_at)
    application = apply_global_gate_response(proposal, response)
    if application.globallyAllowedQuantity <= 0 or application.action in {"REJECT_NEW_ENTRY", "EXIT_ONLY", "EMERGENCY_LIQUIDATE"}:
        rejected_payload = {**payload, "reservedRiskDollars": 0.0, "globalApplication": application.model_dump(mode="json")}
        inventory_repository.record_order_intent(rejected_payload)
        inventory_repository.record_order_status({**rejected_payload, "orderStatus": "REJECTED", "status": "REJECTED", "timestamp": evaluated_at.isoformat()})
        repository.update_execution_outbox(
            outbox_id,
            status="REJECTED",
            payload={**payload, "globalApplication": application.model_dump(mode="json"), "reasonCodes": ["meta_strategy.execution.global_risk_rejected"]},
            now=evaluated_at,
        )
        return {"status": "REJECTED", "submitted": False, "reasonCodes": ("meta_strategy.execution.global_risk_rejected",)}

    reserved = min(float(payload.get("reservedRiskDollars") or payload.get("reserved_risk_dollars") or proposal.plannedRiskDollars), application.maximumAdditionalRiskDollars)
    intent_payload = {
        **payload,
        "quantity": application.globallyAllowedQuantity,
        "reservedRiskDollars": reserved,
        "clientOrderId": deterministic_meta_strategy_client_order_id(payload),
        "globalApplication": application.model_dump(mode="json"),
        "timestamp": evaluated_at.isoformat(),
    }
    inventory_repository.record_order_intent(intent_payload)
    repository.update_execution_outbox(
        outbox_id,
        status="SUBMITTING",
        payload={**intent_payload, "reasonCodes": ["meta_strategy.execution.order_intent_persisted_before_broker"]},
        client_order_id=intent_payload["clientOrderId"],
        now=evaluated_at,
    )
    try:
        gateway_result = paper_gateway.submit(
            proposal=proposal,
            global_application=application,
            local_gate_passed=True,
            mode="automatic",
            evaluated_at=evaluated_at,
        )
    except TimeoutError as exc:
        repository.update_execution_outbox(
            outbox_id,
            status="RECONCILIATION_REQUIRED",
            payload={**intent_payload, "reasonCodes": ["meta_strategy.execution.broker_timeout_no_fill_assumed"]},
            client_order_id=intent_payload["clientOrderId"],
            retryable=False,
            error_category="TimeoutError",
            error_details=str(exc),
            now=evaluated_at,
        )
        return {"status": "RECONCILIATION_REQUIRED", "submitted": False, "reasonCodes": ("meta_strategy.execution.broker_timeout_no_fill_assumed",)}
    except Exception as exc:
        repository.update_execution_outbox(
            outbox_id,
            status="RETRY",
            payload={**intent_payload, "reasonCodes": ["meta_strategy.execution.retryable_broker_error"]},
            client_order_id=intent_payload["clientOrderId"],
            retryable=True,
            error_category=type(exc).__name__,
            error_details=str(exc),
            now=evaluated_at,
        )
        return {"status": "RETRY", "submitted": False, "reasonCodes": ("meta_strategy.execution.retryable_broker_error",)}

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
    repository.update_execution_outbox(
        outbox_id,
        status=status,
        payload={**intent_payload, "gatewayResult": result_payload, "reasonCodes": list(gateway_result.reasonCodes)},
        client_order_id=gateway_result.clientOrderId,
        broker_order_id=broker_order_id,
        now=evaluated_at,
    )
    if gateway_result.fill and gateway_result.fill.filledQuantity > 0:
        _apply_fill_event(repository, inventory_repository, outbox_id, gateway_result.fill.model_dump(mode="json"), evaluated_at=evaluated_at)
    return {"status": status, "submitted": gateway_result.submitted, "reasonCodes": tuple(gateway_result.reasonCodes)}


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
        recorded = repository.record_broker_event(event, now=reconciled_at)
        if recorded["status"] == "QUARANTINED":
            quarantined += 1
            continue
        if recorded["duplicate"]:
            duplicate += 1
            continue
        processed += 1
        outbox = _outbox_for_event(repository, event)
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
        _apply_broker_event(repository, inventory_repository, outbox, event, reconciled_at=reconciled_at)
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
    stale = repository.stale_execution_outbox_records(now=evaluated_at, stale_seconds=stale_seconds)
    cancelled = 0
    for outbox in stale:
        client_order_id = str(outbox.get("clientOrderId") or "")
        if not client_order_id:
            continue
        ok = paper_gateway.broker.cancel_order(client_order_id)
        status = "CANCELLED" if ok else "RECONCILIATION_REQUIRED"
        payload = {**outbox["payload"], "reasonCodes": ["meta_strategy.execution.stale_order_cancelled" if ok else "meta_strategy.execution.stale_order_cancel_unknown"]}
        repository.update_execution_outbox(outbox["outboxId"], status=status, payload=payload, now=evaluated_at)
        inventory_repository.record_order_status({**payload, "clientOrderId": client_order_id, "orderStatus": "CANCELLED" if ok else "UNKNOWN", "status": "CANCELLED" if ok else "UNKNOWN", "timestamp": evaluated_at.isoformat()})
        repository.record_reconciliation_evidence("STALE_ORDER_CANCELLATION", payload, client_order_id=client_order_id, order_intent_id=str(outbox["orderIntentId"]), status=status, now=evaluated_at)
        cancelled += 1 if ok else 0
    return {"status": "OK", "cancelled": cancelled, "reasonCodes": ("meta_strategy.execution.stale_order_cancellation_completed",)}


def build_meta_strategy_global_order_proposal(payload: Mapping[str, Any], *, evaluated_at: datetime) -> GlobalOrderProposal:
    side = _signal(payload.get("side"))
    quantity = max(0, int(payload.get("quantity") or 0))
    settings_version = str(payload.get("settingsVersion") or payload.get("settings_version") or "")
    proposed_at = _as_utc(_parse_datetime(payload.get("createdAt") or payload.get("timestamp"), evaluated_at))
    price = _positive_float(payload.get("limitPrice") or payload.get("entryPrice") or payload.get("price") or 0.01)
    stop = _optional_positive(payload.get("stopPrice"))
    target = _optional_positive(payload.get("targetPrice"))
    proposal_hash = _hash_json(
        {
            "algorithmId": ALGORITHM_ID,
            "decisionId": payload.get("decisionId"),
            "orderIntentId": payload.get("orderIntentId"),
            "settingsVersion": settings_version,
            "quantity": quantity,
            "price": price,
            "stop": stop,
            "target": target,
        }
    )
    return GlobalOrderProposal(
        algorithmId=ALGORITHM_ID,
        capitalPartitionId=str(payload.get("capitalPartitionId") or META_STRATEGY_DEFAULT_CAPITAL_PARTITION),
        decisionId=str(payload.get("decisionId") or ""),
        orderIntentId=str(payload.get("orderIntentId") or payload.get("order_intent_id") or ""),
        intent="new_entry",
        symbol=str(payload.get("symbol") or "UNKNOWN").upper(),
        side=side,
        quantity=quantity,
        triggerPrice=price,
        limitPrice=price,
        stopPrice=stop,
        targetPrice=target,
        plannedRiskDollars=float(payload.get("reservedRiskDollars") or payload.get("riskDollars") or 0.0),
        settingsSnapshot={"settingsVersion": settings_version, "paperOnly": True},
        entryFormula={"kind": "bracket_limit", "timeInForce": "day"},
        stopFormula={"stopPrice": stop, "policy": "protective_stop"},
        targetFormula={"targetPrice": target, "policy": "limit_target"},
        strategyStateHash=proposal_hash,
        proposedAt=proposed_at,
        sessionDate=proposed_at.date(),
        configurationHash=proposal_hash,
    )


def deterministic_meta_strategy_client_order_id(payload: Mapping[str, Any]) -> str:
    stable = {
        "algorithmId": ALGORITHM_ID,
        "decisionId": payload.get("decisionId"),
        "orderIntentId": payload.get("orderIntentId") or payload.get("order_intent_id"),
        "symbol": str(payload.get("symbol") or "").upper(),
        "side": str(payload.get("side") or "").upper(),
    }
    return "meta-paper-" + hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:18]


def _apply_broker_event(
    repository: MetaStrategyJobRepository,
    inventory_repository: MetaStrategySqliteRepository,
    outbox: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    reconciled_at: datetime,
) -> None:
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
    payload = {**outbox["payload"], "brokerEvent": dict(event), "reasonCodes": [f"meta_strategy.execution.broker_event_{mapped.lower()}"]}
    repository.update_execution_outbox(
        str(outbox["outboxId"]),
        status=mapped,
        payload=payload,
        client_order_id=str(event.get("clientOrderId") or outbox.get("clientOrderId") or ""),
        broker_order_id=str(event.get("brokerOrderId") or outbox.get("brokerOrderId") or ""),
        now=reconciled_at,
    )
    if mapped in {"PARTIALLY_FILLED", "FILLED"} and float(event.get("filledQuantity") or 0) > 0:
        _apply_fill_event(repository, inventory_repository, str(outbox["outboxId"]), event, evaluated_at=reconciled_at)
    if mapped in {"CANCELLED", "EXPIRED", "REJECTED"}:
        inventory_repository.record_order_status(
            {
                **payload,
                "orderStatus": mapped,
                "status": mapped,
                "clientOrderId": str(event.get("clientOrderId") or outbox.get("clientOrderId") or ""),
                "brokerOrderId": str(event.get("brokerOrderId") or outbox.get("brokerOrderId") or ""),
                "timestamp": reconciled_at.isoformat(),
            }
        )
    repository.record_reconciliation_evidence("BROKER_EVENT_RECONCILED", payload, client_order_id=str(event.get("clientOrderId") or ""), broker_order_id=str(event.get("brokerOrderId") or ""), order_intent_id=str(event.get("orderIntentId") or ""), status=mapped, now=reconciled_at)


def _apply_fill_event(
    repository: MetaStrategyJobRepository,
    inventory_repository: MetaStrategySqliteRepository,
    outbox_id: str,
    event: Mapping[str, Any],
    *,
    evaluated_at: datetime,
) -> None:
    fill_id = str(event.get("brokerFillId") or event.get("fillId") or event.get("brokerEventId") or "")
    if not fill_id:
        return
    inventory_repository.ingest_broker_fill(
        {
            "algorithmId": ALGORITHM_ID,
            "capitalPartitionId": META_STRATEGY_DEFAULT_CAPITAL_PARTITION,
            "settingsVersion": str(event.get("settingsVersion") or "phase9-settings"),
            "decisionId": str(event.get("decisionId") or "decision-1"),
            "jobId": str(event.get("jobId") or ""),
            "eventId": str(event.get("brokerEventId") or fill_id),
            "orderIntentId": str(event.get("orderIntentId") or ""),
            "clientOrderId": str(event.get("clientOrderId") or ""),
            "brokerOrderId": str(event.get("brokerOrderId") or ""),
            "brokerFillId": fill_id,
            "correlationId": str(event.get("correlationId") or event.get("orderIntentId") or fill_id),
            "symbol": str(event.get("symbol") or "UNKNOWN").upper(),
            "side": str(event.get("side") or "BUY").upper(),
            "filledQuantity": float(event.get("filledQuantity") or 0),
            "fillPrice": float(event.get("averageFillPrice") or event.get("fillPrice") or 0.01),
            "timestamp": str(event.get("timestamp") or evaluated_at.isoformat()),
        }
    )
    repository.record_reconciliation_evidence("FILL_APPLIED_TO_INVENTORY", dict(event), client_order_id=str(event.get("clientOrderId") or ""), broker_order_id=str(event.get("brokerOrderId") or ""), order_intent_id=str(event.get("orderIntentId") or ""), status="FILLED", now=evaluated_at)


def _global_risk_response(source: Any | None, proposal: GlobalOrderProposal, *, evaluated_at: datetime) -> GlobalGateResponse:
    if source is not None and hasattr(source, "approve_order"):
        response = source.approve_order(proposal)
        if isinstance(response, GlobalGateResponse):
            return response
    return GlobalGateResponse(
        action="ALLOW",
        maximumAllowedQuantity=proposal.quantity,
        maximumAdditionalRiskDollars=proposal.plannedRiskDollars,
        evaluatedAt=evaluated_at,
        configurationHash=f"meta-strategy-paper-risk-{proposal.configurationHash}",
    )


def _broker_events(paper_gateway: PaperOrderGateway) -> tuple[dict[str, Any], ...]:
    broker = paper_gateway.broker
    if hasattr(broker, "list_order_events"):
        return tuple(dict(event) for event in broker.list_order_events())
    return ()


def _outbox_for_event(repository: MetaStrategyJobRepository, event: Mapping[str, Any]) -> dict[str, Any] | None:
    order_intent_id = str(event.get("orderIntentId") or "")
    if order_intent_id:
        try:
            return repository.outbox_for_order_intent(order_intent_id)
        except KeyError:
            pass
    client_order_id = str(event.get("clientOrderId") or "")
    for outbox in repository.submitted_execution_outbox_records():
        if str(outbox.get("clientOrderId") or "") == client_order_id:
            return outbox
    return None


def _reject(
    repository: MetaStrategyJobRepository,
    inventory_repository: MetaStrategySqliteRepository,
    outbox_id: str,
    payload: Mapping[str, Any],
    reason_code: str,
    evaluated_at: datetime,
) -> dict[str, Any]:
    inventory_repository.record_order_status({**dict(payload), "orderStatus": "REJECTED", "status": "REJECTED", "timestamp": evaluated_at.isoformat()})
    repository.update_execution_outbox(outbox_id, status="REJECTED", payload={**dict(payload), "reasonCodes": [reason_code]}, now=evaluated_at)
    return {"status": "REJECTED", "submitted": False, "reasonCodes": (reason_code,)}


def _outbox_payload(outbox_record: Mapping[str, Any]) -> dict[str, Any]:
    payload = outbox_record.get("payload")
    return dict(payload) if isinstance(payload, Mapping) else {}


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
