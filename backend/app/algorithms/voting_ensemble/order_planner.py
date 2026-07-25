from __future__ import annotations

from datetime import date, datetime

from backend.app.algorithms.voting_ensemble.execution_adapter import translate_voting_ensemble_candidate_to_order
from backend.app.domain.models import EffectiveTradePolicy, GlobalGateDecision, OrderPlan, TradeCandidate
from backend.app.algorithms.voting_ensemble.ml_contracts import SafeMLInferenceResult


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
        decidedAt: datetime,
        sessionDate: date,
        mlDecision: SafeMLInferenceResult | None = None,
    ) -> OrderPlan | None:
        return translate_voting_ensemble_candidate_to_order(
            candidate=candidate,
            policy=policy,
            gateDecision=gateDecision,
            mlDecision=mlDecision,
            decidedAt=decidedAt,
            sessionDate=sessionDate,
        )
