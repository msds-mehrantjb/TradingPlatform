from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.domain.models import StrategyFamily, StrategyRole


class StrategyCollection(str, Enum):
    DIRECTIONAL = "DIRECTIONAL"
    CONTEXT = "CONTEXT"
    REGIME = "REGIME"
    SAFETY = "SAFETY"
    AGGREGATOR = "AGGREGATOR"


class StrategyRegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, use_enum_values=True)

    strategyId: str = Field(min_length=1)
    strategyName: str = Field(min_length=1)
    strategyVersion: str = Field(min_length=1)
    family: StrategyFamily
    role: StrategyRole
    collection: StrategyCollection
    requiredInputs: tuple[str, ...]
    enabled: bool = True

    @model_validator(mode="after")
    def role_must_match_collection(self) -> StrategyRegistryEntry:
        allowed_roles = {
            StrategyCollection.DIRECTIONAL.value: {StrategyRole.DIRECTIONAL.value},
            StrategyCollection.CONTEXT.value: {StrategyRole.CONTEXT.value},
            StrategyCollection.REGIME.value: {StrategyRole.REGIME.value},
            StrategyCollection.SAFETY.value: {StrategyRole.SAFETY.value},
            StrategyCollection.AGGREGATOR.value: {StrategyRole.AGGREGATOR.value},
        }
        if self.role not in allowed_roles[self.collection]:
            raise ValueError(f"{self.collection} registry entries cannot use role {self.role}")
        return self


def _entry(
    strategy_id: str,
    name: str,
    version: str,
    family: StrategyFamily,
    role: StrategyRole,
    collection: StrategyCollection,
    required_inputs: tuple[str, ...],
    *,
    enabled: bool = True,
) -> StrategyRegistryEntry:
    return StrategyRegistryEntry(
        strategyId=strategy_id,
        strategyName=name,
        strategyVersion=version,
        family=family,
        role=role,
        collection=collection,
        requiredInputs=required_inputs,
        enabled=enabled,
    )


DIRECTIONAL_STRATEGIES: tuple[StrategyRegistryEntry, ...] = (
    _entry(
        "multi_timeframe_trend_alignment",
        "Multi-Timeframe Trend Alignment",
        "2.0.0",
        StrategyFamily.TREND,
        StrategyRole.DIRECTIONAL,
        StrategyCollection.DIRECTIONAL,
        ("spy_1m_features", "spy_5m_features", "spy_15m_features", "session_vwap"),
    ),
    _entry(
        "first_pullback_after_open",
        "First Pullback After Open",
        "2.0.0",
        StrategyFamily.TREND,
        StrategyRole.DIRECTIONAL,
        StrategyCollection.DIRECTIONAL,
        ("spy_1m_candles", "spy_1m_features", "session_vwap"),
    ),
    _entry(
        "vwap_trend_continuation",
        "VWAP Trend Continuation",
        "2.0.0",
        StrategyFamily.TREND,
        StrategyRole.DIRECTIONAL,
        StrategyCollection.DIRECTIONAL,
        ("spy_1m_candles", "spy_1m_features", "session_vwap", "vwap_slope"),
    ),
    _entry(
        "opening_range_breakout",
        "Opening Range Breakout",
        "2.0.0",
        StrategyFamily.BREAKOUT,
        StrategyRole.DIRECTIONAL,
        StrategyCollection.DIRECTIONAL,
        ("spy_1m_candles", "opening_range", "atr", "spread", "relative_volume"),
    ),
    _entry(
        "volatility_breakout",
        "Volatility Breakout",
        "2.0.0",
        StrategyFamily.BREAKOUT,
        StrategyRole.DIRECTIONAL,
        StrategyCollection.DIRECTIONAL,
        ("spy_1m_candles", "atr", "realized_volatility", "bollinger_width", "spread", "relative_volume"),
    ),
    _entry(
        "failed_breakout_reversal",
        "Failed Breakout Reversal",
        "2.0.0",
        StrategyFamily.REVERSAL,
        StrategyRole.DIRECTIONAL,
        StrategyCollection.DIRECTIONAL,
        ("spy_1m_candles", "reference_levels", "atr", "spread"),
    ),
    _entry(
        "liquidity_sweep_reversal",
        "Liquidity Sweep Reversal",
        "2.0.0",
        StrategyFamily.REVERSAL,
        StrategyRole.DIRECTIONAL,
        StrategyCollection.DIRECTIONAL,
        ("spy_1m_candles", "liquidity_levels", "atr", "spread", "activity"),
    ),
    _entry(
        "vwap_mean_reversion",
        "VWAP Mean Reversion",
        "2.0.0",
        StrategyFamily.MEAN_REVERSION,
        StrategyRole.DIRECTIONAL,
        StrategyCollection.DIRECTIONAL,
        ("spy_1m_candles", "session_vwap", "distance_from_vwap", "adx", "vwap_slope", "volume_behavior"),
    ),
    _entry(
        "bollinger_atr_reversion",
        "Bollinger/ATR Reversion",
        "2.0.0",
        StrategyFamily.MEAN_REVERSION,
        StrategyRole.DIRECTIONAL,
        StrategyCollection.DIRECTIONAL,
        ("spy_1m_candles", "bollinger_bands", "atr", "adx", "band_width", "equilibrium_distance"),
    ),
    _entry(
        "gap_continuation_gap_fade",
        "Gap Continuation / Gap Fade",
        "2.0.0",
        StrategyFamily.GAP_SESSION,
        StrategyRole.DIRECTIONAL,
        StrategyCollection.DIRECTIONAL,
        ("prior_regular_session_close", "spy_1m_candles", "regular_session_open", "premarket_range", "atr", "initial_volume", "market_context", "event_context"),
    ),
)


CONTEXT_STRATEGIES: tuple[StrategyRegistryEntry, ...] = (
    _entry("relative_strength_qqq_iwm", "Relative Strength vs QQQ/IWM", "2.0.0", StrategyFamily.MARKET_CONTEXT, StrategyRole.CONTEXT, StrategyCollection.CONTEXT, ("spy_candles", "qqq_candles", "iwm_candles")),
    _entry("market_breadth_momentum", "Market Breadth Momentum", "2.0.0", StrategyFamily.MARKET_CONTEXT, StrategyRole.CONTEXT, StrategyCollection.CONTEXT, ("external_breadth_feed_or_proxy_basket", "component_candles", "component_volume", "component_vwap", "component_ema20")),
    _entry("economic_event_context", "Economic Event Context", "2.0.0", StrategyFamily.MARKET_CONTEXT, StrategyRole.CONTEXT, StrategyCollection.CONTEXT, ("economic_event_state", "session_clock", "spread", "volatility")),
    _entry("market_structure_context", "Market Structure Context", "2.0.0", StrategyFamily.MARKET_CONTEXT, StrategyRole.CONTEXT, StrategyCollection.CONTEXT, ("spy_1m_candles", "rolling_levels", "market_structure", "atr")),
    _entry("volume_confirmation", "Volume Confirmation", "2.0.0", StrategyFamily.MARKET_CONTEXT, StrategyRole.CONTEXT, StrategyCollection.CONTEXT, ("spy_1m_candles", "relative_volume", "volume_history")),
    _entry("vwap_position_context", "VWAP Position Context", "2.0.0", StrategyFamily.MARKET_CONTEXT, StrategyRole.CONTEXT, StrategyCollection.CONTEXT, ("spy_1m_candles", "session_vwap", "distance_from_vwap", "vwap_slope")),
)


REGIME_STRATEGIES: tuple[StrategyRegistryEntry, ...] = (
    _entry("adx_trend_strength_regime", "ADX Trend Strength Regime", "2.0.0", StrategyFamily.MARKET_CONTEXT, StrategyRole.REGIME, StrategyCollection.REGIME, ("spy_1m_candles", "adx", "market_structure", "atr_percentile", "realized_volatility")),
    _entry("atr_volatility_regime", "ATR Volatility Regime", "2.0.0", StrategyFamily.MARKET_CONTEXT, StrategyRole.REGIME, StrategyCollection.REGIME, ("spy_1m_candles", "atr", "atr_percentile", "realized_volatility", "economic_event_state")),
)


SAFETY_STRATEGIES: tuple[StrategyRegistryEntry, ...] = (
    _entry(
        "cash_avoid_trading_filter",
        "Cash / Avoid Trading Filter",
        "2.0.0",
        StrategyFamily.SAFETY,
        StrategyRole.SAFETY,
        StrategyCollection.SAFETY,
        ("feature_snapshot", "operational_state", "account_risk_state", "order_intent"),
    ),
)


AGGREGATOR_STRATEGIES: tuple[StrategyRegistryEntry, ...] = (
    _entry("ensemble_strategy_voting", "Ensemble Strategy Voting", "2.0.0", StrategyFamily.MARKET_CONTEXT, StrategyRole.AGGREGATOR, StrategyCollection.AGGREGATOR, ("strategy_signals", "family_scores")),
)


STRATEGY_ALIAS_MAP: dict[str, str] = {
    "Failed Breakout Strategy": "failed_breakout_reversal",
    "Failed Breakout Reversal": "failed_breakout_reversal",
    "Bollinger Band Reversion": "bollinger_atr_reversion",
    "ATR Overextension Reversion": "bollinger_atr_reversion",
    "Bollinger/ATR Reversion": "bollinger_atr_reversion",
    "Economic Event Reaction Strategy": "economic_event_context",
    "Economic Event Context": "economic_event_context",
    "VWAP Position Strategy": "vwap_position_context",
    "VWAP Position Context": "vwap_position_context",
    "ADX Trend Strength Filter": "adx_trend_strength_regime",
    "ADX Trend Strength Regime": "adx_trend_strength_regime",
    "Ensemble Strategy Voting": "ensemble_strategy_voting",
}


ALL_STRATEGIES: tuple[StrategyRegistryEntry, ...] = (
    *DIRECTIONAL_STRATEGIES,
    *CONTEXT_STRATEGIES,
    *REGIME_STRATEGIES,
    *SAFETY_STRATEGIES,
    *AGGREGATOR_STRATEGIES,
)

_STRATEGIES_BY_ID = {entry.strategyId: entry for entry in ALL_STRATEGIES}
_STRATEGIES_BY_NAME = {entry.strategyName: entry for entry in ALL_STRATEGIES}


def canonical_strategy_id(name_or_id: str) -> str:
    if name_or_id in _STRATEGIES_BY_ID:
        return name_or_id
    if name_or_id in STRATEGY_ALIAS_MAP:
        return STRATEGY_ALIAS_MAP[name_or_id]
    if name_or_id in _STRATEGIES_BY_NAME:
        return _STRATEGIES_BY_NAME[name_or_id].strategyId
    raise KeyError(f"Unknown strategy module: {name_or_id}")


def resolve_strategy(name_or_id: str) -> StrategyRegistryEntry:
    return _STRATEGIES_BY_ID[canonical_strategy_id(name_or_id)]


def resolve_strategy_list(names_or_ids: list[str] | tuple[str, ...]) -> list[StrategyRegistryEntry]:
    resolved: list[StrategyRegistryEntry] = []
    seen: set[str] = set()
    for value in names_or_ids:
        entry = resolve_strategy(value)
        if entry.strategyId in seen:
            continue
        seen.add(entry.strategyId)
        resolved.append(entry)
    return resolved


def directional_strategy_inputs() -> list[StrategyRegistryEntry]:
    return list(DIRECTIONAL_STRATEGIES)


def directional_strategy_input_ids() -> list[str]:
    return [entry.strategyId for entry in DIRECTIONAL_STRATEGIES]


def assert_directional_voter(entry: StrategyRegistryEntry) -> StrategyRegistryEntry:
    if entry.collection != StrategyCollection.DIRECTIONAL.value or entry.role != StrategyRole.DIRECTIONAL.value:
        raise ValueError(f"{entry.strategyName} is not a directional voter")
    return entry


def directional_voters_from(names_or_ids: list[str] | tuple[str, ...]) -> list[StrategyRegistryEntry]:
    return [assert_directional_voter(entry) for entry in resolve_strategy_list(names_or_ids)]
