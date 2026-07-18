import type { RegimePositionSnapshot } from "../types.ts";

export function regimePositionAndBuyingPowerCaps(input: {
  accountEquity: number;
  entryPrice: number;
  maxPositionPercent: number;
  orderAllocationPercent: number;
  dailyAllocationPercent: number;
  currentPosition: RegimePositionSnapshot;
}): {
  maxPositionDollars: number;
  maxOrderDollars: number;
  dailyBuyingPowerDollars: number;
  availableBuyingPower: number;
  allocationBasedQuantity: number;
  positionBasedQuantity: number;
  buyingPowerQuantity: number;
} {
  const maxPositionDollars = input.accountEquity * (input.maxPositionPercent / 100);
  const maxOrderDollars = input.accountEquity * (input.orderAllocationPercent / 100);
  const dailyBuyingPowerDollars = input.accountEquity * (input.dailyAllocationPercent / 100);
  const availableBuyingPower =
    input.currentPosition.availableBuyingPower !== undefined
      ? Math.max(0, input.currentPosition.availableBuyingPower)
      : Math.max(0, Math.min(maxPositionDollars, dailyBuyingPowerDollars) - input.currentPosition.marketValue);
  return {
    maxPositionDollars,
    maxOrderDollars,
    dailyBuyingPowerDollars,
    availableBuyingPower,
    allocationBasedQuantity: input.entryPrice > 0 ? maxOrderDollars / input.entryPrice : 0,
    positionBasedQuantity: input.entryPrice > 0 ? Math.max(0, maxPositionDollars - input.currentPosition.marketValue) / input.entryPrice : 0,
    buyingPowerQuantity: input.entryPrice > 0 ? availableBuyingPower / input.entryPrice : 0,
  };
}
