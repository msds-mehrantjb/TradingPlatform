import type { RegimeBacktestDecision, RegimeBacktestMetrics, RegimeBacktestReportRow, RegimeBacktestReports, RegimeBacktestTrade } from "./types.ts";

export function calculateRegimeBacktestPnl(trades: RegimeBacktestTrade[]): number {
  return round2(trades.reduce((sum, trade) => sum + trade.pnl, 0));
}

export function calculateRegimeBacktestMetrics(input: {
  trades: RegimeBacktestTrade[];
  decisions: RegimeBacktestDecision[];
  startingCapital: number;
  staticBaselinePnl?: number;
}): RegimeBacktestMetrics {
  const { trades, decisions, startingCapital } = input;
  const pnl = calculateRegimeBacktestPnl(trades);
  const wins = trades.filter((trade) => trade.pnl > 0);
  const losses = trades.filter((trade) => trade.pnl < 0);
  const grossProfit = wins.reduce((sum, trade) => sum + trade.pnl, 0);
  const grossLoss = Math.abs(losses.reduce((sum, trade) => sum + trade.pnl, 0));
  const rValues = trades.map((trade) => trade.rMultiple);
  const returns = trades.map((trade) => (startingCapital > 0 ? trade.pnl / startingCapital : 0));
  const downside = returns.filter((value) => value < 0);
  const equity = equityCurve(trades, startingCapital);
  const drawdown = maximumDrawdown(equity);
  const regimeSwitches = decisions.reduce((count, decision, index) => {
    if (index === 0) return count;
    return decision.confirmedRegime !== decisions[index - 1].confirmedRegime ? count + 1 : count;
  }, 0);
  return {
    netReturn: startingCapital > 0 ? round4(pnl / startingCapital) : 0,
    netProfit: pnl,
    tradeCount: trades.length,
    winRate: trades.length ? round4(wins.length / trades.length) : 0,
    profitFactor: grossLoss > 0 ? round4(grossProfit / grossLoss) : grossProfit > 0 ? null : 0,
    expectancy: trades.length ? round4(pnl / trades.length) : 0,
    averageR: average(rValues),
    sharpeRatio: ratio(averageRaw(returns), standardDeviation(returns)),
    sortinoRatio: ratio(averageRaw(returns), standardDeviation(downside)),
    maximumDrawdown: drawdown.amount,
    drawdownDuration: drawdown.duration,
    calmarRatio: drawdown.amount > 0 && startingCapital > 0 ? round4((pnl / startingCapital) / (drawdown.amount / startingCapital)) : null,
    exposure: decisions.length ? round4(trades.reduce((sum, trade) => sum + trade.holdingMinutes, 0) / Math.max(1, decisions.length)) : 0,
    turnover: startingCapital > 0 ? round4(trades.reduce((sum, trade) => sum + trade.entryPrice * trade.quantity, 0) / startingCapital) : 0,
    averageHoldingMinutes: average(trades.map((trade) => trade.holdingMinutes)),
    longPerformance: round2(trades.filter((trade) => trade.side === "Long").reduce((sum, trade) => sum + trade.pnl, 0)),
    shortPerformance: round2(trades.filter((trade) => trade.side === "Short").reduce((sum, trade) => sum + trade.pnl, 0)),
    regimeCoverage: decisions.length ? round4(new Set(decisions.map((decision) => decision.confirmedRegime).filter(Boolean)).size / decisions.length) : 0,
    noTradePercentage: decisions.length ? round4(decisions.filter((decision) => decision.winningDirection === "hold" || decision.globalApprovedQuantity === 0).length / decisions.length) : 0,
    regimeSwitchFrequency: decisions.length ? round4(regimeSwitches / decisions.length) : 0,
    averageConfirmationDelay: average(decisions.map((decision) => decision.confirmationCount)),
    falseTransitionRate: decisions.length ? round4(decisions.filter((decision) => decision.regimeTransition.includes("held")).length / decisions.length) : 0,
    blockedTradeCounterfactualResult: round2(decisions.filter((decision) => decision.entryBlockers.length > 0).reduce((sum, decision) => sum + decision.realizedPnl, 0)),
    staticVersusDynamicProfileDifference: round2(pnl - (input.staticBaselinePnl ?? pnl)),
  };
}

export function buildRegimeBacktestReports(trades: RegimeBacktestTrade[], decisions: RegimeBacktestDecision[]): RegimeBacktestReports {
  return {
    confirmedRegime: reportBy(trades, (trade) => trade.confirmedRegime ?? "unknown"),
    rawRegime: reportBy(trades, (trade) => trade.rawRegime ?? "unknown"),
    transitionState: reportByDecisions(decisions, (decision) => (decision.regimeTransition.includes("held") ? "transition" : "stable")),
    strategy: reportByExploded(trades, (trade) => trade.strategyIds.length ? trade.strategyIds : ["none"]),
    strategyFamily: reportByExploded(trades, (trade) => trade.familyScores.map((score) => score.family)),
    side: reportBy(trades, (trade) => trade.side),
    timeOfDay: reportByDecisions(decisions, (decision) => decision.timeOfDay),
    volatilityState: reportByDecisions(decisions, (decision) => decision.volatilityState),
    liquidityState: reportByDecisions(decisions, (decision) => decision.liquidityState),
    eventPeriod: reportByDecisions(decisions, (decision) => (decision.eventPeriod ? "event" : "non_event")),
    dynamicProfile: reportBy(trades, (trade) => trade.dynamicProfileId ?? "none"),
    signalStrengthBucket: reportByDecisions(decisions, (decision) => decision.signalStrengthBucket),
    winningScoreBucket: reportByDecisions(decisions, (decision) => decision.winningScoreBucket),
    edgeBucket: reportByDecisions(decisions, (decision) => decision.edgeBucket),
    regimeConfidenceBucket: reportByDecisions(decisions, (decision) => decision.regimeConfidenceBucket),
    month: reportBy(trades, (trade) => trade.entryAt.slice(0, 7)),
    year: reportBy(trades, (trade) => trade.entryAt.slice(0, 4)),
    exitReason: reportBy(trades, (trade) => trade.exitReason),
    limitingQuantityCap: reportBy(trades, (trade) => trade.limitingCap),
  };
}

function reportBy(trades: RegimeBacktestTrade[], keyFn: (trade: RegimeBacktestTrade) => string): RegimeBacktestReportRow[] {
  const groups = new Map<string, RegimeBacktestTrade[]>();
  trades.forEach((trade) => groups.set(keyFn(trade), [...(groups.get(keyFn(trade)) ?? []), trade]));
  return [...groups.entries()].map(([key, group]) => row(key, group)).sort((left, right) => right.netPnl - left.netPnl);
}

function reportByExploded(trades: RegimeBacktestTrade[], keyFn: (trade: RegimeBacktestTrade) => string[]): RegimeBacktestReportRow[] {
  const groups = new Map<string, RegimeBacktestTrade[]>();
  trades.forEach((trade) => keyFn(trade).forEach((key) => groups.set(key, [...(groups.get(key) ?? []), trade])));
  return [...groups.entries()].map(([key, group]) => row(key, group)).sort((left, right) => right.netPnl - left.netPnl);
}

function reportByDecisions(decisions: RegimeBacktestDecision[], keyFn: (decision: RegimeBacktestDecision) => string): RegimeBacktestReportRow[] {
  const groups = new Map<string, RegimeBacktestDecision[]>();
  decisions.forEach((decision) => groups.set(keyFn(decision), [...(groups.get(keyFn(decision)) ?? []), decision]));
  return [...groups.entries()].map(([key, group]) => ({
    key,
    trades: group.filter((decision) => decision.realizedPnl !== 0).length,
    netPnl: round2(group.reduce((sum, decision) => sum + decision.realizedPnl, 0)),
    averageR: average(group.map((decision) => decision.rMultiple)),
    winRate: group.length ? round4(group.filter((decision) => decision.realizedPnl > 0).length / group.length) : 0,
  }));
}

function row(key: string, trades: RegimeBacktestTrade[]): RegimeBacktestReportRow {
  return {
    key,
    trades: trades.length,
    netPnl: calculateRegimeBacktestPnl(trades),
    averageR: average(trades.map((trade) => trade.rMultiple)),
    winRate: trades.length ? round4(trades.filter((trade) => trade.pnl > 0).length / trades.length) : 0,
  };
}

function equityCurve(trades: RegimeBacktestTrade[], startingCapital: number): number[] {
  let equity = startingCapital;
  return [equity, ...trades.map((trade) => (equity += trade.pnl))];
}

function maximumDrawdown(equity: number[]): { amount: number; duration: number } {
  let peak = equity[0] ?? 0;
  let maxDrawdownAmount = 0;
  let currentDuration = 0;
  let maxDuration = 0;
  equity.forEach((value) => {
    if (value >= peak) {
      peak = value;
      currentDuration = 0;
      return;
    }
    currentDuration += 1;
    maxDuration = Math.max(maxDuration, currentDuration);
    maxDrawdownAmount = Math.max(maxDrawdownAmount, peak - value);
  });
  return { amount: round2(maxDrawdownAmount), duration: maxDuration };
}

function ratio(numerator: number, denominator: number): number | null {
  return denominator > 0 ? round4(numerator / denominator) : null;
}

function average(values: number[]): number {
  return round4(averageRaw(values));
}

function averageRaw(values: number[]): number {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}

function standardDeviation(values: number[]): number {
  const mean = averageRaw(values);
  return values.length ? Math.sqrt(values.reduce((sum, value) => sum + (value - mean) ** 2, 0) / values.length) : 0;
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

function round4(value: number): number {
  return Math.round(value * 10000) / 10000;
}
