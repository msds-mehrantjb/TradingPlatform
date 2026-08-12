"""Authoritative WCA configuration contracts and migration helpers."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal
from uuid import uuid4

from pydantic import Field, field_validator, model_validator

from backend.app.algorithms.wca.contracts import (
    WCA_ALGORITHM_ID,
    WcaBaselineSettings,
    WcaContractModel,
    WcaDecisionSettings,
    WcaEffectiveSettings,
    WcaEvaluationStatus,
    WcaMarketStatus,
    WcaRuntimeMode,
    WcaTradingSettings,
    coerce_wca_runtime_mode,
)
from backend.app.algorithms.wca.dynamic_profile import WcaDynamicProfileConfig, resolve_dynamic_profile
from backend.app.algorithms.wca.strategy_registry import (
    WCA_HARD_FILTER_REGISTRY,
    WCA_MODIFIER_REGISTRY,
    WCA_STRATEGY_REGISTRY,
)


WCA_CONFIGURATION_SCHEMA_VERSION = "wca_canonical_configuration_schema_v1"
WCA_CONFIGURATION_VERSION = "wca_canonical_configuration_v1"
WCA_LEGACY_CONFIGURATION_VERSION = "wca_legacy_configuration_v1"
WCA_SETTINGS_VERSION = "wca_settings_schema_v1"


class WcaConfigurationLifecycle(str, Enum):
    CANDIDATE = "candidate"
    APPROVED = "approved"
    ACTIVE = "active"
    DISABLED = "disabled"
    ROLLED_BACK = "rolled_back"


class WcaConfigurationUnavailable(RuntimeError):
    """Raised when runtime code cannot load an approved WCA configuration."""


class WcaAggregationSettings(WcaContractModel):
    settings_version: str = WCA_SETTINGS_VERSION
    minimum_score: float = Field(default=0.35, ge=0, le=1)
    strong_buy_threshold: float = Field(default=0.65, ge=0, le=1)
    buy_threshold: float = Field(default=0.35, ge=0, le=1)
    sell_threshold: float = Field(default=-0.35, ge=-1, le=0)
    strong_sell_threshold: float = Field(default=-0.65, ge=-1, le=0)
    minimum_active_strategies: int = Field(default=3, ge=1)
    minimum_directional_agreement: float = Field(default=0.50, ge=0, le=1)
    minimum_average_confidence: float = Field(default=0.45, ge=0, le=1)

    @model_validator(mode="after")
    def validate_threshold_ordering(self) -> "WcaAggregationSettings":
        if not (self.strong_sell_threshold <= self.sell_threshold <= 0 <= self.buy_threshold <= self.strong_buy_threshold):
            raise ValueError("WCA threshold ordering must be strong_sell <= sell <= 0 <= buy <= strong_buy")
        if self.minimum_score > min(abs(self.buy_threshold), abs(self.sell_threshold)):
            raise ValueError("minimum_score cannot exceed the closest entry threshold")
        return self


class WcaRiskSettings(WcaContractModel):
    settings_version: str = WCA_SETTINGS_VERSION
    base_risk_percent: float = Field(default=1.0, ge=0)
    max_daily_loss_percent: float = Field(default=3.0, ge=0)
    max_daily_trades: int = Field(default=5, ge=0)
    hard_max_risk_percent: float = Field(default=1.0, ge=0)
    hard_max_daily_loss_percent: float = Field(default=3.0, ge=0)
    hard_max_daily_trades: int = Field(default=5, ge=0)

    @model_validator(mode="after")
    def validate_hard_caps(self) -> "WcaRiskSettings":
        if self.base_risk_percent > self.hard_max_risk_percent:
            raise ValueError("base_risk_percent cannot exceed hard_max_risk_percent")
        if self.max_daily_loss_percent > self.hard_max_daily_loss_percent:
            raise ValueError("max_daily_loss_percent cannot exceed hard_max_daily_loss_percent")
        if self.max_daily_trades > self.hard_max_daily_trades:
            raise ValueError("max_daily_trades cannot exceed hard_max_daily_trades")
        return self


class WcaSizingSettings(WcaContractModel):
    settings_version: str = WCA_SETTINGS_VERSION
    order_allocation_percent: float = Field(default=10.0, ge=0)
    daily_allocation_percent: float = Field(default=20.0, ge=0)
    max_position_percent: float = Field(default=10.0, ge=0)
    max_participation_percent: float = Field(default=1.0, ge=0)
    max_allowed_shares: int = Field(default=0, ge=0)
    hard_max_order_allocation_percent: float = Field(default=10.0, ge=0)
    hard_max_daily_allocation_percent: float = Field(default=20.0, ge=0)
    hard_max_position_percent: float = Field(default=10.0, ge=0)
    hard_max_participation_percent: float = Field(default=1.0, ge=0)
    hard_max_allowed_shares: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_hard_caps(self) -> "WcaSizingSettings":
        checks = (
            (self.order_allocation_percent, self.hard_max_order_allocation_percent, "order_allocation_percent"),
            (self.daily_allocation_percent, self.hard_max_daily_allocation_percent, "daily_allocation_percent"),
            (self.max_position_percent, self.hard_max_position_percent, "max_position_percent"),
            (self.max_participation_percent, self.hard_max_participation_percent, "max_participation_percent"),
        )
        for value, cap, field_name in checks:
            if value > cap:
                raise ValueError(f"{field_name} cannot exceed its hard cap")
        if self.hard_max_allowed_shares and self.max_allowed_shares > self.hard_max_allowed_shares:
            raise ValueError("max_allowed_shares cannot exceed hard_max_allowed_shares")
        return self


class WcaExecutionSettings(WcaContractModel):
    settings_version: str = WCA_SETTINGS_VERSION
    paper_only: bool = True
    allow_real_money_submission: bool = False
    cooldown_seconds: int = Field(default=0, ge=0)
    entry_cutoff_minutes: int = Field(default=15 * 60 + 30, ge=0)
    pyramiding_enabled: bool = False
    max_spread_percent: float = Field(default=0.10, ge=0)
    minimum_one_minute_volume: float = Field(default=0, ge=0)
    configured_fee_per_share: float = Field(default=0.0, ge=0)
    market_impact_bps: float = Field(default=1.0, ge=0)
    adverse_selection_bps: float = Field(default=1.0, ge=0)
    replacement_cost_bps: float = Field(default=0.5, ge=0)
    observed_slippage_per_share: float = Field(default=0.0, ge=0)
    uncertainty_buffer_per_share: float = Field(default=0.01, ge=0)
    minimum_net_edge_per_share: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def validate_paper_only(self) -> "WcaExecutionSettings":
        if not self.paper_only or self.allow_real_money_submission:
            raise ValueError("WCA remains paper-only; real-money submission is disabled")
        return self


class WcaExitSettings(WcaContractModel):
    settings_version: str = WCA_SETTINGS_VERSION
    atr_stop_multiplier: float = Field(default=2.0, ge=0)
    minimum_stop_distance_percent: float = Field(default=0.05, ge=0)
    take_profit_r: float = Field(default=1.5, ge=0)
    fixed_stop_distance_dollars: float = Field(default=0, ge=0)
    assumed_slippage_per_share: float = Field(default=0.02, ge=0)


class WcaDynamicProfileSettings(WcaContractModel):
    settings_version: str = WCA_SETTINGS_VERSION
    enabled: bool = True
    overlay_ttl_seconds: int = Field(default=900, ge=60)
    hysteresis_confirmation_bars: int = Field(default=3, ge=1)
    minimum_profile_hold_seconds: int = Field(default=300, ge=0)
    risk_expanding_overlays_enabled: bool = False
    maximum_defensive_risk_multiplier: float = Field(default=1.0, ge=0, le=1)
    maximum_defensive_quantity_multiplier: float = Field(default=1.0, ge=0, le=1)


class WcaCalibrationSettings(WcaContractModel):
    settings_version: str = WCA_SETTINGS_VERSION
    enabled: bool = False
    minimum_samples: int = Field(default=100, ge=1)
    prior_success_rate: float = Field(default=0.5, ge=0, le=1)
    prior_strength: float = Field(default=10.0, ge=0)


class WcaWeightSettings(WcaContractModel):
    settings_version: str = WCA_SETTINGS_VERSION
    weight_version: str = "wca_canonical_baseline_weights_v1"
    baseline_weights: dict[str, float] = Field(default_factory=lambda: {entry.slug: entry.base_weight for entry in WCA_STRATEGY_REGISTRY})

    @field_validator("baseline_weights")
    @classmethod
    def validate_primary_weights(cls, weights: dict[str, float]) -> dict[str, float]:
        expected = {entry.slug for entry in WCA_STRATEGY_REGISTRY}
        if set(weights) != expected:
            raise ValueError("baseline_weights must include exactly the authoritative primary voter slugs")
        total = sum(weights.values())
        if abs(total - 1.0) > 0.000001:
            raise ValueError("primary baseline weights must total 1.00")
        return weights


class WcaRuntimeSettings(WcaContractModel):
    settings_version: str = WCA_SETTINGS_VERSION
    runtime_mode: WcaRuntimeMode = WcaRuntimeMode.SHADOW
    fail_closed_for_missing_configuration: bool = True
    require_completed_one_minute_bars: bool = True
    block_new_entries_on_configuration_error: bool = True
    runtime_default_baseline_construction_allowed: bool = False
    maximum_finalized_bar_age_seconds: int = Field(default=20, ge=1)
    maximum_quote_age_seconds: int = Field(default=15, ge=1)
    maximum_authoritative_account_state_age_seconds: int = Field(default=120, ge=1)
    maximum_reconciliation_age_seconds: int = Field(default=120, ge=1)
    maximum_queue_delay_seconds: int = Field(default=20, ge=1)
    maximum_clock_skew_seconds: int = Field(default=2, ge=0)

    @field_validator("runtime_mode", mode="before")
    @classmethod
    def validate_runtime_mode(cls, value: WcaRuntimeMode | str) -> WcaRuntimeMode:
        return coerce_wca_runtime_mode(value)


class WcaLimitedAutomaticPaperSettings(WcaContractModel):
    settings_version: str = WCA_SETTINGS_VERSION
    enabled: bool = True
    symbol: str = "SPY"
    max_quantity: int = Field(default=10, ge=1)
    max_daily_trades: int = Field(default=3, ge=0)
    max_daily_loss_dollars: float = Field(default=100.0, ge=0)
    entry_windows: tuple[str, ...] = ("10:00-11:30 America/New_York", "13:30-15:30 America/New_York")
    permitted_strategy_ids: tuple[str, ...] = ("C1", "C4", "C7")
    shadow_strategy_ids: tuple[str, ...] = tuple(entry.strategy_id for entry in WCA_STRATEGY_REGISTRY if entry.strategy_id not in {"C1", "C4", "C7"})
    broker_account_id: str = "paper"
    rollout_stage: str = "LIMITED_AUTOMATIC_PAPER"
    permitted_order_types: tuple[str, ...] = ("LIMIT",)

    @model_validator(mode="after")
    def validate_limited_controls(self) -> "WcaLimitedAutomaticPaperSettings":
        if self.symbol.upper() != "SPY":
            raise ValueError("limited automatic paper is currently SPY-only")
        expected = {entry.strategy_id for entry in WCA_STRATEGY_REGISTRY}
        unknown = set(self.permitted_strategy_ids) - expected
        if unknown:
            raise ValueError(f"unknown WCA limited-paper strategy IDs: {sorted(unknown)}")
        if not self.permitted_strategy_ids:
            raise ValueError("limited automatic paper requires at least one permitted strategy")
        if any(order_type.upper() not in {"LIMIT", "STOP_LIMIT"} for order_type in self.permitted_order_types):
            raise ValueError("limited automatic paper permits only explicit WCA paper order types")
        for window in self.entry_windows:
            _validate_entry_window(window)
        if not self.broker_account_id:
            raise ValueError("limited automatic paper requires WCA broker account identity")
        return self

class WcaLocalPaperSettings(WcaContractModel):
    settings_version: str = WCA_SETTINGS_VERSION
    enabled: bool = True
    starting_balance: float = Field(default=100_000.00, gt=0)
    reset_policy: Literal["never", "manual_only", "daily_manual_only"] = "manual_only"
    commission_per_share: float = Field(default=0.0, ge=0)
    minimum_commission: float = Field(default=0.0, ge=0)
    slippage_model: Literal["none", "fixed_bps", "spread_aware"] = "none"
    buying_power_multiplier: float = Field(default=1.0, ge=0)
    allow_short: bool = False
    persist_between_sessions: bool = True

    @model_validator(mode="after")
    def validate_local_paper(self) -> "WcaLocalPaperSettings":
        if not self.persist_between_sessions and self.reset_policy == "never":
            raise ValueError("local paper reset policy cannot be never when persistence between sessions is disabled")
        if self.buying_power_multiplier == 0:
            raise ValueError("local paper buying_power_multiplier must be positive")
        return self


class WcaModuleSettings(WcaContractModel):
    settings_version: str = WCA_SETTINGS_VERSION
    enabled: bool = True
    lifecycle: Literal["active", "shadow", "disabled", "unavailable", "not_data_ready"] = "shadow"
    minimum_history: str = ""
    required_market_inputs: tuple[str, ...] = ()
    minimum_confidence_multiplier: float = Field(default=0.50, ge=0)
    maximum_confidence_multiplier: float = Field(default=1.25, ge=0)
    minimum_weight_multiplier: float = Field(default=0.50, ge=0)
    maximum_weight_multiplier: float = Field(default=1.25, ge=0)
    minimum_risk_multiplier: float = Field(default=0.00, ge=0)
    maximum_risk_multiplier: float = Field(default=1.00, ge=0)
    minimum_position_size_multiplier: float = Field(default=0.00, ge=0)
    maximum_position_size_multiplier: float = Field(default=1.00, ge=0)
    minimum_entry_requirement_multiplier: float = Field(default=1.00, ge=0)
    maximum_entry_requirement_multiplier: float = Field(default=1.50, ge=0)

    @model_validator(mode="after")
    def validate_multiplier_bounds(self) -> "WcaModuleSettings":
        bounds = (
            ("confidence", self.minimum_confidence_multiplier, self.maximum_confidence_multiplier),
            ("weight", self.minimum_weight_multiplier, self.maximum_weight_multiplier),
            ("risk", self.minimum_risk_multiplier, self.maximum_risk_multiplier),
            ("position_size", self.minimum_position_size_multiplier, self.maximum_position_size_multiplier),
            ("entry_requirement", self.minimum_entry_requirement_multiplier, self.maximum_entry_requirement_multiplier),
        )
        for label, lower, upper in bounds:
            if lower > upper:
                raise ValueError(f"{label} multiplier minimum cannot exceed maximum")
        return self


class MovingAverageTrendSettings(WcaModuleSettings):
    lifecycle: Literal["active", "shadow", "disabled", "unavailable", "not_data_ready"] = "active"
    fast_period: int = Field(default=20, ge=2)
    slow_period: int = Field(default=50, ge=3)
    slope_lookback: int = Field(default=5, ge=2)
    minimum_slope_percent: float = Field(default=0.00025, ge=0)
    minimum_ma_separation_percent: float = Field(default=0.0010, ge=0)
    persistence_bars: int = Field(default=3, ge=1)
    price_location_tolerance_percent: float = Field(default=0.0010, ge=0)


class FirstPullbackAfterOpenSettings(WcaModuleSettings):
    lifecycle: Literal["active", "shadow", "disabled", "unavailable", "not_data_ready"] = "active"
    opening_impulse_minutes: int = Field(default=10, ge=3)
    minimum_impulse_percent: float = Field(default=0.004, ge=0)
    pullback_max_retrace_percent: float = Field(default=0.55, ge=0, le=1)
    pullback_volume_contraction_ratio: float = Field(default=0.95, ge=0)
    confirmation_close_buffer_percent: float = Field(default=0.0005, ge=0)
    vwap_tolerance_percent: float = Field(default=0.0015, ge=0)


class VwapTrendContinuationSettings(WcaModuleSettings):
    lifecycle: Literal["active", "shadow", "disabled", "unavailable", "not_data_ready"] = "active"
    trend_lookback: int = Field(default=20, ge=5)
    vwap_slope_lookback: int = Field(default=5, ge=2)
    minimum_vwap_slope_percent: float = Field(default=0.00020, ge=0)
    acceptance_bars: int = Field(default=3, ge=1)
    controlled_pullback_max_atr: float = Field(default=1.35, ge=0)
    confirmation_buffer_percent: float = Field(default=0.0005, ge=0)


class VwapMeanReversionSettings(WcaModuleSettings):
    lifecycle: Literal["active", "shadow", "disabled", "unavailable", "not_data_ready"] = "active"
    lookback: int = Field(default=20, ge=5)
    minimum_overextension_percent: float = Field(default=0.0030, ge=0)
    maximum_trend_separation_percent: float = Field(default=0.0040, ge=0)
    minimum_room_to_vwap_atr: float = Field(default=0.35, ge=0)
    reversal_close_fraction: float = Field(default=0.45, ge=0, le=1)


class RsiMeanReversionSettings(WcaModuleSettings):
    lifecycle: Literal["active", "shadow", "disabled", "unavailable", "not_data_ready"] = "active"
    rsi_period: int = Field(default=14, ge=2)
    oversold_threshold: float = Field(default=30.0, ge=0, le=50)
    overbought_threshold: float = Field(default=70.0, ge=50, le=100)
    confirmation_lookback: int = Field(default=2, ge=1)
    maximum_trend_separation_percent: float = Field(default=0.0200, ge=0)


class BollingerAtrReversionSettings(WcaModuleSettings):
    lifecycle: Literal["active", "shadow", "disabled", "unavailable", "not_data_ready"] = "active"
    bollinger_period: int = Field(default=20, ge=5)
    bollinger_stddev: float = Field(default=2.0, gt=0)
    atr_period: int = Field(default=14, ge=2)
    minimum_atr_extension: float = Field(default=0.35, ge=0)
    reversal_close_fraction: float = Field(default=0.45, ge=0, le=1)
    directional_expansion_atr_multiple: float = Field(default=2.0, ge=0)


class OpeningRangeBreakoutSettings(WcaModuleSettings):
    lifecycle: Literal["active", "shadow", "disabled", "unavailable", "not_data_ready"] = "active"
    opening_range_minutes: int = Field(default=15, ge=5)
    evaluation_end_minutes: int = Field(default=10 * 60 + 30, ge=0)
    close_buffer_percent: float = Field(default=0.0005, ge=0)
    accepted_price_fraction: float = Field(default=0.60, ge=0, le=1)
    volume_expansion_ratio: float = Field(default=1.15, ge=0)
    false_breakout_wick_fraction: float = Field(default=0.45, ge=0, le=1)


class IntradayVolatilityBreakoutSettings(WcaModuleSettings):
    lifecycle: Literal["active", "shadow", "disabled", "unavailable", "not_data_ready"] = "active"
    reference_lookback: int = Field(default=20, ge=5)
    compression_lookback: int = Field(default=10, ge=3)
    compression_ratio: float = Field(default=0.85, ge=0)
    expansion_ratio: float = Field(default=1.25, ge=0)
    volume_expansion_ratio: float = Field(default=1.10, ge=0)
    minimum_expected_edge_after_costs: float = Field(default=0.0, ge=0)


class FailedBreakoutReversalSettings(WcaModuleSettings):
    lifecycle: Literal["active", "shadow", "disabled", "unavailable", "not_data_ready"] = "active"
    reference_lookback: int = Field(default=21, ge=5)
    minimum_break_percent: float = Field(default=0.0005, ge=0)
    close_back_inside_buffer_percent: float = Field(default=0.0002, ge=0)
    reversal_close_fraction: float = Field(default=0.45, ge=0, le=1)
    confirmation_volume_ratio: float = Field(default=0.85, ge=0)


class LiquiditySweepReversalSettings(WcaModuleSettings):
    lifecycle: Literal["active", "shadow", "disabled", "unavailable", "not_data_ready"] = "active"
    reference_lookback: int = Field(default=21, ge=5)
    minimum_sweep_percent: float = Field(default=0.0005, ge=0)
    rejection_wick_fraction: float = Field(default=0.35, ge=0, le=1)
    volume_expansion_ratio: float = Field(default=1.20, ge=0)
    follow_through_close_fraction: float = Field(default=0.45, ge=0, le=1)


class GapContinuationFadeSettings(WcaModuleSettings):
    lifecycle: Literal["active", "shadow", "disabled", "unavailable", "not_data_ready"] = "active"
    opening_range_minutes: int = Field(default=15, ge=5)
    minimum_gap_percent: float = Field(default=0.0020, ge=0)
    volume_expansion_ratio: float = Field(default=1.10, ge=0)
    continuation_buffer_percent: float = Field(default=0.0005, ge=0)
    fade_reclaim_buffer_percent: float = Field(default=0.0005, ge=0)
    maximum_event_risk_reason_codes: tuple[str, ...] = ("economic_event_risk", "event_risk.blocked")


class VwapPositionSettings(WcaModuleSettings):
    lifecycle: Literal["active"] = "active"
    neutral_band_percent: float = Field(default=0.0020, ge=0)
    supportive_multiplier: float = Field(default=1.05, ge=0)
    defensive_multiplier: float = Field(default=0.95, ge=0)


class VolumeConfirmationSettings(WcaModuleSettings):
    lifecycle: Literal["active"] = "active"
    lookback_bars: int = Field(default=20, ge=2)
    expanded_volume_ratio: float = Field(default=1.20, ge=0)
    thin_volume_ratio: float = Field(default=0.70, ge=0)


class MacdMomentumSettings(WcaModuleSettings):
    lifecycle: Literal["active"] = "active"
    fast_period: int = Field(default=12, ge=2)
    slow_period: int = Field(default=26, ge=3)
    neutral_band_percent: float = Field(default=0.0010, ge=0)


class MarketStructureSettings(WcaModuleSettings):
    lifecycle: Literal["active"] = "active"
    lookback_bars: int = Field(default=20, ge=5)
    breakout_multiplier: float = Field(default=1.05, ge=0)
    breakdown_multiplier: float = Field(default=0.95, ge=0)


class AdxTrendStrengthSettings(WcaModuleSettings):
    lifecycle: Literal["active"] = "active"
    short_period: int = Field(default=10, ge=2)
    long_period: int = Field(default=20, ge=3)
    strong_threshold_percent: float = Field(default=0.0040, ge=0)
    weak_threshold_percent: float = Field(default=0.0010, ge=0)

    @model_validator(mode="after")
    def validate_trend_thresholds(self) -> "AdxTrendStrengthSettings":
        if self.weak_threshold_percent > self.strong_threshold_percent:
            raise ValueError("weak trend threshold cannot exceed strong trend threshold")
        return self


class AtrVolatilityRegimeSettings(WcaModuleSettings):
    lifecycle: Literal["active"] = "active"
    atr_period: int = Field(default=14, ge=2)
    very_low_atr_percent: float = Field(default=0.0010, ge=0)
    high_atr_percent: float = Field(default=0.0060, ge=0)
    extreme_atr_percent: float = Field(default=0.0120, ge=0)

    @model_validator(mode="after")
    def validate_volatility_thresholds(self) -> "AtrVolatilityRegimeSettings":
        if not self.very_low_atr_percent <= self.high_atr_percent <= self.extreme_atr_percent:
            raise ValueError("ATR volatility thresholds must be ordered very_low <= high <= extreme")
        return self


class MultiTimeframeTrendAlignmentSettings(WcaModuleSettings):
    lifecycle: Literal["active"] = "active"
    short_period: int = Field(default=10, ge=2)
    medium_period: int = Field(default=20, ge=3)
    long_period: int = Field(default=50, ge=5)

    @model_validator(mode="after")
    def validate_timeframe_periods(self) -> "MultiTimeframeTrendAlignmentSettings":
        if not self.short_period < self.medium_period < self.long_period:
            raise ValueError("trend-alignment periods must be ordered short < medium < long")
        return self


class RelativeStrengthVsQqqIwmSettings(WcaModuleSettings):
    lifecycle: Literal["active"] = "active"
    qqq_symbol: str = "QQQ"
    iwm_symbol: str = "IWM"
    lookback_bars: int = Field(default=20, ge=2)
    stale_after_seconds: int = Field(default=120, ge=1)
    supportive_relative_strength_percent: float = Field(default=0.0010, ge=0)
    weak_relative_strength_percent: float = Field(default=0.0010, ge=0)


class MarketBreadthSettings(WcaModuleSettings):
    lifecycle: Literal["active"] = "active"
    advancers_key: str = "advancers"
    decliners_key: str = "decliners"
    up_volume_key: str = "up_volume"
    down_volume_key: str = "down_volume"
    stale_after_seconds: int = Field(default=120, ge=1)
    supportive_breadth_threshold: float = Field(default=0.60, ge=0, le=1)
    weak_breadth_threshold: float = Field(default=0.40, ge=0, le=1)

    @model_validator(mode="after")
    def validate_breadth_thresholds(self) -> "MarketBreadthSettings":
        if self.weak_breadth_threshold > self.supportive_breadth_threshold:
            raise ValueError("weak breadth threshold cannot exceed supportive breadth threshold")
        return self


class SessionPhaseSettings(WcaModuleSettings):
    lifecycle: Literal["active"] = "active"
    regular_open_minutes: int = Field(default=9 * 60 + 30, ge=0)
    opening_defensive_until_minutes: int = Field(default=10 * 60, ge=0)
    midday_start_minutes: int = Field(default=11 * 60 + 30, ge=0)
    afternoon_start_minutes: int = Field(default=13 * 60 + 30, ge=0)
    closing_defensive_start_minutes: int = Field(default=15 * 60 + 30, ge=0)

    @model_validator(mode="after")
    def validate_session_ordering(self) -> "SessionPhaseSettings":
        if not self.regular_open_minutes <= self.opening_defensive_until_minutes <= self.midday_start_minutes <= self.afternoon_start_minutes <= self.closing_defensive_start_minutes:
            raise ValueError("session phase minutes must be ordered from open to close")
        return self


class SpreadLiquiditySettings(WcaModuleSettings):
    lifecycle: Literal["active"] = "active"
    thin_average_volume: float = Field(default=50000, ge=0)
    unsafe_average_volume: float = Field(default=10000, ge=0)
    thin_spread_percent: float = Field(default=0.0008, ge=0)
    unsafe_spread_percent: float = Field(default=0.0020, ge=0)
    deep_average_volume: float = Field(default=250000, ge=0)
    deep_spread_percent: float = Field(default=0.0002, ge=0)

    @model_validator(mode="after")
    def validate_spread_liquidity_thresholds(self) -> "SpreadLiquiditySettings":
        if self.unsafe_average_volume > self.thin_average_volume:
            raise ValueError("unsafe average volume cannot exceed thin average volume")
        if self.thin_average_volume > self.deep_average_volume:
            raise ValueError("thin average volume cannot exceed deep average volume")
        if self.deep_spread_percent > self.thin_spread_percent or self.thin_spread_percent > self.unsafe_spread_percent:
            raise ValueError("spread thresholds must be ordered deep <= thin <= unsafe")
        return self


class CashAvoidTradingSettings(WcaModuleSettings):
    enabled: bool = False
    minimum_remaining_risk_budget: float = Field(default=0, ge=0)


class EconomicEventRiskSettings(WcaModuleSettings):
    enabled: bool = False
    blocking_reason_codes: tuple[str, ...] = ("economic_event_risk", "event_risk.blocked", "economic_event.blackout")


class InvalidOrStaleDataSettings(WcaModuleSettings):
    enabled: bool = True
    stale_after_seconds: int = Field(default=120, ge=1)


class UnsafeSpreadSettings(WcaModuleSettings):
    enabled: bool = True
    maximum_spread_percent: float = Field(default=0.0010, ge=0)
    reduction_spread_percent: float = Field(default=0.0007, ge=0)
    reduction_multiplier: float = Field(default=0.50, ge=0, le=1)

    @model_validator(mode="after")
    def validate_spread_thresholds(self) -> "UnsafeSpreadSettings":
        if self.reduction_spread_percent > self.maximum_spread_percent:
            raise ValueError("spread reduction threshold cannot exceed maximum spread threshold")
        return self


class UnsafeLiquiditySettings(WcaModuleSettings):
    enabled: bool = True
    minimum_average_volume: float = Field(default=10000, ge=0)
    reduction_average_volume: float = Field(default=25000, ge=0)
    reduction_multiplier: float = Field(default=0.50, ge=0, le=1)

    @model_validator(mode="after")
    def validate_liquidity_thresholds(self) -> "UnsafeLiquiditySettings":
        if self.minimum_average_volume > self.reduction_average_volume:
            raise ValueError("minimum average volume cannot exceed reduction average volume")
        return self


class ExtremeVolatilitySettings(WcaModuleSettings):
    enabled: bool = True
    atr_period: int = Field(default=14, ge=2)
    maximum_atr_percent: float = Field(default=0.0120, ge=0)
    reduction_atr_percent: float = Field(default=0.0080, ge=0)
    reduction_multiplier: float = Field(default=0.50, ge=0, le=1)

    @model_validator(mode="after")
    def validate_extreme_volatility_thresholds(self) -> "ExtremeVolatilitySettings":
        if self.reduction_atr_percent > self.maximum_atr_percent:
            raise ValueError("ATR reduction threshold cannot exceed maximum ATR threshold")
        return self


class SessionEntryBlockSettings(WcaModuleSettings):
    enabled: bool = True
    entry_start_minutes: int = Field(default=9 * 60 + 30, ge=0)
    entry_cutoff_minutes: int = Field(default=15 * 60 + 30, ge=0)

    @model_validator(mode="after")
    def validate_entry_session(self) -> "SessionEntryBlockSettings":
        if self.entry_start_minutes > self.entry_cutoff_minutes:
            raise ValueError("entry session start cannot be after entry cutoff")
        return self


class WcaPrimaryStrategySettings(WcaContractModel):
    moving_average_trend: MovingAverageTrendSettings = Field(default_factory=MovingAverageTrendSettings)
    first_pullback_after_open: FirstPullbackAfterOpenSettings = Field(default_factory=FirstPullbackAfterOpenSettings)
    vwap_trend_continuation: VwapTrendContinuationSettings = Field(default_factory=VwapTrendContinuationSettings)
    vwap_mean_reversion: VwapMeanReversionSettings = Field(default_factory=VwapMeanReversionSettings)
    rsi_mean_reversion: RsiMeanReversionSettings = Field(default_factory=RsiMeanReversionSettings)
    bollinger_atr_reversion: BollingerAtrReversionSettings = Field(default_factory=BollingerAtrReversionSettings)
    opening_range_breakout: OpeningRangeBreakoutSettings = Field(default_factory=OpeningRangeBreakoutSettings)
    intraday_volatility_breakout: IntradayVolatilityBreakoutSettings = Field(default_factory=IntradayVolatilityBreakoutSettings)
    failed_breakout_reversal: FailedBreakoutReversalSettings = Field(default_factory=FailedBreakoutReversalSettings)
    liquidity_sweep_reversal: LiquiditySweepReversalSettings = Field(default_factory=LiquiditySweepReversalSettings)
    gap_continuation_fade: GapContinuationFadeSettings = Field(default_factory=GapContinuationFadeSettings)


class WcaModifierSettings(WcaContractModel):
    vwap_position: VwapPositionSettings = Field(default_factory=VwapPositionSettings)
    volume_confirmation: VolumeConfirmationSettings = Field(default_factory=VolumeConfirmationSettings)
    macd_momentum: MacdMomentumSettings = Field(default_factory=MacdMomentumSettings)
    market_structure: MarketStructureSettings = Field(default_factory=MarketStructureSettings)
    adx_trend_strength: AdxTrendStrengthSettings = Field(default_factory=AdxTrendStrengthSettings)
    atr_volatility_regime: AtrVolatilityRegimeSettings = Field(default_factory=AtrVolatilityRegimeSettings)
    multi_timeframe_trend_alignment: MultiTimeframeTrendAlignmentSettings = Field(default_factory=MultiTimeframeTrendAlignmentSettings)
    relative_strength_vs_qqq_iwm: RelativeStrengthVsQqqIwmSettings = Field(default_factory=RelativeStrengthVsQqqIwmSettings)
    market_breadth: MarketBreadthSettings = Field(default_factory=MarketBreadthSettings)
    session_phase: SessionPhaseSettings = Field(default_factory=SessionPhaseSettings)
    spread_liquidity: SpreadLiquiditySettings = Field(default_factory=SpreadLiquiditySettings)


class WcaHardFilterSettings(WcaContractModel):
    cash_avoid_trading: CashAvoidTradingSettings = Field(default_factory=CashAvoidTradingSettings)
    economic_event_risk: EconomicEventRiskSettings = Field(default_factory=EconomicEventRiskSettings)
    invalid_or_stale_data: InvalidOrStaleDataSettings = Field(default_factory=InvalidOrStaleDataSettings)
    unsafe_spread: UnsafeSpreadSettings = Field(default_factory=UnsafeSpreadSettings)
    unsafe_liquidity: UnsafeLiquiditySettings = Field(default_factory=UnsafeLiquiditySettings)
    extreme_volatility: ExtremeVolatilitySettings = Field(default_factory=ExtremeVolatilitySettings)
    session_entry_block: SessionEntryBlockSettings = Field(default_factory=SessionEntryBlockSettings)


WCA_PRIMARY_STRATEGY_SETTINGS_MODELS: dict[str, type[WcaModuleSettings]] = {
    "moving_average_trend": MovingAverageTrendSettings,
    "first_pullback_after_open": FirstPullbackAfterOpenSettings,
    "vwap_trend_continuation": VwapTrendContinuationSettings,
    "vwap_mean_reversion": VwapMeanReversionSettings,
    "rsi_mean_reversion": RsiMeanReversionSettings,
    "bollinger_atr_reversion": BollingerAtrReversionSettings,
    "opening_range_breakout": OpeningRangeBreakoutSettings,
    "intraday_volatility_breakout": IntradayVolatilityBreakoutSettings,
    "failed_breakout_reversal": FailedBreakoutReversalSettings,
    "liquidity_sweep_reversal": LiquiditySweepReversalSettings,
    "gap_continuation_fade": GapContinuationFadeSettings,
}
WCA_MODIFIER_SETTINGS_MODELS: dict[str, type[WcaModuleSettings]] = {
    "vwap_position": VwapPositionSettings,
    "volume_confirmation": VolumeConfirmationSettings,
    "macd_momentum": MacdMomentumSettings,
    "market_structure": MarketStructureSettings,
    "adx_trend_strength": AdxTrendStrengthSettings,
    "atr_volatility_regime": AtrVolatilityRegimeSettings,
    "multi_timeframe_trend_alignment": MultiTimeframeTrendAlignmentSettings,
    "relative_strength_vs_qqq_iwm": RelativeStrengthVsQqqIwmSettings,
    "market_breadth": MarketBreadthSettings,
    "session_phase": SessionPhaseSettings,
    "spread_liquidity": SpreadLiquiditySettings,
}
WCA_HARD_FILTER_SETTINGS_MODELS: dict[str, type[WcaModuleSettings]] = {
    "cash_avoid_trading": CashAvoidTradingSettings,
    "economic_event_risk": EconomicEventRiskSettings,
    "invalid_or_stale_data": InvalidOrStaleDataSettings,
    "unsafe_spread": UnsafeSpreadSettings,
    "unsafe_liquidity": UnsafeLiquiditySettings,
    "extreme_volatility": ExtremeVolatilitySettings,
    "session_entry_block": SessionEntryBlockSettings,
}


class WcaConfiguration(WcaContractModel):
    algorithm_id: str = WCA_ALGORITHM_ID
    configuration_id: str = Field(default_factory=lambda: f"wca-config-{uuid4().hex}", min_length=1)
    configuration_version: str = WCA_CONFIGURATION_VERSION
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    activation_timestamp: datetime | None = None
    content_hash: str = ""
    schema_version: str = WCA_CONFIGURATION_SCHEMA_VERSION
    creator: str = "system"
    source: str = "canonical_default"
    lifecycle: WcaConfigurationLifecycle = WcaConfigurationLifecycle.CANDIDATE
    aggregation: WcaAggregationSettings = Field(default_factory=WcaAggregationSettings)
    risk: WcaRiskSettings = Field(default_factory=WcaRiskSettings)
    sizing: WcaSizingSettings = Field(default_factory=WcaSizingSettings)
    execution: WcaExecutionSettings = Field(default_factory=WcaExecutionSettings)
    exit: WcaExitSettings = Field(default_factory=WcaExitSettings)
    dynamic_profile: WcaDynamicProfileSettings = Field(default_factory=WcaDynamicProfileSettings)
    calibration: WcaCalibrationSettings = Field(default_factory=WcaCalibrationSettings)
    weights: WcaWeightSettings = Field(default_factory=WcaWeightSettings)
    runtime: WcaRuntimeSettings = Field(default_factory=WcaRuntimeSettings)
    limited_automatic_paper: WcaLimitedAutomaticPaperSettings = Field(default_factory=WcaLimitedAutomaticPaperSettings)
    local_paper: WcaLocalPaperSettings = Field(default_factory=WcaLocalPaperSettings)
    primary_strategy_settings: WcaPrimaryStrategySettings = Field(default_factory=WcaPrimaryStrategySettings)
    modifier_settings: WcaModifierSettings = Field(default_factory=WcaModifierSettings)
    hard_filter_settings: WcaHardFilterSettings = Field(default_factory=WcaHardFilterSettings)

    @model_validator(mode="after")
    def validate_configuration(self) -> "WcaConfiguration":
        if self.algorithm_id != WCA_ALGORITHM_ID:
            raise ValueError("WCA configuration cannot target another algorithm")
        _validate_catalog_settings_alignment(self)
        expected_hash = content_hash_for_configuration(self)
        if self.content_hash and self.content_hash != expected_hash:
            raise ValueError("content_hash does not match canonical configuration content")
        object.__setattr__(self, "content_hash", expected_hash)
        return self

    def with_lifecycle(
        self,
        lifecycle: WcaConfigurationLifecycle,
        *,
        activation_timestamp: datetime | None = None,
        configuration_version: str | None = None,
    ) -> "WcaConfiguration":
        payload = self.model_dump(mode="python")
        payload["lifecycle"] = lifecycle
        if activation_timestamp is not None:
            payload["activation_timestamp"] = activation_timestamp
        if configuration_version is not None:
            payload["configuration_version"] = configuration_version
        payload["content_hash"] = ""
        return WcaConfiguration.model_validate(payload)

    def to_baseline_settings(self) -> WcaBaselineSettings:
        runtime_mode = coerce_wca_runtime_mode(self.runtime.runtime_mode)
        limited_active = runtime_mode in {WcaRuntimeMode.LOCAL_AUTOMATIC_PAPER, WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER} and self.limited_automatic_paper.enabled
        controls = self.limited_automatic_paper
        return WcaBaselineSettings(
            settings_version=f"{self.configuration_version}:{self.content_hash[:12]}",
            created_at=self.created_at,
            minimum_score=self.aggregation.minimum_score,
            strong_buy_threshold=self.aggregation.strong_buy_threshold,
            buy_threshold=self.aggregation.buy_threshold,
            sell_threshold=self.aggregation.sell_threshold,
            strong_sell_threshold=self.aggregation.strong_sell_threshold,
            minimum_active_strategies=self.aggregation.minimum_active_strategies,
            minimum_directional_agreement=self.aggregation.minimum_directional_agreement,
            minimum_average_confidence=self.aggregation.minimum_average_confidence,
            base_risk_percent=self.risk.base_risk_percent,
            max_daily_loss_percent=self.risk.max_daily_loss_percent,
            max_daily_loss_dollars=controls.max_daily_loss_dollars if limited_active else None,
            max_daily_trades=self.risk.max_daily_trades,
            order_allocation_percent=self.sizing.order_allocation_percent,
            daily_allocation_percent=self.sizing.daily_allocation_percent,
            max_position_percent=self.sizing.max_position_percent,
            max_participation_percent=self.sizing.max_participation_percent,
            max_allowed_shares=self.sizing.max_allowed_shares,
            entry_windows=controls.entry_windows if limited_active else (),
            permitted_strategy_ids=controls.permitted_strategy_ids if limited_active else tuple(entry.strategy_id for entry in WCA_STRATEGY_REGISTRY),
            permitted_order_types=controls.permitted_order_types if limited_active else ("LIMIT", "STOP_LIMIT"),
            rollout_stage=controls.rollout_stage if limited_active else _enum_value(self.runtime.runtime_mode),
            broker_account_id=controls.broker_account_id,
            configured_fee_per_share=self.execution.configured_fee_per_share,
            market_impact_bps=self.execution.market_impact_bps,
            adverse_selection_bps=self.execution.adverse_selection_bps,
            replacement_cost_bps=self.execution.replacement_cost_bps,
            observed_slippage_per_share=self.execution.observed_slippage_per_share,
            uncertainty_buffer_per_share=self.execution.uncertainty_buffer_per_share,
            minimum_net_edge_per_share=self.execution.minimum_net_edge_per_share,
            atr_stop_multiplier=self.exit.atr_stop_multiplier,
            minimum_stop_distance_percent=self.exit.minimum_stop_distance_percent,
            take_profit_r=self.exit.take_profit_r,
            assumed_slippage_per_share=self.exit.assumed_slippage_per_share,
            cooldown_seconds=self.execution.cooldown_seconds,
            entry_cutoff_minutes=self.execution.entry_cutoff_minutes,
            pyramiding_enabled=self.execution.pyramiding_enabled,
            max_spread_percent=self.execution.max_spread_percent,
            hard_max_risk_percent=self.risk.hard_max_risk_percent,
            hard_max_daily_loss_percent=self.risk.hard_max_daily_loss_percent,
            hard_max_order_allocation_percent=self.sizing.hard_max_order_allocation_percent,
            hard_max_daily_allocation_percent=self.sizing.hard_max_daily_allocation_percent,
            hard_max_position_percent=self.sizing.hard_max_position_percent,
            hard_max_allowed_shares=self.sizing.hard_max_allowed_shares,
        )

    def for_runtime_mode(self, runtime_mode: WcaRuntimeMode | str) -> "WcaConfiguration":
        mode = coerce_wca_runtime_mode(runtime_mode)
        if mode not in {WcaRuntimeMode.LOCAL_AUTOMATIC_PAPER, WcaRuntimeMode.LIMITED_AUTOMATIC_PAPER} or not self.limited_automatic_paper.enabled:
            return self
        controls = self.limited_automatic_paper
        payload = self.model_dump(mode="python")
        payload["sizing"] = {
            **payload["sizing"],
            "max_allowed_shares": min(_positive_or_unlimited(self.sizing.max_allowed_shares, controls.max_quantity), controls.max_quantity),
            "hard_max_allowed_shares": min(_positive_or_unlimited(self.sizing.hard_max_allowed_shares, controls.max_quantity), controls.max_quantity),
        }
        payload["risk"] = {
            **payload["risk"],
            "max_daily_trades": min(self.risk.max_daily_trades, controls.max_daily_trades),
            "hard_max_daily_trades": min(self.risk.hard_max_daily_trades, controls.max_daily_trades),
        }
        payload["runtime"] = {**payload["runtime"], "runtime_mode": mode.value}
        payload["content_hash"] = ""
        return WcaConfiguration.model_validate(payload)


def content_hash_for_configuration(configuration: WcaConfiguration | dict[str, Any]) -> str:
    if isinstance(configuration, WcaConfiguration):
        payload = configuration.model_dump(mode="json")
    else:
        payload = dict(configuration)
    payload.pop("content_hash", None)
    payload.pop("activation_timestamp", None)
    payload.pop("lifecycle", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_wca_configuration(configuration: WcaConfiguration | dict[str, Any]) -> WcaConfiguration:
    if isinstance(configuration, WcaConfiguration):
        return WcaConfiguration.model_validate(configuration.model_dump(mode="python"))
    return WcaConfiguration.model_validate(configuration)


def canonical_configuration_from_legacy(
    decision_settings: WcaDecisionSettings | dict[str, Any] | None = None,
    trading_settings: WcaTradingSettings | dict[str, Any] | None = None,
    *,
    configuration_id: str | None = None,
    configuration_version: str = WCA_CONFIGURATION_VERSION,
    creator: str = "system",
    source: str = "legacy_migration",
    lifecycle: WcaConfigurationLifecycle = WcaConfigurationLifecycle.CANDIDATE,
    activation_timestamp: datetime | None = None,
) -> WcaConfiguration:
    decision = WcaDecisionSettings.model_validate(decision_settings or {})
    trading = WcaTradingSettings.model_validate(trading_settings or {})
    return WcaConfiguration(
        configuration_id=configuration_id or f"wca-config-{configuration_version}",
        configuration_version=configuration_version,
        activation_timestamp=activation_timestamp,
        creator=creator,
        source=source,
        lifecycle=lifecycle,
        aggregation=WcaAggregationSettings(
            minimum_score=min(abs(decision.buy_threshold), abs(decision.sell_threshold)),
            strong_buy_threshold=decision.strong_buy_threshold,
            buy_threshold=decision.buy_threshold,
            sell_threshold=decision.sell_threshold,
            strong_sell_threshold=decision.strong_sell_threshold,
            minimum_active_strategies=decision.minimum_active_strategies,
            minimum_directional_agreement=decision.minimum_directional_agreement,
            minimum_average_confidence=decision.minimum_average_confidence,
        ),
        risk=WcaRiskSettings(
            base_risk_percent=trading.base_risk_percent,
            max_daily_loss_percent=trading.max_daily_loss_percent,
            max_daily_trades=trading.max_daily_trades,
            hard_max_risk_percent=max(trading.base_risk_percent, 1.0),
            hard_max_daily_loss_percent=max(trading.max_daily_loss_percent, 3.0),
            hard_max_daily_trades=max(trading.max_daily_trades, 5),
        ),
        sizing=WcaSizingSettings(
            order_allocation_percent=trading.order_allocation_percent,
            daily_allocation_percent=trading.daily_allocation_percent,
            max_position_percent=trading.max_position_percent,
            max_participation_percent=trading.max_participation_percent,
            max_allowed_shares=trading.max_allowed_shares,
            hard_max_order_allocation_percent=max(trading.order_allocation_percent, 10.0),
            hard_max_daily_allocation_percent=max(trading.daily_allocation_percent, 20.0),
            hard_max_position_percent=max(trading.max_position_percent, 10.0),
            hard_max_participation_percent=max(trading.max_participation_percent, 1.0),
            hard_max_allowed_shares=trading.max_allowed_shares,
        ),
        execution=WcaExecutionSettings(
            pyramiding_enabled=trading.pyramiding_enabled,
            max_spread_percent=trading.max_spread_percent,
            minimum_one_minute_volume=trading.minimum_one_minute_volume,
        ),
        exit=WcaExitSettings(
            atr_stop_multiplier=trading.atr_stop_multiplier,
            minimum_stop_distance_percent=trading.minimum_stop_distance_percent,
            take_profit_r=trading.take_profit_r,
            fixed_stop_distance_dollars=trading.fixed_stop_distance_dollars,
            assumed_slippage_per_share=trading.slippage_per_share,
        ),
    )


def default_wca_configuration(
    *,
    lifecycle: WcaConfigurationLifecycle = WcaConfigurationLifecycle.ACTIVE,
    activation_timestamp: datetime | None = None,
) -> WcaConfiguration:
    trading = WcaTradingSettings(
        baseRiskPercent=1,
        maxPositionPercent=10,
        maxDailyTrades=5,
        maxSpreadPercent=0.1,
        maxParticipationPercent=1,
    )
    return canonical_configuration_from_legacy(
        WcaDecisionSettings(),
        trading,
        configuration_version=WCA_CONFIGURATION_VERSION,
        creator="system",
        source="canonical_default",
        lifecycle=lifecycle,
        activation_timestamp=activation_timestamp or datetime.now(timezone.utc) if lifecycle == WcaConfigurationLifecycle.ACTIVE else activation_timestamp,
    )


def baseline_from_legacy_request(decision: WcaDecisionSettings, trading: WcaTradingSettings) -> WcaBaselineSettings:
    return canonical_configuration_from_legacy(decision, trading, source="api_compatibility_boundary").to_baseline_settings()


def validate_baseline_settings(values: WcaBaselineSettings | dict[str, Any]) -> WcaBaselineSettings:
    return WcaBaselineSettings.model_validate(values)


def default_baseline_settings() -> WcaBaselineSettings:
    raise WcaConfigurationUnavailable("wca.configuration.missing_active_revision: runtime code must load an active WCA configuration")


def default_effective_settings(configuration: WcaConfiguration | None = None) -> WcaEffectiveSettings:
    if configuration is None:
        raise WcaConfigurationUnavailable("wca.configuration.missing_active_revision: no active WCA configuration supplied")
    baseline = configuration.to_baseline_settings()
    profile = resolve_dynamic_profile(
        baseline=baseline,
        market_status=WcaMarketStatus(status=WcaEvaluationStatus.ACTIVE),
        calculation_timestamp=datetime.now(timezone.utc),
        config=WcaDynamicProfileConfig(enabled=False),
    )
    return profile.effective_settings


def _validate_catalog_settings_alignment(configuration: WcaConfiguration) -> None:
    primary = set(configuration.primary_strategy_settings.model_dump())
    modifiers = set(configuration.modifier_settings.model_dump())
    filters = set(configuration.hard_filter_settings.model_dump())
    expected_primary = {entry.slug for entry in WCA_STRATEGY_REGISTRY}
    expected_modifiers = {entry.slug for entry in WCA_MODIFIER_REGISTRY}
    expected_filters = {entry.slug for entry in WCA_HARD_FILTER_REGISTRY}
    if primary != expected_primary:
        raise ValueError("primary_strategy_settings must match the authoritative WCA primary catalog")
    if modifiers != expected_modifiers:
        raise ValueError("modifier_settings must match the authoritative WCA modifier catalog")
    if filters != expected_filters:
        raise ValueError("hard_filter_settings must match the authoritative WCA hard-filter catalog")


def _validate_entry_window(window: str) -> None:
    try:
        times, timezone_name = window.rsplit(" ", 1)
        start, end = times.split("-", 1)
        start_hour, start_minute = (int(part) for part in start.split(":", 1))
        end_hour, end_minute = (int(part) for part in end.split(":", 1))
    except Exception as exc:
        raise ValueError("entry window must be 'HH:MM-HH:MM America/New_York'") from exc
    if timezone_name != "America/New_York":
        raise ValueError("WCA limited-paper entry windows must use America/New_York")
    if not (0 <= start_hour <= 23 and 0 <= end_hour <= 23 and 0 <= start_minute <= 59 and 0 <= end_minute <= 59):
        raise ValueError("entry window time is out of range")
    if (end_hour, end_minute) <= (start_hour, start_minute):
        raise ValueError("entry window end must be after start")


def _positive_or_unlimited(value: int, fallback: int) -> int:
    return value if value > 0 else fallback


def _enum_value(value: Any) -> str:
    return value.value if hasattr(value, "value") else str(value)
