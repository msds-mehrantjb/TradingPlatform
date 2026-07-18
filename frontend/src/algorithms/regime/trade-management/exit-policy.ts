import type { RegimeOpenPositionInput, RegimeOpenPositionManagement } from "./entry-policy.ts";

export function eventRiskReductionPolicy(input: RegimeOpenPositionInput): RegimeOpenPositionManagement | null {
  if (!input.eventRiskReduction) return null;
  return {
    action: input.currentPosition > 0 ? "reduce_long" : "reduce_short",
    exitQuantityPercent: 0.5,
    reasonCodes: ["regime.trade_management.event_risk_reduction"],
  };
}

export function directionalExitPolicy(input: RegimeOpenPositionInput): RegimeOpenPositionManagement | null {
  if (input.signalDirection === "Hold") {
    return { action: input.currentPosition === 0 ? "none" : "hold", reasonCodes: ["regime.trade_management.no_exit_signal"] };
  }
  if (input.currentPosition > 0 && input.signalDirection === "Sell") {
    return { action: "exit_long", reasonCodes: ["regime.trade_management.bearish_signal_exits_long"] };
  }
  if (input.currentPosition < 0 && input.signalDirection === "Buy") {
    return { action: "cover_short", reasonCodes: ["regime.trade_management.bullish_signal_covers_short"] };
  }
  return null;
}
