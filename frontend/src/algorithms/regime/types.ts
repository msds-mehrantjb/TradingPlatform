import type { MarketCandle, MarketDataSnapshot } from "../../trading/shared/market-data-types.ts";
import type { RegimeContextFeedsSnapshot } from "./market/context-feeds.ts";
import type { RegimeDecisionSnapshot, RegimeMlArtifact, RegimeMlMode, RegimeMlSnapshot } from "./ml/types.ts";

export type RegimeAlgoSignal = "Buy" | "Sell" | "Hold";
export type RegimeStrategySignal = "buy" | "sell" | "hold";
export type RegimeStrategyDirection = -1 | 0 | 1;
export type StrategyRole = "directional" | "confirmation" | "regime_context" | "safety_gate";
export type StrategyFamily =
  | "trend_momentum"
  | "breakout"
  | "mean_reversion"
  | "reversal"
  | "gap_session_event"
  | "confirmation"
  | "regime_context"
  | "safety";
export type RegimeAggregationFamily = "trend" | "breakout" | "mean_reversion" | "reversal" | "vwap" | "gap_event";
export type MarketRegimeId =
  | "strong_uptrend"
  | "weak_uptrend"
  | "strong_downtrend"
  | "weak_downtrend"
  | "range_bound"
  | "sideways_range"
  | "opening_breakout"
  | "intraday_expansion"
  | "high_volatility_trend"
  | "low_volatility_quiet"
  | "failed_breakout_reversal"
  | "choppy_mixed"
  | "gap_session"
  | "event_risk"
  | "liquidity_stress"
  | "extreme_volatility_no_trade"
  | "low_volatility"
  | "normal_volatility"
  | "high_volatility"
  | "trend_continuation"
  | "bullish_breakout"
  | "bearish_breakout"
  | "bullish_reversal_risk"
  | "bearish_reversal_risk"
  | "mean_reversion"
  | "no_trade";
export type RegimePrimaryTrend = "Strong uptrend" | "Weak uptrend" | "Strong downtrend" | "Weak downtrend" | "Sideways / range-bound";
export type RegimeVolatilityState = "Low volatility" | "Normal volatility" | "High volatility";
export type RegimeOpportunityState =
  | "Trend continuation"
  | "Bullish breakout"
  | "Bearish breakout"
  | "Bullish reversal risk"
  | "Bearish reversal risk"
  | "Mean reversion"
  | "No-trade";
export type RegimeDecisionSignal = RegimeAlgoSignal | "No-trade";

export type RegimeAxes = {
  direction: "strong_up" | "weak_up" | "neutral" | "weak_down" | "strong_down";
  volatility: "compressed" | "normal" | "expanded" | "extreme";
  structure: "trend" | "range" | "breakout" | "failed_breakout" | "reversal" | "mixed";
  liquidity: "good" | "acceptable" | "poor" | "unknown";
  session: "opening" | "midday" | "afternoon" | "closing" | "outside_regular";
  eventRisk: "none" | "elevated" | "blackout";
};

export type RawRegimeClassification = {
  axes: RegimeAxes;
  rawRegime: MarketRegimeId;
  confidence: number;
  evidence: Record<string, unknown>;
  missingInputs: string[];
  timestamp: string;
};

export type RegimeHysteresisSettings = {
  confirmationBars: number;
  immediateConfidenceThreshold: number;
  minimumDwellBars: number;
  transitionConfidenceGap: number;
  maximumUnknownBars: number;
};

export type ConfirmedRegimeState = {
  rawRegime: MarketRegimeId;
  confirmedRegime: MarketRegimeId;
  rawConfidence: number;
  confirmedConfidence: number;
  candidateRegime: MarketRegimeId | null;
  candidateCount: number;
  dwellBars: number;
  heldPreviousRegime: boolean;
  transitionReason: string;
  timestamp: string;
};

export type RegimeFeatureStatus = "ok" | "warn" | "block" | "na";

export type RegimeSelectionFeature = {
  name: string;
  value: string;
  status: RegimeFeatureStatus;
};

export type RegimeAdxRegime = "range" | "mixed" | "bullish_trend" | "bearish_trend" | "very_strong_bullish_trend" | "very_strong_bearish_trend";

export type RegimeAdxContext = {
  adx: number;
  plusDi: number;
  minusDi: number;
  slope: number;
  regime: RegimeAdxRegime;
};

export type RegimeAtrRegime = "too_low" | "normal" | "high" | "extreme";

export type RegimeAtrContext = {
  atr1m: number | null;
  atr5m: number | null;
  atrPercent: number;
  recentAverageAtr: number | null;
  relativeAtr: number | null;
  regime: RegimeAtrRegime;
  positionSizeMultiplier: number;
  thresholdAdd: number;
  stopDistance: number;
};

export type RegimeVolumeContext = {
  relativeVolume: number;
  volumeSpike: boolean;
  weakVolume: boolean;
  smallCandle: boolean;
  bullishCandle: boolean;
  bearishCandle: boolean;
  rangePercent: number;
  spreadAcceptable: boolean;
  holdsKeyLevel: boolean;
  breaksResistance: boolean;
  breaksSupport: boolean;
  rejectsResistance: boolean;
};

export type RegimeSpreadLiquidityContext = {
  spreadPercent: number;
  maxSpreadPercent: number;
  spreadTooWide: boolean;
  volumeTooLow: boolean;
  minimumOneMinuteVolume: number;
  relativeVolume: number;
};

export type RegimeTimeOfDayContext = {
  minutes: number;
  label: string;
  weightMultiplier: number;
  newTradesAllowed: boolean;
};

export type RegimeMarketStructure = {
  higherHigh: boolean;
  higherLow: boolean;
  lowerHigh: boolean;
  lowerLow: boolean;
  breakOfStructureUp: boolean;
  breakOfStructureDown: boolean;
  changeOfCharacterUp: boolean;
  changeOfCharacterDown: boolean;
  successfulSupportRetest: boolean;
  failedResistanceRetest: boolean;
  breakRetestSucceeded: boolean;
  breakRetestFailed: boolean;
  swingHigh: number;
  swingLow: number;
  summary: string;
};

export type RegimeMarketContext = {
  candles: MarketCandle[];
  allCandles: MarketCandle[];
  oneMinuteCandles: MarketCandle[];
  fiveMinuteCandles: MarketCandle[];
  closes: number[];
  latest: MarketCandle;
  priorClose: number | null;
  dayOpen: number;
  premarketHigh: number | null;
  premarketLow: number | null;
  vwap: number;
  vwapSlope: number;
  openingRange: { high: number; low: number };
  priorHigh: number;
  priorLow: number;
  averageVolume: number;
  sma20: number | null;
  sma50: number | null;
  rsi: number | null;
  macd: { macd: number; signal: number; histogram: number } | null;
  atr: RegimeAtrContext;
  bands: { middle: number; upper: number; lower: number } | null;
  adx: RegimeAdxContext | null;
  volume: RegimeVolumeContext;
  spreadLiquidity: RegimeSpreadLiquidityContext;
  timeOfDay: RegimeTimeOfDayContext;
  structure: RegimeMarketStructure | null;
  contextFeeds: RegimeContextFeedsSnapshot;
};

export type RegimeRawStrategySignal = {
  signal: RegimeAlgoSignal;
  confidence: number;
  reason: string;
  quality?: number;
  evidence?: Record<string, number | string | boolean | null>;
  invalidReason?: string;
  role?: StrategyRole;
  eligible?: boolean;
  passed?: boolean;
  blockNewEntries?: boolean;
};

export type ContextResult = {
  strategyId: string;
  role: "confirmation" | "regime_context";
  eligible: boolean;
  multiplier: number;
  reason: string;
  signal: RegimeAlgoSignal;
  confidence: number;
};

export type SafetyGateResult = {
  strategyId: string;
  passed: boolean;
  blockNewEntries: boolean;
  reason: string;
};

export type StrategyRoutingResult = {
  confirmedRegime: MarketRegimeId;
  selectedStrategyIds: string[];
  skippedStrategies: Array<{
    strategyId: string;
    reason: string;
  }>;
  contextResults: ContextResult[];
  safetyResults: SafetyGateResult[];
};

export type RegimeStrategyDefinition = {
  id: string;
  name: string;
  role: StrategyRole;
  family: StrategyFamily;
  supportedDirections: Array<"long" | "short">;
  requiredInputs: string[];
  minimumBars: number;
  supportedRegimes: MarketRegimeId[];
  incompatibleRegimes: MarketRegimeId[];
  enabledByDefault: boolean;
  baseWeight: number;
  version: string;
  aliases?: string[];
  key?: string;
  signal: (market: RegimeMarketContext) => RegimeRawStrategySignal;
};

export type DirectionalStrategyResult = {
  strategyId: string;
  family: StrategyFamily;
  role: "directional";
  eligible: boolean;
  signal: RegimeAlgoSignal;
  confidence: number;
  quality: number;
  effectiveWeight: number;
  signedContribution: number;
  timestamp: string;
  evidence: Record<string, number | string | boolean | null>;
  reason: string;
  invalidReason?: string;
};

export type RegimeStrategyResult = {
  strategy: string;
  signal: RegimeStrategySignal;
  confidence: number;
  quality: number;
  base_weight: number;
  effective_weight: number;
  effectiveWeight: number;
  direction: RegimeStrategyDirection;
  reason: string;
  timestamp: string;
  evidence: Record<string, number | string | boolean | null>;
  invalidReason?: string;
  signedContribution: number;
  directionalResult?: DirectionalStrategyResult;
  role: StrategyRole;
  family: StrategyFamily;
  eligible: boolean;
  passed?: boolean;
  blockNewEntries?: boolean;
  key?: string;
  name: string;
  contribution: number;
};

export type RegimeSelectedStrategy = RegimeStrategyResult & {
  selected: boolean;
  selectorReason: string;
  rawConfidence?: number;
  effectiveConfidence?: number;
  compatibilityMultiplier?: number;
  contextMultiplier?: number;
  reliabilityMultiplier?: number;
  correlationPenalty?: number;
};

export type RegimeSelectionScores = {
  buy: number;
  sell: number;
  hold: number;
};

export type RegimeFamilyScore = {
  family: RegimeAggregationFamily;
  buyScore: number;
  sellScore: number;
  activeStrategyCount: number;
};

export type RegimeAggregationResult = {
  finalSignal: RegimeStrategySignal;
  scores: RegimeSelectionScores;
  buyScore: number;
  sellScore: number;
  winningDirection: RegimeStrategySignal;
  winningScore: number;
  secondBestScore: number;
  directionalEdge: number;
  activeStrategyCount: number;
  activeFamilyCount: number;
  abstentionRate: number;
  familyScores: RegimeFamilyScore[];
};

export type RegimeConditionSnapshot = {
  primaryTrend: RegimePrimaryTrend;
  volatility: RegimeVolatilityState;
  opportunity: RegimeOpportunityState;
  confidence: number;
  key: string;
  contextKey: string;
  axes?: RegimeAxes;
  rawRegime?: MarketRegimeId;
  confirmedRegime?: MarketRegimeId;
  rawConfidence?: number;
  confirmedConfidence?: number;
  candidateRegime?: MarketRegimeId | null;
  candidateCount?: number;
  dwellBars?: number;
  heldPreviousRegime?: boolean;
  transitionReason?: string;
  timestamp?: string;
};

export type RegimeHysteresisSnapshot = RegimeConditionSnapshot | null;

export type RegimeClassifierFeatures = {
  ema20: number | null;
  ema50: number | null;
  ema20Slope: number;
  vwap: number;
  adx: number | null;
  atr: number | null;
  atrPercent: number;
  atrPercentile: number | null;
  atrExpanding: boolean;
  realizedVolatility: number;
  rsi: number | null;
  macdHistogram: number | null;
  bullScore: number;
  bearScore: number;
  volumeRatio: number;
  structure: RegimeMarketStructure | null;
  higherHigh: boolean;
  higherLow: boolean;
  lowerHigh: boolean;
  lowerLow: boolean;
  higherHighAndHigherLow: boolean;
  lowerHighAndLowerLow: boolean;
  openingRangeHigh: number;
  openingRangeLow: number;
  recentRangeHigh: number;
  recentRangeLow: number;
  priorDayHigh: number | null;
  priorDayLow: number | null;
  distanceFromVwap: number;
  openingBreakUp: boolean;
  openingBreakDown: boolean;
  priorDayBreakUp: boolean;
  priorDayBreakDown: boolean;
  bearishRejectionCandle: boolean;
  bullishRejectionCandle: boolean;
  priceChoppingAroundVwap: boolean;
  spreadTooWide: boolean;
  volumeTooLow: boolean;
  display: RegimeSelectionFeature[];
};

export type RegimeSelectionResult = {
  signal: RegimeDecisionSignal;
  aggregateSignal: RegimeStrategySignal;
  scores: RegimeSelectionScores;
  rawCondition: string;
  confirmedCondition: string;
  rawClassification?: RawRegimeClassification;
  confirmedState?: ConfirmedRegimeState;
  routing?: StrategyRoutingResult;
  familyScores?: RegimeFamilyScore[];
  confirmationCount: number;
  conditionHeld: boolean;
  primaryTrend: RegimePrimaryTrend;
  volatility: RegimeVolatilityState;
  opportunity: RegimeOpportunityState;
  confidence: number;
  buyScore: number;
  sellScore: number;
  holdScore: number;
  winningScore: number;
  winningDirectionScore: number;
  signedNetScore: number;
  secondBestScore: number;
  scoreEdge: number;
  winningDirectionEdge: number;
  winningDirection: RegimeStrategySignal;
  directionalEdge: number;
  activeFamilyCount: number;
  abstentionRate: number;
  normalizedNetScore: number;
  tradeAllowed: boolean;
  tradeBlockers: string[];
  effectiveSettings?: EffectiveRegimeSettings;
  ml?: RegimeMlSnapshot;
  decisionSnapshot?: RegimeDecisionSnapshot;
  activeStrategyCount: number;
  selectedStrategyCount: number;
  features: RegimeSelectionFeature[];
  selectedStrategies: RegimeSelectedStrategy[];
  skippedStrategies: Array<{ name: string; reason: string }>;
  reasons: string[];
  noTradeReasons: string[];
};

export type RegimeTradingSettings = {
  startingCapital: number;
  orderAllocationPercent: number;
  dailyAllocationPercent: number;
  riskBudgetPercentOfOrder: number;
  maxTradesPerDay: number;
  maximumHoldingMinutes?: number;
  stopLossPercent: number;
  fixedStopDistanceDollars: number;
  takeProfitR: number;
  slippagePerShare: number;
  useDefaultSizingSettings: boolean;
  minimumBuyScore: number;
  minimumWinningScore?: number;
  minimumSignalEdge: number;
  minimumDirectionalEdge?: number;
  minimumRegimeConfidence?: number;
  baseRiskPercent: number;
  maxPositionPercent: number;
  atrStopMultiplier: number;
  minimumStopDistancePercent: number;
  maxParticipationPercent: number;
  maximumVolumeParticipationPercent?: number;
  maxAllowedShares: number;
  maximumAllowedShares?: number;
  maxDailyLossPercent: number;
  algorithmDailyLossPercent?: number;
  minimumActiveStrategies: number;
  minimumIndependentFamilies?: number;
  maximumAbstentionRate?: number;
  minimumOneMinuteVolume: number;
  maxSpreadPercent: number;
  pyramidingEnabled: boolean;
  shortEntriesEnabled?: boolean;
  mlMode?: RegimeMlMode;
};

export type RegimeBaseSettings = {
  startingCapital: number;
  orderAllocationPercent: number;
  dailyAllocationPercent: number;
  baseRiskPercent: number;
  maxPositionPercent: number;
  maxTradesPerDay: number;
  minimumWinningScore: number;
  minimumDirectionalEdge: number;
  minimumRegimeConfidence: number;
  minimumActiveStrategies: number;
  minimumIndependentFamilies: number;
  fixedStopDistanceDollars: number;
  atrStopMultiplier: number;
  minimumStopDistancePercent: number;
  takeProfitR: number;
  maximumHoldingMinutes: number;
  maximumVolumeParticipationPercent: number;
  minimumOneMinuteVolume: number;
  maximumAllowedShares: number;
  algorithmDailyLossPercent: number;
  pyramidingEnabled: boolean;
  shortEntriesEnabled: boolean;
  slippagePerShare: number;
};

export type RegimeProfileModifiers = {
  riskMultiplier: number;
  allocationMultiplier: number;
  positionMultiplier: number;
  liquidityParticipationMultiplier: number;
  signalSizeMultiplier: number;
  atrStopMultiplierAdjustment: number;
  targetRMultiplier: number;
  winningScoreAdjustment: number;
  directionalEdgeAdjustment: number;
  regimeConfidenceAdjustment: number;
  maximumTradesOverride: number | null;
  entryCutoffOverride: string | null;
  pyramidingAllowed: boolean;
  newEntriesAllowed: boolean;
  reasons: string[];
};

export type RegimeProfileModifierBreakdown = {
  profile: RegimeProfileModifiers;
  timeOfDay: RegimeProfileModifiers;
  eventProximity: RegimeProfileModifiers;
  spread: RegimeProfileModifiers;
  quoteFreshness: RegimeProfileModifiers;
  relativeVolume: RegimeProfileModifiers;
  accountDrawdown: RegimeProfileModifiers;
  consecutiveLosses: RegimeProfileModifiers;
  accountExposure: RegimeProfileModifiers;
  regimeStability: RegimeProfileModifiers;
  mlDisagreement: RegimeProfileModifiers;
};

export type EffectiveRegimeSettings = {
  baseSettingsVersion: string;
  profileVersion: string;
  profileId: string;
  confirmedRegime: MarketRegimeId;
  generatedAt: string;
  effectiveRiskPercent: number;
  effectiveOrderAllocationPercent: number;
  effectiveMaxPositionPercent: number;
  effectiveAtrStopMultiplier: number;
  effectiveTakeProfitR: number;
  effectiveMaximumParticipationPercent: number;
  effectiveMinimumWinningScore: number;
  effectiveMinimumDirectionalEdge: number;
  effectiveMinimumRegimeConfidence: number;
  effectiveMaximumTrades: number;
  newEntriesAllowed: boolean;
  pyramidingAllowed: boolean;
  reasons: string[];
};

export type RegimeSizingDefaults = {
  baseRiskPercent: number;
  maxPositionPercent: number;
  fixedStopDistanceDollars: number;
  atrStopMultiplier: number;
  minimumStopDistancePercent: number;
  maxParticipationPercent: number;
  maxAllowedShares: number;
};

export type RegimePositionSnapshot = {
  shares?: number;
  avgPrice?: number;
  marketValue: number;
  availableBuyingPower?: number;
  remainingAlgorithmRiskDollars?: number;
  globalRiskCapacityQuantity?: number | null;
  requireSpreadEstimate?: boolean;
};

export type RegimeDecisionInput = {
  marketData: MarketDataSnapshot;
  settings?: RegimeTradingSettings;
  sizingDefaults?: RegimeSizingDefaults;
  currentPosition?: RegimePositionSnapshot;
  hysteresis?: RegimeHysteresisSnapshot;
  hysteresisSettings?: Partial<RegimeHysteresisSettings>;
  mlMode?: RegimeMlMode;
  mlArtifact?: RegimeMlArtifact | null;
  liveTrading?: boolean;
  baseSettingsVersion?: string;
};

export type RegimeDecisionOutput = {
  result: RegimeSelectionResult;
  hysteresis: RegimeHysteresisSnapshot;
};
