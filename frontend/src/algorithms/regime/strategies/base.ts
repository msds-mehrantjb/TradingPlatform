import type {
  MarketRegimeId,
  RegimeMarketContext,
  RegimeRawStrategySignal,
  RegimeStrategyDefinition,
  StrategyFamily,
  StrategyRole,
} from "../types.ts";

export const REGIME_STRATEGY_BASE_WEIGHTS = {
  moving_average_trend: 0.11,
  trend_pullback: 0.1,
  rsi_mean_reversion: 0.07,
  bollinger_band_mean_reversion: 0.07,
  opening_range_breakout: 0.1,
  intraday_breakout: 0.1,
  macd_momentum: 0.08,
  market_structure: 0.12,
  gap_continuation_fade: 0.06,
} as const;

export const REGIME_ALL_COMPOSITE_REGIMES: MarketRegimeId[] = [
  "strong_uptrend",
  "weak_uptrend",
  "strong_downtrend",
  "weak_downtrend",
  "range_bound",
  "sideways_range",
  "opening_breakout",
  "intraday_expansion",
  "high_volatility_trend",
  "low_volatility_quiet",
  "failed_breakout_reversal",
  "choppy_mixed",
  "gap_session",
  "event_risk",
  "liquidity_stress",
  "extreme_volatility_no_trade",
];

export type RegimeStrategySignalFn = (market: RegimeMarketContext) => RegimeRawStrategySignal;

export type StrategyDefinitionInput = {
  id: string;
  name: string;
  role: StrategyRole;
  family: StrategyFamily;
  supportedDirections?: Array<"long" | "short">;
  requiredInputs?: string[];
  minimumBars?: number;
  supportedRegimes?: MarketRegimeId[];
  incompatibleRegimes?: MarketRegimeId[];
  enabledByDefault?: boolean;
  baseWeight?: number;
  version?: string;
  aliases?: string[];
  key?: string;
  signal: RegimeStrategySignalFn;
};

export function defineRegimeStrategy(input: StrategyDefinitionInput): RegimeStrategyDefinition {
  return {
    supportedDirections: input.supportedDirections ?? ["long", "short"],
    requiredInputs: input.requiredInputs ?? ["candles", "latest"],
    minimumBars: input.minimumBars ?? 5,
    supportedRegimes: input.supportedRegimes ?? REGIME_ALL_COMPOSITE_REGIMES,
    incompatibleRegimes: input.incompatibleRegimes ?? [],
    enabledByDefault: input.enabledByDefault ?? true,
    baseWeight: input.baseWeight ?? 0,
    version: input.version ?? "1.0.0",
    aliases: input.aliases ?? [],
    key: input.key,
    id: input.id,
    name: input.name,
    role: input.role,
    family: input.family,
    signal: input.signal,
  };
}

export function vote(signal: RegimeRawStrategySignal["signal"], confidence: number, reason: string): RegimeRawStrategySignal {
  return { signal, confidence, reason, quality: confidence, evidence: {} };
}

export function safetyGate(passed: boolean, reason: string): RegimeRawStrategySignal {
  return {
    role: "safety_gate",
    signal: "Hold",
    confidence: 0,
    eligible: true,
    passed,
    blockNewEntries: !passed,
    reason,
  };
}

