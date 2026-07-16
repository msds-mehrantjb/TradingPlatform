"""Final acceptance ledger for Weighted Voting V2.

Completion is deliberately tied to explicit evidence. If any required item is
not passing, the upgrade must not be described as complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

WEIGHTED_VOTING_FINAL_ACCEPTANCE_VERSION = "weighted_voting_final_acceptance_v1"
WEIGHTED_VOTING_ALGORITHM_ID = "weighted_voting"


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
    "WeightedVotingAcceptanceItem",
    "WeightedVotingAcceptanceStatus",
    "build_weighted_voting_final_acceptance_report",
    "weighted_voting_acceptance_is_complete",
]
