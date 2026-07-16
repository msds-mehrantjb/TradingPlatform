from __future__ import annotations

from datetime import datetime

from backend.app.algorithms.weighted_voting.decision_gates import WeightedVotingGatePipelineResult
from backend.app.algorithms.weighted_voting.observability import record_order_execution_observability
from backend.app.algorithms.weighted_voting.rollout import (
    WeightedVotingRolloutFlags,
    WeightedVotingRolloutValidation,
    automatic_submission_allowed,
)
from backend.app.execution import PaperOrderGateway, PaperOrderGatewayResult, deterministic_gateway_client_order_id
from backend.app.gates import AppliedGlobalGateDecision, GlobalOrderProposal


WEIGHTED_VOTING_EXECUTION_GATEWAY_VERSION = "weighted_voting_execution_gateway_v1"


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
        result = PaperOrderGatewayResult(
            algorithmId=proposal.algorithmId,
            orderIntentId=proposal.orderIntentId,
            clientOrderId=deterministic_gateway_client_order_id(proposal),
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
    return result
