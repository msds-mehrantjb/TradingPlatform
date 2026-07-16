"""Canonical immutable backend contracts for Weighted Voting."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


ALGORITHM_ID = "weighted_voting"
PROBABILITY_SUM_TOLERANCE = 1e-6


class WeightedContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    def deterministic_json(self) -> str:
        payload = self.model_dump(mode="json", exclude_none=True)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def deterministic_hash(self) -> str:
        return hashlib.sha256(self.deterministic_json().encode("utf-8")).hexdigest()


class WeightedSide(str, Enum):
    BUY = "Buy"
    SELL = "Sell"
    HOLD = "Hold"


class WeightedStrategyFamily(str, Enum):
    BREAKOUT = "breakout"
    TREND = "trend"
    MEAN_REVERSION = "mean_reversion"
    REVERSAL = "reversal"


class WeightedTrendDirection(str, Enum):
    STRONG_UPTREND = "strong_uptrend"
    WEAK_UPTREND = "weak_uptrend"
    SIDEWAYS = "sideways"
    WEAK_DOWNTREND = "weak_downtrend"
    STRONG_DOWNTREND = "strong_downtrend"
    UP = "up"
    DOWN = "down"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class WeightedVolatilityLevel(str, Enum):
    VERY_LOW = "very_low"
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"
    UNKNOWN = "unknown"


class WeightedRangeCondition(str, Enum):
    TRENDING = "trending"
    RANGE_BOUND = "range_bound"
    BREAKOUT = "breakout"
    CHOPPY = "choppy"
    UNKNOWN = "unknown"


class WeightedLiquidityLevel(str, Enum):
    GOOD = "good"
    REDUCED = "reduced"
    POOR = "poor"
    UNKNOWN = "unknown"


class WeightedSessionPhase(str, Enum):
    OPENING = "opening"
    MORNING = "morning"
    MIDDAY = "midday"
    AFTERNOON = "afternoon"
    CLOSING = "closing"
    OUTSIDE_SESSION = "outside_session"
    UNKNOWN = "unknown"


class WeightedEventRiskLevel(str, Enum):
    NONE = "none"
    ELEVATED = "elevated"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


class WeightedMarketQuality(str, Enum):
    CLEAN = "clean"
    MIXED = "mixed"
    UNSTABLE = "unstable"
    UNKNOWN = "unknown"


class WeightedDataQualityStatus(str, Enum):
    FULL = "full"
    DEGRADED = "degraded"
    PROXY = "proxy"
    UNAVAILABLE = "unavailable"


class WeightedGateStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    INFO = "info"


class WeightedOrderStatus(str, Enum):
    NO_ORDER = "no_order"
    PROPOSED = "proposed"
    REJECTED = "rejected"
    SUBMITTED_PAPER = "submitted_paper"
    FILLED_PAPER = "filled_paper"
    CANCELLED = "cancelled"


class WeightedExitReason(str, Enum):
    NONE = "none"
    TARGET_HIT = "target_hit"
    STOP_HIT = "stop_hit"
    TIME_EXIT = "time_exit"
    END_OF_DAY = "end_of_day"
    RISK_GATE = "risk_gate"
    MANUAL = "manual"


class WeightedBacktestStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class WeightedWeightStateStatus(str, Enum):
    UNSEEDED_EQUAL_WEIGHTS = "UNSEEDED_EQUAL_WEIGHTS"
    BACKTEST_SEEDED = "BACKTEST_SEEDED"
    LIVE_ADAPTING = "LIVE_ADAPTING"
    FROZEN_INSUFFICIENT_DATA = "FROZEN_INSUFFICIENT_DATA"
    VALIDATION_FAILED = "VALIDATION_FAILED"


class WeightedCandle(WeightedContractModel):
    contract_version: str = "weighted_candle_v1"
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_ohlc_geometry(self) -> WeightedCandle:
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError("candle OHLC geometry is invalid")
        return self


class WeightedMarketSnapshot(WeightedContractModel):
    contract_version: str = "weighted_market_snapshot_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    symbol: str = Field(min_length=1)
    data_timestamp: datetime
    one_minute_candles: tuple[WeightedCandle, ...]
    five_minute_candles: tuple[WeightedCandle, ...] = ()
    bid: float | None = Field(default=None, gt=0)
    ask: float | None = Field(default=None, gt=0)
    data_manifest_hash: str | None = None
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_quote(self) -> WeightedMarketSnapshot:
        if self.bid is not None and self.ask is not None and self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid")
        return self


class WeightedStrategySignal(WeightedContractModel):
    contract_version: str = "weighted_strategy_signal_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    strategy_id: str = Field(min_length=1)
    strategy_name: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    family: WeightedStrategyFamily
    signal: WeightedSide
    p_buy: float = Field(ge=0, le=1)
    p_sell: float = Field(ge=0, le=1)
    p_hold: float = Field(ge=0, le=1)
    directional_confidence: float = Field(default=0.0, ge=0, le=1)
    signal_strength: float = Field(default=0.0, ge=0)
    expected_raw_movement: float = 0.0
    expected_return: float = 0.0
    expected_return_after_costs: float = 0.0
    strength: float = Field(ge=0)
    final_weight: float = Field(ge=0, le=1)
    eligible: bool
    data_ready: bool
    required_data_freshness_seconds: float = Field(default=300.0, gt=0)
    actual_data_freshness_seconds: float | None = Field(default=None, ge=0)
    data_quality_status: WeightedDataQualityStatus = WeightedDataQualityStatus.UNAVAILABLE
    invalidation_level: float | None = Field(default=None, gt=0)
    data_timestamp: datetime
    reason_codes: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_probabilities_sum_to_one(self) -> WeightedStrategySignal:
        total = self.p_buy + self.p_sell + self.p_hold
        if abs(total - 1.0) > PROBABILITY_SUM_TOLERANCE:
            raise ValueError("strategy probabilities must sum to one")
        return self

    @model_validator(mode="after")
    def validate_unavailable_data_has_no_directional_contribution(self) -> WeightedStrategySignal:
        if self.data_quality_status == WeightedDataQualityStatus.UNAVAILABLE and (
            self.signal != WeightedSide.HOLD or self.p_buy != 0 or self.p_sell != 0 or self.p_hold != 1
        ):
            raise ValueError("unavailable data must produce Hold with zero directional contribution")
        return self


class WeightedStrategyOutcome(WeightedContractModel):
    contract_version: str = "weighted_strategy_outcome_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    strategy_id: str = Field(min_length=1)
    side: WeightedSide
    entry_timestamp: datetime
    exit_timestamp: datetime | None = None
    entry_price: float = Field(gt=0)
    exit_price: float | None = Field(default=None, gt=0)
    outcome_return: float | None = None
    exit_reason: WeightedExitReason = WeightedExitReason.NONE
    reason_codes: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)


class WeightedStrategyStatistics(WeightedContractModel):
    contract_version: str = "weighted_strategy_statistics_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    strategy_id: str = Field(min_length=1)
    sample_size: int = Field(ge=0)
    trade_count: int = Field(ge=0)
    win_rate: float | None = Field(default=None, ge=0, le=1)
    expectancy: float | None = None
    recent_expectancy: float | None = None
    max_drawdown: float = Field(default=0, ge=0)
    statistics_version: str = "weighted_strategy_statistics_v1"
    data_timestamp: datetime
    explanation: str = Field(min_length=1)


class WeightedPerformanceWeightMetric(WeightedContractModel):
    contract_version: str = "weighted_performance_weight_metric_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    strategy_id: str = Field(min_length=1)
    sample_size: int = Field(ge=0)
    net_expectancy_after_costs: float = 0.0
    profit_factor: float = Field(default=0.0, ge=0)
    average_win: float = Field(default=0.0, ge=0)
    average_loss: float = Field(default=0.0, ge=0)
    win_loss_ratio: float = Field(default=0.0, ge=0)
    maximum_drawdown: float = Field(default=0.0, ge=0)
    outcome_stability: float = Field(default=0.0, ge=0, le=1)
    recent_performance: float = 0.0
    regime_specific_performance: float = 0.0
    correlation_penalty: float = Field(default=1.0, ge=0, le=1)
    sample_shrinkage: float = Field(default=0.0, ge=0, le=1)
    raw_performance_score: float = Field(default=0.0, ge=0)
    candidate_weight: float = Field(default=0.0, ge=0, le=1)
    smoothed_weight: float = Field(default=0.0, ge=0, le=1)
    final_weight: float = Field(default=0.0, ge=0, le=1)
    reason_codes: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)


class WeightedWeightState(WeightedContractModel):
    contract_version: str = "weighted_weight_state_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    weight_version: str = "weighted_weights_v1"
    state_status: WeightedWeightStateStatus = WeightedWeightStateStatus.UNSEEDED_EQUAL_WEIGHTS
    strategy_weights: dict[str, float]
    active_session_date: str | None = None
    performance_metrics: tuple[WeightedPerformanceWeightMetric, ...] = ()
    last_updated_at: datetime
    data_timestamp: datetime
    reason_codes: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)

    @field_validator("strategy_weights")
    @classmethod
    def validate_strategy_weights(cls, value: dict[str, float]) -> dict[str, float]:
        if not value:
            raise ValueError("strategy_weights cannot be empty")
        if any(weight < 0 or weight > 1 for weight in value.values()):
            raise ValueError("strategy weights must be between zero and one")
        if abs(sum(value.values()) - 1.0) > PROBABILITY_SUM_TOLERANCE:
            raise ValueError("strategy weights must sum to one")
        return value


class WeightedWeightAdjustment(WeightedContractModel):
    contract_version: str = "weighted_weight_adjustment_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    strategy_id: str = Field(min_length=1)
    family: WeightedStrategyFamily
    original_frozen_weight: float = Field(ge=0, le=1)
    correlation_penalty: float = Field(ge=0, le=1)
    family_cap_adjustment: float = Field(ge=0, le=1)
    data_quality_adjustment: float = Field(ge=0, le=1)
    market_condition_adjustment: float = Field(ge=0, le=1)
    final_effective_weight: float = Field(ge=0, le=1)
    reason_codes: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)


class WeightedMarketCondition(WeightedContractModel):
    contract_version: str = "weighted_market_condition_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    trend_direction: WeightedTrendDirection
    volatility_level: WeightedVolatilityLevel
    range_condition: WeightedRangeCondition
    session_label: str = Field(min_length=1)
    liquidity_level: WeightedLiquidityLevel = WeightedLiquidityLevel.UNKNOWN
    session_phase: WeightedSessionPhase = WeightedSessionPhase.UNKNOWN
    event_risk: WeightedEventRiskLevel = WeightedEventRiskLevel.UNKNOWN
    market_quality: WeightedMarketQuality = WeightedMarketQuality.UNKNOWN
    confidence: float = Field(default=0.0, ge=0, le=1)
    condition_inputs: dict[str, float | str | bool | None] = Field(default_factory=dict)
    pending_condition_key: str | None = None
    pending_confirmation_count: int = Field(default=0, ge=0)
    data_ready: bool
    data_timestamp: datetime
    reason_codes: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)


class WeightedDefaultSettings(WeightedContractModel):
    contract_version: str = "weighted_default_settings_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    settings_version: str = "weighted_default_settings_v1"
    settings_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    base_risk_per_trade_percent: float = Field(default=0.50, ge=0, le=100)
    order_allocation_percent: float = Field(default=10.0, ge=0, le=100)
    daily_allocation_percent: float = Field(default=30.0, ge=0, le=100)
    maximum_position_percent: float = Field(default=10.0, ge=0, le=100)
    maximum_shares: int = Field(default=0, ge=0)
    maximum_trades: int = Field(default=10, ge=0)
    maximum_daily_loss_percent: float = Field(default=3.0, ge=0, le=100)
    maximum_participation_rate: float = Field(default=0.01, ge=0, le=1)
    minimum_score: float = Field(default=0.58, ge=0, le=1)
    minimum_edge: float = Field(default=0.12, ge=0, le=1)
    minimum_active_strategies: int = Field(default=1, ge=1)
    minimum_directional_strategies: int = Field(default=1, ge=1)
    maximum_spread_percent: float = Field(default=0.001, ge=0, le=1)
    minimum_liquidity_volume: float = Field(default=10000.0, ge=0)
    atr_stop_multiplier: float = Field(default=1.5, ge=0)
    minimum_stop_distance_percent: float = Field(default=0.001, ge=0, le=1)
    target_r: float = Field(default=2.0, ge=0)
    entry_buffer_percent: float = Field(default=0.0005, ge=0, le=1)
    break_even_trigger_r: float = Field(default=1.0, ge=0)
    trailing_stop_atr_multiplier: float = Field(default=1.0, ge=0)
    time_stop_minutes: int = Field(default=120, ge=0)
    session_cutoff_minutes: int = Field(default=15, ge=0)
    pyramiding_enabled: bool = False
    max_position_percent: float = Field(default=10.0, ge=0, le=100)
    max_daily_loss_percent: float = Field(default=3.0, ge=0, le=100)
    max_daily_trades: int = Field(default=10, ge=0)
    explanation: str = "Canonical default Weighted Voting settings."


class WeightedDynamicEnvelope(WeightedContractModel):
    contract_version: str = "weighted_dynamic_envelope_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    envelope_version: str = "weighted_dynamic_envelope_v1"
    settings_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    enabled: bool = False
    max_weight_adjustment: float = Field(default=0.0, ge=0, le=1)
    base_risk_per_trade_percent_delta: float = Field(default=0.0, ge=0)
    order_allocation_percent_delta: float = Field(default=0.0, ge=0)
    daily_allocation_percent_delta: float = Field(default=0.0, ge=0)
    maximum_position_percent_delta: float = Field(default=0.0, ge=0)
    maximum_shares_delta: int = Field(default=0, ge=0)
    maximum_trades_delta: int = Field(default=0, ge=0)
    maximum_daily_loss_percent_delta: float = Field(default=0.0, ge=0)
    maximum_participation_rate_delta: float = Field(default=0.0, ge=0)
    minimum_score_delta: float = Field(default=0.0, ge=0, le=1)
    minimum_edge_delta: float = Field(default=0.0, ge=0, le=1)
    minimum_active_strategies_delta: int = Field(default=0, ge=0)
    minimum_directional_strategies_delta: int = Field(default=0, ge=0)
    maximum_spread_percent_delta: float = Field(default=0.0, ge=0, le=1)
    minimum_liquidity_volume_delta: float = Field(default=0.0, ge=0)
    atr_stop_multiplier_delta: float = Field(default=0.0, ge=0)
    minimum_stop_distance_percent_delta: float = Field(default=0.0, ge=0, le=1)
    target_r_delta: float = Field(default=0.0, ge=0)
    entry_buffer_percent_delta: float = Field(default=0.0, ge=0, le=1)
    break_even_trigger_r_delta: float = Field(default=0.0, ge=0)
    trailing_stop_atr_multiplier_delta: float = Field(default=0.0, ge=0)
    time_stop_minutes_delta: int = Field(default=0, ge=0)
    session_cutoff_minutes_delta: int = Field(default=0, ge=0)
    pyramiding_may_enable: bool = False
    reason_codes: tuple[str, ...] = ()
    explanation: str = Field(default="Dynamic settings are deterministic and disabled by default.", min_length=1)


class WeightedHardLimits(WeightedContractModel):
    contract_version: str = "weighted_hard_limits_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    limits_version: str = "weighted_hard_limits_v1"
    settings_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    maximum_base_risk_per_trade_percent: float = Field(default=2.0, ge=0, le=100)
    maximum_order_allocation_percent: float = Field(default=25.0, ge=0, le=100)
    maximum_daily_allocation_percent: float = Field(default=50.0, ge=0, le=100)
    maximum_position_percent: float = Field(default=20.0, ge=0, le=100)
    maximum_shares: int = Field(default=1000000, ge=0)
    maximum_trades: int = Field(default=20, ge=0)
    maximum_daily_loss_percent: float = Field(default=5.0, ge=0, le=100)
    maximum_participation_rate: float = Field(default=0.05, ge=0, le=1)
    minimum_score_floor: float = Field(default=0.50, ge=0, le=1)
    minimum_score_ceiling: float = Field(default=0.95, ge=0, le=1)
    minimum_edge_floor: float = Field(default=0.02, ge=0, le=1)
    minimum_edge_ceiling: float = Field(default=0.40, ge=0, le=1)
    minimum_active_strategies_floor: int = Field(default=1, ge=1)
    minimum_directional_strategies_floor: int = Field(default=1, ge=1)
    maximum_spread_percent: float = Field(default=0.003, ge=0, le=1)
    minimum_liquidity_volume_floor: float = Field(default=0.0, ge=0)
    maximum_liquidity_volume_requirement: float = Field(default=10000000.0, ge=0)
    minimum_atr_stop_multiplier: float = Field(default=0.5, ge=0)
    maximum_atr_stop_multiplier: float = Field(default=5.0, ge=0)
    minimum_stop_distance_percent_floor: float = Field(default=0.0001, ge=0, le=1)
    maximum_stop_distance_percent: float = Field(default=0.05, ge=0, le=1)
    minimum_target_r: float = Field(default=0.5, ge=0)
    maximum_target_r: float = Field(default=5.0, ge=0)
    maximum_entry_buffer_percent: float = Field(default=0.01, ge=0, le=1)
    maximum_break_even_trigger_r: float = Field(default=3.0, ge=0)
    maximum_trailing_stop_atr_multiplier: float = Field(default=5.0, ge=0)
    maximum_time_stop_minutes: int = Field(default=390, ge=0)
    maximum_session_cutoff_minutes: int = Field(default=120, ge=0)
    pyramiding_allowed: bool = False
    max_order_quantity: int = Field(default=0, ge=0)
    max_notional: float = Field(default=0, ge=0)
    max_spread_percent: float = Field(default=0.1, ge=0)
    min_one_minute_volume: float = Field(default=0, ge=0)
    explanation: str = "Canonical Weighted Voting hard limits."

    @model_validator(mode="after")
    def validate_score_and_edge_ranges(self) -> WeightedHardLimits:
        if self.minimum_score_floor > self.minimum_score_ceiling:
            raise ValueError("minimum score floor cannot exceed ceiling")
        if self.minimum_edge_floor > self.minimum_edge_ceiling:
            raise ValueError("minimum edge floor cannot exceed ceiling")
        if self.minimum_atr_stop_multiplier > self.maximum_atr_stop_multiplier:
            raise ValueError("ATR stop multiplier floor cannot exceed ceiling")
        if self.minimum_target_r > self.maximum_target_r:
            raise ValueError("target R floor cannot exceed ceiling")
        return self


class WeightedDynamicSettingAdjustment(WeightedContractModel):
    contract_version: str = "weighted_dynamic_setting_adjustment_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    adjustment_category: str = Field(min_length=1)
    setting_name: str = Field(min_length=1)
    default_value: float | int | bool
    condition_multiplier: float = Field(ge=0)
    envelope_minimum: float | int
    envelope_maximum: float | int
    value_after_envelope: float | int
    algorithm_hard_limit: float | int | bool
    global_allowance: float | int | bool | None = None
    final_value: float | int | bool
    reason_codes: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)


class WeightedEffectiveSettings(WeightedContractModel):
    contract_version: str = "weighted_effective_settings_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    settings_version: str
    settings_timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    default_settings: WeightedDefaultSettings
    dynamic_envelope: WeightedDynamicEnvelope
    hard_limits: WeightedHardLimits
    base_risk_per_trade_percent: float = Field(default=0.50, ge=0, le=100)
    order_allocation_percent: float = Field(default=10.0, ge=0, le=100)
    daily_allocation_percent: float = Field(default=30.0, ge=0, le=100)
    maximum_position_percent: float = Field(default=10.0, ge=0, le=100)
    maximum_shares: int = Field(default=0, ge=0)
    maximum_trades: int = Field(default=10, ge=0)
    maximum_daily_loss_percent: float = Field(default=3.0, ge=0, le=100)
    maximum_participation_rate: float = Field(default=0.01, ge=0, le=1)
    minimum_score: float = Field(default=0.58, ge=0, le=1)
    minimum_edge: float = Field(default=0.12, ge=0, le=1)
    minimum_active_strategies: int = Field(default=1, ge=1)
    minimum_directional_strategies: int = Field(default=1, ge=1)
    maximum_spread_percent: float = Field(default=0.001, ge=0, le=1)
    minimum_liquidity_volume: float = Field(default=10000.0, ge=0)
    atr_stop_multiplier: float = Field(default=1.5, ge=0)
    minimum_stop_distance_percent: float = Field(default=0.001, ge=0, le=1)
    target_r: float = Field(default=2.0, ge=0)
    entry_buffer_percent: float = Field(default=0.0005, ge=0, le=1)
    break_even_trigger_r: float = Field(default=1.0, ge=0)
    trailing_stop_atr_multiplier: float = Field(default=1.0, ge=0)
    time_stop_minutes: int = Field(default=120, ge=0)
    session_cutoff_minutes: int = Field(default=15, ge=0)
    pyramiding_enabled: bool = False
    configuration_version: str
    configuration_hash: str = Field(min_length=1)
    dynamic_adjustments: tuple[WeightedDynamicSettingAdjustment, ...] = ()
    reason_codes: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_effective_settings_within_hard_limits(self) -> WeightedEffectiveSettings:
        limits = self.hard_limits
        violations = []
        checks = (
            (self.base_risk_per_trade_percent <= limits.maximum_base_risk_per_trade_percent, "base_risk_per_trade_percent exceeds hard limit"),
            (self.order_allocation_percent <= limits.maximum_order_allocation_percent, "order_allocation_percent exceeds hard limit"),
            (self.daily_allocation_percent <= limits.maximum_daily_allocation_percent, "daily_allocation_percent exceeds hard limit"),
            (self.maximum_position_percent <= limits.maximum_position_percent, "maximum_position_percent exceeds hard limit"),
            (self.maximum_shares <= limits.maximum_shares, "maximum_shares exceeds hard limit"),
            (self.maximum_trades <= limits.maximum_trades, "maximum_trades exceeds hard limit"),
            (self.maximum_daily_loss_percent <= limits.maximum_daily_loss_percent, "maximum_daily_loss_percent exceeds hard limit"),
            (self.maximum_participation_rate <= limits.maximum_participation_rate, "maximum_participation_rate exceeds hard limit"),
            (limits.minimum_score_floor <= self.minimum_score <= limits.minimum_score_ceiling, "minimum_score outside hard limits"),
            (limits.minimum_edge_floor <= self.minimum_edge <= limits.minimum_edge_ceiling, "minimum_edge outside hard limits"),
            (self.minimum_active_strategies >= limits.minimum_active_strategies_floor, "minimum_active_strategies below hard floor"),
            (self.minimum_directional_strategies >= limits.minimum_directional_strategies_floor, "minimum_directional_strategies below hard floor"),
            (self.maximum_spread_percent <= limits.maximum_spread_percent, "maximum_spread_percent exceeds hard limit"),
            (limits.minimum_liquidity_volume_floor <= self.minimum_liquidity_volume <= limits.maximum_liquidity_volume_requirement, "minimum_liquidity_volume outside hard limits"),
            (limits.minimum_atr_stop_multiplier <= self.atr_stop_multiplier <= limits.maximum_atr_stop_multiplier, "atr_stop_multiplier outside hard limits"),
            (limits.minimum_stop_distance_percent_floor <= self.minimum_stop_distance_percent <= limits.maximum_stop_distance_percent, "minimum_stop_distance_percent outside hard limits"),
            (limits.minimum_target_r <= self.target_r <= limits.maximum_target_r, "target_r outside hard limits"),
            (self.entry_buffer_percent <= limits.maximum_entry_buffer_percent, "entry_buffer_percent exceeds hard limit"),
            (self.break_even_trigger_r <= limits.maximum_break_even_trigger_r, "break_even_trigger_r exceeds hard limit"),
            (self.trailing_stop_atr_multiplier <= limits.maximum_trailing_stop_atr_multiplier, "trailing_stop_atr_multiplier exceeds hard limit"),
            (self.time_stop_minutes <= limits.maximum_time_stop_minutes, "time_stop_minutes exceeds hard limit"),
            (self.session_cutoff_minutes <= limits.maximum_session_cutoff_minutes, "session_cutoff_minutes exceeds hard limit"),
            (limits.pyramiding_allowed or not self.pyramiding_enabled, "pyramiding_enabled exceeds hard limit"),
        )
        for valid, message in checks:
            if not valid:
                violations.append(message)
        if violations:
            raise ValueError("; ".join(violations))
        return self


class WeightedVoteScores(WeightedContractModel):
    contract_version: str = "weighted_vote_scores_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    buy_score: float = Field(ge=0, le=1)
    sell_score: float = Field(ge=0, le=1)
    hold_score: float = Field(ge=0, le=1)
    normalized_buy_score: float = Field(default=0.0, ge=0, le=1)
    normalized_sell_score: float = Field(default=0.0, ge=0, le=1)
    normalized_hold_score: float = Field(default=0.0, ge=0, le=1)
    winning_side: WeightedSide = WeightedSide.HOLD
    winner_score: float = Field(default=0.0, ge=0, le=1)
    second_best_score: float = Field(default=0.0, ge=0, le=1)
    winner_edge: float = Field(default=0.0, ge=0, le=1)
    active_strategy_count: int = Field(default=0, ge=0)
    directional_strategy_count: int = Field(default=0, ge=0)
    active_weight: float = Field(default=0.0, ge=0, le=1)
    family_contributions: dict[str, dict[str, float]] = Field(default_factory=dict)
    disagreement_score: float = Field(default=0.0, ge=0, le=1)
    max_score: float = Field(ge=0, le=1)
    margin: float = Field(ge=0, le=1)
    raw_winner: WeightedSide
    score_version: str = "weighted_vote_scores_v1"
    data_timestamp: datetime
    explanation: str = Field(min_length=1)


class WeightedGateResult(WeightedContractModel):
    contract_version: str = "weighted_gate_result_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    gate_id: str = Field(min_length=1)
    gate_name: str = Field(min_length=1)
    status: WeightedGateStatus
    blocks_order: bool
    gate_version: str = "weighted_gate_result_v1"
    data_timestamp: datetime
    reason_codes: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)


class WeightedPositionState(WeightedContractModel):
    contract_version: str = "weighted_position_state_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    symbol: str = Field(min_length=1)
    quantity: int
    average_entry_price: float | None = Field(default=None, gt=0)
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    data_timestamp: datetime
    position_version: str = "weighted_position_state_v1"
    explanation: str = Field(min_length=1)


class WeightedDecision(WeightedContractModel):
    contract_version: str = "weighted_decision_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    decision_id: str = Field(min_length=1)
    configuration_version: str = Field(min_length=1)
    strategy_catalog_version: str = Field(min_length=1)
    weight_version: str = Field(min_length=1)
    data_timestamp: datetime
    data_manifest_hash: str | None = None
    settings_version: str = Field(min_length=1)
    proposed_side: WeightedSide
    proposed_quantity: int = Field(ge=0)
    reason_codes: tuple[str, ...]
    vote_scores: WeightedVoteScores
    weight_adjustments: tuple[WeightedWeightAdjustment, ...] = ()
    gate_results: tuple[WeightedGateResult, ...] = ()
    signal: WeightedSide
    raw_winner: WeightedSide
    eligible: bool
    data_ready: bool
    decision_version: str = "weighted_decision_v1"
    configuration_hash: str = Field(min_length=1)
    explanation: str = Field(min_length=1)


class WeightedOrderProposal(WeightedContractModel):
    contract_version: str = "weighted_order_proposal_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    decision_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    configuration_version: str = Field(min_length=1)
    strategy_catalog_version: str = Field(min_length=1)
    weight_version: str = Field(min_length=1)
    data_timestamp: datetime
    data_manifest_hash: str | None = None
    settings_version: str = Field(min_length=1)
    proposed_side: WeightedSide
    proposed_quantity: int = Field(ge=0)
    order_status: WeightedOrderStatus
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    reason_codes: tuple[str, ...]
    order_version: str = "weighted_order_proposal_v1"
    configuration_hash: str = Field(min_length=1)
    explanation: str = Field(min_length=1)


class WeightedTradeRecord(WeightedContractModel):
    contract_version: str = "weighted_trade_record_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    trade_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: WeightedSide
    quantity: int = Field(gt=0)
    price: float = Field(gt=0)
    trade_timestamp: datetime
    exit_reason: WeightedExitReason = WeightedExitReason.NONE
    trade_version: str = "weighted_trade_record_v1"
    reason_codes: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)


class WeightedBacktestFold(WeightedContractModel):
    contract_version: str = "weighted_backtest_fold_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    fold_id: str = Field(min_length=1)
    train_start: datetime
    train_end: datetime
    test_start: datetime
    test_end: datetime
    data_manifest_hash: str = Field(min_length=1)
    fold_version: str = "weighted_backtest_fold_v1"
    reason_codes: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_chronology(self) -> WeightedBacktestFold:
        if not (self.train_start <= self.train_end < self.test_start <= self.test_end):
            raise ValueError("backtest fold dates must be chronological and non-overlapping")
        return self


class WeightedBacktestRun(WeightedContractModel):
    contract_version: str = "weighted_backtest_run_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    run_id: str = Field(min_length=1)
    status: WeightedBacktestStatus
    configuration_version: str = Field(min_length=1)
    strategy_catalog_version: str = Field(min_length=1)
    weight_version: str = Field(min_length=1)
    settings_version: str = Field(min_length=1)
    data_manifest_hash: str = Field(min_length=1)
    folds: tuple[WeightedBacktestFold, ...]
    started_at: datetime
    completed_at: datetime | None = None
    run_version: str = "weighted_backtest_run_v1"
    reason_codes: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)


class WeightedArtifactManifest(WeightedContractModel):
    contract_version: str = "weighted_artifact_manifest_v1"
    algorithm_id: Literal["weighted_voting"] = ALGORITHM_ID
    artifact_id: str = Field(min_length=1)
    artifact_version: str = "weighted_artifact_manifest_v1"
    configuration_version: str = Field(min_length=1)
    strategy_catalog_version: str = Field(min_length=1)
    weight_version: str = Field(min_length=1)
    settings_version: str = Field(min_length=1)
    data_manifest_hash: str = Field(min_length=1)
    artifact_hash: str = Field(min_length=1)
    created_at: datetime
    reason_codes: tuple[str, ...] = ()
    explanation: str = Field(min_length=1)


# Compatibility aliases for the initial package skeleton.
WeightedVotingSide = WeightedSide
WeightedVotingStrategyFamily = WeightedStrategyFamily
WeightedVotingSignal = WeightedStrategySignal
WeightedVotingDecision = WeightedDecision
