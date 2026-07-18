import type { RegimeStrategyDefinition } from "../types.ts";
import { adxTrendStrength } from "./confirmation/adx-trend-strength.ts";
import { volumeConfirmation } from "./confirmation/volume-confirmation.ts";
import { atrVolatilityRegime } from "./context/atr-volatility-regime.ts";
import { vwapPosition } from "./context/vwap-position.ts";
import { REGIME_STRATEGY_ALIAS_INVENTORY, REGIME_STRATEGY_ALIAS_MAP } from "./alias-map.ts";
import { defineRegimeStrategy, REGIME_STRATEGY_BASE_WEIGHTS } from "./base.ts";
import { bollingerBandMeanReversion } from "./directional/bollinger-band-mean-reversion.ts";
import { failedBreakoutReversal } from "./directional/failed-breakout-reversal.ts";
import { gapContinuationFade } from "./directional/gap-continuation-fade.ts";
import { intradayBreakout } from "./directional/intraday-breakout.ts";
import { liquiditySweepReversal } from "./directional/liquidity-sweep-reversal.ts";
import { macdMomentum } from "./directional/macd-momentum.ts";
import { marketStructure } from "./directional/market-structure.ts";
import { movingAverageTrend } from "./directional/moving-average-trend.ts";
import { openingRangeBreakout } from "./directional/opening-range-breakout.ts";
import { rsiMeanReversion } from "./directional/rsi-mean-reversion.ts";
import { trendPullback } from "./directional/trend-pullback.ts";
import { volatilityBreakout } from "./directional/volatility-breakout.ts";
import { vwapMeanReversion } from "./directional/vwap-mean-reversion.ts";
import { vwapTrendContinuation } from "./directional/vwap-trend-continuation.ts";
import { cashAvoidFilter } from "./safety/cash-avoid-filter.ts";
import { circuitBreakerGate } from "./safety/circuit-breaker.ts";
import { eventBlackoutGate } from "./safety/event-blackout.ts";
import { excessiveSpreadGate } from "./safety/excessive-spread.ts";
import { extremeVolatilityGate } from "./safety/extreme-volatility.ts";
import { haltLuldGate } from "./safety/halt-luld.ts";
import { insufficientLiquidityGate } from "./safety/insufficient-liquidity.ts";
import { missingCriticalDataGate } from "./safety/missing-critical-data.ts";
import { staleDataGate } from "./safety/stale-data.ts";
import { unsupportedSessionGate } from "./safety/unsupported-session.ts";

export const REGIME_DIRECTIONAL_STRATEGY_INVENTORY = [
  { key: "C1", id: "moving_average_trend", name: "Moving Average Trend", family: "Trend/momentum" },
  { key: "C3", id: "trend_pullback", name: "Trend Pullback", family: "Trend/pullback" },
  { key: "C4", id: "rsi_mean_reversion", name: "RSI Mean Reversion", family: "Mean reversion" },
  { key: "C5", id: "bollinger_band_mean_reversion", name: "Bollinger Band Mean Reversion", family: "Mean reversion" },
  { key: "C6", id: "opening_range_breakout", name: "Opening Range Breakout", family: "Breakout" },
  { key: "C7", id: "intraday_breakout", name: "Intraday Breakout", family: "Breakout" },
  { key: "C8", id: "macd_momentum", name: "MACD Momentum", family: "Momentum" },
  { key: "C9", id: "market_structure", name: "Market Structure", family: "Structure/trend" },
  { key: "C10", id: "gap_continuation_fade", name: "Gap Continuation/Fade", family: "Event/gap" },
  { key: "R1", id: "vwap_trend_continuation", name: "VWAP Trend Continuation", family: "Trend" },
  { key: "R2", id: "vwap_mean_reversion", name: "VWAP Mean Reversion", family: "Mean reversion" },
  { key: "R3", id: "failed_breakout_reversal", name: "Failed Breakout Reversal", family: "Reversal" },
  { key: "R4", id: "liquidity_sweep_reversal", name: "Liquidity Sweep Reversal", family: "Reversal" },
  { key: "R7", id: "volatility_breakout", name: "Volatility Breakout", family: "Breakout" },
] as const;

export const REGIME_CONFIRMATION_MODULE_INVENTORY = [
  { key: "C11", id: "volume_confirmation", name: "Volume Confirmation" },
  { key: "R5", id: "adx_trend_strength", name: "ADX Trend Strength" },
] as const;

export const REGIME_CONTEXT_MODULE_INVENTORY = [
  { key: "C2", id: "vwap_position", name: "VWAP Position" },
  { key: "R6", id: "atr_volatility_regime", name: "ATR Volatility Regime" },
] as const;

export const REGIME_SAFETY_GATE_INVENTORY = [
  { key: "R8", id: "cash_avoid_filter", name: "Cash/Avoid Trading" },
  { key: null, id: "missing_critical_data", name: "Missing Critical Data" },
  { key: null, id: "stale_data", name: "Stale Data" },
  { key: null, id: "extreme_volatility", name: "Extreme Volatility" },
  { key: null, id: "excessive_spread", name: "Excessive Spread" },
  { key: null, id: "insufficient_liquidity", name: "Insufficient Liquidity" },
  { key: null, id: "event_blackout", name: "Event Blackout" },
  { key: null, id: "halt_luld", name: "Halt/LULD" },
  { key: null, id: "circuit_breaker", name: "Circuit Breaker" },
  { key: null, id: "unsupported_session", name: "Unsupported Session" },
] as const;

export const REGIME_STRATEGY_ROLE_INVENTORY = {
  directional: REGIME_DIRECTIONAL_STRATEGY_INVENTORY,
  confirmation: REGIME_CONFIRMATION_MODULE_INVENTORY,
  regimeContext: REGIME_CONTEXT_MODULE_INVENTORY,
  safetyGate: REGIME_SAFETY_GATE_INVENTORY,
  aliases: REGIME_STRATEGY_ALIAS_INVENTORY,
} as const;

export const REGIME_TOTAL_STRATEGY_DEFINITION_COUNT =
  REGIME_DIRECTIONAL_STRATEGY_INVENTORY.length +
  REGIME_CONFIRMATION_MODULE_INVENTORY.length +
  REGIME_CONTEXT_MODULE_INVENTORY.length +
  REGIME_SAFETY_GATE_INVENTORY.length;

export const regimeSelectionStrategies: RegimeStrategyDefinition[] = [
  defineRegimeStrategy({ key: "C1", id: "moving_average_trend", name: "Moving Average Trend", role: "directional", family: "trend_momentum", baseWeight: REGIME_STRATEGY_BASE_WEIGHTS.moving_average_trend, requiredInputs: ["candles", "latest", "sma20", "sma50"], minimumBars: 50, signal: movingAverageTrend }),
  defineRegimeStrategy({ key: "C2", id: "vwap_position", name: "VWAP Position", role: "regime_context", family: "regime_context", requiredInputs: ["candles", "latest", "vwap"], minimumBars: 5, signal: vwapPosition }),
  defineRegimeStrategy({ key: "C3", id: "trend_pullback", name: "Trend Pullback", role: "directional", family: "trend_momentum", baseWeight: REGIME_STRATEGY_BASE_WEIGHTS.trend_pullback, aliases: ["first_pullback_after_open"], requiredInputs: ["candles", "latest", "sma20", "sma50", "vwap"], minimumBars: 50, signal: trendPullback }),
  defineRegimeStrategy({ key: "C4", id: "rsi_mean_reversion", name: "RSI Mean Reversion", role: "directional", family: "mean_reversion", baseWeight: REGIME_STRATEGY_BASE_WEIGHTS.rsi_mean_reversion, requiredInputs: ["candles", "latest", "rsi"], minimumBars: 15, signal: rsiMeanReversion }),
  defineRegimeStrategy({ key: "C5", id: "bollinger_band_mean_reversion", name: "Bollinger Band Mean Reversion", role: "directional", family: "mean_reversion", baseWeight: REGIME_STRATEGY_BASE_WEIGHTS.bollinger_band_mean_reversion, aliases: ["bollinger_atr_reversion"], requiredInputs: ["candles", "latest", "bollinger_bands"], minimumBars: 20, signal: bollingerBandMeanReversion }),
  defineRegimeStrategy({ key: "C6", id: "opening_range_breakout", name: "Opening Range Breakout", role: "directional", family: "breakout", baseWeight: REGIME_STRATEGY_BASE_WEIGHTS.opening_range_breakout, requiredInputs: ["candles", "latest", "opening_range"], minimumBars: 15, signal: openingRangeBreakout }),
  defineRegimeStrategy({ key: "C7", id: "intraday_breakout", name: "Intraday Breakout", role: "directional", family: "breakout", baseWeight: REGIME_STRATEGY_BASE_WEIGHTS.intraday_breakout, requiredInputs: ["candles", "latest", "recent_range"], minimumBars: 21, signal: intradayBreakout }),
  defineRegimeStrategy({ key: "C8", id: "macd_momentum", name: "MACD Momentum", role: "directional", family: "trend_momentum", baseWeight: REGIME_STRATEGY_BASE_WEIGHTS.macd_momentum, requiredInputs: ["candles", "latest", "macd"], minimumBars: 26, signal: macdMomentum }),
  defineRegimeStrategy({ key: "C9", id: "market_structure", name: "Market Structure", role: "directional", family: "trend_momentum", baseWeight: REGIME_STRATEGY_BASE_WEIGHTS.market_structure, requiredInputs: ["candles", "latest", "market_structure"], minimumBars: 10, signal: marketStructure }),
  defineRegimeStrategy({ key: "C10", id: "gap_continuation_fade", name: "Gap Continuation/Fade", role: "directional", family: "gap_session_event", baseWeight: REGIME_STRATEGY_BASE_WEIGHTS.gap_continuation_fade, requiredInputs: ["candles", "latest", "prior_close", "opening_range"], minimumBars: 15, signal: gapContinuationFade }),
  defineRegimeStrategy({ key: "C11", id: "volume_confirmation", name: "Volume Confirmation", role: "confirmation", family: "confirmation", requiredInputs: ["candles", "latest", "volume"], minimumBars: 5, signal: volumeConfirmation }),
  defineRegimeStrategy({ key: "R1", id: "vwap_trend_continuation", name: "VWAP Trend Continuation", role: "directional", family: "trend_momentum", baseWeight: 0.1, requiredInputs: ["candles", "latest", "vwap", "sma20", "sma50"], minimumBars: 50, signal: vwapTrendContinuation }),
  defineRegimeStrategy({ key: "R2", id: "vwap_mean_reversion", name: "VWAP Mean Reversion", role: "directional", family: "mean_reversion", baseWeight: 0.09, requiredInputs: ["candles", "latest", "vwap", "adx"], minimumBars: 15, signal: vwapMeanReversion }),
  defineRegimeStrategy({ key: "R3", id: "failed_breakout_reversal", name: "Failed Breakout Reversal", role: "directional", family: "reversal", baseWeight: 0.08, aliases: ["failed_breakout_strategy"], requiredInputs: ["candles", "latest", "recent_range"], minimumBars: 21, signal: failedBreakoutReversal }),
  defineRegimeStrategy({ key: "R4", id: "liquidity_sweep_reversal", name: "Liquidity Sweep Reversal", role: "directional", family: "reversal", baseWeight: 0.08, requiredInputs: ["candles", "latest", "recent_range", "volume"], minimumBars: 21, signal: liquiditySweepReversal }),
  defineRegimeStrategy({ key: "R5", id: "adx_trend_strength", name: "ADX Trend Strength", role: "confirmation", family: "confirmation", requiredInputs: ["candles", "latest", "adx"], minimumBars: 15, signal: adxTrendStrength }),
  defineRegimeStrategy({ key: "R6", id: "atr_volatility_regime", name: "ATR Volatility Regime", role: "regime_context", family: "regime_context", requiredInputs: ["candles", "latest", "atr"], minimumBars: 15, signal: atrVolatilityRegime }),
  defineRegimeStrategy({ key: "R7", id: "volatility_breakout", name: "Volatility Breakout", role: "directional", family: "breakout", baseWeight: 0.08, requiredInputs: ["candles", "latest", "atr", "recent_range", "volume"], minimumBars: 21, signal: volatilityBreakout }),
  defineRegimeStrategy({ key: "R8", id: "cash_avoid_filter", name: "Cash/Avoid Trading", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest", "spread_liquidity", "time_of_day"], minimumBars: 5, signal: cashAvoidFilter }),
  defineRegimeStrategy({ id: "missing_critical_data", name: "Missing Critical Data", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest"], minimumBars: 5, signal: missingCriticalDataGate }),
  defineRegimeStrategy({ id: "stale_data", name: "Stale Data", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest"], minimumBars: 5, signal: staleDataGate }),
  defineRegimeStrategy({ id: "extreme_volatility", name: "Extreme Volatility", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest", "atr"], minimumBars: 15, signal: extremeVolatilityGate }),
  defineRegimeStrategy({ id: "excessive_spread", name: "Excessive Spread", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest", "spread_liquidity"], minimumBars: 5, signal: excessiveSpreadGate }),
  defineRegimeStrategy({ id: "insufficient_liquidity", name: "Insufficient Liquidity", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest", "volume"], minimumBars: 5, signal: insufficientLiquidityGate }),
  defineRegimeStrategy({ id: "event_blackout", name: "Event Blackout", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest"], minimumBars: 5, signal: eventBlackoutGate }),
  defineRegimeStrategy({ id: "halt_luld", name: "Halt/LULD", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest"], minimumBars: 5, signal: haltLuldGate }),
  defineRegimeStrategy({ id: "circuit_breaker", name: "Circuit Breaker", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest"], minimumBars: 5, signal: circuitBreakerGate }),
  defineRegimeStrategy({ id: "unsupported_session", name: "Unsupported Session", role: "safety_gate", family: "safety", requiredInputs: ["candles", "latest", "time_of_day"], minimumBars: 5, signal: unsupportedSessionGate }),
];

export function canonicalRegimeStrategyId(idOrAlias: string): string {
  return REGIME_STRATEGY_ALIAS_MAP[idOrAlias as keyof typeof REGIME_STRATEGY_ALIAS_MAP] ?? idOrAlias;
}
