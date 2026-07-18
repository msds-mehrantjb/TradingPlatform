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

export type RegimeOpenPositionInput = {
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
};

import { directionalExitPolicy, eventRiskReductionPolicy } from "./exit-policy.ts";
import { profitTargetExitPolicy } from "./profit-management.ts";
import { reconcileRegimeOrderLifecycle } from "./position-reconciliation.ts";
import { regimeTransitionExitPolicy } from "./regime-transition-exit.ts";
import { protectiveStopExitPolicy } from "./stop-management.ts";
import { timeExitPolicy } from "./time-exit.ts";

export function manageRegimeOpenPosition(input: RegimeOpenPositionInput): RegimeOpenPositionManagement {
  const lifecycle = orderLifecycleManagement(input);
  if (lifecycle) return lifecycle;
  if (input.currentPosition === 0) {
    return { action: "none", reasonCodes: ["regime.trade_management.no_position"] };
  }
  const timed = timeExitPolicy(input);
  if (timed) return timed;
  const stop = protectiveStopExitPolicy(input);
  if (stop) return stop;
  const profit = profitTargetExitPolicy(input);
  if (profit) return profit;
  const eventRisk = eventRiskReductionPolicy(input);
  if (eventRisk) return eventRisk;
  const transition = regimeTransitionExitPolicy(input);
  if (transition) return transition;
  const directional = directionalExitPolicy(input);
  if (directional) return directional;
  if (!input.pyramidingEnabled && input.freshEntrySignal && ((input.currentPosition > 0 && input.signalDirection === "Buy") || (input.currentPosition < 0 && input.signalDirection === "Sell"))) {
    return { action: "hold", reasonCodes: ["regime.trade_management.pyramiding_disabled"] };
  }
  return { action: "hold", reasonCodes: ["regime.trade_management.signal_matches_position"] };
}

function orderLifecycleManagement(input: RegimeOpenPositionInput): RegimeOpenPositionManagement | null {
  return reconcileRegimeOrderLifecycle(input);
}
