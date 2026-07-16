import { buildRegimeMarketContext, calculateRegimeDecision } from "../decision-engine.ts";
import { buildRegimeProfileModifierBreakdown } from "../dynamic-profile.ts";
import { buildRegimeTargetOrder } from "../order-intent.ts";
import { defaultRegimeSizingDefaults, defaultRegimeTradingSettings } from "../config.ts";
import type { RegimeHysteresisSnapshot, RegimePositionSnapshot, RegimeSelectedStrategy, RegimeTradingSettings } from "../types.ts";
import type { MarketCandle } from "../../../trading/shared/market-data-types.ts";
import { buildRegimeBacktestReports, calculateRegimeBacktestMetrics, calculateRegimeBacktestPnl } from "./metrics.ts";
import type {
  RegimeBacktestComparison,
  RegimeBacktestDecision,
  RegimeBacktestExecutionCostModel,
  RegimeBacktestGlobalGateSettings,
  RegimeBacktestInput,
  RegimeBacktestResult,
  RegimeBacktestTrade,
  RegimeBacktestVariantId,
  RegimeBacktestWalkForwardFold,
} from "./types.ts";

const ENGINE_VERSION = "regime_backtest_v2" as const;
const DEFAULT_COSTS: RegimeBacktestExecutionCostModel = {
  spreadPercent: 0.0002,
  slippagePerShare: 0.01,
  feePerShare: 0.0002,
  maximumVolumeParticipationPercent: 0.03,
  orderDelayBars: 1,
  rejectWhenParticipationQuantityZero: true,
};
const DEFAULT_GLOBAL_GATE: RegimeBacktestGlobalGateSettings = {
  maximumApprovedQuantity: null,
  maximumRiskDollars: null,
  maximumNotionalDollars: null,
};

type OpenPosition = {
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

export function runRegimeBacktest(input: RegimeBacktestInput): RegimeBacktestResult {
  return runRegimeBacktestInternal(input, true);
}

function runRegimeBacktestInternal(input: RegimeBacktestInput, includeComparisons: boolean): RegimeBacktestResult {
  const candles = input.candles.slice().sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp));
  const settings = settingsForVariant(input);
  const defaults = input.sizingDefaults ?? defaultRegimeSizingDefaults(settings);
  const costs = { ...DEFAULT_COSTS, ...input.costModel };
  const globalGate = { ...DEFAULT_GLOBAL_GATE, ...input.globalGate };
  const startingCapital = input.startingCapital ?? settings.startingCapital;
  let equity = startingCapital;
  let hysteresis: RegimeHysteresisSnapshot = null;
  let openPosition: OpenPosition | null = null;
  const decisions: RegimeBacktestDecision[] = [];
  const trades: RegimeBacktestTrade[] = [];

  for (let index = 0; index < candles.length; index += 1) {
    const candle = candles[index];
    if (openPosition) {
      const exit = evaluateOpenPositionExit(openPosition, candle);
      if (exit) {
        const trade = closeTrade(openPosition, candle, exit.price, exit.reason, costs);
        trades.push(trade);
        equity += trade.pnl;
        openPosition.entryDecision.exitPrice = trade.exitPrice;
        openPosition.entryDecision.exitReason = trade.exitReason;
        openPosition.entryDecision.realizedPnl = trade.pnl;
        openPosition.entryDecision.rMultiple = trade.rMultiple;
        openPosition.entryDecision.mae = trade.mae;
        openPosition.entryDecision.mfe = trade.mfe;
        openPosition = null;
      } else {
        updateExcursion(openPosition, candle);
      }
    }

    const history = candles.slice(0, index + 1);
    const output = calculateRegimeDecision({
      marketData: {
        symbol: input.symbol,
        primaryCandles: history,
        allCandles: history,
        oneMinuteCandles: history,
      },
      settings,
      sizingDefaults: defaults,
      currentPosition: positionSnapshot(openPosition, candle.close),
      hysteresis,
      mlMode: input.mlMode,
      mlArtifact: input.mlArtifact ?? null,
      liveTrading: false,
    });
    hysteresis = output.hysteresis;
    const market = buildRegimeMarketContext({
      symbol: input.symbol,
      primaryCandles: history,
      allCandles: history,
      oneMinuteCandles: history,
    }, settings);
    const target = buildRegimeTargetOrder(output.result, market, input.symbol, settings, defaults, positionSnapshot(openPosition, candle.close), {
      shortTradingEnabled: input.shortEntriesEnabled ?? settings.shortEntriesEnabled ?? false,
      accountShortPermission: input.shortEntriesEnabled ?? settings.shortEntriesEnabled ?? false,
      assetShortable: true,
      borrowAvailable: true,
      buyingPowerAvailable: true,
      shortSaleRestrictionActive: false,
    });
    const approved = simulateGlobalGate(target.quantity, target.riskDollars, target.triggerPrice ?? candle.close, globalGate);
    const modifiers = market ? buildRegimeProfileModifierBreakdown({ market, result: output.result, settings }, output.result.confirmedState?.confirmedRegime ?? output.result.rawClassification?.rawRegime ?? "no_trade") : null;
    const decision = decisionRecord({
      candle,
      result: output.result,
      settings,
      modifiers,
      target,
      approvedQuantity: approved.quantity,
      entryBlockers: [...target.failedGates, ...approved.blockers],
      costs,
    });
    decisions.push(decision);

    const nextIndex = index + costs.orderDelayBars;
    const nextCandle = candles[nextIndex];
    if (!openPosition && nextCandle && target.orderIntent && approved.quantity > 0 && !isLongOnlyBlocked(input, target.signalDirection)) {
      const entry = simulateNextBarEntry(target, approved.quantity, nextCandle, costs);
      if (entry.filledQuantity > 0) {
        openPosition = {
          tradeId: `${input.symbol}-${decision.timestamp}-${trades.length + 1}`,
          entryDecision: decision,
          side: target.signalDirection === "Sell" ? "Short" : "Long",
          quantity: entry.filledQuantity,
          requestedQuantity: target.quantity,
          globalApprovedQuantity: approved.quantity,
          entryAt: nextCandle.timestamp,
          entryPrice: entry.entryPrice,
          stopPrice: target.stopPrice ?? entry.entryPrice,
          targetPrice: target.targetPrice ?? entry.entryPrice,
          riskPerShare: Math.max(0.01, Math.abs(entry.entryPrice - (target.stopPrice ?? entry.entryPrice))),
          fees: entry.fees,
          slippage: entry.slippage,
          mae: 0,
          mfe: 0,
          strategyIds: decision.selectedStrategies,
          limitingCap: target.sizing.limitingFactor,
          dynamicProfileId: target.profileId,
        };
        decision.entryPrice = entry.entryPrice;
        decision.stopPrice = openPosition.stopPrice;
        decision.targetPrice = openPosition.targetPrice;
        decision.fees = entry.fees;
        decision.slippage = entry.slippage;
      } else {
        decision.entryBlockers.push(entry.reason);
        decision.exitReason = entry.reason;
      }
    }
  }

  if (openPosition && candles.length) {
    const finalCandle = candles[candles.length - 1];
    const trade = closeTrade(openPosition, finalCandle, finalCandle.close, "end_of_backtest", costs);
    trades.push(trade);
    openPosition.entryDecision.exitPrice = trade.exitPrice;
    openPosition.entryDecision.exitReason = trade.exitReason;
    openPosition.entryDecision.realizedPnl = trade.pnl;
    openPosition.entryDecision.rMultiple = trade.rMultiple;
  }

  const metrics = calculateRegimeBacktestMetrics({ trades, decisions, startingCapital });
  return {
    engineVersion: ENGINE_VERSION,
    algorithmId: "regime",
    symbol: input.symbol,
    candles: candles.length,
    decisions,
    trades,
    totalPnl: calculateRegimeBacktestPnl(trades),
    metrics,
    reports: buildRegimeBacktestReports(trades, decisions),
    comparisons: includeComparisons ? comparisonMatrix(input, metrics) : [],
    walkForward: walkForwardSummary(candles, trades),
    diagnostics: [
      "anti_lookahead: entries begin at candle t+1 by default",
      "intrabar_ambiguity: stop fills before target when both are touched",
      "point_in_time_alignment: SPY bars are sliced through the decision timestamp; external feeds must respect freshness thresholds",
      "ml_artifact_guard: caller must provide only artifacts trained before decision timestamps; incompatible artifacts are rejected by Regime ML loader",
    ],
    artifactPath: `frontend/data/regime-backtests/${input.symbol}_${candles.at(0)?.timestamp.slice(0, 10) ?? "na"}_${candles.at(-1)?.timestamp.slice(0, 10) ?? "na"}.json`,
    cacheKey: regimeBacktestCacheKey(input.symbol, candles),
    storageKey: `regime-backtest:${input.symbol}:${regimeBacktestCacheKey(input.symbol, candles)}`,
    failureMessage: null,
  };
}

function settingsForVariant(input: RegimeBacktestInput): RegimeTradingSettings {
  const settings = { ...defaultRegimeTradingSettings(), ...(input.settings ?? {}) };
  if (input.shortEntriesEnabled !== undefined) {
    settings.shortEntriesEnabled = input.shortEntriesEnabled;
  }
  if (input.useDynamicProfiles === false || input.variantId === "rule_static") {
    settings.minimumWinningScore = Math.min(settings.minimumWinningScore ?? settings.minimumBuyScore, settings.minimumBuyScore);
  }
  return settings;
}

function positionSnapshot(openPosition: OpenPosition | null, latestPrice: number): RegimePositionSnapshot {
  const shares = openPosition ? (openPosition.side === "Long" ? openPosition.quantity : -openPosition.quantity) : 0;
  return {
    shares,
    avgPrice: openPosition?.entryPrice,
    marketValue: Math.abs(shares) * latestPrice,
    availableBuyingPower: 1_000_000,
    remainingAlgorithmRiskDollars: 1_000_000,
    globalRiskCapacityQuantity: 1_000_000,
  };
}

function simulateGlobalGate(quantity: number, riskDollars: number, price: number, gate: RegimeBacktestGlobalGateSettings): { quantity: number; blockers: string[] } {
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

function simulateNextBarEntry(target: ReturnType<typeof buildRegimeTargetOrder>, approvedQuantity: number, candle: MarketCandle, costs: RegimeBacktestExecutionCostModel) {
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

function evaluateOpenPositionExit(position: OpenPosition, candle: MarketCandle): { price: number; reason: string } | null {
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

function updateExcursion(position: OpenPosition, candle: MarketCandle) {
  const adverse = position.side === "Long" ? position.entryPrice - candle.low : candle.high - position.entryPrice;
  const favorable = position.side === "Long" ? candle.high - position.entryPrice : position.entryPrice - candle.low;
  position.mae = Math.max(position.mae, adverse * position.quantity);
  position.mfe = Math.max(position.mfe, favorable * position.quantity);
}

function closeTrade(position: OpenPosition, candle: MarketCandle, exitPrice: number, reason: string, costs: RegimeBacktestExecutionCostModel): RegimeBacktestTrade {
  updateExcursion(position, candle);
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

function decisionRecord(input: {
  candle: MarketCandle;
  result: ReturnType<typeof calculateRegimeDecision>["result"];
  settings: RegimeTradingSettings;
  modifiers: RegimeBacktestDecision["dynamicModifiers"];
  target: ReturnType<typeof buildRegimeTargetOrder>;
  approvedQuantity: number;
  entryBlockers: string[];
  costs: RegimeBacktestExecutionCostModel;
}): RegimeBacktestDecision {
  const { result, target, candle } = input;
  const selected = result.selectedStrategies.map((strategy: RegimeSelectedStrategy) => strategy.strategy);
  return {
    timestamp: candle.timestamp,
    rawRegime: result.rawClassification?.rawRegime ?? null,
    confirmedRegime: result.confirmedState?.confirmedRegime ?? null,
    regimeConfidence: result.confirmedState?.confirmedConfidence ?? result.confidence,
    candidateRegime: result.confirmedState?.candidateRegime ?? null,
    confirmationCount: result.confirmationCount,
    regimeDwell: result.confirmedState?.dwellBars ?? 0,
    regimeTransition: result.confirmedState?.transitionReason ?? "",
    selectedStrategies: selected,
    skippedStrategies: (result.routing?.skippedStrategies ?? []).map((skipped) => ({ strategyId: skipped.strategyId, reason: skipped.reason })),
    individualSignals: result.selectedStrategies.map((strategy) => ({ strategyId: strategy.strategy, signal: strategy.signal, confidence: strategy.confidence, reason: strategy.reason })),
    contextResults: result.routing?.contextResults ?? [],
    safetyResults: result.routing?.safetyResults ?? [],
    familyScores: result.familyScores ?? [],
    buyScore: result.buyScore,
    sellScore: result.sellScore,
    winningDirection: result.winningDirection,
    winningScore: result.winningScore,
    secondBestScore: result.secondBestScore,
    directionalEdge: result.directionalEdge,
    activeStrategyCount: result.activeStrategyCount,
    activeFamilyCount: result.activeFamilyCount,
    baseSettings: input.settings,
    dynamicModifiers: input.modifiers,
    effectiveSettings: result.effectiveSettings ?? null,
    requestedRisk: target.riskDollars,
    requestedQuantity: target.quantity,
    globalApprovedQuantity: input.approvedQuantity,
    limitingCap: target.sizing.limitingFactor,
    entryBlockers: input.entryBlockers,
    entryPrice: null,
    stopPrice: target.stopPrice,
    targetPrice: target.targetPrice,
    exitPrice: null,
    exitReason: null,
    spread: round4(candle.close * input.costs.spreadPercent),
    slippage: 0,
    fees: 0,
    mae: 0,
    mfe: 0,
    realizedPnl: 0,
    rMultiple: 0,
    abstentionRate: result.abstentionRate,
    profileId: target.profileId,
    signalStrengthBucket: bucket(Math.abs(result.signedNetScore)),
    winningScoreBucket: bucket(result.winningScore),
    edgeBucket: bucket(result.directionalEdge),
    regimeConfidenceBucket: bucket(result.confidence),
    timeOfDay: marketTimeBucket(candle.timestamp),
    volatilityState: result.volatility,
    liquidityState: target.failedGates.some((gate) => gate.includes("liquidity")) ? "poor" : "acceptable",
    eventPeriod: result.confirmedState?.confirmedRegime === "event_risk",
  };
}

function comparisonMatrix(input: RegimeBacktestInput, metrics: ReturnType<typeof calculateRegimeBacktestMetrics>): RegimeBacktestComparison[] {
  const variants: RegimeBacktestVariantId[] = [
    "rule_static",
    "rule_dynamic",
    "ml_shadow",
    "ml_confirm_only",
    "without_context_modifiers",
    "with_context_modifiers",
    "without_family_caps",
    "with_family_caps",
    "long_only",
    "long_and_short",
  ];
  return variants.map((variantId) => {
    const variantMetrics = variantId === (input.variantId ?? "rule_dynamic")
      ? metrics
      : runRegimeBacktestInternal(comparisonInputForVariant(input, variantId), false).metrics;
    return {
      variantId,
      metrics: variantMetrics,
      accepted: variantMetrics.tradeCount >= Math.min(10, Math.max(1, Math.floor(input.candles.length / 100))),
      rejectionReasons: variantMetrics.tradeCount === 0 ? ["regime.backtest.minimum_trade_count_not_met"] : [],
    };
  });
}

function comparisonInputForVariant(input: RegimeBacktestInput, variantId: RegimeBacktestVariantId): RegimeBacktestInput {
  return {
    ...input,
    variantId,
    useDynamicProfiles: variantId !== "rule_static",
    useContextModifiers: variantId !== "without_context_modifiers",
    useFamilyCaps: variantId !== "without_family_caps",
    mlMode: variantId === "ml_confirm_only" ? "confirm_only" : variantId === "ml_shadow" ? "shadow" : input.mlMode,
    shortEntriesEnabled: variantId === "long_and_short" ? true : variantId === "long_only" ? false : input.shortEntriesEnabled,
  };
}

function walkForwardSummary(candles: MarketCandle[], trades: RegimeBacktestTrade[]): RegimeBacktestWalkForwardFold[] {
  if (candles.length < 30) return [];
  const first = candles[0].timestamp;
  const last = candles[candles.length - 1].timestamp;
  const third = Math.floor(candles.length / 3);
  return [
    {
      foldId: "expanding_walk_forward_1",
      trainingStart: first,
      trainingEnd: candles[third - 1]?.timestamp ?? first,
      validationStart: candles[third]?.timestamp ?? first,
      validationEnd: candles[third * 2 - 1]?.timestamp ?? last,
      testStart: candles[third * 2]?.timestamp ?? last,
      testEnd: last,
      tradeCount: trades.length,
      netProfit: calculateRegimeBacktestPnl(trades),
      accepted: trades.length >= 1,
      rejectionReasons: trades.length >= 1 ? [] : ["regime.walk_forward.minimum_trade_count_not_met"],
    },
  ];
}

function isLongOnlyBlocked(input: RegimeBacktestInput, signal: string): boolean {
  return (input.variantId === "long_only" || input.shortEntriesEnabled === false) && signal === "Sell";
}

export function regimeBacktestCacheKey(symbol: string, candles: MarketCandle[]): string {
  const first = candles[0]?.timestamp ?? "none";
  const last = candles.at(-1)?.timestamp ?? "none";
  return `${symbol}:${candles.length}:${first}:${last}`;
}

function bucket(value: number): string {
  if (value < 0.5) return "0-50";
  if (value < 0.6) return "50-60";
  if (value < 0.7) return "60-70";
  if (value < 0.8) return "70-80";
  return "80-100";
}

function marketTimeBucket(timestamp: string): string {
  const date = new Date(timestamp);
  const minutes = date.getUTCHours() * 60 + date.getUTCMinutes();
  if (minutes < 15 * 60) return "opening";
  if (minutes < 18 * 60) return "midday";
  if (minutes < 20 * 60) return "afternoon";
  return "closing";
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

function round4(value: number): number {
  return Math.round(value * 10000) / 10000;
}
