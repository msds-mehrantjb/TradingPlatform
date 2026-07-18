import type { RegimeMarketContext, RegimePositionSnapshot, RegimeSelectionResult, RegimeSizingDefaults, RegimeTradingSettings } from "../types.ts";
import { buildRegimeTargetOrder, type RegimeOrderIntentOptions } from "./order-intent.ts";
import { validateRegimeTargetOrder } from "./order-validation.ts";

export const REGIME_EXECUTION_PIPELINE = Object.freeze([
  "decision_result",
  "dynamic_profile",
  "position_sizing",
  "order_intent",
  "order_validation",
  "broker_attribution",
  "reconciliation_adapter",
]);

export function buildRegimeExecutionPipeline(
  result: RegimeSelectionResult,
  market: RegimeMarketContext | null,
  symbol: string,
  settings: RegimeTradingSettings,
  defaults?: RegimeSizingDefaults,
  currentPosition?: RegimePositionSnapshot,
  options?: RegimeOrderIntentOptions,
) {
  const target = buildRegimeTargetOrder(result, market, symbol, settings, defaults, currentPosition, options);
  return {
    target,
    validationErrors: validateRegimeTargetOrder(target),
    modules: REGIME_EXECUTION_PIPELINE,
  };
}
