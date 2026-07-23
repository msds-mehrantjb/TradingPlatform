"""WCA strategy interfaces and registry metadata."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal, Protocol

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


WcaModuleLifecycleStatus = Literal["active", "shadow", "disabled", "unavailable", "not_data_ready", "deprecated_alias"]


@dataclass(frozen=True)
class WcaModuleStatus:
    id: str
    status: WcaModuleLifecycleStatus


@dataclass(frozen=True)
class WcaModuleInventory:
    algorithm_id: str
    primary_voters: tuple[WcaModuleStatus, ...]
    modifiers: tuple[WcaModuleStatus, ...]
    hard_filters: tuple[WcaModuleStatus, ...]


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
    WcaStrategyDefinition("C2", "trend_pullback", "First Pullback After Open", "trend", 0.25),
    WcaStrategyDefinition("C6", "bollinger_atr_reversion", "Bollinger/ATR Reversion", "mean_reversion", 0.25),
    WcaStrategyDefinition("C9", "failed_breakout_reversal", "Failed Breakout Reversal", "reversal", 0.25),
    WcaStrategyDefinition("C10", "liquidity_sweep_reversal", "Liquidity Sweep Reversal", "reversal", 0.25),
)

WCA_MODIFIER_REGISTRY: tuple[WcaModifierDefinition, ...] = (
    WcaModifierDefinition("adx_trend_strength", "ADX Trend Strength", "trend"),
    WcaModifierDefinition("atr_volatility_regime", "ATR Volatility Regime", "volatility"),
    WcaModifierDefinition("relative_strength_vs_qqq_iwm", "Relative Strength vs QQQ/IWM", "relative_strength"),
    WcaModifierDefinition("market_breadth", "Market Breadth", "breadth"),
)

WCA_HARD_FILTER_REGISTRY: tuple[WcaHardFilterDefinition, ...] = (
    WcaHardFilterDefinition("cash_avoid_trading", "Cash/Avoid Trading"),
)

WCA_PRIMARY_VOTER_SLUGS = frozenset(strategy.slug for strategy in WCA_STRATEGY_REGISTRY)
WCA_STRATEGY_IDS = frozenset(strategy.strategy_id for strategy in WCA_STRATEGY_REGISTRY)
WCA_MODIFIER_SLUGS = frozenset(modifier.slug for modifier in WCA_MODIFIER_REGISTRY)
WCA_HARD_FILTER_SLUGS = frozenset(hard_filter.slug for hard_filter in WCA_HARD_FILTER_REGISTRY)


WCA_MODULE_INVENTORY = WcaModuleInventory(
    algorithm_id="wca",
    primary_voters=tuple(WcaModuleStatus(id=strategy.slug, status="active") for strategy in WCA_STRATEGY_REGISTRY),
    modifiers=tuple(WcaModuleStatus(id=modifier.slug, status="active") for modifier in WCA_MODIFIER_REGISTRY),
    hard_filters=tuple(WcaModuleStatus(id=hard_filter.slug, status="active") for hard_filter in WCA_HARD_FILTER_REGISTRY),
)


def wca_module_inventory() -> WcaModuleInventory:
    return WCA_MODULE_INVENTORY


__all__ = [
    "StrategyConfig",
    "WCA_HARD_FILTER_REGISTRY",
    "WCA_HARD_FILTER_SLUGS",
    "WCA_MODIFIER_REGISTRY",
    "WCA_MODIFIER_SLUGS",
    "WCA_MODULE_INVENTORY",
    "WCA_PRIMARY_VOTER_SLUGS",
    "WCA_STRATEGY_IDS",
    "WCA_STRATEGY_REGISTRY",
    "WcaCatalogRole",
    "WcaHardFilterDefinition",
    "WcaModifierDefinition",
    "WcaModuleInventory",
    "WcaModuleLifecycleStatus",
    "WcaModuleStatus",
    "WcaStrategy",
    "WcaStrategyDefinition",
    "wca_module_inventory",
]
