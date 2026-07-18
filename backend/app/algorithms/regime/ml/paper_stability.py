"""Versioned Regime paper-stability policy for ML promotion evidence."""

from __future__ import annotations

from dataclasses import dataclass


REGIME_ML_PAPER_STABILITY_POLICY_VERSION = "regime_ml_paper_stability_policy_v1"


@dataclass(frozen=True)
class RegimeMlPaperStabilityPolicy:
    minimum_paper_trading_days: int = 10
    minimum_shadow_decisions: int = 250
    minimum_eligible_trade_opportunities: int = 25
    minimum_completed_paper_trades: int = 5
    minimum_distinct_regimes: int = 5
    require_trend_condition: bool = True
    require_range_condition: bool = True
    require_volatility_condition: bool = True
    require_event_risk_condition: bool = True
    require_liquidity_condition: bool = True
    maximum_classification_instability_rate: float = 0.08
    maximum_calibration_error: float = 0.08
    maximum_prediction_drift: float = 0.10
    maximum_decision_disagreement_rate: float = 0.15
    maximum_global_risk_rejections_without_reason: int = 0
    maximum_broker_reconciliation_failures: int = 0
    maximum_missing_data_failures: int = 0
    maximum_stale_data_failures: int = 0
    maximum_system_errors: int = 0
    restart_recovery_required: bool = True


@dataclass(frozen=True)
class RegimeMlPaperStabilityEvidence:
    paper_trading_day_count: int
    paper_shadow_decision_count: int
    eligible_trade_opportunity_count: int
    completed_paper_trade_count: int
    distinct_regimes_observed: int
    trend_condition_observed: bool
    range_condition_observed: bool
    volatility_condition_observed: bool
    event_risk_condition_observed: bool
    liquidity_condition_observed: bool
    classification_instability_rate: float
    calibration_error: float
    prediction_drift: float
    decision_disagreement_rate: float
    global_risk_rejections_without_reason: int
    broker_reconciliation_failures: int
    missing_data_failures: int
    stale_data_failures: int
    system_errors: int
    restart_recovery_passed: bool


def evaluate_regime_ml_paper_stability(
    evidence: RegimeMlPaperStabilityEvidence,
    policy: RegimeMlPaperStabilityPolicy = RegimeMlPaperStabilityPolicy(),
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if evidence.paper_trading_day_count < policy.minimum_paper_trading_days:
        reasons.append("regime.ml.paper_stability.insufficient_paper_days")
    if evidence.paper_shadow_decision_count < policy.minimum_shadow_decisions:
        reasons.append("regime.ml.paper_stability.insufficient_shadow_decisions")
    if evidence.eligible_trade_opportunity_count < policy.minimum_eligible_trade_opportunities:
        reasons.append("regime.ml.paper_stability.insufficient_trade_opportunities")
    if evidence.completed_paper_trade_count < policy.minimum_completed_paper_trades:
        reasons.append("regime.ml.paper_stability.insufficient_completed_trades")
    if evidence.distinct_regimes_observed < policy.minimum_distinct_regimes:
        reasons.append("regime.ml.paper_stability.insufficient_distinct_regimes")
    if policy.require_trend_condition and not evidence.trend_condition_observed:
        reasons.append("regime.ml.paper_stability.trend_condition_missing")
    if policy.require_range_condition and not evidence.range_condition_observed:
        reasons.append("regime.ml.paper_stability.range_condition_missing")
    if policy.require_volatility_condition and not evidence.volatility_condition_observed:
        reasons.append("regime.ml.paper_stability.volatility_condition_missing")
    if policy.require_event_risk_condition and not evidence.event_risk_condition_observed:
        reasons.append("regime.ml.paper_stability.event_risk_condition_missing")
    if policy.require_liquidity_condition and not evidence.liquidity_condition_observed:
        reasons.append("regime.ml.paper_stability.liquidity_condition_missing")
    if evidence.classification_instability_rate > policy.maximum_classification_instability_rate:
        reasons.append("regime.ml.paper_stability.classification_unstable")
    if evidence.calibration_error > policy.maximum_calibration_error:
        reasons.append("regime.ml.paper_stability.calibration_unstable")
    if evidence.prediction_drift > policy.maximum_prediction_drift:
        reasons.append("regime.ml.paper_stability.prediction_drift")
    if evidence.decision_disagreement_rate > policy.maximum_decision_disagreement_rate:
        reasons.append("regime.ml.paper_stability.decision_disagreement")
    if evidence.global_risk_rejections_without_reason > policy.maximum_global_risk_rejections_without_reason:
        reasons.append("regime.ml.paper_stability.global_risk_rejection_behavior")
    if evidence.broker_reconciliation_failures > policy.maximum_broker_reconciliation_failures:
        reasons.append("regime.ml.paper_stability.broker_reconciliation_failure")
    if evidence.missing_data_failures > policy.maximum_missing_data_failures:
        reasons.append("regime.ml.paper_stability.missing_data_failure")
    if evidence.stale_data_failures > policy.maximum_stale_data_failures:
        reasons.append("regime.ml.paper_stability.stale_data_failure")
    if evidence.system_errors > policy.maximum_system_errors:
        reasons.append("regime.ml.paper_stability.system_error")
    if policy.restart_recovery_required and not evidence.restart_recovery_passed:
        reasons.append("regime.ml.paper_stability.restart_recovery_missing")
    return not reasons, tuple(reasons)

