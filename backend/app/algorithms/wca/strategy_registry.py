"""Authoritative WCA module catalog and registry metadata."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from importlib import import_module
from typing import Any, Literal, Protocol

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
    implementation_import_path: str = ""
    settings_model: str = "backend.app.algorithms.wca.contracts.WcaBaselineSettings"
    settings_version: str = "wca_baseline_settings_v1"
    strategy_version: str = "wca_strategy_unversioned_v1"
    minimum_history: str = ""
    required_market_inputs: tuple[str, ...] = ()
    lifecycle: WcaModuleLifecycleStatus = "shadow"


@dataclass(frozen=True)
class WcaModifierDefinition:
    module_id: str
    slug: str
    name: str
    family: str
    role: WcaCatalogRole = WcaCatalogRole.MODIFIER
    implementation_import_path: str = ""
    settings_model: str = "backend.app.algorithms.wca.contracts.WcaBaselineSettings"
    settings_version: str = "wca_baseline_settings_v1"
    strategy_version: str = "wca_modifier_v1"
    minimum_history: str = ""
    required_market_inputs: tuple[str, ...] = ()
    lifecycle: WcaModuleLifecycleStatus = "shadow"


@dataclass(frozen=True)
class WcaHardFilterDefinition:
    module_id: str
    slug: str
    name: str
    family: str = "risk"
    role: WcaCatalogRole = WcaCatalogRole.HARD_FILTER
    implementation_import_path: str = "backend.app.algorithms.wca.local_gates"
    settings_model: str = "backend.app.algorithms.wca.contracts.WcaBaselineSettings"
    settings_version: str = "wca_baseline_settings_v1"
    strategy_version: str = "wca_hard_filter_v1"
    minimum_history: str = "current completed one-minute bar and entry context"
    required_market_inputs: tuple[str, ...] = ("completed_one_minute_bar", "wca_local_gate_context")
    lifecycle: WcaModuleLifecycleStatus = "shadow"


@dataclass(frozen=True)
class WcaDeprecatedAlias:
    alias_slug: str
    canonical_slug: str
    canonical_id: str
    lifecycle: WcaModuleLifecycleStatus = "deprecated_alias"


WCA_STRATEGY_REGISTRY: tuple[WcaStrategyDefinition, ...] = (
    WcaStrategyDefinition("C1", "moving_average_trend", "Moving Average Trend", "trend", 0.10, implementation_import_path="backend.app.algorithms.wca.strategies.moving_average_trend.MovingAverageTrendStrategy", settings_model="backend.app.algorithms.wca.configuration.MovingAverageTrendSettings", strategy_version="wca_moving_average_trend_v1", minimum_history="50 completed regular-session candles", required_market_inputs=("completed_one_minute_bars",), lifecycle="active"),
    WcaStrategyDefinition("C2", "first_pullback_after_open", "First Pullback After Open", "trend", 0.09, implementation_import_path="backend.app.algorithms.wca.strategies.trend_pullback.TrendPullbackStrategy", settings_model="backend.app.algorithms.wca.configuration.FirstPullbackAfterOpenSettings", strategy_version="wca_first_pullback_after_open_v1", minimum_history="30 completed regular-session candles", required_market_inputs=("completed_one_minute_bars", "vwap"), lifecycle="active"),
    WcaStrategyDefinition("C3", "vwap_trend_continuation", "VWAP Trend Continuation", "trend", 0.09, implementation_import_path="backend.app.algorithms.wca.strategies.vwap_trend_continuation.VwapTrendContinuationStrategy", settings_model="backend.app.algorithms.wca.configuration.VwapTrendContinuationSettings", strategy_version="wca_vwap_trend_continuation_v1", minimum_history="20 completed regular-session candles", required_market_inputs=("completed_one_minute_bars", "vwap", "volume"), lifecycle="active"),
    WcaStrategyDefinition("C4", "vwap_mean_reversion", "VWAP Mean Reversion", "mean_reversion", 0.08, implementation_import_path="backend.app.algorithms.wca.strategies.vwap_mean_reversion.VwapMeanReversionStrategy", settings_model="backend.app.algorithms.wca.configuration.VwapMeanReversionSettings", strategy_version="wca_vwap_mean_reversion_v1", minimum_history="20 completed regular-session candles", required_market_inputs=("completed_one_minute_bars", "vwap", "volume"), lifecycle="active"),
    WcaStrategyDefinition("C5", "rsi_mean_reversion", "RSI Mean Reversion", "mean_reversion", 0.08, implementation_import_path="backend.app.algorithms.wca.strategies.rsi_mean_reversion.RsiMeanReversionStrategy", settings_model="backend.app.algorithms.wca.configuration.RsiMeanReversionSettings", strategy_version="wca_rsi_mean_reversion_v1", minimum_history="15 completed regular-session candles", required_market_inputs=("completed_one_minute_bars",), lifecycle="active"),
    WcaStrategyDefinition("C6", "bollinger_atr_reversion", "Bollinger/ATR Reversion", "mean_reversion", 0.08, implementation_import_path="backend.app.algorithms.wca.strategies.bollinger_atr_reversion.BollingerAtrReversionStrategy", settings_model="backend.app.algorithms.wca.configuration.BollingerAtrReversionSettings", strategy_version="wca_bollinger_atr_reversion_v1", minimum_history="21 completed regular-session candles", required_market_inputs=("completed_one_minute_bars",), lifecycle="active"),
    WcaStrategyDefinition("C7", "opening_range_breakout", "Opening Range Breakout", "breakout", 0.10, implementation_import_path="backend.app.algorithms.wca.strategies.opening_range_breakout.OpeningRangeBreakoutStrategy", settings_model="backend.app.algorithms.wca.configuration.OpeningRangeBreakoutSettings", strategy_version="wca_opening_range_breakout_v1", minimum_history="15 opening-range candles and one confirmation candle", required_market_inputs=("completed_one_minute_bars", "volume"), lifecycle="active"),
    WcaStrategyDefinition("C8", "intraday_volatility_breakout", "Intraday/Volatility Breakout", "breakout", 0.10, implementation_import_path="backend.app.algorithms.wca.strategies.intraday_volatility_breakout.IntradayVolatilityBreakoutStrategy", settings_model="backend.app.algorithms.wca.configuration.IntradayVolatilityBreakoutSettings", strategy_version="wca_intraday_volatility_breakout_v1", minimum_history="31 completed intraday candles", required_market_inputs=("completed_one_minute_bars", "volume"), lifecycle="active"),
    WcaStrategyDefinition("C9", "failed_breakout_reversal", "Failed Breakout Reversal", "reversal", 0.09, implementation_import_path="backend.app.algorithms.wca.strategies.failed_breakout_reversal.FailedBreakoutReversalStrategy", settings_model="backend.app.algorithms.wca.configuration.FailedBreakoutReversalSettings", strategy_version="wca_failed_breakout_reversal_v1", minimum_history="22 completed regular-session candles", required_market_inputs=("completed_one_minute_bars",), lifecycle="active"),
    WcaStrategyDefinition("C10", "liquidity_sweep_reversal", "Liquidity Sweep Reversal", "reversal", 0.09, implementation_import_path="backend.app.algorithms.wca.strategies.liquidity_sweep_reversal.LiquiditySweepReversalStrategy", settings_model="backend.app.algorithms.wca.configuration.LiquiditySweepReversalSettings", strategy_version="wca_liquidity_sweep_reversal_v1", minimum_history="22 completed regular-session candles", required_market_inputs=("completed_one_minute_bars", "volume"), lifecycle="active"),
    WcaStrategyDefinition("C11", "gap_continuation_fade", "Gap Continuation/Fade", "event", 0.10, implementation_import_path="backend.app.algorithms.wca.strategies.gap_continuation_fade.GapContinuationFadeStrategy", settings_model="backend.app.algorithms.wca.configuration.GapContinuationFadeSettings", strategy_version="wca_gap_continuation_fade_v1", minimum_history="prior regular-session close, 15 opening-range candles, and one confirmation candle", required_market_inputs=("completed_one_minute_bars", "vwap", "volume"), lifecycle="active"),
)

WCA_MODIFIER_REGISTRY: tuple[WcaModifierDefinition, ...] = (
    WcaModifierDefinition("M1", "vwap_position", "VWAP Position", "vwap", implementation_import_path="backend.app.algorithms.wca.modifiers.vwap_position.VwapPositionModifier", settings_model="backend.app.algorithms.wca.configuration.VwapPositionSettings", minimum_history="current completed one-minute bar", required_market_inputs=("completed_one_minute_bars", "vwap"), lifecycle="active"),
    WcaModifierDefinition("M2", "volume_confirmation", "Volume Confirmation", "volume", implementation_import_path="backend.app.algorithms.wca.modifiers.volume_confirmation.VolumeConfirmationModifier", settings_model="backend.app.algorithms.wca.configuration.VolumeConfirmationSettings", minimum_history="20 completed one-minute bars", required_market_inputs=("completed_one_minute_bars", "volume"), lifecycle="active"),
    WcaModifierDefinition("M3", "macd_momentum", "MACD Momentum", "momentum", implementation_import_path="backend.app.algorithms.wca.modifiers.macd_momentum.MacdMomentumModifier", settings_model="backend.app.algorithms.wca.configuration.MacdMomentumSettings", minimum_history="35 completed one-minute bars", required_market_inputs=("completed_one_minute_bars",), lifecycle="active"),
    WcaModifierDefinition("M4", "market_structure", "Market Structure", "structure", implementation_import_path="backend.app.algorithms.wca.modifiers.market_structure.MarketStructureModifier", settings_model="backend.app.algorithms.wca.configuration.MarketStructureSettings", minimum_history="20 completed one-minute bars", required_market_inputs=("completed_one_minute_bars",), lifecycle="active"),
    WcaModifierDefinition("M5", "adx_trend_strength", "ADX Trend Strength", "trend", implementation_import_path="backend.app.algorithms.wca.modifiers.adx_trend_strength.AdxTrendStrengthModifier", settings_model="backend.app.algorithms.wca.configuration.AdxTrendStrengthSettings", minimum_history="30 completed one-minute bars", required_market_inputs=("completed_one_minute_bars",), lifecycle="active"),
    WcaModifierDefinition("M6", "atr_volatility_regime", "ATR Volatility Regime", "volatility", implementation_import_path="backend.app.algorithms.wca.modifiers.atr_volatility_regime.AtrVolatilityRegimeModifier", settings_model="backend.app.algorithms.wca.configuration.AtrVolatilityRegimeSettings", minimum_history="30 completed one-minute bars", required_market_inputs=("completed_one_minute_bars",), lifecycle="active"),
    WcaModifierDefinition("M7", "multi_timeframe_trend_alignment", "Multi-Timeframe Trend Alignment", "trend", implementation_import_path="backend.app.algorithms.wca.modifiers.multi_timeframe_trend_alignment.MultiTimeframeTrendAlignmentModifier", settings_model="backend.app.algorithms.wca.configuration.MultiTimeframeTrendAlignmentSettings", minimum_history="60 completed one-minute bars", required_market_inputs=("completed_one_minute_bars",), lifecycle="active"),
    WcaModifierDefinition("M8", "relative_strength_vs_qqq_iwm", "Relative Strength vs QQQ/IWM", "relative_strength", implementation_import_path="backend.app.algorithms.wca.modifiers.relative_strength_vs_qqq_iwm.RelativeStrengthVsQqqIwmModifier", settings_model="backend.app.algorithms.wca.configuration.RelativeStrengthVsQqqIwmSettings", minimum_history="60 completed one-minute bars", required_market_inputs=("completed_one_minute_bars", "relative_strength_proxies"), lifecycle="active"),
    WcaModifierDefinition("M9", "market_breadth", "Market Breadth", "breadth", implementation_import_path="backend.app.algorithms.wca.modifiers.market_breadth.MarketBreadthModifier", settings_model="backend.app.algorithms.wca.configuration.MarketBreadthSettings", minimum_history="current completed one-minute bar", required_market_inputs=("completed_one_minute_bars", "breadth_proxies"), lifecycle="active"),
    WcaModifierDefinition("M10", "session_phase", "Session Phase", "session", implementation_import_path="backend.app.algorithms.wca.modifiers.session_phase.SessionPhaseModifier", settings_model="backend.app.algorithms.wca.configuration.SessionPhaseSettings", minimum_history="current completed one-minute bar", required_market_inputs=("completed_one_minute_bars", "market_clock"), lifecycle="active"),
    WcaModifierDefinition("M11", "spread_liquidity", "Spread/Liquidity", "liquidity", implementation_import_path="backend.app.algorithms.wca.modifiers.spread_liquidity.SpreadLiquidityModifier", settings_model="backend.app.algorithms.wca.configuration.SpreadLiquiditySettings", minimum_history="current completed one-minute bar", required_market_inputs=("completed_one_minute_bars", "quote"), lifecycle="active"),
)

WCA_HARD_FILTER_REGISTRY: tuple[WcaHardFilterDefinition, ...] = (
    WcaHardFilterDefinition("H1", "cash_avoid_trading", "Cash/Avoid Trading", settings_model="backend.app.algorithms.wca.configuration.CashAvoidTradingSettings", lifecycle="active"),
    WcaHardFilterDefinition("H2", "economic_event_risk", "Economic Event Risk", settings_model="backend.app.algorithms.wca.configuration.EconomicEventRiskSettings", lifecycle="active"),
    WcaHardFilterDefinition("H3", "invalid_or_stale_data", "Invalid or Stale Data", settings_model="backend.app.algorithms.wca.configuration.InvalidOrStaleDataSettings", lifecycle="active"),
    WcaHardFilterDefinition("H4", "unsafe_spread", "Unsafe Spread", settings_model="backend.app.algorithms.wca.configuration.UnsafeSpreadSettings", lifecycle="active"),
    WcaHardFilterDefinition("H5", "unsafe_liquidity", "Unsafe Liquidity", settings_model="backend.app.algorithms.wca.configuration.UnsafeLiquiditySettings", lifecycle="active"),
    WcaHardFilterDefinition("H6", "extreme_volatility", "Extreme Volatility", settings_model="backend.app.algorithms.wca.configuration.ExtremeVolatilitySettings", lifecycle="active"),
    WcaHardFilterDefinition("H7", "session_entry_block", "Session Entry Block", settings_model="backend.app.algorithms.wca.configuration.SessionEntryBlockSettings", lifecycle="active"),
)

WCA_DEPRECATED_ALIASES: tuple[WcaDeprecatedAlias, ...] = (
    WcaDeprecatedAlias("trend_pullback", "first_pullback_after_open", "C2"),
)

WCA_MODULE_CATALOG = (*WCA_STRATEGY_REGISTRY, *WCA_MODIFIER_REGISTRY, *WCA_HARD_FILTER_REGISTRY)
WCA_PRIMARY_VOTER_SLUGS = frozenset(strategy.slug for strategy in WCA_STRATEGY_REGISTRY)
WCA_STRATEGY_IDS = frozenset(strategy.strategy_id for strategy in WCA_STRATEGY_REGISTRY)
WCA_MODIFIER_SLUGS = frozenset(modifier.slug for modifier in WCA_MODIFIER_REGISTRY)
WCA_HARD_FILTER_SLUGS = frozenset(hard_filter.slug for hard_filter in WCA_HARD_FILTER_REGISTRY)


WCA_MODULE_INVENTORY = WcaModuleInventory(
    algorithm_id="wca",
    primary_voters=tuple(WcaModuleStatus(id=strategy.slug, status=strategy.lifecycle) for strategy in WCA_STRATEGY_REGISTRY),
    modifiers=tuple(WcaModuleStatus(id=modifier.slug, status=modifier.lifecycle) for modifier in WCA_MODIFIER_REGISTRY),
    hard_filters=tuple(WcaModuleStatus(id=hard_filter.slug, status=hard_filter.lifecycle) for hard_filter in WCA_HARD_FILTER_REGISTRY),
)


def wca_module_inventory() -> WcaModuleInventory:
    return WCA_MODULE_INVENTORY


def resolve_wca_module_slug(slug: str) -> str:
    for alias in WCA_DEPRECATED_ALIASES:
        if alias.alias_slug == slug:
            return alias.canonical_slug
    return slug


def wca_strategy_definition_for(strategy_id_or_slug: str) -> WcaStrategyDefinition:
    canonical = resolve_wca_module_slug(strategy_id_or_slug)
    for entry in WCA_STRATEGY_REGISTRY:
        if entry.strategy_id == strategy_id_or_slug or entry.slug == canonical:
            return entry
    raise KeyError(strategy_id_or_slug)


def build_wca_primary_voters(*, include_shadow: bool = True) -> tuple[WcaStrategy, ...]:
    allowed_lifecycles = {"active", "shadow"} if include_shadow else {"active"}
    voters: list[WcaStrategy] = []
    for entry in WCA_STRATEGY_REGISTRY:
        if entry.lifecycle not in allowed_lifecycles:
            continue
        implementation = _load_symbol(entry.implementation_import_path)
        if implementation is None:
            raise RuntimeError(f"WCA primary voter {entry.slug} has no executable implementation")
        instance = implementation()
        _metadata_errors(entry, instance, strict=True)
        voters.append(instance)
    return tuple(voters)


def build_wca_modifiers() -> tuple[Any, ...]:
    modifiers: list[Any] = []
    for entry in WCA_MODIFIER_REGISTRY:
        if entry.lifecycle not in {"active", "shadow"}:
            continue
        implementation = _load_symbol(entry.implementation_import_path)
        if implementation is None:
            raise RuntimeError(f"WCA modifier {entry.slug} has no executable implementation")
        instance = implementation()
        _metadata_errors(entry, instance, strict=True)
        modifiers.append(instance)
    return tuple(modifiers)


def validate_wca_module_catalog(
    strategy_registry: tuple[WcaStrategyDefinition, ...] = WCA_STRATEGY_REGISTRY,
    modifier_registry: tuple[WcaModifierDefinition, ...] = WCA_MODIFIER_REGISTRY,
    hard_filter_registry: tuple[WcaHardFilterDefinition, ...] = WCA_HARD_FILTER_REGISTRY,
    deprecated_aliases: tuple[WcaDeprecatedAlias, ...] = WCA_DEPRECATED_ALIASES,
) -> dict[str, Any]:
    errors: list[str] = []
    module_catalog = (*strategy_registry, *modifier_registry, *hard_filter_registry)
    primary_slugs = frozenset(strategy.slug for strategy in strategy_registry)
    modifier_slugs = frozenset(modifier.slug for modifier in modifier_registry)
    hard_filter_slugs = frozenset(hard_filter.slug for hard_filter in hard_filter_registry)

    errors.extend(_duplicate_errors("id", tuple(_catalog_id(entry) for entry in module_catalog)))
    errors.extend(_duplicate_errors("slug", tuple(entry.slug for entry in module_catalog)))

    baseline_total = sum(entry.base_weight for entry in strategy_registry)
    if abs(baseline_total - 1.0) > 0.000001:
        errors.append(f"primary_baseline_weights_total:{baseline_total:.10f}")

    if any(entry.role is not WcaCatalogRole.PRIMARY_VOTER for entry in strategy_registry):
        errors.append("strategy_registry_contains_non_primary_role")
    if any(entry.role is WcaCatalogRole.PRIMARY_VOTER for entry in (*modifier_registry, *hard_filter_registry)):
        errors.append("modifier_or_hard_filter_registered_as_primary")
    if primary_slugs & (modifier_slugs | hard_filter_slugs):
        errors.append("primary_slug_overlaps_modifier_or_hard_filter")

    for entry in module_catalog:
        implementation = _load_symbol(entry.implementation_import_path)
        if entry.lifecycle == "active" and implementation is None:
            errors.append(f"active_module_missing_implementation:{entry.slug}")
        if entry.lifecycle == "active" and isinstance(entry, WcaStrategyDefinition) and not entry.settings_model:
            errors.append(f"active_strategy_missing_settings_model:{entry.slug}")
        if implementation is not None:
            try:
                instance = implementation() if isinstance(entry, (WcaStrategyDefinition, WcaModifierDefinition)) else implementation
            except TypeError:
                instance = implementation
            errors.extend(_metadata_errors(entry, instance, strict=False))

    alias_slugs = {alias.alias_slug for alias in deprecated_aliases}
    if alias_slugs & primary_slugs:
        errors.append("deprecated_alias_registered_as_primary")

    return {"valid": not errors, "errors": tuple(errors)}


def assert_wca_module_catalog_valid() -> None:
    validation = validate_wca_module_catalog()
    if not validation["valid"]:
        raise RuntimeError(f"WCA module catalog validation failed: {', '.join(validation['errors'])}")


def _catalog_id(entry: WcaStrategyDefinition | WcaModifierDefinition | WcaHardFilterDefinition) -> str:
    return entry.strategy_id if isinstance(entry, WcaStrategyDefinition) else entry.module_id


def _duplicate_errors(label: str, values: tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for value in values:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)
    return [f"duplicate_{label}:{value}" for value in duplicates]


def _load_symbol(import_path: str) -> Any | None:
    if not import_path:
        return None
    try:
        return import_module(import_path)
    except ImportError:
        pass
    module_name, separator, symbol_name = import_path.rpartition(".")
    try:
        if not separator:
            return import_module(import_path)
        module = import_module(module_name)
        return getattr(module, symbol_name)
    except (AttributeError, ImportError, ValueError):
        return None


def _metadata_errors(entry: WcaStrategyDefinition | WcaModifierDefinition | WcaHardFilterDefinition, instance: Any, *, strict: bool) -> list[str]:
    errors: list[str] = []
    if isinstance(entry, WcaStrategyDefinition):
        expected = {
            "strategy_id": entry.strategy_id,
            "slug": entry.slug,
            "name": entry.name,
            "family": entry.family,
            "version": entry.strategy_version,
            "base_weight": entry.base_weight,
        }
    elif isinstance(entry, WcaModifierDefinition):
        expected = {
            "modifier_id": entry.slug,
            "name": entry.name,
            "family": entry.family,
        }
    else:
        return errors
    for attribute, value in expected.items():
        if getattr(instance, attribute, None) != value:
            errors.append(f"metadata_mismatch:{entry.slug}:{attribute}")
    if strict and errors:
        raise RuntimeError(", ".join(errors))
    return errors


__all__ = [
    "StrategyConfig",
    "WCA_DEPRECATED_ALIASES",
    "WCA_HARD_FILTER_REGISTRY",
    "WCA_HARD_FILTER_SLUGS",
    "WCA_MODULE_CATALOG",
    "WCA_MODIFIER_REGISTRY",
    "WCA_MODIFIER_SLUGS",
    "WCA_MODULE_INVENTORY",
    "WCA_PRIMARY_VOTER_SLUGS",
    "WCA_STRATEGY_IDS",
    "WCA_STRATEGY_REGISTRY",
    "WcaCatalogRole",
    "WcaDeprecatedAlias",
    "WcaHardFilterDefinition",
    "WcaModifierDefinition",
    "WcaModuleInventory",
    "WcaModuleLifecycleStatus",
    "WcaModuleStatus",
    "WcaStrategy",
    "WcaStrategyDefinition",
    "assert_wca_module_catalog_valid",
    "build_wca_modifiers",
    "build_wca_primary_voters",
    "resolve_wca_module_slug",
    "validate_wca_module_catalog",
    "wca_module_inventory",
    "wca_strategy_definition_for",
]
