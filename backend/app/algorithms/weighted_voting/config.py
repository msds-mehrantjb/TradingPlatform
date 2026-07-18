"""Versioned configuration for Weighted Voting."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from typing import Any

from backend.app.algorithms.weighted_voting.catalog import WEIGHTED_VOTING_STRATEGY_CATALOG
from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_CONFIGURATION_VERSION

WEIGHTED_VOTING_CONFIG_VERSION = WEIGHTED_VOTING_CONFIGURATION_VERSION
WEIGHTED_VOTING_CONFIGURATION_NAMESPACE = "weighted_voting"
WEIGHTED_VOTING_BASELINE_CONFIGURATION_KEY = "weighted_voting.config.baseline"


def _strategy_enablement() -> dict[str, bool]:
    return {entry.strategy_id: entry.enabled for entry in WEIGHTED_VOTING_STRATEGY_CATALOG}


def _strategy_baseline_weights() -> dict[str, float]:
    return {entry.strategy_id: entry.baseline_weight for entry in WEIGHTED_VOTING_STRATEGY_CATALOG}


def _strategy_minimum_weights() -> dict[str, float]:
    return {entry.strategy_id: entry.minimum_weight for entry in WEIGHTED_VOTING_STRATEGY_CATALOG}


def _strategy_maximum_weights() -> dict[str, float]:
    return {entry.strategy_id: entry.maximum_weight for entry in WEIGHTED_VOTING_STRATEGY_CATALOG}


def _strategy_session_windows() -> dict[str, str]:
    return {entry.strategy_id: entry.valid_session_window for entry in WEIGHTED_VOTING_STRATEGY_CATALOG}


@dataclass(frozen=True)
class WeightedVotingConfig:
    config_version: str = WEIGHTED_VOTING_CONFIG_VERSION
    configuration_namespace: str = WEIGHTED_VOTING_CONFIGURATION_NAMESPACE
    configuration_key: str = WEIGHTED_VOTING_BASELINE_CONFIGURATION_KEY
    strategy_enablement: dict[str, bool] = field(default_factory=_strategy_enablement)
    strategy_baseline_weights: dict[str, float] = field(default_factory=_strategy_baseline_weights)
    strategy_minimum_weights: dict[str, float] = field(default_factory=_strategy_minimum_weights)
    strategy_maximum_weights: dict[str, float] = field(default_factory=_strategy_maximum_weights)
    strategy_session_windows: dict[str, str] = field(default_factory=_strategy_session_windows)
    minimum_score: float = 0.58
    minimum_edge: float = 0.12
    minimum_active_strategies: int = 1
    minimum_directional_strategies: int = 1
    minimum_active_weight: float = 0.999999
    aggregation_tie_tolerance: float = 0.000001
    maximum_disagreement_score: float = 0.45
    minimum_expected_value_after_costs: float = 0.0
    expected_value_safety_margin: float = 0.0001
    local_max_spread_percent: float = 0.001
    local_minimum_liquidity_volume: float = 10000.0
    minimum_atr_percent: float = 0.0005
    maximum_atr_percent: float = 0.05
    minimum_entry_quality: float = 0.60
    maximum_weighted_daily_loss_percent: float = 3.0
    maximum_weighted_daily_trades: int = 10
    minimum_capital_available: float = 1.0
    allow_weighted_pyramiding: bool = False
    maximum_strategy_weight: float = 0.25
    maximum_family_weight: float = 0.40
    minimum_enabled_strategy_weight: float = 0.02
    equal_seed_weight: float = 0.125
    minimum_qualified_outcomes_for_adaptation: int = 40
    weight_smoothing_previous: float = 0.70
    weight_smoothing_candidate: float = 0.30
    maximum_daily_weight_change: float = 0.025
    expectancy_score_weight: float = 0.35
    profit_factor_score_weight: float = 0.20
    win_loss_score_weight: float = 0.15
    drawdown_score_weight: float = 0.10
    stability_score_weight: float = 0.10
    recent_performance_score_weight: float = 0.07
    regime_performance_score_weight: float = 0.03
    stale_after_seconds: int = 300
    regular_session_window: str = "09:30-16:00 America/New_York"
    decision_session_window: str = "09:45-15:45 America/New_York"
    data_freshness_limit_seconds: int = 300
    entry_slippage_per_share: float = 0.01
    exit_slippage_per_share: float = 0.01
    fee_per_share: float = 0.01
    transaction_cost_buffer_percent: float = 0.0001
    risk_per_trade_baseline_percent: float = 0.50
    daily_risk_baseline_percent: float = 3.0
    order_allocation_percent: float = 10.0
    daily_allocation_percent: float = 30.0
    maximum_position_percent: float = 10.0
    maximum_shares: int = 0
    maximum_participation_rate: float = 0.01
    atr_stop_multiplier: float = 1.5
    minimum_stop_distance_percent: float = 0.001
    target_r: float = 2.0
    entry_buffer_percent: float = 0.0005
    break_even_trigger_r: float = 1.0
    trailing_stop_atr_multiplier: float = 1.0
    time_stop_minutes: int = 120
    session_cutoff_minutes: int = 15
    force_flat_minutes_before_close: int = 1
    backtest_account_equity: float = 100000.0
    backtest_starting_cash: float = 100000.0
    backtest_decision_start_index: int = 1
    backtest_allow_short: bool = True
    backtest_session_cutoff_eastern_minutes: int = 945
    backtest_force_close_eastern_minutes: int = 959
    backtest_use_performance_weights: bool = False
    backtest_use_dynamic_settings: bool = True
    opening_range_minutes: int = 15
    minimum_breakout_distance: float = 0.0006
    maximum_opening_extension_atr: float = 1.8
    minimum_volume_ratio: float = 1.15
    minimum_vwap_distance: float = 0.002
    acceleration_reject_ratio: float = 1.25
    minimum_wick_ratio: float = 0.45
    minimum_band_extension_atr: float = 0.35
    compression_range_percent: float = 0.004
    expansion_range_percent: float = 0.006
    market_condition_trend_strong_slope: float = 0.006
    market_condition_trend_weak_slope: float = 0.0015
    market_condition_very_low_atr_percent: float = 0.001
    market_condition_low_atr_percent: float = 0.0025
    market_condition_high_atr_percent: float = 0.012
    market_condition_extreme_atr_percent: float = 0.025
    market_condition_good_relative_volume: float = 0.80
    market_condition_reduced_relative_volume: float = 0.40
    market_condition_poor_spread_percent: float = 0.003
    market_condition_reduced_spread_percent: float = 0.0015
    market_condition_elevated_gap_percent: float = 0.015
    market_condition_blocked_gap_percent: float = 0.04
    market_condition_hysteresis_confirmations: int = 3

    @property
    def configuration_hash(self) -> str:
        encoded = json.dumps(self._configuration_payload(), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]

    def baseline_configuration(self) -> dict[str, Any]:
        payload = self._configuration_payload()
        payload["configurationHash"] = self.configuration_hash
        payload["reasonCodes"] = ("weighted_voting.config.baseline_owned",)
        payload["explanation"] = "Versioned Weighted Voting baseline configuration owned under the weighted_voting namespace."
        return payload

    def _configuration_payload(self) -> dict[str, Any]:
        strategy_enablement = dict(self.strategy_enablement)
        strategy_baseline_weights = dict(self.strategy_baseline_weights)
        strategy_minimum_weights = dict(self.strategy_minimum_weights)
        strategy_maximum_weights = dict(self.strategy_maximum_weights)
        strategy_session_windows = dict(self.strategy_session_windows)
        payload = {
            "configVersion": self.config_version,
            "configurationNamespace": self.configuration_namespace,
            "configurationKey": self.configuration_key,
            "strategyEnablement": strategy_enablement,
            "strategyBaselineWeights": strategy_baseline_weights,
            "strategyMinimumWeights": strategy_minimum_weights,
            "strategyMaximumWeights": strategy_maximum_weights,
            "strategySessionWindows": strategy_session_windows,
            "minimumScore": self.minimum_score,
            "minimumEdge": self.minimum_edge,
            "minimumActiveStrategies": self.minimum_active_strategies,
            "minimumDirectionalStrategies": self.minimum_directional_strategies,
            "minimumActiveWeight": self.minimum_active_weight,
            "aggregationTieTolerance": self.aggregation_tie_tolerance,
            "maximumDisagreementScore": self.maximum_disagreement_score,
            "minimumExpectedValueAfterCosts": self.minimum_expected_value_after_costs,
            "expectedValueSafetyMargin": self.expected_value_safety_margin,
            "localMaxSpreadPercent": self.local_max_spread_percent,
            "localMinimumLiquidityVolume": self.local_minimum_liquidity_volume,
            "minimumAtrPercent": self.minimum_atr_percent,
            "maximumAtrPercent": self.maximum_atr_percent,
            "minimumEntryQuality": self.minimum_entry_quality,
            "maximumWeightedDailyLossPercent": self.maximum_weighted_daily_loss_percent,
            "maximumWeightedDailyTrades": self.maximum_weighted_daily_trades,
            "minimumCapitalAvailable": self.minimum_capital_available,
            "allowWeightedPyramiding": self.allow_weighted_pyramiding,
            "baselineSettings": {
                "strategyEnablement": strategy_enablement,
                "baselineWeights": strategy_baseline_weights,
                "minimumWeights": strategy_minimum_weights,
                "maximumWeights": strategy_maximum_weights,
            },
            "decisionThresholds": {
                "minimumWinningScore": self.minimum_score,
                "minimumSignalEdge": self.minimum_edge,
                "minimumActiveStrategies": self.minimum_active_strategies,
                "minimumDirectionalStrategies": self.minimum_directional_strategies,
                "minimumActiveWeight": self.minimum_active_weight,
            },
            "sessionWindows": {
                "regularSession": self.regular_session_window,
                "decisionSession": self.decision_session_window,
                "strategySessions": strategy_session_windows,
            },
            "dataFreshnessLimits": {
                "marketDataSeconds": self.data_freshness_limit_seconds,
                "staleAfterSeconds": self.stale_after_seconds,
            },
            "localGateLimits": {
                "spreadLimitPercent": self.local_max_spread_percent,
                "minimumLiquidityVolume": self.local_minimum_liquidity_volume,
                "minimumAtrPercent": self.minimum_atr_percent,
                "maximumAtrPercent": self.maximum_atr_percent,
                "minimumEntryQuality": self.minimum_entry_quality,
                "maximumDailyLossPercent": self.maximum_weighted_daily_loss_percent,
                "maximumTrades": self.maximum_weighted_daily_trades,
                "minimumCapitalAvailable": self.minimum_capital_available,
            },
            "transactionCostAssumptions": {
                "entrySlippagePerShare": self.entry_slippage_per_share,
                "exitSlippagePerShare": self.exit_slippage_per_share,
                "feePerShare": self.fee_per_share,
                "costBufferPercent": self.transaction_cost_buffer_percent,
            },
            "riskBudget": {
                "riskPerTradeBaselinePercent": self.risk_per_trade_baseline_percent,
                "dailyRiskBaselinePercent": self.daily_risk_baseline_percent,
                "maximumWeightedDailyLossPercent": self.maximum_weighted_daily_loss_percent,
            },
            "positionLimits": {
                "orderAllocationPercent": self.order_allocation_percent,
                "dailyAllocationPercent": self.daily_allocation_percent,
                "maximumPositionPercent": self.maximum_position_percent,
                "maximumShares": self.maximum_shares,
                "maximumParticipationRate": self.maximum_participation_rate,
                "minimumCapitalAvailable": self.minimum_capital_available,
                "allowWeightedPyramiding": self.allow_weighted_pyramiding,
            },
            "tradeLimits": {
                "maximumTrades": self.maximum_weighted_daily_trades,
                "maximumDailyLossPercent": self.maximum_weighted_daily_loss_percent,
                "minimumCapitalAvailable": self.minimum_capital_available,
                "allowWeightedPyramiding": self.allow_weighted_pyramiding,
            },
            "stopRules": {
                "atrStopMultiplier": self.atr_stop_multiplier,
                "minimumStopDistancePercent": self.minimum_stop_distance_percent,
            },
            "targetRules": {
                "targetR": self.target_r,
                "breakEvenTriggerR": self.break_even_trigger_r,
            },
            "exitRules": {
                "trailingStopAtrMultiplier": self.trailing_stop_atr_multiplier,
                "timeStopMinutes": self.time_stop_minutes,
                "sessionCutoffMinutes": self.session_cutoff_minutes,
                "forceFlatMinutesBeforeClose": self.force_flat_minutes_before_close,
            },
            "backtestSettings": {
                "accountEquity": self.backtest_account_equity,
                "startingCash": self.backtest_starting_cash,
                "decisionStartIndex": self.backtest_decision_start_index,
                "allowShort": self.backtest_allow_short,
                "sessionCutoffEasternMinutes": self.backtest_session_cutoff_eastern_minutes,
                "forceCloseEasternMinutes": self.backtest_force_close_eastern_minutes,
                "entrySlippagePerShare": self.entry_slippage_per_share,
                "exitSlippagePerShare": self.exit_slippage_per_share,
                "feePerShare": self.fee_per_share,
                "usePerformanceWeights": self.backtest_use_performance_weights,
                "useDynamicSettings": self.backtest_use_dynamic_settings,
            },
            "weightUpdateSettings": {
                "minimumQualifiedOutcomesForAdaptation": self.minimum_qualified_outcomes_for_adaptation,
                "weightSmoothingPrevious": self.weight_smoothing_previous,
                "weightSmoothingCandidate": self.weight_smoothing_candidate,
                "maximumDailyWeightChange": self.maximum_daily_weight_change,
                "expectancyScoreWeight": self.expectancy_score_weight,
                "profitFactorScoreWeight": self.profit_factor_score_weight,
                "winLossScoreWeight": self.win_loss_score_weight,
                "drawdownScoreWeight": self.drawdown_score_weight,
                "stabilityScoreWeight": self.stability_score_weight,
                "recentPerformanceScoreWeight": self.recent_performance_score_weight,
                "regimePerformanceScoreWeight": self.regime_performance_score_weight,
            },
            "marketConditionThresholds": {
                "trendStrongSlope": self.market_condition_trend_strong_slope,
                "trendWeakSlope": self.market_condition_trend_weak_slope,
                "veryLowAtrPercent": self.market_condition_very_low_atr_percent,
                "lowAtrPercent": self.market_condition_low_atr_percent,
                "highAtrPercent": self.market_condition_high_atr_percent,
                "extremeAtrPercent": self.market_condition_extreme_atr_percent,
                "goodRelativeVolume": self.market_condition_good_relative_volume,
                "reducedRelativeVolume": self.market_condition_reduced_relative_volume,
                "poorSpreadPercent": self.market_condition_poor_spread_percent,
                "reducedSpreadPercent": self.market_condition_reduced_spread_percent,
                "elevatedGapPercent": self.market_condition_elevated_gap_percent,
                "blockedGapPercent": self.market_condition_blocked_gap_percent,
                "hysteresisConfirmations": self.market_condition_hysteresis_confirmations,
            },
            "maximumStrategyWeight": self.maximum_strategy_weight,
            "maximumFamilyWeight": self.maximum_family_weight,
            "minimumEnabledStrategyWeight": self.minimum_enabled_strategy_weight,
            "equalSeedWeight": self.equal_seed_weight,
            "minimumQualifiedOutcomesForAdaptation": self.minimum_qualified_outcomes_for_adaptation,
            "weightSmoothingPrevious": self.weight_smoothing_previous,
            "weightSmoothingCandidate": self.weight_smoothing_candidate,
            "maximumDailyWeightChange": self.maximum_daily_weight_change,
            "expectancyScoreWeight": self.expectancy_score_weight,
            "profitFactorScoreWeight": self.profit_factor_score_weight,
            "winLossScoreWeight": self.win_loss_score_weight,
            "drawdownScoreWeight": self.drawdown_score_weight,
            "stabilityScoreWeight": self.stability_score_weight,
            "recentPerformanceScoreWeight": self.recent_performance_score_weight,
            "regimePerformanceScoreWeight": self.regime_performance_score_weight,
            "staleAfterSeconds": self.stale_after_seconds,
            "openingRangeMinutes": self.opening_range_minutes,
            "minimumBreakoutDistance": self.minimum_breakout_distance,
            "maximumOpeningExtensionAtr": self.maximum_opening_extension_atr,
            "minimumVolumeRatio": self.minimum_volume_ratio,
            "minimumVwapDistance": self.minimum_vwap_distance,
            "accelerationRejectRatio": self.acceleration_reject_ratio,
            "minimumWickRatio": self.minimum_wick_ratio,
            "minimumBandExtensionAtr": self.minimum_band_extension_atr,
            "compressionRangePercent": self.compression_range_percent,
            "expansionRangePercent": self.expansion_range_percent,
            "marketConditionTrendStrongSlope": self.market_condition_trend_strong_slope,
            "marketConditionTrendWeakSlope": self.market_condition_trend_weak_slope,
            "marketConditionVeryLowAtrPercent": self.market_condition_very_low_atr_percent,
            "marketConditionLowAtrPercent": self.market_condition_low_atr_percent,
            "marketConditionHighAtrPercent": self.market_condition_high_atr_percent,
            "marketConditionExtremeAtrPercent": self.market_condition_extreme_atr_percent,
            "marketConditionGoodRelativeVolume": self.market_condition_good_relative_volume,
            "marketConditionReducedRelativeVolume": self.market_condition_reduced_relative_volume,
            "marketConditionPoorSpreadPercent": self.market_condition_poor_spread_percent,
            "marketConditionReducedSpreadPercent": self.market_condition_reduced_spread_percent,
            "marketConditionElevatedGapPercent": self.market_condition_elevated_gap_percent,
            "marketConditionBlockedGapPercent": self.market_condition_blocked_gap_percent,
            "marketConditionHysteresisConfirmations": self.market_condition_hysteresis_confirmations,
        }
        return payload
