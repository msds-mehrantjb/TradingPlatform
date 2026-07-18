from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
from typing import Any, Literal

from backend.app.algorithms.weighted_voting.decision_gates import WeightedVotingGatePipelineResult
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.observability import record_order_execution_observability
from backend.app.algorithms.weighted_voting.order_proposal import WeightedVotingOrderProposal
from backend.app.algorithms.weighted_voting.persistence import WeightedVotingStateStore
from backend.app.algorithms.weighted_voting.rollout import (
    WeightedVotingRolloutFlags,
    WeightedVotingRolloutValidation,
    automatic_submission_allowed,
)
from backend.app.execution import PaperOrderGateway, PaperOrderGatewayResult, deterministic_gateway_client_order_id
from backend.app.gates import AppliedGlobalGateDecision, GlobalOrderProposal


WEIGHTED_VOTING_EXECUTION_GATEWAY_VERSION = "weighted_voting_execution_gateway_v2"
WEIGHTED_VOTING_EXECUTION_NAMESPACE = "weighted_voting.execution_gateway"
WEIGHTED_VOTING_BROKER_CONNECTION_BOUNDARY = "shared_broker_connection"
WEIGHTED_VOTING_EXECUTION_OWNERSHIP = "weighted_voting"

WeightedVotingExecutionStatus = Literal["PENDING_SUBMISSION", "SUBMITTED", "REJECTED", "PARTIALLY_FILLED", "FILLED", "RECONCILED"]


@dataclass(frozen=True)
class WeightedVotingBrokerCommand:
    algorithm_id: Literal["weighted_voting"]
    command_id: str
    decision_id: str
    order_intent_id: str
    client_order_id: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    trigger_price: float | None
    limit_price: float | None
    stop_price: float | None
    target_price: float | None
    time_in_force: str
    capital_partition_id: str
    planned_risk_dollars: float
    strategy_versions: dict[str, str]
    weight_version: str
    settings_version: str
    risk_profile_version: str
    market_snapshot_hash: str
    configuration_hash: str
    accepted_global_action: str
    global_proposal_hash: str
    global_response_hash: str
    created_at: datetime
    expires_at: datetime
    reason_codes: tuple[str, ...]
    gateway_version: str = WEIGHTED_VOTING_EXECUTION_GATEWAY_VERSION
    ownership: str = WEIGHTED_VOTING_EXECUTION_OWNERSHIP
    broker_connection_boundary: str = WEIGHTED_VOTING_BROKER_CONNECTION_BOUNDARY
    submission_status: WeightedVotingExecutionStatus = "PENDING_SUBMISSION"

    def __post_init__(self) -> None:
        if self.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting execution commands cannot be assigned to another algorithm")
        if self.quantity < 0:
            raise ValueError("Weighted Voting execution command quantity must be non-negative")

    def as_dict(self) -> dict[str, Any]:
        return {
            "gatewayVersion": self.gateway_version,
            "algorithmId": self.algorithm_id,
            "commandId": self.command_id,
            "decisionId": self.decision_id,
            "orderIntentId": self.order_intent_id,
            "clientOrderId": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "orderType": self.order_type,
            "triggerPrice": self.trigger_price,
            "limitPrice": self.limit_price,
            "stopPrice": self.stop_price,
            "targetPrice": self.target_price,
            "timeInForce": self.time_in_force,
            "capitalPartitionId": self.capital_partition_id,
            "plannedRiskDollars": self.planned_risk_dollars,
            "strategyVersions": dict(self.strategy_versions),
            "weightVersion": self.weight_version,
            "settingsVersion": self.settings_version,
            "riskProfileVersion": self.risk_profile_version,
            "marketSnapshotHash": self.market_snapshot_hash,
            "configurationHash": self.configuration_hash,
            "acceptedGlobalAction": self.accepted_global_action,
            "globalProposalHash": self.global_proposal_hash,
            "globalResponseHash": self.global_response_hash,
            "createdAt": self.created_at.isoformat(),
            "expiresAt": self.expires_at.isoformat(),
            "reasonCodes": list(self.reason_codes),
            "ownership": self.ownership,
            "brokerConnectionBoundary": self.broker_connection_boundary,
            "submissionStatus": self.submission_status,
        }

    def as_shared_broker_command(self) -> dict[str, Any]:
        payload = self.as_dict()
        payload.update(
            {
                "commandNamespace": WEIGHTED_VOTING_EXECUTION_NAMESPACE,
                "commandType": "submit_bracket_order",
                "brokerConnection": WEIGHTED_VOTING_BROKER_CONNECTION_BOUNDARY,
                "preserveAlgorithmOwnership": True,
                "ownershipMutationAllowed": False,
            }
        )
        return payload


@dataclass(frozen=True)
class WeightedVotingExecutionSubmission:
    command: WeightedVotingBrokerCommand
    status: WeightedVotingExecutionStatus
    submitted_at: datetime
    broker_order_id: str | None = None
    broker_status: str | None = None
    reason_codes: tuple[str, ...] = ("weighted_voting.execution.submitted",)

    def as_dict(self) -> dict[str, Any]:
        return {
            "gatewayVersion": WEIGHTED_VOTING_EXECUTION_GATEWAY_VERSION,
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "command": self.command.as_dict(),
            "status": self.status,
            "brokerOrderId": self.broker_order_id,
            "brokerStatus": self.broker_status,
            "submittedAt": self.submitted_at.isoformat(),
            "reasonCodes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class WeightedVotingExecutionRejection:
    command: WeightedVotingBrokerCommand
    rejected_at: datetime
    broker_status: str
    rejection_reason: str | None
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "gatewayVersion": WEIGHTED_VOTING_EXECUTION_GATEWAY_VERSION,
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "command": self.command.as_dict(),
            "status": "REJECTED",
            "brokerStatus": self.broker_status,
            "rejectionReason": self.rejection_reason,
            "rejectedAt": self.rejected_at.isoformat(),
            "reasonCodes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class WeightedVotingFillAttribution:
    algorithm_id: Literal["weighted_voting"]
    decision_id: str
    order_intent_id: str
    client_order_id: str
    broker_order_id: str | None
    fill_id: str
    symbol: str
    side: str
    filled_quantity: int
    average_fill_price: float | None
    status: str
    filled_at: datetime
    reason_codes: tuple[str, ...]
    gateway_version: str = WEIGHTED_VOTING_EXECUTION_GATEWAY_VERSION

    def __post_init__(self) -> None:
        if self.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting fill attribution cannot be assigned to another algorithm")

    def as_dict(self) -> dict[str, Any]:
        return {
            "gatewayVersion": self.gateway_version,
            "algorithmId": self.algorithm_id,
            "decisionId": self.decision_id,
            "orderIntentId": self.order_intent_id,
            "clientOrderId": self.client_order_id,
            "brokerOrderId": self.broker_order_id,
            "fillId": self.fill_id,
            "symbol": self.symbol,
            "side": self.side,
            "filledQuantity": self.filled_quantity,
            "averageFillPrice": self.average_fill_price,
            "status": self.status,
            "filledAt": self.filled_at.isoformat(),
            "reasonCodes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class WeightedVotingPositionAttribution:
    algorithm_id: Literal["weighted_voting"]
    decision_id: str
    order_intent_id: str
    client_order_id: str
    position_id: str
    symbol: str
    side: str
    quantity: int
    average_entry_price: float | None
    opened_at: datetime
    reason_codes: tuple[str, ...]
    gateway_version: str = WEIGHTED_VOTING_EXECUTION_GATEWAY_VERSION

    def __post_init__(self) -> None:
        if self.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting position attribution cannot be assigned to another algorithm")

    def as_dict(self) -> dict[str, Any]:
        return {
            "gatewayVersion": self.gateway_version,
            "algorithmId": self.algorithm_id,
            "decisionId": self.decision_id,
            "orderIntentId": self.order_intent_id,
            "clientOrderId": self.client_order_id,
            "positionId": self.position_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity": self.quantity,
            "averageEntryPrice": self.average_entry_price,
            "openedAt": self.opened_at.isoformat(),
            "reasonCodes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class WeightedVotingExecutionReconciliation:
    algorithm_id: Literal["weighted_voting"]
    command_id: str
    decision_id: str
    order_intent_id: str
    client_order_id: str
    status: str
    reconciled_at: datetime
    submitted: bool
    broker_order_id: str | None
    fill: WeightedVotingFillAttribution | None
    position: WeightedVotingPositionAttribution | None
    reason_codes: tuple[str, ...]
    gateway_version: str = WEIGHTED_VOTING_EXECUTION_GATEWAY_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "gatewayVersion": self.gateway_version,
            "algorithmId": self.algorithm_id,
            "commandId": self.command_id,
            "decisionId": self.decision_id,
            "orderIntentId": self.order_intent_id,
            "clientOrderId": self.client_order_id,
            "status": self.status,
            "reconciledAt": self.reconciled_at.isoformat(),
            "submitted": self.submitted,
            "brokerOrderId": self.broker_order_id,
            "fill": self.fill.as_dict() if self.fill else None,
            "position": self.position.as_dict() if self.position else None,
            "reasonCodes": list(self.reason_codes),
        }


def build_weighted_voting_broker_command(
    *,
    proposal: GlobalOrderProposal | WeightedVotingOrderProposal,
    global_application: AppliedGlobalGateDecision,
    accepted_at: datetime,
    mode: str = "automatic",
    order_intent_id: str | None = None,
    capital_partition_id: str | None = None,
    planned_risk_dollars: float | None = None,
) -> WeightedVotingBrokerCommand:
    """Convert an accepted Weighted Voting proposal into a shared broker command."""

    if mode not in {"manual", "automatic"}:
        raise ValueError("mode must be manual or automatic")
    _validate_weighted_voting_proposal(proposal)
    _validate_weighted_voting_global_application(global_application)
    decision_id = _proposal_decision_id(proposal)
    if global_application.decisionId != decision_id:
        raise ValueError("Weighted Voting execution command decision does not match the global application")
    resolved_intent = _proposal_order_intent_id(proposal, order_intent_id)
    if global_application.orderIntentId != resolved_intent:
        raise ValueError("Weighted Voting execution command order intent does not match the global application")
    side = _proposal_side(proposal)
    if _side_value(global_application.side) != side:
        raise ValueError("Weighted Voting execution command side does not match the global application")
    proposed_quantity = _proposal_quantity(proposal)
    quantity = min(proposed_quantity, global_application.globallyAllowedQuantity)
    if quantity > proposed_quantity:
        raise ValueError("Weighted Voting execution command cannot increase quantity")

    client_order_id = (
        deterministic_gateway_client_order_id(proposal)
        if isinstance(proposal, GlobalOrderProposal)
        else deterministic_weighted_voting_client_order_id(
            algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
            decision_id=decision_id,
            order_intent_id=resolved_intent,
            symbol=_proposal_symbol(proposal),
            side=side,
            quantity=quantity,
            configuration_hash=_proposal_configuration_hash(proposal),
            global_response_hash=global_application.responseHash,
        )
    )
    command_id = f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.{client_order_id}"
    return WeightedVotingBrokerCommand(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        command_id=command_id,
        decision_id=decision_id,
        order_intent_id=resolved_intent,
        client_order_id=client_order_id,
        symbol=_proposal_symbol(proposal),
        side=side,
        quantity=quantity,
        order_type=_proposal_order_type(proposal),
        trigger_price=_proposal_trigger_price(proposal),
        limit_price=_proposal_limit_price(proposal),
        stop_price=_proposal_stop_price(proposal),
        target_price=_proposal_target_price(proposal),
        time_in_force=_proposal_time_in_force(proposal),
        capital_partition_id=_proposal_capital_partition_id(proposal, capital_partition_id),
        planned_risk_dollars=_proposal_planned_risk(proposal, planned_risk_dollars),
        strategy_versions=_proposal_strategy_versions(proposal),
        weight_version=_proposal_weight_version(proposal),
        settings_version=_proposal_settings_version(proposal),
        risk_profile_version=_proposal_risk_profile_version(proposal),
        market_snapshot_hash=_proposal_market_snapshot_hash(proposal),
        configuration_hash=_proposal_configuration_hash(proposal),
        accepted_global_action=global_application.action,
        global_proposal_hash=global_application.proposalHash,
        global_response_hash=global_application.responseHash,
        created_at=accepted_at,
        expires_at=_proposal_expires_at(proposal, accepted_at),
        reason_codes=tuple(
            dict.fromkeys(
                (
                    "weighted_voting.execution.command_created",
                    *_proposal_reason_codes(proposal),
                    *global_application.rejectionReasons,
                )
            )
        ),
    )


def deterministic_weighted_voting_client_order_id(
    *,
    algorithm_id: str,
    decision_id: str,
    order_intent_id: str,
    symbol: str,
    side: str,
    quantity: int,
    configuration_hash: str,
    global_response_hash: str,
) -> str:
    if algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
        raise ValueError("Weighted Voting client order IDs cannot be generated for another algorithm")
    payload = {
        "gatewayVersion": WEIGHTED_VOTING_EXECUTION_GATEWAY_VERSION,
        "algorithmId": algorithm_id,
        "decisionId": decision_id,
        "orderIntentId": order_intent_id,
        "symbol": symbol.upper(),
        "side": side.upper(),
        "quantity": quantity,
        "configurationHash": configuration_hash,
        "globalResponseHash": global_response_hash,
    }
    return "wv-" + _hash_json(payload)[:24]


def persist_weighted_voting_broker_command(store: WeightedVotingStateStore, command: WeightedVotingBrokerCommand) -> None:
    store.write_snapshot(_command_key(command.client_order_id), command.as_shared_broker_command())
    store.write_snapshot(
        _decision_command_key(command.decision_id),
        {
            "gatewayVersion": WEIGHTED_VOTING_EXECUTION_GATEWAY_VERSION,
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "decisionId": command.decision_id,
            "orderIntentId": command.order_intent_id,
            "clientOrderId": command.client_order_id,
            "commandId": command.command_id,
        },
    )


def record_weighted_voting_submission(
    *,
    store: WeightedVotingStateStore,
    command: WeightedVotingBrokerCommand,
    submitted_at: datetime,
    broker_order_id: str | None = None,
    broker_status: str | None = None,
) -> WeightedVotingExecutionSubmission:
    _validate_command(command)
    persist_weighted_voting_broker_command(store, command)
    record = WeightedVotingExecutionSubmission(
        command=command,
        status="SUBMITTED",
        submitted_at=submitted_at,
        broker_order_id=broker_order_id,
        broker_status=broker_status,
    )
    store.write_snapshot(_submission_key(command.client_order_id), record.as_dict())
    return record


def record_weighted_voting_rejection(
    *,
    store: WeightedVotingStateStore,
    command: WeightedVotingBrokerCommand,
    rejected_at: datetime,
    reason_codes: tuple[str, ...],
    broker_status: str = "REJECTED",
    rejection_reason: str | None = None,
) -> WeightedVotingExecutionRejection:
    _validate_command(command)
    persist_weighted_voting_broker_command(store, command)
    record = WeightedVotingExecutionRejection(
        command=command,
        rejected_at=rejected_at,
        broker_status=broker_status,
        rejection_reason=rejection_reason,
        reason_codes=tuple(dict.fromkeys(("weighted_voting.execution.rejected", *reason_codes))),
    )
    store.write_snapshot(_rejection_key(command.client_order_id), record.as_dict())
    return record


def record_weighted_voting_fill(
    *,
    store: WeightedVotingStateStore,
    command: WeightedVotingBrokerCommand,
    filled_quantity: int,
    average_fill_price: float | None,
    filled_at: datetime,
    status: str = "FILLED",
    broker_order_id: str | None = None,
    broker_fill_id: str | None = None,
) -> tuple[WeightedVotingFillAttribution, WeightedVotingPositionAttribution]:
    _validate_command(command)
    if filled_quantity <= 0:
        raise ValueError("Weighted Voting fill attribution requires a positive filled quantity")
    fill_id = broker_fill_id or f"{command.client_order_id}.fill.{_hash_json({'quantity': filled_quantity, 'filledAt': filled_at.isoformat(), 'status': status})[:12]}"
    fill = WeightedVotingFillAttribution(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        decision_id=command.decision_id,
        order_intent_id=command.order_intent_id,
        client_order_id=command.client_order_id,
        broker_order_id=broker_order_id,
        fill_id=fill_id,
        symbol=command.symbol,
        side=command.side,
        filled_quantity=filled_quantity,
        average_fill_price=average_fill_price,
        status=status,
        filled_at=filled_at,
        reason_codes=("weighted_voting.execution.fill_attributed_to_decision",),
    )
    signed_quantity = -filled_quantity if command.side.upper() == "SELL" else filled_quantity
    position = WeightedVotingPositionAttribution(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        decision_id=command.decision_id,
        order_intent_id=command.order_intent_id,
        client_order_id=command.client_order_id,
        position_id=f"weighted_voting.position.{command.symbol}.{command.client_order_id}",
        symbol=command.symbol,
        side="SHORT" if signed_quantity < 0 else "LONG",
        quantity=signed_quantity,
        average_entry_price=average_fill_price,
        opened_at=filled_at,
        reason_codes=("weighted_voting.execution.position_owned_by_weighted_voting",),
    )
    store.write_snapshot(_fill_key(fill.fill_id), fill.as_dict())
    store.write_snapshot(_position_key(position.position_id), position.as_dict())
    return fill, position


def reconcile_weighted_voting_broker_result(
    *,
    store: WeightedVotingStateStore,
    command: WeightedVotingBrokerCommand,
    broker_result: PaperOrderGatewayResult | dict[str, Any],
    reconciled_at: datetime,
) -> WeightedVotingExecutionReconciliation:
    _validate_command(command)
    result = _payload(broker_result)
    if result.get("algorithmId") != WEIGHTED_VOTING_ALGORITHM_ID:
        raise ValueError("Weighted Voting execution gateway cannot reconcile another algorithm's broker result")
    if result.get("decisionId") not in {None, command.decision_id}:
        raise ValueError("Weighted Voting execution gateway broker result decision mismatch")
    if result.get("orderIntentId") != command.order_intent_id:
        raise ValueError("Weighted Voting execution gateway broker result order intent mismatch")
    if result.get("clientOrderId") != command.client_order_id:
        raise ValueError("Weighted Voting execution gateway broker result client order mismatch")

    ack = _payload(result.get("brokerAck") or {})
    fill_payload = _payload(result.get("fill") or {})
    broker_order_id = ack.get("brokerOrderId")
    submitted = bool(result.get("submitted"))
    status = str(result.get("status") or "RECONCILED")
    fill = None
    position = None
    reason_codes = ["weighted_voting.execution.reconciled_to_owned_storage"]
    if submitted:
        record_weighted_voting_submission(
            store=store,
            command=command,
            submitted_at=reconciled_at,
            broker_order_id=broker_order_id,
            broker_status=ack.get("status") or status,
        )
    if status == "REJECTED" or (not submitted and status in {"NOT_SUBMITTED", "REJECTED"}):
        record_weighted_voting_rejection(
            store=store,
            command=command,
            rejected_at=reconciled_at,
            broker_status=status,
            rejection_reason=ack.get("rejectedReason"),
            reason_codes=tuple(result.get("reasonCodes") or ()),
        )
    if fill_payload and int(fill_payload.get("filledQuantity") or 0) > 0:
        fill, position = record_weighted_voting_fill(
            store=store,
            command=command,
            filled_quantity=int(fill_payload.get("filledQuantity") or 0),
            average_fill_price=fill_payload.get("averageFillPrice"),
            filled_at=_datetime_from_payload(fill_payload.get("filledAt"), reconciled_at),
            status=str(fill_payload.get("status") or status),
            broker_order_id=broker_order_id,
        )
        reason_codes.append("weighted_voting.execution.fill_and_position_attributed")
    record = WeightedVotingExecutionReconciliation(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        command_id=command.command_id,
        decision_id=command.decision_id,
        order_intent_id=command.order_intent_id,
        client_order_id=command.client_order_id,
        status=status,
        reconciled_at=reconciled_at,
        submitted=submitted,
        broker_order_id=broker_order_id,
        fill=fill,
        position=position,
        reason_codes=tuple(reason_codes),
    )
    store.write_snapshot(_reconciliation_key(command.client_order_id), record.as_dict())
    return record


def submit_weighted_voting_paper_order(
    *,
    gateway: PaperOrderGateway,
    proposal: GlobalOrderProposal,
    global_application: AppliedGlobalGateDecision,
    local_gate_result: WeightedVotingGatePipelineResult,
    mode: str,
    evaluated_at: datetime,
    rollout_flags: WeightedVotingRolloutFlags | None = None,
    rollout_validation: WeightedVotingRolloutValidation | None = None,
) -> PaperOrderGatewayResult:
    if proposal.algorithmId != "weighted_voting":
        raise ValueError("Weighted Voting execution gateway only accepts weighted_voting proposals")
    if global_application.algorithmId != "weighted_voting":
        raise ValueError("Weighted Voting execution gateway only accepts weighted_voting global applications")
    if mode not in {"manual", "automatic"}:
        raise ValueError("mode must be manual or automatic")
    if mode == "automatic" and not automatic_submission_allowed(flags=rollout_flags, validation=rollout_validation):
        command = build_weighted_voting_broker_command(
            proposal=proposal,
            global_application=global_application,
            accepted_at=evaluated_at,
            mode=mode,
        )
        result = PaperOrderGatewayResult(
            algorithmId=proposal.algorithmId,
            orderIntentId=proposal.orderIntentId,
            clientOrderId=command.client_order_id,
            mode="automatic",
            submitted=False,
            duplicate=False,
            status="NOT_SUBMITTED",
            cancelReplacePolicy="cancel_stale_unfilled_orders_replace_requires_new_intent",
            reasonCodes=("weighted_voting.rollout.auto_submit_blocked",),
            explanation="Weighted Voting automatic paper submission is disabled until all rollout acceptance metrics pass.",
            evaluatedAt=evaluated_at,
            configurationHash="weighted_voting_rollout_auto_submit_blocked",
        )
        record_order_execution_observability(
            store=gateway.store,
            decision_id=proposal.decisionId,
            order_intent_id=proposal.orderIntentId,
            execution_result=result,
            recorded_at=evaluated_at,
        )
        reconcile_weighted_voting_broker_result(
            store=gateway.store,
            command=command,
            broker_result=result,
            reconciled_at=evaluated_at,
        )
        return result
    command = build_weighted_voting_broker_command(
        proposal=proposal,
        global_application=global_application,
        accepted_at=evaluated_at,
        mode=mode,
    )
    result = gateway.submit(
        proposal=proposal,
        global_application=global_application,
        local_gate_passed=local_gate_result.permission_granted,
        mode=mode,
        evaluated_at=evaluated_at,
    )
    record_order_execution_observability(
        store=gateway.store,
        decision_id=proposal.decisionId,
        order_intent_id=proposal.orderIntentId,
        execution_result=result,
        recorded_at=evaluated_at,
    )
    reconcile_weighted_voting_broker_result(
        store=gateway.store,
        command=command,
        broker_result=result,
        reconciled_at=evaluated_at,
    )
    return result


def execution_gateway_status() -> dict[str, Any]:
    return {
        "gatewayVersion": WEIGHTED_VOTING_EXECUTION_GATEWAY_VERSION,
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "namespace": WEIGHTED_VOTING_EXECUTION_NAMESPACE,
        "brokerConnectionBoundary": WEIGHTED_VOTING_BROKER_CONNECTION_BOUNDARY,
        "ownedResponsibilities": [
            "accepted_proposal_to_shared_broker_command",
            "deterministic_client_order_id",
            "algorithm_ownership_preservation",
            "submission_status_recording",
            "rejection_status_recording",
            "fill_to_decision_attribution",
            "position_to_weighted_voting_attribution",
            "broker_result_reconciliation_to_weighted_voting_storage",
        ],
        "sharedServices": ["broker_connection"],
    }


def _validate_command(command: WeightedVotingBrokerCommand) -> None:
    if command.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID or command.ownership != WEIGHTED_VOTING_EXECUTION_OWNERSHIP:
        raise ValueError("Weighted Voting execution gateway received a command without Weighted Voting ownership")


def _validate_weighted_voting_proposal(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> None:
    if _proposal_algorithm_id(proposal) != WEIGHTED_VOTING_ALGORITHM_ID:
        raise ValueError("Weighted Voting execution gateway only accepts weighted_voting proposals")


def _validate_weighted_voting_global_application(global_application: AppliedGlobalGateDecision) -> None:
    if global_application.algorithmId != WEIGHTED_VOTING_ALGORITHM_ID:
        raise ValueError("Weighted Voting execution gateway only accepts weighted_voting global applications")


def _proposal_algorithm_id(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> str:
    return proposal.algorithmId if isinstance(proposal, GlobalOrderProposal) else proposal.algorithm_id


def _proposal_decision_id(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> str:
    return proposal.decisionId if isinstance(proposal, GlobalOrderProposal) else proposal.decision_id


def _proposal_order_intent_id(proposal: GlobalOrderProposal | WeightedVotingOrderProposal, fallback: str | None) -> str:
    if isinstance(proposal, GlobalOrderProposal):
        return proposal.orderIntentId
    return fallback or proposal.proposal_id


def _proposal_symbol(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> str:
    symbol = proposal.symbol
    return str(symbol).upper()


def _proposal_side(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> str:
    return _side_value(proposal.side)


def _proposal_quantity(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> int:
    return int(proposal.quantity)


def _proposal_order_type(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> str:
    if isinstance(proposal, WeightedVotingOrderProposal):
        return proposal.order_type
    kind = proposal.entryFormula.get("kind") if isinstance(proposal.entryFormula, dict) else None
    return str(kind or "bracket_limit")


def _proposal_trigger_price(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> float | None:
    return proposal.triggerPrice if isinstance(proposal, GlobalOrderProposal) else proposal.trigger_price


def _proposal_limit_price(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> float | None:
    return proposal.limitPrice if isinstance(proposal, GlobalOrderProposal) else proposal.limit_price


def _proposal_stop_price(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> float | None:
    return proposal.stopPrice if isinstance(proposal, GlobalOrderProposal) else proposal.stop_price


def _proposal_target_price(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> float | None:
    return proposal.targetPrice if isinstance(proposal, GlobalOrderProposal) else proposal.target_price


def _proposal_time_in_force(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> str:
    if isinstance(proposal, WeightedVotingOrderProposal):
        return proposal.time_in_force
    value = proposal.settingsSnapshot.get("timeInForce") or proposal.settingsSnapshot.get("time_in_force")
    return str(value or "day")


def _proposal_capital_partition_id(proposal: GlobalOrderProposal | WeightedVotingOrderProposal, fallback: str | None) -> str:
    if isinstance(proposal, GlobalOrderProposal):
        return proposal.capitalPartitionId
    return fallback or "weighted_voting.paper.default"


def _proposal_planned_risk(proposal: GlobalOrderProposal | WeightedVotingOrderProposal, fallback: float | None) -> float:
    if isinstance(proposal, GlobalOrderProposal):
        return float(proposal.plannedRiskDollars)
    return float(fallback or 0.0)


def _proposal_strategy_versions(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> dict[str, str]:
    if isinstance(proposal, WeightedVotingOrderProposal):
        return dict(proposal.strategy_versions)
    value = proposal.settingsSnapshot.get("strategyVersions") or proposal.settingsSnapshot.get("strategy_versions") or {}
    return {str(key): str(item) for key, item in dict(value).items()}


def _proposal_weight_version(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> str:
    if isinstance(proposal, WeightedVotingOrderProposal):
        return proposal.weight_version
    return str(proposal.settingsSnapshot.get("weightVersion") or proposal.settingsSnapshot.get("weight_version") or "")


def _proposal_settings_version(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> str:
    if isinstance(proposal, WeightedVotingOrderProposal):
        return proposal.settings_version
    return str(proposal.settingsSnapshot.get("settingsVersion") or proposal.settingsSnapshot.get("settings_version") or "")


def _proposal_risk_profile_version(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> str:
    if isinstance(proposal, WeightedVotingOrderProposal):
        return proposal.risk_profile_version
    return str(proposal.settingsSnapshot.get("riskProfileVersion") or proposal.settingsSnapshot.get("risk_profile_version") or "")


def _proposal_market_snapshot_hash(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> str:
    if isinstance(proposal, WeightedVotingOrderProposal):
        return proposal.market_snapshot_hash
    return str(proposal.settingsSnapshot.get("marketSnapshotHash") or proposal.settingsSnapshot.get("market_snapshot_hash") or proposal.strategyStateHash)


def _proposal_configuration_hash(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> str:
    return proposal.configurationHash if isinstance(proposal, GlobalOrderProposal) else proposal.configuration_hash


def _proposal_expires_at(proposal: GlobalOrderProposal | WeightedVotingOrderProposal, accepted_at: datetime) -> datetime:
    if isinstance(proposal, WeightedVotingOrderProposal):
        return proposal.expires_at
    return proposal.proposedAt + timedelta(minutes=5) if proposal.proposedAt > accepted_at - timedelta(days=1) else accepted_at + timedelta(minutes=5)


def _proposal_reason_codes(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> tuple[str, ...]:
    if isinstance(proposal, WeightedVotingOrderProposal):
        return proposal.reason_codes
    return tuple(str(reason) for reason in proposal.settingsSnapshot.get("reasonCodes", ()))


def _side_value(value: Any) -> str:
    if isinstance(value, Enum):
        value = value.value
    return str(value).upper()


def _payload(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return dict(value.model_dump(mode="json"))
    if hasattr(value, "as_dict"):
        return dict(value.as_dict())
    if isinstance(value, dict):
        return dict(value)
    return {}


def _datetime_from_payload(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return fallback


def _command_key(client_order_id: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.command.{client_order_id}"


def _decision_command_key(decision_id: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.decision_command.{decision_id}"


def _submission_key(client_order_id: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.submission.{client_order_id}"


def _rejection_key(client_order_id: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.rejection.{client_order_id}"


def _fill_key(fill_id: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.fill.{fill_id}"


def _position_key(position_id: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.position.{position_id}"


def _reconciliation_key(client_order_id: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.reconciliation.{client_order_id}"


def _hash_json(value: Any) -> str:
    return hashlib.sha256(json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "as_dict"):
        return value.as_dict()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


__all__ = [
    "WEIGHTED_VOTING_BROKER_CONNECTION_BOUNDARY",
    "WEIGHTED_VOTING_EXECUTION_GATEWAY_VERSION",
    "WEIGHTED_VOTING_EXECUTION_NAMESPACE",
    "WEIGHTED_VOTING_EXECUTION_OWNERSHIP",
    "WeightedVotingBrokerCommand",
    "WeightedVotingExecutionReconciliation",
    "WeightedVotingExecutionRejection",
    "WeightedVotingExecutionStatus",
    "WeightedVotingExecutionSubmission",
    "WeightedVotingFillAttribution",
    "WeightedVotingPositionAttribution",
    "build_weighted_voting_broker_command",
    "deterministic_weighted_voting_client_order_id",
    "execution_gateway_status",
    "persist_weighted_voting_broker_command",
    "reconcile_weighted_voting_broker_result",
    "record_weighted_voting_fill",
    "record_weighted_voting_rejection",
    "record_weighted_voting_submission",
    "submit_weighted_voting_paper_order",
]
