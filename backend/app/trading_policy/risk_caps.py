from __future__ import annotations

from datetime import UTC, datetime

from backend.app.domain.models import AccountRiskState, BaselineTradingSettings, ContextSignal, DynamicPolicyBounds, HardRiskLimits, MetaModelPrediction, RegimeState, Signal, TradeCandidate
from backend.app.trading_policy.models import DynamicRiskCap, DynamicTradingPolicyConfig


def hard_risk_cap_dollars(account: AccountRiskState, limits: HardRiskLimits) -> float:
    return account.equity * (limits.maximumRiskPerTradePercent / 100.0)


def daily_loss_remaining_dollars(account: AccountRiskState, limits: HardRiskLimits) -> float:
    loss_limit = account.equity * (limits.maximumDailyLossPercent / 100.0)
    daily_net = account.dailyNetPnlAfterExitCosts if account.dailyNetPnlAfterExitCosts is not None else account.realizedPnlToday
    daily_loss = abs(min(0.0, daily_net))
    return max(0.0, loss_limit - daily_loss)


def open_risk_cap_dollars(account: AccountRiskState, limits: HardRiskLimits) -> float:
    return account.equity * (limits.maximumOpenRiskPercent / 100.0)


def order_notional_cap_dollars(account: AccountRiskState, settings: BaselineTradingSettings, limits: HardRiskLimits) -> float:
    baseline = account.equity * (settings.baseOrderAllocationPercent / 100.0)
    hard_limit = account.equity * (limits.maximumOrderNotionalPercent / 100.0)
    return max(0.0, min(baseline, hard_limit, limits.maxOrderNotional))


def position_notional_cap_dollars(account: AccountRiskState, settings: BaselineTradingSettings, limits: HardRiskLimits) -> float:
    baseline = account.equity * (settings.basePositionPercent / 100.0)
    hard_limit = account.equity * (limits.maximumPositionPercent / 100.0)
    remaining_legacy = max(0.0, limits.maxPositionNotional - account.openPositionNotional)
    return max(0.0, min(baseline, hard_limit, remaining_legacy))


def daily_notional_cap_dollars(account: AccountRiskState, settings: BaselineTradingSettings, limits: HardRiskLimits) -> float:
    baseline = account.equity * (settings.baseDailyAllocationPercent / 100.0)
    hard_limit = account.equity * (limits.maximumDailyNotionalPercent / 100.0)
    return max(0.0, min(baseline, hard_limit))


def share_cap(limits: HardRiskLimits) -> int:
    return min(limits.maximumShares, limits.maxShareQuantity)


def dynamic_risk_caps(
    *,
    candidate: TradeCandidate,
    regime: RegimeState,
    context_signals: list[ContextSignal],
    prediction: MetaModelPrediction,
    account: AccountRiskState,
    baseline: BaselineTradingSettings,
    hard_limits: HardRiskLimits,
    bounds: DynamicPolicyBounds,
    config: DynamicTradingPolicyConfig,
    evaluated_at: datetime,
) -> list[DynamicRiskCap]:
    return [
        _signal_quality_cap(candidate),
        _family_agreement_cap(candidate, config),
        _regime_cap(regime, config),
        _volatility_cap(regime, config),
        _liquidity_cap(account),
        _event_cap(context_signals, config, evaluated_at),
        _time_of_day_cap(evaluated_at, hard_limits, config),
        _drawdown_cap(account, hard_limits),
        _ml_cap(prediction, config),
        _data_quality_cap(regime, context_signals, config),
        DynamicRiskCap(
            capName="dynamicBoundsCap",
            multiplier=min(1.0, bounds.maximumRiskMultiplier),
            reasonCodes=["policy.cap.dynamic_bounds"],
            explanation="Dynamic bounds cap maximum risk multiplier at or below the configured maximum.",
        ),
    ]


def effective_risk_multiplier_from_caps(caps: list[DynamicRiskCap]) -> tuple[float, DynamicRiskCap]:
    limiting = min(caps, key=lambda cap: (cap.multiplier, cap.capName))
    return limiting.multiplier, limiting


def _signal_quality_cap(candidate: TradeCandidate) -> DynamicRiskCap:
    multiplier = min(1.0, max(0.0, candidate.confidence))
    return DynamicRiskCap(
        capName="signalQualityCap",
        multiplier=multiplier,
        reasonCodes=["policy.cap.signal_quality"],
        explanation="Signal quality cap is bounded by the candidate confidence.",
    )


def _family_agreement_cap(candidate: TradeCandidate, config: DynamicTradingPolicyConfig) -> DynamicRiskCap:
    agreement = candidate.expectedValue
    multiplier = 1.0 if agreement is None or agreement >= 0 else config.weakFamilyAgreementCap
    return DynamicRiskCap(
        capName="familyAgreementCap",
        multiplier=multiplier,
        reasonCodes=["policy.cap.family_agreement"],
        explanation="Family agreement cap uses full risk unless candidate evidence exposes negative agreement.",
    )


def _regime_cap(regime: RegimeState, config: DynamicTradingPolicyConfig) -> DynamicRiskCap:
    label = regime.label.lower()
    if "unknown" in label or regime.confidence < 0.5:
        multiplier = min(1.0, config.weakRegimeRiskMultiplier)
        reason = "policy.cap.regime_weak_or_unknown"
        explanation = "Weak or unknown regime limits risk."
    else:
        multiplier = min(1.0, config.strongRegimeRiskMultiplier)
        reason = "policy.cap.regime_supported"
        explanation = "Regime cap permits baseline risk because regime confidence is sufficient."
    return DynamicRiskCap(capName="regimeCap", multiplier=multiplier, reasonCodes=[reason], explanation=explanation)


def _volatility_cap(regime: RegimeState, config: DynamicTradingPolicyConfig) -> DynamicRiskCap:
    if regime.volatility == "EXTREME":
        multiplier = min(1.0, config.extremeVolatilityRiskMultiplier)
        reason = "policy.cap.volatility_extreme"
        explanation = "Extreme volatility is a severe adverse cap."
    elif regime.volatility == "HIGH":
        multiplier = min(1.0, config.highVolatilityRiskMultiplier)
        reason = "policy.cap.volatility_high"
        explanation = "High volatility limits risk."
    else:
        multiplier = 1.0
        reason = "policy.cap.volatility_normal"
        explanation = "Volatility cap permits baseline risk."
    return DynamicRiskCap(capName="volatilityCap", multiplier=multiplier, reasonCodes=[reason], explanation=explanation)


def _liquidity_cap(account: AccountRiskState) -> DynamicRiskCap:
    multiplier = 1.0 if account.buyingPower > 0 else 0.0
    return DynamicRiskCap(
        capName="liquidityCap",
        multiplier=multiplier,
        reasonCodes=["policy.cap.liquidity"],
        explanation="Liquidity cap requires positive buying power.",
    )


def _event_cap(context_signals: list[ContextSignal], config: DynamicTradingPolicyConfig, evaluated_at: datetime) -> DynamicRiskCap:
    event_contexts = [
        context
        for context in context_signals
        if "event" in context.contextId.lower() and _event_context_has_controls(context, evaluated_at=evaluated_at)
    ]
    if not event_contexts:
        return DynamicRiskCap(
            capName="eventCap",
            multiplier=1.0,
            reasonCodes=["policy.cap.event_clear"],
            explanation="Event cap permits baseline risk because no adverse event context is present.",
        )
    explicit_caps = [_event_control_multiplier(context, config, evaluated_at=evaluated_at) for context in event_contexts]
    reason_codes = ["policy.cap.event_risk"]
    if any(_event_bool(context, "event_blackout", "eventBlackout") for context in event_contexts):
        reason_codes.append("policy.cap.event_blackout")
    if any(not _event_bool(context, "allow_new_entries", "allowNewEntries", default=True) for context in event_contexts):
        reason_codes.append("policy.cap.event_new_entries_disabled")
    if any(_event_value(context, "cooldown_until", "cooldownUntil") for context in event_contexts):
        reason_codes.append("policy.cap.event_cooldown")
    if any(float(_event_value(context, "minimum_edge_multiplier", "minimumEdgeMultiplier", default=1.0) or 1.0) > 1.0 for context in event_contexts):
        reason_codes.append("policy.cap.event_minimum_edge_increased")
    return DynamicRiskCap(
        capName="eventCap",
        multiplier=min(1.0, max(0.0, min(explicit_caps))),
        reasonCodes=reason_codes,
        explanation="Economic-event context limits risk using explicit event trading controls.",
    )


def _event_context_has_controls(context: ContextSignal, *, evaluated_at: datetime) -> bool:
    return bool(
        context.signal != Signal.HOLD.value
        or _event_bool(context, "event_blackout", "eventBlackout")
        or not _event_bool(context, "allow_new_entries", "allowNewEntries", default=True)
        or not _event_bool(context, "allow_position_increase", "allowPositionIncrease", default=True)
        or float(_event_value(context, "recommended_risk_cap", "recommendedRiskCap", default=1.0) or 1.0) < 1.0
        or float(_event_value(context, "recommended_size_multiplier", "recommendedSizeMultiplier", default=1.0) or 1.0) < 1.0
        or float(_event_value(context, "minimum_edge_multiplier", "minimumEdgeMultiplier", default=1.0) or 1.0) > 1.0
        or str(_event_value(context, "execution_mode", "executionMode", default="normal")).lower() not in {"", "normal"}
        or _event_cooldown_active(context, evaluated_at)
    )


def _event_control_multiplier(context: ContextSignal, config: DynamicTradingPolicyConfig, *, evaluated_at: datetime) -> float:
    if _event_bool(context, "event_blackout", "eventBlackout"):
        return 0.0
    if not _event_bool(context, "allow_new_entries", "allowNewEntries", default=True):
        return 0.0
    if _event_cooldown_active(context, evaluated_at):
        return 0.0
    mode = str(_event_value(context, "execution_mode", "executionMode", default="normal")).lower()
    if mode in {"blackout", "no_new_entries", "halt"}:
        return 0.0
    risk_cap = float(_event_value(context, "recommended_risk_cap", "recommendedRiskCap", default=config.eventRiskCap) or config.eventRiskCap)
    size_multiplier = float(_event_value(context, "recommended_size_multiplier", "recommendedSizeMultiplier", default=1.0) or 1.0)
    return min(risk_cap, size_multiplier)


def _event_bool(context: ContextSignal, *keys: str, default: bool = False) -> bool:
    value = _event_value(context, *keys, default=default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _event_value(context: ContextSignal, *keys: str, default: object = None) -> object:
    for key in keys:
        if key in context.features:
            return context.features[key]
    return default


def _event_cooldown_active(context: ContextSignal, evaluated_at: datetime) -> bool:
    value = _event_value(context, "cooldown_until", "cooldownUntil")
    if not value:
        return False
    try:
        cooldown_until = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return True
    if cooldown_until.tzinfo is None:
        cooldown_until = cooldown_until.replace(tzinfo=UTC)
    return evaluated_at < cooldown_until.astimezone(UTC)


def _time_of_day_cap(evaluated_at: datetime, hard_limits: HardRiskLimits, config: DynamicTradingPolicyConfig) -> DynamicRiskCap:
    cutoff = hard_limits.newEntryCutoff
    minutes_to_cutoff = (cutoff.hour * 60 + cutoff.minute) - (evaluated_at.time().hour * 60 + evaluated_at.time().minute)
    if minutes_to_cutoff <= 0:
        multiplier = 0.0
        reason = "policy.cap.after_new_entry_cutoff"
        explanation = "Time-of-day cap blocks new entry after the configured cutoff."
    elif minutes_to_cutoff <= config.nearCutoffMinutes:
        multiplier = config.nearCutoffRiskCap
        reason = "policy.cap.near_new_entry_cutoff"
        explanation = "Time-of-day cap reduces risk near the new-entry cutoff."
    else:
        multiplier = 1.0
        reason = "policy.cap.time_of_day_clear"
        explanation = "Time-of-day cap permits baseline risk."
    return DynamicRiskCap(capName="timeOfDayCap", multiplier=multiplier, reasonCodes=[reason], explanation=explanation)


def _drawdown_cap(account: AccountRiskState, hard_limits: HardRiskLimits) -> DynamicRiskCap:
    loss_limit = account.equity * (hard_limits.maximumDailyLossPercent / 100.0)
    if loss_limit <= 0:
        multiplier = 0.0
    else:
        daily_net = account.dailyNetPnlAfterExitCosts if account.dailyNetPnlAfterExitCosts is not None else account.realizedPnlToday
        daily_loss = abs(min(0.0, daily_net))
        multiplier = max(0.0, min(1.0, 1.0 - (daily_loss / loss_limit)))
    return DynamicRiskCap(
        capName="drawdownCap",
        multiplier=multiplier,
        reasonCodes=["policy.cap.daily_drawdown"],
        explanation="Drawdown cap progressively reduces risk as realized daily loss approaches the hard stop.",
    )


def _ml_cap(prediction: MetaModelPrediction, config: DynamicTradingPolicyConfig) -> DynamicRiskCap:
    probability = prediction.probabilityCandidateSuccess
    if probability is None:
        return DynamicRiskCap(
            capName="MLCap",
            multiplier=config.missingMlFallbackCap,
            reasonCodes=["policy.cap.ml_missing_fallback"],
            explanation="Missing optional ML uses the configured fallback cap.",
        )
    if probability < config.minimumMetaProbabilityForRisk:
        return DynamicRiskCap(
            capName="MLCap",
            multiplier=0.0,
            reasonCodes=["policy.cap.ml_probability_below_minimum"],
            explanation="ML probability below the configured minimum caps risk to zero.",
        )
    return DynamicRiskCap(
        capName="MLCap",
        multiplier=min(1.0, max(0.0, probability), config.maximumMetaRiskMultiplier),
        reasonCodes=["policy.cap.ml_probability"],
        explanation="ML cap is bounded by current candidate success probability and never exceeds baseline risk.",
    )


def _data_quality_cap(regime: RegimeState, context_signals: list[ContextSignal], config: DynamicTradingPolicyConfig) -> DynamicRiskCap:
    stale_or_missing_context = any(not context.dataReady for context in context_signals)
    if regime.confidence <= 0 or stale_or_missing_context:
        multiplier = config.dataQualityFallbackCap
        reason = "policy.cap.data_quality_reduced"
        explanation = "Missing or low-quality context/regime data uses the configured data-quality fallback cap."
    else:
        multiplier = 1.0
        reason = "policy.cap.data_quality_ready"
        explanation = "Data-quality cap permits baseline risk."
    return DynamicRiskCap(capName="dataQualityCap", multiplier=multiplier, reasonCodes=[reason], explanation=explanation)
