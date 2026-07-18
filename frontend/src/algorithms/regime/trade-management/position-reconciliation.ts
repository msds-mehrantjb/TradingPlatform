import type { RegimeOpenPositionInput, RegimeOpenPositionManagement, RegimeTradeHistoryRow } from "./entry-policy.ts";
import { minutesBetween } from "./time-exit.ts";

export function summarizeRegimeTradeHistory(rows: RegimeTradeHistoryRow[], symbol: string): { tradeCount: number; netQuantity: number } {
  return rows
    .filter((row) => row.symbol === symbol)
    .reduce(
      (summary, row) => ({
        tradeCount: summary.tradeCount + 1,
        netQuantity: summary.netQuantity + (row.side === "Buy" ? row.quantity : -row.quantity),
      }),
      { tradeCount: 0, netQuantity: 0 },
    );
}

export function reconcileRegimeOrderLifecycle(input: RegimeOpenPositionInput): RegimeOpenPositionManagement | null {
  if (input.orderStatus === "partial_fill") return { action: "hold", reasonCodes: ["regime.trade_management.partial_fill_monitor"] };
  if (input.orderStatus === "rejected") return { action: "none", reasonCodes: ["regime.trade_management.rejected_order_no_position_change"] };
  if (input.orderStatus === "cancelled") return { action: "none", reasonCodes: ["regime.trade_management.cancelled_order_no_position_change"] };
  if (input.orderStatus === "pending" && input.pendingOrderSubmittedAt && input.now && minutesBetween(input.pendingOrderSubmittedAt, input.now) >= (input.stalePendingOrderMinutes ?? 5)) {
    return { action: "cancel_order", reasonCodes: ["regime.trade_management.stale_pending_order_cancel"] };
  }
  return null;
}
