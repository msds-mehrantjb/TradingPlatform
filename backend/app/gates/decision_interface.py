from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from enum import Enum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.app.domain.models import DomainModel, Signal, _require_utc
from backend.app.gates.neutral import NeutralGlobalGateDecision


GLOBAL_DECISION_INTERFACE_VERSION = "global_decision_interface_v1"
GlobalGateResponseAction = Literal["ALLOW", "REDUCE_QUANTITY", "REJECT_NEW_ENTRY", "EXIT_ONLY", "EMERGENCY_LIQUIDATE"]
GlobalOrderIntent = Literal["new_entry", "protective_exit", "risk_reducing", "end_of_day_liquidation", "reconciliation"]


class GlobalOrderProposal(DomainModel):
    interfaceVersion: str = GLOBAL_DECISION_INTERFACE_VERSION
    algorithmId: str = Field(min_length=1)
    capitalPartitionId: str = Field(min_length=1)
    decisionId: str = Field(min_length=1)
    orderIntentId: str = Field(min_length=1)
    intent: GlobalOrderIntent
    symbol: str = Field(min_length=1)
    side: Signal
    quantity: int = Field(ge=0)
    triggerPrice: float | None = Field(default=None, gt=0)
    limitPrice: float | None = Field(default=None, gt=0)
    stopPrice: float | None = Field(default=None, gt=0)
    targetPrice: float | None = Field(default=None, gt=0)
    plannedRiskDollars: float = Field(ge=0)
    settingsSnapshot: dict[str, Any] = Field(default_factory=dict)
    entryFormula: dict[str, Any] = Field(default_factory=dict)
    stopFormula: dict[str, Any] = Field(default_factory=dict)
    targetFormula: dict[str, Any] = Field(default_factory=dict)
    strategyStateHash: str = Field(min_length=1)
    proposedAt: datetime
    sessionDate: date
    configurationHash: str = Field(min_length=1)

    @field_validator("proposedAt")
    @classmethod
    def proposed_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class GlobalGateResponse(DomainModel):
    interfaceVersion: str = GLOBAL_DECISION_INTERFACE_VERSION
    action: GlobalGateResponseAction
    maximumAllowedQuantity: int = Field(ge=0)
    maximumAdditionalRiskDollars: float = Field(ge=0)
    rejectionReasons: tuple[str, ...] = ()
    emergencyAction: str | None = None
    evaluatedAt: datetime
    configurationHash: str = Field(min_length=1)

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


class AppliedGlobalGateDecision(DomainModel):
    interfaceVersion: str = GLOBAL_DECISION_INTERFACE_VERSION
    algorithmId: str
    decisionId: str
    orderIntentId: str
    action: GlobalGateResponseAction
    side: Signal
    proposedQuantity: int = Field(ge=0)
    globallyAllowedQuantity: int = Field(ge=0)
    proposedPlannedRiskDollars: float = Field(ge=0)
    maximumAdditionalRiskDollars: float = Field(ge=0)
    quantityReduced: bool
    riskReducingExitAllowed: bool
    emergencyAction: str | None = None
    rejectionReasons: tuple[str, ...] = ()
    immutableChecks: tuple[str, ...]
    proposalHash: str = Field(min_length=1)
    responseHash: str = Field(min_length=1)
    evaluatedAt: datetime
    explanation: str = Field(min_length=1)

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def globally_allowed_quantity_cannot_exceed_proposed(self) -> AppliedGlobalGateDecision:
        if self.globallyAllowedQuantity > self.proposedQuantity:
            raise ValueError("global gate may not increase quantity above the algorithm proposal")
        return self


def response_from_neutral_gate(
    proposal: GlobalOrderProposal,
    decision: NeutralGlobalGateDecision,
    *,
    maximum_allowed_quantity: int | None = None,
    maximum_additional_risk_dollars: float | None = None,
) -> GlobalGateResponse:
    action = {
        "allow": "ALLOW",
        "reduce_quantity": "REDUCE_QUANTITY",
        "reject_new_entry": "REJECT_NEW_ENTRY",
        "exits_only": "EXIT_ONLY",
        "emergency_liquidation": "EMERGENCY_LIQUIDATE",
    }[decision.action]
    quantity_cap = maximum_allowed_quantity if maximum_allowed_quantity is not None else int(proposal.quantity * decision.quantityMultiplierCap)
    risk_cap = maximum_additional_risk_dollars if maximum_additional_risk_dollars is not None else proposal.plannedRiskDollars * decision.quantityMultiplierCap
    if action in {"REJECT_NEW_ENTRY", "EXIT_ONLY", "EMERGENCY_LIQUIDATE"} and proposal.intent == "new_entry":
        quantity_cap = 0
        risk_cap = 0.0
    return GlobalGateResponse(
        action=action,
        maximumAllowedQuantity=max(0, quantity_cap),
        maximumAdditionalRiskDollars=max(0.0, risk_cap),
        rejectionReasons=decision.reasonCodes,
        emergencyAction="liquidate_owned_risk_reducing_positions" if action == "EMERGENCY_LIQUIDATE" else None,
        evaluatedAt=decision.evaluatedAt,
        configurationHash=decision.configurationHash,
    )


def apply_global_gate_response(proposal: GlobalOrderProposal, response: GlobalGateResponse) -> AppliedGlobalGateDecision:
    allowed_quantity = _allowed_quantity(proposal, response)
    proposal_hash = _hash_model(proposal)
    response_hash = _hash_model(response)
    return AppliedGlobalGateDecision(
        algorithmId=proposal.algorithmId,
        decisionId=proposal.decisionId,
        orderIntentId=proposal.orderIntentId,
        action=response.action,
        side=proposal.side,
        proposedQuantity=proposal.quantity,
        globallyAllowedQuantity=allowed_quantity,
        proposedPlannedRiskDollars=proposal.plannedRiskDollars,
        maximumAdditionalRiskDollars=response.maximumAdditionalRiskDollars,
        quantityReduced=allowed_quantity < proposal.quantity,
        riskReducingExitAllowed=proposal.intent != "new_entry" or response.action in {"EXIT_ONLY", "EMERGENCY_LIQUIDATE"},
        emergencyAction=response.emergencyAction,
        rejectionReasons=response.rejectionReasons,
        immutableChecks=(
            "global_gate.side_immutable",
            "global_gate.strategy_state_not_modified",
            "global_gate.entry_formula_not_modified",
            "global_gate.stop_formula_not_modified",
            "global_gate.target_formula_not_modified",
        ),
        proposalHash=proposal_hash,
        responseHash=response_hash,
        evaluatedAt=response.evaluatedAt,
        explanation="Global gate response was applied one-way: only quantity/risk permission changed.",
    )


def _allowed_quantity(proposal: GlobalOrderProposal, response: GlobalGateResponse) -> int:
    if response.action == "ALLOW":
        return min(proposal.quantity, response.maximumAllowedQuantity)
    if response.action == "REDUCE_QUANTITY":
        by_quantity = min(proposal.quantity, response.maximumAllowedQuantity)
        if proposal.plannedRiskDollars <= 0:
            return by_quantity
        risk_ratio = min(1.0, response.maximumAdditionalRiskDollars / proposal.plannedRiskDollars)
        return min(by_quantity, int(proposal.quantity * risk_ratio))
    if response.action in {"REJECT_NEW_ENTRY", "EXIT_ONLY", "EMERGENCY_LIQUIDATE"} and proposal.intent == "new_entry":
        return 0
    return min(proposal.quantity, response.maximumAllowedQuantity)


def _hash_model(value: Any) -> str:
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
