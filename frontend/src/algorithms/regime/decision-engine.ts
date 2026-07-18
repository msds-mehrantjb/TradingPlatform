import type { MarketCandle, MarketDataSnapshot } from "../../trading/shared/market-data-types.ts";
import { buildRawRegimeCondition } from "./classifier.ts";
import {
  REGIME_MAX_ABSTENTION_RATE,
  REGIME_MIN_CONDITION_CONFIDENCE,
  REGIME_MIN_INDEPENDENT_FAMILIES,
  REGIME_MIN_WINNING_EDGE,
  REGIME_MIN_WINNING_SCORE,
} from "./config.ts";
import { aggregateRegimeStrategyScores, regimeSystemWeightMultiplier } from "./family-aggregation.ts";
import { confirmedRegimeCondition } from "./hysteresis.ts";
import {
  aggregateCandlesToFiveMinute,
  averageDirectionalIndex,
  averageTrueRange,
  bollingerBands,
  clampNumber,
  easternDateString,
  easternMinutes,
  isPremarketSession,
  isRegularSession,
  macdValues,
  marketStructureContext,
  openingRangeValues,
  relativeStrengthIndex,
  rollingAtrSeries,
  roundNumber,
  sessionLabelForMinutes,
  sessionVwapValue,
  simpleMovingAverage,
} from "./indicators.ts";
import { loadRegimeMlArtifact } from "./ml/artifact-loader.ts";
import { buildRegimeMlFeatures } from "./ml/feature-builder.ts";
import { predictRegimeMl } from "./ml/predictor.ts";
import { normalizeRegimeContextFeeds, type RegimeContextFeedInput } from "./market/context-feeds.ts";
import { buildRegimeDecisionSnapshot } from "./persistence.ts";
import { resolveEffectiveRegimeSettings } from "./dynamic-profile.ts";
import {
  contextMultiplierForSignal,
  correlationPenalty,
  regimeCompatibilityMultiplier,
  regimeStrategySelectorReason,
  reliabilityMultiplier,
  routeRegimeStrategies,
} from "./router.ts";
import { buildDirectionalStrategyResult, evaluateRegimeStrategyDefinition, regimeSelectionStrategies } from "./strategy-catalog.ts";
import type {
  MarketRegimeId,
  RegimeAggregationResult,
  RegimeDecisionInput,
  RegimeDecisionOutput,
  RegimeMarketContext,
  RegimeSelectionResult,
  RegimeSelectionScores,
  RegimeStrategySignal,
  RegimeTradingSettings,
} from "./types.ts";
import type { RegimeMlMode, RegimeMlSnapshot } from "./ml/types.ts";

export function calculateRegimeDecision(input: RegimeDecisionInput): RegimeDecisionOutput {
  const market = buildRegimeMarketContext(input.marketData, input.settings);
  if (!market) {
    const result = emptyRegimeSelectionResult("Waiting for regular-session candles");
    return { result, hysteresis: input.hysteresis ?? null };
  }

  const rawCondition = buildRawRegimeCondition(market);
  const confirmation = confirmedRegimeCondition(market, rawCondition, input.hysteresis ?? null, input.hysteresisSettings);
  const features = rawCondition.features;
  const noTradeReasons = rawCondition.noTradeReasons;
  const primaryTrend = confirmation.condition.primaryTrend;
  const volatility = confirmation.condition.volatility;
  const opportunity = confirmation.condition.opportunity;
  const confirmedRegime = confirmation.state.confirmedRegime;
  const routing = routeRegimeStrategies(confirmedRegime, market);
  const selectedStrategyIdSet = new Set(routing.selectedStrategyIds);
  const selectedStrategies: RegimeSelectionResult["selectedStrategies"] = [];
  const skippedStrategies: RegimeSelectionResult["skippedStrategies"] = routing.skippedStrategies.map((skipped) => ({
    name: regimeSelectionStrategies.find((strategy) => strategy.id === skipped.strategyId)?.name ?? skipped.strategyId,
    reason: skipped.reason,
  }));
  const decisionTimestamp = market.latest.timestamp;

  regimeSelectionStrategies.forEach((strategy) => {
    const selected = strategy.role === "directional" ? selectedStrategyIdSet.has(strategy.id) : true;
    if (!selected) {
      return;
    }

    const raw = evaluateRegimeStrategyDefinition(strategy, market);
    const confidence = clampNumber(raw.confidence, 0, 1);
    const contractSignal = regimeContractSignal(raw.signal);
    const direction = regimeSignalDirection(contractSignal);
    const eligible = raw.eligible !== false && strategy.enabledByDefault;
    const contextMultiplier = contextMultiplierForSignal(strategy, contractSignal, routing.contextResults);
    const compatibilityMultiplier = regimeCompatibilityMultiplier(strategy.id, confirmedRegime);
    const reliability = reliabilityMultiplier(strategy);
    const correlation = correlationPenalty(strategy);
    const effectiveConfidence =
      strategy.role === "directional"
        ? clampNumber(raw.confidence * compatibilityMultiplier * contextMultiplier * reliability * correlation, 0, 1)
        : confidence;
    const effectiveWeight =
      strategy.role === "directional" && eligible
        ? roundNumber(Math.max(0, strategy.baseWeight * regimeSystemWeightMultiplier(strategy, contractSignal, market)), 4)
        : 0;
    const directionalResult = buildDirectionalStrategyResult({
      strategy,
      raw,
      market,
      decisionTimestamp,
      effectiveWeight,
    });
    const standardizedSignal = directionalResult ? regimeContractSignal(directionalResult.signal) : contractSignal;
    const standardizedDirection = regimeSignalDirection(standardizedSignal);
    const standardizedEligible = directionalResult ? directionalResult.eligible : eligible;
    const standardizedConfidence = directionalResult ? effectiveConfidence : confidence;
    const standardizedEffectiveWeight = directionalResult ? directionalResult.effectiveWeight : effectiveWeight;
    const standardizedContribution = directionalResult
      ? roundNumber(standardizedDirection * standardizedEffectiveWeight * standardizedConfidence * directionalResult.quality, 4)
      : strategy.role === "directional" && eligible
        ? roundNumber(direction * effectiveWeight * standardizedConfidence, 4)
        : 0;
    selectedStrategies.push({
      strategy: strategy.id,
      signal: standardizedSignal,
      confidence: standardizedConfidence,
      quality: directionalResult?.quality ?? clampNumber(raw.quality ?? confidence, 0, 1),
      base_weight: strategy.baseWeight,
      effective_weight: standardizedEffectiveWeight,
      effectiveWeight: standardizedEffectiveWeight,
      direction: standardizedDirection,
      reason: raw.reason,
      timestamp: directionalResult?.timestamp ?? decisionTimestamp,
      evidence: directionalResult?.evidence ?? raw.evidence ?? {},
      invalidReason: directionalResult?.invalidReason ?? raw.invalidReason,
      signedContribution: standardizedContribution,
      directionalResult: directionalResult ?? undefined,
      role: strategy.role,
      family: strategy.family,
      eligible: standardizedEligible,
      passed: raw.passed,
      blockNewEntries: raw.blockNewEntries,
      key: strategy.key,
      name: strategy.name,
      contribution: standardizedContribution,
      selected: true,
      selectorReason: regimeStrategySelectorReason(strategy.id, confirmedRegime),
      rawConfidence: confidence,
      effectiveConfidence: standardizedConfidence,
      compatibilityMultiplier,
      contextMultiplier,
      reliabilityMultiplier: reliability,
      correlationPenalty: correlation,
    });
  });

  const aggregation = aggregateRegimeStrategyScores(selectedStrategies);
  const buyScore = aggregation.buyScore;
  const sellScore = aggregation.sellScore;
  const holdScore = aggregation.abstentionRate;
  const winningScore = aggregation.winningScore;
  const secondBestScore = aggregation.secondBestScore;
  const scoreEdge = aggregation.directionalEdge;
  const conditionConfidence = confirmation.condition.confidence;
  const signedNetScore = signedRegimeNetScore(aggregation.scores);
  const normalizedNetScore = signedNetScore;
  const safetyBlockers = routing.safetyResults.filter((result) => result.blockNewEntries || !result.passed).map((result) => result.reason);
  const tradeBlockers = [
    ...regimeTradeBlockers(aggregation, conditionConfidence, opportunity, confirmation.held, input.settings, confirmedRegime),
    ...safetyBlockers,
  ];
  const tradeAllowed = tradeBlockers.length === 0;
  const signal =
    opportunity === "No-trade"
      ? "No-trade"
      : tradeAllowed && (aggregation.finalSignal === "buy" || aggregation.finalSignal === "sell")
        ? aggregation.finalSignal === "buy"
          ? "Buy"
          : "Sell"
        : "Hold";

  const result: RegimeSelectionResult = {
      signal,
      rawCondition: rawCondition.key,
      confirmedCondition: confirmation.condition.key,
      rawClassification: rawCondition.classification,
      confirmedState: confirmation.state,
      routing,
      familyScores: aggregation.familyScores,
      confirmationCount: confirmation.confirmationCount,
      conditionHeld: confirmation.held,
      primaryTrend,
      volatility,
      opportunity,
      confidence: conditionConfidence,
      aggregateSignal: aggregation.finalSignal,
      scores: aggregation.scores,
      buyScore,
      sellScore,
      holdScore,
      winningScore,
      winningDirectionScore: winningScore,
      signedNetScore,
      secondBestScore,
      scoreEdge,
      winningDirectionEdge: scoreEdge,
      winningDirection: aggregation.winningDirection,
      directionalEdge: scoreEdge,
      activeFamilyCount: aggregation.activeFamilyCount,
      abstentionRate: aggregation.abstentionRate,
      normalizedNetScore,
      tradeAllowed,
      tradeBlockers,
      activeStrategyCount: aggregation.activeStrategyCount,
      selectedStrategyCount: selectedStrategies.length,
      features: features.display,
      selectedStrategies,
      skippedStrategies,
      reasons: [
        `${primaryTrend} + ${volatility} + ${opportunity}`,
        `Raw composite regime ${rawCondition.rawRegime} from axes ${rawCondition.axes.direction}/${rawCondition.axes.volatility}/${rawCondition.axes.structure}/${rawCondition.axes.liquidity}/${rawCondition.axes.session}/${rawCondition.axes.eventRisk}`,
        confirmation.state.transitionReason,
        `${selectedStrategies.length} components selected, ${aggregation.activeStrategyCount} directional outputs across ${aggregation.activeFamilyCount} families before voting; aggregate winner ${aggregation.finalSignal}`,
        `Directional scores buy ${buyScore.toFixed(2)}, sell ${sellScore.toFixed(2)}; abstention ${holdScore.toFixed(2)}; winning direction edge ${scoreEdge.toFixed(2)}`,
      ],
      noTradeReasons,
    };
  const mlSnapshot = buildRegimeMlSnapshot(result, input);
  result.ml = mlSnapshot;
  result.effectiveSettings = resolveEffectiveRegimeSettings({
    market,
    result,
    settings: input.settings,
    baseSettingsVersion: input.baseSettingsVersion,
  });
  result.tradeBlockers = [...result.tradeBlockers, ...effectiveProfileBlockers(result)];
  result.tradeAllowed = result.tradeBlockers.length === 0;
  result.signal = result.tradeAllowed && (result.signal === "Buy" || result.signal === "Sell") ? result.signal : result.signal === "No-trade" ? "No-trade" : "Hold";
  result.decisionSnapshot = buildRegimeDecisionSnapshot(result, mlSnapshot.features, mlSnapshot.prediction, {
    symbol: input.marketData.symbol,
    settingsVersion: input.baseSettingsVersion,
    baseSettings: (input.settings ?? {}) as Record<string, unknown>,
    modelVersion: input.mlArtifact?.model_version ?? null,
  });
  if (mlSnapshot.appliedEffect === "blocked_transition" || mlSnapshot.appliedEffect === "reduced_confidence") {
    result.tradeBlockers = [...result.tradeBlockers, ...mlSnapshot.reasonCodes.filter((reason) => reason.startsWith("regime.ml.confirm_only"))];
    result.tradeAllowed = result.tradeBlockers.length === 0;
    result.signal = result.tradeAllowed && (result.signal === "Buy" || result.signal === "Sell") ? result.signal : "Hold";
    result.decisionSnapshot = buildRegimeDecisionSnapshot(result, mlSnapshot.features, mlSnapshot.prediction, {
      symbol: input.marketData.symbol,
      settingsVersion: input.baseSettingsVersion,
      baseSettings: (input.settings ?? {}) as Record<string, unknown>,
      modelVersion: input.mlArtifact?.model_version ?? null,
    });
  }
  return {
    result,
    hysteresis: confirmation.hysteresis,
  };
}

function effectiveProfileBlockers(result: RegimeSelectionResult): string[] {
  const effective = result.effectiveSettings;
  if (!effective) {
    return [];
  }
  const blockers: string[] = [];
  if (!effective.newEntriesAllowed) {
    blockers.push(`Effective profile ${effective.profileId} blocks new entries`);
  }
  if (result.winningScore < effective.effectiveMinimumWinningScore) {
    blockers.push(`Effective winning score ${probability(result.winningScore)} is below ${probability(effective.effectiveMinimumWinningScore)}`);
  }
  if (result.directionalEdge < effective.effectiveMinimumDirectionalEdge) {
    blockers.push(`Effective directional edge ${probability(result.directionalEdge)} is below ${probability(effective.effectiveMinimumDirectionalEdge)}`);
  }
  if (result.confidence < effective.effectiveMinimumRegimeConfidence) {
    blockers.push(`Effective regime confidence ${probability(result.confidence)} is below ${probability(effective.effectiveMinimumRegimeConfidence)}`);
  }
  if (effective.effectiveMaximumTrades <= 0) {
    blockers.push(`Effective profile ${effective.profileId} allows zero new trades`);
  }
  return blockers;
}

function buildRegimeMlSnapshot(result: RegimeSelectionResult, input: RegimeDecisionInput): RegimeMlSnapshot {
  const mode = resolveRegimeMlMode(input);
  if (mode === "off") {
    const prediction = predictRegimeMl(null, null, mode);
    return {
      mode,
      features: null,
      prediction,
      appliedEffect: "none",
      reasonCodes: ["regime.ml.off"],
    };
  }
  const features = buildRegimeMlFeatures(result);
  const artifactLoad = loadRegimeMlArtifact(input.mlArtifact, features, features.decisionTimestamp);
  const prediction = predictRegimeMl(features, artifactLoad.artifact, mode);
  const confirmOnlyEffect = mode === "confirm_only" || mode === "active" ? conservativeMlEffect(result, prediction) : null;
  return {
    mode,
    features,
    prediction: {
      ...prediction,
      reasonCodes: [...artifactLoad.reasonCodes, ...prediction.reasonCodes],
    },
    appliedEffect: confirmOnlyEffect?.effect ?? (mode === "shadow" ? "shadow_only" : "none"),
    reasonCodes: confirmOnlyEffect?.reasonCodes ?? (mode === "shadow" ? ["regime.ml.shadow_no_decision_change"] : artifactLoad.reasonCodes),
  };
}

function resolveRegimeMlMode(input: RegimeDecisionInput): RegimeMlMode {
  if (input.liveTrading) {
    return input.mlMode ?? input.settings?.mlMode ?? "off";
  }
  return input.mlMode ?? input.settings?.mlMode ?? "shadow";
}

function conservativeMlEffect(result: RegimeSelectionResult, prediction: RegimeMlSnapshot["prediction"]): { effect: RegimeMlSnapshot["appliedEffect"]; reasonCodes: string[] } | null {
  if (!prediction.enabled) {
    return null;
  }
  if (prediction.transitionProbability !== null && prediction.transitionProbability >= 0.7) {
    return {
      effect: "blocked_transition",
      reasonCodes: ["regime.ml.confirm_only.transition_probability_block"],
    };
  }
  if (
    prediction.deterministicStabilityConfidence !== null &&
    prediction.deterministicStabilityConfidence < 0.45 &&
    (result.signal === "Buy" || result.signal === "Sell")
  ) {
    return {
      effect: "reduced_confidence",
      reasonCodes: ["regime.ml.confirm_only.low_rule_stability_block"],
    };
  }
  return null;
}

export function buildRegimeMarketContext(
  marketData: MarketDataSnapshot & { regimeContextFeeds?: RegimeContextFeedInput },
  settings?: { slippagePerShare?: number; minimumOneMinuteVolume?: number; maxSpreadPercent?: number },
): RegimeMarketContext | null {
  const sorted = marketData.primaryCandles.slice().sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime());
  const regularCandles = sorted.filter((candle) => isRegularSession(candle.timestamp));
  const latest = regularCandles.at(-1);
  if (!latest) {
    return null;
  }
  const latestDay = easternDateString(latest.timestamp);
  const sessionCandles = regularCandles.filter((candle) => easternDateString(candle.timestamp) === latestDay);
  if (sessionCandles.length < 5) {
    return null;
  }

  const allCandles = (marketData.allCandles?.length ? marketData.allCandles : sorted).slice().sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime());
  const oneMinuteCandles = (marketData.oneMinuteCandles?.length ? marketData.oneMinuteCandles : allCandles).slice();
  const fiveMinuteCandles = marketData.fiveMinuteCandles?.length ? marketData.fiveMinuteCandles.slice() : aggregateCandlesToFiveMinute(sessionCandles);
  const allBeforeSession = allCandles.filter((candle) => new Date(candle.timestamp).getTime() < new Date(sessionCandles[0].timestamp).getTime());
  const priorClose = allBeforeSession.filter((candle) => isRegularSession(candle.timestamp)).at(-1)?.close ?? null;
  const premarketCandles = allCandles.filter((candle) => easternDateString(candle.timestamp) === latestDay && isPremarketSession(candle.timestamp));
  const closes = sessionCandles.map((candle) => candle.close);
  const vwap = sessionVwapValue(sessionCandles);
  const previousVwap = sessionCandles.length > 1 ? sessionVwapValue(sessionCandles.slice(0, -1)) : vwap;
  const priorRange = sessionCandles.slice(-21, -1);
  const openingRange = openingRangeValues(sessionCandles, Math.min(15, sessionCandles.length));
  const priorHigh = priorRange.length ? Math.max(...priorRange.map((candle) => candle.high)) : latest.high;
  const priorLow = priorRange.length ? Math.min(...priorRange.map((candle) => candle.low)) : latest.low;
  const averageVolume = simpleMovingAverage(sessionCandles.map((candle) => candle.volume), Math.min(20, sessionCandles.length)) ?? latest.volume;
  const volume = volumeContext(latest, averageVolume, vwap, openingRange, priorHigh, priorLow);
  const structure = marketStructureContext(sessionCandles, vwap);
  const atr = atrContext(sessionCandles, fiveMinuteCandles, latest.close);
  const contextFeeds = normalizeRegimeContextFeeds(marketData.regimeContextFeeds, latest.timestamp);
  const spreadLiquidity = spreadLiquidityWithQuoteFeed(spreadLiquidityContext(latest, volume, settings), contextFeeds.quoteFreshness.spreadPercent);

  return {
    candles: sessionCandles,
    allCandles,
    oneMinuteCandles,
    fiveMinuteCandles,
    closes,
    latest,
    priorClose,
    dayOpen: sessionCandles[0].open,
    premarketHigh: premarketCandles.length ? Math.max(...premarketCandles.map((candle) => candle.high)) : null,
    premarketLow: premarketCandles.length ? Math.min(...premarketCandles.map((candle) => candle.low)) : null,
    vwap,
    vwapSlope: vwap && previousVwap ? (vwap - previousVwap) / vwap : 0,
    openingRange,
    priorHigh,
    priorLow,
    averageVolume,
    sma20: simpleMovingAverage(closes, 20),
    sma50: simpleMovingAverage(closes, 50),
    rsi: relativeStrengthIndex(closes, 14),
    macd: macdValues(closes),
    atr,
    bands: bollingerBands(closes, 20, 2),
    adx: averageDirectionalIndex(sessionCandles, Math.min(14, sessionCandles.length - 1)),
    volume,
    spreadLiquidity,
    timeOfDay: timeOfDayContext(latest.timestamp),
    structure,
    contextFeeds,
  };
}

export function emptyRegimeSelectionResult(reason: string): RegimeSelectionResult {
  return {
    signal: "No-trade",
    aggregateSignal: "hold",
    scores: { buy: 0, sell: 0, hold: 1 },
    rawCondition: "No-trade",
    confirmedCondition: "No-trade",
    confirmationCount: 0,
    conditionHeld: false,
    primaryTrend: "Sideways / range-bound",
    volatility: "Normal volatility",
    opportunity: "No-trade",
    confidence: 0,
    buyScore: 0,
    sellScore: 0,
    holdScore: 1,
    winningScore: 0,
    winningDirectionScore: 0,
    signedNetScore: 0,
    secondBestScore: 0,
    scoreEdge: 1,
    winningDirectionEdge: 1,
    winningDirection: "hold",
    directionalEdge: 0,
    activeFamilyCount: 0,
    abstentionRate: 1,
    normalizedNetScore: 0,
    tradeAllowed: false,
    tradeBlockers: [reason],
    activeStrategyCount: 0,
    selectedStrategyCount: 0,
    features: [{ name: "Market data", value: reason, status: "na" }],
    selectedStrategies: [],
    skippedStrategies: regimeSelectionStrategies.map((strategy) => ({ name: strategy.name, reason })),
    reasons: [reason],
    noTradeReasons: [reason],
  };
}

export type ResolvedRegimeDecision = {
  signal: "Buy" | "Sell" | "Hold";
  decisionLabel: "Buy" | "Sell" | "Hold";
  signedNetScore: number;
  winningScore: number;
  winningEdge: number;
  tradeAllowed: boolean;
  failedGates: string[];
};

export function resolveRegimeDecision(result: RegimeSelectionResult): ResolvedRegimeDecision {
  const signal =
    result.tradeAllowed && (result.signal === "Buy" || result.signal === "Sell")
      ? result.signal
      : "Hold";
  const scores = result.scores;
  return {
    signal,
    decisionLabel: signal,
    signedNetScore: signedRegimeNetScore(scores),
    winningScore: signal === "Buy" ? scores.buy : signal === "Sell" ? scores.sell : 0,
    winningEdge: result.scoreEdge,
    tradeAllowed: result.tradeAllowed,
    failedGates: result.tradeBlockers,
  };
}

export function regimeTradeBlockers(
  aggregation: RegimeAggregationResult,
  conditionConfidence: number,
  opportunity: string,
  conditionHeld: boolean,
  settings: RegimeTradingSettings | undefined,
  confirmedRegime: MarketRegimeId,
): string[] {
  const gates = regimeDecisionGateSettings(settings);
  const blockers: string[] = [];
  if (conditionHeld) {
    blockers.push("Market condition switch is not confirmed yet");
  }
  if (aggregation.winningDirection !== "buy" && aggregation.winningDirection !== "sell") {
    blockers.push("Winning direction is missing");
  }
  if (aggregation.winningScore < gates.minimumWinningScore) {
    blockers.push(`Winning direction score ${probability(aggregation.winningScore)} is below ${probability(gates.minimumWinningScore)}`);
  }
  if (aggregation.directionalEdge < gates.minimumDirectionalEdge) {
    blockers.push(`Winning direction edge ${probability(aggregation.directionalEdge)} is below ${probability(gates.minimumDirectionalEdge)}`);
  }
  if (conditionConfidence < gates.minimumRegimeConfidence) {
    blockers.push(`Market condition confidence ${probability(conditionConfidence)} is below ${probability(gates.minimumRegimeConfidence)}`);
  }
  if (aggregation.activeStrategyCount < gates.minimumActiveStrategies) {
    blockers.push(`Strategy coverage ${aggregation.activeStrategyCount} active is below ${gates.minimumActiveStrategies}`);
  }
  if (aggregation.activeFamilyCount < gates.minimumIndependentFamilies) {
    blockers.push(`Independent family coverage ${aggregation.activeFamilyCount} is below ${gates.minimumIndependentFamilies}`);
  }
  if (aggregation.abstentionRate > gates.maximumAbstentionRate) {
    blockers.push(`Abstention rate ${probability(aggregation.abstentionRate)} is above ${probability(gates.maximumAbstentionRate)}`);
  }
  if (opportunity === "No-trade") {
    blockers.push("Opportunity state is No-trade");
  }
  if (confirmedRegime === "extreme_volatility_no_trade" || confirmedRegime === "event_risk" || confirmedRegime === "liquidity_stress") {
    blockers.push(`Dynamic Regime profile prohibits new entries for ${confirmedRegime}`);
  }
  return blockers;
}

function regimeDecisionGateSettings(settings?: RegimeTradingSettings) {
  return {
    minimumWinningScore: settings?.minimumWinningScore ?? settings?.minimumBuyScore ?? REGIME_MIN_WINNING_SCORE,
    minimumDirectionalEdge: settings?.minimumDirectionalEdge ?? settings?.minimumSignalEdge ?? REGIME_MIN_WINNING_EDGE,
    minimumRegimeConfidence: settings?.minimumRegimeConfidence ?? REGIME_MIN_CONDITION_CONFIDENCE,
    minimumActiveStrategies: Math.max(1, Math.floor(settings?.minimumActiveStrategies ?? 3)),
    minimumIndependentFamilies: Math.max(1, Math.floor(settings?.minimumIndependentFamilies ?? REGIME_MIN_INDEPENDENT_FAMILIES)),
    maximumAbstentionRate: Math.max(0, Math.min(1, settings?.maximumAbstentionRate ?? REGIME_MAX_ABSTENTION_RATE)),
  };
}

export function winningDirectionScore(signal: RegimeStrategySignal, scores: RegimeSelectionScores): number {
  if (signal === "buy") {
    return scores.buy;
  }
  if (signal === "sell") {
    return scores.sell;
  }
  return 0;
}

export function signedRegimeNetScore(scores: RegimeSelectionScores): number {
  return roundNumber(scores.buy - scores.sell, 4);
}

export function secondBestScoreForDirection(signal: RegimeStrategySignal, scores: RegimeSelectionScores): number {
  if (signal === "buy") {
    return Math.max(scores.sell, scores.hold);
  }
  if (signal === "sell") {
    return Math.max(scores.buy, scores.hold);
  }
  return Math.max(scores.buy, scores.sell);
}

function regimeContractSignal(signal: "Buy" | "Sell" | "Hold"): RegimeStrategySignal {
  return signal === "Buy" ? "buy" : signal === "Sell" ? "sell" : "hold";
}

function regimeSignalDirection(signal: RegimeStrategySignal): -1 | 0 | 1 {
  return signal === "buy" ? 1 : signal === "sell" ? -1 : 0;
}

function atrContext(oneMinuteCandles: MarketCandle[], fiveMinuteCandles: MarketCandle[], latestPrice: number) {
  const atr1m = averageTrueRange(oneMinuteCandles, Math.min(14, oneMinuteCandles.length - 1));
  const atr5m = averageTrueRange(fiveMinuteCandles, Math.min(14, fiveMinuteCandles.length - 1));
  const atrSeries = rollingAtrSeries(oneMinuteCandles, 14).slice(-30);
  const recentAverageAtr = atrSeries.length ? atrSeries.reduce((sum, value) => sum + value, 0) / atrSeries.length : null;
  const primaryAtr = atr1m ?? (atr5m !== null ? atr5m / 5 : 0);
  const atrPercent = latestPrice ? primaryAtr / latestPrice : 0;
  const relativeAtr = recentAverageAtr ? primaryAtr / recentAverageAtr : null;
  const regime = atrRegime(atrPercent, relativeAtr);
  return {
    atr1m,
    atr5m,
    atrPercent,
    recentAverageAtr,
    relativeAtr,
    regime,
    positionSizeMultiplier: regime === "extreme" ? 0 : regime === "high" ? 0.6 : regime === "too_low" ? 0.85 : 1,
    thresholdAdd: regime === "extreme" ? 0.15 : regime === "high" ? 0.06 : regime === "too_low" ? 0.08 : 0,
    stopDistance: Math.max(primaryAtr * 2, latestPrice * 0.0005),
  };
}

function atrRegime(atrPercent: number, relativeAtr: number | null): "too_low" | "normal" | "high" | "extreme" {
  if (atrPercent <= 0 || atrPercent < 0.00035 || (relativeAtr !== null && relativeAtr < 0.55)) {
    return "too_low";
  }
  if (atrPercent > 0.0045 || (relativeAtr !== null && relativeAtr > 2.2)) {
    return "extreme";
  }
  if (atrPercent > 0.0025 || (relativeAtr !== null && relativeAtr > 1.45)) {
    return "high";
  }
  return "normal";
}

function spreadLiquidityContext(latest: MarketCandle, volume: ReturnType<typeof volumeContext>, settings?: { slippagePerShare?: number; minimumOneMinuteVolume?: number; maxSpreadPercent?: number }) {
  const spreadPercent = latest.close ? ((settings?.slippagePerShare ?? 0.02) * 2) / latest.close : 0;
  const maxSpreadPercent = (settings?.maxSpreadPercent ?? 0.03) / 100;
  const minimumOneMinuteVolume = settings?.minimumOneMinuteVolume ?? 0;
  const volumeTooLow = minimumOneMinuteVolume > 0 && latest.volume < minimumOneMinuteVolume;
  return {
    spreadPercent,
    maxSpreadPercent,
    spreadTooWide: maxSpreadPercent > 0 && spreadPercent > maxSpreadPercent,
    volumeTooLow,
    minimumOneMinuteVolume,
    relativeVolume: volume.relativeVolume,
  };
}

function spreadLiquidityWithQuoteFeed(base: ReturnType<typeof spreadLiquidityContext>, quoteSpreadPercent: number | null) {
  if (quoteSpreadPercent === null || !Number.isFinite(quoteSpreadPercent)) {
    return base;
  }
  return {
    ...base,
    spreadPercent: quoteSpreadPercent,
    spreadTooWide: base.maxSpreadPercent > 0 && quoteSpreadPercent > base.maxSpreadPercent,
  };
}

function timeOfDayContext(timestamp: string) {
  const minutes = easternMinutes(timestamp);
  const label = sessionLabelForMinutes(minutes);
  const beforeFirstFive = minutes < 9 * 60 + 35;
  const afterCutoff = minutes >= 15 * 60 + 30;
  const weightMultiplier = beforeFirstFive ? 0.75 : label === "Opening drive" ? 1.05 : label === "Midday" ? 0.85 : label === "Closing window" ? 0.9 : 1;
  return {
    minutes,
    label,
    weightMultiplier,
    newTradesAllowed: !beforeFirstFive && !afterCutoff,
  };
}

function volumeContext(latest: MarketCandle, averageVolume: number, vwap: number, openingRange: { high: number; low: number }, priorHigh: number, priorLow: number) {
  const relativeVolume = averageVolume ? latest.volume / averageVolume : 1;
  const range = Math.max(latest.high - latest.low, 0);
  const rangePercent = latest.close ? range / latest.close : 0;
  const bullishCandle = latest.close > latest.open;
  const bearishCandle = latest.close < latest.open;
  const breaksResistance = latest.close > Math.max(openingRange.high, priorHigh);
  const breaksSupport = latest.close < Math.min(openingRange.low, priorLow);
  const holdsKeyLevel = latest.low <= vwap * 1.001 && latest.close > vwap;
  const rejectsResistance = latest.high >= Math.max(vwap, priorHigh) * 0.999 && latest.close < Math.max(vwap, priorHigh);
  return {
    relativeVolume,
    volumeSpike: relativeVolume >= 1.5,
    weakVolume: relativeVolume < 0.8,
    smallCandle: rangePercent < 0.00045,
    bullishCandle,
    bearishCandle,
    rangePercent,
    spreadAcceptable: rangePercent >= 0.00045 && rangePercent <= 0.006,
    holdsKeyLevel,
    breaksResistance,
    breaksSupport,
    rejectsResistance,
  };
}

function probability(value: number): string {
  return `${roundNumber(value * 100, 1)}%`;
}
