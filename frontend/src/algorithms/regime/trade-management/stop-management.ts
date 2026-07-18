import type { RegimeOpenPositionInput, RegimeOpenPositionManagement } from "./entry-policy.ts";

export function protectiveStopExitPolicy(input: RegimeOpenPositionInput): RegimeOpenPositionManagement | null {
  const latestPrice = input.latestPrice;
  if (!Number.isFinite(latestPrice)) return null;
  if (input.gapThroughStop) {
    return {
      action: input.currentPosition > 0 ? "exit_long" : "cover_short",
      reasonCodes: ["regime.trade_management.gap_through_stop_exit"],
    };
  }
  if (input.currentPosition > 0 && input.protectiveStopPrice !== null && input.protectiveStopPrice !== undefined && latestPrice! <= input.protectiveStopPrice) {
    return { action: "exit_long", reasonCodes: ["regime.trade_management.protective_stop_exit"] };
  }
  if (input.currentPosition < 0 && input.protectiveStopPrice !== null && input.protectiveStopPrice !== undefined && latestPrice! >= input.protectiveStopPrice) {
    return { action: "cover_short", reasonCodes: ["regime.trade_management.protective_stop_exit"] };
  }
  return null;
}
