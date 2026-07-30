"""Final acceptance ledger for WCA modernization.

This module is deliberately conservative: it records completion evidence, but
does not infer that the modernization is complete while any required checklist
item remains pending.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum

from backend.app.algorithms.wca.contracts import WCA_ALGORITHM_ID
from backend.app.algorithms.wca.rollout import WCA_REQUIRED_ROLLOUT_EVIDENCE, WcaRolloutEvidence


WCA_FINAL_ACCEPTANCE_VERSION = "wca_final_acceptance_checklist_v2"

WCA_FINAL_ACCEPTANCE_REQUIRED_TESTS = frozenset(
    {
        "backend/tests/test_wca_step16_safety_critical_ci.py",
        "backend/tests/test_wca_step20_rollout.py",
        "backend/tests/test_wca_step21_final_acceptance.py",
    }
)

WCA_EVIDENCE_DERIVED_ACCEPTANCE_STATEMENTS = frozenset(
    {
        "Live, paper, and backtest use the same engine.",
        "Final order validation occurs after every override.",
        "Duplicate broker orders are prevented atomically.",
        "Broker positions and orders are reconciled.",
        "Dynamic settings use the same resolver as paper trading.",
        "Shadow comparison completed.",
        "Critical tests pass.",
        "Paper trading is stable.",
        "Rollback is tested.",
        "Latency performance is accepted.",
        "Multi-condition paper evidence is accepted.",
        "Real-money execution remains disabled unless explicitly enabled through a separate controlled process.",
    }
)


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

    def with_runtime_result(
        self,
        status: WcaAcceptanceStatus,
        *,
        evidence: tuple[str, ...],
        limitations: tuple[str, ...] = (),
    ) -> "WcaAcceptanceItem":
        return replace(self, status=status, evidence=(*self.evidence, *evidence), limitations=limitations)

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
class WcaFinalAcceptanceEvidence:
    passing_test_files: frozenset[str] = frozenset()
    migration_passed: bool = False
    architecture_boundary_passed: bool = False
    registry_parity_passed: bool = False
    acceptance_evidence_present: bool = False
    final_validation_after_override_passed: bool = False
    paper_replay_backtest_parity_passed: bool = False
    dynamic_settings_parity_passed: bool = False
    rollout_evidence: WcaRolloutEvidence = field(default_factory=WcaRolloutEvidence)


def build_wca_final_acceptance_report(evidence: WcaFinalAcceptanceEvidence | None = None) -> dict[str, object]:
    runtime_evidence = evidence or WcaFinalAcceptanceEvidence()
    items = derive_wca_final_acceptance_items(runtime_evidence)
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
        "evidenceDerivedStatements": sorted(WCA_EVIDENCE_DERIVED_ACCEPTANCE_STATEMENTS),
    }


def derive_wca_final_acceptance_items(evidence: WcaFinalAcceptanceEvidence) -> tuple[WcaAcceptanceItem, ...]:
    return tuple(_apply_runtime_evidence(item, evidence) for item in WCA_FINAL_ACCEPTANCE_ITEMS)


def wca_acceptance_is_complete(evidence: WcaFinalAcceptanceEvidence | None = None) -> bool:
    return bool(build_wca_final_acceptance_report(evidence)["complete"])


def _apply_runtime_evidence(item: WcaAcceptanceItem, evidence: WcaFinalAcceptanceEvidence) -> WcaAcceptanceItem:
    statement = item.statement
    rollout = evidence.rollout_evidence
    if statement == "Live, paper, and backtest use the same engine.":
        return _pass_or_pending(
            item,
            evidence.paper_replay_backtest_parity_passed and "deterministic_replay_parity" in rollout.persisted_evidence_ids,
            ("wca.final_acceptance.paper_replay_backtest_parity",),
            ("Requires passing paper/replay/backtest parity test results and persisted deterministic replay parity evidence.",),
        )
    if statement == "Final order validation occurs after every override.":
        return _pass_or_pending(
            item,
            evidence.final_validation_after_override_passed,
            ("wca.final_acceptance.final_validation_after_override",),
            ("Requires executed evidence for final validation after each WCA override.",),
        )
    if statement == "Duplicate broker orders are prevented atomically.":
        return _pass_or_pending(
            item,
            "zero_duplicate_broker_orders" in rollout.persisted_evidence_ids and rollout.duplicate_broker_orders == 0,
            ("wca.rollout.evidence.zero_duplicate_broker_orders",),
            ("Requires persisted duplicate-submission evidence with zero duplicate broker orders.",),
        )
    if statement == "Broker positions and orders are reconciled.":
        return _pass_or_pending(
            item,
            "accepted_reconciliation" in rollout.persisted_evidence_ids and rollout.reconciliation_passed,
            ("wca.rollout.evidence.accepted_reconciliation",),
            ("Requires persisted broker reconciliation evidence.",),
        )
    if statement == "Dynamic settings use the same resolver as paper trading.":
        return _pass_or_pending(
            item,
            evidence.dynamic_settings_parity_passed,
            ("wca.final_acceptance.dynamic_settings_parity",),
            ("Requires executed evidence that backtest and paper paths use the same dynamic settings resolver.",),
        )
    if statement == "Shadow comparison completed.":
        return _pass_or_pending(
            item,
            "zero_unexplained_decision_mismatches" in rollout.persisted_evidence_ids
            and rollout.unexplained_decision_mismatches == 0,
            ("wca.rollout.evidence.zero_unexplained_decision_mismatches",),
            ("Requires persisted shadow comparison evidence with no unexplained decision mismatches.",),
        )
    if statement == "Critical tests pass.":
        passed = (
            WCA_FINAL_ACCEPTANCE_REQUIRED_TESTS.issubset(evidence.passing_test_files)
            and evidence.migration_passed
            and evidence.architecture_boundary_passed
            and evidence.registry_parity_passed
            and evidence.acceptance_evidence_present
        )
        return _pass_or_pending(
            item,
            passed,
            tuple(sorted(WCA_FINAL_ACCEPTANCE_REQUIRED_TESTS)),
            ("Requires passing safety-critical tests, migration gate, registry parity, boundary scan, and acceptance evidence checks.",),
        )
    if statement == "Paper trading is stable.":
        return _pass_or_pending(
            item,
            _paper_stability_accepted(rollout),
            ("wca.rollout.evidence.paper_stability",),
            ("Requires persisted multi-condition paper evidence, latency, slippage, restart, reconciliation, and rollback proof.",),
        )
    if statement == "Rollback is tested.":
        return _pass_or_pending(
            item,
            "tested_rollback" in rollout.persisted_evidence_ids
            and rollout.rollback_tested
            and rollout.rollback_restored_safe_state,
            ("wca.rollout.evidence.tested_rollback",),
            ("Requires persisted rollback evidence and safe-state restoration proof.",),
        )
    if statement == "Latency performance is accepted.":
        return _pass_or_pending(
            item,
            _latency_accepted(rollout),
            ("wca.rollout.evidence.latency",),
            ("Requires persisted acceptable event, decision, and broker latency evidence.",),
        )
    if statement == "Multi-condition paper evidence is accepted.":
        return _pass_or_pending(
            item,
            _multi_condition_evidence_accepted(rollout),
            ("wca.rollout.evidence.multiple_market_conditions",),
            ("Requires persisted paper evidence across market conditions, session periods, volatility, and economic-event sessions.",),
        )
    if statement == "Real-money execution remains disabled unless explicitly enabled through a separate controlled process.":
        if rollout.live_trading_enabled:
            return item.with_runtime_result(
                WcaAcceptanceStatus.FAIL,
                evidence=("wca.rollout.live_trading_enabled",),
                limitations=("WCA rollout evidence indicates live trading is enabled, which is outside the paper-only rollout.",),
            )
        return item.with_runtime_result(
            WcaAcceptanceStatus.PASS,
            evidence=("wca.rollout.paper_only.real_money_disabled",),
            limitations=(),
        )
    return item


def _pass_or_pending(
    item: WcaAcceptanceItem,
    accepted: bool,
    evidence: tuple[str, ...],
    limitations: tuple[str, ...],
) -> WcaAcceptanceItem:
    return item.with_runtime_result(
        WcaAcceptanceStatus.PASS if accepted else WcaAcceptanceStatus.PENDING,
        evidence=evidence,
        limitations=() if accepted else limitations,
    )


def _paper_stability_accepted(evidence: WcaRolloutEvidence) -> bool:
    return (
        WCA_REQUIRED_ROLLOUT_EVIDENCE.issubset(evidence.persisted_evidence_ids)
        and evidence.prior_steps_passed
        and evidence.deterministic_replay_parity
        and evidence.unexplained_decision_mismatches == 0
        and evidence.duplicate_broker_orders == 0
        and evidence.cross_algorithm_inventory_mutations == 0
        and evidence.restart_recovery_passed
        and evidence.reconciliation_passed
        and evidence.unprotected_positions == 0
        and _latency_accepted(evidence)
        and evidence.average_realised_slippage_per_share is not None
        and evidence.average_realised_slippage_per_share <= 0.05
        and _multi_condition_evidence_accepted(evidence)
        and evidence.paper_observation_days >= 10
        and evidence.paper_trade_count >= 10
        and evidence.rollback_tested
        and evidence.rollback_restored_safe_state
        and not evidence.critical_failure_open
        and not evidence.live_trading_enabled
    )


def _latency_accepted(evidence: WcaRolloutEvidence) -> bool:
    return (
        {"accepted_event_latency", "accepted_decision_latency", "accepted_broker_latency"}.issubset(
            evidence.persisted_evidence_ids
        )
        and evidence.max_event_lag_seconds is not None
        and evidence.max_event_lag_seconds <= 60
        and evidence.max_decision_latency_seconds is not None
        and evidence.max_decision_latency_seconds <= 2
        and evidence.max_broker_latency_seconds is not None
        and evidence.max_broker_latency_seconds <= 5
    )


def _multi_condition_evidence_accepted(evidence: WcaRolloutEvidence) -> bool:
    return (
        {"opening_session_evidence", "midday_evidence", "closing_session_evidence", "high_volatility_evidence", "economic_event_session_evidence"}.issubset(
            evidence.persisted_evidence_ids
        )
        and len(set(evidence.market_conditions)) >= 3
        and {"opening", "midday", "closing"}.issubset(set(evidence.session_periods))
        and evidence.high_volatility_sessions > 0
        and evidence.economic_event_sessions > 0
    )


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
        ("backend/app/algorithms/wca/modifiers", "backend/tests/test_wca_modifier_inventory.py"),
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
        ("scripts/ci_quality_gates.py", "backend/tests/test_wca_step16_safety_critical_ci.py", "backend/tests/test_wca_step19_comprehensive.py", "backend/tests/test_wca_step21_final_acceptance.py"),
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
        "Latency performance is accepted.",
        WcaAcceptanceStatus.PENDING,
        ("backend/app/algorithms/wca/rollout.py",),
        ("No accepted event, decision, and broker latency evidence has been recorded.",),
    ),
    WcaAcceptanceItem(
        "Deployment",
        "Multi-condition paper evidence is accepted.",
        WcaAcceptanceStatus.PENDING,
        ("backend/app/algorithms/wca/rollout.py",),
        ("No accepted opening, midday, closing, high-volatility, and economic-event paper evidence has been recorded.",),
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
    "WCA_EVIDENCE_DERIVED_ACCEPTANCE_STATEMENTS",
    "WCA_FINAL_ACCEPTANCE_REQUIRED_TESTS",
    "WCA_FINAL_ACCEPTANCE_VERSION",
    "WcaAcceptanceItem",
    "WcaFinalAcceptanceEvidence",
    "WcaAcceptanceStatus",
    "build_wca_final_acceptance_report",
    "derive_wca_final_acceptance_items",
    "wca_acceptance_is_complete",
]
