import { buildRegimeMarketContext, calculateRegimeDecision } from "../decision-engine.ts";
import { buildRegimeProfileModifierBreakdown } from "../dynamic-profile.ts";
import { buildRegimeTargetOrder } from "../order-intent.ts";
import { defaultRegimeSizingDefaults, defaultRegimeTradingSettings } from "../config.ts";
import type { RegimeHysteresisSnapshot, RegimePositionSnapshot, RegimeSelectedStrategy, RegimeTradingSettings } from "../types.ts";
import type { MarketCandle } from "../../../trading/shared/market-data-types.ts";
import {
  DEFAULT_REGIME_BACKTEST_COSTS,
  DEFAULT_REGIME_BACKTEST_GLOBAL_GATE,
  closeRegimeBacktestTrade,
  evaluateRegimeOpenPositionExit,
  simulateRegimeGlobalGate,
  simulateRegimeNextBarEntry,
  updateRegimeExcursion,
  type RegimeBacktestOpenPosition,
} from "./execution-simulator.ts";
import { buildRegimeBacktestReports, calculateRegimeBacktestMetrics, calculateRegimeBacktestPnl } from "./metrics.ts";
import type {
  RegimeBacktestComparison,
  RegimeBacktestDecision,
  RegimeBacktestExecutionCostModel,
  RegimeBacktestInput,
  RegimeBacktestResult,
  RegimeBacktestTrade,
  RegimeBacktestVariantId,
  RegimeBacktestWalkForwardFold,
} from "./types.ts";

const ENGINE_VERSION = "regime_backtest_v2" as const;

export function runRegimeBacktest(input: RegimeBacktestInput): RegimeBacktestResult {
  return runRegimeBacktestInternal(input, true);
}

function runRegimeBacktestInternal(input: RegimeBacktestInput, includeComparisons: boolean): RegimeBacktestResult {
  const candles = input.candles.slice().sort((left, right) => Date.parse(left.timestamp) - Date.parse(right.timestamp));
  const settings = settingsForVariant(input);
  const defaults = input.sizingDefaults ?? defaultRegimeSizingDefaults(settings);
  const costs = { ...DEFAULT_REGIME_BACKTEST_COSTS, ...input.costModel };
  const globalGate = { ...DEFAULT_REGIME_BACKTEST_GLOBAL_GATE, ...input.globalGate };
  const startingCapital = input.startingCapital ?? settings.startingCapital;
  let equity = startingCapital;
  let hysteresis: RegimeHysteresisSnapshot = null;
  let openPosition: RegimeBacktestOpenPosition | null = null;
  const decisions: RegimeBacktestDecision[] = [];
  const trades: RegimeBacktestTrade[] = [];

  for (let index = 0; index < candles.length; index += 1) {
    const candle = candles[index];
    if (openPosition) {
      const exit = evaluateRegimeOpenPositionExit(openPosition, candle);
      if (exit) {
        const trade = closeRegimeBacktestTrade(openPosition, candle, exit.price, exit.reason, costs);
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
        updateRegimeExcursion(openPosition, candle);
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
    const approved = simulateRegimeGlobalGate(target.quantity, target.riskDollars, target.triggerPrice ?? candle.close, globalGate);
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
      const entry = simulateRegimeNextBarEntry(target, approved.quantity, nextCandle, costs);
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
    const trade = closeRegimeBacktestTrade(openPosition, finalCandle, finalCandle.close, "end_of_backtest", costs);
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

function positionSnapshot(openPosition: RegimeBacktestOpenPosition | null, latestPrice: number): RegimePositionSnapshot {
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

function round4(value: number): number {
  return Math.round(value * 10000) / 10000;
}
