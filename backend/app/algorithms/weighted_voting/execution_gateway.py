from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
from typing import Any, Literal

from backend.app.algorithms.weighted_voting.decision_gates import WeightedVotingGatePipelineResult
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.inventory import CURRENT_SNAPSHOT_KEY, WeightedVotingInventoryEventType, WeightedVotingInventoryRepository, WeightedVotingInventorySnapshot
from backend.app.algorithms.weighted_voting.local_paper_logging import record_weighted_voting_local_paper_lifecycle_event
from backend.app.algorithms.weighted_voting.observability import record_order_execution_observability
from backend.app.algorithms.weighted_voting.order_proposal import WeightedVotingOrderProposal
from backend.app.algorithms.weighted_voting.persistence import WeightedVotingStateStore
from backend.app.algorithms.weighted_voting.rollout import (
    WeightedVotingRolloutFlags,
    WeightedVotingRolloutValidation,
    automatic_submission_allowed,
)
from backend.app.domain.models import Signal
from backend.app.execution import (
    PaperGatewayProtectiveOrder,
    PaperOrderGateway,
    PaperOrderGatewayResult,
    PaperOrderIntentRecord,
    deterministic_gateway_client_order_id,
)
from backend.app.gates import AppliedGlobalGateDecision, GlobalOrderProposal


WEIGHTED_VOTING_EXECUTION_GATEWAY_VERSION = "weighted_voting_execution_gateway_v2"
WEIGHTED_VOTING_EXECUTION_NAMESPACE = "weighted_voting.execution_gateway"
WEIGHTED_VOTING_BROKER_CONNECTION_BOUNDARY = "weighted_voting_local_paper_broker"
WEIGHTED_VOTING_EXECUTION_OWNERSHIP = "weighted_voting"

WeightedVotingExecutionStatus = Literal[
    "PENDING",
    "PENDING_SUBMISSION",
    "SUBMITTED",
    "ACKNOWLEDGED",
    "REJECTED",
    "PARTIALLY_FILLED",
    "FILLED",
    "CANCELLED",
    "EXPIRED",
    "REPLACED",
    "RECONCILED",
]


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
                "commandType": "simulate_local_paper_order",
                "brokerConnection": WEIGHTED_VOTING_BROKER_CONNECTION_BOUNDARY,
                "preserveAlgorithmOwnership": True,
                "ownershipMutationAllowed": False,
                "executionMode": "LOCAL_PAPER",
            }
        )
        return payload


@dataclass(frozen=True)
class WeightedVotingExecutionQueueItem:
    algorithm_id: Literal["weighted_voting"]
    queue_id: str
    idempotency_key: str
    command: WeightedVotingBrokerCommand
    proposal: GlobalOrderProposal
    global_application: AppliedGlobalGateDecision
    local_gate_passed: bool
    local_gate_reason_codes: tuple[str, ...]
    enqueued_at: datetime
    inventory_snapshot_version: int
    status: WeightedVotingExecutionStatus = "PENDING"
    reason_codes: tuple[str, ...] = ("weighted_voting.execution_queue.enqueued",)
    queue_version: str = WEIGHTED_VOTING_EXECUTION_GATEWAY_VERSION

    def __post_init__(self) -> None:
        if self.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting execution queue rejects cross-algorithm items")
        _validate_command(self.command)
        _validate_weighted_voting_proposal(self.proposal)
        _validate_weighted_voting_global_application(self.global_application)
        if self.command.capital_partition_id != self.proposal.capitalPartitionId:
            raise ValueError("Weighted Voting execution queue requires a matching capital partition")
        if self.inventory_snapshot_version < 0:
            raise ValueError("Weighted Voting execution queue requires an authoritative inventory snapshot version")

    def as_dict(self) -> dict[str, Any]:
        return {
            "queueVersion": self.queue_version,
            "algorithmId": self.algorithm_id,
            "queueId": self.queue_id,
            "idempotencyKey": self.idempotency_key,
            "command": self.command.as_shared_broker_command(),
            "proposal": self.proposal.model_dump(mode="json"),
            "globalApplication": self.global_application.model_dump(mode="json"),
            "localGatePassed": self.local_gate_passed,
            "localGateReasonCodes": list(self.local_gate_reason_codes),
            "enqueuedAt": self.enqueued_at.isoformat(),
            "inventorySnapshotVersion": self.inventory_snapshot_version,
            "status": self.status,
            "reasonCodes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class WeightedVotingExecutionLifecycleRecord:
    algorithm_id: Literal["weighted_voting"]
    client_order_id: str
    order_intent_id: str
    decision_id: str
    status: WeightedVotingExecutionStatus
    recorded_at: datetime
    reason_codes: tuple[str, ...]
    broker_status: str | None = None
    queue_id: str | None = None
    lifecycle_version: str = WEIGHTED_VOTING_EXECUTION_GATEWAY_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "lifecycleVersion": self.lifecycle_version,
            "algorithmId": self.algorithm_id,
            "clientOrderId": self.client_order_id,
            "orderIntentId": self.order_intent_id,
            "decisionId": self.decision_id,
            "status": self.status,
            "brokerStatus": self.broker_status,
            "queueId": self.queue_id,
            "recordedAt": self.recorded_at.isoformat(),
            "reasonCodes": list(self.reason_codes),
        }


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
    market_reference_price: float | None = None
    slippage_per_share: float = 0.0
    spread_impact_per_share: float = 0.0
    commission: float = 0.0
    regulatory_fees: float = 0.0
    total_execution_cost: float = 0.0
    execution_cost_breakdown: dict[str, Any] | None = None
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
            "marketReferencePrice": self.market_reference_price,
            "slippagePerShare": self.slippage_per_share,
            "spreadImpactPerShare": self.spread_impact_per_share,
            "commission": self.commission,
            "regulatoryFees": self.regulatory_fees,
            "totalExecutionCost": self.total_execution_cost,
            "executionCostBreakdown": self.execution_cost_breakdown or {},
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

    def __post_init__(self) -> None:
        if self.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting execution reconciliation cannot be assigned to another algorithm")
        if self.fill is not None and self.fill.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting execution reconciliation rejects foreign fill attribution")
        if self.position is not None and self.position.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting execution reconciliation rejects foreign position attribution")

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
    """Convert an accepted Weighted Voting proposal into a local paper command."""

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
    if global_application.globallyAllowedQuantity > proposed_quantity:
        raise ValueError("Weighted Voting global application cannot increase proposed quantity")
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


def deterministic_weighted_voting_order_intent_idempotency_key(
    *,
    decision_id: str,
    intent_revision: int | str = 1,
) -> str:
    payload = {
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "decisionId": decision_id,
        "intentRevision": str(intent_revision),
    }
    return "weighted_voting.order_intent." + _hash_json(payload)[:24]


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


def enqueue_weighted_voting_execution_order(
    *,
    store: WeightedVotingStateStore,
    proposal: GlobalOrderProposal,
    global_application: AppliedGlobalGateDecision,
    local_gate_result: WeightedVotingGatePipelineResult,
    enqueued_at: datetime,
    idempotency_key: str,
    inventory_snapshot_version: int | None = None,
) -> WeightedVotingExecutionQueueItem | None:
    """Persist an accepted automatic paper order intent for the execution worker."""

    _validate_weighted_voting_proposal(proposal)
    _validate_weighted_voting_global_application(global_application)
    if not proposal.capitalPartitionId.startswith("weighted_voting."):
        raise ValueError("Weighted Voting automatic execution requires a Weighted Voting capital partition")
    order_intent_idempotency_key = deterministic_weighted_voting_order_intent_idempotency_key(
        decision_id=proposal.decisionId,
        intent_revision=_proposal_intent_revision(proposal),
    )
    existing_intent = _read_optional(store, _order_intent_index_key(order_intent_idempotency_key))
    if existing_intent:
        client_order_id = str(existing_intent.get("clientOrderId") or "")
        if client_order_id and _read_optional(store, _automatic_result_key(client_order_id)):
            return None
        if client_order_id and _read_optional(store, _queue_key(client_order_id)):
            return _queue_item_from_payload(store.read_snapshot(_queue_key(client_order_id)))
        return None
    command = build_weighted_voting_broker_command(
        proposal=proposal,
        global_application=global_application,
        accepted_at=enqueued_at,
        mode="automatic",
    )
    persist_weighted_voting_broker_command(store, command)
    store.write_snapshot(
        _order_intent_index_key(order_intent_idempotency_key),
        {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "decisionId": command.decision_id,
            "orderIntentId": command.order_intent_id,
            "clientOrderId": command.client_order_id,
            "intentRevision": _proposal_intent_revision(proposal),
            "orderIntentIdempotencyKey": order_intent_idempotency_key,
            "recordedAt": enqueued_at.isoformat(),
            "reasonCodes": ("weighted_voting.execution_queue.order_intent_idempotency_claimed",),
        },
    )
    if _read_optional(store, _automatic_result_key(command.client_order_id)):
        _record_lifecycle(
            store=store,
            command=command,
            status="REPLACED",
            recorded_at=enqueued_at,
            reason_codes=("weighted_voting.execution_queue.duplicate_result_not_requeued",),
        )
        return None
    if not _accepted_for_execution(proposal, global_application, local_gate_result):
        _record_lifecycle(
            store=store,
            command=command,
            status="REJECTED",
            recorded_at=enqueued_at,
            reason_codes=tuple(
                dict.fromkeys(
                    (
                        "weighted_voting.execution_queue.not_enqueued_rejected_proposal",
                        *local_gate_result.reason_codes,
                        *global_application.rejectionReasons,
                    )
                )
            ),
        )
        return None
    queue_id = f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.queue.{command.client_order_id}"
    if _read_optional(store, _queue_key(command.client_order_id)):
        return _queue_item_from_payload(store.read_snapshot(_queue_key(command.client_order_id)))
    item = WeightedVotingExecutionQueueItem(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        queue_id=queue_id,
        idempotency_key=idempotency_key,
        command=command,
        proposal=proposal,
        global_application=global_application,
        local_gate_passed=local_gate_result.permission_granted,
        local_gate_reason_codes=local_gate_result.reason_codes,
        enqueued_at=enqueued_at,
        inventory_snapshot_version=_inventory_snapshot_version_for_enqueue(store, proposal, explicit_version=inventory_snapshot_version),
    )
    store.write_snapshot(_queue_key(command.client_order_id), item.as_dict())
    store.write_snapshot(_queue_index_key(idempotency_key), {"algorithmId": WEIGHTED_VOTING_ALGORITHM_ID, "clientOrderId": command.client_order_id, "queueId": queue_id, "marketEventId": idempotency_key})
    _record_lifecycle(
        store=store,
        command=command,
        status="PENDING",
        recorded_at=enqueued_at,
        reason_codes=item.reason_codes,
        queue_id=queue_id,
    )
    return item


def submit_queued_weighted_voting_paper_order(
    *,
    gateway: PaperOrderGateway,
    queue_item: WeightedVotingExecutionQueueItem,
    inventory_repository: WeightedVotingInventoryRepository,
    evaluated_at: datetime,
    rollout_flags: WeightedVotingRolloutFlags | None = None,
    rollout_validation: WeightedVotingRolloutValidation | None = None,
) -> PaperOrderGatewayResult:
    """Reserve Weighted Voting inventory, submit once through the paper gateway, and reconcile."""

    command = queue_item.command
    _validate_command(command)
    previous = _read_optional(gateway.store, _automatic_result_key(command.client_order_id))
    if previous:
        _record_lifecycle(
            store=gateway.store,
            command=command,
            status="RECONCILED",
            recorded_at=evaluated_at,
            reason_codes=("weighted_voting.execution.duplicate_event_returned_persisted_result",),
            queue_id=queue_item.queue_id,
        )
        return PaperOrderGatewayResult.model_validate(previous)
    lifecycle_store = gateway.store if _is_weighted_voting_local_paper_gateway(gateway) else None
    if evaluated_at >= command.expires_at:
        _release_inventory_reservation(
            inventory_repository,
            command=command,
            occurred_at=evaluated_at,
            reason_code="weighted_voting.execution.expired_before_submission",
            store=lifecycle_store,
        )
        result = _not_submitted_result(
            command=command,
            proposal=queue_item.proposal,
            mode="automatic",
            status="NOT_SUBMITTED",
            reason_codes=("weighted_voting.execution.expired_before_submission",),
            explanation="Queued Weighted Voting entry order expired before paper submission.",
            evaluated_at=evaluated_at,
        )
        _persist_automatic_result(gateway.store, queue_item, result, status="EXPIRED", recorded_at=evaluated_at)
        return result
    if _is_market_order(command):
        _release_inventory_reservation(
            inventory_repository,
            command=command,
            occurred_at=evaluated_at,
            reason_code="weighted_voting.execution.market_entry_rejected",
            store=lifecycle_store,
        )
        result = _not_submitted_result(
            command=command,
            proposal=queue_item.proposal,
            mode="automatic",
            status="NOT_SUBMITTED",
            reason_codes=("weighted_voting.execution.market_entry_rejected",),
            explanation="Weighted Voting automatic entries require limit or stop-limit order policy.",
            evaluated_at=evaluated_at,
        )
        _persist_automatic_result(gateway.store, queue_item, result, status="REJECTED", recorded_at=evaluated_at)
        return result

    current_inventory = inventory_repository.current_snapshot(now=evaluated_at)
    if current_inventory.snapshot_version != queue_item.inventory_snapshot_version:
        result = _not_submitted_result(
            command=command,
            proposal=queue_item.proposal,
            mode="automatic",
            status="NOT_SUBMITTED",
            reason_codes=("weighted_voting.execution.stale_inventory_version",),
            explanation="Queued Weighted Voting entry order was rejected because inventory changed after the decision was created.",
            evaluated_at=evaluated_at,
        )
        _persist_automatic_result(gateway.store, queue_item, result, status="REJECTED", recorded_at=evaluated_at)
        return result

    short_rejection = _unsupported_open_short_rejection(command, current_inventory)
    if short_rejection is not None:
        reason_code, explanation = short_rejection
        result = _not_submitted_result(
            command=command,
            proposal=queue_item.proposal,
            mode="automatic",
            status="NOT_SUBMITTED",
            reason_codes=(reason_code,),
            explanation=explanation,
            evaluated_at=evaluated_at,
        )
        _persist_automatic_result(gateway.store, queue_item, result, status="REJECTED", recorded_at=evaluated_at)
        return result

    try:
        _reserve_inventory_for_command(inventory_repository, command=command, occurred_at=evaluated_at, expected_snapshot_version=current_inventory.snapshot_version)
    except RuntimeError as exc:
        reason_codes = ["weighted_voting.execution.inventory_reservation_failed"]
        if "buying power" in str(exc).lower():
            reason_codes.append("weighted_voting.execution.insufficient_local_buying_power")
        result = _not_submitted_result(
            command=command,
            proposal=queue_item.proposal,
            mode="automatic",
            status="NOT_SUBMITTED",
            reason_codes=tuple(reason_codes),
            explanation="Queued Weighted Voting entry order was rejected because local inventory reservation failed.",
            evaluated_at=evaluated_at,
        )
        _persist_automatic_result(gateway.store, queue_item, result, status="REJECTED", recorded_at=evaluated_at)
        return result
    _record_lifecycle(
        store=gateway.store,
        command=command,
        status="PENDING_SUBMISSION",
        recorded_at=evaluated_at,
        reason_codes=("weighted_voting.execution.inventory_reserved_before_submission",),
        queue_id=queue_item.queue_id,
    )
    persist_weighted_voting_broker_command(gateway.store, command)
    result = submit_weighted_voting_paper_order(
        gateway=gateway,
        proposal=queue_item.proposal,
        global_application=queue_item.global_application,
        local_gate_result=_local_gate_result_from_queue(queue_item),
        mode="automatic",
        evaluated_at=evaluated_at,
        rollout_flags=rollout_flags,
        rollout_validation=rollout_validation,
    )
    if result.fill and result.fill.filledQuantity > 0:
        _record_inventory_fill(inventory_repository, command=command, result=result, occurred_at=evaluated_at, store=lifecycle_store)
    elif (not result.submitted) or result.status in {"REJECTED", "CANCELED", "EXPIRED", "NOT_SUBMITTED"}:
        _release_inventory_reservation(
            inventory_repository,
            command=command,
            occurred_at=evaluated_at,
            reason_code="weighted_voting.execution.reservation_released_after_terminal_response",
            store=lifecycle_store,
        )
    _persist_automatic_result(gateway.store, queue_item, result, status=_execution_status_from_gateway_result(result), recorded_at=evaluated_at)
    return result


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
    market_reference_price: float | None = None,
    slippage_per_share: float = 0.0,
    spread_impact_per_share: float = 0.0,
    commission: float = 0.0,
    regulatory_fees: float = 0.0,
    total_execution_cost: float = 0.0,
    execution_cost_breakdown: dict[str, Any] | None = None,
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
        market_reference_price=market_reference_price,
        slippage_per_share=slippage_per_share,
        spread_impact_per_share=spread_impact_per_share,
        commission=commission,
        regulatory_fees=regulatory_fees,
        total_execution_cost=total_execution_cost,
        execution_cost_breakdown=execution_cost_breakdown,
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
            market_reference_price=fill_payload.get("marketReferencePrice"),
            slippage_per_share=float(fill_payload.get("slippagePerShare") or 0.0),
            spread_impact_per_share=float(fill_payload.get("spreadImpactPerShare") or 0.0),
            commission=float(fill_payload.get("commission") or 0.0),
            regulatory_fees=float(fill_payload.get("regulatoryFees") or 0.0),
            total_execution_cost=float(fill_payload.get("totalExecutionCost") or 0.0),
            execution_cost_breakdown=_payload(fill_payload.get("executionCostBreakdown") or {}),
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
    if mode == "automatic" and not automatic_submission_allowed(flags=rollout_flags, validation=rollout_validation, store=gateway.store if rollout_flags is None and rollout_validation is None else None):
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
    if not _verify_weighted_voting_paper_endpoint(gateway):
        result = _not_submitted_result(
            command=command,
            proposal=proposal,
            mode=mode,
            status="NOT_SUBMITTED",
            reason_codes=("weighted_voting.execution.paper_endpoint_unverified", "paper_gateway.paper_endpoint_unverified"),
            explanation="Weighted Voting rejected paper submission because the configured execution boundary is not verified as local paper or paper-only.",
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
    if _is_weighted_voting_local_paper_gateway(gateway):
        result = _submit_weighted_voting_local_paper_order(
            gateway=gateway,
            command=command,
            proposal=proposal,
            global_application=global_application,
            local_gate_result=local_gate_result,
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


def _submit_weighted_voting_local_paper_order(
    *,
    gateway: PaperOrderGateway,
    command: WeightedVotingBrokerCommand,
    proposal: GlobalOrderProposal,
    global_application: AppliedGlobalGateDecision,
    local_gate_result: WeightedVotingGatePipelineResult,
    mode: str,
    evaluated_at: datetime,
) -> PaperOrderGatewayResult:
    if _read_optional(gateway.store, _local_intent_key(proposal.orderIntentId)):
        result = PaperOrderGatewayResult(
            executionMode="LOCAL_PAPER",
            algorithmId=WEIGHTED_VOTING_ALGORITHM_ID,
            orderIntentId=proposal.orderIntentId,
            clientOrderId=command.client_order_id,
            mode=mode,  # type: ignore[arg-type]
            submitted=False,
            duplicate=True,
            status="DUPLICATE",
            cancelReplacePolicy="cancel_stale_unfilled_orders_replace_requires_new_intent",
            reasonCodes=("weighted_voting.local_paper.duplicate_intent",),
            explanation="Duplicate Weighted Voting local paper intent was not resubmitted.",
            evaluatedAt=evaluated_at,
            configurationHash=_hash_json({"clientOrderId": command.client_order_id, "status": "DUPLICATE"}),
        )
        gateway.store.write_snapshot(_local_result_key(proposal.orderIntentId), result.model_dump(mode="json"))
        return result
    previous = _read_optional(gateway.store, _local_result_key(proposal.orderIntentId))
    if previous:
        return PaperOrderGatewayResult.model_validate(previous)

    intent = _local_paper_intent_record(
        command=command,
        proposal=proposal,
        global_application=global_application,
        local_gate_result=local_gate_result,
        mode=mode,
        evaluated_at=evaluated_at,
    )
    gateway.store.write_snapshot(_local_intent_key(proposal.orderIntentId), intent.model_dump(mode="json"))
    gateway.store.write_snapshot(
        _local_client_order_key(command.client_order_id),
        {
            "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
            "executionMode": "LOCAL_PAPER",
            "orderIntentId": proposal.orderIntentId,
            "clientOrderId": command.client_order_id,
            "capitalPartitionId": proposal.capitalPartitionId,
            "reasonCodes": ("weighted_voting.local_paper.intent_persisted_before_submission",),
        },
    )

    blocker = _local_submission_blocker(intent, proposal, global_application, evaluated_at)
    if blocker:
        status, reason, explanation = blocker
        blocked_intent = intent.model_copy(update={"status": status, "reasonCodes": (*intent.reasonCodes, reason)})
        gateway.store.write_snapshot(_local_intent_key(proposal.orderIntentId), blocked_intent.model_dump(mode="json"))
        result = PaperOrderGatewayResult(
            executionMode="LOCAL_PAPER",
            algorithmId=WEIGHTED_VOTING_ALGORITHM_ID,
            orderIntentId=proposal.orderIntentId,
            clientOrderId=command.client_order_id,
            mode=mode,  # type: ignore[arg-type]
            submitted=False,
            duplicate=False,
            status=status,  # type: ignore[arg-type]
            cancelReplacePolicy="cancel_stale_unfilled_orders_replace_requires_new_intent",
            reasonCodes=(reason,),
            explanation=explanation,
            evaluatedAt=evaluated_at,
            configurationHash=_hash_json({"clientOrderId": command.client_order_id, "status": status, "reason": reason}),
        )
        gateway.store.write_snapshot(_local_result_key(proposal.orderIntentId), result.model_dump(mode="json"))
        return result

    verifier = getattr(gateway.broker, "verify_paper_account", None)
    inventory_unavailable = False
    account_verified = True
    if callable(verifier):
        try:
            account_verified = bool(verifier())
        except Exception:
            account_verified = False
            inventory_unavailable = True
    if not account_verified:
        reason_code = "weighted_voting.local_paper.authoritative_inventory_unavailable" if inventory_unavailable else "weighted_voting.local_paper.account_unverified"
        rejected_intent = intent.model_copy(update={"status": "NOT_SUBMITTED", "reasonCodes": (*intent.reasonCodes, reason_code)})
        gateway.store.write_snapshot(_local_intent_key(proposal.orderIntentId), rejected_intent.model_dump(mode="json"))
        result = PaperOrderGatewayResult(
            executionMode="LOCAL_PAPER",
            algorithmId=WEIGHTED_VOTING_ALGORITHM_ID,
            orderIntentId=proposal.orderIntentId,
            clientOrderId=command.client_order_id,
            mode=mode,  # type: ignore[arg-type]
            submitted=False,
            duplicate=False,
            status="NOT_SUBMITTED",
            cancelReplacePolicy="cancel_stale_unfilled_orders_replace_requires_new_intent",
            reasonCodes=(reason_code,),
            explanation="Weighted Voting local paper authoritative inventory is unavailable." if inventory_unavailable else "Weighted Voting local paper account state is unavailable or unverified.",
            evaluatedAt=evaluated_at,
            configurationHash=_hash_json({"clientOrderId": command.client_order_id, "status": "NOT_SUBMITTED", "reason": reason_code}),
        )
        gateway.store.write_snapshot(_local_result_key(proposal.orderIntentId), result.model_dump(mode="json"))
        return result

    verified = intent.model_copy(update={"paperAccountVerified": True})
    gateway.store.write_snapshot(_local_intent_key(proposal.orderIntentId), verified.model_dump(mode="json"))
    ack = gateway.broker.submit_bracket_order(verified)
    fill = gateway.broker.refresh_order(command.client_order_id)
    if fill is not None:
        fill = fill.model_copy(
            update={
                "executionMode": "LOCAL_PAPER",
                "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
                "capitalPartitionId": command.capital_partition_id,
                "accountId": command.capital_partition_id,
                "orderIntentId": command.order_intent_id,
                "symbol": command.symbol,
                "side": Signal.SELL if command.side.upper() == "SELL" else Signal.BUY,
            }
        )
        gateway.store.write_snapshot(_local_fill_key(command.client_order_id), fill.model_dump(mode="json"))
    protective = _local_protective_order(verified, fill)
    if protective is not None:
        gateway.store.write_snapshot(_local_protective_key(protective.clientOrderId), protective.model_dump(mode="json"))
    submitted = ack.status != "REJECTED"
    status = fill.status if fill else ack.status
    reason_codes = ["weighted_voting.local_paper.submitted", "paper_gateway.submitted"] if submitted else ["weighted_voting.local_paper.rejected", "paper_gateway.broker_rejected"]
    if fill and fill.status == "PARTIALLY_FILLED":
        reason_codes.append("paper_gateway.partial_fill_mapped_to_intent")
    final_intent = verified.model_copy(update={"status": status, "reasonCodes": tuple(reason_codes)})
    gateway.store.write_snapshot(_local_intent_key(proposal.orderIntentId), final_intent.model_dump(mode="json"))
    result = PaperOrderGatewayResult(
        executionMode="LOCAL_PAPER",
        algorithmId=WEIGHTED_VOTING_ALGORITHM_ID,
        orderIntentId=proposal.orderIntentId,
        clientOrderId=command.client_order_id,
        mode=mode,  # type: ignore[arg-type]
        submitted=submitted,
        duplicate=False,
        status=status,  # type: ignore[arg-type]
        brokerAck=ack,
        fill=fill,
        protectiveOrder=protective,
        cancelReplacePolicy="cancel_stale_unfilled_orders_replace_requires_new_intent",
        reasonCodes=tuple(reason_codes),
        explanation="Weighted Voting local paper order was simulated and reconciled through Weighted Voting-owned state.",
        evaluatedAt=evaluated_at,
        configurationHash=_hash_json({"clientOrderId": command.client_order_id, "status": status, "reasonCodes": reason_codes}),
    )
    gateway.store.write_snapshot(_local_result_key(proposal.orderIntentId), result.model_dump(mode="json"))
    return result


def _local_paper_intent_record(
    *,
    command: WeightedVotingBrokerCommand,
    proposal: GlobalOrderProposal,
    global_application: AppliedGlobalGateDecision,
    local_gate_result: WeightedVotingGatePipelineResult,
    mode: str,
    evaluated_at: datetime,
) -> PaperOrderIntentRecord:
    submitted_quantity = min(int(proposal.quantity), int(global_application.globallyAllowedQuantity))
    return PaperOrderIntentRecord(
        executionMode="LOCAL_PAPER",
        algorithmId=WEIGHTED_VOTING_ALGORITHM_ID,
        capitalPartitionId=command.capital_partition_id,
        decisionId=command.decision_id,
        orderIntentId=command.order_intent_id,
        clientOrderId=command.client_order_id,
        mode=mode,  # type: ignore[arg-type]
        symbol=command.symbol,
        side=Signal.SELL if command.side.upper() == "SELL" else Signal.BUY,
        proposedQuantity=int(proposal.quantity),
        globallyAllowedQuantity=int(global_application.globallyAllowedQuantity),
        submittedQuantity=submitted_quantity,
        triggerPrice=command.trigger_price,
        orderType=command.order_type.upper(),
        timeInForce=command.time_in_force.upper(),
        limitPrice=command.limit_price,
        stopPrice=command.stop_price,
        targetPrice=command.target_price,
        plannedRiskDollars=float(global_application.maximumAdditionalRiskDollars),
        globalAction=str(global_application.action),
        localGatePassed=local_gate_result.permission_granted,
        globalGatePassed=global_application.globallyAllowedQuantity > 0,
        paperAccountVerified=False,
        persistedBeforeSubmission=True,
        status="PENDING_SUBMISSION",
        reasonCodes=("weighted_voting.local_paper.intent_persisted_before_submission",),
        createdAt=evaluated_at,
        decisionTimestamp=proposal.proposedAt,
        staleAfterSeconds=int(proposal.settingsSnapshot.get("maximumOrderAgeSeconds") or 300),
        settingsSnapshot=dict(proposal.settingsSnapshot or {}),
    )


def _local_submission_blocker(
    intent: PaperOrderIntentRecord,
    proposal: GlobalOrderProposal,
    global_application: AppliedGlobalGateDecision,
    evaluated_at: datetime,
) -> tuple[str, str, str] | None:
    if not intent.localGatePassed:
        return "NOT_SUBMITTED", "paper_gateway.local_gate_failed", "Mandatory Weighted Voting local gates failed."
    if global_application.action not in {"ALLOW", "REDUCE_QUANTITY"} and proposal.intent == "new_entry":
        return "NOT_SUBMITTED", "paper_gateway.global_gate_rejected", "Weighted Voting local/global risk application rejected the new entry."
    if (evaluated_at - proposal.proposedAt) > timedelta(seconds=intent.staleAfterSeconds):
        return "NOT_SUBMITTED", "paper_gateway.stale_decision", "Decision timestamp is stale."
    if intent.submittedQuantity <= 0:
        return "NOT_SUBMITTED", "paper_gateway.zero_quantity", "Zero-quantity orders are not submitted."
    return None


def _local_protective_order(intent: PaperOrderIntentRecord, fill) -> PaperGatewayProtectiveOrder | None:
    if fill is None or fill.filledQuantity <= 0:
        return None
    return PaperGatewayProtectiveOrder(
        executionMode="LOCAL_PAPER",
        clientOrderId=f"{intent.clientOrderId}-protective",
        parentClientOrderId=intent.clientOrderId,
        algorithmId=WEIGHTED_VOTING_ALGORITHM_ID,
        capitalPartitionId=intent.capitalPartitionId,
        accountId=intent.capitalPartitionId,
        orderIntentId=intent.orderIntentId,
        quantity=fill.filledQuantity,
        stopPrice=intent.stopPrice,
        targetPrice=intent.targetPrice,
        bracket=intent.stopPrice is not None and intent.targetPrice is not None,
        reasonCodes=("weighted_voting.local_paper.protective_order_matches_fill",),
    )


def execution_gateway_status() -> dict[str, Any]:
    return {
        "gatewayVersion": WEIGHTED_VOTING_EXECUTION_GATEWAY_VERSION,
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "namespace": WEIGHTED_VOTING_EXECUTION_NAMESPACE,
        "brokerConnectionBoundary": WEIGHTED_VOTING_BROKER_CONNECTION_BOUNDARY,
        "executionMode": "LOCAL_PAPER",
        "ownedResponsibilities": [
            "accepted_proposal_to_local_paper_command",
            "accepted_proposal_to_execution_queue",
            "deterministic_client_order_id",
            "inventory_reservation_before_submission",
            "reservation_release_on_rejection_cancellation_or_expiry",
            "algorithm_ownership_preservation",
            "idempotent_automatic_submission",
            "submission_status_recording",
            "rejection_status_recording",
            "pending_acknowledged_rejected_partial_filled_filled_cancelled_expired_replaced_lifecycle",
            "fill_to_decision_attribution",
            "position_to_weighted_voting_attribution",
            "local_paper_result_reconciliation_to_weighted_voting_storage",
        ],
        "sharedServices": [],
        "localPaperBroker": "weighted_voting.local_paper_broker.WeightedVotingLocalPaperBroker",
    }


def _accepted_for_execution(
    proposal: GlobalOrderProposal,
    global_application: AppliedGlobalGateDecision,
    local_gate_result: WeightedVotingGatePipelineResult,
) -> bool:
    if proposal.algorithmId != WEIGHTED_VOTING_ALGORITHM_ID:
        return False
    if proposal.intent == "new_entry":
        return (
            local_gate_result.permission_granted
            and global_application.action in {"ALLOW", "REDUCE_QUANTITY"}
            and global_application.globallyAllowedQuantity > 0
        )
    if proposal.intent in {"protective_exit", "risk_reducing", "end_of_day_liquidation"}:
        return global_application.riskReducingExitAllowed and global_application.globallyAllowedQuantity > 0
    return False


def _verify_weighted_voting_paper_endpoint(gateway: PaperOrderGateway) -> bool:
    if _is_weighted_voting_local_paper_gateway(gateway):
        return True
    broker = gateway.broker
    if getattr(gateway, "execution_mode", None) != "BROKER_PAPER" or getattr(broker, "broker_kind", None) != "alpaca_paper":
        return False
    verifier = getattr(broker, "verify_paper_endpoint", None)
    return bool(verifier()) if callable(verifier) else False


def _is_weighted_voting_local_paper_gateway(gateway: PaperOrderGateway) -> bool:
    broker = gateway.broker
    if getattr(gateway, "execution_mode", None) != "LOCAL_PAPER" or getattr(broker, "broker_kind", None) != "weighted_voting_local_paper":
        return False
    verifier = getattr(broker, "verify_paper_endpoint", None)
    return bool(verifier()) if callable(verifier) else True


def _reserve_inventory_for_command(
    inventory_repository: WeightedVotingInventoryRepository,
    *,
    command: WeightedVotingBrokerCommand,
    occurred_at: datetime,
    expected_snapshot_version: int | None = None,
) -> None:
    snapshot = inventory_repository.current_snapshot(now=occurred_at)
    version = snapshot.snapshot_version if expected_snapshot_version is None else expected_snapshot_version
    if snapshot.snapshot_version != version:
        raise RuntimeError("Weighted Voting inventory optimistic version check failed")
    cash_reservation = round(command.quantity * _entry_price(command), 10) if command.side.upper() == "BUY" else 0.0
    inventory_repository.append_event(
        event_id=f"{command.client_order_id}.reserve",
        event_type=WeightedVotingInventoryEventType.ORDER_RESERVED,
        payload={
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "order_id": command.order_intent_id,
            "symbol": command.symbol,
            "side": command.side,
            "quantity": command.quantity,
            "filled_quantity": 0,
            "remaining_quantity": command.quantity,
            "order_type": command.order_type,
            "limit_price": command.limit_price,
            "stop_price": command.stop_price,
            "reserved_buying_power": cash_reservation,
            "reserved_cash": cash_reservation,
            "planned_risk_dollars": command.planned_risk_dollars,
            "decision_id": command.decision_id,
            "order_intent_id": command.order_intent_id,
            "client_order_id": command.client_order_id,
            "status": "WORKING",
            "created_at": occurred_at.isoformat(),
            "updated_at": occurred_at.isoformat(),
            "expiration": command.expires_at.isoformat(),
        },
        occurred_at=occurred_at,
        expected_snapshot_version=version,
    )


def _unsupported_open_short_rejection(command: WeightedVotingBrokerCommand, snapshot: WeightedVotingInventorySnapshot) -> tuple[str, str] | None:
    if command.side.upper() != "SELL":
        return None
    long_quantity = sum(max(0, int(position.quantity)) for position in snapshot.open_positions if position.symbol.upper() == command.symbol.upper())
    if command.quantity <= long_quantity:
        return None
    if long_quantity > 0:
        return (
            "weighted_voting.execution.open_short_not_supported",
            "Weighted Voting local paper does not support opening short inventory; this SELL would exceed the existing long position.",
        )
    return (
        "weighted_voting.execution.open_short_not_supported",
        "Weighted Voting local paper does not support opening short inventory; SELL orders may only close or reduce an existing long position.",
    )


def _release_inventory_reservation(
    inventory_repository: WeightedVotingInventoryRepository,
    *,
    command: WeightedVotingBrokerCommand,
    occurred_at: datetime,
    reason_code: str,
    store: WeightedVotingStateStore | None = None,
) -> WeightedVotingInventorySnapshot:
    snapshot = inventory_repository.current_snapshot(now=occurred_at)
    updated = inventory_repository.append_event(
        event_id=f"{command.client_order_id}.release.{reason_code}",
        event_type=WeightedVotingInventoryEventType.ORDER_RELEASED,
        payload={
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "order_id": command.order_intent_id,
            "client_order_id": command.client_order_id,
            "decision_id": command.decision_id,
            "reason_code": reason_code,
        },
        occurred_at=occurred_at,
        expected_snapshot_version=snapshot.snapshot_version,
    )
    if store is not None:
        _record_local_paper_lifecycle_from_command(
            store=store,
            command=command,
            event_name="weighted_voting.local_paper.reservation_released",
            occurred_at=occurred_at,
            inventory_snapshot_version=updated.snapshot_version,
            status="RELEASED",
            reason_codes=(reason_code,),
        )
    return updated


def _record_inventory_fill(
    inventory_repository: WeightedVotingInventoryRepository,
    *,
    command: WeightedVotingBrokerCommand,
    result: PaperOrderGatewayResult,
    occurred_at: datetime,
    store: WeightedVotingStateStore | None = None,
) -> WeightedVotingInventorySnapshot | None:
    fill = result.fill
    if fill is None or fill.filledQuantity <= 0 or fill.averageFillPrice is None:
        return None
    snapshot = inventory_repository.current_snapshot(now=occurred_at)
    signed_quantity = -fill.filledQuantity if command.side.upper() == "SELL" else fill.filledQuantity
    fill_id = f"{command.client_order_id}.{fill.status}.{int(fill.filledQuantity)}.{fill.filledAt.isoformat()}"
    updated = inventory_repository.append_event(
        event_id=f"{command.client_order_id}.fill.{fill.status}",
        event_type=WeightedVotingInventoryEventType.FILL_RECORDED,
        payload={
            "algorithm_id": WEIGHTED_VOTING_ALGORITHM_ID,
            "fill_id": fill_id,
            "position_id": f"weighted_voting.position.{command.symbol}.{command.client_order_id}",
            "symbol": command.symbol,
            "side": "SHORT" if signed_quantity < 0 else "LONG",
            "quantity": signed_quantity,
            "average_entry_price": fill.averageFillPrice,
            "market_reference_price": fill.marketReferencePrice,
            "slippage_per_share": fill.slippagePerShare,
            "spread_impact_per_share": fill.spreadImpactPerShare,
            "commission": fill.commission,
            "regulatory_fees": fill.regulatoryFees,
            "total_execution_cost": fill.totalExecutionCost,
            "execution_costs": fill.executionCostBreakdown,
            "opened_at": fill.filledAt.isoformat(),
            "decision_id": command.decision_id,
            "order_intent_id": command.order_intent_id,
            "client_order_id": command.client_order_id,
            "source": "weighted_voting.execution_gateway.paper_fill",
        },
        occurred_at=occurred_at,
        expected_snapshot_version=snapshot.snapshot_version,
    )
    if store is not None:
        position_id = f"weighted_voting.position.{command.symbol}.{command.client_order_id}"
        _record_local_paper_lifecycle_from_command(
            store=store,
            command=command,
            event_name="weighted_voting.local_paper.fill_recorded",
            occurred_at=occurred_at,
            inventory_snapshot_version=updated.snapshot_version,
            status=fill.status,
            reason_codes=("weighted_voting.local_paper.fill_simulated_locally",),
            position_id=position_id,
            filled_quantity=fill.filledQuantity,
        )
        _record_local_paper_lifecycle_from_command(
            store=store,
            command=command,
            event_name="weighted_voting.local_paper.position_updated",
            occurred_at=occurred_at,
            inventory_snapshot_version=updated.snapshot_version,
            status=fill.status,
            reason_codes=("weighted_voting.execution_gateway.paper_fill",),
            position_id=position_id,
            filled_quantity=fill.filledQuantity,
        )
    return updated


def _record_local_paper_lifecycle_from_command(
    *,
    store: WeightedVotingStateStore,
    command: WeightedVotingBrokerCommand,
    event_name: str,
    occurred_at: datetime,
    inventory_snapshot_version: int | None,
    status: str,
    reason_codes: tuple[str, ...],
    position_id: str | None = None,
    filled_quantity: int | None = None,
) -> None:
    source = {
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "decisionId": command.decision_id,
        "orderIntentId": command.order_intent_id,
        "clientOrderId": command.client_order_id,
        "symbol": command.symbol,
        "side": command.side,
        "quantity": command.quantity,
        "status": status,
    }
    if filled_quantity is not None:
        source["filledQuantity"] = filled_quantity
    record_weighted_voting_local_paper_lifecycle_event(
        store,
        event_name=event_name,
        source=source,
        occurred_at=occurred_at,
        inventory_snapshot_version=inventory_snapshot_version,
        position_id=position_id,
        status=status,
        reason_codes=reason_codes,
    )


def _persist_automatic_result(
    store: WeightedVotingStateStore,
    queue_item: WeightedVotingExecutionQueueItem,
    result: PaperOrderGatewayResult,
    *,
    status: WeightedVotingExecutionStatus,
    recorded_at: datetime,
) -> None:
    command = queue_item.command
    store.write_snapshot(_automatic_result_key(command.client_order_id), result.model_dump(mode="json"))
    updated = {
        **queue_item.as_dict(),
        "status": status,
        "result": result.model_dump(mode="json"),
        "updatedAt": recorded_at.isoformat(),
    }
    store.write_snapshot(_queue_key(command.client_order_id), updated)
    _record_lifecycle(
        store=store,
        command=command,
        status=status,
        recorded_at=recorded_at,
        broker_status=result.status,
        queue_id=queue_item.queue_id,
        reason_codes=tuple(result.reasonCodes),
    )


def _record_lifecycle(
    *,
    store: WeightedVotingStateStore,
    command: WeightedVotingBrokerCommand,
    status: WeightedVotingExecutionStatus,
    recorded_at: datetime,
    reason_codes: tuple[str, ...],
    broker_status: str | None = None,
    queue_id: str | None = None,
) -> WeightedVotingExecutionLifecycleRecord:
    record = WeightedVotingExecutionLifecycleRecord(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        client_order_id=command.client_order_id,
        order_intent_id=command.order_intent_id,
        decision_id=command.decision_id,
        status=status,
        recorded_at=recorded_at,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        broker_status=broker_status,
        queue_id=queue_id,
    )
    store.write_snapshot(_lifecycle_key(command.client_order_id, status), record.as_dict())
    store.write_snapshot(_latest_lifecycle_key(command.client_order_id), record.as_dict())
    return record


def _not_submitted_result(
    *,
    command: WeightedVotingBrokerCommand,
    proposal: GlobalOrderProposal,
    mode: str,
    status: str,
    reason_codes: tuple[str, ...],
    explanation: str,
    evaluated_at: datetime,
) -> PaperOrderGatewayResult:
    return PaperOrderGatewayResult(
        algorithmId=WEIGHTED_VOTING_ALGORITHM_ID,
        orderIntentId=command.order_intent_id,
        clientOrderId=command.client_order_id,
        mode=mode,  # type: ignore[arg-type]
        submitted=False,
        duplicate=False,
        status=status,  # type: ignore[arg-type]
        cancelReplacePolicy="cancel_stale_unfilled_orders_replace_requires_new_intent",
        reasonCodes=reason_codes,
        explanation=explanation,
        evaluatedAt=evaluated_at,
        configurationHash=_hash_json({"proposal": proposal, "status": status, "reasonCodes": reason_codes}),
    )


def _execution_status_from_gateway_result(result: PaperOrderGatewayResult) -> WeightedVotingExecutionStatus:
    if result.duplicate:
        return "RECONCILED"
    if result.status == "ACCEPTED":
        return "ACKNOWLEDGED"
    if result.status == "CANCELED":
        return "CANCELLED"
    if result.status in {"REJECTED", "PARTIALLY_FILLED", "FILLED"}:
        return result.status  # type: ignore[return-value]
    if result.submitted:
        return "SUBMITTED"
    return "REJECTED"


def _entry_price(command: WeightedVotingBrokerCommand) -> float:
    return float(command.limit_price or command.trigger_price or command.stop_price or 0.0)


def _is_market_order(command: WeightedVotingBrokerCommand) -> bool:
    return "market" in command.order_type.lower() and "stop" not in command.order_type.lower()


def _queue_item_from_payload(payload: dict[str, Any]) -> WeightedVotingExecutionQueueItem:
    return WeightedVotingExecutionQueueItem(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        queue_id=str(payload["queueId"]),
        idempotency_key=str(payload["idempotencyKey"]),
        command=_broker_command_from_payload(_payload(payload.get("command"))),
        proposal=GlobalOrderProposal.model_validate(payload["proposal"]),
        global_application=AppliedGlobalGateDecision.model_validate(payload["globalApplication"]),
        local_gate_passed=bool(payload.get("localGatePassed")),
        local_gate_reason_codes=tuple(str(code) for code in payload.get("localGateReasonCodes", ())),
        enqueued_at=_datetime_from_payload(payload["enqueuedAt"], datetime.utcnow()),
        inventory_snapshot_version=int(payload.get("inventorySnapshotVersion") if payload.get("inventorySnapshotVersion") is not None else payload.get("inventory_snapshot_version", -1)),
        status=str(payload.get("status") or "PENDING"),  # type: ignore[arg-type]
        reason_codes=tuple(str(code) for code in payload.get("reasonCodes", ())),
    )


def weighted_voting_execution_queue_item_from_payload(payload: dict[str, Any]) -> WeightedVotingExecutionQueueItem:
    """Restore a persisted Weighted Voting execution queue item for runtime recovery."""

    return _queue_item_from_payload(payload)


def _inventory_snapshot_version_for_enqueue(
    store: WeightedVotingStateStore,
    proposal: GlobalOrderProposal,
    *,
    explicit_version: int | None,
) -> int:
    if explicit_version is not None:
        return int(explicit_version)
    for key in ("inventorySnapshotVersion", "inventory_snapshot_version", "inventoryVersion", "inventory_version"):
        value = proposal.settingsSnapshot.get(key)
        if value is not None:
            return int(value)
    snapshot = _read_optional(store, CURRENT_SNAPSHOT_KEY) or {}
    value = snapshot.get("snapshotVersion") if snapshot.get("snapshotVersion") is not None else snapshot.get("snapshot_version")
    if value is None:
        return -1
    return int(value)


def _local_gate_result_from_queue(queue_item: WeightedVotingExecutionQueueItem) -> WeightedVotingGatePipelineResult:
    return WeightedVotingGatePipelineResult(
        permission_granted=queue_item.local_gate_passed,
        mode="automatic",
        gate_results=(),
        reason_codes=queue_item.local_gate_reason_codes,
        explanation="Weighted Voting execution queue restored the persisted local gate outcome for automatic paper submission.",
    )


def _broker_command_from_payload(payload: dict[str, Any]) -> WeightedVotingBrokerCommand:
    values = {str(key): value for key, value in payload.items()}
    return WeightedVotingBrokerCommand(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        command_id=str(values["commandId"]),
        decision_id=str(values["decisionId"]),
        order_intent_id=str(values["orderIntentId"]),
        client_order_id=str(values["clientOrderId"]),
        symbol=str(values["symbol"]),
        side=str(values["side"]),
        quantity=int(values["quantity"]),
        order_type=str(values["orderType"]),
        trigger_price=None if values.get("triggerPrice") is None else float(values["triggerPrice"]),
        limit_price=None if values.get("limitPrice") is None else float(values["limitPrice"]),
        stop_price=None if values.get("stopPrice") is None else float(values["stopPrice"]),
        target_price=None if values.get("targetPrice") is None else float(values["targetPrice"]),
        time_in_force=str(values["timeInForce"]),
        capital_partition_id=str(values["capitalPartitionId"]),
        planned_risk_dollars=float(values["plannedRiskDollars"]),
        strategy_versions={str(key): str(item) for key, item in dict(values.get("strategyVersions") or {}).items()},
        weight_version=str(values.get("weightVersion") or ""),
        settings_version=str(values.get("settingsVersion") or ""),
        risk_profile_version=str(values.get("riskProfileVersion") or ""),
        market_snapshot_hash=str(values.get("marketSnapshotHash") or ""),
        configuration_hash=str(values["configurationHash"]),
        accepted_global_action=str(values["acceptedGlobalAction"]),
        global_proposal_hash=str(values["globalProposalHash"]),
        global_response_hash=str(values["globalResponseHash"]),
        created_at=_datetime_from_payload(values["createdAt"], datetime.utcnow()),
        expires_at=_datetime_from_payload(values["expiresAt"], datetime.utcnow()),
        reason_codes=tuple(str(code) for code in values.get("reasonCodes", ())),
        submission_status=str(values.get("submissionStatus") or "PENDING_SUBMISSION"),  # type: ignore[arg-type]
    )


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


def _proposal_intent_revision(proposal: GlobalOrderProposal | WeightedVotingOrderProposal) -> int:
    settings = getattr(proposal, "settingsSnapshot", None)
    if isinstance(settings, dict):
        for key in ("intentRevision", "intent_revision"):
            if key in settings:
                try:
                    return int(settings[key])
                except (TypeError, ValueError):
                    return 1
    return 1


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


def _queue_key(client_order_id: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.queue.{client_order_id}"


def _queue_index_key(idempotency_key: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.queue_index.{idempotency_key}"


def _order_intent_index_key(idempotency_key: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.order_intent_index.{idempotency_key}"


def _automatic_result_key(client_order_id: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.automatic_result.{client_order_id}"


def _local_intent_key(order_intent_id: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.local_paper.intent.{order_intent_id}"


def _local_client_order_key(client_order_id: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.local_paper.client_order.{client_order_id}"


def _local_fill_key(client_order_id: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.local_paper.fill.{client_order_id}"


def _local_protective_key(client_order_id: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.local_paper.protective.{client_order_id}"


def _local_result_key(order_intent_id: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.local_paper.result.{order_intent_id}"


def _lifecycle_key(client_order_id: str, status: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.lifecycle.{client_order_id}.{status}"


def _latest_lifecycle_key(client_order_id: str) -> str:
    return f"{WEIGHTED_VOTING_EXECUTION_NAMESPACE}.lifecycle.{client_order_id}.latest"


def _read_optional(store: WeightedVotingStateStore, key: str) -> dict[str, Any] | None:
    try:
        return store.read_snapshot(key)
    except KeyError:
        return None


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
    "WeightedVotingExecutionLifecycleRecord",
    "WeightedVotingExecutionQueueItem",
    "WeightedVotingExecutionReconciliation",
    "WeightedVotingExecutionRejection",
    "WeightedVotingExecutionStatus",
    "WeightedVotingExecutionSubmission",
    "WeightedVotingFillAttribution",
    "WeightedVotingPositionAttribution",
    "build_weighted_voting_broker_command",
    "deterministic_weighted_voting_client_order_id",
    "enqueue_weighted_voting_execution_order",
    "execution_gateway_status",
    "persist_weighted_voting_broker_command",
    "reconcile_weighted_voting_broker_result",
    "record_weighted_voting_fill",
    "record_weighted_voting_rejection",
    "record_weighted_voting_submission",
    "submit_queued_weighted_voting_paper_order",
    "submit_weighted_voting_paper_order",
]
