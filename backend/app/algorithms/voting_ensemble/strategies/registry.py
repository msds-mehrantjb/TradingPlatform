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
    def role_must_match_collection(self) -> "StrategyRegistryEntry":
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


VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES: tuple[StrategyRegistryEntry, ...] = (
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
        "bollinger_atr_reversion",
        "Bollinger/ATR Reversion",
        "2.0.0",
        StrategyFamily.MEAN_REVERSION,
        StrategyRole.DIRECTIONAL,
        StrategyCollection.DIRECTIONAL,
        ("spy_1m_candles", "bollinger_bands", "atr", "adx", "band_width", "equilibrium_distance"),
    ),
)


VOTING_ENSEMBLE_AGGREGATOR_STRATEGIES: tuple[StrategyRegistryEntry, ...] = (
    _entry(
        "ensemble_strategy_voting",
        "Ensemble Strategy Voting",
        "2.0.0",
        StrategyFamily.MARKET_CONTEXT,
        StrategyRole.AGGREGATOR,
        StrategyCollection.AGGREGATOR,
        ("strategy_signals", "family_scores"),
    ),
)


VOTING_ENSEMBLE_CONTEXT_STRATEGIES: tuple[StrategyRegistryEntry, ...] = (
    _entry(
        "relative_strength_qqq_iwm",
        "Relative Strength vs QQQ/IWM",
        "2.0.0",
        StrategyFamily.MARKET_CONTEXT,
        StrategyRole.CONTEXT,
        StrategyCollection.CONTEXT,
        ("spy_candles", "qqq_candles", "iwm_candles"),
    ),
    _entry(
        "market_breadth_momentum",
        "Market Breadth Momentum",
        "2.0.0",
        StrategyFamily.MARKET_CONTEXT,
        StrategyRole.CONTEXT,
        StrategyCollection.CONTEXT,
        ("external_breadth_feed_or_proxy_basket", "component_candles", "component_volume", "component_vwap", "component_ema20"),
    ),
)


STRATEGY_ALIAS_MAP: dict[str, str] = {
    "Failed Breakout Strategy": "failed_breakout_reversal",
    "Failed Breakout Reversal": "failed_breakout_reversal",
    "Bollinger Band Reversion": "bollinger_atr_reversion",
    "ATR Overextension Reversion": "bollinger_atr_reversion",
    "Bollinger/ATR Reversion": "bollinger_atr_reversion",
    "Ensemble Strategy Voting": "ensemble_strategy_voting",
}


VOTING_ENSEMBLE_STRATEGIES: tuple[StrategyRegistryEntry, ...] = (
    *VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES,
    *VOTING_ENSEMBLE_CONTEXT_STRATEGIES,
    *VOTING_ENSEMBLE_AGGREGATOR_STRATEGIES,
)

_STRATEGIES_BY_ID = {entry.strategyId: entry for entry in VOTING_ENSEMBLE_STRATEGIES}
_STRATEGIES_BY_NAME = {entry.strategyName: entry for entry in VOTING_ENSEMBLE_STRATEGIES}


def directional_strategy_input_ids() -> tuple[str, ...]:
    return tuple(entry.strategyId for entry in VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES)


def canonical_strategy_id(name_or_id: str) -> str:
    if name_or_id in _STRATEGIES_BY_ID:
        return name_or_id
    if name_or_id in STRATEGY_ALIAS_MAP:
        return STRATEGY_ALIAS_MAP[name_or_id]
    if name_or_id in _STRATEGIES_BY_NAME:
        return _STRATEGIES_BY_NAME[name_or_id].strategyId
    raise KeyError(f"Unknown Voting Ensemble strategy module: {name_or_id}")


def resolve_strategy(name_or_id: str) -> StrategyRegistryEntry:
    return _STRATEGIES_BY_ID[canonical_strategy_id(name_or_id)]
