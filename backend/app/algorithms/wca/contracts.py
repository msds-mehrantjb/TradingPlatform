"""Canonical contracts for Weighted Confidence Aggregation (WCA)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


WCA_ALGORITHM_ID = "wca"
WCA_CONTRACT_VERSION = "wca_contracts_v1"
WCA_FEATURE_SNAPSHOT_SCHEMA_VERSION = "wca_read_only_feature_snapshot_v1"
WCA_BROKER_RECONCILIATION_SCHEMA_VERSION = "wca_broker_reconciliation_v1"
WCA_SHADOW_COMPARISON_EVIDENCE_SCHEMA_VERSION = "wca_shadow_comparison_evidence_v1"
WCA_PAPER_STABILITY_VALIDATION_SCHEMA_VERSION = "wca_paper_stability_validation_v1"


@dataclass(frozen=True)
class WcaSharedPlatformComponent:
    component_id: str
    shared_component: str
    sharing_rule: str


@dataclass(frozen=True)
class WcaDedicatedComponent:
    component_id: str
    dedicated_component: str
    owner_module: str


WCA_SHARED_PLATFORM_COMPONENT_INVENTORY: tuple[WcaSharedPlatformComponent, ...] = (
    WcaSharedPlatformComponent("raw_and_normalized_market_data_services", "Raw and normalized market-data services", "Read-only input."),
    WcaSharedPlatformComponent("clock_and_market_calendar_service", "Clock and market-calendar service", "Read-only input."),
    WcaSharedPlatformComponent("account_equity_and_buying_power_snapshot", "Account-equity and buying-power snapshot", "Read-only input."),
    WcaSharedPlatformComponent("broker_api_client", "Broker API client", "Executes approved proposals only."),
    WcaSharedPlatformComponent("global_account_risk_engine", "Global account-risk engine", "May reduce or reject WCA risk."),
    WcaSharedPlatformComponent("global_portfolio_risk_ledger", "Global portfolio-risk ledger", "Must preserve algorithm attribution."),
    WcaSharedPlatformComponent("global_emergency_controls", "Global emergency controls", "May block new entries."),
    WcaSharedPlatformComponent("idempotency_service", "Idempotency service", "Must include WCA algorithm and intent identifiers."),
    WcaSharedPlatformComponent("broker_reconciliation_infrastructure", "Broker reconciliation infrastructure", "Must preserve WCA ownership."),
    WcaSharedPlatformComponent("database_connection_path_utilities", "Database connection/path utilities", "Infrastructure only."),
    WcaSharedPlatformComponent("logging_metrics_and_tracing", "Logging, metrics, and tracing", "Must tag records with algorithm_id=wca."),
    WcaSharedPlatformComponent("api_framework_and_authentication", "API framework and authentication", "Transport only."),
)

WCA_SHARED_PLATFORM_COMPONENT_IDS = frozenset(component.component_id for component in WCA_SHARED_PLATFORM_COMPONENT_INVENTORY)
WCA_GLOBAL_RISK_FORBIDDEN_REWRITE_TARGETS = frozenset(
    {
        "wca_signals",
        "strategy_confidence",
        "strategy_weights",
        "wca_thresholds",
        "wca_dynamic_profiles",
        "wca_stop_logic",
        "wca_backtest_results",
    }
)
WCA_GLOBAL_RISK_ALLOWED_CONSTRAINTS = frozenset({"reduce_wca_risk", "reject_wca_entry", "block_new_entries"})

WCA_DEDICATED_COMPONENT_INVENTORY: tuple[WcaDedicatedComponent, ...] = (
    WcaDedicatedComponent("wca_strategies", "WCA strategies", "backend.app.algorithms.wca.strategies"),
    WcaDedicatedComponent("wca_modifier_implementations", "WCA modifier implementations", "backend.app.algorithms.wca.modifiers"),
    WcaDedicatedComponent("wca_indicator_interpretation", "WCA indicator interpretation", "backend.app.algorithms.wca.strategies.indicators"),
    WcaDedicatedComponent("wca_confidence_calibration", "WCA confidence calibration", "backend.app.algorithms.wca.confidence"),
    WcaDedicatedComponent("wca_performance_statistics", "WCA performance statistics", "backend.app.algorithms.wca.weights"),
    WcaDedicatedComponent("wca_weight_snapshots", "WCA weight snapshots", "backend.app.algorithms.wca.weights"),
    WcaDedicatedComponent("wca_family_correlation_state", "WCA family-correlation state", "backend.app.algorithms.wca.weights"),
    WcaDedicatedComponent("wca_aggregation_logic", "WCA aggregation logic", "backend.app.algorithms.wca.aggregation"),
    WcaDedicatedComponent("wca_local_gates", "WCA local gates", "backend.app.algorithms.wca.local_gates"),
    WcaDedicatedComponent("wca_baseline_settings", "WCA baseline settings", "backend.app.algorithms.wca.configuration"),
    WcaDedicatedComponent("wca_dynamic_profiles", "WCA dynamic profiles", "backend.app.algorithms.wca.dynamic_profile"),
    WcaDedicatedComponent("wca_sizing_policy", "WCA sizing policy", "backend.app.algorithms.wca.sizing"),
    WcaDedicatedComponent("wca_exit_policy", "WCA exit policy", "backend.app.algorithms.wca.exits"),
    WcaDedicatedComponent("wca_decisions", "WCA decisions", "backend.app.algorithms.wca.contracts"),
    WcaDedicatedComponent("wca_order_intents", "WCA order intents", "backend.app.algorithms.wca.repository"),
    WcaDedicatedComponent("wca_positions_and_trades", "WCA positions and trades", "backend.app.algorithms.wca.repository"),
    WcaDedicatedComponent("wca_backtesting", "WCA backtesting", "backend.app.algorithms.wca.backtest"),
    WcaDedicatedComponent("wca_diagnostics", "WCA diagnostics", "backend.app.algorithms.wca.backtest.metrics"),
    WcaDedicatedComponent("wca_rollout_state", "WCA rollout state", "backend.app.algorithms.wca.rollout"),
)

WCA_DEDICATED_COMPONENT_IDS = frozenset(component.component_id for component in WCA_DEDICATED_COMPONENT_INVENTORY)
WCA_DEDICATED_COMPONENT_OWNER_MODULES = frozenset(component.owner_module for component in WCA_DEDICATED_COMPONENT_INVENTORY)


class WcaContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    def deterministic_json(self) -> str:
        payload = self.model_dump(mode="json", exclude_none=True)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def deterministic_hash(self) -> str:
        return hashlib.sha256(self.deterministic_json().encode("utf-8")).hexdigest()


class WcaLegacyModel(WcaContractModel):
    model_config = ConfigDict(extra="ignore", frozen=True, populate_by_name=True, use_enum_values=True)


class WcaSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class WcaEvaluationStatus(str, Enum):
    ACTIVE = "ACTIVE"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INVALID = "INVALID"
    DEGRADED = "DEGRADED"


class WcaTrendStatus(str, Enum):
    STRONG_UPTREND = "strong_uptrend"
    WEAK_UPTREND = "weak_uptrend"
    RANGE = "range"
    WEAK_DOWNTREND = "weak_downtrend"
    STRONG_DOWNTREND = "strong_downtrend"


class WcaVolatilityStatus(str, Enum):
    VERY_LOW = "very_low"
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    EXTREME = "extreme"


class WcaLiquidityStatus(str, Enum):
    DEEP = "deep"
    NORMAL = "normal"
    THIN = "thin"
    UNSAFE = "unsafe"


class WcaSessionStatus(str, Enum):
    OPENING = "opening"
    MORNING = "morning"
    MIDDAY = "midday"
    AFTERNOON = "afternoon"
    CLOSING = "closing"


class WcaEventRiskStatus(str, Enum):
    NORMAL = "normal"
    ELEVATED = "elevated"
    BLOCKED = "blocked"


class WcaDataQualityStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    INVALID = "invalid"


class WcaAlgorithmRiskStatus(str, Enum):
    NORMAL = "normal"
    REDUCED = "reduced"
    DEFENSIVE = "defensive"
    DAILY_STOP = "daily_stop"


class WcaGateStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INFO = "INFO"


class WcaOrderStatus(str, Enum):
    PROPOSED = "PROPOSED"
    REJECTED = "REJECTED"
    ACCEPTED_FOR_PAPER = "ACCEPTED_FOR_PAPER"
    FILLED_PAPER = "FILLED_PAPER"
    CANCELLED = "CANCELLED"


class WcaBacktestMode(str, Enum):
    PARITY = "PARITY"
    PAPER_SIMULATION = "PAPER_SIMULATION"
    WALK_FORWARD = "WALK_FORWARD"
    DAILY_SMOKE = "DAILY_SMOKE"
    ROLLING_20 = "ROLLING_20"
    ROLLING_60 = "ROLLING_60"
    ROLLING_252 = "ROLLING_252"
    CUSTOM_WINDOW = "CUSTOM_WINDOW"
    FULL_HISTORY = "FULL_HISTORY"
    UNTOUCHED_HOLDOUT = "UNTOUCHED_HOLDOUT"


class WcaBacktestSideMode(str, Enum):
    LONG_ONLY = "long_only"
    SHORT_ONLY = "short_only"
    LONG_AND_SHORT = "long_and_short"


class WcaCandle(WcaContractModel):
    timestamp: datetime
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    vwap: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_ohlc(self) -> "WcaCandle":
        if self.high < max(self.open, self.close, self.low):
            raise ValueError("high must be at least open, close, and low")
        if self.low > min(self.open, self.close, self.high):
            raise ValueError("low must be at most open, close, and high")
        return self


class WcaQuote(WcaContractModel):
    timestamp: datetime
    bid: float = Field(gt=0)
    ask: float = Field(gt=0)

    @model_validator(mode="after")
    def validate_quote(self) -> "WcaQuote":
        if self.ask < self.bid:
            raise ValueError("ask must be greater than or equal to bid")
        return self


class WcaMarketSnapshot(WcaContractModel):
    algorithm_id: str = WCA_ALGORITHM_ID
    schema_version: str = WCA_CONTRACT_VERSION
    symbol: str = Field(min_length=1)
    data_timestamp: datetime
    decision_timestamp: datetime
    candles: tuple[WcaCandle, ...] = Field(min_length=1)
    quote: WcaQuote | None = None
    source: str = "neutral_market_data"
    data_ready: bool = True
    reason_codes: tuple[str, ...] = ()


class WcaStrategyEvaluation(WcaContractModel):
    strategy_id: str = Field(min_length=1)
    strategy_version: str = "wca_strategy_unversioned_v1"
    name: str = Field(min_length=1)
    status: WcaEvaluationStatus = WcaEvaluationStatus.ACTIVE
    signal: WcaSide = WcaSide.HOLD
    confidence: float = Field(ge=0, le=1)
    raw_confidence: float = Field(ge=0, le=1)
    calibrated_confidence: float = Field(ge=0, le=1)
    direction: WcaSide = WcaSide.HOLD
    applicability: WcaEvaluationStatus = WcaEvaluationStatus.ACTIVE
    evidence_strength: float = Field(ge=0, le=1)
    data_quality_status: WcaEvaluationStatus = WcaEvaluationStatus.ACTIVE
    calibration_version: str = "wca_confidence_calibration_disabled_v1"
    base_weight: float = Field(ge=0)
    effective_weight: float = Field(ge=0)
    contribution: float
    reason_codes: tuple[str, ...] = ()
    explanation: str = ""

    @model_validator(mode="before")
    @classmethod
    def populate_confidence_contract(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        confidence = data.get("confidence", data.get("calibrated_confidence", data.get("raw_confidence", 0)))
        status = data.get("status", WcaEvaluationStatus.ACTIVE)
        signal = data.get("signal", WcaSide.HOLD)
        populated = dict(data)
        populated.setdefault("raw_confidence", confidence)
        populated.setdefault("calibrated_confidence", confidence)
        populated.setdefault("direction", signal)
        populated.setdefault("applicability", status)
        populated.setdefault("evidence_strength", confidence if status == WcaEvaluationStatus.ACTIVE or status == WcaEvaluationStatus.ACTIVE.value else 0)
        populated.setdefault("data_quality_status", status)
        populated.setdefault("calibration_version", "wca_confidence_calibration_disabled_v1")
        return populated

    @model_validator(mode="after")
    def not_applicable_has_no_directional_signal(self) -> "WcaStrategyEvaluation":
        status = self.status.value if isinstance(self.status, WcaEvaluationStatus) else self.status
        signal = self.signal.value if isinstance(self.signal, WcaSide) else self.signal
        if status == WcaEvaluationStatus.NOT_APPLICABLE.value and signal != WcaSide.HOLD.value:
            raise ValueError("NOT_APPLICABLE strategy evaluations cannot carry BUY or SELL")
        if self.direction != self.signal:
            raise ValueError("strategy evaluation direction must match signal")
        if abs(self.confidence - self.calibrated_confidence) > 1e-9:
            raise ValueError("confidence must mirror calibrated_confidence")
        return self


class WcaConfidenceCalibrationOutcome(WcaContractModel):
    strategy_id: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    raw_confidence: float = Field(ge=0, le=1)
    realized_success: bool
    decision_timestamp: datetime
    outcome_available_at: datetime


class WcaConfidenceCalibrationBin(WcaContractModel):
    lower_bound: float = Field(ge=0, le=1)
    upper_bound: float = Field(ge=0, le=1)
    sample_count: int = Field(ge=0)
    success_count: int = Field(ge=0)
    prior_success_rate: float = Field(ge=0, le=1)
    prior_strength: float = Field(ge=0)
    posterior_success_rate: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_bin(self) -> "WcaConfidenceCalibrationBin":
        if self.upper_bound <= self.lower_bound:
            raise ValueError("calibration bin upper_bound must be greater than lower_bound")
        if self.success_count > self.sample_count:
            raise ValueError("calibration bin success_count cannot exceed sample_count")
        return self


class WcaConfidenceCalibrationTable(WcaContractModel):
    strategy_id: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    calibration_version: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    outcome_cutoff_timestamp: datetime
    minimum_samples: int = Field(ge=1)
    prior_success_rate: float = Field(ge=0, le=1)
    prior_strength: float = Field(ge=0)
    bins: tuple[WcaConfidenceCalibrationBin, ...] = Field(min_length=1)


class WcaModifierEvaluation(WcaContractModel):
    modifier_id: str = Field(min_length=1)
    status: WcaEvaluationStatus
    multiplier: float = Field(ge=0)
    reason_codes: tuple[str, ...] = ()
    explanation: str = ""


class WcaMarketStatus(WcaContractModel):
    status: WcaEvaluationStatus
    trend: WcaTrendStatus | str = WcaTrendStatus.RANGE
    volatility: WcaVolatilityStatus | str = WcaVolatilityStatus.NORMAL
    liquidity: WcaLiquidityStatus | str = WcaLiquidityStatus.NORMAL
    session: WcaSessionStatus | str = WcaSessionStatus.MIDDAY
    event_risk: WcaEventRiskStatus | str = WcaEventRiskStatus.NORMAL
    data_quality: WcaDataQualityStatus | str = WcaDataQualityStatus.HEALTHY
    algorithm_risk: WcaAlgorithmRiskStatus | str = WcaAlgorithmRiskStatus.NORMAL
    classification_confidence: float = Field(default=0, ge=0, le=1)
    input_timestamp: datetime | None = None
    data_quality_flags: tuple[str, ...] = ()
    profile_expiration: datetime | None = None
    reason_codes: tuple[str, ...] = ()
    explanation: str = ""


class WcaBaselineSettings(WcaContractModel):
    settings_version: str = "wca_baseline_settings_v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    minimum_score: float = Field(default=0.35, ge=0, le=1)
    strong_buy_threshold: float = Field(default=0.65, ge=0, le=1)
    buy_threshold: float = Field(default=0.35, ge=0, le=1)
    sell_threshold: float = Field(default=-0.35, ge=-1, le=0)
    strong_sell_threshold: float = Field(default=-0.65, ge=-1, le=0)
    minimum_active_strategies: int = Field(default=3, ge=1)
    minimum_directional_agreement: float = Field(default=0.50, ge=0, le=1)
    minimum_average_confidence: float = Field(default=0.45, ge=0, le=1)
    base_risk_percent: float = Field(default=1.0, ge=0)
    order_allocation_percent: float = Field(default=10.0, ge=0)
    daily_allocation_percent: float = Field(default=20.0, ge=0)
    max_position_percent: float = Field(default=10.0, ge=0)
    max_daily_loss_percent: float = Field(default=3.0, ge=0)
    max_daily_trades: int = Field(default=5, ge=0)
    atr_stop_multiplier: float = Field(default=2.0, ge=0)
    minimum_stop_distance_percent: float = Field(default=0.05, ge=0)
    take_profit_r: float = Field(default=1.5, ge=0)
    assumed_slippage_per_share: float = Field(default=0.02, ge=0)
    cooldown_seconds: int = Field(default=0, ge=0)
    entry_cutoff_minutes: int = Field(default=15 * 60 + 30, ge=0)
    pyramiding_enabled: bool = False
    max_spread_percent: float = Field(default=0.10, ge=0)
    max_participation_percent: float = Field(default=1.0, ge=0)
    max_allowed_shares: int = Field(default=0, ge=0)
    hard_max_risk_percent: float = Field(default=1.0, ge=0)
    hard_max_order_allocation_percent: float = Field(default=10.0, ge=0)
    hard_max_daily_allocation_percent: float = Field(default=20.0, ge=0)
    hard_max_position_percent: float = Field(default=10.0, ge=0)
    hard_max_daily_loss_percent: float = Field(default=3.0, ge=0)
    hard_max_allowed_shares: int = Field(default=0, ge=0)


class WcaEffectiveSettings(WcaContractModel):
    baseline: WcaBaselineSettings
    baseline_settings_version: str = "wca_baseline_settings_v1"
    profile_id: str = "baseline"
    profile_version: str = "wca_dynamic_profile_disabled_v1"
    market_status: WcaMarketStatus | None = None
    active_overlays: tuple[str, ...] = ()
    settings_version: str = "wca_effective_settings_v1"
    effective_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expiration_timestamp: datetime | None = None
    risk_multiplier: float = Field(default=1.0, ge=0)
    quantity_multiplier: float = Field(default=1.0, ge=0)
    allocation_multiplier: float = Field(default=1.0, ge=0)
    entry_strictness_multiplier: float = Field(default=1.0, ge=0)
    threshold_adjustment: float = Field(default=0, ge=0)
    final_risk_percent: float = Field(ge=0)
    final_order_allocation_percent: float = Field(default=10.0, ge=0)
    final_daily_allocation_percent: float = Field(default=20.0, ge=0)
    final_max_position_percent: float = Field(default=10.0, ge=0)
    final_max_daily_loss_percent: float = Field(default=3.0, ge=0)
    final_max_daily_trades: int = Field(default=5, ge=0)
    final_max_allowed_shares: int = Field(default=0, ge=0)
    final_minimum_score: float = Field(default=0.35, ge=0, le=1)
    final_minimum_agreement: float = Field(default=0.50, ge=0, le=1)
    final_minimum_confidence: float = Field(default=0.45, ge=0, le=1)
    final_atr_stop_multiplier: float = Field(default=2.0, ge=0)
    final_minimum_stop_distance_percent: float = Field(default=0.05, ge=0)
    final_take_profit_r: float = Field(default=1.5, ge=0)
    final_assumed_slippage_per_share: float = Field(default=0.02, ge=0)
    final_cooldown_seconds: int = Field(default=0, ge=0)
    final_entry_cutoff_minutes: int = Field(default=15 * 60 + 30, ge=0)
    final_max_spread_percent: float = Field(default=0.10, ge=0)
    final_max_participation_percent: float = Field(default=1.0, ge=0)
    final_pyramiding_enabled: bool = False
    entries_blocked: bool = False
    reason_codes: tuple[str, ...] = ()


class WcaDynamicProfile(WcaContractModel):
    profile_id: str = Field(min_length=1)
    profile_version: str = Field(min_length=1)
    baseline_settings_version: str = Field(min_length=1)
    market_status: WcaMarketStatus
    active_overlays: tuple[str, ...]
    effective_settings: WcaEffectiveSettings
    calculation_timestamp: datetime
    expiration_timestamp: datetime
    reason_codes: tuple[str, ...] = ()


class WcaWeightSnapshot(WcaContractModel):
    weight_version: str = "wca_weights_unseeded_v1"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    weights: dict[str, float]
    details: tuple["WcaStrategyWeightDetail", ...] = ()
    metrics_cutoff_timestamp: datetime | None = None
    status: WcaEvaluationStatus = WcaEvaluationStatus.ACTIVE
    reason_codes: tuple[str, ...] = ()

    @field_validator("weights")
    @classmethod
    def validate_weights(cls, weights: dict[str, float]) -> dict[str, float]:
        if not weights:
            raise ValueError("weights cannot be empty")
        if any(value < 0 for value in weights.values()):
            raise ValueError("weights must be nonnegative")
        total = sum(weights.values())
        if abs(total - 1.0) > 1e-6:
            raise ValueError("weights must sum to one")
        return weights


class WcaStrategyPerformanceRecord(WcaContractModel):
    strategy_id: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    family: str = Field(min_length=1)
    decision_timestamp: datetime
    outcome_available_at: datetime
    r_multiple: float
    pnl: float = 0
    success: bool
    regime: str = "default"


class WcaStrategyWeightDetail(WcaContractModel):
    strategy_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    base_weight: float = Field(ge=0)
    performance_factor: float = Field(ge=0)
    reliability_factor: float = Field(ge=0)
    regime_factor: float = Field(ge=0)
    health_factor: float = Field(ge=0)
    correlation_factor: float = Field(ge=0)
    final_weight: float = Field(ge=0)
    trade_count: int = Field(ge=0)
    rolling_expectancy: float = 0
    profit_factor: float = Field(default=1, ge=0)
    win_rate: float = Field(default=0, ge=0, le=1)
    average_r: float = 0
    downside_deviation: float = Field(default=0, ge=0)
    maximum_drawdown: float = Field(default=0, ge=0)
    consecutive_losses: int = Field(default=0, ge=0)
    metrics_cutoff_timestamp: datetime
    weight_version: str = Field(min_length=1)
    reason_codes: tuple[str, ...] = ()


class WcaStrategyContribution(WcaContractModel):
    strategy_id: str = Field(min_length=1)
    family: str = Field(min_length=1)
    signal: WcaSide
    effective_weight: float = Field(ge=0)
    adjusted_weight: float = Field(ge=0)
    calibrated_confidence: float = Field(ge=0, le=1)
    score_contribution: float
    reason_codes: tuple[str, ...] = ()


class WcaFamilyContribution(WcaContractModel):
    family: str = Field(min_length=1)
    buy_score: float = Field(ge=0)
    sell_score: float = Field(ge=0)
    directional_weight: float = Field(ge=0)
    total_weight: float = Field(ge=0)


class WcaAggregationExclusion(WcaContractModel):
    strategy_id: str = Field(min_length=1)
    family: str = "unknown"
    reason_codes: tuple[str, ...]


class WcaAggregationResult(WcaContractModel):
    signal: WcaSide
    decision_label: str
    pre_gate_decision: WcaSide = WcaSide.HOLD
    post_local_gate_decision: WcaSide = WcaSide.HOLD
    buy_score: float
    sell_score: float
    net_score: float
    active_weight: float = Field(ge=0)
    normalized_net_score: float
    active_strategy_count: int = Field(ge=0)
    runner_up_score: float = Field(default=0, ge=0)
    winner_edge: float = Field(default=0, ge=0)
    buy_agreement: float = Field(ge=0, le=1)
    sell_agreement: float = Field(ge=0, le=1)
    buy_average_confidence: float = Field(ge=0, le=1)
    sell_average_confidence: float = Field(ge=0, le=1)
    family_concentration: float = Field(default=0, ge=0, le=1)
    estimated_expectancy_after_costs: float = 0
    strategy_contributions: tuple[WcaStrategyContribution, ...] = ()
    family_contributions: tuple[WcaFamilyContribution, ...] = ()
    exclusions: tuple[WcaAggregationExclusion, ...] = ()
    strategy_evaluations: tuple[WcaStrategyEvaluation, ...]
    reason_codes: tuple[str, ...] = ()


class WcaLocalGateResult(WcaContractModel):
    gate_id: str = Field(min_length=1)
    status: WcaGateStatus
    blocks_entry: bool
    severity: str = "info"
    reason_code: str = ""
    detail: str = ""
    evaluated_value: float | int | str | bool | None = None
    required_value: float | int | str | bool | None = None
    reason_codes: tuple[str, ...] = ()
    explanation: str = ""


class WcaSizingResult(WcaContractModel):
    final_quantity: int = Field(ge=0)
    risk_dollars: float = Field(ge=0)
    stop_distance: float = Field(ge=0)
    shares_by_risk: float = Field(ge=0)
    shares_by_order: float = Field(ge=0)
    shares_by_capital: float = Field(ge=0)
    shares_by_buying_power: float = Field(ge=0)
    shares_by_liquidity: float = Field(ge=0)
    limiting_factor: str
    blocked_reason: str = ""
    side: WcaSide | str = WcaSide.HOLD
    entry_price: float = Field(default=0, ge=0)
    stop_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    spread: float = Field(default=0, ge=0)
    estimated_costs: float = Field(default=0, ge=0)
    minimum_reward_risk: float = Field(default=0, ge=0)
    reward_risk_ratio: float = Field(default=0, ge=0)
    approved_risk_budget: float | None = Field(default=None, ge=0)
    stop_risk_dollars: float = Field(default=0, ge=0)
    shares_by_maximum_shares: float = Field(default=0, ge=0)
    shares_by_global_gate: float = Field(default=0, ge=0)
    reason_codes: tuple[str, ...] = ()


class ProposedOrder(WcaContractModel):
    algorithm_id: str = WCA_ALGORITHM_ID
    decision_id: str = Field(min_length=1)
    order_intent_id: str = Field(min_length=1)
    idempotency_key: str | None = Field(default=None, min_length=1)
    account_id: str = Field(default="paper", min_length=1)
    symbol: str = Field(min_length=1)
    side: WcaSide
    quantity: int = Field(ge=0)
    trigger_price: float | None = Field(default=None, gt=0)
    limit_price: float | None = Field(default=None, gt=0)
    stop_price: float | None = Field(default=None, gt=0)
    target_price: float | None = Field(default=None, gt=0)
    status: WcaOrderStatus = WcaOrderStatus.PROPOSED
    reason_codes: tuple[str, ...] = ()


class GlobalGateResult(WcaContractModel):
    status: WcaGateStatus
    proposed_quantity: int = Field(ge=0)
    allowed_quantity: int = Field(ge=0)
    reason_codes: tuple[str, ...] = ()
    explanation: str = ""

    @model_validator(mode="after")
    def cannot_increase_quantity(self) -> "GlobalGateResult":
        if self.allowed_quantity > self.proposed_quantity:
            raise ValueError("global gate cannot increase proposed quantity")
        return self


class WcaDecision(WcaContractModel):
    algorithm_id: str = WCA_ALGORITHM_ID
    decision_id: str = Field(min_length=1)
    configuration_version: str
    weight_version: str
    data_timestamp: datetime
    decision_timestamp: datetime
    market_snapshot: WcaMarketSnapshot
    market_status: WcaMarketStatus
    effective_settings: WcaEffectiveSettings | None = None
    aggregation: WcaAggregationResult
    local_gates: tuple[WcaLocalGateResult, ...]
    sizing: WcaSizingResult
    proposed_order: ProposedOrder | None = None
    global_gate_result: GlobalGateResult | None = None
    reason_codes: tuple[str, ...] = ()


class WcaReadOnlyFeatureSnapshot(WcaContractModel):
    algorithm_id: str = WCA_ALGORITHM_ID
    schema_version: str = WCA_FEATURE_SNAPSHOT_SCHEMA_VERSION
    snapshot_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    strategy_signals: tuple[WcaStrategyEvaluation, ...]
    strategy_calibrated_confidences: dict[str, float]
    effective_weights: dict[str, float]
    family_contributions: tuple[WcaFamilyContribution, ...]
    buy_score: float
    sell_score: float
    normalized_score: float
    agreement: float = Field(ge=0, le=1)
    score_edge: float = Field(ge=0)
    market_status: WcaMarketStatus
    dynamic_profile: WcaDynamicProfile | None = None
    local_gate_results: tuple[WcaLocalGateResult, ...]
    final_wca_decision: WcaSide
    reason_codes: tuple[str, ...] = ()


class BacktestRunConfiguration(WcaContractModel):
    run_id: str = Field(min_length=1)
    mode: WcaBacktestMode = WcaBacktestMode.PARITY
    symbol: str = Field(min_length=1)
    start: datetime
    end: datetime
    configuration_version: str
    data_manifest_hash: str
    configuration_hash: str = ""
    side_mode: WcaBacktestSideMode | str = WcaBacktestSideMode.LONG_ONLY
    starting_equity: float = Field(default=100000, gt=0)
    slippage_per_share: float = Field(default=0.01, ge=0)
    fee_per_share: float = Field(default=0.0, ge=0)
    spread_bps: float = Field(default=2.0, ge=0)
    market_impact_bps: float = Field(default=1.0, ge=0)
    max_participation_percent: float = Field(default=10.0, ge=0)
    allow_partial_fills: bool = True
    custom_window_sessions: int | None = Field(default=None, ge=1)
    smoke_sessions: int = Field(default=3, ge=1, le=3)
    walk_forward_lookback_sessions: int = Field(default=60, ge=1)
    walk_forward_test_sessions: int = Field(default=20, ge=1)
    walk_forward_roll_sessions: int = Field(default=20, ge=1)
    holdout_sessions: int = Field(default=20, ge=1)


class BacktestTrade(WcaContractModel):
    trade_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    side: WcaSide
    quantity: int = Field(gt=0)
    entry_at: datetime
    exit_at: datetime | None = None
    entry_price: float = Field(gt=0)
    exit_price: float | None = Field(default=None, gt=0)
    pnl: float = 0
    exit_reason: str | None = None


class BacktestResult(WcaContractModel):
    run_configuration: BacktestRunConfiguration
    trades: tuple[BacktestTrade, ...] = ()
    decisions: tuple[WcaDecision, ...] = ()
    total_pnl: float = 0
    total_return_percent: float = 0
    max_drawdown: float = Field(default=0, ge=0)
    metrics: dict[str, Any] = Field(default_factory=dict)


class WcaBacktestRequest(WcaContractModel):
    configuration: BacktestRunConfiguration
    candles: tuple[WcaCandle, ...] = Field(min_length=2)
    quotes: tuple[WcaQuote, ...] = ()


class WcaManualSizingOverrideRequest(WcaLegacyModel):
    quantity: int | None = Field(default=None, ge=0)
    limit_price: float | None = Field(default=None, gt=0, alias="limitPrice")
    stop_price: float | None = Field(default=None, gt=0, alias="stopPrice")
    target_price: float | None = Field(default=None, gt=0, alias="targetPrice")


class WcaPaperExecutionRequest(WcaLegacyModel):
    mode: Literal["manual", "automatic"] = "manual"
    run_id: str = Field(default="wca-paper", min_length=1, alias="runId")
    account_id: str = Field(default="paper", min_length=1, alias="accountId")
    symbol: str = Field(default="SPY", min_length=1)
    configuration_version: str = Field(default="wca_legacy_configuration_v1", alias="configurationVersion")
    candles: tuple[WcaCandle, ...] = Field(min_length=1)
    quotes: tuple[WcaQuote, ...] = ()
    account_equity: float = Field(default=100000, gt=0, alias="accountEquity")
    available_buying_power: float = Field(default=100000, ge=0, alias="availableBuyingPower")
    global_gate_quantity_cap: int | None = Field(default=2_147_483_647, ge=0, alias="globalGateQuantityCap")
    approved_risk_budget: float | None = Field(default=None, ge=0, alias="approvedRiskBudget")
    trades_today: int = Field(default=0, ge=0, alias="tradesToday")
    realized_daily_loss: float = Field(default=0, ge=0, alias="realizedDailyLoss")
    allocated_daily_loss_budget: float | None = Field(default=None, ge=0, alias="allocatedDailyLossBudget")
    remaining_allocated_risk_budget: float | None = Field(default=None, ge=0, alias="remainingAllocatedRiskBudget")
    current_position_quantity: int = Field(default=0, ge=0, alias="currentPositionQuantity")
    current_position_side: WcaSide | None = Field(default=None, alias="currentPositionSide")
    current_position_entry_price: float | None = Field(default=None, gt=0, alias="currentPositionEntryPrice")
    current_position_stop_price: float | None = Field(default=None, gt=0, alias="currentPositionStopPrice")
    current_position_target_price: float | None = Field(default=None, gt=0, alias="currentPositionTargetPrice")
    current_position_entry_at: datetime | None = Field(default=None, alias="currentPositionEntryAt")
    allow_position_increase: bool = Field(default=False, alias="allowPositionIncrease")
    estimated_cost_per_share: float = Field(default=0.01, ge=0, alias="estimatedCostPerShare")
    estimated_expectancy_after_costs: float = Field(default=0.01, alias="estimatedExpectancyAfterCosts")
    emergency_exit: bool = Field(default=False, alias="emergencyExit")
    manual_override: WcaManualSizingOverrideRequest | None = Field(default=None, alias="manualOverride")


class WcaPaperExecutionResult(WcaContractModel):
    mode: Literal["manual", "automatic"]
    action_status: str
    submitted: bool = False
    idempotency_key: str | None = None
    decision: WcaDecision
    proposed_order: ProposedOrder | None = None
    called_production_modules: tuple[str, ...]
    reason_codes: tuple[str, ...] = ()
    explanation: str = ""


@dataclass(frozen=True)
class WcaOrderValidationContext:
    evaluation_timestamp: datetime
    paper_only_mode: bool = True
    current_position_quantity: int = 0
    current_position_side: WcaSide | str | None = None
    allow_position_increase: bool = False
    position_owned_by_wca: bool = True


@dataclass(frozen=True)
class WcaOrderValidationResult:
    valid: bool
    reason_codes: tuple[str, ...]


class WcaBrokerReconciliationDiscrepancy(WcaContractModel):
    discrepancy_type: Literal[
        "missing_broker_order",
        "missing_backend_fill",
        "rejected_order",
        "orphan_position",
        "stale_open_order",
        "mismatched_quantity",
        "attribution_missing",
    ]
    severity: Literal["info", "warning", "hard"] = "warning"
    account_id: str = Field(min_length=1)
    algorithm_id: str = WCA_ALGORITHM_ID
    symbol: str = Field(min_length=1)
    side: WcaSide | str
    order_intent_id: str | None = None
    decision_id: str | None = None
    idempotency_key: str | None = None
    broker_status: str | None = None
    broker_quantity: int | None = Field(default=None, ge=0)
    backend_quantity: int | None = Field(default=None, ge=0)
    broker_filled_quantity: int | None = Field(default=None, ge=0)
    age_seconds: int | None = Field(default=None, ge=0)
    preserves_wca_attribution: bool = True
    attribution: dict[str, str | None] = Field(default_factory=dict)
    reason_codes: tuple[str, ...] = ()
    explanation: str = ""


class WcaBrokerReconciliationResult(WcaContractModel):
    reconciliation_id: str = Field(min_length=1)
    reconciliation_version: str = WCA_BROKER_RECONCILIATION_SCHEMA_VERSION
    account_id: str = Field(min_length=1)
    evaluated_at: datetime
    intents_checked: int = Field(ge=0)
    broker_open_orders_checked: int = Field(ge=0)
    broker_positions_checked: int = Field(ge=0)
    discrepancies: tuple[WcaBrokerReconciliationDiscrepancy, ...] = ()
    hard_operational_warning: bool = False
    reason_codes: tuple[str, ...] = ()
    explanation: str = ""


class WcaShadowFieldComparison(WcaContractModel):
    field: str = Field(min_length=1)
    legacy_value: Any
    backend_value: Any
    matched: bool
    tolerance: float = Field(ge=0)
    reason_codes: tuple[str, ...] = ()


class WcaShadowComparisonEvidence(WcaContractModel):
    evidence_id: str = Field(min_length=1)
    evidence_version: str = WCA_SHADOW_COMPARISON_EVIDENCE_SCHEMA_VERSION
    snapshot_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    evaluated_at: datetime
    compared_fields: tuple[str, ...]
    field_comparisons: tuple[WcaShadowFieldComparison, ...]
    mismatched_fields: tuple[str, ...] = ()
    within_tolerance: bool
    rollout_phase: str = "legacy_parity"
    rollout_phase_passed: bool = False
    submission_allowed: bool = False
    legacy_result: dict[str, Any] = Field(default_factory=dict)
    backend_result: dict[str, Any] = Field(default_factory=dict)
    reason_codes: tuple[str, ...] = ()
    explanation: str = ""


class WcaPaperValidationDecision(WcaContractModel):
    decision_id: str = Field(min_length=1)
    timestamp: datetime
    market_condition: str = Field(min_length=1)
    side: WcaSide | str
    quantity: int = Field(ge=0)
    submitted: bool = False
    rejected: bool = False
    reason_codes: tuple[str, ...] = ()


class WcaPaperValidationFill(WcaContractModel):
    order_intent_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    timestamp: datetime
    side: WcaSide | str
    quantity: int = Field(gt=0)
    expected_price: float = Field(gt=0)
    fill_price: float = Field(gt=0)
    slippage_per_share: float = Field(ge=0)


class WcaPaperValidationExit(WcaContractModel):
    order_intent_id: str = Field(min_length=1)
    decision_id: str = Field(min_length=1)
    timestamp: datetime
    side: WcaSide | str
    quantity: int = Field(gt=0)
    exit_price: float = Field(gt=0)
    pnl: float
    reason_codes: tuple[str, ...] = ()


class WcaPaperValidationEquityPoint(WcaContractModel):
    timestamp: datetime
    equity: float = Field(ge=0)


class WcaPaperValidationRollbackEvidence(WcaContractModel):
    tested: bool = False
    restored_safe_state: bool = False
    reason_codes: tuple[str, ...] = ()


class WcaPaperStabilityValidationRequest(WcaContractModel):
    validation_id: str = Field(default="wca-paper-stability", min_length=1)
    account_id: str = Field(default="paper", min_length=1)
    started_at: datetime
    ended_at: datetime
    min_validation_days: int = Field(default=14, ge=1)
    min_market_conditions: int = Field(default=3, ge=1)
    max_drawdown_percent: float = Field(default=5.0, ge=0)
    max_average_slippage_per_share: float = Field(default=0.05, ge=0)
    decisions: tuple[WcaPaperValidationDecision, ...] = ()
    fills: tuple[WcaPaperValidationFill, ...] = ()
    exits: tuple[WcaPaperValidationExit, ...] = ()
    equity_curve: tuple[WcaPaperValidationEquityPoint, ...] = ()
    reconciliation_results: tuple[WcaBrokerReconciliationResult, ...] = ()
    duplicate_requests: int = Field(default=0, ge=0)
    duplicate_preventions: int = Field(default=0, ge=0)
    rollback: WcaPaperValidationRollbackEvidence = Field(default_factory=WcaPaperValidationRollbackEvidence)

    @model_validator(mode="after")
    def ended_after_started(self) -> "WcaPaperStabilityValidationRequest":
        if self.ended_at < self.started_at:
            raise ValueError("ended_at must be after started_at")
        return self


class WcaPaperStabilityValidationResult(WcaContractModel):
    validation_id: str = Field(min_length=1)
    validation_version: str = WCA_PAPER_STABILITY_VALIDATION_SCHEMA_VERSION
    account_id: str = Field(min_length=1)
    started_at: datetime
    ended_at: datetime
    validation_days: float = Field(ge=0)
    market_conditions: tuple[str, ...]
    decisions: int = Field(ge=0)
    rejected_entries: int = Field(ge=0)
    fills: int = Field(ge=0)
    exits: int = Field(ge=0)
    total_pnl: float
    max_drawdown_percent: float = Field(ge=0)
    average_slippage_per_share: float = Field(ge=0)
    reconciliation_checks: int = Field(ge=0)
    reconciliation_discrepancies: int = Field(ge=0)
    duplicate_requests: int = Field(ge=0)
    duplicate_preventions: int = Field(ge=0)
    rollback_tested: bool
    rollback_restored_safe_state: bool
    paper_trading_stable: bool = False
    rollout_phase: str = "extended_paper_validation"
    rollout_phase_passed: bool = False
    blocking_reasons: tuple[str, ...] = ()
    reason_codes: tuple[str, ...] = ()
    explanation: str = ""


class WcaBacktestModeResult(WcaContractModel):
    label: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    production_validation: bool = False
    result: BacktestResult


class WcaBacktestComparison(WcaContractModel):
    label: str = Field(min_length=1)
    baseline_run_id: str = Field(min_length=1)
    variant_run_id: str = Field(min_length=1)
    dataset_hash: str = Field(min_length=1)
    execution_assumptions_hash: str = Field(min_length=1)
    metrics: dict[str, Any] = Field(default_factory=dict)


class WcaBacktestSuiteResult(WcaContractModel):
    suite_id: str = Field(min_length=1)
    configuration_hash: str = Field(min_length=1)
    smoke: WcaBacktestModeResult
    rolling: tuple[WcaBacktestModeResult, ...]
    full_history: WcaBacktestModeResult
    walk_forward: WcaBacktestModeResult
    holdout: WcaBacktestModeResult
    comparisons: tuple[WcaBacktestComparison, ...]
    reason_codes: tuple[str, ...] = ()


class WcaDecisionSettings(WcaLegacyModel):
    strong_buy_threshold: float = Field(default=0.65, alias="strongBuyThreshold")
    buy_threshold: float = Field(default=0.35, alias="buyThreshold")
    sell_threshold: float = Field(default=-0.35, alias="sellThreshold")
    strong_sell_threshold: float = Field(default=-0.65, alias="strongSellThreshold")
    minimum_active_strategies: int = Field(default=3, ge=1, alias="minimumActiveStrategies")
    minimum_directional_agreement: float = Field(default=0.50, ge=0, le=1, alias="minimumDirectionalAgreement")
    minimum_average_confidence: float = Field(default=0.45, ge=0, le=1, alias="minimumAverageConfidence")


class WcaTradingSettings(WcaLegacyModel):
    starting_capital: float = Field(default=25000, ge=0, alias="startingCapital")
    order_allocation_percent: float = Field(default=10, ge=0, alias="orderAllocationPercent")
    daily_allocation_percent: float = Field(default=50, ge=0, alias="dailyAllocationPercent")
    take_profit_r: float = Field(default=1.5, ge=0, alias="takeProfitR")
    slippage_per_share: float = Field(default=0.02, ge=0, alias="slippagePerShare")
    use_default_sizing_settings: bool = Field(default=True, alias="useDefaultSizingSettings")
    base_risk_percent: float = Field(default=0.25, ge=0, alias="baseRiskPercent")
    max_position_percent: float = Field(default=50, ge=0, alias="maxPositionPercent")
    max_daily_trades: int = Field(default=10, ge=0, alias="maxDailyTrades")
    fixed_stop_distance_dollars: float = Field(default=0, ge=0, alias="fixedStopDistanceDollars")
    atr_stop_multiplier: float = Field(default=2, ge=0, alias="atrStopMultiplier")
    minimum_stop_distance_percent: float = Field(default=0.05, ge=0, alias="minimumStopDistancePercent")
    max_spread_percent: float = Field(default=0.03, ge=0, alias="maxSpreadPercent")
    minimum_one_minute_volume: float = Field(default=0, ge=0, alias="minimumOneMinuteVolume")
    max_participation_percent: float = Field(default=0.3, ge=0, alias="maxParticipationPercent")
    max_allowed_shares: int = Field(default=0, ge=0, alias="maxAllowedShares")
    max_daily_loss_percent: float = Field(default=1, ge=0, alias="maxDailyLossPercent")
    pyramiding_enabled: bool = Field(default=True, alias="pyramidingEnabled")


class WcaLegacyStrategySignal(WcaLegacyModel):
    key: str = Field(min_length=1)
    strategy: str = Field(min_length=1)
    name: str = Field(min_length=1)
    family: str = "unknown"
    signal: str
    confidence: float = Field(ge=0, le=1)
    base_weight: float = Field(ge=0, alias="baseWeight")
    weight_multiplier: float = Field(default=1, ge=0, alias="weightMultiplier")
    effective_weight: float = Field(ge=0, alias="effectiveWeight")
    direction: int
    reason: str = ""


class WcaLegacyHardFilter(WcaLegacyModel):
    label: str
    status: str
    detail: str = ""


class WcaSizingInputs(WcaLegacyModel):
    account_equity: float = Field(default=25000, ge=0, alias="accountEquity")
    price: float = Field(gt=0)
    base_risk_percent: float = Field(default=1, ge=0, alias="baseRiskPercent")
    order_allocation_percent: float = Field(default=10, ge=0, alias="orderAllocationPercent")
    daily_allocation_percent: float = Field(default=20, ge=0, alias="dailyAllocationPercent")
    max_position_percent: float = Field(default=10, ge=0, alias="maxPositionPercent")
    current_position_value: float = Field(default=0, ge=0, alias="currentPositionValue")
    atr: float = Field(default=0, ge=0)
    atr_stop_multiplier: float = Field(default=2, ge=0, alias="atrStopMultiplier")
    minimum_stop_distance_percent: float = Field(default=0.05, ge=0, alias="minimumStopDistancePercent")
    latest_volume: float = Field(default=0, ge=0, alias="latestVolume")
    max_participation_percent: float = Field(default=1, ge=0, alias="maxParticipationPercent")
    max_allowed_shares: int = Field(default=0, ge=0, alias="maxAllowedShares")


class WcaEvaluateRequest(WcaLegacyModel):
    snapshot_id: Optional[str] = Field(default=None, alias="snapshotId")
    symbol: str = Field(default="SPY", min_length=1)
    timestamp: Optional[datetime] = None
    market_snapshot: Optional[dict[str, Any]] = Field(default=None, alias="marketSnapshot")
    decision_settings: WcaDecisionSettings = Field(default_factory=WcaDecisionSettings, alias="decisionSettings")
    trading_settings: WcaTradingSettings = Field(default_factory=WcaTradingSettings, alias="tradingSettings")
    strategy_signals: tuple[WcaLegacyStrategySignal, ...] = Field(default=(), alias="strategySignals")
    hard_filters: tuple[WcaLegacyHardFilter, ...] = Field(default=(), alias="hardFilters")
    sizing_inputs: Optional[WcaSizingInputs] = Field(default=None, alias="sizingInputs")

    @model_validator(mode="before")
    @classmethod
    def map_fixture_id(cls, data: Any) -> Any:
        if isinstance(data, dict) and "snapshotId" not in data and "snapshot_id" not in data and "id" in data:
            return {**data, "snapshotId": data["id"]}
        return data


class WcaLegacySizingResult(WcaLegacyModel):
    signal_strength: float = Field(alias="signalStrength")
    size_multiplier: float = Field(alias="sizeMultiplier")
    risk_dollars: float = Field(alias="riskDollars")
    stop_distance: float = Field(alias="stopDistance")
    shares_by_risk: float = Field(alias="sharesByRisk")
    shares_by_order: float = Field(alias="sharesByOrder")
    shares_by_capital: float = Field(alias="sharesByCapital")
    shares_by_buying_power: float = Field(alias="sharesByBuyingPower")
    shares_by_liquidity: float = Field(alias="sharesByLiquidity")
    final_quantity: int = Field(alias="finalQuantity")
    available_buying_power: float = Field(alias="availableBuyingPower")
    account_equity: float = Field(alias="accountEquity")
    max_position_dollars: float = Field(alias="maxPositionDollars")
    current_position_value: float = Field(alias="currentPositionValue")
    limiting_factor: str = Field(alias="limitingFactor")
    blocked_reason: str = Field(default="", alias="blockedReason")


class WcaEvaluateResponse(WcaLegacyModel):
    algorithm_id: str = Field(default=WCA_ALGORITHM_ID, alias="algorithmId")
    configuration_version: str = Field(alias="configurationVersion")
    engine_version: str = Field(alias="engineVersion")
    base_weights: dict[str, float] = Field(alias="baseWeights")
    effective_weights: dict[str, float] = Field(alias="effectiveWeights")
    strategy_evaluations: tuple[WcaLegacyStrategySignal, ...] = Field(alias="strategyEvaluations")
    buy_score: float = Field(alias="buyScore")
    sell_score: float = Field(alias="sellScore")
    net_score: float = Field(alias="netScore")
    active_weight: float = Field(alias="activeWeight")
    normalized_net_score: float = Field(alias="normalizedNetScore")
    active_strategy_count: int = Field(alias="activeStrategyCount")
    buy_weight: float = Field(alias="buyWeight")
    sell_weight: float = Field(alias="sellWeight")
    buy_agreement: float = Field(alias="buyAgreement")
    sell_agreement: float = Field(alias="sellAgreement")
    buy_average_confidence: float = Field(alias="buyAverageConfidence")
    sell_average_confidence: float = Field(alias="sellAverageConfidence")
    raw_decision: str = Field(alias="rawDecision")
    raw_signal: str = Field(alias="rawSignal")
    local_gate_result: tuple[WcaLegacyHardFilter, ...] = Field(alias="localGateResult")
    effective_decision: str = Field(alias="effectiveDecision")
    signal: str
    sizing_result: WcaLegacySizingResult = Field(alias="sizingResult")
    proposed_order: Optional[ProposedOrder] = Field(default=None, alias="proposedOrder")
    reason_codes: tuple[str, ...] = Field(default=(), alias="reasonCodes")
    decision: Optional[WcaDecision] = None
