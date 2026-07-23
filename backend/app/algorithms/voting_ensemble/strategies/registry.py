from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.domain.models import StrategyFamily, StrategyRole


ModuleLifecycleStatus = Literal["active", "shadow", "disabled", "unavailable", "not_data_ready", "deprecated_alias"]


class ModuleStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    status: ModuleLifecycleStatus


class VotingEnsembleInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    algorithm_id: Literal["voting_ensemble"] = "voting_ensemble"
    directional: tuple[ModuleStatus, ...]
    context: tuple[ModuleStatus, ...]
    regime: tuple[ModuleStatus, ...]
    safety: tuple[ModuleStatus, ...]

    @model_validator(mode="after")
    def module_ids_are_unique(self) -> "VotingEnsembleInventory":
        ids = [
            module.id
            for collection in (self.directional, self.context, self.regime, self.safety)
            for module in collection
        ]
        duplicates = sorted(module_id for module_id in set(ids) if ids.count(module_id) > 1)
        if duplicates:
            raise ValueError(f"duplicate Voting Ensemble module ids: {', '.join(duplicates)}")
        return self


VOTING_ENSEMBLE_MODULE_INVENTORY = VotingEnsembleInventory(
    directional=(
        ModuleStatus(id="multi_timeframe_trend_alignment", status="active"),
        ModuleStatus(id="first_pullback_after_open", status="active"),
        ModuleStatus(id="failed_breakout_reversal", status="active"),
        ModuleStatus(id="liquidity_sweep_reversal", status="active"),
        ModuleStatus(id="bollinger_atr_reversion", status="active"),
    ),
    context=(
        ModuleStatus(id="relative_strength_qqq_iwm", status="active"),
        ModuleStatus(id="market_breadth_momentum", status="active"),
    ),
    regime=(
        ModuleStatus(id="adx_atr_regime_classifier", status="active"),
    ),
    safety=(
        ModuleStatus(id="cash_avoid_trading_filter", status="active"),
    ),
)


_MODULE_STATUS_BY_ID = {
    module.id: module.status
    for collection in (
        VOTING_ENSEMBLE_MODULE_INVENTORY.directional,
        VOTING_ENSEMBLE_MODULE_INVENTORY.context,
        VOTING_ENSEMBLE_MODULE_INVENTORY.regime,
        VOTING_ENSEMBLE_MODULE_INVENTORY.safety,
    )
    for module in collection
}


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
    evidence: tuple[str, ...] = ()
    status: ModuleLifecycleStatus = "active"
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
        if self.enabled != (self.status == "active"):
            raise ValueError("registry enabled flag must match authoritative inventory status")
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
    evidence: tuple[str, ...] = (),
    status: ModuleLifecycleStatus | None = None,
) -> StrategyRegistryEntry:
    module_status = status or _MODULE_STATUS_BY_ID[strategy_id]
    return StrategyRegistryEntry(
        strategyId=strategy_id,
        strategyName=name,
        strategyVersion=version,
        family=family,
        role=role,
        collection=collection,
        requiredInputs=required_inputs,
        evidence=evidence,
        status=module_status,
        enabled=module_status == "active",
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


VOTING_ENSEMBLE_REGIME_STRATEGIES: tuple[StrategyRegistryEntry, ...] = (
    _entry(
        "adx_atr_regime_classifier",
        "ADX/ATR Regime Classifier",
        "2.0.0",
        StrategyFamily.MARKET_CONTEXT,
        StrategyRole.REGIME,
        StrategyCollection.REGIME,
        (
            "spy_1m_candles",
            "adx",
            "atr",
            "market_structure",
            "liquidity_state",
            "session_state",
            "economic_event_state",
        ),
        evidence=("Trend strength", "Volatility level", "Structure", "Liquidity", "Session", "Event risk"),
    ),
)


VOTING_ENSEMBLE_SAFETY_STRATEGIES: tuple[StrategyRegistryEntry, ...] = (
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


VOTING_ENSEMBLE_AGGREGATOR_STRATEGIES: tuple[StrategyRegistryEntry, ...] = (
    _entry(
        "ensemble_strategy_voting",
        "Ensemble Strategy Voting",
        "2.0.0",
        StrategyFamily.MARKET_CONTEXT,
        StrategyRole.AGGREGATOR,
        StrategyCollection.AGGREGATOR,
        ("strategy_signals", "family_scores"),
        status="active",
    ),
)


VOTING_ENSEMBLE_ACTIVE_DIRECTIONAL_STRATEGIES: tuple[StrategyRegistryEntry, ...] = tuple(
    entry for entry in VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES if entry.status == "active"
)
VOTING_ENSEMBLE_SHADOW_DIRECTIONAL_STRATEGIES: tuple[StrategyRegistryEntry, ...] = tuple(
    entry for entry in VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES if entry.status == "shadow"
)
VOTING_ENSEMBLE_ACTIVE_CONTEXT_STRATEGIES: tuple[StrategyRegistryEntry, ...] = tuple(
    entry for entry in VOTING_ENSEMBLE_CONTEXT_STRATEGIES if entry.status == "active"
)
VOTING_ENSEMBLE_SHADOW_CONTEXT_STRATEGIES: tuple[StrategyRegistryEntry, ...] = tuple(
    entry for entry in VOTING_ENSEMBLE_CONTEXT_STRATEGIES if entry.status == "shadow"
)


STRATEGY_ALIAS_MAP: dict[str, str] = {
    "Failed Breakout Strategy": "failed_breakout_reversal",
    "Failed Breakout Reversal": "failed_breakout_reversal",
    "Bollinger Band Reversion": "bollinger_atr_reversion",
    "ATR Overextension Reversion": "bollinger_atr_reversion",
    "Bollinger/ATR Reversion": "bollinger_atr_reversion",
    "adx_trend_strength_regime": "adx_atr_regime_classifier",
    "atr_volatility_regime": "adx_atr_regime_classifier",
    "ADX Trend Strength Filter": "adx_atr_regime_classifier",
    "ADX Trend Strength Regime": "adx_atr_regime_classifier",
    "ATR Volatility Regime": "adx_atr_regime_classifier",
    "ADX/ATR Regime Classifier": "adx_atr_regime_classifier",
    "Ensemble Strategy Voting": "ensemble_strategy_voting",
}


VOTING_ENSEMBLE_STRATEGIES: tuple[StrategyRegistryEntry, ...] = (
    *VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES,
    *VOTING_ENSEMBLE_CONTEXT_STRATEGIES,
    *VOTING_ENSEMBLE_REGIME_STRATEGIES,
    *VOTING_ENSEMBLE_SAFETY_STRATEGIES,
    *VOTING_ENSEMBLE_AGGREGATOR_STRATEGIES,
)

_STRATEGIES_BY_ID = {entry.strategyId: entry for entry in VOTING_ENSEMBLE_STRATEGIES}
_STRATEGIES_BY_NAME = {entry.strategyName: entry for entry in VOTING_ENSEMBLE_STRATEGIES}


def inventory_status(strategy_id: str) -> ModuleLifecycleStatus:
    return _MODULE_STATUS_BY_ID[strategy_id]


def active_module_ids(collection: StrategyCollection) -> tuple[str, ...]:
    return tuple(
        entry.strategyId
        for entry in VOTING_ENSEMBLE_STRATEGIES
        if entry.collection == collection.value and entry.status == "active"
    )


def shadow_module_ids(collection: StrategyCollection) -> tuple[str, ...]:
    return tuple(
        entry.strategyId
        for entry in VOTING_ENSEMBLE_STRATEGIES
        if entry.collection == collection.value and entry.status == "shadow"
    )


def directional_strategy_input_ids() -> tuple[str, ...]:
    return active_module_ids(StrategyCollection.DIRECTIONAL)


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


__all__ = [
    "ModuleLifecycleStatus",
    "ModuleStatus",
    "STRATEGY_ALIAS_MAP",
    "StrategyCollection",
    "StrategyRegistryEntry",
    "VOTING_ENSEMBLE_ACTIVE_CONTEXT_STRATEGIES",
    "VOTING_ENSEMBLE_ACTIVE_DIRECTIONAL_STRATEGIES",
    "VOTING_ENSEMBLE_AGGREGATOR_STRATEGIES",
    "VOTING_ENSEMBLE_CONTEXT_STRATEGIES",
    "VOTING_ENSEMBLE_DIRECTIONAL_STRATEGIES",
    "VOTING_ENSEMBLE_MODULE_INVENTORY",
    "VOTING_ENSEMBLE_REGIME_STRATEGIES",
    "VOTING_ENSEMBLE_SAFETY_STRATEGIES",
    "VOTING_ENSEMBLE_SHADOW_CONTEXT_STRATEGIES",
    "VOTING_ENSEMBLE_SHADOW_DIRECTIONAL_STRATEGIES",
    "VOTING_ENSEMBLE_STRATEGIES",
    "VotingEnsembleInventory",
    "active_module_ids",
    "canonical_strategy_id",
    "directional_strategy_input_ids",
    "inventory_status",
    "resolve_strategy",
    "shadow_module_ids",
]
