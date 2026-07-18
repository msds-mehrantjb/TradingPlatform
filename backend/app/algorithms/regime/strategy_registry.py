"""Backend-authoritative Regime strategy catalog."""

from __future__ import annotations

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


REGIME_STRATEGY_DEFINITIONS: tuple[RegimeStrategyDefinition, ...] = (
    RegimeStrategyDefinition("moving_average_trend", "Moving Average Trend", "trend", "directional", 0.08, 50, moving_average_trend.evaluate),
    RegimeStrategyDefinition("trend_pullback", "Trend Pullback", "trend", "directional", 0.07, 50, trend_pullback.evaluate),
    RegimeStrategyDefinition("rsi_mean_reversion", "RSI Mean Reversion", "mean_reversion", "directional", 0.07, 20, rsi_mean_reversion.evaluate),
    RegimeStrategyDefinition("bollinger_band_mean_reversion", "Bollinger Band Mean Reversion", "mean_reversion", "directional", 0.07, 20, bollinger_band_mean_reversion.evaluate),
    RegimeStrategyDefinition("opening_range_breakout", "Opening Range Breakout", "breakout", "directional", 0.08, 10, opening_range_breakout.evaluate),
    RegimeStrategyDefinition("intraday_breakout", "Intraday Breakout", "breakout", "directional", 0.08, 20, intraday_breakout.evaluate),
    RegimeStrategyDefinition("macd_momentum", "MACD Momentum", "momentum", "directional", 0.07, 35, macd_momentum.evaluate),
    RegimeStrategyDefinition("market_structure", "Market Structure", "structure", "directional", 0.07, 10, market_structure.evaluate),
    RegimeStrategyDefinition("gap_continuation_fade", "Gap Continuation/Fade", "event", "directional", 0.07, 10, gap_continuation_fade.evaluate),
    RegimeStrategyDefinition("vwap_trend_continuation", "VWAP Trend Continuation", "vwap", "directional", 0.07, 10, vwap_trend_continuation.evaluate),
    RegimeStrategyDefinition("vwap_mean_reversion", "VWAP Mean Reversion", "vwap", "directional", 0.07, 10, vwap_mean_reversion.evaluate),
    RegimeStrategyDefinition("failed_breakout_reversal", "Failed Breakout Reversal", "reversal", "directional", 0.07, 10, failed_breakout_reversal.evaluate),
    RegimeStrategyDefinition("liquidity_sweep_reversal", "Liquidity Sweep Reversal", "reversal", "directional", 0.07, 10, liquidity_sweep_reversal.evaluate),
    RegimeStrategyDefinition("volatility_breakout", "Volatility Breakout", "breakout", "directional", 0.08, 20, volatility_breakout.evaluate),
    RegimeStrategyDefinition("volume_confirmation", "Volume Confirmation", "confirmation", "confirmation", 0.0, 10, volume_confirmation.evaluate),
    RegimeStrategyDefinition("adx_trend_strength", "ADX Trend Strength", "confirmation", "confirmation", 0.0, 20, adx_trend_strength.evaluate),
    RegimeStrategyDefinition("vwap_position", "VWAP Position", "regime_context", "regime_context", 0.0, 10, vwap_position.evaluate),
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
    "first_pullback_after_open": "trend_pullback",
    "bollinger_atr_reversion": "bollinger_band_mean_reversion",
    "failed_breakout_strategy": "failed_breakout_reversal",
}


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
        "aliases": REGIME_STRATEGY_ALIASES,
    }

