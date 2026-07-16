from __future__ import annotations

from backend.app.domain.models import ContextSignal, Direction, MetaModelPrediction, OperatingMode, RegimeState, Signal, TradeCandidate
from backend.app.trading_policy.models import DynamicTradingPolicyConfig


def regime_risk_multiplier(regime: RegimeState, config: DynamicTradingPolicyConfig) -> tuple[float, list[str], list[str]]:
    reasons: list[str] = []
    explanations: list[str] = []
    label = regime.label.lower()
    multiplier = 1.0
    if regime.volatility == "EXTREME":
        multiplier *= config.extremeVolatilityRiskMultiplier
        reasons.append("policy.regime.extreme_volatility")
        explanations.append("Extreme volatility applies the configured risk floor.")
    elif regime.volatility == "HIGH":
        multiplier *= config.highVolatilityRiskMultiplier
        reasons.append("policy.regime.high_volatility")
        explanations.append("High volatility reduces risk.")
    if "strong_trend" in label or "strong trend" in label:
        multiplier *= config.strongRegimeRiskMultiplier
        reasons.append("policy.regime.strong_trend")
        explanations.append("Strong trend regime uses the strong-regime multiplier.")
    elif "unknown" in label or regime.confidence < 0.5:
        multiplier *= config.weakRegimeRiskMultiplier
        reasons.append("policy.regime.weak_or_unknown")
        explanations.append("Weak or unknown regime reduces risk.")
    return multiplier, reasons, explanations


def context_risk_multiplier(
    candidate: TradeCandidate,
    context_signals: list[ContextSignal],
    config: DynamicTradingPolicyConfig,
) -> tuple[float, list[str], list[str]]:
    conflicts = [
        context
        for context in context_signals
        if context.dataReady
        and context.signal != Signal.HOLD.value
        and int(context.direction) not in {0, int(candidate.direction)}
    ]
    if not conflicts:
        return 1.0, ["policy.context.no_hard_conflict"], ["Context modules do not materially conflict with the candidate."]
    return (
        config.contextConflictRiskMultiplier,
        ["policy.context.conflict_reduced_risk"],
        [f"{len(conflicts)} context signal(s) conflict with the candidate side, so risk is reduced."],
    )


def ml_risk_multiplier(
    prediction: MetaModelPrediction,
    config: DynamicTradingPolicyConfig,
) -> tuple[float, list[str], list[str]]:
    probability = prediction.probabilityCandidateSuccess
    if not config.useMetaModelRiskModifier or probability is None:
        return 1.0, ["policy.ml.no_risk_modifier"], ["ML risk modifier is unavailable or disabled."]
    if probability < config.minimumMetaProbabilityForRisk:
        return 0.0, ["policy.ml.probability_below_minimum"], ["ML probability is below the configured minimum for risk."]
    scaled = min(config.maximumMetaRiskMultiplier, max(0.0, probability))
    return scaled, ["policy.ml.bounded_risk_modifier"], ["ML contributes a bounded risk multiplier."]


def effective_dynamic_multiplier(
    *,
    candidate: TradeCandidate,
    regime: RegimeState,
    context_signals: list[ContextSignal],
    prediction: MetaModelPrediction,
    config: DynamicTradingPolicyConfig,
) -> tuple[float, list[str], list[str]]:
    regime_multiplier, regime_reasons, regime_explanations = regime_risk_multiplier(regime, config)
    context_multiplier, context_reasons, context_explanations = context_risk_multiplier(candidate, context_signals, config)
    ml_multiplier, ml_reasons, ml_explanations = ml_risk_multiplier(prediction, config)
    if str(config.mode) in {OperatingMode.OFF.value, OperatingMode.FALLBACK.value}:
        return 1.0, ["policy.dynamic.baseline_fallback"], ["Dynamic policy is disabled or in fallback; baseline settings are used."]
    multiplier = regime_multiplier * context_multiplier * ml_multiplier
    return (
        multiplier,
        regime_reasons + context_reasons + ml_reasons,
        regime_explanations + context_explanations + ml_explanations,
    )
