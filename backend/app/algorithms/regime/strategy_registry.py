"""Backend-authoritative Regime strategy catalog."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from backend.app.algorithms.regime.contracts import RegimeClassification, RegimeMarketSnapshot, RegimeStrategyEvaluation
from backend.app.algorithms.regime.strategies.base import RegimeStrategyDefinition, evaluate_definition
from backend.app.algorithms.regime.strategies.confirmation import adx_trend_strength, volume_confirmation
from backend.app.algorithms.regime.strategies.context import atr_volatility_regime, vwap_position
from backend.app.algorithms.regime.strategies.directional import (
    bollinger_band_mean_reversion,
    failed_breakout_reversal,
    gap_continuation_fade,
    intraday_breakout,
    liquidity_sweep_reversal,
    macd_momentum,
    market_structure,
    moving_average_trend,
    opening_range_breakout,
    rsi_mean_reversion,
    trend_pullback,
    volatility_breakout,
    vwap_mean_reversion,
    vwap_trend_continuation,
)
from backend.app.algorithms.regime.strategies.safety import (
    cash_avoid_filter,
    circuit_breaker,
    event_blackout,
    excessive_spread,
    extreme_volatility,
    halt_luld,
    insufficient_liquidity,
    missing_critical_data,
    stale_data,
    unsupported_session,
)


RegimeModuleLifecycleStatus = Literal["active", "shadow", "disabled", "unavailable", "not_data_ready", "deprecated_alias"]
REGIME_REQUIRED_INITIAL_DIRECTIONAL_FAMILIES = frozenset({"trend", "breakout", "mean_reversion", "reversal"})
REGIME_MINIMUM_INDEPENDENT_DIRECTIONAL_FAMILIES = 2


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
    RegimeStrategyDefinition("moving_average_trend", "Moving Average Trend", "trend", "directional", 0.09, 20, moving_average_trend.evaluate),
    RegimeStrategyDefinition("trend_pullback", "Trend Pullback", "trend", "directional", 0.08, 20, trend_pullback.evaluate),
    RegimeStrategyDefinition("rsi_mean_reversion", "RSI Mean Reversion", "mean_reversion", "directional", 0.07, 20, rsi_mean_reversion.evaluate),
    RegimeStrategyDefinition("bollinger_band_mean_reversion", "Bollinger Band Mean Reversion", "mean_reversion", "directional", 0.07, 20, bollinger_band_mean_reversion.evaluate),
    RegimeStrategyDefinition("opening_range_breakout", "Opening Range Breakout", "breakout", "directional", 0.08, 20, opening_range_breakout.evaluate),
    RegimeStrategyDefinition("intraday_breakout", "Intraday Breakout", "breakout", "directional", 0.08, 20, intraday_breakout.evaluate),
    RegimeStrategyDefinition("macd_momentum", "MACD Momentum", "momentum", "directional", 0.07, 20, macd_momentum.evaluate),
    RegimeStrategyDefinition("market_structure", "Market Structure", "structure", "directional", 0.07, 20, market_structure.evaluate),
    RegimeStrategyDefinition("gap_continuation_fade", "Gap Continuation/Fade", "event", "directional", 0.06, 20, gap_continuation_fade.evaluate),
    RegimeStrategyDefinition("vwap_trend_continuation", "VWAP Trend Continuation", "vwap", "directional", 0.08, 20, vwap_trend_continuation.evaluate),
    RegimeStrategyDefinition("vwap_mean_reversion", "VWAP Mean Reversion", "vwap", "directional", 0.08, 20, vwap_mean_reversion.evaluate),
    RegimeStrategyDefinition("failed_breakout_reversal", "Failed Breakout Reversal", "reversal", "directional", 0.06, 20, failed_breakout_reversal.evaluate),
    RegimeStrategyDefinition("liquidity_sweep_reversal", "Liquidity Sweep Reversal", "reversal", "directional", 0.06, 20, liquidity_sweep_reversal.evaluate),
    RegimeStrategyDefinition("volatility_breakout", "Volatility Breakout", "breakout", "directional", 0.07, 20, volatility_breakout.evaluate),
    RegimeStrategyDefinition("volume_confirmation", "Volume Confirmation", "confirmation", "confirmation", 0.0, 20, volume_confirmation.evaluate),
    RegimeStrategyDefinition("adx_trend_strength", "ADX Trend Strength", "confirmation", "confirmation", 0.0, 20, adx_trend_strength.evaluate),
    RegimeStrategyDefinition("vwap_position", "VWAP Position", "regime_context", "regime_context", 0.0, 20, vwap_position.evaluate),
    RegimeStrategyDefinition("atr_volatility_regime", "ATR Volatility Regime", "regime_context", "regime_context", 0.0, 20, atr_volatility_regime.evaluate),
    RegimeStrategyDefinition("cash_avoid_filter", "Cash/Avoid Trading", "safety", "safety_gate", 0.0, 1, cash_avoid_filter.evaluate),
    RegimeStrategyDefinition("missing_critical_data", "Missing Critical Data", "safety", "safety_gate", 0.0, 1, missing_critical_data.evaluate),
    RegimeStrategyDefinition("stale_data", "Stale Data", "safety", "safety_gate", 0.0, 1, stale_data.evaluate),
    RegimeStrategyDefinition("extreme_volatility", "Extreme Volatility", "safety", "safety_gate", 0.0, 1, extreme_volatility.evaluate),
    RegimeStrategyDefinition("excessive_spread", "Excessive Spread", "safety", "safety_gate", 0.0, 1, excessive_spread.evaluate),
    RegimeStrategyDefinition("insufficient_liquidity", "Insufficient Liquidity", "safety", "safety_gate", 0.0, 1, insufficient_liquidity.evaluate),
    RegimeStrategyDefinition("event_blackout", "Event Blackout", "safety", "safety_gate", 0.0, 1, event_blackout.evaluate),
    RegimeStrategyDefinition("halt_luld", "Halt/LULD", "safety", "safety_gate", 0.0, 1, halt_luld.evaluate),
    RegimeStrategyDefinition("circuit_breaker", "Circuit Breaker", "safety", "safety_gate", 0.0, 1, circuit_breaker.evaluate),
    RegimeStrategyDefinition("unsupported_session", "Unsupported Session", "safety", "safety_gate", 0.0, 1, unsupported_session.evaluate),
)

REGIME_STRATEGY_ALIASES = {
    "adx_trend_strength_regime": "adx_trend_strength",
    "adx_atr_regime_classifier": "adx_trend_strength",
    "first_pullback_after_open": "trend_pullback",
    "bollinger_atr_reversion": "bollinger_band_mean_reversion",
    "failed_breakout_strategy": "failed_breakout_reversal",
    "cash_avoid_trading_filter": "cash_avoid_filter",
}


def _focused_test_path(definition: RegimeStrategyDefinition) -> Path:
    role_folder = {
        "directional": "directional",
        "confirmation": "confirmation",
        "regime_context": "context",
        "safety_gate": "safety",
    }[definition.role]
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "backend" / "tests" / "regime" / "strategies" / role_folder / f"test_{definition.strategy_id}.py"


def validate_regime_strategy_registry(definitions: tuple[RegimeStrategyDefinition, ...] = REGIME_STRATEGY_DEFINITIONS) -> dict[str, Any]:
    directional = tuple(definition for definition in definitions if definition.role == "directional")
    if not directional:
        raise RuntimeError("Regime strategy registry has zero directional strategies")
    families = {definition.family for definition in directional}
    if len(families) < REGIME_MINIMUM_INDEPENDENT_DIRECTIONAL_FAMILIES:
        raise RuntimeError("Regime strategy registry requires at least two independent directional families")
    missing_initial = REGIME_REQUIRED_INITIAL_DIRECTIONAL_FAMILIES - families
    if missing_initial:
        raise RuntimeError(f"Regime strategy registry missing required initial families: {sorted(missing_initial)}")
    definition_ids = [definition.strategy_id for definition in definitions]
    duplicates = sorted({strategy_id for strategy_id in definition_ids if definition_ids.count(strategy_id) > 1})
    if duplicates:
        raise RuntimeError(f"Regime strategy registry has duplicate strategy IDs: {duplicates}")
    aliases_as_definitions = sorted(set(REGIME_STRATEGY_ALIASES) & set(definition_ids))
    if aliases_as_definitions:
        raise RuntimeError(f"Regime strategy aliases cannot be registered as production definitions: {aliases_as_definitions}")
    missing_evaluators = sorted(definition.strategy_id for definition in definitions if not callable(definition.evaluator))
    if missing_evaluators:
        raise RuntimeError(f"Regime strategy registry missing evaluators: {missing_evaluators}")
    missing_tests = sorted(definition.strategy_id for definition in definitions if not _focused_test_path(definition).exists())
    if missing_tests:
        raise RuntimeError(f"Regime strategy registry missing focused tests: {missing_tests}")
    return {
        "algorithmId": "regime",
        "validated": True,
        "directionalCount": len(directional),
        "independentDirectionalFamilies": tuple(sorted(families)),
        "requiredInitialFamilies": tuple(sorted(REGIME_REQUIRED_INITIAL_DIRECTIONAL_FAMILIES)),
    }


REGIME_STRATEGY_REGISTRY_VALIDATION = validate_regime_strategy_registry()


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


def evaluate_strategy(
    strategy_id: str,
    snapshot: RegimeMarketSnapshot,
    classification: RegimeClassification,
    strategy_settings: dict[str, Any] | None = None,
) -> RegimeStrategyEvaluation:
    canonical = REGIME_STRATEGY_ALIASES.get(strategy_id, strategy_id)
    for definition in REGIME_STRATEGY_DEFINITIONS:
        if definition.strategy_id == canonical:
            return evaluate_definition(definition, snapshot, classification, strategy_settings)
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
        "registryValidation": REGIME_STRATEGY_REGISTRY_VALIDATION,
        "moduleInventory": asdict(REGIME_MODULE_INVENTORY),
        "aliases": REGIME_STRATEGY_ALIASES,
    }


__all__ = [
    "REGIME_MODULE_INVENTORY",
    "REGIME_MINIMUM_INDEPENDENT_DIRECTIONAL_FAMILIES",
    "REGIME_REQUIRED_INITIAL_DIRECTIONAL_FAMILIES",
    "REGIME_STRATEGY_ALIASES",
    "REGIME_STRATEGY_DEFINITIONS",
    "REGIME_STRATEGY_REGISTRY_VALIDATION",
    "RegimeModuleInventory",
    "RegimeModuleLifecycleStatus",
    "RegimeModuleStatus",
    "evaluate_strategy",
    "regime_strategy_inventory",
    "validate_regime_strategy_registry",
]
