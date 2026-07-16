export type RegimeTradeHistoryRow = {
  id: string;
  symbol: string;
  side: "Buy" | "Sell";
  quantity: number;
  price: number;
  submittedAt: string;
};

export type RegimeOpenPositionManagement = {
  action: "hold" | "exit_long" | "cover_short" | "reduce_long" | "reduce_short" | "cancel_order" | "none";
  reasonCodes: string[];
  exitQuantityPercent?: number;
};

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

export function manageRegimeOpenPosition(input: {
  signalDirection: "Buy" | "Sell" | "Hold";
  currentPosition: number;
  tradeAllowed: boolean;
  latestPrice?: number;
  protectiveStopPrice?: number | null;
  profitTargetPrice?: number | null;
  entryTimestamp?: string | null;
  now?: string;
  maximumHoldingMinutes?: number | null;
  confirmedRegime?: string;
  entryRegime?: string | null;
  endOfDayExitRequired?: boolean;
  eventRiskReduction?: boolean;
  orderStatus?: "none" | "partial_fill" | "rejected" | "cancelled" | "pending";
  pendingOrderSubmittedAt?: string | null;
  stalePendingOrderMinutes?: number;
  gapThroughStop?: boolean;
  pyramidingEnabled?: boolean;
  freshEntrySignal?: boolean;
}): RegimeOpenPositionManagement {
  const lifecycle = orderLifecycleManagement(input);
  if (lifecycle) return lifecycle;
  if (input.currentPosition === 0) {
    return { action: "none", reasonCodes: ["regime.trade_management.no_position"] };
  }
  const protective = protectiveExitManagement(input);
  if (protective) return protective;
  if (input.eventRiskReduction) {
    return {
      action: input.currentPosition > 0 ? "reduce_long" : "reduce_short",
      exitQuantityPercent: 0.5,
      reasonCodes: ["regime.trade_management.event_risk_reduction"],
    };
  }
  if (input.confirmedRegime && input.entryRegime && input.confirmedRegime !== input.entryRegime) {
    return {
      action: input.currentPosition > 0 ? "exit_long" : "cover_short",
      reasonCodes: ["regime.trade_management.regime_invalidation_exit"],
    };
  }
  if (input.signalDirection === "Hold") {
    return { action: input.currentPosition === 0 ? "none" : "hold", reasonCodes: ["regime.trade_management.no_exit_signal"] };
  }
  if (input.currentPosition > 0 && input.signalDirection === "Sell") {
    return { action: "exit_long", reasonCodes: ["regime.trade_management.bearish_signal_exits_long"] };
  }
  if (input.currentPosition < 0 && input.signalDirection === "Buy") {
    return { action: "cover_short", reasonCodes: ["regime.trade_management.bullish_signal_covers_short"] };
  }
  if (!input.pyramidingEnabled && input.freshEntrySignal && ((input.currentPosition > 0 && input.signalDirection === "Buy") || (input.currentPosition < 0 && input.signalDirection === "Sell"))) {
    return { action: "hold", reasonCodes: ["regime.trade_management.pyramiding_disabled"] };
  }
  return { action: "hold", reasonCodes: ["regime.trade_management.signal_matches_position"] };
}

function protectiveExitManagement(input: Parameters<typeof manageRegimeOpenPosition>[0]): RegimeOpenPositionManagement | null {
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
  const latestPrice = input.latestPrice;
  if (!Number.isFinite(latestPrice)) return null;
  if (input.gapThroughStop) {
    return {
      action: input.currentPosition > 0 ? "exit_long" : "cover_short",
      reasonCodes: ["regime.trade_management.gap_through_stop_exit"],
    };
  }
  if (input.currentPosition > 0 && input.protectiveStopPrice !== null && input.protectiveStopPrice !== undefined && latestPrice! <= input.protectiveStopPrice) {
    return { action: "exit_long", reasonCodes: ["regime.trade_management.protective_stop_exit"] };
  }
  if (input.currentPosition < 0 && input.protectiveStopPrice !== null && input.protectiveStopPrice !== undefined && latestPrice! >= input.protectiveStopPrice) {
    return { action: "cover_short", reasonCodes: ["regime.trade_management.protective_stop_exit"] };
  }
  if (input.currentPosition > 0 && input.profitTargetPrice !== null && input.profitTargetPrice !== undefined && latestPrice! >= input.profitTargetPrice) {
    return { action: "exit_long", reasonCodes: ["regime.trade_management.profit_target_exit"] };
  }
  if (input.currentPosition < 0 && input.profitTargetPrice !== null && input.profitTargetPrice !== undefined && latestPrice! <= input.profitTargetPrice) {
    return { action: "cover_short", reasonCodes: ["regime.trade_management.profit_target_exit"] };
  }
  return null;
}

function orderLifecycleManagement(input: Parameters<typeof manageRegimeOpenPosition>[0]): RegimeOpenPositionManagement | null {
  if (input.orderStatus === "partial_fill") return { action: "hold", reasonCodes: ["regime.trade_management.partial_fill_monitor"] };
  if (input.orderStatus === "rejected") return { action: "none", reasonCodes: ["regime.trade_management.rejected_order_no_position_change"] };
  if (input.orderStatus === "cancelled") return { action: "none", reasonCodes: ["regime.trade_management.cancelled_order_no_position_change"] };
  if (input.orderStatus === "pending" && input.pendingOrderSubmittedAt && input.now && minutesBetween(input.pendingOrderSubmittedAt, input.now) >= (input.stalePendingOrderMinutes ?? 5)) {
    return { action: "cancel_order", reasonCodes: ["regime.trade_management.stale_pending_order_cancel"] };
  }
  return null;
}

function minutesBetween(start: string, end: string): number {
  const startMs = new Date(start).getTime();
  const endMs = new Date(end).getTime();
  return Number.isFinite(startMs) && Number.isFinite(endMs) ? (endMs - startMs) / 60_000 : 0;
}
