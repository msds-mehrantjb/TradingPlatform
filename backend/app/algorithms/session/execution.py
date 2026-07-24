"""Session execution boundary before neutral order validation and global gates."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time
from enum import Enum
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from backend.app.algorithms.session.config import DEFAULT_SESSION_CONFIG, SessionConfig
from backend.app.algorithms.session.models import LiquidityState, SessionClassification
from backend.app.algorithms.session.profile import SessionProfile
from backend.app.algorithms.session.router import resolve_session_profile
from backend.app.domain.models import DomainModel, Signal, _require_utc
from backend.app.execution.order_contracts import OrderIntent
from backend.app.execution.order_validator import validate_paper_order_intent
from backend.app.gates import AppliedGlobalGateDecision, GlobalGateResponse, GlobalOrderProposal, apply_global_gate_response


SESSION_EXECUTION_BOUNDARY_VERSION = "session_execution_boundary_v1"
SESSION_ALGORITHM_ID = "session"
SESSION_GLOBAL_IMMUTABILITY_CHECKS = (
    "session.execution.neutral_order_validator_required",
    "session.execution.global_gate_required",
    "session.execution.no_direct_submission",
    "session.execution.costs_in_eligibility",
    "session.execution.global_response_one_way",
)
SessionOrderType = Literal["limit", "stop_limit"]
SessionExecutionStatus = Literal["ACCEPTED", "REJECTED", "REDUCED"]


class SessionCandidateDecision(DomainModel):
    boundaryVersion: str = SESSION_EXECUTION_BOUNDARY_VERSION
    classificationId: str = Field(min_length=1)
    originatingStrategyCandidateId: str = Field(min_length=1)
    symbol: str = Field(default="SPY", min_length=1)
    side: Signal
    orderType: SessionOrderType
    desiredQuantity: int = Field(ge=0)
    entryPrice: float = Field(gt=0)
    stopPrice: float | None = Field(default=None, gt=0)
    targetPrice: float | None = Field(default=None, gt=0)
    marketEventTime: datetime
    featureSnapshotTime: datetime
    decisionTime: datetime
    validUntil: datetime
    permittedEntryPriceRange: tuple[float, float]
    expectedGrossEdge: float
    spreadEstimate: float = Field(ge=0.0)
    slippageEstimate: float = Field(ge=0.0)
    fees: float = Field(ge=0.0)
    marketImpactEstimate: float = Field(ge=0.0)
    adverseSelectionBuffer: float = Field(ge=0.0)
    expectedNetEdge: float
    fillProbability: float = Field(ge=0.0, le=1.0)
    quantityCap: int = Field(ge=0)
    sessionProfileVersion: str = Field(min_length=1)
    sessionPhase: str = Field(min_length=1)
    sessionProfileId: str = Field(min_length=1)
    plannedRiskDollars: float = Field(default=0.0, ge=0.0)
    featureReadyLatencyMs: float | None = Field(default=None, ge=0.0)
    inferenceClassificationLatencyMs: float | None = Field(default=None, ge=0.0)
    configurationHash: str = Field(min_length=1)

    @field_validator("marketEventTime", "featureSnapshotTime", "decisionTime", "validUntil")
    @classmethod
    def timestamps_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)

    @model_validator(mode="after")
    def validate_candidate(self) -> SessionCandidateDecision:
        lower, upper = self.permittedEntryPriceRange
        if lower <= 0 or upper <= 0 or lower > upper:
            raise ValueError("permitted entry range must be positive and ordered")
        expected = session_expected_net_edge(
            expected_gross_edge=self.expectedGrossEdge,
            spread_cost=self.spreadEstimate,
            estimated_slippage=self.slippageEstimate,
            fees=self.fees,
            estimated_market_impact=self.marketImpactEstimate,
            adverse_selection_buffer=self.adverseSelectionBuffer,
        )
        if abs(expected - self.expectedNetEdge) > 1e-9:
            raise ValueError("expectedNetEdge must equal gross edge minus execution costs")
        if self.validUntil < self.decisionTime:
            raise ValueError("validUntil must not precede decisionTime")
        if self.featureSnapshotTime < self.marketEventTime:
            raise ValueError("featureSnapshotTime must not precede marketEventTime")
        if self.decisionTime < self.featureSnapshotTime:
            raise ValueError("decisionTime must not precede featureSnapshotTime")
        return self

    def deterministic_hash(self) -> str:
        return _hash_json(self)


class SessionExecutionLatencyRecord(DomainModel):
    featureReadyLatencyMs: float | None = Field(default=None, ge=0.0)
    inferenceClassificationLatencyMs: float | None = Field(default=None, ge=0.0)
    decisionToSubmitLatencyMs: float = Field(ge=0.0)
    orderAcknowledgementLatencyMs: float | None = Field(default=None, ge=0.0)
    opportunityDecay: float = Field(ge=0.0)


class SessionOrderGateDecision(DomainModel):
    boundaryVersion: str = SESSION_EXECUTION_BOUNDARY_VERSION
    status: SessionExecutionStatus
    accepted: bool
    submitted: bool = False
    candidate: SessionCandidateDecision
    profile: dict[str, Any]
    globalOrderProposal: dict[str, Any] | None = None
    appliedGlobalGate: dict[str, Any] | None = None
    validatedOrderIntent: dict[str, Any] | None = None
    approvedQuantity: int = Field(ge=0)
    quantityReduced: bool
    expectedNetEdge: float
    latencies: SessionExecutionLatencyRecord
    reasonCodes: tuple[str, ...]
    immutableChecks: tuple[str, ...] = SESSION_GLOBAL_IMMUTABILITY_CHECKS
    evaluatedAt: datetime
    configurationHash: str = Field(min_length=1)

    @field_validator("evaluatedAt")
    @classmethod
    def evaluated_at_must_be_utc(cls, value: datetime) -> datetime:
        return _require_utc(value)


def build_session_candidate_decision(
    *,
    classification: SessionClassification,
    profile: SessionProfile,
    originating_strategy_candidate_id: str,
    side: Signal | str,
    order_type: SessionOrderType,
    desired_quantity: int,
    entry_price: float,
    permitted_entry_price_range: tuple[float, float],
    expected_gross_edge: float,
    spread_estimate: float,
    slippage_estimate: float,
    fees: float,
    market_impact_estimate: float,
    adverse_selection_buffer: float,
    fill_probability: float,
    quantity_cap: int,
    stop_price: float | None = None,
    target_price: float | None = None,
    planned_risk_dollars: float = 0.0,
    feature_ready_latency_ms: float | None = None,
    inference_classification_latency_ms: float | None = None,
) -> SessionCandidateDecision:
    net_edge = session_expected_net_edge(
        expected_gross_edge=expected_gross_edge,
        spread_cost=spread_estimate,
        estimated_slippage=slippage_estimate,
        fees=fees,
        estimated_market_impact=market_impact_estimate,
        adverse_selection_buffer=adverse_selection_buffer,
    )
    classification_id = _classification_id(classification)
    payload = {
        "classificationId": classification_id,
        "originatingStrategyCandidateId": originating_strategy_candidate_id,
        "symbol": classification.symbol,
        "side": Signal(side),
        "orderType": order_type,
        "desiredQuantity": desired_quantity,
        "entryPrice": entry_price,
        "stopPrice": stop_price,
        "targetPrice": target_price,
        "marketEventTime": classification.market_event_time or classification.decision_time,
        "featureSnapshotTime": classification.feature_snapshot_time or classification.decision_time,
        "decisionTime": classification.decision_time,
        "validUntil": min(classification.valid_until, classification.decision_time + _seconds(profile.signal_validity_period_seconds)),
        "permittedEntryPriceRange": permitted_entry_price_range,
        "expectedGrossEdge": expected_gross_edge,
        "spreadEstimate": spread_estimate,
        "slippageEstimate": slippage_estimate,
        "fees": fees,
        "marketImpactEstimate": market_impact_estimate,
        "adverseSelectionBuffer": adverse_selection_buffer,
        "expectedNetEdge": net_edge,
        "fillProbability": fill_probability,
        "quantityCap": min(max(0, desired_quantity), max(0, quantity_cap)),
        "sessionProfileVersion": profile.profile_version,
        "sessionPhase": classification.phase.value,
        "sessionProfileId": profile.profile_id,
        "plannedRiskDollars": planned_risk_dollars,
        "featureReadyLatencyMs": feature_ready_latency_ms,
        "inferenceClassificationLatencyMs": inference_classification_latency_ms,
    }
    return SessionCandidateDecision(**payload, configurationHash=_hash_json(payload))


def evaluate_session_candidate_order_gate(
    *,
    candidate: SessionCandidateDecision,
    profile: SessionProfile,
    current_classification: SessionClassification,
    current_price: float,
    current_time: datetime,
    quote_age_seconds: float | None,
    global_gate_response: GlobalGateResponse | dict[str, Any] | None,
    order_acknowledged_at: datetime | None = None,
    config: SessionConfig = DEFAULT_SESSION_CONFIG,
) -> SessionOrderGateDecision:
    current_time = _require_utc(current_time)
    order_acknowledged_at = _require_utc(order_acknowledged_at) if order_acknowledged_at else None
    blocking_reasons = _local_rejection_reasons(
        candidate=candidate,
        profile=profile,
        current_classification=current_classification,
        current_price=current_price,
        current_time=current_time,
        quote_age_seconds=quote_age_seconds,
        config=config,
    )
    advisory_reasons: list[str] = []
    latencies = _latencies(candidate, current_time, order_acknowledged_at)
    if latencies.decisionToSubmitLatencyMs > config.session_execution_latency_budget_ms:
        blocking_reasons.append("session.execution.latency_budget_violation")
    if latencies.opportunityDecay > config.session_execution_max_opportunity_decay:
        blocking_reasons.append("session.execution.opportunity_decay_exceeded")
    if global_gate_response is None:
        blocking_reasons.append("session.execution.global_gate_not_evaluated")

    proposal = _global_order_proposal(candidate, profile, current_time, config)
    applied: AppliedGlobalGateDecision | None = None
    if global_gate_response is not None:
        response = global_gate_response if isinstance(global_gate_response, GlobalGateResponse) else GlobalGateResponse.model_validate(global_gate_response)
        _validate_global_response_reduction_only(proposal, response)
        applied = apply_global_gate_response(proposal, response)
        if applied.globallyAllowedQuantity <= 0 or applied.action in {"REJECT_NEW_ENTRY", "EXIT_ONLY", "EMERGENCY_LIQUIDATE"}:
            blocking_reasons.extend(applied.rejectionReasons or ("session.execution.global_gate_rejected",))
        elif applied.quantityReduced:
            advisory_reasons.extend(applied.rejectionReasons or ("session.execution.global_gate_quantity_reduced",))

    approved_quantity = applied.globallyAllowedQuantity if applied else 0
    has_blocking_reasons = bool(blocking_reasons)
    intent = OrderIntent(
        order_intent_id=proposal.orderIntentId,
        algorithm_id=proposal.algorithmId,
        decision_id=proposal.decisionId,
        symbol=proposal.symbol,
        side=proposal.side,
        quantity=approved_quantity if not has_blocking_reasons else 0,
        limit_price=proposal.limitPrice,
        created_at=current_time,
    )
    validated = validate_paper_order_intent(intent)
    if validated.status == "REJECTED":
        blocking_reasons.append("session.execution.neutral_order_validator_rejected")

    accepted = applied is not None and not blocking_reasons and validated.status == "VALIDATED"
    status: SessionExecutionStatus = "ACCEPTED" if accepted and not (applied and applied.quantityReduced) else "REDUCED" if accepted else "REJECTED"
    final_reasons = tuple(
        dict.fromkeys((*blocking_reasons, *advisory_reasons) or ("session.execution.accepted_after_neutral_and_global_gates",))
    )
    return SessionOrderGateDecision(
        status=status,
        accepted=accepted,
        submitted=False,
        candidate=candidate,
        profile=profile.as_dict(),
        globalOrderProposal=proposal.model_dump(mode="json"),
        appliedGlobalGate=applied.model_dump(mode="json") if applied else None,
        validatedOrderIntent=validated.model_dump(mode="json"),
        approvedQuantity=approved_quantity if accepted else 0,
        quantityReduced=bool(applied and applied.quantityReduced),
        expectedNetEdge=candidate.expectedNetEdge,
        latencies=latencies,
        reasonCodes=final_reasons,
        evaluatedAt=current_time,
        configurationHash=_hash_json(
            {
                "candidate": candidate.deterministic_hash(),
                "profile": profile.deterministic_hash(),
                "currentClassification": _classification_id(current_classification),
                "globalResponse": global_gate_response,
                "latencies": latencies.model_dump(mode="json"),
            }
        ),
    )


def session_expected_net_edge(
    *,
    expected_gross_edge: float,
    spread_cost: float,
    estimated_slippage: float,
    fees: float,
    estimated_market_impact: float,
    adverse_selection_buffer: float,
) -> float:
    return round(float(expected_gross_edge) - float(spread_cost) - float(estimated_slippage) - float(fees) - float(estimated_market_impact) - float(adverse_selection_buffer), 10)


def _local_rejection_reasons(
    *,
    candidate: SessionCandidateDecision,
    profile: SessionProfile,
    current_classification: SessionClassification,
    current_price: float,
    current_time: datetime,
    quote_age_seconds: float | None,
    config: SessionConfig,
) -> list[str]:
    reasons: list[str] = []
    if candidate.expectedNetEdge < profile.minimum_net_expected_edge:
        reasons.append("session.execution.expected_net_edge_below_profile_minimum")
    if quote_age_seconds is None or quote_age_seconds > profile.maximum_quote_age_seconds:
        reasons.append("session.execution.quote_stale")
    if current_time > candidate.validUntil:
        reasons.append("session.execution.signal_stale")
    lower, upper = candidate.permittedEntryPriceRange
    if current_price < lower or current_price > upper:
        reasons.append("session.execution.price_left_permitted_entry_range")
    current_profile = resolve_session_profile(current_classification, config=config)
    if current_classification.block_new_entries or current_profile.block_new_entries:
        reasons.append("session.execution.current_phase_or_profile_blocks_entries")
    if current_classification.liquidity_state in {LiquidityState.STRESSED, LiquidityState.STALE, LiquidityState.UNKNOWN}:
        reasons.append("session.execution.liquidity_stressed")
    if candidate.fillProbability < config.session_execution_minimum_fill_probability:
        reasons.append("session.execution.fill_probability_too_low")
    if profile.block_new_entries or candidate.quantityCap <= 0:
        reasons.append("session.execution.profile_blocks_entries")
    if candidate.orderType not in profile.allowed_order_types:
        reasons.append("session.execution.order_type_not_allowed_by_profile")
    return reasons


def _global_order_proposal(candidate: SessionCandidateDecision, profile: SessionProfile, proposed_at: datetime, config: SessionConfig) -> GlobalOrderProposal:
    quantity = min(candidate.desiredQuantity, candidate.quantityCap, profile.maximum_concurrent_session_originated_positions * max(candidate.quantityCap, 0))
    return GlobalOrderProposal(
        algorithmId=SESSION_ALGORITHM_ID,
        capitalPartitionId=config.session_execution_default_capital_partition_id,
        decisionId=candidate.classificationId,
        orderIntentId=f"{candidate.classificationId}.{candidate.originatingStrategyCandidateId}.order",
        intent="new_entry",
        symbol=candidate.symbol,
        side=candidate.side,
        quantity=max(0, quantity),
        triggerPrice=candidate.entryPrice if candidate.orderType == "stop_limit" else None,
        limitPrice=candidate.entryPrice,
        stopPrice=candidate.stopPrice,
        targetPrice=candidate.targetPrice,
        plannedRiskDollars=candidate.plannedRiskDollars,
        settingsSnapshot={"sessionProfile": profile.as_dict(), "sessionCandidate": candidate.model_dump(mode="json")},
        entryFormula={"source": "session_candidate_decision", "permittedEntryPriceRange": candidate.permittedEntryPriceRange, "expectedNetEdge": candidate.expectedNetEdge},
        stopFormula={"source": "originating_strategy_candidate", "stopPrice": candidate.stopPrice},
        targetFormula={"source": "originating_strategy_candidate", "targetPrice": candidate.targetPrice},
        strategyStateHash=_hash_json({"sessionProfileHash": profile.deterministic_hash(), "sessionCandidateHash": candidate.deterministic_hash(), "readOnly": True}),
        proposedAt=proposed_at,
        sessionDate=proposed_at.date(),
        configurationHash=_hash_json({"boundaryVersion": SESSION_EXECUTION_BOUNDARY_VERSION, "candidate": candidate.deterministic_hash(), "profile": profile.deterministic_hash()}),
    )


def _validate_global_response_reduction_only(proposal: GlobalOrderProposal, response: GlobalGateResponse) -> None:
    if response.maximumAllowedQuantity > proposal.quantity:
        raise ValueError("global response attempted to increase Session quantity")
    if response.maximumAdditionalRiskDollars > proposal.plannedRiskDollars:
        raise ValueError("global response attempted to increase Session risk")


def _latencies(candidate: SessionCandidateDecision, current_time: datetime, order_acknowledged_at: datetime | None) -> SessionExecutionLatencyRecord:
    decision_to_submit_ms = max(0.0, (current_time - candidate.decisionTime).total_seconds() * 1000.0)
    ack_ms = None if order_acknowledged_at is None else max(0.0, (order_acknowledged_at - current_time).total_seconds() * 1000.0)
    decay = 0.0 if candidate.expectedGrossEdge == 0 else min(1.0, decision_to_submit_ms / max(1.0, (candidate.validUntil - candidate.decisionTime).total_seconds() * 1000.0))
    return SessionExecutionLatencyRecord(
        featureReadyLatencyMs=candidate.featureReadyLatencyMs,
        inferenceClassificationLatencyMs=candidate.inferenceClassificationLatencyMs,
        decisionToSubmitLatencyMs=decision_to_submit_ms,
        orderAcknowledgementLatencyMs=ack_ms,
        opportunityDecay=round(decay, 10),
    )


def _classification_id(classification: SessionClassification) -> str:
    evidence = classification.evidence or {}
    candidate = evidence.get("classificationId") or evidence.get("classification_id")
    return str(candidate) if candidate else f"session-classification-{classification.deterministic_hash()[:16]}"


def _seconds(value: int) -> Any:
    from datetime import timedelta

    return timedelta(seconds=max(0, int(value)))


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
