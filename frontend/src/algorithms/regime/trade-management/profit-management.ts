import type { RegimeOpenPositionInput, RegimeOpenPositionManagement } from "./entry-policy.ts";

export function profitTargetExitPolicy(input: RegimeOpenPositionInput): RegimeOpenPositionManagement | null {
  const latestPrice = input.latestPrice;
  if (!Number.isFinite(latestPrice)) return null;
  if (input.currentPosition > 0 && input.profitTargetPrice !== null && input.profitTargetPrice !== undefined && latestPrice! >= input.profitTargetPrice) {
    return { action: "exit_long", reasonCodes: ["regime.trade_management.profit_target_exit"] };
  }
  if (input.currentPosition < 0 && input.profitTargetPrice !== null && input.profitTargetPrice !== undefined && latestPrice! <= input.profitTargetPrice) {
    return { action: "cover_short", reasonCodes: ["regime.trade_management.profit_target_exit"] };
  }
  return null;
}
