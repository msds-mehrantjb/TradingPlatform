"""Backend-authoritative settings resolution for Weighted Voting.

This module is deterministic settings plumbing only. It must not introduce ML
or algorithm-controlled weights.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

from backend.app.algorithms.weighted_voting.catalog import WEIGHTED_VOTING_STRATEGY_CATALOG
from backend.app.algorithms.weighted_voting.config import WeightedVotingConfig
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_ALGORITHM_ID
from backend.app.algorithms.weighted_voting.models import (
    WeightedDefaultSettings,
    WeightedDynamicSettingAdjustment,
    WeightedDynamicEnvelope,
    WeightedEffectiveSettings,
    WeightedEventRiskLevel,
    WeightedHardLimits,
    WeightedLiquidityLevel,
    WeightedMarketCondition,
    WeightedMarketQuality,
    WeightedVolatilityLevel,
)


WEIGHTED_VOTING_DYNAMIC_SETTINGS_VERSION = "weighted_voting_dynamic_settings_v2"
WEIGHTED_VOTING_EFFECTIVE_SETTINGS_VERSION = "weighted_effective_settings_v2"
WEIGHTED_VOTING_DYNAMIC_RESOLVER_VERSION = "weighted_voting_dynamic_settings_resolver_v1"
WEIGHTED_VOTING_DYNAMIC_PROFILE_VERSION = "weighted_voting_dynamic_profile_v1"
WEIGHTED_VOTING_DYNAMIC_SETTINGS_TTL_SECONDS = 300
WEIGHTED_VOTING_FORBIDDEN_DYNAMIC_SETTING_SOURCES = (
    "voting_ensemble",
    "wca",
    "regime_based_trading",
    "meta_strategy",
)


SETTING_FIELDS = (
    "base_risk_per_trade_percent",
    "order_allocation_percent",
    "daily_allocation_percent",
    "maximum_position_percent",
    "maximum_shares",
    "maximum_trades",
    "maximum_daily_loss_percent",
    "maximum_participation_rate",
    "minimum_score",
    "minimum_edge",
    "minimum_active_strategies",
    "minimum_directional_strategies",
    "maximum_spread_percent",
    "minimum_liquidity_volume",
    "atr_stop_multiplier",
    "minimum_stop_distance_percent",
    "target_r",
    "entry_buffer_percent",
    "break_even_trigger_r",
    "trailing_stop_atr_multiplier",
    "time_stop_minutes",
    "session_cutoff_minutes",
    "slippage_allowance_per_share",
    "cooldown_seconds",
)

INTEGER_FIELDS = {
    "maximum_shares",
    "maximum_trades",
    "minimum_active_strategies",
    "minimum_directional_strategies",
    "time_stop_minutes",
    "session_cutoff_minutes",
    "cooldown_seconds",
}

LEGACY_PERCENT_TO_RATE_FIELDS = {
    "maximum_participation_rate",
    "maximum_spread_percent",
    "minimum_stop_distance_percent",
    "entry_buffer_percent",
}

LEGACY_FIELD_MAP = {
    "baseRiskPercent": "base_risk_per_trade_percent",
    "riskPerTradePercent": "base_risk_per_trade_percent",
    "orderAllocationPercent": "order_allocation_percent",
    "dailyAllocationPercent": "daily_allocation_percent",
    "maxPositionPercent": "maximum_position_percent",
    "maxAllowedShares": "maximum_shares",
    "maximumShares": "maximum_shares",
    "maxTradesPerDay": "maximum_trades",
    "maximumTrades": "maximum_trades",
    "maxDailyLossPercent": "maximum_daily_loss_percent",
    "maximumDailyLossPercent": "maximum_daily_loss_percent",
    "maxParticipationPercent": "maximum_participation_rate",
    "maximumParticipationRate": "maximum_participation_rate",
    "minimumBuyScore": "minimum_score",
    "minimumScore": "minimum_score",
    "minimumSignalEdge": "minimum_edge",
    "minimumEdge": "minimum_edge",
    "minimumActiveStrategies": "minimum_active_strategies",
    "minimumDirectionalStrategies": "minimum_directional_strategies",
    "maximumSpreadPercent": "maximum_spread_percent",
    "minimumOneMinuteVolume": "minimum_liquidity_volume",
    "minimumLiquidityVolume": "minimum_liquidity_volume",
    "atrStopMultiplier": "atr_stop_multiplier",
    "minimumStopDistancePercent": "minimum_stop_distance_percent",
    "takeProfitR": "target_r",
    "targetR": "target_r",
    "entryBufferPercent": "entry_buffer_percent",
    "breakEvenTriggerR": "break_even_trigger_r",
    "trailingStopAtrMultiplier": "trailing_stop_atr_multiplier",
    "timeStopMinutes": "time_stop_minutes",
    "sessionCutoffMinutes": "session_cutoff_minutes",
    "pyramidingEnabled": "pyramiding_enabled",
}

SETTING_CATEGORY = {
    "base_risk_per_trade_percent": "risk",
    "maximum_daily_loss_percent": "risk",
    "maximum_position_percent": "position_exposure",
    "maximum_shares": "position_exposure",
    "order_allocation_percent": "order_allocation",
    "minimum_score": "entry_strictness",
    "minimum_edge": "entry_strictness",
    "minimum_active_strategies": "entry_strictness",
    "minimum_directional_strategies": "entry_strictness",
    "maximum_spread_percent": "entry_strictness",
    "minimum_liquidity_volume": "entry_strictness",
    "entry_buffer_percent": "entry_strictness",
    "atr_stop_multiplier": "stop_distance",
    "minimum_stop_distance_percent": "stop_distance",
    "trailing_stop_atr_multiplier": "stop_distance",
    "target_r": "target_distance",
    "break_even_trigger_r": "target_distance",
    "maximum_participation_rate": "participation_rate",
    "daily_allocation_percent": "trade_frequency",
    "maximum_trades": "trade_frequency",
    "time_stop_minutes": "trade_frequency",
    "session_cutoff_minutes": "trade_frequency",
    "cooldown_seconds": "trade_frequency",
    "slippage_allowance_per_share": "execution_cost",
}

TIGHTENING_FIELDS = {
    "minimum_score",
    "minimum_edge",
    "minimum_active_strategies",
    "minimum_directional_strategies",
    "minimum_liquidity_volume",
    "entry_buffer_percent",
    "minimum_stop_distance_percent",
    "slippage_allowance_per_share",
}


def default_weighted_settings(*, timestamp: datetime | None = None, baseline_config: WeightedVotingConfig | None = None) -> WeightedDefaultSettings:
    baseline = baseline_config or WeightedVotingConfig()
    return WeightedDefaultSettings(
        settings_version=f"weighted_default_settings_from_{baseline.config_version}",
        settings_timestamp=timestamp or _now(),
        base_risk_per_trade_percent=baseline.risk_per_trade_baseline_percent,
        order_allocation_percent=baseline.order_allocation_percent,
        daily_allocation_percent=baseline.daily_allocation_percent,
        maximum_position_percent=baseline.maximum_position_percent,
        maximum_shares=baseline.maximum_shares,
        maximum_trades=baseline.maximum_weighted_daily_trades,
        maximum_daily_loss_percent=baseline.maximum_weighted_daily_loss_percent,
        maximum_participation_rate=baseline.maximum_participation_rate,
        minimum_score=baseline.minimum_score,
        minimum_edge=baseline.minimum_edge,
        minimum_active_strategies=baseline.minimum_active_strategies,
        minimum_directional_strategies=baseline.minimum_directional_strategies,
        maximum_spread_percent=baseline.local_max_spread_percent,
        minimum_liquidity_volume=baseline.local_minimum_liquidity_volume,
        atr_stop_multiplier=baseline.atr_stop_multiplier,
        minimum_stop_distance_percent=baseline.minimum_stop_distance_percent,
        target_r=baseline.target_r,
        entry_buffer_percent=baseline.entry_buffer_percent,
        break_even_trigger_r=baseline.break_even_trigger_r,
        trailing_stop_atr_multiplier=baseline.trailing_stop_atr_multiplier,
        time_stop_minutes=baseline.time_stop_minutes,
        session_cutoff_minutes=baseline.session_cutoff_minutes,
        slippage_allowance_per_share=baseline.entry_slippage_per_share,
        cooldown_seconds=300,
        strategy_eligibility=dict(baseline.strategy_enablement),
        strategy_risk_multipliers={strategy_id: 1.0 for strategy_id in baseline.strategy_enablement},
        pyramiding_enabled=baseline.allow_weighted_pyramiding,
        max_position_percent=baseline.maximum_position_percent,
        max_daily_loss_percent=baseline.maximum_weighted_daily_loss_percent,
        max_daily_trades=baseline.maximum_weighted_daily_trades,
    )


def default_dynamic_envelope(*, timestamp: datetime | None = None) -> WeightedDynamicEnvelope:
    return WeightedDynamicEnvelope(settings_timestamp=timestamp or _now())


def default_hard_limits(*, timestamp: datetime | None = None) -> WeightedHardLimits:
    return WeightedHardLimits(settings_timestamp=timestamp or _now())


def migrate_legacy_weighted_settings(
    payload: dict[str, Any],
    *,
    timestamp: datetime | None = None,
    settings_version: str = "weighted_default_settings_migrated_v1",
) -> WeightedDefaultSettings:
    values: dict[str, Any] = {
        "settings_version": settings_version,
        "settings_timestamp": timestamp or _now(),
    }
    for source_key, target_key in LEGACY_FIELD_MAP.items():
        if source_key not in payload:
            continue
        value = payload[source_key]
        if target_key == "pyramiding_enabled":
            values[target_key] = bool(value)
        elif target_key in INTEGER_FIELDS:
            values[target_key] = max(0, int(round(_number(value, 0))))
        elif target_key in LEGACY_PERCENT_TO_RATE_FIELDS:
            values[target_key] = _number(value, getattr(WeightedDefaultSettings(), target_key)) / 100.0
        else:
            values[target_key] = _number(value, getattr(WeightedDefaultSettings(), target_key))
    return WeightedDefaultSettings(**values)


def resolve_effective_settings(
    *,
    default_settings: WeightedDefaultSettings | None = None,
    dynamic_envelope: WeightedDynamicEnvelope | None = None,
    hard_limits: WeightedHardLimits | None = None,
    dynamic_values: dict[str, Any] | None = None,
    baseline_config: WeightedVotingConfig | None = None,
    configuration_version: str = WEIGHTED_VOTING_DYNAMIC_SETTINGS_VERSION,
    timestamp: datetime | None = None,
    expiration_timestamp: datetime | None = None,
    source_evidence: tuple[str, ...] = (),
    market_condition_input: dict[str, Any] | None = None,
    dynamic_profile_version: str = WEIGHTED_VOTING_DYNAMIC_PROFILE_VERSION,
) -> WeightedEffectiveSettings:
    _reject_foreign_dynamic_settings_payload(dynamic_values or {})
    baseline = baseline_config or WeightedVotingConfig()
    defaults = default_settings or default_weighted_settings(timestamp=timestamp, baseline_config=baseline)
    envelope = dynamic_envelope or default_dynamic_envelope(timestamp=timestamp)
    limits = hard_limits or default_hard_limits(timestamp=timestamp)
    resolved: dict[str, Any] = {}
    reason_codes = ["weighted_voting.settings.defaults_visible"]
    for field_name in SETTING_FIELDS:
        default_value = getattr(defaults, field_name)
        requested = _requested_dynamic_value(field_name, default_value, dynamic_values or {}, envelope)
        bounded_by_envelope = _clamp_to_envelope(field_name, default_value, requested, envelope)
        bounded_by_limits = _clamp_to_hard_limits(field_name, bounded_by_envelope, limits)
        if bounded_by_limits != requested:
            reason_codes.append(f"weighted_voting.settings.{field_name}.clamped")
        resolved[field_name] = _coerce_field(field_name, bounded_by_limits)
    requested_pyramiding = bool((dynamic_values or {}).get("pyramiding_enabled", defaults.pyramiding_enabled))
    resolved["pyramiding_enabled"] = bool(
        requested_pyramiding
        and (defaults.pyramiding_enabled or (envelope.enabled and envelope.pyramiding_may_enable))
        and limits.pyramiding_allowed
    )
    if requested_pyramiding and not resolved["pyramiding_enabled"]:
        reason_codes.append("weighted_voting.settings.pyramiding.clamped")
    effective_timestamp = timestamp or _now()
    expires_at = expiration_timestamp or effective_timestamp + timedelta(seconds=WEIGHTED_VOTING_DYNAMIC_SETTINGS_TTL_SECONDS)
    strategy_eligibility = _resolve_strategy_eligibility(defaults, dynamic_values or {}, None, envelope)
    strategy_risk_multipliers = _resolve_strategy_risk_multipliers(defaults, dynamic_values or {}, None, envelope, limits)
    settings_version = f"{WEIGHTED_VOTING_EFFECTIVE_SETTINGS_VERSION}_{effective_timestamp.strftime('%Y%m%dT%H%M%S')}"
    configuration_hash = _configuration_hash(
        defaults,
        envelope,
        limits,
        {
            **resolved,
            "strategyEligibility": strategy_eligibility,
            "strategyRiskMultipliers": strategy_risk_multipliers,
            "baselineConfigurationHash": baseline.configuration_hash,
            "marketConditionInput": market_condition_input or {},
        },
        configuration_version,
    )
    return WeightedEffectiveSettings(
        settings_version=settings_version,
        settings_timestamp=effective_timestamp,
        default_settings=defaults,
        dynamic_envelope=envelope,
        hard_limits=limits,
        expiration_timestamp=expires_at,
        source_evidence=tuple(dict.fromkeys(source_evidence or ("weighted_voting.baseline_configuration",))),
        market_condition_input=market_condition_input or {},
        baseline_configuration_version=baseline.config_version,
        dynamic_profile_version=dynamic_profile_version,
        hard_limit_version=limits.limits_version,
        configuration_version=configuration_version,
        configuration_hash=configuration_hash,
        strategy_eligibility=strategy_eligibility,
        strategy_risk_multipliers=strategy_risk_multipliers,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        explanation="Backend-authoritative Weighted Voting settings resolved from defaults, dynamic envelope, and hard limits.",
        **resolved,
    )


class DynamicSettingsResolver:
    """Deterministic condition-driven settings resolver for Weighted Voting."""

    version = WEIGHTED_VOTING_DYNAMIC_RESOLVER_VERSION

    def __init__(
        self,
        *,
        default_settings: WeightedDefaultSettings | None = None,
        dynamic_envelope: WeightedDynamicEnvelope | None = None,
        hard_limits: WeightedHardLimits | None = None,
        baseline_config: WeightedVotingConfig | None = None,
    ) -> None:
        self.baseline_config = baseline_config or WeightedVotingConfig()
        self.default_settings = default_settings or default_weighted_settings(baseline_config=self.baseline_config)
        self.dynamic_envelope = dynamic_envelope or default_dynamic_envelope()
        self.hard_limits = hard_limits or default_hard_limits()

    def resolve(
        self,
        condition: WeightedMarketCondition,
        *,
        global_allowances: dict[str, float | int | bool] | None = None,
        timestamp: datetime | None = None,
    ) -> WeightedEffectiveSettings:
        return resolve_dynamic_settings_for_condition(
            default_settings=self.default_settings,
            dynamic_envelope=self.dynamic_envelope,
            hard_limits=self.hard_limits,
            condition=condition,
            global_allowances=global_allowances,
            baseline_config=self.baseline_config,
            timestamp=timestamp,
        )


def resolve_dynamic_settings_for_condition(
    *,
    default_settings: WeightedDefaultSettings | None = None,
    dynamic_envelope: WeightedDynamicEnvelope | None = None,
    hard_limits: WeightedHardLimits | None = None,
    condition: WeightedMarketCondition,
    global_allowances: dict[str, float | int | bool] | None = None,
    baseline_config: WeightedVotingConfig | None = None,
    timestamp: datetime | None = None,
    configuration_version: str = WEIGHTED_VOTING_DYNAMIC_RESOLVER_VERSION,
    dynamic_profile_version: str = WEIGHTED_VOTING_DYNAMIC_PROFILE_VERSION,
) -> WeightedEffectiveSettings:
    _reject_foreign_dynamic_settings_payload(allowances_payload := dict(global_allowances or {}))
    _reject_foreign_market_condition(condition)
    baseline = baseline_config or WeightedVotingConfig()
    default_settings = default_settings or default_weighted_settings(timestamp=timestamp, baseline_config=baseline)
    dynamic_envelope = dynamic_envelope or default_dynamic_envelope(timestamp=timestamp)
    hard_limits = hard_limits or default_hard_limits(timestamp=timestamp)
    multipliers, multiplier_reasons = _condition_multipliers(condition)
    resolved: dict[str, Any] = {}
    adjustments: list[WeightedDynamicSettingAdjustment] = []
    reason_codes = ["weighted_voting.dynamic_settings.condition_resolved", *multiplier_reasons]
    allowances = allowances_payload
    for field_name in SETTING_FIELDS:
        category = SETTING_CATEGORY[field_name]
        default_value = getattr(default_settings, field_name)
        multiplier = multipliers[category]
        envelope_minimum, envelope_maximum = _envelope_bounds(field_name, default_value, dynamic_envelope)
        proposed = _multiply_setting(field_name, default_value, multiplier)
        after_envelope = max(envelope_minimum, min(envelope_maximum, proposed))
        after_limits = _clamp_to_hard_limits(field_name, after_envelope, hard_limits)
        final_value = _apply_global_allowance(field_name, after_limits, allowances)
        final_value = _coerce_field(field_name, final_value)
        resolved[field_name] = final_value
        adjustment_reasons = [
            f"weighted_voting.dynamic_settings.{category}_multiplier",
            *multiplier_reasons,
        ]
        if after_envelope != proposed:
            adjustment_reasons.append(f"weighted_voting.dynamic_settings.{field_name}.envelope_clamped")
        if after_limits != after_envelope:
            adjustment_reasons.append(f"weighted_voting.dynamic_settings.{field_name}.hard_limit_clamped")
        if final_value != _coerce_field(field_name, after_limits):
            adjustment_reasons.append(f"weighted_voting.dynamic_settings.{field_name}.global_allowance_clamped")
        adjustments.append(
            WeightedDynamicSettingAdjustment(
                adjustment_category=category,
                setting_name=field_name,
                default_value=default_value,
                condition_multiplier=round(multiplier, 10),
                envelope_minimum=_coerce_field(field_name, envelope_minimum),
                envelope_maximum=_coerce_field(field_name, envelope_maximum),
                value_after_envelope=_coerce_field(field_name, after_envelope),
                algorithm_hard_limit=_hard_limit_value(field_name, hard_limits),
                global_allowance=allowances.get(field_name),
                final_value=final_value,
                reason_codes=tuple(dict.fromkeys(adjustment_reasons)),
                explanation=f"{field_name} resolved from default, condition multiplier, envelope, hard limit, and global allowance.",
            )
        )

    resolved["pyramiding_enabled"] = bool(
        default_settings.pyramiding_enabled
        and dynamic_envelope.pyramiding_may_enable
        and hard_limits.pyramiding_allowed
        and bool(allowances.get("pyramiding_enabled", True))
    )
    effective_timestamp = timestamp or _now()
    expires_at = effective_timestamp + timedelta(seconds=WEIGHTED_VOTING_DYNAMIC_SETTINGS_TTL_SECONDS)
    strategy_eligibility = _resolve_strategy_eligibility(default_settings, {}, condition, dynamic_envelope)
    strategy_risk_multipliers = _resolve_strategy_risk_multipliers(default_settings, {}, condition, dynamic_envelope, hard_limits)
    condition_input = condition.model_dump(mode="json")
    configuration_hash = _configuration_hash(
        default_settings,
        dynamic_envelope,
        hard_limits,
        {
            **resolved,
            "condition": condition_input,
            "strategyEligibility": strategy_eligibility,
            "strategyRiskMultipliers": strategy_risk_multipliers,
            "baselineConfigurationHash": baseline.configuration_hash,
        },
        configuration_version,
    )
    return WeightedEffectiveSettings(
        settings_version=f"{WEIGHTED_VOTING_EFFECTIVE_SETTINGS_VERSION}_{effective_timestamp.strftime('%Y%m%dT%H%M%S')}",
        settings_timestamp=effective_timestamp,
        default_settings=default_settings,
        dynamic_envelope=dynamic_envelope,
        hard_limits=hard_limits,
        expiration_timestamp=expires_at,
        source_evidence=tuple(dict.fromkeys(("weighted_voting.baseline_configuration", *condition.reason_codes))),
        market_condition_input=condition_input,
        baseline_configuration_version=baseline.config_version,
        dynamic_profile_version=dynamic_profile_version,
        hard_limit_version=hard_limits.limits_version,
        configuration_version=configuration_version,
        configuration_hash=configuration_hash,
        strategy_eligibility=strategy_eligibility,
        strategy_risk_multipliers=strategy_risk_multipliers,
        dynamic_adjustments=tuple(adjustments),
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        explanation="Condition-driven Weighted Voting settings resolved deterministically from defaults and market-condition inputs.",
        **resolved,
    )


def _requested_dynamic_value(field_name: str, default_value: Any, dynamic_values: dict[str, Any], envelope: WeightedDynamicEnvelope) -> Any:
    if not envelope.enabled:
        return default_value
    if field_name not in dynamic_values:
        return default_value
    if field_name in INTEGER_FIELDS:
        return int(round(_number(dynamic_values[field_name], default_value)))
    return _number(dynamic_values[field_name], default_value)


def _clamp_to_envelope(field_name: str, default_value: Any, requested: Any, envelope: WeightedDynamicEnvelope) -> Any:
    if not envelope.enabled:
        return default_value
    delta = getattr(envelope, f"{field_name}_delta")
    lower = default_value - delta
    upper = default_value + delta
    return max(lower, min(upper, requested))


def _envelope_bounds(field_name: str, default_value: Any, envelope: WeightedDynamicEnvelope) -> tuple[float | int, float | int]:
    if not envelope.enabled:
        return default_value, default_value
    delta = getattr(envelope, f"{field_name}_delta")
    return max(0, default_value - delta), default_value + delta


def _clamp_to_hard_limits(field_name: str, value: Any, limits: WeightedHardLimits) -> Any:
    bounds = {
        "base_risk_per_trade_percent": (0.0, limits.maximum_base_risk_per_trade_percent),
        "order_allocation_percent": (0.0, limits.maximum_order_allocation_percent),
        "daily_allocation_percent": (0.0, limits.maximum_daily_allocation_percent),
        "maximum_position_percent": (0.0, limits.maximum_position_percent),
        "maximum_shares": (0, limits.maximum_shares),
        "maximum_trades": (0, limits.maximum_trades),
        "maximum_daily_loss_percent": (0.0, limits.maximum_daily_loss_percent),
        "maximum_participation_rate": (0.0, limits.maximum_participation_rate),
        "minimum_score": (limits.minimum_score_floor, limits.minimum_score_ceiling),
        "minimum_edge": (limits.minimum_edge_floor, limits.minimum_edge_ceiling),
        "minimum_active_strategies": (limits.minimum_active_strategies_floor, 8),
        "minimum_directional_strategies": (limits.minimum_directional_strategies_floor, 8),
        "maximum_spread_percent": (0.0, limits.maximum_spread_percent),
        "minimum_liquidity_volume": (limits.minimum_liquidity_volume_floor, limits.maximum_liquidity_volume_requirement),
        "atr_stop_multiplier": (limits.minimum_atr_stop_multiplier, limits.maximum_atr_stop_multiplier),
        "minimum_stop_distance_percent": (limits.minimum_stop_distance_percent_floor, limits.maximum_stop_distance_percent),
        "target_r": (limits.minimum_target_r, limits.maximum_target_r),
        "entry_buffer_percent": (0.0, limits.maximum_entry_buffer_percent),
        "break_even_trigger_r": (0.0, limits.maximum_break_even_trigger_r),
        "trailing_stop_atr_multiplier": (0.0, limits.maximum_trailing_stop_atr_multiplier),
        "time_stop_minutes": (0, limits.maximum_time_stop_minutes),
        "session_cutoff_minutes": (0, limits.maximum_session_cutoff_minutes),
        "slippage_allowance_per_share": (0.0, limits.maximum_slippage_allowance_per_share),
        "cooldown_seconds": (0, limits.maximum_cooldown_seconds),
    }
    lower, upper = bounds[field_name]
    return max(lower, min(upper, value))


def _coerce_field(field_name: str, value: Any) -> Any:
    if field_name in INTEGER_FIELDS:
        return int(round(value))
    return float(value)


def _multiply_setting(field_name: str, default_value: Any, multiplier: float) -> float | int:
    proposed = default_value * multiplier
    if field_name in INTEGER_FIELDS:
        return int(round(proposed))
    return float(proposed)


def _apply_global_allowance(field_name: str, value: Any, allowances: dict[str, float | int | bool]) -> Any:
    if field_name not in allowances:
        return value
    allowance = allowances[field_name]
    if isinstance(allowance, bool):
        return value
    return min(value, allowance)


def _hard_limit_value(field_name: str, limits: WeightedHardLimits) -> float | int | bool:
    hard_limits = {
        "base_risk_per_trade_percent": limits.maximum_base_risk_per_trade_percent,
        "order_allocation_percent": limits.maximum_order_allocation_percent,
        "daily_allocation_percent": limits.maximum_daily_allocation_percent,
        "maximum_position_percent": limits.maximum_position_percent,
        "maximum_shares": limits.maximum_shares,
        "maximum_trades": limits.maximum_trades,
        "maximum_daily_loss_percent": limits.maximum_daily_loss_percent,
        "maximum_participation_rate": limits.maximum_participation_rate,
        "minimum_score": limits.minimum_score_ceiling,
        "minimum_edge": limits.minimum_edge_ceiling,
        "minimum_active_strategies": 8,
        "minimum_directional_strategies": 8,
        "maximum_spread_percent": limits.maximum_spread_percent,
        "minimum_liquidity_volume": limits.maximum_liquidity_volume_requirement,
        "atr_stop_multiplier": limits.maximum_atr_stop_multiplier,
        "minimum_stop_distance_percent": limits.maximum_stop_distance_percent,
        "target_r": limits.maximum_target_r,
        "entry_buffer_percent": limits.maximum_entry_buffer_percent,
        "break_even_trigger_r": limits.maximum_break_even_trigger_r,
        "trailing_stop_atr_multiplier": limits.maximum_trailing_stop_atr_multiplier,
        "time_stop_minutes": limits.maximum_time_stop_minutes,
        "session_cutoff_minutes": limits.maximum_session_cutoff_minutes,
        "slippage_allowance_per_share": limits.maximum_slippage_allowance_per_share,
        "cooldown_seconds": limits.maximum_cooldown_seconds,
    }
    return hard_limits[field_name]


def _condition_multipliers(condition: WeightedMarketCondition) -> tuple[dict[str, float], list[str]]:
    multipliers = {
        "risk": 1.0,
        "position_exposure": 1.0,
        "order_allocation": 1.0,
        "entry_strictness": 1.0,
        "stop_distance": 1.0,
        "target_distance": 1.0,
        "participation_rate": 1.0,
        "trade_frequency": 1.0,
        "execution_cost": 1.0,
    }
    reasons = ["weighted_voting.dynamic_settings.condition_inputs"]
    quality = str(condition.market_quality)
    volatility = str(condition.volatility_level)
    liquidity = str(condition.liquidity_level)
    event_risk = str(condition.event_risk)

    if quality == WeightedMarketQuality.CLEAN.value:
        if condition.pending_confirmation_count == 0 and "weighted_voting.market_condition.hysteresis_confirmed" in condition.reason_codes:
            _scale(multipliers, ("risk", "position_exposure", "order_allocation", "participation_rate", "trade_frequency"), 1.10)
            _scale(multipliers, ("entry_strictness",), 1.06)
            reasons.append("weighted_voting.dynamic_settings.confirmed_better_conditions")
        else:
            reasons.append("weighted_voting.dynamic_settings.better_conditions_waiting_for_confirmation")
    elif quality == WeightedMarketQuality.MIXED.value:
        _scale(multipliers, ("risk", "position_exposure", "order_allocation", "participation_rate", "trade_frequency"), 0.75)
        _scale(multipliers, ("entry_strictness", "stop_distance", "execution_cost"), 1.15)
        _scale(multipliers, ("target_distance",), 0.90)
        reasons.append("weighted_voting.dynamic_settings.mixed_quality_reduction")
    elif quality == WeightedMarketQuality.UNSTABLE.value:
        _scale(multipliers, ("risk", "position_exposure", "order_allocation", "participation_rate", "trade_frequency"), 0.35)
        _scale(multipliers, ("entry_strictness", "stop_distance", "execution_cost"), 1.35)
        _scale(multipliers, ("target_distance",), 0.75)
        reasons.append("weighted_voting.dynamic_settings.unstable_quality_reduction")

    if volatility == WeightedVolatilityLevel.HIGH.value:
        _scale(multipliers, ("risk", "position_exposure", "order_allocation", "participation_rate"), 0.70)
        _scale(multipliers, ("entry_strictness", "stop_distance", "execution_cost"), 1.20)
        reasons.append("weighted_voting.dynamic_settings.high_volatility")
    if volatility == WeightedVolatilityLevel.EXTREME.value:
        _zero_new_entry_risk(multipliers)
        _scale(multipliers, ("entry_strictness", "stop_distance", "execution_cost"), 1.50)
        reasons.append("weighted_voting.dynamic_settings.extreme_volatility_zero_new_entry_risk")
    if liquidity == WeightedLiquidityLevel.REDUCED.value:
        _scale(multipliers, ("risk", "position_exposure", "order_allocation", "participation_rate", "trade_frequency"), 0.65)
        _scale(multipliers, ("entry_strictness",), 1.20)
        reasons.append("weighted_voting.dynamic_settings.reduced_liquidity")
    if liquidity == WeightedLiquidityLevel.POOR.value:
        _zero_new_entry_risk(multipliers)
        _scale(multipliers, ("entry_strictness",), 1.50)
        reasons.append("weighted_voting.dynamic_settings.poor_liquidity_zero_new_entry_risk")
    if event_risk == WeightedEventRiskLevel.ELEVATED.value:
        _scale(multipliers, ("risk", "position_exposure", "order_allocation", "participation_rate", "trade_frequency"), 0.50)
        _scale(multipliers, ("entry_strictness",), 1.25)
        reasons.append("weighted_voting.dynamic_settings.elevated_event_risk")
    if event_risk == WeightedEventRiskLevel.BLOCKED.value:
        _zero_new_entry_risk(multipliers)
        _scale(multipliers, ("entry_strictness",), 1.50)
        reasons.append("weighted_voting.dynamic_settings.blocked_event_zero_new_entry_risk")
    if condition.pending_confirmation_count > 0:
        multipliers["risk"] = min(multipliers["risk"], 1.0)
        multipliers["position_exposure"] = min(multipliers["position_exposure"], 1.0)
        multipliers["order_allocation"] = min(multipliers["order_allocation"], 1.0)
        multipliers["participation_rate"] = min(multipliers["participation_rate"], 1.0)
        multipliers["trade_frequency"] = min(multipliers["trade_frequency"], 1.0)
        reasons.append("weighted_voting.dynamic_settings.hysteresis_blocks_exposure_increase")
    return multipliers, reasons


def _scale(multipliers: dict[str, float], categories: tuple[str, ...], factor: float) -> None:
    for category in categories:
        multipliers[category] *= factor


def _zero_new_entry_risk(multipliers: dict[str, float]) -> None:
    for category in ("risk", "position_exposure", "order_allocation", "participation_rate", "trade_frequency"):
        multipliers[category] = 0.0


def _resolve_strategy_eligibility(
    defaults: WeightedDefaultSettings,
    dynamic_values: dict[str, Any],
    condition: WeightedMarketCondition | None,
    envelope: WeightedDynamicEnvelope,
) -> dict[str, bool]:
    catalog_ids = _catalog_strategy_ids()
    eligibility = {strategy_id: bool(defaults.strategy_eligibility.get(strategy_id, True)) for strategy_id in catalog_ids}
    requested = dynamic_values.get("strategy_eligibility") if envelope.enabled else None
    if isinstance(requested, dict):
        for strategy_id in catalog_ids:
            if strategy_id in requested:
                eligibility[strategy_id] = bool(requested[strategy_id])
    if condition is not None:
        tags = set(getattr(condition, "condition_tags", ()) or ())
        blocked = (
            "weighted_voting.market_condition.avoid_trading" in tags
            or str(condition.event_risk) == WeightedEventRiskLevel.BLOCKED.value
            or (str(condition.volatility_level) == WeightedVolatilityLevel.EXTREME.value and str(condition.liquidity_level) == WeightedLiquidityLevel.POOR.value)
        )
        if blocked:
            eligibility = {strategy_id: False for strategy_id in catalog_ids}
    return eligibility


def _resolve_strategy_risk_multipliers(
    defaults: WeightedDefaultSettings,
    dynamic_values: dict[str, Any],
    condition: WeightedMarketCondition | None,
    envelope: WeightedDynamicEnvelope,
    limits: WeightedHardLimits,
) -> dict[str, float]:
    catalog_ids = _catalog_strategy_ids()
    multipliers = {strategy_id: float(defaults.strategy_risk_multipliers.get(strategy_id, 1.0)) for strategy_id in catalog_ids}
    requested = dynamic_values.get("strategy_risk_multipliers") if envelope.enabled else None
    if isinstance(requested, dict):
        delta = envelope.strategy_risk_multiplier_delta
        for strategy_id in catalog_ids:
            if strategy_id not in requested:
                continue
            requested_value = _number(requested[strategy_id], multipliers[strategy_id])
            lower = max(0.0, multipliers[strategy_id] - delta)
            upper = multipliers[strategy_id] + delta
            multipliers[strategy_id] = max(lower, min(upper, requested_value))
    if condition is not None:
        fit_by_family = getattr(condition, "regime_fit_multipliers", {}) or {}
        for entry in WEIGHTED_VOTING_STRATEGY_CATALOG:
            family_fit = _number(fit_by_family.get(str(entry.family), fit_by_family.get(entry.family.value, 1.0)), 1.0)
            multipliers[entry.strategy_id] *= max(0.0, min(1.5, family_fit))
    return {
        strategy_id: round(max(0.0, min(limits.maximum_strategy_risk_multiplier, multiplier)), 10)
        for strategy_id, multiplier in multipliers.items()
    }


def _catalog_strategy_ids() -> tuple[str, ...]:
    return tuple(entry.strategy_id for entry in WEIGHTED_VOTING_STRATEGY_CATALOG)


def _reject_foreign_market_condition(condition: WeightedMarketCondition) -> None:
    if getattr(condition, "algorithm_id", WEIGHTED_VOTING_ALGORITHM_ID) != WEIGHTED_VOTING_ALGORITHM_ID:
        raise ValueError("Weighted Voting dynamic settings cannot use another algorithm's market-condition input")


def _reject_foreign_dynamic_settings_payload(payload: dict[str, Any]) -> None:
    if _contains_foreign_dynamic_settings_source(payload):
        raise ValueError("Weighted Voting dynamic settings cannot apply settings from another algorithm")


def _contains_foreign_dynamic_settings_source(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized_key = str(key).lower()
            if normalized_key in {"algorithm_id", "algorithmid", "source_algorithm", "sourcealgorithm"} and str(item) != WEIGHTED_VOTING_ALGORITHM_ID:
                return True
            if normalized_key in {"source", "settings_source", "settingssource"} and str(item).lower() in WEIGHTED_VOTING_FORBIDDEN_DYNAMIC_SETTING_SOURCES:
                return True
            if _contains_foreign_dynamic_settings_source(item):
                return True
    elif isinstance(value, (list, tuple, set)):
        return any(_contains_foreign_dynamic_settings_source(item) for item in value)
    return False


def _configuration_hash(
    defaults: WeightedDefaultSettings,
    envelope: WeightedDynamicEnvelope,
    limits: WeightedHardLimits,
    resolved: dict[str, Any],
    configuration_version: str,
) -> str:
    payload = {
        "configurationVersion": configuration_version,
        "defaults": defaults.model_dump(mode="json"),
        "dynamicEnvelope": envelope.model_dump(mode="json"),
        "hardLimits": limits.model_dump(mode="json"),
        "resolved": resolved,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _number(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if number != number:
        return float(default)
    return number


def _now() -> datetime:
    return datetime.now(timezone.utc)
