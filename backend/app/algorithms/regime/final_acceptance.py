"""Final acceptance ledger for Regime V2 activation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


REGIME_FINAL_ACCEPTANCE_VERSION = "regime_final_acceptance_v1"
REGIME_ALGORITHM_ID = "regime"


class RegimeAcceptanceStatus(str, Enum):
    PASS = "pass"
    PENDING = "pending"
    FAIL = "fail"


@dataclass(frozen=True)
class RegimeAcceptanceItem:
    statement: str
    status: RegimeAcceptanceStatus
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


def build_regime_final_acceptance_report() -> dict[str, object]:
    items = REGIME_FINAL_ACCEPTANCE_ITEMS
    blocking = [
        item
        for item in items
        if item.required_for_completion and item.status is not RegimeAcceptanceStatus.PASS
    ]
    counts = {
        "pass": sum(1 for item in items if item.status is RegimeAcceptanceStatus.PASS),
        "pending": sum(1 for item in items if item.status is RegimeAcceptanceStatus.PENDING),
        "fail": sum(1 for item in items if item.status is RegimeAcceptanceStatus.FAIL),
    }
    return {
        "algorithmId": REGIME_ALGORITHM_ID,
        "version": REGIME_FINAL_ACCEPTANCE_VERSION,
        "complete": not blocking,
        "counts": counts,
        "blockingStatements": [item.statement for item in blocking],
        "items": [item.as_dict() for item in items],
    }


def regime_acceptance_is_complete() -> bool:
    return bool(build_regime_final_acceptance_report()["complete"])


REGIME_FINAL_ACCEPTANCE_ITEMS: tuple[RegimeAcceptanceItem, ...] = (
    RegimeAcceptanceItem(
        "Regime logic is isolated from main.ts.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/execution_pipeline.py", "backend/tests/test_regime_backend_boundary.py"),
        category="Isolation",
    ),
    RegimeAcceptanceItem(
        "Allowed Sell decisions remain Sell.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/order_intent.py", "backend/tests/test_regime_backend_boundary.py"),
        category="Direction",
    ),
    RegimeAcceptanceItem(
        "Regime no longer uses WCA sizing or order adapters.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/order_intent.py", "backend/app/algorithms/regime/sizing.py", "backend/tests/test_regime_backend_boundary.py"),
        category="Isolation",
    ),
    RegimeAcceptanceItem(
        "Directional, context, and safety roles are separated.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/strategy_registry.py", "backend/app/algorithms/regime/family_aggregation.py", "backend/tests/test_regime_backend_boundary.py"),
        category="Strategy roles",
    ),
    RegimeAcceptanceItem(
        "Strategy aliases cannot double vote.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/strategy_registry.py", "backend/tests/test_regime_backend_boundary.py"),
        category="Strategy roles",
    ),
    RegimeAcceptanceItem(
        "Regime classification is deterministic and explainable.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/classifier.py", "backend/tests/test_regime_backend_boundary.py"),
        category="Classification",
    ),
    RegimeAcceptanceItem(
        "Hysteresis is configurable and tested.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/hysteresis.py", "backend/tests/test_regime_backend_boundary.py"),
        category="Classification",
    ),
    RegimeAcceptanceItem(
        "Dynamic settings derive from immutable defaults.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/dynamic_profile.py", "backend/tests/test_regime_backend_boundary.py"),
        category="Settings",
    ),
    RegimeAcceptanceItem(
        "Dynamic risk cannot exceed permitted limits.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/dynamic_profile.py", "backend/app/algorithms/regime/sizing.py", "backend/tests/test_regime_backend_boundary.py"),
        category="Settings",
    ),
    RegimeAcceptanceItem(
        "Global account risk is enforced across all algorithms.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/risk", "backend/tests/test_global_account_risk_state.py", "backend/tests/test_algorithm_ownership_ledger.py"),
        category="Risk",
    ),
    RegimeAcceptanceItem(
        "Global evaluation is enforced server-side.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/risk/api.py", "backend/tests/test_global_portfolio_risk_manager_phase12.py"),
        category="Risk",
    ),
    RegimeAcceptanceItem(
        "Regime has a dedicated backtest.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/backtest/engine.py", "backend/tests/test_regime_phase13_backtest_api.py"),
        category="Backtesting",
    ),
    RegimeAcceptanceItem(
        "Daily backtesting includes Regime independently.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/backtest/engine.py", "backend/tests/test_regime_phase13_backtest_api.py"),
        category="Backtesting",
    ),
    RegimeAcceptanceItem(
        "Regime archives reference Regime results.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/persistence.py", "backend/tests/test_regime_phase14_persistence.py", "frontend/tests/V2DecisionPanel.test.ts"),
        category="Persistence",
    ),
    RegimeAcceptanceItem(
        "ML defaults to shadow mode.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/rollout.py", "backend/app/config.py", "frontend/tests/V2DecisionPanel.test.ts"),
        category="ML",
    ),
    RegimeAcceptanceItem(
        "ML has no lookahead leakage.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/ml", "backend/tests/test_regime_backend_boundary.py"),
        category="ML",
    ),
    RegimeAcceptanceItem(
        "Regime ML cannot move beyond shadow until deterministic walk-forward, untouched holdout, and paper-stability requirements pass.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/ml/promotion_policy.py", "backend/app/algorithms/regime/ml/paper_stability.py", "backend/tests/test_regime_ml_promotion_policy.py"),
        category="ML",
    ),
    RegimeAcceptanceItem(
        "Other algorithms' outputs remain unchanged.",
        RegimeAcceptanceStatus.PASS,
        ("frontend/tests/V2DecisionPanel.test.ts", "backend/tests/test_v1_ensemble_baseline.py", "backend/tests/test_weighted_voting_algorithm_isolation.py"),
        category="Isolation",
    ),
    RegimeAcceptanceItem(
        "Frontend build passes.",
        RegimeAcceptanceStatus.PASS,
        ("frontend/package.json", "scripts/ci_quality_gates.py"),
        category="Verification",
    ),
    RegimeAcceptanceItem(
        "Backend tests pass.",
        RegimeAcceptanceStatus.PASS,
        ("backend/tests", "scripts/ci_quality_gates.py"),
        category="Verification",
    ),
    RegimeAcceptanceItem(
        "Every authoritative Regime strategy, classifier state, transition rule, dynamic profile, local gate, sizing rule, trade-management rule, and execution boundary has a focused automated test suite.",
        RegimeAcceptanceStatus.PENDING,
        (
            "backend/tests/regime/coverage_manifest.json",
            "backend/tests/regime/test_coverage_manifest.py",
            "scripts/ci_quality_gates.py",
        ),
        category="Verification",
        limitations=(
            "Pending until the focused Regime suite passes in CI with branch coverage enabled.",
            "Pending until runtime parity and no-skip manifest checks pass in the quality gate.",
        ),
    ),
    RegimeAcceptanceItem(
        "Frontend tests pass.",
        RegimeAcceptanceStatus.PASS,
        ("frontend/package.json", "frontend/tests/V2DecisionPanel.test.ts", "scripts/ci_quality_gates.py"),
        category="Verification",
    ),
    RegimeAcceptanceItem(
        "Paper-trading rollout is disabled by default or controlled through feature flags.",
        RegimeAcceptanceStatus.PASS,
        ("backend/app/algorithms/regime/rollout.py", "backend/tests/test_regime_phase17_rollout.py"),
        category="Deployment",
    ),
)


__all__ = [
    "REGIME_FINAL_ACCEPTANCE_ITEMS",
    "REGIME_FINAL_ACCEPTANCE_VERSION",
    "RegimeAcceptanceItem",
    "RegimeAcceptanceStatus",
    "build_regime_final_acceptance_report",
    "regime_acceptance_is_complete",
]
