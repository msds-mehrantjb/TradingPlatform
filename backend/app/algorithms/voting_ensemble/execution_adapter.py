from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from math import floor
from typing import Any, Literal

from pydantic import Field, field_validator

from backend.app.algorithms.voting_ensemble.exit_policy import VOTING_ENSEMBLE_DEFAULT_MAX_HOLDING_MINUTES
from backend.app.algorithms.voting_ensemble.intelligence_capture import VotingEnsembleCaptureWriter, capture_operational_event
from backend.app.algorithms.voting_ensemble.ml_contracts import SafeMLInferenceResult
from backend.app.domain.models import DomainModel, EffectiveTradePolicy, GateStatus, GlobalGateDecision, OperatingMode, OrderPlan, Signal, TradeCandidate, _require_utc
from backend.app.execution.broker_reconciliation import BrokerFillUpdate, BrokerOrderAck, PaperBrokerClient, ProtectiveOrderPlan, protective_order_for_fill
from backend.app.gates import BrokerOrderState, BrokerPositionState


VOTING_ENSEMBLE_EXECUTION_ADAPTER_VERSION = "voting_ensemble_execution_adapter_v1"
VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE = "voting_ensemble.execution_state"
VOTING_ENSEMBLE_CLIENT_ORDER_PREFIX = "ve"


class VotingEnsembleExecutionState(DomainModel):
    namespace: Literal["voting_ensemble.execution_state"] = VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE
    clientOrderId: str = Field(min_length=1)
    idempotencyKey: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: Signal
    status: Literal["PLANNED", "SUBMITTED", "ACCEPTED", "REJECTED", "PARTIALLY_FILLED", "FILLED", "CANCELED", "EXPIRED", "RECONCILIATION_REQUIRED", "BLOCKED"]
    orderPlan: dict[str, Any]
    filledQuantity: int = Field(ge=0)
    protectiveOrder: dict[str, Any] | None = None
    cooldownUntil: datetime | None = None
    createdAt: datetime
    updatedAt: datetime
    reasonCodes: list[str] = Field(default_factory=list)

    @field_validator("createdAt", "updatedAt", "cooldownUntil")
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None


class VotingEnsembleExecutionAdapterResult(DomainModel):
    adapterVersion: str
    namespace: Literal["voting_ensemble.execution_state"] = VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE
    clientOrderId: str
    idempotencyKey: str
    submitted: bool
    duplicate: bool
    brokerAccepted: bool
    status: str
    orderPlan: OrderPlan
    brokerAck: BrokerOrderAck | None = None
    fillUpdate: BrokerFillUpdate | None = None
    protectiveOrder: ProtectiveOrderPlan | None = None
    cooldownUntil: datetime | None = None
    blocksAdditionalEntries: bool
    reasonCodes: list[str]
    evaluatedAt: datetime
    configurationHash: str

    @field_validator("evaluatedAt", "cooldownUntil")
    @classmethod
    def result_timestamps_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None


@dataclass
class VotingEnsembleExecutionStateStore:
    namespace: str = VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE
    records_by_client_order_id: dict[str, VotingEnsembleExecutionState] = field(default_factory=dict)
    client_order_by_idempotency_key: dict[str, str] = field(default_factory=dict)
    unknown_symbols: set[str] = field(default_factory=set)
    cooldown_until_by_symbol: dict[str, datetime] = field(default_factory=dict)

    def get_by_idempotency_key(self, idempotency_key: str) -> VotingEnsembleExecutionState | None:
        client_order_id = self.client_order_by_idempotency_key.get(idempotency_key)
        return self.records_by_client_order_id.get(client_order_id or "")

    def get(self, client_order_id: str) -> VotingEnsembleExecutionState | None:
        return self.records_by_client_order_id.get(client_order_id)

    def put(self, state: VotingEnsembleExecutionState) -> VotingEnsembleExecutionState:
        self.records_by_client_order_id[state.clientOrderId] = state
        self.client_order_by_idempotency_key[state.idempotencyKey] = state.clientOrderId
        if state.cooldownUntil:
            self.cooldown_until_by_symbol[state.symbol.upper()] = state.cooldownUntil
        return state

    def entries_blocked(self, symbol: str, evaluated_at: datetime) -> bool:
        normalized = symbol.upper()
        if normalized in self.unknown_symbols:
            return True
        cooldown = self.cooldown_until_by_symbol.get(normalized)
        return bool(cooldown and _utc(evaluated_at) < cooldown)

    def mark_unknown_order_state(self, symbol: str) -> None:
        self.unknown_symbols.add(symbol.upper())


class VotingEnsembleExecutionAdapter:
    def __init__(
        self,
        *,
        state_store: VotingEnsembleExecutionStateStore | None = None,
        capture_writer: VotingEnsembleCaptureWriter | None = None,
        max_order_age_seconds: int = 60,
        max_retries: int = 1,
        execution_cooldown_seconds: int = 60,
    ) -> None:
        self.state_store = state_store or VotingEnsembleExecutionStateStore()
        self.capture_writer = capture_writer
        self.max_order_age_seconds = max_order_age_seconds
        self.max_retries = max_retries
        self.execution_cooldown_seconds = execution_cooldown_seconds

    def translate_candidate_to_order(
        self,
        *,
        candidate: TradeCandidate | None,
        policy: EffectiveTradePolicy,
        gateDecision: GlobalGateDecision,
        decidedAt: datetime,
        sessionDate: date,
        mlDecision: SafeMLInferenceResult | None = None,
        orderType: Literal["LIMIT", "STOP_LIMIT"] = "LIMIT",
        limitOffsetBps: float = 0.0,
        timeInForce: Literal["DAY", "GTC"] = "DAY",
        maximumHoldingMinutes: int = VOTING_ENSEMBLE_DEFAULT_MAX_HOLDING_MINUTES,
    ) -> OrderPlan | None:
        if candidate is None:
            return None
        errors = _entry_validation_errors(candidate, policy, gateDecision, mlDecision)
        if errors:
            return _no_order(candidate, policy, errors, decidedAt, sessionDate, timeInForce=timeInForce, maximumHoldingMinutes=maximumHoldingMinutes)
        quantity = min(candidate.quantity, policy.maxQuantity, floor(policy.maxNotional / max(candidate.entryPrice, 0.01)))
        if quantity <= 0:
            return _no_order(candidate, policy, ["voting_ensemble.order_planner.zero_quantity"], decidedAt, sessionDate, timeInForce=timeInForce, maximumHoldingMinutes=maximumHoldingMinutes)
        limit_price = _limit_price(candidate.signal, candidate.entryPrice, limitOffsetBps)
        return OrderPlan(
            orderPlanId=_order_plan_id(candidate, policy, orderType, limit_price),
            candidateId=candidate.candidateId,
            symbol=candidate.symbol,
            side=candidate.signal,
            orderType=orderType,
            quantity=quantity,
            entryPrice=candidate.entryPrice,
            stopPrice=candidate.stopPrice,
            targetPrice=candidate.targetPrice,
            limitPrice=limit_price,
            maximumHoldingMinutes=maximumHoldingMinutes,
            timeInForce=timeInForce,
            eligible=True,
            validationErrors=[],
            explanation="Voting Ensemble execution adapter translated candidate into a bounded paper entry order.",
            generatedAt=decidedAt,
            sessionDate=sessionDate,
            configurationHash=f"{policy.configurationHash}:{VOTING_ENSEMBLE_EXECUTION_ADAPTER_VERSION}",
        )

    def submit_order_once(
        self,
        *,
        orderPlan: OrderPlan,
        broker: PaperBrokerClient,
        idempotencyKey: str | None = None,
        evaluatedAt: datetime,
    ) -> VotingEnsembleExecutionAdapterResult:
        evaluated_at = _utc(evaluatedAt)
        key = idempotencyKey or orderPlan.orderPlanId
        client_order_id = voting_ensemble_client_order_id(orderPlan=orderPlan, idempotencyKey=key)
        prior = self.state_store.get_by_idempotency_key(key)
        if prior is not None:
            return self._result_from_state(prior, orderPlan, evaluated_at, duplicate=True)
        if self.state_store.entries_blocked(orderPlan.symbol, evaluated_at):
            return self._blocked(orderPlan, client_order_id, key, evaluated_at, ["voting_ensemble.execution_adapter.entries_blocked_by_unknown_state_or_cooldown"])
        validation = _submission_validation_errors(orderPlan)
        if validation:
            return self._blocked(orderPlan, client_order_id, key, evaluated_at, validation)
        if not broker.verify_symbol_tradable(orderPlan.symbol) or not broker.verify_buying_power(orderPlan):
            return self._blocked(orderPlan, client_order_id, key, evaluated_at, ["voting_ensemble.execution_adapter.broker_precheck_failed"])
        ack = broker.submit_order(orderPlan, client_order_id)
        fill = broker.refresh_order(client_order_id)
        protective = protective_order_for_fill(orderPlan, client_order_id, fill) if fill and fill.filledQuantity > 0 else None
        cooldown_until = evaluated_at + timedelta(seconds=self.execution_cooldown_seconds) if ack.status == "REJECTED" else None
        status = _status_from_ack_and_fill(ack, fill)
        state = self.state_store.put(
            VotingEnsembleExecutionState(
                clientOrderId=client_order_id,
                idempotencyKey=key,
                symbol=orderPlan.symbol,
                side=orderPlan.side,
                status=status,
                orderPlan=orderPlan.model_dump(mode="json"),
                filledQuantity=fill.filledQuantity if fill else 0,
                protectiveOrder=protective.model_dump(mode="json") if protective else None,
                cooldownUntil=cooldown_until,
                createdAt=evaluated_at,
                updatedAt=evaluated_at,
                reasonCodes=_submission_reason_codes(ack, fill, protective),
            )
        )
        result = self._result_from_state(state, orderPlan, evaluated_at, broker_ack=ack, fill_update=fill, protective_order=protective)
        self._capture_broker_event(ack, result)
        if fill:
            self._capture_fill_event(fill, result)
        return result

    def process_fill_event(
        self,
        *,
        clientOrderId: str,
        fillUpdate: BrokerFillUpdate,
        evaluatedAt: datetime,
        entriesBlockedByProfile: bool = False,
    ) -> VotingEnsembleExecutionState:
        state = self.state_store.get(clientOrderId)
        if state is None:
            self.state_store.mark_unknown_order_state("SPY")
            raise ValueError("unknown Voting Ensemble order state requires reconciliation")
        order_plan = OrderPlan.model_validate(state.orderPlan)
        protective = protective_order_for_fill(order_plan, clientOrderId, fillUpdate) if fillUpdate.filledQuantity > 0 else None
        reason_codes = [
            *state.reasonCodes,
            "voting_ensemble.execution_adapter.fill_event_processed",
            "voting_ensemble.execution_adapter.protective_exits_survive_entry_blocks" if entriesBlockedByProfile and protective else "",
        ]
        updated = self.state_store.put(
            state.model_copy(
                update={
                    "status": _status_from_fill(fillUpdate),
                    "filledQuantity": fillUpdate.filledQuantity,
                    "protectiveOrder": protective.model_dump(mode="json") if protective else state.protectiveOrder,
                    "updatedAt": _utc(evaluatedAt),
                    "reasonCodes": [code for code in dict.fromkeys(reason_codes) if code],
                }
            )
        )
        self._capture_fill_event(fillUpdate, self._result_from_state(updated, order_plan, _utc(evaluatedAt), fill_update=fillUpdate))
        return updated

    def expire_stale_orders(self, *, evaluatedAt: datetime) -> tuple[VotingEnsembleExecutionState, ...]:
        evaluated_at = _utc(evaluatedAt)
        expired: list[VotingEnsembleExecutionState] = []
        for state in tuple(self.state_store.records_by_client_order_id.values()):
            if state.status not in {"SUBMITTED", "ACCEPTED", "PLANNED"}:
                continue
            if evaluated_at - state.createdAt <= timedelta(seconds=self.max_order_age_seconds):
                continue
            expired.append(
                self.state_store.put(
                    state.model_copy(
                        update={
                            "status": "EXPIRED",
                            "updatedAt": evaluated_at,
                            "reasonCodes": [*state.reasonCodes, "voting_ensemble.execution_adapter.entry_order_expired"],
                        }
                    )
                )
            )
        return tuple(expired)

    def reconcile_broker_state(
        self,
        *,
        openOrders: list[BrokerOrderState],
        positions: list[BrokerPositionState],
        observedAt: datetime,
    ) -> tuple[VotingEnsembleExecutionState, ...]:
        reconciled: list[VotingEnsembleExecutionState] = []
        for order in openOrders:
            if order.algorithmId != "voting_ensemble":
                continue
            if order.clientOrderId and self.state_store.get(order.clientOrderId):
                continue
            self.state_store.mark_unknown_order_state(order.symbol)
            reconciled.append(
                self.state_store.put(
                    VotingEnsembleExecutionState(
                        clientOrderId=order.clientOrderId or f"unknown-{order.symbol}-{len(reconciled)}",
                        idempotencyKey=f"reconciliation:{order.clientOrderId or order.symbol}",
                        symbol=order.symbol,
                        side=order.side,
                        status="RECONCILIATION_REQUIRED",
                        orderPlan=order.model_dump(mode="json"),
                        filledQuantity=order.filledQuantity,
                        createdAt=_utc(observedAt),
                        updatedAt=_utc(observedAt),
                        reasonCodes=["voting_ensemble.execution_adapter.unknown_order_state_reconciliation_required"],
                    )
                )
            )
        for position in positions:
            if position.algorithmId == "voting_ensemble" and position.parentOrderId and self.state_store.get(position.parentOrderId) is None:
                self.state_store.mark_unknown_order_state(position.symbol)
        return tuple(reconciled)

    def _blocked(self, order_plan: OrderPlan, client_order_id: str, key: str, evaluated_at: datetime, reason_codes: list[str]) -> VotingEnsembleExecutionAdapterResult:
        state = self.state_store.put(
            VotingEnsembleExecutionState(
                clientOrderId=client_order_id,
                idempotencyKey=key,
                symbol=order_plan.symbol,
                side=order_plan.side,
                status="BLOCKED",
                orderPlan=order_plan.model_dump(mode="json"),
                filledQuantity=0,
                createdAt=evaluated_at,
                updatedAt=evaluated_at,
                reasonCodes=reason_codes,
            )
        )
        return self._result_from_state(state, order_plan, evaluated_at)

    def _result_from_state(
        self,
        state: VotingEnsembleExecutionState,
        order_plan: OrderPlan,
        evaluated_at: datetime,
        *,
        duplicate: bool = False,
        broker_ack: BrokerOrderAck | None = None,
        fill_update: BrokerFillUpdate | None = None,
        protective_order: ProtectiveOrderPlan | None = None,
    ) -> VotingEnsembleExecutionAdapterResult:
        reason_codes = list(dict.fromkeys([*state.reasonCodes, "voting_ensemble.execution_adapter.idempotent_duplicate_decision" if duplicate else ""]))
        result = VotingEnsembleExecutionAdapterResult(
            adapterVersion=VOTING_ENSEMBLE_EXECUTION_ADAPTER_VERSION,
            clientOrderId=state.clientOrderId,
            idempotencyKey=state.idempotencyKey,
            submitted=state.status in {"SUBMITTED", "ACCEPTED", "PARTIALLY_FILLED", "FILLED", "REJECTED"},
            duplicate=duplicate,
            brokerAccepted=state.status in {"ACCEPTED", "PARTIALLY_FILLED", "FILLED"},
            status=state.status,
            orderPlan=order_plan,
            brokerAck=broker_ack,
            fillUpdate=fill_update,
            protectiveOrder=protective_order or (ProtectiveOrderPlan.model_validate(state.protectiveOrder) if state.protectiveOrder else None),
            cooldownUntil=state.cooldownUntil,
            blocksAdditionalEntries=state.status in {"BLOCKED", "REJECTED", "RECONCILIATION_REQUIRED"} or self.state_store.entries_blocked(order_plan.symbol, evaluated_at),
            reasonCodes=[code for code in reason_codes if code],
            evaluatedAt=evaluated_at,
            configurationHash=_hash({"clientOrderId": state.clientOrderId, "status": state.status, "reasonCodes": reason_codes}),
        )
        self._capture_order_plan(result)
        return result

    def _capture_order_plan(self, result: VotingEnsembleExecutionAdapterResult) -> None:
        if self.capture_writer is None:
            return
        capture_operational_event(
            writer=self.capture_writer,
            event_type="order_plan",
            payload=result.model_dump(mode="json"),
            correlation_id=result.idempotencyKey,
            decision_id=result.orderPlan.orderPlanId,
            order_id=result.clientOrderId,
            settings_hash=result.orderPlan.configurationHash,
            snapshot_timestamp=result.evaluatedAt,
        )

    def _capture_broker_event(self, ack: BrokerOrderAck, result: VotingEnsembleExecutionAdapterResult) -> None:
        if self.capture_writer is None:
            return
        capture_operational_event(
            writer=self.capture_writer,
            event_type="broker_event",
            payload=ack.model_dump(mode="json"),
            correlation_id=result.idempotencyKey,
            decision_id=result.orderPlan.orderPlanId,
            order_id=result.clientOrderId,
            settings_hash=result.orderPlan.configurationHash,
            snapshot_timestamp=result.evaluatedAt,
        )

    def _capture_fill_event(self, fill: BrokerFillUpdate, result: VotingEnsembleExecutionAdapterResult) -> None:
        if self.capture_writer is None:
            return
        capture_operational_event(
            writer=self.capture_writer,
            event_type="fill",
            payload=fill.model_dump(mode="json"),
            correlation_id=result.idempotencyKey,
            decision_id=result.orderPlan.orderPlanId,
            order_id=result.clientOrderId,
            settings_hash=result.orderPlan.configurationHash,
            snapshot_timestamp=result.evaluatedAt,
        )


def translate_voting_ensemble_candidate_to_order(
    *,
    candidate: TradeCandidate | None,
    policy: EffectiveTradePolicy,
    gateDecision: GlobalGateDecision,
    decidedAt: datetime,
    sessionDate: date,
    mlDecision: SafeMLInferenceResult | None = None,
) -> OrderPlan | None:
    return VotingEnsembleExecutionAdapter().translate_candidate_to_order(
        candidate=candidate,
        policy=policy,
        gateDecision=gateDecision,
        decidedAt=decidedAt,
        sessionDate=sessionDate,
        mlDecision=mlDecision,
    )


def voting_ensemble_client_order_id(*, orderPlan: OrderPlan, idempotencyKey: str) -> str:
    payload = {
        "namespace": VOTING_ENSEMBLE_EXECUTION_STATE_NAMESPACE,
        "orderPlanId": orderPlan.orderPlanId,
        "candidateId": orderPlan.candidateId,
        "symbol": orderPlan.symbol.upper(),
        "side": Signal(orderPlan.side).value,
        "idempotencyKey": idempotencyKey,
    }
    return f"{VOTING_ENSEMBLE_CLIENT_ORDER_PREFIX}-{_hash(payload)[:20]}"


def _entry_validation_errors(
    candidate: TradeCandidate,
    policy: EffectiveTradePolicy,
    gate_decision: GlobalGateDecision,
    ml_decision: SafeMLInferenceResult | None,
) -> list[str]:
    errors: list[str] = []
    if not gate_decision.eligible or gate_decision.status == GateStatus.FAIL.value:
        errors.append("voting_ensemble.order_planner.local_gate_block")
    if ml_decision and ml_decision.effectiveMode == OperatingMode.ACTIVE.value and not ml_decision.candidateAccepted:
        errors.append("voting_ensemble.order_planner.ml_filter_block")
    if policy.maxQuantity <= 0:
        errors.append("voting_ensemble.order_planner.max_quantity_zero")
    if policy.maxNotional <= 0:
        errors.append("voting_ensemble.order_planner.max_notional_zero")
    if candidate.quantity <= 0:
        errors.append("voting_ensemble.order_planner.candidate_quantity_zero")
    return errors


def _submission_validation_errors(order_plan: OrderPlan) -> list[str]:
    errors: list[str] = []
    if order_plan.orderType == "MARKET":
        errors.append("voting_ensemble.execution_adapter.market_orders_not_authorized")
    if order_plan.orderType not in {"LIMIT", "STOP_LIMIT"}:
        errors.append("voting_ensemble.execution_adapter.unsupported_order_type")
    if not order_plan.eligible or order_plan.quantity <= 0:
        errors.append("voting_ensemble.execution_adapter.order_plan_ineligible")
    if order_plan.limitPrice is None:
        errors.append("voting_ensemble.execution_adapter.limit_price_required")
    if order_plan.stopPrice is None or order_plan.targetPrice is None:
        errors.append("voting_ensemble.execution_adapter.protective_prices_required")
    return errors


def _no_order(
    candidate: TradeCandidate,
    policy: EffectiveTradePolicy,
    errors: list[str],
    decided_at: datetime,
    session_date: date,
    *,
    timeInForce: Literal["DAY", "GTC"],
    maximumHoldingMinutes: int,
) -> OrderPlan:
    return OrderPlan(
        orderPlanId=f"voting-ensemble-no-order-{candidate.candidateId}",
        candidateId=candidate.candidateId,
        symbol=candidate.symbol,
        side=candidate.signal,
        orderType="NO_ORDER",
        quantity=0,
        entryPrice=candidate.entryPrice,
        stopPrice=candidate.stopPrice,
        targetPrice=candidate.targetPrice,
        limitPrice=None,
        maximumHoldingMinutes=maximumHoldingMinutes,
        timeInForce=timeInForce,
        eligible=False,
        validationErrors=errors,
        explanation="Voting Ensemble execution adapter blocked this new entry.",
        generatedAt=decided_at,
        sessionDate=session_date,
        configurationHash=f"{policy.configurationHash}:{VOTING_ENSEMBLE_EXECUTION_ADAPTER_VERSION}",
    )


def _order_plan_id(candidate: TradeCandidate, policy: EffectiveTradePolicy, order_type: str, limit_price: float) -> str:
    return f"voting-ensemble-order-{_hash({'candidate': candidate.candidateId, 'policy': policy.configurationHash, 'orderType': order_type, 'limitPrice': limit_price})[:16]}"


def _limit_price(side: Signal | str, entry_price: float, limit_offset_bps: float) -> float:
    offset = max(0.0, float(limit_offset_bps)) / 10000.0
    if Signal(side) == Signal.BUY:
        return round(max(0.01, entry_price * (1.0 - offset)), 4)
    return round(entry_price * (1.0 + offset), 4)


def _status_from_ack_and_fill(ack: BrokerOrderAck, fill: BrokerFillUpdate | None) -> str:
    if fill is not None:
        return _status_from_fill(fill)
    return ack.status


def _status_from_fill(fill: BrokerFillUpdate) -> str:
    return "PARTIALLY_FILLED" if fill.status == "PARTIALLY_FILLED" else fill.status


def _submission_reason_codes(ack: BrokerOrderAck, fill: BrokerFillUpdate | None, protective: ProtectiveOrderPlan | None) -> list[str]:
    codes = [f"voting_ensemble.execution_adapter.broker_{ack.status.lower()}"]
    if fill and fill.filledQuantity > 0:
        codes.append("voting_ensemble.execution_adapter.fill_update_processed")
    if fill and fill.status == "PARTIALLY_FILLED":
        codes.append("voting_ensemble.execution_adapter.partial_fill_tracked")
    if protective:
        codes.append("voting_ensemble.execution_adapter.protective_exits_resized_to_fill")
    return codes


def _hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
