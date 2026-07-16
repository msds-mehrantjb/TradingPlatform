"""Final acceptance ledger for WCA modernization.

This module is deliberately conservative: it records completion evidence, but
does not infer that the modernization is complete while any required checklist
item remains pending.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID


WCA_FINAL_ACCEPTANCE_VERSION = "wca_final_acceptance_checklist_v1"


class WcaAcceptanceStatus(str, Enum):
    PASS = "pass"
    PENDING = "pending"
    FAIL = "fail"


@dataclass(frozen=True)
class WcaAcceptanceItem:
    category: str
    statement: str
    status: WcaAcceptanceStatus
    evidence: tuple[str, ...]
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


def build_wca_final_acceptance_report() -> dict[str, object]:
    items = WCA_FINAL_ACCEPTANCE_ITEMS
    blocking = [
        item
        for item in items
        if item.required_for_completion and item.status is not WcaAcceptanceStatus.PASS
    ]
    counts = {
        "pass": sum(1 for item in items if item.status is WcaAcceptanceStatus.PASS),
        "pending": sum(1 for item in items if item.status is WcaAcceptanceStatus.PENDING),
        "fail": sum(1 for item in items if item.status is WcaAcceptanceStatus.FAIL),
    }
    return {
        "algorithmId": WCA_ALGORITHM_ID,
        "version": WCA_FINAL_ACCEPTANCE_VERSION,
        "complete": not blocking,
        "counts": counts,
        "blockingStatements": [item.statement for item in blocking],
        "items": [item.as_dict() for item in items],
    }


def wca_acceptance_is_complete() -> bool:
    return bool(build_wca_final_acceptance_report()["complete"])


WCA_FINAL_ACCEPTANCE_ITEMS: tuple[WcaAcceptanceItem, ...] = (
    WcaAcceptanceItem(
        "Architecture",
        "WCA is an isolated backend algorithm.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca", "backend/tests/test_wca_step19_comprehensive.py"),
    ),
    WcaAcceptanceItem(
        "Architecture",
        "Strategies are isolated modules.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/strategies", "backend/tests/test_wca_step19_comprehensive.py"),
    ),
    WcaAcceptanceItem(
        "Architecture",
        "Frontend is presentation-only.",
        WcaAcceptanceStatus.PASS,
        ("frontend/src/features/wca", "frontend/src/main.ts", "backend/tests/test_wca_step21_final_acceptance.py"),
    ),
    WcaAcceptanceItem(
        "Architecture",
        "Live, paper, and backtest use the same engine.",
        WcaAcceptanceStatus.PENDING,
        ("backend/app/algorithms/wca/backtest/engine.py", "backend/app/algorithms/wca/engine.py"),
        ("Backend backtesting exists, but WCA paper execution is not yet fully routed through the same production path.",),
    ),
    WcaAcceptanceItem(
        "Architecture",
        "WCA does not depend on ML.",
        WcaAcceptanceStatus.PASS,
        ("backend/tests/test_wca_step13_ml_forecast_decoupling.py", "backend/app/algorithms/wca/feature_snapshot.py"),
    ),
    WcaAcceptanceItem(
        "Strategies",
        "Only primary alpha strategies cast votes.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/strategy_registry.py", "backend/tests/test_wca_step3_strategy_catalog.py"),
    ),
    WcaAcceptanceItem(
        "Strategies",
        "Context indicators are modifiers.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/modifiers", "backend/tests/test_wca_step3_strategy_catalog.py"),
    ),
    WcaAcceptanceItem(
        "Strategies",
        "Risk filters are gates.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/local_gates.py", "backend/tests/test_wca_step10_local_gates.py"),
    ),
    WcaAcceptanceItem(
        "Strategies",
        "Duplicate strategy logic is removed.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/strategies", "backend/tests/test_wca_step3_strategy_catalog.py"),
    ),
    WcaAcceptanceItem(
        "Strategies",
        "Hold and Not Applicable are different.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/contracts.py", "backend/tests/test_wca_step9_aggregation.py"),
    ),
    WcaAcceptanceItem(
        "Strategies",
        "Strategy-family concentration is controlled.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/aggregation.py", "backend/app/algorithms/wca/weights.py"),
    ),
    WcaAcceptanceItem(
        "Confidence and weights",
        "Confidence is statistically calibrated.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/confidence.py", "backend/tests/test_wca_step5_confidence_calibration.py"),
    ),
    WcaAcceptanceItem(
        "Confidence and weights",
        "Weights are leakage-free.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/weights.py", "backend/tests/test_wca_step6_performance_weights.py"),
    ),
    WcaAcceptanceItem(
        "Confidence and weights",
        "Weights use sample reliability and shrinkage.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/weights.py", "backend/tests/test_wca_step6_performance_weights.py"),
    ),
    WcaAcceptanceItem(
        "Confidence and weights",
        "Family and strategy caps are enforced.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/weights.py", "backend/tests/test_wca_step19_comprehensive.py"),
    ),
    WcaAcceptanceItem(
        "Confidence and weights",
        "Weight snapshots are versioned and reproducible.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/contracts.py", "backend/app/algorithms/wca/repository.py"),
    ),
    WcaAcceptanceItem(
        "Settings",
        "User defaults remain the baseline.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/configuration.py", "backend/tests/test_wca_step8_dynamic_profile.py"),
    ),
    WcaAcceptanceItem(
        "Settings",
        "Dynamic profiles are bounded.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/dynamic_profile.py", "backend/tests/test_wca_step8_dynamic_profile.py"),
    ),
    WcaAcceptanceItem(
        "Settings",
        "Effective settings do not overwrite defaults.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/dynamic_profile.py", "backend/tests/test_wca_step8_dynamic_profile.py"),
    ),
    WcaAcceptanceItem(
        "Settings",
        "Initial dynamic behavior is defensive only.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/dynamic_profile.py", "backend/tests/test_wca_step8_dynamic_profile.py"),
    ),
    WcaAcceptanceItem(
        "Settings",
        "Profile changes use hysteresis.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/market_status.py", "backend/tests/test_wca_step7_market_status.py"),
    ),
    WcaAcceptanceItem(
        "Risk and execution",
        "Local and global gates are separate.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/local_gates.py", "backend/app/risk/global_gate_engine.py"),
    ),
    WcaAcceptanceItem(
        "Risk and execution",
        "Account risk is aggregated across algorithms.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/risk/account_risk_ledger.py", "backend/tests/test_global_gate_engine.py"),
    ),
    WcaAcceptanceItem(
        "Risk and execution",
        "New entries and risk-reducing exits use separate permissions.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/risk/global_gate_engine.py", "backend/tests/test_wca_step12_global_gate_engine.py"),
    ),
    WcaAcceptanceItem(
        "Risk and execution",
        "Protective stops cannot be overridden or delayed by forecasts.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/exits.py", "backend/tests/test_wca_step13_ml_forecast_decoupling.py"),
    ),
    WcaAcceptanceItem(
        "Risk and execution",
        "Final order validation occurs after every override.",
        WcaAcceptanceStatus.PENDING,
        ("backend/app/execution/order_validator.py",),
        ("Order validation exists, but a complete WCA override-to-final-validation execution path has not been accepted yet.",),
    ),
    WcaAcceptanceItem(
        "Risk and execution",
        "Duplicate broker orders are prevented atomically.",
        WcaAcceptanceStatus.PENDING,
        ("backend/app/execution/idempotency.py", "backend/app/risk/global_gate_engine.py"),
        ("Idempotency contracts exist; atomic broker-submission proof for WCA paper execution is still pending.",),
    ),
    WcaAcceptanceItem(
        "Risk and execution",
        "Broker positions and orders are reconciled.",
        WcaAcceptanceStatus.PENDING,
        ("backend/app/execution/reconciliation.py",),
        ("Shared reconciliation scaffolding exists; accepted WCA broker reconciliation flow is still pending.",),
    ),
    WcaAcceptanceItem(
        "Backtesting",
        "The backtest is backend-authoritative.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/backtest/engine.py", "backend/tests/test_wca_step14_15_backend_backtest.py"),
    ),
    WcaAcceptanceItem(
        "Backtesting",
        "There is no same-candle signal/fill bias.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/backtest/engine.py", "backend/tests/test_wca_step19_comprehensive.py"),
    ),
    WcaAcceptanceItem(
        "Backtesting",
        "Early-session strategies receive proper warm-up data.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/backtest/engine.py", "backend/tests/test_wca_step14_15_backend_backtest.py"),
    ),
    WcaAcceptanceItem(
        "Backtesting",
        "Costs and open-position drawdown are included.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/backtest/metrics.py", "backend/tests/test_wca_step16_diagnostics.py"),
    ),
    WcaAcceptanceItem(
        "Backtesting",
        "Full-history, walk-forward, and holdout results exist.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/backtest/walk_forward.py", "backend/tests/test_wca_step14_15_backend_backtest.py"),
    ),
    WcaAcceptanceItem(
        "Backtesting",
        "Dynamic settings use the same resolver as paper trading.",
        WcaAcceptanceStatus.PENDING,
        ("backend/app/algorithms/wca/dynamic_profile.py", "backend/app/algorithms/wca/backtest/engine.py"),
        ("The resolver is shared by backend components, but WCA paper execution parity is not accepted yet.",),
    ),
    WcaAcceptanceItem(
        "Backtesting",
        "Smoke-test results are not used as profitability proof.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/backtest/reports.py", "backend/tests/test_wca_step14_15_backend_backtest.py"),
    ),
    WcaAcceptanceItem(
        "ML isolation",
        "ML may read WCA outputs.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/feature_snapshot.py", "backend/tests/test_wca_step13_ml_forecast_decoupling.py"),
    ),
    WcaAcceptanceItem(
        "ML isolation",
        "ML cannot write into WCA.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/feature_snapshot.py", "backend/tests/test_wca_step13_ml_forecast_decoupling.py"),
    ),
    WcaAcceptanceItem(
        "ML isolation",
        "ML cannot block WCA entries.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/local_gates.py", "backend/tests/test_wca_step13_ml_forecast_decoupling.py"),
    ),
    WcaAcceptanceItem(
        "ML isolation",
        "ML cannot delay WCA exits.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/exits.py", "backend/tests/test_wca_step13_ml_forecast_decoupling.py"),
    ),
    WcaAcceptanceItem(
        "ML isolation",
        "ML failure cannot stop WCA evaluation or backtesting.",
        WcaAcceptanceStatus.PASS,
        ("backend/tests/test_wca_step13_ml_forecast_decoupling.py", "backend/tests/test_wca_step14_15_backend_backtest.py"),
    ),
    WcaAcceptanceItem(
        "Deployment",
        "Shadow comparison completed.",
        WcaAcceptanceStatus.PENDING,
        ("backend/app/algorithms/wca/rollout.py",),
        ("Shadow comparison support exists; completed validation evidence has not been recorded.",),
    ),
    WcaAcceptanceItem(
        "Deployment",
        "Critical tests pass.",
        WcaAcceptanceStatus.PASS,
        ("scripts/ci_quality_gates.py", "backend/tests/test_wca_step19_comprehensive.py", "backend/tests/test_wca_step21_final_acceptance.py"),
    ),
    WcaAcceptanceItem(
        "Deployment",
        "Paper trading is stable.",
        WcaAcceptanceStatus.PENDING,
        ("backend/app/algorithms/wca/rollout.py",),
        ("No accepted multi-condition paper-trading stability run has been recorded.",),
    ),
    WcaAcceptanceItem(
        "Deployment",
        "Rollback is tested.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/algorithms/wca/rollout.py", "backend/tests/test_wca_step20_rollout.py"),
    ),
    WcaAcceptanceItem(
        "Deployment",
        "Real-money execution remains disabled unless explicitly enabled through a separate controlled process.",
        WcaAcceptanceStatus.PASS,
        ("backend/app/config.py", "backend/app/algorithms/wca/rollout.py"),
    ),
)


__all__ = [
    "WCA_FINAL_ACCEPTANCE_ITEMS",
    "WCA_FINAL_ACCEPTANCE_VERSION",
    "WcaAcceptanceItem",
    "WcaAcceptanceStatus",
    "build_wca_final_acceptance_report",
    "wca_acceptance_is_complete",
]
