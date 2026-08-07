from __future__ import annotations

import hashlib
import inspect
import json
from datetime import UTC, date, datetime, time, timedelta
from enum import Enum
from typing import Any, Callable, Literal, Protocol

from pydantic import Field, field_validator

from backend.app.domain.models import DomainModel, Signal, _require_utc
from backend.app.execution.cost_model import record_execution_cost_observation_from_order_log
from backend.app.gates import AppliedGlobalGateDecision, GlobalOrderProposal
from backend.app.risk.manager import GlobalPortfolioRiskManager
from backend.app.risk.types import (
    AccountSnapshot,
    GateResult,
    GlobalGateDecision as PortfolioGateDecision,
    GlobalOrderIntent,
    MarketSnapshot,
    PendingOrder,
    PortfolioPosition,
    PortfolioSnapshot,
)


PAPER_ORDER_GATEWAY_VERSION = "paper_order_gateway_v1"
SubmissionMode = Literal["manual", "automatic"]
PaperExecutionMode = Literal["LOCAL_PAPER", "BROKER_PAPER"]
GatewayOrderStatus = Literal["NOT_SUBMITTED", "PENDING_SUBMISSION", "NEW", "OPEN", "ACCEPTED", "REJECTED", "PARTIALLY_FILLED", "FILLED", "CANCELED", "DUPLICATE", "RECOVERED", "REPLACED", "EXPIRED"]
GatewaySnapshotProvider = Callable[..., AccountSnapshot | PortfolioSnapshot | dict[str, Any] | None]


class PaperGatewayBrokerAck(DomainModel):
    clientOrderId: str = Field(min_length=1)
    brokerOrderId: str | None = None
    status: GatewayOrderStatus
    acceptedAt: datetime | None = None
    rejectedReason: str | None = None

    @field_validator("acceptedAt")
    @classmethod
    def accepted_at_must_be_utc(cls, value: datetime | None) -> datetime | None:
        return _require_utc(value) if value else None


class PaperGatewayFill(DomainModel):
    executionMode: PaperExecutionMode = "BROKER_PAPER"
    clientOrderId: str = Field(min_length=1)
    algorithmId: str = Field(min_length=1)
    capitalPartitionId: str | None = None
    accountId: str | None = None
    orderIntentId: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: Signal
    filledQuantity: int = Field(ge=0)
    averageFillPrice: float | None = Field(default=None, gt=0)
    status: GatewayOrderStatus
    filledAt: datetime

    @field_validator("filledAt")
    @classmethod
    def filled_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class PaperGatewayProtectiveOrder(DomainModel):
    executionMode: PaperExecutionMode = "BROKER_PAPER"
    clientOrderId: str = Field(min_length=1)
    parentClientOrderId: str = Field(min_length=1)
    algorithmId: str = Field(min_length=1)
    capitalPartitionId: str | None = None
    accountId: str | None = None
    orderIntentId: str = Field(min_length=1)
    quantity: int = Field(ge=0)
    stopPrice: float | None = Field(default=None, gt=0)
    targetPrice: float | None = Field(default=None, gt=0)
    bracket: bool
    reasonCodes: tuple[str, ...] = ()


class PaperOrderIntentRecord(DomainModel):
    gatewayVersion: str = PAPER_ORDER_GATEWAY_VERSION
    executionMode: PaperExecutionMode = "BROKER_PAPER"
    algorithmId: str = Field(min_length=1)
    capitalPartitionId: str = Field(min_length=1)
    decisionId: str = Field(min_length=1)
    orderIntentId: str = Field(min_length=1)
    clientOrderId: str = Field(min_length=1)
    mode: SubmissionMode
    symbol: str = Field(min_length=1)
    side: Signal
    proposedQuantity: int = Field(ge=0)
    globallyAllowedQuantity: int = Field(ge=0)
    submittedQuantity: int = Field(ge=0)
    triggerPrice: float | None = Field(default=None, gt=0)
    orderType: str = Field(default="LIMIT", min_length=1)
    timeInForce: str = Field(default="DAY", min_length=1)
    limitPrice: float | None = Field(default=None, gt=0)
    stopPrice: float | None = Field(default=None, gt=0)
    stopLimitPrice: float | None = Field(default=None, gt=0)
    targetPrice: float | None = Field(default=None, gt=0)
    profitTargetOrderType: str = Field(default="LIMIT", min_length=1)
    plannedRiskDollars: float = Field(ge=0)
    globalAction: str = Field(min_length=1)
    localGatePassed: bool
    globalGatePassed: bool
    paperAccountVerified: bool = False
    persistedBeforeSubmission: bool = True
    status: GatewayOrderStatus = "PENDING_SUBMISSION"
    reasonCodes: tuple[str, ...] = ()
    createdAt: datetime
    decisionTimestamp: datetime
    staleAfterSeconds: int = Field(default=300, ge=0)
    cancelAndReplaceEnabled: bool = False
    maxReplacementCount: int = Field(default=0, ge=0)
    replacementCount: int = Field(default=0, ge=0)
    protectiveExitEscalationPolicy: str = Field(default="CANCEL_AND_MARKETABLE_LIMIT", min_length=1)
    settingsSnapshot: dict[str, Any] = Field(default_factory=dict)

    @field_validator("createdAt", "decisionTimestamp")
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class PaperOrderGatewayResult(DomainModel):
    gatewayVersion: str = PAPER_ORDER_GATEWAY_VERSION
    executionMode: PaperExecutionMode = "BROKER_PAPER"
    algorithmId: str
    orderIntentId: str
    clientOrderId: str
    mode: SubmissionMode
    submitted: bool
    duplicate: bool
    status: GatewayOrderStatus
    brokerAck: PaperGatewayBrokerAck | None = None
    fill: PaperGatewayFill | None = None
    protectiveOrder: PaperGatewayProtectiveOrder | None = None
    cancelReplacePolicy: str
    staleOrderCancelled: bool = False
    orphanPositionsDetected: tuple[str, ...] = ()
    reasonCodes: tuple[str, ...]
    explanation: str
    evaluatedAt: datetime
    configurationHash: str

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class PaperOrderGatewayStore(Protocol):
    def read_snapshot(self, key: str) -> dict:
        ...

    def write_snapshot(self, key: str, snapshot: dict) -> None:
        ...


class PaperOrderBroker(Protocol):
    def verify_paper_account(self) -> bool:
        ...

    def submit_bracket_order(self, intent: PaperOrderIntentRecord) -> PaperGatewayBrokerAck:
        ...

    def refresh_order(self, client_order_id: str) -> PaperGatewayFill | None:
        ...

    def cancel_order(self, client_order_id: str) -> bool:
        ...

    def refresh_positions(self) -> list[dict[str, Any]]:
        ...


class PaperOrderGateway:
    def __init__(
        self,
        broker: PaperOrderBroker,
        store: PaperOrderGatewayStore,
        *,
        max_decision_age_seconds: int = 300,
        global_risk_manager: GlobalPortfolioRiskManager | None = None,
        execution_mode: PaperExecutionMode = "BROKER_PAPER",
        account_snapshot_provider: GatewaySnapshotProvider | None = None,
        portfolio_snapshot_provider: GatewaySnapshotProvider | None = None,
    ) -> None:
        self.broker = broker
        self.store = store
        self.max_decision_age_seconds = max_decision_age_seconds
        self.global_risk_manager = global_risk_manager or GlobalPortfolioRiskManager()
        self.execution_mode = execution_mode
        self.account_snapshot_provider = account_snapshot_provider
        self.portfolio_snapshot_provider = portfolio_snapshot_provider

    def submit(
        self,
        *,
        proposal: GlobalOrderProposal,
        global_application: AppliedGlobalGateDecision,
        local_gate_passed: bool,
        mode: SubmissionMode,
        evaluated_at: datetime,
    ) -> PaperOrderGatewayResult:
        evaluated_at = _require_utc(evaluated_at)
        client_order_id = _client_order_id_for_proposal(proposal)
        duplicate = _read_optional(self.store, _intent_key(proposal.orderIntentId)) is not None
        if duplicate:
            return self._result(proposal, client_order_id, mode, False, True, "DUPLICATE", ("paper_gateway.duplicate_intent",), "Duplicate order intent was not resubmitted.", evaluated_at)

        intent = self._intent_record(proposal, global_application, local_gate_passed, mode, client_order_id, evaluated_at)
        self.store.write_snapshot(_intent_key(proposal.orderIntentId), intent.model_dump(mode="json"))
        self.store.write_snapshot(
            _client_key(client_order_id),
            {
                "executionMode": self.execution_mode,
                "clientOrderId": client_order_id,
                "orderIntentId": proposal.orderIntentId,
                "algorithmId": proposal.algorithmId,
                "capitalPartitionId": proposal.capitalPartitionId,
            },
        )

        blocker = self._submission_blocker(intent, proposal, global_application, evaluated_at)
        if blocker:
            status, reason, explanation = blocker
            blocked = intent.model_copy(update={"status": status, "reasonCodes": (*intent.reasonCodes, reason)})
            self.store.write_snapshot(_intent_key(proposal.orderIntentId), blocked.model_dump(mode="json"))
            return self._result(proposal, client_order_id, mode, False, False, status, (reason,), explanation, evaluated_at)

        global_risk_decision = self._evaluate_global_portfolio_risk(proposal, intent, evaluated_at)
        self.store.write_snapshot(_global_risk_key(proposal.orderIntentId), global_risk_decision.model_dump(mode="json"))
        if global_risk_decision.status == "denied":
            blocked = intent.model_copy(update={"status": "NOT_SUBMITTED", "reasonCodes": (*intent.reasonCodes, "paper_gateway.global_portfolio_risk_denied")})
            self.store.write_snapshot(_intent_key(proposal.orderIntentId), blocked.model_dump(mode="json"))
            return self._result(proposal, client_order_id, mode, False, False, "NOT_SUBMITTED", ("paper_gateway.global_portfolio_risk_denied",), "Shared global portfolio risk manager denied the order intent before broker submission.", evaluated_at)
        if global_risk_decision.approvedQuantity < intent.submittedQuantity:
            intent = intent.model_copy(
                update={
                    "globallyAllowedQuantity": global_risk_decision.approvedQuantity,
                    "submittedQuantity": global_risk_decision.approvedQuantity,
                    "plannedRiskDollars": global_risk_decision.approvedRiskDollars,
                    "reasonCodes": (*intent.reasonCodes, "paper_gateway.global_portfolio_risk_resized"),
                }
            )
            self.store.write_snapshot(_intent_key(proposal.orderIntentId), intent.model_dump(mode="json"))
            if intent.submittedQuantity <= 0:
                return self._result(proposal, client_order_id, mode, False, False, "NOT_SUBMITTED", ("paper_gateway.global_portfolio_risk_denied",), "Shared global portfolio risk manager reduced quantity to zero.", evaluated_at)

        if not self.broker.verify_paper_account():
            if global_risk_decision.reservationId:
                self.global_risk_manager.release_reservation(global_risk_decision.reservationId)
            blocked = intent.model_copy(update={"status": "NOT_SUBMITTED", "reasonCodes": (*intent.reasonCodes, "paper_gateway.paper_account_unverified")})
            self.store.write_snapshot(_intent_key(proposal.orderIntentId), blocked.model_dump(mode="json"))
            return self._result(proposal, client_order_id, mode, False, False, "NOT_SUBMITTED", ("paper_gateway.paper_account_unverified",), "Paper account verification failed; live trading is never used.", evaluated_at)

        verified = intent.model_copy(update={"paperAccountVerified": True})
        self.store.write_snapshot(_intent_key(proposal.orderIntentId), verified.model_dump(mode="json"))
        ack = self.broker.submit_bracket_order(verified)
        fill = self.broker.refresh_order(client_order_id)
        if fill is not None:
            fill = fill.model_copy(
                update={
                    "algorithmId": verified.algorithmId,
                    "capitalPartitionId": verified.capitalPartitionId,
                    "orderIntentId": verified.orderIntentId,
                    "symbol": verified.symbol,
                    "side": verified.side,
                }
            )
        protective = _protective_order(verified, fill)
        submitted = ack.status != "REJECTED"
        status = fill.status if fill else ack.status
        reason_codes = ["paper_gateway.submitted"] if submitted else ["paper_gateway.broker_rejected"]
        if fill and fill.status == "PARTIALLY_FILLED":
            reason_codes.append("paper_gateway.partial_fill_mapped_to_intent")
        if fill:
            self.store.write_snapshot(_fill_key(fill.clientOrderId), fill.model_dump(mode="json"))
        if protective:
            self.store.write_snapshot(_protective_key(protective.clientOrderId), protective.model_dump(mode="json"))
        final_intent = verified.model_copy(update={"status": status, "reasonCodes": tuple(reason_codes)})
        self.store.write_snapshot(_intent_key(proposal.orderIntentId), final_intent.model_dump(mode="json"))
        result = self._result(proposal, client_order_id, mode, submitted, False, status, tuple(reason_codes), "Paper order submission was reconciled through the shared gateway.", evaluated_at, ack=ack, fill=fill, protective=protective)
        self.store.write_snapshot(_result_key(proposal.orderIntentId), result.model_dump(mode="json"))
        self._record_execution_cost_observation(verified, result)
        if global_risk_decision.reservationId:
            if submitted and ack.brokerOrderId:
                self.global_risk_manager.commit_reservation(global_risk_decision.reservationId, broker_order_id=ack.brokerOrderId)
            elif not submitted:
                self.global_risk_manager.release_reservation(global_risk_decision.reservationId)
        return result

    def cancel_stale_orders(self, *, evaluated_at: datetime) -> tuple[PaperOrderGatewayResult, ...]:
        evaluated_at = _require_utc(evaluated_at)
        results = []
        for key, payload in _store_items(self.store):
            if not key.startswith("paper_order_gateway.intent."):
                continue
            intent = PaperOrderIntentRecord.model_validate(payload)
            if intent.status not in {"PENDING_SUBMISSION", "ACCEPTED", "PARTIALLY_FILLED"}:
                continue
            if (evaluated_at - intent.createdAt) <= timedelta(seconds=intent.staleAfterSeconds):
                continue
            canceled = self.broker.cancel_order(intent.clientOrderId)
            status = "CANCELED" if canceled else intent.status
            reason_codes = [*intent.reasonCodes, "paper_gateway.stale_order_cancelled"]
            if canceled:
                risk_snapshot = _read_optional(self.store, _global_risk_key(intent.orderIntentId)) or {}
                reservation_id = risk_snapshot.get("reservationId") if isinstance(risk_snapshot, dict) else None
                if reservation_id:
                    self.global_risk_manager.release_reservation(str(reservation_id))
                    reason_codes.append("paper_gateway.global_risk_reservation_released")
                    self.store.write_snapshot(
                        _global_risk_key(intent.orderIntentId),
                        {
                            **risk_snapshot,
                            "reservationReleasedAt": evaluated_at.isoformat().replace("+00:00", "Z"),
                            "reservationReleaseReason": "paper_gateway.stale_order_cancelled",
                        },
                    )
            updated = intent.model_copy(update={"status": status, "reasonCodes": tuple(reason_codes)})
            self.store.write_snapshot(key, updated.model_dump(mode="json"))
            results.append(
                PaperOrderGatewayResult(
                    executionMode=self.execution_mode,
                    algorithmId=intent.algorithmId,
                    orderIntentId=intent.orderIntentId,
                    clientOrderId=intent.clientOrderId,
                    mode=intent.mode,
                    submitted=False,
                    duplicate=False,
                    status=status,
                    cancelReplacePolicy="cancel_stale_unfilled_orders_replace_requires_new_intent",
                    staleOrderCancelled=canceled,
                    reasonCodes=("paper_gateway.stale_order_cancelled",),
                    explanation="Stale paper order was canceled; replacement requires a new order intent.",
                    evaluatedAt=evaluated_at,
                    configurationHash=_hash_payload({"clientOrderId": intent.clientOrderId, "status": status}),
                )
            )
        return tuple(results)

    def recover_from_restart(self, *, evaluated_at: datetime) -> dict[str, Any]:
        evaluated_at = _require_utc(evaluated_at)
        positions = [] if self.execution_mode == "LOCAL_PAPER" else self.broker.refresh_positions()
        known_client_ids = {
            value.get("clientOrderId")
            for key, value in _store_items(self.store)
            if key.startswith("paper_order_gateway.client_order.")
        }
        orphan_positions = tuple(
            str(position.get("positionId") or position.get("clientOrderId") or position)
            for position in positions
            if position.get("clientOrderId") not in known_client_ids
        )
        snapshot = {
            "gatewayVersion": PAPER_ORDER_GATEWAY_VERSION,
            "executionMode": self.execution_mode,
            "recoveredAt": evaluated_at.isoformat(),
            "knownClientOrderIds": sorted(id_ for id_ in known_client_ids if id_),
            "orphanPositionsDetected": orphan_positions,
            "reasonCodes": ["paper_gateway.restart_recovery_completed"],
        }
        self.store.write_snapshot("paper_order_gateway.restart_recovery.latest", snapshot)
        return snapshot

    def _submission_blocker(
        self,
        intent: PaperOrderIntentRecord,
        proposal: GlobalOrderProposal,
        global_application: AppliedGlobalGateDecision,
        evaluated_at: datetime,
    ) -> tuple[GatewayOrderStatus, str, str] | None:
        if not intent.localGatePassed:
            return "NOT_SUBMITTED", "paper_gateway.local_gate_failed", "Mandatory local gates failed."
        if global_application.action in {"REJECT_NEW_ENTRY", "EXIT_ONLY", "EMERGENCY_LIQUIDATE"} and proposal.intent == "new_entry":
            return "NOT_SUBMITTED", "paper_gateway.global_gate_rejected", "Global gate response rejected the new entry."
        if (evaluated_at - proposal.proposedAt) > timedelta(seconds=self.max_decision_age_seconds):
            return "NOT_SUBMITTED", "paper_gateway.stale_decision", "Decision timestamp is stale."
        if intent.submittedQuantity <= 0:
            return "NOT_SUBMITTED", "paper_gateway.zero_quantity", "Zero-quantity orders are not submitted."
        return None

    def _intent_record(
        self,
        proposal: GlobalOrderProposal,
        global_application: AppliedGlobalGateDecision,
        local_gate_passed: bool,
        mode: SubmissionMode,
        client_order_id: str,
        evaluated_at: datetime,
    ) -> PaperOrderIntentRecord:
        submitted_quantity = min(proposal.quantity, global_application.globallyAllowedQuantity)
        return PaperOrderIntentRecord(
            executionMode=self.execution_mode,
            algorithmId=proposal.algorithmId,
            capitalPartitionId=proposal.capitalPartitionId,
            decisionId=proposal.decisionId,
            orderIntentId=proposal.orderIntentId,
            clientOrderId=client_order_id,
            mode=mode,
            symbol=proposal.symbol,
            side=proposal.side,
            proposedQuantity=proposal.quantity,
            globallyAllowedQuantity=global_application.globallyAllowedQuantity,
            submittedQuantity=submitted_quantity,
            triggerPrice=proposal.triggerPrice,
            orderType=str(proposal.entryFormula.get("orderType") or proposal.entryFormula.get("kind") or "LIMIT").upper(),
            timeInForce=str(proposal.entryFormula.get("timeInForce") or proposal.settingsSnapshot.get("timeInForce") or "DAY").upper(),
            limitPrice=proposal.limitPrice,
            stopPrice=proposal.stopPrice,
            stopLimitPrice=_optional_float(proposal.stopFormula.get("stopLimitPrice") or proposal.settingsSnapshot.get("stopLimitPrice")),
            targetPrice=proposal.targetPrice,
            profitTargetOrderType=str(proposal.targetFormula.get("orderType") or "LIMIT").upper(),
            plannedRiskDollars=proposal.plannedRiskDollars,
            globalAction=global_application.action,
            localGatePassed=local_gate_passed,
            globalGatePassed=global_application.globallyAllowedQuantity > 0,
            reasonCodes=("paper_gateway.intent_persisted_before_submission",),
            createdAt=evaluated_at,
            decisionTimestamp=proposal.proposedAt,
            staleAfterSeconds=int(proposal.settingsSnapshot.get("maximumOrderAgeSeconds") or self.max_decision_age_seconds),
            cancelAndReplaceEnabled=bool(proposal.settingsSnapshot.get("cancelAndReplaceEnabled") or False),
            maxReplacementCount=int(proposal.settingsSnapshot.get("maximumReplacementCount") or 0),
            protectiveExitEscalationPolicy=str(proposal.settingsSnapshot.get("protectiveExitEscalationPolicy") or "CANCEL_AND_MARKETABLE_LIMIT"),
            settingsSnapshot=dict(proposal.settingsSnapshot or {}),
        )

    def _evaluate_global_portfolio_risk(self, proposal: GlobalOrderProposal, intent: PaperOrderIntentRecord, evaluated_at: datetime) -> PortfolioGateDecision:
        order_intent = GlobalOrderIntent(
            decisionId=proposal.decisionId,
            clientOrderId=intent.clientOrderId,
            algorithmId=proposal.algorithmId,
            symbol=proposal.symbol,
            side="Buy" if proposal.side == Signal.BUY else "Sell",
            positionEffect=_position_effect_for_proposal(proposal),
            intentType=proposal.intent,
            requestedQuantity=intent.submittedQuantity,
            expectedEntryPrice=proposal.limitPrice or proposal.triggerPrice or 0.01,
            protectiveStopPrice=proposal.stopPrice,
            targetPrice=proposal.targetPrice,
            requestedRiskDollars=proposal.plannedRiskDollars,
            orderType="bracket_limit",
            marketDataTimestamp=proposal.proposedAt,
            generatedAt=proposal.proposedAt,
            expiresAt=proposal.proposedAt + timedelta(seconds=self.max_decision_age_seconds),
            settingsVersion=proposal.configurationHash,
            profileVersion=str(proposal.settingsSnapshot.get("profileVersion") or proposal.settingsSnapshot.get("settings_version") or proposal.configurationHash),
            shortable=proposal.side != Signal.SELL,
        )
        account = self._authoritative_account_snapshot(proposal, evaluated_at)
        if account is None:
            return _denied_risk_decision(
                proposal=proposal,
                account_snapshot_id=f"paper-gateway-account-missing-{proposal.orderIntentId}",
                reason="paper_gateway.authoritative_account_snapshot_missing",
                evaluated_at=evaluated_at,
            )
        self.store.write_snapshot(_global_risk_account_key(proposal.orderIntentId), account.model_dump(mode="json"))
        market = MarketSnapshot(
            marketSnapshotId=f"paper-gateway-market-{proposal.orderIntentId}",
            candleTimestamp=proposal.proposedAt,
            quoteTimestamp=evaluated_at,
            spreadPercent=0.0,
            oneMinuteVolume=max(1, proposal.quantity),
            estimatedSlippagePercent=0.0,
            evaluatedAt=evaluated_at,
        )
        portfolio = self._authoritative_portfolio_snapshot(proposal, evaluated_at)
        if portfolio is None:
            return _denied_risk_decision(
                proposal=proposal,
                account_snapshot_id=account.accountSnapshotId,
                reason="paper_gateway.authoritative_portfolio_snapshot_missing",
                evaluated_at=evaluated_at,
            )
        self.store.write_snapshot(_global_risk_portfolio_key(proposal.orderIntentId), portfolio.model_dump(mode="json"))
        return self.global_risk_manager.evaluate(intent=order_intent, account=account, market=market, portfolio=portfolio, evaluated_at=evaluated_at, reserve=True)

    def _authoritative_account_snapshot(self, proposal: GlobalOrderProposal, evaluated_at: datetime) -> AccountSnapshot | None:
        provided = _call_snapshot_provider(self.account_snapshot_provider, proposal=proposal, evaluated_at=evaluated_at, intent=None)
        if provided is not None:
            return _account_snapshot_from_payload(provided, proposal=proposal, evaluated_at=evaluated_at)
        if _requires_authoritative_snapshots(self.execution_mode, proposal):
            return None
        synthetic_equity = max(1.0, (proposal.limitPrice or proposal.triggerPrice or 1.0) * max(1, proposal.quantity) * 10)
        return AccountSnapshot(
            accountSnapshotId=f"paper-gateway-synthetic-{proposal.orderIntentId}",
            equity=synthetic_equity,
            highWaterEquity=synthetic_equity,
            availableBuyingPower=synthetic_equity,
            observedAt=evaluated_at,
        )

    def _authoritative_portfolio_snapshot(self, proposal: GlobalOrderProposal, evaluated_at: datetime) -> PortfolioSnapshot | None:
        provided = _call_snapshot_provider(self.portfolio_snapshot_provider, proposal=proposal, evaluated_at=evaluated_at, intent=None)
        if provided is not None:
            return _portfolio_snapshot_from_payload(provided, proposal=proposal, evaluated_at=evaluated_at)
        if _requires_authoritative_snapshots(self.execution_mode, proposal):
            return None
        return PortfolioSnapshot()

    def _result(
        self,
        proposal: GlobalOrderProposal,
        client_order_id: str,
        mode: SubmissionMode,
        submitted: bool,
        duplicate: bool,
        status: GatewayOrderStatus,
        reason_codes: tuple[str, ...],
        explanation: str,
        evaluated_at: datetime,
        *,
        ack: PaperGatewayBrokerAck | None = None,
        fill: PaperGatewayFill | None = None,
        protective: PaperGatewayProtectiveOrder | None = None,
    ) -> PaperOrderGatewayResult:
        return PaperOrderGatewayResult(
            executionMode=self.execution_mode,
            algorithmId=proposal.algorithmId,
            orderIntentId=proposal.orderIntentId,
            clientOrderId=client_order_id,
            mode=mode,
            submitted=submitted,
            duplicate=duplicate,
            status=status,
            brokerAck=ack,
            fill=fill,
            protectiveOrder=protective,
            cancelReplacePolicy="cancel_stale_unfilled_orders_replace_requires_new_intent",
            reasonCodes=reason_codes,
            explanation=explanation,
            evaluatedAt=evaluated_at,
            configurationHash=_hash_payload({"clientOrderId": client_order_id, "status": status, "reasonCodes": reason_codes}),
        )

    def _record_execution_cost_observation(self, intent: PaperOrderIntentRecord, result: PaperOrderGatewayResult) -> None:
        try:
            observation = record_execution_cost_observation_from_order_log(
                {
                    "intent": intent,
                    "result": result,
                    "sourceMode": "paper",
                    "orderSubmissionTimestamp": result.brokerAck.acceptedAt if result.brokerAck and result.brokerAck.acceptedAt else result.evaluatedAt,
                }
            )
            self.store.write_snapshot(_execution_cost_observation_key(intent.orderIntentId), observation)
        except Exception as exc:
            self.store.write_snapshot(
                _execution_cost_observation_error_key(intent.orderIntentId),
                {
                    "status": "EXECUTION_COST_OBSERVATION_NOT_RECORDED",
                    "orderIntentId": intent.orderIntentId,
                    "clientOrderId": intent.clientOrderId,
                    "error": str(exc),
                    "recordedAt": evaluated_error_time(),
                },
            )


def deterministic_gateway_client_order_id(proposal: GlobalOrderProposal) -> str:
    payload = {
        "gatewayVersion": PAPER_ORDER_GATEWAY_VERSION,
        "algorithmId": proposal.algorithmId,
        "decisionId": proposal.decisionId,
        "orderIntentId": proposal.orderIntentId,
        "symbol": proposal.symbol.upper(),
        "side": proposal.side,
    }
    return "paper-" + hashlib.sha256(json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:20]


def _call_snapshot_provider(
    provider: GatewaySnapshotProvider | None,
    *,
    proposal: GlobalOrderProposal,
    evaluated_at: datetime,
    intent: GlobalOrderIntent | None,
) -> AccountSnapshot | PortfolioSnapshot | dict[str, Any] | None:
    if provider is None:
        return None
    kwargs = {"proposal": proposal, "evaluated_at": evaluated_at, "intent": intent}
    try:
        signature = inspect.signature(provider)
        accepted = {name: value for name, value in kwargs.items() if name in signature.parameters}
        if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in signature.parameters.values()):
            accepted = kwargs
        return provider(**accepted)
    except (TypeError, ValueError):
        return provider()


def _account_snapshot_from_payload(payload: AccountSnapshot | dict[str, Any], *, proposal: GlobalOrderProposal, evaluated_at: datetime) -> AccountSnapshot | None:
    if isinstance(payload, AccountSnapshot):
        return payload
    if not isinstance(payload, dict):
        return None
    equity = _positive_float(payload.get("equity") or payload.get("accountEquity") or payload.get("portfolioValue"))
    buying_power = _non_negative_float(
        _first_present(payload, "availableBuyingPower", "buyingPower", "usableEntryBuyingPower", "cashBuyingPower", "cash")
    )
    if equity <= 0:
        return None
    return AccountSnapshot(
        accountSnapshotId=str(payload.get("accountSnapshotId") or payload.get("riskSnapshotId") or f"paper-gateway-authoritative-{proposal.orderIntentId}"),
        accountId=str(payload.get("accountId") or "paper-account"),
        equity=equity,
        highWaterEquity=max(equity, _positive_float(payload.get("highWaterEquity") or payload.get("intradayEquityHigh") or equity)),
        availableBuyingPower=buying_power,
        settledCash=_optional_non_negative_float(_first_present(payload, "settledCash", "cash")),
        realizedDailyPnl=_signed_float(payload.get("realizedDailyPnl") or payload.get("realizedPnlToday")),
        unrealizedDailyPnl=_signed_float(payload.get("unrealizedDailyPnl") or payload.get("unrealizedPnlToday") or payload.get("unrealizedPnl")),
        brokerConnected=bool(payload.get("brokerConnected", True)),
        brokerAccountActive=bool(payload.get("brokerAccountActive", True)),
        tradingPermission=bool(payload.get("tradingPermission", True)),
        clockSynchronized=bool(payload.get("clockSynchronized", True)),
        accountSnapshotFresh=bool(payload.get("accountSnapshotFresh", True)),
        localBrokerOrdersReconciled=bool(payload.get("localBrokerOrdersReconciled", True)),
        localBrokerPositionsReconciled=bool(payload.get("localBrokerPositionsReconciled", True)),
        unresolvedSubmissionFailure=bool(payload.get("unresolvedSubmissionFailure", False)),
        brokerRateLimited=bool(payload.get("brokerRateLimited", False)),
        observedAt=_parse_datetime(payload.get("observedAt") or payload.get("evaluatedAt") or payload.get("updatedAt")) or evaluated_at,
    )


def _portfolio_snapshot_from_payload(payload: PortfolioSnapshot | dict[str, Any], *, proposal: GlobalOrderProposal, evaluated_at: datetime) -> PortfolioSnapshot | None:
    if isinstance(payload, PortfolioSnapshot):
        return payload
    if not isinstance(payload, dict):
        return None
    positions = tuple(
        position
        for position in (_portfolio_position_from_payload(item, proposal=proposal) for item in payload.get("positions") or [])
        if position is not None
    )
    pending_orders = tuple(
        order
        for order in (_pending_order_from_payload(item, evaluated_at=evaluated_at) for item in [*(payload.get("orders") or []), *(payload.get("pendingOrders") or [])])
        if order is not None
    )
    return PortfolioSnapshot(
        positions=positions,
        pendingOrders=pending_orders,
        tradesToday=int(_non_negative_float(payload.get("tradesToday"))),
        algorithmTradesToday={str(key): int(_non_negative_float(value)) for key, value in dict(payload.get("algorithmTradesToday") or {}).items()},
        ordersSubmittedInLastMinute=int(_non_negative_float(payload.get("ordersSubmittedInLastMinute"))),
    )


def _portfolio_position_from_payload(payload: Any, *, proposal: GlobalOrderProposal) -> PortfolioPosition | None:
    if not isinstance(payload, dict):
        return None
    raw_quantity = int(_signed_float(payload.get("signedQuantity", payload.get("quantity", payload.get("qty", 0)))))
    if raw_quantity == 0:
        return None
    raw_side = str(payload.get("side") or "").upper()
    side = "short" if raw_side in {"SHORT", "SELL"} or raw_quantity < 0 else "long"
    symbol = str(payload.get("symbol") or proposal.symbol).upper()
    market_value = _non_negative_float(payload.get("marketValue") or payload.get("notional") or abs(raw_quantity) * _positive_float(payload.get("markPrice") or payload.get("averageEntryPrice") or 0.0))
    return PortfolioPosition(
        algorithmId=str(payload.get("algorithmId", payload.get("algorithm_id", proposal.algorithmId))),
        symbol=symbol,
        sector=str(payload["sector"]) if payload.get("sector") else None,
        quantity=abs(raw_quantity),
        marketValue=market_value,
        openRiskDollars=_non_negative_float(payload.get("openRiskDollars") or payload.get("riskDollars") or payload.get("totalOpenRiskDollars")),
        side=side,
    )


def _pending_order_from_payload(payload: Any, *, evaluated_at: datetime) -> PendingOrder | None:
    if not isinstance(payload, dict):
        return None
    status = str(payload.get("status") or payload.get("state") or "").upper()
    if status and status not in {"PENDING", "CLAIMED", "SUBMITTING", "SUBMITTED", "ACCEPTED", "NEW", "OPEN", "PARTIALLY_FILLED"}:
        return None
    quantity = int(_non_negative_float(payload.get("remainingQuantity") or payload.get("quantity") or payload.get("submittedQuantity")))
    symbol = str(payload.get("symbol") or "").upper()
    if quantity <= 0 or not symbol:
        return None
    price = _positive_float(payload.get("limitPrice") or payload.get("triggerPrice") or payload.get("averageFillPrice") or 0.01)
    return PendingOrder(
        algorithmId=str(payload.get("algorithmId", payload.get("algorithm_id", "unknown_algorithm"))),
        symbol=symbol,
        side="Sell" if str(payload.get("side") or "").lower().startswith("s") else "Buy",
        quantity=quantity,
        notional=_non_negative_float(payload.get("notional") or quantity * price),
        riskDollars=_non_negative_float(payload.get("plannedRiskDollars") or payload.get("riskDollars")),
        decisionId=str(payload.get("decisionId") or payload.get("orderIntentId") or payload.get("clientOrderId") or "unknown_decision"),
        clientOrderId=str(payload["clientOrderId"]) if payload.get("clientOrderId") else None,
        intentKey=str(payload.get("orderIntentId") or payload.get("clientOrderId") or _hash_payload(payload)),
        submittedAt=_parse_datetime(payload.get("submittedAt") or payload.get("createdAt") or payload.get("updatedAt")) or evaluated_at,
    )


def _requires_authoritative_snapshots(execution_mode: PaperExecutionMode, proposal: GlobalOrderProposal) -> bool:
    return execution_mode == "LOCAL_PAPER" and proposal.algorithmId == "voting_ensemble"


def _denied_risk_decision(
    *,
    proposal: GlobalOrderProposal,
    account_snapshot_id: str,
    reason: str,
    evaluated_at: datetime,
) -> PortfolioGateDecision:
    gate = GateResult(
        gateId=reason,
        gateName="Authoritative Account Snapshot",
        status="fail",
        reason=reason,
        blocksNewEntries=proposal.intent == "new_entry",
        blocksProtectiveExits=False,
        evaluatedAt=evaluated_at,
    )
    return PortfolioGateDecision(
        status="denied",
        approvedQuantity=0,
        approvedRiskDollars=0.0,
        failedGates=(gate,),
        accountSnapshotId=account_snapshot_id,
        evaluatedAt=evaluated_at,
    )


def _client_order_id_for_proposal(proposal: GlobalOrderProposal) -> str:
    configured = proposal.settingsSnapshot.get("clientOrderId") if isinstance(proposal.settingsSnapshot, dict) else None
    if configured:
        return str(configured)
    return deterministic_gateway_client_order_id(proposal)


def _protective_order(intent: PaperOrderIntentRecord, fill: PaperGatewayFill | None) -> PaperGatewayProtectiveOrder | None:
    if fill is None or fill.filledQuantity <= 0:
        return None
    return PaperGatewayProtectiveOrder(
        executionMode=intent.executionMode,
        clientOrderId=f"{intent.clientOrderId}-protective",
        parentClientOrderId=intent.clientOrderId,
        algorithmId=intent.algorithmId,
        capitalPartitionId=intent.capitalPartitionId,
        accountId=fill.accountId,
        orderIntentId=intent.orderIntentId,
        quantity=fill.filledQuantity,
        stopPrice=intent.stopPrice,
        targetPrice=intent.targetPrice,
        bracket=intent.stopPrice is not None and intent.targetPrice is not None,
        reasonCodes=("paper_gateway.protective_order_matches_fill",),
    )


def _intent_key(order_intent_id: str) -> str:
    return f"paper_order_gateway.intent.{order_intent_id}"


def _client_key(client_order_id: str) -> str:
    return f"paper_order_gateway.client_order.{client_order_id}"


def _fill_key(client_order_id: str) -> str:
    return f"paper_order_gateway.fill.{client_order_id}"


def _protective_key(client_order_id: str) -> str:
    return f"paper_order_gateway.protective.{client_order_id}"


def _result_key(order_intent_id: str) -> str:
    return f"paper_order_gateway.result.{order_intent_id}"


def _execution_cost_observation_key(order_intent_id: str) -> str:
    return f"paper_order_gateway.execution_cost_observation.{order_intent_id}"


def _execution_cost_observation_error_key(order_intent_id: str) -> str:
    return f"paper_order_gateway.execution_cost_observation_error.{order_intent_id}"


def _global_risk_key(order_intent_id: str) -> str:
    return f"paper_order_gateway.global_risk.{order_intent_id}"


def _global_risk_account_key(order_intent_id: str) -> str:
    return f"paper_order_gateway.global_risk_account.{order_intent_id}"


def _global_risk_portfolio_key(order_intent_id: str) -> str:
    return f"paper_order_gateway.global_risk_portfolio.{order_intent_id}"


def _position_effect_for_proposal(proposal: GlobalOrderProposal):
    if proposal.intent != "new_entry":
        return "exit_long" if proposal.side == Signal.SELL else "cover_short"
    return "enter_long" if proposal.side == Signal.BUY else "enter_short"


def _read_optional(store: PaperOrderGatewayStore, key: str) -> dict | None:
    try:
        return store.read_snapshot(key)
    except KeyError:
        return None


def _store_items(store: PaperOrderGatewayStore):
    snapshots = getattr(store, "snapshots", None)
    if isinstance(snapshots, dict):
        return list(snapshots.items())
    return []


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(_jsonable(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()[:16]


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _first_present(payload: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        value = payload.get(key)
        if value is not None and value != "":
            return value
    return default


def _positive_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def _non_negative_float(value: Any) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return 0.0
    return parsed if parsed > 0 else 0.0


def _optional_non_negative_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    return _non_negative_float(value)


def _signed_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _require_utc(value)
    if not isinstance(value, str) or not value:
        return None
    try:
        return _require_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def evaluated_error_time() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value
