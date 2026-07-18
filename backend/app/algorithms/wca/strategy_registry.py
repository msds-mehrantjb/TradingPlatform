"""WCA strategy interfaces and registry metadata."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from backend.app.algorithms.wca.contracts import WcaMarketSnapshot, WcaStrategyEvaluation


@dataclass(frozen=True)
class StrategyConfig:
    enabled: bool = True


class WcaStrategy(Protocol):
    """Pure strategy interface.

    Implementations must derive evaluations only from the supplied market
    snapshot and configuration.
    """

    strategy_id: str
    family: str
    version: str
    name: str

    def evaluate(self, market: WcaMarketSnapshot, config: StrategyConfig) -> WcaStrategyEvaluation:
        ...


class WcaCatalogRole(str, Enum):
    PRIMARY_VOTER = "primary_voter"
    MODIFIER = "modifier"
    HARD_FILTER = "hard_filter"


@dataclass(frozen=True)
class WcaStrategyDefinition:
    strategy_id: str
    slug: str
    name: str
    family: str
    base_weight: float
    role: WcaCatalogRole = WcaCatalogRole.PRIMARY_VOTER


@dataclass(frozen=True)
class WcaModifierDefinition:
    slug: str
    name: str
    family: str
    role: WcaCatalogRole = WcaCatalogRole.MODIFIER


@dataclass(frozen=True)
class WcaHardFilterDefinition:
    slug: str
    name: str
    family: str = "risk"
    role: WcaCatalogRole = WcaCatalogRole.HARD_FILTER


WCA_STRATEGY_REGISTRY: tuple[WcaStrategyDefinition, ...] = (
    WcaStrategyDefinition("C1", "moving_average_trend", "Moving Average Trend", "trend", 0.10),
    WcaStrategyDefinition("C2", "trend_pullback", "Trend Pullback", "trend", 0.09),
    WcaStrategyDefinition("C3", "vwap_trend_continuation", "VWAP Trend Continuation", "trend", 0.09),
    WcaStrategyDefinition("C4", "vwap_mean_reversion", "VWAP Mean Reversion", "mean_reversion", 0.08),
    WcaStrategyDefinition("C5", "rsi_mean_reversion", "RSI Mean Reversion", "mean_reversion", 0.08),
    WcaStrategyDefinition("C6", "bollinger_atr_reversion", "Bollinger/ATR Reversion", "mean_reversion", 0.08),
    WcaStrategyDefinition("C7", "opening_range_breakout", "Opening Range Breakout", "breakout", 0.10),
    WcaStrategyDefinition("C8", "intraday_volatility_breakout", "Intraday/Volatility Breakout", "breakout", 0.10),
    WcaStrategyDefinition("C9", "failed_breakout_reversal", "Failed Breakout Reversal", "reversal", 0.09),
    WcaStrategyDefinition("C10", "liquidity_sweep_reversal", "Liquidity Sweep Reversal", "reversal", 0.09),
    WcaStrategyDefinition("C11", "gap_continuation_fade", "Gap Continuation/Fade", "event", 0.10),
)

WCA_MODIFIER_REGISTRY: tuple[WcaModifierDefinition, ...] = (
    WcaModifierDefinition("vwap_position", "VWAP Position", "vwap"),
    WcaModifierDefinition("volume_confirmation", "Volume Confirmation", "volume"),
    WcaModifierDefinition("macd_momentum", "MACD Momentum", "momentum"),
    WcaModifierDefinition("market_structure", "Market Structure", "structure"),
    WcaModifierDefinition("adx_trend_strength", "ADX Trend Strength", "trend"),
    WcaModifierDefinition("atr_volatility_regime", "ATR Volatility Regime", "volatility"),
    WcaModifierDefinition("multi_timeframe_trend_alignment", "Multi-Timeframe Trend Alignment", "trend"),
    WcaModifierDefinition("relative_strength_vs_qqq_iwm", "Relative Strength vs QQQ/IWM", "relative_strength"),
    WcaModifierDefinition("market_breadth", "Market Breadth", "breadth"),
    WcaModifierDefinition("session_phase", "Session Phase", "session"),
    WcaModifierDefinition("spread_liquidity", "Spread/Liquidity", "liquidity"),
)

WCA_HARD_FILTER_REGISTRY: tuple[WcaHardFilterDefinition, ...] = (
    WcaHardFilterDefinition("cash_avoid_trading", "Cash/Avoid Trading"),
    WcaHardFilterDefinition("economic_event_risk", "Economic Event Risk"),
    WcaHardFilterDefinition("invalid_or_stale_data", "Invalid or Stale Data"),
    WcaHardFilterDefinition("unsafe_spread", "Unsafe Spread"),
    WcaHardFilterDefinition("unsafe_liquidity", "Unsafe Liquidity"),
    WcaHardFilterDefinition("extreme_volatility", "Extreme Volatility"),
    WcaHardFilterDefinition("session_entry_block", "Session Entry Block"),
)

WCA_PRIMARY_VOTER_SLUGS = frozenset(strategy.slug for strategy in WCA_STRATEGY_REGISTRY)
WCA_STRATEGY_IDS = frozenset(strategy.strategy_id for strategy in WCA_STRATEGY_REGISTRY)
WCA_MODIFIER_SLUGS = frozenset(modifier.slug for modifier in WCA_MODIFIER_REGISTRY)
WCA_HARD_FILTER_SLUGS = frozenset(hard_filter.slug for hard_filter in WCA_HARD_FILTER_REGISTRY)
