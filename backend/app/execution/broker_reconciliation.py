from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol

from pydantic import Field, field_validator

from backend.app.domain.models import DomainModel, GateStatus, GlobalGateDecision, OrderPlan, Signal, _require_utc
from backend.app.execution.cost_model import record_execution_cost_observation_from_order_log
from backend.app.gates import BrokerAccountSnapshot, BrokerOrderState, BrokerPositionState, GlobalGateEngine, GlobalGateInput, aggregate_global_account_risk


BROKER_RECONCILIATION_VERSION = "broker_reconciliation_v1"
BrokerOrderStatus = Literal["ACCEPTED", "REJECTED", "CANCELED", "PARTIALLY_FILLED", "FILLED"]


class BrokerSubmissionRequest(DomainModel):
    orderPlan: OrderPlan
    decisionTimestampUtc: datetime
    algorithmVersion: str = Field(min_length=1)
    setupId: str = Field(min_length=1)
    gateInputTemplate: GlobalGateInput

    @field_validator("decisionTimestampUtc")
    @classmethod
    def decision_timestamp_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class BrokerOrderAck(DomainModel):
    clientOrderId: str = Field(min_length=1)
    brokerOrderId: str | None = None
    status: BrokerOrderStatus
    acceptedAt: datetime | None = None
    rejectedReason: str | None = None

    @field_validator("acceptedAt")
    @classmethod
    def accepted_at_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None


class BrokerFillUpdate(DomainModel):
    clientOrderId: str = Field(min_length=1)
    filledQuantity: int = Field(ge=0)
    averageFillPrice: float | None = Field(default=None, gt=0)
    status: BrokerOrderStatus
    updatedAt: datetime

    @field_validator("updatedAt")
    @classmethod
    def updated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class ProtectiveOrderPlan(DomainModel):
    clientOrderId: str = Field(min_length=1)
    parentClientOrderId: str = Field(min_length=1)
    quantity: int = Field(ge=0)
    stopPrice: float | None = Field(default=None, gt=0)
    targetPrice: float | None = Field(default=None, gt=0)
    reasonCodes: list[str] = Field(default_factory=list)


class BrokerReconciliationResult(DomainModel):
    reconciliationVersion: str
    clientOrderId: str
    submitted: bool
    duplicate: bool
    brokerAccepted: bool
    brokerStatus: BrokerOrderStatus | Literal["DUPLICATE", "BLOCKED"]
    gateDecision: GlobalGateDecision
    brokerAck: BrokerOrderAck | None = None
    fillUpdate: BrokerFillUpdate | None = None
    protectiveOrder: ProtectiveOrderPlan | None = None
    orderSubmissionTimestamp: datetime | None = None
    actualBrokerFillLifecycle: dict[str, Any] = Field(default_factory=dict)
    executionCostObservation: dict[str, Any] | None = None
    executionCostObservationError: str | None = None
    localPositionCreated: bool
    hardOperationalWarning: bool
    reasonCodes: list[str]
    explanation: str
    evaluatedAt: datetime
    configurationHash: str

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @field_validator("orderSubmissionTimestamp")
    @classmethod
    def order_submission_timestamp_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None


class PaperBrokerClient(Protocol):
    def refresh_account_snapshot(self) -> BrokerAccountSnapshot:
        ...

    def verify_symbol_tradable(self, symbol: str) -> bool:
        ...

    def verify_buying_power(self, order_plan: OrderPlan) -> bool:
        ...

    def submit_order(self, order_plan: OrderPlan, client_order_id: str) -> BrokerOrderAck:
        ...

    def refresh_order(self, client_order_id: str) -> BrokerFillUpdate | None:
        ...

    def refresh_positions(self) -> list[BrokerPositionState]:
        ...

    def refresh_open_orders(self) -> list[BrokerOrderState]:
        ...


@dataclass
class IdempotentOrderLedger:
    records: dict[str, BrokerReconciliationResult] = field(default_factory=dict)

    def get(self, client_order_id: str) -> BrokerReconciliationResult | None:
        return self.records.get(client_order_id)

    def put(self, result: BrokerReconciliationResult) -> BrokerReconciliationResult:
        self.records[result.clientOrderId] = result
        return result


class BrokerReconciliationEngine:
    def __init__(
        self,
        broker: PaperBrokerClient,
        *,
        gate_engine: GlobalGateEngine | None = None,
        ledger: IdempotentOrderLedger | None = None,
    ) -> None:
        self.broker = broker
        self.gate_engine = gate_engine or GlobalGateEngine()
        self.ledger = ledger or IdempotentOrderLedger()

    def submit_once(self, request: BrokerSubmissionRequest) -> BrokerReconciliationResult:
        order_plan = request.orderPlan
        client_order_id = deterministic_client_order_id(
            symbol=order_plan.symbol,
            decision_timestamp=request.decisionTimestampUtc,
            algorithm_version=request.algorithmVersion,
            setup_id=request.setupId,
            side=order_plan.side,
        )
        prior = self.ledger.get(client_order_id)
        if prior is not None:
            return prior.model_copy(update={"duplicate": True, "reasonCodes": sorted(set([*prior.reasonCodes, "broker.idempotent_duplicate_request"]))})

        refreshed = self._refreshed_gate_input(request, client_order_id)
        gate_decision = self.gate_engine.evaluate(refreshed).to_global_gate_decision()
        if not gate_decision.eligible:
            return self.ledger.put(
                self._result(
                    client_order_id=client_order_id,
                    submitted=False,
                    duplicate=False,
                    broker_accepted=False,
                    broker_status="BLOCKED",
                    gate_decision=gate_decision,
                    reason_codes=["broker.submission_blocked_by_refreshed_gates", *gate_decision.reasonCodes],
                    explanation="Order was blocked after refreshing broker account, positions, open orders, tradability, and buying power.",
                    evaluated_at=request.decisionTimestampUtc,
                    local_position_created=False,
                    hard_operational_warning=False,
                )
            )

        submission_requested_at = datetime.now(UTC)
        ack = self.broker.submit_order(order_plan, client_order_id)
        order_submission_timestamp = ack.acceptedAt or submission_requested_at
        fill = self.broker.refresh_order(client_order_id)
        positions = self.broker.refresh_positions()
        open_orders = self.broker.refresh_open_orders()
        divergence = self._detect_divergence(order_plan, ack, fill, positions, open_orders)
        protective = protective_order_for_fill(order_plan, client_order_id, fill) if fill and fill.filledQuantity > 0 else None
        broker_accepted = ack.status in {"ACCEPTED", "PARTIALLY_FILLED", "FILLED"}
        local_position_created = bool(fill and fill.filledQuantity > 0 and broker_accepted and not divergence)
        if ack.status == "REJECTED":
            local_position_created = False
        reason_codes = [
            "broker.submission_confirmed" if broker_accepted else f"broker.submission_{ack.status.lower()}",
            *(fill.reasonCodes if hasattr(fill, "reasonCodes") else []),
        ]
        if fill and fill.status == "PARTIALLY_FILLED":
            reason_codes.append("broker.partial_fill_tracked")
        if protective:
            reason_codes.append("broker.protective_quantity_matches_fill")
        if divergence:
            reason_codes.append("broker.local_broker_state_divergence")
        result = self._result(
            client_order_id=client_order_id,
            submitted=True,
            duplicate=False,
            broker_accepted=broker_accepted,
            broker_status=ack.status,
            gate_decision=gate_decision,
            broker_ack=ack,
            fill_update=fill,
            protective_order=protective,
            order_submission_timestamp=order_submission_timestamp,
            actual_broker_fill_lifecycle=actual_broker_fill_lifecycle(
                request=request,
                client_order_id=client_order_id,
                submission_requested_at=submission_requested_at,
                order_submission_timestamp=order_submission_timestamp,
                ack=ack,
                fill=fill,
                open_orders=open_orders,
            ),
            local_position_created=local_position_created,
            hard_operational_warning=divergence,
            reason_codes=reason_codes,
            explanation="Order submission was reconciled against broker acceptance, fills, positions, and open orders.",
            evaluated_at=request.decisionTimestampUtc,
        )
        return self.ledger.put(self._with_execution_cost_observation(request, result))

    def _refreshed_gate_input(self, request: BrokerSubmissionRequest, client_order_id: str) -> GlobalGateInput:
        order_plan = request.orderPlan
        account_snapshot = self.broker.refresh_account_snapshot()
        positions = self.broker.refresh_positions()
        open_orders = self.broker.refresh_open_orders()
        account_snapshot = account_snapshot.model_copy(update={"positions": positions, "pendingOrders": open_orders})
        risk = aggregate_global_account_risk(account_snapshot, candidateSymbol=order_plan.symbol, candidateSide=order_plan.side)
        broker_state = dict(risk.brokerState)
        broker_state["symbolTradable"] = self.broker.verify_symbol_tradable(order_plan.symbol)
        broker_state["buyingPowerCurrent"] = self.broker.verify_buying_power(order_plan)
        execution_state = dict(request.gateInputTemplate.executionState)
        execution_state.update(
            {
                "riskWithinBudget": risk.riskState.get("totalOpenRiskPercent", 0.0) < self.gate_engine.config.maximumOpenRiskPercent,
                "notionalWithinCap": risk.riskState.get("totalSpyNotionalPercent", 0.0) < self.gate_engine.config.maximumSpyNotionalPercent,
                "protectiveOrderPossible": order_plan.stopPrice is not None and order_plan.targetPrice is not None,
                "uniqueClientOrderId": self.ledger.get(client_order_id) is None,
                "duplicateOrder": any(order.clientOrderId == client_order_id for order in open_orders if hasattr(order, "clientOrderId")),
            }
        )
        return request.gateInputTemplate.model_copy(
            update={
                "accountRiskState": risk.accountRiskState,
                "brokerState": broker_state,
                "riskState": {**request.gateInputTemplate.riskState, **risk.riskState},
                "executionState": execution_state,
                "orderPlan": order_plan,
            }
        )

    def _detect_divergence(
        self,
        order_plan: OrderPlan,
        ack: BrokerOrderAck,
        fill: BrokerFillUpdate | None,
        positions: list[BrokerPositionState],
        open_orders: list[BrokerOrderState],
    ) -> bool:
        if ack.status == "REJECTED":
            return any(position.symbol.upper() == order_plan.symbol.upper() and position.side == order_plan.side for position in positions)
        if fill and fill.filledQuantity > 0:
            return not any(position.symbol.upper() == order_plan.symbol.upper() and position.side == order_plan.side for position in positions)
        if ack.status in {"CANCELED", "REJECTED"}:
            return any(order.symbol.upper() == order_plan.symbol.upper() and order.side == order_plan.side for order in open_orders)
        return False

    def _result(
        self,
        *,
        client_order_id: str,
        submitted: bool,
        duplicate: bool,
        broker_accepted: bool,
        broker_status: BrokerOrderStatus | Literal["DUPLICATE", "BLOCKED"],
        gate_decision: GlobalGateDecision,
        reason_codes: list[str],
        explanation: str,
        evaluated_at: datetime,
        local_position_created: bool,
        hard_operational_warning: bool,
        broker_ack: BrokerOrderAck | None = None,
        fill_update: BrokerFillUpdate | None = None,
        protective_order: ProtectiveOrderPlan | None = None,
        order_submission_timestamp: datetime | None = None,
        actual_broker_fill_lifecycle: dict[str, Any] | None = None,
        execution_cost_observation: dict[str, Any] | None = None,
        execution_cost_observation_error: str | None = None,
    ) -> BrokerReconciliationResult:
        return BrokerReconciliationResult(
            reconciliationVersion=BROKER_RECONCILIATION_VERSION,
            clientOrderId=client_order_id,
            submitted=submitted,
            duplicate=duplicate,
            brokerAccepted=broker_accepted,
            brokerStatus=broker_status,
            gateDecision=gate_decision,
            brokerAck=broker_ack,
            fillUpdate=fill_update,
            protectiveOrder=protective_order,
            orderSubmissionTimestamp=order_submission_timestamp,
            actualBrokerFillLifecycle=actual_broker_fill_lifecycle or {},
            executionCostObservation=execution_cost_observation,
            executionCostObservationError=execution_cost_observation_error,
            localPositionCreated=local_position_created,
            hardOperationalWarning=hard_operational_warning,
            reasonCodes=sorted(set(reason_codes)),
            explanation=explanation,
            evaluatedAt=evaluated_at,
            configurationHash=_result_hash(client_order_id, gate_decision.configurationHash, reason_codes),
        )

    def _with_execution_cost_observation(
        self,
        request: BrokerSubmissionRequest,
        result: BrokerReconciliationResult,
    ) -> BrokerReconciliationResult:
        try:
            observation = record_execution_cost_observation_from_order_log(
                execution_cost_order_log_payload(request=request, result=result)
            )
            return result.model_copy(update={"executionCostObservation": observation})
        except Exception as exc:
            return result.model_copy(update={"executionCostObservationError": str(exc)})


def deterministic_client_order_id(
    *,
    symbol: str,
    decision_timestamp: datetime,
    algorithm_version: str,
    setup_id: str,
    side: Signal | str,
) -> str:
    decision_at = _require_utc(decision_timestamp)
    payload = {
        "symbol": symbol.upper(),
        "decisionTimestampUtc": decision_at.isoformat().replace("+00:00", "Z"),
        "algorithmVersion": algorithm_version,
        "setupId": setup_id,
        "side": Signal(side).value,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:20]
    return f"paper-{digest}"


def actual_broker_fill_lifecycle(
    *,
    request: BrokerSubmissionRequest,
    client_order_id: str,
    submission_requested_at: datetime,
    order_submission_timestamp: datetime,
    ack: BrokerOrderAck,
    fill: BrokerFillUpdate | None,
    open_orders: list[BrokerOrderState],
) -> dict[str, Any]:
    matching_open_order = next((order for order in open_orders if order.clientOrderId == client_order_id), None)
    return {
        "sourceAuthority": "broker",
        "clientOrderId": client_order_id,
        "brokerOrderId": ack.brokerOrderId,
        "decisionTimestamp": request.decisionTimestampUtc.isoformat().replace("+00:00", "Z"),
        "submissionRequestedAt": submission_requested_at.isoformat().replace("+00:00", "Z"),
        "orderSubmissionTimestamp": order_submission_timestamp.isoformat().replace("+00:00", "Z"),
        "brokerAcceptedAt": ack.acceptedAt.isoformat().replace("+00:00", "Z") if ack.acceptedAt else None,
        "brokerStatus": ack.status,
        "filledQuantity": fill.filledQuantity if fill else 0,
        "averageFillPrice": fill.averageFillPrice if fill else None,
        "fillStatus": fill.status if fill else None,
        "fillUpdatedAt": fill.updatedAt.isoformat().replace("+00:00", "Z") if fill else None,
        "openOrderStatus": matching_open_order.status if matching_open_order else None,
        "openOrderSubmittedAt": matching_open_order.submittedAt.isoformat().replace("+00:00", "Z") if matching_open_order else None,
        "requestedQuantity": request.orderPlan.quantity,
        "orderType": request.orderPlan.orderType,
    }


def execution_cost_order_log_payload(
    *,
    request: BrokerSubmissionRequest,
    result: BrokerReconciliationResult,
) -> dict[str, Any]:
    order = request.orderPlan
    mode = "paper" if request.gateInputTemplate.operationalState.get("paperTradingMode", True) else "live"
    lifecycle = result.actualBrokerFillLifecycle
    market_state = request.gateInputTemplate.marketState
    execution_state = request.gateInputTemplate.executionState
    spread_bps = _optional_float(market_state.get("spreadBps") or execution_state.get("spreadBps"))
    spread_dollars = (order.entryPrice * spread_bps / 10000.0) if spread_bps is not None else None
    return {
        "sourceRecordType": "broker_reconciliation_lifecycle",
        "sourceMode": mode,
        "symbol": order.symbol,
        "side": order.side,
        "orderType": order.orderType,
        "status": result.brokerStatus,
        "submitted": result.submitted,
        "clientOrderId": result.clientOrderId,
        "orderIntentId": order.orderPlanId,
        "decisionTimestamp": request.decisionTimestampUtc,
        "orderSubmissionTimestamp": result.orderSubmissionTimestamp,
        "fillTimestamp": lifecycle.get("fillUpdatedAt"),
        "submittedQuantity": order.quantity,
        "filledQuantity": result.fillUpdate.filledQuantity if result.fillUpdate else 0,
        "averageFillPrice": result.fillUpdate.averageFillPrice if result.fillUpdate else None,
        "limitPrice": order.limitPrice,
        "triggerPrice": order.entryPrice if order.orderType == "STOP_LIMIT" else None,
        "midAtDecision": order.entryPrice,
        "midAtSubmit": order.entryPrice,
        "spreadAtDecision": spread_dollars,
        "fees": execution_state.get("fees"),
        "estimatedExecutionCost": execution_state.get("expectedExecutionCost") or execution_state.get("expectedSlippageDollars"),
        "reasonCodes": tuple(result.reasonCodes),
        "rawLifecycleLog": {
            "request": request.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        },
    }


def protective_order_for_fill(order_plan: OrderPlan, parent_client_order_id: str, fill: BrokerFillUpdate | None) -> ProtectiveOrderPlan | None:
    if fill is None or fill.filledQuantity <= 0:
        return None
    return ProtectiveOrderPlan(
        clientOrderId=f"{parent_client_order_id}-protective",
        parentClientOrderId=parent_client_order_id,
        quantity=fill.filledQuantity,
        stopPrice=order_plan.stopPrice,
        targetPrice=order_plan.targetPrice,
        reasonCodes=["broker.protective_quantity_matches_actual_fill"],
    )


def _result_hash(client_order_id: str, gate_hash: str, reason_codes: list[str]) -> str:
    payload = {"clientOrderId": client_order_id, "gateHash": gate_hash, "reasonCodes": sorted(reason_codes)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _optional_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None
