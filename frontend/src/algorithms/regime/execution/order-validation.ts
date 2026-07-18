import type { PositionEffect } from "../../../trading/shared/order-intent-types.ts";
import type { RegimeOrderIntent, RegimeOrderIntentOptions, RegimeTargetOrder } from "./order-intent.ts";
import type { RegimeSelectionResult } from "../types.ts";

export type RegimeOrderValidationInput = {
  result: RegimeSelectionResult;
  signalDirection: "Buy" | "Sell" | "Hold";
  positionEffect: PositionEffect;
  requestedQuantity: number;
  expectedEntryPrice: number | null;
  protectiveStopPrice: number | null;
  targetPrice: number | null;
  options: RegimeOrderIntentOptions;
};

export function validateRegimeOrderIntent(intent: RegimeOrderIntent | null): string[] {
  if (!intent) return ["regime.order_validation.intent_missing"];
  const errors: string[] = [];
  if (intent.algorithmId !== "regime") errors.push("regime.order_validation.algorithm_id");
  if (!intent.algorithmVersion) errors.push("regime.order_validation.algorithm_version");
  if (!intent.decisionId) errors.push("regime.order_validation.decision_id");
  if (!intent.symbol) errors.push("regime.order_validation.symbol");
  if (intent.signal !== "Buy" && intent.signal !== "Sell") errors.push("regime.order_validation.side");
  if (intent.positionEffect === "none") errors.push("regime.order_validation.position_effect");
  if (!Number.isFinite(intent.requestedQuantity) || intent.requestedQuantity <= 0) errors.push("regime.order_validation.quantity");
  if (!Number.isFinite(intent.expectedEntryPrice) || intent.expectedEntryPrice <= 0) errors.push("regime.order_validation.entry_price");
  if (!Number.isFinite(intent.protectiveStopPrice) || intent.protectiveStopPrice <= 0) errors.push("regime.order_validation.stop_price");
  if (!Number.isFinite(intent.targetPrice) || intent.targetPrice <= 0) errors.push("regime.order_validation.target_price");
  if (!Number.isFinite(intent.requestedRiskDollars) || intent.requestedRiskDollars < 0) errors.push("regime.order_validation.risk_amount");
  if (!Number.isFinite(intent.regimeConfidence) || intent.regimeConfidence < 0 || intent.regimeConfidence > 1) errors.push("regime.order_validation.regime_confidence");
  return errors;
}

export function validateRegimeTargetOrder(target: RegimeTargetOrder): string[] {
  if (!target.eligible) return target.failedGates;
  return [
    ...validateRegimeOrderIntent(target.orderIntent),
    ...(target.quantity > 0 ? [] : ["regime.order_validation.target_quantity"]),
    ...(target.triggerPrice !== null && target.triggerPrice > 0 ? [] : ["regime.order_validation.trigger_price"]),
    ...(target.stopPrice !== null && target.stopPrice > 0 ? [] : ["regime.order_validation.target_stop"]),
    ...(target.targetPrice !== null && target.targetPrice > 0 ? [] : ["regime.order_validation.target_profit"]),
  ];
}

export function regimeOrderIntentFailureReasons(input: RegimeOrderValidationInput): string[] {
  return [
    "regime.order_intent.blocked",
    ...(input.result.tradeAllowed ? [] : ["regime.order_intent.trade_not_allowed", ...input.result.tradeBlockers]),
    ...(input.signalDirection === "Hold" ? ["regime.order_intent.no_direction"] : []),
    ...(input.positionEffect === "none" ? ["regime.order_intent.no_position_effect"] : []),
    ...(input.requestedQuantity > 0 ? [] : ["regime.order_intent.quantity_zero"]),
    ...(finitePositive(input.expectedEntryPrice ?? 0) > 0 ? [] : ["regime.order_intent.invalid_entry_price"]),
    ...(finitePositive(input.protectiveStopPrice ?? 0) > 0 ? [] : ["regime.order_intent.invalid_protective_stop"]),
    ...(finitePositive(input.targetPrice ?? 0) > 0 ? [] : ["regime.order_intent.invalid_target"]),
    ...shortSaleGateReasons(input.options),
  ];
}

export function shortSaleGateReasons(options: RegimeOrderIntentOptions): string[] {
  const reasons: string[] = [];
  if (!options.shortTradingEnabled) reasons.push("regime.short.disabled");
  if (!options.accountShortPermission) reasons.push("regime.short.account_permission_missing");
  if (!options.assetShortable) reasons.push("regime.short.asset_not_shortable");
  if (options.borrowAvailable === false) reasons.push("regime.short.borrow_unavailable");
  if (options.buyingPowerAvailable === false) reasons.push("regime.short.buying_power_unavailable");
  if (options.shortSaleRestrictionActive) reasons.push("regime.short.short_sale_restriction_active");
  return reasons;
}

function finitePositive(value: number | undefined): number {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : 0;
}
