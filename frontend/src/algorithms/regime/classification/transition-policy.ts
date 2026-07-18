import type { MarketRegimeId } from "../types.ts";

export const REGIME_RISK_OFF_REGIME_IDS = [
  "event_risk",
  "liquidity_stress",
  "extreme_volatility_no_trade",
] as const satisfies readonly MarketRegimeId[];

export function isRegimeRiskOffTransition(regime: MarketRegimeId): boolean {
  return (REGIME_RISK_OFF_REGIME_IDS as readonly string[]).includes(regime);
}

