from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from enum import Enum
from typing import Any

from backend.app.algorithms.weighted_voting.identity import weighted_voting_shared_service_boundary
from backend.app.algorithms.weighted_voting.models import WeightedDecision, WeightedEffectiveSettings, WeightedSide
from backend.app.algorithms.weighted_voting.order_proposal import WeightedVotingOrderProposal, build_weighted_voting_order_proposal
from backend.app.algorithms.weighted_voting.position_sizing import WeightedVotingSizingResult
from backend.app.gates import AppliedGlobalGateDecision, GlobalGateResponse, GlobalOrderProposal, apply_global_gate_response


WEIGHTED_VOTING_GLOBAL_INTERFACE_VERSION = "weighted_voting_global_interface_v1"
WEIGHTED_VOTING_CAPITAL_PARTITION_ID = "weighted_voting.paper.default"
WEIGHTED_VOTING_ALLOWED_GLOBAL_ACTIONS = frozenset(
    {
        "ALLOW",
        "REDUCE_QUANTITY",
        "REJECT_NEW_ENTRY",
        "EXIT_ONLY",
        "EMERGENCY_LIQUIDATE",
    }
)
WEIGHTED_VOTING_GLOBAL_IMMUTABILITY_CHECKS = (
    "weighted_voting.global_interface.algorithm_id_locked",
    "weighted_voting.global_interface.capital_partition_locked",
    "weighted_voting.global_interface.decision_id_locked",
    "weighted_voting.global_interface.side_not_reversed",
    "weighted_voting.global_interface.quantity_not_increased",
    "weighted_voting.global_interface.risk_not_increased",
    "weighted_voting.global_interface.ownership_not_reassigned",
    "weighted_voting.global_interface.settings_not_mutated",
    "weighted_voting.global_interface.active_weights_not_mutated",
)


def build_weighted_voting_global_order_proposal(
    *,
    decision: WeightedDecision,
    sizing: WeightedVotingSizingResult,
    effective_settings: WeightedEffectiveSettings,
    symbol: str,
    trigger_price: float | None,
    limit_price: float | None,
    stop_price: float | None,
    target_price: float | None,
    proposed_at: datetime,
) -> GlobalOrderProposal:
    _validate_decision_ownership(decision)
    local_proposal = build_weighted_voting_order_proposal(
        decision=decision,
        sizing=sizing,
        effective_settings=effective_settings,
        market_snapshot=_minimal_snapshot(symbol=symbol, decision=decision),
        trigger_price=trigger_price,
        limit_price=limit_price,
        stop_price=stop_price,
        target_price=target_price,
        created_at=proposed_at,
    )
    return build_global_order_proposal_from_weighted_voting_proposal(
        proposal=local_proposal,
        decision=decision,
        sizing=sizing,
        effective_settings=effective_settings,
        proposed_at=proposed_at,
    )


def build_global_order_proposal_from_weighted_voting_proposal(
    *,
    proposal: WeightedVotingOrderProposal,
    decision: WeightedDecision,
    sizing: WeightedVotingSizingResult,
    effective_settings: WeightedEffectiveSettings,
    proposed_at: datetime | None = None,
) -> GlobalOrderProposal:
    _validate_weighted_order_proposal(proposal, decision, sizing, effective_settings)
    submitted_at = proposed_at or proposal.created_at
    entry_formula = {
        "source": "weighted_voting.entry_policy_or_quote",
        "proposalId": proposal.proposal_id,
        "triggerPrice": proposal.trigger_price,
        "limitPrice": proposal.limit_price,
        "side": proposal.side,
        "orderType": proposal.order_type,
    }
    stop_formula = {
        "source": "weighted_voting.position_sizing",
        "stopDistance": sizing.stop_distance,
        "structuralStopDistance": sizing.structural_stop_distance,
        "atrStopDistance": sizing.atr_stop_distance,
        "minimumPriceStopDistance": sizing.minimum_price_stop_distance,
        "spreadSafetyBuffer": sizing.spread_safety_buffer,
    }
    target_formula = {
        "source": "weighted_voting.effective_settings",
        "targetR": effective_settings.target_r,
        "targetPrice": proposal.target_price,
    }
    settings_snapshot = effective_settings.model_dump(mode="json")
    settings_snapshot["weightedOrderProposal"] = proposal.as_dict()
    strategy_state_hash = _hash_json(
        {
            "weightedOrderProposalHash": proposal.configuration_hash,
            "voteScores": decision.vote_scores.model_dump(mode="json"),
            "weightAdjustments": [adjustment.model_dump(mode="json") for adjustment in decision.weight_adjustments],
            "configurationHash": decision.configuration_hash,
            "weightVersion": decision.weight_version,
            "marketConditionNotIncluded": "global interface receives no market-condition mutator",
        }
    )
    global_proposal = GlobalOrderProposal(
        algorithmId="weighted_voting",
        capitalPartitionId=WEIGHTED_VOTING_CAPITAL_PARTITION_ID,
        decisionId=proposal.decision_id,
        orderIntentId=f"{proposal.proposal_id}.global_intent",
        intent="new_entry" if proposal.side in (WeightedSide.BUY.value, WeightedSide.SELL.value) else "reconciliation",
        symbol=proposal.symbol,
        side=_global_side(proposal.side),
        quantity=proposal.quantity,
        triggerPrice=proposal.trigger_price,
        limitPrice=proposal.limit_price,
        stopPrice=proposal.stop_price,
        targetPrice=proposal.target_price,
        plannedRiskDollars=sizing.effective_risk_dollars,
        settingsSnapshot=settings_snapshot,
        entryFormula=entry_formula,
        stopFormula=stop_formula,
        targetFormula=target_formula,
        strategyStateHash=strategy_state_hash,
        proposedAt=submitted_at,
        sessionDate=submitted_at.date(),
        configurationHash=_hash_json(
            {
                "interface": WEIGHTED_VOTING_GLOBAL_INTERFACE_VERSION,
                "weightedOrderProposalHash": proposal.configuration_hash,
                "decisionId": proposal.decision_id,
                "quantity": proposal.quantity,
                "settingsHash": effective_settings.configuration_hash,
                "strategyStateHash": strategy_state_hash,
            }
        ),
    )
    _validate_global_order_proposal(global_proposal)
    return global_proposal


def apply_global_response_to_weighted_voting_proposal(
    proposal: GlobalOrderProposal,
    response: GlobalGateResponse,
) -> AppliedGlobalGateDecision:
    _validate_global_order_proposal(proposal)
    _validate_global_response_reduction_only(proposal, response)
    proposal_hash_before = _hash_json(proposal)
    settings_hash_before = _hash_json(proposal.settingsSnapshot)
    strategy_state_hash_before = proposal.strategyStateHash
    applied = apply_global_gate_response(proposal, response)
    proposal_hash_after = _hash_json(proposal)
    settings_hash_after = _hash_json(proposal.settingsSnapshot)
    if applied.side != proposal.side:
        raise ValueError("global response attempted to change Weighted Voting side")
    if applied.globallyAllowedQuantity > proposal.quantity:
        raise ValueError("global response attempted to increase Weighted Voting quantity")
    if applied.maximumAdditionalRiskDollars > proposal.plannedRiskDollars:
        raise ValueError("global response attempted to increase Weighted Voting risk")
    if applied.algorithmId != "weighted_voting":
        raise ValueError("global response attempted to reassign Weighted Voting ownership")
    if applied.decisionId != proposal.decisionId:
        raise ValueError("global response attempted to change Weighted Voting decision id")
    if proposal_hash_before != proposal_hash_after or applied.proposalHash != proposal_hash_before:
        raise ValueError("global response attempted to mutate Weighted Voting proposal")
    if settings_hash_before != settings_hash_after:
        raise ValueError("global response attempted to mutate Weighted Voting settings")
    if strategy_state_hash_before != proposal.strategyStateHash:
        raise ValueError("global response attempted to mutate Weighted Voting active weights")
    return applied


def global_interface_status() -> dict[str, object]:
    shared_boundary = weighted_voting_shared_service_boundary()
    return {
        "version": WEIGHTED_VOTING_GLOBAL_INTERFACE_VERSION,
        "algorithmId": "weighted_voting",
        "capitalPartitionId": WEIGHTED_VOTING_CAPITAL_PARTITION_ID,
        "allowedActions": tuple(sorted(WEIGHTED_VOTING_ALLOWED_GLOBAL_ACTIONS)),
        "immutabilityChecks": WEIGHTED_VOTING_GLOBAL_IMMUTABILITY_CHECKS,
        "sharedServiceBoundary": shared_boundary,
        "sharedServiceAllowedActions": shared_boundary["allowedSharedServiceActions"],
        "sharedServiceForbiddenActions": shared_boundary["forbiddenSharedServiceActions"],
        "explanation": "Weighted Voting global-gate adapter is one-way: shared gates may allow, reduce, reject, or emergency-close, never mutate strategy state.",
    }


def _validate_decision_ownership(decision: WeightedDecision) -> None:
    if decision.algorithm_id != "weighted_voting":
        raise ValueError("Weighted Voting global interface requires a weighted_voting decision")


def _validate_weighted_order_proposal(
    proposal: WeightedVotingOrderProposal,
    decision: WeightedDecision,
    sizing: WeightedVotingSizingResult,
    effective_settings: WeightedEffectiveSettings,
) -> None:
    if proposal.algorithm_id != "weighted_voting":
        raise ValueError("Only Weighted Voting-owned proposals may be submitted through the Weighted Voting global adapter")
    if proposal.decision_id != decision.decision_id:
        raise ValueError("Weighted Voting order proposal decision id does not match decision")
    if proposal.quantity != sizing.quantity:
        raise ValueError("Weighted Voting order proposal quantity does not match sizing")
    if proposal.weight_version != decision.weight_version:
        raise ValueError("Weighted Voting order proposal weight version does not match decision")
    if proposal.settings_version != effective_settings.settings_version:
        raise ValueError("Weighted Voting order proposal settings version does not match effective settings")


def _validate_global_order_proposal(proposal: GlobalOrderProposal) -> None:
    if proposal.algorithmId != "weighted_voting":
        raise ValueError("Weighted Voting global proposal must use algorithm_id weighted_voting")
    if proposal.capitalPartitionId != WEIGHTED_VOTING_CAPITAL_PARTITION_ID:
        raise ValueError("Weighted Voting global proposal has an incorrect capital partition")
    if not proposal.decisionId:
        raise ValueError("Weighted Voting global proposal requires a decision id")
    embedded = proposal.settingsSnapshot.get("weightedOrderProposal")
    if isinstance(embedded, dict):
        if embedded.get("algorithm_id") != "weighted_voting":
            raise ValueError("Weighted Voting global proposal embedded order ownership was reassigned")
        if embedded.get("decision_id") != proposal.decisionId:
            raise ValueError("Weighted Voting global proposal embedded decision id does not match")
        if embedded.get("ownership") != "weighted_voting_until_global_gates":
            raise ValueError("Weighted Voting global proposal embedded ownership is invalid")
        if embedded.get("quantity") is not None and int(embedded["quantity"]) != proposal.quantity:
            raise ValueError("Weighted Voting global proposal embedded quantity does not match")


def _validate_global_response_reduction_only(proposal: GlobalOrderProposal, response: GlobalGateResponse) -> None:
    if response.action not in WEIGHTED_VOTING_ALLOWED_GLOBAL_ACTIONS:
        raise ValueError("global response action is not allowed for Weighted Voting")
    if response.maximumAllowedQuantity > proposal.quantity:
        raise ValueError("global response attempted to increase Weighted Voting quantity")
    if response.maximumAdditionalRiskDollars > proposal.plannedRiskDollars:
        raise ValueError("global response attempted to increase Weighted Voting risk")


def _global_side(side: str) -> str:
    if side == WeightedSide.BUY.value:
        return "BUY"
    if side == WeightedSide.SELL.value:
        return "SELL"
    return "HOLD"


def _minimal_snapshot(*, symbol: str, decision: WeightedDecision):
    from backend.app.algorithms.weighted_voting.models import WeightedCandle, WeightedMarketSnapshot

    timestamp = decision.data_timestamp
    synthetic_price = 1.0
    return WeightedMarketSnapshot(
        symbol=symbol,
        data_timestamp=timestamp,
        one_minute_candles=(WeightedCandle(timestamp=timestamp, open=synthetic_price, high=synthetic_price, low=synthetic_price, close=synthetic_price, volume=0.0),),
        data_manifest_hash=decision.data_manifest_hash,
        explanation="Minimal snapshot reference for legacy Weighted Voting global adapter.",
    )


def _hash_json(value: Any) -> str:
    serialized = json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


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
