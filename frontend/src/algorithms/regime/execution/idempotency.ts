import type { PositionEffect } from "../../../trading/shared/order-intent-types.ts";

export function generateRegimeOrderIntentIdempotencyKey(input: {
  algorithmId?: "regime";
  symbol: string;
  decisionCandle: string;
  positionEffect: PositionEffect;
  settingsVersion: string;
  profileVersion: string;
}): string {
  const parts = [
    input.algorithmId ?? "regime",
    input.symbol.toUpperCase(),
    input.decisionCandle,
    input.positionEffect,
    input.settingsVersion,
    input.profileVersion,
  ];
  return parts.map(stableKeyPart).join(":");
}

function stableKeyPart(value: string): string {
  return value.trim().replace(/[^A-Za-z0-9_.-]+/g, "_") || "unknown";
}
