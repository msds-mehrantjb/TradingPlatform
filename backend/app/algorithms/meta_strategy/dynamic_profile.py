"""Dynamic Meta-Strategy profile resolution."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from backend.app.algorithms.meta_strategy.configuration import MetaStrategyBaselineSettings
from backend.app.algorithms.meta_strategy.versions import META_STRATEGY_DYNAMIC_PROFILE_VERSION


VolatilityLevel = Literal["LOW", "NORMAL", "HIGH", "EXTREME"]
LiquidityLevel = Literal["POOR", "NORMAL", "GOOD"]


@dataclass(frozen=True)
class MetaStrategyDynamicProfileConfig:
    dynamic_profile_version: str = META_STRATEGY_DYNAMIC_PROFILE_VERSION
    minimum_risk_percentage: float = 0.0
    maximum_risk_percentage: float = 0.02
    minimum_position_cap: float = 0.0
    maximum_position_cap: float = 0.25
    minimum_stop_multiplier: float = 0.50
    maximum_stop_multiplier: float = 3.00
    minimum_target_multiplier: float = 0.50
    maximum_target_multiplier: float = 4.00
    minimum_holding_minutes: int = 1
    maximum_holding_minutes: int = 120
    minimum_entry_threshold: float = 0.0
    maximum_entry_threshold: float = 1.0
    minimum_probability_threshold: float = 0.0
    maximum_probability_threshold: float = 1.0


@dataclass(frozen=True)
class MetaStrategyDynamicProfileContext:
    timestamp: datetime
    volatility_level: VolatilityLevel = "NORMAL"
    liquidity_level: LiquidityLevel = "NORMAL"
    spread_bps: float = 0.0
    event_blackout: bool = False
    session_allowed: bool = True
    model_health_score: float = 1.0
    missingness: float = 0.0
    ood_score: float = 0.0
    drawdown_risk_off: bool = False
    long_bias_allowed: bool = True
    short_bias_allowed: bool = True

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("meta_strategy.dynamic_profile.timestamp_must_be_timezone_aware")


@dataclass(frozen=True)
class MetaStrategyEffectiveSettings:
    baseline_configuration_version: str
    baseline_settings_hash: str
    entry_threshold: float
    model_probability_threshold: float
    risk_percentage: float
    position_cap: float
    stop_multiplier: float
    target_multiplier: float
    maximum_holding_minutes: int
    spread_limit_bps: float
    liquidity_requirement: float
    trade_count_limit: int
    allow_long: bool
    allow_short: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "baselineConfigurationVersion": self.baseline_configuration_version,
            "baselineSettingsHash": self.baseline_settings_hash,
            "entryThreshold": self.entry_threshold,
            "modelProbabilityThreshold": self.model_probability_threshold,
            "riskPercentage": self.risk_percentage,
            "positionCap": self.position_cap,
            "stopMultiplier": self.stop_multiplier,
            "targetMultiplier": self.target_multiplier,
            "maximumHoldingMinutes": self.maximum_holding_minutes,
            "spreadLimitBps": self.spread_limit_bps,
            "liquidityRequirement": self.liquidity_requirement,
            "tradeCountLimit": self.trade_count_limit,
            "allowLong": self.allow_long,
            "allowShort": self.allow_short,
        }


@dataclass(frozen=True)
class MetaStrategyDynamicProfile:
    profile_id: str
    profile_version: str
    calculated_at: datetime
    baseline_configuration_version: str
    baseline_settings_hash: str
    effective_settings: MetaStrategyEffectiveSettings
    active_overlays: tuple[str, ...]
    reason_codes: tuple[str, ...]

    def persisted_payload(self) -> dict[str, object]:
        return {
            "profileId": self.profile_id,
            "profileVersion": self.profile_version,
            "calculatedAt": self.calculated_at.isoformat(),
            "baselineConfigurationVersion": self.baseline_configuration_version,
            "baselineSettingsHash": self.baseline_settings_hash,
            "effectiveSettings": self.effective_settings.as_dict(),
            "activeOverlays": self.active_overlays,
            "reasonCodes": self.reason_codes,
        }


def resolve_meta_strategy_dynamic_profile(
    baseline: MetaStrategyBaselineSettings,
    context: MetaStrategyDynamicProfileContext,
    *,
    config: MetaStrategyDynamicProfileConfig | None = None,
) -> MetaStrategyDynamicProfile:
    settings = config or MetaStrategyDynamicProfileConfig()
    active_overlays: list[str] = []
    reason_codes: list[str] = ["meta_strategy.dynamic_profile.resolved"]

    entry_threshold = baseline.entry_threshold
    probability_threshold = baseline.model_probability_threshold
    risk_percentage = baseline.risk_percentage
    position_cap = baseline.position_cap
    stop_multiplier = baseline.stop_multiplier
    target_multiplier = baseline.target_multiplier
    maximum_holding_minutes = baseline.maximum_holding_minutes
    spread_limit_bps = baseline.spread_limit_bps
    liquidity_requirement = baseline.liquidity_requirement
    trade_count_limit = baseline.trade_count_limit
    allow_long = baseline.allow_long and context.long_bias_allowed
    allow_short = baseline.allow_short and context.short_bias_allowed

    risk_off_reasons = _risk_off_reasons(context)
    if risk_off_reasons:
        active_overlays.append("risk_off")
        reason_codes.extend(risk_off_reasons)
        risk_percentage = 0.0
        position_cap = 0.0
        trade_count_limit = 0
        allow_long = False
        allow_short = False
    elif context.volatility_level in {"HIGH", "EXTREME"}:
        active_overlays.append(f"volatility_{context.volatility_level.lower()}")
        reason_codes.append("meta_strategy.dynamic_profile.volatility_defensive")
        entry_threshold += 0.05 if context.volatility_level == "HIGH" else 0.10
        probability_threshold += 0.03 if context.volatility_level == "HIGH" else 0.08
        risk_percentage *= 0.50 if context.volatility_level == "HIGH" else 0.25
        position_cap *= 0.60 if context.volatility_level == "HIGH" else 0.35
        stop_multiplier *= 1.25 if context.volatility_level == "HIGH" else 1.50
        target_multiplier *= 1.10
        maximum_holding_minutes = int(max(1, maximum_holding_minutes * (0.75 if context.volatility_level == "HIGH" else 0.50)))
        spread_limit_bps *= 0.80

    if context.liquidity_level == "POOR" and not risk_off_reasons:
        active_overlays.append("liquidity_poor")
        reason_codes.append("meta_strategy.dynamic_profile.poor_liquidity_defensive")
        risk_percentage *= 0.50
        position_cap *= 0.50
        liquidity_requirement *= 1.50
        spread_limit_bps *= 0.85
        trade_count_limit = min(trade_count_limit, max(0, baseline.trade_count_limit - 2))
    elif context.liquidity_level == "GOOD" and not risk_off_reasons:
        active_overlays.append("liquidity_good")
        reason_codes.append("meta_strategy.dynamic_profile.good_liquidity")
        liquidity_requirement = max(0.0, liquidity_requirement * 0.90)

    if context.spread_bps > baseline.spread_limit_bps and not risk_off_reasons:
        active_overlays.append("spread_wide")
        reason_codes.append("meta_strategy.dynamic_profile.wide_spread_defensive")
        risk_percentage *= 0.50
        spread_limit_bps = min(spread_limit_bps, baseline.spread_limit_bps)

    effective = MetaStrategyEffectiveSettings(
        baseline_configuration_version=baseline.configuration_version,
        baseline_settings_hash=baseline.settings_hash,
        entry_threshold=_clamp(entry_threshold, settings.minimum_entry_threshold, settings.maximum_entry_threshold),
        model_probability_threshold=_clamp(probability_threshold, settings.minimum_probability_threshold, settings.maximum_probability_threshold),
        risk_percentage=_clamp(risk_percentage, settings.minimum_risk_percentage, min(settings.maximum_risk_percentage, baseline.risk_percentage)),
        position_cap=_clamp(position_cap, settings.minimum_position_cap, min(settings.maximum_position_cap, baseline.position_cap)),
        stop_multiplier=_clamp(stop_multiplier, settings.minimum_stop_multiplier, settings.maximum_stop_multiplier),
        target_multiplier=_clamp(target_multiplier, settings.minimum_target_multiplier, settings.maximum_target_multiplier),
        maximum_holding_minutes=int(_clamp(maximum_holding_minutes, settings.minimum_holding_minutes, settings.maximum_holding_minutes)),
        spread_limit_bps=max(0.0, spread_limit_bps),
        liquidity_requirement=max(0.0, liquidity_requirement),
        trade_count_limit=max(0, int(trade_count_limit)),
        allow_long=bool(allow_long),
        allow_short=bool(allow_short),
    )
    profile_id = _profile_id(active_overlays)
    return MetaStrategyDynamicProfile(
        profile_id=f"{profile_id}:{settings.dynamic_profile_version}",
        profile_version=settings.dynamic_profile_version,
        calculated_at=context.timestamp.astimezone(UTC),
        baseline_configuration_version=baseline.configuration_version,
        baseline_settings_hash=baseline.settings_hash,
        effective_settings=effective,
        active_overlays=tuple(active_overlays or ["baseline"]),
        reason_codes=tuple(reason_codes if active_overlays else (*reason_codes, "meta_strategy.dynamic_profile.baseline_effective")),
    )


def _risk_off_reasons(context: MetaStrategyDynamicProfileContext) -> tuple[str, ...]:
    reasons: list[str] = []
    if context.event_blackout:
        reasons.append("meta_strategy.dynamic_profile.risk_off_event_blackout")
    if not context.session_allowed:
        reasons.append("meta_strategy.dynamic_profile.risk_off_session_restricted")
    if context.drawdown_risk_off:
        reasons.append("meta_strategy.dynamic_profile.risk_off_drawdown")
    if context.volatility_level == "EXTREME":
        reasons.append("meta_strategy.dynamic_profile.risk_off_extreme_volatility")
    if context.model_health_score < 0.30:
        reasons.append("meta_strategy.dynamic_profile.risk_off_model_health")
    if context.missingness > 0.60:
        reasons.append("meta_strategy.dynamic_profile.risk_off_missingness")
    if context.ood_score > 0.90:
        reasons.append("meta_strategy.dynamic_profile.risk_off_ood")
    return tuple(reasons)


def _profile_id(active_overlays: list[str]) -> str:
    if not active_overlays:
        return "baseline"
    if "risk_off" in active_overlays:
        return "risk_off"
    return "+".join(active_overlays)


def _clamp(value: float, lower: float, upper: float) -> float:
    return round(max(float(lower), min(float(upper), float(value))), 10)


__all__ = [
    "LiquidityLevel",
    "MetaStrategyDynamicProfile",
    "MetaStrategyDynamicProfileConfig",
    "MetaStrategyDynamicProfileContext",
    "MetaStrategyEffectiveSettings",
    "VolatilityLevel",
    "resolve_meta_strategy_dynamic_profile",
]
