"""Backend-authoritative Regime strategy catalog."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from backend.app.algorithms.regime.contracts import RegimeClassification, RegimeMarketSnapshot, RegimeStrategyEvaluation
from backend.app.algorithms.regime.strategies.base import RegimeStrategyDefinition, evaluate_definition
from backend.app.algorithms.regime.strategies.safety import cash_avoid_filter


RegimeModuleLifecycleStatus = Literal["active", "shadow", "disabled", "unavailable", "not_data_ready", "deprecated_alias"]


@dataclass(frozen=True)
class RegimeModuleStatus:
    id: str
    status: RegimeModuleLifecycleStatus


@dataclass(frozen=True)
class RegimeModuleInventory:
    algorithm_id: str
    catalog_version: str
    directional: tuple[RegimeModuleStatus, ...]
    context: tuple[RegimeModuleStatus, ...]
    regime: tuple[RegimeModuleStatus, ...]
    safety: tuple[RegimeModuleStatus, ...]


def _evaluate_adx_atr_regime_classifier(
    snapshot: RegimeMarketSnapshot,
    classification: RegimeClassification,
) -> tuple[str, float, str, dict]:
    axes = classification.axes
    return (
        "Hold",
        classification.confidence,
        f"regime.classifier.{classification.raw_regime}",
        {
            "rawRegime": classification.raw_regime,
            "trendStrength": axes.direction,
            "volatilityLevel": axes.volatility,
            "structure": axes.structure,
            "liquidity": axes.liquidity,
            "session": axes.session,
            "eventRisk": axes.event_risk,
            "evidenceAxes": ("Trend strength", "Volatility level", "Structure", "Liquidity", "Session", "Event risk"),
            "features": classification.features,
            "classifierEvidence": classification.evidence,
        },
    )


REGIME_STRATEGY_DEFINITIONS: tuple[RegimeStrategyDefinition, ...] = (
    RegimeStrategyDefinition("adx_atr_regime_classifier", "ADX/ATR Regime Classifier", "regime", "regime_context", 0.0, 20, _evaluate_adx_atr_regime_classifier),
    RegimeStrategyDefinition("cash_avoid_filter", "Cash/Avoid Trading", "safety", "safety_gate", 0.0, 1, cash_avoid_filter.evaluate),
)

REGIME_STRATEGY_ALIASES = {
    "adx_trend_strength": "adx_atr_regime_classifier",
    "adx_trend_strength_regime": "adx_atr_regime_classifier",
    "atr_volatility_regime": "adx_atr_regime_classifier",
    "cash_avoid_trading_filter": "cash_avoid_filter",
}


def _module_status(definition: RegimeStrategyDefinition) -> RegimeModuleStatus:
    return RegimeModuleStatus(id=definition.strategy_id, status="active")


REGIME_MODULE_INVENTORY = RegimeModuleInventory(
    algorithm_id="regime",
    catalog_version="regime_strategy_catalog_v3_backend",
    directional=tuple(_module_status(strategy) for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "directional"),
    context=tuple(_module_status(strategy) for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "confirmation"),
    regime=tuple(_module_status(strategy) for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "regime_context"),
    safety=tuple(_module_status(strategy) for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "safety_gate"),
)


def evaluate_strategy(strategy_id: str, snapshot: RegimeMarketSnapshot, classification: RegimeClassification) -> RegimeStrategyEvaluation:
    canonical = REGIME_STRATEGY_ALIASES.get(strategy_id, strategy_id)
    for definition in REGIME_STRATEGY_DEFINITIONS:
        if definition.strategy_id == canonical:
            return evaluate_definition(definition, snapshot, classification)
    raise KeyError(f"Unknown Regime strategy: {strategy_id}")


def regime_strategy_inventory() -> dict[str, object]:
    return {
        "algorithmId": "regime",
        "catalogVersion": "regime_strategy_catalog_v3_backend",
        "strategyCount": len(REGIME_STRATEGY_DEFINITIONS),
        "directionalCount": sum(1 for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "directional"),
        "confirmationCount": sum(1 for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "confirmation"),
        "contextCount": sum(1 for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "regime_context"),
        "safetyCount": sum(1 for strategy in REGIME_STRATEGY_DEFINITIONS if strategy.role == "safety_gate"),
        "moduleInventory": asdict(REGIME_MODULE_INVENTORY),
        "aliases": REGIME_STRATEGY_ALIASES,
    }


__all__ = [
    "REGIME_MODULE_INVENTORY",
    "REGIME_STRATEGY_ALIASES",
    "REGIME_STRATEGY_DEFINITIONS",
    "RegimeModuleInventory",
    "RegimeModuleLifecycleStatus",
    "RegimeModuleStatus",
    "evaluate_strategy",
    "regime_strategy_inventory",
]
