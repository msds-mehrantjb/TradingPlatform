from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from enum import Enum
from typing import Any

from backend.app.algorithms.weighted_voting.models import WeightedDecision, WeightedEffectiveSettings, WeightedSide
from backend.app.algorithms.weighted_voting.position_sizing import WeightedVotingSizingResult
from backend.app.gates import AppliedGlobalGateDecision, GlobalGateResponse, GlobalOrderProposal, apply_global_gate_response


WEIGHTED_VOTING_GLOBAL_INTERFACE_VERSION = "weighted_voting_global_interface_v1"
WEIGHTED_VOTING_CAPITAL_PARTITION_ID = "weighted_voting.paper.default"


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
    entry_formula = {
        "source": "weighted_voting.entry_policy_or_quote",
        "triggerPrice": trigger_price,
        "limitPrice": limit_price,
        "side": decision.proposed_side,
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
        "targetPrice": target_price,
    }
    settings_snapshot = effective_settings.model_dump(mode="json")
    strategy_state_hash = _hash_json(
        {
            "voteScores": decision.vote_scores.model_dump(mode="json"),
            "weightAdjustments": [adjustment.model_dump(mode="json") for adjustment in decision.weight_adjustments],
            "configurationHash": decision.configuration_hash,
            "weightVersion": decision.weight_version,
            "marketConditionNotIncluded": "global interface receives no market-condition mutator",
        }
    )
    return GlobalOrderProposal(
        algorithmId="weighted_voting",
        capitalPartitionId=WEIGHTED_VOTING_CAPITAL_PARTITION_ID,
        decisionId=decision.decision_id,
        orderIntentId=f"{decision.decision_id}.order_intent",
        intent="new_entry" if decision.proposed_side in (WeightedSide.BUY.value, WeightedSide.SELL.value) else "reconciliation",
        symbol=symbol,
        side=_global_side(decision.proposed_side),
        quantity=sizing.quantity,
        triggerPrice=trigger_price,
        limitPrice=limit_price,
        stopPrice=stop_price,
        targetPrice=target_price,
        plannedRiskDollars=sizing.effective_risk_dollars,
        settingsSnapshot=settings_snapshot,
        entryFormula=entry_formula,
        stopFormula=stop_formula,
        targetFormula=target_formula,
        strategyStateHash=strategy_state_hash,
        proposedAt=proposed_at,
        sessionDate=proposed_at.date(),
        configurationHash=_hash_json(
            {
                "interface": WEIGHTED_VOTING_GLOBAL_INTERFACE_VERSION,
                "decisionId": decision.decision_id,
                "quantity": sizing.quantity,
                "settingsHash": effective_settings.configuration_hash,
                "strategyStateHash": strategy_state_hash,
            }
        ),
    )


def apply_global_response_to_weighted_voting_proposal(
    proposal: GlobalOrderProposal,
    response: GlobalGateResponse,
) -> AppliedGlobalGateDecision:
    applied = apply_global_gate_response(proposal, response)
    if applied.side != proposal.side:
        raise ValueError("global response attempted to change Weighted Voting side")
    if applied.proposalHash != apply_global_gate_response(proposal, response).proposalHash:
        raise ValueError("global response attempted to mutate Weighted Voting proposal")
    return applied


def _global_side(side: str) -> str:
    if side == WeightedSide.BUY.value:
        return "BUY"
    if side == WeightedSide.SELL.value:
        return "SELL"
    return "HOLD"


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
