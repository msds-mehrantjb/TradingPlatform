"""Meta-Strategy-owned strategy registry."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyContractModel
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.versions import META_STRATEGY_STRATEGY_CATALOG_VERSION


META_STRATEGY_REGISTRY_VERSION = "meta_strategy_registry_v1"
META_STRATEGY_STRATEGY_VERSION = "meta_strategy_strategy_v1"
META_STRATEGY_STRATEGY_PACKAGE = "backend.app.algorithms.meta_strategy.strategies"


class MetaStrategyRole(str, Enum):
    DIRECTIONAL = "DIRECTIONAL"
    CONTEXT = "CONTEXT"
    REGIME = "REGIME"
    SAFETY = "SAFETY"


class MetaStrategyFamily(str, Enum):
    TREND = "TREND"
    BREAKOUT = "BREAKOUT"
    REVERSAL = "REVERSAL"
    MEAN_REVERSION = "MEAN_REVERSION"
    GAP_SESSION = "GAP_SESSION"
    MARKET_CONTEXT = "MARKET_CONTEXT"
    REGIME = "REGIME"
    SAFETY = "SAFETY"


class MetaStrategyDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class MetaStrategyConfigurationSchema(MetaStrategyContractModel):
    schema_id: str = Field(min_length=1)
    properties: dict[str, Any] = Field(default_factory=dict)
    required: tuple[str, ...] = ()


class MetaStrategyRegistryEntry(MetaStrategyContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    algorithm_id: Literal["meta_strategy"] = ALGORITHM_ID
    strategy_id: str = Field(min_length=1)
    strategy_name: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    role: MetaStrategyRole
    family: MetaStrategyFamily
    required_inputs: tuple[str, ...] = Field(min_length=1)
    minimum_warmup: int = Field(ge=0)
    enabled: bool
    supported_directions: tuple[MetaStrategyDirection, ...] = Field(min_length=1)
    configuration_schema: MetaStrategyConfigurationSchema
    implementation_module: str = Field(min_length=1)
    implementation_class: str = Field(min_length=1)
    canonical_influence_id: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()

    @model_validator(mode="after")
    def implementation_must_be_meta_strategy_owned(self) -> MetaStrategyRegistryEntry:
        if not self.implementation_module.startswith(META_STRATEGY_STRATEGY_PACKAGE):
            raise ValueError("strategy implementation must be owned by Meta-Strategy")
        if self.canonical_influence_id != self.strategy_id:
            raise ValueError("canonical influence id must match the strategy id")
        if self.role != MetaStrategyRole.DIRECTIONAL and any(direction != MetaStrategyDirection.HOLD for direction in self.supported_directions):
            raise ValueError("non-directional strategies cannot declare directional influence")
        return self


def meta_strategy_strategy_catalog() -> tuple[MetaStrategyRegistryEntry, ...]:
    return ALL_META_STRATEGY_STRATEGIES


def validate_meta_strategy_registry(entries: tuple[MetaStrategyRegistryEntry, ...] | list[MetaStrategyRegistryEntry]) -> dict[str, Any]:
    strategy_ids = [entry.strategy_id for entry in entries]
    duplicate_ids = tuple(sorted(strategy_id for strategy_id in set(strategy_ids) if strategy_ids.count(strategy_id) > 1))
    foreign = tuple(entry.strategy_id for entry in entries if entry.algorithm_id != ALGORITHM_ID or not entry.implementation_module.startswith(META_STRATEGY_STRATEGY_PACKAGE))
    alias_targets = tuple(sorted(target for target in META_STRATEGY_ALIAS_MAP.values() if target not in strategy_ids))
    valid = not duplicate_ids and not foreign and not alias_targets
    return {
        "algorithmId": ALGORITHM_ID,
        "registryVersion": META_STRATEGY_REGISTRY_VERSION,
        "strategyCatalogVersion": META_STRATEGY_STRATEGY_CATALOG_VERSION,
        "valid": valid,
        "strategyCount": len(entries),
        "duplicateStrategyIds": duplicate_ids,
        "foreignImplementations": foreign,
        "missingAliasTargets": alias_targets,
        "reasonCodes": ("meta_strategy.registry.valid" if valid else "meta_strategy.registry.invalid",),
    }


def canonical_strategy_id(name_or_id: str) -> str:
    normalized = str(name_or_id).strip()
    if normalized in META_STRATEGY_BY_ID:
        return normalized
    if normalized in META_STRATEGY_BY_NAME:
        return META_STRATEGY_BY_NAME[normalized].strategy_id
    if normalized in META_STRATEGY_ALIAS_MAP:
        return META_STRATEGY_ALIAS_MAP[normalized]
    raise KeyError(f"Unknown Meta-Strategy strategy: {name_or_id}")


def resolve_strategy(name_or_id: str) -> MetaStrategyRegistryEntry:
    return META_STRATEGY_BY_ID[canonical_strategy_id(name_or_id)]


def resolve_strategy_list(names_or_ids: tuple[str, ...] | list[str]) -> tuple[MetaStrategyRegistryEntry, ...]:
    resolved: list[MetaStrategyRegistryEntry] = []
    seen: set[str] = set()
    for value in names_or_ids:
        entry = resolve_strategy(value)
        if entry.canonical_influence_id in seen:
            continue
        seen.add(entry.canonical_influence_id)
        resolved.append(entry)
    return tuple(resolved)


def influence_strategy_ids(names_or_ids: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(entry.canonical_influence_id for entry in resolve_strategy_list(names_or_ids))


def directional_strategy_input_ids() -> tuple[str, ...]:
    return tuple(entry.strategy_id for entry in DIRECTIONAL_STRATEGIES)


def _schema(schema_id: str, properties: dict[str, Any] | None = None, required: tuple[str, ...] = ()) -> MetaStrategyConfigurationSchema:
    return MetaStrategyConfigurationSchema(
        schema_id=schema_id,
        properties=properties or {"enabled": {"type": "boolean"}},
        required=required,
    )


def _entry(
    strategy_id: str,
    strategy_name: str,
    role: MetaStrategyRole,
    family: MetaStrategyFamily,
    required_inputs: tuple[str, ...],
    minimum_warmup: int,
    supported_directions: tuple[MetaStrategyDirection, ...],
    implementation_module: str,
    implementation_class: str,
    *,
    enabled: bool = True,
    aliases: tuple[str, ...] = (),
    configuration_schema: MetaStrategyConfigurationSchema | None = None,
) -> MetaStrategyRegistryEntry:
    return MetaStrategyRegistryEntry(
        strategy_id=strategy_id,
        strategy_name=strategy_name,
        strategy_version=META_STRATEGY_STRATEGY_VERSION,
        role=role,
        family=family,
        required_inputs=required_inputs,
        minimum_warmup=minimum_warmup,
        enabled=enabled,
        supported_directions=supported_directions,
        configuration_schema=configuration_schema or _schema(f"{strategy_id}.config.v1"),
        implementation_module=implementation_module,
        implementation_class=implementation_class,
        canonical_influence_id=strategy_id,
        aliases=aliases,
    )


DIRECTIONAL_DIRECTIONS = (MetaStrategyDirection.BUY, MetaStrategyDirection.SELL, MetaStrategyDirection.HOLD)
CONTEXT_DIRECTIONS = (MetaStrategyDirection.HOLD,)

DIRECTIONAL_STRATEGIES: tuple[MetaStrategyRegistryEntry, ...] = (
    _entry("multi_timeframe_trend_alignment", "Multi-Timeframe Trend Alignment", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.TREND, ("candles", "moving_averages", "vwap", "atr", "adx"), 50, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.multi_timeframe_trend_alignment", "MultiTimeframeTrendAlignmentStrategy"),
    _entry("first_pullback_after_open", "First Pullback After Open", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.TREND, ("candles", "session_phase", "vwap", "relative_volume"), 30, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.first_pullback_after_open", "FirstPullbackAfterOpenStrategy"),
    _entry("vwap_trend_continuation", "VWAP Trend Continuation", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.TREND, ("candles", "vwap", "moving_averages", "relative_volume"), 30, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.vwap_trend_continuation", "VwapTrendContinuationStrategy"),
    _entry("opening_range_breakout", "Opening Range Breakout", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.BREAKOUT, ("candles", "atr", "relative_volume", "spread", "liquidity", "openingRangeHigh", "openingRangeLow"), 30, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.opening_range_breakout", "OpeningRangeBreakoutStrategy"),
    _entry("volatility_breakout", "Volatility Breakout", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.BREAKOUT, ("candles", "atr", "bollinger_bands", "relative_volume", "spread"), 50, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.volatility_breakout", "VolatilityBreakoutStrategy"),
    _entry("failed_breakout_reversal", "Failed Breakout Reversal", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.REVERSAL, ("candles", "atr", "spread", "liquidity", "failedBreakoutSide"), 40, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.failed_breakout_reversal", "FailedBreakoutReversalStrategy", aliases=("Failed Breakout Strategy", "Failed Breakout Reversal")),
    _entry("liquidity_sweep_reversal", "Liquidity Sweep Reversal", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.REVERSAL, ("candles", "liquidity", "spread", "volume", "sweepSide"), 40, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.liquidity_sweep_reversal", "LiquiditySweepReversalStrategy"),
    _entry("vwap_mean_reversion", "VWAP Mean Reversion", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.MEAN_REVERSION, ("candles", "vwap", "adx", "rsi", "volume"), 40, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.vwap_mean_reversion", "VwapMeanReversionStrategy"),
    _entry("bollinger_atr_reversion", "Bollinger/ATR Reversion", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.MEAN_REVERSION, ("candles", "bollinger_bands", "atr", "adx", "rsi"), 50, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.bollinger_atr_reversion", "BollingerAtrReversionStrategy", aliases=("Bollinger Band Reversion", "ATR Overextension Reversion", "Bollinger/ATR Reversion")),
    _entry("gap_continuation_gap_fade", "Gap Continuation / Gap Fade", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.GAP_SESSION, ("candles", "gap_state", "session_phase", "qqq_iwm_context", "economic_event_state"), 30, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.gap_continuation_gap_fade", "GapContinuationGapFadeStrategy"),
)

CONTEXT_STRATEGIES: tuple[MetaStrategyRegistryEntry, ...] = (
    _entry("relative_strength_qqq_iwm", "Relative Strength vs QQQ/IWM", MetaStrategyRole.CONTEXT, MetaStrategyFamily.MARKET_CONTEXT, ("qqq_iwm_context",), 20, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.context.relative_strength_qqq_iwm", "RelativeStrengthQqqIwmStrategy"),
    _entry("market_breadth_momentum", "Market Breadth Momentum", MetaStrategyRole.CONTEXT, MetaStrategyFamily.MARKET_CONTEXT, ("breadth",), 20, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.context.market_breadth_momentum", "MarketBreadthMomentumStrategy"),
    _entry("economic_event_context", "Economic Event Context", MetaStrategyRole.CONTEXT, MetaStrategyFamily.MARKET_CONTEXT, ("economic_event_state", "session_phase", "spread"), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.context.economic_event_context", "EconomicEventContextStrategy", aliases=("Economic Event Reaction Strategy", "Economic Event Context")),
    _entry("market_structure_context", "Market Structure Context", MetaStrategyRole.CONTEXT, MetaStrategyFamily.MARKET_CONTEXT, ("candles", "moving_averages", "atr"), 30, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.context.market_structure_context", "MarketStructureContextStrategy"),
    _entry("volume_confirmation", "Volume Confirmation", MetaStrategyRole.CONTEXT, MetaStrategyFamily.MARKET_CONTEXT, ("volume", "relative_volume"), 20, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.context.volume_confirmation", "VolumeConfirmationStrategy"),
    _entry("vwap_position_context", "VWAP Position Context", MetaStrategyRole.CONTEXT, MetaStrategyFamily.MARKET_CONTEXT, ("vwap", "moving_averages"), 20, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.context.vwap_position_context", "VwapPositionContextStrategy", aliases=("VWAP Position Strategy", "VWAP Position Context")),
)

REGIME_STRATEGIES: tuple[MetaStrategyRegistryEntry, ...] = (
    _entry("adx_trend_strength_regime", "ADX Trend Strength Regime", MetaStrategyRole.REGIME, MetaStrategyFamily.REGIME, ("adx", "atr", "moving_averages"), 50, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.regime.adx_trend_strength", "AdxTrendStrengthRegimeStrategy", aliases=("ADX Trend Strength Filter", "ADX Trend Strength Regime")),
    _entry("atr_volatility_regime", "ATR Volatility Regime", MetaStrategyRole.REGIME, MetaStrategyFamily.REGIME, ("atr", "relative_volume", "economic_event_state"), 50, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.regime.atr_volatility_regime", "AtrVolatilityRegimeStrategy"),
)

SAFETY_STRATEGIES: tuple[MetaStrategyRegistryEntry, ...] = (
    _entry("cash_avoid_trading_filter", "Cash / Avoid Trading Filter", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, ("cash_available", "avoid_trading"), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.cash_avoid_trading", "CashAvoidTradingFilterStrategy"),
    _entry("missing_critical_data_filter", "Missing Critical Data", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, ("critical_data",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.missing_critical_data", "MissingCriticalDataFilterStrategy"),
    _entry("stale_market_data_filter", "Stale Market Data", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, ("source_cutoff_timestamp",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.stale_market_data", "StaleMarketDataFilterStrategy"),
    _entry("excessive_spread_filter", "Excessive Spread", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, ("spread",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.excessive_spread", "ExcessiveSpreadFilterStrategy"),
    _entry("insufficient_liquidity_filter", "Insufficient Liquidity", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, ("liquidity",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.insufficient_liquidity", "InsufficientLiquidityFilterStrategy"),
    _entry("extreme_volatility_filter", "Extreme Volatility", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, ("atr", "relative_volume"), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.extreme_volatility", "ExtremeVolatilityFilterStrategy"),
    _entry("economic_event_blackout_filter", "Economic Event Blackout", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, ("economic_event_state",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.economic_event_blackout", "EconomicEventBlackoutFilterStrategy"),
    _entry("unsupported_session_filter", "Unsupported Session", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, ("session_phase",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.unsupported_session", "UnsupportedSessionFilterStrategy"),
    _entry("halt_luld_filter", "Halt / LULD", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, ("halt_luld_state",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.halt_luld", "HaltLuldFilterStrategy"),
    _entry("operational_health_filter", "Operational Health", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, ("operational_health",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.operational_health", "OperationalHealthFilterStrategy"),
)

ALL_META_STRATEGY_STRATEGIES: tuple[MetaStrategyRegistryEntry, ...] = (
    *DIRECTIONAL_STRATEGIES,
    *CONTEXT_STRATEGIES,
    *REGIME_STRATEGIES,
    *SAFETY_STRATEGIES,
)

META_STRATEGY_BY_ID = {entry.strategy_id: entry for entry in ALL_META_STRATEGY_STRATEGIES}
META_STRATEGY_BY_NAME = {entry.strategy_name: entry for entry in ALL_META_STRATEGY_STRATEGIES}
META_STRATEGY_ALIAS_MAP = {
    alias: entry.strategy_id
    for entry in ALL_META_STRATEGY_STRATEGIES
    for alias in entry.aliases
}


__all__ = [
    "ALL_META_STRATEGY_STRATEGIES",
    "CONTEXT_STRATEGIES",
    "DIRECTIONAL_STRATEGIES",
    "META_STRATEGY_ALIAS_MAP",
    "META_STRATEGY_REGISTRY_VERSION",
    "META_STRATEGY_STRATEGY_PACKAGE",
    "META_STRATEGY_STRATEGY_VERSION",
    "REGIME_STRATEGIES",
    "SAFETY_STRATEGIES",
    "MetaStrategyConfigurationSchema",
    "MetaStrategyDirection",
    "MetaStrategyFamily",
    "MetaStrategyRegistryEntry",
    "MetaStrategyRole",
    "canonical_strategy_id",
    "directional_strategy_input_ids",
    "influence_strategy_ids",
    "meta_strategy_strategy_catalog",
    "resolve_strategy",
    "resolve_strategy_list",
    "validate_meta_strategy_registry",
]
