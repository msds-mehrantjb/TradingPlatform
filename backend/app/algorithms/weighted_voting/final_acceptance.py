"""Final acceptance ledger for Weighted Voting V2.

Completion is deliberately tied to explicit evidence. If any required item is
not passing, the upgrade must not be described as complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.models import WeightedDecision, WeightedMarketSnapshot, WeightedSide
from backend.app.algorithms.weighted_voting.order_proposal import WeightedVotingOrderProposal
from backend.app.algorithms.weighted_voting.position_sizing import WeightedVotingSizingResult
from backend.app.gates import AppliedGlobalGateDecision

WEIGHTED_VOTING_FINAL_ACCEPTANCE_VERSION = "weighted_voting_final_acceptance_v1"
WEIGHTED_VOTING_FINAL_ORDER_ACCEPTANCE_VERSION = "weighted_voting_final_order_acceptance_v1"


class WeightedVotingAcceptanceStatus(str, Enum):
    PASS = "pass"
    PENDING = "pending"
    FAIL = "fail"


@dataclass(frozen=True)
class WeightedVotingAcceptanceItem:
    statement: str
    status: WeightedVotingAcceptanceStatus
    evidence: tuple[str, ...]
    category: str = "Final acceptance"
    limitations: tuple[str, ...] = ()
    required_for_completion: bool = True

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "statement": self.statement,
            "status": self.status.value,
            "evidence": list(self.evidence),
            "limitations": list(self.limitations),
            "requiredForCompletion": self.required_for_completion,
        }


@dataclass(frozen=True)
class WeightedVotingFinalOrderAcceptanceInputs:
    decision: WeightedDecision
    market_snapshot: WeightedMarketSnapshot
    sizing_result: WeightedVotingSizingResult
    order_proposal: WeightedVotingOrderProposal
    global_gate_application: AppliedGlobalGateDecision
    local_gates_passed: bool
    current_time: datetime
    expected_configuration_version: str
    expected_weight_version: str
    existing_order_ids: tuple[str, ...] = ()
    position_algorithm_id: str = WEIGHTED_VOTING_ALGORITHM_ID
    data_stale_after_seconds: int = 300


@dataclass(frozen=True)
class WeightedVotingFinalOrderAcceptanceCheck:
    check_id: str
    passed: bool
    reason_code: str
    explanation: str

    def as_dict(self) -> dict[str, object]:
        return {
            "checkId": self.check_id,
            "passed": self.passed,
            "reasonCode": self.reason_code,
            "explanation": self.explanation,
        }


@dataclass(frozen=True)
class WeightedVotingFinalOrderAcceptanceResult:
    accepted: bool
    version: str
    algorithm_id: str
    decision_id: str
    order_proposal_id: str
    approved_quantity: int
    checks: tuple[WeightedVotingFinalOrderAcceptanceCheck, ...]
    reason_codes: tuple[str, ...]
    explanation: str

    def as_dict(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "version": self.version,
            "algorithmId": self.algorithm_id,
            "decisionId": self.decision_id,
            "orderProposalId": self.order_proposal_id,
            "approvedQuantity": self.approved_quantity,
            "checks": [check.as_dict() for check in self.checks],
            "reasonCodes": self.reason_codes,
            "explanation": self.explanation,
        }


def evaluate_weighted_voting_final_order_acceptance(
    inputs: WeightedVotingFinalOrderAcceptanceInputs,
) -> WeightedVotingFinalOrderAcceptanceResult:
    decision = inputs.decision
    proposal = inputs.order_proposal
    global_gate = inputs.global_gate_application
    checks = (
        _check("decision_current", proposal.decision_id == decision.decision_id, "weighted_voting.final_acceptance.decision_stale", "Decision id must still match the order proposal."),
        _check("market_data_fresh", _market_data_fresh(inputs), "weighted_voting.final_acceptance.market_data_stale", "Market data must not be stale at final acceptance."),
        _check("local_gates_passed", inputs.local_gates_passed, "weighted_voting.final_acceptance.local_gates_failed", "All local Weighted Voting gates must pass."),
        _check("quantity_positive", proposal.quantity > 0 and inputs.sizing_result.quantity > 0, "weighted_voting.final_acceptance.quantity_not_positive", "Final local quantity must be greater than zero."),
        _check("stop_and_target_valid", _stop_and_target_valid(proposal), "weighted_voting.final_acceptance.invalid_stop_or_target", "Stop and target must be valid for the proposed side."),
        _check("global_gates_allowed", global_gate.globallyAllowedQuantity > 0 and global_gate.action in {"ALLOW", "REDUCE_QUANTITY"}, "weighted_voting.final_acceptance.global_gate_not_allowed", "Global gates must allow a positive quantity."),
        _check("global_risk_not_increased", global_gate.maximumAdditionalRiskDollars <= global_gate.proposedPlannedRiskDollars, "weighted_voting.final_acceptance.global_risk_increased", "Global gates must not increase Weighted Voting risk."),
        _check("global_direction_not_reversed", _same_direction(proposal.side, global_gate.side), "weighted_voting.final_acceptance.global_direction_reversed", "Global gates must not reverse Buy/Sell direction."),
        _check("position_owned_by_weighted_voting", inputs.position_algorithm_id == WEIGHTED_VOTING_ALGORITHM_ID, "weighted_voting.final_acceptance.position_not_owned", "Accepted position must belong to Weighted Voting."),
        _check("no_duplicate_order", proposal.proposal_id not in inputs.existing_order_ids, "weighted_voting.final_acceptance.duplicate_order", "No duplicate Weighted Voting order may already exist."),
        _check("order_not_expired", inputs.current_time <= proposal.expires_at, "weighted_voting.final_acceptance.order_expired", "Order proposal must not be expired."),
        _check("configuration_version_matches", decision.configuration_version == inputs.expected_configuration_version, "weighted_voting.final_acceptance.configuration_version_mismatch", "Configuration version must match the decision."),
        _check("weight_version_matches", decision.weight_version == proposal.weight_version == inputs.expected_weight_version, "weighted_voting.final_acceptance.weight_version_mismatch", "Weight version must match the decision and proposal."),
    )
    accepted = all(check.passed for check in checks)
    reason_codes = tuple(
        dict.fromkeys(
            ["weighted_voting.final_acceptance.accepted" if accepted else "weighted_voting.final_acceptance.rejected"]
            + [check.reason_code for check in checks if not check.passed]
        )
    )
    return WeightedVotingFinalOrderAcceptanceResult(
        accepted=accepted,
        version=WEIGHTED_VOTING_FINAL_ORDER_ACCEPTANCE_VERSION,
        algorithm_id=WEIGHTED_VOTING_ALGORITHM_ID,
        decision_id=decision.decision_id,
        order_proposal_id=proposal.proposal_id,
        approved_quantity=global_gate.globallyAllowedQuantity if accepted else 0,
        checks=checks,
        reason_codes=reason_codes,
        explanation="Weighted Voting final acceptance verifies local freshness, gates, sizing, risk, ownership, and version invariants before order submission.",
    )


def build_weighted_voting_final_acceptance_report() -> dict[str, object]:
    items = WEIGHTED_VOTING_FINAL_ACCEPTANCE_ITEMS
    blocking = [
        item
        for item in items
        if item.required_for_completion and item.status is not WeightedVotingAcceptanceStatus.PASS
    ]
    counts = {
        "pass": sum(1 for item in items if item.status is WeightedVotingAcceptanceStatus.PASS),
        "pending": sum(1 for item in items if item.status is WeightedVotingAcceptanceStatus.PENDING),
        "fail": sum(1 for item in items if item.status is WeightedVotingAcceptanceStatus.FAIL),
    }
    return {
        "algorithmId": WEIGHTED_VOTING_ALGORITHM_ID,
        "version": WEIGHTED_VOTING_FINAL_ACCEPTANCE_VERSION,
        "complete": not blocking,
        "counts": counts,
        "blockingStatements": [item.statement for item in blocking],
        "items": [item.as_dict() for item in items],
    }


def weighted_voting_acceptance_is_complete() -> bool:
    return bool(build_weighted_voting_final_acceptance_report()["complete"])


def _check(check_id: str, passed: bool, failure_code: str, explanation: str) -> WeightedVotingFinalOrderAcceptanceCheck:
    return WeightedVotingFinalOrderAcceptanceCheck(
        check_id=check_id,
        passed=bool(passed),
        reason_code="weighted_voting.final_acceptance.pass" if passed else failure_code,
        explanation=explanation,
    )


def _market_data_fresh(inputs: WeightedVotingFinalOrderAcceptanceInputs) -> bool:
    if inputs.current_time < inputs.market_snapshot.data_timestamp:
        return False
    age = (inputs.current_time - inputs.market_snapshot.data_timestamp).total_seconds()
    return age <= inputs.data_stale_after_seconds


def _stop_and_target_valid(proposal: WeightedVotingOrderProposal) -> bool:
    if proposal.stop_price is None or proposal.target_price is None or proposal.limit_price is None:
        return False
    if proposal.side == WeightedSide.BUY.value:
        return proposal.stop_price < proposal.limit_price < proposal.target_price
    if proposal.side == WeightedSide.SELL.value:
        return proposal.target_price < proposal.limit_price < proposal.stop_price
    return False


def _same_direction(weighted_side: str, global_side: str) -> bool:
    normalized = {
        WeightedSide.BUY.value: "BUY",
        WeightedSide.SELL.value: "SELL",
        "BUY": "BUY",
        "SELL": "SELL",
    }
    return normalized.get(weighted_side) == normalized.get(global_side)


WEIGHTED_VOTING_FINAL_ACCEPTANCE_ITEMS: tuple[WeightedVotingAcceptanceItem, ...] = (
    WeightedVotingAcceptanceItem(
        "It runs with all ML systems disabled.",
        WeightedVotingAcceptanceStatus.PASS,
        ("backend/tests/test_weighted_voting_ml_decoupling.py", "docs/weighted_voting/final_acceptance_validation.md"),
        category="ML isolation",
    ),
    WeightedVotingAcceptanceItem(
        "It runs when every other algorithm is unavailable.",
        WeightedVotingAcceptanceStatus.PASS,
        ("backend/tests/test_weighted_voting_algorithm_isolation.py", "backend/tests/test_weighted_voting_package_architecture.py"),
        category="Isolation",
    ),
    WeightedVotingAcceptanceItem(
        "Changing another algorithm does not change its output.",
        WeightedVotingAcceptanceStatus.PASS,
        ("backend/tests/test_weighted_voting_algorithm_isolation.py",),
        category="Isolation",
    ),
    WeightedVotingAcceptanceItem(
        "Weighted winner always determines candidate direction.",
        WeightedVotingAcceptanceStatus.PASS,
        ("backend/app/algorithms/weighted_voting/aggregation.py", "backend/tests/test_weighted_voting_aggregation.py", "backend/tests/test_weighted_voting_decision_gates.py"),
        category="Decision correctness",
    ),
    WeightedVotingAcceptanceItem(
        "Automatic mode cannot bypass local gates.",
        WeightedVotingAcceptanceStatus.PASS,
        ("backend/app/algorithms/weighted_voting/execution_gateway.py", "backend/tests/test_weighted_voting_paper_order_gateway.py"),
        category="Execution safety",
    ),
    WeightedVotingAcceptanceItem(
        "Actual quotes are used for spread.",
        WeightedVotingAcceptanceStatus.PASS,
        ("backend/app/algorithms/weighted_voting/service.py", "backend/tests/test_weighted_voting_api_endpoints.py", "backend/tests/test_weighted_voting_decision_gates.py"),
        category="Market data",
    ),
    WeightedVotingAcceptanceItem(
        "Defaults remain the dynamic settings baseline.",
        WeightedVotingAcceptanceStatus.PASS,
        ("backend/app/algorithms/weighted_voting/dynamic_settings.py", "backend/tests/test_weighted_voting_settings.py"),
        category="Dynamic settings",
    ),
    WeightedVotingAcceptanceItem(
        "Dynamic values remain inside envelopes and hard limits.",
        WeightedVotingAcceptanceStatus.PASS,
        ("backend/app/algorithms/weighted_voting/dynamic_settings.py", "backend/tests/test_weighted_voting_settings.py"),
        category="Dynamic settings",
    ),
    WeightedVotingAcceptanceItem(
        "Backtesting and paper trading call the same decision functions.",
        WeightedVotingAcceptanceStatus.PASS,
        ("backend/app/algorithms/weighted_voting/backtest/engine.py", "backend/app/algorithms/weighted_voting/service.py", "backend/tests/test_weighted_voting_backtest_engine.py", "backend/tests/test_weighted_voting_step32_comprehensive.py"),
        category="Replay parity",
    ),
    WeightedVotingAcceptanceItem(
        "Weights use only completed prior data.",
        WeightedVotingAcceptanceStatus.PASS,
        ("backend/app/algorithms/weighted_voting/scheduler.py", "backend/tests/test_weighted_voting_scheduler.py", "backend/tests/test_weighted_voting_walk_forward.py"),
        category="Weights",
    ),
    WeightedVotingAcceptanceItem(
        "Global gates only reduce, reject, or emergency-exit.",
        WeightedVotingAcceptanceStatus.PASS,
        ("backend/app/algorithms/weighted_voting/global_interface.py", "backend/tests/test_global_decision_interface.py", "backend/tests/test_neutral_global_gate_service.py"),
        category="Global safety",
    ),
    WeightedVotingAcceptanceItem(
        "Positions, P/L, risk, and capital remain attributable to Weighted Voting.",
        WeightedVotingAcceptanceStatus.PASS,
        ("backend/tests/test_algorithm_ownership_ledger.py", "backend/tests/test_global_account_risk_state.py", "backend/tests/test_weighted_voting_observability.py"),
        category="Attribution",
    ),
    WeightedVotingAcceptanceItem(
        "The system remains paper-trading only.",
        WeightedVotingAcceptanceStatus.PASS,
        ("backend/app/algorithms/weighted_voting/rollout.py", "backend/tests/test_weighted_voting_rollout.py", "backend/tests/test_weighted_voting_paper_order_gateway.py"),
        category="Deployment",
    ),
    WeightedVotingAcceptanceItem(
        "All unit, integration, isolation, property, and replay tests pass.",
        WeightedVotingAcceptanceStatus.PASS,
        ("backend/tests/test_weighted_voting_step32_comprehensive.py", "backend/tests/test_weighted_voting_algorithm_isolation.py", "backend/tests/test_weighted_voting_backtest_engine.py", "backend/tests/test_weighted_voting_walk_forward.py", "scripts/ci_quality_gates.py"),
        category="Test coverage",
    ),
)


__all__ = [
    "WEIGHTED_VOTING_FINAL_ACCEPTANCE_ITEMS",
    "WEIGHTED_VOTING_FINAL_ACCEPTANCE_VERSION",
    "WEIGHTED_VOTING_FINAL_ORDER_ACCEPTANCE_VERSION",
    "WeightedVotingAcceptanceItem",
    "WeightedVotingAcceptanceStatus",
    "WeightedVotingFinalOrderAcceptanceCheck",
    "WeightedVotingFinalOrderAcceptanceInputs",
    "WeightedVotingFinalOrderAcceptanceResult",
    "build_weighted_voting_final_acceptance_report",
    "evaluate_weighted_voting_final_order_acceptance",
    "weighted_voting_acceptance_is_complete",
]
