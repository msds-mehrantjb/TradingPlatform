import type { RegimeOrderIntent } from "./order-intent.ts";

export type RegimeBrokerAttribution = {
  algorithmId: "regime";
  algorithmVersion: string;
  decisionId: string;
  symbol: string;
  side: "Buy" | "Sell";
  positionEffect: RegimeOrderIntent["positionEffect"];
  requestedQuantity: number;
};

export function buildRegimeBrokerAttribution(intent: RegimeOrderIntent): RegimeBrokerAttribution {
  return Object.freeze({
    algorithmId: intent.algorithmId,
    algorithmVersion: intent.algorithmVersion,
    decisionId: intent.decisionId,
    symbol: intent.symbol,
    side: intent.signal,
    positionEffect: intent.positionEffect,
    requestedQuantity: intent.requestedQuantity,
  });
}
