import type { MarketCandle } from "../../../trading/shared/market-data-types.ts";
import type {
  RegimeBacktestDecision,
  RegimeBacktestExecutionCostModel,
  RegimeBacktestGlobalGateSettings,
  RegimeBacktestTrade,
} from "./types.ts";

export const DEFAULT_REGIME_BACKTEST_COSTS: RegimeBacktestExecutionCostModel = {
  spreadPercent: 0.0002,
  slippagePerShare: 0.01,
  feePerShare: 0.0002,
  maximumVolumeParticipationPercent: 0.03,
  orderDelayBars: 1,
  rejectWhenParticipationQuantityZero: true,
};

export const DEFAULT_REGIME_BACKTEST_GLOBAL_GATE: RegimeBacktestGlobalGateSettings = {
  maximumApprovedQuantity: null,
  maximumRiskDollars: null,
  maximumNotionalDollars: null,
};

export type RegimeBacktestOpenPosition = {
  tradeId: string;
  entryDecision: RegimeBacktestDecision;
  side: "Long" | "Short";
  quantity: number;
  requestedQuantity: number;
  globalApprovedQuantity: number;
  entryAt: string;
  entryPrice: number;
  stopPrice: number;
  targetPrice: number;
  riskPerShare: number;
  fees: number;
  slippage: number;
  mae: number;
  mfe: number;
  strategyIds: string[];
  limitingCap: string;
  dynamicProfileId: string | null;
};

export type RegimeBacktestExecutionTarget = {
  signalDirection: string;
};

export function simulateRegimeGlobalGate(
  quantity: number,
  riskDollars: number,
  price: number,
  gate: RegimeBacktestGlobalGateSettings,
): { quantity: number; blockers: string[] } {
  const caps = [quantity];
  const blockers: string[] = [];
  if (gate.maximumApprovedQuantity !== null) caps.push(gate.maximumApprovedQuantity);
  if (gate.maximumRiskDollars !== null && riskDollars > 0) caps.push(Math.floor(quantity * gate.maximumRiskDollars / riskDollars));
  if (gate.maximumNotionalDollars !== null && price > 0) caps.push(Math.floor(gate.maximumNotionalDollars / price));
  const approved = Math.max(0, Math.min(...caps));
  if (approved < quantity) blockers.push("regime.backtest.global_gate_capacity_reduced_quantity");
  if (approved <= 0 && quantity > 0) blockers.push("regime.backtest.global_gate_capacity_zero");
  return { quantity: approved, blockers };
}

export function simulateRegimeNextBarEntry(
  target: RegimeBacktestExecutionTarget,
  approvedQuantity: number,
  candle: MarketCandle,
  costs: RegimeBacktestExecutionCostModel,
): { filledQuantity: number; entryPrice: number; fees: number; slippage: number; reason: string } {
  const participation = Math.floor(candle.volume * costs.maximumVolumeParticipationPercent);
  if (participation <= 0 && costs.rejectWhenParticipationQuantityZero) {
    return { filledQuantity: 0, entryPrice: 0, fees: 0, slippage: 0, reason: "regime.backtest.rejected_participation_zero" };
  }
  const filledQuantity = Math.max(0, Math.min(approvedQuantity, participation || approvedQuantity));
  const halfSpread = candle.open * costs.spreadPercent / 2;
  const direction = target.signalDirection === "Sell" ? -1 : 1;
  const entryPrice = round2(candle.open + direction * (halfSpread + costs.slippagePerShare));
  return {
    filledQuantity,
    entryPrice,
    fees: round2(filledQuantity * costs.feePerShare),
    slippage: round2(filledQuantity * (halfSpread + costs.slippagePerShare)),
    reason: filledQuantity < approvedQuantity ? "regime.backtest.partial_fill" : "regime.backtest.filled",
  };
}

export function evaluateRegimeOpenPositionExit(
  position: RegimeBacktestOpenPosition,
  candle: MarketCandle,
): { price: number; reason: string } | null {
  if (position.side === "Long") {
    if (candle.open <= position.stopPrice) return { price: candle.open, reason: "gap_through_stop" };
    if (candle.low <= position.stopPrice) return { price: position.stopPrice, reason: "protective_stop" };
    if (candle.high >= position.targetPrice) return { price: position.targetPrice, reason: "profit_target" };
  } else {
    if (candle.open >= position.stopPrice) return { price: candle.open, reason: "gap_through_stop" };
    if (candle.high >= position.stopPrice) return { price: position.stopPrice, reason: "protective_stop" };
    if (candle.low <= position.targetPrice) return { price: position.targetPrice, reason: "profit_target" };
  }
  return null;
}

export function updateRegimeExcursion(position: RegimeBacktestOpenPosition, candle: MarketCandle): void {
  const adverse = position.side === "Long" ? position.entryPrice - candle.low : candle.high - position.entryPrice;
  const favorable = position.side === "Long" ? candle.high - position.entryPrice : position.entryPrice - candle.low;
  position.mae = Math.max(position.mae, adverse * position.quantity);
  position.mfe = Math.max(position.mfe, favorable * position.quantity);
}

export function closeRegimeBacktestTrade(
  position: RegimeBacktestOpenPosition,
  candle: MarketCandle,
  exitPrice: number,
  reason: string,
  costs: RegimeBacktestExecutionCostModel,
): RegimeBacktestTrade {
  updateRegimeExcursion(position, candle);
  const direction = position.side === "Long" ? 1 : -1;
  const exitSlippage = position.quantity * (candle.close * costs.spreadPercent / 2 + costs.slippagePerShare);
  const exitFees = position.quantity * costs.feePerShare;
  const gross = (exitPrice - position.entryPrice) * position.quantity * direction;
  const pnl = round2(gross - position.fees - exitFees - position.slippage - exitSlippage);
  const risk = position.riskPerShare * position.quantity;
  return {
    tradeId: position.tradeId,
    entryDecisionTimestamp: position.entryDecision.timestamp,
    entryAt: position.entryAt,
    exitAt: candle.timestamp,
    side: position.side,
    entryPrice: position.entryPrice,
    exitPrice: round2(exitPrice),
    stopPrice: position.stopPrice,
    targetPrice: position.targetPrice,
    quantity: position.quantity,
    requestedQuantity: position.requestedQuantity,
    globalApprovedQuantity: position.globalApprovedQuantity,
    pnl,
    fees: round2(position.fees + exitFees),
    slippage: round2(position.slippage + exitSlippage),
    mae: round2(position.mae),
    mfe: round2(position.mfe),
    rMultiple: risk > 0 ? round4(pnl / risk) : 0,
    holdingMinutes: Math.max(0, Math.round((Date.parse(candle.timestamp) - Date.parse(position.entryAt)) / 60000)),
    exitReason: reason,
    confirmedRegime: position.entryDecision.confirmedRegime,
    rawRegime: position.entryDecision.rawRegime,
    strategyIds: position.strategyIds,
    familyScores: position.entryDecision.familyScores,
    limitingCap: position.limitingCap,
    dynamicProfileId: position.dynamicProfileId,
  };
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

function round4(value: number): number {
  return Math.round(value * 10000) / 10000;
}
