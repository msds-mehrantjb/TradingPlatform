import type { RegimeOpenPositionInput, RegimeOpenPositionManagement } from "./entry-policy.ts";

export function regimeTransitionExitPolicy(input: RegimeOpenPositionInput): RegimeOpenPositionManagement | null {
  if (input.confirmedRegime && input.entryRegime && input.confirmedRegime !== input.entryRegime) {
    return {
      action: input.currentPosition > 0 ? "exit_long" : "cover_short",
      reasonCodes: ["regime.trade_management.regime_invalidation_exit"],
    };
  }
  return null;
}
