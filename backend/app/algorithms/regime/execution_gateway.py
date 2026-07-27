"""Regime-owned durable execution outbox and paper-gateway adapter."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from typing import Any, Literal

from backend.app.algorithms.regime.contracts import RegimeRuntimeMode, normalize_regime_runtime_mode
from backend.app.algorithms.regime.persistence import RegimeSqliteRepository
from backend.app.algorithms.regime.position_manager import RegimePositionManager
from backend.app.domain.models import Signal
from backend.app.execution import PaperGatewayFill, PaperOrderGateway, PaperOrderGatewayResult
from backend.app.gates import AppliedGlobalGateDecision, GlobalGateResponse, GlobalOrderProposal, apply_global_gate_response


REGIME_EXECUTION_GATEWAY_VERSION = "regime_execution_gateway_v1"
REGIME_EXECUTION_OUTBOX_STATUSES = (
    "pending",
    "risk_reserved",
    "submitting",
    "submitted",
    "acknowledged",
    "partially_filled",
    "filled",
    "cancel_requested",
    "cancelled",
    "rejected",
    "expired",
    "reconciliation_required",
)


@dataclass(frozen=True)
class RegimeExecutionResult:
    algorithm_id: Literal["regime"]
    order_intent_id: str
    status: str
    submitted: bool
    duplicate: bool
    reason_codes: tuple[str, ...]
    gateway_result: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "algorithmId": self.algorithm_id,
            "orderIntentId": self.order_intent_id,
            "status": self.status,
            "submitted": self.submitted,
            "duplicate": self.duplicate,
            "reasonCodes": list(self.reason_codes),
            "gatewayResult": self.gateway_result,
        }


class RegimePaperGatewayStore:
    """Paper gateway store backed by Regime-owned runtime snapshots."""

    def __init__(self, repository: RegimeSqliteRepository, identity: dict[str, Any]) -> None:
        self.repository = repository
        self.identity = identity
        self.snapshots: dict[str, dict[str, Any]] = {}

    def read_snapshot(self, key: str) -> dict[str, Any]:
        if key in self.snapshots:
            return self.snapshots[key]
        snapshot = self.repository.read_runtime_snapshot(self.identity, key)
        if snapshot is None:
            raise KeyError(key)
        self.snapshots[key] = snapshot
        return snapshot

    def write_snapshot(self, key: str, snapshot: dict[str, Any]) -> None:
        self.snapshots[key] = dict(snapshot)
        self.repository.write_runtime_snapshot(self.identity, key, dict(snapshot))


def process_regime_execution_outbox_once(
    *,
    repository: RegimeSqliteRepository,
    identity: dict[str, Any],
    paper_gateway: PaperOrderGateway,
    evaluated_at: datetime | None = None,
) -> RegimeExecutionResult | None:
    evaluated_at = _as_utc(evaluated_at or datetime.now(UTC))
    records = repository.pending_execution_outbox_records(identity)
    if not records:
        return None
    return submit_regime_outbox_record(
        repository=repository,
        identity=identity,
        paper_gateway=paper_gateway,
        outbox_record=records[0],
        evaluated_at=evaluated_at,
    )


def submit_regime_outbox_record(
    *,
    repository: RegimeSqliteRepository,
    identity: dict[str, Any],
    paper_gateway: PaperOrderGateway,
    outbox_record: dict[str, Any],
    evaluated_at: datetime,
) -> RegimeExecutionResult:
    evaluated_at = _as_utc(evaluated_at)
    order_intent = _order_intent_from_outbox(outbox_record)
    order_intent_id = str(order_intent.get("orderIntentId") or order_intent.get("order_intent_id") or outbox_record.get("orderIntentId") or "")
    if not order_intent_id:
        raise ValueError("Regime execution outbox record requires orderIntentId")
    raw_mode = identity.get("runtimeMode") or identity.get("runtime_mode") or outbox_record.get("runtimeMode") or ""
    if str(raw_mode).lower() == "live" or str(outbox_record.get("runtimeMode") or "").lower() == "live":
        return _terminal(
            repository,
            identity,
            order_intent_id,
            "rejected",
            ("regime.execution.live_mode_rejected",),
            {"paperOnly": True},
        )
    try:
        mode = normalize_regime_runtime_mode(raw_mode).value
    except ValueError:
        return _terminal(
            repository,
            identity,
            order_intent_id,
            "rejected",
            ("regime.execution.runtime_mode_rejected",),
            {"paperOnly": True},
        )
    if mode != RegimeRuntimeMode.PAPER.value:
        return _terminal(
            repository,
            identity,
            order_intent_id,
            "rejected",
            ("regime.execution.paper_runtime_required",),
            {"runtimeMode": mode, "paperOnly": True},
        )

    existing_status = str(outbox_record.get("processingStatus") or "")
    if existing_status in {"filled", "cancelled", "rejected", "expired"}:
        return RegimeExecutionResult("regime", order_intent_id, existing_status, False, True, ("regime.execution.terminal_outbox_not_resubmitted",), None)

    local_risk_result, local_risk_failure = _local_risk_result_or_failure(repository, identity, order_intent, order_intent_id, evaluated_at)
    if local_risk_failure is not None:
        reason_code, details = local_risk_failure
        return _terminal(repository, identity, order_intent_id, "rejected", (reason_code,), details)

    proposal = build_regime_global_order_proposal(order_intent, identity=identity, evaluated_at=evaluated_at)
    application = _global_application_from_proposal(proposal, evaluated_at=evaluated_at)
    timings = {"decisionToRiskMs": _latency_ms(proposal.proposedAt, evaluated_at)}
    base_payload = {
        **outbox_record,
        "immutableProposal": proposal.model_dump(mode="json"),
        "localRiskResult": local_risk_result,
        "globalApplication": application.model_dump(mode="json"),
        "latency": timings,
    }
    repository.update_execution_outbox_status(
        identity,
        order_intent_id,
        status="submitting",
        payload={**base_payload, "reasonCodes": ["regime.execution.local_and_global_transport_ready"]},
    )
    result: PaperOrderGatewayResult
    risk_started = datetime.now(UTC)
    try:
        result = paper_gateway.submit(
            proposal=proposal,
            global_application=application,
            local_gate_passed=bool(local_risk_result and local_risk_result.get("passed")),
            mode="automatic",
            evaluated_at=evaluated_at,
        )
    except Exception as exc:
        repository.update_execution_outbox_status(
            identity,
            order_intent_id,
            status="reconciliation_required",
            payload={**outbox_record, "failureMessage": str(exc), "reasonCodes": ["regime.execution.paper_gateway_connection_interrupted"]},
        )
        return RegimeExecutionResult("regime", order_intent_id, "reconciliation_required", False, False, ("regime.execution.paper_gateway_connection_interrupted",), None)

    gateway_payload = result.model_dump(mode="json")
    risk_snapshot = _optional_gateway_snapshot(paper_gateway, f"paper_order_gateway.global_risk.{order_intent_id}")
    reservation_id = risk_snapshot.get("reservationId") if isinstance(risk_snapshot, dict) else None
    status = _outbox_status_from_gateway(result)
    timings.update(
        {
            "riskToSubmitMs": _latency_ms(risk_started, result.evaluatedAt),
            "submitToAckMs": _latency_ms(result.evaluatedAt, result.brokerAck.acceptedAt if result.brokerAck and result.brokerAck.acceptedAt else result.evaluatedAt),
        }
    )
    if reservation_id:
        repository.update_execution_outbox_status(
            identity,
            order_intent_id,
            status="risk_reserved",
            payload={
                **base_payload,
                "globalRiskDecision": risk_snapshot,
                "reservationId": reservation_id,
                "latency": timings,
                "reasonCodes": ["regime.execution.global_risk_reserved"],
            },
        )
    if result.submitted:
        repository.update_execution_outbox_status(
            identity,
            order_intent_id,
            status="submitted",
            payload={
                **base_payload,
                "globalRiskDecision": risk_snapshot,
                "gatewayResult": gateway_payload,
                "reservationId": reservation_id,
                "latency": timings,
                "reasonCodes": ["regime.execution.paper_order_submitted"],
            },
        )
    repository.update_execution_outbox_status(
        identity,
        order_intent_id,
        status=status,
        payload={
            **outbox_record,
            "immutableProposal": proposal.model_dump(mode="json"),
            "localRiskResult": local_risk_result,
            "globalApplication": application.model_dump(mode="json"),
            "globalRiskDecision": risk_snapshot,
            "gatewayResult": gateway_payload,
            "reservationId": reservation_id,
            "latency": timings,
        },
    )
    reconcile_regime_paper_gateway_result(repository=repository, identity=identity, proposal=proposal, result=result, reconciled_at=evaluated_at)
    return RegimeExecutionResult(
        algorithm_id="regime",
        order_intent_id=order_intent_id,
        status=status,
        submitted=result.submitted,
        duplicate=result.duplicate,
        reason_codes=tuple(str(code) for code in result.reasonCodes),
        gateway_result=gateway_payload,
    )


def cancel_expired_regime_outbox_orders(
    *,
    repository: RegimeSqliteRepository,
    identity: dict[str, Any],
    paper_gateway: PaperOrderGateway,
    evaluated_at: datetime,
) -> tuple[RegimeExecutionResult, ...]:
    evaluated_at = _as_utc(evaluated_at)
    gateway_results = paper_gateway.cancel_stale_orders(evaluated_at=evaluated_at)
    results: list[RegimeExecutionResult] = []
    for result in gateway_results:
        if result.algorithmId != "regime":
            continue
        repository.update_execution_outbox_status(
            identity,
            result.orderIntentId,
            status="cancel_requested",
            payload={"gatewayResult": result.model_dump(mode="json"), "reasonCodes": ["regime.execution.order_ttl_expired_cancel_requested"]},
        )
        repository.update_execution_outbox_status(
            identity,
            result.orderIntentId,
            status="cancelled" if result.staleOrderCancelled else "reconciliation_required",
            payload={"gatewayResult": result.model_dump(mode="json"), "reasonCodes": list(result.reasonCodes)},
        )
        results.append(RegimeExecutionResult("regime", result.orderIntentId, "cancelled" if result.staleOrderCancelled else "reconciliation_required", False, False, tuple(result.reasonCodes), result.model_dump(mode="json")))
    return tuple(results)


def reconcile_regime_paper_gateway_result(
    *,
    repository: RegimeSqliteRepository,
    identity: dict[str, Any],
    proposal: GlobalOrderProposal,
    result: PaperOrderGatewayResult,
    reconciled_at: datetime,
) -> None:
    if result.algorithmId != "regime":
        raise ValueError("Regime reconciliation cannot consume another algorithm's broker event")
    ack = result.brokerAck.model_dump(mode="json") if result.brokerAck else {}
    observation_base = {
        **identity,
        "algorithmId": "regime",
        "decisionId": proposal.decisionId,
        "orderIntentId": proposal.orderIntentId,
        "brokerOrderId": ack.get("brokerOrderId"),
        "clientOrderId": result.clientOrderId,
        "symbol": proposal.symbol,
        "side": proposal.side.value if isinstance(proposal.side, Enum) else str(proposal.side),
        "timestamp": reconciled_at.isoformat().replace("+00:00", "Z"),
        "processingStatus": result.status.lower(),
    }
    repository.copy_broker_observation({**observation_base, "type": "order", "status": result.status, "brokerAck": ack})
    if result.fill and result.fill.filledQuantity > 0:
        fill_payload = result.fill.model_dump(mode="json")
        enriched_fill = {
            **observation_base,
            "type": "fill",
            **fill_payload,
            "decisionId": proposal.decisionId,
            "submittedQuantity": proposal.quantity,
            "stopPrice": proposal.stopPrice,
            "targetPrice": proposal.targetPrice,
            "settingsVersion": proposal.settingsSnapshot.get("settingsVersion"),
            "profileVersion": proposal.settingsSnapshot.get("profileVersion"),
        }
        repository.copy_broker_observation(enriched_fill)
        RegimePositionManager(repository).apply_fill_observation(identity, enriched_fill, settings_snapshot=proposal.settingsSnapshot)
    repository.copy_broker_observation({**observation_base, "type": "reconciliation", "gatewayResult": result.model_dump(mode="json")})


def build_regime_global_order_proposal(order_intent: dict[str, Any], *, identity: dict[str, Any], evaluated_at: datetime) -> GlobalOrderProposal:
    evaluated_at = _as_utc(evaluated_at)
    side_text = str(order_intent.get("side") or "Hold").upper()
    side = Signal.BUY if side_text == "BUY" or side_text == "BUY" else Signal.SELL
    decision_id = str(order_intent.get("decisionId") or order_intent.get("decision_id") or "")
    order_intent_id = str(order_intent.get("orderIntentId") or order_intent.get("order_intent_id") or "")
    entry_price = _positive_float(order_intent.get("entryPrice") or order_intent.get("entry_price") or order_intent.get("limitPrice") or order_intent.get("limit_price") or 0.01)
    stop_price = _optional_positive(order_intent.get("stopPrice") or order_intent.get("stop_price"))
    target_price = _optional_positive(order_intent.get("targetPrice") or order_intent.get("target_price"))
    settings_snapshot = dict(order_intent.get("settingsSnapshot") or {})
    settings_version = str(order_intent.get("settingsVersion") or order_intent.get("settings_version") or settings_snapshot.get("settingsVersion") or "regime_unknown_settings")
    profile_version = str(order_intent.get("profileVersion") or order_intent.get("profile_version") or settings_snapshot.get("profileVersion") or "regime_unknown_profile")
    data_manifest_hash = str(order_intent.get("dataManifestHash") or order_intent.get("data_manifest_hash") or "")
    quantity = int(order_intent.get("quantity") or 0)
    proposed_at = _as_utc(_parse_datetime(order_intent.get("createdAt") or order_intent.get("timestamp"), evaluated_at))
    configuration_hash = _hash_json(
        {
            "algorithmId": "regime",
            "decisionId": decision_id,
            "orderIntentId": order_intent_id,
            "quantity": quantity,
            "entryPrice": entry_price,
            "stopPrice": stop_price,
            "targetPrice": target_price,
            "settingsVersion": settings_version,
            "profileVersion": profile_version,
        }
    )
    return GlobalOrderProposal(
        algorithmId="regime",
        capitalPartitionId=f"regime.{identity.get('accountId') or identity.get('account_id') or 'paper'}.{identity.get('runtimeMode') or identity.get('runtime_mode') or 'paper'}",
        decisionId=decision_id,
        orderIntentId=order_intent_id,
        intent="new_entry" if str(order_intent.get("positionEffect") or order_intent.get("position_effect") or "").startswith("enter") else "risk_reducing",
        symbol=str(order_intent.get("symbol") or identity.get("symbol") or "SPY").upper(),
        side=side,
        quantity=quantity,
        triggerPrice=entry_price,
        limitPrice=entry_price,
        stopPrice=stop_price,
        targetPrice=target_price,
        plannedRiskDollars=float(order_intent.get("riskDollars") or order_intent.get("risk_dollars") or 0.0),
        settingsSnapshot={**settings_snapshot, "settingsVersion": settings_version, "profileVersion": profile_version, "paperOnly": True},
        entryFormula={"kind": _order_kind(settings_snapshot), "timeInForce": "day", "orderTtlSeconds": _order_ttl_seconds(settings_snapshot)},
        stopFormula={"stopPrice": stop_price, "policy": "protective_stop_or_stop_limit"},
        targetFormula={"targetPrice": target_price, "policy": "limit_target"},
        strategyStateHash=data_manifest_hash or configuration_hash,
        proposedAt=proposed_at,
        sessionDate=proposed_at.date(),
        configurationHash=configuration_hash,
    )


def _local_risk_result_or_failure(
    repository: RegimeSqliteRepository,
    identity: dict[str, Any],
    order_intent: dict[str, Any],
    order_intent_id: str,
    evaluated_at: datetime,
) -> tuple[dict[str, Any] | None, tuple[str, dict[str, Any]] | None]:
    decision_id = str(order_intent.get("decisionId") or order_intent.get("decision_id") or "")
    settings_snapshot = dict(order_intent.get("settingsSnapshot") or {})
    settings_version = str(order_intent.get("settingsVersion") or order_intent.get("settings_version") or settings_snapshot.get("settingsVersion") or "")
    if not decision_id or not settings_version:
        return None, (
            "regime.execution.local_risk_identity_missing",
            {"decisionId": decision_id, "orderIntentId": order_intent_id, "settingsVersion": settings_version},
        )
    local_risk = repository.read_latest_local_risk_result(identity, decision_id=decision_id, order_intent_id=order_intent_id)
    if local_risk is None:
        return None, (
            "regime.execution.local_risk_missing",
            {"decisionId": decision_id, "orderIntentId": order_intent_id, "settingsVersion": settings_version},
        )
    if str(local_risk.get("decisionId") or local_risk.get("decision_id")) != decision_id:
        return local_risk, ("regime.execution.local_risk_decision_mismatch", {"localRiskResult": local_risk, "decisionId": decision_id})
    if str(local_risk.get("orderIntentId") or local_risk.get("order_intent_id")) != order_intent_id:
        return local_risk, ("regime.execution.local_risk_order_intent_mismatch", {"localRiskResult": local_risk, "orderIntentId": order_intent_id})
    if str(local_risk.get("settingsVersion") or local_risk.get("settings_version")) != settings_version:
        return local_risk, ("regime.execution.local_risk_settings_mismatch", {"localRiskResult": local_risk, "settingsVersion": settings_version})
    if not bool(local_risk.get("passed")):
        return local_risk, ("regime.execution.local_risk_failed", {"localRiskResult": local_risk})
    expires_at = _parse_datetime(local_risk.get("expiresAt") or local_risk.get("expires_at"), evaluated_at)
    if _as_utc(expires_at) <= evaluated_at:
        return local_risk, ("regime.execution.local_risk_expired", {"localRiskResult": local_risk, "evaluatedAt": evaluated_at.isoformat().replace("+00:00", "Z")})
    approved_quantity = int(local_risk.get("approvedQuantity") or local_risk.get("approved_quantity") or 0)
    order_quantity = int(order_intent.get("quantity") or 0)
    if approved_quantity <= 0:
        return local_risk, ("regime.execution.local_risk_zero_approved_quantity", {"localRiskResult": local_risk})
    if approved_quantity != order_quantity:
        return local_risk, (
            "regime.execution.local_risk_quantity_mismatch",
            {"localRiskResult": local_risk, "orderQuantity": order_quantity, "approvedQuantity": approved_quantity},
        )
    return local_risk, None


def _global_application_from_proposal(proposal: GlobalOrderProposal, *, evaluated_at: datetime) -> AppliedGlobalGateDecision:
    transport_quantity_limit = max(0, int(proposal.quantity))
    transport_risk_limit = max(0.0, float(proposal.plannedRiskDollars))
    response = GlobalGateResponse(
        action="ALLOW",
        maximumAllowedQuantity=transport_quantity_limit,
        maximumAdditionalRiskDollars=transport_risk_limit,
        evaluatedAt=evaluated_at,
        configurationHash=f"regime-pre-gateway-{proposal.configurationHash}",
    )
    return apply_global_gate_response(proposal, response)


def _terminal(
    repository: RegimeSqliteRepository,
    identity: dict[str, Any],
    order_intent_id: str,
    status: str,
    reason_codes: tuple[str, ...],
    payload: dict[str, Any],
) -> RegimeExecutionResult:
    repository.update_execution_outbox_status(identity, order_intent_id, status=status, payload={**payload, "reasonCodes": list(reason_codes)})
    return RegimeExecutionResult("regime", order_intent_id, status, False, False, reason_codes, None)


def _outbox_status_from_gateway(result: PaperOrderGatewayResult) -> str:
    if result.duplicate:
        return "acknowledged"
    if result.status == "ACCEPTED":
        return "acknowledged"
    if result.status == "PARTIALLY_FILLED":
        return "partially_filled"
    if result.status == "FILLED":
        return "filled"
    if result.status == "CANCELED":
        return "cancelled"
    if result.status in {"REJECTED", "NOT_SUBMITTED"}:
        return "rejected"
    return "submitted" if result.submitted else "reconciliation_required"


def _order_intent_from_outbox(outbox_record: dict[str, Any]) -> dict[str, Any]:
    nested = outbox_record.get("orderIntent")
    if isinstance(nested, dict):
        return nested
    return outbox_record


def _optional_gateway_snapshot(gateway: PaperOrderGateway, key: str) -> dict[str, Any] | None:
    try:
        return gateway.store.read_snapshot(key)
    except KeyError:
        return None


def _order_kind(settings_snapshot: dict[str, Any]) -> str:
    execution = settings_snapshot.get("execution") if isinstance(settings_snapshot.get("execution"), dict) else {}
    configured = str(execution.get("orderType") or execution.get("entryOrderType") or "limit").lower()
    if "stop" in configured:
        return "stop_limit"
    return "bracket_limit"


def _order_ttl_seconds(settings_snapshot: dict[str, Any]) -> int:
    execution = settings_snapshot.get("execution") if isinstance(settings_snapshot.get("execution"), dict) else {}
    try:
        return max(1, int(execution.get("orderTimeToLiveSeconds") or execution.get("order_ttl_seconds") or 300))
    except (TypeError, ValueError):
        return 300


def _latency_ms(start: datetime, end: datetime) -> int:
    return max(0, int((_as_utc(end) - _as_utc(start)).total_seconds() * 1000))


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
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


__all__ = [
    "REGIME_EXECUTION_GATEWAY_VERSION",
    "REGIME_EXECUTION_OUTBOX_STATUSES",
    "RegimeExecutionResult",
    "RegimePaperGatewayStore",
    "build_regime_global_order_proposal",
    "cancel_expired_regime_outbox_orders",
    "process_regime_execution_outbox_once",
    "reconcile_regime_paper_gateway_result",
    "submit_regime_outbox_record",
]
