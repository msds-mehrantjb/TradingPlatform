import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  buildRegimeOrderIntent,
  buildRegimeBrokerAttribution,
  buildRegimeExecutionPipeline,
  buildRegimeTargetOrder,
  adaptRegimeBrokerReconciliation,
  buildRegimeMarketContext,
  buildRegimeMarketSnapshot,
  buildRawRegimeCondition,
  activeDirectionalRegimeOutputs,
  applyRegimeFamilyContributionCap,
  calculateRegimeDecision,
  cappedRegimeStrategyContribution,
  buildDirectionalStrategyResult,
  buildRegimeFeatureSnapshot,
  buildOfflineRegimeLabel,
  buildRegimeMlFeatures,
  baseRegimeSettingsFromTradingSettings,
  boundedRegimeEffectiveSettings,
  calculateRegimePositionSize,
  confirmedRegimeCondition,
  createConfirmedRegimeState,
  compositeRegimeIdFromAxes,
  contextMultiplierForSignal,
  evaluateRegimeMlPromotionPolicy,
  evaluateRegimeDwellPolicy,
  evaluateRegimeStrategyDefinition,
  generateRegimeOrderIntentIdempotencyKey,
  loadRegimeMlArtifact,
  manageRegimeOpenPosition,
  minutesBetween,
  REGIME_ALGORITHM_ID,
  REGIME_ALGORITHM_VERSION,
  REGIME_ALLOWED_SHARED_COMPONENTS,
  REGIME_BACKTEST_FILE_INVENTORY,
  REGIME_BACKTEST_OWNED_CAPABILITIES,
  REGIME_COMPATIBILITY_MATRIX,
  REGIME_COMPOSITE_REGIME_IDS,
  REGIME_DIAGNOSTICS_FILE_INVENTORY,
  REGIME_IDENTITY_CONTRACT_FILES,
  REGIME_LEGACY_ALIASES,
  REGIME_ML_FILE_INVENTORY,
  REGIME_ML_INITIAL_MODE,
  REGIME_ML_SHADOW_FORBIDDEN_ACTIONS,
  REGIME_NEVER_SHARED_COMPONENTS,
  REGIME_OPPORTUNITY_TAGS,
  REGIME_FRONTEND_ROLLOUT_PHASES,
  REGIME_PROFILE_MATRIX,
  REGIME_PROFILE_VERSION,
  REGIME_ROLLOUT_FILE_INVENTORY,
  REGIME_CONFIRMATION_MODULE_INVENTORY,
  REGIME_CONTEXT_MODULE_INVENTORY,
  REGIME_DIRECTIONAL_STRATEGY_INVENTORY,
  REGIME_EXECUTION_PIPELINE,
  REGIME_SAFETY_GATE_INVENTORY,
  REGIME_SETTINGS_VERSION,
  REGIME_STRATEGY_ALIAS_INVENTORY,
  REGIME_STRATEGY_CATALOG_VERSION,
  REGIME_TOTAL_STRATEGY_DEFINITION_COUNT,
  canonicalRegimeRoutingStrategyId,
  permittedDirectionForRegime,
  regimeDecisionGateSettings,
  regimeBacktestInventoryStatus,
  regimeDiagnosticsInventoryStatus,
  regimeSharedBoundaryStatus,
  buildRegimeDiagnosticsBundle,
  compareRegimeShadowDecisions,
  evaluateRegimeFrontendRolloutPolicy,
  evaluateRegimePaperStability,
  regimeFrontendRollbackPolicy,
  regimeMlInventoryStatus,
  regimeLiquidityCap,
  regimePositionAndBuyingPowerCaps,
  regimeRiskBudget,
  regimeStopDistance,
  regimeTargetDistance,
  regimeTradeBlockers,
  runRegimeBacktest,
  simulateRegimeGlobalGate,
  simulateRegimeNextBarEntry,
  regimeSelectionStrategies,
  representedRegimeFamilies,
  resolveEffectiveRegimeSettings,
  routeRegimeStrategies,
  resolveRegimeHysteresisSettings,
  recoverRegimeHysteresisState,
  signalStrengthMultiplierForWinningStrength,
  signedRegimeNetScore,
  summarizeRegimeTradeHistory,
  validateDirectionalStrategyResult,
  validateEffectiveRegimeProfile,
  validateRegimeOrderIntent,
  validateRegimeTargetOrder,
  validateRegimeMlArtifact,
  validateRegimeHysteresisSettings,
  validateRegimeIdentityContracts,
  validateRegimeTradingSettings,
  winningDirectionScore,
} from "../src/algorithms/regime/index.ts";
import { aggregateRegimeStrategyScores } from "../src/algorithms/regime/family-aggregation.ts";
import { REGIME_MAX_FAMILY_CONTRIBUTION } from "../src/algorithms/regime/config.ts";
import { renderV2DecisionPanel, type V2DecisionPanelState } from "../src/components/V2DecisionPanel.ts";
import type { RegimeMlArtifact } from "../src/algorithms/regime/ml/types.ts";
import type { DirectionalStrategyResult, RegimeAxes, RegimeSelectedStrategy, RegimeSelectionResult, RegimeStrategyDefinition } from "../src/algorithms/regime/types.ts";
import type { MarketCandle } from "../src/trading/shared/market-data-types.ts";

test("renders every canonical Voting Ensemble V2 presentation section", () => {
  const html = renderV2DecisionPanel(readyState());

  for (const label of [
    "Directional strategies",
    "Context",
    "Regime and safety",
    "Ensemble",
    "ML",
    "Dynamic policy",
    "Global gates",
  ]) {
    assert.match(html, new RegExp(label));
  }
});

test("shows missing canonical strategy and context modules instead of hiding them", () => {
  const state = readyState();
  state.decision!.strategyOutputs = state.decision!.strategyOutputs.slice(0, 1);
  state.decision!.contextOutputs = [];

  const html = renderV2DecisionPanel(state);

  assert.match(html, /Backend did not return this canonical directional strategy/);
  assert.match(html, /Market Breadth Momentum|Breadth/);
  assert.match(html, /Missing/);
  assert.doesNotMatch(html, /10 active strategies/i);
});

test("distinguishes hard blockers cautions information and not evaluated gates", () => {
  const html = renderV2DecisionPanel(readyState());
  const emptyGateHtml = renderV2DecisionPanel({
    ...readyState(),
    decision: { ...readyState().decision!, gateResults: [] },
  });

  assert.match(html, /data-status="hard-blocker"/);
  assert.match(html, /data-status="caution"/);
  assert.match(html, /data-status="information"/);
  assert.match(emptyGateHtml, /Not evaluated/);
  assert.match(emptyGateHtml, /data-status="not-evaluated"/);
});

test("Regime core runs from supplied candles without DOM access", () => {
  const candles = deterministicRegimeCandles();
  const output = calculateRegimeDecision({
    marketData: {
      symbol: "SPY",
      primaryCandles: candles,
      allCandles: candles,
      oneMinuteCandles: candles,
    },
  });

  assert.equal(output.result.primaryTrend, "Strong uptrend");
  assert.equal(output.result.volatility, "Low volatility");
  assert.equal(output.result.selectedStrategyCount > 0, true);
  assert.equal(typeof output.result.buyScore, "number");
});

test("Regime daily backtest production path calls backend runtime only", () => {
  const mainSource = readFileSync(fileURLToPath(new URL("../src/main.ts", import.meta.url)), "utf-8");
  const apiSource = readFileSync(fileURLToPath(new URL("../src/features/regime/api.ts", import.meta.url)), "utf-8");

  assert.match(mainSource, /runRegimeBacktestOnBackend/);
  assert.match(apiSource, /\/api\/regime\/backtests\/run/);
  assert.doesNotMatch(mainSource, /\brunRegimeBacktest\(/);
});

test("Regime order intents preserve allowed Buy and allowed Sell directions", () => {
  const buyIntent = buildRegimeOrderIntent(regimeResult({ signal: "Buy", tradeAllowed: true }), "SPY", 12, {
    currentPosition: 0,
    ...pricedIntentOptions(),
  });
  const sellIntent = buildRegimeOrderIntent(regimeResult({ signal: "Sell", tradeAllowed: true }), "SPY", 5, {
    currentPosition: 10,
    ...pricedIntentOptions(),
  });

  assert.equal(buyIntent?.signal, "Buy");
  assert.equal(buyIntent?.positionEffect, "enter_long");
  assert.equal(buyIntent?.requestedQuantity, 12);
  assert.equal(sellIntent?.signal, "Sell");
  assert.equal(sellIntent?.positionEffect, "exit_long");
  assert.equal(sellIntent?.requestedQuantity, 5);
});

test("Regime order intents are not created for blocked or Hold signals", () => {
  const blockedBuy = buildRegimeOrderIntent(regimeResult({ signal: "Buy", tradeAllowed: false }), "SPY", 10, {
    currentPosition: 0,
    ...pricedIntentOptions(),
  });
  const blockedSell = buildRegimeOrderIntent(regimeResult({ signal: "Sell", tradeAllowed: false }), "SPY", 10, {
    currentPosition: 10,
    ...pricedIntentOptions(),
  });
  const hold = buildRegimeOrderIntent(regimeResult({ signal: "Hold", tradeAllowed: true }), "SPY", 10, {
    currentPosition: 0,
    ...pricedIntentOptions(),
  });

  assert.equal(blockedBuy, null);
  assert.equal(blockedSell, null);
  assert.equal(hold, null);
});

test("Regime never converts Sell signals into Buy orders", () => {
  const intent = buildRegimeOrderIntent(regimeResult({ signal: "Sell", tradeAllowed: true }), "SPY", 10, {
    currentPosition: 0,
    ...pricedIntentOptions(),
  });

  assert.equal(intent, null);
});

test("Regime signed net score is positive for Buy dominance and negative for Sell dominance", () => {
  assert.equal(signedRegimeNetScore({ buy: 0.72, sell: 0.18, hold: 0.1 }), 0.54);
  assert.equal(signedRegimeNetScore({ buy: 0.18, sell: 0.72, hold: 0.1 }), -0.54);
  assert.equal(winningDirectionScore("buy", { buy: 0.72, sell: 0.18, hold: 0.1 }), 0.72);
  assert.equal(winningDirectionScore("sell", { buy: 0.18, sell: 0.72, hold: 0.1 }), 0.72);
});

test("Regime bearish signals exit longs but require explicit short enablement from flat", () => {
  const exitLong = buildRegimeOrderIntent(regimeResult({ signal: "Sell", tradeAllowed: true }), "SPY", 4, {
    currentPosition: 10,
    ...pricedIntentOptions(),
  });
  const flatShortDisabled = buildRegimeOrderIntent(regimeResult({ signal: "Sell", tradeAllowed: true }), "SPY", 4, {
    currentPosition: 0,
    ...pricedIntentOptions(),
  });
  const flatShortEnabled = buildRegimeOrderIntent(regimeResult({ signal: "Sell", tradeAllowed: true }), "SPY", 4, {
    currentPosition: 0,
    ...pricedIntentOptions(),
    shortTradingEnabled: true,
    accountShortPermission: true,
    assetShortable: true,
    borrowAvailable: true,
    buyingPowerAvailable: true,
    shortSaleRestrictionActive: false,
  });

  assert.equal(exitLong?.positionEffect, "exit_long");
  assert.equal(exitLong?.signal, "Sell");
  assert.equal(flatShortDisabled, null);
  assert.equal(flatShortEnabled?.positionEffect, "enter_short");
  assert.equal(flatShortEnabled?.signal, "Sell");
  assert.equal(flatShortEnabled?.requestedQuantity, 4);
});

test("Regime order intents are immutable and use deterministic idempotency keys", () => {
  const options = pricedIntentOptions({
    marketDataTimestamp: "2026-01-05T15:30:00.000Z",
    baseSettingsVersion: "regime_base_settings_v3",
    profileVersion: "regime_profile_matrix_v2",
    effectiveProfileId: "strong_uptrend:regime_profile_matrix_v2",
    strategyIds: ["moving_average_trend"],
    familyScores: { trend: 0.4 },
  });
  const first = buildRegimeOrderIntent(regimeResult({ signal: "Buy", tradeAllowed: true }), "spy", 12, options);
  const second = buildRegimeOrderIntent(regimeResult({ signal: "Buy", tradeAllowed: true }), "SPY", 12, options);
  const expectedKey = generateRegimeOrderIntentIdempotencyKey({
    symbol: "SPY",
    decisionCandle: "2026-01-05T15:30:00.000Z",
    positionEffect: "enter_long",
    settingsVersion: "regime_base_settings_v3",
    profileVersion: "regime_profile_matrix_v2",
  });

  assert.equal(first?.decisionId, expectedKey);
  assert.equal(second?.decisionId, expectedKey);
  assert.equal(first?.algorithmId, "regime");
  assert.equal(first?.effectiveProfileId, "strong_uptrend:regime_profile_matrix_v2");
  assert.equal(Object.isFrozen(first), true);
  assert.equal(Object.isFrozen(first?.strategyIds), true);
  assert.equal(Object.isFrozen(first?.familyScores), true);
  assert.equal(Object.isFrozen(first?.reasons), true);
});

test("Regime owns dedicated order-intent and validation inventory", () => {
  assert.deepEqual(directoryFiles("execution"), [
    "broker-attribution.ts",
    "execution-pipeline.ts",
    "idempotency.ts",
    "order-intent.ts",
    "order-validation.ts",
    "reconciliation-adapter.ts",
  ]);
  assert.deepEqual(REGIME_EXECUTION_PIPELINE, [
    "decision_result",
    "dynamic_profile",
    "position_sizing",
    "order_intent",
    "order_validation",
    "broker_attribution",
    "reconciliation_adapter",
  ]);
  const intent = buildRegimeOrderIntent(regimeResult({ signal: "Buy", tradeAllowed: true }), "SPY", 12, pricedIntentOptions());
  assert.ok(intent);
  assert.deepEqual(validateRegimeOrderIntent(intent), []);
  assert.deepEqual(buildRegimeBrokerAttribution(intent), {
    algorithmId: "regime",
    algorithmVersion: REGIME_ALGORITHM_VERSION,
    decisionId: intent.decisionId,
    symbol: "SPY",
    side: "Buy",
    positionEffect: "enter_long",
    requestedQuantity: 12,
  });
  assert.deepEqual(adaptRegimeBrokerReconciliation(intent, { brokerOrderId: "BRK-1", status: "accepted" }), {
    attribution: buildRegimeBrokerAttribution(intent),
    brokerOrderId: "BRK-1",
    brokerStatus: "accepted",
    reconciledAt: intent.generatedAt,
  });
});

test("Regime target orders are built without WCA confidence adaptation", () => {
  const candles = deterministicRegimeCandles();
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: candles,
    allCandles: candles,
    oneMinuteCandles: candles,
  });
  const decision = calculateRegimeDecision({
    marketData: {
      symbol: "SPY",
      primaryCandles: candles,
      allCandles: candles,
      oneMinuteCandles: candles,
    },
  }).result;
  const target = buildRegimeTargetOrder(decision, market, "SPY");
  const pipeline = buildRegimeExecutionPipeline(decision, market, "SPY", regimeTradingSettingsFixture());

  assert.equal(target.symbol, "SPY");
  assert.equal(target.signalDirection === "Buy" || target.signalDirection === "Sell" || target.signalDirection === "Hold", true);
  assert.equal(typeof target.sizing.finalQuantity, "number");
  assert.equal(pipeline.modules, REGIME_EXECUTION_PIPELINE);
  assert.deepEqual(validateRegimeTargetOrder(target), target.eligible ? [] : target.failedGates);
});

test("Regime module contains no WCA storage keys or confidence target-order adapter references", () => {
  const text = readRegimeModuleText();

  assert.doesNotMatch(text, /confidenceTargetOrderRecommendation/);
  assert.doesNotMatch(text, /weighted-confidence/);
  assert.doesNotMatch(text, /confidenceBacktestResult/);
});

test("Regime owns the dedicated algorithm identity and contract inventory", () => {
  const root = fileURLToPath(new URL("../src/algorithms/regime", import.meta.url));
  const topLevelFiles = new Set(readdirSync(root));
  const expectedFiles = ["index.ts", "types.ts", "versions.ts", "config.ts", "validation.ts"];

  assert.deepEqual(REGIME_IDENTITY_CONTRACT_FILES.map((entry) => entry.file), expectedFiles);
  for (const file of expectedFiles) {
    assert.equal(topLevelFiles.has(file), true, `${file} should exist in the Regime package root`);
  }

  assert.equal(REGIME_ALGORITHM_ID, "regime");
  assert.equal(REGIME_ALGORITHM_VERSION, "regime_algorithm_v2");
  assert.equal(REGIME_SETTINGS_VERSION, "regime_base_settings_v1");
  assert.equal(REGIME_STRATEGY_CATALOG_VERSION, "regime_strategy_catalog_v2");
  assert.equal(REGIME_PROFILE_VERSION, "regime_profile_matrix_v1");

  const contracts = validateRegimeIdentityContracts();
  assert.equal(contracts.valid, true);
  assert.equal(contracts.algorithmId, "regime");
});

test("Regime validation clamps configuration contracts at the package boundary", () => {
  const tradingSettings = validateRegimeTradingSettings({
    startingCapital: -1,
    minimumWinningScore: 2,
    minimumDirectionalEdge: -0.4,
    minimumActiveStrategies: 0,
    mlMode: "unsafe" as never,
  });
  const hysteresisSettings = validateRegimeHysteresisSettings({
    confirmationBars: 0,
    immediateConfidenceThreshold: 2,
    minimumDwellBars: -5,
  });

  assert.equal(tradingSettings.startingCapital, 0);
  assert.equal(tradingSettings.minimumWinningScore, 1);
  assert.equal(tradingSettings.minimumDirectionalEdge, 0);
  assert.equal(tradingSettings.minimumActiveStrategies, 1);
  assert.equal(tradingSettings.mlMode, "shadow");
  assert.equal(hysteresisSettings.confirmationBars, 1);
  assert.equal(hysteresisSettings.immediateConfidenceThreshold, 1);
  assert.equal(hysteresisSettings.minimumDwellBars, 0);
});

test("Regime owns immutable market and feature snapshot inventory", () => {
  const root = fileURLToPath(new URL("../src/algorithms/regime/market", import.meta.url));
  const marketFiles = new Set(readdirSync(root));
  const expectedFiles = [
    "market-snapshot.ts",
    "feature-snapshot.ts",
    "indicators.ts",
    "context-feeds.ts",
    "freshness.ts",
    "session-context.ts",
  ];
  for (const file of expectedFiles) {
    assert.equal(marketFiles.has(file), true, `${file} should exist in the Regime market boundary`);
  }

  const candles = deterministicRegimeCandles();
  const snapshot = buildRegimeMarketSnapshot({
    symbol: "SPY",
    primaryCandles: candles,
    oneMinuteCandles: candles,
    allCandles: candles,
    regimeContextFeeds: regimeContextFeedsFixture(candles.at(-1)!.timestamp),
  });
  const featureSnapshot = buildRegimeFeatureSnapshot(snapshot);

  assert.equal(snapshot.algorithmId, "regime");
  assert.equal(Object.isFrozen(snapshot), true);
  assert.equal(Object.isFrozen(snapshot.primarySymbolCandles), true);
  assert.equal(snapshot.contextFeeds.quoteFreshness.status, "fresh");
  assert.equal(snapshot.contextFeeds.qqqRelativeStrength.availability, "available");
  assert.equal(featureSnapshot?.algorithmId, "regime");
  assert.equal(featureSnapshot?.contextFeedEvidence.quoteFreshness, "fresh");
});

test("Regime context feeds make external classifier evidence operational", () => {
  const candles = deterministicRegimeCandles();
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: candles,
    oneMinuteCandles: candles,
    allCandles: candles,
    regimeContextFeeds: regimeContextFeedsFixture(candles.at(-1)!.timestamp),
  }, liquidRegimeSettings())!;
  const condition = buildRawRegimeCondition(market);

  for (const missing of [
    "spread quote freshness",
    "QQQ/IWM relative strength",
    "market breadth",
    "VIX state",
    "ES futures state",
    "scheduled event state",
  ]) {
    assert.equal(condition.missingInputs.includes(missing), false, `${missing} should be supplied by Regime context feeds`);
  }
  assert.equal(condition.evidence.quoteFreshness, "fresh");
  assert.equal(condition.evidence.qqqRelativeStrength, 0.2);
  assert.equal(condition.evidence.iwmRelativeStrength, -0.1);
  assert.equal(condition.evidence.marketBreadth, 1.4);
  assert.equal(condition.evidence.vixState, 14.8);
  assert.equal(condition.evidence.esFuturesState, 0.15);
  assert.equal(condition.evidence.scheduledEventState, "none");
});

test("Regime scheduled-event feeds classify operational event risk", () => {
  const candles = deterministicRegimeCandles();
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: candles,
    oneMinuteCandles: candles,
    allCandles: candles,
    regimeContextFeeds: {
      ...regimeContextFeedsFixture(candles.at(-1)!.timestamp),
      scheduledEconomicEvent: {
        state: "blackout",
        eventName: "FOMC",
        minutesUntil: 5,
        source: "fixture",
      },
    },
  }, liquidRegimeSettings())!;
  const condition = buildRawRegimeCondition(market);

  assert.equal(condition.axes.eventRisk, "blackout");
  assert.equal(condition.rawRegime, "event_risk");
  assert.equal(condition.noTradeReasons.some((reason) => reason.includes("Scheduled event blackout")), true);
});

test("Regime owns dedicated classification inventory and canonical regime IDs", () => {
  const root = fileURLToPath(new URL("../src/algorithms/regime/classification", import.meta.url));
  const classificationFiles = new Set(readdirSync(root));
  const expectedFiles = [
    "classifier.ts",
    "classification-axes.ts",
    "composite-regimes.ts",
    "evidence-builder.ts",
    "hysteresis.ts",
    "transition-policy.ts",
    "no-trade-classifier.ts",
  ];
  for (const file of expectedFiles) {
    assert.equal(classificationFiles.has(file), true, `${file} should exist in the Regime classification boundary`);
  }

  assert.deepEqual(REGIME_COMPOSITE_REGIME_IDS, [
    "strong_uptrend",
    "weak_uptrend",
    "strong_downtrend",
    "weak_downtrend",
    "range_bound",
    "sideways_range",
    "choppy_mixed",
    "opening_breakout",
    "intraday_expansion",
    "high_volatility_trend",
    "low_volatility_quiet",
    "failed_breakout_reversal",
    "gap_session",
    "event_risk",
    "liquidity_stress",
    "extreme_volatility_no_trade",
  ]);
  assert.deepEqual(REGIME_LEGACY_ALIASES, [
    "low_volatility",
    "normal_volatility",
    "high_volatility",
    "trend_continuation",
    "bullish_breakout",
    "bearish_breakout",
    "bullish_reversal_risk",
    "bearish_reversal_risk",
    "mean_reversion",
  ]);
  assert.equal(REGIME_OPPORTUNITY_TAGS.includes("mean_reversion"), true);
});

test("Regime MarketRegimeId excludes legacy opportunity aliases", () => {
  const typesText = readFileSync(fileURLToPath(new URL("../src/algorithms/regime/types.ts", import.meta.url)), "utf8");
  const marketRegimeBlock = typesText.slice(
    typesText.indexOf("export type MarketRegimeId ="),
    typesText.indexOf("export type LegacyRegimeAlias ="),
  );
  const legacyBlock = typesText.slice(
    typesText.indexOf("export type LegacyRegimeAlias ="),
    typesText.indexOf("export type RegimeNoTradeTag ="),
  );

  for (const legacy of REGIME_LEGACY_ALIASES) {
    assert.doesNotMatch(marketRegimeBlock, new RegExp(`"${legacy}"`));
    assert.match(legacyBlock, new RegExp(`"${legacy}"`));
  }
  assert.doesNotMatch(marketRegimeBlock, /"mean_reversion"/);
  assert.doesNotMatch(marketRegimeBlock, /"trend_continuation"/);
  assert.doesNotMatch(marketRegimeBlock, /"no_trade"/);
});

test("Regime owns dedicated hysteresis and transition state inventory", () => {
  assert.deepEqual(directoryFiles("state"), [
    "confirmed-regime-state.ts",
    "dwell-policy.ts",
    "hysteresis.ts",
    "state-recovery.ts",
    "transition-history.ts",
  ]);
  const dwell = evaluateRegimeDwellPolicy(2, {
    confirmationBars: 3,
    immediateConfidenceThreshold: 0.8,
    minimumDwellBars: 3,
    transitionConfidenceGap: 0.1,
    maximumUnknownBars: 4,
  });
  assert.deepEqual(dwell, { dwellBars: 3, minimumDwellSatisfied: true });
  const state = createConfirmedRegimeState({
    rawRegime: "weak_downtrend",
    confirmedRegime: "strong_uptrend",
    rawConfidence: 0.82,
    confirmedConfidence: 0.71,
    candidateRegime: "weak_downtrend",
    candidateCount: 2,
    dwellBars: 4,
    heldPreviousRegime: true,
    transitionReason: "candidate waiting",
    timestamp: "2026-01-05T15:35:00.000Z",
    previousRegime: "strong_uptrend",
    regimeStartTime: "2026-01-05T15:30:00.000Z",
    minimumDwellSatisfied: true,
    unknownRegimeCount: 1,
    transitionConfidence: 0.82,
    transitionEvidence: { transitionState: "held" },
  });
  assert.equal(state.previousRegime, "strong_uptrend");
  assert.equal(state.regimeStartTime, "2026-01-05T15:30:00.000Z");
  assert.equal(state.minimumDwellSatisfied, true);
  assert.equal(state.unknownRegimeCount, 1);
  assert.equal(state.transitionConfidence, 0.82);
  assert.deepEqual(state.transitionEvidence, { transitionState: "held" });
  const recovered = recoverRegimeHysteresisState({
    primaryTrend: "Strong uptrend",
    volatility: "Normal volatility",
    opportunity: "Trend continuation",
    confidence: 0.71,
    key: "strong_uptrend",
    contextKey: "SPY:2026-01-05",
    ...state,
  }, "SPY:2026-01-05");
  assert.equal(recovered.previousRegime, "strong_uptrend");
  assert.equal(recovered.previousConfidence, 0.71);
  assert.equal(recovered.previousDwellBars, 4);
});

test("Regime isolation keeps settings trade history and archives separate from other algorithms", () => {
  const main = readFrontendMainText();
  const regime = readRegimeModuleText();

  assert.match(main, /regime-selection-trading-settings-v1/);
  assert.match(main, /weighted-voting-trading-settings-v1/);
  assert.match(main, /weighted-confidence-trading-settings-v1/);
  assert.match(main, /regimeTradeHistory/);
  assert.match(main, /confidenceTradeHistory/);
  assert.doesNotMatch(regime, /confidenceBacktestResult|weightedConfidence|weighted-confidence/);
  assert.doesNotMatch(regime, /Meta-Strategy|metaStrategy|weightedVotingTradeHistory/);
});

test("Regime UI exposes dedicated Phase 15 diagnostics without WCA backtest relabeling", () => {
  const text = readFrontendMainText();

  for (const label of [
    "Market condition",
    "Raw regime",
    "Confirmed regime",
    "Direction axis",
    "Volatility axis",
    "Structure axis",
    "Liquidity axis",
    "Session axis",
    "Event-risk axis",
    "Strategy routing",
    "Selected directional strategies",
    "Skipped strategies and reasons",
    "Raw confidence",
    "Effective confidence",
    "Correlation penalty",
    "Family score",
    "Winning direction",
    "Winning score",
    "Directional edge",
    "Hold/abstention rate",
    "Default baseline",
    "Effective current profile",
    "Reset baseline to defaults",
    "Reset profile matrix to defaults",
    "Profile version",
    "Settings version",
    "Current profile ID",
    "ML mode",
    "Artifact status",
    "Probability vector",
    "Rule/ML agreement",
    "Promotion status",
    "Global gates",
    "Requested quantity",
    "Approved quantity",
    "Dedicated Regime Backtest",
  ]) {
    assert.match(text, new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  }

  const regimePanelStart = text.indexOf('id="algoRegimeSelectionPanel"');
  const regimePanelEnd = text.indexOf('id="regimeIndicatorsToggle"', regimePanelStart);
  const regimePanel = text.slice(regimePanelStart, regimePanelEnd);
  assert.match(regimePanel, /Dedicated Regime Backtest/);
  assert.doesNotMatch(regimePanel, /WCA Backtest/);
});

test("Regime owns dedicated diagnostics rollout and acceptance inventories", () => {
  const output = calculateRegimeDecision({
    marketData: {
      symbol: "SPY",
      primaryCandles: deterministicRegimeCandles(),
      allCandles: deterministicRegimeCandles(),
      oneMinuteCandles: deterministicRegimeCandles(),
    },
    settings: liquidRegimeSettings() as never,
  });
  const diagnostics = buildRegimeDiagnosticsBundle(output.result);
  const diagnosticsStatus = regimeDiagnosticsInventoryStatus();

  assert.deepEqual(REGIME_DIAGNOSTICS_FILE_INVENTORY, [
    "diagnostics.ts",
    "decision-trace.ts",
    "classification-trace.ts",
    "strategy-attribution.ts",
    "profile-attribution.ts",
  ]);
  assert.deepEqual(diagnosticsStatus.files, REGIME_DIAGNOSTICS_FILE_INVENTORY);
  assert.deepEqual(diagnosticsStatus.ownedTraceTypes, [
    "decision_trace",
    "classification_trace",
    "strategy_attribution",
    "profile_attribution",
  ]);
  assert.equal(diagnosticsStatus.readOnly, true);
  assert.equal(diagnostics.decisionTrace.signal, output.result.signal);
  assert.equal(diagnostics.classificationTrace.rawRegime, output.result.rawClassification?.rawRegime);
  assert.equal(diagnostics.strategyAttribution.selectedStrategies.length, output.result.selectedStrategies.length);
  assert.equal(diagnostics.profileAttribution.profileId, output.result.effectiveSettings?.profileId ?? null);

  assert.deepEqual(REGIME_ROLLOUT_FILE_INVENTORY, [
    "shadow-comparison.ts",
    "paper-stability.ts",
    "rollout-policy.ts",
    "rollback-policy.ts",
  ]);
  assert.deepEqual(REGIME_FRONTEND_ROLLOUT_PHASES, [
    "shadow_comparison",
    "paper_stability",
    "limited_paper_rollout",
    "rollback_ready",
  ]);
  const rollout = evaluateRegimeFrontendRolloutPolicy({
    shadowComparisonPassed: true,
    paperStabilityPassed: true,
    globalRiskAdapterReady: true,
    brokerAdapterReady: true,
    rollbackReady: true,
  });
  assert.equal(rollout.algorithmId, "regime");
  assert.equal(rollout.limitedPaperAllowed, true);
  assert.equal(rollout.liveTradingAllowed, false);
  assert.equal(compareRegimeShadowDecisions(output.result, output.result).submitOrders, false);
  assert.equal(evaluateRegimePaperStability({
    decisions: 10,
    rejectedOrders: 0,
    duplicateOrderIntents: 0,
    reconciliationBreaks: 0,
    rollbackDrillsPassed: 1,
  }).passed, true);
  const rollback = regimeFrontendRollbackPolicy();
  assert.equal(rollback.disableNewEntries, true);
  assert.equal(rollback.preserveProtectiveExits, true);
  assert.equal(rollback.deleteHistoricalRecords, false);
  assert.equal(rollback.liveOrders, false);
});

test("Regime allowed shared components are read-only transport or approval boundaries", () => {
  const status = regimeSharedBoundaryStatus();
  const expected = [
    { component: "Raw market-data service", allowedUse: "Read-only input" },
    { component: "Quote and candle cache", allowedUse: "Read-only input" },
    { component: "Market clock and calendar", allowedUse: "Read-only input" },
    { component: "Economic-event feed", allowedUse: "Read-only input" },
    { component: "Account equity and buying power", allowedUse: "Read-only snapshot" },
    { component: "Broker client", allowedUse: "Submit approved Regime intents" },
    { component: "Global account-risk engine", allowedUse: "Reduce or reject Regime proposals" },
    { component: "Global risk reservations", allowedUse: "Account-wide exposure control" },
    { component: "Database connection utilities", allowedUse: "Infrastructure only" },
    { component: "Logging and telemetry", allowedUse: "Must include algorithm_id=regime" },
    { component: "Order-side contract types", allowedUse: "Type definitions only" },
    { component: "Authentication and API framework", allowedUse: "Transport only" },
  ];

  assert.deepEqual(REGIME_ALLOWED_SHARED_COMPONENTS, expected);
  assert.deepEqual(status.allowedSharedComponents, REGIME_ALLOWED_SHARED_COMPONENTS);
  assert.equal(status.globalRiskLayerSharedServerSide, true);
  assert.equal(status.localControlsRemainRegimeOwned, true);
  assert.equal(status.sharedComponentsMayRewriteRegimeState, false);
  assert.equal(status.otherAlgorithmsMayModifyPrivateRegimeComponents, false);
});

test("Regime private components are never shared with other algorithms", () => {
  const status = regimeSharedBoundaryStatus();
  const expected = [
    "Regime classification formulas",
    "Regime classification thresholds",
    "Regime axes and composite-state mapping",
    "Regime hysteresis state",
    "Regime transition history",
    "Regime strategy implementations",
    "Regime strategy compatibility matrix",
    "Regime strategy aliases",
    "Regime strategy health",
    "Regime strategy outputs",
    "Regime context outputs",
    "Regime family scores",
    "Regime aggregation",
    "Regime local gates",
    "Regime baseline settings",
    "Regime dynamic profiles",
    "Regime position sizing",
    "Regime entry and exit policy",
    "Regime decisions",
    "Regime order intents",
    "Regime positions and trades",
    "Regime backtest state",
    "Regime backtest results",
    "Regime ML features and artifacts",
    "Regime rollout state",
  ];

  assert.deepEqual(REGIME_NEVER_SHARED_COMPONENTS, expected);
  assert.deepEqual(status.neverSharedComponents, REGIME_NEVER_SHARED_COMPONENTS);
  assert.equal(new Set(REGIME_NEVER_SHARED_COMPONENTS).size, REGIME_NEVER_SHARED_COMPONENTS.length);
  for (const privateComponent of REGIME_NEVER_SHARED_COMPONENTS) {
    assert.match(privateComponent, /^Regime /);
  }
});

test("Regime context and safety components cannot modify directional totals", () => {
  const contextOutput = strategyOutput({
    strategy: "vwap_position",
    role: "regime_context",
    signal: "buy",
    confidence: 1,
    effective_weight: 99,
  });
  const safetyOutput = strategyOutput({
    strategy: "cash_avoid_filter",
    role: "safety_gate",
    signal: "sell",
    confidence: 1,
    effective_weight: 99,
    blockNewEntries: true,
  });
  const directionalOutput = strategyOutput({
    strategy: "moving_average_trend",
    role: "directional",
    signal: "sell",
    confidence: 0.8,
    effective_weight: 1,
  });

  const scores = aggregateRegimeStrategyScores([contextOutput, safetyOutput, directionalOutput]).scores;

  assert.equal(scores.buy, 0);
  assert.equal(scores.sell, 1);
  assert.equal(scores.hold, 0);
});

test("Regime safety-only outputs normalize to Hold instead of directional weight", () => {
  const scores = aggregateRegimeStrategyScores([
    strategyOutput({
      strategy: "cash_avoid_filter",
      role: "safety_gate",
      signal: "hold",
      confidence: 1,
      effective_weight: 99,
      blockNewEntries: true,
    }),
  ]).scores;

  assert.deepEqual(scores, { buy: 0, sell: 0, hold: 1 });
});

test("Regime aliases cannot vote as separate strategies", () => {
  const ids = new Set(regimeSelectionStrategies.map((strategy) => strategy.id));
  const aliases = regimeSelectionStrategies.flatMap((strategy) => strategy.aliases ?? []);

  assert.equal(ids.size, regimeSelectionStrategies.length);
  assert.deepEqual(REGIME_STRATEGY_ALIAS_INVENTORY.map((entry) => entry.alias), [
    "first_pullback_after_open",
    "bollinger_atr_reversion",
    "failed_breakout_strategy",
  ]);
  for (const alias of aliases) {
    assert.equal(ids.has(alias), false);
  }
  for (const entry of REGIME_STRATEGY_ALIAS_INVENTORY) {
    assert.equal(ids.has(entry.canonicalStrategyId), true);
    assert.equal(ids.has(entry.alias), false);
  }
});

test("Regime owns the dedicated strategy role inventory", () => {
  const root = fileURLToPath(new URL("../src/algorithms/regime/strategies", import.meta.url));
  const strategyInventoryFiles = new Set(readdirSync(root));
  for (const file of [
    "base.ts",
    "registry.ts",
    "evaluator.ts",
    "alias-map.ts",
  ]) {
    assert.equal(strategyInventoryFiles.has(file), true, `${file} should exist in the Regime strategy inventory`);
  }
  assert.deepEqual(directoryFiles("strategies/directional"), [
    "bollinger-band-mean-reversion.ts",
    "failed-breakout-reversal.ts",
    "gap-continuation-fade.ts",
    "intraday-breakout.ts",
    "liquidity-sweep-reversal.ts",
    "macd-momentum.ts",
    "market-structure.ts",
    "moving-average-trend.ts",
    "opening-range-breakout.ts",
    "rsi-mean-reversion.ts",
    "trend-pullback.ts",
    "volatility-breakout.ts",
    "vwap-mean-reversion.ts",
    "vwap-trend-continuation.ts",
  ]);
  assert.deepEqual(directoryFiles("strategies/confirmation"), [
    "adx-trend-strength.ts",
    "volume-confirmation.ts",
  ]);
  assert.deepEqual(directoryFiles("strategies/context"), [
    "atr-volatility-regime.ts",
    "vwap-position.ts",
  ]);
  assert.deepEqual(directoryFiles("strategies/safety"), [
    "cash-avoid-filter.ts",
    "circuit-breaker.ts",
    "event-blackout.ts",
    "excessive-spread.ts",
    "extreme-volatility.ts",
    "halt-luld.ts",
    "insufficient-liquidity.ts",
    "missing-critical-data.ts",
    "stale-data.ts",
    "unsupported-session.ts",
  ]);

  assert.equal(REGIME_TOTAL_STRATEGY_DEFINITION_COUNT, 28);
  assert.equal(regimeSelectionStrategies.length, 28);
  assert.deepEqual(catalogInventory("directional"), REGIME_DIRECTIONAL_STRATEGY_INVENTORY.map(({ key, id, name }) => ({ key, id, name })));
  assert.deepEqual(catalogInventory("confirmation"), REGIME_CONFIRMATION_MODULE_INVENTORY.map(({ key, id, name }) => ({ key, id, name })));
  assert.deepEqual(catalogInventory("regime_context"), REGIME_CONTEXT_MODULE_INVENTORY.map(({ key, id, name }) => ({ key, id, name })));
  assert.deepEqual(catalogInventory("safety_gate"), REGIME_SAFETY_GATE_INVENTORY.map(({ key, id, name }) => ({ key, id, name })));
});

test("Regime owns dedicated strategy routing inventory and outputs", () => {
  assert.deepEqual(directoryFiles("routing"), [
    "alias-deduplication.ts",
    "compatibility-matrix.ts",
    "conflict-resolution.ts",
    "regime-family-map.ts",
    "router.ts",
    "strategy-eligibility.ts",
  ]);
  assert.deepEqual(REGIME_COMPATIBILITY_MATRIX.strong_uptrend, [
    "moving_average_trend",
    "trend_pullback",
    "macd_momentum",
    "market_structure",
    "vwap_trend_continuation",
  ]);
  assert.equal(canonicalRegimeRoutingStrategyId("first_pullback_after_open"), "trend_pullback");
  assert.equal(permittedDirectionForRegime("strong_uptrend"), "long");
  assert.equal(permittedDirectionForRegime("strong_downtrend"), "short");
  assert.equal(permittedDirectionForRegime("extreme_volatility_no_trade"), "none");
  assert.deepEqual(representedRegimeFamilies(["moving_average_trend", "vwap_trend_continuation", "opening_range_breakout"]), [
    "trend",
    "vwap",
    "breakout",
  ]);

  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: deterministicRegimeCandles(),
    allCandles: deterministicRegimeCandles(),
    oneMinuteCandles: deterministicRegimeCandles(),
  }, liquidRegimeSettings())!;
  const routing = routeRegimeStrategies("strong_uptrend", market);

  assert.equal(routing.permittedDirection, "long");
  assert.deepEqual(routing.representedFamilies, ["trend", "vwap"]);
  assert.equal(routing.minimumIndependentFamilyParticipationMet, true);
  assert.equal(routing.incompatibleStrategyIds.includes("rsi_mean_reversion"), true);
  assert.equal(routing.abstainedStrategyIds.includes("rsi_mean_reversion"), true);
  assert.equal(routing.disabledStrategyIds.length, 0);
  assert.equal(routing.unhealthyStrategyIds.length, 0);
  assert.deepEqual(routing.aliasDeduplication, [
    { alias: "first_pullback_after_open", canonicalStrategyId: "trend_pullback", alreadyVoted: true },
    { alias: "bollinger_atr_reversion", canonicalStrategyId: "bollinger_band_mean_reversion", alreadyVoted: false },
    { alias: "failed_breakout_strategy", canonicalStrategyId: "failed_breakout_reversal", alreadyVoted: false },
  ]);
});

test("Regime owns dedicated aggregation and decision inventory", () => {
  assert.deepEqual(directoryFiles("decision"), [
    "abstention-policy.ts",
    "contribution-caps.ts",
    "decision-engine.ts",
    "decision-evidence.ts",
    "decision-gates.ts",
    "family-aggregation.ts",
  ]);
  assert.equal(cappedRegimeStrategyContribution(1, 1), 0.15);
  assert.deepEqual(applyRegimeFamilyContributionCap({
    family: "trend",
    buyScore: 0.5,
    sellScore: 0.2,
    activeStrategyCount: 3,
  }), {
    family: "trend",
    buyScore: 0.25,
    sellScore: 0.1,
    activeStrategyCount: 3,
  });
  assert.deepEqual(activeDirectionalRegimeOutputs([
    strategyOutput({ strategy: "moving_average_trend", role: "directional", eligible: true, signal: "buy" }),
    strategyOutput({ strategy: "volume_confirmation", role: "confirmation", eligible: true, signal: "hold" }),
    strategyOutput({ strategy: "disabled_directional", role: "directional", eligible: false, signal: "sell" }),
  ]).map((output) => output.strategy), ["moving_average_trend"]);

  const gateSettings = {
    ...liquidRegimeSettings(),
    minimumWinningScore: 0.7,
    minimumDirectionalEdge: 0.25,
    minimumRegimeConfidence: 0.8,
    minimumActiveStrategies: 4,
    minimumIndependentFamilies: 3,
    maximumAbstentionRate: 0.4,
  };
  const gates = regimeDecisionGateSettings(gateSettings);
  assert.deepEqual(gates, {
    minimumWinningScore: 0.7,
    minimumDirectionalEdge: 0.25,
    minimumRegimeConfidence: 0.8,
    minimumActiveStrategies: 4,
    minimumIndependentFamilies: 3,
    maximumAbstentionRate: 0.4,
  });

  const blockers = regimeTradeBlockers({
    finalSignal: "sell",
    scores: { buy: 0.1, sell: 0.9, hold: 0 },
    buyScore: 0.1,
    sellScore: 0.9,
    winningDirection: "sell",
    winningScore: 0.9,
    secondBestScore: 0.1,
    directionalEdge: 0.8,
    activeStrategyCount: 4,
    activeFamilyCount: 3,
    abstentionRate: 0.2,
    familyScores: [],
  }, 0.9, "Trend continuation", false, gateSettings, "strong_downtrend");
  assert.deepEqual(blockers, []);
});

test("Regime non-directional modules cannot emit Buy or Sell votes", () => {
  const candles = deterministicRegimeCandles();
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: candles,
    allCandles: candles,
    oneMinuteCandles: candles,
  }, liquidRegimeSettings())!;

  for (const strategy of regimeSelectionStrategies.filter((candidate) => candidate.role !== "directional")) {
    const raw = evaluateRegimeStrategyDefinition(strategy, market);
    assert.equal(raw.signal, "Hold", `${strategy.id} must not emit an independent ${raw.signal} vote`);
  }
});

test("Regime disabled strategies do not run", () => {
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: deterministicRegimeCandles(),
    allCandles: deterministicRegimeCandles(),
    oneMinuteCandles: deterministicRegimeCandles(),
  });
  const disabled: RegimeStrategyDefinition = {
    ...regimeSelectionStrategies.find((strategy) => strategy.id === "moving_average_trend")!,
    enabledByDefault: false,
    signal: () => {
      throw new Error("disabled strategy should not run");
    },
  };

  const result = evaluateRegimeStrategyDefinition(disabled, market!);

  assert.equal(result.eligible, false);
  assert.equal(result.signal, "Hold");
  assert.equal(result.confidence, 0);
});

test("Regime missing required inputs make a strategy ineligible", () => {
  const candles = deterministicRegimeCandles().slice(0, 10);
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: candles,
    allCandles: candles,
    oneMinuteCandles: candles,
  });
  const needsSma50: RegimeStrategyDefinition = {
    ...regimeSelectionStrategies.find((strategy) => strategy.id === "moving_average_trend")!,
    minimumBars: 5,
    requiredInputs: ["sma50"],
    signal: () => {
      throw new Error("missing-input strategy should not run");
    },
  };

  const result = evaluateRegimeStrategyDefinition(needsSma50, market!);

  assert.equal(result.eligible, false);
  assert.match(result.reason, /missing required inputs: sma50/);
  assert.equal(result.confidence, 0);
});

test("Regime directional strategy outputs include standardized fields", () => {
  const candles = deterministicRegimeCandles();
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: candles,
    allCandles: candles,
    oneMinuteCandles: candles,
  }, liquidRegimeSettings())!;
  const strategy = regimeSelectionStrategies.find((candidate) => candidate.id === "moving_average_trend")!;
  const raw = evaluateRegimeStrategyDefinition(strategy, market);
  const directional = buildDirectionalStrategyResult({
    strategy,
    raw,
    market,
    decisionTimestamp: market.latest.timestamp,
    effectiveWeight: 0.11,
  });

  assert.ok(directional);
  assert.equal(directional.role, "directional");
  assert.equal(directional.strategyId, "moving_average_trend");
  assert.equal(typeof directional.timestamp, "string");
  assert.equal(typeof directional.reason, "string");
  assert.equal(typeof directional.evidence.latestCandleTimestamp, "string");
});

test("Regime directional strategy validation rejects invalid numeric and temporal outputs", () => {
  const base = directionalResultFixture();
  const context = {
    knownStrategyIds: ["moving_average_trend"],
    decisionTimestamp: "2026-01-05T15:30:00.000Z",
    latestCandleTimestamp: "2026-01-05T15:30:00.000Z",
  };

  assert.deepEqual(validateDirectionalStrategyResult(base, context), []);
  assert.match(validateDirectionalStrategyResult({ ...base, confidence: Number.NaN }, context).join("; "), /confidence must be finite/);
  assert.match(validateDirectionalStrategyResult({ ...base, quality: Number.POSITIVE_INFINITY }, context).join("; "), /quality must be finite/);
  assert.match(validateDirectionalStrategyResult({ ...base, confidence: 1.2 }, context).join("; "), /confidence outside/);
  assert.match(validateDirectionalStrategyResult({ ...base, timestamp: "" }, context).join("; "), /timestamp/);
  assert.match(validateDirectionalStrategyResult({ ...base, strategyId: "unknown_strategy" }, context).join("; "), /Unknown strategy ID/);
  assert.match(validateDirectionalStrategyResult({ ...base, timestamp: "2026-01-05T15:31:00.000Z" }, context).join("; "), /newer than decision/);
  assert.match(
    validateDirectionalStrategyResult(
      { ...base, evidence: { ...base.evidence, latestCandleTimestamp: "2026-01-05T15:31:00.000Z" } },
      context,
    ).join("; "),
    /future candles/,
  );
  assert.match(validateDirectionalStrategyResult({ ...base, signal: "Sell", signedContribution: 0.2 }, context).join("; "), /Sell signedContribution/);
  assert.match(validateDirectionalStrategyResult({ ...base, signal: "Hold", signedContribution: 0.2 }, context).join("; "), /Hold signedContribution/);
});

test("Regime classifier exposes independent axes and point-in-time evidence", () => {
  const candles = deterministicRegimeCandles();
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: candles,
    allCandles: candles,
    oneMinuteCandles: candles,
  }, liquidRegimeSettings())!;
  const raw = buildRawRegimeCondition(market);

  assert.equal(raw.classification.timestamp, market.latest.timestamp);
  assert.equal(raw.axes.direction, "strong_up");
  assert.equal(raw.rawRegime, "intraday_expansion");
  assert.equal(typeof raw.classification.evidence.realizedVolatility, "number");
  assert.equal(raw.classification.missingInputs.includes("QQQ/IWM relative strength"), true);
  assert.equal(raw.classification.missingInputs.includes("VIX state"), true);
});

test("Regime classifier supports required composite regime IDs", () => {
  const candles = deterministicRegimeCandles();
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: candles,
    allCandles: candles,
    oneMinuteCandles: candles,
  })!;
  const raw = buildRawRegimeCondition(market);
  const baseAxes: RegimeAxes = {
    direction: "neutral",
    volatility: "normal",
    structure: "mixed",
    liquidity: "good",
    session: "afternoon",
    eventRisk: "none",
  };
  const regimes = [
    compositeRegimeIdFromAxes(market, raw.features, { ...baseAxes, direction: "strong_up", structure: "trend" }),
    compositeRegimeIdFromAxes(market, raw.features, { ...baseAxes, direction: "weak_up", structure: "trend" }),
    compositeRegimeIdFromAxes(market, raw.features, { ...baseAxes, direction: "strong_down", structure: "trend" }),
    compositeRegimeIdFromAxes(market, raw.features, { ...baseAxes, direction: "weak_down", structure: "trend" }),
    compositeRegimeIdFromAxes(market, raw.features, { ...baseAxes, structure: "range" }),
    compositeRegimeIdFromAxes(market, raw.features, { ...baseAxes, structure: "breakout", session: "opening" }),
    compositeRegimeIdFromAxes(market, raw.features, { ...baseAxes, structure: "breakout", session: "afternoon" }),
    compositeRegimeIdFromAxes(market, raw.features, { ...baseAxes, direction: "strong_up", volatility: "expanded", structure: "trend" }),
    compositeRegimeIdFromAxes(market, raw.features, { ...baseAxes, volatility: "compressed", structure: "range" }),
    compositeRegimeIdFromAxes(market, raw.features, { ...baseAxes, structure: "failed_breakout" }),
    compositeRegimeIdFromAxes(market, raw.features, { ...baseAxes, structure: "mixed" }),
    compositeRegimeIdFromAxes(market, raw.features, { ...baseAxes, eventRisk: "elevated" }),
    compositeRegimeIdFromAxes(market, raw.features, { ...baseAxes, liquidity: "poor" }),
    compositeRegimeIdFromAxes(market, raw.features, { ...baseAxes, volatility: "extreme" }),
  ];

  for (const expected of [
    "strong_uptrend",
    "weak_uptrend",
    "strong_downtrend",
    "weak_downtrend",
    "range_bound",
    "opening_breakout",
    "intraday_expansion",
    "high_volatility_trend",
    "low_volatility_quiet",
    "failed_breakout_reversal",
    "choppy_mixed",
    "event_risk",
    "liquidity_stress",
    "extreme_volatility_no_trade",
  ]) {
    assert.equal(regimes.includes(expected as never), true, `${expected} should be derivable`);
  }
});

test("Regime hysteresis uses configurable confirmation before normal transitions", () => {
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: deterministicRegimeCandles(),
    allCandles: deterministicRegimeCandles(),
    oneMinuteCandles: deterministicRegimeCandles(),
  }, liquidRegimeSettings())!;
  const initialRaw = buildRawRegimeCondition(market);
  const initial = confirmedRegimeCondition(market, initialRaw, null, {
    confirmationBars: 3,
    immediateConfidenceThreshold: 0.9,
    transitionConfidenceGap: 0.5,
  });
  const candidateRaw = {
    ...initialRaw,
    rawRegime: "weak_downtrend",
    key: "weak_downtrend",
    confidence: 0.62,
    classification: { ...initialRaw.classification, rawRegime: "weak_downtrend", confidence: 0.62 },
  };

  const first = confirmedRegimeCondition(market, candidateRaw, initial.hysteresis, {
    confirmationBars: 3,
    immediateConfidenceThreshold: 0.9,
    transitionConfidenceGap: 0.5,
  });
  const second = confirmedRegimeCondition(market, candidateRaw, first.hysteresis, {
    confirmationBars: 3,
    immediateConfidenceThreshold: 0.9,
    transitionConfidenceGap: 0.5,
  });
  const third = confirmedRegimeCondition(market, candidateRaw, second.hysteresis, {
    confirmationBars: 3,
    immediateConfidenceThreshold: 0.9,
    transitionConfidenceGap: 0.5,
  });

  assert.equal(first.held, true);
  assert.equal(first.state.candidateCount, 1);
  assert.equal(second.held, true);
  assert.equal(second.state.candidateCount, 2);
  assert.equal(third.held, false);
  assert.equal(third.state.confirmedRegime, "weak_downtrend");
});

test("Regime hysteresis moves risk-off immediately but confirms risk-on recovery", () => {
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: deterministicRegimeCandles(),
    allCandles: deterministicRegimeCandles(),
    oneMinuteCandles: deterministicRegimeCandles(),
  }, liquidRegimeSettings())!;
  const raw = buildRawRegimeCondition(market);
  const initial = confirmedRegimeCondition(market, raw, null);
  const riskOffRaw = {
    ...raw,
    axes: { ...raw.axes, volatility: "extreme" },
    rawRegime: "extreme_volatility_no_trade",
    key: "extreme_volatility_no_trade",
    confidence: 0.7,
    noTradeReasons: ["Extreme volatility"],
    classification: { ...raw.classification, rawRegime: "extreme_volatility_no_trade", confidence: 0.7 },
  };
  const riskOff = confirmedRegimeCondition(market, riskOffRaw, initial.hysteresis, {
    confirmationBars: 3,
    immediateConfidenceThreshold: 0.95,
  });
  const riskOnRaw = {
    ...raw,
    confidence: 0.99,
    classification: { ...raw.classification, confidence: 0.99 },
  };
  const riskOn = confirmedRegimeCondition(market, riskOnRaw, riskOff.hysteresis, {
    confirmationBars: 3,
    immediateConfidenceThreshold: 0.65,
    transitionConfidenceGap: 0,
  });

  assert.equal(riskOff.held, false);
  assert.equal(riskOff.state.confirmedRegime, "extreme_volatility_no_trade");
  assert.match(riskOff.state.transitionReason, /Risk-off/);
  assert.equal(riskOn.held, true);
  assert.equal(riskOn.state.confirmedRegime, "extreme_volatility_no_trade");
  assert.match(riskOn.state.transitionReason, /Risk-on candidate/);
});

test("Regime router runs only compatible directional strategies for confirmed regimes", () => {
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: deterministicRegimeCandles(),
    allCandles: deterministicRegimeCandles(),
    oneMinuteCandles: deterministicRegimeCandles(),
  }, liquidRegimeSettings())!;

  const strongTrend = routeRegimeStrategies("strong_uptrend", market);
  assert.deepEqual(strongTrend.selectedStrategyIds, [
    "moving_average_trend",
    "trend_pullback",
    "macd_momentum",
    "market_structure",
    "vwap_trend_continuation",
  ]);
  assert.equal(strongTrend.skippedStrategies.some((strategy) => strategy.strategyId === "rsi_mean_reversion"), true);
  assert.equal(strongTrend.contextResults.length > 0, true);
  assert.equal(strongTrend.safetyResults.length > 0, true);

  const riskOff = routeRegimeStrategies("extreme_volatility_no_trade", market);
  assert.deepEqual(riskOff.selectedStrategyIds, []);
  assert.equal(riskOff.skippedStrategies.every((strategy) => /allows no new directional/.test(strategy.reason)), true);
});

test("Regime context multipliers only reduce or preserve directional confidence", () => {
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: deterministicRegimeCandles(),
    allCandles: deterministicRegimeCandles(),
    oneMinuteCandles: deterministicRegimeCandles(),
  }, liquidRegimeSettings())!;
  const strategy = regimeSelectionStrategies.find((candidate) => candidate.id === "moving_average_trend")!;
  const routing = routeRegimeStrategies("strong_uptrend", market);
  const buyMultiplier = contextMultiplierForSignal(strategy, "buy", routing.contextResults);
  const sellMultiplier = contextMultiplierForSignal(strategy, "sell", routing.contextResults);

  assert.equal(buyMultiplier <= 1, true);
  assert.equal(sellMultiplier <= 1, true);
  assert.equal(buyMultiplier >= 0, true);
  assert.equal(sellMultiplier >= 0, true);
});

test("Regime family aggregation separates abstention from directional score", () => {
  const aggregation = aggregateRegimeStrategyScores([
    strategyOutput({
      strategy: "moving_average_trend",
      signal: "buy",
      confidence: 0.9,
      effectiveWeight: 1,
      effective_weight: 1,
      family: "trend_momentum",
    }),
    strategyOutput({
      strategy: "macd_momentum",
      signal: "buy",
      confidence: 0.9,
      effectiveWeight: 1,
      effective_weight: 1,
      family: "trend_momentum",
    }),
    strategyOutput({
      strategy: "vwap_mean_reversion",
      signal: "sell",
      confidence: 0.7,
      effectiveWeight: 1,
      effective_weight: 1,
      family: "mean_reversion",
    }),
    strategyOutput({
      strategy: "rsi_mean_reversion",
      signal: "hold",
      confidence: 0.8,
      effectiveWeight: 1,
      effective_weight: 1,
      family: "mean_reversion",
    }),
  ]);

  assert.equal(aggregation.finalSignal, "buy");
  assert.equal(aggregation.winningDirection, "buy");
  assert.equal(aggregation.activeStrategyCount, 3);
  assert.equal(aggregation.activeFamilyCount, 2);
  assert.equal(aggregation.abstentionRate, 0.25);
  assert.equal(aggregation.scores.hold, 0.25);
  assert.equal(aggregation.scores.buy > aggregation.scores.sell, true);
});

test("Regime Phase 16 strategy sweep covers every strategy warm-up missing-data and stale-data contracts", () => {
  const candles = deterministicRegimeCandles();
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: candles,
    allCandles: candles,
    oneMinuteCandles: candles,
  }, liquidRegimeSettings())!;
  const warmupMarket = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: candles.slice(0, 8),
    allCandles: candles.slice(0, 8),
    oneMinuteCandles: candles.slice(0, 8),
  }, liquidRegimeSettings())!;

  assert.equal(regimeSelectionStrategies.length >= 25, true);
  for (const strategy of regimeSelectionStrategies) {
    const raw = evaluateRegimeStrategyDefinition(strategy, market);
    assert.equal(typeof raw.reason, "string", `${strategy.id} reason`);
    assert.equal(Number.isFinite(raw.confidence), true, `${strategy.id} confidence finite`);
    assert.equal(raw.confidence >= 0 && raw.confidence <= 1, true, `${strategy.id} confidence range`);
    assert.equal(raw.role ?? strategy.role, strategy.role, `${strategy.id} preserves role`);
    if (strategy.role === "directional") {
      const directional = buildDirectionalStrategyResult({
        strategy,
        raw,
        market,
        decisionTimestamp: market.latest.timestamp,
        effectiveWeight: strategy.baseWeight,
      });
      assert.ok(directional, `${strategy.id} directional result`);
      assert.equal(validateDirectionalStrategyResult(directional!, {
        knownStrategyIds: regimeSelectionStrategies.map((candidate) => candidate.id),
        decisionTimestamp: market.latest.timestamp,
        latestCandleTimestamp: market.latest.timestamp,
      }).length, 0, `${strategy.id} validates`);
    }

    if (warmupMarket.candles.length < strategy.minimumBars) {
      const warmup = evaluateRegimeStrategyDefinition(strategy, warmupMarket);
      assert.equal(warmup.eligible, false, `${strategy.id} warm-up ineligible`);
      assert.match(warmup.reason, /needs \d+ bars/);
    }
  }

  const stale = evaluateRegimeStrategyDefinition(regimeSelectionStrategies.find((strategy) => strategy.id === "stale_data")!, market);
  assert.equal(stale.signal, "Hold");
  assert.match(stale.reason, /Stale-data check/);
});

test("Regime correlated VWAP strategies are family-capped and context cannot create direction alone", () => {
  const aggregation = aggregateRegimeStrategyScores([
    strategyOutput({
      strategy: "vwap_trend_continuation",
      name: "VWAP Trend Continuation",
      signal: "buy",
      confidence: 1,
      effectiveWeight: 10,
      effective_weight: 10,
      family: "trend_momentum",
    }),
    strategyOutput({
      strategy: "vwap_mean_reversion",
      name: "VWAP Mean Reversion",
      signal: "buy",
      confidence: 1,
      effectiveWeight: 10,
      effective_weight: 10,
      family: "mean_reversion",
    }),
    strategyOutput({
      strategy: "vwap_position",
      name: "VWAP Position Strategy",
      signal: "buy",
      role: "regime_context",
      confidence: 1,
      effectiveWeight: 10,
      effective_weight: 10,
      family: "regime_context",
    }),
  ]);

  for (const family of aggregation.familyScores) {
    assert.equal(family.buyScore + family.sellScore <= REGIME_MAX_FAMILY_CONTRIBUTION, true);
  }
  const contextOnly = aggregateRegimeStrategyScores([
    strategyOutput({
      strategy: "vwap_position",
      name: "VWAP Position Strategy",
      signal: "buy",
      role: "regime_context",
      confidence: 1,
      effectiveWeight: 10,
      effective_weight: 10,
      family: "regime_context",
    }),
  ]);
  assert.equal(contextOnly.finalSignal, "hold");
  assert.deepEqual(contextOnly.scores, { buy: 0, sell: 0, hold: 1 });
});

test("Regime Phase 16 property invariants hold across deterministic scenarios", () => {
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: deterministicRegimeCandles(),
    allCandles: deterministicRegimeCandles(),
    oneMinuteCandles: deterministicRegimeCandles(),
  }, liquidRegimeSettings())!;
  const settings = relaxedRegimeBacktestSettings();
  const scenarios = [
    { label: "buy", result: regimeResult({ signal: "Buy", tradeAllowed: true }), options: {} },
    { label: "sell-exit", result: regimeResult({ signal: "Sell", tradeAllowed: true, aggregateSignal: "sell", scores: { buy: 0.1, sell: 0.82, hold: 0.08 } }), currentPosition: { shares: 10, marketValue: 1000 }, options: {} },
    { label: "sell-short", result: regimeResult({ signal: "Sell", tradeAllowed: true, aggregateSignal: "sell", scores: { buy: 0.1, sell: 0.82, hold: 0.08 } }), options: shortEnabledOptions() },
    { label: "blocked", result: regimeResult({ signal: "Buy", tradeAllowed: false, tradeBlockers: ["blocked"] }), options: {} },
  ];

  for (const scenario of scenarios) {
    const target = buildRegimeTargetOrder(
      scenario.result,
      market,
      "SPY",
      settings,
      undefined,
      scenario.currentPosition ?? { shares: 0, marketValue: 0, availableBuyingPower: 100000, remainingAlgorithmRiskDollars: 10000, globalRiskCapacityQuantity: 1000 },
      scenario.options,
    );
    assert.equal(Number.isFinite(target.quantity), true, scenario.label);
    assert.equal(target.quantity >= 0, true, scenario.label);
    assert.equal(target.sizing.finalQuantity >= 0, true, scenario.label);
    assert.equal(Number.isFinite(target.sizing.finalQuantity), true, scenario.label);
    assert.equal(target.sizing.riskDollars <= settings.startingCapital * (settings.baseRiskPercent / 100), true, scenario.label);
    if (target.orderIntent) {
      assert.equal(target.orderIntent.requestedQuantity, target.quantity);
      assert.equal(target.plannedStopRiskDollars <= target.orderIntent.requestedRiskDollars + 0.01, true, scenario.label);
      if (target.orderIntent.signal === "Buy") {
        assert.equal(target.orderIntent.protectiveStopPrice < target.orderIntent.expectedEntryPrice, true, scenario.label);
      } else {
        assert.equal(target.orderIntent.protectiveStopPrice > target.orderIntent.expectedEntryPrice, true, scenario.label);
      }
    }
  }

  const first = calculateRegimeDecision({
    marketData: { symbol: "SPY", primaryCandles: market.candles, allCandles: market.allCandles, oneMinuteCandles: market.oneMinuteCandles },
    settings,
  }).result;
  const second = calculateRegimeDecision({
    marketData: { symbol: "SPY", primaryCandles: market.candles, allCandles: market.allCandles, oneMinuteCandles: market.oneMinuteCandles },
    settings,
  }).result;
  assert.deepEqual(first.scores, second.scores);
  assert.deepEqual(first.familyScores, second.familyScores);
  assert.equal(first.signal, second.signal);
});

test("Regime decision exposes routing and blocks insufficient family coverage", () => {
  const candles = deterministicRegimeCandles();
  const output = calculateRegimeDecision({
    marketData: {
      symbol: "SPY",
      primaryCandles: candles,
      allCandles: candles,
      oneMinuteCandles: candles,
    },
    settings: {
      ...liquidRegimeSettings(),
      minimumBuyScore: 0.01,
      minimumWinningScore: 0.01,
      minimumSignalEdge: 0,
      minimumDirectionalEdge: 0,
      minimumRegimeConfidence: 0,
      minimumActiveStrategies: 1,
      minimumIndependentFamilies: 6,
      maximumAbstentionRate: 1,
    } as never,
  });

  assert.ok(output.result.routing);
  assert.equal(typeof output.result.activeFamilyCount, "number");
  assert.equal(output.result.tradeAllowed, false);
  assert.equal(output.result.tradeBlockers.some((blocker) => /Independent family coverage/.test(blocker)), true);
});

test("Regime ML defaults to shadow for paper and off for live", () => {
  const candles = deterministicRegimeCandles();
  const paper = calculateRegimeDecision({
    marketData: {
      symbol: "SPY",
      primaryCandles: candles,
      allCandles: candles,
      oneMinuteCandles: candles,
    },
    settings: liquidRegimeSettings() as never,
  });
  const live = calculateRegimeDecision({
    marketData: {
      symbol: "SPY",
      primaryCandles: candles,
      allCandles: candles,
      oneMinuteCandles: candles,
    },
    settings: liquidRegimeSettings() as never,
    liveTrading: true,
  });

  assert.equal(paper.result.ml?.mode, "shadow");
  assert.equal(paper.result.ml?.appliedEffect, "shadow_only");
  assert.ok(paper.result.decisionSnapshot?.pointInTimeFeatures);
  assert.equal(paper.result.decisionSnapshot?.subsequentRealizedRegimeLabel, null);
  assert.equal(live.result.ml?.mode, "off");
  assert.equal(live.result.ml?.features, null);
});

test("Regime optional ML inventory is shadow-only and cannot control deterministic decisions initially", () => {
  const inventory = regimeMlInventoryStatus();

  assert.deepEqual(REGIME_ML_FILE_INVENTORY, [
    "feature-builder.ts",
    "label-builder.ts",
    "predictor.ts",
    "artifact-loader.ts",
    "validation.ts",
    "promotion-policy.ts",
    "types.ts",
  ]);
  assert.deepEqual(inventory.files, REGIME_ML_FILE_INVENTORY);
  assert.equal(REGIME_ML_INITIAL_MODE, "shadow");
  assert.deepEqual(REGIME_ML_SHADOW_FORBIDDEN_ACTIONS, [
    "Replace deterministic classification",
    "Change strategy signals",
    "Change dynamic settings",
    "Block trades",
    "Increase position size",
    "Change stops or exits",
    "Write into another algorithm",
    "Train using future information",
    "Promote itself automatically",
  ]);
  assert.deepEqual(inventory.shadowForbiddenActions, REGIME_ML_SHADOW_FORBIDDEN_ACTIONS);
  assert.equal(inventory.optional, true);
  assert.equal(inventory.maximumAutomaticPromotionMode, "confirm_only");
  assert.equal(inventory.isolatedFromOtherAlgorithms, true);
  assert.equal(inventory.decisionTimeFeaturesOnly, true);

  const candles = deterministicRegimeCandles();
  const settings = {
    ...liquidRegimeSettings(),
    minimumWinningScore: 0,
    minimumBuyScore: 0,
    minimumDirectionalEdge: 0,
    minimumSignalEdge: 0,
    minimumRegimeConfidence: 0,
    minimumActiveStrategies: 1,
    minimumIndependentFamilies: 1,
    maximumAbstentionRate: 1,
  } as never;
  const deterministic = calculateRegimeDecision({
    marketData: {
      symbol: "SPY",
      primaryCandles: candles,
      allCandles: candles,
      oneMinuteCandles: candles,
    },
    settings,
    mlMode: "off",
  });
  const shadow = calculateRegimeDecision({
    marketData: {
      symbol: "SPY",
      primaryCandles: candles,
      allCandles: candles,
      oneMinuteCandles: candles,
    },
    settings,
    mlMode: "shadow",
    mlArtifact: validRegimeMlArtifact(),
  });
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: candles,
    allCandles: candles,
    oneMinuteCandles: candles,
  }, settings);
  assert.ok(market);
  const deterministicOrder = buildRegimeTargetOrder(deterministic.result, market, "SPY", settings, undefined, { currentPosition: 0, ...pricedIntentOptions() });
  const shadowOrder = buildRegimeTargetOrder(shadow.result, market, "SPY", settings, undefined, { currentPosition: 0, ...pricedIntentOptions() });

  assert.equal(shadow.result.ml?.mode, "shadow");
  assert.equal(shadow.result.ml?.appliedEffect, "shadow_only");
  assert.equal(shadow.result.rawClassification?.rawRegime, deterministic.result.rawClassification?.rawRegime);
  assert.equal(shadow.result.confirmedState?.confirmedRegime, deterministic.result.confirmedState?.confirmedRegime);
  assert.deepEqual(
    shadow.result.selectedStrategies.map((strategy) => [strategy.strategy, strategy.signal]),
    deterministic.result.selectedStrategies.map((strategy) => [strategy.strategy, strategy.signal]),
  );
  assert.equal(shadow.result.signal, deterministic.result.signal);
  assert.equal(shadow.result.tradeAllowed, deterministic.result.tradeAllowed);
  assert.deepEqual(shadow.result.tradeBlockers, deterministic.result.tradeBlockers);
  assert.deepEqual(shadow.result.effectiveSettings, deterministic.result.effectiveSettings);
  assert.equal(shadowOrder.quantity, deterministicOrder.quantity);
  assert.equal(shadowOrder.positionEffect, deterministicOrder.positionEffect);
  assert.equal(shadowOrder.triggerPrice, deterministicOrder.triggerPrice);
  assert.equal(shadowOrder.stopPrice, deterministicOrder.stopPrice);
  assert.equal(shadowOrder.targetPrice, deterministicOrder.targetPrice);
  assert.equal(shadowOrder.riskDollars, deterministicOrder.riskDollars);
});

test("Regime ML artifact loader rejects unsafe or leaking artifacts", () => {
  const result = regimeResult();
  const features = buildRegimeMlFeatures({
    ...result,
    rawClassification: {
      axes: {
        direction: "strong_up",
        volatility: "normal",
        structure: "trend",
        liquidity: "good",
        session: "midday",
        eventRisk: "none",
      },
      rawRegime: "strong_uptrend",
      confidence: 0.8,
      evidence: {},
      missingInputs: [],
      timestamp: "2026-01-05T15:30:00.000Z",
    },
    confirmedState: {
      rawRegime: "strong_uptrend",
      confirmedRegime: "strong_uptrend",
      rawConfidence: 0.8,
      confirmedConfidence: 0.8,
      candidateRegime: null,
      candidateCount: 0,
      dwellBars: 3,
      heldPreviousRegime: false,
      transitionReason: "test",
      timestamp: "2026-01-05T15:30:00.000Z",
    },
  });
  const unsafe = {
    ...validRegimeMlArtifact(),
    feature_schema_version: "wrong_schema",
    artifact_hash: "bad",
    training_end: "2026-01-06T00:00:00.000Z",
    trusted: false,
    feature_names: ["rawAdx"],
    feature_imputation_policy: { rawAdx: "none" },
  } as unknown as RegimeMlArtifact;

  const reasons = validateRegimeMlArtifact(unsafe, features, "2026-01-05T15:30:00.000Z");
  assert.equal(reasons.includes("regime.ml.feature_schema_mismatch"), true);
  assert.equal(reasons.includes("regime.ml.invalid_artifact_hash"), true);
  assert.equal(reasons.includes("regime.ml.training_after_decision"), true);
  assert.equal(reasons.includes("regime.ml.artifact_untrusted"), true);
  assert.equal(loadRegimeMlArtifact(unsafe, features, "2026-01-05T15:30:00.000Z").loaded, false);
});

test("Regime unavailable ML artifacts fall back to deterministic rules without future features", () => {
  const candles = deterministicRegimeCandles();
  const output = calculateRegimeDecision({
    marketData: {
      symbol: "SPY",
      primaryCandles: candles,
      allCandles: candles,
      oneMinuteCandles: candles,
    },
    settings: { ...liquidRegimeSettings(), mlMode: "confirm_only" } as never,
    mlMode: "confirm_only",
    mlArtifact: null,
  });
  const featureKeys = Object.keys(output.result.ml?.features?.values ?? {});

  assert.equal(output.result.ml?.prediction.enabled, false);
  assert.equal(output.result.ml?.appliedEffect, "none");
  assert.match(output.result.ml?.reasonCodes.join("; ") ?? "", /regime\.ml\.no_artifact/);
  assert.equal(featureKeys.some((key) => /future|subsequent/i.test(key)), false);
});

test("Regime ML confirm-only can only make decisions more conservative", () => {
  const candles = deterministicRegimeCandles();
  const output = calculateRegimeDecision({
    marketData: {
      symbol: "SPY",
      primaryCandles: candles,
      allCandles: candles,
      oneMinuteCandles: candles,
    },
    settings: {
      ...liquidRegimeSettings(),
      mlMode: "confirm_only",
      minimumWinningScore: 0,
      minimumBuyScore: 0,
      minimumDirectionalEdge: 0,
      minimumSignalEdge: 0,
      minimumRegimeConfidence: 0,
      minimumActiveStrategies: 1,
      minimumIndependentFamilies: 1,
      maximumAbstentionRate: 1,
    } as never,
    mlArtifact: {
      ...validRegimeMlArtifact(),
      transition_intercept: 3,
    },
  });

  assert.equal(output.result.ml?.mode, "confirm_only");
  assert.equal(output.result.ml?.appliedEffect, "blocked_transition");
  assert.equal(output.result.tradeAllowed, false);
  assert.equal(output.result.signal, "Hold");
  assert.equal(output.result.tradeBlockers.some((blocker) => blocker === "regime.ml.confirm_only.transition_probability_block"), true);
});

test("Regime ML labels are offline-only and promotion remains conservative", () => {
  const candles = deterministicRegimeCandles();
  const label = buildOfflineRegimeLabel({
    decisionTimestamp: "2026-01-05T15:30:00.000Z",
    futureCandles: candles.slice(10, 40),
    labelDefinitionVersion: "regime_labels_v1",
    futureObservationWindowBars: 20,
    thresholds: { returnThreshold: 0.002, rangeThreshold: 0.004 },
  });
  const output = calculateRegimeDecision({
    marketData: {
      symbol: "SPY",
      primaryCandles: candles,
      allCandles: candles,
      oneMinuteCandles: candles,
    },
    settings: liquidRegimeSettings() as never,
  });
  const promotion = evaluateRegimeMlPromotionPolicy(output.result, validRegimeMlArtifact());

  assert.ok(label);
  assert.equal(output.result.ml?.features?.values.subsequentRealizedRegimeLabel, undefined);
  assert.equal(output.result.decisionSnapshot?.subsequentRealizedRegimeLabel, null);
  assert.equal(promotion.promoted, false);
  assert.equal(promotion.targetMode, "shadow");
  assert.deepEqual(promotion.reasonCodes, ["regime.ml.promotion_backend_policy_required"]);
});

test("Regime decision snapshots include Phase 14 recorder fields", () => {
  const candles = deterministicRegimeCandles();
  const output = calculateRegimeDecision({
    marketData: {
      symbol: "SPY",
      primaryCandles: candles,
      allCandles: candles,
      oneMinuteCandles: candles,
    },
    settings: liquidRegimeSettings() as never,
  });
  const snapshot = output.result.decisionSnapshot!;

  assert.equal(snapshot.algorithm_id, "regime");
  assert.equal(snapshot.algorithmVersion, "regime_algorithm_v2");
  assert.equal(snapshot.symbol, "SPY");
  assert.equal(snapshot.dataTimestamp, snapshot.decisionTimestamp);
  assert.equal(snapshot.decisionId.includes("regime:SPY:"), true);
  assert.equal(typeof snapshot.settingsVersion, "string");
  assert.equal(typeof snapshot.strategyVersion, "string");
  assert.equal(typeof snapshot.profileVersion, "string");
  assert.deepEqual(snapshot.axes, output.result.rawClassification?.axes);
  assert.deepEqual(snapshot.missingInputs, output.result.rawClassification?.missingInputs);
  assert.deepEqual(snapshot.hysteresisState, output.result.confirmedState);
  assert.equal(snapshot.selectedStrategies.length, output.result.selectedStrategies.length);
  assert.equal(snapshot.contextResults.length, output.result.routing?.contextResults.length);
  assert.equal(snapshot.safetyResults.length, output.result.routing?.safetyResults.length);
  assert.deepEqual(snapshot.effectiveSettings, output.result.effectiveSettings);
  assert.equal(snapshot.mlMode, output.result.ml?.mode);
  assert.equal(snapshot.globalGateOutcome, null);
  assert.equal(snapshot.brokerReconciliationResult, null);
});

test("Regime owns dedicated dynamic trading profile inventory", () => {
  assert.deepEqual(directoryFiles("profile"), [
    "baseline-settings.ts",
    "dynamic-profile.ts",
    "profile-bounds.ts",
    "profile-validation.ts",
    "profile-versioning.ts",
    "regime-profile-matrix.ts",
  ]);
  assert.equal(REGIME_PROFILE_VERSION, "regime_profile_matrix_v1");
  assert.equal(REGIME_PROFILE_MATRIX.extreme_volatility_no_trade.newEntriesAllowed, false);
  assert.equal(REGIME_PROFILE_MATRIX.extreme_volatility_no_trade.maximumTradesOverride, 0);

  const base = baseRegimeSettingsFromTradingSettings(regimeTradingSettingsFixture());
  const bounded = boundedRegimeEffectiveSettings(base, {
    baseSettingsVersion: "settings",
    profileVersion: REGIME_PROFILE_VERSION,
    profileId: "strong_uptrend:test",
    confirmedRegime: "strong_uptrend",
    generatedAt: "2026-01-05T15:30:00.000Z",
    effectiveRiskPercent: base.baseRiskPercent * 3,
    effectiveOrderAllocationPercent: base.orderAllocationPercent * 3,
    effectiveMaxPositionPercent: base.maxPositionPercent * 3,
    effectiveAtrStopMultiplier: base.atrStopMultiplier,
    effectiveTakeProfitR: base.takeProfitR,
    effectiveMaximumParticipationPercent: base.maximumVolumeParticipationPercent * 3,
    effectiveMinimumWinningScore: 0,
    effectiveMinimumDirectionalEdge: 0,
    effectiveMinimumRegimeConfidence: 0,
    effectiveMaximumTrades: base.maxTradesPerDay + 10,
    newEntriesAllowed: true,
    pyramidingAllowed: base.pyramidingEnabled,
    reasons: [],
  });

  assert.equal(bounded.effectiveRiskPercent, base.baseRiskPercent);
  assert.equal(bounded.effectiveOrderAllocationPercent, base.orderAllocationPercent);
  assert.equal(bounded.effectiveMaxPositionPercent, base.maxPositionPercent);
  assert.equal(bounded.effectiveMaximumParticipationPercent, base.maximumVolumeParticipationPercent);
  assert.equal(bounded.effectiveMinimumWinningScore, base.minimumWinningScore);
  assert.equal(bounded.effectiveMinimumDirectionalEdge, base.minimumDirectionalEdge);
  assert.equal(bounded.effectiveMinimumRegimeConfidence, base.minimumRegimeConfidence);
  assert.equal(bounded.effectiveMaximumTrades, base.maxTradesPerDay);
  assert.deepEqual(validateEffectiveRegimeProfile(base, bounded), []);
});

test("Regime dynamic profiles derive effective settings without mutating base settings", () => {
  const settings = regimeTradingSettingsFixture();
  const baseBefore = baseRegimeSettingsFromTradingSettings(settings);
  const result = regimeResult({
    confirmedState: {
      rawRegime: "high_volatility_trend",
      confirmedRegime: "high_volatility_trend",
      rawConfidence: 0.8,
      confirmedConfidence: 0.8,
      candidateRegime: null,
      candidateCount: 0,
      dwellBars: 5,
      heldPreviousRegime: false,
      transitionReason: "test",
      timestamp: "2026-01-05T15:30:00.000Z",
    },
    rawClassification: {
      axes: {
        direction: "strong_up",
        volatility: "expanded",
        structure: "trend",
        liquidity: "good",
        session: "midday",
        eventRisk: "none",
      },
      rawRegime: "high_volatility_trend",
      confidence: 0.8,
      evidence: {},
      missingInputs: [],
      timestamp: "2026-01-05T15:30:00.000Z",
    },
  });
  const effective = resolveEffectiveRegimeSettings({
    result,
    settings,
    baseSettingsVersion: "base-test-v1",
  });

  assert.deepEqual(baseRegimeSettingsFromTradingSettings(settings), baseBefore);
  assert.equal(effective.baseSettingsVersion, "base-test-v1");
  assert.equal(effective.confirmedRegime, "high_volatility_trend");
  assert.equal(effective.effectiveRiskPercent <= settings.baseRiskPercent, true);
  assert.equal(effective.effectiveOrderAllocationPercent <= settings.orderAllocationPercent, true);
  assert.equal(effective.effectiveMaxPositionPercent <= settings.maxPositionPercent, true);
  assert.equal(effective.effectiveAtrStopMultiplier >= settings.atrStopMultiplier, true);
  assert.equal(effective.reasons.some((reason) => /High-volatility trend/.test(reason)), true);
});

test("Regime no-trade dynamic profiles block only new entries through effective settings", () => {
  const settings = regimeTradingSettingsFixture();
  const result = regimeResult({
    signal: "Buy",
    tradeAllowed: true,
    confirmedState: {
      rawRegime: "liquidity_stress",
      confirmedRegime: "liquidity_stress",
      rawConfidence: 0.8,
      confirmedConfidence: 0.8,
      candidateRegime: null,
      candidateCount: 0,
      dwellBars: 1,
      heldPreviousRegime: false,
      transitionReason: "risk-off",
      timestamp: "2026-01-05T15:30:00.000Z",
    },
  });
  const effective = resolveEffectiveRegimeSettings({ result, settings });

  assert.equal(effective.newEntriesAllowed, false);
  assert.equal(effective.pyramidingAllowed, false);
  assert.equal(effective.effectiveRiskPercent, 0);
  assert.equal(effective.effectiveMaximumTrades, 0);
});

test("Regime wider ATR profile stop reduces quantity while keeping dollar risk controlled", () => {
  const candles = deterministicRegimeCandles();
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: candles,
    allCandles: candles,
    oneMinuteCandles: candles,
  }, liquidRegimeSettings())!;
  const settings = {
    ...regimeTradingSettingsFixture(),
    fixedStopDistanceDollars: 0,
    atrStopMultiplier: 1,
    baseRiskPercent: 1,
  };
  const baseSizing = calculateRegimePositionSize(market, "Buy", 1, settings);
  const profileSizing = calculateRegimePositionSize(market, "Buy", 1, settings, undefined, { marketValue: 0 }, {
    riskMultiplier: 1,
    targetMultiplier: 1,
    reasonCodes: ["test"],
    effectiveSettings: {
      baseSettingsVersion: "base",
      profileVersion: "profile",
      profileId: "high_volatility_trend:test",
      confirmedRegime: "high_volatility_trend",
      generatedAt: market.latest.timestamp,
      effectiveRiskPercent: 1,
      effectiveOrderAllocationPercent: settings.orderAllocationPercent,
      effectiveMaxPositionPercent: settings.maxPositionPercent,
      effectiveAtrStopMultiplier: 2,
      effectiveTakeProfitR: settings.takeProfitR,
      effectiveMaximumParticipationPercent: settings.maxParticipationPercent,
      effectiveMinimumWinningScore: settings.minimumWinningScore,
      effectiveMinimumDirectionalEdge: settings.minimumDirectionalEdge,
      effectiveMinimumRegimeConfidence: settings.minimumRegimeConfidence,
      effectiveMaximumTrades: settings.maxTradesPerDay,
      newEntriesAllowed: true,
      pyramidingAllowed: true,
      reasons: ["wider stop"],
    },
  });

  assert.equal(profileSizing.stopDistance > baseSizing.stopDistance, true);
  assert.equal(profileSizing.finalQuantity <= baseSizing.finalQuantity, true);
  assert.equal(profileSizing.riskDollars <= baseSizing.riskDollars, true);
});

test("Regime target orders store the dynamic profile ID", () => {
  const candles = deterministicRegimeCandles();
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: candles,
    allCandles: candles,
    oneMinuteCandles: candles,
  }, liquidRegimeSettings())!;
  const result = calculateRegimeDecision({
    marketData: {
      symbol: "SPY",
      primaryCandles: candles,
      allCandles: candles,
      oneMinuteCandles: candles,
    },
    settings: {
      ...regimeTradingSettingsFixture(),
      minimumWinningScore: 0,
      minimumBuyScore: 0,
      minimumDirectionalEdge: 0,
      minimumSignalEdge: 0,
      minimumRegimeConfidence: 0,
      minimumActiveStrategies: 1,
      minimumIndependentFamilies: 1,
      maximumAbstentionRate: 1,
    },
  }).result;
  const target = buildRegimeTargetOrder(result, market, "SPY", regimeTradingSettingsFixture());

  assert.equal(typeof target.profileId, "string");
  assert.match(target.profileId ?? "", /regime_profile_matrix_v1/);
});

test("Regime position sizing uses the direction-neutral signal strength ladder", () => {
  assert.equal(signalStrengthMultiplierForWinningStrength(0.49), 0);
  assert.equal(signalStrengthMultiplierForWinningStrength(0.55), 0.25);
  assert.equal(signalStrengthMultiplierForWinningStrength(0.65), 0.5);
  assert.equal(signalStrengthMultiplierForWinningStrength(0.75), 0.75);
  assert.equal(signalStrengthMultiplierForWinningStrength(0.8), 1);
  assert.equal(signalStrengthMultiplierForWinningStrength(0.95), 1);
});

test("Regime owns dedicated sizing risk inventory", () => {
  assert.deepEqual(directoryFiles("risk"), [
    "exposure-cap.ts",
    "liquidity-cap.ts",
    "position-sizing.ts",
    "risk-budget.ts",
    "stop-calculation.ts",
    "target-calculation.ts",
  ]);
  const settings = regimeTradingSettingsFixture();
  const defaults = {
    baseRiskPercent: 1,
    maxPositionPercent: 25,
    fixedStopDistanceDollars: 0.5,
    atrStopMultiplier: 2,
    minimumStopDistancePercent: 0.1,
    maxParticipationPercent: 0.5,
    maxAllowedShares: 500,
  };

  assert.equal(regimeRiskBudget(100000, 1, 0.5), 500);
  assert.equal(regimeStopDistance(100, 0.75, 2, defaults), 1.5);
  assert.equal(regimeTargetDistance(1.5, 2), 3);
  assert.equal(regimeLiquidityCap(10000, 0.5), 50);
  const caps = regimePositionAndBuyingPowerCaps({
    accountEquity: settings.startingCapital,
    entryPrice: 100,
    maxPositionPercent: 50,
    orderAllocationPercent: 20,
    dailyAllocationPercent: 30,
    currentPosition: { marketValue: 1000 },
  });
  assert.equal(caps.maxPositionDollars, settings.startingCapital * 0.5);
  assert.equal(caps.maxOrderDollars, settings.startingCapital * 0.2);
  assert.equal(caps.availableBuyingPower, Math.min(settings.startingCapital * 0.5, settings.startingCapital * 0.3) - 1000);
});

test("Regime position sizing records every cap and limiting factor", () => {
  const candles = deterministicRegimeCandles();
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: candles,
    allCandles: candles,
    oneMinuteCandles: candles,
  }, liquidRegimeSettings())!;
  const settings = {
    ...regimeTradingSettingsFixture(),
    fixedStopDistanceDollars: 1,
    baseRiskPercent: 1,
    orderAllocationPercent: 20,
    maxPositionPercent: 50,
    maxParticipationPercent: 0.01,
    maximumVolumeParticipationPercent: 0.01,
    maxAllowedShares: 500,
    maximumAllowedShares: 500,
  };
  const sizing = calculateRegimePositionSize(market, "Buy", 0.85, settings, undefined, {
    marketValue: 0,
    availableBuyingPower: 100000,
    remainingAlgorithmRiskDollars: 10000,
    globalRiskCapacityQuantity: 1000,
  });

  assert.equal(sizing.signalStrengthMultiplier, 1);
  assert.equal(sizing.quantityCaps.some((cap) => cap.label === "global_risk_capacity" && cap.quantity === 1000), true);
  assert.equal(sizing.finalQuantity, Math.floor(Math.min(
    sizing.riskBasedQuantity,
    sizing.allocationBasedQuantity,
    sizing.positionBasedQuantity,
    sizing.buyingPowerQuantity,
    sizing.liquidityBasedQuantity,
    sizing.shareLimitQuantity,
  )));
  assert.equal(sizing.limitingFactor, "liquidity");
});

test("Regime position sizing blocks invalid or unavailable required protections", () => {
  const candles = deterministicRegimeCandles().map((candle) => ({ ...candle, volume: 1 }));
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: candles,
    allCandles: candles,
    oneMinuteCandles: candles,
  }, liquidRegimeSettings())!;
  const settings = {
    ...regimeTradingSettingsFixture(),
    fixedStopDistanceDollars: 0,
    minimumOneMinuteVolume: 1000,
  };
  const sizing = calculateRegimePositionSize(
    { ...market, atr: { ...market.atr, atr1m: null, atr5m: null, atrPercent: 0 } },
    "Buy",
    0.9,
    settings,
    undefined,
    {
      marketValue: 0,
      availableBuyingPower: 100000,
      remainingAlgorithmRiskDollars: 1,
      globalRiskCapacityQuantity: null,
      requireSpreadEstimate: true,
    },
  );

  assert.equal(sizing.finalQuantity, 0);
  assert.equal(sizing.blockerCodes.includes("regime.sizing.atr_unavailable"), true);
  assert.equal(sizing.blockerCodes.includes("regime.sizing.volume_below_minimum"), true);
  assert.equal(sizing.blockerCodes.includes("regime.sizing.algorithm_risk_capacity_exceeded"), true);
  assert.equal(sizing.blockerCodes.includes("regime.sizing.global_capacity_unavailable"), true);
});

test("Regime short target orders use reversed stop and target direction", () => {
  const candles = deterministicRegimeCandles();
  const market = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: candles,
    allCandles: candles,
    oneMinuteCandles: candles,
  }, liquidRegimeSettings())!;
  const result = regimeResult({
    signal: "Sell",
    tradeAllowed: true,
    aggregateSignal: "sell",
    scores: { buy: 0.1, sell: 0.85, hold: 0.05 },
    confirmedState: {
      rawRegime: "strong_downtrend",
      confirmedRegime: "strong_downtrend",
      rawConfidence: 0.8,
      confirmedConfidence: 0.8,
      candidateRegime: null,
      candidateCount: 0,
      dwellBars: 5,
      heldPreviousRegime: false,
      transitionReason: "test",
      timestamp: "2026-01-05T15:30:00.000Z",
    },
  });
  const target = buildRegimeTargetOrder(result, market, "SPY", regimeTradingSettingsFixture(), undefined, {
    marketValue: 0,
    availableBuyingPower: 100000,
    remainingAlgorithmRiskDollars: 10000,
    globalRiskCapacityQuantity: 1000,
  }, {
    shortTradingEnabled: true,
    accountShortPermission: true,
    assetShortable: true,
    borrowAvailable: true,
    buyingPowerAvailable: true,
  });

  assert.equal(target.signalDirection, "Sell");
  assert.equal(target.positionEffect, "enter_short");
  assert.equal(target.stopPrice! > target.triggerPrice!, true);
  assert.equal(target.targetPrice! < target.triggerPrice!, true);
});

test("Regime Phase 16 integration flows cover trend range breakout choppy event and regime-change exits", () => {
  const bullishMarket = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: deterministicRegimeCandles(),
    allCandles: deterministicRegimeCandles(),
    oneMinuteCandles: deterministicRegimeCandles(),
  }, liquidRegimeSettings())!;
  const bearishMarket = buildRegimeMarketContext({
    symbol: "SPY",
    primaryCandles: deterministicBearishRegimeCandles(),
    allCandles: deterministicBearishRegimeCandles(),
    oneMinuteCandles: deterministicBearishRegimeCandles(),
  }, liquidRegimeSettings())!;
  const relaxed = relaxedRegimeBacktestSettings();
  const longIntent = buildRegimeTargetOrder(regimeResult({ signal: "Buy", tradeAllowed: true, confirmedState: confirmedStateFixture("strong_uptrend", bullishMarket.latest.timestamp) }), bullishMarket, "SPY", relaxed, undefined, {
    shares: 0,
    marketValue: 0,
    availableBuyingPower: 100000,
    remainingAlgorithmRiskDollars: 10000,
    globalRiskCapacityQuantity: 1000,
  });
  const exitLong = buildRegimeTargetOrder(
    regimeResult({ signal: "Sell", tradeAllowed: true, aggregateSignal: "sell", scores: { buy: 0.08, sell: 0.86, hold: 0.06 }, confirmedState: confirmedStateFixture("strong_downtrend", bearishMarket.latest.timestamp) }),
    bearishMarket,
    "SPY",
    relaxed,
    undefined,
    { shares: 12, marketValue: 1200, availableBuyingPower: 100000, remainingAlgorithmRiskDollars: 10000, globalRiskCapacityQuantity: 1000 },
  );
  const enterShort = buildRegimeTargetOrder(
    regimeResult({ signal: "Sell", tradeAllowed: true, aggregateSignal: "sell", scores: { buy: 0.08, sell: 0.86, hold: 0.06 }, confirmedState: confirmedStateFixture("strong_downtrend", bearishMarket.latest.timestamp) }),
    bearishMarket,
    "SPY",
    relaxed,
    undefined,
    { shares: 0, marketValue: 0, availableBuyingPower: 100000, remainingAlgorithmRiskDollars: 10000, globalRiskCapacityQuantity: 1000 },
    shortEnabledOptions(),
  );

  assert.equal(longIntent.positionEffect, "enter_long");
  assert.equal(longIntent.eligible, true);
  assert.equal(exitLong.positionEffect, "exit_long");
  assert.equal(enterShort.positionEffect, "enter_short");

  const rangeRouting = routeRegimeStrategies("range_bound", bullishMarket);
  assert.equal(rangeRouting.selectedStrategyIds.includes("rsi_mean_reversion"), true);
  assert.equal(rangeRouting.selectedStrategyIds.includes("bollinger_band_mean_reversion"), true);
  assert.equal(rangeRouting.selectedStrategyIds.includes("vwap_mean_reversion"), true);
  const breakoutRouting = routeRegimeStrategies("opening_breakout", bullishMarket);
  assert.equal(breakoutRouting.selectedStrategyIds.includes("opening_range_breakout"), true);
  assert.equal(breakoutRouting.selectedStrategyIds.includes("volatility_breakout"), true);
  assert.equal(breakoutRouting.contextResults.some((context) => context.strategyId === "volume_confirmation"), true);

  const choppy = resolveEffectiveRegimeSettings({
    result: regimeResult({
      confirmedState: {
        rawRegime: "choppy_mixed",
        confirmedRegime: "choppy_mixed",
        rawConfidence: 0.58,
        confirmedConfidence: 0.58,
        candidateRegime: null,
        candidateCount: 0,
        dwellBars: 2,
        heldPreviousRegime: false,
        transitionReason: "choppy fixture",
        timestamp: bullishMarket.latest.timestamp,
      },
    }),
    settings: relaxed,
  });
  assert.equal(choppy.effectiveMaximumTrades, 1);
  assert.equal(choppy.effectiveRiskPercent < relaxed.baseRiskPercent, true);

  const eventNoEntry = buildRegimeTargetOrder(
    regimeResult({
      signal: "Buy",
      tradeAllowed: true,
      confirmedState: {
        rawRegime: "event_risk",
        confirmedRegime: "event_risk",
        rawConfidence: 0.8,
        confirmedConfidence: 0.8,
        candidateRegime: null,
        candidateCount: 0,
        dwellBars: 1,
        heldPreviousRegime: false,
        transitionReason: "event blackout",
        timestamp: bullishMarket.latest.timestamp,
      },
    }),
    bullishMarket,
    "SPY",
    relaxed,
    undefined,
    { shares: 0, marketValue: 0, availableBuyingPower: 100000, remainingAlgorithmRiskDollars: 10000, globalRiskCapacityQuantity: 1000 },
  );
  assert.equal(eventNoEntry.eligible, false);
  assert.equal(eventNoEntry.positionEffect, "enter_long");
  assert.equal(eventNoEntry.quantity, 0);

  const invalidation = manageRegimeOpenPosition({
    signalDirection: "Hold",
    currentPosition: 10,
    tradeAllowed: false,
    confirmedRegime: "strong_downtrend",
    entryRegime: "strong_uptrend",
    protectiveStopPrice: 99,
    latestPrice: 101,
  });
  assert.equal(invalidation.action, "exit_long");
  assert.equal("protectiveStopPrice" in invalidation, false);
});

test("Regime owns dedicated trade-management inventory", () => {
  assert.deepEqual(directoryFiles("trade-management"), [
    "entry-policy.ts",
    "exit-policy.ts",
    "position-reconciliation.ts",
    "profit-management.ts",
    "regime-transition-exit.ts",
    "stop-management.ts",
    "time-exit.ts",
  ]);
  assert.equal(minutesBetween("2026-01-05T15:00:00.000Z", "2026-01-05T15:07:30.000Z"), 7.5);
  assert.deepEqual(
    summarizeRegimeTradeHistory([
      { id: "1", symbol: "SPY", side: "Buy", quantity: 10, price: 100, submittedAt: "2026-01-05T15:00:00.000Z" },
      { id: "2", symbol: "SPY", side: "Sell", quantity: 4, price: 101, submittedAt: "2026-01-05T15:01:00.000Z" },
      { id: "3", symbol: "QQQ", side: "Buy", quantity: 99, price: 400, submittedAt: "2026-01-05T15:02:00.000Z" },
    ], "SPY"),
    { tradeCount: 2, netQuantity: 6 },
  );
});

test("Regime trade management exits do not depend on new-entry permission", () => {
  assert.deepEqual(
    manageRegimeOpenPosition({
      signalDirection: "Hold",
      currentPosition: 10,
      tradeAllowed: false,
      latestPrice: 98,
      protectiveStopPrice: 99,
    }),
    { action: "exit_long", reasonCodes: ["regime.trade_management.protective_stop_exit"] },
  );
  assert.deepEqual(
    manageRegimeOpenPosition({
      signalDirection: "Hold",
      currentPosition: 10,
      tradeAllowed: false,
      latestPrice: 102,
      profitTargetPrice: 101,
    }),
    { action: "exit_long", reasonCodes: ["regime.trade_management.profit_target_exit"] },
  );
  assert.deepEqual(
    manageRegimeOpenPosition({
      signalDirection: "Hold",
      currentPosition: 10,
      tradeAllowed: false,
      latestPrice: 100,
      entryTimestamp: "2026-01-05T15:00:00.000Z",
      now: "2026-01-05T16:01:00.000Z",
      maximumHoldingMinutes: 60,
    }),
    { action: "exit_long", reasonCodes: ["regime.trade_management.maximum_holding_time_exit"] },
  );
  assert.deepEqual(
    manageRegimeOpenPosition({
      signalDirection: "Hold",
      currentPosition: 10,
      tradeAllowed: false,
      endOfDayExitRequired: true,
    }),
    { action: "exit_long", reasonCodes: ["regime.trade_management.end_of_day_exit"] },
  );
});

test("Regime trade management handles order lifecycle and event reductions", () => {
  assert.deepEqual(
    manageRegimeOpenPosition({
      signalDirection: "Hold",
      currentPosition: 10,
      tradeAllowed: false,
      orderStatus: "pending",
      pendingOrderSubmittedAt: "2026-01-05T15:00:00.000Z",
      now: "2026-01-05T15:10:00.000Z",
      stalePendingOrderMinutes: 5,
    }),
    { action: "cancel_order", reasonCodes: ["regime.trade_management.stale_pending_order_cancel"] },
  );
  assert.deepEqual(
    manageRegimeOpenPosition({
      signalDirection: "Hold",
      currentPosition: 10,
      tradeAllowed: false,
      orderStatus: "rejected",
    }),
    { action: "none", reasonCodes: ["regime.trade_management.rejected_order_no_position_change"] },
  );
  assert.deepEqual(
    manageRegimeOpenPosition({
      signalDirection: "Hold",
      currentPosition: 10,
      tradeAllowed: false,
      eventRiskReduction: true,
    }),
    { action: "reduce_long", exitQuantityPercent: 0.5, reasonCodes: ["regime.trade_management.event_risk_reduction"] },
  );
  assert.deepEqual(
    manageRegimeOpenPosition({
      signalDirection: "Hold",
      currentPosition: -10,
      tradeAllowed: false,
      gapThroughStop: true,
      latestPrice: 110,
    }),
    { action: "cover_short", reasonCodes: ["regime.trade_management.gap_through_stop_exit"] },
  );
});

test("Regime dedicated backtest records point-in-time decisions and next-bar execution", () => {
  const candles = deterministicRegimeCandles();
  const result = runRegimeBacktest({
    symbol: "SPY",
    candles,
    settings: relaxedRegimeBacktestSettings(),
    globalGate: { maximumApprovedQuantity: 10 },
  });

  assert.equal(result.algorithmId, "regime");
  assert.equal(result.engineVersion, "regime_backtest_v2");
  assert.equal(result.decisions.length, candles.length);
  assert.equal(result.trades.length >= 1, true);
  assert.equal(result.decisions.some((decision) => decision.selectedStrategies.length > 0), true);
  assert.equal(result.decisions.some((decision) => decision.contextResults.length > 0), true);
  assert.equal(result.decisions.some((decision) => decision.safetyResults.length > 0), true);
  assert.equal(result.decisions.some((decision) => decision.familyScores.length > 0), true);
  assert.match(result.diagnostics.join(" "), /t\+1/);
  assert.equal(result.trades[0].entryAt > result.trades[0].entryDecisionTimestamp, true);
});

test("Regime backtest stays isolated from WCA evidence and exposes required comparisons", () => {
  const result = runRegimeBacktest({
    symbol: "SPY",
    candles: deterministicRegimeCandles(),
    settings: relaxedRegimeBacktestSettings(),
    globalGate: { maximumApprovedQuantity: 10 },
  });

  assert.doesNotMatch(JSON.stringify(result), /confidenceBacktestResult|WCA/i);
  assert.equal(result.storageKey.startsWith("regime-backtest:"), true);
  assert.match(result.artifactPath, /regime-backtests/);
  assert.deepEqual(
    result.comparisons.map((comparison) => comparison.variantId),
    [
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
    ],
  );
  for (const key of [
    "confirmedRegime",
    "rawRegime",
    "transitionState",
    "strategy",
    "strategyFamily",
    "side",
    "timeOfDay",
    "volatilityState",
    "liquidityState",
    "eventPeriod",
    "dynamicProfile",
    "signalStrengthBucket",
    "winningScoreBucket",
    "edgeBucket",
    "regimeConfidenceBucket",
    "month",
    "year",
    "exitReason",
    "limitingQuantityCap",
  ] as const) {
    assert.ok(Array.isArray(result.reports[key]), `${key} report exists`);
  }
});

test("Regime dedicated backtest inventory exposes owned files capabilities and simulator rules", () => {
  const status = regimeBacktestInventoryStatus();

  assert.deepEqual(REGIME_BACKTEST_FILE_INVENTORY, [
    "engine.ts",
    "execution-simulator.ts",
    "metrics.ts",
    "diagnostics.ts",
    "walk-forward.ts",
    "runner.ts",
    "types.ts",
  ]);
  assert.deepEqual(status.files, REGIME_BACKTEST_FILE_INVENTORY);
  assert.deepEqual(REGIME_BACKTEST_OWNED_CAPABILITIES, [
    "Regime replay",
    "Warm-up handling",
    "Point-in-time classification",
    "Hysteresis replay",
    "Strategy routing",
    "Dynamic-profile reconstruction",
    "Family aggregation",
    "Entry and exit simulation",
    "Costs and slippage",
    "Position ledger",
    "Trade ledger",
    "Regime-segmented performance",
    "Strategy-family attribution",
    "Walk-forward validation",
    "Untouched holdout testing",
    "Daily independent backtests",
  ]);
  assert.deepEqual(status.ownedCapabilities, REGIME_BACKTEST_OWNED_CAPABILITIES);
  assert.equal(status.algorithmId, "regime");
  assert.equal(status.authoritativeEngine, "frontend/src/algorithms/regime/backtest/engine.ts");
  assert.equal(status.isolatedFromWca, true);

  const gate = simulateRegimeGlobalGate(100, 500, 20, {
    maximumApprovedQuantity: 80,
    maximumRiskDollars: 250,
    maximumNotionalDollars: 1200,
  });
  assert.equal(gate.quantity, 50);
  assert.deepEqual(gate.blockers, ["regime.backtest.global_gate_capacity_reduced_quantity"]);

  const entry = simulateRegimeNextBarEntry(
    { signalDirection: "Buy" },
    100,
    { ...deterministicRegimeCandles()[1], open: 100, volume: 1000 },
    {
      spreadPercent: 0.0002,
      slippagePerShare: 0.01,
      feePerShare: 0.0002,
      maximumVolumeParticipationPercent: 0.03,
      orderDelayBars: 1,
      rejectWhenParticipationQuantityZero: true,
    },
  );
  assert.equal(entry.filledQuantity, 30);
  assert.equal(entry.entryPrice, 100.02);
  assert.equal(entry.reason, "regime.backtest.partial_fill");
});

function deterministicRegimeCandles(): MarketCandle[] {
  const start = new Date("2026-01-05T14:30:00.000Z").getTime();
  return Array.from({ length: 70 }, (_, index) => {
    const base = 100 + index * 0.08;
    return {
      provider: "fixture",
      feed: "fixture",
      symbol: "SPY",
      timeframe: "1Min",
      timestamp: new Date(start + index * 60_000).toISOString(),
      open: base - 0.02,
      high: base + 0.1,
      low: base - 0.06,
      close: base + 0.04,
      volume: 120000 + index * 200,
      trade_count: 1000 + index,
      vwap: null,
    };
  });
}

function deterministicBearishRegimeCandles(): MarketCandle[] {
  const start = new Date("2026-01-05T14:30:00.000Z").getTime();
  return Array.from({ length: 70 }, (_, index) => {
    const base = 106 - index * 0.08;
    return {
      provider: "fixture",
      feed: "fixture",
      symbol: "SPY",
      timeframe: "1Min",
      timestamp: new Date(start + index * 60_000).toISOString(),
      open: base + 0.02,
      high: base + 0.06,
      low: base - 0.1,
      close: base - 0.04,
      volume: 120000 + index * 200,
      trade_count: 1000 + index,
      vwap: null,
    };
  });
}

function regimeContextFeedsFixture(timestamp: string) {
  return {
    quote: {
      bid: 105.58,
      ask: 105.6,
      timestamp,
      maxAgeMs: 60_000,
      source: "fixture",
    },
    qqqRelativeStrength: {
      relativeToPrimaryPercent: 0.2,
      state: "risk_on" as const,
      source: "fixture",
    },
    iwmRelativeStrength: {
      relativeToPrimaryPercent: -0.1,
      state: "neutral" as const,
      source: "fixture",
    },
    marketBreadth: {
      advanceDeclineRatio: 1.4,
      percentAboveVwap: 58,
      state: "risk_on" as const,
      source: "fixture",
    },
    vix: {
      value: 14.8,
      changePercent: -1.2,
      state: "risk_on" as const,
      source: "fixture",
    },
    esFutures: {
      changePercent: 0.15,
      trend: "risk_on" as const,
      source: "fixture",
    },
    scheduledEconomicEvent: {
      state: "none" as const,
      eventName: null,
      minutesUntil: null,
      source: "fixture",
    },
    haltLuldCircuitBreaker: {
      haltState: "none" as const,
      circuitBreakerState: "none" as const,
      reason: null,
      source: "fixture",
    },
  };
}

function relaxedRegimeBacktestSettings() {
  return {
    ...regimeTradingSettingsFixture(),
    minimumBuyScore: 0,
    minimumWinningScore: 0,
    minimumSignalEdge: 0,
    minimumDirectionalEdge: 0,
    minimumRegimeConfidence: 0,
    minimumActiveStrategies: 1,
    minimumIndependentFamilies: 1,
    maximumAbstentionRate: 1,
  };
}

function liquidRegimeSettings() {
  return {
    maxSpreadPercent: 0.1,
    slippagePerShare: 0.005,
    minimumOneMinuteVolume: 0,
  };
}

function regimeTradingSettingsFixture() {
  return {
    startingCapital: 25000,
    orderAllocationPercent: 10,
    dailyAllocationPercent: 50,
    riskBudgetPercentOfOrder: 50,
    maxTradesPerDay: 10,
    maximumHoldingMinutes: 120,
    stopLossPercent: 0.35,
    fixedStopDistanceDollars: 1,
    takeProfitR: 1.5,
    slippagePerShare: 0.005,
    useDefaultSizingSettings: true,
    minimumBuyScore: 0.6,
    minimumWinningScore: 0.6,
    minimumSignalEdge: 0.2,
    minimumDirectionalEdge: 0.2,
    minimumRegimeConfidence: 0.65,
    baseRiskPercent: 0.25,
    maxPositionPercent: 50,
    atrStopMultiplier: 2,
    minimumStopDistancePercent: 0.05,
    maxParticipationPercent: 0.3,
    maximumVolumeParticipationPercent: 0.3,
    maxAllowedShares: 0,
    maximumAllowedShares: 0,
    maxDailyLossPercent: 1,
    algorithmDailyLossPercent: 1,
    minimumActiveStrategies: 3,
    minimumIndependentFamilies: 2,
    maximumAbstentionRate: 0.6,
    minimumOneMinuteVolume: 0,
    maxSpreadPercent: 0.1,
    pyramidingEnabled: true,
    shortEntriesEnabled: false,
    mlMode: "shadow" as const,
  };
}

function validRegimeMlArtifact(): RegimeMlArtifact {
  return {
    algorithm_id: "regime",
    model_version: "regime_ml_test_v1",
    feature_schema_version: "regime_ml_features_v1",
    label_version: "regime_labels_v1",
    training_start: "2025-01-01T00:00:00.000Z",
    training_end: "2026-01-01T00:00:00.000Z",
    validation_periods: [{ start: "2025-06-01T00:00:00.000Z", end: "2025-09-01T00:00:00.000Z" }],
    test_period: { start: "2025-09-02T00:00:00.000Z", end: "2025-12-31T00:00:00.000Z" },
    model_type: "multinomial_logistic_regression",
    hyperparameters: { regularization: 1 },
    metrics: {
      macro_f1: 0.4,
      per_regime_precision_recall: {},
      balanced_accuracy: 0.42,
      log_loss: 1.2,
      brier_score: 0.24,
      calibration_error: 0.2,
      confusion_matrix: {},
      transition_detection_delay_bars: 3,
      confirm_only_trading_results: {},
      performance_by_year: {},
      performance_by_volatility_state: {},
    },
    class_distribution: { strong_uptrend: 20, range_bound: 20 },
    calibration_data: [{ predicted: 0.5, observed: 0.45, count: 20 }],
    feature_names: ["buyScore", "sellScore", "directionalEdge"],
    feature_imputation_policy: { buyScore: "none", sellScore: "none", directionalEdge: "none" },
    artifact_hash: "abcdef1234567890",
    created_at: "2026-01-02T00:00:00.000Z",
    promotion_status: "confirm_only",
    trusted: true,
    coefficients: {
      strong_uptrend: { buyScore: 1, directionalEdge: 0.5 },
      range_bound: { sellScore: 0.2 },
    },
    intercepts: {
      strong_uptrend: 0.1,
      range_bound: 0,
    },
    transition_coefficients: {},
    transition_intercept: 0,
  };
}

function directionalResultFixture(): DirectionalStrategyResult {
  return {
    strategyId: "moving_average_trend",
    family: "trend_momentum",
    role: "directional",
    eligible: true,
    signal: "Buy",
    confidence: 0.7,
    quality: 0.8,
    effectiveWeight: 0.11,
    signedContribution: 0.0616,
    timestamp: "2026-01-05T15:30:00.000Z",
    evidence: { latestCandleTimestamp: "2026-01-05T15:30:00.000Z", close: 101 },
    reason: "fixture",
  };
}

function strategyOutput(overrides: Partial<RegimeSelectedStrategy>): RegimeSelectedStrategy {
  return {
    strategy: "test",
    signal: "hold",
    confidence: 0,
    quality: 0,
    base_weight: 1,
    effective_weight: 1,
    effectiveWeight: 1,
    direction: 0,
    reason: "test",
    timestamp: "2026-01-05T15:30:00.000Z",
    evidence: { latestCandleTimestamp: "2026-01-05T15:30:00.000Z" },
    signedContribution: 0,
    role: "directional",
    family: "trend_momentum",
    eligible: true,
    name: "Test",
    contribution: 0,
    selected: true,
    selectorReason: "test",
    ...overrides,
  };
}

function regimeResult(overrides: Partial<RegimeSelectionResult> = {}): RegimeSelectionResult {
  const signal = overrides.signal ?? "Buy";
  const scores = overrides.scores ?? { buy: 0.72, sell: 0.18, hold: 0.1 };
  const aggregateSignal = overrides.aggregateSignal ?? (signal === "Sell" ? "sell" : signal === "Hold" ? "hold" : "buy");
  const signedNetScore = signedRegimeNetScore(scores);
  const winningScore = winningDirectionScore(aggregateSignal, scores);
  return {
    signal,
    aggregateSignal,
    scores,
    rawCondition: "test",
    confirmedCondition: "test",
    confirmationCount: 3,
    conditionHeld: false,
    primaryTrend: signal === "Sell" ? "Strong downtrend" : "Strong uptrend",
    volatility: "Normal volatility",
    opportunity: signal === "Sell" ? "Bearish breakout" : "Trend continuation",
    confidence: 0.8,
    buyScore: scores.buy,
    sellScore: scores.sell,
    holdScore: scores.hold,
    winningScore,
    winningDirectionScore: winningScore,
    signedNetScore,
    secondBestScore: Math.max(scores.buy, scores.sell, scores.hold),
    scoreEdge: 0.3,
    winningDirectionEdge: 0.3,
    normalizedNetScore: signedNetScore,
    tradeAllowed: true,
    tradeBlockers: [],
    activeStrategyCount: 1,
    selectedStrategyCount: 1,
    features: [],
    selectedStrategies: [],
    skippedStrategies: [],
    reasons: [],
    noTradeReasons: [],
    ...overrides,
  };
}

function confirmedStateFixture(regime: NonNullable<RegimeSelectionResult["confirmedState"]>["confirmedRegime"], timestamp = "2026-01-05T15:30:00.000Z") {
  return {
    rawRegime: regime,
    confirmedRegime: regime,
    rawConfidence: 0.8,
    confirmedConfidence: 0.8,
    candidateRegime: null,
    candidateCount: 0,
    dwellBars: 5,
    heldPreviousRegime: false,
    transitionReason: "fixture",
    timestamp,
  };
}

function pricedIntentOptions(overrides: Record<string, unknown> = {}) {
  return {
    expectedEntryPrice: 100,
    protectiveStopPrice: 99,
    targetPrice: 102,
    requestedRiskDollars: 25,
    marketDataTimestamp: "2026-01-05T15:30:00.000Z",
    generatedAt: "2026-01-05T15:30:05.000Z",
    expiresAt: "2026-01-05T15:35:05.000Z",
    ...overrides,
  };
}

function shortEnabledOptions() {
  return {
    shortTradingEnabled: true,
    accountShortPermission: true,
    assetShortable: true,
    borrowAvailable: true,
    buyingPowerAvailable: true,
    shortSaleRestrictionActive: false,
  };
}

function readRegimeModuleText(): string {
  const root = fileURLToPath(new URL("../src/algorithms/regime", import.meta.url));
  const files: string[] = [];
  const visit = (directory: string) => {
    for (const entry of readdirSync(directory)) {
      const path = join(directory, entry);
      if (statSync(path).isDirectory()) {
        visit(path);
      } else if (path.endsWith(".ts")) {
        files.push(path);
      }
    }
  };
  visit(root);
  return files.map((file) => readFileSync(file, "utf8")).join("\n");
}

function readFrontendMainText(): string {
  return readFileSync(fileURLToPath(new URL("../src/main.ts", import.meta.url)), "utf8");
}

function catalogInventory(role: RegimeStrategyDefinition["role"]) {
  return regimeSelectionStrategies
    .filter((strategy) => strategy.role === role)
    .map((strategy) => ({ key: strategy.key ?? null, id: strategy.id, name: strategy.name }));
}

function directoryFiles(relativePath: string): string[] {
  const root = fileURLToPath(new URL(`../src/algorithms/regime/${relativePath}`, import.meta.url));
  return readdirSync(root)
    .filter((entry) => entry.endsWith(".ts"))
    .sort((left, right) => left.localeCompare(right));
}

function readyState(): V2DecisionPanelState {
  return {
    status: "ready",
    updatedAt: "15:30:00",
    configurationHash: "test-config",
    decision: {
      strategyOutputs: [
        {
          strategyId: "multi_timeframe_trend_alignment",
          strategyName: "Multi-Timeframe Trend Alignment",
          strategyVersion: "2.0.0",
          family: "TREND",
          role: "DIRECTIONAL",
          signal: "BUY",
          direction: 1,
          confidence: 0.8,
          active: true,
          eligible: true,
          dataReady: true,
          setupDetected: true,
          regimeFit: 0.7,
          reliability: 0.6,
          reliabilityVersion: "neutral",
          reliabilitySourceWindow: {},
          structuralInvalidationPrice: 99,
          reasonCodes: ["test.buy"],
          explanation: "Synthetic buy setup.",
          features: {},
          requiredInputs: [],
          inputTimestamps: {},
          evaluatedAt: "2026-01-05T15:30:00Z",
          sessionDate: "2026-01-05",
          configurationHash: "strategy-config",
        },
      ],
      contextOutputs: [
        {
          contextId: "relative_strength_qqq_iwm",
          signal: "HOLD",
          direction: 0,
          confidence: 0.5,
          dataReady: true,
          explanation: "Neutral relative strength.",
          features: { primaryRelativeReturn: 0.01 },
          evaluatedAt: "2026-01-05T15:30:00Z",
          sessionDate: "2026-01-05",
          configurationHash: "context-config",
        },
      ],
      regime: {
        regimeId: "adx_atr_regime",
        label: "weak_trend",
        direction: 1,
        volatility: "NORMAL",
        confidence: 0.7,
        features: {
          trendFit: 0.6,
          breakoutFit: 0.5,
          reversalFit: 0.3,
          meanReversionFit: 0.4,
          gapSessionFit: 0.2,
        },
        evaluatedAt: "2026-01-05T15:30:00Z",
        sessionDate: "2026-01-05",
        configurationHash: "regime-config",
      },
      familyEnsemble: {
        decisionId: "ensemble",
        signal: "BUY",
        direction: 1,
        confidence: 0.7,
        rawScore: 0.55,
        finalScore: 0.52,
        buyConfidence: 0.7,
        sellConfidence: 0.1,
        holdConfidence: 0.2,
        supportingFamilies: ["TREND", "BREAKOUT"],
        opposingFamilies: ["REVERSAL"],
        eligibleStrategyCount: 2,
        familyScores: [
          {
            family: "TREND",
            buyScore: 0.7,
            sellScore: 0,
            holdScore: 0.3,
            confidence: 0.7,
            reliability: 0.8,
            explanation: "Trend supports.",
          },
        ],
        strategySignals: [],
        contextAdjustments: [],
        safetyStatus: "PASS",
        reasonCodes: ["ensemble.buy"],
        explanation: "Synthetic ensemble.",
        dataReady: true,
        eligible: true,
        decidedAt: "2026-01-05T15:30:00Z",
        sessionDate: "2026-01-05",
        configurationHash: "ensemble-config",
        engineVersion: "ensemble-v2",
      },
      gateResults: [
        gate("cash_avoid_trading_filter", "Cash / Avoid Trading Filter", "FAIL", true),
        gate("spread", "Spread", "CAUTION", false),
        gate("market_open", "Market Open", "INFO", false),
      ],
      mlResult: {
        mode: "SHADOW",
        effectiveMode: "SHADOW",
        deterministicSignal: "BUY",
        finalSignal: "BUY",
        candidateAccepted: true,
        mlWouldAcceptCandidate: true,
        appliedToOrder: false,
        successProbability: 0.62,
        calibratedProbability: 0.61,
        expectedValueAfterCosts: 0.12,
        uncertainty: 0.2,
        outOfDistributionScore: 0.1,
        featureMissingness: 0,
        modelHealth: { status: "OK", score: 0.9 },
        recommendedRiskCap: 1,
        reasonCodes: [],
        predictedAt: "2026-01-05T15:30:00Z",
        sessionDate: "2026-01-05",
        configurationHash: "ml-config",
      },
      effectivePolicy: {
        mode: "ACTIVE",
        baselineSettings: {
          baseRiskPercent: 1,
          basePositionPercent: 50,
          baseOrderAllocationPercent: 10,
          baseDailyAllocationPercent: 50,
          baseAtrStopMultiplier: 2,
          baseMinimumStopPercent: 0.05,
          baseTargetR: 2,
          baseMaximumHoldingMinutes: 30,
          baseParticipationPercent: 1,
          baseEntryOffsetBps: 0,
          baseSlippagePerShare: 0.02,
          minimumExpectedValue: 0,
          minimumModelProbability: 0.55,
          settingsVersion: "settings-v1",
          configurationHash: "baseline",
          startingCapital: 10000,
          orderAllocationPercent: 10,
          dailyAllocationPercent: 50,
          riskBudgetPercentOfOrder: 1,
          maxTradesPerDay: 10,
          stopLossPercent: 1,
          fixedStopDistanceDollars: 1,
          takeProfitR: 2,
          slippagePerShare: 0.02,
          positionSizingMode: "risk",
        },
        hardRiskLimits: {
          maximumRiskPerTradePercent: 1,
          maximumDailyLossPercent: 3,
          maximumOpenRiskPercent: 3,
          maximumPositionPercent: 50,
          maximumOrderNotionalPercent: 20,
          maximumDailyNotionalPercent: 50,
          maximumShares: 1000,
          maximumVolumeParticipationPercent: 1,
          maximumTradesPerDay: 10,
          maximumConsecutiveLosses: 3,
          maximumSpreadBps: 25,
          allowPyramiding: false,
          newEntryCutoff: "15:45:00",
          configurationHash: "hard",
          maxDailyLossPercent: 3,
          maxOrderNotional: 2000,
          maxPositionNotional: 5000,
          maxShareQuantity: 1000,
          minStopDistanceDollars: 0.01,
          maxSlippagePerShare: 0.05,
        },
        dynamicBounds: {
          minimumRiskMultiplier: 0,
          maximumRiskMultiplier: 1,
          minimumTargetR: 1,
          maximumTargetR: 3,
          minimumHoldingMinutes: 1,
          maximumHoldingMinutes: 120,
          minimumAtrStopMultiplier: 0.5,
          maximumAtrStopMultiplier: 4,
          minConfidence: 0,
          minReliability: 0,
          minRegimeFit: 0,
          maxSpreadPercent: 100,
          maxParticipationPercent: 1,
          minLiquidityShares: 0,
          configurationHash: "bounds",
        },
        accountRiskState: {
          accountId: "paper",
          equity: 10000,
          buyingPower: 10000,
          openPositionNotional: 0,
          realizedPnlToday: 0,
          unrealizedPnlToday: 0,
          estimatedExitCosts: 0,
          dailyNetPnlAfterExitCosts: 0,
          intradayEquityHigh: 10000,
          drawdownFromIntradayHighPercent: 0,
          totalOpenRiskPercent: 0,
          totalSpyNotionalPercent: 0,
          sameDirectionExposurePercent: 0,
          tradesToday: 0,
          observedAt: "2026-01-05T15:30:00Z",
          sessionDate: "2026-01-05",
        },
        maxQuantity: 100,
        maxNotional: 1000,
        riskDollars: 50,
        explanation: "Synthetic policy.",
        effectiveAt: "2026-01-05T15:30:00Z",
        sessionDate: "2026-01-05",
        configurationHash: "policy",
      },
      orderPlan: {
        orderPlanId: "order",
        candidateId: "candidate",
        symbol: "SPY",
        side: "BUY",
        orderType: "LIMIT",
        quantity: 25,
        entryPrice: 100,
        stopPrice: 99,
        targetPrice: 102,
        limitPrice: 100,
        maximumHoldingMinutes: 30,
        strategyInvalidationPrice: 98.5,
        endOfDayExit: true,
        timeInForce: "DAY",
        eligible: true,
        validationErrors: [],
        explanation: "Synthetic order.",
        generatedAt: "2026-01-05T15:30:00Z",
        sessionDate: "2026-01-05",
        configurationHash: "order",
      },
      eligibility: {
        eligible: true,
        orderSubmissionRequired: true,
        submissionSeparated: true,
      },
      explanation: "Synthetic paper decision.",
    },
  };
}

function gate(gateId: string, gateName: string, status: "PASS" | "CAUTION" | "FAIL" | "INFO", blocksTrading: boolean) {
  return {
    gateId,
    gateName,
    status,
    blocksTrading,
    reasonCodes: [`${gateId}.reason`],
    explanation: `${gateName} ${status}`,
    checkedAt: "2026-01-05T15:30:00Z",
    configurationHash: "gate",
  };
}
