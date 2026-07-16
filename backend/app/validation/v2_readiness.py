from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from pydantic import Field

from backend.app.domain.models import DomainModel, StrategyRole
from backend.app.strategies.registry import DIRECTIONAL_STRATEGIES


V2_READINESS_VERSION = "voting_ensemble_v2_completion_readiness_v1"
ROOT = Path(__file__).resolve().parents[3]


class V2CompletionConditionResult(DomainModel):
    conditionId: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    passed: bool
    evidence: list[str] = Field(default_factory=list)
    missingEvidence: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)


class V2CompletionReadinessReport(DomainModel):
    readinessVersion: str = V2_READINESS_VERSION
    complete: bool
    passedCount: int = Field(ge=0)
    failedCount: int = Field(ge=0)
    conditions: list[V2CompletionConditionResult]
    explanation: str = Field(min_length=1)


@dataclass(frozen=True)
class ConditionSpec:
    condition_id: str
    statement: str
    paths: tuple[str, ...]
    terms: tuple[str, ...] = ()
    checker: Callable[[], tuple[bool, list[str], list[str]]] | None = None


def build_v2_completion_readiness_report(root: Path | None = None) -> V2CompletionReadinessReport:
    base = root or ROOT
    results = [_evaluate_condition(spec, base) for spec in COMPLETION_CONDITIONS]
    failed = [result for result in results if not result.passed]
    return V2CompletionReadinessReport(
        complete=not failed,
        passedCount=len(results) - len(failed),
        failedCount=len(failed),
        conditions=results,
        explanation=(
            "Voting Ensemble V2 completion readiness maps the user-defined definition of done "
            "to executable implementation and regression-test evidence."
        ),
    )


def _evaluate_condition(spec: ConditionSpec, root: Path) -> V2CompletionConditionResult:
    if spec.checker is not None:
        passed, evidence, missing = spec.checker()
    else:
        evidence, missing = _evidence_terms(root, spec.paths, spec.terms)
        passed = not missing
    return V2CompletionConditionResult(
        conditionId=spec.condition_id,
        statement=spec.statement,
        passed=passed,
        evidence=evidence,
        missingEvidence=missing,
        explanation="Condition passed." if passed else "Condition is missing required implementation or test evidence.",
    )


def _evidence_terms(root: Path, paths: tuple[str, ...], terms: tuple[str, ...]) -> tuple[list[str], list[str]]:
    text_by_path = {path: _read(root / path) for path in paths}
    combined = "\n".join(text_by_path.values())
    evidence = [path for path, text in text_by_path.items() if text]
    missing = [term for term in terms if term not in combined]
    missing.extend(path for path, text in text_by_path.items() if not text)
    return evidence, missing


def _read(path: Path) -> str:
    if path.is_dir():
        return "\n".join(child.read_text(encoding="utf-8", errors="ignore") for child in sorted(path.rglob("*.py")))
    if path.exists():
        return path.read_text(encoding="utf-8", errors="ignore")
    return ""


def _directional_strategy_catalog_check() -> tuple[bool, list[str], list[str]]:
    expected_names = {
        "Multi-Timeframe Trend Alignment",
        "First Pullback After Open",
        "VWAP Trend Continuation",
        "Opening Range Breakout",
        "Volatility Breakout",
        "Failed Breakout Reversal",
        "Liquidity Sweep Reversal",
        "VWAP Mean Reversion",
        "Bollinger/ATR Reversion",
        "Gap Continuation / Gap Fade",
    }
    actual_names = {entry.strategyName for entry in DIRECTIONAL_STRATEGIES}
    directional_roles = all(entry.role == StrategyRole.DIRECTIONAL.value for entry in DIRECTIONAL_STRATEGIES)
    missing = sorted(expected_names - actual_names)
    extras = sorted(actual_names - expected_names)
    passed = len(DIRECTIONAL_STRATEGIES) == 10 and not missing and not extras and directional_roles
    evidence = [f"directional:{entry.strategyId}:{entry.strategyName}" for entry in DIRECTIONAL_STRATEGIES]
    return passed, evidence, [*missing, *extras] + ([] if directional_roles else ["non_directional_role_present"])


def _no_direction_proxy_fallback_check() -> tuple[bool, list[str], list[str]]:
    directional_root = ROOT / "backend" / "app" / "strategies" / "directional"
    forbidden = (
        "session.directionBias",
        "event.directionBias",
        "sessionDirection",
        "eventDirection",
        "directionBias",
    )
    evidence = [str(path.relative_to(ROOT)) for path in sorted(directional_root.glob("*.py"))]
    missing = []
    for path in sorted(directional_root.glob("*.py")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for term in forbidden:
            if term in text:
                missing.append(f"{path.name}:{term}")
    return not missing, evidence, missing


COMPLETION_CONDITIONS: tuple[ConditionSpec, ...] = (
    ConditionSpec("directional_10", "Ten independent directional strategies are implemented.", ("backend/app/strategies/registry.py",), checker=_directional_strategy_catalog_check),
    ConditionSpec("no_direction_proxy", "No directional strategy uses session/event direction as a fallback proxy.", ("backend/app/strategies/directional",), checker=_no_direction_proxy_fallback_check),
    ConditionSpec("relative_strength_actual_aux", "Relative strength uses actual aligned QQQ/IWM measurements.", ("backend/app/strategies/context/relative_strength_qqq_iwm.py", "backend/tests/test_relative_strength_qqq_iwm_context.py"), ("qqq", "iwm", "relative_return")),
    ConditionSpec("breadth_feed_or_proxy", "Breadth uses an actual feed or clearly labeled, sufficiently populated proxy.", ("backend/app/strategies/context/market_breadth_momentum.py", "backend/tests/test_market_breadth_momentum_context.py"), ("breadth_proxy", "minComponentCoverage", "dataReady=False")),
    ConditionSpec("context_not_directional_votes", "Context modules do not cast full directional votes.", ("backend/app/ensemble/family_aware.py", "backend/tests/test_family_aware_ensemble.py"), ("context_adjustments", "context_cannot_cast_full_votes")),
    ConditionSpec("regime_fit_not_direction", "Regime modules modify fit rather than duplicate direction.", ("backend/app/ensemble/family_aware.py", "backend/app/strategies/regime/adx_atr_regime.py"), ("REGIME_FIT_KEYS", "trendFit", "meanReversionFit")),
    ConditionSpec("cash_hard_safety", "Cash/Avoid Trading is a hard entry safety module.", ("backend/app/strategies/safety/cash_avoid_trading.py", "backend/tests/test_cash_avoid_trading_safety.py"), ("manualCashMode", "protective_exit", "blocks")),
    ConditionSpec("no_self_vote", "Ensemble Strategy Voting is not one of its own inputs.", ("backend/app/ensemble/family_aware.py", "backend/tests/test_family_aware_ensemble.py"), ("aggregator cannot vote for itself", "aggregator_cannot_vote_for_itself")),
    ConditionSpec("family_average", "Strategies are averaged within independent families.", ("backend/app/ensemble/family_aware.py", "backend/tests/test_family_aware_ensemble.py"), ("weighted mean", "averaged_not_counted", "duplicating_strategy")),
    ConditionSpec("deterministic_baseline", "The deterministic family-aware ensemble is the permanent baseline.", ("backend/app/backtesting/deterministic_activation.py", "backend/app/backtesting/dynamic_policy_activation.py", "backend/app/backtesting/ml_risk_modifier_experiment.py"), ("DETERMINISTIC_V2_BASELINE_VERSION", "deterministicPolicyFallback")),
    ConditionSpec("v1_v2_not_mixed", "V1 and V2 snapshots cannot be mixed.", ("backend/app/domain/snapshot_store.py", "backend/tests/test_decision_snapshot_v2_archive.py"), ("incompatible", "V1", "V2")),
    ConditionSpec("point_in_time_features", "Decision features are point-in-time and reproducible.", ("backend/app/domain/feature_engine.py", "backend/tests/test_point_in_time_feature_engine.py", "docs/point-in-time-feature-engine.md"), ("_completed_candles", "future", "replay")),
    ConditionSpec("candidate_success_ml", "Meta-Model V2 predicts candidate success rather than replacing direction.", ("backend/app/ml/meta_labeling.py", "backend/app/ml/inference.py", "backend/tests/test_safe_ml_inference_modes.py"), ("candidate_success_probability", "cannot_create_trade_from_hold", "test_ml_cannot_flip_side")),
    ConditionSpec("oos_upstream_predictions", "Upstream ML predictions are out of sample.", ("backend/app/ml/forecast_oos.py", "backend/tests/test_forecast_oos_features.py"), ("trainingWindowEnd", "cannot_predict_rows_from_its_own_fitting_period")),
    ConditionSpec("purged_walk_forward", "Training uses purged chronological walk-forward validation.", ("backend/app/meta_strategy_training.py", "backend/tests/test_meta_strategy_nested_training.py"), ("purged", "embargo", "chronological")),
    ConditionSpec("oof_calibration", "Probability calibration uses out-of-fold predictions.", ("backend/app/meta_strategy_training.py", "backend/tests/test_meta_probability_calibration.py"), ("out_of_fold", "rejects_in_sample")),
    ConditionSpec("economic_promotion", "Model promotion uses economic and risk metrics, not accuracy alone.", ("backend/app/meta_strategy_training.py", "backend/tests/test_meta_strategy_economic_promotion.py"), ("netExpectancyAfterCosts", "drawdown", "accuracy")),
    ConditionSpec("shared_replay_code", "Backtesting uses the same strategy, gate, and policy code as paper trading.", ("backend/app/backtesting/event_replay.py", "backend/tests/test_event_driven_replay_engine.py"), ("live-style", "GlobalGateEngine", "DynamicPolicyEngine")),
    ConditionSpec("realistic_execution", "Entry/fill/exit simulation includes realistic spread, slippage, latency, and order behavior.", ("backend/app/execution/simulation.py", "backend/tests/test_execution_simulation.py"), ("bidAskSpreadDollars", "latencySeconds", "same_bar_target_stop_ambiguity")),
    ConditionSpec("baseline_fallback", "Baseline settings remain the starting point and fallback.", ("backend/app/trading_policy/engine.py", "backend/app/backtesting/ml_risk_modifier_experiment.py"), ("baseline_settings", "deterministic_fallback_used")),
    ConditionSpec("dynamic_hard_limits", "Dynamic settings are bounded by absolute hard limits.", ("backend/app/trading_policy/engine.py", "backend/tests/test_dynamic_trading_policy_engine.py"), ("hard_limits", "hard_limits_cannot_be_overridden")),
    ConditionSpec("size_multiplier_fixed", "The sizeMultiplier defect is fixed.", ("backend/app/domain/trading_settings.py", "backend/tests/test_trading_settings_schema.py"), ("old_frontend_multiplier_clamp_is_not_present", "0.25")),
    ConditionSpec("settings_hash_complete", "Every effective setting participates in configuration hashing.", ("backend/app/domain/trading_settings.py", "backend/tests/test_trading_settings_schema.py"), ("trading_settings_configuration_hash", "strategy_configuration_hash", "gate_configuration_hash")),
    ConditionSpec("quantity_min_caps", "Quantity is the minimum of all risk, capital, buying-power, liquidity, exposure, and share caps.", ("backend/app/trading_policy/position_sizing.py", "backend/tests/test_dynamic_trading_policy_engine.py"), ("riskBasedShares", "buyingPowerShares", "liquidityParticipationShares", "globalExposureShares")),
    ConditionSpec("entry_exit_policies", "Strategy-specific entry and exit policies are implemented.", ("backend/app/trading_policy/entry_policy.py", "backend/app/trading_policy/exit_policy.py", "backend/tests/test_dynamic_trading_policy_engine.py"), ("strategyFamily", "bracketOco", "time_stop")),
    ConditionSpec("no_stop_widening", "Stops can never be widened after entry.", ("backend/app/trading_policy/exit_policy.py", "backend/tests/test_dynamic_trading_policy_engine.py"), ("exit.stop_widening_rejected", "test_stop_widening_is_impossible")),
    ConditionSpec("pyramiding_disabled", "Pyramiding remains disabled until separately validated.", ("backend/app/trading_policy/exit_policy.py", "backend/app/backtesting/dynamic_policy_activation.py"), ("pyramidingEnabled", "pyramiding_disabled")),
    ConditionSpec("one_global_gate", "One Global Gate Engine is used by every algorithm.", ("backend/app/gates/engine.py", "backend/app/backtesting/event_replay.py", "backend/app/execution/broker_reconciliation.py"), ("GlobalGateEngine", "to_global_gate_decision")),
    ConditionSpec("global_risk_all_algorithms", "Global risk includes positions and orders from every algorithm.", ("backend/app/gates/account_risk.py", "backend/tests/test_global_account_risk_state.py"), ("algorithmId", "pendingOrders", "two_algorithms")),
    ConditionSpec("daily_loss_conservative", "Daily loss includes realized, unrealized, and conservative exit costs.", ("backend/app/gates/account_risk.py", "backend/tests/test_global_account_risk_state.py"), ("dailyNetPnlAfterExitCosts", "estimatedExitCosts")),
    ConditionSpec("critical_entry_gates", "Entry cutoff, halt, LULD, data freshness, broker health, and duplicate-order protections are actually enforced.", ("backend/app/gates/engine.py", "backend/tests/test_global_gate_engine.py", "backend/tests/test_phase12_comprehensive.py"), ("entryWindowOpen", "symbolHalt", "luldPause", "freshQuote", "duplicateOrder")),
    ConditionSpec("protective_exits_allowed", "Protective exits remain available when new entries are blocked.", ("backend/app/gates/engine.py", "backend/tests/test_phase12_comprehensive.py"), ("protective_exit", "cautions")),
    ConditionSpec("event_not_direction", "Event context cannot replace ensemble direction.", ("backend/app/strategies/context/economic_event_context.py", "backend/app/gates/engine.py", "backend/tests/test_remaining_context_modules.py"), ("candidate_side_not_replaced", "event context cannot")),
    ConditionSpec("ui_gate_display", "The UI never claims an unevaluated gate passed.", ("frontend/src/components/V2DecisionPanel.ts", "frontend/tests/V2DecisionPanel.test.ts"), ("Not evaluated", "distinguishes hard blockers")),
    ConditionSpec("broker_reconcile_before_submit", "Broker positions and orders are reconciled before submission.", ("backend/app/execution/broker_reconciliation.py", "backend/tests/test_broker_reconciliation.py"), ("refresh_positions", "refresh_open_orders", "refresh_account_snapshot")),
    ConditionSpec("idempotent_submission", "Order submission is idempotent.", ("backend/app/execution/broker_reconciliation.py", "backend/tests/test_broker_reconciliation.py"), ("deterministic_client_order_id", "idempotent_duplicate_request")),
    ConditionSpec("hard_risk_no_lookahead_tests", "All hard-risk and no-lookahead invariants have automated tests.", ("backend/tests/test_phase12_comprehensive.py", "backend/tests/test_point_in_time_feature_engine.py"), ("hard_risk_cap_is_never_exceeded", "no_lookahead")),
    ConditionSpec("incremental_activation", "V2 is activated incrementally through shadow and paper-trading stages.", ("backend/app/backtesting/paper_shadow.py", "backend/app/backtesting/deterministic_activation.py", "backend/app/backtesting/ml_filter_rollout.py", "backend/app/backtesting/dynamic_policy_activation.py"), ("SHADOW", "FILTER_ACTIVE", "activation")),
    ConditionSpec("stage_rollback", "Every stage has a rollback flag.", ("backend/app/backtesting/deterministic_activation.py", "backend/app/backtesting/dynamic_policy_activation.py"), ("rollbackMode", "disableStopAndQuantity", "disableAllDynamicPolicy")),
    ConditionSpec("no_live_trading", "No live-trading capability is enabled.", ("backend/app/gates/engine.py", "docs/ensemble-v1-baseline.md", "backend/tests/test_global_gate_engine.py"), ("paperTradingMode", "live_trading_not_allowed", "no live trading")),
)
