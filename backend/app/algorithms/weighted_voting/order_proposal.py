"""Weighted Voting-owned order proposal before global gate submission."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import Enum
import hashlib
import json
from typing import Any, Literal

from backend.app.algorithms.weighted_voting.entry_policy import WeightedEntryPolicyResult
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.models import WeightedDecision, WeightedEffectiveSettings, WeightedMarketSnapshot, WeightedSide, WeightedVotingSignal
from backend.app.algorithms.weighted_voting.position_sizing import WeightedVotingSizingResult
from backend.app.algorithms.weighted_voting.risk_budget import WEIGHTED_VOTING_RISK_BUDGET_VERSION, WeightedVotingRiskBudget


WEIGHTED_VOTING_ORDER_PROPOSAL_VERSION = "weighted_voting_order_proposal_v2"
WEIGHTED_VOTING_ORDER_PROPOSAL_OWNERSHIP = "weighted_voting_until_global_gates"


@dataclass(frozen=True)
class WeightedVotingOrderProposal:
    algorithm_id: Literal["weighted_voting"]
    decision_id: str
    symbol: str
    side: str
    quantity: int
    order_type: str
    trigger_price: float | None
    limit_price: float | None
    stop_price: float | None
    target_price: float | None
    time_in_force: str
    strategy_versions: dict[str, str]
    weight_version: str
    settings_version: str
    risk_profile_version: str
    market_snapshot_hash: str
    created_at: datetime
    expires_at: datetime
    reason_codes: tuple[str, ...]
    proposal_id: str
    proposal_version: str = WEIGHTED_VOTING_ORDER_PROPOSAL_VERSION
    ownership: str = WEIGHTED_VOTING_ORDER_PROPOSAL_OWNERSHIP
    configuration_hash: str = ""
    explanation: str = "Weighted Voting owns this proposal until it is submitted to global gates."

    def __post_init__(self) -> None:
        if self.algorithm_id != WEIGHTED_VOTING_ALGORITHM_ID:
            raise ValueError("Weighted Voting order proposals cannot be assigned to another algorithm")
        if self.quantity < 0:
            raise ValueError("Weighted Voting order quantity must be non-negative")
        if self.expires_at < self.created_at:
            raise ValueError("Weighted Voting order proposal cannot expire before creation")

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        payload["expires_at"] = self.expires_at.isoformat()
        return payload


def build_weighted_voting_order_proposal(
    *,
    decision: WeightedDecision,
    sizing: WeightedVotingSizingResult,
    effective_settings: WeightedEffectiveSettings,
    market_snapshot: WeightedMarketSnapshot,
    signals: tuple[WeightedVotingSignal, ...] = (),
    entry_policy: WeightedEntryPolicyResult | None = None,
    risk_budget: WeightedVotingRiskBudget | None = None,
    trigger_price: float | None = None,
    limit_price: float | None = None,
    stop_price: float | None = None,
    target_price: float | None = None,
    order_type: str | None = None,
    time_in_force: str = "day",
    created_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> WeightedVotingOrderProposal:
    created = created_at or market_snapshot.data_timestamp
    expiry = expires_at or _entry_expiration(entry_policy, created)
    resolved_trigger = entry_policy.trigger_price if entry_policy is not None else trigger_price
    resolved_limit = entry_policy.limit_price if entry_policy is not None else limit_price
    resolved_order_type = entry_policy.order_type if entry_policy is not None else (order_type or "limit")
    risk_profile_version = risk_budget.budget_version if risk_budget is not None else WEIGHTED_VOTING_RISK_BUDGET_VERSION
    reason_codes = tuple(
        dict.fromkeys(
            (
                "weighted_voting.order_proposal.created",
                *decision.reason_codes,
                *sizing.reason_codes,
                *((entry_policy.reason_codes if entry_policy is not None else ())),
            )
        )
    )
    proposal_id = f"{decision.decision_id}.weighted_order_proposal"
    configuration_hash = _hash_json(
        {
            "proposalVersion": WEIGHTED_VOTING_ORDER_PROPOSAL_VERSION,
            "decisionId": decision.decision_id,
            "symbol": market_snapshot.symbol,
            "side": _side_value(decision.proposed_side),
            "quantity": sizing.quantity,
            "weightVersion": decision.weight_version,
            "settingsVersion": effective_settings.settings_version,
            "riskProfileVersion": risk_profile_version,
            "marketSnapshotHash": _market_snapshot_hash(market_snapshot),
        }
    )
    return WeightedVotingOrderProposal(
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        decision_id=decision.decision_id,
        symbol=market_snapshot.symbol,
        side=_side_value(decision.proposed_side),
        quantity=sizing.quantity,
        order_type=resolved_order_type,
        trigger_price=resolved_trigger,
        limit_price=resolved_limit,
        stop_price=stop_price,
        target_price=target_price,
        time_in_force=time_in_force,
        strategy_versions=_strategy_versions(signals),
        weight_version=decision.weight_version,
        settings_version=effective_settings.settings_version,
        risk_profile_version=risk_profile_version,
        market_snapshot_hash=_market_snapshot_hash(market_snapshot),
        created_at=created,
        expires_at=expiry,
        reason_codes=reason_codes,
        proposal_id=proposal_id,
        configuration_hash=configuration_hash,
    )


def _entry_expiration(entry_policy: WeightedEntryPolicyResult | None, created_at: datetime) -> datetime:
    if entry_policy is not None and entry_policy.entry_expiration is not None:
        return entry_policy.entry_expiration
    return created_at + timedelta(minutes=5)


def _strategy_versions(signals: tuple[WeightedVotingSignal, ...]) -> dict[str, str]:
    return {signal.strategy_id: signal.strategy_version for signal in signals}


def _market_snapshot_hash(snapshot: WeightedMarketSnapshot) -> str:
    if snapshot.data_manifest_hash:
        return str(snapshot.data_manifest_hash)
    return snapshot.deterministic_hash()[:16]


def _side_value(side: WeightedSide | str) -> str:
    if isinstance(side, Enum):
        return str(side.value)
    return str(side)


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
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


__all__ = [
    "WEIGHTED_VOTING_ORDER_PROPOSAL_OWNERSHIP",
    "WEIGHTED_VOTING_ORDER_PROPOSAL_VERSION",
    "WeightedVotingOrderProposal",
    "build_weighted_voting_order_proposal",
]
