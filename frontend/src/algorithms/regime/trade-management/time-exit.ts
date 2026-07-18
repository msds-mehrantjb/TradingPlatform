import type { RegimeOpenPositionInput, RegimeOpenPositionManagement } from "./entry-policy.ts";

export function timeExitPolicy(input: RegimeOpenPositionInput): RegimeOpenPositionManagement | null {
  if (input.maximumHoldingMinutes && input.entryTimestamp && input.now && minutesBetween(input.entryTimestamp, input.now) >= input.maximumHoldingMinutes) {
    return {
      action: input.currentPosition > 0 ? "exit_long" : "cover_short",
      reasonCodes: ["regime.trade_management.maximum_holding_time_exit"],
    };
  }
  if (input.endOfDayExitRequired) {
    return {
      action: input.currentPosition > 0 ? "exit_long" : "cover_short",
      reasonCodes: ["regime.trade_management.end_of_day_exit"],
    };
  }
  return null;
}

export function minutesBetween(start: string, end: string): number {
  const startMs = new Date(start).getTime();
  const endMs = new Date(end).getTime();
  return Number.isFinite(startMs) && Number.isFinite(endMs) ? (endMs - startMs) / 60_000 : 0;
}
