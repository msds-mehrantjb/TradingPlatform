"""Versioned configuration for Weighted Voting."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from backend.app.algorithms.weighted_voting.identity import WEIGHTED_VOTING_CONFIGURATION_VERSION

WEIGHTED_VOTING_CONFIG_VERSION = WEIGHTED_VOTING_CONFIGURATION_VERSION


@dataclass(frozen=True)
class WeightedVotingConfig:
    config_version: str = WEIGHTED_VOTING_CONFIG_VERSION
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
        payload = {
            "configVersion": self.config_version,
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
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()[:16]
