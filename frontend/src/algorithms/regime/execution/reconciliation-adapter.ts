import type { RegimeOrderIntent } from "./order-intent.ts";
import { buildRegimeBrokerAttribution, type RegimeBrokerAttribution } from "./broker-attribution.ts";

export type RegimeReconciliationAdapterResult = {
  attribution: RegimeBrokerAttribution;
  brokerOrderId: string | null;
  brokerStatus: string;
  reconciledAt: string;
};

export function adaptRegimeBrokerReconciliation(
  intent: RegimeOrderIntent,
  broker: { brokerOrderId?: string | null; status?: string; reconciledAt?: string } = {},
): RegimeReconciliationAdapterResult {
  return Object.freeze({
    attribution: buildRegimeBrokerAttribution(intent),
    brokerOrderId: broker.brokerOrderId ?? null,
    brokerStatus: broker.status ?? "unknown",
    reconciledAt: broker.reconciledAt ?? intent.generatedAt,
  });
}
