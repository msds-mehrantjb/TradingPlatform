from __future__ import annotations

from datetime import date, datetime
from math import floor

from backend.app.algorithms.voting_ensemble.exit_policy import VOTING_ENSEMBLE_DEFAULT_MAX_HOLDING_MINUTES
from backend.app.domain.models import EffectiveTradePolicy, GlobalGateDecision, OrderPlan, TradeCandidate
from backend.app.algorithms.meta_strategy.inference.safe_inference import SafeMLInferenceResult


VOTING_ENSEMBLE_ORDER_PLANNER_VERSION = "voting_ensemble_order_planner_v1"


def order_planner_reason_codes() -> tuple[str, ...]:
    return (
        VOTING_ENSEMBLE_ORDER_PLANNER_VERSION,
        "voting_ensemble.order_planner.limit_entry",
        "voting_ensemble.order_planner.policy_quantity_cap",
        "voting_ensemble.order_planner.policy_notional_cap",
        "voting_ensemble.order_planner.max_holding_period",
    )


class VotingEnsembleOrderPlanner:
    def order_plan(
        self,
        *,
        candidate: TradeCandidate | None,
        policy: EffectiveTradePolicy,
        gateDecision: GlobalGateDecision,
        mlDecision: SafeMLInferenceResult,
        decidedAt: datetime,
        sessionDate: date,
    ) -> OrderPlan | None:
        if candidate is None:
            return None

        errors = _entry_validation_errors(candidate, policy, gateDecision, mlDecision)
        if errors:
            return _no_order(candidate, policy, errors, decidedAt, sessionDate)

        quantity = min(candidate.quantity or 1, policy.maxQuantity, floor(policy.maxNotional / max(candidate.entryPrice, 0.01)))
        if quantity <= 0:
            return _no_order(candidate, policy, ["voting_ensemble.order_planner.zero_quantity"], decidedAt, sessionDate)

        return OrderPlan(
            orderPlanId=f"voting-ensemble-order-{candidate.candidateId}",
            candidateId=candidate.candidateId,
            symbol=candidate.symbol,
            side=candidate.signal,
            orderType="LIMIT",
            quantity=quantity,
            entryPrice=candidate.entryPrice,
            stopPrice=candidate.stopPrice,
            targetPrice=candidate.targetPrice,
            limitPrice=candidate.entryPrice,
            maximumHoldingMinutes=VOTING_ENSEMBLE_DEFAULT_MAX_HOLDING_MINUTES,
            timeInForce="DAY",
            eligible=True,
            validationErrors=[],
            explanation="Voting Ensemble order planner accepted a bounded paper order plan.",
            generatedAt=decidedAt,
            sessionDate=sessionDate,
            configurationHash=f"{policy.configurationHash}:{VOTING_ENSEMBLE_ORDER_PLANNER_VERSION}",
        )


def _entry_validation_errors(
    candidate: TradeCandidate,
    policy: EffectiveTradePolicy,
    gate_decision: GlobalGateDecision,
    ml_decision: SafeMLInferenceResult,
) -> list[str]:
    errors: list[str] = []
    if not gate_decision.eligible:
        errors.append("voting_ensemble.order_planner.local_gate_block")
    if not ml_decision.candidateAccepted:
        errors.append("voting_ensemble.order_planner.ml_filter_block")
    if policy.maxQuantity <= 0:
        errors.append("voting_ensemble.order_planner.max_quantity_zero")
    if policy.maxNotional <= 0:
        errors.append("voting_ensemble.order_planner.max_notional_zero")
    if candidate.quantity <= 0:
        errors.append("voting_ensemble.order_planner.candidate_quantity_zero")
    return errors


def _no_order(
    candidate: TradeCandidate,
    policy: EffectiveTradePolicy,
    errors: list[str],
    decided_at: datetime,
    session_date: date,
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
        maximumHoldingMinutes=VOTING_ENSEMBLE_DEFAULT_MAX_HOLDING_MINUTES,
        timeInForce="DAY",
        eligible=False,
        validationErrors=errors,
        explanation="Voting Ensemble order planner blocked this new entry.",
        generatedAt=decided_at,
        sessionDate=session_date,
        configurationHash=f"{policy.configurationHash}:{VOTING_ENSEMBLE_ORDER_PLANNER_VERSION}",
    )
