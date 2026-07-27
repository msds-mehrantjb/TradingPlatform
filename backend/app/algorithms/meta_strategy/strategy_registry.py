"""Meta-Strategy-owned strategy registry."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from backend.app.algorithms.meta_strategy.contracts import MetaStrategyContractModel
from backend.app.algorithms.meta_strategy.feature_contracts import validate_required_input_producers
from backend.app.algorithms.meta_strategy.identity import ALGORITHM_ID
from backend.app.algorithms.meta_strategy.versions import META_STRATEGY_STRATEGY_CATALOG_VERSION


META_STRATEGY_REGISTRY_VERSION = "meta_strategy_registry_v1"
META_STRATEGY_STRATEGY_VERSION = "meta_strategy_strategy_v1"
META_STRATEGY_STRATEGY_PACKAGE = "backend.app.algorithms.meta_strategy.strategies"
MetaStrategyModuleLifecycleStatus = Literal["active", "shadow", "disabled", "unavailable", "not_data_ready", "deprecated_alias"]
MetaStrategyStrategyMode = Literal["ACTIVE", "SHADOW", "DISABLED"]


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
    EVENT_DRIVEN = "EVENT_DRIVEN"
    MARKET_CONTEXT = "MARKET_CONTEXT"
    REGIME = "REGIME"
    SAFETY = "SAFETY"


class MetaStrategyDirection(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class MetaStrategyModuleStatus(MetaStrategyContractModel):
    id: str = Field(min_length=1)
    status: MetaStrategyModuleLifecycleStatus


class MetaStrategyInventory(MetaStrategyContractModel):
    algorithm_id: Literal["meta_strategy"] = ALGORITHM_ID
    directional: tuple[MetaStrategyModuleStatus, ...]
    context: tuple[MetaStrategyModuleStatus, ...]
    regime: tuple[MetaStrategyModuleStatus, ...]
    safety: tuple[MetaStrategyModuleStatus, ...]

    @model_validator(mode="after")
    def module_ids_are_unique(self) -> MetaStrategyInventory:
        ids = [
            module.id
            for collection in (self.directional, self.context, self.regime, self.safety)
            for module in collection
        ]
        duplicates = tuple(sorted(module_id for module_id in set(ids) if ids.count(module_id) > 1))
        if duplicates:
            raise ValueError(f"duplicate Meta-Strategy module ids: {', '.join(duplicates)}")
        return self


class MetaStrategyConfigurationSchema(MetaStrategyContractModel):
    schema_id: str = Field(min_length=1)
    properties: dict[str, Any] = Field(default_factory=dict)
    required: tuple[str, ...] = ()


class StrategyDescriptor(MetaStrategyContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    algorithm_id: Literal["meta_strategy"] = ALGORITHM_ID
    strategy_id: str = Field(min_length=1)
    strategy_name: str = Field(min_length=1)
    strategy_version: str = Field(min_length=1)
    role: MetaStrategyRole
    family: MetaStrategyFamily
    correlation_group: str = Field(min_length=1)
    mode: MetaStrategyStrategyMode
    required_inputs: tuple[str, ...] = Field(min_length=1)
    supported_sessions: tuple[str, ...] = Field(min_length=1)
    supported_regimes: tuple[str, ...] = Field(min_length=1)
    settings_type: str = Field(min_length=1)
    output_schema: str = Field(min_length=1)
    minimum_warmup: int = Field(ge=0)
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

    @property
    def enabled(self) -> bool:
        return self.mode == "ACTIVE"

    @property
    def version(self) -> str:
        return self.strategy_version

    @classmethod
    def from_registry_entry(cls, entry: StrategyDescriptor) -> StrategyDescriptor:
        return entry


MetaStrategyRegistryEntry = StrategyDescriptor


def meta_strategy_strategy_catalog() -> tuple[MetaStrategyRegistryEntry, ...]:
    return ALL_META_STRATEGY_STRATEGIES


def validate_meta_strategy_registry(entries: tuple[MetaStrategyRegistryEntry, ...] | list[MetaStrategyRegistryEntry]) -> dict[str, Any]:
    strategy_ids = [entry.strategy_id for entry in entries]
    duplicate_ids = tuple(sorted(strategy_id for strategy_id in set(strategy_ids) if strategy_ids.count(strategy_id) > 1))
    foreign = tuple(entry.strategy_id for entry in entries if entry.algorithm_id != ALGORITHM_ID or not entry.implementation_module.startswith(META_STRATEGY_STRATEGY_PACKAGE))
    alias_targets = tuple(sorted(target for target in META_STRATEGY_ALIAS_MAP.values() if target not in strategy_ids))
    input_contracts = validate_required_input_producers(entries)
    valid = not duplicate_ids and not foreign and not alias_targets and input_contracts["valid"]
    return {
        "algorithmId": ALGORITHM_ID,
        "registryVersion": META_STRATEGY_REGISTRY_VERSION,
        "strategyCatalogVersion": META_STRATEGY_STRATEGY_CATALOG_VERSION,
        "valid": valid,
        "strategyCount": len(entries),
        "duplicateStrategyIds": duplicate_ids,
        "foreignImplementations": foreign,
        "missingAliasTargets": alias_targets,
        "missingRequiredInputProducers": input_contracts["missingProducers"],
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
    correlation_group: str,
    required_inputs: tuple[str, ...],
    minimum_warmup: int,
    supported_directions: tuple[MetaStrategyDirection, ...],
    implementation_module: str,
    implementation_class: str,
    *,
    mode: MetaStrategyStrategyMode = "ACTIVE",
    supported_sessions: tuple[str, ...] = ("OPENING", "MORNING", "MIDDAY", "AFTERNOON"),
    supported_regimes: tuple[str, ...] = ("trend", "range", "transition", "high_volatility", "normal_volatility"),
    settings_type: str | None = None,
    output_schema: str | None = None,
    aliases: tuple[str, ...] = (),
    configuration_schema: MetaStrategyConfigurationSchema | None = None,
) -> MetaStrategyRegistryEntry:
    settings_name = settings_type
    if settings_name is None:
        settings_name = {
            MetaStrategyRole.DIRECTIONAL: "MetaStrategyStrategySettings",
            MetaStrategyRole.CONTEXT: "MetaStrategyContextSettings",
            MetaStrategyRole.REGIME: "MetaStrategyRegimeSettings",
            MetaStrategyRole.SAFETY: "MetaStrategySafetyGateSettings",
        }[role]
    schema_name = output_schema
    if schema_name is None:
        schema_name = {
            MetaStrategyRole.DIRECTIONAL: "SnapshotEvaluationResult",
            MetaStrategyRole.CONTEXT: "SnapshotEvaluationResult",
            MetaStrategyRole.REGIME: "RegimeEvaluation",
            MetaStrategyRole.SAFETY: "SafetyEvaluation",
        }[role]
    return MetaStrategyRegistryEntry(
        strategy_id=strategy_id,
        strategy_name=strategy_name,
        strategy_version=META_STRATEGY_STRATEGY_VERSION,
        role=role,
        family=family,
        correlation_group=correlation_group,
        mode=mode,
        required_inputs=required_inputs,
        supported_sessions=supported_sessions,
        supported_regimes=supported_regimes,
        settings_type=settings_name,
        output_schema=schema_name,
        minimum_warmup=minimum_warmup,
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
    _entry("multi_timeframe_trend_alignment", "Multi-Timeframe Trend Alignment", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.TREND, "trend_continuation", ("candles", "moving_averages", "vwap", "atr", "adx"), 50, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.multi_timeframe_trend_alignment", "MultiTimeframeTrendAlignmentStrategy"),
    _entry("first_pullback_after_open", "First Pullback After Open", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.TREND, "trend_continuation", ("candles", "session_phase", "vwap", "relative_volume", "pullbackDepthAtr"), 30, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.first_pullback_after_open", "FirstPullbackAfterOpenStrategy", supported_sessions=("OPENING", "MORNING")),
    _entry("opening_range_breakout", "Opening Range Breakout", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.BREAKOUT, "breakout", ("candles", "atr", "relative_volume", "spread", "liquidity", "openingRangeHigh", "openingRangeLow"), 30, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.opening_range_breakout", "OpeningRangeBreakoutStrategy", supported_sessions=("OPENING", "MORNING")),
    _entry("vwap_trend_continuation", "VWAP Trend Continuation", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.TREND, "trend_continuation", ("candles", "vwap", "moving_averages", "relative_volume", "vwap_relationship", "vwap_slope"), 30, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.vwap_trend_continuation", "VwapTrendContinuationStrategy"),
    _entry("volatility_breakout", "Volatility Breakout", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.BREAKOUT, "breakout", ("candles", "atr", "bollinger_bands", "bollingerWidthPercentile", "relative_volume", "spread"), 50, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.volatility_breakout", "VolatilityBreakoutStrategy"),
    _entry("failed_breakout_reversal", "Failed Breakout Reversal", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.REVERSAL, "failed_breakout_reversal", ("candles", "atr", "spread", "liquidity", "failedBreakoutSide"), 40, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.failed_breakout_reversal", "FailedBreakoutReversalStrategy", aliases=("Failed Breakout Strategy", "Failed Breakout Reversal")),
    _entry("bollinger_atr_reversion", "Bollinger/ATR Reversion", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.MEAN_REVERSION, "mean_reversion", ("candles", "bollinger_bands", "atr", "adx", "rsi", "rejectionWickRatio"), 50, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.bollinger_atr_reversion", "BollingerAtrReversionStrategy", aliases=("Bollinger Band Reversion", "ATR Overextension Reversion", "Bollinger/ATR Reversion")),
    _entry("vwap_mean_reversion", "VWAP Mean Reversion", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.MEAN_REVERSION, "mean_reversion", ("candles", "vwap", "atr", "adx", "rsi", "volume", "vwap_relationship", "reclaimDistanceAtr"), 40, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.vwap_mean_reversion", "VwapMeanReversionStrategy"),
    _entry("liquidity_sweep_reversal", "Liquidity Sweep Reversal", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.REVERSAL, "failed_breakout_reversal", ("candles", "liquidity", "spread", "volume", "sweepSide"), 40, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.liquidity_sweep_reversal", "LiquiditySweepReversalStrategy", mode="SHADOW"),
    _entry("gap_continuation", "Gap Continuation", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.GAP_SESSION, "gap_session", ("candles", "gap_state", "session_phase", "qqq_iwm_context", "economic_event_state"), 30, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.gap_continuation", "GapContinuationStrategy", mode="SHADOW", supported_sessions=("OPENING", "MORNING")),
    _entry("gap_fade", "Gap Fade", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.GAP_SESSION, "gap_session", ("candles", "gap_state", "session_phase", "qqq_iwm_context", "economic_event_state"), 30, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.gap_fade", "GapFadeStrategy", mode="SHADOW", supported_sessions=("OPENING", "MORNING")),
    _entry("economic_event_reaction", "Economic Event Reaction", MetaStrategyRole.DIRECTIONAL, MetaStrategyFamily.EVENT_DRIVEN, "event_driven", ("candles", "economic_event_state", "session_phase", "spread", "relative_volume"), 30, DIRECTIONAL_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.directional.economic_event_reaction", "EconomicEventReactionStrategy", mode="SHADOW", supported_sessions=("PREMARKET", "OPENING", "MORNING", "MIDDAY", "AFTERNOON")),
)

CONTEXT_STRATEGIES: tuple[MetaStrategyRegistryEntry, ...] = (
    _entry(
        "economic_event_context",
        "Economic Event Context",
        MetaStrategyRole.CONTEXT,
        MetaStrategyFamily.MARKET_CONTEXT,
        "event_driven",
        ("economic_event_state", "session_phase", "spread"),
        0,
        CONTEXT_DIRECTIONS,
        f"{META_STRATEGY_STRATEGY_PACKAGE}.context.economic_event_context",
        "EconomicEventContextStrategy",
    ),
    _entry("relative_strength_qqq_iwm", "Relative Strength vs QQQ/IWM", MetaStrategyRole.CONTEXT, MetaStrategyFamily.MARKET_CONTEXT, "market_context", ("qqq_iwm_context",), 20, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.context.relative_strength_qqq_iwm", "RelativeStrengthQqqIwmStrategy"),
    _entry("market_breadth_momentum", "Market Breadth Momentum", MetaStrategyRole.CONTEXT, MetaStrategyFamily.MARKET_CONTEXT, "market_context", ("breadth",), 20, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.context.market_breadth_momentum", "MarketBreadthMomentumStrategy"),
    _entry(
        "market_structure_context",
        "Market Structure Context",
        MetaStrategyRole.CONTEXT,
        MetaStrategyFamily.MARKET_CONTEXT,
        "market_context",
        ("candles", "moving_averages", "atr"),
        20,
        CONTEXT_DIRECTIONS,
        f"{META_STRATEGY_STRATEGY_PACKAGE}.context.market_structure_context",
        "MarketStructureContextStrategy",
    ),
    _entry(
        "volume_confirmation_context",
        "Volume Confirmation",
        MetaStrategyRole.CONTEXT,
        MetaStrategyFamily.MARKET_CONTEXT,
        "market_context",
        ("volume", "relative_volume"),
        20,
        CONTEXT_DIRECTIONS,
        f"{META_STRATEGY_STRATEGY_PACKAGE}.context.volume_confirmation",
        "VolumeConfirmationStrategy",
        aliases=("volume_confirmation",),
    ),
    _entry(
        "vwap_position_context",
        "VWAP Position Context",
        MetaStrategyRole.CONTEXT,
        MetaStrategyFamily.MARKET_CONTEXT,
        "market_context",
        ("vwap", "moving_averages"),
        20,
        CONTEXT_DIRECTIONS,
        f"{META_STRATEGY_STRATEGY_PACKAGE}.context.vwap_position_context",
        "VwapPositionContextStrategy",
        aliases=("VWAP Position Strategy",),
    ),
)

REGIME_STRATEGIES: tuple[MetaStrategyRegistryEntry, ...] = (
    _entry("adx_atr_regime_classifier", "ADX/ATR Regime Classifier", MetaStrategyRole.REGIME, MetaStrategyFamily.REGIME, "regime_classification", ("adx", "atr", "moving_averages", "relative_volume", "economic_event_state"), 50, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.regime.adx_atr_regime_classifier", "AdxAtrRegimeClassifierStrategy", aliases=("adx_trend_strength_regime", "atr_volatility_regime", "ADX Trend Strength Filter", "ADX Trend Strength Regime", "ATR Volatility Regime")),
)

SAFETY_STRATEGIES: tuple[MetaStrategyRegistryEntry, ...] = (
    _entry("cash_avoid_trading_filter", "Cash / Avoid Trading Filter", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, "local_risk", ("cash_available", "avoid_trading"), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.cash_avoid_trading", "CashAvoidTradingFilterStrategy"),
    _entry("missing_critical_data_filter", "Missing Critical Data Filter", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, "data_quality", ("critical_data",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.missing_critical_data", "MissingCriticalDataFilterStrategy"),
    _entry("stale_market_data_filter", "Stale Market Data Filter", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, "data_quality", ("source_cutoff_timestamp",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.stale_market_data", "StaleMarketDataFilterStrategy"),
    _entry("excessive_spread_filter", "Excessive Spread Filter", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, "liquidity_cost", ("spread",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.excessive_spread", "ExcessiveSpreadFilterStrategy"),
    _entry("insufficient_liquidity_filter", "Insufficient Liquidity Filter", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, "liquidity_cost", ("liquidity",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.insufficient_liquidity", "InsufficientLiquidityFilterStrategy"),
    _entry("extreme_volatility_filter", "Extreme Volatility Filter", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, "volatility_risk", ("atr", "relative_volume"), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.extreme_volatility", "ExtremeVolatilityFilterStrategy"),
    _entry("economic_event_blackout_filter", "Economic Event Blackout Filter", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, "event_driven", ("economic_event_state",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.economic_event_blackout", "EconomicEventBlackoutFilterStrategy"),
    _entry("unsupported_session_filter", "Unsupported Session Filter", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, "session_policy", ("session_phase",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.unsupported_session", "UnsupportedSessionFilterStrategy"),
    _entry("operational_health_filter", "Operational Health Filter", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, "operational", ("operational_health",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.operational_health", "OperationalHealthFilterStrategy"),
    _entry("halt_luld_filter", "Halt / LULD Filter", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, "operational", ("halt_luld_state",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.halt_luld", "HaltLuldFilterStrategy"),
    _entry("daily_loss_limit_filter", "Daily Loss Limit Filter", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, "local_risk", ("daily_loss_state",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.local_risk_controls", "DailyLossLimitFilterStrategy"),
    _entry("trade_count_limit_filter", "Trade Count Limit Filter", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, "local_risk", ("trade_count_state",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.local_risk_controls", "TradeCountLimitFilterStrategy"),
    _entry("duplicate_order_protection_filter", "Duplicate Order Protection Filter", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, "order_integrity", ("duplicate_order_state",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.local_risk_controls", "DuplicateOrderProtectionFilterStrategy"),
    _entry("existing_position_policy_filter", "Existing Position Policy Filter", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, "position_policy", ("existing_position_state",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.local_risk_controls", "ExistingPositionPolicyFilterStrategy"),
    _entry("local_risk_budget_filter", "Local Risk Budget Filter", MetaStrategyRole.SAFETY, MetaStrategyFamily.SAFETY, "local_risk", ("local_risk_budget",), 0, CONTEXT_DIRECTIONS, f"{META_STRATEGY_STRATEGY_PACKAGE}.safety.local_risk_controls", "LocalRiskBudgetFilterStrategy"),
)

ALL_META_STRATEGY_STRATEGIES: tuple[MetaStrategyRegistryEntry, ...] = (
    *DIRECTIONAL_STRATEGIES,
    *CONTEXT_STRATEGIES,
    *REGIME_STRATEGIES,
    *SAFETY_STRATEGIES,
)
ACTIVE_DIRECTIONAL_STRATEGIES: tuple[MetaStrategyRegistryEntry, ...] = tuple(
    entry for entry in DIRECTIONAL_STRATEGIES if entry.mode == "ACTIVE"
)
SHADOW_DIRECTIONAL_STRATEGIES: tuple[MetaStrategyRegistryEntry, ...] = tuple(
    entry for entry in DIRECTIONAL_STRATEGIES if entry.mode == "SHADOW"
)


def _module_status(entry: MetaStrategyRegistryEntry) -> MetaStrategyModuleStatus:
    return MetaStrategyModuleStatus(id=entry.strategy_id, status=str(entry.mode).lower())


META_STRATEGY_MODULE_INVENTORY = MetaStrategyInventory(
    directional=tuple(_module_status(entry) for entry in DIRECTIONAL_STRATEGIES),
    context=tuple(_module_status(entry) for entry in CONTEXT_STRATEGIES),
    regime=tuple(_module_status(entry) for entry in REGIME_STRATEGIES),
    safety=tuple(_module_status(entry) for entry in SAFETY_STRATEGIES),
)

META_STRATEGY_BY_ID = {entry.strategy_id: entry for entry in ALL_META_STRATEGY_STRATEGIES}
META_STRATEGY_BY_NAME = {entry.strategy_name: entry for entry in ALL_META_STRATEGY_STRATEGIES}
META_STRATEGY_ALIAS_MAP = {
    alias: entry.strategy_id
    for entry in ALL_META_STRATEGY_STRATEGIES
    for alias in entry.aliases
}
META_STRATEGY_STARTUP_FEATURE_CONTRACT_VALIDATION = validate_required_input_producers(ALL_META_STRATEGY_STRATEGIES)


__all__ = [
    "ACTIVE_DIRECTIONAL_STRATEGIES",
    "ALL_META_STRATEGY_STRATEGIES",
    "CONTEXT_STRATEGIES",
    "DIRECTIONAL_STRATEGIES",
    "META_STRATEGY_ALIAS_MAP",
    "META_STRATEGY_MODULE_INVENTORY",
    "META_STRATEGY_REGISTRY_VERSION",
    "META_STRATEGY_STRATEGY_PACKAGE",
    "META_STRATEGY_STRATEGY_VERSION",
    "META_STRATEGY_STARTUP_FEATURE_CONTRACT_VALIDATION",
    "REGIME_STRATEGIES",
    "SAFETY_STRATEGIES",
    "SHADOW_DIRECTIONAL_STRATEGIES",
    "StrategyDescriptor",
    "MetaStrategyConfigurationSchema",
    "MetaStrategyDirection",
    "MetaStrategyFamily",
    "MetaStrategyInventory",
    "MetaStrategyModuleLifecycleStatus",
    "MetaStrategyModuleStatus",
    "MetaStrategyRegistryEntry",
    "MetaStrategyRole",
    "MetaStrategyStrategyMode",
    "canonical_strategy_id",
    "directional_strategy_input_ids",
    "influence_strategy_ids",
    "meta_strategy_strategy_catalog",
    "resolve_strategy",
    "resolve_strategy_list",
    "validate_meta_strategy_registry",
]
