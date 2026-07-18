import type { RegimeMarketContext, RegimeRawStrategySignal } from "../../types.ts";

export function cashAvoidFilter(market: RegimeMarketContext): RegimeRawStrategySignal {
  const blockers: string[] = [];
  if (market.spreadLiquidity.spreadTooWide) blockers.push("spread too wide");
  if (market.spreadLiquidity.volumeTooLow || market.volume.relativeVolume < 0.55) blockers.push("volume too light");
  if (!market.timeOfDay.newTradesAllowed) blockers.push("outside new-trade window");
  return blockers.length
    ? {
        role: "safety_gate",
        signal: "Hold",
        confidence: 0,
        eligible: true,
        passed: false,
        blockNewEntries: true,
        reason: `Avoid trading: ${blockers.join(", ")}`,
      }
    : {
        role: "safety_gate",
        signal: "Hold",
        confidence: 0,
        eligible: true,
        passed: true,
        blockNewEntries: false,
        reason: "Cash filter has no hard block",
      };
}

