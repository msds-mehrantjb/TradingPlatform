import "./styles.css";

type Timeframe = "1Min" | "3Min" | "5Min" | "15Min" | "1Hour" | "1Day";

type Candle = {
  provider: string;
  feed: string;
  symbol: string;
  timeframe: Timeframe;
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  trade_count: number | null;
  vwap: number | null;
};

type CandleResponse = {
  source: "alpaca" | "cache" | "demo";
  warning?: string;
  candles: Candle[];
};

type MarketForecastPrediction = {
  status: "ready" | "fallback" | "insufficient_data" | "error";
  symbol: string;
  horizonMinutes: number;
  probabilitySuccess: number | null;
  probabilityBuySuccess: number | null;
  probabilitySellSuccess: number | null;
  probabilityStop: number | null;
  probabilityTimeout: number | null;
  outcome: {
    predicted: "target_hit_first" | "stop_hit_first" | "timeout_no_edge";
    probabilities: Record<"target_hit_first" | "stop_hit_first" | "timeout_no_edge", number | null>;
    labels: Record<"target_hit_first" | "stop_hit_first" | "timeout_no_edge", number>;
  };
  decision: {
    action: "buy" | "sell" | "no_trade";
    candidateAction: "buy" | "sell" | "no_trade";
    confidence: number | null;
    edgeGap: number | null;
    minimumConfidence: number;
    minimumEdgeGap: number;
    modelDisagreement?: number | null;
    maximumModelDisagreement?: number;
    spreadAtr?: number;
    maximumSpreadAtr?: number;
    expectedValue: number | null;
    positionSizeMultiplier: number;
    reasons: string[];
  };
  threshold: number;
  minimumEdgeGap: number;
  maximumModelDisagreement?: number;
  expectedValue: number | null;
  buyExpectedValue?: number | null;
  sellExpectedValue?: number | null;
  barriers?: {
    targetDistance: number | null;
    stopDistance: number | null;
    minTargetPct: number;
    minStopPct: number;
    targetAtrMultiplier: number;
    stopAtrMultiplier: number;
    fixedTargetDollars?: number;
    fixedStopDollars?: number;
    atr5m?: number;
  };
  expectedMove: number | null;
  costs: number;
  allowed: boolean;
  model: {
    status: string;
    kind: string;
    message: string;
    brier?: number;
    calibration?: string | null;
    uncertaintyMembers?: number;
    installedLibraries?: string[];
    libraryCandidates?: Record<string, boolean>;
  };
  regime: {
    trend: string;
    volatility: string;
    vwap: string;
    timeOfDay: string;
  };
  marketRegime?: {
    trend: string;
    volatility: string;
    session: string;
    allowedLong: boolean;
    allowedShort: boolean;
    thresholdAdjustment: number;
    positionSizeMultiplier: number;
    notes: string[];
  };
  algorithmSignals?: {
    weightedScores?: {
      buy?: number;
      sell?: number;
      hold?: number;
      buyMinusSell?: number;
      winnerMargin?: number;
    };
    familyScores?: MetaFamilyAggregationScores;
    disagreement?: number;
  };
  uncertainty?: {
    modelCount: number;
    modelDisagreement: number | null;
    maximumModelDisagreement: number;
    members: Array<{ name: string; buy: number; sell: number; timeout: number }>;
  };
  features: Record<string, unknown>;
  topDrivers: string[];
  missingInputs: string[];
  updatedAt: string;
};

type ForecastSafetyDecisionChange = {
  id: string;
  recordedAt: string;
  mode: TradingWindowMode;
  symbol: string;
  action: "block_buy" | "keep_stopped_lot" | "close_stopped_lot" | "keep_opening_grace";
  originalDecision: string;
  finalDecision: string;
  reason: string;
  latestPrice: number;
  latestTimestamp: string | null;
  forecastAction: MarketForecastPrediction["decision"]["action"] | null;
  forecastConfidence: number | null;
  forecastExpectedValue: number | null;
  forecastEdgeGap: number | null;
};

type MarketStatus = {
  status: string;
  isOpen: boolean;
  timestamp?: string;
  nextOpen?: string | null;
  nextClose?: string | null;
  warning?: string;
};

type MacroEvent = {
  id: string;
  category: "cpi" | "jobs";
  title: string;
  referenceMonth: string;
  releaseAt: string;
  daysUntil: number;
  importance: "high" | "medium" | "low";
  source: string;
};

type MacroEventsResponse = {
  source: string;
  updatedAt: string;
  events: MacroEvent[];
};

type FedEvent = {
  id: string;
  category: "fomc" | "speech";
  title: string;
  detail: string;
  releaseAt: string;
  daysUntil: number;
  source: string;
};

type FedEventsResponse = {
  source: string;
  updatedAt: string;
  events: FedEvent[];
};

type TradingAlert = {
  id: string;
  category: "halt" | "luld";
  symbol: string;
  title: string;
  detail: string;
  publishedAt: string | null;
  source: string;
};

type TradingAlertsResponse = {
  source: string;
  updatedAt: string;
  warning?: string;
  events: TradingAlert[];
};

type CircuitBreakerRule = {
  level: 1 | 2 | 3;
  percent: number;
  label: string;
  action: string;
  referenceValue: number | null;
};

type CircuitBreakersResponse = {
  source: string;
  updatedAt: string;
  referenceIndex: string;
  referenceNote: string;
  referenceSymbol: string;
  referenceClose: number | null;
  referenceDate: string | null;
  rules: CircuitBreakerRule[];
};

type MocImbalanceUpdate = {
  symbol: string;
  auction: string;
  side: "buy" | "sell" | "none";
  imbalanceShares: number;
  pairedShares: number;
  referencePrice: number | null;
  indicativePrice: number | null;
  publishedAt: string;
};

type MocImbalanceResponse = {
  source: string;
  updatedAt: string;
  symbol: string;
  status: "active_window" | "pre_window" | "closed";
  auction: "closing";
  window: {
    start: string;
    end: string;
    updateFrequency: string;
  };
  fields: string[];
  latest: MocImbalanceUpdate | null;
  warning?: string;
};

type VixQuote = {
  last: number;
  open: number | null;
  high: number | null;
  low: number | null;
  date: string | null;
  time: string | null;
};

type VixRiskLevel = {
  label: string;
  min: number;
  max: number | null;
  severity: "low" | "normal" | "elevated" | "high" | "extreme";
  alert: string;
};

type VixRiskResponse = {
  source: string;
  updatedAt: string;
  symbol: "VIX";
  quote: VixQuote | null;
  activeLevel: VixRiskLevel | null;
  levels: VixRiskLevel[];
  warning?: string;
};

type EsQuote = {
  last: number;
  open: number | null;
  high: number | null;
  low: number | null;
  volume: number | null;
  date: string | null;
  time: string | null;
};

type EsDirectionLevel = {
  label: string;
  minPercent: number | null;
  maxPercent: number | null;
  severity: "strong_up" | "up" | "flat" | "down" | "strong_down";
  alert: string;
};

type EsSnapshotResponse = {
  source: string;
  updatedAt: string;
  symbol: "ES";
  session: "premarket" | "regular" | "overnight";
  quote: EsQuote | null;
  changePoints: number | null;
  changePercent: number | null;
  activeLevel: EsDirectionLevel | null;
  levels: EsDirectionLevel[];
  warning?: string;
};

type NewsFeedItem = {
  id: string;
  headline: string;
  summary: string;
  url: string;
  source: string;
  publishedAt: string | null;
  symbols: string[];
};

type NewsFeedSource = {
  name: string;
  kind: string;
  status: string;
  note: string;
};

type NewsFeedResponse = {
  source: string;
  updatedAt: string;
  symbol: string;
  items: NewsFeedItem[];
  sources: NewsFeedSource[];
  warning?: string;
};

type TradeSummary = {
  bias: string;
  confidence: string;
  conclusion: string;
  drivers: string[];
  risks: string[];
  actionPlan: string[];
};

type TradeSummaryResponse = {
  source: string;
  updatedAt: string;
  symbol: string;
  summary: TradeSummary;
  warning?: string;
};

type AlgoSignal = "Buy" | "Sell" | "Hold";
type BacktestResultTimeframe = "1Min" | "5Min" | "1Hour" | "1Day" | "1Week" | "Event";
type AlgoBacktestTimeframe = BacktestResultTimeframe | "Trading";
const FAST_INTRADAY_ALGO_TIMEFRAMES = new Set<AlgoBacktestTimeframe>(["1Min", "5Min"]);

function visibleAlgoBacktestTimeframe(timeframe: AlgoBacktestTimeframe | undefined): AlgoBacktestTimeframe {
  return timeframe && FAST_INTRADAY_ALGO_TIMEFRAMES.has(timeframe) ? timeframe : "1Min";
}

function shouldRefreshVotingTradingRag() {
  return state.algoBacktestTimeframe === "1Min" || state.algoBacktestTimeframe === "5Min" || state.algoBacktestTimeframe === "Trading";
}

type AlgoVote = {
  strategy: string;
  signal: AlgoSignal;
  detail: string;
  status?: StrategyFit["status"];
  score?: number;
};

type ConfidenceStrategyKey = string;

type ConfidenceStrategy = {
  key: ConfidenceStrategyKey;
  slug: string;
  name: string;
  baseWeight: number;
  signal: (market: ConfidenceMarket) => ConfidenceStrategyRawSignal;
};

type MetaStrategyRole = "directional" | "context" | "meta_safety";
type MetaStrategyFamily =
  | "trend"
  | "breakout"
  | "mean_reversion"
  | "reversal"
  | "volume_confirmation"
  | "market_regime"
  | "vwap"
  | "event"
  | "safety";

type MetaStrategyDefinition = {
  name: string;
  role: MetaStrategyRole;
  family: MetaStrategyFamily;
  source: "ensemble" | "confidence" | "alias";
  alias?: string;
};

type ConfidenceStrategySignal = "buy" | "sell" | "hold";

type ConfidenceStrategyDirection = -1 | 0 | 1;

type ConfidenceDecisionLabel = "Strong Buy" | "Buy" | "Hold" | "Sell" | "Strong Sell";

type ConfidenceAdxRegime = "range" | "mixed" | "bullish_trend" | "bearish_trend" | "very_strong_bullish_trend" | "very_strong_bearish_trend";

type ConfidenceAdxContext = {
  adx: number;
  plusDi: number;
  minusDi: number;
  slope: number;
  regime: ConfidenceAdxRegime;
};

type ConfidenceAtrRegime = "too_low" | "normal" | "high" | "extreme";

type ConfidenceAtrContext = {
  atr1m: number | null;
  atr5m: number | null;
  atrPercent: number;
  recentAverageAtr: number | null;
  relativeAtr: number | null;
  regime: ConfidenceAtrRegime;
  positionSizeMultiplier: number;
  thresholdAdd: number;
  stopDistance: number;
};

type ConfidenceSpreadLiquidityContext = {
  spreadPercent: number;
  maxSpreadPercent: number;
  spreadTooWide: boolean;
  volumeTooLow: boolean;
  minimumOneMinuteVolume: number;
  relativeVolume: number;
};

type ConfidenceTimeOfDayContext = {
  minutes: number;
  label: string;
  weightMultiplier: number;
  newTradesAllowed: boolean;
};

type ConfidenceStrategyRawSignal = {
  signal: AlgoSignal;
  confidence: number;
  reason: string;
};

type ConfidenceStrategyResult = {
  strategy: string;
  signal: ConfidenceStrategySignal;
  confidence: number;
  base_weight: number;
  effective_weight: number;
  direction: ConfidenceStrategyDirection;
  reason: string;
  key?: ConfidenceStrategyKey;
  name: string;
  contribution: number;
};

type ConfidenceMarket = {
  candles: Candle[];
  closes: number[];
  latest: Candle;
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
  macd: ReturnType<typeof macdValues>;
  atr: ConfidenceAtrContext;
  bands: ReturnType<typeof bollingerBands>;
  adx: ConfidenceAdxContext | null;
  volume: ConfidenceVolumeContext;
  spreadLiquidity: ConfidenceSpreadLiquidityContext;
  timeOfDay: ConfidenceTimeOfDayContext;
};

type RegimePrimaryTrend = "Strong uptrend" | "Weak uptrend" | "Strong downtrend" | "Weak downtrend" | "Sideways / range-bound";
type RegimeVolatilityState = "Low volatility" | "Normal volatility" | "High volatility";
type RegimeOpportunityState =
  | "Trend continuation"
  | "Bullish breakout"
  | "Bearish breakout"
  | "Bullish reversal risk"
  | "Bearish reversal risk"
  | "Mean reversion"
  | "No-trade";
type RegimeDecisionSignal = AlgoSignal | "No-trade";

type RegimeSelectionFeature = {
  name: string;
  value: string;
  status: "ok" | "warn" | "block" | "na";
};

type RegimeSelectedStrategy = ConfidenceStrategyResult & {
  selected: boolean;
  selectorReason: string;
};

type RegimeSelectionScores = {
  buy: number;
  sell: number;
  hold: number;
};

type RegimeConditionSnapshot = {
  primaryTrend: RegimePrimaryTrend;
  volatility: RegimeVolatilityState;
  opportunity: RegimeOpportunityState;
  confidence: number;
  key: string;
  contextKey: string;
};

type RegimeSelectionResult = {
  signal: RegimeDecisionSignal;
  aggregateSignal: ConfidenceStrategySignal;
  scores: RegimeSelectionScores;
  rawCondition: string;
  confirmedCondition: string;
  confirmationCount: number;
  conditionHeld: boolean;
  primaryTrend: RegimePrimaryTrend;
  volatility: RegimeVolatilityState;
  opportunity: RegimeOpportunityState;
  confidence: number;
  buyScore: number;
  sellScore: number;
  holdScore: number;
  secondBestScore: number;
  scoreEdge: number;
  normalizedNetScore: number;
  tradeAllowed: boolean;
  tradeBlockers: string[];
  activeStrategyCount: number;
  selectedStrategyCount: number;
  features: RegimeSelectionFeature[];
  selectedStrategies: RegimeSelectedStrategy[];
  skippedStrategies: Array<{ name: string; reason: string }>;
  reasons: string[];
  noTradeReasons: string[];
};

type MetaStrategyFeature = {
  name: string;
  role: MetaStrategyRole;
  family: MetaStrategyFamily;
  signal: ConfidenceStrategySignal;
  confidence: number;
  direction: ConfidenceStrategyDirection;
  contribution: number;
  effectiveContribution: number;
  source: string;
  reason: string;
};

type MetaFamilyAggregationScores = {
  trend_buy_score: number;
  trend_sell_score: number;
  breakout_buy_score: number;
  breakout_sell_score: number;
  mean_reversion_buy_score: number;
  mean_reversion_sell_score: number;
  reversal_buy_score: number;
  reversal_sell_score: number;
  confirmation_score: number;
  regime_score: number;
};

type MetaFamilyDisplayScore = {
  label: string;
  value: number;
};

type MetaStrategyResult = {
  signal: AlgoSignal;
  decisionLabel: ConfidenceDecisionLabel;
  buyScore: number;
  sellScore: number;
  holdScore: number;
  netScore: number;
  edge: number;
  contextMultiplier: number;
  aggregateScale: number;
  activeDirectionalCount: number;
  familyAggregation: MetaFamilyAggregationScores;
  familyScores: Record<MetaStrategyFamily, { buy: number; sell: number; hold: number; capped: boolean }>;
  familyDisplayScores: Partial<Record<MetaStrategyFamily, MetaFamilyDisplayScore>>;
  safetyGates: { label: string; status: "pass" | "fail" | "info"; detail: string }[];
  strategies: MetaStrategyFeature[];
  reasons: string[];
};

type MetaStrategyTrainingMetric = {
  accuracy?: number;
  macroF1?: number;
  directionalMacroF1?: number;
  nonHoldRecall?: number;
  balancedAccuracy?: number;
  trustScore?: number;
  [key: string]: unknown;
};

type MetaStrategyTrainingStatus = {
  status: string;
  trusted?: boolean;
  trainedAt?: string;
  symbol?: string;
  sessionDate?: string | null;
  rows?: number;
  trainRows?: number;
  testRows?: number;
  featureCount?: number;
  bestModel?: string;
  bestBaselineMacroF1?: number;
  labelCounts?: Record<string, number>;
  trainingLabelCounts?: Record<string, number>;
  validationLabelCounts?: Record<string, number>;
  labelPolicy?: string;
  message?: string;
  latestPath?: string;
  artifactPath?: string;
  metrics?: {
    baselines?: Record<string, MetaStrategyTrainingMetric>;
    models?: Record<string, MetaStrategyTrainingMetric>;
    unavailableModels?: Record<string, string>;
    bestModel?: string;
    trusted?: boolean;
    bestBaselineMacroF1?: number;
    bestBaselineTrustScore?: number;
    bestBaselineDirectionalMacroF1?: number;
  };
};

type ConfidenceVolumeContext = {
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

type ConfidenceAggregationResult = {
  signal: AlgoSignal;
  decisionLabel: ConfidenceDecisionLabel;
  buyScore: number;
  sellScore: number;
  netScore: number;
  activeWeight: number;
  normalizedNetScore: number;
  activeStrategyCount: number;
  buyWeight: number;
  sellWeight: number;
  buyAgreement: number;
  sellAgreement: number;
  buyAverageConfidence: number;
  sellAverageConfidence: number;
  buyThreshold: number;
  sellThreshold: number;
  strongBuyThreshold: number;
  strongSellThreshold: number;
  strategies: ConfidenceStrategyResult[];
  stopDistance: number;
  positionSizeMultiplier: number;
  positionSize: number;
  sizing: ConfidencePositionSizing;
  hardFilters: { label: string; status: "pass" | "fail" | "info"; detail: string }[];
  logs: string[];
  detail: string;
};

type ConfidenceDecisionSettings = {
  strongBuyThreshold: number;
  buyThreshold: number;
  sellThreshold: number;
  strongSellThreshold: number;
  minimumActiveStrategies: number;
  minimumDirectionalAgreement: number;
  minimumAverageConfidence: number;
};

type ConfidencePositionSizing = {
  signalStrength: number;
  sizeMultiplier: number;
  riskDollars: number;
  stopDistance: number;
  sharesByRisk: number;
  sharesByOrder: number;
  sharesByCapital: number;
  sharesByBuyingPower: number;
  sharesByLiquidity: number;
  finalQuantity: number;
  availableBuyingPower: number;
  accountEquity: number;
  maxPositionDollars: number;
  currentPositionValue: number;
  limitingFactor: string;
  blockedReason: string;
};

type WeightedAlphaKey = "S1" | "S2" | "S3" | "S4" | "S5" | "S6" | "S7" | "S8";

type WeightedAlphaStrategy = {
  key: WeightedAlphaKey;
  name: string;
  family: "breakout" | "trend" | "reversion" | "volatility";
};

type WeightedAlphaResult = WeightedAlphaStrategy & {
  pBuy: number;
  pSell: number;
  pHold: number;
  expectedReturn: number;
  expectedReturnAfterCosts: number;
  signalQuality: number;
  strength: number;
  tradeCount: number;
  sampleSizeScore: number;
  recentExpectancy: number | null;
  recentDrawdown: number;
  drawdownScore: number;
  baseWeight: number;
  adjustedWeight: number;
  finalWeight: number;
  smoothedWeight: number;
  recalculatedWeight: number;
  multipliers: Record<string, number>;
  disabledReason: string;
  detail: string;
};

type WeightedVotingWeightState = {
  weights: Record<WeightedAlphaKey, number>;
  lastUpdatedDate: string;
  source: "default" | "backtest" | "daily";
  seededAt?: string;
};

type WeightedStrategyPerformance = {
  tradeCount: number;
  liveOutcomeCount: number;
  recentOutcomes: number[];
  recentExpectancy: number | null;
  recentDrawdown: number;
  pendingSignal?: {
    side: "Buy" | "Sell";
    entryPrice: number;
    timestamp: string;
    cost: number;
    targetReturn?: number;
    stopReturn?: number;
    horizonBars?: number;
  };
};

type WeightedResolvedBarrierOutcome = {
  outcome: number;
  label: "target" | "stop" | "time";
  barsHeld: number;
};

type WeightedTimeframeDirection = "up" | "down" | "neutral";

type WeightedTimeframeSignal = {
  direction: WeightedTimeframeDirection;
  score: number;
  source: string;
};

type WeightedTimeframeContext = {
  fiveMinute: WeightedTimeframeSignal;
};

type WeightedTripleBarrierResult = {
  passed: boolean;
  label: "positive" | "negative" | "neutral";
  targetReturn: number;
  stopReturn: number;
  horizonBars: number;
  expectedPathReturn: number;
  detail: string;
};

type WeightedInitialBacktestResult = {
  weights: Record<WeightedAlphaKey, number>;
  performance: Record<WeightedAlphaKey, WeightedStrategyPerformance>;
};

type WeightedVoteResult = {
  signal: AlgoSignal;
  rawWinner: AlgoSignal;
  buyScore: number;
  sellScore: number;
  holdScore: number;
  maxScore: number;
  margin: number;
  expectedReturnAfterCosts: number;
  metaLabelProbability: number;
  tripleBarrier: WeightedTripleBarrierResult;
  positionSize: number;
  confidenceMultiplier: number;
  volatilityAdjustment: number;
  gates: { label: string; status: "pass" | "fail" | "info"; detail: string }[];
  strategies: WeightedAlphaResult[];
  data: {
    latest: Candle | null;
    vwap: number | null;
    atr: number | null;
    bollinger: ReturnType<typeof bollingerBands>;
    averageVolume: number;
    relativeStrength: number;
    relativeStrengthSource: string;
    breadth: number;
    breadthSource: string;
    eventRisk: number;
    eventRiskSource: string;
    trendDirection: "up" | "down" | "neutral";
    trendSource: string;
    volatilityLevel: string;
    rangeCondition: string;
    sessionLabel: string;
    timeframes: WeightedTimeframeContext;
    weightSource: "default" | "backtest" | "daily";
    weightSeededAt?: string;
    updatePolicy: string;
  };
};

type BacktestTrade = {
  side: "Long" | "Short";
  entryAt: string;
  exitAt: string;
  entryPrice: number;
  exitPrice: number;
  shares?: number;
  stopPrice?: number;
  targetPrice?: number;
  exitReason?: string;
  rMultiple?: number;
  accountReturnPercent?: number;
  grossPnl?: number;
  expenses?: number;
  pnl: number;
  returnPercent: number;
};

type BacktestResult = {
  timeframe: BacktestResultTimeframe;
  dateLabel: string;
  trades: BacktestTrade[];
  totalTrades?: number;
  displayedTrades?: number;
  totalPnl: number;
  totalReturnPercent: number;
  startingCapital?: number;
  finalEquity?: number;
  maxDrawdown?: number;
  maxDrawdownPercent?: number;
  grossProfit?: number;
  grossLoss?: number;
  totalExpenses?: number;
  profitFactor?: number | null;
  averageWin?: number;
  averageLoss?: number;
  expectancy?: number;
  winners: number;
  losers?: number;
  bars?: number;
  sessions?: number;
  rangeLabel?: string;
  startDate?: string;
  endDate?: string;
  strategyDescription?: string;
  riskConfig?: {
    startingCapital: number;
    riskPerTradePercent: number;
    maxDailyLossPercent: number;
    maxTradesPerDay: number;
    sessionStart: string;
    newTradesUntil: string;
    forceClose: string;
    execution: string;
    stopLossPercent: number;
    takeProfitR: number;
    slippagePerShare: number;
    expenseModel?: {
      description?: string;
      additionalLiquidityCostPerSharePerSide?: number;
      commissionPerSharePerSide?: number;
      secFeeRateOnSellNotional?: number;
      finraTafPerSellShare?: number;
      finraTafMaxPerTrade?: number;
    };
    positionSizing: string;
    entryConfirmationBars?: number;
    signalFadeExit?: string;
    hybridOneHour?: {
      label?: string;
      directionTimeframe?: string;
      executionTimeframe?: string;
      blockedDirectionHours?: string[];
      blockedRegimes?: string[];
      requireDailyTrendAlignment?: boolean;
      takeProfitR?: number;
      atrPeriod?: number;
      atrMultiplier?: number;
      minDirectionalVotes?: number;
    };
    swing?: Record<
      string,
      {
        label?: string;
        maxHoldingBars?: number;
        stopPercent?: number;
        atrPeriod?: number;
        atrMultiplier?: number;
        takeProfitR?: number;
      }
    >;
    openCloseEvents?: {
      label?: string;
      weeklyFilter?: string;
      openingWindow?: string;
      closingWindow?: string;
      openingRangeMinutes?: number;
      takeProfitR?: number;
      stopLossPercent?: number;
      maxTradesPerDay?: number;
      minOpeningWeeklyDirectionalVotes?: number;
      minClosingWeeklyDirectionalVotes?: number;
      enableClosingEvents?: boolean;
      blockedRegimes?: string[];
    };
    allowedEntryHoursByTimeframe?: Record<string, string[]>;
  };
  diagnostics?: Record<string, BacktestDiagnosticRow[]>;
};

type BacktestDiagnosticRow = {
  label: string;
  trades: number;
  pnl: number;
  winRate: number;
  profitFactor?: number | null;
  averageR?: number;
  maxDrawdown?: number;
  maxDrawdownPercent?: number;
};

type MlComparisonRow = {
  timeframe: BacktestResultTimeframe;
  variant: string;
  threshold?: number | null;
  trades: number;
  pnl: number;
  returnPercent: number;
  maxDrawdown: number;
  maxDrawdownPercent: number;
  profitFactor?: number | null;
  winRate: number;
  expectancy: number;
  averageR: number;
  skippedTrades: number;
  skippedWinners: number;
  skippedLosers: number;
  skippedPnl: number;
  pnlChange?: number;
  drawdownChange?: number;
  profitFactorChange?: number | null;
  verdict?: "Improved" | "Worse" | "Mixed" | "Inconclusive";
};

type MlComparisonResult = {
  model: {
    name: string;
    role: string;
    trainingPolicy: string;
    thresholds: number[];
    featureCount: number;
    rows: number;
    positiveRows: number;
    note: string;
  };
  rows: MlComparisonRow[];
  bestByTimeframe: Array<{
    timeframe: BacktestResultTimeframe;
    basePnl: number;
    baseProfitFactor?: number | null;
    baseMaxDrawdown: number;
    bestVariant: string;
    bestPnl: number;
    bestProfitFactor?: number | null;
    bestMaxDrawdown: number;
    verdict: string;
  }>;
};

type CandidateDatasetSummary = {
  version: string;
  rows: number;
  candidateRows: number;
  outcomeRows: number;
  labeledRows: number;
  skippedRows: number;
  files: {
    jsonl?: string;
    csv?: string;
    manifest?: string;
  };
  timeframes: Array<{
    timeframe: BacktestResultTimeframe;
    rows: number;
    candidates: number;
    outcomes: number;
    skipped: number;
    pnl: number;
  }>;
};

type MlDiagnosticsResult = {
  version: string;
  model: {
    name: string;
    trainingRows: number;
    positiveRows: number;
    featureCount: number;
  };
  featureWeights: {
    topPositive: MlFeatureWeight[];
    topNegative: MlFeatureWeight[];
    topMagnitude: MlFeatureWeight[];
  };
  featureEdges: {
    bestExpectancy: MlFeatureEdge[];
    worstExpectancy: MlFeatureEdge[];
    bestWinRate: MlFeatureEdge[];
  };
  timeframeGuidance: Array<{
    timeframe: BacktestResultTimeframe;
    labeledTrades: number;
    basePnl: number;
    baseProfitFactor?: number | null;
    baseExpectancy: number;
    bestVariant: string;
    bestPnl: number;
    bestProfitFactor?: number | null;
    bestMaxDrawdown: number;
    verdict: string;
    action: string;
  }>;
  recommendations: string[];
};

type DailyRefinementResult = {
  version: string;
  goal: string;
  timeframe: BacktestResultTimeframe;
  base: MlComparisonRow & { variant: string };
  thresholds: number[];
  variants: MlComparisonRow[];
  best?: MlComparisonRow | null;
  recommendation: string;
  notes: string[];
};

type EventRefinementResult = {
  version: string;
  goal: string;
  timeframe: BacktestResultTimeframe;
  model: {
    name: string;
    trainingRows: number;
    positiveRows: number;
    featureCount: number;
  };
  base: MlComparisonRow & { variant: string };
  thresholds: number[];
  variants: MlComparisonRow[];
  qualityBest?: MlComparisonRow | null;
  profitPreservingBest?: MlComparisonRow | null;
  recommendation: string;
  notes: string[];
  featureWeights: {
    topPositive: MlFeatureWeight[];
    topNegative: MlFeatureWeight[];
    topMagnitude: MlFeatureWeight[];
  };
  featureEdges: {
    bestExpectancy: MlFeatureEdge[];
    worstExpectancy: MlFeatureEdge[];
    bestWinRate: MlFeatureEdge[];
  };
};

type WeeklyRiskTuningResult = {
  version: string;
  goal: string;
  timeframe: BacktestResultTimeframe;
  base: WeeklyRiskVariant;
  testedVariants: number;
  searchSpace: Record<string, Array<number>>;
  bestProfit?: WeeklyRiskVariant | null;
  bestRiskAdjusted?: WeeklyRiskVariant | null;
  bestLowDrawdown?: WeeklyRiskVariant | null;
  topVariants: WeeklyRiskVariant[];
  recommendation: string;
  notes: string[];
};

type WeeklyRiskVariant = MlComparisonRow & {
  settings: {
    riskPercent: number;
    atrMultiplier: number;
    takeProfitR: number;
    maxHoldingBars: number;
    maxDrawdownStopPercent: number;
  };
  pnlChange?: number;
  drawdownChange?: number;
  returnToDrawdown?: number | null;
  capitalEfficiency?: number;
};

type MlFeatureWeight = {
  feature: string;
  avgWeight: number;
  avgAbsWeight: number;
  years: number;
};

type MlFeatureEdge = {
  feature: string;
  trades: number;
  winRate: number;
  pnl: number;
  expectancy: number;
  averageR: number;
};

type TradingRagResponse = {
  source: string;
  updatedAt: string;
  symbol: string;
  query: string;
  answer: {
    conclusion: string;
    bias: string;
    confidence: string;
    bestHistoricalMatch: string;
    drivers: string[];
    risks: string[];
    actionPlan: string[];
  };
  retrieved: Array<{
    id: string;
    kind: string;
    title: string;
    timeframe?: string | null;
    score: number;
    text: string;
    metrics: Record<string, unknown>;
    sourcePath?: string;
  }>;
  corpus: {
    documentCount: number;
    path?: string;
    createdAt?: string;
    range?: { startDate?: string; endDate?: string };
  };
  warning?: string;
};

type TradingSettings = {
  startingCapital: number;
  orderAllocationPercent: number;
  dailyAllocationPercent: number;
  riskBudgetPercentOfOrder: number;
  maxTradesPerDay: number;
  stopLossPercent: number;
  fixedStopDistanceDollars: number;
  takeProfitR: number;
  slippagePerShare: number;
  useDefaultSizingSettings: boolean;
  minimumBuyScore: number;
  minimumSignalEdge: number;
  baseRiskPercent: number;
  maxPositionPercent: number;
  atrStopMultiplier: number;
  minimumStopDistancePercent: number;
  maxParticipationPercent: number;
  maxAllowedShares: number;
  maxDailyLossPercent: number;
  minimumActiveStrategies: number;
  minimumBuyStrategyCount: number;
  maxSpreadPercent: number;
  minimumOneMinuteVolume: number;
  pyramidingEnabled: boolean;
  positionSizingMode: "allocation";
};

const defaultTradingSettings = (): TradingSettings => ({
  startingCapital: 25000,
  orderAllocationPercent: 10,
  dailyAllocationPercent: 50,
  riskBudgetPercentOfOrder: 50,
  maxTradesPerDay: 10,
  stopLossPercent: 0.35,
  fixedStopDistanceDollars: 1,
  takeProfitR: 1.5,
  slippagePerShare: 0.02,
  useDefaultSizingSettings: true,
  minimumBuyScore: 0.6,
  minimumSignalEdge: 0.2,
  baseRiskPercent: 0.25,
  maxPositionPercent: 50,
  atrStopMultiplier: 2,
  minimumStopDistancePercent: 0.05,
  maxParticipationPercent: 0.3,
  maxAllowedShares: 0,
  maxDailyLossPercent: 1,
  minimumActiveStrategies: 3,
  minimumBuyStrategyCount: 2,
  maxSpreadPercent: 0.03,
  minimumOneMinuteVolume: 0,
  pyramidingEnabled: true,
  positionSizingMode: "allocation",
});

const MIN_TARGET_PROFIT_PER_SHARE = 1;
const MIN_TARGET_PROFIT_PER_TRADE = 4;
const MAX_ORDER_ALLOCATION_PERCENT = 100;
const VOTING_MAX_ORDER_ALLOCATION_PERCENT = 100;
const REGIME_MAX_ORDER_ALLOCATION_PERCENT = 100;
const FORECAST_SAFETY_STRONG_CONFIDENCE = 0.7;
const FORECAST_SAFETY_STOP_OVERRIDE_CONFIDENCE = 0.65;
const FORECAST_SAFETY_OVEREXTENSION_THRESHOLD = 0.003;
const OPENING_GRACE_MINUTES = 15;
const OPENING_GRACE_EMERGENCY_RISK_MULTIPLE = 1.5;
const FORECAST_SAFETY_LOG_STORAGE_KEY = "market-forecast-safety-decision-log-v1";

const weightedTradingSettingsStorageKey = "weighted-voting-trading-settings-v1";
const weightedTargetOrderOverridesStorageKey = "weighted-voting-target-order-overrides-v1";
const confidenceDecisionSettingsStorageKey = "weighted-confidence-decision-settings-v1";
const confidenceTradingSettingsStorageKey = "weighted-confidence-trading-settings-v1";
const confidenceTargetOrderOverridesStorageKey = "weighted-confidence-target-order-overrides-v1";
const regimeTradingSettingsStorageKey = "regime-selection-trading-settings-v1";
const regimeTargetOrderOverridesStorageKey = "regime-selection-target-order-overrides-v1";
const metaTradingSettingsStorageKey = "meta-strategy-trading-settings-v1";
const metaTargetOrderOverridesStorageKey = "meta-strategy-target-order-overrides-v1";
const tradingSettingsStorageKey = "voting-ensemble-trading-settings-v1";
const targetOrderOverridesStorageKey = "voting-ensemble-target-order-overrides-v1";
const uiStateStorageKey = "trading-dashboard-ui-state-v1";
const autoSubmittedOrderKeysStorageKey = "trading-dashboard.auto-submitted-order-keys.v1";

function sanitizeTradingSettings(input: Partial<TradingSettings>, maxOrderAllocationPercent = MAX_ORDER_ALLOCATION_PERCENT): TradingSettings {
  const defaults = defaultTradingSettings();
  const dailyAllocationPercent = finiteOrDefault(input.dailyAllocationPercent, defaults.dailyAllocationPercent);
  const maxTradesPerDay = finiteOrDefault(input.maxTradesPerDay, defaults.maxTradesPerDay);
  const maxParticipationPercent = finiteOrDefault(input.maxParticipationPercent, defaults.maxParticipationPercent);
  return {
    startingCapital: finiteOrDefault(input.startingCapital, defaults.startingCapital),
    orderAllocationPercent: clampNumber(
      finiteOrDefault(input.orderAllocationPercent, defaults.orderAllocationPercent),
      0,
      maxOrderAllocationPercent,
    ),
    dailyAllocationPercent: dailyAllocationPercent === 30 ? defaults.dailyAllocationPercent : dailyAllocationPercent,
    riskBudgetPercentOfOrder: finiteOrDefault(input.riskBudgetPercentOfOrder, defaults.riskBudgetPercentOfOrder),
    maxTradesPerDay: maxTradesPerDay === 3 ? defaults.maxTradesPerDay : maxTradesPerDay,
    stopLossPercent: finiteOrDefault(input.stopLossPercent, defaults.stopLossPercent),
    fixedStopDistanceDollars: Math.max(0, finiteOrDefault(input.fixedStopDistanceDollars, defaults.fixedStopDistanceDollars)),
    takeProfitR: finiteOrDefault(input.takeProfitR, defaults.takeProfitR),
    slippagePerShare: finiteOrDefault(input.slippagePerShare, defaults.slippagePerShare),
    useDefaultSizingSettings: typeof input.useDefaultSizingSettings === "boolean" ? input.useDefaultSizingSettings : defaults.useDefaultSizingSettings,
    minimumBuyScore: finiteOrDefault(input.minimumBuyScore, defaults.minimumBuyScore),
    minimumSignalEdge: finiteOrDefault(input.minimumSignalEdge, defaults.minimumSignalEdge),
    baseRiskPercent: finiteOrDefault(input.baseRiskPercent, defaults.baseRiskPercent),
    maxPositionPercent: finiteOrDefault(input.maxPositionPercent, defaults.maxPositionPercent),
    atrStopMultiplier: finiteOrDefault(input.atrStopMultiplier, defaults.atrStopMultiplier),
    minimumStopDistancePercent: finiteOrDefault(input.minimumStopDistancePercent, defaults.minimumStopDistancePercent),
    maxParticipationPercent: maxParticipationPercent === 0.1 ? defaults.maxParticipationPercent : maxParticipationPercent,
    maxAllowedShares: finiteOrDefault(input.maxAllowedShares, defaults.maxAllowedShares),
    maxDailyLossPercent: finiteOrDefault(input.maxDailyLossPercent, defaults.maxDailyLossPercent),
    minimumActiveStrategies: finiteOrDefault(input.minimumActiveStrategies, defaults.minimumActiveStrategies),
    minimumBuyStrategyCount: finiteOrDefault(input.minimumBuyStrategyCount, defaults.minimumBuyStrategyCount),
    maxSpreadPercent: finiteOrDefault(input.maxSpreadPercent, defaults.maxSpreadPercent),
    minimumOneMinuteVolume: finiteOrDefault(input.minimumOneMinuteVolume, defaults.minimumOneMinuteVolume),
    pyramidingEnabled: typeof input.pyramidingEnabled === "boolean" ? input.pyramidingEnabled : defaults.pyramidingEnabled,
    positionSizingMode: "allocation",
  };
}

function sanitizeLoadedTradingSettings(input: Partial<TradingSettings>, maxOrderAllocationPercent = MAX_ORDER_ALLOCATION_PERCENT): TradingSettings {
  const settings = sanitizeTradingSettings(input, maxOrderAllocationPercent);
  const defaults = defaultTradingSettings();
  const savedMinimumOneMinuteVolume = Number(input.minimumOneMinuteVolume);
  return {
    ...settings,
    minimumOneMinuteVolume: savedMinimumOneMinuteVolume === 50000 ? defaults.minimumOneMinuteVolume : settings.minimumOneMinuteVolume,
  };
}

function finiteOrDefault(value: unknown, fallback: number) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function finitePositiveOrDefault(value: unknown, fallback: number) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : fallback;
}

function fixedStopDistanceDollars(value: unknown) {
  const numeric = Number(value);
  return Number.isFinite(numeric) && numeric > 0 ? numeric : 0;
}

function defaultSizingStopDistance(
  defaults: { fixedStopDistanceDollars?: number; atrStopMultiplier: number; minimumStopDistancePercent: number },
  priceValue: number,
  atrValue: number,
) {
  const fixed = fixedStopDistanceDollars(defaults.fixedStopDistanceDollars);
  if (fixed > 0) {
    return fixed;
  }
  return Math.max(atrValue * Math.max(0.01, defaults.atrStopMultiplier), priceValue * (Math.max(0, defaults.minimumStopDistancePercent) / 100));
}

function tradingSettingsStopDistance(settings: TradingSettings, priceValue: number, atrValue = 0) {
  const fixed = fixedStopDistanceDollars(settings.fixedStopDistanceDollars);
  if (fixed > 0) {
    return fixed;
  }
  if (settings.useDefaultSizingSettings) {
    return Math.max(atrValue * Math.max(0.01, settings.atrStopMultiplier), priceValue * (Math.max(0, settings.minimumStopDistancePercent) / 100));
  }
  return priceValue * (Math.max(0.0001, settings.stopLossPercent) / 100);
}

function clampNumber(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.max(min, Math.min(max, value));
}

function loadWeightedTradingSettings(): TradingSettings {
  try {
    const raw = window.localStorage.getItem(weightedTradingSettingsStorageKey);
    return raw ? sanitizeLoadedTradingSettings(JSON.parse(raw) as Partial<TradingSettings>) : defaultTradingSettings();
  } catch {
    return defaultTradingSettings();
  }
}

function saveWeightedTradingSettings(settings: TradingSettings) {
  window.localStorage.setItem(weightedTradingSettingsStorageKey, JSON.stringify(sanitizeTradingSettings(settings)));
}

function loadConfidenceTradingSettings(): TradingSettings {
  try {
    const raw = window.localStorage.getItem(confidenceTradingSettingsStorageKey);
    return raw ? sanitizeLoadedTradingSettings(JSON.parse(raw) as Partial<TradingSettings>) : defaultTradingSettings();
  } catch {
    return defaultTradingSettings();
  }
}

function saveConfidenceTradingSettings(settings: TradingSettings) {
  window.localStorage.setItem(confidenceTradingSettingsStorageKey, JSON.stringify(sanitizeTradingSettings(settings)));
}

function loadRegimeTradingSettings(): TradingSettings {
  try {
    const raw = window.localStorage.getItem(regimeTradingSettingsStorageKey);
    return raw ? sanitizeLoadedTradingSettings(JSON.parse(raw) as Partial<TradingSettings>, REGIME_MAX_ORDER_ALLOCATION_PERCENT) : defaultTradingSettings();
  } catch {
    return defaultTradingSettings();
  }
}

function saveRegimeTradingSettings(settings: TradingSettings) {
  window.localStorage.setItem(regimeTradingSettingsStorageKey, JSON.stringify(sanitizeTradingSettings(settings, REGIME_MAX_ORDER_ALLOCATION_PERCENT)));
}

function loadMetaTradingSettings(): TradingSettings {
  try {
    const raw = window.localStorage.getItem(metaTradingSettingsStorageKey);
    return raw ? sanitizeLoadedTradingSettings(JSON.parse(raw) as Partial<TradingSettings>, REGIME_MAX_ORDER_ALLOCATION_PERCENT) : defaultTradingSettings();
  } catch {
    return defaultTradingSettings();
  }
}

function saveMetaTradingSettings(settings: TradingSettings) {
  window.localStorage.setItem(metaTradingSettingsStorageKey, JSON.stringify(sanitizeTradingSettings(settings, REGIME_MAX_ORDER_ALLOCATION_PERCENT)));
}

function loadTradingSettings(): TradingSettings {
  try {
    const raw = window.localStorage.getItem(tradingSettingsStorageKey);
    return raw ? sanitizeLoadedTradingSettings(JSON.parse(raw) as Partial<TradingSettings>, VOTING_MAX_ORDER_ALLOCATION_PERCENT) : defaultTradingSettings();
  } catch {
    return defaultTradingSettings();
  }
}

function saveTradingSettings(settings: TradingSettings) {
  window.localStorage.setItem(tradingSettingsStorageKey, JSON.stringify(sanitizeTradingSettings(settings, VOTING_MAX_ORDER_ALLOCATION_PERCENT)));
}

function loadWeightedTargetOrderOverrides(): Partial<TargetOrderSettings> {
  try {
    const raw = window.localStorage.getItem(weightedTargetOrderOverridesStorageKey);
    return raw ? (JSON.parse(raw) as Partial<TargetOrderSettings>) : {};
  } catch {
    return {};
  }
}

function saveWeightedTargetOrderOverrides(overrides: Partial<TargetOrderSettings>) {
  window.localStorage.setItem(weightedTargetOrderOverridesStorageKey, JSON.stringify(overrides));
}

function loadConfidenceTargetOrderOverrides(): Partial<TargetOrderSettings> {
  try {
    const raw = window.localStorage.getItem(confidenceTargetOrderOverridesStorageKey);
    return raw ? (JSON.parse(raw) as Partial<TargetOrderSettings>) : {};
  } catch {
    return {};
  }
}

function saveConfidenceTargetOrderOverrides(overrides: Partial<TargetOrderSettings>) {
  window.localStorage.setItem(confidenceTargetOrderOverridesStorageKey, JSON.stringify(overrides));
}

function loadRegimeTargetOrderOverrides(): Partial<TargetOrderSettings> {
  try {
    const raw = window.localStorage.getItem(regimeTargetOrderOverridesStorageKey);
    return raw ? (JSON.parse(raw) as Partial<TargetOrderSettings>) : {};
  } catch {
    return {};
  }
}

function saveRegimeTargetOrderOverrides(overrides: Partial<TargetOrderSettings>) {
  window.localStorage.setItem(regimeTargetOrderOverridesStorageKey, JSON.stringify(overrides));
}

function loadMetaTargetOrderOverrides(): Partial<TargetOrderSettings> {
  try {
    const raw = window.localStorage.getItem(metaTargetOrderOverridesStorageKey);
    return raw ? (JSON.parse(raw) as Partial<TargetOrderSettings>) : {};
  } catch {
    return {};
  }
}

function saveMetaTargetOrderOverrides(overrides: Partial<TargetOrderSettings>) {
  window.localStorage.setItem(metaTargetOrderOverridesStorageKey, JSON.stringify(overrides));
}

function loadTargetOrderOverrides(): Partial<TargetOrderSettings> {
  try {
    const raw = window.localStorage.getItem(targetOrderOverridesStorageKey);
    return raw ? (JSON.parse(raw) as Partial<TargetOrderSettings>) : {};
  } catch {
    return {};
  }
}

function saveTargetOrderOverrides(overrides: Partial<TargetOrderSettings>) {
  window.localStorage.setItem(targetOrderOverridesStorageKey, JSON.stringify(overrides));
}

function loadAutoSubmittedOrderKeys(): string[] {
  try {
    const raw = window.localStorage.getItem(autoSubmittedOrderKeysStorageKey);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((key) => typeof key === "string").slice(0, 100) : [];
  } catch {
    return [];
  }
}

function saveAutoSubmittedOrderKeys(keys: string[]) {
  window.localStorage.setItem(autoSubmittedOrderKeysStorageKey, JSON.stringify(keys.slice(0, 100)));
}

const defaultConfidenceDecisionSettings: ConfidenceDecisionSettings = {
  strongBuyThreshold: 0.65,
  buyThreshold: 0.35,
  sellThreshold: -0.35,
  strongSellThreshold: -0.65,
  minimumActiveStrategies: 3,
  minimumDirectionalAgreement: 0.5,
  minimumAverageConfidence: 0.45,
};

const legacyConfidenceDecisionDefaults: ConfidenceDecisionSettings = {
  strongBuyThreshold: 0.7,
  buyThreshold: 0.5,
  sellThreshold: -0.5,
  strongSellThreshold: -0.7,
  minimumActiveStrategies: 4,
  minimumDirectionalAgreement: 0.6,
  minimumAverageConfidence: 0.55,
};

function sanitizeConfidenceDecisionSettings(settings: Partial<ConfidenceDecisionSettings>): ConfidenceDecisionSettings {
  const buyThreshold = clampNumber(Number(settings.buyThreshold ?? defaultConfidenceDecisionSettings.buyThreshold), 0.01, 1);
  const strongBuyThreshold = Math.max(buyThreshold, clampNumber(Number(settings.strongBuyThreshold ?? defaultConfidenceDecisionSettings.strongBuyThreshold), 0.01, 1));
  const sellThreshold = -clampNumber(Math.abs(Number(settings.sellThreshold ?? defaultConfidenceDecisionSettings.sellThreshold)), 0.01, 1);
  const strongSellThreshold = -Math.max(Math.abs(sellThreshold), clampNumber(Math.abs(Number(settings.strongSellThreshold ?? defaultConfidenceDecisionSettings.strongSellThreshold)), 0.01, 1));
  return {
    strongBuyThreshold: roundNumber(strongBuyThreshold, 2),
    buyThreshold: roundNumber(buyThreshold, 2),
    sellThreshold: roundNumber(sellThreshold, 2),
    strongSellThreshold: roundNumber(strongSellThreshold, 2),
    minimumActiveStrategies: Math.max(1, Math.round(Number(settings.minimumActiveStrategies ?? defaultConfidenceDecisionSettings.minimumActiveStrategies))),
    minimumDirectionalAgreement: roundNumber(clampNumber(Number(settings.minimumDirectionalAgreement ?? defaultConfidenceDecisionSettings.minimumDirectionalAgreement), 0, 1), 2),
    minimumAverageConfidence: roundNumber(clampNumber(Number(settings.minimumAverageConfidence ?? defaultConfidenceDecisionSettings.minimumAverageConfidence), 0, 1), 2),
  };
}

function loadConfidenceDecisionSettings(): ConfidenceDecisionSettings {
  try {
    const raw = window.localStorage.getItem(confidenceDecisionSettingsStorageKey);
    const settings = sanitizeConfidenceDecisionSettings(raw ? (JSON.parse(raw) as Partial<ConfidenceDecisionSettings>) : defaultConfidenceDecisionSettings);
    return isLegacyConfidenceDecisionDefaults(settings) ? { ...defaultConfidenceDecisionSettings } : settings;
  } catch {
    return { ...defaultConfidenceDecisionSettings };
  }
}

function isLegacyConfidenceDecisionDefaults(settings: ConfidenceDecisionSettings) {
  return (
    settings.strongBuyThreshold === legacyConfidenceDecisionDefaults.strongBuyThreshold &&
    settings.buyThreshold === legacyConfidenceDecisionDefaults.buyThreshold &&
    settings.sellThreshold === legacyConfidenceDecisionDefaults.sellThreshold &&
    settings.strongSellThreshold === legacyConfidenceDecisionDefaults.strongSellThreshold &&
    settings.minimumActiveStrategies === legacyConfidenceDecisionDefaults.minimumActiveStrategies &&
    settings.minimumDirectionalAgreement === legacyConfidenceDecisionDefaults.minimumDirectionalAgreement &&
    settings.minimumAverageConfidence === legacyConfidenceDecisionDefaults.minimumAverageConfidence
  );
}

function saveConfidenceDecisionSettings(settings: ConfidenceDecisionSettings) {
  window.localStorage.setItem(confidenceDecisionSettingsStorageKey, JSON.stringify(sanitizeConfidenceDecisionSettings(settings)));
}

type TargetOrderSettings = {
  symbol: string;
  side: AlgoSignal;
  orderType: string;
  quantity: number;
  triggerPrice: number | null;
  limitPrice: number | null;
  stopPrice: number | null;
  targetPrice: number | null;
  accountBalance: number;
  orderLimitDollars: number;
  dailyLimitDollars: number;
  riskDollars: number;
  orderNotional: number;
  plannedStopRiskDollars: number;
  estimatedSlippage: number;
  timeInForce: string;
  cutoff: string;
  submitMode: SubmitOrderMode;
};

type SubmitOrderMode = "Manual" | "Automatic";
type TradingWindowMode = "ensemble" | "weighted" | "confidence" | "regime" | "meta";

type AlgoTab = "voting" | "weighted" | "confidence" | "regime" | "meta";

type PersistedUiState = {
  algoTab?: AlgoTab;
  tradingWindowMode?: TradingWindowMode;
  tradingEnabled?: boolean;
  selectedSellSetupByMode?: Record<TradingWindowMode, string>;
  sellSetupSelectionLockedByMode?: Record<TradingWindowMode, boolean>;
  feed?: string;
  timeframe?: Timeframe;
  refreshSeconds?: number;
  start?: string;
  end?: string;
  candleWidthPercent?: number;
  showWicks?: boolean;
  showVolume?: boolean;
  showPriceLine?: boolean;
  showVisualConditions?: boolean;
  showLayerBackgrounds?: boolean;
  algoBacktestTimeframe?: AlgoBacktestTimeframe;
  algoIntradayTradesExpanded?: boolean;
  algoVotesExpanded?: boolean;
  weightedVotingExpanded?: boolean;
  weightedDataExpanded?: boolean;
  weightedGatesExpanded?: boolean;
  weightedControlsExpanded?: boolean;
  confidenceRequirementsExpanded?: boolean;
  confidenceTradingSettingsExpanded?: boolean;
  confidenceDefaultSizingExpanded?: boolean;
  confidenceStrategiesExpanded?: boolean;
  regimeTradingSettingsExpanded?: boolean;
  regimeDefaultSizingExpanded?: boolean;
  regimeIndicatorsExpanded?: boolean;
  regimeStrategiesExpanded?: boolean;
  metaStrategiesExpanded?: boolean;
  metaChecksExpanded?: boolean;
  tradingSettingsExpanded?: boolean;
  votingDefaultSizingExpanded?: boolean;
  weightedTradingSettingsExpanded?: boolean;
  weightedDefaultSizingExpanded?: boolean;
  macroExpanded?: boolean;
  fedExpanded?: boolean;
  tradingAlertsExpanded?: boolean;
  circuitBreakersExpanded?: boolean;
  mocImbalanceExpanded?: boolean;
  vixRiskExpanded?: boolean;
  esSnapshotExpanded?: boolean;
};

function loadUiState(): PersistedUiState {
  try {
    const raw = window.localStorage.getItem(uiStateStorageKey);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as PersistedUiState;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function saveUiState() {
  const payload: PersistedUiState = {
    algoTab: state.algoTab,
    tradingWindowMode: state.tradingWindowMode,
    tradingEnabled: state.tradingEnabled,
    selectedSellSetupByMode: state.selectedSellSetupByMode,
    sellSetupSelectionLockedByMode: state.sellSetupSelectionLockedByMode,
    feed: state.feed,
    timeframe: state.timeframe,
    refreshSeconds: state.refreshSeconds,
    start: state.start,
    end: state.end,
    candleWidthPercent: state.candleWidthPercent,
    showWicks: state.showWicks,
    showVolume: state.showVolume,
    showPriceLine: state.showPriceLine,
    showVisualConditions: state.showVisualConditions,
    showLayerBackgrounds: state.showLayerBackgrounds,
    algoBacktestTimeframe: state.algoBacktestTimeframe,
    algoIntradayTradesExpanded: state.algoIntradayTradesExpanded,
    algoVotesExpanded: state.algoVotesExpanded,
    weightedVotingExpanded: state.weightedVotingExpanded,
    weightedDataExpanded: state.weightedDataExpanded,
    weightedGatesExpanded: state.weightedGatesExpanded,
    weightedControlsExpanded: state.weightedControlsExpanded,
    confidenceRequirementsExpanded: state.confidenceRequirementsExpanded,
    confidenceTradingSettingsExpanded: state.confidenceTradingSettingsExpanded,
    confidenceDefaultSizingExpanded: state.confidenceDefaultSizingExpanded,
    confidenceStrategiesExpanded: state.confidenceStrategiesExpanded,
    regimeTradingSettingsExpanded: state.regimeTradingSettingsExpanded,
    regimeDefaultSizingExpanded: state.regimeDefaultSizingExpanded,
    regimeIndicatorsExpanded: state.regimeIndicatorsExpanded,
    regimeStrategiesExpanded: state.regimeStrategiesExpanded,
    metaStrategiesExpanded: state.metaStrategiesExpanded,
    metaChecksExpanded: state.metaChecksExpanded,
    tradingSettingsExpanded: state.tradingSettingsExpanded,
    votingDefaultSizingExpanded: state.votingDefaultSizingExpanded,
    weightedTradingSettingsExpanded: state.weightedTradingSettingsExpanded,
    weightedDefaultSizingExpanded: state.weightedDefaultSizingExpanded,
    macroExpanded: state.macroExpanded,
    fedExpanded: state.fedExpanded,
    tradingAlertsExpanded: state.tradingAlertsExpanded,
    circuitBreakersExpanded: state.circuitBreakersExpanded,
    mocImbalanceExpanded: state.mocImbalanceExpanded,
    vixRiskExpanded: state.vixRiskExpanded,
    esSnapshotExpanded: state.esSnapshotExpanded,
  };
  window.localStorage.setItem(uiStateStorageKey, JSON.stringify(payload));
}

type TradeHistoryRow = {
  id: string;
  side: "Buy" | "Sell";
  symbol: string;
  quantity: number;
  price: number;
  notional: number;
  recordedAt: string;
  closedLotId?: string;
  evidence?: OrderEvidenceSnapshot;
};

type TradeHistoryRolloverState = {
  archivedSessionDates: string[];
  clearedSessionDates: string[];
};

type OrderEvidenceSnapshot = {
  algorithm: TradingWindowMode;
  algorithmLabel: string;
  submitMode: SubmitOrderMode | "Close Position";
  trigger: string;
  capturedAt: string;
  market: {
    status: string;
    symbol: string;
    timeframe: Timeframe;
    feed: string;
    source: string;
    latest?: Pick<Candle, "timestamp" | "open" | "high" | "low" | "close" | "volume">;
  };
  execution: {
    side: "Buy" | "Sell";
    quantity: number;
    price: number;
    notional: number;
    closedLotId?: string;
  };
  positionBefore: PositionSummary | null;
  settings: Partial<TradingSettings>;
  targetOrder: Partial<ManualOrderRecommendation> | null;
  sellSetup: Partial<LotOrderTemplate> | null;
  backtest: {
    label: string;
    timeframe?: string;
    range?: string;
    trades?: number;
    pnl?: number;
    profitFactor?: number | null;
    winRate?: number;
    maxDrawdown?: number;
  } | null;
  mlArtifact: {
    status: string;
    artifactId?: string;
    range?: string;
    best?: string;
  };
  decision: {
    winner?: AlgoSignal;
    voteCounts?: string;
    strategies?: string[];
    weighted?: string;
    confidence?: string;
    gates?: string[];
    failedGates?: string[];
    summary?: string;
  };
};

type OpenOrderLot = {
  id: string;
  symbol: string;
  originalQuantity: number;
  remainingQuantity: number;
  entryPrice: number;
  recordedAt: string;
};

type LotOrderTemplate = {
  action: "Keep" | "Sell";
  quantity: number;
  triggerPrice: number;
  limitPrice: number;
  stopPrice: number;
  targetPrice: number;
  riskDollars: number;
  plannedStopRiskDollars: number;
  estimatedSlippage: number;
  forecastSafetyNote?: string;
  forecastExitReason?: string;
};

type LotOrderOverride = Partial<Omit<LotOrderTemplate, "action">>;

type PositionSummary = {
  shares: number;
  avgPrice: number;
  costBasis: number;
  marketValue: number;
  unrealizedPnl: number;
  realizedPnl: number;
  dailyPnl: number;
  returnPct: number;
};

type DynamicTradingArtifact = {
  status: "Ready";
  artifactId: string;
  configHash: string;
  createdAt: string;
  rangeLabel: string;
  riskConfig: Record<string, unknown>;
  backtests: Record<string, BacktestResult>;
  mlComparison: MlComparisonResult;
};

type TradeLayerGate = {
  layer: string;
  status: "pass" | "fail" | "caution" | "info";
  signal: string;
  detail: string;
};

type ManualOrderRecommendation = {
  eligible: boolean;
  side: AlgoSignal;
  orderType: string;
  symbol: string;
  quantity: number;
  triggerPrice: number | null;
  limitPrice: number | null;
  stopPrice: number | null;
  targetPrice: number | null;
  accountBalance: number;
  orderLimitDollars: number;
  dailyLimitDollars: number;
  riskDollars: number;
  orderNotional: number;
  plannedStopRiskDollars: number;
  estimatedSlippage: number;
  timeInForce: string;
  cutoff: string;
  submitMode: SubmitOrderMode;
  failedGates: string[];
  gates: TradeLayerGate[];
  levels: {
    last: number | null;
    vwap: number | null;
    openingHigh: number | null;
    openingLow: number | null;
    lastTime: string | null;
  };
  summary: string;
};

type MarketLayer = {
  layer: "regime" | "session" | "event";
  label: string;
  directionBias: "long" | "short" | "neutral" | "cash";
  volatility: "low" | "normal" | "high" | "expanding" | "contracting";
  confidence: number;
  reasons: string[];
  signals: Array<{
    name: string;
    value: string;
    status: "ok" | "na";
  }>;
  strategyTags: string[];
  candleWindow: {
    timeframe: Timeframe;
    count: number;
    label: string;
    start?: string | null;
    end?: string | null;
    segments?: Array<{
      start?: string | null;
      end?: string | null;
    }>;
  };
  validUntil?: string | null;
};

type StrategyFit = {
  name: string;
  role?: MetaStrategyRole;
  family?: MetaStrategyFamily | "uncategorized";
  strategy_family?: MetaStrategyFamily | "uncategorized";
  status: "Strong Fit" | "Allowed" | "Watch" | "Avoid";
  score: number;
  matches: string[];
  risks: string[];
};

type MarketContext = {
  symbol: string;
  updatedAt?: string | null;
  regime: MarketLayer;
  session: MarketLayer;
  event: MarketLayer;
  strategies: StrategyFit[];
};

type BacktestRange = {
  startDate: string;
  endDate: string;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";
const BACKTEST_API_CANDIDATES = ["http://127.0.0.1:8020", API_BASE].filter(
  (value, index, values) => values.indexOf(value) === index,
);
const BROWSER_STORAGE_SNAPSHOT_INTERVAL_MS = 5 * 60_000;
const AUTO_DAILY_ALGORITHM_BACKTESTS = false;
const WAKE_CHECK_INTERVAL_MS = 15_000;
const WAKE_GAP_THRESHOLD_MS = 90_000;
const DECISION_SNAPSHOT_MAX_CANDLES = 500;
let browserStorageSnapshotTimer: number | null = null;
let browserStorageSnapshotInFlight = false;
let lastBrowserStorageSnapshotBody = "";
let decisionSnapshotInFlight = false;
let lastDecisionSnapshotKey = "";
let wakeCheckTimer: number | null = null;
let lastWakeCheckAt = Date.now();
let appActivationInFlight = false;
let tradeHistoryRolloverInFlight = false;

function collectBrowserStorageItems(): Record<string, string> {
  const items: Record<string, string> = {};
  for (let index = 0; index < window.localStorage.length; index += 1) {
    const key = window.localStorage.key(index);
    if (!key) {
      continue;
    }
    items[key] = window.localStorage.getItem(key) ?? "";
  }
  return items;
}

function browserStorageSnapshotBody(reason: string) {
  return JSON.stringify({
    origin: window.location.origin,
    userAgent: window.navigator.userAgent,
    reason,
    items: collectBrowserStorageItems(),
  });
}

async function saveBrowserStorageSnapshot(reason: string, keepalive = false) {
  if (browserStorageSnapshotInFlight && !keepalive) {
    return;
  }
  const body = browserStorageSnapshotBody(reason);
  if (body === lastBrowserStorageSnapshotBody && reason !== "page-hide" && reason !== "unload") {
    return;
  }
  browserStorageSnapshotInFlight = true;
  try {
    const response = await fetch(`${API_BASE}/api/browser-state/snapshot`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive,
    });
    if (response.ok) {
      lastBrowserStorageSnapshotBody = body;
    }
  } catch {
    // Browser state snapshots are best-effort; the dashboard should keep running offline.
  } finally {
    browserStorageSnapshotInFlight = false;
  }
}

function compactCandle(candle: Candle) {
  return {
    timestamp: candle.timestamp,
    open: candle.open,
    high: candle.high,
    low: candle.low,
    close: candle.close,
    volume: candle.volume,
    trade_count: candle.trade_count,
    vwap: candle.vwap,
    timeframe: candle.timeframe,
    symbol: candle.symbol,
  };
}

function compactIndicatorsFromMarket(market: ConfidenceMarket | null) {
  if (!market) {
    return null;
  }
  return {
    latest: compactCandle(market.latest),
    priorClose: market.priorClose,
    dayOpen: market.dayOpen,
    premarketHigh: market.premarketHigh,
    premarketLow: market.premarketLow,
    vwap: market.vwap,
    vwapSlope: market.vwapSlope,
    openingRange: market.openingRange,
    priorHigh: market.priorHigh,
    priorLow: market.priorLow,
    averageVolume: market.averageVolume,
    sma20: market.sma20,
    sma50: market.sma50,
    rsi: market.rsi,
    macd: market.macd,
    atr: market.atr,
    bands: market.bands,
    adx: market.adx,
    volume: market.volume,
    spreadLiquidity: market.spreadLiquidity,
    timeOfDay: market.timeOfDay,
  };
}

function paperTradeResultForMode(mode: TradingWindowMode, latest: Candle | null) {
  const latestPrice = latest?.close ?? 0;
  const previousPrice = latest ? latest.open : latestPrice;
  const history = tradeHistoryForMode(mode);
  return {
    mode,
    tradeCount: history.length,
    tradesToday: history.filter(isTradeHistoryRowFromToday).length,
    todayRealizedPnl: todayRealizedPnlForMode(mode),
    todayPnl: latest ? todayPnlForMode(mode, latest.close) : todayRealizedPnlForMode(mode),
    position: latest ? summarizePositionFromTradeHistory(latestPrice, previousPrice, mode) : null,
    latestTrade: history[0] ?? null,
    trades: history.slice(0, 20),
  };
}

function buildDecisionRecorderSnapshot(reason: string) {
  const latest = latestExecutionCandleForMode();
  const market = confidenceMarketSnapshot();
  const voting = votingEnsembleScoreSummary();
  const weighted = calculateWeightedVote();
  const confidence = calculateConfidenceAggregation();
  const regime = calculateRegimeSelection();
  const meta = calculateMetaStrategy();
  const activeMode = state.tradingWindowMode;
  const candles = latestRegularSessionCandles().length ? latestRegularSessionCandles() : state.candles.slice(-DECISION_SNAPSHOT_MAX_CANDLES);
  const weightedOneMinuteCandles = latestRegularSessionCandlesFrom(state.weightedMarketData.timeframeCandles["1Min"] ?? []);
  const candlePayload = {
    chart: state.candles.slice(-DECISION_SNAPSHOT_MAX_CANDLES).map(compactCandle),
    session: candles.slice(-DECISION_SNAPSHOT_MAX_CANDLES).map(compactCandle),
    weightedOneMinute: weightedOneMinuteCandles.slice(-DECISION_SNAPSHOT_MAX_CANDLES).map(compactCandle),
  };
  const targetOrder = targetOrderForMode(activeMode);
  const finalDecision = {
    activeMode,
    activeAlgorithmLabel: algorithmDisplayName(activeMode),
    activeTargetOrder: compactTargetOrder(targetOrder),
    voting: { signal: voting.winner, scores: voting.scores },
    weighted: { signal: weighted.signal, buyScore: weighted.buyScore, sellScore: weighted.sellScore, holdScore: weighted.holdScore, margin: weighted.margin },
    confidence: { signal: confidence.signal, decisionLabel: confidence.decisionLabel, normalizedNetScore: confidence.normalizedNetScore },
    regime: { signal: regime.signal, aggregateSignal: regime.aggregateSignal, confidence: regime.confidence, scoreEdge: regime.scoreEdge },
    meta: { signal: meta.signal, decisionLabel: meta.decisionLabel, netScore: meta.netScore, edge: meta.edge },
  };

  return {
    version: 1,
    reason,
    capturedAt: new Date().toISOString(),
    sessionDate: latest ? localDateKey(latest.timestamp) : localDateKey(new Date()),
    symbol: state.symbol,
    feed: state.feed,
    timeframe: state.timeframe,
    marketStatus: state.marketStatus,
    source: state.source,
    candles: candlePayload,
    indicators: compactIndicatorsFromMarket(market),
    marketContext: state.marketContext,
    strategyOutputs: {
      voting: voting.votes,
      weighted: weighted.strategies,
      confidence: confidence.strategies,
      regime: {
        selectedStrategies: regime.selectedStrategies,
        skippedStrategies: regime.skippedStrategies,
      },
      meta: meta.strategies,
    },
    familyScores: {
      meta: meta.familyAggregation,
      forecast: state.marketForecast?.algorithmSignals?.familyScores ?? null,
    },
    ensembleVotingResult: {
      signal: voting.winner,
      eligibleCount: voting.eligibleVotes.length,
      scores: voting.scores,
      votes: voting.votes,
    },
    metaModelFeatures: {
      familyAggregation: meta.familyAggregation,
      familyScores: meta.familyScores,
      contextMultiplier: meta.contextMultiplier,
      activeDirectionalCount: meta.activeDirectionalCount,
      safetyGates: meta.safetyGates,
      forecastFeatures: state.marketForecast?.features ?? null,
    },
    finalDecision,
    paperTradeResult: {
      ensemble: paperTradeResultForMode("ensemble", latest),
      weighted: paperTradeResultForMode("weighted", latest),
      confidence: paperTradeResultForMode("confidence", latest),
      regime: paperTradeResultForMode("regime", latest),
    },
  };
}

function decisionSnapshotKey(snapshot: ReturnType<typeof buildDecisionRecorderSnapshot>) {
  const latestTimestamp =
    snapshot.candles.weightedOneMinute.at(-1)?.timestamp
    ?? snapshot.candles.session.at(-1)?.timestamp
    ?? snapshot.candles.chart.at(-1)?.timestamp
    ?? "";
  const paperCounts = Object.values(snapshot.paperTradeResult).map((result) => `${result.mode}:${result.tradeCount}:${result.tradesToday}`).join("|");
  return [
    snapshot.symbol,
    snapshot.feed,
    snapshot.timeframe,
    latestTimestamp,
    snapshot.finalDecision.activeMode,
    snapshot.finalDecision.meta.signal,
    snapshot.finalDecision.voting.signal,
    snapshot.finalDecision.weighted.signal,
    snapshot.finalDecision.confidence.signal,
    snapshot.finalDecision.regime.signal,
    paperCounts,
  ].join("::");
}

async function maybeSaveDecisionSnapshot(reason: string) {
  if (state.marketStatus !== "open" || decisionSnapshotInFlight) {
    return;
  }
  const snapshot = buildDecisionRecorderSnapshot(reason);
  const key = decisionSnapshotKey(snapshot);
  if (key === lastDecisionSnapshotKey) {
    return;
  }
  decisionSnapshotInFlight = true;
  try {
    const response = await fetch(`${API_BASE}/api/decision-snapshots`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ snapshot }),
    });
    if (response.ok) {
      lastDecisionSnapshotKey = key;
    }
  } catch {
    // Decision snapshots are a recorder aid; live trading UI should continue if storage is offline.
  } finally {
    decisionSnapshotInFlight = false;
  }
}

function startBrowserStorageDiskSnapshots() {
  void saveBrowserStorageSnapshot("startup");
  if (browserStorageSnapshotTimer !== null) {
    window.clearInterval(browserStorageSnapshotTimer);
  }
  browserStorageSnapshotTimer = window.setInterval(() => {
    void saveBrowserStorageSnapshot("interval");
  }, BROWSER_STORAGE_SNAPSHOT_INTERVAL_MS);
  window.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      void saveBrowserStorageSnapshot("page-hide", true);
    }
  });
  window.addEventListener("beforeunload", () => {
    void saveBrowserStorageSnapshot("unload", true);
  });
}
const DEFAULT_BACKTEST_START_DATE = "2020-07-28";
const DEFAULT_BACKTEST_END_DATE = "2026-06-18";
const TRADE_HISTORY_STORAGE_KEY = "trading-dashboard.trade-history.v1";
const ORDER_CONTROL_MODES_STORAGE_KEY = "trading-dashboard.order-control-modes.v1";
const ORDER_CONTROL_OVERRIDES_STORAGE_KEY = "trading-dashboard.order-control-overrides.v1";
const WEIGHTED_TRADE_HISTORY_STORAGE_KEY = "trading-dashboard.weighted-trade-history.v1";
const CONFIDENCE_TRADE_HISTORY_STORAGE_KEY = "trading-dashboard.confidence-trade-history.v1";
const REGIME_TRADE_HISTORY_STORAGE_KEY = "trading-dashboard.regime-trade-history.v1";
const META_TRADE_HISTORY_STORAGE_KEY = "trading-dashboard.meta-strategy-trade-history.v1";
const WEIGHTED_ORDER_CONTROL_MODES_STORAGE_KEY = "trading-dashboard.weighted-order-control-modes.v1";
const WEIGHTED_ORDER_CONTROL_OVERRIDES_STORAGE_KEY = "trading-dashboard.weighted-order-control-overrides.v1";
const CONFIDENCE_ORDER_CONTROL_MODES_STORAGE_KEY = "trading-dashboard.confidence-order-control-modes.v1";
const CONFIDENCE_ORDER_CONTROL_OVERRIDES_STORAGE_KEY = "trading-dashboard.confidence-order-control-overrides.v1";
const REGIME_ORDER_CONTROL_MODES_STORAGE_KEY = "trading-dashboard.regime-order-control-modes.v1";
const REGIME_ORDER_CONTROL_OVERRIDES_STORAGE_KEY = "trading-dashboard.regime-order-control-overrides.v1";
const META_ORDER_CONTROL_MODES_STORAGE_KEY = "trading-dashboard.meta-strategy-order-control-modes.v1";
const META_ORDER_CONTROL_OVERRIDES_STORAGE_KEY = "trading-dashboard.meta-strategy-order-control-overrides.v1";
const TRADE_HISTORY_ROLLOVER_STORAGE_KEY = "trading-dashboard.trade-history-rollover.v1";
const DEFAULT_SUBMIT_MODE: SubmitOrderMode = "Automatic";
const CONFIDENCE_BACKTEST_RESULT_STORAGE_KEY = "trading-dashboard.confidence-backtest-result.v1";
const DAILY_BACKTEST_COMPLETION_POPUP_STORAGE_KEY = "trading-dashboard.daily-backtest-completion-popup.v1";
const CONFIDENCE_BACKTEST_MAX_SESSIONS = 3;
let backtestRangeCache: BacktestRange | null = null;
let algoBacktestLoadId = 0;
const storedConfidenceBacktest = loadStoredConfidenceBacktest();
let confidenceBacktestCache: { key: string; result: BacktestResult } | null = storedConfidenceBacktest
  ? { key: storedConfidenceBacktest.key, result: storedConfidenceBacktest.result }
  : null;
let confidenceBacktestStatus: "idle" | "waiting" | "running" | "ready" | "error" = storedConfidenceBacktest ? "ready" : "idle";
let confidenceBacktestResult: BacktestResult | null = storedConfidenceBacktest?.result ?? null;
let confidenceBacktestError = "";
let lastConfirmedRegimeCondition: RegimeConditionSnapshot | null = null;
let confidenceBacktestAutoRunKey = "";
let confidenceBacktestDatasetCheckInFlight = false;
let confidenceBacktestNextDatasetCheckAt = 0;
let dailyAlgorithmBacktestsInFlight = false;
let dailyAlgorithmBacktestsNextCheckAt = 0;
let dailyAlgorithmBacktestsLastRunKey = "";
const BAR_CLOSE_REFRESH_MODE = -1;
const BAR_CLOSE_REFRESH_DELAY_MS = 2500;
const refreshOptions = [
  { value: BAR_CLOSE_REFRESH_MODE, label: "1m bar" },
  { value: 0, label: "off" },
] as const;
const fallbackMacroEvents: MacroEvent[] = [
  {
    id: "empsit-2026-06",
    category: "jobs",
    title: "Employment Situation",
    referenceMonth: "June 2026",
    releaseAt: "2026-07-02T08:30:00-04:00",
    daysUntil: 0,
    importance: "high",
    source: "BLS",
  },
  {
    id: "cpi-2026-06",
    category: "cpi",
    title: "Consumer Price Index",
    referenceMonth: "June 2026",
    releaseAt: "2026-07-14T08:30:00-04:00",
    daysUntil: 0,
    importance: "high",
    source: "BLS",
  },
  {
    id: "empsit-2026-07",
    category: "jobs",
    title: "Employment Situation",
    referenceMonth: "July 2026",
    releaseAt: "2026-08-07T08:30:00-04:00",
    daysUntil: 0,
    importance: "high",
    source: "BLS",
  },
  {
    id: "cpi-2026-07",
    category: "cpi",
    title: "Consumer Price Index",
    referenceMonth: "July 2026",
    releaseAt: "2026-08-12T08:30:00-04:00",
    daysUntil: 0,
    importance: "high",
    source: "BLS",
  },
  {
    id: "empsit-2026-08",
    category: "jobs",
    title: "Employment Situation",
    referenceMonth: "August 2026",
    releaseAt: "2026-09-04T08:30:00-04:00",
    daysUntil: 0,
    importance: "high",
    source: "BLS",
  },
  {
    id: "cpi-2026-08",
    category: "cpi",
    title: "Consumer Price Index",
    referenceMonth: "August 2026",
    releaseAt: "2026-09-11T08:30:00-04:00",
    daysUntil: 0,
    importance: "high",
    source: "BLS",
  },
];
const fallbackFedEvents: FedEvent[] = [
  {
    id: "fed-waller-2026-06-22",
    category: "speech",
    title: "Speech - Governor Christopher J. Waller",
    detail: "Welcoming Remarks",
    releaseAt: "2026-06-22T09:00:00-04:00",
    daysUntil: 0,
    source: "Federal Reserve",
  },
  {
    id: "fed-barr-2026-06-22",
    category: "speech",
    title: "Speech - Governor Michael S. Barr",
    detail: "Supervision and Regulation",
    releaseAt: "2026-06-22T12:00:00-04:00",
    daysUntil: 0,
    source: "Federal Reserve",
  },
  {
    id: "fomc-minutes-2026-07-08",
    category: "fomc",
    title: "FOMC Minutes",
    detail: "Meeting of June 16-17",
    releaseAt: "2026-07-08T14:00:00-04:00",
    daysUntil: 0,
    source: "Federal Reserve",
  },
  {
    id: "fomc-meeting-2026-07-29",
    category: "fomc",
    title: "FOMC Meeting",
    detail: "Two-day meeting, July 28-29",
    releaseAt: "2026-07-29T14:00:00-04:00",
    daysUntil: 0,
    source: "Federal Reserve",
  },
  {
    id: "fomc-press-2026-07-29",
    category: "fomc",
    title: "FOMC Press Conference",
    detail: "July FOMC decision press conference",
    releaseAt: "2026-07-29T14:30:00-04:00",
    daysUntil: 0,
    source: "Federal Reserve",
  },
  {
    id: "fomc-meeting-2026-09-16",
    category: "fomc",
    title: "FOMC Meeting",
    detail: "Two-day meeting, September 15-16; SEP meeting",
    releaseAt: "2026-09-16T14:00:00-04:00",
    daysUntil: 0,
    source: "Federal Reserve",
  },
];
const fallbackCircuitBreakers: CircuitBreakersResponse = {
  source: "Built-in NYSE Market-Wide Circuit Breaker rules",
  updatedAt: new Date().toISOString(),
  referenceIndex: "S&P 500 Index",
  referenceNote: "Official MWCB levels use the prior S&P 500 Index close. Restart the backend for cached SPY proxy levels.",
  referenceSymbol: "SPY",
  referenceClose: null,
  referenceDate: null,
  rules: [
    {
      level: 1,
      percent: 7,
      label: "Level 1",
      action: "15-minute market-wide halt if triggered before 3:25 p.m. ET",
      referenceValue: null,
    },
    {
      level: 2,
      percent: 13,
      label: "Level 2",
      action: "15-minute market-wide halt if triggered before 3:25 p.m. ET",
      referenceValue: null,
    },
    {
      level: 3,
      percent: 20,
      label: "Level 3",
      action: "Trading halts for the remainder of the day",
      referenceValue: null,
    },
  ],
};
const fallbackMocImbalance: MocImbalanceResponse = {
  source: "Closing auction imbalance feed",
  updatedAt: new Date().toISOString(),
  symbol: "SPY",
  status: "closed",
  auction: "closing",
  window: {
    start: "15:50 ET",
    end: "16:00 ET",
    updateFrequency: "Every 5 seconds when a live imbalance feed is configured",
  },
  fields: [
    "symbol",
    "auction",
    "side",
    "imbalanceShares",
    "pairedShares",
    "referencePrice",
    "indicativePrice",
    "publishedAt",
  ],
  latest: null,
  warning: "No live MOC/NOII imbalance feed is configured.",
};
const fallbackVixRisk: VixRiskResponse = {
  source: "Built-in VIX risk thresholds",
  updatedAt: new Date().toISOString(),
  symbol: "VIX",
  quote: null,
  activeLevel: null,
  levels: [
    { label: "Calm", min: 0, max: 15, severity: "low", alert: "Complacent volatility regime" },
    { label: "Normal", min: 15, max: 20, severity: "normal", alert: "Routine volatility regime" },
    { label: "Elevated", min: 20, max: 30, severity: "elevated", alert: "Risk is elevated; expect wider ranges" },
    { label: "Stress", min: 30, max: 40, severity: "high", alert: "Volatility stress; reduce size and widen risk controls" },
    { label: "Shock", min: 40, max: null, severity: "extreme", alert: "Volatility shock regime; preserve capital" },
  ],
  warning: "VIX quote unavailable",
};
const fallbackEsSnapshot: EsSnapshotResponse = {
  source: "Built-in ES direction thresholds",
  updatedAt: new Date().toISOString(),
  symbol: "ES",
  session: "premarket",
  quote: null,
  changePoints: null,
  changePercent: null,
  activeLevel: null,
  levels: [
    { label: "Strong Bullish", minPercent: 0.75, maxPercent: null, severity: "strong_up", alert: "Premarket futures are strongly bid" },
    { label: "Bullish", minPercent: 0.25, maxPercent: 0.75, severity: "up", alert: "Premarket futures point higher" },
    { label: "Flat", minPercent: -0.25, maxPercent: 0.25, severity: "flat", alert: "Premarket futures are near unchanged" },
    { label: "Bearish", minPercent: -0.75, maxPercent: -0.25, severity: "down", alert: "Premarket futures point lower" },
    { label: "Strong Bearish", minPercent: null, maxPercent: -0.75, severity: "strong_down", alert: "Premarket futures are under pressure" },
  ],
  warning: "ES futures quote unavailable",
};
const fallbackNewsFeed: NewsFeedResponse = {
  source: "Dashboard fallback",
  updatedAt: new Date().toISOString(),
  symbol: "SPY",
  items: [
    {
      id: "fallback-spy-flows",
      headline: "ETF flows remain active as traders watch broad-market momentum",
      summary: "Fallback headline shown while live news providers are unavailable.",
      url: "",
      source: "Dashboard fallback",
      publishedAt: new Date().toISOString(),
      symbols: ["SPY"],
    },
    {
      id: "fallback-index-futures",
      headline: "Index futures steady ahead of session catalysts",
      summary: "Fallback headline shown while live news providers are unavailable.",
      url: "",
      source: "Dashboard fallback",
      publishedAt: new Date(Date.now() - 30 * 60 * 1000).toISOString(),
      symbols: ["SPY", "SPX"],
    },
  ],
  sources: [
    {
      name: "Alpaca News",
      kind: "websocket/rest",
      status: "unavailable",
      note: "Backend news endpoint unavailable.",
    },
  ],
  warning: "SPY news endpoint unavailable.",
};
const fallbackTradeSummary: TradeSummaryResponse = {
  source: "Local fallback",
  updatedAt: new Date().toISOString(),
  symbol: "SPY",
  summary: {
    bias: "Cautious",
    confidence: "Low",
    conclusion: "Summary is waiting for the local Ollama endpoint and current market feeds.",
    drivers: ["SPY headlines are loading.", "News Feeds are loading."],
    risks: ["Ollama may not be running.", "Use price confirmation before acting."],
    actionPlan: ["Refresh Summary after SPY News and News Feeds load.", "Wait for VWAP/opening range confirmation."],
  },
  warning: "Summary endpoint unavailable.",
};

const weightedAlphaStrategies: WeightedAlphaStrategy[] = [
  { key: "S1", name: "Opening Range Breakout", family: "breakout" },
  { key: "S2", name: "First Pullback After Open", family: "trend" },
  { key: "S3", name: "VWAP Trend Continuation", family: "trend" },
  { key: "S4", name: "VWAP Mean Reversion", family: "reversion" },
  { key: "S5", name: "Failed Breakout Reversal", family: "reversion" },
  { key: "S6", name: "Liquidity Sweep Reversal", family: "reversion" },
  { key: "S7", name: "Bollinger/ATR Reversion", family: "reversion" },
  { key: "S8", name: "Volatility Breakout", family: "volatility" },
];

const confidenceBaseWeights = {
  moving_average_trend: 0.11,
  vwap_position: 0.11,
  trend_pullback: 0.1,
  rsi_mean_reversion: 0.07,
  bollinger_band_mean_reversion: 0.07,
  opening_range_breakout: 0.1,
  intraday_breakout: 0.1,
  macd_momentum: 0.08,
  market_structure: 0.12,
  gap_continuation_fade: 0.06,
  volume_confirmation: 0.08,
} satisfies Record<string, number>;

const confidenceAggregationStrategies: ConfidenceStrategy[] = [
  { key: "C1", slug: "moving_average_trend", name: "Moving Average Trend", baseWeight: confidenceBaseWeights.moving_average_trend, signal: confidenceMovingAverageTrend },
  { key: "C2", slug: "vwap_position", name: "VWAP Position Strategy", baseWeight: confidenceBaseWeights.vwap_position, signal: confidenceVwapPosition },
  { key: "C3", slug: "trend_pullback", name: "Trend Pullback Strategy", baseWeight: confidenceBaseWeights.trend_pullback, signal: confidenceTrendPullback },
  { key: "C4", slug: "rsi_mean_reversion", name: "RSI Mean Reversion", baseWeight: confidenceBaseWeights.rsi_mean_reversion, signal: confidenceRsiMeanReversion },
  { key: "C5", slug: "bollinger_band_mean_reversion", name: "Bollinger Band Mean Reversion", baseWeight: confidenceBaseWeights.bollinger_band_mean_reversion, signal: confidenceBollingerMeanReversion },
  { key: "C6", slug: "opening_range_breakout", name: "Opening Range Breakout", baseWeight: confidenceBaseWeights.opening_range_breakout, signal: confidenceOpeningRangeBreakout },
  { key: "C7", slug: "intraday_breakout", name: "Intraday Breakout Strategy", baseWeight: confidenceBaseWeights.intraday_breakout, signal: confidenceIntradayBreakout },
  { key: "C8", slug: "macd_momentum", name: "MACD Momentum", baseWeight: confidenceBaseWeights.macd_momentum, signal: confidenceMacdMomentum },
  { key: "C9", slug: "market_structure", name: "Market Structure Strategy", baseWeight: confidenceBaseWeights.market_structure, signal: confidenceMarketStructure },
  { key: "C10", slug: "gap_continuation_fade", name: "Gap Continuation / Gap Fade", baseWeight: confidenceBaseWeights.gap_continuation_fade, signal: confidenceGapContinuationFade },
  { key: "C11", slug: "volume_confirmation", name: "Volume Confirmation", baseWeight: confidenceBaseWeights.volume_confirmation, signal: confidenceVolumeConfirmation },
];

const regimeSelectionStrategies: ConfidenceStrategy[] = [
  ...confidenceAggregationStrategies,
  { key: "R1", slug: "vwap_trend_continuation", name: "VWAP Trend Continuation", baseWeight: 0.1, signal: confidenceVwapTrendContinuation },
  { key: "R2", slug: "vwap_mean_reversion", name: "VWAP Mean Reversion", baseWeight: 0.09, signal: confidenceVwapMeanReversion },
  { key: "R3", slug: "failed_breakout_reversal", name: "Failed Breakout Reversal", baseWeight: 0.08, signal: confidenceFailedBreakoutReversal },
  { key: "R4", slug: "liquidity_sweep_reversal", name: "Liquidity Sweep Reversal", baseWeight: 0.08, signal: confidenceLiquiditySweepReversal },
  { key: "R5", slug: "adx_trend_strength", name: "ADX Trend Strength Filter", baseWeight: 0.08, signal: confidenceAdxTrendStrength },
  { key: "R6", slug: "atr_volatility_regime", name: "ATR Volatility Regime", baseWeight: 0.07, signal: confidenceAtrVolatilityRegime },
  { key: "R7", slug: "volatility_breakout", name: "Volatility Breakout", baseWeight: 0.08, signal: confidenceVolatilityBreakout },
  { key: "R8", slug: "cash_avoid_filter", name: "Cash / Avoid Trading Filter", baseWeight: 0.04, signal: confidenceCashAvoidFilter },
];

const metaStrategyCatalog: MetaStrategyDefinition[] = [
  { name: "Multi-Timeframe Trend Alignment", role: "directional", family: "trend", source: "ensemble" },
  { name: "First Pullback After Open", role: "directional", family: "trend", source: "ensemble" },
  { name: "Failed Breakout Strategy", role: "directional", family: "reversal", source: "ensemble" },
  { name: "Liquidity Sweep Reversal", role: "directional", family: "reversal", source: "ensemble" },
  { name: "Bollinger Band Reversion", role: "directional", family: "mean_reversion", source: "alias", alias: "Bollinger Band Mean Reversion" },
  { name: "ATR Overextension Reversion", role: "directional", family: "mean_reversion", source: "ensemble" },
  { name: "Moving Average Trend", role: "directional", family: "trend", source: "confidence" },
  { name: "Trend Pullback Strategy", role: "directional", family: "trend", source: "confidence" },
  { name: "RSI Mean Reversion", role: "directional", family: "mean_reversion", source: "confidence" },
  { name: "Bollinger Band Mean Reversion", role: "directional", family: "mean_reversion", source: "confidence" },
  { name: "Opening Range Breakout", role: "directional", family: "breakout", source: "confidence" },
  { name: "Intraday Breakout Strategy", role: "directional", family: "breakout", source: "confidence" },
  { name: "MACD Momentum", role: "directional", family: "trend", source: "confidence" },
  { name: "Market Structure Strategy", role: "directional", family: "trend", source: "confidence" },
  { name: "Gap Continuation / Gap Fade", role: "directional", family: "event", source: "confidence" },
  { name: "VWAP Trend Continuation", role: "directional", family: "vwap", source: "confidence" },
  { name: "VWAP Mean Reversion", role: "directional", family: "mean_reversion", source: "confidence" },
  { name: "Failed Breakout Reversal", role: "directional", family: "reversal", source: "confidence" },
  { name: "Bollinger/ATR Reversion", role: "directional", family: "mean_reversion", source: "alias", alias: "Bollinger Band Mean Reversion" },
  { name: "Volatility Breakout", role: "directional", family: "breakout", source: "confidence" },
  { name: "Relative Strength vs QQQ/IWM", role: "context", family: "market_regime", source: "ensemble" },
  { name: "Market Breadth Momentum", role: "context", family: "market_regime", source: "ensemble" },
  { name: "Economic Event Reaction Strategy", role: "context", family: "event", source: "ensemble" },
  { name: "VWAP Position Strategy", role: "context", family: "vwap", source: "confidence" },
  { name: "Volume Confirmation", role: "context", family: "volume_confirmation", source: "confidence" },
  { name: "ADX Trend Strength Filter", role: "context", family: "market_regime", source: "confidence" },
  { name: "ATR Volatility Regime", role: "context", family: "market_regime", source: "confidence" },
  { name: "Ensemble Strategy Voting", role: "meta_safety", family: "safety", source: "ensemble" },
  { name: "Cash / Avoid Trading Filter", role: "meta_safety", family: "safety", source: "confidence" },
];

const weightedVotingStorageKey = "weighted-voting-daily-weights-short-cycle-v5";
const weightedStrategyPerformanceStorageKey = "weighted-strategy-performance-short-cycle-v5";
const WEIGHTED_BROWSER_BACKTEST_BOOTSTRAP_ENABLED = false;
const weightedMinimumTradesForFullWeight = 40;
const weightedVotingDefaultWeights: Record<WeightedAlphaKey, number> = {
  S1: 0.125,
  S2: 0.125,
  S3: 0.125,
  S4: 0.125,
  S5: 0.125,
  S6: 0.125,
  S7: 0.125,
  S8: 0.125,
};

const weightedRelativeStrengthSymbols = ["QQQ", "IWM"];
const weightedSectorEtfSymbols: string[] = [];
const weightedSpyBasketSampleSymbols: string[] = [];
const weightedAuxiliarySymbols = [...weightedRelativeStrengthSymbols, ...weightedSectorEtfSymbols, ...weightedSpyBasketSampleSymbols];
const weightedSpyContextTimeframes = ["1Min", "5Min"] as const;
type WeightedSpyContextTimeframe = (typeof weightedSpyContextTimeframes)[number];

const persistedUiState = loadUiState();

const state = {
  symbol: "SPY",
  feed: persistedUiState.feed ?? "iex",
  timeframe: persistedUiState.timeframe ?? "1Min" as Timeframe,
  refreshSeconds: persistedUiState.refreshSeconds === 0 ? 0 : BAR_CLOSE_REFRESH_MODE,
  start: persistedUiState.start ?? "",
  end: persistedUiState.end ?? "",
  viewportOffset: 0,
  visibleCount: 180,
  candleWidthPercent: persistedUiState.candleWidthPercent ?? 62,
  showWicks: persistedUiState.showWicks ?? true,
  showVolume: persistedUiState.showVolume ?? true,
  showPriceLine: persistedUiState.showPriceLine ?? true,
  showVisualConditions: persistedUiState.showVisualConditions ?? false,
  showLayerBackgrounds: persistedUiState.showLayerBackgrounds ?? true,
  loadingOlder: false,
  historyEndReached: false,
  candles: [] as Candle[],
  marketForecast: null as MarketForecastPrediction | null,
  marketForecastStatus: "idle" as "idle" | "loading" | "ready" | "fallback" | "error",
  marketForecastError: "",
  hoveredIndex: -1,
  hoverX: -1,
  hoverY: -1,
  lastRefreshAt: "",
  lastRefreshStatus: "waiting",
  algoTab: persistedUiState.algoTab ?? "voting" as AlgoTab,
  algoBacktestTimeframe: visibleAlgoBacktestTimeframe(persistedUiState.algoBacktestTimeframe),
  algoBacktestCandles: [] as Candle[],
  algoBacktestResult: null as BacktestResult | null,
  algoBacktestStatus: "loading",
  algoBacktestWarning: "",
  algoIntradayTradesExpanded:
    persistedUiState.algoIntradayTradesExpanded ?? ["1Min", "5Min"].includes(visibleAlgoBacktestTimeframe(persistedUiState.algoBacktestTimeframe)),
  algoVotesExpanded: persistedUiState.algoVotesExpanded ?? false,
  weightedVotingExpanded: persistedUiState.weightedVotingExpanded ?? true,
  weightedDataExpanded: persistedUiState.weightedDataExpanded ?? false,
  weightedGatesExpanded: persistedUiState.weightedGatesExpanded ?? false,
  weightedControlsExpanded: persistedUiState.weightedControlsExpanded ?? false,
  confidenceRequirementsExpanded: persistedUiState.confidenceRequirementsExpanded ?? false,
  confidenceTradingSettingsExpanded: persistedUiState.confidenceTradingSettingsExpanded ?? false,
  confidenceDefaultSizingExpanded: persistedUiState.confidenceDefaultSizingExpanded ?? false,
  confidenceStrategiesExpanded: persistedUiState.confidenceStrategiesExpanded ?? true,
  regimeTradingSettingsExpanded: persistedUiState.regimeTradingSettingsExpanded ?? false,
  regimeDefaultSizingExpanded: persistedUiState.regimeDefaultSizingExpanded ?? false,
  regimeIndicatorsExpanded: persistedUiState.regimeIndicatorsExpanded ?? false,
  regimeStrategiesExpanded: persistedUiState.regimeStrategiesExpanded ?? false,
  metaStrategiesExpanded: persistedUiState.metaStrategiesExpanded ?? false,
  metaChecksExpanded: persistedUiState.metaChecksExpanded ?? false,
  metaTrainingStatus: "idle" as "idle" | "loading" | "ready" | "error",
  metaTrainingResult: null as MetaStrategyTrainingStatus | null,
  metaTrainingWarning: "",
  confidenceDecisionSettings: loadConfidenceDecisionSettings(),
  confidenceTradingSettings: loadConfidenceTradingSettings(),
  regimeTradingSettings: loadRegimeTradingSettings(),
  metaTradingSettings: loadMetaTradingSettings(),
  weightedMarketData: {
    candlesBySymbol: {} as Record<string, Candle[]>,
    timeframeCandles: {} as Partial<Record<WeightedSpyContextTimeframe, Candle[]>>,
    status: "idle" as "idle" | "loading" | "ready" | "partial" | "error",
    warning: "",
    updatedAt: "",
  },
  mlComparison: null as MlComparisonResult | null,
  mlComparisonStatus: "loading" as "loading" | "ready" | "error",
  mlComparisonWarning: "",
  candidateDataset: null as CandidateDatasetSummary | null,
  candidateDatasetStatus: "loading" as "loading" | "ready" | "error",
  candidateDatasetWarning: "",
  mlDiagnostics: null as MlDiagnosticsResult | null,
  mlDiagnosticsStatus: "loading" as "loading" | "ready" | "error",
  mlDiagnosticsWarning: "",
  dailyRefinement: null as DailyRefinementResult | null,
  dailyRefinementStatus: "loading" as "loading" | "ready" | "error",
  dailyRefinementWarning: "",
  eventRefinement: null as EventRefinementResult | null,
  eventRefinementStatus: "loading" as "loading" | "ready" | "error",
  eventRefinementWarning: "",
  weeklyRiskTuning: null as WeeklyRiskTuningResult | null,
  weeklyRiskTuningStatus: "loading" as "loading" | "ready" | "error",
  weeklyRiskTuningWarning: "",
  tradingRag: null as TradingRagResponse | null,
  tradingRagStatus: "idle" as "idle" | "loading" | "ready" | "error",
  tradingRagWarning: "",
  tradingSettings: loadTradingSettings(),
  tradingSettingsExpanded: persistedUiState.tradingSettingsExpanded ?? false,
  votingDefaultSizingExpanded: persistedUiState.votingDefaultSizingExpanded ?? false,
  weightedTradingSettings: loadWeightedTradingSettings(),
  weightedTradingSettingsExpanded: persistedUiState.weightedTradingSettingsExpanded ?? false,
  weightedDefaultSizingExpanded: persistedUiState.weightedDefaultSizingExpanded ?? false,
  weightedTargetOrderOverrides: loadWeightedTargetOrderOverrides(),
  confidenceTargetOrderOverrides: loadConfidenceTargetOrderOverrides(),
  regimeTargetOrderOverrides: loadRegimeTargetOrderOverrides(),
  metaTargetOrderOverrides: loadMetaTargetOrderOverrides(),
  targetOrderOverrides: loadTargetOrderOverrides(),
  currentTargetOrder: null as ManualOrderRecommendation | null,
  currentWeightedTargetOrder: null as ManualOrderRecommendation | null,
  currentConfidenceTargetOrder: null as ManualOrderRecommendation | null,
  currentRegimeTargetOrder: null as ManualOrderRecommendation | null,
  currentMetaTargetOrder: null as ManualOrderRecommendation | null,
  autoSubmittedOrderKeys: loadAutoSubmittedOrderKeys(),
  tradingEnabled: persistedUiState.tradingEnabled ?? false,
  tradingWindowMode: persistedUiState.tradingWindowMode ?? "ensemble" as TradingWindowMode,
  selectedSellSetupByMode: {
    ensemble: persistedUiState.selectedSellSetupByMode?.ensemble ?? "",
    weighted: persistedUiState.selectedSellSetupByMode?.weighted ?? "",
    confidence: persistedUiState.selectedSellSetupByMode?.confidence ?? "",
    regime: persistedUiState.selectedSellSetupByMode?.regime ?? "",
    meta: persistedUiState.selectedSellSetupByMode?.meta ?? "",
  } as Record<TradingWindowMode, string>,
  sellSetupSelectionLockedByMode: {
    ensemble: persistedUiState.sellSetupSelectionLockedByMode?.ensemble ?? false,
    weighted: persistedUiState.sellSetupSelectionLockedByMode?.weighted ?? false,
    confidence: persistedUiState.sellSetupSelectionLockedByMode?.confidence ?? false,
    regime: persistedUiState.sellSetupSelectionLockedByMode?.regime ?? false,
    meta: persistedUiState.sellSetupSelectionLockedByMode?.meta ?? false,
  } as Record<TradingWindowMode, boolean>,
  orderControlModes: loadOrderControlModes(),
  orderControlOverrides: loadOrderControlOverrides(),
  tradeHistory: loadTradeHistory(),
  weightedOrderControlModes: loadWeightedOrderControlModes(),
  weightedOrderControlOverrides: loadWeightedOrderControlOverrides(),
  weightedTradeHistory: loadWeightedTradeHistory(),
  confidenceOrderControlModes: loadConfidenceOrderControlModes(),
  confidenceOrderControlOverrides: loadConfidenceOrderControlOverrides(),
  confidenceTradeHistory: loadConfidenceTradeHistory(),
  regimeOrderControlModes: loadRegimeOrderControlModes(),
  regimeOrderControlOverrides: loadRegimeOrderControlOverrides(),
  regimeTradeHistory: loadRegimeTradeHistory(),
  metaOrderControlModes: loadMetaOrderControlModes(),
  metaOrderControlOverrides: loadMetaOrderControlOverrides(),
  metaTradeHistory: loadMetaTradeHistory(),
  dynamicArtifact: null as DynamicTradingArtifact | null,
  dynamicArtifactStatus: "idle" as "idle" | "loading" | "ready" | "error" | "stale",
  dynamicArtifactWarning: "",
  dynamicArtifactSettingsKey: "",
  marketStatus: "checking",
  marketContext: null as MarketContext | null,
  macroEvents: [] as MacroEvent[],
  macroExpanded: persistedUiState.macroExpanded ?? false,
  macroStatus: "loading",
  macroSource: "",
  macroUpdatedAt: "",
  fedEvents: [] as FedEvent[],
  fedExpanded: persistedUiState.fedExpanded ?? false,
  fedStatus: "loading",
  fedSource: "",
  fedUpdatedAt: "",
  tradingAlerts: [] as TradingAlert[],
  tradingAlertsExpanded: persistedUiState.tradingAlertsExpanded ?? false,
  tradingAlertsStatus: "loading",
  tradingAlertsSource: "",
  tradingAlertsWarning: "",
  tradingAlertsUpdatedAt: "",
  circuitBreakers: null as CircuitBreakersResponse | null,
  circuitBreakersExpanded: persistedUiState.circuitBreakersExpanded ?? false,
  circuitBreakersStatus: "loading",
  circuitBreakersWarning: "",
  mocImbalance: null as MocImbalanceResponse | null,
  mocImbalanceExpanded: persistedUiState.mocImbalanceExpanded ?? false,
  mocImbalanceStatus: "loading",
  mocImbalanceWarning: "",
  vixRisk: null as VixRiskResponse | null,
  vixRiskExpanded: persistedUiState.vixRiskExpanded ?? false,
  vixRiskStatus: "loading",
  vixRiskWarning: "",
  esSnapshot: null as EsSnapshotResponse | null,
  esSnapshotExpanded: persistedUiState.esSnapshotExpanded ?? false,
  esSnapshotStatus: "loading",
  esSnapshotWarning: "",
  newsFeed: fallbackNewsFeed,
  newsFeedStatus: "loading",
  newsFeedWarning: "",
  tradeSummary: fallbackTradeSummary,
  tradeSummaryStatus: "loading",
  tradeSummaryWarning: "",
  contextStatus: "loading",
  contextError: "",
  contextAsOf: "",
  source: "loading",
  error: "",
};

let refreshTimer: number | undefined;
let nextChartRefreshAt = 0;
let contextTimer: number | undefined;
let tradingRagRefreshInFlight = false;
let automaticSubmitInFlight = false;
let marketForecastRequestKey = "";
let lastTradingRagCandleTimestamp = "";
let chartDrawFrame: number | undefined;
let pendingRegimeSelectionUpdateFrame: number | undefined;
let lastMetaKey = "";
let tradingSettingsMountKey = "";
let weightedTradingSettingsMountKey = "";
let weightedMarketDataInFlight = false;
let weightedInitialWeightsInFlight = false;
let isDragging = false;
let lastDragX = 0;
let dragCarry = 0;
const minVisibleCandles = 30;
const maxVisibleCandles = 500;
const defaultVisibleCandles = 180;
const zoomFactor = 1.25;

const timeframeItems: Array<{ label: string; value: Timeframe; limit: number }> = [
  { label: "1m", value: "1Min", limit: 1000 },
  { label: "3m", value: "3Min", limit: 800 },
  { label: "5m", value: "5Min", limit: 700 },
  { label: "15m", value: "15Min", limit: 500 },
  { label: "1h", value: "1Hour", limit: 320 },
  { label: "1d", value: "1Day", limit: 260 },
];

document.querySelector<HTMLDivElement>("#app")!.innerHTML = `
  <main class="workspace">
    <div class="dashboard-layout">
      <section class="chart-shell">
        <header class="topbar">
          <div class="candle-settings-wrap">
            <button id="candleSettingsButton" class="icon-button candle-settings-button" title="Candle settings" aria-label="Candle settings" aria-expanded="false">
              <span class="candle-icon" aria-hidden="true">
                <span></span>
                <span></span>
                <span></span>
              </span>
            </button>
            <div id="candleSettingsMenu" class="candle-settings-menu" hidden>
              <div class="settings-title">Candle display</div>
              <label class="settings-range">
                <span>Candle width</span>
                <input id="candleWidthInput" type="range" min="35" max="100" value="62" />
                <strong id="candleWidthValue">62%</strong>
              </label>
              <label class="settings-toggle">
                <input id="wickToggle" type="checkbox" checked />
                <span>Wicks</span>
              </label>
              <label class="settings-toggle">
                <input id="volumeToggle" type="checkbox" checked />
                <span>Volume</span>
              </label>
              <label class="settings-toggle">
                <input id="priceLineToggle" type="checkbox" checked />
                <span>Price line</span>
              </label>
            </div>
          </div>
          <div class="zoom-control" aria-label="Chart zoom">
            <button id="zoomOutButton" class="icon-button" title="Zoom out" aria-label="Zoom out">-</button>
            <button id="zoomResetButton" class="icon-button reset-zoom-button" title="Reset zoom" aria-label="Reset zoom">⟲</button>
            <span id="zoomLevel" class="zoom-level" aria-live="polite">100%</span>
            <button id="zoomInButton" class="icon-button" title="Zoom in" aria-label="Zoom in">+</button>
          </div>
          <div class="overlay-control" aria-label="Chart overlays">
            <button id="visualConditionsButton" class="icon-button overlay-button" title="Visual conditions" aria-label="Toggle visual conditions" aria-pressed="false">
              <span class="condition-icon" aria-hidden="true"></span>
            </button>
            <button id="layerBackgroundsButton" class="icon-button overlay-button" title="Regime/session/event backgrounds" aria-label="Toggle regime session event backgrounds" aria-pressed="true">
              <span class="layers-icon" aria-hidden="true"></span>
            </button>
          </div>
          <div class="range-control">
            <input id="startInput" type="datetime-local" aria-label="Start" />
            <input id="endInput" type="datetime-local" aria-label="End" />
            <button id="rangeButton" class="tool-button" title="Load range">Load</button>
            <span class="range-actions">
              <button id="latestButton" class="icon-button" title="Latest" aria-label="Latest">>|</button>
              <button id="refreshButton" class="icon-button reset-zoom-button" title="Refresh" aria-label="Refresh">⟲</button>
            </span>
          </div>
          <div class="symbol-control">
            <select id="feedSelect" aria-label="Feed">
              <option value="iex">IEX</option>
              <option value="sip">SIP</option>
              <option value="otc">OTC</option>
            </select>
          </div>
        </header>

        <div class="chart-meta">
          <div>
            <strong id="chartTitle">SPY - 1 - IEX</strong>
            <span id="ohlc"></span>
          </div>
          <div class="chart-badges">
            <span id="marketStatusBadge" class="market-status-badge">checking</span>
            <span id="sourceBadge" class="source-badge">loading</span>
          </div>
        </div>

        <section class="context-stack" aria-label="Market context layers">
          <div id="regimeLayer" class="context-layer" data-layer="regime">
            <div class="layer-heading">
              <span>Regime</span>
              <strong>Loading daily context</strong>
            </div>
            <div class="layer-metrics"></div>
            <div class="layer-signals"></div>
            <div class="layer-reasons"></div>
          </div>
          <div id="sessionLayer" class="context-layer" data-layer="session">
            <div class="layer-heading">
              <span>Session</span>
              <strong>Loading intraday context</strong>
            </div>
            <div class="layer-metrics"></div>
            <div class="layer-signals"></div>
            <div class="layer-reasons"></div>
          </div>
          <div id="eventLayer" class="context-layer" data-layer="event">
            <div class="layer-heading">
              <span>Event</span>
              <strong>Checking open and event window</strong>
            </div>
            <div class="layer-metrics"></div>
            <div class="layer-signals"></div>
            <div class="layer-reasons"></div>
          </div>
        </section>

        <div class="canvas-wrap">
          <canvas id="chartCanvas"></canvas>
          <div id="emptyState" class="empty-state">Loading candles...</div>
        </div>

        <section class="strategy-strip" aria-label="Strategy selection">
          <div class="strategy-strip-header">
            <div>
              <span>Strategy Fit</span>
              <strong id="strategySummary">Waiting for market context</strong>
            </div>
            <span id="contextUpdatedAt" class="context-updated">--</span>
          </div>
          <div id="strategyList" class="strategy-list"></div>
        </section>

        <section class="decision-strip" aria-label="Market forecast and context">
          <div class="decision-header">
            <span>Market Forecast</span>
            <strong id="decisionAction"></strong>
          </div>
          <div id="marketForecastPanel" class="market-forecast-panel" data-status="waiting"></div>
          <div class="decision-context-scroll">
            <div class="decision-grid">
              <div class="decision-tile">
                <span>Regime</span>
                <strong id="decisionBias">--</strong>
              </div>
              <div class="decision-tile">
                <span>Risk State</span>
                <strong id="decisionRisk">--</strong>
              </div>
            </div>
            <div id="decisionChecklist" class="decision-checklist"></div>
          </div>
        </section>

        <footer class="statusbar">
          <div class="status-timeframes"></div>
          <div class="session-info">
            <span id="clock"></span>
            <span>ETH</span>
            <span>%</span>
            <span>log</span>
            <span id="lastCandleStatus" class="last-candle-status">last candle --</span>
            <span id="refreshStatus" class="refresh-status">waiting</span>
            <select id="refreshSelect" class="auto-select" aria-label="Auto refresh">
              ${refreshOptions.map((option) => `<option value="${option.value}">${option.label}</option>`).join("")}
            </select>
          </div>
        </footer>
      </section>

      <aside class="quote-shell" aria-label="SPY order and position card">
        <div class="quote-sticky-summary">
          <div class="quote-header">
            <span>SPDR S&P 500 ETF TRUST</span>
            <div class="quote-header-actions">
              <button id="tradeToggleButton" class="trade-toggle-button" type="button" aria-pressed="false">Trade Off</button>
              <strong id="quoteSymbol">SPY</strong>
            </div>
          </div>
          <div class="quote-price-row">
            <div>
              <div id="quotePrice" class="quote-price">--</div>
              <div id="quoteChange" class="quote-change">--</div>
            </div>
            <div class="quote-market">
              <span>Ask <strong id="quoteAsk">--</strong></span>
              <span>Bid <strong id="quoteBid">--</strong></span>
            </div>
          </div>
        </div>
        <div class="order-buttons">
          <button id="buyOrderButton" class="buy-button">Buy Order</button>
          <button id="sellOrderButton" class="sell-button">Sell Order</button>
        </div>
        <div id="quoteStats" class="quote-grid"></div>
        <div class="position-section">
          <div class="trading-window-tabs" role="tablist" aria-label="Trading ledger tabs">
            <button id="ensembleTradingWindowTab" class="trading-window-tab active" type="button" role="tab" aria-selected="true" aria-controls="tradingWindowPanel">Voting Ensemble</button>
            <button id="weightedTradingWindowTab" class="trading-window-tab" type="button" role="tab" aria-selected="false" aria-controls="tradingWindowPanel">Weighted Voting</button>
            <button id="confidenceTradingWindowTab" class="trading-window-tab" type="button" role="tab" aria-selected="false" aria-controls="tradingWindowPanel">WCA</button>
            <button id="regimeTradingWindowTab" class="trading-window-tab" type="button" role="tab" aria-selected="false" aria-controls="tradingWindowPanel">Regime</button>
            <button id="metaTradingWindowTab" class="trading-window-tab" type="button" role="tab" aria-selected="false" aria-controls="tradingWindowPanel">Meta-Strategy</button>
          </div>
          <div id="tradingWindowPanel" class="trading-window-panel" role="tabpanel">
            <div class="section-title">Position</div>
            <div id="quotePosition" class="quote-grid"></div>
            <button id="closePositionButton" class="close-position">Close Position</button>
            <div class="open-order-controls-section">
              <div class="section-title">Order Controls</div>
              <div id="openOrderControls"></div>
            </div>
            <div class="trade-history-section">
              <div class="trade-history-header">
                <span id="tradeHistoryTitle">Trade History</span>
                <button id="clearTradeHistoryButton" type="button">Clear</button>
              </div>
              <div class="trade-history-table-wrap">
                <table class="trade-history-table">
                  <thead>
                    <tr>
                      <th>Time</th>
                      <th>Side</th>
                      <th>Qty</th>
                      <th>Price</th>
                      <th>Value</th>
                    </tr>
                  </thead>
                  <tbody id="tradeHistoryBody"></tbody>
                  <tfoot>
                    <tr>
                      <td colspan="4">Balance</td>
                      <td id="tradeHistoryBalance">$0.00</td>
                    </tr>
                  </tfoot>
                </table>
              </div>
            </div>
          </div>
        </div>
        <div class="news-section">
          <div class="news-header">
            <div>
              <div class="section-title">SPY News</div>
              <strong id="newsFeedSummary">Loading headlines</strong>
            </div>
            <button id="newsFeedRefreshButton" class="icon-button news-refresh" type="button" aria-label="Refresh SPY news">R</button>
          </div>
          <div id="newsFeedList" class="news-feed-list" aria-live="polite"></div>
          <div id="newsFeedSources" class="news-source-list"></div>
        </div>
      </aside>
    </div>
    <div id="dailyBacktestPopup" class="daily-backtest-popup" role="dialog" aria-modal="true" aria-labelledby="dailyBacktestPopupTitle" hidden>
      <div class="daily-backtest-popup-card">
        <div class="daily-backtest-popup-head">
          <div>
            <span>End of Day Process</span>
            <strong id="dailyBacktestPopupTitle">Daily backtesting finished</strong>
          </div>
          <button id="dailyBacktestPopupClose" class="icon-button" type="button" aria-label="Close daily backtest message">X</button>
        </div>
        <div id="dailyBacktestPopupBody" class="daily-backtest-popup-body"></div>
      </div>
    </div>
  </main>
`;

const dashboardLayout = document.querySelector<HTMLDivElement>(".dashboard-layout")!;
const chartShell = document.querySelector<HTMLElement>(".chart-shell")!;
const dailyBacktestPopup = document.querySelector<HTMLDivElement>("#dailyBacktestPopup")!;
const dailyBacktestPopupTitle = document.querySelector<HTMLElement>("#dailyBacktestPopupTitle")!;
const dailyBacktestPopupBody = document.querySelector<HTMLDivElement>("#dailyBacktestPopupBody")!;
const dailyBacktestPopupClose = document.querySelector<HTMLButtonElement>("#dailyBacktestPopupClose")!;
const contextStackElement = document.querySelector<HTMLElement>(".context-stack")!;
const strategyStripElement = document.querySelector<HTMLElement>(".strategy-strip")!;
const decisionStripElement = document.querySelector<HTMLElement>(".decision-strip")!;
const newsSectionElement = document.querySelector<HTMLElement>(".news-section")!;

const layersShell = document.createElement("section");
layersShell.className = "layers-shell";
layersShell.setAttribute("aria-label", "Market context layer card");
layersShell.append(contextStackElement);

const leftRail = document.createElement("aside");
leftRail.className = "left-rail";
leftRail.setAttribute("aria-label", "Algorithmic trading strategies");
leftRail.innerHTML = `
  <section class="placeholder-shell">
    <div class="algo-shell">
      <div class="algo-tabs" role="tablist" aria-label="Algorithmic strategies">
        <button id="algoVotingEnsembleTabButton" class="algo-tab-button active" type="button" role="tab" aria-selected="true" aria-controls="algoVotingEnsemblePanel"><span class="algo-tab-label">Voting Ensemble</span></button>
        <button id="algoWeightedVotingTabButton" class="algo-tab-button" type="button" role="tab" aria-selected="false" aria-controls="algoWeightedVotingPanel"><span class="algo-tab-label">Weighted Voting</span></button>
        <button id="algoConfidenceAggregationTabButton" class="algo-tab-button" type="button" role="tab" aria-selected="false" aria-controls="algoConfidenceAggregationPanel"><span class="algo-tab-label">WCA</span></button>
        <button id="algoRegimeSelectionTabButton" class="algo-tab-button" type="button" role="tab" aria-selected="false" aria-controls="algoRegimeSelectionPanel"><span class="algo-tab-label">Regime</span></button>
        <button id="algoMetaStrategyTabButton" class="algo-tab-button" type="button" role="tab" aria-selected="false" aria-controls="algoMetaStrategyPanel"><span class="algo-tab-label">Meta-Strategy</span></button>
      </div>
      <div id="algoVotingEnsemblePanel" class="algo-panel active" role="tabpanel" aria-labelledby="algoVotingEnsembleTabButton">
        <div class="algo-header">
          <div id="algoFinalSignal" class="algo-final hold">Hold</div>
        </div>
        <div id="algoVoteCounts" class="algo-vote-grid"></div>
        <div id="algoResultsBody" class="algo-rule-list"></div>
        <div id="algoTradePlanTitle" class="algo-section-title">Trades</div>
        <div id="algoTradePlan" class="algo-rule-list"></div>
        <div class="algo-table-wrap">
          <table class="algo-trade-table">
            <thead>
              <tr>
                <th>Side</th>
                <th>Entry</th>
                <th>Exit</th>
                <th>P/L</th>
              </tr>
            </thead>
            <tbody id="algoTradesTable"></tbody>
          </table>
        </div>
        <div id="tradingSettingsMount" class="algo-stable-settings"></div>
        <button id="algoVotesToggle" class="algo-expand-toggle" type="button" aria-expanded="false" aria-controls="algoVoteList">
          <span>Strategies</span>
          <strong id="algoVotesToggleMeta">10 strategies</strong>
          <b id="algoVotesToggleIcon">+</b>
        </button>
        <div id="algoVoteList" class="algo-vote-list" hidden></div>
        <div class="algo-timeframe-toggle" role="group" aria-label="Voting Ensemble backtest timeframe">
          <button id="algoIntradayTradesToggle" class="algo-timeframe-button algo-timeframe-menu-toggle" type="button" aria-expanded="false" aria-controls="algoIntradayTradesPanel">
            <span>1m/5m Trades</span>
            <b id="algoIntradayTradesToggleIcon">+</b>
          </button>
          <div id="algoIntradayTradesPanel" class="algo-timeframe-subtoggle" hidden>
            <button id="algoBacktest1mButton" class="algo-timeframe-button active" type="button">1m</button>
            <button id="algoBacktest5mButton" class="algo-timeframe-button" type="button">5m</button>
            <div id="algoIntradayTradesContent" class="algo-intraday-trades-content">
              <div id="algoIntradayTradesSummary" class="algo-rule-list algo-intraday-trades-summary"></div>
              <div class="algo-table-wrap">
                <table class="algo-trade-table">
                  <thead>
                    <tr>
                      <th>Side</th>
                      <th>Entry</th>
                      <th>Exit</th>
                      <th>P/L</th>
                    </tr>
                  </thead>
                  <tbody id="algoIntradayTradesTable"></tbody>
                </table>
              </div>
            </div>
          </div>
          <button id="algoBacktestTradingButton" class="algo-timeframe-button" type="button" hidden>Trading</button>
        </div>
      </div>
      <div id="algoWeightedVotingPanel" class="algo-panel" role="tabpanel" aria-labelledby="algoWeightedVotingTabButton" hidden>
        <div class="algo-header">
          <div id="weightedFinalSignal" class="algo-final hold">Hold</div>
        </div>
        <div id="weightedScoreGrid" class="weighted-score-grid"></div>
        <div id="weightedSummary" class="algo-rule-list weighted-summary"></div>
        <div id="weightedTradingSettingsMount" class="algo-stable-settings"></div>
        <button id="weightedStrategiesToggle" class="algo-expand-toggle" type="button" aria-expanded="true" aria-controls="weightedStrategiesList">
          <span>Weighted Strategies</span>
          <strong id="weightedStrategiesToggleMeta">8 alpha models</strong>
          <b id="weightedStrategiesToggleIcon">-</b>
        </button>
        <div id="weightedStrategiesList" class="weighted-strategy-list"></div>
        <button id="weightedDataToggle" class="algo-expand-toggle" type="button" aria-expanded="false" aria-controls="weightedDataGrid">
          <span>Data & Conditions</span>
          <strong id="weightedDataToggleMeta">Market inputs</strong>
          <b id="weightedDataToggleIcon">+</b>
        </button>
        <div id="weightedDataGrid" class="weighted-data-grid" hidden></div>
        <button id="weightedGatesToggle" class="algo-expand-toggle" type="button" aria-expanded="false" aria-controls="weightedGateList">
          <span>Risk Gates</span>
          <strong id="weightedGatesToggleMeta">Safety checks</strong>
          <b id="weightedGatesToggleIcon">+</b>
        </button>
        <div id="weightedGateList" class="weighted-gate-list" hidden></div>
        <button id="weightedControlsToggle" class="algo-expand-toggle weighted-controls-toggle" type="button" aria-expanded="false" aria-controls="weightedControlRules">
          <span>Weight Controls</span>
          <strong>Safety rules</strong>
          <b id="weightedControlsToggleIcon">+</b>
        </button>
        <div id="weightedControlRules" class="algo-rule-list weighted-controls" hidden></div>
      </div>
      <div id="algoConfidenceAggregationPanel" class="algo-panel" role="tabpanel" aria-labelledby="algoConfidenceAggregationTabButton" hidden>
        <div class="algo-header">
          <div id="confidenceFinalSignal" class="algo-final hold">Hold</div>
        </div>
        <div id="confidenceScoreGrid" class="weighted-score-grid"></div>
        <div id="confidenceSummary" class="algo-rule-list weighted-summary"></div>
        <div id="confidenceTradingSettingsMount" class="algo-stable-settings"></div>
        <div class="confidence-backtest-panel">
          <div class="confidence-backtest-head">
            <div class="algo-section-title">WCA Backtest</div>
            <span id="confidenceBacktestStatusLabel" class="confidence-backtest-status">Daily closed-market run</span>
          </div>
          <div id="confidenceBacktestSummary" class="algo-rule-list confidence-backtest-summary"></div>
          <div class="algo-table-wrap confidence-backtest-table-wrap">
            <table class="algo-trade-table">
              <thead>
                <tr>
                  <th>Side</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>P/L</th>
                </tr>
              </thead>
              <tbody id="confidenceBacktestTradesTable"></tbody>
            </table>
          </div>
        </div>
        <button id="confidenceRequirementsToggle" class="algo-expand-toggle" type="button" aria-expanded="false" aria-controls="confidenceRequirementsPanel">
          <span>Agreement Requirements</span>
          <strong id="confidenceRequirementsToggleMeta">4 active / 60% / 55%</strong>
          <b id="confidenceRequirementsToggleIcon">+</b>
        </button>
        <div id="confidenceRequirementsPanel" class="confidence-requirements-panel" hidden></div>
        <button id="confidenceStrategiesToggle" class="algo-expand-toggle" type="button" aria-expanded="true" aria-controls="confidenceStrategiesList">
          <span>Strategies</span>
          <strong id="confidenceStrategiesToggleMeta">11 strategies</strong>
          <b id="confidenceStrategiesToggleIcon">-</b>
        </button>
        <div id="confidenceStrategiesList" class="weighted-strategy-list"></div>
      </div>
      <div id="algoRegimeSelectionPanel" class="algo-panel" role="tabpanel" aria-labelledby="algoRegimeSelectionTabButton" hidden>
        <div class="algo-header">
          <div id="regimeFinalSignal" class="algo-final hold">Hold</div>
        </div>
        <div id="regimeScoreGrid" class="weighted-score-grid"></div>
        <div id="regimeSummary" class="algo-rule-list weighted-summary"></div>
        <div id="regimeTradingSettingsMount" class="algo-stable-settings"></div>
        <button id="regimeIndicatorsToggle" class="algo-expand-toggle regime-indicators-toggle" type="button" aria-expanded="true" aria-controls="regimeFeatureGrid">
          <span>Market Indicators</span>
          <strong id="regimeIndicatorsToggleMeta">0 indicators</strong>
          <b id="regimeIndicatorsToggleIcon">-</b>
        </button>
        <div id="regimeFeatureGrid" class="regime-feature-grid"></div>
        <button id="regimeStrategiesToggle" class="algo-expand-toggle regime-strategies-toggle" type="button" aria-expanded="true" aria-controls="regimeStrategiesList">
          <span>Strategies</span>
          <strong id="regimeStrategiesToggleMeta">0 selected</strong>
          <b id="regimeStrategiesToggleIcon">-</b>
        </button>
        <div id="regimeStrategiesList" class="weighted-strategy-list"></div>
      </div>
      <div id="algoMetaStrategyPanel" class="algo-panel" role="tabpanel" aria-labelledby="algoMetaStrategyTabButton" hidden>
        <div class="algo-header">
          <div id="metaFinalSignal" class="algo-final hold">Hold</div>
        </div>
        <div id="metaScoreGrid" class="weighted-score-grid"></div>
        <div id="metaSummary" class="algo-rule-list weighted-summary" hidden></div>
        <div id="metaMlReadinessBox" class="meta-ml-readiness-box" data-status="loading"></div>
        <div id="metaFamilyGrid" class="regime-feature-grid meta-family-grid expanded"></div>
        <button id="metaStrategiesToggle" class="algo-expand-toggle meta-strategies-toggle" type="button" aria-expanded="false" aria-controls="metaStrategiesPanel">
          <span>Strategies</span>
          <strong id="metaStrategiesToggleMeta">0 active / 29 strategies</strong>
          <b id="metaStrategiesToggleIcon">+</b>
        </button>
        <div id="metaStrategiesPanel" class="meta-strategies-panel" hidden>
          <div id="metaStrategiesList" class="weighted-strategy-list"></div>
        </div>
        <button id="metaChecksToggle" class="algo-expand-toggle meta-checks-toggle" type="button" aria-expanded="false" aria-controls="metaGateList">
          <span>Meta Decision Checks</span>
          <strong id="metaChecksToggleMeta">0 checks</strong>
          <b id="metaChecksToggleIcon">+</b>
        </button>
        <div id="metaGateList" class="weighted-gate-list meta-checks-list" hidden></div>
      </div>
    </div>
  </section>
`;

const rightRail = document.createElement("aside");
rightRail.className = "right-rail";
rightRail.setAttribute("aria-label", "Strategy and decision cards");

const strategyShell = document.createElement("section");
strategyShell.className = "strategy-shell";
strategyShell.setAttribute("aria-label", "Strategy fit card");
strategyShell.append(strategyStripElement);

const decisionShell = document.createElement("section");
decisionShell.className = "decision-shell";
decisionShell.setAttribute("aria-label", "Decision making card");
decisionShell.append(decisionStripElement);

const macroShell = document.createElement("section");
macroShell.className = "macro-shell";
macroShell.setAttribute("aria-label", "Upcoming macro events tab");
macroShell.innerHTML = `
  <section class="macro-calendar" aria-label="CPI and jobs calendar">
    <button id="macroToggleButton" class="macro-tab" type="button" aria-expanded="false" aria-controls="macroPanel">
      <div>
        <span>Macro Calendar</span>
        <strong id="macroSummary">Loading CPI and jobs</strong>
      </div>
      <small id="macroTabMeta">Loading</small>
      <span id="macroTabIcon" class="macro-tab-icon" aria-hidden="true">+</span>
    </button>
    <div id="macroPanel" class="macro-panel" hidden>
      <div class="macro-actions">
        <button id="macroRefreshButton" class="icon-button macro-refresh" title="Refresh macro calendar" aria-label="Refresh macro calendar">R</button>
      </div>
      <div id="macroNext" class="macro-next loading">
        <span>Next</span>
        <strong>Loading</strong>
      </div>
      <div id="macroList" class="macro-list"></div>
    </div>
  </section>
`;

const fedShell = document.createElement("section");
fedShell.className = "fed-shell";
fedShell.setAttribute("aria-label", "FOMC and Fed speaker events tab");
fedShell.innerHTML = `
  <section class="fed-calendar event-calendar" aria-label="FOMC and Fed speaker schedule">
    <button id="fedToggleButton" class="event-tab" type="button" aria-expanded="false" aria-controls="fedPanel">
      <div>
        <span>Fed Calendar</span>
        <strong id="fedSummary">Loading FOMC and speeches</strong>
      </div>
      <small id="fedTabMeta">Loading</small>
      <span id="fedTabIcon" class="event-tab-icon" aria-hidden="true">+</span>
    </button>
    <div id="fedPanel" class="event-panel" hidden>
      <div class="event-actions">
        <button id="fedRefreshButton" class="icon-button event-refresh" title="Refresh Fed calendar" aria-label="Refresh Fed calendar">R</button>
      </div>
      <div id="fedNext" class="event-next loading">
        <span>Next</span>
        <strong>Loading</strong>
      </div>
      <div id="fedList" class="event-list"></div>
    </div>
  </section>
`;

const tradingAlertsShell = document.createElement("section");
tradingAlertsShell.className = "trading-alerts-shell";
tradingAlertsShell.setAttribute("aria-label", "Trading halt and LULD alerts tab");
tradingAlertsShell.innerHTML = `
  <section class="trading-alerts-calendar event-calendar" aria-label="Trading halt and LULD alerts">
    <button id="tradingAlertsToggleButton" class="event-tab" type="button" aria-expanded="false" aria-controls="tradingAlertsPanel">
      <div>
        <span>Trading Halts / LULD</span>
        <strong id="tradingAlertsSummary">Loading halt alerts</strong>
      </div>
      <small id="tradingAlertsTabMeta">Loading</small>
      <span id="tradingAlertsTabIcon" class="event-tab-icon" aria-hidden="true">+</span>
    </button>
    <div id="tradingAlertsPanel" class="event-panel" hidden>
      <div class="event-actions">
        <button id="tradingAlertsRefreshButton" class="icon-button event-refresh" title="Refresh halt alerts" aria-label="Refresh halt alerts">R</button>
      </div>
      <div id="tradingAlertsNext" class="event-next loading">
        <span>Status</span>
        <strong>Loading</strong>
      </div>
      <div id="tradingAlertsList" class="event-list"></div>
    </div>
  </section>
`;

const circuitBreakersShell = document.createElement("section");
circuitBreakersShell.className = "circuit-breakers-shell";
circuitBreakersShell.setAttribute("aria-label", "Market-wide circuit breaker levels tab");
circuitBreakersShell.innerHTML = `
  <section class="circuit-breakers-calendar event-calendar" aria-label="Market-wide circuit breaker levels">
    <button id="circuitBreakersToggleButton" class="event-tab" type="button" aria-expanded="false" aria-controls="circuitBreakersPanel">
      <div>
        <span>Circuit Breakers</span>
        <strong id="circuitBreakersSummary">Loading market-wide levels</strong>
      </div>
      <small id="circuitBreakersTabMeta">Loading</small>
      <span id="circuitBreakersTabIcon" class="event-tab-icon" aria-hidden="true">+</span>
    </button>
    <div id="circuitBreakersPanel" class="event-panel" hidden>
      <div class="event-actions">
        <button id="circuitBreakersRefreshButton" class="icon-button event-refresh" title="Refresh circuit breaker levels" aria-label="Refresh circuit breaker levels">R</button>
      </div>
      <div id="circuitBreakersNext" class="event-next loading">
        <span>Reference</span>
        <strong>Loading</strong>
      </div>
      <div id="circuitBreakersList" class="event-list"></div>
    </div>
  </section>
`;

const mocImbalanceShell = document.createElement("section");
mocImbalanceShell.className = "moc-imbalance-shell";
mocImbalanceShell.setAttribute("aria-label", "Closing auction imbalance updates tab");
mocImbalanceShell.innerHTML = `
  <section class="moc-imbalance-calendar event-calendar" aria-label="Closing auction imbalance updates">
    <button id="mocImbalanceToggleButton" class="event-tab" type="button" aria-expanded="false" aria-controls="mocImbalancePanel">
      <div>
        <span>MOC Imbalance</span>
        <strong id="mocImbalanceSummary">Loading closing auction</strong>
      </div>
      <small id="mocImbalanceTabMeta">Loading</small>
      <span id="mocImbalanceTabIcon" class="event-tab-icon" aria-hidden="true">+</span>
    </button>
    <div id="mocImbalancePanel" class="event-panel" hidden>
      <div class="event-actions">
        <button id="mocImbalanceRefreshButton" class="icon-button event-refresh" title="Refresh MOC imbalance" aria-label="Refresh MOC imbalance">R</button>
      </div>
      <div id="mocImbalanceNext" class="event-next loading">
        <span>Status</span>
        <strong>Loading</strong>
      </div>
      <div id="mocImbalanceList" class="event-list"></div>
    </div>
  </section>
`;

const vixRiskShell = document.createElement("section");
vixRiskShell.className = "vix-risk-shell";
vixRiskShell.setAttribute("aria-label", "VIX volatility and risk alerts tab");
vixRiskShell.innerHTML = `
  <section class="vix-risk-calendar event-calendar" aria-label="VIX volatility and risk alerts">
    <button id="vixRiskToggleButton" class="event-tab" type="button" aria-expanded="false" aria-controls="vixRiskPanel">
      <div>
        <span>VIX</span>
        <strong id="vixRiskSummary">Loading volatility risk</strong>
      </div>
      <small id="vixRiskTabMeta">Loading</small>
      <span id="vixRiskTabIcon" class="event-tab-icon" aria-hidden="true">+</span>
    </button>
    <div id="vixRiskPanel" class="event-panel" hidden>
      <div class="event-actions">
        <button id="vixRiskRefreshButton" class="icon-button event-refresh" title="Refresh VIX risk" aria-label="Refresh VIX risk">R</button>
      </div>
      <div id="vixRiskNext" class="event-next loading">
        <span>Status</span>
        <strong>Loading</strong>
      </div>
      <div id="vixRiskList" class="event-list"></div>
    </div>
  </section>
`;

const esSnapshotShell = document.createElement("section");
esSnapshotShell.className = "es-snapshot-shell";
esSnapshotShell.setAttribute("aria-label", "ES futures premarket snapshot tab");
esSnapshotShell.innerHTML = `
  <section class="es-snapshot-calendar event-calendar" aria-label="ES futures premarket market snapshot">
    <button id="esSnapshotToggleButton" class="event-tab" type="button" aria-expanded="false" aria-controls="esSnapshotPanel">
      <div>
        <span>ES Futures</span>
        <strong id="esSnapshotSummary">Loading premarket snapshot</strong>
      </div>
      <small id="esSnapshotTabMeta">Loading</small>
      <span id="esSnapshotTabIcon" class="event-tab-icon" aria-hidden="true">+</span>
    </button>
    <div id="esSnapshotPanel" class="event-panel" hidden>
      <div class="event-actions">
        <button id="esSnapshotRefreshButton" class="icon-button event-refresh" title="Refresh ES snapshot" aria-label="Refresh ES snapshot">R</button>
      </div>
      <div id="esSnapshotNext" class="event-next loading">
        <span>Status</span>
        <strong>Loading</strong>
      </div>
      <div id="esSnapshotList" class="event-list"></div>
    </div>
  </section>
`;

const marketTabsShell = document.createElement("section");
marketTabsShell.className = "market-tabs-shell";
marketTabsShell.setAttribute("aria-label", "Market widgets and news window");
marketTabsShell.innerHTML = `
  <div class="market-tabs-header" role="tablist" aria-label="Market right rail tabs">
    <button id="widgetsTabButton" class="market-tab-button" type="button" role="tab" aria-selected="false" aria-controls="widgetsTabPanel">News Feeds</button>
    <button id="newsTabButton" class="market-tab-button" type="button" role="tab" aria-selected="false" aria-controls="newsTabPanel">SPY News</button>
    <button id="summaryTabButton" class="market-tab-button active" type="button" role="tab" aria-selected="true" aria-controls="summaryTabPanel">Summary</button>
  </div>
  <div id="widgetsTabPanel" class="market-tab-panel" role="tabpanel" aria-labelledby="widgetsTabButton" hidden></div>
  <div id="newsTabPanel" class="market-tab-panel" role="tabpanel" aria-labelledby="newsTabButton" hidden></div>
  <div id="summaryTabPanel" class="market-tab-panel active" role="tabpanel" aria-labelledby="summaryTabButton"></div>
`;
const widgetsTabPanel = marketTabsShell.querySelector<HTMLDivElement>("#widgetsTabPanel")!;
const newsTabPanel = marketTabsShell.querySelector<HTMLDivElement>("#newsTabPanel")!;
const summaryTabPanel = marketTabsShell.querySelector<HTMLDivElement>("#summaryTabPanel")!;

const newsShell = document.createElement("section");
newsShell.className = "news-shell";
newsShell.setAttribute("aria-label", "SPY news card");
newsShell.append(newsSectionElement);

const summaryShell = document.createElement("section");
summaryShell.className = "summary-shell";
summaryShell.setAttribute("aria-label", "Local LLM trade summary card");
summaryShell.innerHTML = `
  <div class="summary-section">
    <div class="summary-header">
      <div>
        <div class="section-title">Summary</div>
        <strong id="tradeSummaryHeadline">Loading trade conclusion</strong>
      </div>
      <button id="tradeSummaryRefreshButton" class="icon-button news-refresh" type="button" aria-label="Refresh trade summary">R</button>
    </div>
    <div id="tradeSummaryStatus" class="summary-status"></div>
    <div id="tradeSummaryBody" class="summary-body" aria-live="polite"></div>
  </div>
`;
widgetsTabPanel.append(
  macroShell,
  fedShell,
  tradingAlertsShell,
  circuitBreakersShell,
  mocImbalanceShell,
  vixRiskShell,
  esSnapshotShell,
);
newsTabPanel.append(newsShell);
summaryTabPanel.append(summaryShell);

dashboardLayout.insertBefore(layersShell, chartShell);
dashboardLayout.insertBefore(leftRail, chartShell);
rightRail.append(strategyShell, decisionShell, marketTabsShell);
dashboardLayout.append(rightRail);

const canvas = document.querySelector<HTMLCanvasElement>("#chartCanvas")!;
const shell = document.querySelector<HTMLElement>(".chart-shell")!;
const emptyState = document.querySelector<HTMLDivElement>("#emptyState")!;
const candleSettingsButton = document.querySelector<HTMLButtonElement>("#candleSettingsButton")!;
const candleSettingsMenu = document.querySelector<HTMLDivElement>("#candleSettingsMenu")!;
const candleWidthInput = document.querySelector<HTMLInputElement>("#candleWidthInput")!;
const candleWidthValue = document.querySelector<HTMLElement>("#candleWidthValue")!;
const wickToggle = document.querySelector<HTMLInputElement>("#wickToggle")!;
const volumeToggle = document.querySelector<HTMLInputElement>("#volumeToggle")!;
const priceLineToggle = document.querySelector<HTMLInputElement>("#priceLineToggle")!;
const visualConditionsButton = document.querySelector<HTMLButtonElement>("#visualConditionsButton")!;
const layerBackgroundsButton = document.querySelector<HTMLButtonElement>("#layerBackgroundsButton")!;
const ohlc = document.querySelector<HTMLSpanElement>("#ohlc")!;
const chartTitle = document.querySelector<HTMLSpanElement>("#chartTitle")!;
const sourceBadge = document.querySelector<HTMLSpanElement>("#sourceBadge")!;
const marketStatusBadge = document.querySelector<HTMLSpanElement>("#marketStatusBadge")!;
const regimeLayer = document.querySelector<HTMLDivElement>("#regimeLayer")!;
const sessionLayer = document.querySelector<HTMLDivElement>("#sessionLayer")!;
const eventLayer = document.querySelector<HTMLDivElement>("#eventLayer")!;
const strategySummary = document.querySelector<HTMLSpanElement>("#strategySummary")!;
const contextUpdatedAt = document.querySelector<HTMLSpanElement>("#contextUpdatedAt")!;
const strategyList = document.querySelector<HTMLDivElement>("#strategyList")!;
const decisionAction = document.querySelector<HTMLSpanElement>("#decisionAction")!;
const decisionBias = document.querySelector<HTMLSpanElement>("#decisionBias")!;
const decisionRisk = document.querySelector<HTMLSpanElement>("#decisionRisk")!;
const decisionChecklist = document.querySelector<HTMLDivElement>("#decisionChecklist")!;
const marketForecastPanel = document.querySelector<HTMLDivElement>("#marketForecastPanel")!;
const widgetsTabButton = document.querySelector<HTMLButtonElement>("#widgetsTabButton")!;
const newsTabButton = document.querySelector<HTMLButtonElement>("#newsTabButton")!;
const summaryTabButton = document.querySelector<HTMLButtonElement>("#summaryTabButton")!;
const macroCalendar = document.querySelector<HTMLElement>(".macro-calendar")!;
const macroToggleButton = document.querySelector<HTMLButtonElement>("#macroToggleButton")!;
const macroSummary = document.querySelector<HTMLSpanElement>("#macroSummary")!;
const macroTabMeta = document.querySelector<HTMLSpanElement>("#macroTabMeta")!;
const macroTabIcon = document.querySelector<HTMLSpanElement>("#macroTabIcon")!;
const macroPanel = document.querySelector<HTMLDivElement>("#macroPanel")!;
const macroRefreshButton = document.querySelector<HTMLButtonElement>("#macroRefreshButton")!;
const macroNext = document.querySelector<HTMLDivElement>("#macroNext")!;
const macroList = document.querySelector<HTMLDivElement>("#macroList")!;
const fedCalendar = document.querySelector<HTMLElement>(".fed-calendar")!;
const fedToggleButton = document.querySelector<HTMLButtonElement>("#fedToggleButton")!;
const fedSummary = document.querySelector<HTMLSpanElement>("#fedSummary")!;
const fedTabMeta = document.querySelector<HTMLSpanElement>("#fedTabMeta")!;
const fedTabIcon = document.querySelector<HTMLSpanElement>("#fedTabIcon")!;
const fedPanel = document.querySelector<HTMLDivElement>("#fedPanel")!;
const fedRefreshButton = document.querySelector<HTMLButtonElement>("#fedRefreshButton")!;
const fedNext = document.querySelector<HTMLDivElement>("#fedNext")!;
const fedList = document.querySelector<HTMLDivElement>("#fedList")!;
const tradingAlertsCalendar = document.querySelector<HTMLElement>(".trading-alerts-calendar")!;
const tradingAlertsToggleButton = document.querySelector<HTMLButtonElement>("#tradingAlertsToggleButton")!;
const tradingAlertsSummary = document.querySelector<HTMLSpanElement>("#tradingAlertsSummary")!;
const tradingAlertsTabMeta = document.querySelector<HTMLSpanElement>("#tradingAlertsTabMeta")!;
const tradingAlertsTabIcon = document.querySelector<HTMLSpanElement>("#tradingAlertsTabIcon")!;
const tradingAlertsPanel = document.querySelector<HTMLDivElement>("#tradingAlertsPanel")!;
const tradingAlertsRefreshButton = document.querySelector<HTMLButtonElement>("#tradingAlertsRefreshButton")!;
const tradingAlertsNext = document.querySelector<HTMLDivElement>("#tradingAlertsNext")!;
const tradingAlertsList = document.querySelector<HTMLDivElement>("#tradingAlertsList")!;
const circuitBreakersCalendar = document.querySelector<HTMLElement>(".circuit-breakers-calendar")!;
const circuitBreakersToggleButton = document.querySelector<HTMLButtonElement>("#circuitBreakersToggleButton")!;
const circuitBreakersSummary = document.querySelector<HTMLSpanElement>("#circuitBreakersSummary")!;
const circuitBreakersTabMeta = document.querySelector<HTMLSpanElement>("#circuitBreakersTabMeta")!;
const circuitBreakersTabIcon = document.querySelector<HTMLSpanElement>("#circuitBreakersTabIcon")!;
const circuitBreakersPanel = document.querySelector<HTMLDivElement>("#circuitBreakersPanel")!;
const circuitBreakersRefreshButton = document.querySelector<HTMLButtonElement>("#circuitBreakersRefreshButton")!;
const circuitBreakersNext = document.querySelector<HTMLDivElement>("#circuitBreakersNext")!;
const circuitBreakersList = document.querySelector<HTMLDivElement>("#circuitBreakersList")!;
const mocImbalanceCalendar = document.querySelector<HTMLElement>(".moc-imbalance-calendar")!;
const mocImbalanceToggleButton = document.querySelector<HTMLButtonElement>("#mocImbalanceToggleButton")!;
const mocImbalanceSummary = document.querySelector<HTMLSpanElement>("#mocImbalanceSummary")!;
const mocImbalanceTabMeta = document.querySelector<HTMLSpanElement>("#mocImbalanceTabMeta")!;
const mocImbalanceTabIcon = document.querySelector<HTMLSpanElement>("#mocImbalanceTabIcon")!;
const mocImbalancePanel = document.querySelector<HTMLDivElement>("#mocImbalancePanel")!;
const mocImbalanceRefreshButton = document.querySelector<HTMLButtonElement>("#mocImbalanceRefreshButton")!;
const mocImbalanceNext = document.querySelector<HTMLDivElement>("#mocImbalanceNext")!;
const mocImbalanceList = document.querySelector<HTMLDivElement>("#mocImbalanceList")!;
const vixRiskCalendar = document.querySelector<HTMLElement>(".vix-risk-calendar")!;
const vixRiskToggleButton = document.querySelector<HTMLButtonElement>("#vixRiskToggleButton")!;
const vixRiskSummary = document.querySelector<HTMLSpanElement>("#vixRiskSummary")!;
const vixRiskTabMeta = document.querySelector<HTMLSpanElement>("#vixRiskTabMeta")!;
const vixRiskTabIcon = document.querySelector<HTMLSpanElement>("#vixRiskTabIcon")!;
const vixRiskPanel = document.querySelector<HTMLDivElement>("#vixRiskPanel")!;
const vixRiskRefreshButton = document.querySelector<HTMLButtonElement>("#vixRiskRefreshButton")!;
const vixRiskNext = document.querySelector<HTMLDivElement>("#vixRiskNext")!;
const vixRiskList = document.querySelector<HTMLDivElement>("#vixRiskList")!;
const esSnapshotCalendar = document.querySelector<HTMLElement>(".es-snapshot-calendar")!;
const esSnapshotToggleButton = document.querySelector<HTMLButtonElement>("#esSnapshotToggleButton")!;
const esSnapshotSummary = document.querySelector<HTMLSpanElement>("#esSnapshotSummary")!;
const esSnapshotTabMeta = document.querySelector<HTMLSpanElement>("#esSnapshotTabMeta")!;
const esSnapshotTabIcon = document.querySelector<HTMLSpanElement>("#esSnapshotTabIcon")!;
const esSnapshotPanel = document.querySelector<HTMLDivElement>("#esSnapshotPanel")!;
const esSnapshotRefreshButton = document.querySelector<HTMLButtonElement>("#esSnapshotRefreshButton")!;
const esSnapshotNext = document.querySelector<HTMLDivElement>("#esSnapshotNext")!;
const esSnapshotList = document.querySelector<HTMLDivElement>("#esSnapshotList")!;
const newsFeedSummary = document.querySelector<HTMLElement>("#newsFeedSummary")!;
const newsFeedRefreshButton = document.querySelector<HTMLButtonElement>("#newsFeedRefreshButton")!;
const newsFeedList = document.querySelector<HTMLDivElement>("#newsFeedList")!;
const newsFeedSources = document.querySelector<HTMLDivElement>("#newsFeedSources")!;
const tradeSummaryHeadline = document.querySelector<HTMLElement>("#tradeSummaryHeadline")!;
const tradeSummaryRefreshButton = document.querySelector<HTMLButtonElement>("#tradeSummaryRefreshButton")!;
const tradeSummaryStatus = document.querySelector<HTMLDivElement>("#tradeSummaryStatus")!;
const tradeSummaryBody = document.querySelector<HTMLDivElement>("#tradeSummaryBody")!;
const algoVotingEnsembleTabButton = document.querySelector<HTMLButtonElement>("#algoVotingEnsembleTabButton")!;
const algoVotingEnsemblePanel = document.querySelector<HTMLDivElement>("#algoVotingEnsemblePanel")!;
const algoWeightedVotingTabButton = document.querySelector<HTMLButtonElement>("#algoWeightedVotingTabButton")!;
const algoWeightedVotingPanel = document.querySelector<HTMLDivElement>("#algoWeightedVotingPanel")!;
const algoConfidenceAggregationTabButton = document.querySelector<HTMLButtonElement>("#algoConfidenceAggregationTabButton")!;
const algoConfidenceAggregationPanel = document.querySelector<HTMLDivElement>("#algoConfidenceAggregationPanel")!;
const algoRegimeSelectionTabButton = document.querySelector<HTMLButtonElement>("#algoRegimeSelectionTabButton")!;
const algoRegimeSelectionPanel = document.querySelector<HTMLDivElement>("#algoRegimeSelectionPanel")!;
const algoMetaStrategyTabButton = document.querySelector<HTMLButtonElement>("#algoMetaStrategyTabButton")!;
const algoMetaStrategyPanel = document.querySelector<HTMLDivElement>("#algoMetaStrategyPanel")!;
const algoFinalSignal = document.querySelector<HTMLDivElement>("#algoFinalSignal")!;
const algoVoteCounts = document.querySelector<HTMLDivElement>("#algoVoteCounts")!;
const algoVoteList = document.querySelector<HTMLDivElement>("#algoVoteList")!;
const algoVotesToggle = document.querySelector<HTMLButtonElement>("#algoVotesToggle")!;
const algoVotesToggleMeta = document.querySelector<HTMLElement>("#algoVotesToggleMeta")!;
const algoVotesToggleIcon = document.querySelector<HTMLElement>("#algoVotesToggleIcon")!;
const algoIntradayTradesToggle = document.querySelector<HTMLButtonElement>("#algoIntradayTradesToggle")!;
const algoIntradayTradesToggleIcon = document.querySelector<HTMLElement>("#algoIntradayTradesToggleIcon")!;
const algoIntradayTradesPanel = document.querySelector<HTMLDivElement>("#algoIntradayTradesPanel")!;
const algoIntradayTradesSummary = document.querySelector<HTMLDivElement>("#algoIntradayTradesSummary")!;
const algoIntradayTradesTable = document.querySelector<HTMLTableSectionElement>("#algoIntradayTradesTable")!;
const algoBacktest1mButton = document.querySelector<HTMLButtonElement>("#algoBacktest1mButton")!;
const algoBacktest5mButton = document.querySelector<HTMLButtonElement>("#algoBacktest5mButton")!;
const algoBacktestTradingButton = document.querySelector<HTMLButtonElement>("#algoBacktestTradingButton")!;
const algoTradePlanTitle = document.querySelector<HTMLDivElement>("#algoTradePlanTitle")!;
const algoTradePlan = document.querySelector<HTMLDivElement>("#algoTradePlan")!;
const algoTableWrap = document.querySelector<HTMLDivElement>(".algo-table-wrap")!;
const algoTradesTable = document.querySelector<HTMLTableSectionElement>("#algoTradesTable")!;
const tradingSettingsMount = document.querySelector<HTMLDivElement>("#tradingSettingsMount")!;
const algoResultsBody = document.querySelector<HTMLDivElement>("#algoResultsBody")!;
const weightedFinalSignal = document.querySelector<HTMLDivElement>("#weightedFinalSignal")!;
const weightedScoreGrid = document.querySelector<HTMLDivElement>("#weightedScoreGrid")!;
const weightedSummary = document.querySelector<HTMLDivElement>("#weightedSummary")!;
const weightedTradingSettingsMount = document.querySelector<HTMLDivElement>("#weightedTradingSettingsMount")!;
const weightedStrategiesToggle = document.querySelector<HTMLButtonElement>("#weightedStrategiesToggle")!;
const weightedStrategiesToggleMeta = document.querySelector<HTMLElement>("#weightedStrategiesToggleMeta")!;
const weightedStrategiesToggleIcon = document.querySelector<HTMLElement>("#weightedStrategiesToggleIcon")!;
const weightedStrategiesList = document.querySelector<HTMLDivElement>("#weightedStrategiesList")!;
const weightedDataToggle = document.querySelector<HTMLButtonElement>("#weightedDataToggle")!;
const weightedDataToggleMeta = document.querySelector<HTMLElement>("#weightedDataToggleMeta")!;
const weightedDataToggleIcon = document.querySelector<HTMLElement>("#weightedDataToggleIcon")!;
const weightedDataGrid = document.querySelector<HTMLDivElement>("#weightedDataGrid")!;
const weightedGatesToggle = document.querySelector<HTMLButtonElement>("#weightedGatesToggle")!;
const weightedGatesToggleMeta = document.querySelector<HTMLElement>("#weightedGatesToggleMeta")!;
const weightedGatesToggleIcon = document.querySelector<HTMLElement>("#weightedGatesToggleIcon")!;
const weightedGateList = document.querySelector<HTMLDivElement>("#weightedGateList")!;
const weightedControlsToggle = document.querySelector<HTMLButtonElement>("#weightedControlsToggle")!;
const weightedControlsToggleIcon = document.querySelector<HTMLElement>("#weightedControlsToggleIcon")!;
const weightedControlRules = document.querySelector<HTMLDivElement>("#weightedControlRules")!;
const confidenceFinalSignal = document.querySelector<HTMLDivElement>("#confidenceFinalSignal")!;
const confidenceScoreGrid = document.querySelector<HTMLDivElement>("#confidenceScoreGrid")!;
const confidenceSummary = document.querySelector<HTMLDivElement>("#confidenceSummary")!;
const confidenceTradingSettingsMount = document.querySelector<HTMLDivElement>("#confidenceTradingSettingsMount")!;
const confidenceBacktestStatusLabel = document.querySelector<HTMLSpanElement>("#confidenceBacktestStatusLabel")!;
const confidenceBacktestSummary = document.querySelector<HTMLDivElement>("#confidenceBacktestSummary")!;
const confidenceBacktestTradesTable = document.querySelector<HTMLTableSectionElement>("#confidenceBacktestTradesTable")!;
const confidenceRequirementsToggle = document.querySelector<HTMLButtonElement>("#confidenceRequirementsToggle")!;
const confidenceRequirementsToggleMeta = document.querySelector<HTMLElement>("#confidenceRequirementsToggleMeta")!;
const confidenceRequirementsToggleIcon = document.querySelector<HTMLElement>("#confidenceRequirementsToggleIcon")!;
const confidenceRequirementsPanel = document.querySelector<HTMLDivElement>("#confidenceRequirementsPanel")!;
const confidenceStrategiesToggle = document.querySelector<HTMLButtonElement>("#confidenceStrategiesToggle")!;
const confidenceStrategiesToggleMeta = document.querySelector<HTMLElement>("#confidenceStrategiesToggleMeta")!;
const confidenceStrategiesToggleIcon = document.querySelector<HTMLElement>("#confidenceStrategiesToggleIcon")!;
const confidenceStrategiesList = document.querySelector<HTMLDivElement>("#confidenceStrategiesList")!;
const regimeFinalSignal = document.querySelector<HTMLDivElement>("#regimeFinalSignal")!;
const regimeScoreGrid = document.querySelector<HTMLDivElement>("#regimeScoreGrid")!;
const regimeSummary = document.querySelector<HTMLDivElement>("#regimeSummary")!;
const regimeTradingSettingsMount = document.querySelector<HTMLDivElement>("#regimeTradingSettingsMount")!;
const regimeIndicatorsToggle = document.querySelector<HTMLButtonElement>("#regimeIndicatorsToggle")!;
const regimeIndicatorsToggleMeta = document.querySelector<HTMLElement>("#regimeIndicatorsToggleMeta")!;
const regimeIndicatorsToggleIcon = document.querySelector<HTMLElement>("#regimeIndicatorsToggleIcon")!;
const regimeFeatureGrid = document.querySelector<HTMLDivElement>("#regimeFeatureGrid")!;
const regimeStrategiesToggle = document.querySelector<HTMLButtonElement>("#regimeStrategiesToggle")!;
const regimeStrategiesToggleMeta = document.querySelector<HTMLElement>("#regimeStrategiesToggleMeta")!;
const regimeStrategiesToggleIcon = document.querySelector<HTMLElement>("#regimeStrategiesToggleIcon")!;
const regimeStrategiesList = document.querySelector<HTMLDivElement>("#regimeStrategiesList")!;
const metaFinalSignal = document.querySelector<HTMLDivElement>("#metaFinalSignal")!;
const metaScoreGrid = document.querySelector<HTMLDivElement>("#metaScoreGrid")!;
const metaSummary = document.querySelector<HTMLDivElement>("#metaSummary")!;
const metaGateList = document.querySelector<HTMLDivElement>("#metaGateList")!;
const metaStrategiesToggle = document.querySelector<HTMLButtonElement>("#metaStrategiesToggle")!;
const metaStrategiesToggleMeta = document.querySelector<HTMLElement>("#metaStrategiesToggleMeta")!;
const metaStrategiesToggleIcon = document.querySelector<HTMLElement>("#metaStrategiesToggleIcon")!;
const metaStrategiesPanel = document.querySelector<HTMLDivElement>("#metaStrategiesPanel")!;
const metaChecksToggle = document.querySelector<HTMLButtonElement>("#metaChecksToggle")!;
const metaChecksToggleMeta = document.querySelector<HTMLElement>("#metaChecksToggleMeta")!;
const metaChecksToggleIcon = document.querySelector<HTMLElement>("#metaChecksToggleIcon")!;
const metaMlReadinessBox = document.querySelector<HTMLDivElement>("#metaMlReadinessBox")!;
const metaFamilyGrid = document.querySelector<HTMLDivElement>("#metaFamilyGrid")!;
const metaStrategiesList = document.querySelector<HTMLDivElement>("#metaStrategiesList")!;
const zoomLevel = document.querySelector<HTMLSpanElement>("#zoomLevel")!;
const feedSelect = document.querySelector<HTMLSelectElement>("#feedSelect")!;
const startInput = document.querySelector<HTMLInputElement>("#startInput")!;
const endInput = document.querySelector<HTMLInputElement>("#endInput")!;
const refreshSelect = document.querySelector<HTMLSelectElement>("#refreshSelect")!;
const lastCandleStatus = document.querySelector<HTMLSpanElement>("#lastCandleStatus")!;
const refreshStatus = document.querySelector<HTMLSpanElement>("#refreshStatus")!;
const quoteSymbol = document.querySelector<HTMLSpanElement>("#quoteSymbol")!;
const quotePrice = document.querySelector<HTMLDivElement>("#quotePrice")!;
const quoteChange = document.querySelector<HTMLDivElement>("#quoteChange")!;
const quoteAsk = document.querySelector<HTMLSpanElement>("#quoteAsk")!;
const quoteBid = document.querySelector<HTMLSpanElement>("#quoteBid")!;
const quoteStats = document.querySelector<HTMLDivElement>("#quoteStats")!;
const quotePosition = document.querySelector<HTMLDivElement>("#quotePosition")!;
const tradeToggleButton = document.querySelector<HTMLButtonElement>("#tradeToggleButton")!;
const buyOrderButton = document.querySelector<HTMLButtonElement>("#buyOrderButton")!;
const sellOrderButton = document.querySelector<HTMLButtonElement>("#sellOrderButton")!;
const closePositionButton = document.querySelector<HTMLButtonElement>("#closePositionButton")!;
const clearTradeHistoryButton = document.querySelector<HTMLButtonElement>("#clearTradeHistoryButton")!;
const ensembleTradingWindowTab = document.querySelector<HTMLButtonElement>("#ensembleTradingWindowTab")!;
const weightedTradingWindowTab = document.querySelector<HTMLButtonElement>("#weightedTradingWindowTab")!;
const confidenceTradingWindowTab = document.querySelector<HTMLButtonElement>("#confidenceTradingWindowTab")!;
const regimeTradingWindowTab = document.querySelector<HTMLButtonElement>("#regimeTradingWindowTab")!;
const metaTradingWindowTab = document.querySelector<HTMLButtonElement>("#metaTradingWindowTab")!;
const openOrderControls = document.querySelector<HTMLDivElement>("#openOrderControls")!;
const tradeHistoryTitle = document.querySelector<HTMLSpanElement>("#tradeHistoryTitle")!;
const tradeHistoryBody = document.querySelector<HTMLTableSectionElement>("#tradeHistoryBody")!;
const tradeHistoryBalance = document.querySelector<HTMLTableCellElement>("#tradeHistoryBalance")!;

refreshSelect.value = String(state.refreshSeconds);
feedSelect.value = state.feed;
startInput.value = state.start;
endInput.value = state.end;
updateCandleSettingsControls();
updateOverlayToggleControls();
updateZoomLevel();
updateTradeToggleButton();
setAlgoTab(state.algoTab);

function makeTimeframeButtons(container: Element, compact = false) {
  const items = compact ? timeframeItems.filter((item) => item.value === "1Min" || item.value === "5Min") : timeframeItems;
  container.innerHTML = items
    .map(
      (item) => `
        <button class="tf-button ${item.value === state.timeframe ? "active" : ""}" data-timeframe="${item.value}">
          ${compact ? item.label : item.label.replace("m", "m")}
        </button>
      `,
    )
    .join("");
}

function setMarketRailTab(tab: "widgets" | "news" | "summary") {
  const showWidgets = tab === "widgets";
  const showNews = tab === "news";
  const showSummary = tab === "summary";
  widgetsTabButton.classList.toggle("active", showWidgets);
  newsTabButton.classList.toggle("active", showNews);
  summaryTabButton.classList.toggle("active", showSummary);
  widgetsTabButton.setAttribute("aria-selected", String(showWidgets));
  newsTabButton.setAttribute("aria-selected", String(showNews));
  summaryTabButton.setAttribute("aria-selected", String(showSummary));
  widgetsTabPanel.hidden = !showWidgets;
  newsTabPanel.hidden = !showNews;
  summaryTabPanel.hidden = !showSummary;
  widgetsTabPanel.classList.toggle("active", showWidgets);
  newsTabPanel.classList.toggle("active", showNews);
  summaryTabPanel.classList.toggle("active", showSummary);
  if (showSummary) {
    void loadTradeSummary();
  }
}

type ExpandableTabKey =
  | "macro"
  | "fed"
  | "tradingAlerts"
  | "circuitBreakers"
  | "mocImbalance"
  | "vixRisk"
  | "esSnapshot";

function toggleExpandableTab(key: ExpandableTabKey) {
  const nextExpanded = !expandedStateFor(key);
  state.macroExpanded = key === "macro" ? nextExpanded : false;
  state.fedExpanded = key === "fed" ? nextExpanded : false;
  state.tradingAlertsExpanded = key === "tradingAlerts" ? nextExpanded : false;
  state.circuitBreakersExpanded = key === "circuitBreakers" ? nextExpanded : false;
  state.mocImbalanceExpanded = key === "mocImbalance" ? nextExpanded : false;
  state.vixRiskExpanded = key === "vixRisk" ? nextExpanded : false;
  state.esSnapshotExpanded = key === "esSnapshot" ? nextExpanded : false;
  saveUiState();
  updateAllExpandableTabs();
  if (nextExpanded) {
    scrollExpandedMarketTabIntoView(key);
  }
}

function expandedStateFor(key: ExpandableTabKey) {
  const states: Record<ExpandableTabKey, boolean> = {
    macro: state.macroExpanded,
    fed: state.fedExpanded,
    tradingAlerts: state.tradingAlertsExpanded,
    circuitBreakers: state.circuitBreakersExpanded,
    mocImbalance: state.mocImbalanceExpanded,
    vixRisk: state.vixRiskExpanded,
    esSnapshot: state.esSnapshotExpanded,
  };
  return states[key];
}

function updateAllExpandableTabs() {
  updateMacroExpansion();
  updateFedExpansion();
  updateTradingAlertsExpansion();
  updateCircuitBreakersExpansion();
  updateMocImbalanceExpansion();
  updateVixRiskExpansion();
  updateEsSnapshotExpansion();
}

function scrollExpandedMarketTabIntoView(key: ExpandableTabKey) {
  const shells: Record<ExpandableTabKey, HTMLElement> = {
    macro: macroShell,
    fed: fedShell,
    tradingAlerts: tradingAlertsShell,
    circuitBreakers: circuitBreakersShell,
    mocImbalance: mocImbalanceShell,
    vixRisk: vixRiskShell,
    esSnapshot: esSnapshotShell,
  };
  window.requestAnimationFrame(() => {
    shells[key].scrollIntoView({ block: "nearest", inline: "nearest" });
  });
}

makeTimeframeButtons(document.querySelector(".status-timeframes")!, true);

document.addEventListener("click", (event) => {
  const target = event.target as HTMLElement;
  const timeframe = target.closest<HTMLButtonElement>("[data-timeframe]")?.dataset.timeframe as Timeframe | undefined;
  if (timeframe) {
    state.timeframe = timeframe;
    saveUiState();
    resetViewport();
    resetZoomState();
    makeTimeframeButtons(document.querySelector(".status-timeframes")!, true);
    void loadCandles();
  }
});

document.querySelector("#refreshButton")!.addEventListener("click", () => {
  void loadCandles({ refresh: true });
});

candleSettingsButton.addEventListener("click", (event) => {
  event.stopPropagation();
  setCandleSettingsOpen(candleSettingsMenu.hidden);
});

candleSettingsMenu.addEventListener("click", (event) => {
  event.stopPropagation();
});

candleWidthInput.addEventListener("input", () => {
  state.candleWidthPercent = Number(candleWidthInput.value);
  saveUiState();
  updateCandleSettingsControls();
  drawChart();
});

wickToggle.addEventListener("change", () => {
  state.showWicks = wickToggle.checked;
  saveUiState();
  drawChart();
});

volumeToggle.addEventListener("change", () => {
  state.showVolume = volumeToggle.checked;
  saveUiState();
  drawChart();
});

priceLineToggle.addEventListener("change", () => {
  state.showPriceLine = priceLineToggle.checked;
  saveUiState();
  drawChart();
});

visualConditionsButton.addEventListener("click", () => {
  state.showVisualConditions = !state.showVisualConditions;
  saveUiState();
  updateOverlayToggleControls();
  drawChart();
});

layerBackgroundsButton.addEventListener("click", () => {
  state.showLayerBackgrounds = !state.showLayerBackgrounds;
  saveUiState();
  updateOverlayToggleControls();
  drawChart();
});

document.addEventListener("click", () => {
  setCandleSettingsOpen(false);
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    setCandleSettingsOpen(false);
  }
});

document.querySelector("#zoomInButton")!.addEventListener("click", () => {
  zoomChart("in");
});

document.querySelector("#zoomOutButton")!.addEventListener("click", () => {
  zoomChart("out");
});

document.querySelector("#zoomResetButton")!.addEventListener("click", () => {
  resetZoom();
});

document.querySelector("#rangeButton")!.addEventListener("click", () => {
  state.start = startInput.value;
  state.end = endInput.value;
  saveUiState();
  resetViewport();
  void loadCandles({ refresh: true });
});

document.querySelector("#latestButton")!.addEventListener("click", () => {
  state.start = "";
  state.end = "";
  startInput.value = "";
  endInput.value = "";
  saveUiState();
  resetViewport();
  void loadCandles({ refresh: true });
});

tradeToggleButton.addEventListener("click", () => {
  state.tradingEnabled = !state.tradingEnabled;
  saveUiState();
  updateTradeToggleButton();
  updateQuoteCard(currentCandle());
  if (state.tradingEnabled) {
    maybeAutoSubmitAllAlgorithms();
  }
});

buyOrderButton.addEventListener("click", () => {
  recordTradeHistory("Buy", state.tradingWindowMode);
});

sellOrderButton.addEventListener("click", () => {
  submitSelectedOpenOrderSell(state.tradingWindowMode);
});

clearTradeHistoryButton.addEventListener("click", () => {
  clearTodaysTradeHistoryForMode(state.tradingWindowMode);
  if (state.tradingWindowMode === "ensemble") {
    clearRecommendedTargetOverrides();
  }
  suppressCurrentAutomaticTargetOrder(state.tradingWindowMode);
  updateAlgorithmPanel(visibleCandles());
  updateQuoteCard(currentCandle());
  void saveBrowserStorageSnapshot("clear-today-trade-history");
});

ensembleTradingWindowTab.addEventListener("click", () => {
  setTradingWindowMode("ensemble");
});

weightedTradingWindowTab.addEventListener("click", () => {
  setTradingWindowMode("weighted");
});

confidenceTradingWindowTab.addEventListener("click", () => {
  setTradingWindowMode("confidence");
});

regimeTradingWindowTab.addEventListener("click", () => {
  setTradingWindowMode("regime");
});

metaTradingWindowTab.addEventListener("click", () => {
  setTradingWindowMode("meta");
});

openOrderControls.addEventListener("change", (event) => {
  const target = event.target as HTMLElement;
  const sellSetupSelect = target.closest<HTMLSelectElement>("[data-selected-sell-setup]");
  if (sellSetupSelect) {
    state.selectedSellSetupByMode[state.tradingWindowMode] = sellSetupSelect.value;
    state.sellSetupSelectionLockedByMode[state.tradingWindowMode] = true;
    saveUiState();
    updateQuoteCard(currentCandle());
    return;
  }
  const submitSelect = target.closest<HTMLSelectElement>("[data-order-submit-mode]");
  if (submitSelect) {
    setOrderControlModesForMode(state.tradingWindowMode, {
      ...orderControlModesForMode(state.tradingWindowMode),
      [submitSelect.dataset.orderSubmitMode ?? ""]: submitSelect.value as SubmitOrderMode,
    });
    maybeAutoSubmitOpenOrderControls();
    return;
  }
  const lotInput = target.closest<HTMLInputElement | HTMLSelectElement>("[data-lot-order-setting]");
  if (!lotInput) {
    return;
  }
  updateLotOrderOverride(lotInput);
});

openOrderControls.addEventListener("input", (event) => {
  const input = (event.target as HTMLElement).closest<HTMLInputElement>("[data-lot-order-setting]");
  if (!input) {
    return;
  }
  updateLotOrderOverride(input);
});

openOrderControls.addEventListener("click", (event) => {
  const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-sell-lot-id]");
  if (!button) {
    return;
  }
  submitOpenOrderLot(button.dataset.sellLotId ?? "", false, state.tradingWindowMode);
});

closePositionButton.addEventListener("click", () => {
  const mode = state.tradingWindowMode;
  const latest = latestExecutionCandleForMode(mode);
  if (!latest || !canSubmitTrades()) {
    return;
  }
  const lots = openOrderLots(mode);
  if (!lots.length) {
    return;
  }
  lots.forEach((lot) => {
    appendTradeHistory("Sell", lot.remainingQuantity, latest.close, lot.id, mode, {
      submitMode: "Close Position",
      trigger: "Close Position button",
    });
  });
  if (mode === "ensemble") {
    clearRecommendedTargetOverrides();
  }
  updateAlgorithmPanel(visibleCandles());
  updateQuoteCard(latest);
});

feedSelect.addEventListener("change", () => {
  state.feed = feedSelect.value;
  saveUiState();
  state.algoBacktestCandles = [];
  state.algoBacktestResult = null;
  resetViewport();
  void loadCandles();
  void loadAlgoBacktestCandles();
});

refreshSelect.addEventListener("change", () => {
  state.refreshSeconds = Number(refreshSelect.value);
  saveUiState();
  scheduleAutoRefresh();
});

widgetsTabButton.addEventListener("click", () => {
  setMarketRailTab("widgets");
});

newsTabButton.addEventListener("click", () => {
  setMarketRailTab("news");
});

summaryTabButton.addEventListener("click", () => {
  setMarketRailTab("summary");
});

macroToggleButton.addEventListener("click", () => {
  toggleExpandableTab("macro");
});

macroRefreshButton.addEventListener("click", () => {
  void loadMacroEvents();
});

fedToggleButton.addEventListener("click", () => {
  toggleExpandableTab("fed");
});

fedRefreshButton.addEventListener("click", () => {
  void loadFedEvents();
});

tradingAlertsToggleButton.addEventListener("click", () => {
  toggleExpandableTab("tradingAlerts");
});

tradingAlertsRefreshButton.addEventListener("click", () => {
  void loadTradingAlerts();
});

circuitBreakersToggleButton.addEventListener("click", () => {
  toggleExpandableTab("circuitBreakers");
});

circuitBreakersRefreshButton.addEventListener("click", () => {
  void loadCircuitBreakers();
});

mocImbalanceToggleButton.addEventListener("click", () => {
  toggleExpandableTab("mocImbalance");
});

mocImbalanceRefreshButton.addEventListener("click", () => {
  void loadMocImbalance();
});

vixRiskToggleButton.addEventListener("click", () => {
  toggleExpandableTab("vixRisk");
});

vixRiskRefreshButton.addEventListener("click", () => {
  void loadVixRisk();
});

esSnapshotToggleButton.addEventListener("click", () => {
  toggleExpandableTab("esSnapshot");
});

esSnapshotRefreshButton.addEventListener("click", () => {
  void loadEsSnapshot();
});

newsFeedRefreshButton.addEventListener("click", () => {
  void loadSpyNews();
});

tradeSummaryRefreshButton.addEventListener("click", () => {
  void loadTradeSummary();
});

algoVotingEnsembleTabButton.addEventListener("click", () => {
  setAlgoTab("voting");
});

algoWeightedVotingTabButton.addEventListener("click", () => {
  setAlgoTab("weighted");
});

algoConfidenceAggregationTabButton.addEventListener("click", () => {
  setAlgoTab("confidence");
});

algoRegimeSelectionTabButton.addEventListener("click", () => {
  setAlgoTab("regime");
});

algoMetaStrategyTabButton.addEventListener("click", () => {
  setAlgoTab("meta");
});

algoVotesToggle.addEventListener("click", () => {
  state.algoVotesExpanded = !state.algoVotesExpanded;
  saveUiState();
  renderAlgoVotesExpandedState();
  updateWeightedVotingPanel();
});

regimeStrategiesToggle.addEventListener("click", () => {
  state.regimeStrategiesExpanded = !state.regimeStrategiesExpanded;
  saveUiState();
  if (state.regimeStrategiesExpanded) {
    updateRegimeSelectionPanel();
  } else {
    renderRegimeStrategiesExpandedState();
  }
});

regimeIndicatorsToggle.addEventListener("click", () => {
  state.regimeIndicatorsExpanded = !state.regimeIndicatorsExpanded;
  saveUiState();
  if (state.regimeIndicatorsExpanded) {
    updateRegimeSelectionPanel();
  } else {
    renderRegimeIndicatorsExpandedState();
  }
});

metaStrategiesToggle.addEventListener("click", () => {
  state.metaStrategiesExpanded = !state.metaStrategiesExpanded;
  saveUiState();
  renderMetaStrategiesExpandedState();
});

metaChecksToggle.addEventListener("click", () => {
  state.metaChecksExpanded = !state.metaChecksExpanded;
  saveUiState();
  renderMetaChecksExpandedState();
});

metaMlReadinessBox.addEventListener("click", (event) => {
  const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-meta-training-action]");
  if (!button) {
    return;
  }
  void loadMetaStrategyTrainingStatus({ train: button.dataset.metaTrainingAction === "train" });
});

weightedStrategiesToggle.addEventListener("click", () => {
  state.weightedVotingExpanded = !state.weightedVotingExpanded;
  saveUiState();
  renderWeightedStrategiesExpandedState();
});

weightedDataToggle.addEventListener("click", () => {
  state.weightedDataExpanded = !state.weightedDataExpanded;
  saveUiState();
  renderWeightedDataExpandedState();
});

weightedGatesToggle.addEventListener("click", () => {
  state.weightedGatesExpanded = !state.weightedGatesExpanded;
  saveUiState();
  renderWeightedGatesExpandedState();
});

weightedControlsToggle.addEventListener("click", () => {
  state.weightedControlsExpanded = !state.weightedControlsExpanded;
  saveUiState();
  renderWeightedControlsExpandedState();
});

confidenceRequirementsToggle.addEventListener("click", () => {
  state.confidenceRequirementsExpanded = !state.confidenceRequirementsExpanded;
  saveUiState();
  renderConfidenceRequirementsExpandedState();
});

confidenceRequirementsPanel.addEventListener("input", (event) => {
  const input = (event.target as HTMLElement).closest<HTMLInputElement>("[data-confidence-requirement]");
  if (!input) {
    return;
  }
  const key = input.dataset.confidenceRequirement as keyof ConfidenceDecisionSettings | undefined;
  if (!key) {
    return;
  }
  state.confidenceDecisionSettings = sanitizeConfidenceDecisionSettings({
    ...state.confidenceDecisionSettings,
    [key]: Number(input.value),
  });
  saveConfidenceDecisionSettings(state.confidenceDecisionSettings);
  updateConfidenceAggregationPanel();
});

confidenceStrategiesToggle.addEventListener("click", () => {
  state.confidenceStrategiesExpanded = !state.confidenceStrategiesExpanded;
  saveUiState();
  renderConfidenceStrategiesExpandedState();
});

algoIntradayTradesToggle.addEventListener("click", () => {
  state.algoIntradayTradesExpanded = !state.algoIntradayTradesExpanded;
  saveUiState();
  updateAlgoBacktestControls();
});

algoBacktest1mButton.addEventListener("click", () => {
  void setAlgoBacktestTimeframe("1Min");
});

algoBacktest5mButton.addEventListener("click", () => {
  void setAlgoBacktestTimeframe("5Min");
});

algoBacktestTradingButton.addEventListener("click", () => {
  void setAlgoBacktestTimeframe("Trading");
});

function handleTradingSettingChange(event: Event) {
  const input = (event.target as HTMLElement).closest<HTMLInputElement>("[data-trading-setting]");
  if (!input) {
    return;
  }
  const key = input.dataset.tradingSetting as keyof TradingSettings | undefined;
  if (!key || key === "positionSizingMode") {
    return;
  }
  const shouldRerender = event.type !== "input" || input.type === "checkbox";
  if (key === "useDefaultSizingSettings" || key === "pyramidingEnabled") {
    state.tradingSettings = sanitizeTradingSettings(
      { ...state.tradingSettings, [key]: input.checked },
      VOTING_MAX_ORDER_ALLOCATION_PERCENT,
    );
    saveTradingSettings(state.tradingSettings);
    clearRecommendedTargetOverrides();
    if (shouldRerender) {
      updateAlgorithmPanel(visibleCandles());
    }
    return;
  }
  if (input.value.trim() === "") {
    return;
  }
  const value = Number(input.value);
  if (!Number.isFinite(value)) {
    return;
  }
  state.tradingSettings = sanitizeTradingSettings(
    { ...state.tradingSettings, [key]: value },
    VOTING_MAX_ORDER_ALLOCATION_PERCENT,
  );
  saveTradingSettings(state.tradingSettings);
  clearRecommendedTargetOverrides();
  if (shouldRerender) {
    updateAlgorithmPanel(visibleCandles());
  }
}

document.addEventListener("input", handleTradingSettingChange);
document.addEventListener("change", handleTradingSettingChange);

function handleWeightedTradingSettingChange(event: Event) {
  const input = (event.target as HTMLElement).closest<HTMLInputElement>("[data-weighted-trading-setting]");
  if (!input) {
    return;
  }
  const key = input.dataset.weightedTradingSetting as keyof TradingSettings | undefined;
  if (!key || key === "positionSizingMode") {
    return;
  }
  const shouldRerender = event.type !== "input" || input.type === "checkbox";
  if (key === "useDefaultSizingSettings" || key === "pyramidingEnabled") {
    state.weightedTradingSettings = sanitizeTradingSettings({ ...state.weightedTradingSettings, [key]: input.checked });
    saveWeightedTradingSettings(state.weightedTradingSettings);
    if (shouldRerender) {
      updateWeightedVotingPanel();
    }
    return;
  }
  if (input.value.trim() === "") {
    return;
  }
  const value = Number(input.value);
  if (!Number.isFinite(value)) {
    return;
  }
  state.weightedTradingSettings = sanitizeTradingSettings({ ...state.weightedTradingSettings, [key]: value });
  saveWeightedTradingSettings(state.weightedTradingSettings);
  if (shouldRerender) {
    updateWeightedVotingPanel();
  }
}

document.addEventListener("input", handleWeightedTradingSettingChange);
document.addEventListener("change", handleWeightedTradingSettingChange);

function handleConfidenceTradingSettingChange(event: Event) {
  const input = (event.target as HTMLElement).closest<HTMLInputElement>("[data-confidence-trading-setting]");
  if (!input) {
    return;
  }
  const key = input.dataset.confidenceTradingSetting as keyof TradingSettings | undefined;
  if (!key || key === "positionSizingMode") {
    return;
  }
  const shouldRerender = event.type !== "input" || input.type === "checkbox";
  if (key === "useDefaultSizingSettings" || key === "pyramidingEnabled") {
    state.confidenceTradingSettings = sanitizeTradingSettings({ ...state.confidenceTradingSettings, [key]: input.checked });
    saveConfidenceTradingSettings(state.confidenceTradingSettings);
    if (shouldRerender) {
      updateConfidenceAggregationPanel();
      updateRegimeSelectionPanel();
    }
    return;
  }
  if (input.value.trim() === "") {
    return;
  }
  const value = Number(input.value);
  if (!Number.isFinite(value)) {
    return;
  }
  state.confidenceTradingSettings = sanitizeTradingSettings({ ...state.confidenceTradingSettings, [key]: value });
  saveConfidenceTradingSettings(state.confidenceTradingSettings);
  if (shouldRerender) {
    updateConfidenceAggregationPanel();
    updateRegimeSelectionPanel();
  }
}

document.addEventListener("input", handleConfidenceTradingSettingChange);
document.addEventListener("change", handleConfidenceTradingSettingChange);

function handleRegimeTradingSettingChange(event: Event) {
  const input = (event.target as HTMLElement).closest<HTMLInputElement>("[data-regime-trading-setting]");
  if (!input) {
    return;
  }
  const key = input.dataset.regimeTradingSetting as keyof TradingSettings | undefined;
  if (!key || key === "positionSizingMode") {
    return;
  }
  const shouldRerender = event.type !== "input" || input.type === "checkbox";
  if (key === "useDefaultSizingSettings" || key === "pyramidingEnabled") {
    state.regimeTradingSettings = sanitizeTradingSettings({ ...state.regimeTradingSettings, [key]: input.checked }, REGIME_MAX_ORDER_ALLOCATION_PERCENT);
    saveRegimeTradingSettings(state.regimeTradingSettings);
    if (shouldRerender) {
      updateRegimeSelectionPanel();
    }
    return;
  }
  if (input.value.trim() === "") {
    return;
  }
  const value = Number(input.value);
  if (!Number.isFinite(value)) {
    return;
  }
  state.regimeTradingSettings = sanitizeTradingSettings({ ...state.regimeTradingSettings, [key]: value }, REGIME_MAX_ORDER_ALLOCATION_PERCENT);
  saveRegimeTradingSettings(state.regimeTradingSettings);
  if (shouldRerender) {
    updateRegimeSelectionPanel();
  }
}

document.addEventListener("input", handleRegimeTradingSettingChange);
document.addEventListener("change", handleRegimeTradingSettingChange);

document.addEventListener("click", (event) => {
  const tradingSettingsToggle = (event.target as HTMLElement).closest<HTMLButtonElement>("#confidenceTradingSettingsToggle");
  if (tradingSettingsToggle) {
    state.confidenceTradingSettingsExpanded = !state.confidenceTradingSettingsExpanded;
    saveUiState();
    updateConfidenceAggregationPanel();
    return;
  }
  const regimeTradingSettingsToggle = (event.target as HTMLElement).closest<HTMLButtonElement>("#regimeTradingSettingsToggle");
  if (regimeTradingSettingsToggle) {
    state.regimeTradingSettingsExpanded = !state.regimeTradingSettingsExpanded;
    saveUiState();
    updateRegimeSelectionPanel();
    return;
  }
  const defaultSizingToggle = (event.target as HTMLElement).closest<HTMLButtonElement>("#confidenceDefaultSizingToggle");
  if (defaultSizingToggle) {
    state.confidenceDefaultSizingExpanded = !state.confidenceDefaultSizingExpanded;
    saveUiState();
    updateConfidenceAggregationPanel();
    return;
  }
  const regimeDefaultSizingToggle = (event.target as HTMLElement).closest<HTMLButtonElement>("#regimeDefaultSizingToggle");
  if (regimeDefaultSizingToggle) {
    state.regimeDefaultSizingExpanded = !state.regimeDefaultSizingExpanded;
    saveUiState();
    updateRegimeSelectionPanel();
  }
});

function handleTargetSettingInput(event: Event) {
  const input = (event.target as HTMLElement).closest<HTMLInputElement | HTMLSelectElement>("[data-target-setting]");
  if (!input) {
    return;
  }
  const key = input.dataset.targetSetting as keyof TargetOrderSettings | undefined;
  if (!key) {
    return;
  }
  const shouldRerender = event.type !== "input" || !(input instanceof HTMLInputElement);
  if (isRecommendedTargetSetting(key)) {
    if (shouldRerender) {
      updateAlgorithmPanel(visibleCandles());
    }
    return;
  }
  const numericKeys = new Set<keyof TargetOrderSettings>([
    "quantity",
    "triggerPrice",
    "limitPrice",
    "stopPrice",
    "targetPrice",
    "accountBalance",
    "orderLimitDollars",
    "dailyLimitDollars",
    "riskDollars",
    "orderNotional",
    "plannedStopRiskDollars",
    "estimatedSlippage",
  ]);
  if (numericKeys.has(key) && input.value.trim() === "") {
    return;
  }
  const nextValue = numericKeys.has(key) ? Number(input.value) : input.value;
  if (numericKeys.has(key) && !Number.isFinite(nextValue as number)) {
    return;
  }
  state.targetOrderOverrides = { ...state.targetOrderOverrides, [key]: nextValue };
  saveTargetOrderOverrides(state.targetOrderOverrides);
  if (shouldRerender) {
    updateAlgorithmPanel(visibleCandles());
  }
}

function isRecommendedTargetSetting(key: keyof TargetOrderSettings) {
  return new Set<keyof TargetOrderSettings>([
    "side",
    "quantity",
    "triggerPrice",
    "limitPrice",
    "stopPrice",
    "targetPrice",
    "orderNotional",
    "riskDollars",
    "plannedStopRiskDollars",
    "estimatedSlippage",
  ]).has(key);
}

function clearRecommendedTargetOverrides() {
  const manualOverrides = { ...state.targetOrderOverrides };
  delete manualOverrides.side;
  delete manualOverrides.quantity;
  delete manualOverrides.triggerPrice;
  delete manualOverrides.limitPrice;
  delete manualOverrides.stopPrice;
  delete manualOverrides.targetPrice;
  delete manualOverrides.orderNotional;
  delete manualOverrides.riskDollars;
  delete manualOverrides.plannedStopRiskDollars;
  delete manualOverrides.estimatedSlippage;
  state.targetOrderOverrides = manualOverrides;
  saveTargetOrderOverrides(state.targetOrderOverrides);
}

document.addEventListener("input", handleTargetSettingInput);
document.addEventListener("change", handleTargetSettingInput);

function handleWeightedTargetSettingInput(event: Event) {
  const input = (event.target as HTMLElement).closest<HTMLInputElement | HTMLSelectElement>("[data-weighted-target-setting]");
  if (!input) {
    return;
  }
  if (event.type === "input" && input instanceof HTMLInputElement) {
    return;
  }
  const key = input.dataset.weightedTargetSetting as keyof TargetOrderSettings | undefined;
  if (!key) {
    return;
  }
  if (isRecommendedTargetSetting(key)) {
    updateWeightedVotingPanel();
    return;
  }
  const numericKeys = new Set<keyof TargetOrderSettings>([
    "quantity",
    "triggerPrice",
    "limitPrice",
    "stopPrice",
    "targetPrice",
    "accountBalance",
    "orderLimitDollars",
    "dailyLimitDollars",
    "riskDollars",
    "orderNotional",
    "plannedStopRiskDollars",
    "estimatedSlippage",
  ]);
  const nextValue = numericKeys.has(key) ? Number(input.value) : input.value;
  if (numericKeys.has(key) && !Number.isFinite(nextValue as number)) {
    return;
  }
  state.weightedTargetOrderOverrides = { ...state.weightedTargetOrderOverrides, [key]: nextValue };
  saveWeightedTargetOrderOverrides(state.weightedTargetOrderOverrides);
  updateWeightedVotingPanel();
}

document.addEventListener("input", handleWeightedTargetSettingInput);
document.addEventListener("change", handleWeightedTargetSettingInput);

function handleConfidenceTargetSettingInput(event: Event) {
  const input = (event.target as HTMLElement).closest<HTMLInputElement | HTMLSelectElement>("[data-confidence-target-setting]");
  if (!input) {
    return;
  }
  const key = input.dataset.confidenceTargetSetting as keyof TargetOrderSettings | undefined;
  if (!key) {
    return;
  }
  if (key === "quantity") {
    updateConfidenceAggregationPanel();
    return;
  }
  const generatedKeys = new Set<keyof TargetOrderSettings>([
    "symbol",
    "side",
    "orderType",
    "triggerPrice",
    "limitPrice",
    "stopPrice",
    "targetPrice",
    "accountBalance",
    "orderLimitDollars",
    "dailyLimitDollars",
    "riskDollars",
    "orderNotional",
    "plannedStopRiskDollars",
    "estimatedSlippage",
    "timeInForce",
    "cutoff",
  ]);
  if (state.confidenceTradingSettings.useDefaultSizingSettings && generatedKeys.has(key)) {
    updateConfidenceAggregationPanel();
    return;
  }
  const numericKeys = new Set<keyof TargetOrderSettings>([
    "quantity",
    "triggerPrice",
    "limitPrice",
    "stopPrice",
    "targetPrice",
    "accountBalance",
    "orderLimitDollars",
    "dailyLimitDollars",
    "riskDollars",
    "orderNotional",
    "plannedStopRiskDollars",
    "estimatedSlippage",
  ]);
  const nextValue = numericKeys.has(key) ? Number(input.value) : input.value;
  if (numericKeys.has(key) && !Number.isFinite(nextValue as number)) {
    return;
  }
  state.confidenceTargetOrderOverrides = { ...state.confidenceTargetOrderOverrides, [key]: nextValue };
  saveConfidenceTargetOrderOverrides(state.confidenceTargetOrderOverrides);
  updateConfidenceAggregationPanel();
}

document.addEventListener("input", handleConfidenceTargetSettingInput);
document.addEventListener("change", handleConfidenceTargetSettingInput);

function handleRegimeTargetSettingInput(event: Event) {
  const input = (event.target as HTMLElement).closest<HTMLInputElement | HTMLSelectElement>("[data-regime-target-setting]");
  if (!input) {
    return;
  }
  const key = input.dataset.regimeTargetSetting as keyof TargetOrderSettings | undefined;
  if (!key) {
    return;
  }
  if (key === "quantity") {
    updateRegimeSelectionPanel();
    return;
  }
  const generatedKeys = new Set<keyof TargetOrderSettings>([
    "symbol",
    "side",
    "orderType",
    "triggerPrice",
    "limitPrice",
    "stopPrice",
    "targetPrice",
    "accountBalance",
    "orderLimitDollars",
    "dailyLimitDollars",
    "riskDollars",
    "orderNotional",
    "plannedStopRiskDollars",
    "estimatedSlippage",
    "timeInForce",
    "cutoff",
  ]);
  if (state.regimeTradingSettings.useDefaultSizingSettings && generatedKeys.has(key)) {
    updateRegimeSelectionPanel();
    return;
  }
  const numericKeys = new Set<keyof TargetOrderSettings>([
    "quantity",
    "triggerPrice",
    "limitPrice",
    "stopPrice",
    "targetPrice",
    "accountBalance",
    "orderLimitDollars",
    "dailyLimitDollars",
    "riskDollars",
    "orderNotional",
    "plannedStopRiskDollars",
    "estimatedSlippage",
  ]);
  const nextValue = numericKeys.has(key) ? Number(input.value) : input.value;
  if (numericKeys.has(key) && !Number.isFinite(nextValue as number)) {
    return;
  }
  state.regimeTargetOrderOverrides = { ...state.regimeTargetOrderOverrides, [key]: nextValue };
  saveRegimeTargetOrderOverrides(state.regimeTargetOrderOverrides);
  updateRegimeSelectionPanel();
}

document.addEventListener("input", handleRegimeTargetSettingInput);
document.addEventListener("change", handleRegimeTargetSettingInput);

function handleMetaTargetSettingInput(event: Event) {
  const input = (event.target as HTMLElement).closest<HTMLInputElement | HTMLSelectElement>("[data-meta-target-setting]");
  if (!input) {
    return;
  }
  const key = input.dataset.metaTargetSetting as keyof TargetOrderSettings | undefined;
  if (!key) {
    return;
  }
  if (key === "quantity") {
    updateMetaStrategyPanel();
    return;
  }
  const generatedKeys = new Set<keyof TargetOrderSettings>([
    "symbol",
    "side",
    "orderType",
    "triggerPrice",
    "limitPrice",
    "stopPrice",
    "targetPrice",
    "accountBalance",
    "orderLimitDollars",
    "dailyLimitDollars",
    "riskDollars",
    "orderNotional",
    "plannedStopRiskDollars",
    "estimatedSlippage",
    "timeInForce",
    "cutoff",
  ]);
  if (state.metaTradingSettings.useDefaultSizingSettings && generatedKeys.has(key)) {
    updateMetaStrategyPanel();
    return;
  }
  const numericKeys = new Set<keyof TargetOrderSettings>([
    "quantity",
    "triggerPrice",
    "limitPrice",
    "stopPrice",
    "targetPrice",
    "accountBalance",
    "orderLimitDollars",
    "dailyLimitDollars",
    "riskDollars",
    "orderNotional",
    "plannedStopRiskDollars",
    "estimatedSlippage",
  ]);
  const nextValue = numericKeys.has(key) ? Number(input.value) : input.value;
  if (numericKeys.has(key) && !Number.isFinite(nextValue as number)) {
    return;
  }
  state.metaTargetOrderOverrides = { ...state.metaTargetOrderOverrides, [key]: nextValue };
  saveMetaTargetOrderOverrides(state.metaTargetOrderOverrides);
  updateMetaStrategyPanel();
}

document.addEventListener("input", handleMetaTargetSettingInput);
document.addEventListener("change", handleMetaTargetSettingInput);

dailyBacktestPopupClose.addEventListener("click", () => {
  dailyBacktestPopup.hidden = true;
});

dailyBacktestPopup.addEventListener("click", (event) => {
  if (event.target === dailyBacktestPopup) {
    dailyBacktestPopup.hidden = true;
  }
});

document.addEventListener("click", (event) => {
  const button = (event.target as HTMLElement).closest<HTMLButtonElement>("#runDynamicArtifactButton");
  if (!button) {
    return;
  }
  void refreshDynamicTradingArtifactForCurrentSettings();
});

document.addEventListener("click", (event) => {
  const button = (event.target as HTMLElement).closest<HTMLButtonElement>("#tradingSettingsToggle");
  if (!button) {
    return;
  }
  state.tradingSettingsExpanded = !state.tradingSettingsExpanded;
  saveUiState();
  updateAlgorithmPanel(visibleCandles());
});

document.addEventListener("click", (event) => {
  const button = (event.target as HTMLElement).closest<HTMLButtonElement>("#tradingDefaultSizingToggle");
  if (!button) {
    return;
  }
  state.votingDefaultSizingExpanded = !state.votingDefaultSizingExpanded;
  saveUiState();
  updateAlgorithmPanel(visibleCandles());
});

document.addEventListener("click", (event) => {
  const button = (event.target as HTMLElement).closest<HTMLButtonElement>("#weightedTradingSettingsToggle");
  if (!button) {
    return;
  }
  state.weightedTradingSettingsExpanded = !state.weightedTradingSettingsExpanded;
  saveUiState();
  updateWeightedVotingPanel();
});

document.addEventListener("click", (event) => {
  const button = (event.target as HTMLElement).closest<HTMLButtonElement>("#weightedDefaultSizingToggle");
  if (!button) {
    return;
  }
  state.weightedDefaultSizingExpanded = !state.weightedDefaultSizingExpanded;
  saveUiState();
  updateWeightedVotingPanel();
});

canvas.addEventListener("mousemove", (event) => {
  if (isDragging) {
    return;
  }

  const rect = canvas.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const ratio = window.devicePixelRatio || 1;
  const plot = chartBounds(canvas.width / ratio, canvas.height / ratio);
  const visible = visibleCandles();

  if (!visible.length || x < plot.left || x > plot.right) {
    state.hoveredIndex = -1;
    state.hoverX = -1;
    state.hoverY = -1;
    canvas.classList.remove("over-candle");
  } else {
    const candleWidth = (plot.right - plot.left) / visible.length;
    state.hoveredIndex = Math.max(0, Math.min(visible.length - 1, Math.floor((x - plot.left) / candleWidth)));
    state.hoverX = x;
    state.hoverY = event.clientY - rect.top;
    canvas.classList.toggle("over-candle", isOverCandle(state.hoverX, state.hoverY, plot, visible));
  }
  scheduleDrawChart();
});

canvas.addEventListener(
  "wheel",
  (event) => {
    if (Math.abs(event.deltaY) < 1) {
      return;
    }
    event.preventDefault();
    zoomChart(event.deltaY < 0 ? "in" : "out");
  },
  { passive: false },
);

canvas.addEventListener("mousedown", (event) => {
  isDragging = true;
  lastDragX = event.clientX;
  dragCarry = 0;
  canvas.classList.add("dragging");
  event.preventDefault();
});

window.addEventListener("mousemove", (event) => {
  if (isDragging) {
    handleDragMove(event);
  }
});

window.addEventListener("mouseup", () => {
  isDragging = false;
  dragCarry = 0;
  canvas.classList.remove("dragging");
});

canvas.addEventListener("mouseleave", () => {
  if (isDragging) {
    return;
  }
  state.hoveredIndex = -1;
  state.hoverX = -1;
  state.hoverY = -1;
  canvas.classList.remove("over-candle");
  scheduleDrawChart();
});

window.addEventListener("resize", scheduleDrawChart);

function handleDragMove(event: MouseEvent) {
  const ratio = window.devicePixelRatio || 1;
  const plot = chartBounds(canvas.width / ratio, canvas.height / ratio);
  const visible = visibleCandles();
  const candleWidth = visible.length ? (plot.right - plot.left) / visible.length : 1;
  dragCarry += (event.clientX - lastDragX) / candleWidth;
  const wholeCandles = Math.trunc(dragCarry);
  if (wholeCandles !== 0) {
    state.viewportOffset = clampViewportOffset(state.viewportOffset + wholeCandles);
    dragCarry -= wholeCandles;
    state.hoveredIndex = -1;
    state.hoverX = -1;
    state.hoverY = -1;
    canvas.classList.remove("over-candle");
    drawChart();
    scheduleVisibleContextUpdate();
    markRefresh(state.viewportOffset === 0 ? "armed" : "paused");
    if (wholeCandles > 0 && state.viewportOffset >= maxViewportOffset()) {
      void loadOlderCandles();
    }
  }
  lastDragX = event.clientX;
}

async function loadCandles(options: { showLoading?: boolean; refresh?: boolean } = {}) {
  const showLoading = options.showLoading ?? true;
  const shouldRefresh = options.refresh ?? state.candles.length > 0;
  state.error = "";
  state.historyEndReached = false;
  if (showLoading) {
    state.source = "loading";
    emptyState.textContent = "Loading candles...";
    emptyState.hidden = false;
    updateMeta();
  }

  const item = timeframeItems.find((candidate) => candidate.value === state.timeframe)!;
  const params = new URLSearchParams({
    symbol: state.symbol,
    feed: state.feed,
    timeframe: state.timeframe,
    limit: String(item.limit),
    refresh: String(shouldRefresh),
  });
  const start = toAlpacaTime(state.start);
  const end = toAlpacaTime(state.end);
  if (start) {
    params.set("start", start);
  }
  if (end) {
    params.set("end", end);
  }

  try {
    const response = await fetch(`${API_BASE}/api/candles?${params.toString()}`);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const payload = (await response.json()) as CandleResponse;
    const previousLatest = state.candles[state.candles.length - 1];
    const wasLive = state.viewportOffset === 0;
    const shouldStayLive = options.refresh && !state.start && !state.end && (wasLive || shouldRefreshVotingTradingRag());
    const nextCandles = normalizeCandles(payload.candles);
    const nextLatest = nextCandles[nextCandles.length - 1];
    const latestChanged = candleChanged(previousLatest, nextLatest);
    state.candles = nextCandles;
    state.historyEndReached = false;
    state.viewportOffset = shouldStayLive ? 0 : clampViewportOffset(state.viewportOffset);
    resetHoverState();
    state.source = payload.source;
    state.error = payload.warning ?? "";
    markRefresh(latestChanged ? "updated" : "checked");
    updateLastCandleStatus(nextLatest);
    if (latestChanged && nextLatest && shouldRefreshVotingTradingRag()) {
      void refreshVotingEnsembleTradingOnNewCandle(nextLatest.timestamp);
    }
  } catch (error) {
    if (showLoading) {
      state.candles = [];
    }
    state.error = error instanceof Error ? error.message : "Unable to load candles";
    if (!state.candles.length) {
      state.source = "error";
    }
    markRefresh("failed");
  }

  emptyState.hidden = state.candles.length > 0;
  emptyState.textContent = state.error || "No candles available";
  updateLastCandleStatus(state.candles[state.candles.length - 1]);
  updateMeta();
  drawChart();
  void loadMarketForecast({ refresh: false });
  void loadWeightedMarketData({ refresh: shouldRefresh });
  if (WEIGHTED_BROWSER_BACKTEST_BOOTSTRAP_ENABLED) {
    void ensureWeightedInitialWeightsFromBacktest();
  }
  void maybeRunDailyAlgorithmBacktests("candles");
  if (state.algoBacktestTimeframe !== "Trading") {
    scheduleVisibleContextUpdate(0);
  }
}

async function setAlgoBacktestTimeframe(timeframe: AlgoBacktestTimeframe) {
  timeframe = visibleAlgoBacktestTimeframe(timeframe);
  if (state.algoBacktestTimeframe === timeframe && (state.algoBacktestResult || (timeframe === "Trading" && state.tradingRag))) {
    return;
  }
  state.algoBacktestTimeframe = timeframe;
  if (timeframe === "1Min" || timeframe === "5Min") {
    state.algoIntradayTradesExpanded = true;
  }
  saveUiState();
  if (timeframe === "Trading") {
    state.algoBacktestResult = null;
    state.algoBacktestCandles = [];
    state.algoBacktestStatus = "ready";
    updateAlgorithmPanel(visibleCandles());
    await loadTradingRag();
    return;
  }
  await loadAlgoBacktestCandles();
}

async function getBacktestRange(options: { refresh?: boolean } = {}) {
  if (backtestRangeCache && !options.refresh) {
    return backtestRangeCache;
  }
  const candidates = [
    ...BACKTEST_API_CANDIDATES,
  ];
  for (const baseUrl of candidates) {
    try {
      const response = await fetch(`${baseUrl}/api/backtest-data/latest?symbol=${encodeURIComponent(state.symbol)}`);
      if (!response.ok) {
        continue;
      }
      const manifest = await response.json();
      const startDate = String(manifest.requestedStartDate || DEFAULT_BACKTEST_START_DATE).slice(0, 10);
      const endDate = String(manifest.requestedEndDate || manifest.latestSessionDate || DEFAULT_BACKTEST_END_DATE).slice(0, 10);
      backtestRangeCache = { startDate, endDate };
      return backtestRangeCache;
    } catch {
      // Try the next backend fallback.
    }
  }
  backtestRangeCache = { startDate: DEFAULT_BACKTEST_START_DATE, endDate: DEFAULT_BACKTEST_END_DATE };
  return backtestRangeCache;
}

async function backtestRangeParams(extra: Record<string, string> = {}) {
  const range = await getBacktestRange();
  return new URLSearchParams({
    symbol: state.symbol,
    start_date: range.startDate,
    end_date: range.endDate,
    ...extra,
  });
}

async function fetchPreparedBacktestCandles(timeframe: "1Min" | "5Min" | "1Day") {
  const range = await getBacktestRange();
  const params = new URLSearchParams({
    symbol: state.symbol,
    timeframe,
    start_date: range.startDate,
    end_date: range.endDate,
  });
  let lastStatus = 0;
  for (const baseUrl of BACKTEST_API_CANDIDATES) {
    try {
      const response = await fetchWithTimeout(`${baseUrl}/api/backtest-data/candles?${params.toString()}`, 30000);
      lastStatus = response.status;
      if (response.ok) {
        const payload = (await response.json()) as CandleResponse;
        return payload.candles;
      }
      if (response.status !== 404) {
        throw new Error(await response.text());
      }
    } catch (error) {
      if (baseUrl === BACKTEST_API_CANDIDATES[BACKTEST_API_CANDIDATES.length - 1]) {
        throw error;
      }
    }
  }
  throw new Error(`Prepared ${timeframe} backtest candles unavailable (${lastStatus || 503})`);
}

async function requestDailyBacktestDatasetRefresh(targetDate: string, options: { force?: boolean } = {}) {
  const params = new URLSearchParams({
    symbol: state.symbol,
    feed: state.feed,
    start_date: DEFAULT_BACKTEST_START_DATE,
    end_date: targetDate,
    force: String(options.force ?? false),
  });
  let lastStatus = 0;
  for (const baseUrl of BACKTEST_API_CANDIDATES) {
    try {
      const response = await fetchWithTimeout(`${baseUrl}/api/backtest-data/daily-refresh?${params.toString()}`, 45000, {
        method: "POST",
      });
      lastStatus = response.status;
      if (response.ok) {
        return await response.json();
      }
      if (response.status !== 404) {
        throw new Error(await response.text());
      }
    } catch (error) {
      if (baseUrl === BACKTEST_API_CANDIDATES[BACKTEST_API_CANDIDATES.length - 1]) {
        throw error;
      }
    }
  }
  throw new Error(`Daily backtest dataset refresh unavailable (${lastStatus || 503})`);
}

async function ensureBacktestDatasetThrough(targetDate: string) {
  let refreshResult: unknown = null;
  try {
    refreshResult = await requestDailyBacktestDatasetRefresh(targetDate);
  } catch {
    // If the daily-refresh endpoint is unavailable, the manifest check below still protects the run.
  }
  const range = await getBacktestRange({ refresh: true });
  return { ready: range.endDate >= targetDate, range, refreshResult };
}

async function loadAlgoBacktestCandles() {
  const loadId = ++algoBacktestLoadId;
  const requestedTimeframe = state.algoBacktestTimeframe;
  if (requestedTimeframe === "Trading") {
    await loadTradingRag();
    return;
  }
  state.algoBacktestStatus = "loading";
  state.algoBacktestWarning = "";
  updateAlgorithmPanel(visibleCandles());
  if (requestedTimeframe === "1Min" || requestedTimeframe === "5Min") {
    try {
      const cachedBacktest = await fetchVotingEnsembleBacktest(requestedTimeframe);
      if (loadId !== algoBacktestLoadId || state.algoBacktestTimeframe !== requestedTimeframe) {
        return;
      }
      state.algoBacktestResult = compactVotingBacktestForDisplay(cachedBacktest, { preserveTrades: true });
      state.algoBacktestCandles = [];
      state.algoBacktestStatus = "ready";
      state.algoBacktestWarning = "";
      updateAlgorithmPanel(visibleCandles());
      return;
    } catch (error) {
      if (loadId !== algoBacktestLoadId || state.algoBacktestTimeframe !== requestedTimeframe) {
        return;
      }
      state.algoBacktestWarning =
        error instanceof Error ? error.message : `Unable to load ${algoBacktestTimeframeLabel(requestedTimeframe)} cached backtest`;
    }
  }
  const dynamicBacktest = dynamicBacktestForTimeframe(requestedTimeframe);
  if (dynamicBacktest) {
    state.algoBacktestResult = compactVotingBacktestForDisplay(dynamicBacktest, {
      preserveTrades: requestedTimeframe === "1Min" || requestedTimeframe === "5Min",
    });
    state.algoBacktestCandles = [];
    state.algoBacktestStatus = "ready";
    state.algoBacktestWarning = "";
    updateAlgorithmPanel(visibleCandles());
    return;
  }
  state.algoBacktestStatus = "fallback";
  state.algoBacktestWarning = "Using loaded chart candles while the dynamic artifact is unavailable.";
  updateAlgorithmPanel(visibleCandles());

  try {
    const timeframe = requestedTimeframe;
    if (timeframe === state.timeframe && state.candles.length) {
      if (loadId !== algoBacktestLoadId || state.algoBacktestTimeframe !== requestedTimeframe) {
        return;
      }
      state.algoBacktestCandles = state.candles;
      state.algoBacktestStatus = "ready";
      updateAlgorithmPanel(visibleCandles());
      return;
    }
    let candles = await fetchAlgoBacktestCandles(timeframe);
    if (!candles.length && timeframe === "5Min") {
      const oneMinuteCandles =
        state.timeframe === "1Min" && state.candles.length ? state.candles : await fetchAlgoBacktestCandles("1Min");
      candles = aggregateCandlesToFiveMinute(oneMinuteCandles);
      state.algoBacktestWarning = candles.length ? "5m candles aggregated from 1m data." : "No 5m or 1m candles available.";
    }
    if (loadId !== algoBacktestLoadId || state.algoBacktestTimeframe !== requestedTimeframe) {
      return;
    }
    state.algoBacktestCandles = candles;
    state.algoBacktestStatus = "ready";
  } catch (error) {
    if (loadId !== algoBacktestLoadId || state.algoBacktestTimeframe !== requestedTimeframe) {
      return;
    }
    state.algoBacktestCandles = requestedTimeframe === state.timeframe ? state.candles : [];
    state.algoBacktestStatus = "fallback";
    state.algoBacktestWarning =
      error instanceof Error ? error.message : `Unable to load ${algoBacktestTimeframeLabel(requestedTimeframe)} backtest candles`;
  }

  updateAlgorithmPanel(visibleCandles());
}

async function loadTradingRag() {
  state.tradingRagStatus = "loading";
  state.tradingRagWarning = "";
  updateAlgorithmPanel(visibleCandles());
  try {
    state.tradingRag = await fetchTradingRag();
    state.tradingRagStatus = "ready";
  } catch (error) {
    state.tradingRag = null;
    state.tradingRagStatus = "error";
    state.tradingRagWarning = error instanceof Error ? error.message : "Trading RAG unavailable";
  }
  updateAlgorithmPanel(visibleCandles());
}

async function refreshVotingEnsembleTradingOnNewCandle(timestamp: string) {
  if (!shouldRefreshVotingTradingRag() || tradingRagRefreshInFlight || lastTradingRagCandleTimestamp === timestamp) {
    return;
  }
  tradingRagRefreshInFlight = true;
  lastTradingRagCandleTimestamp = timestamp;
  if (contextTimer) {
    window.clearTimeout(contextTimer);
    contextTimer = undefined;
  }
  try {
    await loadMarketContext({ showLoading: false, refresh: true, asOf: timestamp });
    await loadTradingRag();
  } finally {
    tradingRagRefreshInFlight = false;
    updateTradingRefreshCountdown();
  }
}

async function fetchTradingRag() {
  const range = await getBacktestRange();
  const votes = strategyEnsembleSignals(state.marketContext);
  const eligibleVotes = votes.filter(isEligibleStrategyVote);
  const voteCounts = {
    buy: eligibleVotes.filter((vote) => vote.signal === "Buy").length,
    sell: eligibleVotes.filter((vote) => vote.signal === "Sell").length,
    hold: eligibleVotes.filter((vote) => vote.signal === "Hold").length,
  };
  const winner = winningVoteSignal(voteCounts.buy, voteCounts.sell, voteCounts.hold);
  const payload = {
    symbol: state.symbol,
    startDate: range.startDate,
    endDate: range.endDate,
    query: "Given today's SPY condition and current strategy votes, which strategy historically worked best?",
    winner,
    voteCounts,
    selectedTimeframe: "All",
    marketContext: compactMarketContext(state.marketContext),
    votes: votes.map((vote) => ({
      strategy: vote.strategy,
      signal: vote.signal,
      status: vote.status,
      score: vote.score,
      detail: vote.detail,
      eligible: isEligibleStrategyVote(vote),
    })),
  };
  let lastStatus = 0;
  for (const baseUrl of BACKTEST_API_CANDIDATES) {
    try {
      const response = await fetch(`${baseUrl}/api/voting-ensemble/trading-rag`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      lastStatus = response.status;
      if (response.ok) {
        return (await response.json()) as TradingRagResponse;
      }
      if (response.status !== 404) {
        throw new Error(`Trading RAG unavailable (${response.status})`);
      }
    } catch (error) {
      if (error instanceof Error && !error.message.includes("Failed to fetch")) {
        throw error;
      }
    }
  }
  throw new Error(`Trading RAG unavailable (${lastStatus || 503})`);
}

async function loadLatestDynamicTradingArtifact() {
  state.dynamicArtifactStatus = "loading";
  state.dynamicArtifactWarning = "";
  updateAlgorithmPanel(visibleCandles());
  try {
    const artifact = await fetchLatestDynamicTradingArtifact();
    state.dynamicArtifact = artifact;
    state.dynamicArtifactStatus = "ready";
    state.dynamicArtifactSettingsKey = tradingSettingsKey(state.tradingSettings);
    if (state.algoBacktestTimeframe !== "Trading") {
      const dynamicBacktest = dynamicBacktestForTimeframe(state.algoBacktestTimeframe);
      if (dynamicBacktest) {
        state.algoBacktestResult = dynamicBacktest;
        state.algoBacktestCandles = [];
        state.algoBacktestStatus = "ready";
        state.algoBacktestWarning = "";
      }
    }
  } catch (error) {
    state.dynamicArtifact = null;
    state.dynamicArtifactStatus = "error";
    state.dynamicArtifactWarning = error instanceof Error ? error.message : "Dynamic artifact unavailable";
  }
  updateAlgorithmPanel(visibleCandles());
}

function dynamicBacktestForTimeframe(timeframe: BacktestResultTimeframe) {
  const settingsKey = tradingSettingsKey(state.tradingSettings);
  if (
    state.dynamicArtifactStatus !== "ready" ||
    state.dynamicArtifactSettingsKey !== settingsKey ||
    !state.dynamicArtifact
  ) {
    return null;
  }
  const result = state.dynamicArtifact.backtests?.[timeframe];
  return result ? ({ ...result, timeframe } as BacktestResult) : null;
}

async function fetchLatestDynamicTradingArtifact() {
  const range = await getBacktestRange();
  const params = new URLSearchParams({
    symbol: state.symbol,
    start_date: range.startDate,
    end_date: range.endDate,
  });
  let lastStatus = 0;
  for (const baseUrl of BACKTEST_API_CANDIDATES) {
    try {
      const response = await fetchWithTimeout(`${baseUrl}/api/voting-ensemble/dynamic-artifact/latest?${params.toString()}`, 10000);
      lastStatus = response.status;
      if (response.ok) {
        return (await response.json()) as DynamicTradingArtifact;
      }
      if (response.status === 409) {
        throw new Error(await artifactNotReadyMessage(response, "Daily Trading Settings artifact"));
      }
      if (response.status !== 404) {
        throw new Error(await response.text());
      }
    } catch (error) {
      if (baseUrl === BACKTEST_API_CANDIDATES[BACKTEST_API_CANDIDATES.length - 1]) {
        throw error;
      }
    }
  }
  throw new Error(`Dynamic artifact unavailable (${lastStatus || 503})`);
}

async function refreshDynamicTradingArtifactForCurrentSettings() {
  const settingsKey = tradingSettingsKey(state.tradingSettings);
  state.dynamicArtifactStatus = "loading";
  state.dynamicArtifactWarning = "Starting artifact refresh for current Trading Settings.";
  updateAlgorithmPanel(visibleCandles());
  try {
    const artifact = await startDynamicTradingArtifactForCurrentSettings();
    state.dynamicArtifact = artifact;
    state.dynamicArtifactStatus = "ready";
    state.dynamicArtifactSettingsKey = settingsKey;
    state.dynamicArtifactWarning = "";
  } catch (error) {
    state.dynamicArtifact = null;
    state.dynamicArtifactStatus = "error";
    state.dynamicArtifactWarning = error instanceof Error ? error.message : "Dynamic artifact unavailable";
  }
  updateAlgorithmPanel(visibleCandles());
}

async function startDynamicTradingArtifactForCurrentSettings() {
  const range = await getBacktestRange();
  const payload = {
    symbol: state.symbol,
    startDate: range.startDate,
    endDate: range.endDate,
    settings: state.tradingSettings,
    reason: "Manual Trading Settings artifact refresh",
  };
  let lastStatus = 0;
  for (const baseUrl of BACKTEST_API_CANDIDATES) {
    try {
      const response = await fetchWithTimeout(`${baseUrl}/api/voting-ensemble/dynamic-artifact/jobs`, 10000, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      lastStatus = response.status;
      if (response.ok) {
        const job = await response.json();
        return await pollDynamicTradingArtifactJob(baseUrl, String(job.jobId ?? ""));
      }
      if (response.status !== 404) {
        throw new Error(await response.text());
      }
    } catch (error) {
      if (baseUrl === BACKTEST_API_CANDIDATES[BACKTEST_API_CANDIDATES.length - 1]) {
        throw error;
      }
    }
  }
  throw new Error(`Dynamic artifact refresh unavailable (${lastStatus || 503})`);
}

async function pollDynamicTradingArtifactJob(baseUrl: string, jobId: string) {
  if (!jobId) {
    throw new Error("Dynamic artifact job did not return a job id.");
  }
  const deadline = Date.now() + 3 * 60 * 60 * 1000;
  while (Date.now() < deadline) {
    await wait(5000);
    const response = await fetch(`${baseUrl}/api/voting-ensemble/dynamic-artifact/jobs/${encodeURIComponent(jobId)}`);
    if (!response.ok) {
      throw new Error(`Dynamic artifact job unavailable (${response.status})`);
    }
    const job = await response.json();
    const status = String(job.status || "").toLowerCase();
    state.dynamicArtifactWarning = job.message ? String(job.message) : `Artifact job ${status || "running"}`;
    updateAlgorithmPanel(visibleCandles());
    if (status === "ready") {
      if (!job.artifact) {
        throw new Error("Dynamic artifact job finished without an artifact.");
      }
      state.dynamicArtifactWarning = "";
      return job.artifact as DynamicTradingArtifact;
    }
    if (status === "error" || status === "stopped" || status === "stalled") {
      throw new Error(job.message || job.error || "Dynamic artifact job failed.");
    }
  }
  throw new Error("Dynamic artifact job is still running after 3 hours. Check the artifact job status before using the Order Template.");
}

function wait(ms: number) {
  return new Promise<void>((resolve) => window.setTimeout(resolve, ms));
}

function tradingSettingsKey(settings: TradingSettings) {
  return JSON.stringify({
    startingCapital: roundNumber(settings.startingCapital, 2),
    orderAllocationPercent: roundNumber(settings.orderAllocationPercent, 4),
    dailyAllocationPercent: roundNumber(settings.dailyAllocationPercent, 4),
    riskBudgetPercentOfOrder: roundNumber(settings.riskBudgetPercentOfOrder, 4),
    maxTradesPerDay: Math.round(settings.maxTradesPerDay),
    stopLossPercent: roundNumber(settings.stopLossPercent, 4),
    fixedStopDistanceDollars: roundNumber(settings.fixedStopDistanceDollars, 4),
    takeProfitR: roundNumber(settings.takeProfitR, 4),
    slippagePerShare: roundNumber(settings.slippagePerShare, 4),
    positionSizingMode: settings.positionSizingMode,
  });
}

function compactMarketContext(context: MarketContext | null) {
  if (!context) {
    return null;
  }
  return {
    regime: compactLayer(context.regime),
    session: compactLayer(context.session),
    event: compactLayer(context.event),
    strategies: context.strategies.map((strategy) => ({
      name: strategy.name,
      status: strategy.status,
      score: strategy.score,
      matches: strategy.matches,
      risks: strategy.risks,
    })),
  };
}

function compactLayer(layer: MarketLayer) {
  return {
    label: layer.label,
    directionBias: layer.directionBias,
    volatility: layer.volatility,
    confidence: layer.confidence,
    reasons: layer.reasons,
    strategyTags: layer.strategyTags,
  };
}

async function fetchVotingEnsembleBacktest(timeframe: AlgoBacktestTimeframe) {
  if (timeframe === "Trading") {
    throw new Error("Trading RAG is not a backtest timeframe");
  }
  if (timeframe === "Event") {
    return fetchOpenCloseEventsBacktest();
  }
  const params = await backtestRangeParams({
    timeframe,
    max_trades: "20",
  });
  const candidates = [
    ...BACKTEST_API_CANDIDATES,
  ];

  let lastStatus = 0;
  for (const baseUrl of candidates) {
    try {
      const response = await fetchWithTimeout(`${baseUrl}/api/voting-ensemble/backtest?${params.toString()}`, 20000);
      lastStatus = response.status;
      if (response.ok) {
        return (await response.json()) as BacktestResult;
      }
      if (response.status !== 404) {
        throw new Error(`Full-range Voting Ensemble backtest unavailable (${response.status})`);
      }
    } catch (error) {
      if (error instanceof Error && !error.message.includes("Failed to fetch")) {
        throw error;
      }
    }
  }
  throw new Error(`Full-range Voting Ensemble backtest unavailable (${lastStatus || 503})`);
}

async function fetchOpenCloseEventsBacktest() {
  const params = await backtestRangeParams({
    max_trades: "20",
  });
  const candidates = [
    ...BACKTEST_API_CANDIDATES,
  ];

  let lastStatus = 0;
  for (const baseUrl of candidates) {
    try {
      const response = await fetchWithTimeout(`${baseUrl}/api/open-close-events/backtest?${params.toString()}`, 20000);
      lastStatus = response.status;
      if (response.ok) {
        return (await response.json()) as BacktestResult;
      }
      if (response.status !== 404) {
        throw new Error(`Opening/closing event backtest unavailable (${response.status})`);
      }
    } catch (error) {
      if (error instanceof Error && !error.message.includes("Failed to fetch")) {
        throw error;
      }
    }
  }
  throw new Error(`Opening/closing event backtest unavailable (${lastStatus || 503})`);
}

function compactVotingBacktestForDisplay(result: BacktestResult, options: { preserveTrades?: boolean } = {}): BacktestResult {
  const totalTrades = result.totalTrades ?? result.trades.length;
  if (options.preserveTrades) {
    return {
      ...result,
      totalTrades,
      displayedTrades: result.displayedTrades ?? result.trades.length,
    };
  }
  return {
    ...result,
    trades: [],
    totalTrades,
    displayedTrades: 0,
  };
}

async function loadMlComparison() {
  const settingsKey = tradingSettingsKey(state.tradingSettings);
  if (
    state.dynamicArtifactStatus === "ready" &&
    state.dynamicArtifactSettingsKey === settingsKey &&
    state.dynamicArtifact?.mlComparison
  ) {
    state.mlComparison = state.dynamicArtifact.mlComparison;
    state.mlComparisonStatus = "ready";
    state.mlComparisonWarning = "";
    updateAlgorithmPanel(visibleCandles());
    return;
  }
  if (state.mlComparisonStatus === "ready" && state.mlComparison) {
    return;
  }
  state.mlComparisonStatus = "loading";
  state.mlComparisonWarning = "";
  updateAlgorithmPanel(visibleCandles());
  try {
    state.mlComparison = await fetchMlComparison();
    state.mlComparisonStatus = "ready";
  } catch (error) {
    state.mlComparison = null;
    state.mlComparisonStatus = "error";
    state.mlComparisonWarning = error instanceof Error ? error.message : "ML comparison unavailable";
  }
  updateAlgorithmPanel(visibleCandles());
}

async function fetchWithTimeout(url: string, timeoutMs: number, init: RequestInit = {}) {
  const controller = new AbortController();
  const timeoutId = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new Error(`Request timed out after ${Math.round(timeoutMs / 1000)}s`);
    }
    throw error;
  } finally {
    window.clearTimeout(timeoutId);
  }
}

async function fetchMlComparison() {
  const params = await backtestRangeParams();
  const candidates = [
    ...BACKTEST_API_CANDIDATES,
  ];
  let lastStatus = 0;
  for (const baseUrl of candidates) {
    try {
      const response = await fetchWithTimeout(`${baseUrl}/api/voting-ensemble/ml-comparison?${params.toString()}`, 15000);
      lastStatus = response.status;
      if (response.ok) {
        return (await response.json()) as MlComparisonResult;
      }
      if (response.status === 409) {
        throw new Error(await artifactNotReadyMessage(response, "ML comparison"));
      }
      if (response.status !== 404) {
        throw new Error(`ML comparison unavailable (${response.status})`);
      }
    } catch (error) {
      if (error instanceof Error && !error.message.includes("Failed to fetch")) {
        throw error;
      }
    }
  }
  throw new Error(`ML comparison unavailable (${lastStatus || 503})`);
}

async function artifactNotReadyMessage(response: Response, label: string) {
  try {
    const payload = await response.json();
    const detail = payload?.detail ?? payload;
    const latestJob = detail?.latestJob;
    const status = String(latestJob?.status ?? "").toLowerCase();
    if (status === "running" || status === "queued") {
      return `${label} artifacts are regenerating. Refresh after the artifact job finishes.`;
    }
    if (status === "stopped" || status === "error") {
      return `${label} artifacts need regeneration. Last artifact job ${status}.`;
    }
    if (detail?.message) {
      return String(detail.message);
    }
  } catch {
    // Keep the fallback below if the backend response cannot be parsed.
  }
  return `${label} artifacts are not ready yet.`;
}

async function loadCandidateDataset() {
  if (state.candidateDatasetStatus === "ready" && state.candidateDataset) {
    return;
  }
  state.candidateDatasetStatus = "loading";
  state.candidateDatasetWarning = "";
  updateAlgorithmPanel(visibleCandles());
  try {
    state.candidateDataset = await fetchCandidateDataset();
    state.candidateDatasetStatus = "ready";
  } catch (error) {
    state.candidateDataset = null;
    state.candidateDatasetStatus = "error";
    state.candidateDatasetWarning = error instanceof Error ? error.message : "Candidate dataset unavailable";
  }
  updateAlgorithmPanel(visibleCandles());
}

async function fetchCandidateDataset() {
  const params = await backtestRangeParams();
  const candidates = [
    ...BACKTEST_API_CANDIDATES,
  ];
  let lastStatus = 0;
  for (const baseUrl of candidates) {
    try {
      const response = await fetchWithTimeout(`${baseUrl}/api/voting-ensemble/candidate-dataset?${params.toString()}`, 15000);
      lastStatus = response.status;
      if (response.ok) {
        return (await response.json()) as CandidateDatasetSummary;
      }
      if (response.status === 409) {
        throw new Error(await artifactNotReadyMessage(response, "Candidate dataset"));
      }
      if (response.status !== 404) {
        throw new Error(`Candidate dataset unavailable (${response.status})`);
      }
    } catch (error) {
      if (error instanceof Error && !error.message.includes("Failed to fetch")) {
        throw error;
      }
    }
  }
  throw new Error(`Candidate dataset unavailable (${lastStatus || 503})`);
}

async function loadMlDiagnostics() {
  if (state.mlDiagnosticsStatus === "ready" && state.mlDiagnostics) {
    return;
  }
  state.mlDiagnosticsStatus = "loading";
  state.mlDiagnosticsWarning = "";
  updateAlgorithmPanel(visibleCandles());
  try {
    state.mlDiagnostics = await fetchMlDiagnostics();
    state.mlDiagnosticsStatus = "ready";
  } catch (error) {
    state.mlDiagnostics = null;
    state.mlDiagnosticsStatus = "error";
    state.mlDiagnosticsWarning = error instanceof Error ? error.message : "ML diagnostics unavailable";
  }
  updateAlgorithmPanel(visibleCandles());
}

async function fetchMlDiagnostics() {
  const params = await backtestRangeParams();
  const candidates = [
    ...BACKTEST_API_CANDIDATES,
  ];
  let lastStatus = 0;
  for (const baseUrl of candidates) {
    try {
      const response = await fetchWithTimeout(`${baseUrl}/api/voting-ensemble/ml-diagnostics?${params.toString()}`, 15000);
      lastStatus = response.status;
      if (response.ok) {
        return (await response.json()) as MlDiagnosticsResult;
      }
      if (response.status === 409) {
        throw new Error(await artifactNotReadyMessage(response, "ML diagnostics"));
      }
      if (response.status !== 404) {
        throw new Error(`ML diagnostics unavailable (${response.status})`);
      }
    } catch (error) {
      if (error instanceof Error && !error.message.includes("Failed to fetch")) {
        throw error;
      }
    }
  }
  throw new Error(`ML diagnostics unavailable (${lastStatus || 503})`);
}

async function loadDailyRefinement() {
  if (state.dailyRefinementStatus === "ready" && state.dailyRefinement) {
    return;
  }
  state.dailyRefinementStatus = "loading";
  state.dailyRefinementWarning = "";
  updateAlgorithmPanel(visibleCandles());
  try {
    state.dailyRefinement = await fetchDailyRefinement();
    state.dailyRefinementStatus = "ready";
  } catch (error) {
    state.dailyRefinement = null;
    state.dailyRefinementStatus = "error";
    state.dailyRefinementWarning = error instanceof Error ? error.message : "Daily refinement unavailable";
  }
  updateAlgorithmPanel(visibleCandles());
}

async function fetchDailyRefinement() {
  const params = await backtestRangeParams();
  const candidates = [
    ...BACKTEST_API_CANDIDATES,
  ];
  let lastStatus = 0;
  for (const baseUrl of candidates) {
    try {
      const response = await fetch(`${baseUrl}/api/voting-ensemble/daily-refinement?${params.toString()}`);
      lastStatus = response.status;
      if (response.ok) {
        return (await response.json()) as DailyRefinementResult;
      }
      if (response.status !== 404) {
        throw new Error(`Daily refinement unavailable (${response.status})`);
      }
    } catch (error) {
      if (error instanceof Error && !error.message.includes("Failed to fetch")) {
        throw error;
      }
    }
  }
  throw new Error(`Daily refinement unavailable (${lastStatus || 503})`);
}

async function loadEventRefinement() {
  if (state.eventRefinementStatus === "ready" && state.eventRefinement) {
    return;
  }
  state.eventRefinementStatus = "loading";
  state.eventRefinementWarning = "";
  updateAlgorithmPanel(visibleCandles());
  try {
    state.eventRefinement = await fetchEventRefinement();
    state.eventRefinementStatus = "ready";
  } catch (error) {
    state.eventRefinement = null;
    state.eventRefinementStatus = "error";
    state.eventRefinementWarning = error instanceof Error ? error.message : "Event refinement unavailable";
  }
  updateAlgorithmPanel(visibleCandles());
}

async function fetchEventRefinement() {
  const params = await backtestRangeParams();
  const candidates = [
    ...BACKTEST_API_CANDIDATES,
  ];
  let lastStatus = 0;
  for (const baseUrl of candidates) {
    try {
      const response = await fetch(`${baseUrl}/api/voting-ensemble/event-refinement?${params.toString()}`);
      lastStatus = response.status;
      if (response.ok) {
        return (await response.json()) as EventRefinementResult;
      }
      if (response.status !== 404) {
        throw new Error(`Event refinement unavailable (${response.status})`);
      }
    } catch (error) {
      if (error instanceof Error && !error.message.includes("Failed to fetch")) {
        throw error;
      }
    }
  }
  throw new Error(`Event refinement unavailable (${lastStatus || 503})`);
}

async function loadWeeklyRiskTuning() {
  if (state.weeklyRiskTuningStatus === "ready" && state.weeklyRiskTuning) {
    return;
  }
  state.weeklyRiskTuningStatus = "loading";
  state.weeklyRiskTuningWarning = "";
  updateAlgorithmPanel(visibleCandles());
  try {
    state.weeklyRiskTuning = await fetchWeeklyRiskTuning();
    state.weeklyRiskTuningStatus = "ready";
  } catch (error) {
    state.weeklyRiskTuning = null;
    state.weeklyRiskTuningStatus = "error";
    state.weeklyRiskTuningWarning = error instanceof Error ? error.message : "Weekly risk tuning unavailable";
  }
  updateAlgorithmPanel(visibleCandles());
}

async function fetchWeeklyRiskTuning() {
  const params = await backtestRangeParams();
  const candidates = [
    ...BACKTEST_API_CANDIDATES,
  ];
  let lastStatus = 0;
  for (const baseUrl of candidates) {
    try {
      const response = await fetch(`${baseUrl}/api/voting-ensemble/weekly-risk-tuning?${params.toString()}`);
      lastStatus = response.status;
      if (response.ok) {
        return (await response.json()) as WeeklyRiskTuningResult;
      }
      if (response.status !== 404) {
        throw new Error(`Weekly risk tuning unavailable (${response.status})`);
      }
    } catch (error) {
      if (error instanceof Error && !error.message.includes("Failed to fetch")) {
        throw error;
      }
    }
  }
  throw new Error(`Weekly risk tuning unavailable (${lastStatus || 503})`);
}

async function fetchAlgoBacktestCandles(timeframe: BacktestResultTimeframe) {
  if (timeframe === "Event") {
    return [];
  }
  const params = new URLSearchParams({
    symbol: state.symbol,
    feed: state.feed,
    timeframe,
    limit: timeframe === "1Min" ? "1000" : "700",
    refresh: "false",
  });
  const response = await fetchWithTimeout(`${API_BASE}/api/candles?${params.toString()}`, 10000);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const payload = (await response.json()) as CandleResponse;
  return payload.candles;
}

async function loadWeightedMarketData(options: { refresh?: boolean } = {}) {
  if (weightedMarketDataInFlight || !state.candles.length) {
    return;
  }
  weightedMarketDataInFlight = true;
  state.weightedMarketData.status = "loading";
  state.weightedMarketData.warning = "";
  try {
    const [results, timeframeResults] = await Promise.all([
      Promise.allSettled(weightedAuxiliarySymbols.map(async (symbol) => [symbol, await fetchWeightedSymbolCandles(symbol, options.refresh ?? false)] as const)),
      Promise.allSettled(
        weightedSpyContextTimeframes.map(async (timeframe) => [timeframe, await fetchWeightedTimeframeCandles(timeframe, options.refresh ?? false)] as const),
      ),
    ]);
    const candlesBySymbol: Record<string, Candle[]> = { ...state.weightedMarketData.candlesBySymbol };
    const timeframeCandles: Partial<Record<WeightedSpyContextTimeframe, Candle[]>> = { ...state.weightedMarketData.timeframeCandles };
    const failed: string[] = [];
    results.forEach((result, index) => {
      if (result.status === "fulfilled") {
        const [symbol, candles] = result.value;
        candlesBySymbol[symbol] = normalizeCandles(candles);
      } else {
        failed.push(weightedAuxiliarySymbols[index]);
      }
    });
    timeframeResults.forEach((result, index) => {
      const timeframe = weightedSpyContextTimeframes[index];
      if (result.status === "fulfilled") {
        const [, candles] = result.value;
        timeframeCandles[timeframe] = normalizeCandles(candles);
      } else {
        failed.push(`SPY ${timeframe}`);
      }
    });
    state.weightedMarketData.candlesBySymbol = candlesBySymbol;
    state.weightedMarketData.timeframeCandles = timeframeCandles;
    state.weightedMarketData.updatedAt = new Date().toISOString();
    const loadedCount = weightedAuxiliarySymbols.filter((symbol) => candlesBySymbol[symbol]?.length).length;
    const timeframeLoadedCount = weightedSpyContextTimeframes.filter((timeframe) => timeframeCandles[timeframe]?.length).length;
    const expectedCount = weightedAuxiliarySymbols.length + weightedSpyContextTimeframes.length;
    const totalLoadedCount = loadedCount + timeframeLoadedCount;
    state.weightedMarketData.status = totalLoadedCount === expectedCount ? "ready" : totalLoadedCount ? "partial" : "error";
    state.weightedMarketData.warning = failed.length ? `Weighted market data unavailable: ${failed.join(", ")}` : "";
    updateAlgorithmPanel(visibleCandles());
  } catch (error) {
    state.weightedMarketData.status = "error";
    state.weightedMarketData.warning = error instanceof Error ? error.message : "Weighted market data refresh failed";
  } finally {
    weightedMarketDataInFlight = false;
  }
}

async function fetchWeightedSymbolCandles(symbol: string, refresh: boolean) {
  const params = new URLSearchParams({
    symbol,
    feed: state.feed,
    timeframe: "1Min",
    limit: "120",
    refresh: String(refresh),
  });
  const start = toAlpacaTime(state.start);
  const end = toAlpacaTime(state.end);
  if (start) {
    params.set("start", start);
  }
  if (end) {
    params.set("end", end);
  }
  const response = await fetchWithTimeout(`${API_BASE}/api/candles?${params.toString()}`, 10000);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const payload = (await response.json()) as CandleResponse;
  return payload.candles;
}

async function fetchWeightedTimeframeCandles(timeframe: WeightedSpyContextTimeframe, refresh: boolean) {
  const params = new URLSearchParams({
    symbol: state.symbol,
    feed: state.feed,
    timeframe,
    limit: "120",
    refresh: String(refresh),
  });
  const start = toAlpacaTime(state.start);
  const end = toAlpacaTime(state.end);
  if (start) {
    params.set("start", start);
  }
  if (end) {
    params.set("end", end);
  }
  const response = await fetchWithTimeout(`${API_BASE}/api/candles?${params.toString()}`, 10000);
  if (!response.ok) {
    throw new Error(await response.text());
  }
  const payload = (await response.json()) as CandleResponse;
  return payload.candles;
}

async function loadMarketStatus() {
  try {
    const response = await fetch(`${API_BASE}/api/market-status`);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const payload = (await response.json()) as MarketStatus;
    state.marketStatus = payload.status;
    updateMarketStatus(payload);
    void handleTradeHistoryMarketRollover(payload);
    void maybeRunDailyAlgorithmBacktests("market-status");
    return payload;
  } catch (error) {
    const fallback = {
      status: "unknown",
      isOpen: false,
      warning: error instanceof Error ? error.message : "Unable to load market status",
    };
    state.marketStatus = "unknown";
    updateMarketStatus(fallback);
    void handleTradeHistoryMarketRollover(fallback);
    void maybeRunDailyAlgorithmBacktests("market-status");
    return fallback;
  }
}

async function loadMacroEvents() {
  state.macroStatus = "loading";
  updateMacroCalendar();

  try {
    const response = await fetch(`${API_BASE}/api/macro-events?limit=8`);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const payload = (await response.json()) as MacroEventsResponse;
    state.macroEvents = payload.events;
    state.macroSource = payload.source;
    state.macroUpdatedAt = payload.updatedAt;
    state.macroStatus = "ready";
  } catch {
    state.macroEvents = upcomingFallbackMacroEvents();
    state.macroSource = "Built-in BLS 2026 release schedule";
    state.macroUpdatedAt = new Date().toISOString();
    state.macroStatus = "fallback";
  }

  updateMacroCalendar();
}

async function loadFedEvents() {
  state.fedStatus = "loading";
  updateFedCalendar();

  try {
    const response = await fetch(`${API_BASE}/api/fed-events?limit=8`);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const payload = (await response.json()) as FedEventsResponse;
    state.fedEvents = payload.events;
    state.fedSource = payload.source;
    state.fedUpdatedAt = payload.updatedAt;
    state.fedStatus = "ready";
  } catch {
    state.fedEvents = upcomingFallbackFedEvents();
    state.fedSource = "Built-in Federal Reserve calendar";
    state.fedUpdatedAt = new Date().toISOString();
    state.fedStatus = "fallback";
  }

  updateFedCalendar();
}

async function loadTradingAlerts() {
  state.tradingAlertsStatus = "loading";
  updateTradingAlerts();

  try {
    const response = await fetchTradingAlertsResponse(8);
    if (!response.ok) {
      throw new Error(await tradingAlertErrorMessage(response));
    }
    const payload = (await response.json()) as TradingAlertsResponse;
    state.tradingAlerts = payload.events;
    state.tradingAlertsSource = payload.source;
    state.tradingAlertsWarning = payload.warning ?? "";
    state.tradingAlertsUpdatedAt = payload.updatedAt;
    state.tradingAlertsStatus = payload.warning ? "warning" : "ready";
  } catch (error) {
    state.tradingAlerts = [];
    state.tradingAlertsSource = "Nasdaq Trader Trade Halt RSS";
    state.tradingAlertsWarning = error instanceof Error ? error.message : "Unable to load trading halt alerts";
    state.tradingAlertsUpdatedAt = new Date().toISOString();
    state.tradingAlertsStatus = "warning";
  }

  updateTradingAlerts();
}

async function loadCircuitBreakers() {
  state.circuitBreakersStatus = "loading";
  updateCircuitBreakers();

  try {
    const response = await fetchCircuitBreakersResponse();
    if (!response.ok) {
      throw new Error(await response.text());
    }
    state.circuitBreakers = (await response.json()) as CircuitBreakersResponse;
    state.circuitBreakersWarning = "";
    state.circuitBreakersStatus = "ready";
  } catch {
    state.circuitBreakers = {
      ...fallbackCircuitBreakers,
      updatedAt: new Date().toISOString(),
    };
    state.circuitBreakersWarning = "Using built-in rule reference; backend levels unavailable.";
    state.circuitBreakersStatus = "fallback";
  }

  updateCircuitBreakers();
}

async function loadMocImbalance() {
  state.mocImbalanceStatus = "loading";
  updateMocImbalance();

  try {
    const response = await fetchMocImbalanceResponse();
    if (!response.ok) {
      throw new Error(await response.text());
    }
    state.mocImbalance = (await response.json()) as MocImbalanceResponse;
    state.mocImbalanceWarning = state.mocImbalance.warning ?? "";
    state.mocImbalanceStatus = state.mocImbalance.latest ? "ready" : "not_configured";
  } catch {
    state.mocImbalance = {
      ...fallbackMocImbalance,
      symbol: state.symbol,
      updatedAt: new Date().toISOString(),
    };
    state.mocImbalanceWarning = "MOC imbalance endpoint unavailable.";
    state.mocImbalanceStatus = "fallback";
  }

  updateMocImbalance();
}

async function loadVixRisk() {
  state.vixRiskStatus = "loading";
  updateVixRisk();

  try {
    const response = await fetchVixRiskResponse();
    if (!response.ok) {
      throw new Error(await response.text());
    }
    state.vixRisk = (await response.json()) as VixRiskResponse;
    state.vixRiskWarning = state.vixRisk.warning ?? "";
    state.vixRiskStatus = state.vixRisk.quote ? "ready" : "fallback";
  } catch {
    state.vixRisk = {
      ...fallbackVixRisk,
      updatedAt: new Date().toISOString(),
    };
    state.vixRiskWarning = "VIX quote endpoint unavailable.";
    state.vixRiskStatus = "fallback";
  }

  updateVixRisk();
}

async function loadEsSnapshot() {
  state.esSnapshotStatus = "loading";
  updateEsSnapshot();

  try {
    const response = await fetchEsSnapshotResponse();
    if (!response.ok) {
      throw new Error(await response.text());
    }
    state.esSnapshot = (await response.json()) as EsSnapshotResponse;
    state.esSnapshotWarning = state.esSnapshot.warning ?? "";
    state.esSnapshotStatus = state.esSnapshot.quote ? "ready" : "fallback";
  } catch {
    state.esSnapshot = {
      ...fallbackEsSnapshot,
      updatedAt: new Date().toISOString(),
    };
    state.esSnapshotWarning = "ES futures quote endpoint unavailable.";
    state.esSnapshotStatus = "fallback";
  }

  updateEsSnapshot();
}

async function loadMarketForecast(options: { refresh?: boolean } = {}) {
  const requestKey = `${state.symbol}|${state.feed}|${options.refresh ? "refresh" : "cache"}`;
  if (marketForecastRequestKey === requestKey) {
    return;
  }
  marketForecastRequestKey = requestKey;
  state.marketForecastStatus = state.marketForecast ? state.marketForecastStatus : "loading";
  renderMarketForecastPanel();
  try {
    const params = new URLSearchParams({
      symbol: state.symbol,
      feed: state.feed,
      timeframe: "1Min",
      limit: "240",
      refresh: String(options.refresh ?? false),
    });
    const response = await fetchWithTimeout(`${API_BASE}/api/market-forecast/prediction?${params.toString()}`, 4000);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    state.marketForecast = (await response.json()) as MarketForecastPrediction;
    state.marketForecastStatus = state.marketForecast.status === "ready" ? "ready" : "fallback";
    state.marketForecastError = "";
  } catch (error) {
    state.marketForecastStatus = state.marketForecast ? "fallback" : "error";
    state.marketForecastError = error instanceof Error ? error.message : "Market forecast unavailable";
  } finally {
    if (marketForecastRequestKey === requestKey) {
      marketForecastRequestKey = "";
    }
  }
  renderMarketForecastPanel();
}

async function startMarketForecastPredictionLedger(reason: string) {
  try {
    await fetchWithTimeout(`${API_BASE}/api/market-forecast/ledger/start`, 10000, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    });
  } catch {
    // Wake activation should keep the app live even if the ledger kick fails.
  }
}

async function requestSystemSleepIfMarketClosed(reason: string) {
  try {
    await fetchWithTimeout(`${API_BASE}/api/system/sleep-if-market-closed`, 5000, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason }),
    });
  } catch {
    // Browser-level app sleep still applies even if Windows rejects system sleep.
  }
}

async function loadSpyNews() {
  state.newsFeedStatus = "loading";
  updateSpyNews();

  try {
    const response = await fetchSpyNewsResponse(10);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    state.newsFeed = (await response.json()) as NewsFeedResponse;
    state.newsFeedWarning = state.newsFeed.warning ?? "";
    state.newsFeedStatus = state.newsFeed.warning ? "fallback" : "ready";
  } catch {
    state.newsFeed = {
      ...fallbackNewsFeed,
      updatedAt: new Date().toISOString(),
      symbol: state.symbol,
    };
    state.newsFeedWarning = "SPY news endpoint unavailable.";
    state.newsFeedStatus = "fallback";
  }

  updateSpyNews();
}

async function loadTradeSummary() {
  state.tradeSummaryStatus = "loading";
  updateTradeSummary();

  try {
    const response = await fetchTradeSummaryResponse();
    if (!response.ok) {
      throw new Error(await response.text());
    }
    state.tradeSummary = (await response.json()) as TradeSummaryResponse;
    state.tradeSummaryWarning = state.tradeSummary.warning ?? "";
    state.tradeSummaryStatus = state.tradeSummary.warning ? "fallback" : "ready";
  } catch {
    state.tradeSummary = {
      ...fallbackTradeSummary,
      updatedAt: new Date().toISOString(),
      symbol: state.symbol,
    };
    state.tradeSummaryWarning = "Trade summary endpoint unavailable.";
    state.tradeSummaryStatus = "fallback";
  }

  updateTradeSummary();
}

async function fetchTradeSummaryResponse() {
  const candidates = [
    API_BASE,
    "http://127.0.0.1:8034",
    "http://127.0.0.1:8033",
    "http://127.0.0.1:8032",
    "http://127.0.0.1:8030",
    "http://127.0.0.1:8031",
    "http://127.0.0.1:8020",
  ].filter((value, index, values) => values.indexOf(value) === index);

  let lastResponse: Response | null = null;
  for (const baseUrl of candidates) {
    try {
      const params = new URLSearchParams({
        symbol: state.symbol,
        limit: "10",
      });
      const response = await fetch(`${baseUrl}/api/news-summary?${params.toString()}`);
      if (response.ok || response.status !== 404) {
        return response;
      }
      lastResponse = response;
    } catch {
      continue;
    }
  }

  return lastResponse ?? new Response("Trade summary backend unavailable", { status: 503 });
}

async function fetchSpyNewsResponse(limit: number) {
  const candidates = [
    API_BASE,
    "http://127.0.0.1:8033",
    "http://127.0.0.1:8032",
    "http://127.0.0.1:8030",
    "http://127.0.0.1:8031",
    "http://127.0.0.1:8020",
  ].filter((value, index, values) => values.indexOf(value) === index);

  let lastResponse: Response | null = null;
  for (const baseUrl of candidates) {
    try {
      const params = new URLSearchParams({
        symbol: state.symbol,
        limit: String(limit),
      });
      const response = await fetch(`${baseUrl}/api/news-feed?${params.toString()}`);
      if (response.ok || response.status !== 404) {
        return response;
      }
      lastResponse = response;
    } catch {
      continue;
    }
  }

  return lastResponse ?? new Response("SPY news backend unavailable", { status: 503 });
}

async function fetchEsSnapshotResponse() {
  const candidates = [
    API_BASE,
    "http://127.0.0.1:8020",
  ].filter((value, index, values) => values.indexOf(value) === index);

  let lastResponse: Response | null = null;
  for (const baseUrl of candidates) {
    try {
      const response = await fetch(`${baseUrl}/api/es-snapshot`);
      if (response.ok || response.status !== 404) {
        return response;
      }
      lastResponse = response;
    } catch {
      continue;
    }
  }

  return lastResponse ?? new Response("ES snapshot backend unavailable", { status: 503 });
}

async function fetchVixRiskResponse() {
  const candidates = [
    API_BASE,
    "http://127.0.0.1:8020",
  ].filter((value, index, values) => values.indexOf(value) === index);

  let lastResponse: Response | null = null;
  for (const baseUrl of candidates) {
    try {
      const response = await fetch(`${baseUrl}/api/vix-risk`);
      if (response.ok || response.status !== 404) {
        return response;
      }
      lastResponse = response;
    } catch {
      continue;
    }
  }

  return lastResponse ?? new Response("VIX risk backend unavailable", { status: 503 });
}

async function fetchMocImbalanceResponse() {
  const candidates = [
    API_BASE,
    "http://127.0.0.1:8020",
  ].filter((value, index, values) => values.indexOf(value) === index);

  let lastResponse: Response | null = null;
  for (const baseUrl of candidates) {
    try {
      const response = await fetch(`${baseUrl}/api/moc-imbalance?symbol=${encodeURIComponent(state.symbol)}`);
      if (response.ok || response.status !== 404) {
        return response;
      }
      lastResponse = response;
    } catch {
      continue;
    }
  }

  return lastResponse ?? new Response("MOC imbalance backend unavailable", { status: 503 });
}

async function fetchCircuitBreakersResponse() {
  const candidates = [
    API_BASE,
    "http://127.0.0.1:8020",
  ].filter((value, index, values) => values.indexOf(value) === index);

  let lastResponse: Response | null = null;
  for (const baseUrl of candidates) {
    try {
      const response = await fetch(`${baseUrl}/api/circuit-breakers?symbol=${encodeURIComponent(state.symbol)}`);
      if (response.ok || response.status !== 404) {
        return response;
      }
      lastResponse = response;
    } catch {
      continue;
    }
  }

  return lastResponse ?? new Response("Circuit breaker backend unavailable", { status: 503 });
}

async function fetchTradingAlertsResponse(limit: number) {
  const candidates = [
    API_BASE,
    "http://127.0.0.1:8020",
  ].filter((value, index, values) => values.indexOf(value) === index);

  let lastResponse: Response | null = null;
  for (const baseUrl of candidates) {
    try {
      const response = await fetch(`${baseUrl}/api/trading-alerts?limit=${limit}`);
      if (response.ok || response.status !== 404) {
        return response;
      }
      lastResponse = response;
    } catch {
      continue;
    }
  }

  return lastResponse ?? new Response("Trading alert backend unavailable", { status: 503 });
}

async function tradingAlertErrorMessage(response: Response) {
  if (response.status === 404) {
    return "Trading alert endpoint is not loaded. Restart the FastAPI backend to enable halt and LULD alerts.";
  }
  const text = await response.text();
  try {
    const parsed = JSON.parse(text) as { detail?: string };
    return parsed.detail ? `Trading alert feed unavailable: ${parsed.detail}` : "Trading alert feed unavailable";
  } catch {
    return text ? `Trading alert feed unavailable: ${text}` : "Trading alert feed unavailable";
  }
}

async function loadMarketContext(options: { showLoading?: boolean; refresh?: boolean; asOf?: string } = {}) {
  const showLoading = options.showLoading ?? true;
  const shouldRefresh = options.refresh ?? false;
  const asOf = options.asOf ?? "";
  if (showLoading) {
    state.contextStatus = "loading";
    state.contextError = "";
    updateMarketContext();
  }

  const params = new URLSearchParams({
    symbol: state.symbol,
    feed: state.feed,
    refresh: String(shouldRefresh),
  });
  if (asOf) {
    params.set("as_of", asOf);
  }

  try {
    const response = await fetch(`${API_BASE}/api/market-context?${params.toString()}`);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    state.marketContext = (await response.json()) as MarketContext;
    state.contextStatus = "ready";
    state.contextError = "";
    state.contextAsOf = asOf;
  } catch (error) {
    state.contextStatus = "error";
    state.contextError = error instanceof Error ? error.message : "Unable to load market context";
  }

  updateMarketContext();
}

function updateSpyNews() {
  if (state.newsFeedStatus === "loading" && !state.newsFeed.items.length) {
    newsFeedSummary.textContent = "Loading SPY headlines";
    newsFeedList.innerHTML = skeletonNewsItems();
    newsFeedSources.innerHTML = "";
    return;
  }

  const feed = state.newsFeed;
  const items = feed.items.slice(0, 10);
  const warning = state.newsFeedWarning || feed.warning || "";
  newsFeedSummary.textContent = items.length
    ? `${items.length} ${feed.symbol || state.symbol} headline${items.length === 1 ? "" : "s"} - ${feed.source}`
    : `No ${feed.symbol || state.symbol} headlines`;
  newsFeedList.innerHTML = items.length
    ? items.map(renderNewsFeedItem).join("")
    : `<div class="news-empty">${escapeHtml(warning || "No SPY headlines returned from configured sources.")}</div>`;
  newsFeedSources.innerHTML = renderNewsSourceStatus(feed.sources, warning);
}

function updateTradeSummary() {
  if (state.tradeSummaryStatus === "loading") {
    tradeSummaryHeadline.textContent = "Building trade conclusion";
    tradeSummaryStatus.innerHTML = `
      <span class="summary-pill">Analyzing feeds</span>
    `;
    tradeSummaryBody.innerHTML = skeletonTradeSummary();
    return;
  }

  const payload = state.tradeSummary;
  const summary = payload.summary;
  const warning = state.tradeSummaryWarning || payload.warning || "";
  tradeSummaryHeadline.textContent = `${summary.bias} bias - ${summary.confidence} confidence`;
  tradeSummaryStatus.innerHTML = `
    <span class="summary-pill ${summaryBiasClass(summary.bias)}">${escapeHtml(summary.bias)}</span>
    <span class="summary-pill">${escapeHtml(summary.confidence)} confidence</span>
    <span class="summary-time">${formatCompactTime(payload.updatedAt)}</span>
  `;
  tradeSummaryBody.innerHTML = `
    ${warning ? `<div class="news-warning">${escapeHtml(warning)}</div>` : ""}
    <div class="summary-conclusion">${escapeHtml(summary.conclusion)}</div>
    <div class="summary-block">
      <strong>Drivers</strong>
      ${renderSummaryList(summary.drivers)}
    </div>
    <div class="summary-block">
      <strong>Risks</strong>
      ${renderSummaryList(summary.risks)}
    </div>
    <div class="summary-block">
      <strong>Trade Plan</strong>
      ${renderSummaryList(summary.actionPlan)}
    </div>
  `;
}

function renderSummaryList(items: string[]) {
  return `
    <ul>
      ${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}
    </ul>
  `;
}

function summaryBiasClass(value: string) {
  const normalized = value.toLowerCase();
  if (normalized.includes("bull")) {
    return "bullish";
  }
  if (normalized.includes("bear")) {
    return "bearish";
  }
  if (normalized.includes("caut")) {
    return "cautious";
  }
  return "neutral";
}

function skeletonTradeSummary() {
  return `
    <div class="summary-conclusion loading">Building trade conclusion from SPY News and News Feeds...</div>
    <div class="summary-block loading">
      <strong>Drivers</strong>
      <ul>
        <li>Reading latest headlines</li>
        <li>Checking macro/Fed/VIX/ES context</li>
      </ul>
    </div>
  `;
}

function renderNewsFeedItem(item: NewsFeedItem) {
  const headline = escapeHtml(item.headline);
  const title = item.url
    ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">${headline}</a>`
    : `<strong>${headline}</strong>`;
  const summary = item.summary ? `<p>${escapeHtml(item.summary)}</p>` : "";
  return `
    <article class="news-feed-item">
      <div class="news-feed-title">${title}</div>
      ${summary}
      <div class="news-feed-meta">
        <span>${escapeHtml(item.source || "News")}</span>
        <span>${formatCompactTime(item.publishedAt)}</span>
        <span>${escapeHtml(item.symbols.slice(0, 4).join(", "))}</span>
      </div>
    </article>
  `;
}

function renderNewsSourceStatus(sources: NewsFeedSource[], warning: string) {
  const visibleSources = sources.slice(0, 7);
  const warningMarkup = warning ? `<div class="news-warning">${escapeHtml(warning)}</div>` : "";
  return `
    ${warningMarkup}
    ${visibleSources
      .map(
        (source) => `
          <div class="news-source-row" data-status="${escapeHtml(source.status)}">
            <span>${escapeHtml(source.name)}</span>
            <strong>${escapeHtml(source.status.replaceAll("_", " "))}</strong>
          </div>
        `,
      )
      .join("")}
  `;
}

function skeletonNewsItems() {
  return [1, 2, 3]
    .map(
      () => `
        <article class="news-feed-item loading">
          <div class="news-feed-title"><strong>Loading headline</strong></div>
          <div class="news-feed-meta">
            <span>Source</span>
            <span>--</span>
          </div>
        </article>
      `,
    )
    .join("");
}

function updateMacroCalendar() {
  if (state.macroStatus === "loading" && !state.macroEvents.length) {
    macroSummary.textContent = "Loading CPI and jobs";
    macroTabMeta.textContent = "Loading";
    macroNext.className = "macro-next loading";
    macroNext.innerHTML = `
      <span>Next</span>
      <strong>Loading</strong>
    `;
    macroList.innerHTML = skeletonMacroEvents();
    updateMacroExpansion();
    return;
  }

  const events = state.macroEvents.slice(0, 6);
  const next = events[0];
  if (!next) {
    macroSummary.textContent = "No scheduled macro events";
    macroTabMeta.textContent = "No events";
    macroNext.className = "macro-next";
    macroNext.innerHTML = `
      <span>Next</span>
      <strong>None scheduled</strong>
    `;
    macroList.innerHTML = "";
    updateMacroExpansion();
    return;
  }

  macroSummary.textContent =
    state.macroStatus === "fallback" ? "CPI and jobs schedule fallback" : "Upcoming CPI and jobs";
  macroTabMeta.textContent = `${macroCategoryLabel(next.category)} ${formatMacroDay(next.releaseAt)} ${formatMacroMonth(next.releaseAt)} - ${daysUntilLabel(next.daysUntil)}`;
  macroNext.className = `macro-next ${next.category}`;
  macroNext.innerHTML = `
    <span>Next ${macroCategoryLabel(next.category)}</span>
    <strong>${escapeHtml(next.title)}</strong>
    <small>${escapeHtml(next.referenceMonth)} - ${formatMacroDate(next.releaseAt)} - ${daysUntilLabel(next.daysUntil)}</small>
  `;
  macroList.innerHTML = events.map(renderMacroEvent).join("");
  updateMacroExpansion();
}

function updateMacroExpansion() {
  macroShell.classList.toggle("expanded", state.macroExpanded);
  macroCalendar.classList.toggle("expanded", state.macroExpanded);
  macroToggleButton.setAttribute("aria-expanded", String(state.macroExpanded));
  macroPanel.hidden = !state.macroExpanded;
  macroTabIcon.textContent = state.macroExpanded ? "-" : "+";
}

function updateFedCalendar() {
  if (state.fedStatus === "loading" && !state.fedEvents.length) {
    fedSummary.textContent = "Loading FOMC and speeches";
    fedTabMeta.textContent = "Loading";
    fedNext.className = "event-next loading";
    fedNext.innerHTML = `
      <span>Next</span>
      <strong>Loading</strong>
    `;
    fedList.innerHTML = skeletonFedEvents();
    updateFedExpansion();
    return;
  }

  const events = state.fedEvents.slice(0, 6);
  const next = events[0];
  if (!next) {
    fedSummary.textContent = "No scheduled Fed events";
    fedTabMeta.textContent = "No events";
    fedNext.className = "event-next";
    fedNext.innerHTML = `
      <span>Next</span>
      <strong>None scheduled</strong>
    `;
    fedList.innerHTML = "";
    updateFedExpansion();
    return;
  }

  fedSummary.textContent = state.fedStatus === "fallback" ? "FOMC and speeches fallback" : "FOMC and Fed speeches";
  fedTabMeta.textContent = `${fedCategoryLabel(next.category)} ${formatMacroDay(next.releaseAt)} ${formatMacroMonth(next.releaseAt)} - ${daysUntilLabel(next.daysUntil)}`;
  fedNext.className = `event-next ${next.category}`;
  fedNext.innerHTML = `
    <span>Next ${fedCategoryLabel(next.category)}</span>
    <strong>${escapeHtml(next.title)}</strong>
    <small>${escapeHtml(next.detail)} - ${formatMacroDate(next.releaseAt)} - ${daysUntilLabel(next.daysUntil)}</small>
  `;
  fedList.innerHTML = events.map(renderFedEvent).join("");
  updateFedExpansion();
}

function updateFedExpansion() {
  fedShell.classList.toggle("expanded", state.fedExpanded);
  fedCalendar.classList.toggle("expanded", state.fedExpanded);
  fedToggleButton.setAttribute("aria-expanded", String(state.fedExpanded));
  fedPanel.hidden = !state.fedExpanded;
  fedTabIcon.textContent = state.fedExpanded ? "-" : "+";
}

function renderFedEvent(event: FedEvent) {
  return `
    <article class="event-item" data-category="${event.category}">
      <div class="event-date">
        <span>${formatMacroDay(event.releaseAt)}</span>
        <strong>${formatMacroMonth(event.releaseAt)}</strong>
      </div>
      <div class="event-item-main">
        <div class="event-item-title">
          <strong>${escapeHtml(event.title)}</strong>
          <span>${fedCategoryLabel(event.category)}</span>
        </div>
        <div class="event-item-detail">
          <span>${escapeHtml(event.detail)}</span>
          <span>${formatMacroTime(event.releaseAt)}</span>
          <span>${daysUntilLabel(event.daysUntil)}</span>
        </div>
      </div>
    </article>
  `;
}

function skeletonFedEvents() {
  return [1, 2, 3]
    .map(
      () => `
        <article class="event-item loading">
          <div class="event-date">
            <span>--</span>
            <strong>--</strong>
          </div>
          <div class="event-item-main">
            <div class="event-item-title">
              <strong>Loading Fed event</strong>
              <span>--</span>
            </div>
            <div class="event-item-detail">
              <span>--</span>
            </div>
          </div>
        </article>
      `,
    )
    .join("");
}

function updateTradingAlerts() {
  if (state.tradingAlertsStatus === "loading" && !state.tradingAlerts.length) {
    tradingAlertsSummary.textContent = "Loading halt alerts";
    tradingAlertsTabMeta.textContent = "Loading";
    tradingAlertsNext.className = "event-next loading";
    tradingAlertsNext.innerHTML = `
      <span>Status</span>
      <strong>Loading</strong>
    `;
    tradingAlertsList.innerHTML = skeletonTradingAlerts();
    updateTradingAlertsExpansion();
    return;
  }

  if (!state.tradingAlerts.length) {
    const endpointMissing = state.tradingAlertsWarning.includes("endpoint is not loaded");
    tradingAlertsSummary.textContent =
      state.tradingAlertsStatus === "warning"
        ? endpointMissing
          ? "Trading alert endpoint not loaded"
          : "Halt feed unavailable"
        : "No active halt alerts";
    tradingAlertsTabMeta.textContent =
      state.tradingAlertsStatus === "warning"
        ? endpointMissing
          ? "Restart backend"
          : "Check feed"
        : `Clear ${formatCompactTime(state.tradingAlertsUpdatedAt)}`;
    tradingAlertsNext.className = `event-next ${state.tradingAlertsStatus === "warning" ? "halt" : "clear"}`;
    tradingAlertsNext.innerHTML = `
      <span>Status</span>
      <strong>${state.tradingAlertsStatus === "warning" ? (endpointMissing ? "Endpoint not loaded" : "Feed unavailable") : "No active halts"}</strong>
      <small>${escapeHtml(state.tradingAlertsWarning || "Nasdaq Trader feed returned no current halt alerts")}</small>
    `;
    tradingAlertsList.innerHTML = "";
    updateTradingAlertsExpansion();
    return;
  }

  const alerts = state.tradingAlerts.slice(0, 6);
  const first = alerts[0];
  tradingAlertsSummary.textContent = `${alerts.length} halt/LULD alert${alerts.length === 1 ? "" : "s"}`;
  tradingAlertsTabMeta.textContent = `${tradingAlertLabel(first.category)} ${escapeHtml(first.symbol)}`;
  tradingAlertsNext.className = `event-next ${first.category}`;
  tradingAlertsNext.innerHTML = `
    <span>Latest ${tradingAlertLabel(first.category)}</span>
    <strong>${escapeHtml(first.title)}</strong>
    <small>${escapeHtml(cleanAlertDetail(first.detail))} - ${formatCompactTime(first.publishedAt)}</small>
  `;
  tradingAlertsList.innerHTML = alerts.map(renderTradingAlert).join("");
  updateTradingAlertsExpansion();
}

function updateTradingAlertsExpansion() {
  tradingAlertsShell.classList.toggle("expanded", state.tradingAlertsExpanded);
  tradingAlertsCalendar.classList.toggle("expanded", state.tradingAlertsExpanded);
  tradingAlertsToggleButton.setAttribute("aria-expanded", String(state.tradingAlertsExpanded));
  tradingAlertsPanel.hidden = !state.tradingAlertsExpanded;
  tradingAlertsTabIcon.textContent = state.tradingAlertsExpanded ? "-" : "+";
}

function renderTradingAlert(alert: TradingAlert) {
  return `
    <article class="event-item" data-category="${alert.category}">
      <div class="event-date">
        <span>${escapeHtml(alert.symbol.slice(0, 4))}</span>
        <strong>${tradingAlertLabel(alert.category)}</strong>
      </div>
      <div class="event-item-main">
        <div class="event-item-title">
          <strong>${escapeHtml(alert.title)}</strong>
          <span>${tradingAlertLabel(alert.category)}</span>
        </div>
        <div class="event-item-detail">
          <span>${escapeHtml(cleanAlertDetail(alert.detail))}</span>
          <span>${formatCompactTime(alert.publishedAt)}</span>
        </div>
      </div>
    </article>
  `;
}

function skeletonTradingAlerts() {
  return [1, 2]
    .map(
      () => `
        <article class="event-item loading">
          <div class="event-date">
            <span>--</span>
            <strong>--</strong>
          </div>
          <div class="event-item-main">
            <div class="event-item-title">
              <strong>Loading halt alert</strong>
              <span>--</span>
            </div>
            <div class="event-item-detail">
              <span>--</span>
            </div>
          </div>
        </article>
      `,
    )
    .join("");
}

function updateCircuitBreakers() {
  if (state.circuitBreakersStatus === "loading" && !state.circuitBreakers) {
    circuitBreakersSummary.textContent = "Loading market-wide levels";
    circuitBreakersTabMeta.textContent = "Loading";
    circuitBreakersNext.className = "event-next loading";
    circuitBreakersNext.innerHTML = `
      <span>Reference</span>
      <strong>Loading</strong>
    `;
    circuitBreakersList.innerHTML = skeletonCircuitBreakers();
    updateCircuitBreakersExpansion();
    return;
  }

  const payload = state.circuitBreakers ?? fallbackCircuitBreakers;
  const hasReference = typeof payload.referenceClose === "number";
  circuitBreakersSummary.textContent = hasReference
    ? `${payload.referenceSymbol} proxy levels`
    : "Market-wide circuit breaker rules";
  circuitBreakersTabMeta.textContent = hasReference
    ? `L1 ${price(payload.rules[0].referenceValue ?? 0)}`
    : "7% / 13% / 20%";
  circuitBreakersNext.className = `event-next ${state.circuitBreakersStatus === "fallback" ? "luld" : "clear"}`;
  circuitBreakersNext.innerHTML = `
    <span>Reference</span>
    <strong>${escapeHtml(payload.referenceIndex)}</strong>
    <small>${escapeHtml(hasReference ? `${payload.referenceSymbol} close ${price(payload.referenceClose ?? 0)} - ${formatCompactTime(payload.referenceDate)}` : payload.referenceNote)}</small>
  `;
  circuitBreakersList.innerHTML = payload.rules.map(renderCircuitBreakerRule).join("");
  updateCircuitBreakersExpansion();
}

function updateCircuitBreakersExpansion() {
  circuitBreakersShell.classList.toggle("expanded", state.circuitBreakersExpanded);
  circuitBreakersCalendar.classList.toggle("expanded", state.circuitBreakersExpanded);
  circuitBreakersToggleButton.setAttribute("aria-expanded", String(state.circuitBreakersExpanded));
  circuitBreakersPanel.hidden = !state.circuitBreakersExpanded;
  circuitBreakersTabIcon.textContent = state.circuitBreakersExpanded ? "-" : "+";
}

function renderCircuitBreakerRule(rule: CircuitBreakerRule) {
  return `
    <article class="event-item" data-category="level${rule.level}">
      <div class="event-date">
        <span>L${rule.level}</span>
        <strong>${rule.percent}%</strong>
      </div>
      <div class="event-item-main">
        <div class="event-item-title">
          <strong>${escapeHtml(rule.label)}</strong>
          <span>${rule.referenceValue ? price(rule.referenceValue) : `${rule.percent}%`}</span>
        </div>
        <div class="event-item-detail">
          <span>${escapeHtml(rule.action)}</span>
        </div>
      </div>
    </article>
  `;
}

function skeletonCircuitBreakers() {
  return [1, 2, 3]
    .map(
      () => `
        <article class="event-item loading">
          <div class="event-date">
            <span>--</span>
            <strong>--</strong>
          </div>
          <div class="event-item-main">
            <div class="event-item-title">
              <strong>Loading level</strong>
              <span>--</span>
            </div>
            <div class="event-item-detail">
              <span>--</span>
            </div>
          </div>
        </article>
      `,
    )
    .join("");
}

function updateMocImbalance() {
  if (state.mocImbalanceStatus === "loading" && !state.mocImbalance) {
    mocImbalanceSummary.textContent = "Loading closing auction";
    mocImbalanceTabMeta.textContent = "Loading";
    mocImbalanceNext.className = "event-next loading";
    mocImbalanceNext.innerHTML = `
      <span>Status</span>
      <strong>Loading</strong>
    `;
    mocImbalanceList.innerHTML = skeletonMocImbalance();
    updateMocImbalanceExpansion();
    return;
  }

  const payload = state.mocImbalance ?? fallbackMocImbalance;
  const latest = payload.latest;
  mocImbalanceSummary.textContent = latest ? `${latest.symbol} ${mocSideLabel(latest.side)} imbalance` : "Closing auction feed not configured";
  mocImbalanceTabMeta.textContent = latest
    ? `${compact(latest.imbalanceShares)} ${mocSideLabel(latest.side)}`
    : `${payload.window.start}-${payload.window.end}`;
  mocImbalanceNext.className = `event-next ${latest ? mocSideClass(latest.side) : "luld"}`;
  mocImbalanceNext.innerHTML = latest
    ? `
      <span>Latest Closing Auction</span>
      <strong>${escapeHtml(latest.symbol)} ${mocSideLabel(latest.side)} ${compact(latest.imbalanceShares)}</strong>
      <small>Paired ${compact(latest.pairedShares)} - Ref ${latest.referencePrice ? price(latest.referencePrice) : "--"} - Indicative ${latest.indicativePrice ? price(latest.indicativePrice) : "--"} - ${formatCompactTime(latest.publishedAt)}</small>
    `
    : `
      <span>Status</span>
      <strong>No live MOC feed configured</strong>
      <small>${escapeHtml(payload.warning ?? "Closing auction imbalance updates require an exchange imbalance feed.")}</small>
    `;
  mocImbalanceList.innerHTML = latest ? renderMocImbalanceUpdate(latest) : renderMocImbalanceFields(payload);
  updateMocImbalanceExpansion();
}

function updateMocImbalanceExpansion() {
  mocImbalanceShell.classList.toggle("expanded", state.mocImbalanceExpanded);
  mocImbalanceCalendar.classList.toggle("expanded", state.mocImbalanceExpanded);
  mocImbalanceToggleButton.setAttribute("aria-expanded", String(state.mocImbalanceExpanded));
  mocImbalancePanel.hidden = !state.mocImbalanceExpanded;
  mocImbalanceTabIcon.textContent = state.mocImbalanceExpanded ? "-" : "+";
}

function renderMocImbalanceUpdate(update: MocImbalanceUpdate) {
  return `
    <article class="event-item" data-category="${mocSideClass(update.side)}">
      <div class="event-date">
        <span>${escapeHtml(update.symbol.slice(0, 4))}</span>
        <strong>${mocSideLabel(update.side)}</strong>
      </div>
      <div class="event-item-main">
        <div class="event-item-title">
          <strong>${compact(update.imbalanceShares)} imbalance shares</strong>
          <span>${update.indicativePrice ? price(update.indicativePrice) : "--"}</span>
        </div>
        <div class="event-item-detail">
          <span>Paired ${compact(update.pairedShares)}</span>
          <span>Reference ${update.referencePrice ? price(update.referencePrice) : "--"}</span>
          <span>${formatCompactTime(update.publishedAt)}</span>
        </div>
      </div>
    </article>
  `;
}

function renderMocImbalanceFields(payload: MocImbalanceResponse) {
  return `
    <article class="event-item" data-category="clear">
      <div class="event-date">
        <span>MOC</span>
        <strong>${payload.window.start.replace(" ET", "")}</strong>
      </div>
      <div class="event-item-main">
        <div class="event-item-title">
          <strong>Closing auction window</strong>
          <span>${payload.status.replace("_", " ")}</span>
        </div>
        <div class="event-item-detail">
          <span>${escapeHtml(payload.window.updateFrequency)}</span>
          <span>Fields: ${escapeHtml(payload.fields.slice(2, 6).join(", "))}</span>
        </div>
      </div>
    </article>
  `;
}

function skeletonMocImbalance() {
  return `
    <article class="event-item loading">
      <div class="event-date">
        <span>--</span>
        <strong>--</strong>
      </div>
      <div class="event-item-main">
        <div class="event-item-title">
          <strong>Loading imbalance state</strong>
          <span>--</span>
        </div>
        <div class="event-item-detail">
          <span>--</span>
        </div>
      </div>
    </article>
  `;
}

function updateVixRisk() {
  if (state.vixRiskStatus === "loading" && !state.vixRisk) {
    vixRiskSummary.textContent = "Loading volatility risk";
    vixRiskTabMeta.textContent = "Loading";
    vixRiskNext.className = "event-next loading";
    vixRiskNext.innerHTML = `
      <span>Status</span>
      <strong>Loading</strong>
    `;
    vixRiskList.innerHTML = skeletonVixRisk();
    updateVixRiskExpansion();
    return;
  }

  const payload = state.vixRisk ?? fallbackVixRisk;
  const quote = payload.quote;
  const level = payload.activeLevel ?? (quote ? vixLevelForValue(quote.last, payload.levels) : null);
  vixRiskSummary.textContent = quote && level ? `${level.label} volatility` : "VIX quote unavailable";
  vixRiskTabMeta.textContent = quote ? `${price(quote.last)} VIX` : "Risk levels";
  vixRiskNext.className = `event-next ${level ? vixSeverityClass(level.severity) : "luld"}`;
  vixRiskNext.innerHTML = quote && level
    ? `
      <span>${escapeHtml(level.label)} Risk</span>
      <strong>VIX ${price(quote.last)}</strong>
      <small>${escapeHtml(level.alert)} - O ${quote.open ? price(quote.open) : "--"} H ${quote.high ? price(quote.high) : "--"} L ${quote.low ? price(quote.low) : "--"}</small>
    `
    : `
      <span>Status</span>
      <strong>Quote unavailable</strong>
      <small>${escapeHtml(payload.warning || state.vixRiskWarning || "Showing built-in VIX risk thresholds")}</small>
    `;
  vixRiskList.innerHTML = payload.levels.map((item) => renderVixRiskLevel(item, level)).join("");
  updateVixRiskExpansion();
}

function updateVixRiskExpansion() {
  vixRiskShell.classList.toggle("expanded", state.vixRiskExpanded);
  vixRiskCalendar.classList.toggle("expanded", state.vixRiskExpanded);
  vixRiskToggleButton.setAttribute("aria-expanded", String(state.vixRiskExpanded));
  vixRiskPanel.hidden = !state.vixRiskExpanded;
  vixRiskTabIcon.textContent = state.vixRiskExpanded ? "-" : "+";
}

function renderVixRiskLevel(item: VixRiskLevel, active: VixRiskLevel | null) {
  const maxLabel = item.max ? `<${item.max}` : "+";
  return `
    <article class="event-item ${active?.label === item.label ? "active" : ""}" data-category="${vixSeverityClass(item.severity)}">
      <div class="event-date">
        <span>${item.min}</span>
        <strong>${maxLabel}</strong>
      </div>
      <div class="event-item-main">
        <div class="event-item-title">
          <strong>${escapeHtml(item.label)}</strong>
          <span>${escapeHtml(item.severity)}</span>
        </div>
        <div class="event-item-detail">
          <span>${escapeHtml(item.alert)}</span>
        </div>
      </div>
    </article>
  `;
}

function skeletonVixRisk() {
  return `
    <article class="event-item loading">
      <div class="event-date">
        <span>--</span>
        <strong>--</strong>
      </div>
      <div class="event-item-main">
        <div class="event-item-title">
          <strong>Loading VIX risk</strong>
          <span>--</span>
        </div>
        <div class="event-item-detail">
          <span>--</span>
        </div>
      </div>
    </article>
  `;
}

function updateEsSnapshot() {
  if (state.esSnapshotStatus === "loading" && !state.esSnapshot) {
    esSnapshotSummary.textContent = "Loading premarket snapshot";
    esSnapshotTabMeta.textContent = "Loading";
    esSnapshotNext.className = "event-next loading";
    esSnapshotNext.innerHTML = `
      <span>Status</span>
      <strong>Loading</strong>
    `;
    esSnapshotList.innerHTML = skeletonEsSnapshot();
    updateEsSnapshotExpansion();
    return;
  }

  const payload = state.esSnapshot ?? fallbackEsSnapshot;
  const quote = payload.quote;
  const level = payload.activeLevel ?? (payload.changePercent !== null ? esLevelForValue(payload.changePercent, payload.levels) : null);
  esSnapshotSummary.textContent = quote && level ? `${level.label} futures` : "ES quote unavailable";
  esSnapshotTabMeta.textContent = quote && payload.changePercent !== null ? `${signed(payload.changePercent)}%` : "Premarket";
  esSnapshotNext.className = `event-next ${level ? esSeverityClass(level.severity) : "luld"}`;
  esSnapshotNext.innerHTML = quote && level
    ? `
      <span>${escapeHtml(payload.session)} Snapshot</span>
      <strong>ES ${price(quote.last)} ${signed(payload.changePoints ?? 0)} (${signed(payload.changePercent ?? 0)}%)</strong>
      <small>${escapeHtml(level.alert)} - O ${quote.open ? price(quote.open) : "--"} H ${quote.high ? price(quote.high) : "--"} L ${quote.low ? price(quote.low) : "--"}</small>
    `
    : `
      <span>Status</span>
      <strong>ES quote unavailable</strong>
      <small>${escapeHtml(payload.warning || state.esSnapshotWarning || "Showing built-in ES direction thresholds")}</small>
    `;
  esSnapshotList.innerHTML = quote ? renderEsQuoteRows(payload) : payload.levels.map(renderEsDirectionLevel).join("");
  updateEsSnapshotExpansion();
}

function updateEsSnapshotExpansion() {
  esSnapshotShell.classList.toggle("expanded", state.esSnapshotExpanded);
  esSnapshotCalendar.classList.toggle("expanded", state.esSnapshotExpanded);
  esSnapshotToggleButton.setAttribute("aria-expanded", String(state.esSnapshotExpanded));
  esSnapshotPanel.hidden = !state.esSnapshotExpanded;
  esSnapshotTabIcon.textContent = state.esSnapshotExpanded ? "-" : "+";
}

function renderEsQuoteRows(payload: EsSnapshotResponse) {
  const quote = payload.quote!;
  const level = payload.activeLevel ?? (payload.changePercent !== null ? esLevelForValue(payload.changePercent, payload.levels) : null);
  return `
    <article class="event-item active" data-category="${level ? esSeverityClass(level.severity) : "flat"}">
      <div class="event-date">
        <span>ES</span>
        <strong>${payload.session.slice(0, 3).toUpperCase()}</strong>
      </div>
      <div class="event-item-main">
        <div class="event-item-title">
          <strong>${level ? escapeHtml(level.label) : "Snapshot"}</strong>
          <span>${payload.changePercent !== null ? `${signed(payload.changePercent)}%` : "--"}</span>
        </div>
        <div class="event-item-detail">
          <span>Last ${price(quote.last)}</span>
          <span>Open ${quote.open ? price(quote.open) : "--"}</span>
          <span>Volume ${quote.volume ? compact(quote.volume) : "--"}</span>
          <span>${quote.date ?? ""} ${quote.time ?? ""}</span>
        </div>
      </div>
    </article>
    ${payload.levels.map(renderEsDirectionLevel).join("")}
  `;
}

function renderEsDirectionLevel(item: EsDirectionLevel) {
  const minLabel = item.minPercent === null ? "" : `${signed(item.minPercent)}%`;
  const maxLabel = item.maxPercent === null ? "+" : `${signed(item.maxPercent)}%`;
  return `
    <article class="event-item" data-category="${esSeverityClass(item.severity)}">
      <div class="event-date">
        <span>${item.severity.includes("up") ? "UP" : item.severity.includes("down") ? "DN" : "FL"}</span>
        <strong>${item.maxPercent === null ? maxLabel : minLabel}</strong>
      </div>
      <div class="event-item-main">
        <div class="event-item-title">
          <strong>${escapeHtml(item.label)}</strong>
          <span>${minLabel || maxLabel}</span>
        </div>
        <div class="event-item-detail">
          <span>${escapeHtml(item.alert)}</span>
        </div>
      </div>
    </article>
  `;
}

function skeletonEsSnapshot() {
  return `
    <article class="event-item loading">
      <div class="event-date">
        <span>ES</span>
        <strong>--</strong>
      </div>
      <div class="event-item-main">
        <div class="event-item-title">
          <strong>Loading ES snapshot</strong>
          <span>--</span>
        </div>
        <div class="event-item-detail">
          <span>--</span>
        </div>
      </div>
    </article>
  `;
}

function renderMacroEvent(event: MacroEvent) {
  return `
    <article class="macro-event" data-category="${event.category}">
      <div class="macro-date">
        <span>${formatMacroDay(event.releaseAt)}</span>
        <strong>${formatMacroMonth(event.releaseAt)}</strong>
      </div>
      <div class="macro-event-main">
        <div class="macro-event-title">
          <strong>${escapeHtml(event.title)}</strong>
          <span>${macroCategoryLabel(event.category)}</span>
        </div>
        <div class="macro-event-detail">
          <span>${escapeHtml(event.referenceMonth)}</span>
          <span>${formatMacroTime(event.releaseAt)}</span>
          <span>${daysUntilLabel(event.daysUntil)}</span>
        </div>
      </div>
    </article>
  `;
}

function skeletonMacroEvents() {
  return [1, 2, 3]
    .map(
      () => `
        <article class="macro-event loading">
          <div class="macro-date">
            <span>--</span>
            <strong>--</strong>
          </div>
          <div class="macro-event-main">
            <div class="macro-event-title">
              <strong>Loading release</strong>
              <span>--</span>
            </div>
            <div class="macro-event-detail">
              <span>--</span>
            </div>
          </div>
        </article>
      `,
    )
    .join("");
}

function scheduleVisibleContextUpdate(delay = 250) {
  const asOf = visibleContextTimestamp();
  if (!asOf || asOf === state.contextAsOf) {
    return;
  }
  if (contextTimer) {
    window.clearTimeout(contextTimer);
  }
  contextTimer = window.setTimeout(() => {
    void loadMarketContext({ showLoading: false, refresh: false, asOf });
  }, delay);
}

function visibleContextTimestamp() {
  const visible = visibleCandles();
  return visible[visible.length - 1]?.timestamp ?? "";
}

async function loadOlderCandles() {
  if (state.loadingOlder || state.historyEndReached || !state.candles.length) {
    return;
  }

  const oldest = state.candles[0];
  const item = timeframeItems.find((candidate) => candidate.value === state.timeframe)!;
  const oldestTime = new Date(oldest.timestamp);
  const rangeStart = toAlpacaTime(state.start);
  const requestedStart = new Date(oldestTime.getTime() - historyLookbackMs(state.timeframe));
  const boundedStart = rangeStart && requestedStart < new Date(rangeStart) ? new Date(rangeStart) : requestedStart;

  if (rangeStart && oldestTime <= new Date(rangeStart)) {
    state.historyEndReached = true;
    return;
  }

  state.loadingOlder = true;

  const params = new URLSearchParams({
    symbol: state.symbol,
    feed: state.feed,
    timeframe: state.timeframe,
    limit: String(item.limit),
    refresh: "true",
    end: oldestTime.toISOString(),
    sort: "desc",
  });
  params.set("start", boundedStart.toISOString());

  try {
    const response = await fetch(`${API_BASE}/api/candles?${params.toString()}`);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const payload = (await response.json()) as CandleResponse;
    const existing = new Set(state.candles.map((candle) => candle.timestamp));
    const older = normalizeCandles(payload.candles)
      .filter((candle) => !existing.has(candle.timestamp) && candle.timestamp < oldest.timestamp)
      .sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime());

    if (!older.length) {
      state.historyEndReached = true;
      return;
    }

    state.candles = normalizeCandles([...older, ...state.candles]);
    state.source = payload.source;
    state.error = payload.warning ?? "";
    scheduleVisibleContextUpdate();
  } catch (error) {
    state.error = error instanceof Error ? error.message : "Unable to load older candles";
  } finally {
    state.loadingOlder = false;
  }
}

function updateMeta(force = false) {
  const latest = currentCandle();
  const tfLabel = timeframeItems.find((item) => item.value === state.timeframe)?.label ?? state.timeframe;
  chartTitle.textContent = `${state.symbol} - ${tfLabel.replace("m", "")} - ${state.feed.toUpperCase()}`;
  quoteSymbol.textContent = state.symbol;
  sourceBadge.textContent = state.source;
  sourceBadge.dataset.source = state.source;
  sourceBadge.title = state.error ? `${state.source.toUpperCase()}: ${state.error}` : `Data source: ${state.source.toUpperCase()}`;
  const metaKey = latest
    ? [
        state.symbol,
        state.feed,
        state.timeframe,
        latest.timestamp,
        latest.open,
        latest.high,
        latest.low,
        latest.close,
        latest.volume,
        state.tradingWindowMode,
        state.tradeHistory.length,
        state.weightedTradeHistory.length,
        state.confidenceTradeHistory.length,
        state.regimeTradeHistory.length,
        state.metaTradeHistory.length,
        state.marketStatus,
        state.currentTargetOrder?.side ?? "none",
        state.currentTargetOrder?.quantity ?? 0,
        state.currentTargetOrder?.limitPrice ?? 0,
        state.currentWeightedTargetOrder?.side ?? "none",
        state.currentWeightedTargetOrder?.quantity ?? 0,
        state.currentWeightedTargetOrder?.limitPrice ?? 0,
        state.currentConfidenceTargetOrder?.side ?? "none",
        state.currentConfidenceTargetOrder?.quantity ?? 0,
        state.currentConfidenceTargetOrder?.limitPrice ?? 0,
        state.currentRegimeTargetOrder?.side ?? "none",
        state.currentRegimeTargetOrder?.quantity ?? 0,
        state.currentRegimeTargetOrder?.limitPrice ?? 0,
        state.currentMetaTargetOrder?.side ?? "none",
        state.currentMetaTargetOrder?.quantity ?? 0,
        state.currentMetaTargetOrder?.limitPrice ?? 0,
      ].join("|")
    : `empty|${state.symbol}|${state.feed}|${state.timeframe}|${state.source}|${state.tradingWindowMode}|${state.tradeHistory.length}|${state.weightedTradeHistory.length}|${state.confidenceTradeHistory.length}|${state.regimeTradeHistory.length}|${state.metaTradeHistory.length}`;

  if (!force && metaKey === lastMetaKey) {
    return;
  }
  lastMetaKey = metaKey;

  if (!latest) {
    ohlc.textContent = "";
    updateQuoteCard(null);
    return;
  }

  const diff = latest.close - latest.open;
  const pct = latest.open ? (diff / latest.open) * 100 : 0;
  ohlc.innerHTML = `
    <span>O${price(latest.open)}</span>
    <span>H${price(latest.high)}</span>
    <span>L${price(latest.low)}</span>
    <span>C${price(latest.close)}</span>
    <span class="${diff >= 0 ? "up" : "down"}">${signed(diff)} (${signed(pct)}%)</span>
  `;
  updateQuoteCard(latest);
}

function currentCandle() {
  const visible = visibleCandles();
  if (!visible.length) {
    return null;
  }
  return state.hoveredIndex >= 0 && state.hoveredIndex < visible.length
    ? visible[state.hoveredIndex]
    : visible[visible.length - 1];
}

function latestLoadedCandle() {
  return state.candles.at(-1) ?? null;
}

function latestWeightedOneMinuteCandle() {
  const oneMinuteCandles = state.weightedMarketData.timeframeCandles["1Min"] ?? [];
  const sessionCandles = oneMinuteCandles.length ? latestRegularSessionCandlesFrom(oneMinuteCandles) : [];
  return sessionCandles.at(-1) ?? oneMinuteCandles.at(-1) ?? null;
}

function newestCandle(...candles: Array<Candle | null | undefined>) {
  return candles
    .filter((candle): candle is Candle => Boolean(candle))
    .sort((left, right) => new Date(right.timestamp).getTime() - new Date(left.timestamp).getTime())
    .at(0) ?? null;
}

function latestExecutionCandleForMode(mode: TradingWindowMode = state.tradingWindowMode) {
  if (mode === "weighted" || mode === "confidence" || mode === "regime") {
    return newestCandle(latestWeightedOneMinuteCandle(), latestLoadedCandle());
  }
  return latestLoadedCandle();
}

function summarizePositionFromTradeHistory(latestPrice: number, previousPrice: number, mode: TradingWindowMode = state.tradingWindowMode): PositionSummary {
  const lots = openOrderLots(mode);
  const shares = lots.reduce((sum, lot) => sum + lot.remainingQuantity, 0);
  const costBasis = lots.reduce((sum, lot) => sum + lot.remainingQuantity * lot.entryPrice, 0);
  const avgPrice = shares ? costBasis / shares : 0;
  const realizedPnl = specificLotRealizedPnl(mode);
  const marketValue = shares * latestPrice;
  const unrealizedPnl = shares === 0 ? 0 : (latestPrice - avgPrice) * shares;
  const dailyPnl = shares * (latestPrice - previousPrice);
  const returnPct = costBasis ? (unrealizedPnl / costBasis) * 100 : 0;
  return {
    shares,
    avgPrice,
    costBasis,
    marketValue,
    unrealizedPnl,
    realizedPnl,
    dailyPnl,
    returnPct,
  };
}

function targetOrderExecutionPrice(order: ManualOrderRecommendation, fallbackPrice: number) {
  return order.limitPrice ?? order.triggerPrice ?? fallbackPrice;
}

function isActiveTargetOrder(order: ManualOrderRecommendation | null) {
  if (!order) {
    return false;
  }
  const orderType = order.orderType.trim().toLowerCase();
  const executionPrice = targetOrderExecutionPrice(order, 0);
  return orderType !== "" && orderType !== "no order" && order.quantity > 0 && executionPrice > 0;
}

function isMarketOpenForOrders() {
  return state.marketStatus === "open";
}

function canSubmitTrades() {
  return state.tradingEnabled && isMarketOpenForOrders();
}

function tradeSubmissionBlockedTitle() {
  return state.tradingEnabled ? "Market is closed" : "Trade is off";
}

function updateTradeToggleButton() {
  tradeToggleButton.textContent = state.tradingEnabled ? "Trade On" : "Trade Off";
  tradeToggleButton.setAttribute("aria-pressed", String(state.tradingEnabled));
  tradeToggleButton.dataset.enabled = String(state.tradingEnabled);
  tradeToggleButton.title = state.tradingEnabled ? "Trading enabled for all algorithms" : "Trading disabled for all algorithms";
}

function updateOrderButtonStates(position: PositionSummary) {
  const mode = state.tradingWindowMode;
  const order = targetOrderForMode(mode);
  const targetSide = order?.side ?? "Hold";
  const latest = latestExecutionCandleForMode(mode);
  const sellableLot = latest ? selectedSellableOpenOrderLot(mode, latest.close) : null;
  if (!canSubmitTrades()) {
    buyOrderButton.disabled = true;
    sellOrderButton.disabled = true;
    buyOrderButton.title = tradeSubmissionBlockedTitle();
    sellOrderButton.title = tradeSubmissionBlockedTitle();
    return;
  }

  buyOrderButton.disabled = false;
  sellOrderButton.disabled = position.shares <= 0 || !sellableLot;
  buyOrderButton.title = targetSide === "Buy"
    ? "Buy using target order settings"
    : "Buy differs from target order settings; confirmation required";
  sellOrderButton.title = position.shares <= 0
    ? "No shares available to sell"
    : sellableLot
      ? "Sell using the Order Controls settings"
      : "No Order Controls sell setup is active";
}

function activeTradingModeFromAlgoTab(): TradingWindowMode {
  return algoRegimeSelectionTabButton.classList.contains("active")
    ? "regime"
    : algoMetaStrategyTabButton.classList.contains("active")
      ? "meta"
    : algoConfidenceAggregationTabButton.classList.contains("active")
      ? "confidence"
    : algoWeightedVotingTabButton.classList.contains("active")
      ? "weighted"
      : "ensemble";
}

function setTradingWindowMode(mode: TradingWindowMode) {
  state.tradingWindowMode = mode;
  saveUiState();
  ensembleTradingWindowTab.classList.toggle("active", mode === "ensemble");
  weightedTradingWindowTab.classList.toggle("active", mode === "weighted");
  confidenceTradingWindowTab.classList.toggle("active", mode === "confidence");
  regimeTradingWindowTab.classList.toggle("active", mode === "regime");
  metaTradingWindowTab.classList.toggle("active", mode === "meta");
  ensembleTradingWindowTab.setAttribute("aria-selected", String(mode === "ensemble"));
  weightedTradingWindowTab.setAttribute("aria-selected", String(mode === "weighted"));
  confidenceTradingWindowTab.setAttribute("aria-selected", String(mode === "confidence"));
  regimeTradingWindowTab.setAttribute("aria-selected", String(mode === "regime"));
  metaTradingWindowTab.setAttribute("aria-selected", String(mode === "meta"));
  tradeHistoryTitle.textContent =
    mode === "meta"
      ? "Meta-Strategy Trade History"
      : mode === "regime"
      ? "Regime Trade History"
      : mode === "confidence"
        ? "WCA Trade History"
        : mode === "weighted"
          ? "Weighted Voting Trade History"
          : "Voting Ensemble Trade History";
  updateQuoteCard(currentCandle());
}

function tradeHistoryForMode(mode: TradingWindowMode) {
  return mode === "meta"
    ? state.metaTradeHistory
    : mode === "regime"
    ? state.regimeTradeHistory
    : mode === "confidence"
      ? state.confidenceTradeHistory
      : mode === "weighted"
        ? state.weightedTradeHistory
        : state.tradeHistory;
}

function setTradingLedger(mode: TradingWindowMode, rows: TradeHistoryRow[]) {
  if (mode === "meta") {
    state.metaTradeHistory = rows;
    saveMetaTradeHistory();
    return;
  }
  if (mode === "regime") {
    state.regimeTradeHistory = rows;
    saveRegimeTradeHistory();
    return;
  }
  if (mode === "confidence") {
    state.confidenceTradeHistory = rows;
    saveConfidenceTradeHistory();
    return;
  }
  if (mode === "weighted") {
    state.weightedTradeHistory = rows;
    saveWeightedTradeHistory();
    return;
  }
  state.tradeHistory = rows;
  saveTradeHistory();
}

function isTradeHistoryRowFromToday(row: TradeHistoryRow) {
  return new Date(row.recordedAt).toDateString() === new Date().toDateString();
}

function clearTodaysTradeHistoryForMode(mode: TradingWindowMode) {
  const protectedRows = new Set(openPositionTradeRowsForMode(mode).map((row) => row.id));
  const remainingRows = normalizedTradeHistoryForMode(mode).filter((row) => !isTradeHistoryRowFromToday(row) || protectedRows.has(row.id));
  const remainingBuyLotIds = new Set(remainingRows.filter((row) => row.side === "Buy").map((row) => row.id));
  const remainingOrderModes = Object.fromEntries(
    Object.entries(orderControlModesForMode(mode)).filter(([lotId]) => remainingBuyLotIds.has(lotId)),
  );
  const remainingOrderOverrides = Object.fromEntries(
    Object.entries(orderControlOverridesForMode(mode)).filter(([lotId]) => remainingBuyLotIds.has(lotId)),
  );
  setTradingLedger(mode, remainingRows);
  setOrderControlModesForMode(mode, remainingOrderModes);
  setOrderControlOverridesForMode(mode, remainingOrderOverrides);
}

function tradeHistoryRolloverState(): TradeHistoryRolloverState {
  try {
    const raw = window.localStorage.getItem(TRADE_HISTORY_ROLLOVER_STORAGE_KEY);
    if (!raw) {
      return { archivedSessionDates: [], clearedSessionDates: [] };
    }
    const parsed = JSON.parse(raw) as Partial<TradeHistoryRolloverState>;
    return {
      archivedSessionDates: Array.isArray(parsed.archivedSessionDates) ? parsed.archivedSessionDates.filter((value) => typeof value === "string") : [],
      clearedSessionDates: Array.isArray(parsed.clearedSessionDates) ? parsed.clearedSessionDates.filter((value) => typeof value === "string") : [],
    };
  } catch {
    return { archivedSessionDates: [], clearedSessionDates: [] };
  }
}

function saveTradeHistoryRolloverState(rollover: TradeHistoryRolloverState) {
  window.localStorage.setItem(
    TRADE_HISTORY_ROLLOVER_STORAGE_KEY,
    JSON.stringify({
      archivedSessionDates: rollover.archivedSessionDates.slice(-20),
      clearedSessionDates: rollover.clearedSessionDates.slice(-20),
    }),
  );
}

function localDateKey(value: string | Date) {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function latestTradeHistorySessionDate() {
  const dates = (["ensemble", "weighted", "confidence", "regime", "meta"] as TradingWindowMode[])
    .flatMap((mode) => tradeHistoryForMode(mode))
    .map((row) => localDateKey(row.recordedAt))
    .filter(Boolean)
    .sort();
  return dates.at(-1) ?? "";
}

function marketReferenceDateKey(market: MarketStatus) {
  return localDateKey(market.timestamp || new Date());
}

function marketNextOpenDateKey(market: MarketStatus) {
  return market.nextOpen ? localDateKey(market.nextOpen) : "";
}

function shouldArchiveTradeHistoryForMarket(market: MarketStatus, sessionDate: string) {
  if (!sessionDate) {
    return false;
  }
  if (!market.isOpen && (market.status === "closed" || market.status === "holiday")) {
    return true;
  }
  const marketDate = marketReferenceDateKey(market);
  return market.isOpen && Boolean(marketDate) && sessionDate < marketDate;
}

function shouldClearArchivedTradeHistoryBeforeOpen(market: MarketStatus, sessionDate: string, rollover: TradeHistoryRolloverState) {
  if (!rollover.archivedSessionDates.includes(sessionDate) || rollover.clearedSessionDates.includes(sessionDate)) {
    return false;
  }
  const marketDate = marketReferenceDateKey(market);
  const nextOpenDate = marketNextOpenDateKey(market);
  if (market.isOpen) {
    return Boolean(marketDate) && sessionDate < marketDate;
  }
  return Boolean(marketDate && nextOpenDate) && marketDate >= nextOpenDate;
}

function openPositionTradeRowsForMode(mode: TradingWindowMode) {
  const rows = normalizedTradeHistoryForMode(mode);
  const openLotIds = new Set(openOrderLots(mode).map((lot) => lot.id));
  if (!openLotIds.size) {
    return [];
  }
  return rows.filter((row) => openLotIds.has(row.id) || Boolean(row.closedLotId && openLotIds.has(row.closedLotId)));
}

function clearClosedTradeHistoryForMode(mode: TradingWindowMode) {
  const remainingRows = openPositionTradeRowsForMode(mode);
  const remainingBuyLotIds = new Set(remainingRows.filter((row) => row.side === "Buy").map((row) => row.id));
  const remainingOrderModes = Object.fromEntries(
    Object.entries(orderControlModesForMode(mode)).filter(([lotId]) => remainingBuyLotIds.has(lotId)),
  );
  const remainingOrderOverrides = Object.fromEntries(
    Object.entries(orderControlOverridesForMode(mode)).filter(([lotId]) => remainingBuyLotIds.has(lotId)),
  );
  setTradingLedger(mode, remainingRows);
  setOrderControlModesForMode(mode, remainingOrderModes);
  setOrderControlOverridesForMode(mode, remainingOrderOverrides);
}

function archiveRowsForMode(mode: TradingWindowMode) {
  const rows = normalizedTradeHistoryForMode(mode);
  const latest = currentCandle();
  return {
    algorithm: mode,
    algorithmLabel: algorithmDisplayName(mode),
    rowCount: rows.length,
    rows,
    openLots: openOrderLots(mode),
    position: latest ? summarizePositionFromTradeHistory(latest.close, latest.close, mode) : null,
    settings: compactTradingSettings(tradingSettingsForMode(mode)),
    targetOrder: compactTargetOrder(targetOrderForMode(mode)),
    decision: safeArchiveValue(() => decisionEvidence(mode, targetOrderForMode(mode)), { summary: "Decision evidence unavailable" }),
    backtest:
      mode === "meta"
        ? null
        : mode === "confidence" || mode === "regime"
        ? compactBacktestEvidence(confidenceBacktestResult, mode === "regime" ? "Regime backtest" : "WCA backtest")
        : mode === "weighted"
          ? null
          : compactBacktestEvidence(state.algoBacktestResult, "Voting Ensemble backtest"),
  };
}

function safeArchiveValue<T>(reader: () => T, fallback: T): T {
  try {
    return reader();
  } catch {
    return fallback;
  }
}

function buildTradeHistoryArchivePayload(sessionDate: string, reason: string) {
  const latest = currentCandle();
  return {
    sessionDate,
    reason,
    symbol: state.symbol,
    marketStatus: state.marketStatus,
    appContext: {
      capturedAt: new Date().toISOString(),
      feed: state.feed,
      timeframe: state.timeframe,
      source: state.source,
      latestCandle: latest
        ? {
            timestamp: latest.timestamp,
            open: latest.open,
            high: latest.high,
            low: latest.low,
            close: latest.close,
            volume: latest.volume,
          }
        : null,
      marketContext: state.marketContext,
      mlArtifact: mlArtifactEvidence(),
      dynamicArtifact: state.dynamicArtifact
        ? {
            artifactId: state.dynamicArtifact.artifactId,
            status: state.dynamicArtifact.status,
            rangeLabel: state.dynamicArtifact.rangeLabel,
          }
        : null,
    },
    algorithms: {
      ensemble: archiveRowsForMode("ensemble"),
      weighted: archiveRowsForMode("weighted"),
      confidence: archiveRowsForMode("confidence"),
      regime: archiveRowsForMode("regime"),
      meta: archiveRowsForMode("meta"),
    },
  };
}

async function archiveTradeHistoryForSession(sessionDate: string, reason: string) {
  const response = await fetch(`${API_BASE}/api/trade-history/archive`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildTradeHistoryArchivePayload(sessionDate, reason)),
  });
  if (!response.ok) {
    throw new Error(await response.text());
  }
  return response.json() as Promise<{ ok: boolean; path: string; savedAt: string; sessionDate: string }>;
}

function clearAllAlgorithmTradeHistory() {
  (["ensemble", "weighted", "confidence", "regime", "meta"] as TradingWindowMode[]).forEach((mode) => {
    clearClosedTradeHistoryForMode(mode);
  });
  updateQuoteCard(currentCandle());
}

async function handleTradeHistoryMarketRollover(market: MarketStatus) {
  if (tradeHistoryRolloverInFlight) {
    return;
  }
  const sessionDate = latestTradeHistorySessionDate();
  if (!shouldArchiveTradeHistoryForMarket(market, sessionDate)) {
    return;
  }
  const rollover = tradeHistoryRolloverState();
  tradeHistoryRolloverInFlight = true;
  try {
    if (!rollover.archivedSessionDates.includes(sessionDate)) {
      await archiveTradeHistoryForSession(sessionDate, market.isOpen ? "market-open-carryover" : `market-${market.status}`);
      rollover.archivedSessionDates = [...rollover.archivedSessionDates, sessionDate];
      saveTradeHistoryRolloverState(rollover);
      void saveBrowserStorageSnapshot(`trade-history-archive-${sessionDate}`);
    }
    if (shouldClearArchivedTradeHistoryBeforeOpen(market, sessionDate, rollover)) {
      clearAllAlgorithmTradeHistory();
      rollover.clearedSessionDates = [...rollover.clearedSessionDates, sessionDate];
      saveTradeHistoryRolloverState(rollover);
      void saveBrowserStorageSnapshot(`trade-history-clear-${sessionDate}`);
    }
  } catch {
    // Keep trade rows in local storage if archiving fails; losing the archive is worse than stale UI.
  } finally {
    tradeHistoryRolloverInFlight = false;
  }
}

function orderControlModesForMode(mode: TradingWindowMode) {
  return mode === "meta"
    ? state.metaOrderControlModes
    : mode === "regime"
    ? state.regimeOrderControlModes
    : mode === "confidence"
      ? state.confidenceOrderControlModes
      : mode === "weighted"
        ? state.weightedOrderControlModes
        : state.orderControlModes;
}

function orderControlSubmitMode(mode: TradingWindowMode, lotId: string): SubmitOrderMode {
  return orderControlModesForMode(mode)[lotId] ?? DEFAULT_SUBMIT_MODE;
}

function setOrderControlModesForMode(mode: TradingWindowMode, modes: Record<string, SubmitOrderMode>) {
  if (mode === "meta") {
    state.metaOrderControlModes = modes;
    saveMetaOrderControlModes();
    return;
  }
  if (mode === "regime") {
    state.regimeOrderControlModes = modes;
    saveRegimeOrderControlModes();
    return;
  }
  if (mode === "confidence") {
    state.confidenceOrderControlModes = modes;
    saveConfidenceOrderControlModes();
    return;
  }
  if (mode === "weighted") {
    state.weightedOrderControlModes = modes;
    saveWeightedOrderControlModes();
    return;
  }
  state.orderControlModes = modes;
  saveOrderControlModes();
}

function orderControlOverridesForMode(mode: TradingWindowMode) {
  return mode === "meta"
    ? state.metaOrderControlOverrides
    : mode === "regime"
    ? state.regimeOrderControlOverrides
    : mode === "confidence"
      ? state.confidenceOrderControlOverrides
      : mode === "weighted"
        ? state.weightedOrderControlOverrides
        : state.orderControlOverrides;
}

function setOrderControlOverridesForMode(mode: TradingWindowMode, overrides: Record<string, LotOrderOverride>) {
  if (mode === "meta") {
    state.metaOrderControlOverrides = overrides;
    saveMetaOrderControlOverrides();
    return;
  }
  if (mode === "regime") {
    state.regimeOrderControlOverrides = overrides;
    saveRegimeOrderControlOverrides();
    return;
  }
  if (mode === "confidence") {
    state.confidenceOrderControlOverrides = overrides;
    saveConfidenceOrderControlOverrides();
    return;
  }
  if (mode === "weighted") {
    state.weightedOrderControlOverrides = overrides;
    saveWeightedOrderControlOverrides();
    return;
  }
  state.orderControlOverrides = overrides;
  saveOrderControlOverrides();
}

function tradingSettingsForMode(mode: TradingWindowMode) {
  return mode === "meta"
    ? state.metaTradingSettings
    : mode === "regime"
    ? state.regimeTradingSettings
    : mode === "confidence"
      ? state.confidenceTradingSettings
      : mode === "weighted"
        ? state.weightedTradingSettings
        : state.tradingSettings;
}

function targetOrderForMode(mode: TradingWindowMode) {
  return mode === "meta"
    ? state.currentMetaTargetOrder
    : mode === "regime"
    ? state.currentRegimeTargetOrder
    : mode === "confidence"
      ? state.currentConfidenceTargetOrder
      : mode === "weighted"
        ? state.currentWeightedTargetOrder
        : state.currentTargetOrder;
}

function updateQuoteCard(latest: Candle | null) {
  updateTradeToggleButton();
  if (!latest) {
    quotePrice.textContent = "--";
    quoteChange.textContent = "--";
    quoteAsk.textContent = "--";
    quoteBid.textContent = "--";
    quoteStats.innerHTML = "";
    quotePosition.innerHTML = "";
    openOrderControls.innerHTML = "";
    sellOrderButton.disabled = true;
    buyOrderButton.disabled = true;
    buyOrderButton.title = "Target order unavailable";
    sellOrderButton.title = "No shares available to sell";
    closePositionButton.hidden = true;
    closePositionButton.disabled = true;
    closePositionButton.title = "No open position";
    renderTradeHistory();
    return;
  }

  const visible = visibleCandles();
  const previous = visible.length > 1 ? visible[visible.length - 2] : latest;
  const change = latest.close - previous.close;
  const changePct = previous.close ? (change / previous.close) * 100 : 0;
  const high = Math.max(...visible.map((candle) => candle.high));
  const low = Math.min(...visible.map((candle) => candle.low));
  const volume = latest.volume;
  const avgVolume = visible.reduce((sum, candle) => sum + candle.volume, 0) / visible.length;
  const ask = latest.close + 0.01;
  const bid = latest.close - 0.01;
  const position = summarizePositionFromTradeHistory(latest.close, previous.close);

  quotePrice.textContent = price(latest.close);
  quoteChange.textContent = `${signed(change)} ${signed(changePct)}%`;
  quoteChange.className = `quote-change ${change >= 0 ? "up" : "down"}`;
  quoteAsk.textContent = price(ask);
  quoteBid.textContent = price(bid);

  quoteStats.innerHTML = statColumns([
    ["Open", price(latest.open)],
    ["Prior Close", price(previous.close)],
    ["High", price(high)],
    ["Low", price(low)],
    ["Volume", compact(volume)],
    ["Average Volume", compact(avgVolume)],
    ["Hist Vol Cls", `${Math.abs(changePct * 12).toFixed(2)}%`],
    ["Open Int", "N/A"],
    ["Put/Call Vol", "N/A"],
  ]);

  quotePosition.innerHTML = statColumns([
    ["Shares", String(position.shares)],
    ["Market Value", money(position.marketValue)],
    ["Daily P&L", money(position.dailyPnl)],
    ["Return", `${signed(position.returnPct)}%`],
    ["Avg. Price", money(position.avgPrice)],
    ["Cost Basis", money(position.costBasis)],
    ["Unrealized P&L", money(position.unrealizedPnl)],
    ["Realized P&L", money(position.realizedPnl)],
  ]);
  closePositionButton.hidden = position.shares === 0;
  closePositionButton.disabled = position.shares === 0 || !canSubmitTrades();
  closePositionButton.title = position.shares === 0
    ? "No open position"
    : canSubmitTrades()
      ? "Close the full open position"
      : tradeSubmissionBlockedTitle();
  updateOrderButtonStates(position);
  renderOpenOrderControls(latest.close);
  renderTradeHistory();
}

function recordTradeHistory(side: TradeHistoryRow["side"], mode: TradingWindowMode = activeTradingModeFromAlgoTab()) {
  if (state.tradingWindowMode !== mode) {
    setTradingWindowMode(mode);
  }
  const latest = latestExecutionCandleForMode(mode);
  if (!latest) {
    return;
  }
  if (!canSubmitTrades()) {
    updateQuoteCard(latest);
    return;
  }
  if (side === "Sell") {
    submitSelectedOpenOrderSell(mode);
    return;
  }
  const order = targetOrderForMode(mode);
  const executionPrice = order ? targetOrderExecutionPrice(order, latest.close) : latest.close;
  const position = summarizePositionFromTradeHistory(latest.close, latest.close, mode);
  const quantity = order ? automaticOrderQuantity(order, position, mode) : 1;
  const lateSessionBuyBlocker = side === "Buy" ? lateSessionAboveAverageBuyBlocker(mode, executionPrice, latest.timestamp) : "";
  if (lateSessionBuyBlocker) {
    window.alert(lateSessionBuyBlocker);
    updateQuoteCard(latest);
    return;
  }
  const forecastBuyBlockers = side === "Buy" ? forecastBuySafetyBlockers(mode, executionPrice, latest.timestamp) : [];
  if (forecastBuyBlockers.length) {
    window.alert(forecastBuyBlockers.join("\n"));
    updateQuoteCard(latest);
    return;
  }
  if (quantity <= 0) {
    updateQuoteCard(latest);
    return;
  }
  const warnings = targetOrderConsistencyWarnings(side, order, executionPrice);
  if (warnings.length && !confirmTargetOrderMismatch(side, warnings, quantity, executionPrice)) {
    updateQuoteCard(latest);
    return;
  }
  appendTradeHistory(side, quantity, executionPrice, undefined, mode, {
    submitMode: "Manual",
    trigger: `${side} Order button`,
  });
  if (mode === "ensemble") {
    clearRecommendedTargetOverrides();
  }
  updateAlgorithmPanel(visibleCandles());
  updateQuoteCard(latest);
}

function targetOrderConsistencyWarnings(side: TradeHistoryRow["side"], order: ManualOrderRecommendation | null, executionPrice: number) {
  const warnings: string[] = [];
  if (!order) {
    return ["No target order is loaded in Trading Settings"];
  }
  const orderType = order.orderType.trim();
  if (order.side !== side) {
    warnings.push(`Target side is ${order.side}, not ${side}`);
  }
  if (!isActiveTargetOrder(order)) {
    warnings.push(`Target order is inactive (${orderType || "blank"})`);
  }
  if (Math.floor(Number(order.quantity) || 0) <= 0) {
    warnings.push("Target quantity is not positive");
  }
  if (executionPrice <= 0) {
    warnings.push("Target execution price is not positive");
  }
  if (order.symbol && order.symbol !== state.symbol) {
    warnings.push(`Target symbol is ${order.symbol}, chart symbol is ${state.symbol}`);
  }
  return warnings;
}

function isLastThirtyRegularSessionMinutes(timestamp: string) {
  const minutes = easternMinutes(timestamp);
  return isRegularSession(timestamp) && minutes >= 15 * 60 + 30 && minutes < 16 * 60;
}

function lateSessionAboveAverageBuyBlocker(
  mode: TradingWindowMode,
  latestPrice: number,
  timestamp?: string | null,
) {
  if (!["ensemble", "weighted", "confidence", "regime"].includes(mode)) {
    return "";
  }
  if (!timestamp || latestPrice <= 0 || !isLastThirtyRegularSessionMinutes(timestamp)) {
    return "";
  }
  const position = summarizePositionFromTradeHistory(latestPrice, latestPrice, mode);
  if (position.shares <= 0 || position.avgPrice <= 0 || latestPrice <= position.avgPrice) {
    return "";
  }
  return `${algorithmDisplayName(mode)} late-session buy guard: no new Buy orders in the last 30 minutes before close when price ${price(latestPrice)} is above avg price ${price(position.avgPrice)}.`;
}

function lateSessionAboveAverageBuyGate(
  mode: TradingWindowMode,
  latestPrice: number,
  timestamp?: string | null,
): TradeLayerGate | null {
  const detail = lateSessionAboveAverageBuyBlocker(mode, latestPrice, timestamp);
  if (!detail) {
    return null;
  }
  return {
    layer: `${algorithmDisplayName(mode)} Late-session Buy Guard`,
    status: "fail",
    signal: "Buy blocked",
    detail,
  };
}

function activeReadyMarketForecast() {
  const forecast = state.marketForecast;
  if (!forecast || forecast.status !== "ready") {
    return null;
  }
  const updatedAt = new Date(forecast.updatedAt).getTime();
  if (!Number.isFinite(updatedAt) || Date.now() - updatedAt > 10 * 60 * 1000) {
    return null;
  }
  return forecast;
}

function forecastActionConfidence(forecast: MarketForecastPrediction, action = forecast.decision.action) {
  if (action === "buy") {
    return forecast.probabilityBuySuccess ?? forecast.probabilitySuccess ?? forecast.decision.confidence;
  }
  if (action === "sell") {
    return forecast.probabilitySellSuccess ?? forecast.probabilityStop ?? forecast.decision.confidence;
  }
  return forecast.probabilityTimeout ?? forecast.decision.confidence;
}

function forecastActionExpectedValue(forecast: MarketForecastPrediction, action = forecast.decision.action) {
  if (action === "buy") {
    return forecast.buyExpectedValue ?? forecast.expectedValue ?? forecast.decision.expectedValue;
  }
  if (action === "sell") {
    return forecast.sellExpectedValue ?? forecast.expectedValue ?? forecast.decision.expectedValue;
  }
  return forecast.expectedValue ?? forecast.decision.expectedValue;
}

function forecastMinimumEdge(forecast: MarketForecastPrediction) {
  return Math.max(
    0,
    forecast.minimumEdgeGap ?? 0,
    forecast.decision.minimumEdgeGap ?? 0,
  );
}

function forecastPredictsUpStrongly(forecast: MarketForecastPrediction, minimumConfidence = FORECAST_SAFETY_STOP_OVERRIDE_CONFIDENCE) {
  const confidence = forecastActionConfidence(forecast, "buy") ?? 0;
  const expectedValue = forecastActionExpectedValue(forecast, "buy") ?? 0;
  const edgeGap = forecast.decision.edgeGap ?? 0;
  return (
    forecast.allowed &&
    forecast.decision.action === "buy" &&
    confidence >= Math.max(minimumConfidence, forecast.threshold ?? 0) &&
    expectedValue > 0 &&
    edgeGap >= forecastMinimumEdge(forecast)
  );
}

function forecastPredictsDown(forecast: MarketForecastPrediction) {
  const action = forecast.decision.action === "sell" ? "sell" : forecast.decision.candidateAction === "sell" ? "sell" : "no_trade";
  if (action !== "sell") {
    return false;
  }
  const confidence = forecastActionConfidence(forecast, "sell") ?? 0;
  const edgeGap = forecast.decision.edgeGap ?? 0;
  return confidence >= Math.max(forecast.threshold ?? 0, 0.55) && edgeGap >= forecastMinimumEdge(forecast);
}

function forecastStrongEnoughForOverextendedBuy(forecast: MarketForecastPrediction) {
  const confidence = forecastActionConfidence(forecast, "buy") ?? 0;
  const expectedValue = forecastActionExpectedValue(forecast, "buy") ?? 0;
  const edgeGap = forecast.decision.edgeGap ?? 0;
  return (
    forecastPredictsUpStrongly(forecast, FORECAST_SAFETY_STRONG_CONFIDENCE) &&
    confidence >= FORECAST_SAFETY_STRONG_CONFIDENCE &&
    expectedValue > 0 &&
    edgeGap >= forecastMinimumEdge(forecast) * 1.25
  );
}

function intradayTrendIsUp(latestPrice: number) {
  const forecastTrend = state.marketForecast?.marketRegime?.trend ?? state.marketForecast?.regime?.trend ?? "";
  if (forecastTrend.toLowerCase().includes("up")) {
    return true;
  }
  const sessionCandles = latestRegularSessionCandles();
  if (sessionCandles.length < 20) {
    return false;
  }
  const closes = sessionCandles.map((candle) => candle.close);
  const sma20 = simpleMovingAverage(closes, Math.min(20, closes.length));
  const sma50 = simpleMovingAverage(closes, Math.min(50, closes.length));
  const vwap = sessionVwapValue(sessionCandles);
  return sma20 !== null && sma50 !== null && vwap !== null && latestPrice > vwap && sma20 >= sma50 && latestPrice >= sma20;
}

function dayAverageVwapExtension(latestPrice: number) {
  const sessionCandles = latestRegularSessionCandles();
  if (!sessionCandles.length || latestPrice <= 0) {
    return null;
  }
  const dayAverage = simpleMovingAverage(sessionCandles.map((candle) => candle.close), sessionCandles.length);
  const vwap = sessionVwapValue(sessionCandles);
  const extensions = [dayAverage, vwap]
    .filter((value): value is number => value !== null && value > 0)
    .map((value) => ({ reference: value, extension: (latestPrice - value) / value }));
  const over = extensions.filter((item) => item.extension > 0).sort((left, right) => right.extension - left.extension).at(0);
  return over ? { ...over, dayAverage, vwap } : null;
}

function forecastBuySafetyBlockers(mode: TradingWindowMode, latestPrice: number, timestamp?: string | null) {
  const forecast = activeReadyMarketForecast();
  if (!forecast || !["ensemble", "weighted", "confidence", "regime", "meta"].includes(mode)) {
    return [] as string[];
  }
  const blockers: string[] = [];
  if (intradayTrendIsUp(latestPrice) && forecastPredictsDown(forecast)) {
    blockers.push(`${algorithmDisplayName(mode)} forecast conflict: intraday trend is up, but the 5-minute forecast predicts downside.`);
  }
  const extension = dayAverageVwapExtension(latestPrice);
  if (
    extension &&
    extension.extension >= FORECAST_SAFETY_OVEREXTENSION_THRESHOLD &&
    !forecastStrongEnoughForOverextendedBuy(forecast)
  ) {
    blockers.push(
      `${algorithmDisplayName(mode)} overextension guard: price ${price(latestPrice)} is ${formatProbability(extension.extension)} above day average/VWAP reference ${price(extension.reference)}, and forecast confidence/EV is not strong enough to chase.`,
    );
  }
  blockers.forEach((reason) => {
    recordForecastSafetyDecisionChange({
      mode,
      action: "block_buy",
      originalDecision: "Buy",
      finalDecision: "Hold",
      reason,
      latestPrice,
      latestTimestamp: timestamp ?? null,
      forecast,
    });
  });
  return blockers;
}

function forecastBuySafetyGates(mode: TradingWindowMode, latestPrice: number, timestamp?: string | null): TradeLayerGate[] {
  return forecastBuySafetyBlockers(mode, latestPrice, timestamp).map((detail) => ({
    layer: `${algorithmDisplayName(mode)} Forecast Safety`,
    status: "fail",
    signal: "Buy blocked",
    detail,
  }));
}

function forecastStopOverrideKeepReason(
  mode: TradingWindowMode,
  lot: OpenOrderLot,
  template: LotOrderTemplate,
  latestPrice: number,
  timestamp?: string | null,
) {
  const forecast = activeReadyMarketForecast();
  if (!forecast || !["ensemble", "weighted", "confidence", "regime"].includes(mode)) {
    return "";
  }
  const currentLoss = currentDailyLossDollars(mode, latestPrice, timestamp);
  const maxDailyLoss = maxAllowedDailyLossDollars(mode);
  if (!forecastPredictsUpStrongly(forecast) || maxDailyLoss <= 0 || currentLoss >= maxDailyLoss) {
    return "";
  }
  const stopDistance = Math.max(0, lot.entryPrice - template.stopPrice);
  const reason = `${algorithmDisplayName(mode)} forecast shock-stop hold: price ${price(latestPrice)} is below the protective stop ${price(template.stopPrice)} (${currency(stopDistance)}/share from entry), but the 5-minute forecast predicts upside with ${formatProbability(forecastActionConfidence(forecast, "buy") ?? 0)} confidence and daily loss ${currency(currentLoss)} is still inside the max daily loss ${currency(maxDailyLoss)}.`;
  recordForecastSafetyDecisionChange({
    mode,
    action: "keep_stopped_lot",
    originalDecision: "Sell",
    finalDecision: "Keep",
    reason,
    latestPrice,
    latestTimestamp: timestamp ?? null,
    forecast,
  });
  return reason;
}

function forecastStopCloseReason(
  mode: TradingWindowMode,
  lot: OpenOrderLot,
  template: LotOrderTemplate,
  latestPrice: number,
  timestamp?: string | null,
) {
  const forecast = activeReadyMarketForecast();
  if (!forecast || !["ensemble", "weighted", "confidence", "regime", "meta"].includes(mode) || !forecastPredictsDown(forecast)) {
    return "";
  }
  const currentLoss = currentDailyLossDollars(mode, latestPrice, timestamp);
  const maxDailyLoss = maxAllowedDailyLossDollars(mode);
  const stopDistance = Math.max(0, lot.entryPrice - template.stopPrice);
  const reason = `${algorithmDisplayName(mode)} forecast shock-stop close: price ${price(latestPrice)} hit the protective stop ${price(template.stopPrice)} (${currency(stopDistance)}/share from entry) and the 5-minute forecast predicts downside with ${formatProbability(forecastActionConfidence(forecast, "sell") ?? 0)} confidence. Daily loss is ${currency(currentLoss)} of max ${currency(maxDailyLoss)}.`;
  recordForecastSafetyDecisionChange({
    mode,
    action: "close_stopped_lot",
    originalDecision: "Keep",
    finalDecision: "Sell",
    reason,
    latestPrice,
    latestTimestamp: timestamp ?? null,
    forecast,
  });
  return reason;
}

function isOpeningGraceWindow(timestamp?: string | null) {
  if (!timestamp || !isRegularSession(timestamp)) {
    return false;
  }
  const minutes = easternMinutes(timestamp);
  return minutes >= 9 * 60 + 30 && minutes < 9 * 60 + 30 + OPENING_GRACE_MINUTES;
}

function isPreviousSessionLot(lot: OpenOrderLot, timestamp?: string | null) {
  if (!timestamp) {
    return false;
  }
  return easternDateString(lot.recordedAt) < easternDateString(timestamp);
}

function openingGraceKeepReason(
  mode: TradingWindowMode,
  lot: OpenOrderLot,
  template: LotOrderTemplate,
  latestPrice: number,
  timestamp?: string | null,
) {
  if (!["ensemble", "weighted", "confidence", "regime", "meta"].includes(mode)) {
    return "";
  }
  if (!isOpeningGraceWindow(timestamp) || !isPreviousSessionLot(lot, timestamp)) {
    return "";
  }
  const currentLoss = Math.max(0, (lot.entryPrice - latestPrice) * template.quantity);
  const maxRisk = Math.max(template.riskDollars, template.plannedStopRiskDollars, 0);
  const emergencyRisk = maxRisk * OPENING_GRACE_EMERGENCY_RISK_MULTIPLE;
  if (maxRisk <= 0 || currentLoss > emergencyRisk) {
    return "";
  }
  const forecast = activeReadyMarketForecast();
  if (forecast && forecastPredictsDown(forecast)) {
    return "";
  }
  const forecastText = forecast
    ? `5-minute forecast is not strongly bearish (${forecast.decision.action})`
    : "5-minute forecast is unavailable, so only emergency risk is used";
  const reason = `${algorithmDisplayName(mode)} opening grace: previous-day position is below protective stop ${price(template.stopPrice)} during the first ${OPENING_GRACE_MINUTES} minutes, but current loss ${currency(currentLoss)} is inside emergency risk ${currency(emergencyRisk)} and ${forecastText}.`;
  recordForecastSafetyDecisionChange({
    mode,
    action: "keep_opening_grace",
    originalDecision: "Sell",
    finalDecision: "Keep",
    reason,
    latestPrice,
    latestTimestamp: timestamp ?? null,
    forecast,
  });
  return reason;
}

function recordForecastSafetyDecisionChange(input: {
  mode: TradingWindowMode;
  action: ForecastSafetyDecisionChange["action"];
  originalDecision: string;
  finalDecision: string;
  reason: string;
  latestPrice: number;
  latestTimestamp: string | null;
  forecast?: MarketForecastPrediction | null;
}) {
  const id = [
    input.mode,
    input.action,
    input.latestTimestamp ?? "no-time",
    input.forecast?.updatedAt ?? "no-forecast",
    input.reason,
  ].join("|");
  try {
    const raw = window.localStorage.getItem(FORECAST_SAFETY_LOG_STORAGE_KEY);
    const existing = raw ? (JSON.parse(raw) as ForecastSafetyDecisionChange[]) : [];
    if (existing.some((item) => item.id === id)) {
      return;
    }
    const entry: ForecastSafetyDecisionChange = {
      id,
      recordedAt: new Date().toISOString(),
      mode: input.mode,
      symbol: state.symbol,
      action: input.action,
      originalDecision: input.originalDecision,
      finalDecision: input.finalDecision,
      reason: input.reason,
      latestPrice: roundNumber(input.latestPrice, 4),
      latestTimestamp: input.latestTimestamp,
      forecastAction: input.forecast?.decision.action ?? null,
      forecastConfidence: input.forecast ? forecastActionConfidence(input.forecast) : null,
      forecastExpectedValue: input.forecast ? forecastActionExpectedValue(input.forecast) : null,
      forecastEdgeGap: input.forecast?.decision.edgeGap ?? null,
    };
    window.localStorage.setItem(FORECAST_SAFETY_LOG_STORAGE_KEY, JSON.stringify([entry, ...existing].slice(0, 500)));
  } catch {
    // Logging must never block a trade-safety decision.
  }
}

function confirmTargetOrderMismatch(
  side: TradeHistoryRow["side"],
  warnings: string[],
  quantity: number,
  executionPrice: number,
) {
  return window.confirm(
    [
      `${side} order does not match the Target Order settings.`,
      "",
      ...warnings.map((warning) => `- ${warning}`),
      "",
      `Continue with ${side} ${quantity} ${state.symbol} at ${moneyWithCents(executionPrice)}?`,
    ].join("\n"),
  );
}

function maybeAutoSubmitTargetOrder() {
  if (automaticSubmitInFlight) {
    return;
  }
  const latest = latestExecutionCandleForMode("ensemble");
  const order = state.currentTargetOrder;
  if (!latest || !order || order.submitMode !== "Automatic" || !canSubmitTrades()) {
    return;
  }
  if (order.side === "Sell") {
    return;
  }
  if (automaticTradeAlreadySubmittedForCandle("ensemble", latest)) {
    return;
  }
  const executionPrice = targetOrderExecutionPrice(order, latest.close);
  const position = summarizePositionFromTradeHistory(latest.close, latest.close, "ensemble");
  const quantity = automaticOrderQuantity(order, position, "ensemble");
  const rejection = automaticOrderRejectionReason(order, position, quantity, executionPrice, "ensemble");
  if (rejection) {
    return;
  }
  const key = automaticOrderKeyForMode("ensemble", order, quantity, executionPrice);
  if (state.autoSubmittedOrderKeys.includes(key)) {
    return;
  }

  automaticSubmitInFlight = true;
  try {
    appendTradeHistory(order.side as TradeHistoryRow["side"], quantity, executionPrice, undefined, "ensemble", {
      submitMode: "Automatic",
      trigger: "Voting Ensemble target order",
    });
    rememberAutoSubmittedOrderKey(key);
    clearRecommendedTargetOverrides();
    updateAlgorithmPanel(visibleCandles());
    updateQuoteCard(latest);
  } finally {
    automaticSubmitInFlight = false;
  }
}

function maybeAutoSubmitWeightedTargetOrder() {
  if (automaticSubmitInFlight) {
    return;
  }
  const latest = latestExecutionCandleForMode("weighted");
  const order = state.currentWeightedTargetOrder;
  if (!latest || !order || order.submitMode !== "Automatic" || !canSubmitTrades()) {
    return;
  }
  if (order.side !== "Buy") {
    return;
  }
  if (automaticTradeAlreadySubmittedForCandle("weighted", latest)) {
    return;
  }
  const executionPrice = targetOrderExecutionPrice(order, latest.close);
  const position = summarizePositionFromTradeHistory(latest.close, latest.close, "weighted");
  const quantity = automaticOrderQuantity(order, position, "weighted");
  const rejection = automaticOrderRejectionReason(order, position, quantity, executionPrice, "weighted");
  if (rejection) {
    return;
  }
  const key = automaticOrderKeyForMode("weighted", order, quantity, executionPrice);
  if (state.autoSubmittedOrderKeys.includes(key)) {
    return;
  }

  automaticSubmitInFlight = true;
  try {
    appendTradeHistory("Buy", quantity, executionPrice, undefined, "weighted", {
      submitMode: "Automatic",
      trigger: "Weighted Voting target order",
    });
    rememberAutoSubmittedOrderKey(key);
    updateWeightedVotingPanel();
    updateQuoteCard(latest);
  } finally {
    automaticSubmitInFlight = false;
  }
}

function maybeAutoSubmitConfidenceTargetOrder() {
  if (automaticSubmitInFlight) {
    return;
  }
  const latest = latestExecutionCandleForMode("confidence");
  const order = state.currentConfidenceTargetOrder;
  if (!latest || !order || order.submitMode !== "Automatic" || !canSubmitTrades()) {
    return;
  }
  if (order.side !== "Buy") {
    return;
  }
  if (automaticTradeAlreadySubmittedForCandle("confidence", latest)) {
    return;
  }
  const executionPrice = targetOrderExecutionPrice(order, latest.close);
  const position = summarizePositionFromTradeHistory(latest.close, latest.close, "confidence");
  const quantity = automaticOrderQuantity(order, position, "confidence");
  const rejection = automaticOrderRejectionReason(order, position, quantity, executionPrice, "confidence");
  if (rejection) {
    return;
  }
  const key = automaticOrderKeyForMode("confidence", order, quantity, executionPrice);
  if (state.autoSubmittedOrderKeys.includes(key)) {
    return;
  }

  automaticSubmitInFlight = true;
  try {
    appendTradeHistory("Buy", quantity, executionPrice, undefined, "confidence", {
      submitMode: "Automatic",
      trigger: "WCA target order",
    });
    rememberAutoSubmittedOrderKey(key);
    updateConfidenceAggregationPanel();
    updateQuoteCard(latest);
  } finally {
    automaticSubmitInFlight = false;
  }
}

function maybeAutoSubmitRegimeTargetOrder() {
  if (automaticSubmitInFlight) {
    return;
  }
  const latest = latestExecutionCandleForMode("regime");
  const order = state.currentRegimeTargetOrder;
  if (!latest || !order || order.submitMode !== "Automatic" || !canSubmitTrades()) {
    return;
  }
  if (order.side !== "Buy") {
    return;
  }
  if (automaticTradeAlreadySubmittedForCandle("regime", latest)) {
    return;
  }
  const executionPrice = targetOrderExecutionPrice(order, latest.close);
  const position = summarizePositionFromTradeHistory(latest.close, latest.close, "regime");
  const quantity = automaticOrderQuantity(order, position, "regime");
  const rejection = automaticOrderRejectionReason(order, position, quantity, executionPrice, "regime");
  if (rejection) {
    return;
  }
  const key = automaticOrderKeyForMode("regime", order, quantity, executionPrice);
  if (state.autoSubmittedOrderKeys.includes(key)) {
    return;
  }

  automaticSubmitInFlight = true;
  try {
    appendTradeHistory("Buy", quantity, executionPrice, undefined, "regime", {
      submitMode: "Automatic",
      trigger: "Regime target order",
    });
    rememberAutoSubmittedOrderKey(key);
    updateRegimeSelectionPanel();
    updateQuoteCard(latest);
  } finally {
    automaticSubmitInFlight = false;
  }
}

function maybeAutoSubmitAllAlgorithms() {
  if (!canSubmitTrades()) {
    return;
  }
  maybeAutoSubmitTargetOrder();
  maybeAutoSubmitWeightedTargetOrder();
  maybeAutoSubmitConfidenceTargetOrder();
  maybeAutoSubmitRegimeTargetOrder();
  maybeAutoSubmitOpenOrderControls();
}

function openOrderLots(mode: TradingWindowMode = state.tradingWindowMode) {
  const lots: OpenOrderLot[] = [];
  const trades = tradeHistoryForMode(mode)
    .filter((trade) => trade.symbol === state.symbol)
    .slice()
    .reverse();

  for (const trade of trades) {
    const quantity = Math.max(0, Math.floor(Number(trade.quantity) || 0));
    if (!quantity) {
      continue;
    }
    if (trade.side === "Buy") {
      lots.push({
        id: trade.id,
        symbol: trade.symbol,
        originalQuantity: quantity,
        remainingQuantity: quantity,
        entryPrice: trade.price,
        recordedAt: trade.recordedAt,
      });
      continue;
    }

    let remainingSell = quantity;
    if (trade.closedLotId) {
      const lot = lots.find((item) => item.id === trade.closedLotId);
      if (lot) {
        const closedQuantity = Math.min(lot.remainingQuantity, remainingSell);
        lot.remainingQuantity -= closedQuantity;
      }
    }
  }

  return lots.filter((lot) => lot.remainingQuantity > 0);
}

function specificLotRealizedPnl(mode: TradingWindowMode = state.tradingWindowMode) {
  const history = tradeHistoryForMode(mode);
  const buyRows = new Map(
    history
      .filter((trade) => trade.symbol === state.symbol && trade.side === "Buy")
      .map((trade) => [trade.id, trade]),
  );
  return history
    .filter((trade) => trade.symbol === state.symbol && trade.side === "Sell" && trade.closedLotId)
    .reduce((total, sell) => {
      const buy = buyRows.get(sell.closedLotId ?? "");
      return buy ? total + (sell.price - buy.price) * sell.quantity : total;
    }, 0);
}

function lotOrderTemplate(lot: OpenOrderLot, latestPrice: number, mode: TradingWindowMode = state.tradingWindowMode): LotOrderTemplate {
  const targetOrder = targetOrderForMode(mode);
  const settings = tradingSettingsForMode(mode);
  const weightedDefaults = mode === "weighted" ? weightedDefaultSizingSettings() : null;
  const shouldSell = Boolean(targetOrder?.eligible && targetOrder.side === "Sell" && isActiveTargetOrder(targetOrder));
  const atr = mode === "weighted" ? averageTrueRange(latestRegularSessionCandles(), 14) ?? 0 : 0;
  const weightedStopDistance = weightedDefaults
    ? defaultSizingStopDistance(weightedDefaults, lot.entryPrice, atr)
    : 0;
  const riskPerShare = weightedDefaults ? weightedStopDistance : tradingSettingsStopDistance(settings, lot.entryPrice, atr);
  const fallbackStop = roundNumber(lot.entryPrice - riskPerShare, 2);
  const fallbackTarget = roundNumber(lot.entryPrice + riskPerShare * settings.takeProfitR, 2);
  const triggerPrice = shouldSell && targetOrder?.triggerPrice !== null && targetOrder?.triggerPrice !== undefined
    ? targetOrder.triggerPrice
    : latestPrice;
  const limitPrice = shouldSell && targetOrder?.limitPrice !== null && targetOrder?.limitPrice !== undefined
    ? targetOrder.limitPrice
    : roundNumber(triggerPrice - settings.slippagePerShare, 2);
  const stopPrice = shouldSell && targetOrder?.stopPrice !== null && targetOrder?.stopPrice !== undefined
    ? targetOrder.stopPrice
    : fallbackStop;
  const targetPrice = shouldSell && targetOrder?.targetPrice !== null && targetOrder?.targetPrice !== undefined
    ? targetOrder.targetPrice
    : fallbackTarget;
  const riskDollars = weightedDefaults ? settings.startingCapital * (weightedDefaults.baseRiskPercent / 100) : targetOrder?.riskDollars ?? 0;
  const maxPositionShares = weightedDefaults ? Math.max(1, Math.floor((settings.startingCapital * (weightedDefaults.maxPositionPercent / 100)) / Math.max(lot.entryPrice, 0.01))) : lot.remainingQuantity;
  const participationShares = weightedDefaults
    ? Math.max(1, Math.floor((latestExecutionCandleForMode(mode)?.volume ?? 0) * (weightedDefaults.maxParticipationPercent / 100)))
    : lot.remainingQuantity;
  const maxAllowedShares = weightedDefaults?.maxAllowedShares ? weightedDefaults.maxAllowedShares : Number.MAX_SAFE_INTEGER;
  const defaultQuantity = Math.max(0, Math.min(lot.remainingQuantity, maxPositionShares, participationShares, maxAllowedShares));
  const plannedStopRiskDollars = roundNumber(defaultQuantity * Math.max(0, lot.entryPrice - stopPrice), 2);

  const baseTemplate: LotOrderTemplate = {
    action: shouldSell ? "Sell" : "Keep",
    quantity: defaultQuantity,
    triggerPrice: roundNumber(triggerPrice, 2),
    limitPrice: roundNumber(limitPrice, 2),
    stopPrice: roundNumber(stopPrice, 2),
    targetPrice: roundNumber(targetPrice, 2),
    riskDollars,
    plannedStopRiskDollars,
    estimatedSlippage: roundNumber(defaultQuantity * settings.slippagePerShare * 2, 2),
  };
  return withLotExitAction(lot, applyLotOrderOverrides(lot, baseTemplate, mode), latestPrice, shouldSell, mode);
}

function applyLotOrderOverrides(lot: OpenOrderLot, template: LotOrderTemplate, mode: TradingWindowMode = state.tradingWindowMode): LotOrderTemplate {
  const overrides = orderControlOverridesForMode(mode)[lot.id] ?? {};
  const quantity = Math.max(0, Math.min(lot.remainingQuantity, Math.floor(Number(overrides.quantity ?? template.quantity) || 0)));
  const stopPrice = lotOrderNumberOverride(overrides.stopPrice, template.stopPrice);
  const plannedStopRiskDollars = overrides.plannedStopRiskDollars === undefined
    ? roundNumber(quantity * Math.max(0, lot.entryPrice - stopPrice), 2)
    : lotOrderNumberOverride(overrides.plannedStopRiskDollars, template.plannedStopRiskDollars);
  return {
    action: template.action,
    quantity,
    triggerPrice: lotOrderNumberOverride(overrides.triggerPrice, template.triggerPrice),
    limitPrice: lotOrderNumberOverride(overrides.limitPrice, template.limitPrice),
    stopPrice,
    targetPrice: lotOrderNumberOverride(overrides.targetPrice, template.targetPrice),
    riskDollars: lotOrderNumberOverride(overrides.riskDollars, template.riskDollars),
    plannedStopRiskDollars,
    estimatedSlippage: lotOrderNumberOverride(overrides.estimatedSlippage, template.estimatedSlippage),
  };
}

function lotOrderNumberOverride(value: unknown, fallback: number) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? roundNumber(numeric, 2) : fallback;
}

function withLotExitAction(
  lot: OpenOrderLot,
  template: LotOrderTemplate,
  latestPrice: number,
  _targetOrderSaysSell: boolean,
  mode: TradingWindowMode,
): LotOrderTemplate {
  const sellExecutionPrice = openOrderSellExecutionPrice(latestPrice, mode);
  const reachedTakeProfit = sellExecutionPrice >= template.targetPrice;
  const reachedStop = sellExecutionPrice <= template.stopPrice;
  const profitExitReady = reachedTakeProfit;
  const riskExitReady = reachedStop;
  const latestTimestamp = latestExecutionCandleForMode(mode)?.timestamp;
  const forecastSafetyNote =
    riskExitReady && !profitExitReady
      ? openingGraceKeepReason(mode, lot, template, latestPrice, latestTimestamp) ||
        forecastStopOverrideKeepReason(mode, lot, template, latestPrice, latestTimestamp)
      : "";
  const forecastExitReason =
    riskExitReady && !profitExitReady && !forecastSafetyNote
      ? forecastStopCloseReason(mode, lot, template, latestPrice, latestTimestamp)
      : "";
  const shouldSell = (riskExitReady && !forecastSafetyNote) || profitExitReady;
  return {
    ...template,
    action: shouldSell ? "Sell" : "Keep",
    forecastSafetyNote: forecastSafetyNote || undefined,
    forecastExitReason: forecastExitReason || undefined,
  };
}

function renderOpenOrderControls(latestPrice: number, mode: TradingWindowMode = state.tradingWindowMode) {
  const lots = openOrderLots(mode);
  const openLotIds = new Set(lots.map((lot) => lot.id));
  const currentOverrides = orderControlOverridesForMode(mode);
  const nextOverrides = Object.fromEntries(
    Object.entries(currentOverrides).filter(([lotId]) => openLotIds.has(lotId)),
  ) as Record<string, LotOrderOverride>;
  if (Object.keys(nextOverrides).length !== Object.keys(currentOverrides).length) {
    setOrderControlOverridesForMode(mode, nextOverrides);
  }
  if (!lots.length) {
    openOrderControls.innerHTML = `<span class="open-order-empty">No open orders to manage</span>`;
    return;
  }
  const renderedLots = lots.map((lot) => ({ lot, template: lotOrderTemplate(lot, latestPrice, mode) }));
  const selectableSetups = renderedLots.filter(({ template }) => template.quantity > 0);
  const selectedLotId = selectedSellSetupLotId(mode, selectableSetups);
  openOrderControls.innerHTML = [
    renderSellSetupSelector(
      selectableSetups,
      selectedLotId,
      mode === "confidence" || mode === "regime" ? "Selected entry" : "Selected sell setup",
      mode === "regime" ? "No Regime entries selected" : mode === "confidence" ? "No WCA entries selected" : "No active sell setup selected",
    ),
    ...renderedLots.map(({ lot, template }) => renderOpenOrderControl(lot, template, mode, selectedLotId)),
  ].join("");
}

function selectedSellSetupLotId(
  mode: TradingWindowMode,
  sellableSetups: Array<{ lot: OpenOrderLot; template: LotOrderTemplate }>,
) {
  const sellableLotIds = sellableSetups.map(({ lot }) => lot.id);
  const current = state.selectedSellSetupByMode[mode];
  if (state.sellSetupSelectionLockedByMode[mode] && current && sellableLotIds.includes(current)) {
    return current;
  }
  const fallback = bestProfitSellSetupLotId(sellableSetups);
  if (state.selectedSellSetupByMode[mode] !== fallback || state.sellSetupSelectionLockedByMode[mode]) {
    state.selectedSellSetupByMode[mode] = fallback;
    state.sellSetupSelectionLockedByMode[mode] = false;
    saveUiState();
  }
  return fallback;
}

function bestProfitSellSetupLotId(setups: Array<{ lot: OpenOrderLot; template: LotOrderTemplate }>) {
  return setups
    .slice()
    .sort((left, right) => {
      const profitDiff = lotExitProfitDollars(right) - lotExitProfitDollars(left);
      if (profitDiff !== 0) {
        return profitDiff;
      }
      const priceDiff = right.template.limitPrice - left.template.limitPrice;
      if (priceDiff !== 0) {
        return priceDiff;
      }
      return new Date(right.lot.recordedAt).getTime() - new Date(left.lot.recordedAt).getTime();
    })
    .at(0)?.lot.id ?? "";
}

function lotExitProfitDollars(setup: { lot: OpenOrderLot; template: LotOrderTemplate }) {
  return (setup.template.limitPrice - setup.lot.entryPrice) * setup.template.quantity;
}

function lotExitProfitPerShare(lot: OpenOrderLot, template: LotOrderTemplate) {
  return template.limitPrice - lot.entryPrice;
}

function lotExitMeetsProfitFloor(lot: OpenOrderLot, template: LotOrderTemplate) {
  const profitPerShare = lotExitProfitPerShare(lot, template);
  const profitDollars = profitPerShare * template.quantity;
  return profitPerShare >= MIN_TARGET_PROFIT_PER_SHARE || profitDollars >= MIN_TARGET_PROFIT_PER_TRADE;
}

function sellExecutionMeetsProfitFloor(lot: OpenOrderLot, quantity: number, executionPrice: number) {
  const profitPerShare = executionPrice - lot.entryPrice;
  const profitDollars = profitPerShare * quantity;
  return profitPerShare >= MIN_TARGET_PROFIT_PER_SHARE || profitDollars >= MIN_TARGET_PROFIT_PER_TRADE;
}

function selectedProfitableAutomaticSellReady(
  lot: OpenOrderLot,
  template: LotOrderTemplate,
  mode: TradingWindowMode,
  selectedLotId = state.selectedSellSetupByMode[mode],
) {
  const latestPrice = latestExecutionCandleForMode(mode)?.close;
  const executionPrice = latestPrice === undefined ? 0 : openOrderSellExecutionPrice(latestPrice, mode);
  const reachedTakeProfit = executionPrice >= template.targetPrice;
  const reachedStop = executionPrice <= template.stopPrice;
  return (
    lot.id === selectedLotId &&
    orderControlSubmitMode(mode, lot.id) === "Automatic" &&
    template.quantity > 0 &&
    (reachedTakeProfit || reachedStop)
  );
}

function renderSellSetupSelector(
  setups: Array<{ lot: OpenOrderLot; template: LotOrderTemplate }>,
  selectedLotId: string,
  label = "Selected sell setup",
  emptyLabel = "No active sell setup selected",
) {
  if (!setups.length) {
    return `<span class="open-order-empty">${escapeHtml(emptyLabel)}</span>`;
  }
  return `
    <label class="open-order-sell-selector">
      <span>${escapeHtml(label)}</span>
      <select data-selected-sell-setup="true">
        ${setups.map(({ lot, template }, index) => `
          <option value="${escapeHtml(lot.id)}" ${lot.id === selectedLotId ? "selected" : ""}>
            ${escapeHtml(`${index + 1}. ${lot.symbol} ${formatTradeHistoryTime(lot.recordedAt)} - ${template.quantity} @ ${moneyWithCents(template.limitPrice)} (${signedCurrency(lotExitProfitDollars({ lot, template }))})`)}
          </option>
        `).join("")}
      </select>
    </label>
  `;
}

function renderOpenOrderControl(
  lot: OpenOrderLot,
  template: LotOrderTemplate,
  mode: TradingWindowMode = state.tradingWindowMode,
  selectedLotId = state.selectedSellSetupByMode[mode],
) {
  const submitMode = orderControlSubmitMode(mode, lot.id);
  const canManualSell = lot.id === selectedLotId;
  const selectedAutoSellReady = selectedProfitableAutomaticSellReady(lot, template, mode, selectedLotId);
  const displayAction = template.action === "Sell" || selectedAutoSellReady ? "Sell" : template.action;
  const canSell = canSubmitTrades() && !template.forecastSafetyNote && template.quantity > 0 && (template.action === "Sell" || canManualSell || selectedAutoSellReady);
  const statusText = openOrderControlStatusText({
    lot,
    template,
    submitMode,
    canManualSell,
    selectedAutoSellReady,
    canSell,
  });
  return `
    <article class="open-order-card" data-action="${displayAction.toLowerCase()}">
      <div class="open-order-head">
        <strong>${escapeHtml(lot.symbol)} ${formatTradeHistoryTime(lot.recordedAt)}</strong>
        <b>${displayAction}</b>
      </div>
      <div class="open-order-grid">
        <span>Entry<b>${moneyWithCents(lot.entryPrice)}</b></span>
        ${renderLotOrderInput(lot, "quantity", "Quantity", template.quantity, 1, 0, lot.remainingQuantity)}
        ${renderLotOrderInput(lot, "triggerPrice", "Trigger", template.triggerPrice, 0.01)}
        ${renderLotOrderInput(lot, "limitPrice", "Limit", template.limitPrice, 0.01)}
        ${renderLotOrderInput(lot, "stopPrice", "Stop", template.stopPrice, 0.01)}
        ${renderLotOrderInput(lot, "targetPrice", "Take profit", template.targetPrice, 0.01)}
        ${renderLotOrderInput(lot, "riskDollars", "Risk budget", template.riskDollars, 0.01)}
        ${renderLotOrderInput(lot, "plannedStopRiskDollars", "Stop risk", template.plannedStopRiskDollars, 0.01)}
        ${renderLotOrderInput(lot, "estimatedSlippage", "Slippage", template.estimatedSlippage, 0.01)}
      </div>
      <div class="open-order-actions">
        <label>
          <span>Submit</span>
          <select data-order-submit-mode="${escapeHtml(lot.id)}">
            <option value="Manual" ${submitMode === "Manual" ? "selected" : ""}>Manual</option>
            <option value="Automatic" ${submitMode === "Automatic" ? "selected" : ""}>Automatic</option>
          </select>
        </label>
        <button type="button" data-sell-lot-id="${escapeHtml(lot.id)}" ${canSell ? "" : "disabled"}>Sell Order</button>
      </div>
      <span class="open-order-status">${escapeHtml(statusText)}</span>
    </article>
  `;
}

function openOrderControlStatusText(input: {
  lot: OpenOrderLot;
  template: LotOrderTemplate;
  submitMode: SubmitOrderMode;
  canManualSell: boolean;
  selectedAutoSellReady: boolean;
  canSell: boolean;
}) {
  if (!canSubmitTrades()) {
    return tradeSubmissionBlockedTitle();
  }
  if (input.template.forecastSafetyNote) {
    return input.template.forecastSafetyNote;
  }
  if (input.template.forecastExitReason && input.template.action === "Sell") {
    return input.template.forecastExitReason;
  }
  if (input.template.quantity <= 0) {
    return "Quantity is 0, so no sell can be submitted.";
  }
  if (input.selectedAutoSellReady) {
    return "Automatic sell is armed because the selected entry reached its stop or take-profit.";
  }
  if (input.submitMode === "Automatic") {
    return input.template.action === "Sell"
      ? "Automatic sell condition is ready."
      : "Automatic sell is waiting for profit target or stop trigger.";
  }
  if (input.template.action === "Sell" && input.canManualSell) {
    return "Manual sell is ready. Click Sell Order to close this selected entry.";
  }
  if (input.canManualSell) {
    return "Manual sell is available for the selected entry.";
  }
  return "Select this entry above before using manual Sell Order.";
}

function renderLotOrderInput(
  lot: OpenOrderLot,
  name: keyof LotOrderTemplate,
  label: string,
  value: number,
  step: number,
  min = 0,
  max?: number,
) {
  return `
    <label>
      <span>${escapeHtml(label)}</span>
      <input
        data-lot-order-setting="${escapeHtml(name)}"
        data-lot-id="${escapeHtml(lot.id)}"
        type="number"
        min="${min}"
        ${max === undefined ? "" : `max="${max}"`}
        step="${step}"
        value="${escapeHtml(String(value))}"
      />
    </label>
  `;
}

function updateLotOrderOverride(input: HTMLInputElement | HTMLSelectElement) {
  const lotId = input.dataset.lotId ?? "";
  const key = input.dataset.lotOrderSetting as keyof LotOrderOverride | undefined;
  if (!lotId || !key || !isEditableLotOrderKey(key)) {
    return;
  }
  const mode = state.tradingWindowMode;
  const currentOverrides = orderControlOverridesForMode(mode);
  const current = currentOverrides[lotId] ?? {};
  const numeric = Number(input.value);
  const value: LotOrderOverride[keyof LotOrderOverride] | undefined = Number.isFinite(numeric) ? numeric : undefined;
  setOrderControlOverridesForMode(mode, {
    ...currentOverrides,
    [lotId]: {
      ...current,
      [key]: value,
    },
  });
  maybeAutoSubmitOpenOrderControls();
  if (input instanceof HTMLSelectElement || document.activeElement !== input) {
    updateQuoteCard(currentCandle());
  }
}

function isEditableLotOrderKey(key: string): key is keyof LotOrderOverride {
  return [
    "quantity",
    "triggerPrice",
    "limitPrice",
    "stopPrice",
    "targetPrice",
    "riskDollars",
    "plannedStopRiskDollars",
    "estimatedSlippage",
  ].includes(key);
}

function maybeAutoSubmitOpenOrderControls() {
  if (automaticSubmitInFlight || !canSubmitTrades()) {
    return;
  }
  for (const mode of ["ensemble", "weighted", "confidence", "regime", "meta"] as TradingWindowMode[]) {
    const latest = latestExecutionCandleForMode(mode);
    if (!latest) {
      continue;
    }
    if (automaticTradeAlreadySubmittedForCandle(mode, latest) || automaticTradeAlreadySubmittedThisMinute(mode, "Sell")) {
      continue;
    }
    const selectableSetups = openOrderLots(mode)
      .map((lot) => ({ lot, template: lotOrderTemplate(lot, latest.close, mode) }))
      .filter(({ template }) => template.quantity > 0);
    const selectedLotId = selectedSellSetupLotId(mode, selectableSetups);
    if (selectedLotId && orderControlSubmitMode(mode, selectedLotId) === "Automatic") {
      submitOpenOrderLot(selectedLotId, true, mode);
    }
  }
}

function selectedSellableOpenOrderLot(mode: TradingWindowMode, latestPrice: number) {
  const selectableSetups = openOrderLots(mode)
    .map((lot) => ({ lot, template: lotOrderTemplate(lot, latestPrice, mode) }))
    .filter(({ template }) => template.quantity > 0);
  const selectedLotId = selectedSellSetupLotId(mode, selectableSetups);
  const selectedSetup = selectableSetups.find(({ lot }) => lot.id === selectedLotId) ?? selectableSetups[0] ?? null;
  if (!selectedSetup) {
    return null;
  }
  if (selectedSetup.template.forecastSafetyNote) {
    return null;
  }
  return orderControlSubmitMode(mode, selectedSetup.lot.id) === "Manual" || selectedSetup.template.action === "Sell"
    ? selectedSetup.lot
    : null;
}

function submitSelectedOpenOrderSell(mode: TradingWindowMode = activeTradingModeFromAlgoTab()) {
  if (state.tradingWindowMode !== mode) {
    setTradingWindowMode(mode);
  }
  const latest = latestExecutionCandleForMode(mode);
  if (!latest || !canSubmitTrades()) {
    updateQuoteCard(latest);
    return;
  }
  const lot = selectedSellableOpenOrderLot(mode, latest.close);
  if (!lot) {
    updateQuoteCard(latest);
    return;
  }
  submitOpenOrderLot(lot.id, false, mode);
}

function submitOpenOrderLot(lotId: string, automatic: boolean, mode: TradingWindowMode = state.tradingWindowMode) {
  const latest = latestExecutionCandleForMode(mode);
  if (!latest || !canSubmitTrades()) {
    return;
  }
  const lot = openOrderLots(mode).find((item) => item.id === lotId);
  if (!lot) {
    return;
  }
  const template = lotOrderTemplate(lot, latest.close, mode);
  const selectableSetups = openOrderLots(mode)
    .map((item) => ({ lot: item, template: lotOrderTemplate(item, latest.close, mode) }))
    .filter(({ template: setupTemplate }) => setupTemplate.quantity > 0);
  const selectedLotId = selectedSellSetupLotId(mode, selectableSetups);
  const manualSelectedSell = !automatic && state.selectedSellSetupByMode[mode] === lot.id;
  const selectedAutomaticSell = automatic && selectedProfitableAutomaticSellReady(lot, template, mode, selectedLotId);
  if (template.forecastSafetyNote) {
    return;
  }
  if ((!manualSelectedSell && !selectedAutomaticSell && template.action !== "Sell") || template.quantity <= 0) {
    return;
  }
  if (automatic && automaticTradeAlreadySubmittedThisMinute(mode, "Sell")) {
    return;
  }
  const key = `lot|${lot.id}|${template.quantity}|${price(template.limitPrice)}|${price(template.stopPrice)}|${price(template.targetPrice)}`;
  if (automatic && state.autoSubmittedOrderKeys.includes(key)) {
    return;
  }
  const maxTradesPerDay =
    mode === "regime"
      ? regimeDefaultSizingSettings().maxDailyTrades
      : mode === "confidence"
      ? confidenceDefaultSizingSettings().maxDailyTrades
      : mode === "weighted"
        ? weightedDefaultSizingSettings().maxDailyTrades
        : tradingSettingsForMode(mode).maxTradesPerDay;
  if (automatic && effectiveTodaysTradeCount(mode, maxTradesPerDay, latest.close) >= maxTradesPerDay) {
    return;
  }
  const executionPrice = template.limitPrice;

  automaticSubmitInFlight = true;
  try {
    appendTradeHistory("Sell", template.quantity, executionPrice, lot.id, mode, {
      submitMode: automatic ? "Automatic" : "Manual",
      trigger: automatic ? "Automatic sell setup" : "Selected sell setup",
    });
    rememberAutoSubmittedOrderKey(key);
    if (mode === "ensemble") {
      clearRecommendedTargetOverrides();
    }
    updateAlgorithmPanel(visibleCandles());
    updateQuoteCard(latest);
  } finally {
    automaticSubmitInFlight = false;
  }
}

function openOrderSellExecutionPrice(latestPrice: number, mode: TradingWindowMode) {
  const settings = tradingSettingsForMode(mode);
  return roundNumber(Math.max(0, latestPrice - settings.slippagePerShare), 2);
}

function automaticOrderQuantity(order: ManualOrderRecommendation, position: PositionSummary, mode: TradingWindowMode = state.tradingWindowMode) {
  const requestedQuantity = Math.max(0, Math.floor(Number(order.quantity) || 0));
  if (order.side === "Sell") {
    return Math.min(requestedQuantity, position.shares);
  }
  const executionPrice = targetOrderExecutionPrice(order, latestExecutionCandleForMode(mode)?.close ?? order.triggerPrice ?? 0);
  const maxQuantity = maxPerTradeBuyQuantity(order, executionPrice, mode);
  return Math.min(requestedQuantity, maxQuantity);
}

function maxPerTradeBuyQuantity(order: ManualOrderRecommendation, executionPrice: number, mode: TradingWindowMode = state.tradingWindowMode) {
  const priceValue = executionPrice > 0 ? executionPrice : order.triggerPrice ?? order.limitPrice ?? 0;
  return maxPerTradeBuyQuantityForPrice(priceValue, mode);
}

function maxPerTradeBuyQuantityForPrice(executionPrice: number, mode: TradingWindowMode = state.tradingWindowMode) {
  const settings = tradingSettingsForMode(mode);
  const orderAllocationPercent = Math.min(Math.max(0, settings.orderAllocationPercent), maxOrderAllocationPercentForMode(mode));
  const maxOrderDollars = Math.max(0, settings.startingCapital) * (orderAllocationPercent / 100);
  return executionPrice > 0 ? Math.max(0, Math.floor(maxOrderDollars / executionPrice)) : 0;
}

function maxOrderAllocationPercentForMode(mode: TradingWindowMode) {
  return mode === "ensemble"
    ? VOTING_MAX_ORDER_ALLOCATION_PERCENT
    : mode === "regime" || mode === "meta"
      ? REGIME_MAX_ORDER_ALLOCATION_PERCENT
      : MAX_ORDER_ALLOCATION_PERCENT;
}

function automaticOrderRejectionReason(
  order: ManualOrderRecommendation,
  position: PositionSummary,
  quantity: number,
  executionPrice: number,
  mode: TradingWindowMode = "ensemble",
) {
  if (!order.eligible || !isActiveTargetOrder(order)) {
    return "Target order is not eligible or active";
  }
  if (order.side !== "Buy" && order.side !== "Sell") {
    return "Target side is Hold";
  }
  if (quantity <= 0) {
    return "Recommended quantity is not positive";
  }
  const pyramidingEnabled =
    mode === "regime"
      ? regimeDefaultSizingSettings().pyramidingEnabled
      : mode === "confidence"
      ? confidenceDefaultSizingSettings().pyramidingEnabled
      : mode === "weighted"
        ? weightedDefaultSizingSettings().pyramidingEnabled
        : state.tradingSettings.pyramidingEnabled;
  if (order.side === "Buy" && position.shares > 0 && !pyramidingEnabled) {
    return "Automatic buy blocked while a position is already open";
  }
  if (order.side === "Sell" && position.shares <= 0) {
    return "Automatic sell blocked because there are no shares to close";
  }
  if (order.side === "Buy") {
    const latest = latestExecutionCandleForMode(mode);
    const lateSessionBuyBlocker = lateSessionAboveAverageBuyBlocker(mode, executionPrice, latest?.timestamp);
    if (lateSessionBuyBlocker) {
      return lateSessionBuyBlocker;
    }
    const forecastBuyBlockers = forecastBuySafetyBlockers(mode, executionPrice, latest?.timestamp);
    if (forecastBuyBlockers.length) {
      return forecastBuyBlockers.join(" | ");
    }
  }
  if (targetOrderConsistencyWarnings(order.side as TradeHistoryRow["side"], order, executionPrice).length) {
    return "Target order settings are inconsistent";
  }
  const maxTradesPerDay =
    mode === "regime"
      ? regimeDefaultSizingSettings().maxDailyTrades
      : mode === "confidence"
      ? confidenceDefaultSizingSettings().maxDailyTrades
      : mode === "weighted"
        ? weightedDefaultSizingSettings().maxDailyTrades
        : tradingSettingsForMode(mode).maxTradesPerDay;
  if (effectiveTodaysTradeCount(mode, maxTradesPerDay, executionPrice) >= maxTradesPerDay) {
    return "Max trades/day reached";
  }
  return "";
}

function automaticOrderKey(order: ManualOrderRecommendation, quantity: number, executionPrice: number) {
  return [
    order.symbol,
    order.side,
    quantity,
    price(executionPrice),
    order.stopPrice === null ? "NA" : price(order.stopPrice),
    order.targetPrice === null ? "NA" : price(order.targetPrice),
    order.orderType,
  ].join("|");
}

function automaticOrderKeyForMode(mode: TradingWindowMode, order: ManualOrderRecommendation, quantity: number, executionPrice: number) {
  const key = automaticOrderKey(order, quantity, executionPrice);
  return mode === "ensemble" ? key : `${mode}|${key}`;
}

function automaticTradeAlreadySubmittedForCandle(
  mode: TradingWindowMode,
  latest: Candle | null,
  side?: TradeHistoryRow["side"],
) {
  const latestTimestamp = latest?.timestamp ?? "";
  if (!latestTimestamp) {
    return side
      ? automaticTradeAlreadySubmittedThisMinute(mode, side)
      : (["Buy", "Sell"] as TradeHistoryRow["side"][]).some((candidate) => automaticTradeAlreadySubmittedThisMinute(mode, candidate));
  }
  return tradeHistoryForMode(mode).some((row) => (
    row.symbol === state.symbol &&
    (!side || row.side === side) &&
    row.evidence?.submitMode === "Automatic" &&
    row.evidence?.market.latest?.timestamp === latestTimestamp
  ));
}

function automaticTradeAlreadySubmittedThisMinute(mode: TradingWindowMode, side: TradeHistoryRow["side"]) {
  const minuteKey = new Date().toISOString().slice(0, 16);
  return tradeHistoryForMode(mode).some((row) => (
    row.symbol === state.symbol &&
    row.side === side &&
    row.recordedAt.slice(0, 16) === minuteKey &&
    row.evidence?.submitMode === "Automatic"
  ));
}

function rememberAutoSubmittedOrderKey(key: string) {
  state.autoSubmittedOrderKeys = [key, ...state.autoSubmittedOrderKeys.filter((existing) => existing !== key)].slice(0, 100);
  saveAutoSubmittedOrderKeys(state.autoSubmittedOrderKeys);
}

function suppressCurrentAutomaticTargetOrder(mode: TradingWindowMode) {
  const latest = latestExecutionCandleForMode(mode);
  const order = targetOrderForMode(mode);
  if (!latest || !order || order.submitMode !== "Automatic") {
    return;
  }
  const executionPrice = targetOrderExecutionPrice(order, latest.close);
  const position = summarizePositionFromTradeHistory(latest.close, latest.close, mode);
  const quantity = automaticOrderQuantity(order, position, mode);
  if (quantity <= 0) {
    return;
  }
  rememberAutoSubmittedOrderKey(automaticOrderKeyForMode(mode, order, quantity, executionPrice));
}

function todaysTradeCount(mode: TradingWindowMode = state.tradingWindowMode) {
  return tradeHistoryForMode(mode).filter(isTradeHistoryRowFromToday).length;
}

function effectiveTodaysTradeCount(mode: TradingWindowMode, maxTradesPerDay: number, latestPrice?: number) {
  const rawCount = todaysTradeCount(mode);
  return dailyTradeCounterResetsByProfit(mode, maxTradesPerDay, latestPrice, rawCount) ? 0 : rawCount;
}

function dailyTradeCounterResetsByProfit(
  mode: TradingWindowMode,
  maxTradesPerDay: number,
  latestPrice?: number,
  rawCount = todaysTradeCount(mode),
) {
  return rawCount >= maxTradesPerDay && todayPnlForMode(mode, latestPrice) > 0;
}

function dailyTradeCountDetail(mode: TradingWindowMode, maxTradesPerDay: number, latestPrice?: number) {
  const rawCount = todaysTradeCount(mode);
  const pnl = todayPnlForMode(mode, latestPrice);
  const effectiveCount = dailyTradeCounterResetsByProfit(mode, maxTradesPerDay, latestPrice, rawCount) ? 0 : rawCount;
  const resetText = effectiveCount === 0 && rawCount >= maxTradesPerDay && pnl > 0 ? `, reset after ${signedCurrency(pnl)} today` : "";
  return `${effectiveCount} / ${maxTradesPerDay} trades today${resetText}`;
}

function todayPnlForMode(mode: TradingWindowMode, latestPrice?: number) {
  const realized = todayRealizedPnlForMode(mode);
  if (latestPrice === undefined) {
    return roundNumber(realized, 2);
  }
  const openPnl = openOrderLots(mode)
    .filter((lot) => lot.symbol === state.symbol && new Date(lot.recordedAt).toDateString() === new Date().toDateString())
    .reduce((total, lot) => total + (latestPrice - lot.entryPrice) * lot.remainingQuantity, 0);
  return roundNumber(realized + openPnl, 2);
}

function marketDataCandlesForMode(mode: TradingWindowMode) {
  if (mode === "weighted" || mode === "confidence" || mode === "regime") {
    return state.weightedMarketData.timeframeCandles["1Min"]?.length
      ? state.weightedMarketData.timeframeCandles["1Min"]
      : state.candles;
  }
  return state.candles;
}

function priorRegularCloseForMode(mode: TradingWindowMode, timestamp?: string | null) {
  const latestTimestamp = timestamp ?? latestExecutionCandleForMode(mode)?.timestamp;
  if (!latestTimestamp) {
    return null;
  }
  const latestDay = easternDateString(latestTimestamp);
  return marketDataCandlesForMode(mode)
    .filter((candle) => isRegularSession(candle.timestamp) && easternDateString(candle.timestamp) < latestDay)
    .sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime())
    .at(-1)?.close ?? null;
}

function currentSessionBaselineForMode(mode: TradingWindowMode, timestamp?: string | null) {
  const latestTimestamp = timestamp ?? latestExecutionCandleForMode(mode)?.timestamp;
  if (!latestTimestamp) {
    return null;
  }
  const latestDay = easternDateString(latestTimestamp);
  const currentSession = marketDataCandlesForMode(mode)
    .filter((candle) => isRegularSession(candle.timestamp) && easternDateString(candle.timestamp) === latestDay)
    .sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime());
  return priorRegularCloseForMode(mode, latestTimestamp) ?? currentSession[0]?.open ?? null;
}

function maxAllowedDailyLossDollars(mode: TradingWindowMode) {
  const settings = tradingSettingsForMode(mode);
  return roundNumber(Math.max(0, settings.startingCapital) * (Math.max(0, settings.maxDailyLossPercent) / 100), 2);
}

function currentDailyLossDollars(mode: TradingWindowMode, latestPrice: number, timestamp?: string | null) {
  const realized = todayRealizedPnlForMode(mode);
  const baseline = currentSessionBaselineForMode(mode, timestamp);
  const openDayPnl = baseline === null
    ? summarizePositionFromTradeHistory(latestPrice, latestPrice, mode).unrealizedPnl
    : openOrderLots(mode)
      .filter((lot) => lot.symbol === state.symbol)
      .reduce((total, lot) => total + (latestPrice - baseline) * lot.remainingQuantity, 0);
  return roundNumber(Math.max(0, -(realized + openDayPnl)), 2);
}

function todayRealizedPnlForMode(mode: TradingWindowMode) {
  const today = new Date().toDateString();
  const lots: Array<{ id: string; remainingQuantity: number; entryPrice: number }> = [];
  let realizedPnl = 0;
  for (const trade of tradeHistoryForMode(mode).filter((row) => row.symbol === state.symbol).slice().reverse()) {
    const quantity = Math.max(0, Math.floor(Number(trade.quantity) || 0));
    if (!quantity) {
      continue;
    }
    if (trade.side === "Buy") {
      lots.push({ id: trade.id, remainingQuantity: quantity, entryPrice: trade.price });
      continue;
    }
    let remainingSellQuantity = quantity;
    const matchedLots = trade.closedLotId
      ? lots.filter((lot) => lot.id === trade.closedLotId)
      : lots;
    for (const lot of matchedLots) {
      if (remainingSellQuantity <= 0) {
        break;
      }
      const closedQuantity = Math.min(lot.remainingQuantity, remainingSellQuantity);
      if (new Date(trade.recordedAt).toDateString() === today) {
        realizedPnl += (trade.price - lot.entryPrice) * closedQuantity;
      }
      lot.remainingQuantity -= closedQuantity;
      remainingSellQuantity -= closedQuantity;
    }
  }
  return roundNumber(realizedPnl, 2);
}

function algorithmDisplayName(mode: TradingWindowMode) {
  return mode === "meta"
    ? "Meta-Strategy"
    : mode === "regime"
    ? "Regime"
    : mode === "confidence"
      ? "WCA"
      : mode === "weighted"
        ? "Weighted Voting"
        : "Voting Ensemble";
}

function compactTradingSettings(settings: TradingSettings): Partial<TradingSettings> {
  return {
    startingCapital: settings.startingCapital,
    orderAllocationPercent: settings.orderAllocationPercent,
    dailyAllocationPercent: settings.dailyAllocationPercent,
    riskBudgetPercentOfOrder: settings.riskBudgetPercentOfOrder,
    maxTradesPerDay: settings.maxTradesPerDay,
    stopLossPercent: settings.stopLossPercent,
    fixedStopDistanceDollars: settings.fixedStopDistanceDollars,
    takeProfitR: settings.takeProfitR,
    slippagePerShare: settings.slippagePerShare,
    useDefaultSizingSettings: settings.useDefaultSizingSettings,
    minimumBuyScore: settings.minimumBuyScore,
    minimumSignalEdge: settings.minimumSignalEdge,
    maxPositionPercent: settings.maxPositionPercent,
    maxParticipationPercent: settings.maxParticipationPercent,
    minimumOneMinuteVolume: settings.minimumOneMinuteVolume,
    pyramidingEnabled: settings.pyramidingEnabled,
  };
}

function compactTargetOrder(order: ManualOrderRecommendation | null): Partial<ManualOrderRecommendation> | null {
  if (!order) {
    return null;
  }
  return {
    eligible: order.eligible,
    side: order.side,
    orderType: order.orderType,
    symbol: order.symbol,
    quantity: order.quantity,
    triggerPrice: order.triggerPrice,
    limitPrice: order.limitPrice,
    stopPrice: order.stopPrice,
    targetPrice: order.targetPrice,
    orderLimitDollars: order.orderLimitDollars,
    dailyLimitDollars: order.dailyLimitDollars,
    riskDollars: order.riskDollars,
    orderNotional: order.orderNotional,
    plannedStopRiskDollars: order.plannedStopRiskDollars,
    estimatedSlippage: order.estimatedSlippage,
    submitMode: order.submitMode,
    failedGates: order.failedGates,
    summary: order.summary,
  };
}

function compactBacktestEvidence(backtest: BacktestResult | null, label: string): OrderEvidenceSnapshot["backtest"] {
  if (!backtest) {
    return null;
  }
  const totalTrades = backtest.totalTrades ?? backtest.trades.length;
  return {
    label,
    timeframe: algoBacktestTimeframeLabel(backtest.timeframe),
    range: backtest.rangeLabel ?? `${backtest.startDate ?? ""} to ${backtest.endDate ?? ""}`.trim(),
    trades: totalTrades,
    pnl: roundNumber(backtest.totalPnl, 2),
    profitFactor: backtest.profitFactor ?? null,
    winRate: totalTrades ? roundNumber(backtest.winners / totalTrades, 4) : 0,
    maxDrawdown: typeof backtest.maxDrawdown === "number" ? roundNumber(backtest.maxDrawdown, 2) : undefined,
  };
}

function mlArtifactEvidence(): OrderEvidenceSnapshot["mlArtifact"] {
  const best = state.dynamicArtifact?.mlComparison?.bestByTimeframe?.[0] ?? state.mlComparison?.bestByTimeframe?.[0];
  return {
    status: dynamicArtifactStatusLabel(),
    artifactId: state.dynamicArtifact?.artifactId,
    range: state.dynamicArtifact?.rangeLabel,
    best: best ? `${best.timeframe} ${best.verdict} ${best.bestVariant} ${signedCurrency(best.bestPnl)}` : undefined,
  };
}

function decisionEvidence(mode: TradingWindowMode, order: ManualOrderRecommendation | null): OrderEvidenceSnapshot["decision"] {
  if (mode === "meta") {
    const result = calculateMetaStrategy();
    return {
      winner: result.signal,
      confidence: `${result.decisionLabel} net ${result.netScore.toFixed(2)}, edge ${result.edge.toFixed(2)}, active ${result.activeDirectionalCount}/${metaStrategyCatalog.length}`,
      strategies: result.strategies
        .filter((strategy) => strategy.signal !== "hold")
        .slice(0, 6)
        .map((strategy) => `${strategy.name}: ${strategy.signal} confidence ${strategy.confidence.toFixed(2)} family ${metaFamilyLabel(strategy.family)}`),
      gates: result.safetyGates.map((gate) => `${gate.label}: ${gate.status} - ${gate.detail}`),
      failedGates: result.safetyGates.filter((gate) => gate.status === "fail").map((gate) => `${gate.label}: ${gate.detail}`),
      summary: result.reasons.join("; ") || order?.summary,
    };
  }
  if (mode === "weighted") {
    const result = calculateWeightedVote();
    return {
      winner: result.signal,
      weighted: `${result.signal} score ${result.maxScore.toFixed(2)}, margin ${result.margin.toFixed(2)}, size ${formatProbability(result.positionSize)}`,
      strategies: result.strategies
        .slice(0, 5)
        .map(
          (strategy) =>
            `${strategy.name}: B ${formatProbability(strategy.pBuy)} / S ${formatProbability(strategy.pSell)} / H ${formatProbability(strategy.pHold)} strength ${formatProbability(strategy.strength)}`,
        ),
      gates: result.gates.map((gate) => `${gate.label}: ${gate.status} - ${gate.detail}`),
      failedGates: result.gates.filter((gate) => gate.status === "fail").map((gate) => `${gate.label}: ${gate.detail}`),
      summary: weightedTradingSettingsSummary(state.weightedTradingSettings, result),
    };
  }
  if (mode === "confidence") {
    const result = calculateConfidenceAggregation();
    return {
      winner: result.signal,
      confidence: `${result.decisionLabel} net ${result.normalizedNetScore.toFixed(2)}, active ${result.activeStrategyCount}/${confidenceAggregationStrategies.length}`,
      strategies: result.strategies.slice(0, 5).map((strategy) => `${strategy.name}: ${strategy.direction} confidence ${strategy.confidence.toFixed(2)}`),
      gates: result.hardFilters.map((filter) => `${filter.label}: ${filter.status} - ${filter.detail}`),
      failedGates: result.hardFilters.filter((filter) => filter.status === "fail").map((filter) => `${filter.label}: ${filter.detail}`),
      summary: result.detail,
    };
  }
  if (mode === "regime") {
    const result = calculateRegimeSelection();
    return {
      winner: result.signal === "No-trade" ? "Hold" : result.signal,
      confidence: `${result.confirmedCondition}, confidence ${formatProbability(result.confidence)}, buy ${formatProbability(result.scores.buy)}, edge ${formatProbability(result.scoreEdge)}`,
      strategies: result.selectedStrategies
        .filter((strategy) => strategy.selected)
        .slice(0, 5)
        .map((strategy) => `${strategy.name}: ${strategy.direction} confidence ${strategy.confidence.toFixed(2)}`),
      gates: [
        `Regime Selector: ${result.tradeAllowed ? "pass" : "fail"} - ${result.tradeBlockers.length ? result.tradeBlockers.join("; ") : "Trade gates passed"}`,
        `Market Condition: ${result.confidence >= 0.65 ? "pass" : "fail"} - ${result.confirmedCondition}`,
        `Opportunity: ${result.opportunity === "No-trade" ? "fail" : "pass"} - ${result.opportunity}`,
      ],
      failedGates: result.tradeBlockers,
      summary: result.reasons.join("; "),
    };
  }
  const votes = strategyEnsembleSignals(state.marketContext);
  const eligibleVotes = votes.filter(isEligibleStrategyVote);
  const buyVotes = eligibleVotes.filter((vote) => vote.signal === "Buy").length;
  const sellVotes = eligibleVotes.filter((vote) => vote.signal === "Sell").length;
  const holdVotes = eligibleVotes.filter((vote) => vote.signal === "Hold").length;
  return {
    winner: winningVoteSignal(buyVotes, sellVotes, holdVotes),
    voteCounts: `${buyVotes}B / ${sellVotes}S / ${holdVotes}H`,
    strategies: eligibleVotes
      .filter((vote) => vote.signal !== "Hold")
      .slice(0, 6)
      .map((vote) => `${vote.strategy}: ${vote.signal}${typeof vote.score === "number" ? ` score ${Math.round(vote.score * 100)}%` : ""}`),
    gates: order?.gates.map((gate) => `${gate.layer}: ${gate.status} - ${gate.detail}`) ?? [],
    failedGates: order?.failedGates ?? [],
    summary: order?.summary,
  };
}

function buildOrderEvidenceSnapshot(
  side: TradeHistoryRow["side"],
  quantity: number,
  executionPrice: number,
  mode: TradingWindowMode,
  options: { closedLotId?: string; submitMode?: OrderEvidenceSnapshot["submitMode"]; trigger?: string } = {},
): OrderEvidenceSnapshot {
  const latest = latestExecutionCandleForMode(mode);
  const order = targetOrderForMode(mode);
  const lot = options.closedLotId ? openOrderLots(mode).find((item) => item.id === options.closedLotId) : null;
  const sellTemplate = lot && latest ? lotOrderTemplate(lot, latest.close, mode) : null;
  const submitMode = options.submitMode ?? (automaticSubmitInFlight ? "Automatic" : "Manual");
  const backtest =
    mode === "meta"
      ? null
      : mode === "confidence" || mode === "regime"
      ? compactBacktestEvidence(confidenceBacktestResult, mode === "regime" ? "Regime backtest" : "WCA backtest")
      : mode === "weighted"
        ? null
        : compactBacktestEvidence(state.algoBacktestResult, "Voting Ensemble backtest");
  return {
    algorithm: mode,
    algorithmLabel: algorithmDisplayName(mode),
    submitMode,
    trigger: options.trigger ?? `${submitMode} ${side}`,
    capturedAt: new Date().toISOString(),
    market: {
      status: state.marketStatus,
      symbol: state.symbol,
      timeframe: state.timeframe,
      feed: state.feed,
      source: state.source,
      ...(latest
        ? {
            latest: {
              timestamp: latest.timestamp,
              open: latest.open,
              high: latest.high,
              low: latest.low,
              close: latest.close,
              volume: latest.volume,
            },
          }
        : {}),
    },
    execution: {
      side,
      quantity,
      price: executionPrice,
      notional: roundNumber(quantity * executionPrice, 2),
      ...(options.closedLotId ? { closedLotId: options.closedLotId } : {}),
    },
    positionBefore: latest ? summarizePositionFromTradeHistory(latest.close, latest.close, mode) : null,
    settings: compactTradingSettings(tradingSettingsForMode(mode)),
    targetOrder: compactTargetOrder(order),
    sellSetup: sellTemplate,
    backtest,
    mlArtifact: mlArtifactEvidence(),
    decision: decisionEvidence(mode, order),
  };
}

function appendTradeHistory(
  side: TradeHistoryRow["side"],
  quantity: number,
  tradePrice: number,
  closedLotId?: string,
  mode: TradingWindowMode = state.tradingWindowMode,
  options: { submitMode?: OrderEvidenceSnapshot["submitMode"]; trigger?: string } = {},
) {
  if (!canSubmitTrades()) {
    return;
  }
  const executionPrice = Number.isFinite(tradePrice) && tradePrice > 0 ? tradePrice : 0;
  const requestedQuantity = Math.max(0, Math.floor(Number(quantity) || 0));
  const cappedQuantity =
    side === "Buy" ? Math.min(requestedQuantity, maxPerTradeBuyQuantityForPrice(executionPrice, mode)) : requestedQuantity;
  if (executionPrice <= 0 || cappedQuantity <= 0) {
    return;
  }
  const row: TradeHistoryRow = {
    id: `${Date.now()}-${side.toLowerCase()}`,
    side,
    symbol: state.symbol,
    quantity: cappedQuantity,
    price: executionPrice,
    notional: cappedQuantity * executionPrice,
    recordedAt: new Date().toISOString(),
    ...(closedLotId ? { closedLotId } : {}),
    evidence: buildOrderEvidenceSnapshot(side, cappedQuantity, executionPrice, mode, {
      closedLotId,
      submitMode: options.submitMode,
      trigger: options.trigger,
    }),
  };
  setTradingLedger(mode, [row, ...tradeHistoryForMode(mode)].slice(0, 50));
  seedOrderControlsFromTargetOrder(row, mode);
  void saveBrowserStorageSnapshot("trade-history-update");
  void maybeSaveDecisionSnapshot("paper-trade-update");
}

function normalizedTradeHistoryForMode(mode: TradingWindowMode) {
  const rows = tradeHistoryForMode(mode);
  let changed = false;
  const normalized = rows
    .map((row) => {
      const executionPrice = Number.isFinite(row.price) && row.price > 0 ? row.price : 0;
      const requestedQuantity = Math.max(0, Math.floor(Number(row.quantity) || 0));
      const cappedQuantity =
        row.side === "Buy" ? Math.min(requestedQuantity, maxPerTradeBuyQuantityForPrice(executionPrice, mode)) : requestedQuantity;
      if (executionPrice <= 0 || cappedQuantity <= 0) {
        changed = true;
        return null;
      }
      if (cappedQuantity !== row.quantity || executionPrice !== row.price || cappedQuantity * executionPrice !== row.notional) {
        changed = true;
        return {
          ...row,
          quantity: cappedQuantity,
          price: executionPrice,
          notional: cappedQuantity * executionPrice,
        };
      }
      return row;
    })
    .filter((row): row is TradeHistoryRow => row !== null);
  if (changed) {
    setTradingLedger(mode, normalized);
  }
  return normalized;
}

function seedOrderControlsFromTargetOrder(row: TradeHistoryRow, mode: TradingWindowMode) {
  if (mode !== "confidence" || row.side !== "Buy") {
    return;
  }
  const order = targetOrderForMode(mode);
  if (!order || order.side !== "Buy") {
    return;
  }
  const nextOverride: LotOrderOverride = {
    quantity: Math.min(row.quantity, Math.max(0, Math.floor(Number(order.quantity) || 0))),
    riskDollars: order.riskDollars,
    plannedStopRiskDollars: order.plannedStopRiskDollars,
    estimatedSlippage: order.estimatedSlippage,
  };
  if (order.stopPrice !== null) {
    nextOverride.stopPrice = order.stopPrice;
  }
  if (order.targetPrice !== null) {
    nextOverride.targetPrice = order.targetPrice;
  }
  const currentOverrides = orderControlOverridesForMode(mode);
  setOrderControlOverridesForMode(mode, {
    ...currentOverrides,
    [row.id]: {
      ...nextOverride,
      ...(currentOverrides[row.id] ?? {}),
    },
  });
}

function renderTradeHistory(mode: TradingWindowMode = state.tradingWindowMode) {
  const history = normalizedTradeHistoryForMode(mode);
  renderTradeHistoryBalance(history);
  if (!history.length) {
    tradeHistoryBody.innerHTML = `
      <tr class="trade-history-empty">
        <td colspan="5">No trades today</td>
      </tr>
    `;
    return;
  }
  tradeHistoryBody.innerHTML = history
    .map(
      (trade) => `
        <tr>
          <td>${formatTradeHistoryTime(trade.recordedAt)}</td>
          <td><span class="trade-side ${trade.side.toLowerCase()}">${trade.side}</span></td>
          <td>${trade.quantity}</td>
          <td>${moneyWithCents(trade.price)}</td>
          <td>${moneyWithCents(trade.notional)}</td>
        </tr>
      `,
    )
    .join("");
}

function renderTradeHistoryBalance(history = normalizedTradeHistoryForMode(state.tradingWindowMode)) {
  const balance = history
    .filter((trade) => trade.symbol === state.symbol)
    .reduce((total, trade) => total + (trade.side === "Sell" ? trade.notional : -trade.notional), 0);
  tradeHistoryBalance.textContent = moneyWithCents(balance);
  tradeHistoryBalance.classList.toggle("up", balance > 0);
  tradeHistoryBalance.classList.toggle("down", balance < 0);
}

function scheduleDrawChart() {
  if (chartDrawFrame !== undefined) {
    return;
  }
  chartDrawFrame = window.requestAnimationFrame(() => {
    chartDrawFrame = undefined;
    drawChart();
  });
}

function drawChart() {
  const wrap = shell.querySelector<HTMLElement>(".canvas-wrap")!;
  const rect = wrap.getBoundingClientRect();
  const shellRect = shell.getBoundingClientRect();
  const width = Math.max(320, Math.floor(Math.min(rect.width, shellRect.width)));
  const height = Math.max(320, Math.floor(rect.height));
  const ratio = window.devicePixelRatio || 1;
  const canvasWidth = Math.floor(width * ratio);
  const canvasHeight = Math.floor(height * ratio);
  if (canvas.width !== canvasWidth || canvas.height !== canvasHeight) {
    canvas.width = canvasWidth;
    canvas.height = canvasHeight;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
  }

  const ctx = canvas.getContext("2d")!;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = "#ffffff";
  ctx.fillRect(0, 0, width, height);

  const visible = visibleCandles();
  if (!visible.length) {
    updateAlgorithmPanel([]);
    return;
  }

  const bounds = chartBounds(width, height);
  const overlayLevels = state.showVisualConditions ? visualConditionLevels(visible).map((line) => line.value) : [];
  const prices = visible.flatMap((candle) => [candle.high, candle.low]).concat(overlayLevels);
  const maxPrice = Math.max(...prices);
  const minPrice = Math.min(...prices);
  const padding = Math.max((maxPrice - minPrice) * 0.08, 0.05);
  const high = maxPrice + padding;
  const low = minPrice - padding;
  const maxVolume = Math.max(...visible.map((candle) => candle.volume), 1);

  if (state.showLayerBackgrounds) {
    drawLayerBackgrounds(ctx, bounds, visible);
  }
  drawGrid(ctx, bounds, high, low, visible);
  drawCandles(ctx, bounds, high, low, maxVolume, visible);
  if (state.showVisualConditions) {
    drawVisualConditions(ctx, bounds, high, low, visible);
  }
  drawAxes(ctx, bounds, high, low, visible);
  drawHover(ctx, bounds, high, low, visible);
  updateMeta();
}

function visibleCandles() {
  if (!state.candles.length) {
    return [];
  }
  const count = Math.min(state.visibleCount, state.candles.length);
  const offset = clampViewportOffset(state.viewportOffset);
  const end = state.candles.length - offset;
  const start = Math.max(0, end - count);
  return state.candles.slice(start, end);
}

function normalizeCandles(candles: Candle[]) {
  const byTimestamp = new Map<string, Candle>();
  candles.forEach((candle) => {
    const time = new Date(candle.timestamp).getTime();
    if (
      Number.isFinite(time) &&
      Number.isFinite(candle.open) &&
      Number.isFinite(candle.high) &&
      Number.isFinite(candle.low) &&
      Number.isFinite(candle.close)
    ) {
      byTimestamp.set(candle.timestamp, candle);
    }
  });
  return Array.from(byTimestamp.values()).sort(
    (a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime(),
  );
}

function resetHoverState() {
  state.hoveredIndex = -1;
  state.hoverX = -1;
  state.hoverY = -1;
  canvas.classList.remove("over-candle");
}

function setAlgoTab(tab: AlgoTab) {
  const weighted = tab === "weighted";
  const confidence = tab === "confidence";
  const regime = tab === "regime";
  const meta = tab === "meta";
  state.algoTab = tab;
  saveUiState();
  algoVotingEnsembleTabButton.classList.toggle("active", tab === "voting");
  algoVotingEnsembleTabButton.setAttribute("aria-selected", String(tab === "voting"));
  algoVotingEnsemblePanel.hidden = tab !== "voting";
  algoWeightedVotingTabButton.classList.toggle("active", weighted);
  algoWeightedVotingTabButton.setAttribute("aria-selected", String(weighted));
  algoWeightedVotingPanel.hidden = !weighted;
  algoConfidenceAggregationTabButton.classList.toggle("active", confidence);
  algoConfidenceAggregationTabButton.setAttribute("aria-selected", String(confidence));
  algoConfidenceAggregationPanel.hidden = !confidence;
  algoRegimeSelectionTabButton.classList.toggle("active", regime);
  algoRegimeSelectionTabButton.setAttribute("aria-selected", String(regime));
  algoRegimeSelectionPanel.hidden = !regime;
  algoMetaStrategyTabButton.classList.toggle("active", meta);
  algoMetaStrategyTabButton.setAttribute("aria-selected", String(meta));
  algoMetaStrategyPanel.hidden = !meta;
  setTradingWindowMode(meta ? "meta" : regime ? "regime" : confidence ? "confidence" : weighted ? "weighted" : "ensemble");
  if (weighted) {
    updateWeightedVotingPanel();
  }
  if (confidence) {
    updateConfidenceAggregationPanel();
  }
  if (regime) {
    scheduleRegimeSelectionPanelUpdate();
  }
  if (meta) {
    updateMetaStrategyPanel();
  }
}

function updateAlgorithmPanel(_candles: Candle[]) {
  const votes = strategyEnsembleSignals(state.marketContext);
  const eligibleVotes = votes.filter(isEligibleStrategyVote);
  const buyVotes = eligibleVotes.filter((vote) => vote.signal === "Buy").length;
  const sellVotes = eligibleVotes.filter((vote) => vote.signal === "Sell").length;
  const holdVotes = eligibleVotes.filter((vote) => vote.signal === "Hold").length;
  const finalSignal = winningVoteSignal(buyVotes, sellVotes, holdVotes);

  updateAlgoBacktestControls();
  algoFinalSignal.textContent = finalSignal;
  algoFinalSignal.className = `algo-final ${finalSignal.toLowerCase()}`;
  const excludedVotes = votes.length - eligibleVotes.length;
  updateConfidenceAggregationPanel();
  updateWeightedVotingPanel();
  updateRegimeSelectionPanel();
  updateMetaStrategyPanel();
  algoVoteCounts.innerHTML = [
    ["Eligible Buy", buyVotes, "buy"],
    ["Eligible Sell", sellVotes, "sell"],
    ["Eligible Hold", holdVotes, "hold"],
  ]
    .map(
      ([label, value, signal]) => `
        <div class="algo-vote-count ${signal}">
          <span>${label}</span>
          <strong>${value}</strong>
        </div>
      `,
    )
    .join("");
  algoVoteList.innerHTML = votes.map(renderAlgoVoteRow).join("");
  algoVotesToggleMeta.textContent = `${eligibleVotes.length} eligible / ${votes.length} strategies · ${buyVotes}B/${sellVotes}S/${holdVotes}H · ${excludedVotes} excluded`;
  renderAlgoVotesExpandedState();
  if (state.algoBacktestTimeframe === "Trading") {
    algoTableWrap.hidden = true;
    setAlgoTradePlanContent("");
    algoTradesTable.innerHTML = "";
    const tradingResults = renderTradingRagResults();
    tradingSettingsMount.hidden = false;
    updateTradingSettingsMount(state.currentTargetOrder ?? undefined, {
      preserveExisting: state.tradingRagStatus === "loading",
    });
    algoResultsBody.innerHTML = tradingResults;
    updateQuoteCard(currentCandle());
    maybeAutoSubmitTargetOrder();
    maybeAutoSubmitOpenOrderControls();
    void maybeSaveDecisionSnapshot("trading-panel");
    return;
  }
  tradingSettingsMount.hidden = false;
  if (state.tradingRagStatus === "idle") {
    void loadTradingRag();
  }
  const manualOrder = syncCurrentTargetOrderFromTradingRag();
  updateTradingSettingsMount(manualOrder ?? undefined, {
    preserveExisting: state.tradingRagStatus === "loading",
  });
  const backtestCandles =
    state.algoBacktestCandles.length || state.algoBacktestTimeframe !== state.timeframe
      ? state.algoBacktestCandles
      : state.candles;
  const backtest = compactVotingBacktestForDisplay(
    state.algoBacktestResult ?? backtestVotingEnsembleLastDay(backtestCandles, state.algoBacktestTimeframe),
    { preserveTrades: state.algoBacktestTimeframe === "1Min" || state.algoBacktestTimeframe === "5Min" },
  );
  algoTableWrap.hidden = true;
  setAlgoTradePlanContent(renderAlgoTradePlan(finalSignal, votes, backtest));
  renderAlgoIntradayTrades(backtest);
  algoResultsBody.innerHTML = renderAlgoResults(finalSignal, buyVotes, sellVotes, holdVotes, votes, backtest);
  maybeAutoSubmitTargetOrder();
  maybeAutoSubmitOpenOrderControls();
  void maybeSaveDecisionSnapshot("algorithm-panel");
}

function renderAlgoIntradayTrades(backtest: BacktestResult) {
  if (state.algoBacktestTimeframe !== "1Min" && state.algoBacktestTimeframe !== "5Min") {
    algoIntradayTradesSummary.innerHTML = "";
    algoIntradayTradesTable.innerHTML = renderBacktestTrades([]);
    return;
  }
  const totalTrades = backtest.totalTrades ?? backtest.trades.length;
  const winRate = totalTrades ? formatProbability(backtest.winners / totalTrades) : "0%";
  const rangeLabel = backtest.rangeLabel ?? backtest.dateLabel;
  algoIntradayTradesSummary.innerHTML = `
    <span>Backtest: <strong>${escapeHtml(algoBacktestTimeframeLabel(backtest.timeframe))}</strong></span>
    <span>Range: ${escapeHtml(rangeLabel)}</span>
    <span>Trades: ${totalTrades} · Win rate: ${winRate}</span>
    <span>P/L: <strong class="${backtest.totalPnl >= 0 ? "positive" : "negative"}">${signedCurrency(backtest.totalPnl)}</strong> (${signed(backtest.totalReturnPercent)}%)</span>
  `;
  algoIntradayTradesTable.innerHTML = renderBacktestTrades(backtest.trades);
}

function setAlgoTradePlanContent(html: string) {
  const hasContent = html.trim().length > 0;
  algoTradePlanTitle.hidden = !hasContent;
  algoTradePlan.hidden = !hasContent;
  algoTradePlan.innerHTML = html;
}

function isEligibleStrategyVote(vote: AlgoVote) {
  return vote.status === "Allowed" || vote.status === "Strong Fit";
}

function renderAlgoVotesExpandedState() {
  algoVotesToggle.setAttribute("aria-expanded", String(state.algoVotesExpanded));
  algoVoteList.hidden = !state.algoVotesExpanded;
  algoVoteList.classList.toggle("expanded", state.algoVotesExpanded);
  algoVotesToggleIcon.textContent = state.algoVotesExpanded ? "-" : "+";
}

function renderWeightedStrategiesExpandedState() {
  weightedStrategiesToggle.setAttribute("aria-expanded", String(state.weightedVotingExpanded));
  weightedStrategiesList.hidden = !state.weightedVotingExpanded;
  weightedStrategiesList.classList.toggle("expanded", state.weightedVotingExpanded);
  weightedStrategiesToggleIcon.textContent = state.weightedVotingExpanded ? "-" : "+";
}

function renderWeightedDataExpandedState() {
  weightedDataToggle.setAttribute("aria-expanded", String(state.weightedDataExpanded));
  weightedDataGrid.hidden = !state.weightedDataExpanded;
  weightedDataGrid.classList.toggle("expanded", state.weightedDataExpanded);
  weightedDataToggleIcon.textContent = state.weightedDataExpanded ? "-" : "+";
}

function renderWeightedGatesExpandedState() {
  weightedGatesToggle.setAttribute("aria-expanded", String(state.weightedGatesExpanded));
  weightedGateList.hidden = !state.weightedGatesExpanded;
  weightedGateList.classList.toggle("expanded", state.weightedGatesExpanded);
  weightedGatesToggleIcon.textContent = state.weightedGatesExpanded ? "-" : "+";
}

function renderWeightedControlsExpandedState() {
  weightedControlsToggle.setAttribute("aria-expanded", String(state.weightedControlsExpanded));
  weightedControlRules.hidden = !state.weightedControlsExpanded;
  weightedControlRules.classList.toggle("expanded", state.weightedControlsExpanded);
  weightedControlsToggleIcon.textContent = state.weightedControlsExpanded ? "-" : "+";
}

function renderConfidenceStrategiesExpandedState() {
  confidenceStrategiesToggle.setAttribute("aria-expanded", String(state.confidenceStrategiesExpanded));
  confidenceStrategiesList.hidden = !state.confidenceStrategiesExpanded;
  confidenceStrategiesList.classList.toggle("expanded", state.confidenceStrategiesExpanded);
  confidenceStrategiesToggleIcon.textContent = state.confidenceStrategiesExpanded ? "-" : "+";
}

function renderConfidenceRequirementsExpandedState() {
  confidenceRequirementsToggle.setAttribute("aria-expanded", String(state.confidenceRequirementsExpanded));
  confidenceRequirementsPanel.hidden = !state.confidenceRequirementsExpanded;
  confidenceRequirementsPanel.classList.toggle("expanded", state.confidenceRequirementsExpanded);
  confidenceRequirementsToggleIcon.textContent = state.confidenceRequirementsExpanded ? "-" : "+";
}

function renderRegimeStrategiesExpandedState() {
  regimeStrategiesToggle.setAttribute("aria-expanded", String(state.regimeStrategiesExpanded));
  regimeStrategiesList.hidden = !state.regimeStrategiesExpanded;
  regimeStrategiesList.classList.toggle("expanded", state.regimeStrategiesExpanded);
  regimeStrategiesToggleIcon.textContent = state.regimeStrategiesExpanded ? "-" : "+";
}

function renderRegimeIndicatorsExpandedState() {
  regimeIndicatorsToggle.setAttribute("aria-expanded", String(state.regimeIndicatorsExpanded));
  regimeFeatureGrid.hidden = !state.regimeIndicatorsExpanded;
  regimeFeatureGrid.classList.toggle("expanded", state.regimeIndicatorsExpanded);
  regimeIndicatorsToggleIcon.textContent = state.regimeIndicatorsExpanded ? "-" : "+";
}

function renderMetaStrategiesExpandedState() {
  metaStrategiesToggle.setAttribute("aria-expanded", String(state.metaStrategiesExpanded));
  metaStrategiesPanel.hidden = !state.metaStrategiesExpanded;
  metaStrategiesPanel.classList.toggle("expanded", state.metaStrategiesExpanded);
  metaStrategiesToggleIcon.textContent = state.metaStrategiesExpanded ? "-" : "+";
}

function renderMetaChecksExpandedState() {
  metaChecksToggle.setAttribute("aria-expanded", String(state.metaChecksExpanded));
  metaGateList.hidden = !state.metaChecksExpanded;
  metaGateList.classList.toggle("expanded", state.metaChecksExpanded);
  metaChecksToggleIcon.textContent = state.metaChecksExpanded ? "-" : "+";
}

async function loadMetaStrategyTrainingStatus(options: { train?: boolean } = {}) {
  state.metaTrainingStatus = "loading";
  state.metaTrainingWarning = "";
  renderMetaMlReadiness();
  try {
    const response = await fetch(`${API_BASE}${options.train ? "/api/meta-strategy/train-baselines" : "/api/meta-strategy/training-status"}`, {
      method: options.train ? "POST" : "GET",
      headers: options.train ? { "Content-Type": "application/json" } : undefined,
      body: options.train ? JSON.stringify({ symbol: state.symbol, minRows: 30 }) : undefined,
    });
    if (!response.ok) {
      if (response.status === 404) {
        state.metaTrainingResult = {
          status: "endpoint_unavailable",
          trusted: false,
          message: options.train
            ? "Train endpoint is unavailable in the running backend. Restart the backend so the Meta-Strategy training route is loaded."
            : "Training status endpoint is unavailable in the running backend. Restart the backend so the Meta-Strategy status route is loaded.",
        };
        state.metaTrainingStatus = "ready";
        renderMetaMlReadiness();
        return;
      }
      throw new Error(await readableResponseError(response));
    }
    state.metaTrainingResult = (await response.json()) as MetaStrategyTrainingStatus;
    state.metaTrainingStatus = "ready";
  } catch (error) {
    state.metaTrainingStatus = "error";
    state.metaTrainingWarning = error instanceof Error ? error.message : "Meta-Strategy training status unavailable.";
  }
  renderMetaMlReadiness();
}

async function readableResponseError(response: Response) {
  const text = await response.text();
  if (!text) {
    return `${response.status} ${response.statusText}`.trim();
  }
  try {
    const parsed = JSON.parse(text) as { detail?: unknown; message?: unknown };
    const detail = parsed.detail ?? parsed.message;
    return typeof detail === "string" ? detail : JSON.stringify(parsed);
  } catch {
    return text;
  }
}

function renderMetaMlReadiness() {
  const result = state.metaTrainingResult;
  const metrics = result?.metrics ?? {};
  const trusted = Boolean(metrics.trusted ?? result?.trusted);
  const status = state.metaTrainingStatus === "loading" ? "loading" : state.metaTrainingStatus === "error" ? "error" : trusted ? "trusted" : "untrusted";
  metaMlReadinessBox.dataset.status = status;

  if (state.metaTrainingStatus === "loading") {
    metaMlReadinessBox.innerHTML = `
      <div class="meta-ml-readiness-head">
        <span>ML Selector Readiness</span>
        <strong>Loading</strong>
      </div>
      <small>Reading latest Meta-Strategy training results.</small>
    `;
    return;
  }

  if (state.metaTrainingStatus === "error") {
    metaMlReadinessBox.innerHTML = `
      <div class="meta-ml-readiness-head">
        <span>ML Selector Readiness</span>
        <strong>Error</strong>
      </div>
      <small>${escapeHtml(state.metaTrainingWarning || "Training status unavailable.")}</small>
      <div class="meta-ml-readiness-actions">
        <button type="button" data-meta-training-action="refresh">Refresh</button>
        <button type="button" data-meta-training-action="train">Train</button>
      </div>
    `;
    return;
  }

  if (!result || result.status === "not_trained" || result.status === "endpoint_unavailable") {
    metaMlReadinessBox.innerHTML = `
      <div class="meta-ml-readiness-head">
        <span>ML Selector Readiness</span>
        <strong>${result?.status === "endpoint_unavailable" ? "Backend update needed" : "Not trained"}</strong>
      </div>
      <div class="meta-ml-readiness-grid">
        ${renderMetaTrainingStatusTile("Status", result?.status === "endpoint_unavailable" ? "Route unavailable" : "No artifact", result?.message ?? "No training result has been created yet.")}
        ${renderMetaTrainingStatusTile("Result", "Baseline active", "No trusted ML selector is available yet.")}
      </div>
      <div class="meta-ml-readiness-actions">
        <button type="button" data-meta-training-action="refresh">Refresh</button>
        <button type="button" data-meta-training-action="train">Train</button>
      </div>
    `;
    return;
  }

  const bestModel = String(metrics.bestModel ?? result.bestModel ?? "NA");
  const bestModelMetric = (metrics.models ?? {})[bestModel] ?? {};
  const bestBaseline = bestBaselineMetric(metrics.baselines ?? {});
  const statusLabel =
    trusted
      ? "Ready"
      : result.status === "insufficient_class_balance"
        ? "Class balance needed"
        : result.status === "insufficient_data"
          ? "More labels needed"
          : "Keep baseline";
  const rowDetail =
    result.featureCount === undefined || result.featureCount === null
      ? `${result.trainRows ?? 0} train / ${result.testRows ?? 0} test / not trained`
      : `${result.trainRows ?? 0} train / ${result.testRows ?? 0} test / ${result.featureCount} features`;
  metaMlReadinessBox.innerHTML = `
    <div class="meta-ml-readiness-head">
      <span>ML Selector Readiness</span>
      <strong>${trusted ? "Trusted" : "Not trusted"}</strong>
    </div>
    <div class="meta-ml-readiness-grid">
      ${renderMetaTrainingStatusTile("Status", statusLabel, trusted ? "Best ML model beat every baseline." : result.message ?? "ML has not beaten every baseline yet.")}
      ${renderMetaTrainingStatusTile("Best ML", metaTrainingLabel(bestModel), metricSummary(bestModelMetric))}
      ${renderMetaTrainingStatusTile("Best Baseline", metaTrainingLabel(bestBaseline.name), metricSummary(bestBaseline.metric))}
      ${renderMetaTrainingStatusTile("Rows", `${result.rows ?? 0} total`, rowDetail)}
    </div>
    ${renderMetaTrainingScoreTable("ML Models", metrics.models ?? {})}
    ${renderMetaTrainingScoreTable("Baselines", metrics.baselines ?? {})}
    ${renderMetaTrainingUnavailableModels(metrics.unavailableModels ?? {})}
    <small>${escapeHtml(renderMetaTrainingLabels(result))}</small>
    <div class="meta-ml-readiness-actions">
      <button type="button" data-meta-training-action="refresh">Refresh</button>
      <button type="button" data-meta-training-action="train">Train</button>
    </div>
  `;
}

function renderMetaTrainingStatusTile(label: string, value: string, detail: string) {
  return `
    <div>
      <b>${escapeHtml(label)}</b>
      <strong>${escapeHtml(value)}</strong>
      <span>${escapeHtml(detail)}</span>
    </div>
  `;
}

function renderMetaTrainingScoreTable(title: string, rows: Record<string, MetaStrategyTrainingMetric>) {
  const entries = Object.entries(rows);
  if (!entries.length) {
    return "";
  }
  return `
    <div class="meta-ml-score-table">
      <b>${escapeHtml(title)}</b>
      ${entries
        .map(
          ([name, metric]) => `
            <span>${escapeHtml(metaTrainingLabel(name))}</span>
            <strong>${escapeHtml(metricSummary(metric))}</strong>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderMetaTrainingUnavailableModels(rows: Record<string, string>) {
  const entries = Object.entries(rows);
  if (!entries.length) {
    return "";
  }
  return `
    <div class="meta-ml-score-table">
      <b>Unavailable Models</b>
      ${entries
        .map(
          ([name, reason]) => `
            <span>${escapeHtml(metaTrainingLabel(name))}</span>
            <strong>${escapeHtml(reason)}</strong>
          `,
        )
        .join("")}
    </div>
  `;
}

function bestBaselineMetric(rows: Record<string, MetaStrategyTrainingMetric>) {
  return Object.entries(rows).reduce(
    (best, [name, metric]) => {
      const score = metricTrustScore(metric);
      const bestScore = metricTrustScore(best.metric);
      return score > bestScore ? { name, metric } : best;
    },
    { name: "NA", metric: {} as MetaStrategyTrainingMetric },
  );
}

function metricSummary(metric: MetaStrategyTrainingMetric) {
  const trustScore = metricTrustScore(metric);
  const directionalScore = metricDirectionalMacroF1(metric);
  const trust = Number.isFinite(trustScore) && trustScore >= 0 ? formatProbability(trustScore) : "NA";
  const directionalF1 = Number.isFinite(directionalScore) && directionalScore >= 0 ? formatProbability(directionalScore) : "NA";
  const macroF1 = typeof metric.macroF1 === "number" ? formatProbability(metric.macroF1) : "NA";
  const accuracy = typeof metric.accuracy === "number" ? formatProbability(metric.accuracy) : "NA";
  return `Trust ${trust} / Dir F1 ${directionalF1} / F1 ${macroF1} / Acc ${accuracy}`;
}

function metricTrustScore(metric: MetaStrategyTrainingMetric) {
  if (typeof metric.trustScore === "number") {
    return metric.trustScore;
  }
  const directionalF1 = metricDirectionalMacroF1(metric);
  const macroF1 = typeof metric.macroF1 === "number" ? metric.macroF1 : -1;
  const nonHoldRecall = metricNonHoldRecall(metric);
  if (directionalF1 < 0 || macroF1 < 0 || nonHoldRecall < 0) {
    return macroF1;
  }
  return 0.45 * directionalF1 + 0.35 * macroF1 + 0.2 * nonHoldRecall;
}

function metricDirectionalMacroF1(metric: MetaStrategyTrainingMetric) {
  if (typeof metric.directionalMacroF1 === "number") {
    return metric.directionalMacroF1;
  }
  const buyF1 = metricClassValue(metric, "BUY", "f1");
  const sellF1 = metricClassValue(metric, "SELL", "f1");
  if (buyF1 === null && sellF1 === null) {
    return -1;
  }
  return ((buyF1 ?? 0) + (sellF1 ?? 0)) / 2;
}

function metricNonHoldRecall(metric: MetaStrategyTrainingMetric) {
  if (typeof metric.nonHoldRecall === "number") {
    return metric.nonHoldRecall;
  }
  const recalls = (["BUY", "SELL"] as const)
    .map((label) => metricClassValue(metric, label, "recall"))
    .filter((value): value is number => value !== null);
  if (!recalls.length) {
    return -1;
  }
  return recalls.reduce((sum, value) => sum + value, 0) / recalls.length;
}

function metricClassValue(metric: MetaStrategyTrainingMetric, label: "BUY" | "SELL" | "HOLD", key: "f1" | "recall") {
  const perClass = metric.perClass;
  if (!perClass || typeof perClass !== "object") {
    return null;
  }
  const classMetric = (perClass as Record<string, unknown>)[label];
  if (!classMetric || typeof classMetric !== "object") {
    return null;
  }
  const value = (classMetric as Record<string, unknown>)[key];
  return typeof value === "number" ? value : null;
}

function metaTrainingLabel(value: string) {
  return value.replaceAll("_", " ");
}

function renderMetaTrainingLabels(result: MetaStrategyTrainingStatus) {
  const labels = result.trainingLabelCounts ?? result.labelCounts ?? {};
  const validationLabels = result.validationLabelCounts;
  const trainedAt = result.trainedAt ? `Trained ${formatDate(result.trainedAt)}` : "No trained timestamp";
  const validationText = validationLabels
    ? ` - Strict B ${validationLabels.BUY ?? 0} / S ${validationLabels.SELL ?? 0} / H ${validationLabels.HOLD ?? 0}`
    : "";
  return `${trainedAt} - Training labels B ${labels.BUY ?? 0} / S ${labels.SELL ?? 0} / H ${labels.HOLD ?? 0}${validationText}`;
}

function scheduleRegimeSelectionPanelUpdate() {
  if (pendingRegimeSelectionUpdateFrame !== undefined) {
    window.cancelAnimationFrame(pendingRegimeSelectionUpdateFrame);
  }
  pendingRegimeSelectionUpdateFrame = window.requestAnimationFrame(() => {
    pendingRegimeSelectionUpdateFrame = undefined;
    if (!algoRegimeSelectionPanel.hidden) {
      updateRegimeSelectionPanel();
    }
  });
}

function updateRegimeSelectionPanel() {
  const result = calculateRegimeSelection();
  regimeFinalSignal.textContent = result.signal;
  regimeFinalSignal.className = `algo-final ${regimeSignalClass(result.signal)}`;
  regimeScoreGrid.innerHTML = renderRegimeScoreGrid(result);
  regimeSummary.innerHTML = renderRegimeSummary(result);
  if (!isEditingWithin(regimeTradingSettingsMount)) {
    regimeTradingSettingsMount.innerHTML = renderRegimeTradingSettingsPanel(result);
  }
  if (state.regimeIndicatorsExpanded) {
    regimeFeatureGrid.innerHTML = renderRegimeFeatureGrid(result.features);
  }
  regimeIndicatorsToggleMeta.textContent = `${result.features.length} indicators`;
  regimeStrategiesToggleMeta.textContent = `${result.selectedStrategyCount} selected / ${regimeSelectionStrategies.length} available`;
  if (state.regimeStrategiesExpanded) {
    regimeStrategiesList.innerHTML = renderRegimeStrategyList(result);
  }
  renderRegimeIndicatorsExpandedState();
  renderRegimeStrategiesExpandedState();
  updateQuoteCard(currentCandle());
  maybeAutoSubmitRegimeTargetOrder();
  maybeAutoSubmitOpenOrderControls();
}

function calculateRegimeSelection(): RegimeSelectionResult {
  const market = confidenceMarketSnapshot();
  if (!market) {
    return emptyRegimeSelectionResult("Waiting for regular-session candles");
  }

  const rawCondition = rawRegimeConditionFromMarket(market);
  const confirmation = confirmedRegimeCondition(market, rawCondition);
  const features = rawCondition.features;
  const noTradeReasons = rawCondition.noTradeReasons;
  const primaryTrend = confirmation.condition.primaryTrend;
  const volatility = confirmation.condition.volatility;
  const opportunity = confirmation.condition.opportunity;
  const selectedSlugs = regimeSelectedStrategySlugs(primaryTrend, volatility, opportunity);
  const selectedSlugSet = new Set(selectedSlugs);
  const selectedStrategies: RegimeSelectedStrategy[] = [];
  const skippedStrategies: Array<{ name: string; reason: string }> = [];

  regimeSelectionStrategies.forEach((strategy) => {
    if (!selectedSlugSet.has(strategy.slug)) {
      skippedStrategies.push({ name: strategy.name, reason: regimeStrategyAvoidReason(strategy.slug, primaryTrend, volatility, opportunity) });
      return;
    }

    const raw = strategy.signal(market);
    const confidence = clampNumber(raw.confidence, 0, 1);
    const contractSignal = confidenceContractSignal(raw.signal);
    const direction = confidenceSignalDirection(contractSignal);
    const effectiveWeight = roundNumber(Math.max(0, strategy.baseWeight * confidenceSystemWeightMultiplier(strategy, contractSignal, market)), 4);
    selectedStrategies.push({
      strategy: strategy.slug,
      signal: contractSignal,
      confidence,
      base_weight: strategy.baseWeight,
      effective_weight: effectiveWeight,
      direction,
      reason: raw.reason,
      key: strategy.key,
      name: strategy.name,
      contribution: roundNumber(direction * effectiveWeight * confidence, 4),
      selected: true,
      selectorReason: regimeStrategySelectorReason(strategy.slug, primaryTrend, volatility, opportunity),
    });
  });

  const activeStrategies = selectedStrategies.filter((strategy) => strategy.confidence > 0);
  const aggregation = aggregateRegimeStrategyScores(selectedStrategies);
  const buyScore = aggregation.scores.buy;
  const sellScore = aggregation.scores.sell;
  const holdScore = aggregation.scores.hold;
  const secondBestScore = Math.max(sellScore, holdScore);
  const scoreEdge = roundNumber(buyScore - secondBestScore, 4);
  const conditionConfidence = confirmation.condition.confidence;
  const normalizedNetScore = roundNumber(buyScore - sellScore, 4);
  const tradeBlockers = regimeTradeBlockers(aggregation.finalSignal, aggregation.scores, scoreEdge, conditionConfidence, opportunity, confirmation.held);
  const tradeAllowed = tradeBlockers.length === 0;
  const signal: RegimeDecisionSignal =
    opportunity === "No-trade"
      ? "No-trade"
      : tradeAllowed && aggregation.finalSignal === "buy"
        ? "Buy"
        : aggregation.finalSignal === "sell"
          ? "Sell"
          : "Hold";

  return {
    signal,
    rawCondition: rawCondition.key,
    confirmedCondition: confirmation.condition.key,
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
    secondBestScore,
    scoreEdge,
    normalizedNetScore,
    tradeAllowed,
    tradeBlockers,
    activeStrategyCount: activeStrategies.length,
    selectedStrategyCount: selectedStrategies.length,
    features: features.display,
    selectedStrategies,
    skippedStrategies,
    reasons: [
      `${primaryTrend} + ${volatility} + ${opportunity}`,
      confirmation.held
        ? `Raw condition ${rawCondition.key} is waiting for 2-of-3 confirmation or 65% confidence before switching`
        : `Condition confirmed: ${confirmation.confirmationCount}/3 recent candles match or confidence is strong`,
      `${selectedStrategies.length} strategies selected before voting; aggregate winner ${aggregation.finalSignal}`,
      `Normalized scores buy ${buyScore.toFixed(2)}, sell ${sellScore.toFixed(2)}, hold ${holdScore.toFixed(2)}; edge ${scoreEdge.toFixed(2)}`,
    ],
    noTradeReasons,
  };
}

function emptyRegimeSelectionResult(reason: string): RegimeSelectionResult {
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
    secondBestScore: 0,
    scoreEdge: 1,
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

function regimeSelectionFeatures(market: ConfidenceMarket) {
  const ema20Series = exponentialMovingAverageSeries(market.closes, 20);
  const ema20 = lastDefined(ema20Series);
  const ema50 = lastDefined(exponentialMovingAverageSeries(market.closes, 50));
  const structure = marketStructureContext(market.candles, market.vwap);
  const priorDay = priorDayRangeForRegimeSelection(market.latest.timestamp);
  const vwapDistance = (market.latest.close - market.vwap) / Math.max(market.vwap, 0.01);
  const recentRange = recentRangeForRegimeSelection(market.candles);
  const openingBreakUp = market.latest.close > market.openingRange.high;
  const openingBreakDown = market.latest.close < market.openingRange.low;
  const priorDayBreakUp = priorDay ? market.latest.close > priorDay.high : false;
  const priorDayBreakDown = priorDay ? market.latest.close < priorDay.low : false;
  const ema20Slope = emaSlopeFromSeries(ema20Series, market.latest.close);
  const higherHighAndHigherLow = Boolean(structure?.higherHigh && structure.higherLow);
  const lowerHighAndLowerLow = Boolean(structure?.lowerHigh && structure.lowerLow);
  const macdHistogram = market.macd?.histogram ?? null;
  const atrPercent = market.atr.atr1m !== null ? market.atr.atr1m / Math.max(market.latest.close, 0.01) : market.atr.atrPercent;
  const atrHistory = oneMinuteAtrPercentHistoryForRegimeSelection(market);
  const atrPercentile = atrHistory.length && atrPercent ? percentileRank(atrHistory, atrPercent) : null;
  const atrExpanding = atrHistory.length >= 6 && atrPercent > meanNumber(atrHistory.slice(-6));
  const bearishRejectionCandle = isBearishRejectionCandle(market.latest);
  const bullishRejectionCandle = isBullishRejectionCandle(market.latest);
  const recentVwapCrosses = recentLevelCrosses(market.candles.map((candle) => candle.close), market.vwap, 14);
  const priceChoppingAroundVwap = recentVwapCrosses >= 4 && Math.abs(vwapDistance) < 0.0015;
  const bullScore =
    (market.latest.close > market.vwap ? 1 : 0) +
    (ema20 !== null && market.latest.close > ema20 ? 1 : 0) +
    (ema20 !== null && ema50 !== null && ema20 > ema50 ? 1 : 0) +
    (ema20Slope > 0 ? 1 : 0) +
    (higherHighAndHigherLow ? 1 : 0) +
    (macdHistogram !== null && macdHistogram > 0 ? 1 : 0);
  const bearScore =
    (market.latest.close < market.vwap ? 1 : 0) +
    (ema20 !== null && market.latest.close < ema20 ? 1 : 0) +
    (ema20 !== null && ema50 !== null && ema20 < ema50 ? 1 : 0) +
    (ema20Slope < 0 ? 1 : 0) +
    (lowerHighAndLowerLow ? 1 : 0) +
    (macdHistogram !== null && macdHistogram < 0 ? 1 : 0);
  return {
    ema20,
    ema50,
    ema20Slope,
    vwap: market.vwap,
    adx: market.adx?.adx ?? null,
    atr: market.atr.atr1m,
    atrPercent,
    atrPercentile,
    atrExpanding,
    rsi: market.rsi,
    macdHistogram,
    bullScore,
    bearScore,
    volumeRatio: market.volume.relativeVolume,
    structure,
    higherHigh: Boolean(structure?.higherHigh),
    higherLow: Boolean(structure?.higherLow),
    lowerHigh: Boolean(structure?.lowerHigh),
    lowerLow: Boolean(structure?.lowerLow),
    higherHighAndHigherLow,
    lowerHighAndLowerLow,
    openingRangeHigh: market.openingRange.high,
    openingRangeLow: market.openingRange.low,
    recentRangeHigh: recentRange.high,
    recentRangeLow: recentRange.low,
    priorDayHigh: priorDay?.high ?? null,
    priorDayLow: priorDay?.low ?? null,
    distanceFromVwap: vwapDistance,
    openingBreakUp,
    openingBreakDown,
    priorDayBreakUp,
    priorDayBreakDown,
    bearishRejectionCandle,
    bullishRejectionCandle,
    priceChoppingAroundVwap,
    spreadTooWide: market.spreadLiquidity.spreadTooWide,
    volumeTooLow: market.spreadLiquidity.volumeTooLow || market.volume.relativeVolume < 0.55,
    display: [
      regimeFeature("Bull / Bear score", `${bullScore} / ${bearScore}`, Math.abs(bullScore - bearScore) <= 1 ? "warn" : "ok"),
      regimeFeature("EMA 20", ema20 === null ? "NA" : price(ema20), ema20 === null ? "na" : "ok"),
      regimeFeature("EMA 50", ema50 === null ? "NA" : price(ema50), ema50 === null ? "na" : "ok"),
      regimeFeature("EMA20 slope", formatProbability(ema20Slope), ema20Slope === 0 ? "warn" : "ok"),
      regimeFeature("VWAP", price(market.vwap), "ok"),
      regimeFeature("ADX 14", market.adx === null ? "NA" : market.adx.adx.toFixed(1), market.adx === null ? "na" : market.adx.adx >= 20 ? "ok" : "warn"),
      regimeFeature("ATR %ile", atrPercentile === null ? "NA" : formatProbability(atrPercentile), atrPercentile === null ? "na" : atrPercentile > 0.7 || atrPercentile < 0.3 ? "warn" : "ok"),
      regimeFeature("ATR expanding", atrExpanding ? "Yes" : "No", atrExpanding ? "ok" : "warn"),
      regimeFeature("RSI 14", market.rsi === null ? "NA" : market.rsi.toFixed(1), market.rsi === null ? "na" : market.rsi <= 30 || market.rsi >= 70 ? "warn" : "ok"),
      regimeFeature("MACD histogram", macdHistogram === null ? "NA" : macdHistogram.toFixed(3), macdHistogram === null ? "na" : macdHistogram === 0 ? "warn" : "ok"),
      regimeFeature("Volume ratio", `${market.volume.relativeVolume.toFixed(2)}x`, market.volume.relativeVolume < 0.75 ? "warn" : "ok"),
      regimeFeature("HH+HL / LH+LL", `${higherHighAndHigherLow ? "HH+HL" : "--"} / ${lowerHighAndLowerLow ? "LH+LL" : "--"}`, structure ? "ok" : "na"),
      regimeFeature("Recent range", `${price(recentRange.low)} - ${price(recentRange.high)}`, "ok"),
      regimeFeature("Prior day H/L", priorDay ? `${price(priorDay.low)} - ${price(priorDay.high)}` : "NA", priorDay ? "ok" : "na"),
      regimeFeature("Distance from VWAP", formatProbability(vwapDistance), Math.abs(vwapDistance) >= 0.004 ? "warn" : "ok"),
    ],
  };
}

function regimeFeature(name: string, value: string, status: RegimeSelectionFeature["status"]): RegimeSelectionFeature {
  return { name, value, status };
}

function rawRegimeConditionFromMarket(market: ConfidenceMarket) {
  const features = regimeSelectionFeatures(market);
  const noTradeReasons = regimeNoTradeReasons(market, features);
  const primaryTrend = classifyRegimePrimaryTrend(market, features);
  const volatility = classifyRegimeVolatility(features);
  const opportunity = classifyRegimeOpportunity(market, features, primaryTrend, noTradeReasons);
  const confidence = regimeMarketConditionConfidence(features, primaryTrend, volatility, opportunity, noTradeReasons);
  return {
    features,
    noTradeReasons,
    primaryTrend,
    volatility,
    opportunity,
    confidence,
    key: regimeConditionKey(primaryTrend, volatility, opportunity),
    contextKey: regimeConditionContextKey(market),
  };
}

function confirmedRegimeCondition(market: ConfidenceMarket, raw: ReturnType<typeof rawRegimeConditionFromMarket>) {
  if (lastConfirmedRegimeCondition?.contextKey !== raw.contextKey) {
    lastConfirmedRegimeCondition = null;
  }

  const confirmationCount = recentRegimeConditionKeys(market).filter((key) => key === raw.key).length;
  const previous = lastConfirmedRegimeCondition;
  const isSwitch = Boolean(previous && previous.key !== raw.key);
  const switchConfirmed = !previous || !isSwitch || confirmationCount >= 2 || raw.confidence >= 0.65;

  if (switchConfirmed) {
    lastConfirmedRegimeCondition = {
      primaryTrend: raw.primaryTrend,
      volatility: raw.volatility,
      opportunity: raw.opportunity,
      confidence: raw.confidence,
      key: raw.key,
      contextKey: raw.contextKey,
    };
    return { condition: lastConfirmedRegimeCondition, confirmationCount, held: false };
  }

  return { condition: previous!, confirmationCount, held: true };
}

function recentRegimeConditionKeys(market: ConfidenceMarket) {
  const keys: string[] = [];
  const start = Math.max(0, market.candles.length - 3);
  for (let index = start; index < market.candles.length; index += 1) {
    const prefix = market.candles.slice(0, index + 1);
    const snapshot = confidenceMarketSnapshotFromCandles(prefix, state.candles);
    if (!snapshot) {
      continue;
    }
    const condition = rawRegimeConditionFromMarket(snapshot);
    keys.push(condition.key);
  }
  return keys;
}

function regimeConditionKey(primaryTrend: RegimePrimaryTrend, volatility: RegimeVolatilityState, opportunity: RegimeOpportunityState) {
  return `${primaryTrend} + ${volatility} + ${opportunity}`;
}

function regimeConditionContextKey(market: ConfidenceMarket) {
  return `${market.latest.symbol}|${easternDateString(market.latest.timestamp)}`;
}

function aggregateRegimeStrategyScores(strategyOutputs: RegimeSelectedStrategy[]) {
  const scores: RegimeSelectionScores = { buy: 0, sell: 0, hold: 0 };
  strategyOutputs.forEach((output) => {
    const signal = output.signal;
    const confidence = clampNumber(output.confidence, 0, 1);
    const weight = Number.isFinite(output.effective_weight) && output.effective_weight > 0 ? output.effective_weight : 1;
    scores[signal] += confidence * weight;
  });

  const total = scores.buy + scores.sell + scores.hold;
  if (total > 0) {
    scores.buy = roundNumber(scores.buy / total, 4);
    scores.sell = roundNumber(scores.sell / total, 4);
    scores.hold = roundNumber(scores.hold / total, 4);
  }

  const finalSignal = (Object.entries(scores).sort((left, right) => right[1] - left[1])[0]?.[0] ?? "hold") as ConfidenceStrategySignal;
  return { finalSignal, scores };
}

function regimeMarketConditionConfidence(
  features: ReturnType<typeof regimeSelectionFeatures>,
  primaryTrend: RegimePrimaryTrend,
  volatility: RegimeVolatilityState,
  opportunity: RegimeOpportunityState,
  noTradeReasons: string[],
) {
  if (opportunity === "No-trade") {
    return roundNumber(clampNumber(0.35 - noTradeReasons.length * 0.08, 0, 0.6), 4);
  }
  const trendScore = Math.max(features.bullScore, features.bearScore) / 6;
  const trendClarity = Math.abs(features.bullScore - features.bearScore) / 6;
  const adxConfidence = clampNumber((features.adx ?? 0) / 30, 0, 1);
  const volatilityConfidence =
    features.atrPercentile === null
      ? 0.5
      : volatility === "Normal volatility"
        ? 0.65
        : Math.max(features.atrPercentile, 1 - features.atrPercentile);
  const opportunityBoost =
    opportunity === "Bullish breakout" || opportunity === "Bearish breakout"
      ? features.atrExpanding && features.volumeRatio > 1.2
        ? 0.15
        : 0
      : opportunity === "Bullish reversal risk" || opportunity === "Bearish reversal risk"
        ? 0.1
        : 0.05;
  const trendPenalty = primaryTrend === "Sideways / range-bound" && opportunity === "Trend continuation" ? -0.15 : 0;
  return roundNumber(clampNumber(0.2 + trendScore * 0.25 + trendClarity * 0.25 + adxConfidence * 0.15 + volatilityConfidence * 0.15 + opportunityBoost + trendPenalty, 0, 1), 4);
}

function regimeTradeBlockers(
  finalSignal: ConfidenceStrategySignal,
  scores: RegimeSelectionScores,
  scoreEdge: number,
  conditionConfidence: number,
  opportunity: RegimeOpportunityState,
  conditionHeld: boolean,
) {
  const blockers: string[] = [];
  if (conditionHeld) {
    blockers.push("Market condition switch is not confirmed yet");
  }
  if (finalSignal !== "buy") {
    blockers.push("Final aggregate signal is not Buy");
  }
  if (scores.buy < 0.6) {
    blockers.push(`Buy score ${formatProbability(scores.buy)} is below 60%`);
  }
  if (scoreEdge < 0.2) {
    blockers.push(`Buy edge ${formatProbability(scoreEdge)} is below 20%`);
  }
  if (conditionConfidence < 0.65) {
    blockers.push(`Market condition confidence ${formatProbability(conditionConfidence)} is below 65%`);
  }
  if (opportunity === "No-trade") {
    blockers.push("Opportunity state is No-trade");
  }
  return blockers;
}

function classifyRegimePrimaryTrend(market: ConfidenceMarket, features: ReturnType<typeof regimeSelectionFeatures>): RegimePrimaryTrend {
  const adx = features.adx ?? 0;
  if (features.bullScore >= 5 && adx >= 20) {
    return "Strong uptrend";
  }
  if (features.bullScore >= 3) {
    return "Weak uptrend";
  }
  if (features.bearScore >= 5 && adx >= 20) {
    return "Strong downtrend";
  }
  if (features.bearScore >= 3) {
    return "Weak downtrend";
  }
  return "Sideways / range-bound";
}

function classifyRegimeVolatility(features: ReturnType<typeof regimeSelectionFeatures>): RegimeVolatilityState {
  if (features.atrPercentile !== null && features.atrPercentile > 0.7) {
    return "High volatility";
  }
  if (features.atrPercentile !== null && features.atrPercentile < 0.3) {
    return "Low volatility";
  }
  return "Normal volatility";
}

function classifyRegimeOpportunity(
  market: ConfidenceMarket,
  features: ReturnType<typeof regimeSelectionFeatures>,
  primaryTrend: RegimePrimaryTrend,
  noTradeReasons: string[],
): RegimeOpportunityState {
  if (noTradeReasons.length > 0) {
    return "No-trade";
  }
  const volumeConfirms = features.volumeRatio > 1.2;
  if (market.latest.close > features.recentRangeHigh && volumeConfirms && features.atrExpanding) {
    return "Bullish breakout";
  }
  if (market.latest.close < features.recentRangeLow && volumeConfirms && features.atrExpanding) {
    return "Bearish breakout";
  }
  if (
    features.rsi !== null &&
    features.rsi > 70 &&
    features.distanceFromVwap > 0.004 &&
    features.bearishRejectionCandle
  ) {
    return "Bearish reversal risk";
  }
  if (
    features.rsi !== null &&
    features.rsi < 30 &&
    features.distanceFromVwap < -0.004 &&
    features.bullishRejectionCandle
  ) {
    return "Bullish reversal risk";
  }
  if (primaryTrend === "Sideways / range-bound") {
    return "Mean reversion";
  }
  return "Trend continuation";
}

function regimeNoTradeReasons(market: ConfidenceMarket, features: ReturnType<typeof regimeSelectionFeatures>) {
  const reasons: string[] = [];
  if (market.candles.length < 50) {
    reasons.push("Need at least 50 regular-session candles for EMA 50 and regime quality");
  }
  if (features.spreadTooWide) {
    reasons.push("Spread is too wide");
  }
  if (features.volumeTooLow) {
    reasons.push("Volume is too light versus the recent average");
  }
  if (Math.abs(features.bullScore - features.bearScore) <= 1 && Math.max(features.bullScore, features.bearScore) >= 2) {
    reasons.push(`Bull and bear scores are close (${features.bullScore}/${features.bearScore})`);
  }
  if (features.priceChoppingAroundVwap) {
    reasons.push("Price is chopping around VWAP");
  }
  if (market.timeOfDay.minutes >= 15 * 60 + 45) {
    reasons.push("New entries are late in the session");
  }
  return reasons;
}

function regimeSelectedStrategySlugs(
  primaryTrend: RegimePrimaryTrend,
  volatility: RegimeVolatilityState,
  opportunity: RegimeOpportunityState,
) {
  const slugs = new Set<string>();
  if (opportunity === "No-trade") {
    return ["cash_avoid_filter"];
  }

  const activate = (...names: string[]) => names.forEach((slug) => slugs.add(slug));
  const avoid = (...names: string[]) => names.forEach((slug) => slugs.delete(slug));

  switch (primaryTrend) {
    case "Strong uptrend":
      activate(
        "moving_average_trend",
        "trend_pullback",
        "opening_range_breakout",
        "intraday_breakout",
        "vwap_trend_continuation",
        "macd_momentum",
        "adx_trend_strength",
        "market_structure",
        "volume_confirmation",
      );
      avoid("vwap_mean_reversion", "rsi_mean_reversion", "bollinger_band_mean_reversion", "failed_breakout_reversal", "liquidity_sweep_reversal");
      break;
    case "Weak uptrend":
      activate("trend_pullback", "vwap_mean_reversion", "rsi_mean_reversion", "vwap_position", "market_structure", "volume_confirmation");
      avoid("volatility_breakout", "intraday_breakout");
      break;
    case "Strong downtrend":
      activate(
        "moving_average_trend",
        "vwap_position",
        "vwap_trend_continuation",
        "macd_momentum",
        "adx_trend_strength",
        "market_structure",
        "volatility_breakout",
        "atr_volatility_regime",
        "volume_confirmation",
      );
      avoid("rsi_mean_reversion", "bollinger_band_mean_reversion");
      break;
    case "Weak downtrend":
      activate("vwap_position", "market_structure", "atr_volatility_regime", "volume_confirmation", "cash_avoid_filter");
      avoid("opening_range_breakout", "intraday_breakout", "vwap_trend_continuation");
      break;
    case "Sideways / range-bound":
      activate("vwap_mean_reversion", "rsi_mean_reversion", "bollinger_band_mean_reversion", "failed_breakout_reversal", "liquidity_sweep_reversal", "market_structure");
      avoid("moving_average_trend", "trend_pullback", "vwap_trend_continuation", "macd_momentum", "adx_trend_strength");
      break;
  }

  if (opportunity === "Bullish breakout" || opportunity === "Bearish breakout") {
    activate("opening_range_breakout", "volatility_breakout", "intraday_breakout", "vwap_trend_continuation", "macd_momentum", "volume_confirmation");
    avoid("vwap_mean_reversion", "rsi_mean_reversion", "bollinger_band_mean_reversion", "failed_breakout_reversal", "liquidity_sweep_reversal");
  }
  if (opportunity === "Bullish reversal risk" || opportunity === "Bearish reversal risk") {
    activate("rsi_mean_reversion", "vwap_mean_reversion", "bollinger_band_mean_reversion", "failed_breakout_reversal", "liquidity_sweep_reversal", "cash_avoid_filter");
    avoid("moving_average_trend", "trend_pullback", "vwap_trend_continuation", "opening_range_breakout", "intraday_breakout", "volatility_breakout", "macd_momentum");
  }
  if (opportunity === "Mean reversion") {
    activate("vwap_mean_reversion", "rsi_mean_reversion", "bollinger_band_mean_reversion", "failed_breakout_reversal", "market_structure");
    avoid("moving_average_trend", "vwap_trend_continuation", "macd_momentum");
  }
  if (volatility === "Low volatility") {
    if (opportunity !== "Bullish breakout" && opportunity !== "Bearish breakout") {
      activate("vwap_mean_reversion", "rsi_mean_reversion", "bollinger_band_mean_reversion");
    }
    avoid("volatility_breakout", "intraday_breakout");
  }
  if (volatility === "High volatility" && opportunity !== "Bullish reversal risk" && opportunity !== "Bearish reversal risk") {
    activate("volatility_breakout", "atr_volatility_regime");
  }

  return Array.from(slugs);
}

function regimeStrategySelectorReason(
  slug: string,
  primaryTrend: RegimePrimaryTrend,
  volatility: RegimeVolatilityState,
  opportunity: RegimeOpportunityState,
) {
  if (slug.includes("breakout") || slug === "opening_range_breakout") {
    return opportunity === "Bullish breakout" || opportunity === "Bearish breakout"
      ? `Selected for ${opportunity}`
      : `Selected as a range-edge check in ${opportunity}`;
  }
  if (slug.includes("reversion") || slug.includes("rsi") || slug.includes("bollinger")) {
    return opportunity === "Bullish reversal risk" || opportunity === "Bearish reversal risk" || opportunity === "Mean reversion"
      ? `Selected for ${opportunity}`
      : `Selected as a pullback risk check`;
  }
  if (slug.includes("volatility") || slug.includes("atr")) {
    return `Selected for ${volatility}`;
  }
  return `Selected for ${primaryTrend}`;
}

function regimeStrategyAvoidReason(
  slug: string,
  primaryTrend: RegimePrimaryTrend,
  volatility: RegimeVolatilityState,
  opportunity: RegimeOpportunityState,
) {
  const trendFollowing = ["moving_average_trend", "trend_pullback", "vwap_trend_continuation", "macd_momentum", "adx_trend_strength"].includes(slug);
  const meanReversion = ["vwap_mean_reversion", "rsi_mean_reversion", "bollinger_band_mean_reversion", "failed_breakout_reversal", "liquidity_sweep_reversal"].includes(slug);
  const breakout = ["opening_range_breakout", "intraday_breakout", "volatility_breakout"].includes(slug);

  if (opportunity === "No-trade") {
    return "Avoided because No-trade activates only Cash / Avoid Trading";
  }
  if ((opportunity === "Bullish breakout" || opportunity === "Bearish breakout") && meanReversion) {
    return "Avoided: mean reversion against active breakout";
  }
  if ((opportunity === "Bullish reversal risk" || opportunity === "Bearish reversal risk") && (trendFollowing || breakout)) {
    return "Avoided: reversal/exhaustion risk blocks chasing trend late";
  }
  if (primaryTrend === "Strong uptrend" && meanReversion) {
    return "Avoided: short mean reversion in strong uptrend";
  }
  if (primaryTrend === "Strong downtrend" && meanReversion) {
    return "Avoided: dip-buying behavior in strong downtrend";
  }
  if (primaryTrend === "Weak uptrend" && breakout) {
    return "Avoided: aggressive breakout in weak uptrend";
  }
  if (primaryTrend === "Weak downtrend" && breakout) {
    return "Avoided: strong long breakout in weak downtrend";
  }
  if (primaryTrend === "Sideways / range-bound" && trendFollowing) {
    return "Avoided: trend following in sideways market";
  }
  if (volatility === "Low volatility" && breakout) {
    return "Avoided: wide breakout system in low volatility";
  }
  if (volatility === "High volatility" && slug === "cash_avoid_filter") {
    return "Avoided: high volatility favors ATR/volatility logic before cash filter unless other blockers exist";
  }
  return `Avoided: not suitable for ${primaryTrend}, ${volatility}, ${opportunity}`;
}

function priorDayRangeForRegimeSelection(latestTimestamp: string) {
  const latestDay = easternDateString(latestTimestamp);
  const priorCandles = state.candles
    .filter((candle) => isRegularSession(candle.timestamp) && easternDateString(candle.timestamp) < latestDay)
    .sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime());
  const priorDay = priorCandles.at(-1) ? easternDateString(priorCandles.at(-1)!.timestamp) : "";
  const dayCandles = priorCandles.filter((candle) => easternDateString(candle.timestamp) === priorDay);
  if (!dayCandles.length) {
    return null;
  }
  return {
    high: Math.max(...dayCandles.map((candle) => candle.high)),
    low: Math.min(...dayCandles.map((candle) => candle.low)),
  };
}

function recentRangeForRegimeSelection(candles: Candle[]) {
  const sample = candles.slice(-21, -1);
  const rangeCandles = sample.length ? sample : candles.slice(-20);
  return {
    high: Math.max(...rangeCandles.map((candle) => candle.high)),
    low: Math.min(...rangeCandles.map((candle) => candle.low)),
  };
}

function emaSlopeFromSeries(values: Array<number | null>, latestPrice: number) {
  const defined = values.filter((value): value is number => value !== null);
  if (defined.length < 6) {
    return 0;
  }
  const current = defined.at(-1)!;
  const previous = defined.at(-6)!;
  return (current - previous) / Math.max(latestPrice, 0.01);
}

function oneMinuteAtrPercentHistoryForRegimeSelection(market: ConfidenceMarket) {
  const latestTime = new Date(market.latest.timestamp).getTime();
  const source = (state.weightedMarketData.timeframeCandles["1Min"]?.length ? state.weightedMarketData.timeframeCandles["1Min"]! : state.candles)
    .filter((candle) => isRegularSession(candle.timestamp) && new Date(candle.timestamp).getTime() <= latestTime)
    .sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime());
  const historySource = source.length >= 80 ? source.slice(-1600) : market.candles;
  const values: number[] = [];
  for (let end = 15; end <= historySource.length; end += 1) {
    const sample = historySource.slice(0, end);
    const atr = averageTrueRange(sample, 14);
    const close = sample.at(-1)?.close ?? 0;
    if (atr !== null && close > 0) {
      values.push(atr / close);
    }
  }
  return values;
}

function percentileRank(values: number[], current: number) {
  const finiteValues = values.filter((value) => Number.isFinite(value)).sort((left, right) => left - right);
  if (!finiteValues.length) {
    return null;
  }
  return finiteValues.filter((value) => value <= current).length / finiteValues.length;
}

function meanNumber(values: number[]) {
  return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
}

function recentLevelCrosses(values: number[], level: number, lookback: number) {
  const sample = values.slice(-lookback);
  if (sample.length < 2 || !level) {
    return 0;
  }
  const signs = sample.map((value) => value >= level);
  return signs.slice(1).reduce((count, sign, index) => count + (sign !== signs[index] ? 1 : 0), 0);
}

function isBearishRejectionCandle(candle: Candle) {
  const range = Math.max(candle.high - candle.low, 0.01);
  const body = Math.abs(candle.close - candle.open);
  const upperWick = candle.high - Math.max(candle.open, candle.close);
  const closeBelowMidpoint = candle.close < candle.low + range * 0.45;
  return upperWick >= Math.max(body * 1.2, range * 0.35) && closeBelowMidpoint;
}

function isBullishRejectionCandle(candle: Candle) {
  const range = Math.max(candle.high - candle.low, 0.01);
  const body = Math.abs(candle.close - candle.open);
  const lowerWick = Math.min(candle.open, candle.close) - candle.low;
  const closeAboveMidpoint = candle.close > candle.low + range * 0.55;
  return lowerWick >= Math.max(body * 1.2, range * 0.35) && closeAboveMidpoint;
}

function lastDefined(values: Array<number | null>) {
  for (let index = values.length - 1; index >= 0; index -= 1) {
    if (values[index] !== null) {
      return values[index]!;
    }
  }
  return null;
}

function regimeSignalClass(signal: RegimeDecisionSignal) {
  return signal === "No-trade" ? "no-trade" : signal.toLowerCase();
}

function renderRegimeScoreGrid(result: RegimeSelectionResult) {
  return [
    ["Buy", result.scores.buy.toFixed(2), "buy"],
    ["Sell", result.scores.sell.toFixed(2), "sell"],
    ["Hold", result.scores.hold.toFixed(2), "hold"],
    ["Buy Edge", result.scoreEdge.toFixed(2), result.scoreEdge >= 0.2 ? "buy" : "no-trade"],
    ["Condition", formatProbability(result.confidence), result.confidence >= 0.65 ? "buy" : "no-trade"],
  ]
    .map(
      ([label, value, kind]) => `
        <div class="weighted-score-card ${kind}">
          <span>${label}</span>
          <strong>${value}</strong>
        </div>
      `,
    )
    .join("");
}

function renderRegimeSummary(result: RegimeSelectionResult) {
  const adapted = regimeSelectionAsConfidenceResult(result);
  const sizingBlocker = adapted.sizing.blockedReason ? `Sizing: ${adapted.sizing.blockedReason}` : "";
  const quantityBlockers = uniqueStrings([...result.tradeBlockers, sizingBlocker].filter(Boolean));
  const noTrade = result.noTradeReasons.length
    ? renderRegimeSummaryRow("No-trade checks", escapeHtml(result.noTradeReasons.join("; ")), "warn")
    : "";
  const blockers = quantityBlockers.length
    ? renderRegimeSummaryRow("Quantity gate", `0 - ${escapeHtml(quantityBlockers.join("; "))}`, "block")
    : renderRegimeSummaryRow("Quantity gate", `${adapted.positionSize} shares - Buy score, edge, condition confidence, opportunity, and sizing passed.`, "pass");
  const confirmation = result.conditionHeld
    ? `Raw ${escapeHtml(result.rawCondition)} has ${result.confirmationCount}/3 confirmation, keeping ${escapeHtml(result.confirmedCondition)}.`
    : `${result.confirmationCount}/3 recent candles support ${escapeHtml(result.confirmedCondition)}, or confidence is >= 65%.`;
  return `
    <div class="regime-summary-grid">
      ${renderRegimeSummaryRow("Condition", `${escapeHtml(result.primaryTrend)} + ${escapeHtml(result.volatility)} + ${escapeHtml(result.opportunity)}`, "info")}
      ${renderRegimeSummaryRow(result.conditionHeld ? "Condition hold" : "Confirmation", confirmation, result.conditionHeld ? "pass" : "info")}
      ${renderRegimeSummaryRow("Aggregate winner", `${escapeHtml(result.aggregateSignal)} (${result.activeStrategyCount} active strategy outputs)`, "info")}
      ${renderRegimeSummaryRow(
        "Trade decision",
        `${escapeHtml(result.signal)}; target qty ${adapted.positionSize}; buy ${formatProbability(result.scores.buy)}, second best ${formatProbability(result.secondBestScore)}, edge ${formatProbability(result.scoreEdge)}`,
        quantityBlockers.length || result.signal === "No-trade" ? "block" : result.signal === "Hold" ? "warn" : "pass",
      )}
      ${blockers}
      ${renderRegimeSummaryRow("Selector", "Strategies are filtered before voting; skipped strategies cannot affect this result.", "neutral")}
      ${noTrade}
    </div>
  `;
}

function renderRegimeSummaryRow(label: string, value: string, tone: "info" | "pass" | "warn" | "block" | "neutral") {
  return `
    <div class="regime-summary-row" data-tone="${tone}">
      <b>${escapeHtml(label)}</b>
      <span>${value}</span>
    </div>
  `;
}

function renderRegimeFeatureGrid(features: RegimeSelectionFeature[]) {
  return features
    .map(
      (feature) => `
        <div class="regime-feature" data-status="${feature.status}">
          <span>${escapeHtml(feature.name)}</span>
          <strong>${escapeHtml(feature.value)}</strong>
        </div>
      `,
    )
    .join("");
}

function renderRegimeStrategyList(result: RegimeSelectionResult) {
  const selected = result.selectedStrategies.length
    ? result.selectedStrategies.map(renderRegimeStrategyRow).join("")
    : `<article class="algo-vote-card" data-signal="hold" data-eligible="false"><div><strong>No selected strategy</strong><span>${escapeHtml(result.noTradeReasons[0] ?? "Waiting for a clean market condition")}</span></div><b class="algo-signal-badge">Excluded</b></article>`;
  const skipped = result.skippedStrategies.slice(0, 8);
  return `
    ${selected}
    <div class="regime-skipped">
      <strong>Skipped</strong>
      ${skipped.map((strategy) => `<span>${escapeHtml(strategy.name)} - ${escapeHtml(strategy.reason)}</span>`).join("")}
    </div>
  `;
}

function renderRegimeStrategyRow(strategy: RegimeSelectedStrategy) {
  return `
    <article class="algo-vote-card" data-signal="${strategy.signal}" data-eligible="true">
      <div>
        <strong>${escapeHtml(strategy.name)}</strong>
        <span>${escapeHtml(strategy.selectorReason)}</span>
        <span>${escapeHtml(strategy.reason)}; confidence ${formatProbability(strategy.confidence)}, weight ${strategy.effective_weight.toFixed(2)}</span>
      </div>
      <b class="algo-signal-badge">${escapeHtml(strategy.signal === "buy" ? "Buy" : strategy.signal === "sell" ? "Sell" : "Hold")}</b>
    </article>
  `;
}

function updateConfidenceAggregationPanel() {
  const result = calculateConfidenceAggregation();
  confidenceFinalSignal.textContent = result.decisionLabel;
  confidenceFinalSignal.className = `algo-final ${result.signal.toLowerCase()}`;
  confidenceScoreGrid.innerHTML = renderConfidenceScoreGrid(result);
  confidenceSummary.innerHTML = renderConfidenceSummary(result);
  if (!isEditingWithin(confidenceTradingSettingsMount)) {
    confidenceTradingSettingsMount.innerHTML = renderConfidenceTradingSettingsPanel(result);
  }
  renderConfidenceBacktestState();
  confidenceRequirementsToggleMeta.textContent = `${result.activeStrategyCount}/${state.confidenceDecisionSettings.minimumActiveStrategies} active · ${formatProbability(state.confidenceDecisionSettings.minimumDirectionalAgreement)} agree · ${formatProbability(state.confidenceDecisionSettings.minimumAverageConfidence)} avg`;
  confidenceRequirementsPanel.innerHTML = renderConfidenceRequirementsPanel(result);
  renderConfidenceRequirementsExpandedState();
  confidenceStrategiesToggleMeta.textContent = `${result.strategies.filter((strategy) => strategy.signal !== "hold").length} active / ${result.strategies.length} strategies`;
  confidenceStrategiesList.innerHTML = renderConfidenceStrategies(result.strategies);
  renderConfidenceStrategiesExpandedState();
  maybeAutoSubmitConfidenceTargetOrder();
}

function calculateConfidenceAggregation(): ConfidenceAggregationResult {
  return calculateConfidenceAggregationFromMarket(confidenceMarketSnapshot());
}

function calculateConfidenceAggregationFromMarket(
  market: ConfidenceMarket | null,
  options: {
    hardFilters?: (market: ConfidenceMarket, rawSignal: AlgoSignal) => ConfidenceAggregationResult["hardFilters"];
    sizing?: (market: ConfidenceMarket, signal: AlgoSignal, normalizedNetScore: number) => ConfidencePositionSizing;
  } = {},
): ConfidenceAggregationResult {
  if (!market) {
    return {
      signal: "Hold",
      decisionLabel: "Hold",
      buyScore: 0,
      sellScore: 0,
      netScore: 0,
      activeWeight: 0,
      normalizedNetScore: 0,
      activeStrategyCount: 0,
      buyWeight: 0,
      sellWeight: 0,
      buyAgreement: 0,
      sellAgreement: 0,
      buyAverageConfidence: 0,
      sellAverageConfidence: 0,
      buyThreshold: state.confidenceDecisionSettings.buyThreshold,
      sellThreshold: state.confidenceDecisionSettings.sellThreshold,
      strongBuyThreshold: state.confidenceDecisionSettings.strongBuyThreshold,
      strongSellThreshold: state.confidenceDecisionSettings.strongSellThreshold,
      strategies: confidenceAggregationStrategies.map((strategy) => ({
        strategy: strategy.slug,
        signal: "hold",
        confidence: 0,
        base_weight: strategy.baseWeight,
        effective_weight: strategy.baseWeight,
        direction: 0,
        reason: "Waiting for session candles",
        key: strategy.key,
        name: strategy.name,
        contribution: 0,
      })),
      stopDistance: 0,
      positionSizeMultiplier: 0,
      positionSize: 0,
      sizing: confidenceEmptyPositionSizing("Waiting for session candles"),
      hardFilters: [{ label: "Market data", status: "info", detail: "Waiting for session candles" }],
      logs: ["Waiting for current regular-session candles before running WCA."],
      detail: "Need current regular-session candles before aggregating confidence.",
    };
  }

  const strategies = confidenceAggregationStrategies.map((strategy) => {
    const raw = strategy.signal(market);
    const confidence = clampNumber(raw.confidence, 0, 1);
    const contractSignal = confidenceContractSignal(raw.signal);
    const direction = confidenceSignalDirection(contractSignal);
    const weightMultiplier = confidenceSystemWeightMultiplier(strategy, contractSignal, market);
    const effectiveWeight = roundNumber(Math.max(0, strategy.baseWeight * weightMultiplier), 4);
    const signedContribution = direction * effectiveWeight * confidence;
    return {
      strategy: strategy.slug,
      signal: contractSignal,
      confidence,
      base_weight: strategy.baseWeight,
      effective_weight: effectiveWeight,
      direction,
      reason: raw.reason,
      key: strategy.key,
      name: strategy.name,
      contribution: roundNumber(signedContribution, 4),
    };
  });
  const buyScore = roundNumber(
    strategies.filter((strategy) => strategy.direction === 1).reduce((sum, strategy) => sum + strategy.effective_weight * strategy.confidence, 0),
    4,
  );
  const sellScore = roundNumber(
    strategies.filter((strategy) => strategy.direction === -1).reduce((sum, strategy) => sum + strategy.effective_weight * strategy.confidence, 0),
    4,
  );
  const activeWeight = roundNumber(strategies.filter((strategy) => strategy.direction !== 0).reduce((sum, strategy) => sum + strategy.effective_weight, 0), 4);
  const activeStrategyCount = strategies.filter((strategy) => strategy.direction !== 0).length;
  const buyWeight = roundNumber(strategies.filter((strategy) => strategy.direction === 1).reduce((sum, strategy) => sum + strategy.effective_weight, 0), 4);
  const sellWeight = roundNumber(strategies.filter((strategy) => strategy.direction === -1).reduce((sum, strategy) => sum + strategy.effective_weight, 0), 4);
  const netScore = roundNumber(buyScore - sellScore, 4);
  const normalizedNetScore = activeWeight ? roundNumber(netScore / activeWeight, 4) : 0;
  const buyAgreement = activeWeight ? roundNumber(buyWeight / activeWeight, 4) : 0;
  const sellAgreement = activeWeight ? roundNumber(sellWeight / activeWeight, 4) : 0;
  const buyAverageConfidence = buyWeight ? roundNumber(buyScore / buyWeight, 4) : 0;
  const sellAverageConfidence = sellWeight ? roundNumber(sellScore / sellWeight, 4) : 0;
  const settings = state.confidenceDecisionSettings;
  const enoughActiveStrategies = activeStrategyCount >= settings.minimumActiveStrategies;
  const buyRequirementsMet =
    enoughActiveStrategies && buyAgreement >= settings.minimumDirectionalAgreement && buyAverageConfidence >= settings.minimumAverageConfidence;
  const sellRequirementsMet =
    enoughActiveStrategies && sellAgreement >= settings.minimumDirectionalAgreement && sellAverageConfidence >= settings.minimumAverageConfidence;
  const rawDecisionLabel: ConfidenceDecisionLabel =
    normalizedNetScore >= settings.strongBuyThreshold && buyRequirementsMet
      ? "Strong Buy"
      : normalizedNetScore >= settings.buyThreshold && buyRequirementsMet
        ? "Buy"
        : normalizedNetScore <= settings.strongSellThreshold && sellRequirementsMet
          ? "Strong Sell"
          : normalizedNetScore <= settings.sellThreshold && sellRequirementsMet
            ? "Sell"
            : "Hold";
  const rawSignal: AlgoSignal = rawDecisionLabel === "Strong Buy" || rawDecisionLabel === "Buy" ? "Buy" : rawDecisionLabel === "Strong Sell" || rawDecisionLabel === "Sell" ? "Sell" : "Hold";
  const hardFilters = (options.hardFilters ?? confidenceHardFilters)(market, rawSignal);
  const hardFilterBlocked = hardFilters.some((filter) => filter.status === "fail");
  const signal = hardFilterBlocked ? "Hold" : rawSignal;
  const decisionLabel: ConfidenceDecisionLabel = hardFilterBlocked ? "Hold" : rawDecisionLabel;
  const sizing = (options.sizing ?? confidencePositionSizing)(market, signal, normalizedNetScore);
  const positionSize = sizing.finalQuantity;
  const logs = [
    `Updated indicators: VWAP ${price(market.vwap)}, RSI ${market.rsi === null ? "NA" : market.rsi.toFixed(1)}, MACD histogram ${market.macd === null ? "NA" : market.macd.histogram.toFixed(4)}.`,
    `Modifiers: ${confidenceModifierDetail(market)}`,
    `Scores: net_score ${netScore.toFixed(4)} / active_weight ${activeWeight.toFixed(4)} = normalized_net_score ${normalizedNetScore.toFixed(4)}.`,
    `Agreement: buy ${formatProbability(buyAgreement)}, sell ${formatProbability(sellAgreement)}, required ${formatProbability(settings.minimumDirectionalAgreement)}.`,
    `Average confidence: buy ${formatProbability(buyAverageConfidence)}, sell ${formatProbability(sellAverageConfidence)}, required ${formatProbability(settings.minimumAverageConfidence)}.`,
    `Active strategies: ${activeStrategyCount}/${settings.minimumActiveStrategies}. Thresholds: strong buy >= ${settings.strongBuyThreshold.toFixed(2)}, buy >= ${settings.buyThreshold.toFixed(2)}, sell <= ${settings.sellThreshold.toFixed(2)}, strong sell <= ${settings.strongSellThreshold.toFixed(2)}.`,
    `Raw decision ${rawDecisionLabel}; hard filters ${hardFilterBlocked ? "blocked" : "passed"}; final decision ${decisionLabel}; position size ${positionSize} shares.`,
  ];
  return {
    signal,
    decisionLabel,
    buyScore,
    sellScore,
    netScore,
    activeWeight,
    normalizedNetScore,
    activeStrategyCount,
    buyWeight,
    sellWeight,
    buyAgreement,
    sellAgreement,
    buyAverageConfidence,
    sellAverageConfidence,
    buyThreshold: settings.buyThreshold,
    sellThreshold: settings.sellThreshold,
    strongBuyThreshold: settings.strongBuyThreshold,
    strongSellThreshold: settings.strongSellThreshold,
    strategies,
    stopDistance: market.atr.stopDistance,
    positionSizeMultiplier: sizing.sizeMultiplier,
    positionSize,
    sizing,
    hardFilters,
    logs,
    detail: activeWeight
      ? `Decision follows paper-trading thresholds, minimum active strategy count, agreement, average confidence, and hard filters. ${confidenceModifierDetail(market)}`
      : `No active Buy/Sell strategy weight. ${confidenceModifierDetail(market)}`,
  };
}

function confidenceMarketSnapshot(): ConfidenceMarket | null {
  const sessionCandles = latestWeightedCalculationCandles();
  const candles = sessionCandles.length ? sessionCandles : state.candles.slice(-240);
  const latest = candles.at(-1);
  if (!latest || candles.length < 5) {
    return null;
  }
  const closes = candles.map((candle) => candle.close);
  const vwap = sessionVwapValue(candles);
  const previousVwap = candles.length > 1 ? sessionVwapValue(candles.slice(0, -1)) : vwap;
  const priorRange = candles.slice(-21, -1);
  const fiveMinuteCandles = state.weightedMarketData.timeframeCandles["5Min"]?.length
    ? latestRegularSessionCandlesFrom(state.weightedMarketData.timeframeCandles["5Min"]!)
    : aggregateCandlesToFiveMinute(candles);
  const sourceCandles = state.weightedMarketData.timeframeCandles["1Min"]?.length
    ? state.weightedMarketData.timeframeCandles["1Min"]!
    : state.candles;
  const premarketCandles = sourceCandles.filter(
    (candle) => easternDateString(candle.timestamp) === easternDateString(latest.timestamp) && !isRegularSession(candle.timestamp) && easternMinutes(candle.timestamp) < 570,
  );
  const latestDay = easternDateString(latest.timestamp);
  const priorSessionClose = sourceCandles
    .filter((candle) => easternDateString(candle.timestamp) < latestDay)
    .sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime())
    .at(-1);
  const openingRange = openingRangeValues(candles, Math.min(15, candles.length));
  const priorHigh = priorRange.length ? Math.max(...priorRange.map((candle) => candle.high)) : latest.high;
  const priorLow = priorRange.length ? Math.min(...priorRange.map((candle) => candle.low)) : latest.low;
  const averageVolume = simpleMovingAverage(candles.map((candle) => candle.volume), Math.min(20, candles.length)) ?? latest.volume;
  const volume = confidenceVolumeContext(latest, averageVolume, vwap, openingRange, priorHigh, priorLow);

  return {
    candles,
    closes,
    latest,
    priorClose: priorSessionClose?.close ?? null,
    dayOpen: candles[0].open,
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
    atr: confidenceAtrContext(candles, fiveMinuteCandles, latest.close),
    bands: bollingerBands(closes, 20, 2),
    adx: averageDirectionalIndex(candles, Math.min(14, candles.length - 1)),
    volume,
    spreadLiquidity: confidenceSpreadLiquidityContext(latest, volume),
    timeOfDay: confidenceTimeOfDayContext(latest.timestamp),
  };
}

function confidenceMarketSnapshotFromCandles(candles: Candle[], allCandles: Candle[] = candles): ConfidenceMarket | null {
  const sorted = candles.slice().sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime());
  const regular = sorted.filter((candle) => isRegularSession(candle.timestamp));
  const latest = regular.at(-1);
  if (!latest) {
    return null;
  }
  const latestDay = easternDateString(latest.timestamp);
  const sessionCandles = regular.filter((candle) => easternDateString(candle.timestamp) === latestDay);
  if (sessionCandles.length < 5) {
    return null;
  }

  const allSorted = allCandles.slice().sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime());
  const allBeforeSession = allSorted.filter((candle) => new Date(candle.timestamp).getTime() < new Date(sessionCandles[0].timestamp).getTime());
  const priorClose = allBeforeSession.filter((candle) => isRegularSession(candle.timestamp)).at(-1)?.close ?? null;
  const premarketCandles = allSorted.filter(
    (candle) => easternDateString(candle.timestamp) === latestDay && isPremarketSession(candle.timestamp),
  );
  const closes = sessionCandles.map((candle) => candle.close);
  const vwap = sessionVwapValue(sessionCandles);
  const previousVwap = sessionCandles.length > 1 ? sessionVwapValue(sessionCandles.slice(0, -1)) : vwap;
  const priorRange = sessionCandles.slice(-21, -1);
  const fiveMinuteCandles = aggregateCandlesToFiveMinute(sessionCandles);
  const openingRange = openingRangeValues(sessionCandles, Math.min(15, sessionCandles.length));
  const priorHigh = priorRange.length ? Math.max(...priorRange.map((candle) => candle.high)) : latest.high;
  const priorLow = priorRange.length ? Math.min(...priorRange.map((candle) => candle.low)) : latest.low;
  const averageVolume = simpleMovingAverage(sessionCandles.map((candle) => candle.volume), Math.min(20, sessionCandles.length)) ?? latest.volume;
  const volume = confidenceVolumeContext(latest, averageVolume, vwap, openingRange, priorHigh, priorLow);

  return {
    candles: sessionCandles,
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
    atr: confidenceAtrContext(sessionCandles, fiveMinuteCandles, latest.close),
    bands: bollingerBands(closes, 20, 2),
    adx: averageDirectionalIndex(sessionCandles, Math.min(14, sessionCandles.length - 1)),
    volume,
    spreadLiquidity: confidenceSpreadLiquidityContext(latest, volume),
    timeOfDay: confidenceTimeOfDayContext(latest.timestamp),
  };
}

function confidenceVote(signal: AlgoSignal, confidence: number, reason: string): ConfidenceStrategyRawSignal {
  return { signal, confidence, reason };
}

function confidenceContractSignal(signal: AlgoSignal): ConfidenceStrategySignal {
  return signal === "Buy" ? "buy" : signal === "Sell" ? "sell" : "hold";
}

function confidenceSignalDirection(signal: ConfidenceStrategySignal): ConfidenceStrategyDirection {
  return signal === "buy" ? 1 : signal === "sell" ? -1 : 0;
}

function confidenceSystemWeightMultiplier(strategy: ConfidenceStrategy, signal: ConfidenceStrategySignal, market: ConfidenceMarket) {
  if (signal === "hold") {
    return 1;
  }
  return roundNumber(
    confidenceAdxWeightMultiplier(strategy, signal, market) *
      confidenceAtrWeightMultiplier(strategy, market) *
      confidenceVolumeWeightMultiplier(strategy, market) *
      confidenceTimeOfDayWeightMultiplier(strategy, market),
    4,
  );
}

function confidenceDecisionThreshold(market: ConfidenceMarket) {
  if (market.atr.regime === "extreme") {
    return 0.65;
  }
  if (market.atr.regime === "high") {
    return 0.56;
  }
  if (market.atr.regime === "too_low") {
    return 0.58;
  }
  if (market.adx?.regime === "range") {
    return roundNumber(0.45 + market.atr.thresholdAdd, 2);
  }
  if (market.adx?.regime === "very_strong_bullish_trend" || market.adx?.regime === "very_strong_bearish_trend") {
    return roundNumber(0.55 + market.atr.thresholdAdd, 2);
  }
  return roundNumber(0.5 + market.atr.thresholdAdd, 2);
}

function confidenceAdxWeightMultiplier(strategy: ConfidenceStrategy, signal: ConfidenceStrategySignal, market: ConfidenceMarket) {
  if (market.adx === null) {
    return 1;
  }
  const family = confidenceStrategyFamily(strategy.slug);
  const strongTrend = market.adx.regime === "bullish_trend" || market.adx.regime === "bearish_trend";
  const veryStrongTrend = market.adx.regime === "very_strong_bullish_trend" || market.adx.regime === "very_strong_bearish_trend";
  const trendDirection = market.adx.regime.includes("bullish") ? 1 : market.adx.regime.includes("bearish") ? -1 : 0;
  const signalDirection = confidenceSignalDirection(signal);
  const alignedWithTrend = trendDirection !== 0 && signalDirection === trendDirection;
  const againstTrend = trendDirection !== 0 && signalDirection === -trendDirection;

  if (market.adx.regime === "range") {
    return family === "mean_reversion" ? 1.25 : family === "vwap" ? 1.1 : family === "trend" || family === "breakout" ? 0.7 : 1;
  }
  if (market.adx.regime === "mixed") {
    return 1;
  }
  if (strongTrend || veryStrongTrend) {
    if (family === "trend") {
      return veryStrongTrend ? (againstTrend ? 0.5 : alignedWithTrend ? 1.35 : 1.15) : againstTrend ? 0.65 : alignedWithTrend ? 1.25 : 1.1;
    }
    if (family === "breakout") {
      return veryStrongTrend ? (againstTrend ? 0.55 : alignedWithTrend ? 1.25 : 1.1) : againstTrend ? 0.7 : alignedWithTrend ? 1.2 : 1.05;
    }
    if (family === "mean_reversion") {
      return veryStrongTrend ? (againstTrend ? 0.5 : 0.65) : againstTrend ? 0.65 : 0.8;
    }
    if (family === "vwap") {
      return veryStrongTrend ? 1.2 : 1.15;
    }
  }
  return 1;
}

function confidenceStrategyFamily(slug: string): "trend" | "breakout" | "mean_reversion" | "vwap" | "filter" {
  if (["moving_average_trend", "trend_pullback", "macd_momentum", "market_structure", "vwap_trend_continuation"].includes(slug)) {
    return "trend";
  }
  if (["opening_range_breakout", "intraday_breakout", "volatility_breakout"].includes(slug)) {
    return "breakout";
  }
  if (["rsi_mean_reversion", "bollinger_band_mean_reversion", "vwap_mean_reversion", "failed_breakout_reversal", "liquidity_sweep_reversal"].includes(slug)) {
    return "mean_reversion";
  }
  if (["vwap_position", "gap_continuation_fade"].includes(slug)) {
    return "vwap";
  }
  return "filter";
}

function confidenceAtrWeightMultiplier(strategy: ConfidenceStrategy, market: ConfidenceMarket) {
  const family = confidenceStrategyFamily(strategy.slug);
  if (market.atr.regime === "too_low") {
    return family === "breakout" || family === "trend" ? 0.65 : family === "mean_reversion" ? 0.95 : 0.85;
  }
  if (market.atr.regime === "high") {
    return family === "breakout" || ["failed_breakout_reversal", "liquidity_sweep_reversal"].includes(strategy.slug) ? 1.05 : 0.85;
  }
  if (market.atr.regime === "extreme") {
    return 0.35;
  }
  return 1;
}

function confidenceVolumeWeightMultiplier(strategy: ConfidenceStrategy, market: ConfidenceMarket) {
  const family = confidenceStrategyFamily(strategy.slug);
  if (family === "breakout" && (market.volume.weakVolume || market.volume.smallCandle)) {
    return 0.55;
  }
  if ((family === "breakout" || family === "trend") && market.volume.volumeSpike && !market.volume.smallCandle) {
    return 1.08;
  }
  return 1;
}

function confidenceTimeOfDayWeightMultiplier(strategy: ConfidenceStrategy, market: ConfidenceMarket) {
  const family = confidenceStrategyFamily(strategy.slug);
  const base = market.timeOfDay.weightMultiplier;
  if (market.timeOfDay.label === "Opening drive" && (family === "breakout" || strategy.slug === "gap_continuation_fade")) {
    return roundNumber(base * 1.08, 4);
  }
  if (market.timeOfDay.label === "Midday" && (family === "breakout" || family === "trend")) {
    return roundNumber(base * 0.9, 4);
  }
  return base;
}

function confidenceModifierDetail(market: ConfidenceMarket) {
  const adxLabel =
    market.adx === null
      ? "ADX waiting"
      : `ADX ${market.adx.adx.toFixed(1)} +DI ${market.adx.plusDi.toFixed(1)} -DI ${market.adx.minusDi.toFixed(1)} ${market.adx.regime.replaceAll("_", " ")}`;
  const atrLabel = `ATR ${market.atr.regime.replaceAll("_", " ")} ${formatProbability(market.atr.atrPercent)} relative ${market.atr.relativeAtr === null ? "NA" : `${market.atr.relativeAtr.toFixed(2)}x`}`;
  const spreadLabel = `spread ${formatProbability(market.spreadLiquidity.spreadPercent)} max ${formatProbability(market.spreadLiquidity.maxSpreadPercent)}`;
  return `${adxLabel}, ${atrLabel}, volume ${market.volume.relativeVolume.toFixed(2)}x, ${spreadLabel}, and ${market.timeOfDay.label.toLowerCase()} x${market.timeOfDay.weightMultiplier.toFixed(2)} modify effective weights. Stop ${price(market.atr.stopDistance)}, size x${market.atr.positionSizeMultiplier.toFixed(2)}.`;
}

function confidenceAtrContext(oneMinuteCandles: Candle[], fiveMinuteCandles: Candle[], latestPrice: number): ConfidenceAtrContext {
  const atr1m = averageTrueRange(oneMinuteCandles, Math.min(14, oneMinuteCandles.length - 1));
  const atr5m = averageTrueRange(fiveMinuteCandles, Math.min(14, fiveMinuteCandles.length - 1));
  const atrSeries = rollingAtrSeries(oneMinuteCandles, 14).slice(-30);
  const recentAverageAtr = atrSeries.length ? atrSeries.reduce((sum, value) => sum + value, 0) / atrSeries.length : null;
  const primaryAtr = atr1m ?? (atr5m !== null ? atr5m / 5 : 0);
  const atrPercent = latestPrice ? primaryAtr / latestPrice : 0;
  const relativeAtr = recentAverageAtr ? primaryAtr / recentAverageAtr : null;
  const regime = confidenceAtrRegime(atrPercent, relativeAtr);
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

function confidenceAtrRegime(atrPercent: number, relativeAtr: number | null): ConfidenceAtrRegime {
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

function confidenceSpreadLiquidityContext(latest: Candle, volume: ConfidenceVolumeContext): ConfidenceSpreadLiquidityContext {
  const defaults = confidenceDefaultSizingSettings();
  const settings = state.confidenceTradingSettings;
  const spreadPercent = latest.close ? (settings.slippagePerShare * 2) / latest.close : 0;
  const maxSpreadPercent = defaults.maxSpreadPercent / 100;
  const minimumOneMinuteVolume = defaults.minimumOneMinuteVolume;
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

function confidenceTimeOfDayContext(timestamp: string): ConfidenceTimeOfDayContext {
  const minutes = easternMinutes(timestamp);
  const label = sessionLabelForMinutes(minutes);
  const beforeFirstFive = minutes < 9 * 60 + 35;
  const afterCutoff = minutes >= 15 * 60 + 30;
  const weightMultiplier =
    beforeFirstFive ? 0.75 : label === "Opening drive" ? 1.05 : label === "Midday" ? 0.85 : label === "Closing window" ? 0.9 : 1;
  return {
    minutes,
    label,
    weightMultiplier,
    newTradesAllowed: !beforeFirstFive && !afterCutoff,
  };
}

function confidenceVolumeContext(
  latest: Candle,
  averageVolume: number,
  vwap: number,
  openingRange: { high: number; low: number },
  priorHigh: number,
  priorLow: number,
): ConfidenceVolumeContext {
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

function confidenceMovingAverageTrend(market: ConfidenceMarket) {
  if (market.sma20 === null || market.sma50 === null) {
    return confidenceVote("Hold", 0, "Waiting for 50 candles");
  }
  const spread = Math.abs(market.sma20 - market.sma50) / market.latest.close;
  const confidence = Math.min(0.95, 0.45 + spread * 80);
  if (market.sma20 > market.sma50 && market.latest.close > market.sma20) {
    return confidenceVote("Buy", confidence, `20 SMA ${price(market.sma20)} above 50 SMA ${price(market.sma50)}`);
  }
  if (market.sma20 < market.sma50 && market.latest.close < market.sma20) {
    return confidenceVote("Sell", confidence, `20 SMA ${price(market.sma20)} below 50 SMA ${price(market.sma50)}`);
  }
  return confidenceVote("Hold", 0.2, "Moving averages are mixed");
}

function confidenceVwapPosition(market: ConfidenceMarket) {
  const distance = (market.latest.close - market.vwap) / Math.max(market.vwap, 0.01);
  const recent = market.candles.slice(-5);
  const lastThree = market.candles.slice(-3);
  const closesAbove = lastThree.filter((candle) => candle.close > market.vwap).length;
  const closesBelow = lastThree.filter((candle) => candle.close < market.vwap).length;
  const volumeSupportsBuy = market.latest.volume > market.averageVolume * 1.1 && market.latest.close >= market.latest.open;
  const volumeSupportsSell = market.latest.volume > market.averageVolume * 1.1 && market.latest.close <= market.latest.open;
  const pullbackHeldVwap = recent.some((candle) => candle.low <= market.vwap * 1.001 && candle.close > market.vwap);
  const rejectedVwap = recent.some((candle) => candle.high >= market.vwap * 0.999 && candle.close < market.vwap);

  let buyConfidence = 0;
  const buyReasons: string[] = [];
  if (market.latest.close > market.vwap) {
    buyConfidence += 0.25;
    buyReasons.push("price above VWAP");
  }
  if (market.vwapSlope > 0.00005) {
    buyConfidence += 0.2;
    buyReasons.push("VWAP slope positive");
  }
  if (closesAbove === 3) {
    buyConfidence += 0.2;
    buyReasons.push("last 3 closes above VWAP");
  }
  if (pullbackHeldVwap) {
    buyConfidence += 0.2;
    buyReasons.push("pullback held VWAP");
  }
  if (volumeSupportsBuy) {
    buyConfidence += 0.15;
    buyReasons.push("volume supports move");
  }

  let sellConfidence = 0;
  const sellReasons: string[] = [];
  if (market.latest.close < market.vwap) {
    sellConfidence += 0.25;
    sellReasons.push("price below VWAP");
  }
  if (market.vwapSlope < -0.00005) {
    sellConfidence += 0.2;
    sellReasons.push("VWAP slope negative");
  }
  if (closesBelow === 3) {
    sellConfidence += 0.2;
    sellReasons.push("last 3 closes below VWAP");
  }
  if (rejectedVwap) {
    sellConfidence += 0.2;
    sellReasons.push("retest rejected VWAP");
  }
  if (volumeSupportsSell) {
    sellConfidence += 0.15;
    sellReasons.push("volume supports move");
  }

  const distanceText = `distance ${formatProbability(Math.abs(distance))}`;
  if (buyConfidence >= 0.45 && buyConfidence > sellConfidence) {
    return confidenceVote("Buy", Math.min(1, buyConfidence), `${buyReasons.join(", ")}; ${distanceText}`);
  }
  if (sellConfidence >= 0.45 && sellConfidence > buyConfidence) {
    return confidenceVote("Sell", Math.min(1, sellConfidence), `${sellReasons.join(", ")}; ${distanceText}`);
  }
  return confidenceVote("Hold", Math.max(0.2, Math.min(0.4, Math.max(buyConfidence, sellConfidence))), `VWAP acceptance is mixed; ${distanceText}`);
}

function confidenceTrendPullback(market: ConfidenceMarket) {
  if (market.sma20 === null || market.sma50 === null) {
    return confidenceVote("Hold", 0, "Waiting for trend moving averages");
  }
  const nearSma20 = Math.abs(market.latest.close - market.sma20) / market.latest.close < 0.0035;
  const trendUp = market.sma20 > market.sma50 && market.latest.close > market.vwap;
  const trendDown = market.sma20 < market.sma50 && market.latest.close < market.vwap;
  if (trendUp && nearSma20 && market.latest.close > market.openingRange.high) {
    return confidenceVote("Buy", 0.68, "Uptrend pullback is holding near 20 SMA");
  }
  if (trendDown && nearSma20 && market.latest.close < market.openingRange.low) {
    return confidenceVote("Sell", 0.68, "Downtrend pullback is rejecting near 20 SMA");
  }
  return confidenceVote("Hold", 0.2, "No clean trend pullback");
}

function confidenceVwapTrendContinuation(market: ConfidenceMarket) {
  if (market.sma20 === null || market.sma50 === null) {
    return confidenceVote("Hold", 0, "Waiting for VWAP trend history");
  }
  const trendUp = market.sma20 > market.sma50 && market.latest.close > market.vwap;
  const trendDown = market.sma20 < market.sma50 && market.latest.close < market.vwap;
  if (trendUp && market.latest.close > market.openingRange.high) {
    return confidenceVote("Buy", 0.68, "VWAP, moving averages, and opening range agree upward");
  }
  if (trendDown && market.latest.close < market.openingRange.low) {
    return confidenceVote("Sell", 0.68, "VWAP, moving averages, and opening range agree downward");
  }
  return confidenceVote("Hold", 0.18, "VWAP trend continuation is not confirmed");
}

function confidenceVwapMeanReversion(market: ConfidenceMarket) {
  const distance = (market.latest.close - market.vwap) / Math.max(market.vwap, 0.01);
  const choppy = market.adx !== null ? market.adx.regime === "range" || market.adx.regime === "mixed" : Math.abs(market.vwapSlope) < 0.0002;
  if (choppy && distance < -0.003) {
    return confidenceVote("Buy", Math.min(0.78, 0.52 + Math.abs(distance) * 35), "Price is stretched below VWAP in a weak-trend tape");
  }
  if (choppy && distance > 0.003) {
    return confidenceVote("Sell", Math.min(0.78, 0.52 + Math.abs(distance) * 35), "Price is stretched above VWAP in a weak-trend tape");
  }
  return confidenceVote("Hold", 0.16, "VWAP mean-reversion setup is not active");
}

function confidenceRsiMeanReversion(market: ConfidenceMarket) {
  if (market.rsi === null) {
    return confidenceVote("Hold", 0, "Waiting for RSI history");
  }
  if (market.rsi <= 30) {
    return confidenceVote("Buy", Math.min(0.9, 0.5 + (30 - market.rsi) / 35), `RSI ${market.rsi.toFixed(1)} is oversold`);
  }
  if (market.rsi >= 70) {
    return confidenceVote("Sell", Math.min(0.9, 0.5 + (market.rsi - 70) / 35), `RSI ${market.rsi.toFixed(1)} is overbought`);
  }
  return confidenceVote("Hold", 0.15, `RSI ${market.rsi.toFixed(1)} is neutral`);
}

function confidenceBollingerMeanReversion(market: ConfidenceMarket) {
  if (!market.bands) {
    return confidenceVote("Hold", 0, "Waiting for Bollinger history");
  }
  const width = Math.max(market.bands.upper - market.bands.lower, 0.01);
  if (market.latest.close < market.bands.lower) {
    return confidenceVote("Buy", Math.min(0.9, 0.52 + ((market.bands.lower - market.latest.close) / width) * 2), "Price is stretched below lower Bollinger band");
  }
  if (market.latest.close > market.bands.upper) {
    return confidenceVote("Sell", Math.min(0.9, 0.52 + ((market.latest.close - market.bands.upper) / width) * 2), "Price is stretched above upper Bollinger band");
  }
  return confidenceVote("Hold", 0.12, "Price is inside Bollinger bands");
}

function confidenceOpeningRangeBreakout(market: ConfidenceMarket) {
  const volumeExpansion = market.latest.volume > market.averageVolume * 1.15;
  if (market.latest.close > market.openingRange.high && volumeExpansion) {
    return confidenceVote("Buy", 0.72, `Close broke opening high ${price(market.openingRange.high)} with volume`);
  }
  if (market.latest.close < market.openingRange.low && volumeExpansion) {
    return confidenceVote("Sell", 0.72, `Close broke opening low ${price(market.openingRange.low)} with volume`);
  }
  return confidenceVote("Hold", 0.18, "Opening range has not broken with volume");
}

function confidenceIntradayBreakout(market: ConfidenceMarket) {
  if (market.candles.length < 21) {
    return confidenceVote("Hold", 0, "Waiting for 21 candles");
  }
  if (market.latest.close > market.priorHigh) {
    return confidenceVote("Buy", 0.62, `Close broke prior high ${price(market.priorHigh)}`);
  }
  if (market.latest.close < market.priorLow) {
    return confidenceVote("Sell", 0.62, `Close broke prior low ${price(market.priorLow)}`);
  }
  return confidenceVote("Hold", 0.1, "Price remains inside recent range");
}

function confidenceFailedBreakoutReversal(market: ConfidenceMarket) {
  const failedHigh = market.latest.high > market.priorHigh && market.latest.close < market.priorHigh;
  const failedLow = market.latest.low < market.priorLow && market.latest.close > market.priorLow;
  if (failedHigh) {
    return confidenceVote("Sell", 0.7, "Prior high breakout failed back below range");
  }
  if (failedLow) {
    return confidenceVote("Buy", 0.7, "Prior low breakdown failed back above range");
  }
  return confidenceVote("Hold", 0.14, "No failed breakout reversal");
}

function confidenceLiquiditySweepReversal(market: ConfidenceMarket) {
  const volumeExpansion = market.latest.volume > market.averageVolume * 1.15;
  const failedHigh = market.latest.high > market.priorHigh && market.latest.close < market.priorHigh;
  const failedLow = market.latest.low < market.priorLow && market.latest.close > market.priorLow;
  if (volumeExpansion && failedHigh) {
    return confidenceVote("Sell", 0.72, "High-side liquidity sweep failed with expanded volume");
  }
  if (volumeExpansion && failedLow) {
    return confidenceVote("Buy", 0.72, "Low-side liquidity sweep failed with expanded volume");
  }
  return confidenceVote("Hold", 0.14, "No volume-backed liquidity sweep reversal");
}

function confidenceMacdMomentum(market: ConfidenceMarket) {
  if (!market.macd) {
    return confidenceVote("Hold", 0, "Waiting for MACD history");
  }
  const confidence = Math.min(0.86, 0.45 + Math.abs(market.macd.histogram) / Math.max(market.latest.close * 0.001, 0.01));
  if (market.macd.macd > market.macd.signal && market.macd.histogram > 0) {
    return confidenceVote("Buy", confidence, `MACD histogram ${market.macd.histogram.toFixed(3)} is positive`);
  }
  if (market.macd.macd < market.macd.signal && market.macd.histogram < 0) {
    return confidenceVote("Sell", confidence, `MACD histogram ${market.macd.histogram.toFixed(3)} is negative`);
  }
  return confidenceVote("Hold", 0.12, "MACD is flat or crossing");
}

function confidenceAdxTrendStrength(market: ConfidenceMarket) {
  if (market.adx === null || market.sma20 === null || market.sma50 === null) {
    return confidenceVote("Hold", 0, "Waiting for ADX trend history");
  }
  if (market.adx.adx < 20) {
    return confidenceVote("Hold", Math.min(0.45, market.adx.adx / 50), `ADX ${market.adx.adx.toFixed(1)} is too weak for trend`);
  }
  const confidence = Math.min(0.9, 0.45 + (market.adx.adx - 20) / 45);
  if (market.sma20 > market.sma50 && market.latest.close > market.vwap) {
    return confidenceVote("Buy", confidence, `ADX ${market.adx.adx.toFixed(1)} allows bullish trend`);
  }
  if (market.sma20 < market.sma50 && market.latest.close < market.vwap) {
    return confidenceVote("Sell", confidence, `ADX ${market.adx.adx.toFixed(1)} allows bearish trend`);
  }
  return confidenceVote("Hold", 0.2, `ADX ${market.adx.adx.toFixed(1)} lacks directional alignment`);
}

function confidenceVolumeConfirmation(market: ConfidenceMarket) {
  const volume = market.volume;
  if (volume.weakVolume || volume.smallCandle) {
    return confidenceVote("Hold", 0.25, `Weak participation: volume ${volume.relativeVolume.toFixed(2)}x, range ${formatProbability(volume.rangePercent)}`);
  }

  let buyConfidence = 0;
  const buyReasons: string[] = [];
  if (volume.bullishCandle) {
    buyConfidence += 0.25;
    buyReasons.push("bullish candle");
  }
  if (volume.relativeVolume >= 1) {
    buyConfidence += Math.min(0.25, 0.12 + (volume.relativeVolume - 1) * 0.12);
    buyReasons.push(`${volume.relativeVolume.toFixed(2)}x volume`);
  }
  if (volume.breaksResistance || volume.holdsKeyLevel) {
    buyConfidence += 0.25;
    buyReasons.push(volume.breaksResistance ? "breaks key resistance" : "holds key level");
  }
  if (volume.spreadAcceptable) {
    buyConfidence += 0.15;
    buyReasons.push("range/spread acceptable");
  }
  if (volume.volumeSpike) {
    buyConfidence += 0.1;
    buyReasons.push("volume spike");
  }

  let sellConfidence = 0;
  const sellReasons: string[] = [];
  if (volume.bearishCandle) {
    sellConfidence += 0.25;
    sellReasons.push("bearish candle");
  }
  if (volume.relativeVolume >= 1) {
    sellConfidence += Math.min(0.25, 0.12 + (volume.relativeVolume - 1) * 0.12);
    sellReasons.push(`${volume.relativeVolume.toFixed(2)}x volume`);
  }
  if (volume.breaksSupport || volume.rejectsResistance) {
    sellConfidence += 0.25;
    sellReasons.push(volume.breaksSupport ? "breaks support" : "rejects resistance");
  }
  if (volume.spreadAcceptable) {
    sellConfidence += 0.15;
    sellReasons.push("range/spread acceptable");
  }
  if (volume.volumeSpike) {
    sellConfidence += 0.1;
    sellReasons.push("volume spike");
  }

  if (buyConfidence >= 0.45 && buyConfidence > sellConfidence) {
    return confidenceVote("Buy", Math.min(1, buyConfidence), buyReasons.join(", "));
  }
  if (sellConfidence >= 0.45 && sellConfidence > buyConfidence) {
    return confidenceVote("Sell", Math.min(1, sellConfidence), sellReasons.join(", "));
  }
  return confidenceVote("Hold", Math.max(0.2, Math.min(0.4, Math.max(buyConfidence, sellConfidence))), `Volume participation is mixed at ${volume.relativeVolume.toFixed(2)}x`);
}

function confidenceAtrVolatilityRegime(market: ConfidenceMarket) {
  if (market.sma20 === null || market.sma50 === null) {
    return confidenceVote("Hold", 0, "Waiting for ATR regime history");
  }
  const atrPercent = market.atr.atrPercent;
  if (market.atr.regime === "too_low" || market.atr.regime === "extreme") {
    return confidenceVote("Hold", 0.25, `ATR regime ${market.atr.regime.replaceAll("_", " ")} is not tradable`);
  }
  const confidence = Math.min(0.78, 0.45 + atrPercent * 35);
  if (market.sma20 > market.sma50 && market.latest.close > market.vwap) {
    return confidenceVote("Buy", confidence, `ATR regime ${formatProbability(atrPercent)} supports trend sizing`);
  }
  if (market.sma20 < market.sma50 && market.latest.close < market.vwap) {
    return confidenceVote("Sell", confidence, `ATR regime ${formatProbability(atrPercent)} supports trend sizing`);
  }
  return confidenceVote("Hold", 0.18, `ATR regime ${formatProbability(atrPercent)} has no directional edge`);
}

function confidenceMarketStructure(market: ConfidenceMarket) {
  const structure = marketStructureContext(market.candles, market.vwap);
  if (!structure) {
    return confidenceVote("Hold", 0, "Waiting for swing structure");
  }

  let buyConfidence = 0;
  const buyReasons: string[] = [];
  if (structure.higherHigh) {
    buyConfidence += 0.25;
    buyReasons.push("higher high");
  }
  if (structure.higherLow) {
    buyConfidence += 0.25;
    buyReasons.push("higher low");
  }
  if (market.latest.close > market.vwap) {
    buyConfidence += 0.2;
    buyReasons.push("price above VWAP");
  }
  if (structure.successfulSupportRetest || structure.breakRetestSucceeded) {
    buyConfidence += 0.15;
    buyReasons.push(structure.breakRetestSucceeded ? "break/retest succeeded" : "pullback held support");
  }
  if (market.latest.close > market.latest.open) {
    buyConfidence += 0.15;
    buyReasons.push("bullish candle confirmation");
  }

  let sellConfidence = 0;
  const sellReasons: string[] = [];
  if (structure.lowerLow) {
    sellConfidence += 0.25;
    sellReasons.push("lower low");
  }
  if (structure.lowerHigh) {
    sellConfidence += 0.25;
    sellReasons.push("lower high");
  }
  if (market.latest.close < market.vwap) {
    sellConfidence += 0.2;
    sellReasons.push("price below VWAP");
  }
  if (structure.failedResistanceRetest || structure.breakRetestFailed) {
    sellConfidence += 0.15;
    sellReasons.push(structure.breakRetestFailed ? "break/retest failed" : "rally failed at resistance");
  }
  if (market.latest.close < market.latest.open) {
    sellConfidence += 0.15;
    sellReasons.push("bearish candle confirmation");
  }

  if (buyConfidence >= 0.45 && buyConfidence > sellConfidence) {
    return confidenceVote("Buy", Math.min(1, buyConfidence), `${buyReasons.join(", ")}; ${structure.summary}`);
  }
  if (sellConfidence >= 0.45 && sellConfidence > buyConfidence) {
    return confidenceVote("Sell", Math.min(1, sellConfidence), `${sellReasons.join(", ")}; ${structure.summary}`);
  }
  return confidenceVote("Hold", Math.max(0.15, Math.min(0.4, Math.max(buyConfidence, sellConfidence))), `Mixed structure; ${structure.summary}`);
}

function confidenceVolatilityBreakout(market: ConfidenceMarket) {
  if (!market.latest.close) {
    return confidenceVote("Hold", 0, "Waiting for volatility breakout inputs");
  }
  const atrPercent = market.atr.atrPercent;
  const highVolatility = market.atr.regime === "high" || market.atr.regime === "extreme";
  const volumeExpansion = market.latest.volume > market.averageVolume * 1.15;
  if (highVolatility && volumeExpansion && market.latest.close > market.priorHigh) {
    return confidenceVote("Buy", Math.min(0.78, 0.56 + atrPercent * 28), "Volatility expansion broke above the prior range");
  }
  if (highVolatility && volumeExpansion && market.latest.close < market.priorLow) {
    return confidenceVote("Sell", Math.min(0.78, 0.56 + atrPercent * 28), "Volatility expansion broke below the prior range");
  }
  return confidenceVote("Hold", 0.14, "No volatility breakout confirmation");
}

function confidenceGapContinuationFade(market: ConfidenceMarket) {
  if (market.priorClose === null || !market.priorClose) {
    return confidenceVote("Hold", 0, "Waiting for prior close");
  }
  const minutes = easternMinutes(market.latest.timestamp);
  if (minutes < 570 || minutes > 660) {
    return confidenceVote("Hold", 0.1, "Gap strategy only active in the first 90 minutes");
  }
  const gap = (market.dayOpen - market.priorClose) / market.priorClose;
  if (Math.abs(gap) < 0.002) {
    return confidenceVote("Hold", 0.12, "No meaningful opening gap");
  }
  const volume = market.volume;
  const sellingVolume = volume.relativeVolume >= 1.1 && volume.bearishCandle;
  const buyingVolume = volume.relativeVolume >= 1.1 && volume.bullishCandle;
  const failedPremarketHigh =
    market.premarketHigh !== null && market.latest.high >= market.premarketHigh * 0.999 && market.latest.close < market.premarketHigh;
  const failedPremarketLow =
    market.premarketLow !== null && market.latest.low <= market.premarketLow * 1.001 && market.latest.close > market.premarketLow;

  if (gap > 0 && market.latest.close > market.vwap && market.latest.close > market.openingRange.high && buyingVolume) {
    const confidence = Math.min(0.9, 0.48 + Math.abs(gap) * 18 + Math.min(0.18, (volume.relativeVolume - 1) * 0.12));
    return confidenceVote("Buy", confidence, `Gap up ${formatProbability(gap)} continuation: above VWAP, broke opening high, volume ${volume.relativeVolume.toFixed(2)}x`);
  }
  if (gap > 0 && failedPremarketHigh && market.latest.close < market.vwap && sellingVolume) {
    const confidence = Math.min(0.88, 0.5 + Math.abs(gap) * 14 + Math.min(0.16, (volume.relativeVolume - 1) * 0.1));
    return confidenceVote("Sell", confidence, `Gap up ${formatProbability(gap)} fade: failed premarket high, below VWAP, selling volume ${volume.relativeVolume.toFixed(2)}x`);
  }
  if (gap < 0 && market.latest.close < market.vwap && market.latest.close < market.openingRange.low && sellingVolume) {
    const confidence = Math.min(0.9, 0.48 + Math.abs(gap) * 18 + Math.min(0.18, (volume.relativeVolume - 1) * 0.12));
    return confidenceVote("Sell", confidence, `Gap down ${formatProbability(Math.abs(gap))} continuation: below VWAP, broke opening low, volume ${volume.relativeVolume.toFixed(2)}x`);
  }
  if (gap < 0 && failedPremarketLow && market.latest.close > market.vwap && buyingVolume) {
    const confidence = Math.min(0.88, 0.5 + Math.abs(gap) * 14 + Math.min(0.16, (volume.relativeVolume - 1) * 0.1));
    return confidenceVote("Buy", confidence, `Gap down ${formatProbability(Math.abs(gap))} fade: failed lower, reclaimed VWAP, buying volume ${volume.relativeVolume.toFixed(2)}x`);
  }
  return confidenceVote("Hold", 0.18, `Gap ${formatProbability(Math.abs(gap))} has not confirmed continuation or fade`);
}

function confidenceCashAvoidFilter(market: ConfidenceMarket) {
  const volumeMultiple = market.averageVolume ? market.latest.volume / market.averageVolume : 1;
  if (volumeMultiple < 0.65) {
    return confidenceVote("Hold", 0.85, "Hold-only filter: volume is too light");
  }
  if (market.atr.regime === "extreme") {
    return confidenceVote("Hold", 0.85, "Hold-only filter: volatility is too high");
  }
  return confidenceVote("Hold", 0.2, "Hold-only filter: no cash condition active");
}

function confidenceHardFilters(market: ConfidenceMarket, rawSignal: AlgoSignal): ConfidenceAggregationResult["hardFilters"] {
  const defaults = confidenceDefaultSizingSettings();
  const dailyLoss = confidenceDailyLossStatus(market.latest.close);
  const tradesToday = effectiveTodaysTradeCount("confidence", defaults.maxDailyTrades, market.latest.close);
  return [
    {
      label: "Spread",
      status: market.spreadLiquidity.spreadTooWide ? "fail" : "pass",
      detail: `${formatProbability(market.spreadLiquidity.spreadPercent)} / max ${formatProbability(market.spreadLiquidity.maxSpreadPercent)}`,
    },
    {
      label: "Liquidity",
      status: market.spreadLiquidity.volumeTooLow ? "fail" : "pass",
      detail: confidenceLiquidityDetail(market),
    },
    {
      label: "ATR",
      status: market.atr.regime === "extreme" ? "fail" : market.atr.regime === "high" ? "info" : "pass",
      detail:
        market.atr.regime === "extreme"
          ? "Extreme volatility forces Hold"
          : `${market.atr.regime.replaceAll("_", " ")} volatility, size x${market.atr.positionSizeMultiplier.toFixed(2)}`,
    },
    {
      label: "Time",
      status: market.timeOfDay.newTradesAllowed ? "pass" : rawSignal === "Hold" ? "info" : "fail",
      detail: `${market.timeOfDay.label}, new trades ${market.timeOfDay.newTradesAllowed ? "allowed" : "blocked"}`,
    },
    {
      label: "Max Trades",
      status: tradesToday >= defaults.maxDailyTrades ? "fail" : "pass",
      detail: dailyTradeCountDetail("confidence", defaults.maxDailyTrades, market.latest.close),
    },
    {
      label: "Daily Loss",
      status: dailyLoss.limitReached ? "fail" : "pass",
      detail: `${currency(dailyLoss.pnl)} P&L / ${currency(-dailyLoss.limit)} limit`,
    },
  ];
}

function confidenceLiquidityDetail(market: ConfidenceMarket) {
  const volumeText = `${market.latest.volume.toLocaleString()} shares`;
  const relativeText = `${market.spreadLiquidity.relativeVolume.toFixed(2)}x relative volume`;
  const minimum = market.spreadLiquidity.minimumOneMinuteVolume;
  return minimum > 0
    ? `${volumeText}, min ${minimum.toLocaleString()}, ${relativeText}`
    : `${volumeText}, ${relativeText}, no min 1m volume block`;
}

function confidencePositionSizing(
  market: ConfidenceMarket,
  signal: AlgoSignal,
  normalizedNetScore: number,
  options: { automaticShortCycleBuy?: boolean; result?: ConfidenceAggregationResult; mode?: TradingWindowMode } = {},
): ConfidencePositionSizing {
  const settings =
    options.mode === "regime"
      ? state.regimeTradingSettings
      : options.mode === "meta"
        ? state.metaTradingSettings
        : state.confidenceTradingSettings;
  const defaults = options.mode === "regime" ? regimeDefaultSizingSettings() : confidenceDefaultSizingSettings();
  const accountEquity = settings.startingCapital;
  const priceValue = Math.max(market.latest.close, 0.01);
  const signalStrength = Math.abs(normalizedNetScore);
  const fiveMinuteCandles = aggregateCandlesToFiveMinute(market.candles);
  const latestFive = fiveMinuteCandles.at(-1);
  const priorFive = fiveMinuteCandles.at(-2);
  const fiveMinuteConfirmsBuy = Boolean(latestFive && priorFive && latestFive.close >= priorFive.close);
  const contextResult = options.result;
  const automaticContextBoost =
    options.automaticShortCycleBuy && signal !== "Hold"
      ? (normalizedNetScore >= state.confidenceDecisionSettings.buyThreshold ? 0.1 : 0) +
        (contextResult && contextResult.buyAgreement >= state.confidenceDecisionSettings.minimumDirectionalAgreement ? 0.1 : 0) +
        (contextResult && contextResult.activeStrategyCount >= state.confidenceDecisionSettings.minimumActiveStrategies ? 0.05 : 0) +
        (fiveMinuteConfirmsBuy ? 0.1 : 0)
      : 0;
  const sizeMultiplier =
    options.automaticShortCycleBuy && signal !== "Hold"
      ? clampNumber(confidenceSizeMultiplier(Math.max(0.5, Math.max(0, normalizedNetScore))) + automaticContextBoost, 0.25, 1)
      : confidenceSizeMultiplier(signalStrength);
  const currentPosition =
    options.mode === "regime"
      ? summarizePositionFromTradeHistory(priceValue, priceValue, "regime")
      : options.mode === "meta"
        ? summarizePositionFromTradeHistory(priceValue, priceValue, "meta")
      : confidencePositionSummary(priceValue);
  const maxPositionDollars = accountEquity * (defaults.maxPositionPercent / 100);
  const maxOrderDollars = accountEquity * (settings.orderAllocationPercent / 100);
  const dailyBuyingPowerDollars = accountEquity * (settings.dailyAllocationPercent / 100);
  const availableBuyingPower = Math.max(0, Math.min(maxPositionDollars, dailyBuyingPowerDollars) - currentPosition.marketValue);
  const riskDollars = accountEquity * (defaults.baseRiskPercent / 100) * sizeMultiplier;
  const primaryAtr = market.atr.atr1m ?? (market.atr.atr5m !== null ? market.atr.atr5m / 5 : 0);
  const stopDistance = defaultSizingStopDistance(defaults, priceValue, primaryAtr);
  const sharesByRisk = stopDistance > 0 ? riskDollars / stopDistance : 0;
  const sharesByOrder = maxOrderDollars / priceValue;
  const sharesByCapital = maxPositionDollars / priceValue;
  const sharesByBuyingPower = availableBuyingPower / priceValue;
  const sharesByLiquidity = defaults.maxParticipationPercent > 0 ? market.latest.volume * (defaults.maxParticipationPercent / 100) : Number.POSITIVE_INFINITY;
  const maxAllowedShares = defaults.maxAllowedShares > 0 ? defaults.maxAllowedShares : Number.POSITIVE_INFINITY;
  const sizingCaps = [
    { label: "risk budget", shares: sharesByRisk },
    { label: "order limit", shares: sharesByOrder },
    { label: "max position", shares: sharesByCapital },
    { label: "buying power", shares: sharesByBuyingPower },
    { label: "liquidity participation", shares: sharesByLiquidity },
    { label: "max shares", shares: maxAllowedShares },
  ].filter((cap) => Number.isFinite(cap.shares));
  const limitingCap = sizingCaps.reduce(
    (smallest, cap) => (cap.shares < smallest.shares ? cap : smallest),
    { label: "sizing", shares: Number.POSITIVE_INFINITY },
  );
  const finalQuantity =
    signal === "Hold" || sizeMultiplier <= 0 || stopDistance <= 0
      ? 0
      : Math.max(0, Math.floor(Math.min(sharesByRisk, sharesByOrder, sharesByCapital, sharesByBuyingPower, sharesByLiquidity, maxAllowedShares)));
  const blockedReason =
    signal === "Hold"
      ? "final signal is Hold"
      : sizeMultiplier <= 0
        ? `signal strength ${formatProbability(signalStrength)} is below 50%`
        : stopDistance <= 0
          ? "stop distance is unavailable"
          : finalQuantity < 1
            ? `${limitingCap.label} allows ${formatShareLimit(limitingCap.shares)}, below 1 share`
            : "";
  return {
    signalStrength,
    sizeMultiplier,
    riskDollars,
    stopDistance,
    sharesByRisk,
    sharesByOrder,
    sharesByCapital,
    sharesByBuyingPower,
    sharesByLiquidity,
    finalQuantity,
    availableBuyingPower,
    accountEquity,
    maxPositionDollars,
    currentPositionValue: currentPosition.marketValue,
    limitingFactor: limitingCap.label,
    blockedReason,
  };
}

function confidenceEmptyPositionSizing(blockedReason: string): ConfidencePositionSizing {
  return {
    signalStrength: 0,
    sizeMultiplier: 0,
    riskDollars: 0,
    stopDistance: 0,
    sharesByRisk: 0,
    sharesByOrder: 0,
    sharesByCapital: 0,
    sharesByBuyingPower: 0,
    sharesByLiquidity: 0,
    finalQuantity: 0,
    availableBuyingPower: 0,
    accountEquity: state.confidenceTradingSettings.startingCapital,
    maxPositionDollars: 0,
    currentPositionValue: 0,
    limitingFactor: "sizing",
    blockedReason,
  };
}

function formatShareLimit(value: number) {
  if (!Number.isFinite(value)) {
    return "unlimited shares";
  }
  return value < 10 ? `${value.toFixed(2)} shares` : `${Math.floor(value).toLocaleString()} shares`;
}

function confidenceSizeMultiplier(signalStrength: number) {
  if (signalStrength >= 0.8) {
    return 1;
  }
  if (signalStrength >= 0.7) {
    return 0.75;
  }
  if (signalStrength >= 0.6) {
    return 0.5;
  }
  if (signalStrength >= 0.5) {
    return 0.25;
  }
  return 0;
}

function renderConfidenceScoreGrid(result: ConfidenceAggregationResult) {
  return [
    ["Buy", result.buyScore, "buy"],
    ["Sell", result.sellScore, "sell"],
    ["Net", result.netScore, result.netScore >= 0 ? "buy" : "sell"],
    ["Normalized", result.normalizedNetScore, result.signal.toLowerCase()],
    ["Buy Agree", result.buyAgreement, "buy"],
    ["Sell Agree", result.sellAgreement, "sell"],
  ]
    .map(
      ([label, value, kind]) => `
        <div class="weighted-score-card ${kind}">
          <span>${escapeHtml(String(label))}</span>
          <strong>${Number(value).toFixed(2)}</strong>
        </div>
      `,
    )
    .join("");
}

function renderConfidenceSummary(result: ConfidenceAggregationResult) {
  const settings = state.confidenceDecisionSettings;
  const activeStatus = result.activeStrategyCount >= settings.minimumActiveStrategies ? "pass" : "fail";
  const agreementStatus = Math.max(result.buyAgreement, result.sellAgreement) >= settings.minimumDirectionalAgreement ? "pass" : "fail";
  const confidenceStatus = Math.max(result.buyAverageConfidence, result.sellAverageConfidence) >= settings.minimumAverageConfidence ? "pass" : "fail";
  return `
    <div class="confidence-summary-grid">
      <article data-status="${result.signal === "Hold" ? "info" : "pass"}">
        <small>Decision</small>
        <strong>${escapeHtml(result.decisionLabel)}</strong>
        <span>Signal ${escapeHtml(result.signal)}</span>
      </article>
      <article data-status="${Math.abs(result.normalizedNetScore) >= result.buyThreshold ? "pass" : "info"}">
        <small>Normalized Net</small>
        <strong>${result.normalizedNetScore.toFixed(2)}</strong>
        <span>${result.netScore.toFixed(2)} / ${result.activeWeight.toFixed(2)}</span>
      </article>
      <article data-status="${activeStatus}">
        <small>Active Strategies</small>
        <strong>${result.activeStrategyCount}/${settings.minimumActiveStrategies}</strong>
        <span>Active weight ${result.activeWeight.toFixed(2)}</span>
      </article>
      <article data-status="${agreementStatus}">
        <small>Agreement</small>
        <strong>B ${formatProbability(result.buyAgreement)} / S ${formatProbability(result.sellAgreement)}</strong>
        <span>Need ${formatProbability(settings.minimumDirectionalAgreement)}</span>
      </article>
      <article data-status="${confidenceStatus}">
        <small>Avg Confidence</small>
        <strong>B ${formatProbability(result.buyAverageConfidence)} / S ${formatProbability(result.sellAverageConfidence)}</strong>
        <span>Need ${formatProbability(settings.minimumAverageConfidence)}</span>
      </article>
      <article data-status="${result.positionSize > 0 ? "pass" : "info"}">
        <small>Position</small>
        <strong>${result.positionSize} shares</strong>
        <span>${formatProbability(result.positionSizeMultiplier)} size - stop ${price(result.sizing.stopDistance)}</span>
      </article>
    </div>
    <div class="confidence-score-breakdown">
      <span><b>Scores</b> Buy ${result.buyScore.toFixed(2)} · Sell ${result.sellScore.toFixed(2)} · Net ${result.netScore.toFixed(2)}</span>
      <span><b>Weights</b> Buy ${result.buyWeight.toFixed(2)} · Sell ${result.sellWeight.toFixed(2)} · Active ${result.activeWeight.toFixed(2)}</span>
      <span><b>Thresholds</b> Strong Buy ${result.strongBuyThreshold.toFixed(2)} · Buy ${result.buyThreshold.toFixed(2)} · Sell ${result.sellThreshold.toFixed(2)} · Strong Sell ${result.strongSellThreshold.toFixed(2)}</span>
    </div>
    <div class="confidence-filter-row">
      ${result.hardFilters.map((filter) => `<span data-status="${filter.status}"><b>${escapeHtml(filter.label)}</b>${escapeHtml(filter.detail)}</span>`).join("")}
    </div>
    <details class="confidence-log-details">
      <summary>Calculation Log</summary>
      ${result.logs.map((log) => `<span>${escapeHtml(log)}</span>`).join("")}
      <span>${escapeHtml(result.detail)}</span>
    </details>
  `;
}

function renderConfidenceRequirementsPanel(result: ConfidenceAggregationResult) {
  const settings = state.confidenceDecisionSettings;
  return `
    <div class="confidence-requirements-grid">
      ${renderConfidenceRequirementInput("strongBuyThreshold", "Strong Buy >=", settings.strongBuyThreshold, 0.01, 1, 0.01)}
      ${renderConfidenceRequirementInput("buyThreshold", "Buy >=", settings.buyThreshold, 0.01, 1, 0.01)}
      ${renderConfidenceRequirementInput("sellThreshold", "Sell <=", settings.sellThreshold, -1, -0.01, 0.01)}
      ${renderConfidenceRequirementInput("strongSellThreshold", "Strong Sell <=", settings.strongSellThreshold, -1, -0.01, 0.01)}
      ${renderConfidenceRequirementInput("minimumActiveStrategies", "Min active strategies", settings.minimumActiveStrategies, 1, confidenceAggregationStrategies.length, 1)}
      ${renderConfidenceRequirementInput("minimumDirectionalAgreement", "Min agreement", settings.minimumDirectionalAgreement, 0, 1, 0.01)}
      ${renderConfidenceRequirementInput("minimumAverageConfidence", "Min avg confidence", settings.minimumAverageConfidence, 0, 1, 0.01)}
    </div>
    <div class="confidence-requirements-status">
      <span data-status="${result.activeStrategyCount >= settings.minimumActiveStrategies ? "pass" : "fail"}">
        <b>Active</b>
        ${result.activeStrategyCount}/${settings.minimumActiveStrategies}
      </span>
      <span data-status="${Math.max(result.buyAgreement, result.sellAgreement) >= settings.minimumDirectionalAgreement ? "pass" : "fail"}">
        <b>Agreement</b>
        B ${formatProbability(result.buyAgreement)} / S ${formatProbability(result.sellAgreement)}
      </span>
      <span data-status="${Math.max(result.buyAverageConfidence, result.sellAverageConfidence) >= settings.minimumAverageConfidence ? "pass" : "fail"}">
        <b>Confidence</b>
        B ${formatProbability(result.buyAverageConfidence)} / S ${formatProbability(result.sellAverageConfidence)}
      </span>
      <span data-status="${result.signal === "Hold" ? "info" : "pass"}">
        <b>Decision</b>
        ${escapeHtml(result.decisionLabel)}
      </span>
    </div>
  `;
}

function renderRegimeTradingSettingsPanel(result: RegimeSelectionResult) {
  const adapted = regimeSelectionAsConfidenceResult(result);
  const settings = state.regimeTradingSettings;
  const expanded = state.regimeTradingSettingsExpanded;
  const targetOrder = confidenceTargetOrderRecommendation(adapted, "regime");
  state.currentRegimeTargetOrder = targetOrder;
  return `
    <div class="trading-settings-panel weighted-trading-settings-panel" data-status="ready" data-expanded="${String(expanded)}">
      <button id="regimeTradingSettingsToggle" class="trading-settings-head" type="button" aria-expanded="${String(expanded)}" aria-controls="regimeTradingSettingsBody">
        <span class="trading-settings-title">
          <b>${expanded ? "-" : "+"}</b>
          <strong>Strategy Settings</strong>
        </span>
        <span class="trading-settings-summary">${escapeHtml(regimeTradingSettingsSummary(result, adapted))}</span>
      </button>
      <div id="regimeTradingSettingsBody" class="trading-settings-body" ${expanded ? "" : "hidden"}>
        <div class="trading-settings-grid">
          ${renderRegimeTradingSettingInput("startingCapital", "Total balance", settings.startingCapital, 1000, 10000000, 100)}
          ${renderRegimeTradingSettingInput("orderAllocationPercent", "Order limit %", settings.orderAllocationPercent, 0.1, REGIME_MAX_ORDER_ALLOCATION_PERCENT, 0.1)}
          ${renderRegimeTradingSettingInput("dailyAllocationPercent", "Buying power %", settings.dailyAllocationPercent, 0.1, 100, 0.1)}
          ${renderRegimeTradingSettingInput("riskBudgetPercentOfOrder", "Risk budget %", settings.riskBudgetPercentOfOrder, 0.1, 100, 0.1)}
          ${renderRegimeTradingSettingInput("maxTradesPerDay", "Max trades/day", settings.maxTradesPerDay, 1, 50, 1)}
          ${renderRegimeTradingSettingInput("fixedStopDistanceDollars", "Stop $/share", settings.fixedStopDistanceDollars, 0, 100, 0.01)}
          ${renderRegimeTradingSettingInput("stopLossPercent", "Stop %", settings.stopLossPercent, 0.01, 20, 0.01)}
          ${renderRegimeTradingSettingInput("takeProfitR", "Target R", settings.takeProfitR, 0.1, 20, 0.1)}
          ${renderRegimeTradingSettingInput("slippagePerShare", "Slippage/share", settings.slippagePerShare, 0, 10, 0.01)}
        </div>
        ${renderConfidenceTargetOrderSettings(targetOrder, "Regime", "regime")}
        ${renderRegimeDefaultSizingSection(settings, adapted)}
      </div>
    </div>
  `;
}

function regimeTradingSettingsSummary(result: RegimeSelectionResult, adapted: ConfidenceAggregationResult) {
  return `Qty ${adapted.positionSize} · ${formatProbability(result.scores.buy)} buy · edge ${formatProbability(result.scoreEdge)} · ${formatProbability(result.confidence)} condition`;
}

function renderRegimeDefaultSizingSection(settings: TradingSettings, result: ConfidenceAggregationResult) {
  return renderConfidenceDefaultSizingSection(settings, result, {
    expanded: state.regimeDefaultSizingExpanded,
    toggleId: "regimeDefaultSizingToggle",
    bodyId: "regimeDefaultSizingBody",
    inputRenderer: renderRegimeTradingSettingInput,
    toggleRenderer: renderRegimeTradingSettingToggle,
  });
}

function regimeSelectionAsConfidenceResult(result: RegimeSelectionResult): ConfidenceAggregationResult {
  const market = confidenceMarketSnapshot();
  const signal: AlgoSignal = result.tradeAllowed ? "Buy" : result.signal === "Sell" ? "Sell" : "Hold";
  const normalizedNetScore = result.tradeAllowed ? result.scores.buy : 0;
  const sizing = market ? confidencePositionSizing(market, signal, normalizedNetScore, { mode: "regime" }) : confidenceEmptyPositionSizing("Waiting for session candles");
  const hardFilters: ConfidenceAggregationResult["hardFilters"] = [
    {
      label: "Regime Selector",
      status: result.tradeAllowed ? "pass" : "fail",
      detail: result.tradeBlockers.length ? result.tradeBlockers.join("; ") : "Regime score, edge, confidence, and opportunity passed",
    },
    {
      label: "Market Condition",
      status: result.confidence >= 0.65 ? "pass" : "fail",
      detail: `${result.confirmedCondition}, confidence ${formatProbability(result.confidence)}`,
    },
    {
      label: "Buy Score",
      status: result.scores.buy >= 0.6 ? "pass" : "fail",
      detail: `${formatProbability(result.scores.buy)} / required 60%`,
    },
    {
      label: "Buy Edge",
      status: result.scoreEdge >= 0.2 ? "pass" : "fail",
      detail: `${formatProbability(result.scoreEdge)} / required 20%`,
    },
  ];
  return {
    signal,
    decisionLabel: signal === "Buy" ? "Buy" : signal === "Sell" ? "Sell" : "Hold",
    buyScore: result.scores.buy,
    sellScore: result.scores.sell,
    netScore: result.scores.buy - result.scores.sell,
    activeWeight: 1,
    normalizedNetScore,
    activeStrategyCount: result.activeStrategyCount,
    buyWeight: result.scores.buy,
    sellWeight: result.scores.sell,
    buyAgreement: result.scores.buy,
    sellAgreement: result.scores.sell,
    buyAverageConfidence: result.scores.buy,
    sellAverageConfidence: result.scores.sell,
    buyThreshold: 0.6,
    sellThreshold: -0.6,
    strongBuyThreshold: 0.8,
    strongSellThreshold: -0.8,
    strategies: result.selectedStrategies,
    stopDistance: sizing.stopDistance,
    positionSizeMultiplier: sizing.sizeMultiplier,
    positionSize: sizing.finalQuantity,
    sizing,
    hardFilters,
    logs: result.reasons,
    detail: result.tradeAllowed ? "Regime Selector trade gates passed." : `Regime Selector blocked order: ${result.tradeBlockers.join("; ")}`,
  };
}

function renderConfidenceTradingSettingsPanel(result: ConfidenceAggregationResult) {
  const settings = state.confidenceTradingSettings;
  const expanded = state.confidenceTradingSettingsExpanded;
  const targetOrder = confidenceTargetOrderRecommendation(result);
  state.currentConfidenceTargetOrder = targetOrder;
  return `
    <div class="trading-settings-panel weighted-trading-settings-panel" data-status="ready" data-expanded="${String(expanded)}">
      <button id="confidenceTradingSettingsToggle" class="trading-settings-head" type="button" aria-expanded="${String(expanded)}" aria-controls="confidenceTradingSettingsBody">
        <span class="trading-settings-title">
          <b>${expanded ? "-" : "+"}</b>
          <strong>Trading Settings</strong>
        </span>
        <span class="trading-settings-summary">${escapeHtml(confidenceTradingSettingsSummary(settings, result))}</span>
      </button>
      <div id="confidenceTradingSettingsBody" class="trading-settings-body" ${expanded ? "" : "hidden"}>
        <div class="trading-settings-grid">
          ${renderConfidenceTradingSettingInput("startingCapital", "Total balance", settings.startingCapital, 1000, 10000000, 100)}
          ${renderConfidenceTradingSettingInput("orderAllocationPercent", "Order limit %", settings.orderAllocationPercent, 0.1, MAX_ORDER_ALLOCATION_PERCENT, 0.1)}
          ${renderConfidenceTradingSettingInput("dailyAllocationPercent", "Buying power %", settings.dailyAllocationPercent, 0.1, 100, 0.1)}
          ${renderConfidenceTradingSettingInput("riskBudgetPercentOfOrder", "Risk budget %", settings.riskBudgetPercentOfOrder, 0.1, 100, 0.1)}
          ${renderConfidenceTradingSettingInput("maxTradesPerDay", "Max trades/day", settings.maxTradesPerDay, 1, 50, 1)}
          ${renderConfidenceTradingSettingInput("fixedStopDistanceDollars", "Stop $/share", settings.fixedStopDistanceDollars, 0, 100, 0.01)}
          ${renderConfidenceTradingSettingInput("stopLossPercent", "Stop %", settings.stopLossPercent, 0.01, 20, 0.01)}
          ${renderConfidenceTradingSettingInput("takeProfitR", "Target R", settings.takeProfitR, 0.1, 20, 0.1)}
          ${renderConfidenceTradingSettingInput("slippagePerShare", "Slippage/share", settings.slippagePerShare, 0, 10, 0.01)}
        </div>
        ${renderConfidenceTargetOrderSettings(targetOrder)}
        ${renderConfidenceDefaultSizingSection(settings, result)}
      </div>
    </div>
  `;
}

function confidenceTradingSettingsSummary(settings: TradingSettings, result: ConfidenceAggregationResult) {
  return `Qty ${result.positionSize} · ${formatProbability(result.sizing.signalStrength)} strength · ${formatProbability(result.sizing.sizeMultiplier)} size · ${currency(settings.startingCapital)} account`;
}

function renderConfidenceDefaultSizingSection(
  settings: TradingSettings,
  result: ConfidenceAggregationResult,
  options: {
    expanded?: boolean;
    toggleId?: string;
    bodyId?: string;
    inputRenderer?: typeof renderConfidenceTradingSettingInput;
    toggleRenderer?: typeof renderConfidenceTradingSettingToggle;
  } = {},
) {
  const expanded = options.expanded ?? state.confidenceDefaultSizingExpanded;
  const toggleId = options.toggleId ?? "confidenceDefaultSizingToggle";
  const bodyId = options.bodyId ?? "confidenceDefaultSizingBody";
  const inputRenderer = options.inputRenderer ?? renderConfidenceTradingSettingInput;
  const toggleRenderer = options.toggleRenderer ?? renderConfidenceTradingSettingToggle;
  const defaults = defaultSizingSettingsFromTradingSettings(settings);
  const sizing = result.sizing;
  return `
    <div class="trading-default-section weighted-default-section" data-expanded="${String(expanded)}">
      <div class="trading-default-head">
        <button id="${toggleId}" class="trading-default-expand" type="button" aria-expanded="${String(expanded)}" aria-controls="${bodyId}">
          <b>${expanded ? "-" : "+"}</b>
          <strong>Default Settings</strong>
        </button>
        ${toggleRenderer("useDefaultSizingSettings", "On / Off", settings.useDefaultSizingSettings)}
      </div>
      <div id="${bodyId}" class="trading-default-body" ${expanded ? "" : "hidden"}>
        <div class="trading-settings-grid trading-default-grid">
          ${inputRenderer("minimumBuyScore", "Minimum buy score", settings.minimumBuyScore, 0, 1, 0.01)}
          ${inputRenderer("minimumSignalEdge", "Minimum signal edge", settings.minimumSignalEdge, 0, 1, 0.01)}
          ${inputRenderer("baseRiskPercent", "Base risk %", settings.baseRiskPercent, 0.01, 10, 0.01)}
          ${inputRenderer("maxPositionPercent", "Max position %", settings.maxPositionPercent, 0.1, 100, 0.1)}
          ${inputRenderer("fixedStopDistanceDollars", "Stop $/share", settings.fixedStopDistanceDollars, 0, 100, 0.01)}
          ${inputRenderer("atrStopMultiplier", "ATR stop multiplier", settings.atrStopMultiplier, 0.1, 10, 0.1)}
          ${inputRenderer("minimumStopDistancePercent", "Min stop distance %", settings.minimumStopDistancePercent, 0.001, 5, 0.001)}
          ${inputRenderer("maxParticipationPercent", "Max participation %", settings.maxParticipationPercent, 0.001, 10, 0.001)}
          ${inputRenderer("minimumOneMinuteVolume", "Min 1m volume", settings.minimumOneMinuteVolume, 0, 10000000, 1)}
          ${inputRenderer("maxAllowedShares", "Max shares (0 auto)", settings.maxAllowedShares, 0, 1000000, 1)}
          ${inputRenderer("maxDailyLossPercent", "Max daily loss %", settings.maxDailyLossPercent, 0.1, 10, 0.1)}
          ${toggleRenderer("pyramidingEnabled", "Pyramiding", settings.pyramidingEnabled)}
        </div>
        <div class="confidence-sizing-preview">
          <span><b>Size Ladder</b> 50-60% = 25%, 60-70% = 50%, 70-80% = 75%, 80-100% = 100%</span>
          <span><b>Signal Strength</b> ${formatProbability(sizing.signalStrength)} -> ${formatProbability(sizing.sizeMultiplier)} size multiplier</span>
          <span><b>Risk Dollars</b> ${currency(sizing.accountEquity)} x ${roundNumber(defaults.baseRiskPercent, 3)}% x ${formatProbability(sizing.sizeMultiplier)} = ${currency(sizing.riskDollars)}</span>
          <span><b>Stop Distance</b> ${price(defaults.fixedStopDistanceDollars)} fixed $/share = ${price(sizing.stopDistance)}</span>
          <span><b>Shares By Risk</b> ${Number.isFinite(sizing.sharesByRisk) ? Math.floor(sizing.sharesByRisk).toLocaleString() : "0"}</span>
          <span><b>Shares By Order Limit</b> ${formatShareLimit(sizing.sharesByOrder)}</span>
          <span><b>Shares By Capital</b> ${Math.floor(sizing.sharesByCapital).toLocaleString()}</span>
          <span><b>Shares By Buying Power</b> ${Math.floor(sizing.sharesByBuyingPower).toLocaleString()}</span>
          <span><b>Shares By Liquidity</b> ${formatShareLimit(sizing.sharesByLiquidity)}</span>
          <span><b>Current Position Value</b> ${currency(sizing.currentPositionValue)} / ${currency(sizing.maxPositionDollars)} max</span>
          <span><b>Final Quantity</b> ${sizing.finalQuantity.toLocaleString()}${sizing.blockedReason ? ` · ${escapeHtml(sizing.blockedReason)}` : ""}</span>
        </div>
      </div>
    </div>
  `;
}

function confidenceTargetOrderRecommendation(result: ConfidenceAggregationResult, mode: TradingWindowMode = "confidence"): ManualOrderRecommendation {
  const settings = tradingSettingsForMode(mode);
  const latest = latestExecutionCandleForMode(mode);
  const targetOverrides =
    mode === "regime"
      ? state.regimeTargetOrderOverrides
      : mode === "meta"
        ? state.metaTargetOrderOverrides
        : state.confidenceTargetOrderOverrides;
  const submitMode = (targetOverrides.submitMode as SubmitOrderMode | undefined) ?? DEFAULT_SUBMIT_MODE;
  const automaticShortCycleBuy = submitMode === "Automatic";
  const useAutomaticSizingBoost = automaticShortCycleBuy && mode === "confidence";
  const market = useAutomaticSizingBoost ? confidenceMarketSnapshot() : null;
  const sizing =
    useAutomaticSizingBoost && market
      ? confidencePositionSizing(market, "Buy", result.normalizedNetScore, { automaticShortCycleBuy: true, result, mode })
      : result.sizing;
  const failedGates = confidenceTargetOrderFailedGates(result, useAutomaticSizingBoost, market, mode).map((filter) => `${filter.label}: ${filter.detail}`);
  if (sizing.blockedReason) {
    failedGates.push(`Sizing: ${sizing.blockedReason}`);
  }
  const side = automaticShortCycleBuy ? "Buy" : failedGates.length ? "Hold" : result.signal;
  const priceValue = latest?.close ?? null;
  const pricingSide: Exclude<AlgoSignal, "Hold"> =
    automaticShortCycleBuy ? "Buy" : side === "Buy" || side === "Sell" ? side : result.normalizedNetScore < 0 ? "Sell" : "Buy";
  const triggerPrice =
    pricingSide === "Buy" && priceValue !== null
      ? roundNumber(priceValue + settings.slippagePerShare, 2)
      : pricingSide === "Sell" && priceValue !== null
        ? roundNumber(Math.max(0, priceValue - settings.slippagePerShare), 2)
        : null;
  const limitPrice =
    pricingSide === "Buy" && triggerPrice !== null
      ? roundNumber(triggerPrice + settings.slippagePerShare, 2)
      : pricingSide === "Sell" && triggerPrice !== null
        ? roundNumber(Math.max(0, triggerPrice - settings.slippagePerShare), 2)
        : null;
  const stopPrice =
    pricingSide === "Buy" && triggerPrice !== null
      ? roundNumber(Math.max(0, triggerPrice - sizing.stopDistance), 2)
      : pricingSide === "Sell" && triggerPrice !== null
        ? roundNumber(triggerPrice + sizing.stopDistance, 2)
        : null;
  const quantity = side === "Hold" ? 0 : sizing.finalQuantity;
  const targetDistance = targetProfitDistancePerShare(quantity, sizing.stopDistance, settings.takeProfitR);
  const targetPrice =
    pricingSide === "Buy" && triggerPrice !== null
      ? roundNumber(triggerPrice + targetDistance, 2)
      : pricingSide === "Sell" && triggerPrice !== null
        ? roundNumber(Math.max(0, triggerPrice - targetDistance), 2)
        : null;
  const orderNotional = triggerPrice !== null ? roundNumber(quantity * triggerPrice, 2) : 0;
  const plannedStopRiskDollars = triggerPrice !== null && stopPrice !== null ? roundNumber(quantity * Math.abs(triggerPrice - stopPrice), 2) : 0;
  const estimatedSlippage = roundNumber(quantity * settings.slippagePerShare * 2, 2);
  const eligible = side !== "Hold" && quantity > 0 && failedGates.length === 0;
  const orderType = eligible ? `${side} stop-limit` : "No order";
  const summary =
    !eligible
      ? `No order: ${uniqueStrings(failedGates).join(", ") || `${algorithmDisplayName(mode)} final signal is Hold`}.`
      : `${orderType} ${state.symbol}, ${quantity} shares, trigger ${price(triggerPrice ?? 0)}, limit ${price(limitPrice ?? 0)}, stop ${price(stopPrice ?? 0)}, target ${price(targetPrice ?? 0)}.`;
  return applyConfidenceTargetOrderOverrides({
    eligible,
    side,
    orderType,
    symbol: state.symbol,
    quantity,
    triggerPrice,
    limitPrice,
    stopPrice,
    targetPrice,
    accountBalance: sizing.accountEquity,
    orderLimitDollars: sizing.accountEquity * (settings.orderAllocationPercent / 100),
    dailyLimitDollars: sizing.availableBuyingPower + sizing.currentPositionValue,
    riskDollars: sizing.riskDollars,
    orderNotional,
    plannedStopRiskDollars,
    estimatedSlippage,
    timeInForce: "Day",
    cutoff: "No new trades after 15:30 ET",
    submitMode,
    failedGates: uniqueStrings(failedGates),
    gates: result.hardFilters.map((filter) => ({
      layer: filter.label,
      status: filter.status,
      signal: result.decisionLabel,
      detail: filter.detail,
    })),
    levels: {
      last: priceValue,
      vwap: null,
      openingHigh: null,
      openingLow: null,
      lastTime: latest?.timestamp ?? null,
    },
    summary,
  }, mode);
}

function confidenceTargetOrderFailedGates(
  result: ConfidenceAggregationResult,
  automaticShortCycleBuy: boolean,
  market: ConfidenceMarket | null,
  mode: TradingWindowMode = "confidence",
) {
  const failed = result.hardFilters.filter((filter) => filter.status === "fail");
  const latest = latestExecutionCandleForMode(mode);
  if ((mode === "confidence" || mode === "regime") && latest && (automaticShortCycleBuy || result.signal === "Buy")) {
    const lateSessionBlocker = lateSessionAboveAverageBuyBlocker(mode, latest.close, latest.timestamp);
    if (lateSessionBlocker) {
      failed.push({
        label: mode === "regime" ? "Regime Late-session Buy Guard" : "WCA Late-session Buy Guard",
        status: "fail",
        detail: lateSessionBlocker,
      });
    }
    forecastBuySafetyBlockers(mode, latest.close, latest.timestamp).forEach((detail) => {
      failed.push({
        label: mode === "regime" ? "Regime Forecast Safety" : "WCA Forecast Safety",
        status: "fail",
        detail,
      });
    });
  }
  if (!automaticShortCycleBuy) {
    return failed;
  }
  const hardFailures = [...failed];
  if (!market) {
    hardFailures.push({ label: "Short-cycle Market", status: "fail" as const, detail: "Waiting for live market data" });
    return hardFailures;
  }
  if (market.latest.close <= market.vwap) {
    hardFailures.push({
      label: "Short-cycle VWAP",
      status: "fail" as const,
      detail: `Close ${price(market.latest.close)} is not above VWAP ${price(market.vwap)}`,
    });
  }
  return hardFailures;
}

function applyConfidenceTargetOrderOverrides(order: ManualOrderRecommendation, mode: TradingWindowMode = "confidence"): ManualOrderRecommendation {
  const overrides =
    mode === "regime"
      ? state.regimeTargetOrderOverrides
      : mode === "meta"
        ? state.metaTargetOrderOverrides
        : state.confidenceTargetOrderOverrides;
  const settings = tradingSettingsForMode(mode);
  const useDefaults = settings.useDefaultSizingSettings;
  const quantity = order.quantity;
  const triggerPrice = useDefaults ? order.triggerPrice : overrides.triggerPrice === null ? null : Number(overrides.triggerPrice ?? order.triggerPrice);
  const limitPrice = useDefaults ? order.limitPrice : overrides.limitPrice === null ? null : Number(overrides.limitPrice ?? order.limitPrice);
  const stopPrice = useDefaults ? order.stopPrice : overrides.stopPrice === null ? null : Number(overrides.stopPrice ?? order.stopPrice);
  const targetPrice = useDefaults ? order.targetPrice : overrides.targetPrice === null ? null : Number(overrides.targetPrice ?? order.targetPrice);
  const orderNotional =
    useDefaults
      ? order.orderNotional
      : Number.isFinite(Number(overrides.orderNotional))
      ? Number(overrides.orderNotional)
      : triggerPrice !== null && Number.isFinite(triggerPrice)
        ? roundNumber(quantity * triggerPrice, 2)
        : order.orderNotional;
  const plannedStopRiskDollars =
    useDefaults
      ? order.plannedStopRiskDollars
      : Number.isFinite(Number(overrides.plannedStopRiskDollars))
      ? Number(overrides.plannedStopRiskDollars)
      : triggerPrice !== null && stopPrice !== null && Number.isFinite(triggerPrice) && Number.isFinite(stopPrice)
        ? roundNumber(quantity * Math.abs(triggerPrice - stopPrice), 2)
        : order.plannedStopRiskDollars;
  const estimatedSlippage = useDefaults
    ? order.estimatedSlippage
    : Number.isFinite(Number(overrides.estimatedSlippage))
      ? Number(overrides.estimatedSlippage)
      : roundNumber(quantity * settings.slippagePerShare * 2, 2);
  const submitMode = (overrides.submitMode as SubmitOrderMode | undefined) ?? order.submitMode;
  const side = submitMode === "Automatic" ? "Buy" : useDefaults ? order.side : (overrides.side as AlgoSignal | undefined) ?? order.side;
  return {
    ...order,
    side,
    orderType: useDefaults || submitMode === "Automatic" ? order.orderType : String(overrides.orderType ?? order.orderType),
    symbol: String((useDefaults ? order.symbol : overrides.symbol ?? order.symbol)).toUpperCase(),
    quantity,
    triggerPrice: triggerPrice !== null && Number.isFinite(triggerPrice) ? triggerPrice : null,
    limitPrice: limitPrice !== null && Number.isFinite(limitPrice) ? limitPrice : null,
    stopPrice: stopPrice !== null && Number.isFinite(stopPrice) ? stopPrice : null,
    targetPrice: targetPrice !== null && Number.isFinite(targetPrice) ? targetPrice : null,
    accountBalance: roundNumber(useDefaults ? order.accountBalance : Number(overrides.accountBalance ?? order.accountBalance), 2),
    orderLimitDollars: roundNumber(useDefaults ? order.orderLimitDollars : Number(overrides.orderLimitDollars ?? order.orderLimitDollars), 2),
    dailyLimitDollars: roundNumberUp(useDefaults ? order.dailyLimitDollars : Number(overrides.dailyLimitDollars ?? order.dailyLimitDollars), 2),
    riskDollars: roundNumber(useDefaults ? order.riskDollars : Number(overrides.riskDollars ?? order.riskDollars), 2),
    orderNotional,
    plannedStopRiskDollars,
    estimatedSlippage,
    timeInForce: useDefaults ? order.timeInForce : String(overrides.timeInForce ?? order.timeInForce),
    cutoff: useDefaults ? order.cutoff : String(overrides.cutoff ?? order.cutoff),
    submitMode,
  };
}

function renderConfidenceTargetOrderSettings(order: ManualOrderRecommendation, sourceLabel = "WCA", mode: TradingWindowMode = "confidence") {
  const defaultsOn = tradingSettingsForMode(mode).useDefaultSizingSettings;
  const targetDataset = mode === "regime" ? "regime-target-setting" : mode === "meta" ? "meta-target-setting" : "confidence-target-setting";
  return `
    <div class="target-settings-panel weighted-target-settings-panel" data-side="${escapeHtml(order.side.toLowerCase())}">
      <strong>Target Order</strong>
      <span class="target-settings-note">${defaultsOn ? `Generated from ${escapeHtml(sourceLabel)} sizing and default settings` : "Manual target-order overrides enabled"}</span>
      ${renderTargetOrderBlockers(order)}
      <div class="target-settings-grid">
        ${renderConfidenceTargetSettingInput("accountBalance", "Total balance", order.accountBalance, "number", 0.01, undefined, defaultsOn, targetDataset)}
        ${renderConfidenceTargetSettingInput("dailyLimitDollars", "Buying power", order.dailyLimitDollars, "number", 0.01, undefined, defaultsOn, targetDataset)}
        ${renderConfidenceTargetSettingInput("orderLimitDollars", "Order limit", order.orderLimitDollars, "number", 0.01, undefined, defaultsOn, targetDataset)}
        ${renderConfidenceTargetSettingInput("orderNotional", "Order value", order.orderNotional, "number", 0.01, undefined, defaultsOn, targetDataset)}
        ${renderConfidenceTargetSettingInput("symbol", "Symbol", order.symbol, "text", undefined, undefined, defaultsOn, targetDataset)}
        ${renderConfidenceTargetSettingSelect("side", "Side", order.side, ["Buy", "Sell", "Hold"], undefined, defaultsOn, targetDataset)}
        ${renderConfidenceTargetSettingSelect("orderType", "Order type", order.orderType, ["No order", "Buy stop-limit", "Sell stop-limit"], undefined, defaultsOn, targetDataset)}
        ${renderConfidenceTargetSettingInput("quantity", "Quantity", order.quantity, "number", 1, undefined, true, targetDataset)}
        ${renderConfidenceTargetSettingInput("triggerPrice", "Trigger / stop price", order.triggerPrice, "number", 0.01, undefined, defaultsOn, targetDataset)}
        ${renderConfidenceTargetSettingInput("limitPrice", "Limit price", order.limitPrice, "number", 0.01, undefined, defaultsOn, targetDataset)}
        ${renderConfidenceTargetSettingInput("stopPrice", "Protective stop", order.stopPrice, "number", 0.01, undefined, defaultsOn, targetDataset)}
        ${renderConfidenceTargetSettingInput("targetPrice", "Take profit", order.targetPrice, "number", 0.01, undefined, defaultsOn, targetDataset)}
        ${renderConfidenceTargetSettingInput("riskDollars", "Risk budget", order.riskDollars, "number", 0.01, undefined, defaultsOn, targetDataset)}
        ${renderConfidenceTargetSettingInput("plannedStopRiskDollars", "Planned stop risk", order.plannedStopRiskDollars, "number", 0.01, undefined, defaultsOn, targetDataset)}
        ${renderConfidenceTargetSettingInput("estimatedSlippage", "Estimated slippage", order.estimatedSlippage, "number", 0.01, undefined, defaultsOn, targetDataset)}
        ${renderConfidenceTargetSettingInput("timeInForce", "Time in force", order.timeInForce, "text", undefined, "half", defaultsOn, targetDataset)}
        ${renderConfidenceTargetSettingInput("cutoff", "Cutoff", order.cutoff, "text", undefined, "half", defaultsOn, targetDataset)}
        ${renderConfidenceTargetSettingSelect("submitMode", "Submit order", order.submitMode, ["Manual", "Automatic"], "half", false, targetDataset)}
      </div>
      <span class="trading-settings-warning">${escapeHtml(order.summary)}</span>
    </div>
  `;
}

function renderConfidenceTargetSettingInput(
  name: keyof TargetOrderSettings,
  label: string,
  value: string | number | null,
  type: "number" | "text",
  step?: number,
  layout?: "wide" | "half",
  locked = false,
  datasetName = "confidence-target-setting",
) {
  const inputValue = value === null ? "" : String(value);
  return `
    <label class="${layout ?? ""}" data-generated="${String(locked)}">
      <span>${escapeHtml(label)}</span>
      <input data-${datasetName}="${escapeHtml(name)}" type="${type}" ${step ? `step="${step}"` : ""} value="${escapeHtml(inputValue)}" ${locked ? "readonly" : ""} />
    </label>
  `;
}

function renderConfidenceTargetSettingSelect(
  name: keyof TargetOrderSettings,
  label: string,
  value: string,
  options: string[],
  layout?: "wide" | "half",
  locked = false,
  datasetName = "confidence-target-setting",
) {
  return `
    <label class="${layout ?? ""}" data-generated="${String(locked)}">
      <span>${escapeHtml(label)}</span>
      <select data-${datasetName}="${escapeHtml(name)}" ${locked ? "disabled" : ""}>
        ${options.map((option) => `<option value="${escapeHtml(option)}" ${option === value ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
      </select>
    </label>
  `;
}

function renderConfidenceTradingSettingInput(
  name: keyof TradingSettings,
  label: string,
  value: number,
  min: number,
  max: number,
  step: number,
) {
  return `
    <label>
      <span>${escapeHtml(label)}</span>
      <input data-confidence-trading-setting="${escapeHtml(name)}" type="number" min="${min}" max="${max}" step="${step}" value="${value}" />
    </label>
  `;
}

function renderConfidenceTradingSettingToggle(
  name: keyof TradingSettings,
  label: string,
  checked: boolean,
) {
  return `
    <label class="trading-default-toggle">
      <span>${escapeHtml(label)}</span>
      <input data-confidence-trading-setting="${escapeHtml(name)}" type="checkbox" ${checked ? "checked" : ""} />
    </label>
  `;
}

function renderRegimeTradingSettingInput(
  name: keyof TradingSettings,
  label: string,
  value: number,
  min: number,
  max: number,
  step: number,
) {
  return `
    <label>
      <span>${escapeHtml(label)}</span>
      <input data-regime-trading-setting="${escapeHtml(name)}" type="number" min="${min}" max="${max}" step="${step}" value="${value}" />
    </label>
  `;
}

function renderRegimeTradingSettingToggle(
  name: keyof TradingSettings,
  label: string,
  checked: boolean,
) {
  return `
    <label class="trading-default-toggle">
      <span>${escapeHtml(label)}</span>
      <input data-regime-trading-setting="${escapeHtml(name)}" type="checkbox" ${checked ? "checked" : ""} />
    </label>
  `;
}

function renderConfidenceRequirementInput(
  name: keyof ConfidenceDecisionSettings,
  label: string,
  value: number,
  min: number,
  max: number,
  step: number,
) {
  return `
    <label>
      <span>${escapeHtml(label)}</span>
      <input data-confidence-requirement="${escapeHtml(name)}" type="number" min="${min}" max="${max}" step="${step}" value="${value}" />
    </label>
  `;
}

function renderConfidenceStrategies(strategies: ConfidenceStrategyResult[]) {
  return `
    <table class="weighted-strategy-table confidence-strategy-table">
      <colgroup>
        <col class="confidence-strategy-metric-col" />
        <col class="confidence-strategy-metric-col" />
        <col class="confidence-strategy-metric-col" />
        <col class="confidence-strategy-metric-col" />
        <col class="confidence-strategy-metric-col" />
        <col class="confidence-strategy-reason-col" />
      </colgroup>
      <tbody>
        ${strategies
          .map(
            (strategy, strategyIndex) => `
              <tr class="weighted-strategy-name-row" data-tone="${strategyIndex % 4}" data-disabled="${String(strategy.signal === "hold")}">
                <td colspan="5">${escapeHtml(strategy.name)}</td>
                <td>${escapeHtml(strategy.signal)}</td>
              </tr>
              <tr class="weighted-strategy-detail-row" data-tone="${strategyIndex % 4}" data-disabled="${String(strategy.signal === "hold")}">
                ${renderWeightedMetricCell("Base", strategy.base_weight.toFixed(2))}
                ${renderWeightedMetricCell("Effective", strategy.effective_weight.toFixed(2))}
                ${renderWeightedMetricCell("Confidence", strategy.confidence.toFixed(2))}
                ${renderWeightedMetricCell("Direction", String(strategy.direction))}
                ${renderWeightedMetricCell("Contribution", strategy.contribution.toFixed(2))}
                <td>
                  <small>Reason</small>
                  <strong>${escapeHtml(strategy.reason)}</strong>
                </td>
              </tr>
            `,
          )
          .join("")}
      </tbody>
    </table>
  `;
}

function updateMetaStrategyPanel() {
  const result = calculateMetaStrategy();
  state.currentMetaTargetOrder = metaTargetOrderRecommendation(result);
  metaFinalSignal.textContent = result.decisionLabel;
  metaFinalSignal.className = `algo-final ${result.signal.toLowerCase()}`;
  metaScoreGrid.innerHTML = renderMetaScoreGrid(result);
  metaSummary.innerHTML = "";
  metaSummary.hidden = true;
  renderMetaMlReadiness();
  if (state.metaTrainingStatus === "idle") {
    void loadMetaStrategyTrainingStatus();
  }
  metaGateList.innerHTML = renderMetaDecisionChecks(result);
  metaFamilyGrid.innerHTML = renderMetaFamilyGrid(result);
  metaStrategiesList.innerHTML = renderMetaStrategies(result.strategies);
  metaStrategiesToggleMeta.textContent = `${result.strategies.filter((strategy) => strategy.signal !== "hold").length} active / ${result.strategies.length} strategies`;
  const failedChecks = result.safetyGates.filter((gate) => gate.status === "fail").length;
  const infoChecks = result.safetyGates.filter((gate) => gate.status === "info").length;
  metaChecksToggleMeta.textContent = failedChecks
    ? `${failedChecks} blocked / ${result.safetyGates.length} checks`
    : `${result.safetyGates.length - infoChecks} pass / ${result.safetyGates.length} checks`;
  renderMetaStrategiesExpandedState();
  renderMetaChecksExpandedState();
}

function metaTargetOrderRecommendation(result: MetaStrategyResult): ManualOrderRecommendation | null {
  const market = confidenceMarketSnapshot();
  const latest = latestExecutionCandleForMode("meta");
  if (!market || !latest) {
    return null;
  }
  const settings = state.metaTradingSettings;
  const submitMode = (state.metaTargetOrderOverrides.submitMode as SubmitOrderMode | undefined) ?? DEFAULT_SUBMIT_MODE;
  const sizing = confidencePositionSizing(market, result.signal, result.netScore, { mode: "meta" });
  const failedGates = result.safetyGates.filter((gate) => gate.status === "fail").map((gate) => `${gate.label}: ${gate.detail}`);
  if (result.signal === "Hold") {
    failedGates.push(`Signal: Meta-Strategy final signal is ${result.decisionLabel}`);
  }
  if (sizing.blockedReason) {
    failedGates.push(`Sizing: ${sizing.blockedReason}`);
  }
  const side: AlgoSignal = failedGates.length ? "Hold" : result.signal;
  const priceValue = latest.close;
  const pricingSide: Exclude<AlgoSignal, "Hold"> = side === "Buy" || side === "Sell" ? side : result.netScore < 0 ? "Sell" : "Buy";
  const triggerPrice =
    pricingSide === "Buy"
      ? roundNumber(priceValue + settings.slippagePerShare, 2)
      : roundNumber(Math.max(0, priceValue - settings.slippagePerShare), 2);
  const limitPrice =
    pricingSide === "Buy"
      ? roundNumber(triggerPrice + settings.slippagePerShare, 2)
      : roundNumber(Math.max(0, triggerPrice - settings.slippagePerShare), 2);
  const stopPrice =
    pricingSide === "Buy"
      ? roundNumber(Math.max(0, triggerPrice - sizing.stopDistance), 2)
      : roundNumber(triggerPrice + sizing.stopDistance, 2);
  const quantity = side === "Hold" ? 0 : sizing.finalQuantity;
  const targetDistance = targetProfitDistancePerShare(quantity, sizing.stopDistance, settings.takeProfitR);
  const targetPrice =
    pricingSide === "Buy"
      ? roundNumber(triggerPrice + targetDistance, 2)
      : roundNumber(Math.max(0, triggerPrice - targetDistance), 2);
  const orderNotional = roundNumber(quantity * triggerPrice, 2);
  const plannedStopRiskDollars = roundNumber(quantity * Math.abs(triggerPrice - stopPrice), 2);
  const estimatedSlippage = roundNumber(quantity * settings.slippagePerShare * 2, 2);
  const eligible = side !== "Hold" && quantity > 0 && failedGates.length === 0;
  const orderType = eligible ? `${side} stop-limit` : "No order";
  const summary =
    eligible
      ? `${orderType} ${state.symbol}, ${quantity} shares, trigger ${price(triggerPrice)}, limit ${price(limitPrice)}, stop ${price(stopPrice)}, target ${price(targetPrice)}.`
      : `No order: ${uniqueStrings(failedGates).join(", ") || "Meta-Strategy final signal is Hold"}.`;
  return applyMetaTargetOrderOverrides({
    eligible,
    side,
    orderType,
    symbol: state.symbol,
    quantity,
    triggerPrice,
    limitPrice,
    stopPrice,
    targetPrice,
    accountBalance: sizing.accountEquity,
    orderLimitDollars: sizing.accountEquity * (settings.orderAllocationPercent / 100),
    dailyLimitDollars: sizing.availableBuyingPower + sizing.currentPositionValue,
    riskDollars: sizing.riskDollars,
    orderNotional,
    plannedStopRiskDollars,
    estimatedSlippage,
    timeInForce: "Day",
    cutoff: "No new trades after 15:30 ET",
    submitMode,
    failedGates: uniqueStrings(failedGates),
    gates: result.safetyGates.map((gate) => ({
      layer: gate.label,
      status: gate.status,
      signal: result.decisionLabel,
      detail: gate.detail,
    })),
    levels: {
      last: priceValue,
      vwap: market.vwap,
      openingHigh: market.openingRange.high,
      openingLow: market.openingRange.low,
      lastTime: latest.timestamp,
    },
    summary,
  });
}

function applyMetaTargetOrderOverrides(order: ManualOrderRecommendation): ManualOrderRecommendation {
  const overrides = state.metaTargetOrderOverrides;
  const settings = state.metaTradingSettings;
  const useDefaults = settings.useDefaultSizingSettings;
  const quantity = order.quantity;
  const triggerPrice = useDefaults ? order.triggerPrice : overrides.triggerPrice === null ? null : Number(overrides.triggerPrice ?? order.triggerPrice);
  const limitPrice = useDefaults ? order.limitPrice : overrides.limitPrice === null ? null : Number(overrides.limitPrice ?? order.limitPrice);
  const stopPrice = useDefaults ? order.stopPrice : overrides.stopPrice === null ? null : Number(overrides.stopPrice ?? order.stopPrice);
  const targetPrice = useDefaults ? order.targetPrice : overrides.targetPrice === null ? null : Number(overrides.targetPrice ?? order.targetPrice);
  const orderNotional =
    useDefaults
      ? order.orderNotional
      : Number.isFinite(Number(overrides.orderNotional))
        ? Number(overrides.orderNotional)
        : triggerPrice !== null && Number.isFinite(triggerPrice)
          ? roundNumber(quantity * triggerPrice, 2)
          : order.orderNotional;
  const plannedStopRiskDollars =
    useDefaults
      ? order.plannedStopRiskDollars
      : Number.isFinite(Number(overrides.plannedStopRiskDollars))
        ? Number(overrides.plannedStopRiskDollars)
        : triggerPrice !== null && stopPrice !== null && Number.isFinite(triggerPrice) && Number.isFinite(stopPrice)
          ? roundNumber(quantity * Math.abs(triggerPrice - stopPrice), 2)
          : order.plannedStopRiskDollars;
  const estimatedSlippage = useDefaults
    ? order.estimatedSlippage
    : Number.isFinite(Number(overrides.estimatedSlippage))
      ? Number(overrides.estimatedSlippage)
      : roundNumber(quantity * settings.slippagePerShare * 2, 2);
  const submitMode = (overrides.submitMode as SubmitOrderMode | undefined) ?? order.submitMode;
  const side = useDefaults ? order.side : (overrides.side as AlgoSignal | undefined) ?? order.side;
  return {
    ...order,
    side,
    orderType: useDefaults ? order.orderType : String(overrides.orderType ?? order.orderType),
    symbol: String((useDefaults ? order.symbol : overrides.symbol ?? order.symbol)).toUpperCase(),
    quantity,
    triggerPrice: triggerPrice !== null && Number.isFinite(triggerPrice) ? triggerPrice : null,
    limitPrice: limitPrice !== null && Number.isFinite(limitPrice) ? limitPrice : null,
    stopPrice: stopPrice !== null && Number.isFinite(stopPrice) ? stopPrice : null,
    targetPrice: targetPrice !== null && Number.isFinite(targetPrice) ? targetPrice : null,
    accountBalance: roundNumber(useDefaults ? order.accountBalance : Number(overrides.accountBalance ?? order.accountBalance), 2),
    orderLimitDollars: roundNumber(useDefaults ? order.orderLimitDollars : Number(overrides.orderLimitDollars ?? order.orderLimitDollars), 2),
    dailyLimitDollars: roundNumberUp(useDefaults ? order.dailyLimitDollars : Number(overrides.dailyLimitDollars ?? order.dailyLimitDollars), 2),
    riskDollars: roundNumber(useDefaults ? order.riskDollars : Number(overrides.riskDollars ?? order.riskDollars), 2),
    orderNotional,
    plannedStopRiskDollars,
    estimatedSlippage,
    timeInForce: useDefaults ? order.timeInForce : String(overrides.timeInForce ?? order.timeInForce),
    cutoff: useDefaults ? order.cutoff : String(overrides.cutoff ?? order.cutoff),
    submitMode,
  };
}

function calculateMetaStrategy(): MetaStrategyResult {
  const market = confidenceMarketSnapshot();
  if (!market) {
    return emptyMetaStrategyResult("Waiting for session candles");
  }

  const ensembleVotes = new Map(strategyEnsembleSignals(state.marketContext).map((vote) => [vote.strategy, vote]));
  const rawFeatures = metaStrategyCatalog.map((definition) => metaStrategyFeature(definition, market, ensembleVotes));
  const families = emptyMetaFamilyScores();
  const familyRawTotals = new Map<MetaStrategyFamily, number>();

  rawFeatures.forEach((feature) => {
    if (feature.role !== "directional") {
      return;
    }
    const weight = metaRoleWeight(feature.role);
    const strength = feature.confidence * weight;
    if (feature.direction === 1) {
      families[feature.family].buy += strength;
    } else if (feature.direction === -1) {
      families[feature.family].sell += strength;
    } else {
      families[feature.family].hold += Math.max(0.05, feature.confidence * 0.25);
    }
    familyRawTotals.set(feature.family, (familyRawTotals.get(feature.family) ?? 0) + Math.abs(strength));
  });

  const familyCap = 0.36;
  for (const family of metaStrategyFamilies()) {
    const directionalTotal = families[family].buy + families[family].sell;
    if (directionalTotal > familyCap) {
      const scale = familyCap / directionalTotal;
      families[family].buy = roundNumber(families[family].buy * scale, 4);
      families[family].sell = roundNumber(families[family].sell * scale, 4);
      families[family].capped = true;
    }
    families[family].hold = roundNumber(families[family].hold, 4);
  }

  const contextFeatures = rawFeatures.filter((feature) => feature.role === "context");
  const buyContext = contextFeatures.filter((feature) => feature.direction === 1).reduce((sum, feature) => sum + feature.confidence * 0.07, 0);
  const sellContext = contextFeatures.filter((feature) => feature.direction === -1).reduce((sum, feature) => sum + feature.confidence * 0.07, 0);
  const familyAggregation = metaFamilyAggregationScores(families, contextFeatures);
  const contextMultiplier = clampNumber(1 + Math.max(buyContext, sellContext) - Math.min(buyContext, sellContext) * 0.5, 0.85, 1.18);
  const buyContextMultiplier = clampNumber(1 + buyContext - sellContext * 0.5, 0.75, 1.2);
  const sellContextMultiplier = clampNumber(1 + sellContext - buyContext * 0.5, 0.75, 1.2);

  let buyScore = roundNumber(metaStrategyFamilies().reduce((sum, family) => sum + families[family].buy, 0) * buyContextMultiplier, 4);
  let sellScore = roundNumber(metaStrategyFamilies().reduce((sum, family) => sum + families[family].sell, 0) * sellContextMultiplier, 4);
  const holdScore = roundNumber(metaStrategyFamilies().reduce((sum, family) => sum + families[family].hold, 0), 4);

  const ensembleFeature = rawFeatures.find((feature) => feature.name === "Ensemble Strategy Voting");
  if (ensembleFeature && ensembleFeature.direction !== 0 && ensembleFeature.confidence >= 0.62) {
    if (ensembleFeature.direction === 1) {
      buyScore = roundNumber(buyScore + ensembleFeature.confidence * 0.08, 4);
    } else {
      sellScore = roundNumber(sellScore + ensembleFeature.confidence * 0.08, 4);
    }
  }

  const aggregateDirectionalTotal = buyScore + sellScore;
  const aggregateScale = aggregateDirectionalTotal > 1 ? 1 / aggregateDirectionalTotal : 1;
  if (aggregateScale < 1) {
    buyScore = roundNumber(buyScore * aggregateScale, 4);
    sellScore = roundNumber(sellScore * aggregateScale, 4);
  }

  const cashFeature = rawFeatures.find((feature) => feature.name === "Cash / Avoid Trading Filter");
  const cashBlocked = Boolean(cashFeature && cashFeature.confidence >= 0.75);
  const netScore = roundNumber(clampNumber(buyScore - sellScore, -1, 1), 4);
  const edge = roundNumber(Math.min(1, Math.abs(netScore)), 4);
  const rawSignal: AlgoSignal = edge >= 0.08 && Math.max(buyScore, sellScore) >= 0.22 ? (netScore > 0 ? "Buy" : "Sell") : "Hold";
  const signal: AlgoSignal = cashBlocked ? "Hold" : rawSignal;
  const decisionLabel: ConfidenceDecisionLabel =
    signal === "Buy" && edge >= 0.18
      ? "Strong Buy"
      : signal === "Buy"
        ? "Buy"
        : signal === "Sell" && edge >= 0.18
          ? "Strong Sell"
          : signal === "Sell"
            ? "Sell"
            : "Hold";

  const activeDirectionalCount = rawFeatures.filter((feature) => feature.role === "directional" && feature.direction !== 0).length;
  const cappedFamilies = metaStrategyFamilies().filter((family) => families[family].capped);
  const safetyGates = [
    {
      label: "Cash filter",
      status: cashBlocked ? "fail" : "pass",
      detail: cashFeature ? cashFeature.reason : "Cash filter unavailable",
    },
    {
      label: "Edge",
      status: rawSignal === "Hold" ? "info" : "pass",
      detail: `Net ${netScore.toFixed(2)} with edge ${edge.toFixed(2)} after family and aggregate caps`,
    },
    {
      label: "Family caps",
      status: cappedFamilies.length ? "info" : "pass",
      detail: cappedFamilies.length ? `${cappedFamilies.map(metaFamilyLabel).join(", ")} capped at ${formatProbability(familyCap)}` : "No family hit the cap",
    },
    {
      label: "Aggregate cap",
      status: aggregateScale < 1 ? "info" : "pass",
      detail:
        aggregateScale < 1
          ? `Buy/sell total normalized by ${aggregateScale.toFixed(2)}x to stay within 1.00`
          : "Buy/sell total stayed within 1.00",
    },
    {
      label: "Context",
      status: contextFeatures.some((feature) => feature.direction !== 0) ? "pass" : "info",
      detail: `${contextFeatures.filter((feature) => feature.direction !== 0).length}/${contextFeatures.length} context modifiers active`,
    },
  ] satisfies MetaStrategyResult["safetyGates"];

  const effectiveFeatures = rawFeatures.map((feature) => {
    if (feature.role !== "directional") {
      return feature;
    }
    const rawTotal = familyRawTotals.get(feature.family) ?? 0;
    const cappedTotal = families[feature.family].buy + families[feature.family].sell;
    const scale = (rawTotal > 0 ? Math.min(1, cappedTotal / rawTotal) : 1) * aggregateScale;
    return { ...feature, effectiveContribution: roundNumber(feature.contribution * scale, 4) };
  });
  const familyDisplayScores = metaFamilyDisplayScores(rawFeatures, familyAggregation);

  return {
    signal,
    decisionLabel,
    buyScore,
    sellScore,
    holdScore,
    netScore,
    edge,
    contextMultiplier: roundNumber(contextMultiplier, 4),
    aggregateScale: roundNumber(aggregateScale, 4),
    activeDirectionalCount,
    familyAggregation,
    familyScores: families,
    familyDisplayScores,
    safetyGates,
    strategies: effectiveFeatures,
    reasons: [
      `${activeDirectionalCount} directional strategy outputs are active across ${metaStrategyFamilies().length} capped families.`,
      `Context modifiers adjusted buy/sell by ${buyContextMultiplier.toFixed(2)}x/${sellContextMultiplier.toFixed(2)}x.`,
      aggregateScale < 1 ? `Aggregate directional score normalized by ${aggregateScale.toFixed(2)}x.` : "Aggregate directional score stayed within the 1.00 cap.",
      cashBlocked ? "Cash / Avoid Trading Filter forced Hold." : `Final signal ${decisionLabel} from net score ${netScore.toFixed(2)}.`,
    ],
  };
}

function emptyMetaStrategyResult(reason: string): MetaStrategyResult {
  return {
    signal: "Hold",
    decisionLabel: "Hold",
    buyScore: 0,
    sellScore: 0,
    holdScore: 1,
    netScore: 0,
    edge: 0,
    contextMultiplier: 1,
    aggregateScale: 1,
    activeDirectionalCount: 0,
    familyAggregation: emptyMetaFamilyAggregationScores(),
    familyScores: emptyMetaFamilyScores(),
    familyDisplayScores: {},
    safetyGates: [{ label: "Market data", status: "info", detail: reason }],
    strategies: metaStrategyCatalog.map((definition) => ({
      name: definition.name,
      role: definition.role,
      family: definition.family,
      signal: "hold",
      confidence: 0,
      direction: 0,
      contribution: 0,
      effectiveContribution: 0,
      source: definition.source,
      reason,
    })),
    reasons: [reason],
  };
}

function metaStrategyFeature(
  definition: MetaStrategyDefinition,
  market: ConfidenceMarket,
  ensembleVotes: Map<string, AlgoVote>,
): MetaStrategyFeature {
  const ensembleVote = ensembleVotes.get(definition.name);
  if (definition.source === "ensemble" && ensembleVote) {
    const confidence = isEligibleStrategyVote(ensembleVote) ? clampNumber((ensembleVote.score ?? 0) / 100, 0, 1) : 0;
    const signal = confidenceContractSignal(confidence > 0 ? ensembleVote.signal : "Hold");
    const direction = confidenceSignalDirection(signal);
    const contribution = roundNumber(direction * confidence * metaRoleWeight(definition.role), 4);
    return {
      name: definition.name,
      role: definition.role,
      family: definition.family,
      signal,
      confidence,
      direction,
      contribution,
      effectiveContribution: contribution,
      source: "Voting Ensemble",
      reason: ensembleVote.detail,
    };
  }

  const sourceName = definition.alias ?? definition.name;
  const confidenceStrategy = regimeSelectionStrategies.find((strategy) => strategy.name === sourceName);
  if (!confidenceStrategy) {
    return {
      name: definition.name,
      role: definition.role,
      family: definition.family,
      signal: "hold",
      confidence: 0,
      direction: 0,
      contribution: 0,
      effectiveContribution: 0,
      source: definition.source,
      reason: "No isolated read-only source is available",
    };
  }

  const raw = confidenceStrategy.signal(market);
  const signal = confidenceContractSignal(raw.signal);
  const confidence = clampNumber(raw.confidence, 0, 1);
  const direction = confidenceSignalDirection(signal);
  const contribution = roundNumber(direction * confidence * metaRoleWeight(definition.role), 4);
  return {
    name: definition.name,
    role: definition.role,
    family: definition.family,
    signal,
    confidence,
    direction,
    contribution,
    effectiveContribution: contribution,
    source: definition.alias ? `${sourceName} copy` : "WCA/Regime output",
    reason: raw.reason,
  };
}

function metaRoleWeight(role: MetaStrategyRole) {
  return role === "directional" ? 1 : role === "context" ? 0.35 : 0.2;
}

function metaStrategyFamilies(): MetaStrategyFamily[] {
  return ["trend", "breakout", "mean_reversion", "reversal", "volume_confirmation", "market_regime", "vwap", "event", "safety"];
}

function metaFamilyUsesDisplayScore(family: MetaStrategyFamily) {
  return family === "volume_confirmation" || family === "market_regime" || family === "event" || family === "safety";
}

function emptyMetaFamilyScores(): Record<MetaStrategyFamily, { buy: number; sell: number; hold: number; capped: boolean }> {
  return Object.fromEntries(metaStrategyFamilies().map((family) => [family, { buy: 0, sell: 0, hold: 0, capped: false }])) as Record<
    MetaStrategyFamily,
    { buy: number; sell: number; hold: number; capped: boolean }
  >;
}

function emptyMetaFamilyAggregationScores(): MetaFamilyAggregationScores {
  return {
    trend_buy_score: 0,
    trend_sell_score: 0,
    breakout_buy_score: 0,
    breakout_sell_score: 0,
    mean_reversion_buy_score: 0,
    mean_reversion_sell_score: 0,
    reversal_buy_score: 0,
    reversal_sell_score: 0,
    confirmation_score: 0,
    regime_score: 0,
  };
}

function metaFamilyDisplayScores(features: MetaStrategyFeature[], aggregation: MetaFamilyAggregationScores): Partial<Record<MetaStrategyFamily, MetaFamilyDisplayScore>> {
  const signedAverage = (family: MetaStrategyFamily) => {
    const familyFeatures = features.filter((feature) => feature.family === family && feature.role !== "directional");
    if (!familyFeatures.length) {
      return 0;
    }
    return roundNumber(familyFeatures.reduce((sum, feature) => sum + feature.direction * feature.confidence, 0) / familyFeatures.length, 4);
  };
  const safetyFeature = features.find((feature) => feature.family === "safety" && feature.name === "Cash / Avoid Trading Filter");
  return {
    volume_confirmation: { label: "Confirm", value: signedAverage("volume_confirmation") },
    market_regime: { label: "Regime", value: aggregation.regime_score },
    event: { label: "Event", value: signedAverage("event") },
    safety: { label: "Gate", value: safetyFeature ? roundNumber(safetyFeature.confidence, 4) : 0 },
  };
}

function metaFamilyAggregationScores(
  families: Record<MetaStrategyFamily, { buy: number; sell: number; hold: number; capped: boolean }>,
  contextFeatures: MetaStrategyFeature[],
): MetaFamilyAggregationScores {
  const signedAverage = (features: MetaStrategyFeature[]) => {
    if (!features.length) {
      return 0;
    }
    return roundNumber(features.reduce((sum, feature) => sum + feature.direction * feature.confidence, 0) / features.length, 4);
  };
  return {
    trend_buy_score: roundNumber(families.trend.buy, 4),
    trend_sell_score: roundNumber(families.trend.sell, 4),
    breakout_buy_score: roundNumber(families.breakout.buy, 4),
    breakout_sell_score: roundNumber(families.breakout.sell, 4),
    mean_reversion_buy_score: roundNumber(families.mean_reversion.buy, 4),
    mean_reversion_sell_score: roundNumber(families.mean_reversion.sell, 4),
    reversal_buy_score: roundNumber(families.reversal.buy, 4),
    reversal_sell_score: roundNumber(families.reversal.sell, 4),
    confirmation_score: signedAverage(contextFeatures),
    regime_score: signedAverage(contextFeatures.filter((feature) => feature.family === "market_regime")),
  };
}

function metaFamilyLabel(family: MetaStrategyFamily) {
  return family.replaceAll("_", " ");
}

function renderMetaScoreGrid(result: MetaStrategyResult) {
  return [
    ["Buy", result.buyScore.toFixed(2), "buy"],
    ["Sell", result.sellScore.toFixed(2), "sell"],
    ["Edge", result.edge.toFixed(2), result.signal.toLowerCase()],
    ["Confirm", result.familyAggregation.confirmation_score.toFixed(2), result.familyAggregation.confirmation_score >= 0 ? "buy" : "sell"],
    ["Regime", result.familyAggregation.regime_score.toFixed(2), result.familyAggregation.regime_score >= 0 ? "buy" : "sell"],
  ]
    .map(
      ([label, value, tone]) => `
        <div class="weighted-score-card ${tone}">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
        </div>
      `,
    )
    .join("");
}

function renderMetaSummary(result: MetaStrategyResult) {
  return result.reasons.map((reason) => `<span>${escapeHtml(reason)}</span>`).join("");
}

function renderMetaDecisionChecks(result: MetaStrategyResult) {
  const summary = result.reasons.length
    ? `
      <span class="meta-check-summary" data-status="info">
        <b>Decision summary</b>
        ${result.reasons.map((reason) => `<em>${escapeHtml(reason)}</em>`).join("")}
      </span>
    `
    : "";
  return `${summary}${result.safetyGates.map(renderWeightedGate).join("")}`;
}

function renderMetaFamilyGrid(result: MetaStrategyResult) {
  return metaStrategyFamilies()
    .map((family) => {
      const score = result.familyScores[family];
      const total = score.buy + score.sell + score.hold;
      const displayScore = result.familyDisplayScores[family];
      const usesDisplayScore = metaFamilyUsesDisplayScore(family) && Boolean(displayScore);
      const buyShare = total > 0 ? score.buy / total : 0;
      const sellShare = total > 0 ? score.sell / total : 0;
      const holdShare = total > 0 ? score.hold / total : 0;
      const dominant =
        usesDisplayScore && displayScore
          ? `${displayScore.label} ${displayScore.value >= 0 ? "+" : ""}${displayScore.value.toFixed(2)}`
          : total <= 0
          ? "No signal"
          : buyShare >= sellShare && buyShare >= holdShare
            ? "Buy"
            : sellShare >= buyShare && sellShare >= holdShare
              ? "Sell"
              : "Hold";
      const tone =
        usesDisplayScore && displayScore
          ? displayScore.value > 0
            ? "buy"
            : displayScore.value < 0
              ? "sell"
              : "empty"
          : dominant === "Buy"
            ? "buy"
            : dominant === "Sell"
              ? "sell"
              : dominant === "Hold"
                ? "hold"
                : "empty";
      const normalizedNet = total > 0 ? (score.buy - score.sell) / total : 0;
      const capLabel = score.capped ? "Capped" : "Open";
      const rawTitle = `Raw ${metaFamilyLabel(family)} score: buy ${score.buy.toFixed(2)}, sell ${score.sell.toFixed(2)}, hold ${score.hold.toFixed(2)}`;
      const detailLine =
        usesDisplayScore && displayScore
          ? `${displayScore.label} score ${displayScore.value >= 0 ? "+" : ""}${displayScore.value.toFixed(2)}`
          : `Net ${normalizedNet >= 0 ? "+" : ""}${formatProbability(normalizedNet)} - ${capLabel}`;
      return `
        <div class="meta-family-card" data-tone="${tone}" data-capped="${String(score.capped)}" title="${escapeHtml(rawTitle)}">
          <div class="meta-family-head">
            <span>${escapeHtml(metaFamilyLabel(family))}</span>
            <strong>${escapeHtml(dominant)}</strong>
          </div>
          <div class="meta-family-bar" aria-label="${escapeHtml(rawTitle)}">
            <i class="buy" style="width:${roundNumber(buyShare * 100, 2)}%"></i>
            <i class="sell" style="width:${roundNumber(sellShare * 100, 2)}%"></i>
            <i class="hold" style="width:${roundNumber(holdShare * 100, 2)}%"></i>
          </div>
          <div class="meta-family-values">
            <span><b>B</b>${formatProbability(buyShare)}</span>
            <span><b>S</b>${formatProbability(sellShare)}</span>
            <span><b>H</b>${formatProbability(holdShare)}</span>
          </div>
          <small>${escapeHtml(detailLine)}</small>
        </div>
      `;
    })
    .join("");
}

function renderMetaStrategies(strategies: MetaStrategyFeature[]) {
  return `
    <table class="weighted-strategy-table confidence-strategy-table">
      <colgroup>
        <col class="confidence-strategy-metric-col" />
        <col class="confidence-strategy-metric-col" />
        <col class="confidence-strategy-metric-col" />
        <col class="confidence-strategy-metric-col" />
        <col class="confidence-strategy-metric-col" />
        <col class="confidence-strategy-reason-col" />
      </colgroup>
      <tbody>
        ${strategies
          .map(
            (strategy, strategyIndex) => `
              <tr class="weighted-strategy-name-row" data-tone="${strategyIndex % 4}" data-disabled="${String(strategy.signal === "hold")}">
                <td colspan="5">${escapeHtml(strategy.name)}</td>
                <td>${escapeHtml(strategy.signal)}</td>
              </tr>
              <tr class="weighted-strategy-detail-row" data-tone="${strategyIndex % 4}" data-disabled="${String(strategy.signal === "hold")}">
                ${renderWeightedMetricCell("Role", strategy.role.replaceAll("_", " "))}
                ${renderWeightedMetricCell("Family", metaFamilyLabel(strategy.family))}
                ${renderWeightedMetricCell("Confidence", strategy.confidence.toFixed(2))}
                ${renderWeightedMetricCell("Raw", strategy.contribution.toFixed(2))}
                ${renderWeightedMetricCell("Capped", strategy.effectiveContribution.toFixed(2))}
                <td>
                  <small>${escapeHtml(strategy.source)}</small>
                  <strong>${escapeHtml(strategy.reason)}</strong>
                </td>
              </tr>
            `,
          )
          .join("")}
      </tbody>
    </table>
  `;
}

function updateWeightedVotingPanel() {
  const result = calculateWeightedVote();
  weightedFinalSignal.textContent = result.signal;
  weightedFinalSignal.className = `algo-final ${result.signal.toLowerCase()}`;
  weightedScoreGrid.innerHTML = renderWeightedScoreGrid(result);
  weightedSummary.innerHTML = renderWeightedSummary(result);
  updateWeightedTradingSettingsMount(result);
  weightedStrategiesToggleMeta.textContent = `${result.strategies.filter((strategy) => strategy.finalWeight > 0 && !strategy.disabledReason).length} active / ${result.strategies.length} strategies`;
  weightedStrategiesList.innerHTML = renderWeightedStrategies(result.strategies);
  weightedDataToggleMeta.textContent = `${result.data.timeframes.fiveMinute.direction.toUpperCase()} 5m / ${result.data.trendDirection.toUpperCase()} 1m`;
  weightedDataGrid.innerHTML = renderWeightedDataGrid(result);
  const failedGates = result.gates.filter((gate) => gate.status === "fail").length;
  const infoGates = result.gates.filter((gate) => gate.status === "info").length;
  weightedGatesToggleMeta.textContent = `${failedGates} fail / ${infoGates} info`;
  weightedGateList.innerHTML = result.gates.map(renderWeightedGate).join("");
  weightedControlRules.innerHTML = renderWeightedControlRules();
  renderWeightedStrategiesExpandedState();
  renderWeightedDataExpandedState();
  renderWeightedGatesExpandedState();
  renderWeightedControlsExpandedState();
  maybeAutoSubmitWeightedTargetOrder();
}

function calculateWeightedVote(): WeightedVoteResult {
  const sessionCandles = latestWeightedCalculationCandles();
  const candles = sessionCandles.length ? sessionCandles : state.candles.slice(-240);
  const latest = candles.at(-1) ?? null;
  if (!latest || candles.length < 20) {
    return emptyWeightedVote(latest);
  }

  const closes = candles.map((candle) => candle.close);
  const vwap = sessionVwapValue(candles);
  const atr = averageTrueRange(candles, Math.min(14, candles.length - 1));
  const bands = bollingerBands(closes, 20, 2);
  const openingRange = openingRangeValues(candles, Math.min(15, candles.length));
  const priorRange = candles.slice(-21, -1);
  const priorHigh = priorRange.length ? Math.max(...priorRange.map((candle) => candle.high)) : latest.high;
  const priorLow = priorRange.length ? Math.min(...priorRange.map((candle) => candle.low)) : latest.low;
  const averageVolume = simpleMovingAverage(candles.map((candle) => candle.volume), Math.min(20, candles.length)) ?? latest.volume;
  const sma20 = simpleMovingAverage(closes, 20);
  const sma50 = simpleMovingAverage(closes, 50);
  const relativeStrengthScore = weightedRelativeStrengthScore(candles);
  const breadthScore = weightedBreadthScore(candles);
  const relativeStrength = relativeStrengthScore.value;
  const breadth = breadthScore.value;
  const eventRisk = 0;
  const volatilityPercent = atr && latest.close ? atr / latest.close : 0;
  const minutes = easternMinutes(latest.timestamp);
  const regime = weightedMarketRegime({
    candles,
    closes,
    latest,
    vwap,
    atr,
    bands,
    sma20,
    sma50,
    volatilityPercent,
    minutes,
  });
  const timeframes = weightedTimeframeContext(candles);
  const market = {
    candles,
    latest,
    closes,
    vwap,
    atr,
    bands,
    openingRange,
    priorHigh,
    priorLow,
    averageVolume,
    sma20,
    sma50,
    relativeStrength,
    breadth,
    eventRisk,
    volatilityPercent,
    trendDirection: regime.trendDirection,
    trendSource: regime.trendSource,
    volatilityLevel: regime.volatilityLevel,
    rangeCondition: regime.rangeCondition,
    sessionLabel: regime.sessionLabel,
    timeframes,
    minutes,
  };

  const storedWeightState = loadWeightedVotingWeightState();
  const oldWeights = storedWeightState.weights;
  const previewSignals = weightedAlphaStrategies.map((strategy) => weightedAlphaSignal(strategy, market));
  updateWeightedStrategyPerformanceFromSignals(previewSignals, market);
  const alphaSignals = weightedAlphaStrategies.map((strategy) => weightedAlphaSignal(strategy, market));
  const strengths = alphaSignals.map((strategy) => Math.max(0, strategy.strength));
  const strengthSum = strengths.reduce((sum, value) => sum + value, 0) || 1;
  const withBaseWeights = alphaSignals.map((strategy, index) => ({
    ...strategy,
    baseWeight: strategy.disabledReason ? 0 : strengths[index] / strengthSum,
  }));
  const withMultipliers = withBaseWeights.map((strategy) => {
    const multipliers = weightedFilterMultipliers(strategy, withBaseWeights, market);
    const multiplierValue = Object.values(multipliers).reduce((product, value) => product * value, 1);
    return { ...strategy, multipliers, adjustedWeight: strategy.disabledReason ? 0 : strategy.baseWeight * multiplierValue * strategy.sampleSizeScore };
  });
  const adjustedWeights = normalizeWeightedValues(Object.fromEntries(withMultipliers.map((strategy) => [strategy.key, strategy.adjustedWeight])) as Record<WeightedAlphaKey, number>);
  const cappedRecalculated = capNormalizeWeights(adjustedWeights, withMultipliers);
  const smoothedWeights = capNormalizeWeights(
    Object.fromEntries(
      weightedAlphaStrategies.map((strategy) => [
        strategy.key,
        0.7 * (oldWeights[strategy.key] ?? weightedVotingDefaultWeights[strategy.key]) + 0.3 * cappedRecalculated[strategy.key],
      ]),
    ) as Record<WeightedAlphaKey, number>,
    withMultipliers,
  );
  const activeWeights = resolveWeightedVotingActiveWeights(storedWeightState, smoothedWeights, withMultipliers, latest);
  const strategies = withMultipliers.map((strategy) => ({
    ...strategy,
    recalculatedWeight: cappedRecalculated[strategy.key],
    smoothedWeight: smoothedWeights[strategy.key],
    finalWeight: activeWeights[strategy.key],
  }));

  const buyScore = roundNumber(strategies.reduce((sum, strategy) => sum + strategy.finalWeight * strategy.pBuy, 0), 4);
  const sellScore = roundNumber(strategies.reduce((sum, strategy) => sum + strategy.finalWeight * strategy.pSell, 0), 4);
  const holdScore = roundNumber(strategies.reduce((sum, strategy) => sum + strategy.finalWeight * strategy.pHold, 0), 4);
  const sortedScores = [
    { signal: "Buy" as AlgoSignal, score: buyScore },
    { signal: "Sell" as AlgoSignal, score: sellScore },
    { signal: "Hold" as AlgoSignal, score: holdScore },
  ].sort((left, right) => right.score - left.score);
  const rawWinner = sortedScores[0].signal;
  const maxScore = sortedScores[0].score;
  const margin = roundNumber(sortedScores[0].score - sortedScores[1].score, 4);
  const weightedDefaults = weightedDefaultSizingSettings();
  const activeStrategyCount = strategies.filter((strategy) => strategy.finalWeight > 0 && !strategy.disabledReason).length;
  const buyStrategyCount = strategies.filter((strategy) => strategy.finalWeight > 0 && strategy.pBuy > strategy.pSell && strategy.pBuy > strategy.pHold).length;
  const sellStrategyCount = strategies.filter((strategy) => strategy.finalWeight > 0 && strategy.pSell > strategy.pBuy && strategy.pSell > strategy.pHold).length;
  const directionalStrategyCount = rawWinner === "Buy" ? buyStrategyCount : rawWinner === "Sell" ? sellStrategyCount : 0;
  const expectedReturnAfterCosts = roundNumber(strategies.reduce((sum, strategy) => sum + strategy.finalWeight * strategy.expectedReturnAfterCosts, 0), 5);
  const tripleBarrier = weightedTripleBarrierFilter(rawWinner, expectedReturnAfterCosts, market, strategies);
  const metaLabelProbability = weightedMetaLabelProbability(rawWinner, maxScore, margin, tripleBarrier, timeframes);
  const confidenceMultiplier = maxScore < weightedDefaults.minimumBuyScore || margin < weightedDefaults.minimumSignalEdge ? 0 : Math.min(1.4, 0.7 + maxScore + margin);
  const volatilityAdjustment = atr && latest.close ? Math.max(0.35, Math.min(1.2, 1 - (atr / latest.close) * 18)) : 0.75;
  const baseSize = state.weightedTradingSettings.startingCapital * (weightedDefaults.maxPositionPercent / 100);
  const positionSize = roundNumber(baseSize * confidenceMultiplier * metaLabelProbability * volatilityAdjustment, 2);
  const dailyLoss = weightedDailyLossStatus(latest.close);
  const fiveMinuteGate = weightedFiveMinuteConfirmationGate(rawWinner, maxScore, margin, timeframes.fiveMinute);
  const spreadPercent = latest.close ? ((state.weightedTradingSettings.slippagePerShare * 2) / latest.close) * 100 : 0;
  const weightedTradesToday = effectiveTodaysTradeCount("weighted", weightedDefaults.maxDailyTrades, latest.close);
  const weightedOpenPosition = openOrderLots("weighted").some((lot) => lot.symbol === state.symbol);

  const gates = [
    {
      label: "Confidence",
      status: maxScore >= weightedDefaults.minimumBuyScore && margin >= weightedDefaults.minimumSignalEdge ? "pass" : "fail",
      detail: `Max ${formatProbability(maxScore)}, margin ${formatProbability(margin)}`,
    },
    {
      label: "Active Strategies",
      status: activeStrategyCount >= weightedDefaults.minimumActiveStrategies ? "pass" : "fail",
      detail: `${activeStrategyCount}/${weightedDefaults.minimumActiveStrategies} active`,
    },
    {
      label: "Directional Strategy Count",
      status: rawWinner === "Hold" || directionalStrategyCount >= weightedDefaults.minimumBuyStrategyCount ? "pass" : "fail",
      detail: rawWinner === "Hold"
        ? "Hold does not need directional strategy count"
        : `${directionalStrategyCount}/${weightedDefaults.minimumBuyStrategyCount} ${rawWinner.toLowerCase()}-leaning strategies`,
    },
    {
      label: "5m Confirmation",
      status: fiveMinuteGate.status,
      detail: fiveMinuteGate.detail,
    },
    {
      label: "Expected Value",
      status: expectedReturnAfterCosts > 0 ? "pass" : "fail",
      detail: `${formatBasisPoints(expectedReturnAfterCosts)} after estimated costs`,
    },
    {
      label: "Meta Label",
      status: metaLabelProbability >= 0.6 ? "pass" : "fail",
      detail: `${formatProbability(metaLabelProbability)} probability`,
    },
    {
      label: "Triple Barrier",
      status: tripleBarrier.passed ? "pass" : "fail",
      detail: tripleBarrier.detail,
    },
    {
      label: "Daily Loss",
      status: dailyLoss.limitReached ? "fail" : "pass",
      detail: `${signedCurrency(dailyLoss.pnl)} today, limit ${currency(dailyLoss.limit)}`,
    },
    {
      label: "Spread/Slippage",
      status: spreadPercent <= weightedDefaults.maxSpreadPercent ? "pass" : "fail",
      detail: `${formatProbability(spreadPercent / 100)} round-trip estimate, max ${weightedDefaults.maxSpreadPercent}%`,
    },
    {
      label: "1m Volume",
      status: latest.volume >= weightedDefaults.minimumOneMinuteVolume ? "pass" : "fail",
      detail: `${latest.volume.toLocaleString()} / ${weightedDefaults.minimumOneMinuteVolume.toLocaleString()} shares`,
    },
    {
      label: "Max Daily Trades",
      status: weightedTradesToday < weightedDefaults.maxDailyTrades ? "pass" : "fail",
      detail: dailyTradeCountDetail("weighted", weightedDefaults.maxDailyTrades, latest.close),
    },
    {
      label: "Pyramiding",
      status: rawWinner !== "Buy" || weightedDefaults.pyramidingEnabled || !weightedOpenPosition ? "pass" : "fail",
      detail: weightedDefaults.pyramidingEnabled ? "Additional entries allowed" : "Additional Buy blocked while position is open",
    },
  ] satisfies WeightedVoteResult["gates"];

  const finalSignal = gates.some((gate) => gate.status === "fail") ? "Hold" : rawWinner;
  return {
    signal: finalSignal,
    rawWinner,
    buyScore,
    sellScore,
    holdScore,
    maxScore,
    margin,
    expectedReturnAfterCosts,
    metaLabelProbability,
    tripleBarrier,
    positionSize,
    confidenceMultiplier,
    volatilityAdjustment,
    gates,
    strategies,
    data: {
      latest,
      vwap,
      atr,
      bollinger: bands,
      averageVolume,
      relativeStrength,
      relativeStrengthSource: relativeStrengthScore.source,
      breadth,
      breadthSource: breadthScore.source,
      eventRisk,
      eventRiskSource: "Removed from short-cycle mode",
      trendDirection: regime.trendDirection,
      trendSource: regime.trendSource,
      volatilityLevel: regime.volatilityLevel,
      rangeCondition: regime.rangeCondition,
      sessionLabel: regime.sessionLabel,
      timeframes,
      weightSource: storedWeightState.source,
      weightSeededAt: storedWeightState.seededAt,
      updatePolicy: "Weights update once per day after the close; live candles only update probabilities and gates.",
    },
  };
}

function latestWeightedCalculationCandles() {
  const weightedOneMinute = state.weightedMarketData.timeframeCandles["1Min"] ?? [];
  const weightedSessionCandles = weightedOneMinute.length ? latestRegularSessionCandlesFrom(weightedOneMinute) : [];
  const chartSessionCandles = latestRegularSessionCandles();
  const base = weightedSessionCandles.length ? weightedSessionCandles : chartSessionCandles;
  const chartLatest = chartSessionCandles.at(-1);
  const baseLatest = base.at(-1);
  if (
    chartLatest &&
    (!baseLatest || (
      easternDateString(chartLatest.timestamp) === easternDateString(baseLatest.timestamp) &&
      new Date(chartLatest.timestamp).getTime() > new Date(baseLatest.timestamp).getTime()
    ))
  ) {
    return [...base.filter((candle) => candle.timestamp !== chartLatest.timestamp), chartLatest]
      .sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime());
  }
  return base;
}

function emptyWeightedVote(latest: Candle | null): WeightedVoteResult {
  return {
    signal: "Hold",
    rawWinner: "Hold",
    buyScore: 0,
    sellScore: 0,
    holdScore: 1,
    maxScore: 1,
    margin: 1,
    expectedReturnAfterCosts: 0,
    metaLabelProbability: 0,
    tripleBarrier: emptyWeightedTripleBarrier(),
    positionSize: 0,
    confidenceMultiplier: 0,
    volatilityAdjustment: 0,
    gates: weightedWaitingGates(),
    strategies: weightedAlphaStrategies.map((strategy) => ({
      ...strategy,
      pBuy: 0,
      pSell: 0,
      pHold: 1,
      expectedReturn: 0,
      expectedReturnAfterCosts: 0,
      signalQuality: 0,
      strength: 0,
      tradeCount: weightedStrategyTradeCount(strategy.key),
      sampleSizeScore: weightedStrategySampleSizeScore(weightedStrategyTradeCount(strategy.key)),
      recentExpectancy: weightedStrategyRecentExpectancy(strategy.key),
      recentDrawdown: weightedStrategyRecentDrawdown(strategy.key),
      drawdownScore: weightedStrategyDrawdownScore(weightedStrategyRecentDrawdown(strategy.key)),
      baseWeight: 0,
      adjustedWeight: 0,
      finalWeight: weightedVotingDefaultWeights[strategy.key],
      smoothedWeight: weightedVotingDefaultWeights[strategy.key],
      recalculatedWeight: weightedVotingDefaultWeights[strategy.key],
      multipliers: {},
      disabledReason: "Waiting for data",
      detail: "Insufficient candles",
    })),
    data: {
      latest,
      vwap: null,
      atr: null,
      bollinger: null,
      averageVolume: 0,
      relativeStrength: 0,
      relativeStrengthSource: "Waiting for QQQ/IWM candles",
      breadth: 0,
      breadthSource: "Waiting for sector ETF candles",
      eventRisk: 0,
      eventRiskSource: "Removed from short-cycle mode",
      trendDirection: "neutral",
      trendSource: "Waiting for at least 20 one-minute candles",
      volatilityLevel: "unknown",
      rangeCondition: "unknown",
      sessionLabel: "Waiting",
      timeframes: emptyWeightedTimeframeContext(),
      weightSource: "default",
      updatePolicy: "Weights update once per day after the close; live candles only update probabilities and gates.",
    },
  };
}

function weightedWaitingGates(): WeightedVoteResult["gates"] {
  return [
    { label: "Data", status: "fail", detail: "Waiting for at least 20 one-minute candles" },
    { label: "Confidence", status: "info", detail: "Waiting for weighted Buy/Sell/Hold scores" },
    { label: "5m Confirmation", status: "info", detail: "Waiting for a directional 1m setup" },
    { label: "Expected Value", status: "info", detail: "Waiting for strategy probabilities and costs" },
    { label: "Meta Label", status: "info", detail: "Waiting for final weighted signal" },
    { label: "Triple Barrier", status: "info", detail: "Waiting for target/stop/time-barrier setup" },
    { label: "Daily Loss", status: "info", detail: "Waiting for latest tradable price" },
    { label: "Spread/Slippage", status: "info", detail: "Waiting for latest price and slippage estimate" },
  ];
}

function weightedAlphaSignal(strategy: WeightedAlphaStrategy, market: ReturnType<typeof weightedMarketType>): WeightedAlphaResult {
  const latest = market.latest;
  const volumeExpansion = latest.volume > market.averageVolume * 1.15;
  const failedHigh = latest.high > market.priorHigh && latest.close < market.priorHigh;
  const failedLow = latest.low < market.priorLow && latest.close > market.priorLow;
  const aboveVwap = latest.close > market.vwap;
  const belowVwap = latest.close < market.vwap;
  const trendUp = market.sma20 !== null && market.sma50 !== null && market.sma20 > market.sma50 && aboveVwap;
  const trendDown = market.sma20 !== null && market.sma50 !== null && market.sma20 < market.sma50 && belowVwap;
  const distanceFromVwap = (latest.close - market.vwap) / Math.max(market.vwap, 0.01);
  const atrPercent = market.atr && latest.close ? market.atr / latest.close : 0.0035;
  const costEstimate = latest.close ? (state.weightedTradingSettings.slippagePerShare * 2 + 0.02) / latest.close : 0.0001;
  const recentMomentum = market.closes.length > 8 ? (latest.close - market.closes[Math.max(0, market.closes.length - 9)]) / Math.max(market.closes[Math.max(0, market.closes.length - 9)], 0.01) : 0;
  let pBuy = 0.18;
  let pSell = 0.18;
  let pHold = 0.64;
  let detail = "No active setup";
  let setupActive = false;

  if (strategy.key === "S1") {
    if (latest.close > market.openingRange.high && volumeExpansion) {
      pBuy = 0.66;
      pSell = 0.08;
      pHold = 0.26;
      detail = "Opening range high broke with volume expansion";
      setupActive = true;
    } else if (latest.close < market.openingRange.low && volumeExpansion) {
      pBuy = 0.08;
      pSell = 0.66;
      pHold = 0.26;
      detail = "Opening range low broke with volume expansion";
      setupActive = true;
    }
  } else if (strategy.key === "S2") {
    const nearSma20 = market.sma20 !== null && Math.abs(latest.close - market.sma20) / latest.close < 0.0025;
    if (trendUp && nearSma20) {
      pBuy = 0.63;
      pSell = 0.1;
      pHold = 0.27;
      detail = "Trend pullback is holding near 20 SMA";
      setupActive = true;
    } else if (trendDown && nearSma20) {
      pBuy = 0.1;
      pSell = 0.63;
      pHold = 0.27;
      detail = "Downtrend pullback is rejecting near 20 SMA";
      setupActive = true;
    }
  } else if (strategy.key === "S3") {
    if (trendUp && latest.close > market.openingRange.high) {
      pBuy = 0.68;
      pSell = 0.07;
      pHold = 0.25;
      detail = "VWAP and trend continuation agree above opening range";
      setupActive = true;
    } else if (trendUp && latest.close > market.vwap && recentMomentum > 0.001) {
      pBuy = 0.64;
      pSell = 0.09;
      pHold = 0.27;
      detail = "Bullish intraday continuation above VWAP";
      setupActive = true;
    } else if (trendDown && latest.close < market.openingRange.low) {
      pBuy = 0.07;
      pSell = 0.68;
      pHold = 0.25;
      detail = "VWAP and trend continuation agree below opening range";
      setupActive = true;
    } else if (trendDown && latest.close < market.vwap && recentMomentum < -0.001) {
      pBuy = 0.09;
      pSell = 0.64;
      pHold = 0.27;
      detail = "Bearish intraday continuation below VWAP";
      setupActive = true;
    }
  } else if (strategy.key === "S4") {
    if (distanceFromVwap < -0.003 && market.rangeCondition.toLowerCase().includes("chop")) {
      pBuy = 0.61;
      pSell = 0.13;
      pHold = 0.26;
      detail = "Price is stretched below VWAP during choppy trade";
      setupActive = true;
    } else if (distanceFromVwap > 0.003 && market.rangeCondition.toLowerCase().includes("chop")) {
      pBuy = 0.13;
      pSell = 0.61;
      pHold = 0.26;
      detail = "Price is stretched above VWAP during choppy trade";
      setupActive = true;
    }
  } else if (strategy.key === "S5") {
    if (failedHigh) {
      pBuy = 0.08;
      pSell = 0.7;
      pHold = 0.22;
      detail = "Prior high breakout failed back below range";
      setupActive = true;
    } else if (failedLow) {
      pBuy = 0.7;
      pSell = 0.08;
      pHold = 0.22;
      detail = "Prior low breakdown failed back above range";
      setupActive = true;
    }
  } else if (strategy.key === "S6") {
    if (failedHigh && volumeExpansion) {
      pBuy = 0.07;
      pSell = 0.72;
      pHold = 0.21;
      detail = "High-side liquidity sweep with expanded volume";
      setupActive = true;
    } else if (failedLow && volumeExpansion) {
      pBuy = 0.72;
      pSell = 0.07;
      pHold = 0.21;
      detail = "Low-side liquidity sweep with expanded volume";
      setupActive = true;
    }
  } else if (strategy.key === "S7") {
    if (market.bands && market.atr && latest.close < market.bands.lower - market.atr * 0.15) {
      pBuy = 0.64;
      pSell = 0.11;
      pHold = 0.25;
      detail = "Bollinger and ATR downside extension";
      setupActive = true;
    } else if (market.bands && market.atr && latest.close > market.bands.upper + market.atr * 0.15) {
      pBuy = 0.11;
      pSell = 0.64;
      pHold = 0.25;
      detail = "Bollinger and ATR upside extension";
      setupActive = true;
    }
  } else if (strategy.key === "S8") {
    if (market.volatilityLevel === "high" && volumeExpansion && latest.close > market.priorHigh) {
      pBuy = 0.65;
      pSell = 0.09;
      pHold = 0.26;
      detail = "Volatility expansion broke above prior range";
      setupActive = true;
    } else if (market.volatilityLevel === "high" && volumeExpansion && latest.close < market.priorLow) {
      pBuy = 0.09;
      pSell = 0.65;
      pHold = 0.26;
      detail = "Volatility expansion broke below prior range";
      setupActive = true;
    }
  }

  const maxDirectional = Math.max(pBuy, pSell);
  const signalQuality = Math.max(0, maxDirectional - pHold * 0.35);
  const directionalSide = pBuy > pSell && pBuy > pHold ? "Buy" : pSell > pBuy && pSell > pHold ? "Sell" : "Hold";
  const directionalEdge =
    directionalSide === "Buy"
      ? pBuy - Math.max(pSell, pHold)
      : directionalSide === "Sell"
        ? pSell - Math.max(pBuy, pHold)
        : 0;
  const expectedReturn = Math.max(0, directionalEdge) * Math.max(atrPercent, 0.0015) * 0.6;
  const expectedReturnAfterCosts = expectedReturn - costEstimate;
  const tradeCount = weightedStrategyTradeCount(strategy.key);
  const liveOutcomeCount = weightedStrategyLiveOutcomeCount(strategy.key);
  const sampleSizeScore = weightedStrategySampleSizeScore(tradeCount);
  const recentExpectancy = weightedStrategyRecentExpectancy(strategy.key);
  const recentDrawdown = weightedStrategyRecentDrawdown(strategy.key);
  const drawdownScore = weightedStrategyDrawdownScore(recentDrawdown);
  const components = weightedStrengthComponents(strategy, signalQuality, expectedReturnAfterCosts, market, sampleSizeScore, drawdownScore);
  const strength = roundNumber(
    (0.25 * components.profitFactor +
      0.2 * components.expectancy +
      0.15 * components.precision +
      0.15 * components.drawdown +
      0.1 * components.recentPerformance +
      0.1 * components.regimeMatch +
      0.05 * components.sampleSize) * (setupActive ? 1 : 0.35),
    4,
  );
  const disabledReason =
    liveOutcomeCount >= weightedMinimumTradesForFullWeight && recentExpectancy !== null && recentExpectancy < 0
      ? "Disabled: live triple-barrier expectancy negative"
      : "";
  return {
    ...strategy,
    pBuy,
    pSell,
    pHold,
    expectedReturn,
    expectedReturnAfterCosts,
    signalQuality,
    strength,
    tradeCount,
    sampleSizeScore,
    recentExpectancy,
    recentDrawdown,
    drawdownScore,
    baseWeight: 0,
    adjustedWeight: 0,
    finalWeight: 0,
    smoothedWeight: 0,
    recalculatedWeight: 0,
    multipliers: {},
    disabledReason,
    detail,
  };
}

function weightedMarketType() {
  return {
    candles: [] as Candle[],
    latest: {} as Candle,
    closes: [] as number[],
    vwap: 0,
    atr: null as number | null,
    bands: null as ReturnType<typeof bollingerBands>,
    openingRange: { high: 0, low: 0 },
    priorHigh: 0,
    priorLow: 0,
    averageVolume: 0,
    sma20: null as number | null,
    sma50: null as number | null,
    relativeStrength: 0,
    breadth: 0,
    eventRisk: 0,
    volatilityPercent: 0,
    trendDirection: "neutral" as "up" | "down" | "neutral",
    trendSource: "",
    volatilityLevel: "",
    rangeCondition: "",
    sessionLabel: "",
    timeframes: emptyWeightedTimeframeContext(),
    minutes: 0,
  };
}

function emptyWeightedTimeframeContext(): WeightedTimeframeContext {
  return {
    fiveMinute: { direction: "neutral", score: 0, source: "5m confirmation: unavailable" },
  };
}

function weightedMarketRegime(input: {
  candles: Candle[];
  closes: number[];
  latest: Candle;
  vwap: number;
  atr: number | null;
  bands: ReturnType<typeof bollingerBands>;
  sma20: number | null;
  sma50: number | null;
  volatilityPercent: number;
  minutes: number;
}) {
  const latest = input.latest;
  const lookbackClose = input.closes[Math.max(0, input.closes.length - 11)] ?? latest.close;
  const tenBarReturn = lookbackClose ? (latest.close - lookbackClose) / lookbackClose : 0;
  const vwapDistance = (latest.close - input.vwap) / Math.max(input.vwap, 0.01);
  const smaTrend =
    input.sma20 !== null && input.sma50 !== null
      ? input.sma20 > input.sma50 && latest.close > input.sma20 && latest.close > input.vwap
        ? 1
        : input.sma20 < input.sma50 && latest.close < input.sma20 && latest.close < input.vwap
          ? -1
          : 0
      : 0;
  const momentumTrend = tenBarReturn > 0.0015 && vwapDistance > 0.0008 ? 1 : tenBarReturn < -0.0015 && vwapDistance < -0.0008 ? -1 : 0;
  const trendScore = smaTrend + momentumTrend;
  const trendDirection: "up" | "down" | "neutral" = trendScore > 0 ? "up" : trendScore < 0 ? "down" : "neutral";
  const trendSource =
    trendDirection === "up"
      ? "20/50 SMA, VWAP, and 10-bar momentum bullish"
      : trendDirection === "down"
        ? "20/50 SMA, VWAP, and 10-bar momentum bearish"
        : "mixed SMA/VWAP/momentum";
  const volatilityLevel =
    input.volatilityPercent > 0.008 ? "high" : input.volatilityPercent > 0.004 ? "normal" : input.volatilityPercent > 0 ? "compressed" : "unknown";
  const bandWidth = input.bands && latest.close ? (input.bands.upper - input.bands.lower) / latest.close : 0;
  const recentHigh = Math.max(...input.candles.slice(-20).map((candle) => candle.high));
  const recentLow = Math.min(...input.candles.slice(-20).map((candle) => candle.low));
  const recentRangePercent = latest.close ? (recentHigh - recentLow) / latest.close : 0;
  const isChop = Math.abs(vwapDistance) < 0.0015 && Math.abs(tenBarReturn) < 0.0018 && bandWidth < 0.012;
  const isRange = recentRangePercent < Math.max(0.004, input.volatilityPercent * 2.2) && trendDirection === "neutral";
  const rangeCondition = isChop ? "VWAP Chop" : isRange ? "Compressed Range" : trendDirection === "neutral" ? "Mixed/Transition" : "Directional";
  return {
    trendDirection,
    trendSource,
    volatilityLevel,
    rangeCondition,
    sessionLabel: sessionLabelForMinutes(input.minutes),
  };
}

function weightedTimeframeContext(oneMinuteCandles: Candle[]): WeightedTimeframeContext {
  const fiveMinuteCandles =
    state.weightedMarketData.timeframeCandles["5Min"]?.length
      ? state.weightedMarketData.timeframeCandles["5Min"]!
      : aggregateCandlesToMinutes(oneMinuteCandles, 5, "5Min");
  return {
    fiveMinute: weightedDirectionalTimeframeSignal(fiveMinuteCandles, 6, 0.0008, "5m confirmation"),
  };
}

function weightedDirectionalTimeframeSignal(candles: Candle[], lookback: number, threshold: number, label: string): WeightedTimeframeSignal {
  const comparable = candles.filter((candle) => candle.close > 0);
  if (comparable.length < 3) {
    return { direction: "neutral", score: 0, source: `${label}: unavailable` };
  }
  const latest = comparable.at(-1)!;
  const base = comparable[Math.max(0, comparable.length - 1 - lookback)];
  const returns = base.close ? (latest.close - base.close) / base.close : 0;
  const closes = comparable.map((candle) => candle.close);
  const fast = simpleMovingAverage(closes, Math.min(5, closes.length));
  const slow = simpleMovingAverage(closes, Math.min(Math.max(lookback, 8), closes.length));
  const smaBias = fast !== null && slow !== null ? (fast > slow ? 1 : fast < slow ? -1 : 0) : 0;
  const returnBias = returns > threshold ? 1 : returns < -threshold ? -1 : 0;
  const combined = returnBias + smaBias;
  const direction: WeightedTimeframeDirection = combined > 0 ? "up" : combined < 0 ? "down" : "neutral";
  const score = roundNumber(Math.min(1, Math.abs(returns) / Math.max(threshold * 3, 0.0001)) * (smaBias && returnBias && smaBias === returnBias ? 1 : 0.65), 4);
  return {
    direction,
    score,
    source: `${label}: ${direction}, ${formatBasisPoints(returns)} over ${Math.min(lookback, comparable.length - 1)} bars`,
  };
}

function weightedSignalTimeframeAlignment(signal: AlgoSignal, timeframe: WeightedTimeframeSignal) {
  if (signal === "Hold" || timeframe.direction === "neutral") {
    return 0;
  }
  const signalDirection = signal === "Buy" ? "up" : "down";
  return timeframe.direction === signalDirection ? timeframe.score : -timeframe.score;
}

function weightedFiveMinuteConfirmationGate(signal: AlgoSignal, maxScore: number, margin: number, fiveMinute: WeightedTimeframeSignal) {
  if (signal === "Hold") {
    return { status: "pass" as const, detail: "Hold does not need 5m confirmation" };
  }
  if (fiveMinute.direction === "neutral") {
    return { status: "info" as const, detail: fiveMinute.source };
  }
  const alignment = weightedSignalTimeframeAlignment(signal, fiveMinute);
  if (alignment < -0.35 && (maxScore < 0.68 || margin < 0.18)) {
    return { status: "fail" as const, detail: `${fiveMinute.source}; weak 1m ${signal} blocked` };
  }
  if (alignment < 0) {
    return { status: "info" as const, detail: `${fiveMinute.source}; conflict noted` };
  }
  return { status: "pass" as const, detail: `${fiveMinute.source}; confirms ${signal}` };
}

function weightedStrengthComponents(
  strategy: WeightedAlphaStrategy,
  signalQuality: number,
  expectedReturnAfterCosts: number,
  market: ReturnType<typeof weightedMarketType>,
  sampleSizeScore: number,
  drawdownScore: number,
) {
  const familyBase = {
    breakout: { profitFactor: 0.58, precision: 0.56 },
    trend: { profitFactor: 0.62, precision: 0.58 },
    reversion: { profitFactor: 0.6, precision: 0.57 },
    volatility: { profitFactor: 0.55, precision: 0.54 },
  }[strategy.family];
  const regimeMatch =
    strategy.family === "trend" && market.trendDirection !== "neutral"
      ? 0.74
      : strategy.family === "reversion" && market.rangeCondition.toLowerCase().includes("chop")
        ? 0.78
        : strategy.family === "breakout" && state.marketContext?.event.strategyTags.includes("orb")
          ? 0.76
          : strategy.family === "volatility" && market.volatilityLevel === "high"
            ? 0.74
            : 0.52;
  const recentPerformance = Math.max(0.25, Math.min(0.88, 0.5 + signalQuality * 0.45 + market.breadth * 0.08));
  const expectancy = Math.max(0.1, Math.min(0.9, 0.48 + expectedReturnAfterCosts * 280 + signalQuality * 0.18));
  const drawdown = Math.max(0.15, Math.min(1, drawdownScore - Math.max(0, market.volatilityPercent - 0.004) * 8));
  return {
    profitFactor: familyBase.profitFactor,
    expectancy,
    precision: Math.min(0.9, familyBase.precision + signalQuality * 0.2),
    drawdown,
    recentPerformance,
    regimeMatch,
    sampleSize: sampleSizeScore,
  };
}

function weightedFilterMultipliers(
  strategy: WeightedAlphaResult,
  strategies: WeightedAlphaResult[],
  market: ReturnType<typeof weightedMarketType>,
) {
  const regimeDirectional = market.trendDirection === "up" ? "long" : market.trendDirection === "down" ? "short" : "neutral";
  const sessionText = market.rangeCondition.toLowerCase();
  const familyLeader = strategies
    .filter((item) => item.family === strategy.family)
    .sort((left, right) => right.strength - left.strength)[0];
  const trendRegime = regimeDirectional === "long" || regimeDirectional === "short";
  const regimeMultiplier =
    strategy.family === "trend" && trendRegime
      ? 1.15
      : strategy.family === "reversion" && sessionText.includes("chop")
        ? 1.2
        : strategy.family === "volatility" && market.volatilityLevel === "high"
            ? 1.1
            : trendRegime && strategy.family === "reversion"
              ? 0.85
              : 1;
  const timeOfDayMultiplier =
    market.minutes < 10 * 60 + 30
      ? strategy.key === "S1" || strategy.key === "S2" || strategy.key === "S8"
        ? 1.14
        : 0.95
      : market.minutes >= 12 * 60 && market.minutes < 14 * 60
        ? strategy.family === "reversion"
          ? 1.08
          : 0.92
        : market.minutes >= 15 * 60
          ? strategy.family === "breakout" || strategy.family === "trend"
            ? 1.05
            : 0.98
          : 1;
  const relativeStrengthMultiplier =
    Math.abs(market.relativeStrength) > 0.006
      ? strategy.family === "trend" || strategy.family === "breakout"
        ? 1.08
        : 0.94
      : 1;
  const side = weightedSignalSide(strategy);
  const fiveMinuteAlignment = side ? weightedSignalTimeframeAlignment(side, market.timeframes.fiveMinute) : 0;
  const fiveMinuteMultiplier = fiveMinuteAlignment > 0 ? 1.08 : fiveMinuteAlignment < -0.35 ? 0.78 : fiveMinuteAlignment < 0 ? 0.9 : 1;
  const breadthMultiplier =
    Math.abs(market.breadth) > 0.35
      ? strategy.family === "trend" || strategy.family === "volatility"
        ? 1.07
        : 0.95
      : 1;
  const drawdownMultiplier = strategy.recentDrawdown > 0.006 ? 0.72 : strategy.recentDrawdown > 0.003 ? 0.86 : 1;
  const correlationPenalty = familyLeader?.key === strategy.key ? 1 : strategy.family === familyLeader?.family ? 0.86 : 1;
  return {
    regime: regimeMultiplier,
    timeOfDay: timeOfDayMultiplier,
    fiveMinute: fiveMinuteMultiplier,
    relativeStrength: relativeStrengthMultiplier,
    breadth: breadthMultiplier,
    drawdown: drawdownMultiplier,
    correlation: correlationPenalty,
  };
}

function loadWeightedVotingWeightState(): WeightedVotingWeightState {
  try {
    const raw = window.localStorage.getItem(weightedVotingStorageKey);
    if (!raw) {
      return { weights: { ...weightedVotingDefaultWeights }, lastUpdatedDate: "", source: "default" };
    }
    const parsed = JSON.parse(raw) as Partial<WeightedVotingWeightState> & Partial<Record<WeightedAlphaKey, number>>;
    const sourceWeights = parsed.weights ?? parsed;
    const source = parsed.source === "backtest" || parsed.source === "daily" ? parsed.source : "default";
    return {
      weights: normalizeWeightedValues(
        Object.fromEntries(
          weightedAlphaStrategies.map((strategy) => [strategy.key, Number(sourceWeights[strategy.key] ?? weightedVotingDefaultWeights[strategy.key])]),
        ) as Record<WeightedAlphaKey, number>,
      ),
      lastUpdatedDate: typeof parsed.lastUpdatedDate === "string" ? parsed.lastUpdatedDate : "",
      source,
      seededAt: typeof parsed.seededAt === "string" ? parsed.seededAt : undefined,
    };
  } catch {
    return { weights: { ...weightedVotingDefaultWeights }, lastUpdatedDate: "", source: "default" };
  }
}

function saveWeightedVotingWeightState(weightState: WeightedVotingWeightState) {
  window.localStorage.setItem(
    weightedVotingStorageKey,
    JSON.stringify({
      weights: normalizeWeightedValues(weightState.weights),
      lastUpdatedDate: weightState.lastUpdatedDate,
      source: weightState.source,
      seededAt: weightState.seededAt,
    }),
  );
}

function weightedVotingHasBacktestSeed() {
  const weightState = loadWeightedVotingWeightState();
  return weightState.source === "backtest" || weightState.source === "daily";
}

async function ensureWeightedInitialWeightsFromBacktest() {
  if (!WEIGHTED_BROWSER_BACKTEST_BOOTSTRAP_ENABLED || weightedInitialWeightsInFlight || weightedVotingHasBacktestSeed() || state.symbol !== "SPY") {
    return;
  }
  weightedInitialWeightsInFlight = true;
  try {
    const candles = await fetchWeightedInitialBacktestCandles();
    const result = weightedInitialBacktestWeights(normalizeCandles(candles));
    if (!result) {
      return;
    }
    saveWeightedStrategyPerformance(result.performance);
    saveWeightedVotingWeightState({
      weights: result.weights,
      lastUpdatedDate: "bootstrap",
      source: "backtest",
      seededAt: new Date().toISOString(),
    });
    updateWeightedVotingPanel();
  } catch {
    // Equal weights remain the safe bootstrap if historical 1m data is unavailable.
  } finally {
    weightedInitialWeightsInFlight = false;
  }
}

async function fetchWeightedInitialBacktestCandles() {
  try {
    const params = await backtestRangeParams({ timeframe: "1Min" });
    const response = await fetchWithTimeout(`${API_BASE}/api/backtest-data/candles?${params.toString()}`, 30000);
    if (!response.ok) {
      throw new Error(await response.text());
    }
    const payload = (await response.json()) as CandleResponse;
    return payload.candles;
  } catch {
    return fetchAlgoBacktestCandles("1Min");
  }
}

function weightedInitialBacktestWeights(candles: Candle[]): WeightedInitialBacktestResult | null {
  const regularCandles = candles.filter((candle) => isRegularSession(candle.timestamp));
  if (regularCandles.length < 80) {
    return null;
  }
  const stats = Object.fromEntries(
    weightedAlphaStrategies.map((strategy) => [
      strategy.key,
      {
        trades: 0,
        wins: 0,
        grossProfit: 0,
        grossLoss: 0,
        outcomes: [] as number[],
      },
    ]),
  ) as Record<WeightedAlphaKey, { trades: number; wins: number; grossProfit: number; grossLoss: number; outcomes: number[] }>;

  for (let index = 50; index < regularCandles.length - 1; index += 1) {
    const history = latestRegularSessionCandlesFrom(regularCandles.slice(0, index + 1));
    const next = regularCandles[index + 1];
    if (history.length < 30 || easternDateString(history.at(-1)!.timestamp) !== easternDateString(next.timestamp)) {
      continue;
    }
    const market = weightedBacktestMarket(history);
    if (!market) {
      continue;
    }
    weightedAlphaStrategies.forEach((strategy) => {
      const signal = weightedAlphaSignal(strategy, market);
      const side = weightedSignalSide(signal);
      if (!side) {
        return;
      }
      const entry = market.latest.close;
      const barrier = weightedBarrierSettings(entry, market.atr, market.minutes);
      const futureCandles = regularCandles
        .slice(index + 1, index + 1 + barrier.horizonBars + 1)
        .filter((candle) => easternDateString(candle.timestamp) === easternDateString(market.latest.timestamp));
      const resolved = weightedTripleBarrierOutcome(side, entry, weightedSignalCostEstimate(entry), futureCandles, barrier, true);
      if (!resolved) {
        return;
      }
      const outcome = resolved.outcome;
      const row = stats[strategy.key];
      row.trades += 1;
      row.wins += outcome > 0 ? 1 : 0;
      row.grossProfit += Math.max(0, outcome);
      row.grossLoss += Math.abs(Math.min(0, outcome));
      row.outcomes = [outcome, ...row.outcomes].slice(0, 50);
    });
  }

  const performance = Object.fromEntries(
    weightedAlphaStrategies.map((strategy) => {
      const row = stats[strategy.key];
      const recentExpectancy = row.outcomes.length ? roundNumber(row.outcomes.reduce((sum, value) => sum + value, 0) / row.outcomes.length, 5) : null;
      return [
        strategy.key,
        {
          tradeCount: row.trades,
          liveOutcomeCount: 0,
          recentOutcomes: row.outcomes,
          recentExpectancy,
          recentDrawdown: weightedDrawdownFromOutcomes(row.outcomes),
        },
      ];
    }),
  ) as Record<WeightedAlphaKey, WeightedStrategyPerformance>;

  const pseudoStrategies = weightedAlphaStrategies.map((strategy) => {
    const row = stats[strategy.key];
    const expectancy = row.outcomes.length ? row.outcomes.reduce((sum, value) => sum + value, 0) / row.outcomes.length : 0;
    const profitFactor = row.grossLoss ? row.grossProfit / row.grossLoss : row.grossProfit > 0 ? 3 : 0;
    const profitFactorScore = Math.max(0, Math.min(1, profitFactor / 3));
    const expectancyScore = Math.max(0, Math.min(1, 0.5 + expectancy * 250));
    const precisionScore = row.trades ? row.wins / row.trades : 0;
    const drawdownScore = weightedStrategyDrawdownScore(performance[strategy.key].recentDrawdown);
    const recentPerformanceScore = Math.max(0, Math.min(1, 0.5 + (performance[strategy.key].recentExpectancy ?? 0) * 250));
    const sampleSizeScore = weightedStrategySampleSizeScore(row.trades);
    const strength = roundNumber(
      0.25 * profitFactorScore +
        0.2 * expectancyScore +
        0.15 * precisionScore +
        0.15 * drawdownScore +
        0.1 * recentPerformanceScore +
        0.1 * 0.6 +
        0.05 * sampleSizeScore,
      4,
    );
    return {
      ...strategy,
      pBuy: 0,
      pSell: 0,
      pHold: 1,
      expectedReturn: expectancy,
      expectedReturnAfterCosts: expectancy,
      signalQuality: precisionScore,
      strength,
      tradeCount: row.trades,
      sampleSizeScore,
      recentExpectancy: performance[strategy.key].recentExpectancy,
      recentDrawdown: performance[strategy.key].recentDrawdown,
      drawdownScore,
      baseWeight: 0,
      adjustedWeight: strength * sampleSizeScore,
      finalWeight: 0,
      smoothedWeight: 0,
      recalculatedWeight: 0,
      multipliers: {},
      disabledReason: "",
      detail: "Initial 1m backtest bootstrap",
    };
  });
  const activeStrength = pseudoStrategies.reduce((sum, strategy) => sum + (strategy.disabledReason ? 0 : strategy.adjustedWeight), 0);
  if (!activeStrength) {
    return null;
  }
  const rawWeights = normalizeWeightedValues(
    Object.fromEntries(pseudoStrategies.map((strategy) => [strategy.key, strategy.disabledReason ? 0 : strategy.adjustedWeight])) as Record<WeightedAlphaKey, number>,
  );
  return {
    weights: capNormalizeWeights(rawWeights, pseudoStrategies),
    performance,
  };
}

function weightedBacktestMarket(candles: Candle[]): ReturnType<typeof weightedMarketType> | null {
  const latest = candles.at(-1) ?? null;
  if (!latest || candles.length < 30) {
    return null;
  }
  const closes = candles.map((candle) => candle.close);
  const vwap = sessionVwapValue(candles);
  const atr = averageTrueRange(candles, Math.min(14, candles.length - 1));
  const bands = bollingerBands(closes, 20, 2);
  const averageVolume = simpleMovingAverage(candles.map((candle) => candle.volume), Math.min(20, candles.length)) ?? latest.volume;
  const sma20 = simpleMovingAverage(closes, 20);
  const sma50 = simpleMovingAverage(closes, 50);
  const priorRange = candles.slice(-21, -1);
  const minutes = easternMinutes(latest.timestamp);
  const volatilityPercent = atr && latest.close ? atr / latest.close : 0;
  const regime = weightedMarketRegime({ candles, closes, latest, vwap, atr, bands, sma20, sma50, volatilityPercent, minutes });
  return {
    candles,
    latest,
    closes,
    vwap,
    atr,
    bands,
    openingRange: openingRangeValues(candles, Math.min(15, candles.length)),
    priorHigh: priorRange.length ? Math.max(...priorRange.map((candle) => candle.high)) : latest.high,
    priorLow: priorRange.length ? Math.min(...priorRange.map((candle) => candle.low)) : latest.low,
    averageVolume,
    sma20,
    sma50,
    relativeStrength: 0,
    breadth: 0,
    eventRisk: 0.2,
    volatilityPercent,
    trendDirection: regime.trendDirection,
    trendSource: regime.trendSource,
    volatilityLevel: regime.volatilityLevel,
    rangeCondition: regime.rangeCondition,
    sessionLabel: regime.sessionLabel,
    timeframes: emptyWeightedTimeframeContext(),
    minutes,
  };
}

function loadWeightedStrategyPerformance(): Record<WeightedAlphaKey, WeightedStrategyPerformance> {
  try {
    const raw = window.localStorage.getItem(weightedStrategyPerformanceStorageKey);
    const parsed = raw ? (JSON.parse(raw) as Partial<Record<WeightedAlphaKey, Partial<WeightedStrategyPerformance>>>) : {};
    return Object.fromEntries(
      weightedAlphaStrategies.map((strategy) => [
        strategy.key,
        {
          tradeCount: Math.max(0, Math.floor(Number(parsed[strategy.key]?.tradeCount ?? 0))),
          liveOutcomeCount: Math.max(0, Math.floor(Number(parsed[strategy.key]?.liveOutcomeCount ?? 0))),
          recentOutcomes: Array.isArray(parsed[strategy.key]?.recentOutcomes)
            ? parsed[strategy.key]!.recentOutcomes!.map((value) => Number(value)).filter(Number.isFinite).slice(0, 50)
            : [],
          recentExpectancy:
            typeof parsed[strategy.key]?.recentExpectancy === "number" && Number.isFinite(parsed[strategy.key]?.recentExpectancy)
              ? Number(parsed[strategy.key]?.recentExpectancy)
              : null,
          recentDrawdown:
            typeof parsed[strategy.key]?.recentDrawdown === "number" && Number.isFinite(parsed[strategy.key]?.recentDrawdown)
              ? Number(parsed[strategy.key]?.recentDrawdown)
              : weightedDrawdownFromOutcomes(
                  Array.isArray(parsed[strategy.key]?.recentOutcomes)
                    ? parsed[strategy.key]!.recentOutcomes!.map((value) => Number(value)).filter(Number.isFinite).slice(0, 50)
                    : [],
                ),
          ...(parsed[strategy.key]?.pendingSignal ? { pendingSignal: parsed[strategy.key]!.pendingSignal } : {}),
        },
      ]),
    ) as Record<WeightedAlphaKey, WeightedStrategyPerformance>;
  } catch {
    return defaultWeightedStrategyPerformance();
  }
}

function defaultWeightedStrategyPerformance(): Record<WeightedAlphaKey, WeightedStrategyPerformance> {
  return Object.fromEntries(
    weightedAlphaStrategies.map((strategy) => [
      strategy.key,
      { tradeCount: 0, liveOutcomeCount: 0, recentOutcomes: [], recentExpectancy: null, recentDrawdown: 0 },
    ]),
  ) as unknown as Record<
    WeightedAlphaKey,
    WeightedStrategyPerformance
  >;
}

function saveWeightedStrategyPerformance(performance: Record<WeightedAlphaKey, WeightedStrategyPerformance>) {
  window.localStorage.setItem(weightedStrategyPerformanceStorageKey, JSON.stringify(performance));
}

function weightedStrategyTradeCount(strategyKey: WeightedAlphaKey) {
  return loadWeightedStrategyPerformance()[strategyKey]?.tradeCount ?? 0;
}

function weightedStrategyLiveOutcomeCount(strategyKey: WeightedAlphaKey) {
  return loadWeightedStrategyPerformance()[strategyKey]?.liveOutcomeCount ?? 0;
}

function weightedStrategyRecentExpectancy(strategyKey: WeightedAlphaKey) {
  return loadWeightedStrategyPerformance()[strategyKey]?.recentExpectancy ?? null;
}

function weightedStrategyRecentDrawdown(strategyKey: WeightedAlphaKey) {
  return loadWeightedStrategyPerformance()[strategyKey]?.recentDrawdown ?? 0;
}

function weightedStrategyDrawdownScore(drawdown: number) {
  return roundNumber(Math.max(0.15, Math.min(1, 1 - drawdown / 0.01)), 4);
}

function weightedStrategySampleSizeScore(tradeCount: number) {
  return roundNumber(Math.max(0.2, Math.min(1, tradeCount / weightedMinimumTradesForFullWeight)), 4);
}

function updateWeightedStrategyPerformanceFromSignals(signals: WeightedAlphaResult[], market: ReturnType<typeof weightedMarketType>) {
  const performance = loadWeightedStrategyPerformance();
  let changed = false;
  signals.forEach((signal) => {
    const row = performance[signal.key] ?? { tradeCount: 0, liveOutcomeCount: 0, recentOutcomes: [], recentExpectancy: null, recentDrawdown: 0 };
    const pending = row.pendingSignal;
    if (pending && pending.timestamp !== market.latest.timestamp) {
      const resolved = weightedResolvePendingSignalOutcome(pending, market);
      if (resolved) {
        const recentOutcomes = [resolved.outcome, ...(row.recentOutcomes ?? [])].slice(0, 50);
        row.recentOutcomes = recentOutcomes;
        row.recentExpectancy = roundNumber(recentOutcomes.reduce((sum, value) => sum + value, 0) / recentOutcomes.length, 5);
        row.recentDrawdown = weightedDrawdownFromOutcomes(recentOutcomes);
        row.tradeCount = Math.max(0, Math.floor(Number(row.tradeCount) || 0)) + 1;
        row.liveOutcomeCount = Math.max(0, Math.floor(Number(row.liveOutcomeCount) || 0)) + 1;
        delete row.pendingSignal;
        changed = true;
      } else if (easternDateString(pending.timestamp) !== easternDateString(market.latest.timestamp)) {
        delete row.pendingSignal;
        changed = true;
      }
    }

    const side = weightedSignalSide(signal);
    if (side && row.pendingSignal?.timestamp !== market.latest.timestamp) {
      const barrier = weightedBarrierSettings(market.latest.close, market.atr, market.minutes);
      row.pendingSignal = {
        side,
        entryPrice: market.latest.close,
        timestamp: market.latest.timestamp,
        cost: weightedSignalCostEstimate(market.latest.close),
        targetReturn: barrier.targetReturn,
        stopReturn: barrier.stopReturn,
        horizonBars: barrier.horizonBars,
      };
      changed = true;
    }
    performance[signal.key] = row;
  });
  if (changed) {
    saveWeightedStrategyPerformance(performance);
  }
}

function weightedDrawdownFromOutcomes(outcomesNewestFirst: number[]) {
  let equity = 0;
  let peak = 0;
  let maxDrawdown = 0;
  outcomesNewestFirst
    .slice()
    .reverse()
    .forEach((outcome) => {
      equity += outcome;
      peak = Math.max(peak, equity);
      maxDrawdown = Math.max(maxDrawdown, peak - equity);
    });
  return roundNumber(maxDrawdown, 5);
}

function weightedSignalSide(signal: WeightedAlphaResult): "Buy" | "Sell" | null {
  if (signal.pBuy >= 0.58 && signal.pBuy > signal.pSell && signal.pBuy > signal.pHold) {
    return "Buy";
  }
  if (signal.pSell >= 0.58 && signal.pSell > signal.pBuy && signal.pSell > signal.pHold) {
    return "Sell";
  }
  return null;
}

function weightedSignalCostEstimate(priceValue: number) {
  return priceValue ? (state.weightedTradingSettings.slippagePerShare * 2 + 0.02) / priceValue : 0.0001;
}

function weightedBarrierSettings(priceValue: number, atr: number | null, minutes: number) {
  const atrReturn = atr && priceValue ? atr / priceValue : 0.0025;
  return {
    targetReturn: Math.max(0.0015, atrReturn * 0.9),
    stopReturn: Math.max(0.001, atrReturn * 0.65),
    horizonBars: minutes < 10 * 60 + 30 ? 15 : minutes >= 15 * 60 ? 8 : 20,
  };
}

function weightedTripleBarrierOutcome(
  side: "Buy" | "Sell",
  entryPrice: number,
  cost: number,
  futureCandles: Candle[],
  barrier: { targetReturn: number; stopReturn: number; horizonBars: number },
  forceCloseAtEnd = false,
): WeightedResolvedBarrierOutcome | null {
  if (!entryPrice || !futureCandles.length) {
    return null;
  }
  const candles = futureCandles.slice(0, barrier.horizonBars);
  for (let index = 0; index < candles.length; index += 1) {
    const candle = candles[index];
    const favorableReturn = side === "Buy" ? (candle.high - entryPrice) / entryPrice : (entryPrice - candle.low) / entryPrice;
    const adverseReturn = side === "Buy" ? (entryPrice - candle.low) / entryPrice : (candle.high - entryPrice) / entryPrice;
    const hitTarget = favorableReturn >= barrier.targetReturn;
    const hitStop = adverseReturn >= barrier.stopReturn;
    if (hitTarget && hitStop) {
      const closeReturn = ((candle.close - entryPrice) / entryPrice) * (side === "Buy" ? 1 : -1);
      const label = closeReturn >= 0 ? "target" : "stop";
      return {
        outcome: roundNumber((label === "target" ? barrier.targetReturn : -barrier.stopReturn) - cost, 5),
        label,
        barsHeld: index + 1,
      };
    }
    if (hitTarget) {
      return { outcome: roundNumber(barrier.targetReturn - cost, 5), label: "target", barsHeld: index + 1 };
    }
    if (hitStop) {
      return { outcome: roundNumber(-barrier.stopReturn - cost, 5), label: "stop", barsHeld: index + 1 };
    }
  }
  if (!forceCloseAtEnd && candles.length < barrier.horizonBars) {
    return null;
  }
  const exitCandle = candles.at(-1);
  if (!exitCandle) {
    return null;
  }
  const direction = side === "Buy" ? 1 : -1;
  return {
    outcome: roundNumber(((exitCandle.close - entryPrice) / entryPrice) * direction - cost, 5),
    label: "time",
    barsHeld: candles.length,
  };
}

function weightedResolvePendingSignalOutcome(
  pending: NonNullable<WeightedStrategyPerformance["pendingSignal"]>,
  market: ReturnType<typeof weightedMarketType>,
): WeightedResolvedBarrierOutcome | null {
  const entryIndex = market.candles.findIndex((candle) => candle.timestamp === pending.timestamp);
  if (entryIndex < 0) {
    return null;
  }
  const pendingDate = easternDateString(pending.timestamp);
  const latestDate = easternDateString(market.latest.timestamp);
  const fallbackBarrier = weightedBarrierSettings(pending.entryPrice, null, easternMinutes(pending.timestamp));
  const barrier = {
    targetReturn: pending.targetReturn ?? fallbackBarrier.targetReturn,
    stopReturn: pending.stopReturn ?? fallbackBarrier.stopReturn,
    horizonBars: pending.horizonBars ?? fallbackBarrier.horizonBars,
  };
  const futureCandles = market.candles
    .slice(entryIndex + 1, entryIndex + 1 + barrier.horizonBars + 1)
    .filter((candle) => easternDateString(candle.timestamp) === pendingDate);
  return weightedTripleBarrierOutcome(pending.side, pending.entryPrice, pending.cost, futureCandles, barrier, latestDate !== pendingDate);
}

function resolveWeightedVotingActiveWeights(
  storedWeightState: WeightedVotingWeightState,
  smoothedWeights: Record<WeightedAlphaKey, number>,
  strategies: WeightedAlphaResult[],
  latest: Candle,
) {
  const latestDate = easternDateString(latest.timestamp);
  const storedWeights = capNormalizeWeights(storedWeightState.weights, strategies);
  if (!weightedVotingCanUpdateWeightsAfterClose(latest) || storedWeightState.lastUpdatedDate === latestDate) {
    return storedWeights;
  }
  const nextWeights = capNormalizeWeights(smoothedWeights, strategies);
  saveWeightedVotingWeightState({
    weights: nextWeights,
    lastUpdatedDate: latestDate,
    source: "daily",
    seededAt: storedWeightState.seededAt ?? new Date().toISOString(),
  });
  return nextWeights;
}

function weightedVotingCanUpdateWeightsAfterClose(latest: Candle) {
  const latestDate = easternDateString(latest.timestamp);
  const today = easternDateString(new Date().toISOString());
  const nowMinutes = easternMinutes(new Date().toISOString());
  return latestDate === today && state.marketStatus !== "open" && nowMinutes >= 16 * 60;
}

function normalizeWeightedValues(values: Record<WeightedAlphaKey, number>): Record<WeightedAlphaKey, number> {
  const total = weightedAlphaStrategies.reduce((sum, strategy) => sum + Math.max(0, Number(values[strategy.key] ?? 0)), 0);
  if (!total) {
    return { ...weightedVotingDefaultWeights };
  }
  return Object.fromEntries(weightedAlphaStrategies.map((strategy) => [strategy.key, Math.max(0, values[strategy.key]) / total])) as Record<WeightedAlphaKey, number>;
}

function capNormalizeWeights(values: Record<WeightedAlphaKey, number>, strategies: WeightedAlphaResult[]): Record<WeightedAlphaKey, number> {
  const activeKeys = new Set(strategies.filter((strategy) => !strategy.disabledReason).map((strategy) => strategy.key));
  const next = Object.fromEntries(
    weightedAlphaStrategies.map((strategy) => [strategy.key, activeKeys.has(strategy.key) ? Math.max(0, values[strategy.key]) : 0]),
  ) as Record<WeightedAlphaKey, number>;
  let activeTotal = weightedAlphaStrategies.reduce((sum, strategy) => sum + next[strategy.key], 0);
  if (!activeTotal) {
    return { ...weightedVotingDefaultWeights };
  }
  weightedAlphaStrategies.forEach((strategy) => {
    next[strategy.key] = next[strategy.key] / activeTotal;
  });
  for (let pass = 0; pass < 3; pass += 1) {
    const overweight = weightedAlphaStrategies.filter((strategy) => next[strategy.key] > 0.25);
    if (!overweight.length) {
      break;
    }
    const locked = new Set(overweight.map((strategy) => strategy.key));
    let lockedTotal = 0;
    overweight.forEach((strategy) => {
      next[strategy.key] = 0.25;
      lockedTotal += 0.25;
    });
    const remaining = weightedAlphaStrategies.filter((strategy) => activeKeys.has(strategy.key) && !locked.has(strategy.key));
    const remainingTotal = remaining.reduce((sum, strategy) => sum + next[strategy.key], 0);
    remaining.forEach((strategy) => {
      next[strategy.key] = remainingTotal ? (next[strategy.key] / remainingTotal) * Math.max(0, 1 - lockedTotal) : 0;
    });
  }
  weightedAlphaStrategies.forEach((strategy) => {
    if (activeKeys.has(strategy.key) && next[strategy.key] > 0 && next[strategy.key] < 0.02) {
      next[strategy.key] = 0.02;
    }
  });
  activeTotal = weightedAlphaStrategies.reduce((sum, strategy) => sum + next[strategy.key], 0);
  weightedAlphaStrategies.forEach((strategy) => {
    next[strategy.key] = activeTotal ? next[strategy.key] / activeTotal : weightedVotingDefaultWeights[strategy.key];
  });
  return next;
}

function weightedTripleBarrierFilter(
  signal: AlgoSignal,
  expectedReturnAfterCosts: number,
  market: ReturnType<typeof weightedMarketType>,
  strategies: WeightedAlphaResult[],
): WeightedTripleBarrierResult {
  if (signal === "Hold" || !market.latest.close) {
    return emptyWeightedTripleBarrier();
  }
  const { targetReturn, stopReturn, horizonBars } = weightedBarrierSettings(market.latest.close, market.atr, market.minutes);
  const directionalQuality = strategies.reduce((sum, strategy) => {
    const probability = signal === "Buy" ? strategy.pBuy : strategy.pSell;
    return sum + strategy.finalWeight * probability;
  }, 0);
  const pathReturn = expectedReturnAfterCosts * horizonBars * Math.max(0.5, directionalQuality);
  const oppositePressure = signal === "Buy" ? strategies.reduce((sum, strategy) => sum + strategy.finalWeight * strategy.pSell, 0) : strategies.reduce((sum, strategy) => sum + strategy.finalWeight * strategy.pBuy, 0);
  const timeframeAdjustment =
    weightedSignalTimeframeAlignment(signal, market.timeframes.fiveMinute) * 0.25;
  const adjustedPathReturn = pathReturn + timeframeAdjustment * targetReturn;
  const likelyTargetFirst = adjustedPathReturn >= targetReturn && directionalQuality - oppositePressure + timeframeAdjustment >= 0.12;
  const likelyStopFirst = adjustedPathReturn <= -stopReturn || oppositePressure > directionalQuality + Math.max(0, timeframeAdjustment);
  const label = likelyTargetFirst ? "positive" : likelyStopFirst ? "negative" : "neutral";
  const passed = label === "positive";
  return {
    passed,
    label,
    targetReturn,
    stopReturn,
    horizonBars,
    expectedPathReturn: roundNumber(adjustedPathReturn, 5),
    detail: `${label}; path ${formatBasisPoints(adjustedPathReturn)}, target ${formatBasisPoints(targetReturn)}, stop ${formatBasisPoints(stopReturn)}, ${horizonBars} bars`,
  };
}

function emptyWeightedTripleBarrier(): WeightedTripleBarrierResult {
  return {
    passed: false,
    label: "neutral",
    targetReturn: 0,
    stopReturn: 0,
    horizonBars: 0,
    expectedPathReturn: 0,
    detail: "No directional triple-barrier setup",
  };
}

function weightedMetaLabelProbability(
  signal: AlgoSignal,
  maxScore: number,
  margin: number,
  tripleBarrier: WeightedTripleBarrierResult,
  timeframes: WeightedTimeframeContext,
) {
  if (signal === "Hold") {
    return 0.5;
  }
  const bestRows = state.dynamicArtifact?.mlComparison?.bestByTimeframe ?? state.mlComparison?.bestByTimeframe ?? [];
  const row = bestRows.find((item) => item.timeframe === "5Min");
  const artifactScore =
    row?.verdict === "Improved" ? 0.65 : row?.verdict === "Mixed" ? 0.6 : row?.verdict === "Worse" ? 0.52 : row ? 0.56 : 0.58;
  const tripleBarrierAdjustment = tripleBarrier.label === "positive" ? 0.04 : tripleBarrier.label === "negative" ? -0.08 : -0.03;
  const timeframeAdjustment = weightedSignalTimeframeAlignment(signal, timeframes.fiveMinute) * 0.05;
  return Math.max(0.35, Math.min(0.82, artifactScore + (maxScore - 0.58) * 0.25 + margin * 0.35 + tripleBarrierAdjustment + timeframeAdjustment));
}

function weightedRelativeStrengthScore(spyCandles: Candle[]) {
  const spyReturn = candleReturnOverLookback(spyCandles, 20);
  const qqqReturn = candleReturnOverLookback(state.weightedMarketData.candlesBySymbol.QQQ ?? [], 20);
  const iwmReturn = candleReturnOverLookback(state.weightedMarketData.candlesBySymbol.IWM ?? [], 20);
  const comparisonReturns = [qqqReturn, iwmReturn].filter((value): value is number => value !== null);
  if (spyReturn !== null && comparisonReturns.length) {
    const benchmarkReturn = comparisonReturns.reduce((sum, value) => sum + value, 0) / comparisonReturns.length;
    return {
      value: roundNumber(spyReturn - benchmarkReturn, 5),
      source: comparisonReturns.length === 2 ? "SPY vs QQQ/IWM" : "SPY vs available QQQ/IWM",
    };
  }
  const fallback = marketBreadthProxy();
  return {
    value: roundNumber(fallback * 0.003, 5),
    source: "fallback ensemble proxy",
  };
}

function weightedBreadthScore(spyCandles: Candle[]) {
  const spyReturn = candleReturnOverLookback(spyCandles, 20);
  const sectorReturns = weightedSectorEtfSymbols
    .map((symbol) => candleReturnOverLookback(state.weightedMarketData.candlesBySymbol[symbol] ?? [], 20))
    .filter((value): value is number => value !== null);
  const spyBasketReturns = weightedSpyBasketSampleSymbols
    .map((symbol) => candleReturnOverLookback(state.weightedMarketData.candlesBySymbol[symbol] ?? [], 20))
    .filter((value): value is number => value !== null);
  const qqqReturn = candleReturnOverLookback(state.weightedMarketData.candlesBySymbol.QQQ ?? [], 20);
  const iwmReturn = candleReturnOverLookback(state.weightedMarketData.candlesBySymbol.IWM ?? [], 20);
  const confirmationReturns = [qqqReturn, iwmReturn].filter((value): value is number => value !== null);
  const components: { label: string; weight: number; value: number }[] = [];

  if (spyReturn !== null && sectorReturns.length >= 3) {
    components.push({ label: "sector ETF agreement", weight: 0.4, value: agreementScore(spyReturn, sectorReturns) });
  }
  if (spyReturn !== null && confirmationReturns.length) {
    components.push({ label: "QQQ/IWM confirmation", weight: 0.3, value: agreementScore(spyReturn, confirmationReturns) });
  }
  if (spyReturn !== null && spyBasketReturns.length >= 3) {
    components.push({ label: "SPY basket sample", weight: 0.3, value: agreementScore(spyReturn, spyBasketReturns) });
  }

  if (components.length) {
    const totalWeight = components.reduce((sum, component) => sum + component.weight, 0);
    const value = components.reduce((sum, component) => sum + component.value * component.weight, 0) / totalWeight;
    return {
      value: roundNumber(value, 4),
      source: components.map((component) => component.label).join(" + "),
    };
  }

  return {
    value: marketBreadthProxy(),
    source: "fallback ensemble directional proxy",
  };
}

function candleReturnOverLookback(candles: Candle[], lookback: number) {
  const regular = latestRegularSessionCandlesFrom(candles);
  const comparable = regular.length ? regular : candles;
  if (comparable.length < 2) {
    return null;
  }
  const latest = comparable.at(-1)!;
  const base = comparable[Math.max(0, comparable.length - 1 - lookback)];
  return base.close ? (latest.close - base.close) / base.close : null;
}

function aggregateDailyCandlesToWeeks(candles: Candle[]) {
  const daily = normalizeCandles(candles).filter((candle) => candle.close > 0);
  const buckets = new Map<string, Candle[]>();
  daily.forEach((candle) => {
    const date = new Date(candle.timestamp);
    if (!Number.isFinite(date.getTime())) {
      return;
    }
    const utcDay = date.getUTCDay() || 7;
    const monday = new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate() - utcDay + 1));
    const key = monday.toISOString().slice(0, 10);
    buckets.set(key, [...(buckets.get(key) ?? []), candle]);
  });
  return Array.from(buckets.entries())
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([weekStart, rows]) => {
      const sorted = rows.slice().sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime());
      const first = sorted[0];
      const last = sorted.at(-1)!;
      const volume = sorted.reduce((sum, candle) => sum + candle.volume, 0);
      return {
        ...last,
        timeframe: "1Day" as Timeframe,
        timestamp: `${weekStart}T00:00:00.000Z`,
        open: first.open,
        high: Math.max(...sorted.map((candle) => candle.high)),
        low: Math.min(...sorted.map((candle) => candle.low)),
        close: last.close,
        volume,
        trade_count: null,
        vwap: volume ? sorted.reduce((sum, candle) => sum + (candle.vwap ?? candle.close) * candle.volume, 0) / volume : last.vwap,
      };
    });
}

function latestRegularSessionCandlesFrom(candles: Candle[]) {
  const regular = candles.filter((candle) => isRegularSession(candle.timestamp));
  const latest = regular.at(-1);
  if (!latest) {
    return [];
  }
  const latestDay = easternDateString(latest.timestamp);
  return regular.filter((candle) => easternDateString(candle.timestamp) === latestDay);
}

function agreementScore(referenceReturn: number, comparisonReturns: number[]) {
  if (!comparisonReturns.length || Math.abs(referenceReturn) < 0.0001) {
    return 0;
  }
  const direction = Math.sign(referenceReturn);
  return comparisonReturns.reduce((sum, value) => sum + (Math.sign(value) === direction ? 1 : -1), 0) / comparisonReturns.length;
}

function marketBreadthProxy() {
  const votes = strategyEnsembleSignals(state.marketContext);
  const eligible = votes.filter(isEligibleStrategyVote);
  if (!eligible.length) {
    return 0;
  }
  return (eligible.filter((vote) => vote.signal === "Buy").length - eligible.filter((vote) => vote.signal === "Sell").length) / eligible.length;
}

function weightedEventRiskScore() {
  const context = state.marketContext;
  const calendarRisk = scheduledEventRiskScore();
  let source = calendarRisk.source;
  if (!context) {
    return calendarRisk;
  }
  const tags = new Set(context.event.strategyTags);
  let score = tags.has("event-rules") ? 0.45 : 0.2;
  if (tags.has("news-risk")) {
    score += 0.18;
  }
  if (tags.has("liquidity-stress")) {
    score += 0.18;
  }
  if (tags.has("gap-up") || tags.has("gap-down")) {
    score += 0.1;
  }
  const contextRisk = Math.min(1, score + context.event.confidence * 0.1);
  if (calendarRisk.source !== "no upcoming scheduled high-impact event") {
    source = `${calendarRisk.source} + context tags`;
  } else {
    source = "market-context event tags";
  }
  return { value: Math.max(calendarRisk.value, contextRisk), source };
}

function scheduledEventRiskScore() {
  const now = Date.now();
  const events = [
    ...state.macroEvents.map((event) => ({
      title: event.title,
      releaseAt: event.releaseAt,
      weight: event.importance === "high" ? 0.85 : event.importance === "medium" ? 0.55 : 0.3,
    })),
    ...state.fedEvents.map((event) => ({
      title: event.title,
      releaseAt: event.releaseAt,
      weight: event.category === "fomc" ? 0.9 : 0.5,
    })),
  ];
  const scored = events
    .map((event) => {
      const eventTime = new Date(event.releaseAt).getTime();
      const minutesUntil = Number.isFinite(eventTime) ? (eventTime - now) / 60000 : Number.POSITIVE_INFINITY;
      if (minutesUntil < -30 || minutesUntil > 7 * 24 * 60) {
        return null;
      }
      const timeDecay =
        minutesUntil <= 30
          ? 1
          : minutesUntil <= 120
            ? 0.75
            : minutesUntil <= 24 * 60
              ? 0.45
              : 0.18;
      return {
        title: event.title,
        value: event.weight * timeDecay,
      };
    })
    .filter((event): event is { title: string; value: number } => event !== null)
    .sort((left, right) => right.value - left.value);
  const top = scored[0];
  if (!top) {
    return { value: 0.2, source: "no upcoming scheduled high-impact event" };
  }
  return { value: roundNumber(Math.min(1, top.value), 4), source: `scheduled calendar: ${top.title}` };
}

function weightedDailyLossStatus(latestPrice: number) {
  const pnl = weightedTodayPnl(latestPrice);
  const limit = weightedDailyLossLimitDollars();
  return {
    pnl,
    limit,
    limitReached: limit > 0 && pnl <= -limit,
  };
}

function weightedDailyLossLimitDollars() {
  const settings = state.weightedTradingSettings;
  const weightedDefaults = weightedDefaultSizingSettings();
  if (settings.useDefaultSizingSettings) {
    return roundNumber(settings.startingCapital * (weightedDefaults.maxDailyLossPercent / 100), 2);
  }
  const orderRiskDollars =
    settings.startingCapital *
    (settings.orderAllocationPercent / 100) *
    (settings.riskBudgetPercentOfOrder / 100) *
    (settings.stopLossPercent / 100);
  return roundNumber(Math.max(0, orderRiskDollars * settings.maxTradesPerDay), 2);
}

function weightedDefaultSizingSettings() {
  const settings = state.weightedTradingSettings;
  if (!settings.useDefaultSizingSettings) {
    return {
      minimumBuyScore: 0.58,
      minimumSignalEdge: 0.12,
      minimumActiveStrategies: 1,
      minimumBuyStrategyCount: 1,
      baseRiskPercent: Math.max(0, settings.orderAllocationPercent * (settings.riskBudgetPercentOfOrder / 100)),
      maxPositionPercent: Math.max(0, settings.orderAllocationPercent),
      maxDailyLossPercent: Math.max(0, settings.dailyAllocationPercent),
      maxDailyTrades: Math.max(1, Math.round(settings.maxTradesPerDay)),
      fixedStopDistanceDollars: fixedStopDistanceDollars(settings.fixedStopDistanceDollars),
      atrStopMultiplier: Math.max(0.01, settings.stopLossPercent / 0.05),
      minimumStopDistancePercent: Math.max(0.0001, settings.stopLossPercent),
      maxSpreadPercent: latestManualSpreadPercent(settings),
      minimumOneMinuteVolume: 0,
      maxParticipationPercent: 1,
      maxAllowedShares: 0,
      pyramidingEnabled: true,
    };
  }
  return {
    minimumBuyScore: clampNumber(settings.minimumBuyScore, 0, 1),
    minimumSignalEdge: clampNumber(settings.minimumSignalEdge, 0, 1),
    minimumActiveStrategies: Math.max(1, Math.round(settings.minimumActiveStrategies)),
    minimumBuyStrategyCount: Math.max(1, Math.round(settings.minimumBuyStrategyCount)),
    baseRiskPercent: Math.max(0, settings.baseRiskPercent),
    maxPositionPercent: Math.max(0, settings.maxPositionPercent),
    maxDailyLossPercent: Math.max(0, settings.maxDailyLossPercent),
    maxDailyTrades: Math.max(1, Math.round(settings.maxTradesPerDay)),
    fixedStopDistanceDollars: fixedStopDistanceDollars(settings.fixedStopDistanceDollars),
    atrStopMultiplier: Math.max(0.01, settings.atrStopMultiplier),
    minimumStopDistancePercent: Math.max(0, settings.minimumStopDistancePercent),
    maxSpreadPercent: Math.max(0, settings.maxSpreadPercent),
    minimumOneMinuteVolume: Math.max(0, Math.round(settings.minimumOneMinuteVolume)),
    maxParticipationPercent: Math.max(0, settings.maxParticipationPercent),
    maxAllowedShares: Math.max(0, Math.floor(settings.maxAllowedShares)),
    pyramidingEnabled: settings.pyramidingEnabled,
  };
}

function confidenceDailyLossStatus(latestPrice: number) {
  const pnl = confidenceTodayPnl(latestPrice);
  const limit = confidenceDailyLossLimitDollars();
  return {
    pnl,
    limit,
    limitReached: limit > 0 && pnl <= -limit,
  };
}

function confidenceDailyLossLimitDollars() {
  const settings = state.confidenceTradingSettings;
  const defaults = confidenceDefaultSizingSettings();
  if (settings.useDefaultSizingSettings) {
    return roundNumber(settings.startingCapital * (defaults.maxDailyLossPercent / 100), 2);
  }
  const orderRiskDollars =
    settings.startingCapital *
    (settings.orderAllocationPercent / 100) *
    (settings.riskBudgetPercentOfOrder / 100) *
    (settings.stopLossPercent / 100);
  return roundNumber(Math.max(0, orderRiskDollars * settings.maxTradesPerDay), 2);
}

function confidenceTodayPnl(latestPrice: number) {
  return roundNumber(confidenceTodayRealizedPnl() + confidenceTodayOpenPnl(latestPrice), 2);
}

function confidenceTodayRealizedPnl() {
  return todayRealizedPnlForMode("confidence");
}

function confidenceTodayOpenPnl(latestPrice: number) {
  const today = new Date().toDateString();
  return confidenceOpenOrderLots()
    .filter((lot) => lot.symbol === state.symbol && new Date(lot.recordedAt).toDateString() === today)
    .reduce((total, lot) => total + (latestPrice - lot.entryPrice) * lot.remainingQuantity, 0);
}

function confidenceTodayTradeCount() {
  const today = new Date().toDateString();
  return state.confidenceTradeHistory.filter((trade) => new Date(trade.recordedAt).toDateString() === today).length;
}

function confidenceOpenOrderLots() {
  const lots: OpenOrderLot[] = [];
  const trades = state.confidenceTradeHistory
    .filter((trade) => trade.symbol === state.symbol)
    .slice()
    .reverse();
  for (const trade of trades) {
    const quantity = Math.max(0, Math.floor(Number(trade.quantity) || 0));
    if (!quantity) {
      continue;
    }
    if (trade.side === "Buy") {
      lots.push({
        id: trade.id,
        symbol: trade.symbol,
        originalQuantity: quantity,
        remainingQuantity: quantity,
        entryPrice: trade.price,
        recordedAt: trade.recordedAt,
      });
      continue;
    }
    if (trade.closedLotId) {
      const lot = lots.find((item) => item.id === trade.closedLotId);
      if (lot) {
        lot.remainingQuantity -= Math.min(lot.remainingQuantity, quantity);
      }
    }
  }
  return lots.filter((lot) => lot.remainingQuantity > 0);
}

function confidencePositionSummary(latestPrice: number): PositionSummary {
  const lots = confidenceOpenOrderLots();
  const shares = lots.reduce((sum, lot) => sum + lot.remainingQuantity, 0);
  const costBasis = lots.reduce((sum, lot) => sum + lot.remainingQuantity * lot.entryPrice, 0);
  const avgPrice = shares ? costBasis / shares : 0;
  const marketValue = shares * latestPrice;
  const unrealizedPnl = shares === 0 ? 0 : (latestPrice - avgPrice) * shares;
  return {
    shares,
    avgPrice,
    costBasis,
    marketValue,
    unrealizedPnl,
    realizedPnl: confidenceTodayRealizedPnl(),
    dailyPnl: unrealizedPnl,
    returnPct: costBasis ? (unrealizedPnl / costBasis) * 100 : 0,
  };
}

function defaultSizingSettingsFromTradingSettings(settings: TradingSettings) {
  if (!settings.useDefaultSizingSettings) {
    return {
      minimumBuyScore: 0.6,
      minimumSignalEdge: 0.2,
      minimumActiveStrategies: Math.max(1, Math.round(settings.minimumActiveStrategies)),
      minimumBuyStrategyCount: 1,
      baseRiskPercent: Math.max(0, settings.orderAllocationPercent * (settings.riskBudgetPercentOfOrder / 100)),
      maxPositionPercent: Math.max(0, settings.orderAllocationPercent),
      maxDailyLossPercent: Math.max(0, settings.dailyAllocationPercent),
      maxDailyTrades: Math.max(1, Math.round(settings.maxTradesPerDay)),
      fixedStopDistanceDollars: fixedStopDistanceDollars(settings.fixedStopDistanceDollars),
      atrStopMultiplier: Math.max(0.01, settings.stopLossPercent / 0.05),
      minimumStopDistancePercent: Math.max(0.0001, settings.stopLossPercent),
      maxSpreadPercent: latestManualSpreadPercent(settings),
      minimumOneMinuteVolume: 0,
      maxParticipationPercent: 1,
      maxAllowedShares: 0,
      pyramidingEnabled: true,
    };
  }
  return {
    minimumBuyScore: clampNumber(settings.minimumBuyScore, 0, 1),
    minimumSignalEdge: clampNumber(settings.minimumSignalEdge, 0, 1),
    minimumActiveStrategies: Math.max(1, Math.round(settings.minimumActiveStrategies)),
    minimumBuyStrategyCount: 1,
    baseRiskPercent: Math.max(0, settings.baseRiskPercent),
    maxPositionPercent: Math.max(0, settings.maxPositionPercent),
    maxDailyLossPercent: Math.max(0, settings.maxDailyLossPercent),
    maxDailyTrades: Math.max(1, Math.round(settings.maxTradesPerDay)),
    fixedStopDistanceDollars: fixedStopDistanceDollars(settings.fixedStopDistanceDollars),
    atrStopMultiplier: Math.max(0.01, settings.atrStopMultiplier),
    minimumStopDistancePercent: Math.max(0, settings.minimumStopDistancePercent),
    maxSpreadPercent: Math.max(0, settings.maxSpreadPercent),
    minimumOneMinuteVolume: Math.max(0, Math.round(settings.minimumOneMinuteVolume)),
    maxParticipationPercent: Math.max(0, settings.maxParticipationPercent),
    maxAllowedShares: Math.max(0, Math.floor(settings.maxAllowedShares)),
    pyramidingEnabled: settings.pyramidingEnabled,
  };
}

function confidenceDefaultSizingSettings() {
  return defaultSizingSettingsFromTradingSettings(state.confidenceTradingSettings);
}

function regimeDefaultSizingSettings() {
  return defaultSizingSettingsFromTradingSettings(state.regimeTradingSettings);
}

function latestManualSpreadPercent(settings: TradingSettings) {
  const latest = latestLoadedCandle();
  return latest?.close ? ((settings.slippagePerShare * 2) / latest.close) * 100 : 0.1;
}

function weightedTodayPnl(latestPrice: number) {
  return roundNumber(weightedTodayRealizedPnl() + weightedTodayOpenPnl(latestPrice), 2);
}

function weightedTodayRealizedPnl() {
  return todayRealizedPnlForMode("weighted");
}

function weightedTodayOpenPnl(latestPrice: number) {
  const today = new Date().toDateString();
  return openOrderLots("weighted")
    .filter((lot) => lot.symbol === state.symbol && new Date(lot.recordedAt).toDateString() === today)
    .reduce((total, lot) => total + (latestPrice - lot.entryPrice) * lot.remainingQuantity, 0);
}

function sessionLabelForMinutes(minutes: number) {
  if (minutes < 10 * 60 + 30) {
    return "Opening drive";
  }
  if (minutes < 12 * 60) {
    return "Morning continuation";
  }
  if (minutes < 14 * 60) {
    return "Midday";
  }
  if (minutes < 15 * 60 + 30) {
    return "Afternoon";
  }
  return "Closing window";
}

function formatProbability(value: number) {
  return `${roundNumber(value * 100, 1)}%`;
}

function formatBasisPoints(value: number) {
  return `${roundNumber(value * 10000, 1)} bps`;
}

function renderWeightedScoreGrid(result: WeightedVoteResult) {
  return [
    ["Buy", result.buyScore, "buy"],
    ["Sell", result.sellScore, "sell"],
    ["Hold", result.holdScore, "hold"],
  ]
    .map(
      ([label, value, kind]) => `
        <div class="weighted-score-card ${kind}">
          <span>${escapeHtml(String(label))}</span>
          <strong>${formatProbability(Number(value))}</strong>
        </div>
      `,
    )
    .join("");
}

function renderWeightedSummary(result: WeightedVoteResult) {
  const weightSourceLabel =
    result.data.weightSource === "backtest" ? "Initial weights: 1m backtest seeded." : result.data.weightSource === "daily" ? "Weights: daily smoothed from backtest seed." : "Weights: waiting for backtest seed.";
  return `
    <span>Raw winner: ${result.rawWinner}; final signal: ${result.signal}.</span>
    <span>Confidence rule: max score ${formatProbability(result.maxScore)}, second-place margin ${formatProbability(result.margin)}.</span>
    <span>Expected value: ${formatBasisPoints(result.expectedReturnAfterCosts)} after slippage/liquidity estimate.</span>
    <span>Meta-label probability: ${formatProbability(result.metaLabelProbability)}.</span>
    <span>${escapeHtml(weightSourceLabel)}</span>
    <span>${escapeHtml(result.data.updatePolicy)}</span>
  `;
}

function renderWeightedStrategies(strategies: WeightedAlphaResult[]) {
  return `
    <table class="weighted-strategy-table">
      <tbody>
        ${strategies
          .map(
            (strategy, strategyIndex) => `
              <tr class="weighted-strategy-name-row" data-tone="${strategyIndex % 4}" data-disabled="${String(Boolean(strategy.disabledReason))}">
                <td colspan="5">${escapeHtml(strategy.name)}</td>
                <td>${formatProbability(strategy.finalWeight)}</td>
              </tr>
              <tr class="weighted-strategy-detail-row" data-tone="${strategyIndex % 4}" data-disabled="${String(Boolean(strategy.disabledReason))}">
                ${renderWeightedMetricCell("Buy", formatProbability(strategy.pBuy))}
                ${renderWeightedMetricCell("Sell", formatProbability(strategy.pSell))}
                ${renderWeightedMetricCell("Hold", formatProbability(strategy.pHold))}
                ${renderWeightedMetricCell("EV", `${formatBasisPoints(strategy.expectedReturnAfterCosts)} / ${strategy.recentExpectancy === null ? "new" : formatBasisPoints(strategy.recentExpectancy)}`)}
                ${renderWeightedMetricCell("Quality", `${formatProbability(strategy.signalQuality)} / DD ${formatBasisPoints(strategy.recentDrawdown)}`)}
                ${renderWeightedMetricCell("Strength", `${formatProbability(strategy.strength)} / ${strategy.tradeCount}/${weightedMinimumTradesForFullWeight}`)}
              </tr>
            `,
          )
          .join("")}
      </tbody>
    </table>
  `;
}

function renderWeightedMetricCell(label: string, value: string) {
  return `
    <td>
      <small>${escapeHtml(label)}</small>
      <strong>${escapeHtml(value)}</strong>
    </td>
  `;
}

function renderWeightedDataGrid(result: WeightedVoteResult) {
  const latest = result.data.latest;
  const bands = result.data.bollinger;
  return [
    ["OHLCV", latest ? `O ${price(latest.open)} H ${price(latest.high)} L ${price(latest.low)} C ${price(latest.close)} V ${latest.volume.toLocaleString()}` : "NA"],
    ["VWAP", result.data.vwap === null ? "NA" : price(result.data.vwap)],
    ["ATR", result.data.atr === null ? "NA" : price(result.data.atr)],
    ["Bollinger", bands ? `${price(bands.lower)} / ${price(bands.middle)} / ${price(bands.upper)}` : "NA"],
    ["Relative Strength", `${formatBasisPoints(result.data.relativeStrength)} ${result.data.relativeStrengthSource}`],
    ["Breadth", `${formatProbability((result.data.breadth + 1) / 2)} ${result.data.breadthSource}`],
    ["Regime", `${result.data.trendDirection.toUpperCase()} / ${escapeHtml(result.data.volatilityLevel)} / ${escapeHtml(result.data.rangeCondition)}`],
    ["5m Confirm", result.data.timeframes.fiveMinute.source],
    ["Trend Source", result.data.trendSource],
    ["Session", result.data.sessionLabel],
  ]
    .map(
      ([label, value]) => `
        <span>
          <b>${escapeHtml(label)}</b>
          ${escapeHtml(value)}
        </span>
      `,
    )
    .join("");
}

function renderWeightedGate(gate: WeightedVoteResult["gates"][number]) {
  return `
    <span data-status="${gate.status}">
      <b>${escapeHtml(gate.label)}</b>
      ${escapeHtml(gate.detail)}
    </span>
  `;
}

function renderWeightedControlRules() {
  return `
    <span>Daily update: active weights stay locked during market hours; save one smoothed update after the close.</span>
    <span>Smoothing: new weight = 70% old weight + 30% recalculated weight.</span>
    <span>Caps: maximum one-strategy weight 25%; minimum active weight 2%; disabled strategies receive 0%.</span>
    <span>Eligibility: all strategies start live-eligible; require 40 live triple-barrier outcomes before disabling negative rolling expectancy.</span>
    <span>Short-cycle flow: 1m generates signals and 5m confirms them.</span>
    <span>Risk trims: reduce weight for observed drawdown, poor regime match, 5m conflict, and high correlation with a stronger strategy.</span>
    <span>Final gates: confidence, 5m confirmation, expected value, meta-label, triple barrier, daily loss, and slippage must pass.</span>
  `;
}

function strategyEnsembleSignals(context: MarketContext | null): AlgoVote[] {
  const strategies = context?.strategies ?? [];
  return strategyVoteCatalog.map((name) => {
    const strategy = strategies.find((item) => item.name === name);
    return strategyVote(name, strategy, context);
  });
}

const strategyVoteCatalog = [
  "Multi-Timeframe Trend Alignment",
  "First Pullback After Open",
  "Failed Breakout Strategy",
  "Liquidity Sweep Reversal",
  "Bollinger Band Reversion",
  "ATR Overextension Reversion",
  "Relative Strength vs QQQ/IWM",
  "Market Breadth Momentum",
  "Economic Event Reaction Strategy",
  "Ensemble Strategy Voting",
] as const;

function strategyVote(name: (typeof strategyVoteCatalog)[number], strategy: StrategyFit | undefined, context: MarketContext | null): AlgoVote {
  const inactive = !strategy || strategy.status === "Avoid" || strategy.score < 45;
  const watch = !strategy || strategy.status === "Watch" || strategy.score < 62;
  const status = strategy?.status ?? "Avoid";
  const score = strategy?.score ?? 0;
  const hold = (detail: string): AlgoVote => ({ strategy: name, signal: "Hold", detail, status, score });
  const vote = (signal: AlgoSignal, detail: string): AlgoVote => ({ strategy: name, signal, detail, status, score });

  if (!context) {
    return hold("Waiting for market context");
  }
  if (inactive) {
    return hold(`${status} ${score}% is below action threshold`);
  }
  if (watch) {
    return hold(`${status} ${score}% needs confirmation`);
  }

  switch (name) {
    case "Multi-Timeframe Trend Alignment":
      return vote(directionalSignal(context.session.directionBias, context.event.directionBias), `${status} ${score}% aligns intraday session and event trend`);
    case "First Pullback After Open":
      return vote(directionalSignal(context.session.directionBias, context.event.directionBias), `${status} ${score}% waits for first intraday trend pullback`);
    case "Failed Breakout Strategy":
      return vote(oppositeSignal(context.event.directionBias), `${status} ${score}% fades failed range expansion`);
    case "Liquidity Sweep Reversal":
      return vote(oppositeSignal(context.session.directionBias), `${status} ${score}% fades liquidity sweep`);
    case "Bollinger Band Reversion":
      return vote(oppositeSignal(context.session.directionBias), `${status} ${score}% fades band extension`);
    case "ATR Overextension Reversion":
      return vote(oppositeSignal(context.session.directionBias), `${status} ${score}% fades ATR overextension`);
    case "Relative Strength vs QQQ/IWM":
      return vote(directionalSignal(context.session.directionBias, context.event.directionBias), `${status} ${score}% follows intraday relative-strength proxy`);
    case "Market Breadth Momentum":
      return vote(directionalSignal(context.session.directionBias, context.event.directionBias), `${status} ${score}% follows intraday breadth-momentum proxy`);
    case "Economic Event Reaction Strategy":
      return vote(directionalSignal(context.event.directionBias, context.session.directionBias), `${status} ${score}% follows event reaction`);
    case "Ensemble Strategy Voting": {
      const directional = directionalSignal(context.session.directionBias, context.event.directionBias);
      return vote(directional, `${status} ${score}% confirms ensemble alignment`);
    }
  }
}

function renderAlgoVoteRow(vote: AlgoVote) {
  const statusText = vote.status && typeof vote.score === "number" ? ` - ${vote.status} ${vote.score}%` : "";
  const eligible = isEligibleStrategyVote(vote);
  return `
    <article class="algo-vote-card" data-signal="${vote.signal.toLowerCase()}" data-eligible="${String(eligible)}">
      <div>
        <strong>${escapeHtml(vote.strategy)}</strong>
        <span>${escapeHtml(`${vote.detail}${statusText}`)}</span>
      </div>
      <b class="algo-signal-badge">${eligible ? vote.signal : "Excluded"}</b>
    </article>
  `;
}

function renderTradingRagPlan(finalSignal: AlgoSignal, buyVotes: number, sellVotes: number, holdVotes: number) {
  return `
    <span>Question: Given today's SPY condition and current strategy votes, which strategy historically worked best?</span>
    <span>Current winner vote: ${escapeHtml(finalSignal)} (${buyVotes}B / ${sellVotes}S / ${holdVotes}H).</span>
    <span>Uses stored backtests, trade logs, diagnostics, and strategy summaries from the local artifact corpus.</span>
    <span>Target Order Submit controls Buy entries; Order Controls Submit controls Sell exits.</span>
  `;
}

function renderTradingRagResults() {
  if (state.tradingRagStatus === "loading") {
    const localOrder = syncCurrentTargetOrderFromTradingRag();
    return `
      ${renderManualOrderRecommendation(localOrder)}
      <div class="trading-rag-card loading">
        <strong>Trading RAG</strong>
        <span>Retrieving historical trading results. Voting Ensemble is using live local gates until RAG is ready.</span>
      </div>
    `;
  }
  if (state.tradingRagStatus === "error" || !state.tradingRag) {
    const localOrder = syncCurrentTargetOrderFromTradingRag();
    return `
      ${renderManualOrderRecommendation(localOrder)}
      <div class="trading-rag-card warning">
        <strong>Trading RAG unavailable</strong>
        <span>${escapeHtml(state.tradingRagWarning || "Trading RAG endpoint unavailable.")} Voting Ensemble is using live local gates.</span>
      </div>
    `;
  }
  const answer = state.tradingRag.answer;
  const retrieved = state.tradingRag.retrieved ?? [];
  const bestMatch = retrieved[0];
  const manualOrder = syncCurrentTargetOrderFromTradingRag();
  if (!manualOrder) {
    return `
      <div class="trading-rag-card warning">
        <strong>Trading RAG unavailable</strong>
        <span>Target order could not be generated from the current trading context.</span>
      </div>
    `;
  }
  return `
    ${renderManualOrderRecommendation(manualOrder)}
    <div class="trading-rag-card">
      <div class="trading-rag-head">
        <strong>${escapeHtml(answer.bias)} bias - ${escapeHtml(answer.confidence)} confidence</strong>
        <span>${escapeHtml(state.tradingRag.source)} · ${state.tradingRag.corpus.documentCount} docs</span>
      </div>
      <p>${escapeHtml(answer.conclusion)}</p>
      <span>Best historical match: ${escapeHtml(answer.bestHistoricalMatch)}</span>
      ${state.tradingRag.warning ? `<span class="trading-rag-warning">${escapeHtml(state.tradingRag.warning)}</span>` : ""}
    </div>
    ${renderTradingRagComparison(bestMatch, answer)}
    <div class="trading-rag-grid">
      ${renderTradingRagList("Drivers", answer.drivers)}
      ${renderTradingRagList("Risks", answer.risks)}
      ${renderTradingRagList("Action Plan", tradingRagActionPlan(answer.actionPlan, manualOrder))}
    </div>
    <div class="trading-rag-sources">
      <strong>Retrieved Historical Matches</strong>
      ${retrieved.length ? retrieved.map(renderTradingRagSource).join("") : "<span>No matching source documents found.</span>"}
    </div>
  `;
}

function syncCurrentTargetOrderFromTradingRag() {
  const answer = state.tradingRagStatus === "ready" && state.tradingRag ? state.tradingRag.answer : localVotingEnsembleFallbackAnswer();
  const bestMatch = state.tradingRagStatus === "ready" && state.tradingRag ? (state.tradingRag.retrieved ?? [])[0] : undefined;
  const manualOrder = manualOrderRecommendation(bestMatch, answer);
  state.currentTargetOrder = manualOrder;
  return manualOrder;
}

function localVotingEnsembleFallbackAnswer(): TradingRagResponse["answer"] {
  const summary = votingEnsembleScoreSummary();
  const buyScore = formatProbability(summary.scores.Buy);
  const sellScore = formatProbability(summary.scores.Sell);
  const holdScore = formatProbability(summary.scores.Hold);
  return {
    conclusion: `Local Voting Ensemble winner is ${summary.winner}.`,
    bias: summary.winner,
    confidence: summary.winner === "Hold" ? "low" : "live",
    bestHistoricalMatch: "Local live ensemble gates",
    drivers: [`Votes B/S/H ${buyScore}/${sellScore}/${holdScore}`, "Market permission gate", "Short-cycle direction gate", "Execution gate"],
    risks: ["Historical RAG context is not ready", "Use live gates and target-order risk controls"],
    actionPlan: ["Use the local Voting Ensemble target order until RAG context is available."],
  };
}

function renderTradingRagComparison(bestMatch: TradingRagResponse["retrieved"][number] | undefined, answer: TradingRagResponse["answer"]) {
  const votes = strategyEnsembleSignals(state.marketContext);
  const eligibleVotes = votes.filter(isEligibleStrategyVote);
  const buyVotes = eligibleVotes.filter((vote) => vote.signal === "Buy").length;
  const sellVotes = eligibleVotes.filter((vote) => vote.signal === "Sell").length;
  const holdVotes = eligibleVotes.filter((vote) => vote.signal === "Hold").length;
  const currentWinner = winningVoteSignal(buyVotes, sellVotes, holdVotes);
  const historicalBias = bestMatch ? historicalBiasFromRagSource(bestMatch, answer.bias) : "Mixed";
  const alignment =
    historicalBias === "Mixed" || historicalBias === "Hold"
      ? "Reference"
      : historicalBias === currentWinner
        ? "Aligned"
        : "Divergence";
  const metrics = bestMatch?.metrics ?? {};
  const metricParts = [
    typeof metrics.trades === "number" ? `${metrics.trades} trades` : "",
    typeof metrics.pnl === "number" ? `${signedCurrency(metrics.pnl)} P/L` : "",
    typeof metrics.profitFactor === "number" ? `PF ${metrics.profitFactor}` : "",
    typeof metrics.maxDrawdown === "number" ? `${currency(metrics.maxDrawdown)} DD` : "",
  ].filter(Boolean);
  return `
    <div class="trading-rag-compare" data-alignment="${alignment.toLowerCase()}" data-winner="${escapeHtml(currentWinner.toLowerCase())}">
      <strong>Current Winner vs Best Historical Timeframe</strong>
      <div class="trading-rag-compare-grid">
        <span data-signal="${escapeHtml(currentWinner.toLowerCase())}">Current vote winner<b>${escapeHtml(currentWinner)}</b><small>${buyVotes}B / ${sellVotes}S / ${holdVotes}H</small></span>
        <span>Best historical timeframe<b>${escapeHtml(bestMatch?.timeframe || "NA")}</b><small>${escapeHtml(bestMatch?.title || answer.bestHistoricalMatch || "No match")}</small></span>
        <span data-signal="${escapeHtml(historicalBias.toLowerCase())}">Historical bias<b>${escapeHtml(historicalBias)}</b><small>${metricParts.length ? escapeHtml(metricParts.join(" · ")) : "Metrics unavailable"}</small></span>
        <span>Comparison<b>${alignment}</b><small>${tradingRagAlignmentText(alignment, currentWinner, historicalBias)}</small></span>
      </div>
    </div>
  `;
}

function updateTradingSettingsMount(order?: ManualOrderRecommendation, options: { preserveExisting?: boolean } = {}) {
  if (options.preserveExisting && tradingSettingsMount.innerHTML.trim()) {
    return;
  }
  if (isEditingWithin(tradingSettingsMount)) {
    return;
  }
  const key = JSON.stringify({
    expanded: state.tradingSettingsExpanded,
    defaultSizingExpanded: state.votingDefaultSizingExpanded,
    settings: state.tradingSettings,
    artifactStatus: state.dynamicArtifactStatus,
    artifactSettingsKey: state.dynamicArtifactSettingsKey,
    artifactId: state.dynamicArtifact?.artifactId ?? "",
    order: order ?? null,
  });
  if (key === tradingSettingsMountKey) {
    return;
  }
  tradingSettingsMountKey = key;
  tradingSettingsMount.innerHTML = renderTradingSettingsPanel(order);
}

function isEditingWithin(container: HTMLElement) {
  const activeElement = document.activeElement;
  return (
    activeElement instanceof HTMLElement &&
    container.contains(activeElement) &&
    activeElement.matches("input, select, textarea")
  );
}

function renderTradingSettingsPanel(order?: ManualOrderRecommendation) {
  const settings = state.tradingSettings;
  const artifact = state.dynamicArtifact;
  const settingsKey = tradingSettingsKey(settings);
  const artifactMatches = state.dynamicArtifactStatus === "ready" && state.dynamicArtifactSettingsKey === settingsKey;
  const status = dynamicArtifactStatusLabel();
  const best = artifact?.mlComparison?.bestByTimeframe?.find((row) => row.verdict === "Improved") ?? artifact?.mlComparison?.bestByTimeframe?.[0];
  const expanded = state.tradingSettingsExpanded;
  return `
    <div class="trading-settings-panel" data-status="${escapeHtml(status.toLowerCase().replaceAll(" ", "-"))}" data-expanded="${String(expanded)}">
      <button id="tradingSettingsToggle" class="trading-settings-head" type="button" aria-expanded="${String(expanded)}" aria-controls="tradingSettingsBody">
        <span class="trading-settings-title">
          <b>${expanded ? "-" : "+"}</b>
          <strong>Trading Settings</strong>
        </span>
        <span class="trading-settings-summary">${escapeHtml(dynamicArtifactSummaryText(artifactMatches, best))}</span>
      </button>
      <div id="tradingSettingsBody" class="trading-settings-body" ${expanded ? "" : "hidden"}>
        <div class="trading-settings-grid">
          ${renderTradingSettingInput("startingCapital", "Total balance", settings.startingCapital, 1000, 10000000, 100)}
          ${renderTradingSettingInput("orderAllocationPercent", "Order limit %", settings.orderAllocationPercent, 0.1, VOTING_MAX_ORDER_ALLOCATION_PERCENT, 0.1)}
          ${renderTradingSettingInput("dailyAllocationPercent", "Daily max %", settings.dailyAllocationPercent, 0.1, 100, 0.1)}
          ${renderTradingSettingInput("riskBudgetPercentOfOrder", "Risk budget %", settings.riskBudgetPercentOfOrder, 0.1, 100, 0.1)}
          ${renderTradingSettingInput("maxTradesPerDay", "Max trades/day", settings.maxTradesPerDay, 1, 50, 1)}
          ${renderTradingSettingInput("fixedStopDistanceDollars", "Stop $/share", settings.fixedStopDistanceDollars, 0, 100, 0.01)}
          ${renderTradingSettingInput("stopLossPercent", "Stop %", settings.stopLossPercent, 0.01, 20, 0.01)}
          ${renderTradingSettingInput("takeProfitR", "Target R", settings.takeProfitR, 0.1, 20, 0.1)}
          ${renderTradingSettingInput("slippagePerShare", "Slippage/share", settings.slippagePerShare, 0, 10, 0.01)}
        </div>
        ${order ? renderTargetOrderSettings(order) : ""}
        ${renderTradingDefaultSizingSection(settings)}
      </div>
    </div>
  `;
}

function updateWeightedTradingSettingsMount(result?: WeightedVoteResult) {
  const order = result ? weightedTargetOrderRecommendation(result) : null;
  state.currentWeightedTargetOrder = order;
  const key = JSON.stringify({
    expanded: state.weightedTradingSettingsExpanded,
    defaultSizingExpanded: state.weightedDefaultSizingExpanded,
    settings: state.weightedTradingSettings,
    overrides: state.weightedTargetOrderOverrides,
    signal: result?.signal ?? "",
    maxScore: result?.maxScore ?? 0,
    margin: result?.margin ?? 0,
    order,
  });
  if (key === weightedTradingSettingsMountKey) {
    return;
  }
  if (isEditingWithin(weightedTradingSettingsMount)) {
    return;
  }
  weightedTradingSettingsMountKey = key;
  weightedTradingSettingsMount.innerHTML = renderWeightedTradingSettingsPanel(result, order);
}

function renderWeightedTradingSettingsPanel(result?: WeightedVoteResult, order?: ManualOrderRecommendation | null) {
  const settings = state.weightedTradingSettings;
  const expanded = state.weightedTradingSettingsExpanded;
  return `
    <div class="trading-settings-panel weighted-trading-settings-panel" data-status="ready" data-expanded="${String(expanded)}">
      <button id="weightedTradingSettingsToggle" class="trading-settings-head" type="button" aria-expanded="${String(expanded)}" aria-controls="weightedTradingSettingsBody">
        <span class="trading-settings-title">
          <b>${expanded ? "-" : "+"}</b>
          <strong>Trading Settings</strong>
        </span>
        <span class="trading-settings-summary">${escapeHtml(weightedTradingSettingsSummary(settings, result))}</span>
      </button>
      <div id="weightedTradingSettingsBody" class="trading-settings-body" ${expanded ? "" : "hidden"}>
        <div class="trading-settings-grid">
          ${renderWeightedTradingSettingInput("startingCapital", "Total balance", settings.startingCapital, 1000, 10000000, 100)}
          ${renderWeightedTradingSettingInput("orderAllocationPercent", "Order limit %", settings.orderAllocationPercent, 0.1, MAX_ORDER_ALLOCATION_PERCENT, 0.1)}
          ${renderWeightedTradingSettingInput("dailyAllocationPercent", "Daily max %", settings.dailyAllocationPercent, 0.1, 100, 0.1)}
          ${renderWeightedTradingSettingInput("riskBudgetPercentOfOrder", "Risk budget %", settings.riskBudgetPercentOfOrder, 0.1, 100, 0.1)}
          ${renderWeightedTradingSettingInput("maxTradesPerDay", "Max trades/day", settings.maxTradesPerDay, 1, 50, 1)}
          ${renderWeightedTradingSettingInput("fixedStopDistanceDollars", "Stop $/share", settings.fixedStopDistanceDollars, 0, 100, 0.01)}
          ${renderWeightedTradingSettingInput("stopLossPercent", "Stop %", settings.stopLossPercent, 0.01, 20, 0.01)}
          ${renderWeightedTradingSettingInput("takeProfitR", "Target R", settings.takeProfitR, 0.1, 20, 0.1)}
          ${renderWeightedTradingSettingInput("slippagePerShare", "Slippage/share", settings.slippagePerShare, 0, 10, 0.01)}
        </div>
        ${order ? renderWeightedTargetOrderSettings(order) : ""}
        ${renderWeightedDefaultSizingSection(settings)}
      </div>
    </div>
  `;
}

function weightedTargetOrderRecommendation(result: WeightedVoteResult): ManualOrderRecommendation {
  const settings = state.weightedTradingSettings;
  const latest = result.data.latest ?? latestExecutionCandleForMode("weighted");
  const submitMode = (state.weightedTargetOrderOverrides.submitMode as SubmitOrderMode | undefined) ?? DEFAULT_SUBMIT_MODE;
  const automaticShortCycleBuy = submitMode === "Automatic";
  const requestedSide: AlgoSignal = automaticShortCycleBuy ? weightedAutomaticTargetSide(result, latest) : result.signal;
  const sizing =
    latest && (requestedSide === "Buy" || requestedSide === "Sell")
      ? weightedTargetSizing(result, latest, requestedSide, automaticShortCycleBuy)
      : null;
  const targetFailedGates = weightedTargetOrderFailedGates(result, automaticShortCycleBuy, requestedSide);
  const side = targetFailedGates.length ? "Hold" : sizing?.side ?? "Hold";
  const accountBalance = sizing?.accountEquity ?? settings.startingCapital;
  const orderLimitDollars = accountBalance * (settings.orderAllocationPercent / 100);
  const dailyLimitDollars = sizing?.availableBuyingPower ?? accountBalance * (settings.dailyAllocationPercent / 100);
  const riskDollars = sizing?.riskDollars ?? orderLimitDollars * (settings.riskBudgetPercentOfOrder / 100);
  const failedGates = targetFailedGates.map((gate) => `${gate.label}: ${gate.detail}`);
  if (sizing?.blockedReason) {
    failedGates.push(`Weighted sizing: ${sizing.blockedReason}`);
  }
  const levels = {
    last: latest?.close ?? null,
    vwap: result.data.vwap,
    openingHigh: null,
    openingLow: null,
    lastTime: latest?.timestamp ?? null,
  };
  if (side === "Hold" || !latest || !sizing) {
    const blockedCandidate =
      latest && (requestedSide === "Buy" || requestedSide === "Sell")
        ? weightedBlockedCandidateOrderLevels(result, latest, requestedSide)
        : null;
    return applyWeightedTargetOrderOverrides({
      eligible: false,
      side,
      orderType: "No order",
      symbol: state.symbol,
      quantity: 0,
      triggerPrice: blockedCandidate?.triggerPrice ?? null,
      limitPrice: blockedCandidate?.limitPrice ?? null,
      stopPrice: blockedCandidate?.stopPrice ?? null,
      targetPrice: blockedCandidate?.targetPrice ?? null,
      accountBalance,
      orderLimitDollars,
      dailyLimitDollars,
      riskDollars,
      orderNotional: 0,
      plannedStopRiskDollars: 0,
      estimatedSlippage: 0,
      timeInForce: "Day",
      cutoff: "No new trades after 15:30 ET",
      submitMode,
      failedGates: uniqueStrings(failedGates),
      gates: weightedTargetOrderGates(result),
      levels,
      summary: `No order: ${latest ? "final signal is Hold or risk gates failed" : "latest price unavailable"}.`,
    });
  }

  const directionalSizing = sizing;
  const triggerPrice = directionalSizing.entryPrice;
  const limitPrice =
    side === "Buy"
      ? roundNumber(directionalSizing.ask + settings.slippagePerShare, 2)
      : roundNumber(Math.max(0, directionalSizing.bid - settings.slippagePerShare), 2);
  const stopPrice =
    side === "Buy"
      ? roundNumber(directionalSizing.entryPrice - directionalSizing.stopDistance, 2)
      : roundNumber(directionalSizing.entryPrice + directionalSizing.stopDistance, 2);
  const quantity = directionalSizing.quantity;
  const targetDistance = targetProfitDistancePerShare(quantity, directionalSizing.stopDistance, settings.takeProfitR);
  const targetPrice =
    side === "Buy"
      ? roundNumber(directionalSizing.entryPrice + targetDistance, 2)
      : roundNumber(Math.max(0, directionalSizing.entryPrice - targetDistance), 2);
  const orderNotional = roundNumber(quantity * triggerPrice, 2);
  const plannedStopRiskDollars = roundNumber(quantity * directionalSizing.stopDistance, 2);
  const estimatedSlippage = roundNumber(quantity * (directionalSizing.ask - directionalSizing.bid), 2);
  const nextFailedGates = [...failedGates];
  if (quantity < 1) {
    nextFailedGates.push("weighted sizing quantity below 1 share");
  }
  if (plannedStopRiskDollars > riskDollars) {
    nextFailedGates.push(`planned stop risk exceeds ${directionalSizing.baseRiskPercent}% account risk budget`);
  }
  const eligible = nextFailedGates.length === 0;
  const orderType = eligible ? `${side} stop-limit` : "No order";
  const orderLine = `${side} stop-limit ${state.symbol}, ${quantity} shares, stop ${price(triggerPrice)}, limit ${price(limitPrice)}, protective stop ${price(stopPrice)}, target ${price(targetPrice)}.`;
  return applyWeightedTargetOrderOverrides({
    eligible,
    side,
    orderType,
    symbol: state.symbol,
    quantity,
    triggerPrice,
    limitPrice,
    stopPrice,
    targetPrice,
    accountBalance,
    orderLimitDollars,
    dailyLimitDollars,
    riskDollars,
    orderNotional,
    plannedStopRiskDollars,
    estimatedSlippage,
    timeInForce: "Day",
    cutoff: "No new trades after 15:30 ET",
    submitMode,
    failedGates: uniqueStrings(nextFailedGates),
    gates: weightedTargetOrderGates(result),
    levels,
    summary: eligible
      ? `Order template: ${orderLine} Uses ${currency(orderNotional)} of the ${currency(orderLimitDollars)} per-order limit.`
      : `No order: Wait. Failed gates: ${uniqueStrings(nextFailedGates).join(", ")}.`,
  });
}

function weightedBlockedCandidateOrderLevels(
  result: WeightedVoteResult,
  latest: Candle,
  side: Exclude<AlgoSignal, "Hold">,
) {
  const settings = state.weightedTradingSettings;
  const defaults = weightedDefaultSizingSettings();
  const bid = roundNumber(Math.max(0, latest.close - settings.slippagePerShare), 2);
  const ask = roundNumber(latest.close + settings.slippagePerShare, 2);
  const triggerPrice = side === "Buy" ? ask : bid;
  const limitPrice =
    side === "Buy"
      ? roundNumber(triggerPrice + settings.slippagePerShare, 2)
      : roundNumber(Math.max(0, triggerPrice - settings.slippagePerShare), 2);
  const stopDistance = defaultSizingStopDistance(defaults, latest.close, result.data.atr ?? 0);
  const stopPrice =
    side === "Buy"
      ? roundNumber(Math.max(0, triggerPrice - stopDistance), 2)
      : roundNumber(triggerPrice + stopDistance, 2);
  const targetDistance = Math.max(stopDistance * settings.takeProfitR, MIN_TARGET_PROFIT_PER_SHARE);
  const targetPrice =
    side === "Buy"
      ? roundNumber(triggerPrice + targetDistance, 2)
      : roundNumber(Math.max(0, triggerPrice - targetDistance), 2);
  return { triggerPrice, limitPrice, stopPrice, targetPrice };
}

function weightedTargetOrderFailedGates(result: WeightedVoteResult, automaticShortCycleBuy: boolean, requestedSide: AlgoSignal) {
  const failed = result.gates.filter((gate) => gate.status === "fail");
  if (!automaticShortCycleBuy) {
    return failed;
  }
  const shortCycleContextGates = new Set([
    "Confidence",
    "Active Strategies",
    "Directional Strategy Count",
    "Buy Strategy Count",
    "5m Confirmation",
    "Expected Value",
    "Meta Label",
    "Triple Barrier",
    "Event Risk",
  ]);
  const hardFailures = failed.filter((gate) => !shortCycleContextGates.has(gate.label));
  if (requestedSide === "Hold") {
    hardFailures.push({
      label: "Weighted Decision",
      status: "fail",
      detail: "No automatic short-cycle Buy/Sell side is available",
    });
  }
  const latest = result.data.latest;
  const vwap = result.data.vwap;
  if (latest && requestedSide === "Buy") {
    const lateSessionBlocker = lateSessionAboveAverageBuyBlocker("weighted", latest.close, latest.timestamp);
    if (lateSessionBlocker) {
      hardFailures.push({
        label: "Weighted Late-session Buy Guard",
        status: "fail",
        detail: lateSessionBlocker,
      });
    }
    forecastBuySafetyBlockers("weighted", latest.close, latest.timestamp).forEach((detail) => {
      hardFailures.push({
        label: "Weighted Forecast Safety",
        status: "fail",
        detail,
      });
    });
  }
  if (!latest || vwap === null) {
    hardFailures.push({
      label: "Short-cycle VWAP",
      status: "fail",
      detail: "Waiting for live price and VWAP",
    });
  } else if (requestedSide === "Buy" && latest.close <= vwap) {
    hardFailures.push({
      label: "Short-cycle VWAP",
      status: "fail",
      detail: `Close ${price(latest.close)} is not above VWAP ${price(vwap)}`,
    });
  } else if (requestedSide === "Sell" && latest.close >= vwap) {
    hardFailures.push({
      label: "Short-cycle VWAP",
      status: "fail",
      detail: `Close ${price(latest.close)} is not below VWAP ${price(vwap)}`,
    });
  }
  return hardFailures;
}

function weightedAutomaticTargetSide(result: WeightedVoteResult, latest: Candle | null): AlgoSignal {
  const vwap = result.data.vwap;
  if (latest && vwap !== null) {
    if (latest.close > vwap) {
      return "Buy";
    }
    if (latest.close < vwap) {
      return "Sell";
    }
  }
  return result.rawWinner === "Buy" || result.rawWinner === "Sell" ? result.rawWinner : "Hold";
}

function weightedTargetSizing(
  result: WeightedVoteResult,
  latest: Candle,
  side: Exclude<AlgoSignal, "Hold">,
  automaticShortCycleBuy = false,
) {
  const settings = state.weightedTradingSettings;
  const defaults = weightedDefaultSizingSettings();
  const totalScore = result.buyScore + result.holdScore + result.sellScore;
  const accountEquity = settings.startingCapital;
  const priceValue = latest.close;
  const bid = roundNumber(Math.max(0, priceValue - settings.slippagePerShare), 2);
  const ask = roundNumber(priceValue + settings.slippagePerShare, 2);
  const currentPosition = summarizePositionFromTradeHistory(priceValue, priceValue, "weighted");
  const currentPositionShares = currentPosition.shares;
  const maxPositionDollars = accountEquity * (defaults.maxPositionPercent / 100);
  const maxOrderDollars = accountEquity * (settings.orderAllocationPercent / 100);
  const dailyBuyingPowerDollars = accountEquity * (settings.dailyAllocationPercent / 100);
  const availableBuyingPower = Math.max(0, Math.min(maxPositionDollars, dailyBuyingPowerDollars) - currentPosition.marketValue);
  const atrOneMinute = result.data.atr ?? 0;
  const currentOneMinuteVolume = latest.volume;
  const averageOneMinuteVolume = result.data.averageVolume;
  const dailyRealizedPnl = weightedTodayRealizedPnl();
  const tradesToday = effectiveTodaysTradeCount("weighted", defaults.maxDailyTrades, priceValue);
  const activeStrategyCount = result.strategies.filter((strategy) => strategy.finalWeight > 0 && !strategy.disabledReason).length;
  const directionalStrategyCount = result.strategies.filter((strategy) => strategy.finalWeight > 0 && weightedSignalSide(strategy) === side).length;

  const hold = (blockedReason: string) => ({
    side: "Hold" as AlgoSignal,
    quantity: 0,
    blockedReason,
    accountEquity,
    availableBuyingPower,
    currentPositionShares,
    price: priceValue,
    bid,
    ask,
    atrOneMinute,
    averageOneMinuteVolume: currentOneMinuteVolume,
    dailyRealizedPnl,
    tradesToday,
    maxPositionDollars,
    riskDollars: 0,
    baseRiskPercent: defaults.baseRiskPercent,
    stopDistance: 0,
    entryPrice: ask,
  });

  if (totalScore <= 0) {
    return hold("score total is zero");
  }

  const buyScore = result.buyScore / totalScore;
  const holdScore = result.holdScore / totalScore;
  const sellScore = result.sellScore / totalScore;
  const sideScore = side === "Buy" ? buyScore : sellScore;
  const oppositeScore = side === "Buy" ? sellScore : buyScore;
  const sideIsWinner = sideScore > holdScore && sideScore > oppositeScore;
  const secondBestScore = Math.max(holdScore, oppositeScore);
  const signalEdge = sideScore - secondBestScore;
  const vwap = result.data.vwap;
  const shortCycleConfirmed =
    automaticShortCycleBuy &&
    vwap !== null &&
    (side === "Buy" ? latest.close > vwap : latest.close < vwap);

  if (automaticShortCycleBuy) {
    if (!shortCycleConfirmed) {
      return hold(
        vwap === null
          ? "Waiting for live VWAP"
          : side === "Buy"
            ? `short-cycle close ${price(latest.close)} is not above VWAP ${price(vwap)}`
            : `short-cycle close ${price(latest.close)} is not below VWAP ${price(vwap)}`,
      );
    }
  } else {
    if (!sideIsWinner) {
      return hold(`winner is not ${side} (${formatProbability(Math.max(buyScore, holdScore, sellScore))})`);
    }
    if (activeStrategyCount < defaults.minimumActiveStrategies) {
      return hold(`active strategy count ${activeStrategyCount} is below ${defaults.minimumActiveStrategies}`);
    }
    if (directionalStrategyCount < defaults.minimumBuyStrategyCount) {
      return hold(`${side.toLowerCase()} strategy count ${directionalStrategyCount} is below ${defaults.minimumBuyStrategyCount}`);
    }
    if (sideScore < defaults.minimumBuyScore) {
      return hold(`${side} score ${formatProbability(sideScore)} is below ${formatProbability(defaults.minimumBuyScore)}`);
    }
    if (signalEdge < defaults.minimumSignalEdge) {
      return hold(`signal edge ${formatProbability(signalEdge)} is below ${formatProbability(defaults.minimumSignalEdge)}`);
    }
  }

  const spreadPct = priceValue ? (ask - bid) / priceValue : Number.POSITIVE_INFINITY;
  if (spreadPct > defaults.maxSpreadPercent / 100) {
    return hold(`spread ${formatProbability(spreadPct)} is above ${defaults.maxSpreadPercent}%`);
  }
  if (currentOneMinuteVolume < defaults.minimumOneMinuteVolume) {
    return hold(`1m volume ${Math.round(currentOneMinuteVolume).toLocaleString()} is below ${defaults.minimumOneMinuteVolume.toLocaleString()}`);
  }
  if (atrOneMinute < 0.03) {
    return hold(`ATR ${price(atrOneMinute)} is below 0.03`);
  }
  if (atrOneMinute > 1.5) {
    return hold(`ATR ${price(atrOneMinute)} is above 1.50`);
  }
  const stopDistance = defaultSizingStopDistance(defaults, priceValue, atrOneMinute);
  const entryQualityBlock = weightedEntryQualityBlockedReason(result, latest, side, stopDistance, automaticShortCycleBuy);
  if (entryQualityBlock) {
    return hold(entryQualityBlock);
  }

  if (dailyRealizedPnl <= -(accountEquity * (defaults.maxDailyLossPercent / 100))) {
    return hold(`daily realized P&L ${signedCurrency(dailyRealizedPnl)} reached loss limit`);
  }
  if (tradesToday >= defaults.maxDailyTrades) {
    return hold(`trades today ${tradesToday} reached max ${defaults.maxDailyTrades}`);
  }
  if (currentPositionShares > 0 && !defaults.pyramidingEnabled) {
    return hold("pyramiding disabled while a weighted position is open");
  }

  const fiveMinuteConfirms = automaticShortCycleBuy
    ? weightedAutomaticFiveMinuteConfirms(result, side)
    : result.gates.find((gate) => gate.label === "5m Confirmation")?.status === "pass";
  const contextSizeMultiplier =
    signalEdge < 0.2
      ? 0.25
      : signalEdge < 0.35
        ? 0.5
        : signalEdge < 0.5
          ? 0.75
          : 1;
  const sizeMultiplier = automaticShortCycleBuy
    ? Math.min(1, contextSizeMultiplier + (fiveMinuteConfirms ? 0.25 : 0))
    : signalEdge < 0.35
      ? 0.25
      : signalEdge < 0.5
        ? 0.5
        : signalEdge < 0.65
          ? 0.75
          : 1;
  const riskDollars = accountEquity * (defaults.baseRiskPercent / 100) * sizeMultiplier;
  const riskBasedShares = riskDollars / Math.max(stopDistance, 0.01);
  const orderBasedShares = maxOrderDollars / Math.max(priceValue, 0.01);
  const capitalBasedShares = maxPositionDollars / Math.max(priceValue, 0.01);
  const buyingPowerShares = availableBuyingPower / Math.max(priceValue, 0.01);
  const liquidityBasedShares = currentOneMinuteVolume * (defaults.maxParticipationPercent / 100);
  const maxAllowedShares = defaults.maxAllowedShares > 0 ? defaults.maxAllowedShares : Number.MAX_SAFE_INTEGER;
  const quantity = Math.floor(Math.min(riskBasedShares, orderBasedShares, capitalBasedShares, buyingPowerShares, liquidityBasedShares, maxAllowedShares));

  if (quantity < 1) {
    return hold(`final ${side.toLowerCase()} shares below 1`);
  }

  return {
    side,
    quantity,
    blockedReason: "",
    accountEquity,
    availableBuyingPower,
    currentPositionShares,
    price: priceValue,
    bid,
    ask,
    atrOneMinute,
    averageOneMinuteVolume: currentOneMinuteVolume,
    dailyRealizedPnl,
    tradesToday,
    maxPositionDollars,
    riskDollars,
    baseRiskPercent: defaults.baseRiskPercent,
    stopDistance,
    entryPrice: side === "Buy" ? ask : bid,
    signalEdge,
    sizeMultiplier,
    riskBasedShares,
    orderBasedShares,
    capitalBasedShares,
    buyingPowerShares,
    liquidityBasedShares,
  };
}

function weightedTargetOrderGates(result: WeightedVoteResult): TradeLayerGate[] {
  return result.gates.map((gate) => ({
    layer: gate.label,
    status: gate.status === "pass" ? "pass" : gate.status === "info" ? "info" : "fail",
    signal: result.rawWinner,
    detail: gate.detail,
  }));
}

function weightedEntryQualityBlockedReason(
  result: WeightedVoteResult,
  latest: Candle,
  side: Exclude<AlgoSignal, "Hold">,
  stopDistance: number,
  automaticShortCycleBuy: boolean,
) {
  const vwap = result.data.vwap;
  if (vwap === null) {
    return automaticShortCycleBuy ? "Waiting for live VWAP" : "";
  }
  const entryCushion = automaticShortCycleBuy
    ? weightedAutomaticEntryCushion(result, stopDistance)
    : Math.max(0.05, stopDistance);
  if (side === "Buy") {
    const requiredEntry = vwap + entryCushion;
    if (latest.close < requiredEntry) {
      return `entry ${price(latest.close)} is too close to VWAP ${price(vwap)}; need ${price(requiredEntry)} or higher`;
    }
  } else {
    const requiredEntry = vwap - entryCushion;
    if (latest.close > requiredEntry) {
      return `entry ${price(latest.close)} is too close to VWAP ${price(vwap)}; need ${price(requiredEntry)} or lower`;
    }
  }
  const fragileEntryReason = automaticShortCycleBuy ? weightedFragileEntryBlockedReason(latest, entryCushion, side) : "";
  if (fragileEntryReason) {
    return fragileEntryReason;
  }
  if (automaticShortCycleBuy) {
    const fiveMinute = result.data.timeframes.fiveMinute;
    const alignment = weightedSignalTimeframeAlignment(side, fiveMinute);
    if (alignment < 0) {
      return `5m confirmation is not strong enough: ${fiveMinute.source}`;
    }
  }
  return "";
}

function weightedAutomaticFiveMinuteConfirms(result: WeightedVoteResult, side: Exclude<AlgoSignal, "Hold">) {
  return weightedSignalTimeframeAlignment(side, result.data.timeframes.fiveMinute) >= 0;
}

function weightedAutomaticEntryCushion(result: WeightedVoteResult, stopDistance: number) {
  const atrCushion = (result.data.atr ?? stopDistance) * 0.5;
  return Math.max(0.05, Math.min(0.25, stopDistance * 0.25, atrCushion));
}

function weightedFragileEntryBlockedReason(latest: Candle, entryCushion: number, side: Exclude<AlgoSignal, "Hold">) {
  const sessionCandles = latestWeightedCalculationCandles();
  const recent = sessionCandles.slice(-6);
  if (recent.length < 4) {
    return "fragile entry: waiting for more recent 1m candles";
  }
  const prior = recent.at(-2)!;
  const priorTwo = recent.at(-3)!;
  const latestBody = latest.close - latest.open;
  if (side === "Buy") {
    if (latestBody < 0 && latest.close <= prior.close) {
      return `fragile entry: latest 1m candle is red and below prior close (${price(latest.open)} to ${price(latest.close)})`;
    }
    if (latest.close < prior.close || prior.close < priorTwo.close) {
      return `fragile entry: 1m momentum is weakening (${price(priorTwo.close)} -> ${price(prior.close)} -> ${price(latest.close)})`;
    }
    const recentLow = Math.min(...recent.slice(0, -1).map((candle) => candle.low));
    const lowCushion = latest.close - recentLow;
    const requiredLowCushion = Math.max(0.12, entryCushion * 1.25);
    if (lowCushion < requiredLowCushion) {
      return `fragile entry: only ${price(lowCushion)} above recent low; need ${price(requiredLowCushion)}`;
    }
    const recentHighClose = Math.max(...recent.slice(0, -1).map((candle) => candle.close));
    if (latest.close < recentHighClose) {
      return `fragile entry: close ${price(latest.close)} has not cleared recent close high ${price(recentHighClose)}`;
    }
    return "";
  }
  if (latestBody > 0 && latest.close >= prior.close) {
    return `fragile entry: latest 1m candle is green and above prior close (${price(latest.open)} to ${price(latest.close)})`;
  }
  if (latest.close > prior.close || prior.close > priorTwo.close) {
    return `fragile entry: 1m downside momentum is weakening (${price(priorTwo.close)} -> ${price(prior.close)} -> ${price(latest.close)})`;
  }
  const recentHigh = Math.max(...recent.slice(0, -1).map((candle) => candle.high));
  const highCushion = recentHigh - latest.close;
  const requiredHighCushion = Math.max(0.12, entryCushion * 1.25);
  if (highCushion < requiredHighCushion) {
    return `fragile entry: only ${price(highCushion)} below recent high; need ${price(requiredHighCushion)}`;
  }
  const recentLowClose = Math.min(...recent.slice(0, -1).map((candle) => candle.close));
  if (latest.close > recentLowClose) {
    return `fragile entry: close ${price(latest.close)} has not cleared recent close low ${price(recentLowClose)}`;
  }
  return "";
}

function applyWeightedTargetOrderOverrides(order: ManualOrderRecommendation): ManualOrderRecommendation {
  const overrides = state.weightedTargetOrderOverrides;
  const quantity = order.quantity;
  const triggerPrice = order.triggerPrice;
  const stopPrice = order.stopPrice;
  const orderNotional = triggerPrice !== null ? roundNumber(quantity * triggerPrice, 2) : order.orderNotional;
  const plannedStopRiskDollars = triggerPrice !== null && stopPrice !== null ? roundNumber(quantity * Math.abs(triggerPrice - stopPrice), 2) : order.plannedStopRiskDollars;
  const estimatedSlippage = roundNumber(quantity * state.weightedTradingSettings.slippagePerShare * 2, 2);
  return {
    ...order,
    symbol: String(overrides.symbol ?? order.symbol).toUpperCase(),
    orderType: String(overrides.orderType ?? order.orderType),
    quantity,
    triggerPrice,
    limitPrice: order.limitPrice,
    stopPrice,
    targetPrice: order.targetPrice,
    accountBalance: roundNumber(Number(overrides.accountBalance ?? order.accountBalance), 2),
    orderLimitDollars: roundNumber(Number(overrides.orderLimitDollars ?? order.orderLimitDollars), 2),
    dailyLimitDollars: roundNumberUp(Number(overrides.dailyLimitDollars ?? order.dailyLimitDollars), 2),
    riskDollars: roundNumber(order.riskDollars, 2),
    orderNotional,
    plannedStopRiskDollars,
    estimatedSlippage,
    timeInForce: String(overrides.timeInForce ?? order.timeInForce),
    cutoff: String(overrides.cutoff ?? order.cutoff),
    submitMode: (overrides.submitMode as SubmitOrderMode | undefined) ?? order.submitMode,
  };
}

function weightedTradingSettingsSummary(settings: TradingSettings, result?: WeightedVoteResult) {
  const signalText = result ? ` · ${result.signal} ${formatProbability(result.maxScore)}` : "";
  return `${currency(settings.startingCapital)} balance · ${settings.orderAllocationPercent}% order · ${settings.dailyAllocationPercent}% daily · ${settings.maxTradesPerDay}/day${signalText}`;
}

function renderTargetOrderSettings(order: ManualOrderRecommendation) {
  return `
    <div class="target-settings-panel" data-side="${escapeHtml(order.side.toLowerCase())}">
      <strong>Target Order</strong>
      ${renderTargetOrderBlockers(order)}
      <div class="target-settings-grid">
        ${renderTargetSettingInput("accountBalance", "Total balance", order.accountBalance, "number", 0.01)}
        ${renderTargetSettingInput("dailyLimitDollars", "Daily max allocation", order.dailyLimitDollars, "number", 0.01)}
        ${renderTargetSettingInput("orderLimitDollars", "Order limit", order.orderLimitDollars, "number", 0.01)}
        ${renderTargetSettingInput("orderNotional", "Order value", order.orderNotional, "number", 0.01)}
        ${renderTargetSettingInput("symbol", "Symbol", order.symbol, "text")}
        ${renderTargetSettingSelect("side", "Side", order.side, ["Buy", "Sell", "Hold"])}
        ${renderTargetSettingSelect("orderType", "Order type", order.orderType, ["No order", "Buy stop-limit", "Sell stop-limit"])}
        ${renderTargetSettingInput("quantity", "Quantity", order.quantity, "number", 1)}
        ${renderTargetSettingInput("triggerPrice", "Trigger / stop price", order.triggerPrice, "number", 0.01)}
        ${renderTargetSettingInput("limitPrice", "Limit price", order.limitPrice, "number", 0.01)}
        ${renderTargetSettingInput("stopPrice", "Protective stop", order.stopPrice, "number", 0.01)}
        ${renderTargetSettingInput("targetPrice", "Take profit", order.targetPrice, "number", 0.01)}
        ${renderTargetSettingInput("riskDollars", "Risk budget", order.riskDollars, "number", 0.01)}
        ${renderTargetSettingInput("plannedStopRiskDollars", "Planned stop risk", order.plannedStopRiskDollars, "number", 0.01)}
        ${renderTargetSettingInput("estimatedSlippage", "Estimated slippage", order.estimatedSlippage, "number", 0.01)}
        ${renderTargetSettingInput("timeInForce", "Time in force", order.timeInForce, "text", undefined, "half")}
        ${renderTargetSettingInput("cutoff", "Cutoff", order.cutoff, "text", undefined, "half")}
        ${renderTargetSettingSelect("submitMode", "Submit order", order.submitMode, ["Manual", "Automatic"], "half")}
      </div>
    </div>
  `;
}

function renderTargetOrderBlockers(order: ManualOrderRecommendation) {
  if (order.eligible || !order.failedGates.length) {
    return "";
  }
  return `
    <div class="target-order-blockers">
      <b>Blocked by</b>
      <span>${escapeHtml(order.failedGates.join(" | "))}</span>
    </div>
  `;
}

function renderTargetSettingInput(
  name: keyof TargetOrderSettings,
  label: string,
  value: string | number | null,
  type: "number" | "text",
  step?: number,
  layout?: "wide" | "half",
) {
  const inputValue = value === null ? "" : String(value);
  return `
    <label class="${layout ?? ""}">
      <span>${escapeHtml(label)}</span>
      <input data-target-setting="${escapeHtml(name)}" type="${type}" ${step ? `step="${step}"` : ""} value="${escapeHtml(inputValue)}" />
    </label>
  `;
}

function renderTargetSettingSelect(
  name: keyof TargetOrderSettings,
  label: string,
  value: string,
  options: string[],
  layout?: "wide" | "half",
) {
  return `
    <label class="${layout ?? ""}">
      <span>${escapeHtml(label)}</span>
      <select data-target-setting="${escapeHtml(name)}">
        ${options.map((option) => `<option value="${escapeHtml(option)}" ${option === value ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
      </select>
    </label>
  `;
}

function renderWeightedTargetOrderSettings(order: ManualOrderRecommendation) {
  return `
    <div class="target-settings-panel weighted-target-settings-panel" data-side="${escapeHtml(order.side.toLowerCase())}">
      <strong>Target Order</strong>
      ${renderTargetOrderBlockers(order)}
      <div class="target-settings-grid">
        ${renderWeightedTargetSettingInput("accountBalance", "Total balance", order.accountBalance, "number", 0.01)}
        ${renderWeightedTargetSettingInput("dailyLimitDollars", "Daily max allocation", order.dailyLimitDollars, "number", 0.01)}
        ${renderWeightedTargetSettingInput("orderLimitDollars", "Order limit", order.orderLimitDollars, "number", 0.01)}
        ${renderWeightedTargetSettingInput("orderNotional", "Order value", order.orderNotional, "number", 0.01)}
        ${renderWeightedTargetSettingInput("symbol", "Symbol", order.symbol, "text")}
        ${renderWeightedTargetSettingSelect("side", "Side", order.side, ["Buy", "Sell", "Hold"])}
        ${renderWeightedTargetSettingSelect("orderType", "Order type", order.orderType, ["No order", "Buy stop-limit", "Sell stop-limit"])}
        ${renderWeightedTargetSettingInput("quantity", "Quantity", order.quantity, "number", 1)}
        ${renderWeightedTargetSettingInput("triggerPrice", "Trigger / stop price", order.triggerPrice, "number", 0.01)}
        ${renderWeightedTargetSettingInput("limitPrice", "Limit price", order.limitPrice, "number", 0.01)}
        ${renderWeightedTargetSettingInput("stopPrice", "Protective stop", order.stopPrice, "number", 0.01)}
        ${renderWeightedTargetSettingInput("targetPrice", "Take profit", order.targetPrice, "number", 0.01)}
        ${renderWeightedTargetSettingInput("riskDollars", "Risk budget", order.riskDollars, "number", 0.01)}
        ${renderWeightedTargetSettingInput("plannedStopRiskDollars", "Planned stop risk", order.plannedStopRiskDollars, "number", 0.01)}
        ${renderWeightedTargetSettingInput("estimatedSlippage", "Estimated slippage", order.estimatedSlippage, "number", 0.01)}
        ${renderWeightedTargetSettingInput("timeInForce", "Time in force", order.timeInForce, "text", undefined, "half")}
        ${renderWeightedTargetSettingInput("cutoff", "Cutoff", order.cutoff, "text", undefined, "half")}
        ${renderWeightedTargetSettingSelect("submitMode", "Submit order", order.submitMode, ["Manual", "Automatic"], "half")}
      </div>
      <span class="trading-settings-warning">${escapeHtml(order.summary)}</span>
    </div>
  `;
}

function renderWeightedTargetSettingInput(
  name: keyof TargetOrderSettings,
  label: string,
  value: string | number | null,
  type: "number" | "text",
  step?: number,
  layout?: "wide" | "half",
) {
  const inputValue = value === null ? "" : String(value);
  return `
    <label class="${layout ?? ""}">
      <span>${escapeHtml(label)}</span>
      <input data-weighted-target-setting="${escapeHtml(name)}" type="${type}" ${step ? `step="${step}"` : ""} value="${escapeHtml(inputValue)}" />
    </label>
  `;
}

function renderWeightedTargetSettingSelect(
  name: keyof TargetOrderSettings,
  label: string,
  value: string,
  options: string[],
  layout?: "wide" | "half",
) {
  return `
    <label class="${layout ?? ""}">
      <span>${escapeHtml(label)}</span>
      <select data-weighted-target-setting="${escapeHtml(name)}">
        ${options.map((option) => `<option value="${escapeHtml(option)}" ${option === value ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
      </select>
    </label>
  `;
}

function renderWeightedDefaultSizingSection(settings: TradingSettings) {
  const expanded = state.weightedDefaultSizingExpanded;
  return `
    <div class="trading-default-section weighted-default-section" data-expanded="${String(expanded)}">
      <div class="trading-default-head">
        <button id="weightedDefaultSizingToggle" class="trading-default-expand" type="button" aria-expanded="${String(expanded)}" aria-controls="weightedDefaultSizingBody">
          <b>${expanded ? "-" : "+"}</b>
          <strong>Default Settings</strong>
        </button>
        ${renderWeightedTradingSettingToggle("useDefaultSizingSettings", "On / Off", settings.useDefaultSizingSettings)}
      </div>
      <div id="weightedDefaultSizingBody" class="trading-default-body" ${expanded ? "" : "hidden"}>
        <div class="trading-settings-grid trading-default-grid">
          ${renderWeightedTradingSettingInput("minimumBuyScore", "Minimum buy score", settings.minimumBuyScore, 0, 1, 0.01)}
          ${renderWeightedTradingSettingInput("minimumSignalEdge", "Minimum signal edge", settings.minimumSignalEdge, 0, 1, 0.01)}
          ${renderWeightedTradingSettingInput("baseRiskPercent", "Base risk %", settings.baseRiskPercent, 0.01, 10, 0.01)}
          ${renderWeightedTradingSettingInput("maxPositionPercent", "Max position %", settings.maxPositionPercent, 0.1, 100, 0.1)}
          ${renderWeightedTradingSettingInput("fixedStopDistanceDollars", "Stop $/share", settings.fixedStopDistanceDollars, 0, 100, 0.01)}
          ${renderWeightedTradingSettingInput("atrStopMultiplier", "ATR stop multiplier", settings.atrStopMultiplier, 0.1, 10, 0.1)}
          ${renderWeightedTradingSettingInput("minimumStopDistancePercent", "Min stop distance %", settings.minimumStopDistancePercent, 0.001, 5, 0.001)}
          ${renderWeightedTradingSettingInput("maxParticipationPercent", "Max participation %", settings.maxParticipationPercent, 0.001, 10, 0.001)}
          ${renderWeightedTradingSettingInput("minimumOneMinuteVolume", "Min 1m volume", settings.minimumOneMinuteVolume, 0, 10000000, 1)}
          ${renderWeightedTradingSettingInput("maxAllowedShares", "Max shares (0 auto)", settings.maxAllowedShares, 0, 1000000, 1)}
          ${renderWeightedTradingSettingInput("maxDailyLossPercent", "Max daily loss %", settings.maxDailyLossPercent, 0.1, 10, 0.1)}
          ${renderWeightedTradingSettingToggle("pyramidingEnabled", "Pyramiding", settings.pyramidingEnabled)}
        </div>
      </div>
    </div>
  `;
}

function renderTradingSettingInput(
  name: keyof TradingSettings,
  label: string,
  value: number,
  min: number,
  max: number,
  step: number,
) {
  return `
    <label>
      <span>${escapeHtml(label)}</span>
      <input data-trading-setting="${escapeHtml(name)}" type="number" min="${min}" max="${max}" step="${step}" value="${value}" />
    </label>
  `;
}

function renderWeightedTradingSettingToggle(
  name: keyof TradingSettings,
  label: string,
  checked: boolean,
) {
  return `
    <label class="trading-default-toggle">
      <span>${escapeHtml(label)}</span>
      <input data-weighted-trading-setting="${escapeHtml(name)}" type="checkbox" ${checked ? "checked" : ""} />
    </label>
  `;
}

function renderTradingSettingToggle(
  name: keyof TradingSettings,
  label: string,
  checked: boolean,
) {
  return `
    <label class="trading-default-toggle">
      <span>${escapeHtml(label)}</span>
      <input data-trading-setting="${escapeHtml(name)}" type="checkbox" ${checked ? "checked" : ""} />
    </label>
  `;
}

function renderTradingDefaultSizingSection(settings: TradingSettings) {
  const expanded = state.votingDefaultSizingExpanded;
  return `
    <div class="trading-default-section" data-expanded="${String(expanded)}">
      <div class="trading-default-head">
        <button id="tradingDefaultSizingToggle" class="trading-default-expand" type="button" aria-expanded="${String(expanded)}" aria-controls="tradingDefaultSizingBody">
          <b>${expanded ? "-" : "+"}</b>
          <strong>Default Settings</strong>
        </button>
        ${renderTradingSettingToggle("useDefaultSizingSettings", "On / Off", settings.useDefaultSizingSettings)}
      </div>
      <div id="tradingDefaultSizingBody" class="trading-default-body" ${expanded ? "" : "hidden"}>
        <div class="trading-settings-grid trading-default-grid">
          ${renderTradingSettingInput("minimumBuyScore", "Minimum buy score", settings.minimumBuyScore, 0, 1, 0.01)}
          ${renderTradingSettingInput("minimumSignalEdge", "Minimum signal edge", settings.minimumSignalEdge, 0, 1, 0.01)}
          ${renderTradingSettingInput("baseRiskPercent", "Base risk %", settings.baseRiskPercent, 0.01, 10, 0.01)}
          ${renderTradingSettingInput("maxPositionPercent", "Max position %", settings.maxPositionPercent, 0.1, 100, 0.1)}
          ${renderTradingSettingInput("fixedStopDistanceDollars", "Stop $/share", settings.fixedStopDistanceDollars, 0, 100, 0.01)}
          ${renderTradingSettingInput("atrStopMultiplier", "ATR stop multiplier", settings.atrStopMultiplier, 0.1, 10, 0.1)}
          ${renderTradingSettingInput("minimumStopDistancePercent", "Min stop distance %", settings.minimumStopDistancePercent, 0.001, 5, 0.001)}
          ${renderTradingSettingInput("maxParticipationPercent", "Max participation %", settings.maxParticipationPercent, 0.001, 10, 0.001)}
          ${renderTradingSettingInput("maxAllowedShares", "Max shares (0 auto)", settings.maxAllowedShares, 0, 1000000, 1)}
          ${renderTradingSettingInput("maxDailyLossPercent", "Max daily loss %", settings.maxDailyLossPercent, 0.1, 10, 0.1)}
          ${renderTradingSettingToggle("pyramidingEnabled", "Pyramiding", settings.pyramidingEnabled)}
        </div>
      </div>
    </div>
  `;
}

function renderWeightedTradingSettingInput(
  name: keyof TradingSettings,
  label: string,
  value: number,
  min: number,
  max: number,
  step: number,
) {
  return `
    <label>
      <span>${escapeHtml(label)}</span>
      <input data-weighted-trading-setting="${escapeHtml(name)}" type="number" min="${min}" max="${max}" step="${step}" value="${value}" />
    </label>
  `;
}

function dynamicArtifactSummaryText(matches: boolean, best?: MlComparisonResult["bestByTimeframe"][number]) {
  if (state.dynamicArtifactStatus === "loading") {
    return "Loading latest daily backtest and ML artifact.";
  }
  if (state.dynamicArtifactStatus === "error") {
    return "Latest daily artifact is unavailable. Check the warning below.";
  }
  if (!matches) {
    return "Waiting for the latest daily artifact after backtest dataset refresh.";
  }
  const artifact = state.dynamicArtifact;
  const bestText = best ? ` Best ML: ${best.timeframe} ${best.verdict}, ${signedCurrency(best.bestPnl)}.` : "";
  return `Ready ${artifact?.rangeLabel ?? ""}.${bestText}`;
}

function marketPermissionGate(context: MarketContext | null, intendedSide: AlgoSignal): TradeLayerGate {
  if (!context) {
    return { layer: "Weekly/Daily Permission", status: "fail", signal: "NA", detail: "Market context unavailable" };
  }
  if (!marketContextIsFresh(context)) {
    return { layer: "Weekly/Daily Permission", status: "fail", signal: "Stale", detail: "Market context is stale" };
  }
  const permission = directionalSignal(context.regime.directionBias, "neutral");
  if (context.regime.directionBias === "cash") {
    return { layer: "Weekly/Daily Permission", status: "fail", signal: "Cash", detail: `${context.regime.label}: cash filter active` };
  }
  if (permission === "Hold") {
    return { layer: "Weekly/Daily Permission", status: "fail", signal: "Neutral", detail: `${context.regime.label}: no directional permission` };
  }
  if (intendedSide !== "Hold" && permission !== intendedSide) {
    return { layer: "Weekly/Daily Permission", status: "fail", signal: permission, detail: `${context.regime.label} permits ${permission}, not ${intendedSide}` };
  }
  return { layer: "Weekly/Daily Permission", status: "pass", signal: permission, detail: `${context.regime.label}: ${permission} permission` };
}

function oneHourDirectionGate(intendedSide: AlgoSignal): TradeLayerGate {
  const hourly = oneHourDirectionCandles();
  if (hourly.length < 2) {
    return { layer: "1H Direction", status: "fail", signal: "NA", detail: "Waiting for at least two hourly candles" };
  }
  const latest = hourly[hourly.length - 1];
  const previous = hourly[hourly.length - 2];
  const sessionCandles = latestRegularSessionCandles();
  const vwap = sessionCandles.length ? sessionVwapValue(sessionCandles) : null;
  const signal =
    vwap !== null && latest.close > previous.close && latest.close >= vwap
      ? "Buy"
      : vwap !== null && latest.close < previous.close && latest.close <= vwap
        ? "Sell"
        : "Hold";
  const detail =
    signal === "Hold"
      ? `1H close ${price(latest.close)} is mixed versus prior ${price(previous.close)}${vwap === null ? "" : ` and VWAP ${price(vwap)}`}`
      : `1H close ${price(latest.close)} confirms ${signal} versus prior ${price(previous.close)}${vwap === null ? "" : ` and VWAP ${price(vwap)}`}`;
  if (signal === "Hold") {
    return { layer: "1H Direction", status: "fail", signal, detail };
  }
  if (intendedSide !== "Hold" && signal !== intendedSide) {
    return { layer: "1H Direction", status: "fail", signal, detail: `${detail}; waiting for ${intendedSide} alignment` };
  }
  return { layer: "1H Direction", status: "pass", signal, detail };
}

function shortCycleDirectionGate(intendedSide: AlgoSignal): TradeLayerGate {
  const gate = oneHourDirectionGate(intendedSide);
  if (gate.status !== "fail") {
    return gate;
  }
  return {
    ...gate,
    status: "caution",
    detail: `${gate.detail}; short-cycle context only`,
  };
}

function oneHourDirectionCandles() {
  if (state.timeframe === "1Hour" && state.candles.length >= 2) {
    return state.candles;
  }
  return aggregateCandlesToMinutes(latestRegularSessionCandles(), 60, "1Hour");
}

function eventModeGate(context: MarketContext | null, intendedSide: AlgoSignal): TradeLayerGate & { active: boolean } {
  if (!context) {
    return { layer: "Event Mode", status: "info", signal: "Inactive", detail: "No event context available", active: false };
  }
  const eventTags = new Set(context.event.strategyTags);
  const eventSignal = directionalSignal(context.event.directionBias, "neutral");
  const active = eventTags.has("event-rules") || eventSignal !== "Hold";
  if (!active) {
    return { layer: "Event Mode", status: "info", signal: "Inactive", detail: `${context.event.label}: normal rules`, active: false };
  }
  if (eventSignal === "Hold") {
    return { layer: "Event Mode", status: "caution", signal: "Watch", detail: `${context.event.label}: catalyst present without directional bias`, active: true };
  }
  if (intendedSide !== "Hold" && eventSignal !== intendedSide) {
    return { layer: "Event Mode", status: "fail", signal: eventSignal, detail: `${context.event.label} points ${eventSignal}, not ${intendedSide}`, active: true };
  }
  return { layer: "Event Mode", status: "pass", signal: eventSignal, detail: `${context.event.label}: catalyst mode agrees`, active: true };
}

function executionGate(side: AlgoSignal): TradeLayerGate & { chart: ReturnType<typeof liveChartConfirmation> } {
  const chart = liveChartConfirmation(side);
  if (side === "Hold") {
    return { layer: "1M/5M Execution", status: "fail", signal: "Hold", detail: "No directional side passed into execution", chart };
  }
  const fiveMinute = aggregateCandlesToFiveMinute(latestRegularSessionCandles());
  const latest5 = fiveMinute.at(-1);
  const prior5 = fiveMinute.at(-2);
  const momentumTolerance = prior5 ? Math.max(0.05, prior5.close * 0.0002) : 0;
  const fiveMinuteConfirms =
    !latest5 || !prior5
      ? true
      : side === "Buy"
        ? latest5.close >= prior5.close - momentumTolerance
        : latest5.close <= prior5.close + momentumTolerance;
  const detail =
    chart.confirmed && fiveMinuteConfirms
      ? `${chart.reason}; short-cycle 5m momentum confirms`
      : chart.confirmed
        ? `waiting for short-cycle 5m momentum confirmation; 1m/VWAP condition passed`
        : chart.reason;
  return {
    layer: "1M/5M Execution",
    status: chart.confirmed && fiveMinuteConfirms ? "pass" : "fail",
    signal: side,
    detail,
    chart,
  };
}

function mlQualityGate(side: AlgoSignal, eventActive: boolean): TradeLayerGate {
  const settingsKey = tradingSettingsKey(state.tradingSettings);
  const artifactMatches = state.dynamicArtifactStatus === "ready" && state.dynamicArtifactSettingsKey === settingsKey;
  if (state.dynamicArtifactStatus === "loading") {
    return { layer: "ML Quality", status: "caution", signal: "Running", detail: "Current settings are still running through ML replay; using 1m/5m live gates meanwhile" };
  }
  if (!artifactMatches || !state.dynamicArtifact) {
    return { layer: "ML Quality", status: "caution", signal: "Not ready", detail: "Matching backtest and ML artifact is not ready; using 1m/5m live gates meanwhile" };
  }
  const relevantTimeframes = eventActive ? ["Event"] : ["1Min", "5Min"];
  const rows = (state.dynamicArtifact.mlComparison?.bestByTimeframe ?? []).filter((row) => relevantTimeframes.includes(row.timeframe));
  const best =
    rows.find((row) => row.verdict === "Improved") ??
    rows.find((row) => row.verdict === "Mixed") ??
    rows[0];
  if (!best) {
    return { layer: "ML Quality", status: "caution", signal: "No row", detail: "No relevant ML quality row for this setup; using 1m/5m live gates meanwhile" };
  }
  const detail = `${best.timeframe} ${best.bestVariant}: ${best.verdict}, ${signedCurrency(best.bestPnl)}, PF ${best.bestProfitFactor ?? "NA"}; side remains ${side}`;
  const profitable = best.bestPnl > 0 && (best.bestProfitFactor ?? 0) >= 1;
  if (best.verdict === "Improved") {
    return { layer: "ML Quality", status: "pass", signal: best.verdict, detail };
  }
  if (best.verdict === "Mixed" || profitable) {
    return { layer: "ML Quality", status: "caution", signal: best.verdict, detail };
  }
  return { layer: "ML Quality", status: "fail", signal: best.verdict, detail };
}

function applyTargetOrderOverrides(order: ManualOrderRecommendation): ManualOrderRecommendation {
  const overrides = state.targetOrderOverrides;
  const defaultsOn = state.tradingSettings.useDefaultSizingSettings;
  const quantity = order.quantity;
  const triggerPrice = order.triggerPrice;
  const stopPrice = order.stopPrice;
  const orderNotional =
    triggerPrice !== null
      ? roundNumber(quantity * triggerPrice, 2)
      : order.orderNotional;
  const plannedStopRiskDollars =
    triggerPrice !== null && stopPrice !== null
      ? roundNumber(quantity * Math.abs(triggerPrice - stopPrice), 2)
      : order.plannedStopRiskDollars;
  const estimatedSlippage = roundNumber(quantity * state.tradingSettings.slippagePerShare * 2, 2);
  const nextOrder: ManualOrderRecommendation = {
    ...order,
    symbol: String(overrides.symbol ?? order.symbol).toUpperCase(),
    side: order.side,
    orderType: defaultsOn ? order.orderType : String(overrides.orderType ?? order.orderType),
    quantity,
    triggerPrice,
    limitPrice: order.limitPrice,
    stopPrice,
    targetPrice: order.targetPrice,
    accountBalance: roundNumber(defaultsOn ? order.accountBalance : Number(overrides.accountBalance ?? order.accountBalance), 2),
    orderLimitDollars: roundNumber(defaultsOn ? order.orderLimitDollars : Number(overrides.orderLimitDollars ?? order.orderLimitDollars), 2),
    dailyLimitDollars: roundNumberUp(defaultsOn ? order.dailyLimitDollars : Number(overrides.dailyLimitDollars ?? order.dailyLimitDollars), 2),
    riskDollars: roundNumber(order.riskDollars, 2),
    orderNotional,
    plannedStopRiskDollars,
    estimatedSlippage,
    timeInForce: String(overrides.timeInForce ?? order.timeInForce),
    cutoff: String(overrides.cutoff ?? order.cutoff),
    submitMode: (overrides.submitMode as SubmitOrderMode | undefined) ?? order.submitMode,
  };
  const trigger = nextOrder.triggerPrice === null ? "NA" : price(nextOrder.triggerPrice);
  const limit = nextOrder.limitPrice === null ? "NA" : price(nextOrder.limitPrice);
  const stop = nextOrder.stopPrice === null ? "NA" : price(nextOrder.stopPrice);
  const target = nextOrder.targetPrice === null ? "NA" : price(nextOrder.targetPrice);
  const orderLine =
    nextOrder.side === "Buy"
      ? `Buy stop-limit ${nextOrder.symbol}, ${nextOrder.quantity} shares, stop ${trigger}, limit ${limit}, protective stop ${stop}, target ${target}.`
      : nextOrder.side === "Sell"
        ? `Sell to close stop-limit ${nextOrder.symbol}, ${nextOrder.quantity} shares, stop ${trigger}, limit ${limit}, protective stop ${stop}, target ${target}.`
        : "";
  return {
    ...nextOrder,
    summary: nextOrder.eligible && orderLine
      ? `Order template: ${orderLine} 1M/5M execution, intraday event context, and ML quality are aligned. Uses ${currency(nextOrder.orderNotional)} of the ${currency(nextOrder.orderLimitDollars)} per-order limit.`
      : nextOrder.summary,
  };
}

function nullableNumber(value: unknown) {
  if (value === null || value === "") {
    return null;
  }
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function manualOrderRecommendation(
  bestMatch: TradingRagResponse["retrieved"][number] | undefined,
  answer: TradingRagResponse["answer"],
): ManualOrderRecommendation {
  const symbol = state.symbol;
  const settings = state.tradingSettings;
  const voteWinner = votingEnsembleScoreSummary().winner;
  const eventProbe = eventModeGate(state.marketContext, "Hold");
  const structuralSide = eventProbe.active && eventProbe.signal !== "Watch" && eventProbe.signal !== "Inactive"
    ? (eventProbe.signal as AlgoSignal)
    : voteWinner;
  const event = eventModeGate(state.marketContext, structuralSide);
  const execution = executionGate(structuralSide);
  const mlQuality = mlQualityGate(structuralSide, event.active);
  const historicalBias = bestMatch ? historicalBiasFromRagSource(bestMatch, answer.bias) : "Mixed";
  const historicalGate: TradeLayerGate = {
    layer: "Historical Context",
    status: "info",
    signal: historicalBias,
    detail: bestMatch ? `${bestMatch.timeframe || "NA"} match is ${historicalBias}; context only` : "No historical match; context only",
  };
  const gates: TradeLayerGate[] = [execution, event, mlQuality, historicalGate];
  const failedGates = gates
    .filter((gate) => gate.status === "fail")
    .map((gate) => `${gate.layer}: ${gate.detail}`);
  const chart = execution.chart;
  const latestForPosition = chart.latest?.close ?? latestExecutionCandleForMode("ensemble")?.close ?? 0;
  const position = summarizePositionFromTradeHistory(latestForPosition, latestForPosition, "ensemble");
  const heldShares = Math.max(0, position.shares);
  if (structuralSide === "Buy" && chart.latest) {
    const safetyGates = [
      lateSessionAboveAverageBuyGate("ensemble", chart.latest.close, chart.latest.timestamp),
      ...forecastBuySafetyGates("ensemble", chart.latest.close, chart.latest.timestamp),
    ].filter((gate): gate is TradeLayerGate => Boolean(gate));
    safetyGates.forEach((gate) => {
      gates.push(gate);
      failedGates.push(`${gate.layer}: ${gate.detail}`);
    });
  }
  const accountBalance = settings.startingCapital;
  const orderLimitDollars = accountBalance * (settings.orderAllocationPercent / 100);
  const dailyLimitDollars = accountBalance * (settings.dailyAllocationPercent / 100);
  const availableOrderDollars = Math.min(orderLimitDollars, dailyLimitDollars);
  const baseRiskDollars = settings.useDefaultSizingSettings
    ? accountBalance * (settings.baseRiskPercent / 100)
    : orderLimitDollars * (settings.riskBudgetPercentOfOrder / 100);
  const slippagePerSharePerSide = settings.slippagePerShare;

  if (structuralSide === "Sell" && heldShares <= 0) {
    failedGates.push("Sell recommendation blocked because there are no open shares to close");
  }

  if (structuralSide === "Hold" || !chart.latest || (structuralSide === "Sell" && heldShares <= 0)) {
    const recommendedSide = structuralSide === "Sell" && heldShares <= 0 ? "Hold" : structuralSide;
    return applyTargetOrderOverrides({
      eligible: false,
      side: recommendedSide,
      orderType: "No order",
      symbol,
      quantity: 0,
      triggerPrice: null,
      limitPrice: null,
      stopPrice: null,
      targetPrice: null,
      accountBalance,
      orderLimitDollars,
      dailyLimitDollars,
      riskDollars: baseRiskDollars,
      orderNotional: 0,
      plannedStopRiskDollars: 0,
      estimatedSlippage: 0,
      timeInForce: "Day",
      cutoff: "No new trades after 15:30 ET",
      submitMode: DEFAULT_SUBMIT_MODE,
      failedGates: uniqueStrings(failedGates),
      gates,
      levels: chart.levels,
      summary: `No order: Wait. ${failedGates.length ? `Failed gates: ${uniqueStrings(failedGates).join(", ")}.` : "No eligible directional setup."}`,
    });
  }

  const rawTrigger =
    structuralSide === "Buy"
      ? Math.max(chart.latest.close, chart.latest.high) + slippagePerSharePerSide
      : Math.min(chart.latest.close, chart.latest.low) - slippagePerSharePerSide;
  const triggerPrice = roundNumber(rawTrigger, 2);
  const buySizing = structuralSide === "Buy"
    ? votingEnsembleBuyQuantitySizing(settings, triggerPrice, position)
    : null;
  const orderAtr = averageTrueRange(latestRegularSessionCandles(), 14) ?? 0;
  const riskPerShare = structuralSide === "Buy" && buySizing ? buySizing.stopDistance : tradingSettingsStopDistance(settings, triggerPrice, orderAtr);
  const riskDollars = structuralSide === "Buy" && buySizing ? buySizing.riskDollars : baseRiskDollars;
  const allocationQuantity = Math.floor(availableOrderDollars / triggerPrice);
  const quantity = structuralSide === "Sell"
    ? Math.min(allocationQuantity, heldShares)
    : buySizing?.finalBuyShares ?? allocationQuantity;
  const limitPrice = roundNumber(
    structuralSide === "Buy" ? triggerPrice + 0.03 : triggerPrice - 0.03,
    2,
  );
  const stopPrice = roundNumber(
    structuralSide === "Buy" ? triggerPrice - riskPerShare : triggerPrice + riskPerShare,
    2,
  );
  const targetDistance = targetProfitDistancePerShare(quantity, riskPerShare, settings.takeProfitR);
  const targetPrice = roundNumber(
    structuralSide === "Buy" ? triggerPrice + targetDistance : triggerPrice - targetDistance,
    2,
  );
  const orderNotional = quantity * triggerPrice;
  const plannedStopRiskDollars = quantity * riskPerShare;
  const estimatedSlippage = quantity * slippagePerSharePerSide * 2;
  if (buySizing?.blockedReason) {
    failedGates.push(`Vote sizing: ${buySizing.blockedReason}`);
  }
  if (quantity < 1) {
    failedGates.push(structuralSide === "Buy" ? "vote-size quantity below 1 share" : "order allocation below 1 share");
  }
  if (plannedStopRiskDollars > riskDollars) {
    failedGates.push(
      settings.useDefaultSizingSettings
        ? `planned stop risk exceeds ${settings.baseRiskPercent}% account risk budget`
        : `planned stop risk exceeds ${settings.riskBudgetPercentOfOrder}% order risk budget`,
    );
  }

  const eligible = failedGates.length === 0;
  const orderType = eligible ? `${structuralSide} stop-limit` : "No order";
  const orderLine =
    structuralSide === "Buy"
      ? `Buy stop-limit ${symbol}, ${quantity} shares, stop ${price(triggerPrice)}, limit ${price(limitPrice)}, protective stop ${price(stopPrice)}, target ${price(targetPrice)}.`
      : `Sell to close stop-limit ${symbol}, ${quantity} shares, stop ${price(triggerPrice)}, limit ${price(limitPrice)}, protective stop ${price(stopPrice)}, target ${price(targetPrice)}.`;

  return applyTargetOrderOverrides({
    eligible,
    side: structuralSide,
    orderType,
    symbol,
    quantity,
    triggerPrice,
    limitPrice,
    stopPrice,
    targetPrice,
    accountBalance,
    orderLimitDollars,
    dailyLimitDollars,
    riskDollars,
    orderNotional,
    plannedStopRiskDollars,
    estimatedSlippage,
    timeInForce: "Day",
    cutoff: "No new trades after 15:30 ET",
    submitMode: DEFAULT_SUBMIT_MODE,
    failedGates: uniqueStrings(failedGates),
    gates,
    levels: chart.levels,
    summary: eligible
      ? `Order template: ${orderLine} Weekly/Daily permission, 1H direction, 1M/5M execution, and ML quality are aligned. Uses ${currency(orderNotional)} of the ${currency(orderLimitDollars)} per-order limit.`
      : `No order: Wait. Failed gates: ${uniqueStrings(failedGates).join(", ")}.`,
  });
}

function liveChartConfirmation(side: AlgoSignal) {
  const sessionCandles = latestRegularSessionCandles();
  const latest = sessionCandles.at(-1) ?? null;
  const openingRange = sessionCandles.length >= 15 ? openingRangeValues(sessionCandles, 15) : null;
  const vwap = sessionCandles.length ? sessionVwapValue(sessionCandles) : null;
  const levels = {
    last: latest?.close ?? null,
    vwap,
    openingHigh: openingRange?.high ?? null,
    openingLow: openingRange?.low ?? null,
    lastTime: latest?.timestamp ?? null,
  };
  if (!latest || !openingRange || vwap === null) {
    return { confirmed: false, reason: "live chart levels unavailable", latest, levels };
  }
  if (side === "Buy") {
    const aboveVwap = latest.close > vwap;
    const aboveOpeningHigh = latest.close > openingRange.high;
    const confirmed = aboveVwap;
    return {
      confirmed,
      reason: confirmed
        ? aboveOpeningHigh
          ? "short-cycle Buy confirms above VWAP and opening range high"
          : "short-cycle Buy confirms above VWAP; opening range high not required"
        : "short-cycle Buy blocked because price is below VWAP",
      latest,
      levels,
    };
  }
  if (side === "Sell") {
    const belowVwap = latest.close < vwap;
    const belowOpeningLow = latest.close < openingRange.low;
    const confirmed = belowVwap;
    return {
      confirmed,
      reason: confirmed
        ? belowOpeningLow
          ? "short-cycle Sell confirms below VWAP and opening range low"
          : "short-cycle Sell confirms below VWAP; opening range low not required"
        : "short-cycle Sell blocked because price is above VWAP",
      latest,
      levels,
    };
  }
  return { confirmed: false, reason: "vote winner is Hold", latest, levels };
}

function latestRegularSessionCandles() {
  const regular = state.candles.filter((candle) => isRegularSession(candle.timestamp));
  const latest = regular.at(-1);
  if (!latest) {
    return [];
  }
  const latestDay = easternDateString(latest.timestamp);
  return regular.filter((candle) => easternDateString(candle.timestamp) === latestDay);
}

function marketContextIsFresh(context: MarketContext) {
  if (!context.updatedAt) {
    return false;
  }
  const updatedAt = new Date(context.updatedAt).getTime();
  if (!Number.isFinite(updatedAt)) {
    return false;
  }
  return Date.now() - updatedAt <= 90 * 60 * 1000;
}

function tradingRagActionPlan(items: string[], order: ManualOrderRecommendation) {
  return [order.summary, ...(items || [])];
}

function renderManualOrderRecommendation(order: ManualOrderRecommendation) {
  const dataState = order.eligible ? "eligible" : "wait";
  const status = order.eligible ? "Eligible order template" : "No order / Wait";
  const artifactStatus = dynamicArtifactStatusLabel();
  return `
    <div class="trading-rag-order" data-state="${dataState}" data-side="${escapeHtml(order.side.toLowerCase())}" data-artifact-status="${escapeHtml(artifactStatus.toLowerCase())}">
      <div class="trading-rag-order-head">
        <strong>
          Order Template
          <span class="artifact-status">Artifact <b>${escapeHtml(artifactStatus)}</b></span>
        </strong>
        <b>${status}</b>
      </div>
      <p>${renderSignalColoredText(order.summary)}</p>
      <div class="trading-rag-order-actions">
        <button id="runDynamicArtifactButton" type="button" class="primary-action" ${state.dynamicArtifactStatus === "loading" ? "disabled" : ""}>
          ${state.dynamicArtifactStatus === "loading" ? "Loading..." : "Refresh Artifact"}
        </button>
        ${state.dynamicArtifactWarning ? `<span class="trading-settings-warning">${escapeHtml(state.dynamicArtifactWarning)}</span>` : ""}
      </div>
      <div class="trading-rag-order-gates">
        ${order.gates.map(renderOrderGate).join("")}
      </div>
      <div class="trading-rag-order-levels">
        <span>Last ${order.levels.last === null ? "NA" : price(order.levels.last)}</span>
        <span>VWAP ${order.levels.vwap === null ? "NA" : price(order.levels.vwap)}</span>
        <span>OR high ${order.levels.openingHigh === null ? "NA" : price(order.levels.openingHigh)}</span>
        <span>OR low ${order.levels.openingLow === null ? "NA" : price(order.levels.openingLow)}</span>
        <span>Last candle ${order.levels.lastTime ? formatTime(order.levels.lastTime) : "NA"}</span>
        <span>Refresh <b id="tradingRefreshCountdown">${escapeHtml(tradingRefreshCountdownText())}</b></span>
      </div>
    </div>
  `;
}

function dynamicArtifactStatusLabel() {
  const settingsKey = tradingSettingsKey(state.tradingSettings);
  const artifactMatches = state.dynamicArtifactStatus === "ready" && state.dynamicArtifactSettingsKey === settingsKey;
  return artifactMatches
    ? "Ready"
    : state.dynamicArtifactStatus === "loading"
      ? "Loading"
      : state.dynamicArtifactStatus === "error"
        ? "Error"
        : "Waiting";
}

function compactMlArtifactLabel(timeframe: BacktestResultTimeframe) {
  const status = dynamicArtifactStatusLabel();
  const settingsKey = tradingSettingsKey(state.tradingSettings);
  const artifactMatches = state.dynamicArtifactStatus === "ready" && state.dynamicArtifactSettingsKey === settingsKey;
  if (!artifactMatches || !state.dynamicArtifact?.mlComparison?.bestByTimeframe?.length) {
    return status;
  }
  const rows = state.dynamicArtifact.mlComparison.bestByTimeframe;
  const row =
    rows.find((item) => item.timeframe === timeframe) ??
    rows.find((item) => item.timeframe === "1Min") ??
    rows.find((item) => item.verdict === "Improved") ??
    rows[0];
  const pf = row.bestProfitFactor === null || row.bestProfitFactor === undefined ? "NA" : row.bestProfitFactor;
  return `${status} - ${row.timeframe} ${row.verdict}, ${row.bestVariant} ${signedCurrency(row.bestPnl)}, PF ${pf}`;
}

function renderOrderGate(gate: TradeLayerGate) {
  return `
    <span data-status="${escapeHtml(gate.status)}">
      <b>${escapeHtml(gate.layer)}</b>
      ${renderSignalBadge(gate.signal)}
      <small>${escapeHtml(gate.detail)}</small>
    </span>
  `;
}

function renderSignalBadge(value: string) {
  const normalized = signalColorKey(value);
  if (!normalized) {
    return escapeHtml(value);
  }
  return `<strong class="signal-text ${normalized}">${escapeHtml(value)}</strong>`;
}

function renderSignalColoredText(value: string) {
  const parts = value.split(/\b(Buy|Sell|Hold)\b/g);
  return parts
    .map((part) => {
      const normalized = signalColorKey(part);
      return normalized ? `<strong class="signal-text ${normalized}">${escapeHtml(part)}</strong>` : escapeHtml(part);
    })
    .join("");
}

function signalColorKey(value: string) {
  const normalized = value.trim().toLowerCase();
  return normalized === "buy" || normalized === "sell" || normalized === "hold" ? normalized : "";
}

function historicalBiasFromRagSource(source: TradingRagResponse["retrieved"][number], answerBias: string): AlgoSignal | "Mixed" {
  const text = `${source.title} ${source.text} ${answerBias}`.toLowerCase();
  if (/\b(short|sell|down|bearish)\b/.test(text) && !/\b(long|buy|up|bullish)\b/.test(text)) {
    return "Sell";
  }
  if (/\b(long|buy|up|bullish)\b/.test(text) && !/\b(short|sell|down|bearish)\b/.test(text)) {
    return "Buy";
  }
  if (answerBias === "Buy" || answerBias === "Sell" || answerBias === "Hold") {
    return answerBias;
  }
  return "Mixed";
}

function tradingRagAlignmentText(alignment: string, currentWinner: AlgoSignal, historicalBias: AlgoSignal | "Mixed") {
  if (alignment === "Aligned") {
    return `Current ${currentWinner} vote agrees with the retrieved historical bias.`;
  }
  if (alignment === "Divergence") {
    return `Current ${currentWinner} vote conflicts with historical ${historicalBias}; reduce confidence or wait for confirmation.`;
  }
  return "Historical match is not strongly directional; use timeframe metrics as context.";
}

function renderTradingRagList(title: string, items: string[]) {
  return `
    <div class="trading-rag-list">
      <strong>${escapeHtml(title)}</strong>
      <ul>
        ${(items || []).map((item) => `<li>${escapeHtml(summaryListItemText(item))}</li>`).join("")}
      </ul>
    </div>
  `;
}

function summaryListItemText(item: unknown) {
  if (typeof item === "string") {
    return item;
  }
  if (item && typeof item === "object") {
    const value = item as Record<string, unknown>;
    const name = String(value.name || value.strategy || value.title || value.label || "").trim();
    const status = String(value.status || "").trim();
    const score = value.score !== undefined ? `${value.score}%` : "";
    const matches = Array.isArray(value.matches) ? `matches ${value.matches.slice(0, 3).join(", ")}` : "";
    const risks = Array.isArray(value.risks) ? `risks ${value.risks.slice(0, 3).join(", ")}` : "";
    return [name, [status, score].filter(Boolean).join(" "), matches, risks].filter(Boolean).join("; ");
  }
  return String(item ?? "");
}

function uniqueStrings(items: string[]) {
  return Array.from(new Set(items.filter(Boolean)));
}

function renderTradingRagSource(item: TradingRagResponse["retrieved"][number]) {
  const metrics = item.metrics || {};
  const metricParts = [
    typeof metrics.trades === "number" ? `${metrics.trades} trades` : "",
    typeof metrics.pnl === "number" ? `${signedCurrency(metrics.pnl)}` : "",
    typeof metrics.profitFactor === "number" ? `PF ${metrics.profitFactor}` : "",
    typeof metrics.maxDrawdown === "number" ? `DD ${currency(metrics.maxDrawdown)}` : "",
  ].filter(Boolean);
  return `
    <article class="trading-rag-source">
      <div>
        <strong>${escapeHtml(item.title)}</strong>
        <span>${escapeHtml([item.timeframe || "", item.kind, `score ${item.score}`].filter(Boolean).join(" · "))}</span>
      </div>
      <p>${escapeHtml(item.text)}</p>
      ${metricParts.length ? `<b>${escapeHtml(metricParts.join(" · "))}</b>` : ""}
    </article>
  `;
}

function directionalSignal(primary: MarketLayer["directionBias"], fallback: MarketLayer["directionBias"]): AlgoSignal {
  const direction = primary === "neutral" || primary === "cash" ? fallback : primary;
  if (direction === "long") {
    return "Buy";
  }
  if (direction === "short") {
    return "Sell";
  }
  return "Hold";
}

function oppositeSignal(direction: MarketLayer["directionBias"]): AlgoSignal {
  if (direction === "long") {
    return "Sell";
  }
  if (direction === "short") {
    return "Buy";
  }
  return "Hold";
}

function winningVoteSignal(buyVotes: number, sellVotes: number, holdVotes: number): AlgoSignal {
  const maxVotes = Math.max(buyVotes, sellVotes, holdVotes);
  const winners = [
    buyVotes === maxVotes ? "Buy" : "",
    sellVotes === maxVotes ? "Sell" : "",
    holdVotes === maxVotes ? "Hold" : "",
  ].filter(Boolean);
  return winners.length === 1 ? (winners[0] as AlgoSignal) : "Hold";
}

function votingEnsembleScoreSummary() {
  const votes = strategyEnsembleSignals(state.marketContext);
  const eligibleVotes = votes.filter(isEligibleStrategyVote);
  const total = Math.max(eligibleVotes.length, 1);
  const buyVotes = eligibleVotes.filter((vote) => vote.signal === "Buy").length;
  const sellVotes = eligibleVotes.filter((vote) => vote.signal === "Sell").length;
  const holdVotes = eligibleVotes.filter((vote) => vote.signal === "Hold").length;
  const scores = {
    Buy: buyVotes / total,
    Sell: sellVotes / total,
    Hold: holdVotes / total,
  };
  return {
    votes,
    eligibleVotes,
    scores,
    winner: winningVoteSignal(buyVotes, sellVotes, holdVotes),
    secondBestScore: Math.max(scores.Sell, scores.Hold),
  };
}

function votingEnsembleSizeMultiplier(signalEdge: number) {
  if (signalEdge > 0.65) {
    return 1;
  }
  if (signalEdge >= 0.5) {
    return 0.75;
  }
  if (signalEdge >= 0.35) {
    return 0.5;
  }
  if (signalEdge >= 0.2) {
    return 0.25;
  }
  return 0;
}

function votingEnsembleBuyQuantitySizing(
  settings: TradingSettings,
  latestPrice: number,
  position: PositionSummary,
) {
  const manualAccountEquity = Number(state.targetOrderOverrides.accountBalance ?? settings.startingCapital);
  const manualOrderLimitDollars = Number(state.targetOrderOverrides.orderLimitDollars ?? manualAccountEquity * (settings.orderAllocationPercent / 100));
  const manualDailyLimitDollars = Number(state.targetOrderOverrides.dailyLimitDollars ?? manualAccountEquity * (settings.dailyAllocationPercent / 100));
  const useDefaults = settings.useDefaultSizingSettings;
  const accountEquity = useDefaults ? settings.startingCapital : finitePositiveOrDefault(manualAccountEquity, settings.startingCapital);
  const minimumBuyScore = useDefaults ? clampNumber(settings.minimumBuyScore, 0, 1) : 0.6;
  const minimumSignalEdge = useDefaults ? clampNumber(settings.minimumSignalEdge, 0, 1) : 0.2;
  const baseRiskPct = useDefaults
    ? Math.max(0, settings.baseRiskPercent / 100)
    : Math.max(0, settings.orderAllocationPercent / 100) * Math.max(0, settings.riskBudgetPercentOfOrder / 100);
  const maxPositionPct = useDefaults
    ? Math.max(0, settings.maxPositionPercent / 100)
    : Math.max(0, finitePositiveOrDefault(manualOrderLimitDollars, accountEquity * (settings.orderAllocationPercent / 100)) / Math.max(accountEquity, 0.01));
  const maxParticipationRate = useDefaults ? Math.max(0, settings.maxParticipationPercent / 100) : 0.01;
  const maxAllowedShares = useDefaults
    ? Math.floor(settings.maxAllowedShares > 0 ? settings.maxAllowedShares : (accountEquity * maxPositionPct) / Math.max(latestPrice, 0.01))
    : Math.floor(finitePositiveOrDefault(manualOrderLimitDollars, accountEquity * maxPositionPct) / Math.max(latestPrice, 0.01));
  const summary = votingEnsembleScoreSummary();
  const buyScore = summary.scores.Buy;
  const secondBestScore = summary.secondBestScore;
  const signalEdge = buyScore - secondBestScore;
  const sessionCandles = latestRegularSessionCandles();
  const atr = averageTrueRange(sessionCandles, Math.min(14, sessionCandles.length - 1)) ?? 0;
  const stopDistance = tradingSettingsStopDistance(settings, latestPrice, atr);
  const sizeMultiplier = votingEnsembleSizeMultiplier(signalEdge);
  const riskDollars = accountEquity * baseRiskPct * (useDefaults ? Math.max(sizeMultiplier, 1) : sizeMultiplier);
  const maxOrderDollars = useDefaults
    ? accountEquity * (settings.orderAllocationPercent / 100)
    : finitePositiveOrDefault(manualOrderLimitDollars, accountEquity * (settings.orderAllocationPercent / 100));
  const maxPositionDollars = accountEquity * maxPositionPct;
  const dailyBuyingPower = useDefaults
    ? accountEquity * (settings.dailyAllocationPercent / 100)
    : finitePositiveOrDefault(manualDailyLimitDollars, accountEquity * (settings.dailyAllocationPercent / 100));
  const availableBuyingPower = Math.max(0, Math.min(maxPositionDollars, dailyBuyingPower) - Math.max(0, position.marketValue));
  const averageOneMinuteVolume =
    simpleMovingAverage(sessionCandles.map((candle) => candle.volume), Math.min(20, sessionCandles.length)) ?? 0;

  const blockedReason =
    summary.winner !== "Buy"
      ? `Buy is not the voting winner (${summary.winner})`
      : buyScore < minimumBuyScore
        ? `Buy score ${(buyScore * 100).toFixed(1)}% is below ${(minimumBuyScore * 100).toFixed(0)}%`
        : signalEdge < minimumSignalEdge
          ? `Buy edge ${(signalEdge * 100).toFixed(1)}% is below ${(minimumSignalEdge * 100).toFixed(0)}%`
          : "";

  const riskBasedShares = stopDistance > 0 ? riskDollars / stopDistance : 0;
  const orderBasedShares = latestPrice > 0 ? maxOrderDollars / latestPrice : 0;
  const capitalBasedShares = latestPrice > 0 ? maxPositionDollars / latestPrice : 0;
  const buyingPowerShares = latestPrice > 0 ? availableBuyingPower / latestPrice : 0;
  const hasVolumeCap = averageOneMinuteVolume > 0 && maxParticipationRate > 0;
  const liquidityBasedShares = hasVolumeCap ? averageOneMinuteVolume * maxParticipationRate : Number.MAX_SAFE_INTEGER;
  const shareCaps = [
    { label: "risk budget", shares: riskBasedShares },
    { label: "per-trade order limit", shares: orderBasedShares },
    { label: "max position", shares: capitalBasedShares },
    { label: "available buying power", shares: buyingPowerShares },
    ...(hasVolumeCap ? [{ label: "volume participation", shares: liquidityBasedShares }] : []),
    { label: "max allowed shares", shares: Math.max(0, maxAllowedShares) },
  ];
  const limitingCap = shareCaps.reduce((min, cap) => (cap.shares < min.shares ? cap : min), shareCaps[0]);
  const sizingBlockedReason =
    !blockedReason && limitingCap.shares < 1
      ? `${limitingCap.label} allows less than 1 share`
      : blockedReason;
  const finalBuyShares = blockedReason
    ? 0
    : Math.floor(
        Math.min(
          riskBasedShares,
          orderBasedShares,
          capitalBasedShares,
          buyingPowerShares,
          liquidityBasedShares,
          Math.max(0, maxAllowedShares),
        ),
      );

  return {
    finalBuyShares,
    riskDollars,
    stopDistance,
    buyScore,
    secondBestScore,
    signalEdge,
    sizeMultiplier,
    riskBasedShares,
    capitalBasedShares,
    buyingPowerShares,
    liquidityBasedShares,
    maxAllowedShares,
    useDefaults,
    blockedReason: sizingBlockedReason,
  };
}

function renderAlgoTradePlan(finalSignal: AlgoSignal, votes: AlgoVote[], backtest: ReturnType<typeof backtestVotingEnsembleLastDay>) {
  const activeVotes = votes.filter((vote) => vote.signal === finalSignal && finalSignal !== "Hold");
  const timeframe = algoBacktestTimeframeLabel(backtest.timeframe);
  const strategyDescription = backtest.strategyDescription ?? `${timeframe} intraday backtest`;
  if (backtest.timeframe === "Event") {
    const config = backtest.riskConfig?.openCloseEvents;
    return `
      <span>Trade bias: Event.</span>
      <span>${escapeHtml(strategyDescription)} uses the approved weekly vote filter.</span>
      <span>Opening: ${escapeHtml(config?.openingWindow ?? "09:45-10:30")} post-opening-range breakout.</span>
      <span>Closing: ${config?.enableClosingEvents ? `${escapeHtml(config?.closingWindow ?? "15:30-15:50")} continuation` : "disabled"}.</span>
    `;
  }
  if (finalSignal === "Buy") {
    return `
      <span>Trade bias: Buy.</span>
      <span>${escapeHtml(strategyDescription)} uses the Voting Ensemble winner.</span>
      <span>Supporting strategies: ${escapeHtml(activeVotes.map((vote) => vote.strategy).join(", ") || "NA")}.</span>
      <span>Invalidate if price loses VWAP or vote winner changes.</span>
    `;
  }
  if (finalSignal === "Sell") {
    return `
      <span>Trade bias: Sell.</span>
      <span>${escapeHtml(strategyDescription)} uses the Voting Ensemble winner.</span>
      <span>Supporting strategies: ${escapeHtml(activeVotes.map((vote) => vote.strategy).join(", ") || "NA")}.</span>
      <span>Invalidate if price reclaims VWAP or vote winner changes.</span>
    `;
  }
  return `
    <span>Trade bias: Hold.</span>
    <span>${escapeHtml(strategyDescription)} uses the Voting Ensemble winner.</span>
    <span>No directional bucket has a clean winner.</span>
    <span>Wait for more strategy alignment before opening a new trade.</span>
  `;
}

function renderAlgoResults(
  finalSignal: AlgoSignal,
  buyVotes: number,
  sellVotes: number,
  holdVotes: number,
  votes: AlgoVote[],
  backtest: BacktestResult,
) {
  const strongest = [...votes].sort((a, b) => (b.score ?? -1) - (a.score ?? -1))[0];
  const strongestLabel =
    strongest && strongest.status && typeof strongest.score === "number"
      ? `${strongest.strategy} ${strongest.status} ${strongest.score}%`
      : "NA";
  const totalTrades = backtest.totalTrades ?? backtest.trades.length;
  const winRate = totalTrades ? `${Math.round((backtest.winners / totalTrades) * 100)}%` : "NA";
  const rangeLabel = backtest.rangeLabel ?? backtest.dateLabel;
  return `
    <span>Winner: ${finalSignal}</span>
    <span>Buy ${buyVotes} / Sell ${sellVotes} / Hold ${holdVotes}</span>
    <span>Highest-ranked strategy: ${escapeHtml(strongestLabel)}</span>
    <span>Backtest status: <strong class="algo-backtest-status" data-status="${algoBacktestStatusKind()}">${escapeHtml(algoBacktestStatusLabel())}</strong></span>
    <span>ML artifact: ${escapeHtml(compactMlArtifactLabel(backtest.timeframe))}</span>
    <span>Backtest timeframe: ${algoBacktestTimeframeLabel(backtest.timeframe)}</span>
    <span>Backtest range: ${escapeHtml(rangeLabel)}</span>
    <span>Backtest trades: ${totalTrades}</span>
    <span>Net backtest P/L: ${signedCurrency(backtest.totalPnl)} (${signed(backtest.totalReturnPercent)}%)</span>
    <span>Max drawdown: ${currency(backtest.maxDrawdown ?? 0)} (${signed(-(backtest.maxDrawdownPercent ?? 0))}%)</span>
    <span>Profit factor: ${backtest.profitFactor ?? "NA"}</span>
    <span>Win rate: ${winRate}</span>
  `;
}

function renderOpenCloseTradePlan(backtest: BacktestResult) {
  const config = backtest.riskConfig?.openCloseEvents;
  const closingText = config?.enableClosingEvents
    ? `${escapeHtml(config?.closingWindow ?? "15:30-15:50")} continuation, requiring ${config?.minClosingWeeklyDirectionalVotes ?? 4}+ weekly directional votes`
    : "disabled until closing-event edge improves";
  return `
    <span>Focus: opening and closing events only.</span>
    <span>Opening: ${escapeHtml(config?.openingWindow ?? "09:45-10:30")} post-opening-range breakout with weekly vote confirmation.</span>
    <span>Closing: ${closingText}.</span>
    <span>Blocked regimes: ${escapeHtml(config?.blockedRegimes?.join(", ") || "None")}.</span>
    <span>Risk: ${backtest.riskConfig ? `${backtest.riskConfig.riskPerTradePercent}% risk/trade, ${config?.stopLossPercent ?? backtest.riskConfig.stopLossPercent}% stop, ${config?.takeProfitR ?? backtest.riskConfig.takeProfitR}R target` : "NA"}.</span>
    <span>Expenses: net results deduct slippage, liquidity reserve, and sell-side fee estimates.</span>
  `;
}

function renderOpenCloseResults(backtest: BacktestResult) {
  const totalTrades = backtest.totalTrades ?? backtest.trades.length;
  const winRate = totalTrades ? `${Math.round((backtest.winners / totalTrades) * 100)}%` : "NA";
  const rangeLabel = backtest.rangeLabel ?? backtest.dateLabel;
  const config = backtest.riskConfig?.openCloseEvents;
  return `
    <span>Strategy mode: ${escapeHtml(backtest.strategyDescription ?? "Opening/Closing Event Ensemble")}</span>
    <span>Weekly filter: ${escapeHtml(config?.weeklyFilter ?? "approved weekly vote")}</span>
    <span>Opening gate: ${config?.minOpeningWeeklyDirectionalVotes ?? 3}+ weekly directional votes</span>
    <span>Closing gate: ${config?.enableClosingEvents ? `${config?.minClosingWeeklyDirectionalVotes ?? 4}+ weekly directional votes` : "disabled"}</span>
    <span>Blocked regimes: ${escapeHtml(config?.blockedRegimes?.join(", ") || "None")}</span>
    ${renderMlComparison(backtest.timeframe)}
    ${renderCandidateDataset(backtest.timeframe)}
    ${renderMlDiagnostics(backtest.timeframe)}
    ${renderDailyRefinement(backtest.timeframe)}
    ${renderEventRefinement(backtest.timeframe)}
    ${renderWeeklyRiskTuning(backtest.timeframe)}
    <span>Backtest range: ${escapeHtml(rangeLabel)}</span>
    <span>Backtest bars: ${backtest.bars ?? "NA"} across ${backtest.sessions ?? "NA"} sessions</span>
    <span>Backtest trades: ${totalTrades}</span>
    <span>Displayed trades: ${backtest.displayedTrades ?? backtest.trades.length}</span>
    <span>Final equity: ${currency(backtest.finalEquity ?? 0)}</span>
    <span>Estimated expenses: ${currency(backtest.totalExpenses ?? 0)} deducted from P/L</span>
    <span>Net backtest P/L: ${signedCurrency(backtest.totalPnl)} (${signed(backtest.totalReturnPercent)}%)</span>
    <span>Max drawdown: ${currency(backtest.maxDrawdown ?? 0)} (${signed(-(backtest.maxDrawdownPercent ?? 0))}%)</span>
    <span>Profit factor: ${backtest.profitFactor ?? "NA"}</span>
    <span>Average win / loss: ${currency(backtest.averageWin ?? 0)} / ${currency(backtest.averageLoss ?? 0)}</span>
    <span>Expectancy: ${currency(backtest.expectancy ?? 0)} per trade</span>
    <span>Win rate: ${winRate}</span>
    ${renderBacktestDiagnostics(backtest)}
  `;
}

function renderWeeklyRiskTuning(timeframe: BacktestResultTimeframe) {
  if (timeframe !== "1Week") {
    return "";
  }
  if (state.weeklyRiskTuningStatus === "loading") {
    return `
      <div class="weekly-risk-tuning">
        <strong>Weekly Risk Tuning</strong>
        <span>Testing weekly risk settings...</span>
      </div>
    `;
  }
  if (state.weeklyRiskTuningStatus === "error") {
    return `
      <div class="weekly-risk-tuning">
        <strong>Weekly Risk Tuning</strong>
        <span>${escapeHtml(state.weeklyRiskTuningWarning || "Weekly risk tuning unavailable")}</span>
      </div>
    `;
  }
  const tuning = state.weeklyRiskTuning;
  if (!tuning) {
    return "";
  }
  const cards = [
    ["Risk-adjusted", tuning.bestRiskAdjusted],
    ["Profit max", tuning.bestProfit],
    ["Low drawdown", tuning.bestLowDrawdown],
  ] as Array<[string, WeeklyRiskVariant | null | undefined]>;
  return `
    <div class="weekly-risk-tuning">
      <strong>Weekly Risk Tuning</strong>
      <span>${escapeHtml(tuning.recommendation)}</span>
      <span>Tested ${tuning.testedVariants} variants. Baseline: ${signedCurrency(tuning.base.pnl)}, PF ${tuning.base.profitFactor ?? "NA"}, DD ${currency(tuning.base.maxDrawdown)}.</span>
      <div class="weekly-risk-grid">
        ${cards
          .filter(([, row]) => row)
          .map(
            ([label, row]) => `
              <span>
                <b>${escapeHtml(label)}</b>
                ${escapeHtml(row!.variant)} · ${row!.trades} trades · ${signedCurrency(row!.pnl)} · PF ${row!.profitFactor ?? "NA"} · DD ${currency(row!.maxDrawdown)} (${row!.maxDrawdownPercent.toFixed(2)}%) · Eff ${row!.capitalEfficiency?.toFixed(2) ?? "NA"}
              </span>
            `,
          )
          .join("")}
      </div>
    </div>
  `;
}

function renderEventRefinement(timeframe: BacktestResultTimeframe) {
  if (timeframe !== "Event") {
    return "";
  }
  if (state.eventRefinementStatus === "loading") {
    return `
      <div class="event-refinement">
        <strong>Event Refinement</strong>
        <span>Testing Event-only ML model...</span>
      </div>
    `;
  }
  if (state.eventRefinementStatus === "error") {
    return `
      <div class="event-refinement">
        <strong>Event Refinement</strong>
        <span>${escapeHtml(state.eventRefinementWarning || "Event refinement unavailable")}</span>
      </div>
    `;
  }
  const refinement = state.eventRefinement;
  if (!refinement) {
    return "";
  }
  const profitBest = refinement.profitPreservingBest;
  const qualityBest = refinement.qualityBest;
  const rows = refinement.variants
    .slice()
    .sort((left, right) => right.pnl - left.pnl || right.expectancy - left.expectancy)
    .slice(0, 5);
  return `
    <div class="event-refinement">
      <strong>Event Refinement</strong>
      <span>${escapeHtml(refinement.recommendation)}</span>
      <span>Event-only model: ${refinement.model.trainingRows.toLocaleString()} trades, ${refinement.model.positiveRows.toLocaleString()} winners, ${refinement.model.featureCount} features.</span>
      ${
        profitBest
          ? `<span>Profit-preserving: ${escapeHtml(profitBest.variant)} · ${profitBest.trades} trades · ${signedCurrency(profitBest.pnl)} · PF ${profitBest.profitFactor ?? "NA"} · DD ${currency(profitBest.maxDrawdown)} (${profitBest.maxDrawdownPercent.toFixed(2)}%).</span>`
          : ""
      }
      ${
        qualityBest
          ? `<span>Quality best: ${escapeHtml(qualityBest.variant)} · ${qualityBest.trades} trades · ${signedCurrency(qualityBest.pnl)} · PF ${qualityBest.profitFactor ?? "NA"} · Exp ${currency(qualityBest.expectancy)}.</span>`
          : ""
      }
      <div class="event-refinement-grid">
        ${rows
          .map(
            (row) => `
              <span data-verdict="${escapeHtml((row.verdict ?? "mixed").toLowerCase())}">
                ${escapeHtml(row.variant)} · ${row.trades} trades · ${signedCurrency(row.pnl)} · PF ${row.profitFactor ?? "NA"} · DD ${currency(row.maxDrawdown)}
              </span>
            `,
          )
          .join("")}
      </div>
    </div>
  `;
}

function renderDailyRefinement(timeframe: BacktestResultTimeframe) {
  if (timeframe !== "1Day") {
    return "";
  }
  if (state.dailyRefinementStatus === "loading") {
    return `
      <div class="daily-refinement">
        <strong>Daily Refinement</strong>
        <span>Testing tighter Daily ML thresholds...</span>
      </div>
    `;
  }
  if (state.dailyRefinementStatus === "error") {
    return `
      <div class="daily-refinement">
        <strong>Daily Refinement</strong>
        <span>${escapeHtml(state.dailyRefinementWarning || "Daily refinement unavailable")}</span>
      </div>
    `;
  }
  const refinement = state.dailyRefinement;
  if (!refinement?.best) {
    return "";
  }
  const best = refinement.best;
  const rows = refinement.variants
    .slice()
    .sort((left, right) => right.expectancy - left.expectancy || right.pnl - left.pnl)
    .slice(0, 5);
  return `
    <div class="daily-refinement">
      <strong>Daily Refinement</strong>
      <span>${escapeHtml(refinement.recommendation)}</span>
      <span>Best: ${escapeHtml(best.variant)} · ${best.trades} trades · ${signedCurrency(best.pnl)} · PF ${best.profitFactor ?? "NA"} · DD ${currency(best.maxDrawdown)} (${best.maxDrawdownPercent.toFixed(2)}%) · ${currency(best.expectancy)} expectancy.</span>
      <div class="daily-refinement-grid">
        ${rows
          .map(
            (row) => `
              <span data-verdict="${escapeHtml((row.verdict ?? "mixed").toLowerCase())}">
                ${escapeHtml(row.variant)} · ${row.trades} trades · ${signedCurrency(row.pnl)} · PF ${row.profitFactor ?? "NA"} · Exp ${currency(row.expectancy)}
              </span>
            `,
          )
          .join("")}
      </div>
    </div>
  `;
}

function renderMlDiagnostics(timeframe: BacktestResultTimeframe) {
  if (state.mlDiagnosticsStatus === "loading") {
    return `
      <div class="ml-diagnostics">
        <strong>ML Diagnostics</strong>
        <span class="ml-comparison-note">Calculating feature weights and timeframe guidance...</span>
      </div>
    `;
  }
  if (state.mlDiagnosticsStatus === "error") {
    return `
      <div class="ml-diagnostics">
        <strong>ML Diagnostics</strong>
        <span class="ml-comparison-note">${escapeHtml(state.mlDiagnosticsWarning || "ML diagnostics unavailable")}</span>
      </div>
    `;
  }
  const diagnostics = state.mlDiagnostics;
  if (!diagnostics) {
    return "";
  }
  const guidance = diagnostics.timeframeGuidance.find((row) => row.timeframe === timeframe);
  const positive = diagnostics.featureWeights.topPositive.slice(0, 4);
  const negative = diagnostics.featureWeights.topNegative.slice(0, 4);
  const bestEdges = diagnostics.featureEdges.bestExpectancy.slice(0, 4);
  return `
    <div class="ml-diagnostics">
      <strong>ML Diagnostics</strong>
      <span>${diagnostics.model.trainingRows.toLocaleString()} training trades, ${diagnostics.model.positiveRows.toLocaleString()} winners, ${diagnostics.model.featureCount} features.</span>
      ${
        guidance
          ? `<span>${algoBacktestTimeframeLabel(guidance.timeframe)}: ${escapeHtml(guidance.verdict)} - ${escapeHtml(guidance.action)}. Best ${escapeHtml(guidance.bestVariant)}: ${signedCurrency(guidance.bestPnl)}, PF ${guidance.bestProfitFactor ?? "NA"}, DD ${currency(guidance.bestMaxDrawdown)}.</span>`
          : ""
      }
      <div class="ml-diagnostic-columns">
        <div>
          <b>Top positive weights</b>
          ${positive.map((row) => `<span>${escapeHtml(cleanMlFeatureName(row.feature))}: ${row.avgWeight.toFixed(3)}</span>`).join("")}
        </div>
        <div>
          <b>Top negative weights</b>
          ${negative.map((row) => `<span>${escapeHtml(cleanMlFeatureName(row.feature))}: ${row.avgWeight.toFixed(3)}</span>`).join("")}
        </div>
      </div>
      <div class="ml-diagnostic-columns">
        <div>
          <b>Best realized feature edges</b>
          ${bestEdges.map((row) => `<span>${escapeHtml(cleanMlFeatureName(row.feature))}: ${signedCurrency(row.expectancy)} exp, ${row.winRate.toFixed(1)}% win, ${row.trades} trades</span>`).join("")}
        </div>
        <div>
          <b>Next actions</b>
          ${diagnostics.recommendations.slice(0, 3).map((item) => `<span>${escapeHtml(item)}</span>`).join("")}
        </div>
      </div>
    </div>
  `;
}

function cleanMlFeatureName(value: string) {
  return value.replaceAll("=", ": ").replaceAll("timeframe", "TF").replaceAll("entryHour", "Hour");
}

function renderCandidateDataset(timeframe: BacktestResultTimeframe) {
  if (state.candidateDatasetStatus === "loading") {
    return `
      <div class="candidate-dataset">
        <strong>Candidate Dataset</strong>
        <span class="ml-comparison-note">Preparing candidate and outcome export...</span>
      </div>
    `;
  }
  if (state.candidateDatasetStatus === "error") {
    return `
      <div class="candidate-dataset">
        <strong>Candidate Dataset</strong>
        <span class="ml-comparison-note">${escapeHtml(state.candidateDatasetWarning || "Candidate dataset unavailable")}</span>
      </div>
    `;
  }
  const dataset = state.candidateDataset;
  if (!dataset) {
    return "";
  }
  const row = dataset.timeframes.find((item) => item.timeframe === timeframe);
  return `
    <div class="candidate-dataset">
      <strong>Candidate Dataset</strong>
      <span>${dataset.rows.toLocaleString()} rows · ${dataset.candidateRows.toLocaleString()} candidates · ${dataset.labeledRows.toLocaleString()} labeled outcomes · ${dataset.skippedRows.toLocaleString()} skipped setups</span>
      ${
        row
          ? `<span>${algoBacktestTimeframeLabel(row.timeframe)}: ${row.rows.toLocaleString()} rows, ${row.candidates.toLocaleString()} candidates, ${row.outcomes.toLocaleString()} outcomes, ${row.skipped.toLocaleString()} skipped, ${signedCurrency(row.pnl)} labeled P/L</span>`
          : ""
      }
      <span>CSV: ${escapeHtml(dataset.files.csv || "NA")}</span>
      <span>JSONL: ${escapeHtml(dataset.files.jsonl || "NA")}</span>
    </div>
  `;
}

function renderMlComparison(timeframe: BacktestResultTimeframe) {
  if (state.mlComparisonStatus === "loading") {
    return `
      <div class="ml-comparison">
        <strong>ML Comparison</strong>
        <span class="ml-comparison-note">Training shared model and comparing Base vs ML filters...</span>
      </div>
    `;
  }
  if (state.mlComparisonStatus === "error") {
    return `
      <div class="ml-comparison">
        <strong>ML Comparison</strong>
        <span class="ml-comparison-note">${escapeHtml(state.mlComparisonWarning || "ML comparison unavailable")}</span>
      </div>
    `;
  }
  const comparison = state.mlComparison;
  if (!comparison) {
    return "";
  }
  const rows = comparison.rows.filter((row) => row.timeframe === timeframe);
  const bestRows = comparison.bestByTimeframe;
  return `
    <div class="ml-comparison">
      <strong>ML Comparison</strong>
      <span class="ml-comparison-note">${escapeHtml(comparison.model.name)} · ${comparison.model.rows} shared trades · ${comparison.model.trainingPolicy}</span>
      <div class="ml-table-wrap">
        <table>
          <thead>
            <tr>
              <th>Variant</th>
              <th>Trades</th>
              <th>P/L</th>
              <th>DD</th>
              <th>PF</th>
              <th>Win</th>
              <th>Exp</th>
              <th>Skipped</th>
              <th>Verdict</th>
            </tr>
          </thead>
          <tbody>
            ${rows
              .map(
                (row) => `
                  <tr data-verdict="${escapeHtml((row.verdict ?? "Base").toLowerCase())}">
                    <td>${escapeHtml(row.variant)}</td>
                    <td>${row.trades}</td>
                    <td class="${row.pnl >= 0 ? "positive" : "negative"}">${signedCurrency(row.pnl)}</td>
                    <td>${currency(row.maxDrawdown)} (${row.maxDrawdownPercent.toFixed(2)}%)</td>
                    <td>${row.profitFactor ?? "NA"}</td>
                    <td>${row.winRate.toFixed(1)}%</td>
                    <td>${currency(row.expectancy)}</td>
                    <td>${row.skippedTrades}${row.skippedTrades ? ` / ${signedCurrency(row.skippedPnl)}` : ""}</td>
                    <td>${escapeHtml(row.verdict ?? "Base")}</td>
                  </tr>
                `,
              )
              .join("")}
          </tbody>
        </table>
      </div>
      <div class="ml-best-grid">
        ${bestRows
          .map(
            (row) => `
              <span data-verdict="${escapeHtml(row.verdict.toLowerCase())}">
                <b>${algoBacktestTimeframeLabel(row.timeframe)}</b>
                ${escapeHtml(row.bestVariant)} · ${signedCurrency(row.bestPnl)} · PF ${row.bestProfitFactor ?? "NA"} · ${escapeHtml(row.verdict)}
              </span>
            `,
          )
          .join("")}
      </div>
      <span class="ml-comparison-note">${escapeHtml(comparison.model.note)}</span>
    </div>
  `;
}

function renderBacktestDiagnostics(backtest: BacktestResult) {
  const diagnostics = backtest.diagnostics;
  if (!diagnostics) {
    return "";
  }
  const sections: Array<[string, BacktestDiagnosticRow[] | undefined]> = [
    ["Opening vs Closing", diagnostics.byEventType],
    ["1m vs 5m", diagnostics.byTimeframe],
    ["Long vs Short", diagnostics.bySide],
    ["Hour of day", diagnostics.byHour],
    ["Exit reason", diagnostics.byExitReason],
    ["Vote count strength", diagnostics.byVoteStrength],
    ["Regime", diagnostics.byRegime],
    ["Year/month", diagnostics.byYearMonth],
    ["R multiple", diagnostics.byRMultiple],
    ["Max drawdown by setting", diagnostics.bySetting],
  ];
  return `
    <div class="algo-diagnostics">
      ${sections
        .filter(([, rows]) => rows?.length)
        .map(
          ([title, rows]) => `
            <div class="algo-diagnostic-section">
              <strong>${escapeHtml(title)}</strong>
              <table>
                <thead>
                  <tr>
                    <th>Bucket</th>
                    <th>Trades</th>
                    <th>P/L</th>
                    <th>Win</th>
                    <th>PF</th>
                    <th>Avg R</th>
                    <th>DD</th>
                  </tr>
                </thead>
                <tbody>
                  ${rows!.slice(0, 8).map(renderDiagnosticRow).join("")}
                </tbody>
              </table>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

function renderDiagnosticRow(row: BacktestDiagnosticRow) {
  return `
    <tr>
      <td>${escapeHtml(row.label)}</td>
      <td>${row.trades}</td>
      <td class="${row.pnl >= 0 ? "positive" : "negative"}">${signedCurrency(row.pnl)}</td>
      <td>${price(row.winRate)}%</td>
      <td>${row.profitFactor ?? "NA"}</td>
      <td>${typeof row.averageR === "number" ? row.averageR.toFixed(2) : "NA"}</td>
      <td>${typeof row.maxDrawdown === "number" ? `${currency(row.maxDrawdown)} (${price(row.maxDrawdownPercent ?? 0)}%)` : "NA"}</td>
    </tr>
  `;
}

function updateAlgoBacktestControls() {
  const intradayActive = state.algoBacktestTimeframe === "1Min" || state.algoBacktestTimeframe === "5Min";
  algoIntradayTradesToggle.classList.toggle("active", intradayActive);
  algoIntradayTradesToggle.setAttribute("aria-expanded", String(state.algoIntradayTradesExpanded));
  algoIntradayTradesToggle.setAttribute("aria-pressed", String(intradayActive));
  algoIntradayTradesToggleIcon.textContent = state.algoIntradayTradesExpanded ? "-" : "+";
  algoIntradayTradesPanel.hidden = !state.algoIntradayTradesExpanded;
  const buttons: Array<[AlgoBacktestTimeframe, HTMLButtonElement]> = [
    ["1Min", algoBacktest1mButton],
    ["5Min", algoBacktest5mButton],
    ["Trading", algoBacktestTradingButton],
  ];
  buttons.forEach(([timeframe, button]) => {
    button.hidden = timeframe === "Trading" || !FAST_INTRADAY_ALGO_TIMEFRAMES.has(timeframe);
    const active = state.algoBacktestTimeframe === timeframe;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function algoBacktestTimeframeLabel(timeframe: AlgoBacktestTimeframe) {
  const labels: Record<AlgoBacktestTimeframe, string> = {
    "1Min": "1m",
    "5Min": "5m",
    "1Hour": "1h",
    "1Day": "Daily",
    "1Week": "Weekly",
    Event: "Event",
    Trading: "Trading",
  };
  return labels[timeframe];
}

function algoBacktestStatusLabel() {
  if (state.algoBacktestStatus === "loading") {
    return `Loading ${algoBacktestTimeframeLabel(state.algoBacktestTimeframe)} full-range backtest`;
  }
  if (state.algoBacktestStatus === "fallback" && state.algoBacktestWarning) {
    return `Fallback - ${state.algoBacktestWarning.slice(0, 90)}`;
  }
  if (state.algoBacktestWarning) {
    return state.algoBacktestWarning.slice(0, 90);
  }
  return "Ready";
}

function algoBacktestStatusKind() {
  if (state.algoBacktestStatus === "loading") {
    return "loading";
  }
  if (state.algoBacktestStatus === "ready" && !state.algoBacktestWarning) {
    return "ready";
  }
  return "warning";
}

function loadStoredConfidenceBacktest(): { key: string; result: BacktestResult; ranAt: string; sessionDate: string } | null {
  try {
    const raw = window.localStorage.getItem(CONFIDENCE_BACKTEST_RESULT_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw) as { key?: string; result?: BacktestResult; ranAt?: string; sessionDate?: string };
    return parsed.key && parsed.result ? { key: parsed.key, result: parsed.result, ranAt: parsed.ranAt ?? "", sessionDate: parsed.sessionDate ?? "" } : null;
  } catch {
    return null;
  }
}

function saveStoredConfidenceBacktest(key: string, result: BacktestResult, sessionDate: string) {
  window.localStorage.setItem(
    CONFIDENCE_BACKTEST_RESULT_STORAGE_KEY,
    JSON.stringify({
      key,
      result,
      sessionDate,
      ranAt: new Date().toISOString(),
    }),
  );
}

async function maybeRunConfidenceDailyBacktest(reason: string) {
  if (confidenceBacktestStatus === "running" || confidenceBacktestDatasetCheckInFlight) {
    return;
  }
  if (state.marketStatus !== "closed" && state.marketStatus !== "holiday") {
    return;
  }
  const latestSessionDate = latestRegularSessionDateForConfidenceBacktest(state.candles);
  if (!latestSessionDate) {
    confidenceBacktestStatus = "waiting";
    confidenceBacktestError = "Waiting for loaded regular-session candles before daily WCA backtest.";
    renderConfidenceBacktestState();
    return;
  }
  const cacheKey = confidenceBacktestCacheKey(state.candles);
  if (confidenceBacktestCache?.key === cacheKey && confidenceBacktestResult) {
    confidenceBacktestStatus = "ready";
    renderConfidenceBacktestState();
    return;
  }
  if (Date.now() < confidenceBacktestNextDatasetCheckAt) {
    return;
  }

  confidenceBacktestDatasetCheckInFlight = true;
  confidenceBacktestNextDatasetCheckAt = Date.now() + 10 * 60 * 1000;
  try {
    const range = await getBacktestRange({ refresh: true });
    if (range.endDate < latestSessionDate) {
      confidenceBacktestStatus = "waiting";
      confidenceBacktestError = `Waiting for backtest dataset through ${latestSessionDate}; latest dataset is ${range.endDate}.`;
      renderConfidenceBacktestState();
      return;
    }
  } catch (error) {
    confidenceBacktestStatus = "waiting";
    confidenceBacktestError = error instanceof Error ? error.message : "Waiting for backtest dataset manifest.";
    renderConfidenceBacktestState();
    return;
  } finally {
    confidenceBacktestDatasetCheckInFlight = false;
  }

  const autoRunKey = `${latestSessionDate}:${cacheKey}`;
  if (confidenceBacktestAutoRunKey === autoRunKey) {
    return;
  }
  confidenceBacktestAutoRunKey = autoRunKey;
  confidenceBacktestStatus = "running";
  confidenceBacktestError = `Daily WCA backtest started after market close (${reason}).`;
  renderConfidenceBacktestState();
  window.setTimeout(() => {
    try {
      const result = backtestConfidenceAggregation(state.candles);
      confidenceBacktestResult = result;
      confidenceBacktestStatus = "ready";
      saveStoredConfidenceBacktest(cacheKey, result, latestSessionDate);
    } catch (error) {
      confidenceBacktestStatus = "error";
      confidenceBacktestError = error instanceof Error ? error.message : "Unable to run WCA backtest";
    }
    renderConfidenceBacktestState();
  }, 0);
}

async function maybeRunDailyAlgorithmBacktests(reason: string) {
  if (!AUTO_DAILY_ALGORITHM_BACKTESTS) {
    return;
  }
  if (dailyAlgorithmBacktestsInFlight || (state.marketStatus !== "closed" && state.marketStatus !== "holiday")) {
    return;
  }
  const latestSessionDate = latestRegularSessionDateForConfidenceBacktest(state.candles);
  if (!latestSessionDate) {
    confidenceBacktestStatus = "waiting";
    confidenceBacktestError = "Waiting for loaded regular-session candles before daily algorithm backtests.";
    renderConfidenceBacktestState();
    return;
  }
  if (Date.now() < dailyAlgorithmBacktestsNextCheckAt) {
    return;
  }

  dailyAlgorithmBacktestsInFlight = true;
  dailyAlgorithmBacktestsNextCheckAt = Date.now() + 10 * 60 * 1000;
  try {
    const dataset = await ensureBacktestDatasetThrough(latestSessionDate);
    if (!dataset.ready) {
      confidenceBacktestStatus = "waiting";
      confidenceBacktestError = `Waiting for backtest dataset through ${latestSessionDate}; latest dataset is ${dataset.range.endDate}.`;
      renderConfidenceBacktestState();
      return;
    }
    const dailyRunKey = `${latestSessionDate}:${dataset.range.startDate}:${dataset.range.endDate}:${state.symbol}`;
    if (dailyAlgorithmBacktestsLastRunKey === dailyRunKey) {
      return;
    }
    dailyAlgorithmBacktestsLastRunKey = dailyRunKey;
    const artifactSummary = await waitForDailyBacktestArtifacts(latestSessionDate, dataset.refreshResult);
    const preparedOneMinuteCandles = normalizeCandles(await fetchPreparedBacktestCandles("1Min"));
    const algorithmResults = await Promise.allSettled([
      runVotingEnsembleDailyBacktestRefresh(),
      runWeightedDailyBacktestRefresh(preparedOneMinuteCandles, latestSessionDate),
      runConfidenceDailyBacktestFromPreparedCandles(preparedOneMinuteCandles, latestSessionDate, reason),
    ]);
    showDailyBacktestCompletionPopup({
      key: dailyRunKey,
      sessionDate: latestSessionDate,
      finishedAt: new Date(),
      datasetRange: dataset.range,
      artifactSummary,
      algorithmResults,
    });
  } catch (error) {
    confidenceBacktestStatus = "waiting";
    confidenceBacktestError = error instanceof Error ? error.message : "Waiting for daily backtest dataset refresh.";
    renderConfidenceBacktestState();
  } finally {
    dailyAlgorithmBacktestsInFlight = false;
  }
}

async function waitForDailyBacktestArtifacts(latestSessionDate: string, initialResult: unknown) {
  let latestStatus = initialResult;
  let dynamicArtifactReadySeen = dailyBacktestArtifactSummary(initialResult).dynamicDone;
  let restartAttempted = false;
  const deadline = Date.now() + 3 * 60 * 60 * 1000;
  while (Date.now() < deadline) {
    const summary = dailyBacktestArtifactSummary(
      dynamicArtifactReadySeen ? mergeDailyArtifactStatus(latestStatus, { dynamicArtifactStatus: "ready" }) : latestStatus,
    );
    if (!restartAttempted && dailyArtifactStatusNeedsRestart(summary.mlStatus, summary.dynamicStatus)) {
      restartAttempted = true;
      latestStatus = await requestDailyBacktestDatasetRefresh(latestSessionDate, { force: true });
      dynamicArtifactReadySeen = dailyBacktestArtifactSummary(latestStatus).dynamicDone;
      continue;
    }
    if (summary.mlDone && summary.dynamicDone) {
      return summary;
    }
    await wait(15000);
    if (!summary.dynamicDone) {
      try {
        await fetchLatestDynamicTradingArtifact();
        dynamicArtifactReadySeen = true;
        latestStatus = mergeDailyArtifactStatus(latestStatus, { dynamicArtifactStatus: "ready" });
      } catch {
        // Dynamic artifact is still queued/running or unavailable; keep polling status below.
      }
    }
    try {
      latestStatus = await fetchDailyBacktestRefreshStatus();
      if (dynamicArtifactReadySeen) {
        latestStatus = mergeDailyArtifactStatus(latestStatus, { dynamicArtifactStatus: "ready" });
      }
    } catch {
      latestStatus = {
        artifactStatus: summary.mlStatus,
        dynamicArtifactStatus: summary.dynamicStatus,
        message: `Waiting for artifact status through ${latestSessionDate}.`,
      };
    }
  }
  return {
    ...dailyBacktestArtifactSummary(latestStatus),
    timedOut: true,
  };
}

async function fetchDailyBacktestRefreshStatus() {
  let lastStatus = 0;
  for (const baseUrl of BACKTEST_API_CANDIDATES) {
    try {
      const response = await fetchWithTimeout(`${baseUrl}/api/backtest-data/daily-refresh/status`, 10000);
      lastStatus = response.status;
      if (response.ok) {
        return await response.json();
      }
      if (response.status !== 404) {
        throw new Error(await response.text());
      }
    } catch (error) {
      if (baseUrl === BACKTEST_API_CANDIDATES[BACKTEST_API_CANDIDATES.length - 1]) {
        throw error;
      }
    }
  }
  throw new Error(`Daily backtest refresh status unavailable (${lastStatus || 503})`);
}

function dailyBacktestArtifactSummary(status: unknown) {
  const root = isRecord(status) ? status : {};
  const result = isRecord(root.result) ? root.result : root;
  const artifactJob = isRecord(root.artifactJob) ? root.artifactJob : isRecord(result.artifactJob) ? result.artifactJob : {};
  const dynamicJob = isRecord(result.dynamicArtifactJob) ? result.dynamicArtifactJob : {};
  const mlStatus = String(
    root.artifactStatus ?? result.artifactStatus ?? artifactJob.status ?? "not_reported",
  ).toLowerCase();
  const dynamicStatus = String(
    root.dynamicArtifactStatus ?? result.dynamicArtifactStatus ?? dynamicJob.status ?? "not_reported",
  ).toLowerCase();
  return {
    mlStatus,
    dynamicStatus,
    mlDone: dailyArtifactStatusDone(mlStatus),
    dynamicDone: dailyArtifactStatusDone(dynamicStatus),
    message: String(root.message ?? result.message ?? ""),
    timedOut: false,
  };
}

function mergeDailyArtifactStatus(status: unknown, updates: Record<string, unknown>) {
  return {
    ...(isRecord(status) ? status : {}),
    ...updates,
    result: {
      ...(isRecord(status) && isRecord(status.result) ? status.result : {}),
      ...updates,
    },
  };
}

function dailyArtifactStatusDone(status: string) {
  return ["ready", "up_to_date", "skipped", "not_queued", "not_reported", "error"].includes(status);
}

function dailyArtifactStatusNeedsRestart(...statuses: string[]) {
  return statuses.some((status) => ["stalled", "stopped"].includes(status));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function showDailyBacktestCompletionPopup(details: {
  key: string;
  sessionDate: string;
  finishedAt: Date;
  datasetRange: BacktestRange;
  artifactSummary: ReturnType<typeof dailyBacktestArtifactSummary>;
  algorithmResults: PromiseSettledResult<unknown>[];
}) {
  const storedKey = window.localStorage.getItem(DAILY_BACKTEST_COMPLETION_POPUP_STORAGE_KEY);
  if (storedKey === details.key) {
    return;
  }
  window.localStorage.setItem(DAILY_BACKTEST_COMPLETION_POPUP_STORAGE_KEY, details.key);
  const finishedText = details.finishedAt.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const algorithmLabels = ["Voting Ensemble", "Weighted Voting", "WCA"];
  const artifactOk = !details.artifactSummary.timedOut && details.artifactSummary.mlDone && details.artifactSummary.dynamicDone && !["error", "stalled", "stopped"].includes(details.artifactSummary.mlStatus) && !["error", "stalled", "stopped"].includes(details.artifactSummary.dynamicStatus);
  const algorithmsOk = details.algorithmResults.every((result) => result.status === "fulfilled");
  const outcome = artifactOk && algorithmsOk ? "complete" : details.artifactSummary.timedOut || !artifactOk ? "attention" : "warning";
  dailyBacktestPopup.dataset.status = outcome;
  dailyBacktestPopupTitle.textContent =
    outcome === "complete"
      ? "Daily process completed"
      : outcome === "warning"
        ? "Daily process completed with warnings"
        : "Daily process needs attention";
  const algorithmRows = details.algorithmResults.map((result, index) => {
    const ok = result.status === "fulfilled";
    const reason = result.status === "rejected" && result.reason instanceof Error ? result.reason.message : "";
    return `
      <span data-status="${ok ? "pass" : "warn"}">
        ${escapeHtml(algorithmLabels[index] ?? `Algorithm ${index + 1}`)}
        <b>${ok ? "Finished" : "Finished with warning"}</b>
        ${reason ? `<small>${escapeHtml(reason.slice(0, 120))}</small>` : ""}
      </span>
    `;
  }).join("");
  const summaryText =
    outcome === "complete"
      ? "Current day data was added, ML artifacts are ready, and all algorithm backtests finished."
      : outcome === "warning"
        ? "Current day data was added and artifacts finished, but one or more algorithm refreshes returned a warning."
        : "The after-close process ran, but at least one artifact did not finish cleanly. Review the status below before relying on fresh signals.";
  dailyBacktestPopupBody.innerHTML = `
    <p>${escapeHtml(summaryText)}</p>
    <div class="daily-backtest-popup-grid">
      <span><small>Finished at</small><b>${escapeHtml(finishedText)}</b></span>
      <span><small>Session</small><b>${escapeHtml(details.sessionDate)}</b></span>
      <span><small>Dataset range</small><b>${escapeHtml(`${details.datasetRange.startDate} to ${details.datasetRange.endDate}`)}</b></span>
      <span><small>ML artifacts</small><b>${escapeHtml(details.artifactSummary.mlStatus)}</b></span>
      <span><small>Trading artifact</small><b>${escapeHtml(details.artifactSummary.dynamicStatus)}</b></span>
      <span><small>Symbol</small><b>${escapeHtml(state.symbol)}</b></span>
    </div>
    <div class="daily-backtest-popup-algos">${algorithmRows}</div>
    ${details.artifactSummary.message ? `<p class="daily-backtest-popup-note">${escapeHtml(details.artifactSummary.message)}</p>` : ""}
    ${details.artifactSummary.timedOut ? `<p class="daily-backtest-popup-warning">Artifact status polling timed out; check backend status before relying on fresh artifacts.</p>` : ""}
  `;
  dailyBacktestPopup.hidden = false;
}

async function runVotingEnsembleDailyBacktestRefresh() {
  backtestRangeCache = await getBacktestRange({ refresh: true });
  await loadLatestDynamicTradingArtifact();
  if (state.algoBacktestTimeframe === "Trading") {
    await loadTradingRag();
    return;
  }
  await loadTradingRag();
  await loadAlgoBacktestCandles();
}

async function runWeightedDailyBacktestRefresh(preparedOneMinuteCandles: Candle[], latestSessionDate: string) {
  if (weightedInitialWeightsInFlight || state.symbol !== "SPY") {
    return;
  }
  const current = loadWeightedVotingWeightState();
  if (current.source === "daily" && current.lastUpdatedDate === latestSessionDate) {
    return;
  }
  weightedInitialWeightsInFlight = true;
  try {
    const result = weightedInitialBacktestWeights(preparedOneMinuteCandles);
    if (!result) {
      return;
    }
    saveWeightedStrategyPerformance(result.performance);
    saveWeightedVotingWeightState({
      weights: result.weights,
      lastUpdatedDate: latestSessionDate,
      source: "daily",
      seededAt: new Date().toISOString(),
    });
    updateWeightedVotingPanel();
  } finally {
    weightedInitialWeightsInFlight = false;
  }
}

async function runConfidenceDailyBacktestFromPreparedCandles(preparedOneMinuteCandles: Candle[], latestSessionDate: string, reason: string) {
  const cacheKey = confidenceBacktestCacheKey(preparedOneMinuteCandles);
  if (confidenceBacktestCache?.key === cacheKey && confidenceBacktestResult) {
    confidenceBacktestStatus = "ready";
    renderConfidenceBacktestState();
    return;
  }
  const autoRunKey = `${latestSessionDate}:${cacheKey}`;
  if (confidenceBacktestAutoRunKey === autoRunKey) {
    return;
  }
  confidenceBacktestAutoRunKey = autoRunKey;
  confidenceBacktestStatus = "running";
  confidenceBacktestError = `Daily WCA backtest started after dataset refresh (${reason}).`;
  renderConfidenceBacktestState();
  await wait(0);
  try {
    const result = backtestConfidenceAggregation(preparedOneMinuteCandles);
    confidenceBacktestResult = result;
    confidenceBacktestStatus = "ready";
    saveStoredConfidenceBacktest(cacheKey, result, latestSessionDate);
  } catch (error) {
    confidenceBacktestStatus = "error";
    confidenceBacktestError = error instanceof Error ? error.message : "Unable to run WCA backtest";
  }
  renderConfidenceBacktestState();
}

function renderConfidenceBacktestState() {
  confidenceBacktestStatusLabel.textContent =
    confidenceBacktestStatus === "running"
      ? "Running after close"
      : confidenceBacktestStatus === "ready"
        ? "Daily result ready"
        : confidenceBacktestStatus === "waiting"
          ? "Waiting for dataset"
          : confidenceBacktestStatus === "error"
            ? "Backtest error"
            : "Daily closed-market run";
  if (confidenceBacktestStatus === "running") {
    confidenceBacktestSummary.innerHTML = `<span>Backtest status: <strong>Running WCA replay after market close...</strong></span><span>${escapeHtml(confidenceBacktestError || "Dataset is current; daily WCA backtest is running.")}</span>`;
    confidenceBacktestTradesTable.innerHTML = renderBacktestTrades([]);
    return;
  }
  if (confidenceBacktestStatus === "waiting") {
    confidenceBacktestSummary.innerHTML = `<span>Backtest status: <strong>Waiting</strong></span><span>${escapeHtml(confidenceBacktestError || "WCA backtest runs after market close once the backtest dataset is current.")}</span>`;
    confidenceBacktestTradesTable.innerHTML = confidenceBacktestResult ? renderBacktestTrades(confidenceBacktestResult.trades) : renderBacktestTrades([]);
    return;
  }
  if (confidenceBacktestStatus === "error") {
    confidenceBacktestSummary.innerHTML = `<span>Backtest status: <strong class="negative">${escapeHtml(confidenceBacktestError || "Unable to run WCA backtest")}</strong></span>`;
    confidenceBacktestTradesTable.innerHTML = renderBacktestTrades([]);
    return;
  }
  if (!confidenceBacktestResult) {
    confidenceBacktestSummary.innerHTML = `<span>Backtest status: <strong>Scheduled</strong></span><span>Runs daily after market close once the current session is present in the backtest dataset.</span>`;
    confidenceBacktestTradesTable.innerHTML = renderBacktestTrades([]);
    return;
  }
  confidenceBacktestSummary.innerHTML = renderConfidenceBacktestSummary(confidenceBacktestResult);
  confidenceBacktestTradesTable.innerHTML = renderBacktestTrades(confidenceBacktestResult.trades);
}

function latestRegularSessionDateForConfidenceBacktest(candles: Candle[]) {
  return candles
    .filter((candle) => isRegularSession(candle.timestamp))
    .map((candle) => easternDateString(candle.timestamp))
    .at(-1) ?? "";
}

function backtestConfidenceAggregation(candles: Candle[]): BacktestResult {
  const empty: BacktestResult = {
    timeframe: "1Min",
    dateLabel: "No candles",
    trades: [] as BacktestTrade[],
    totalPnl: 0,
    totalReturnPercent: 0,
    winners: 0,
    strategyDescription: "WCA short-cycle backtest",
  };
  const cacheKey = confidenceBacktestCacheKey(candles);
  if (confidenceBacktestCache?.key === cacheKey) {
    return confidenceBacktestCache.result;
  }
  const regularCandles = candles.filter((candle) => isRegularSession(candle.timestamp));
  if (!regularCandles.length) {
    return empty;
  }

  const sessions = Array.from(
    regularCandles.reduce((map, candle) => {
      const day = easternDateString(candle.timestamp);
      map.set(day, [...(map.get(day) ?? []), candle]);
      return map;
    }, new Map<string, Candle[]>()),
  )
    .map(([day, dayCandles]) => ({
      day,
      candles: dayCandles.slice().sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime()),
    }))
    .filter((session) => session.candles.length >= 60)
    .slice(-CONFIDENCE_BACKTEST_MAX_SESSIONS);
  if (!sessions.length) {
    const latest = regularCandles.at(-1)!;
    return { ...empty, dateLabel: formatBacktestDate(latest.timestamp), rangeLabel: "Loaded range needs at least one 60-candle regular session" };
  }

  const trades: BacktestTrade[] = [];
  const settings = state.confidenceTradingSettings;
  const defaults = confidenceDefaultSizingSettings();
  const startingCapital = settings.startingCapital;
  let equity = startingCapital;
  let peakEquity = startingCapital;
  let maxDrawdown = 0;
  let maxDrawdownPercent = 0;
  const allSortedCandles = candles.slice().sort((left, right) => new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime());
  let allCandlesCursor = 0;

  const warmup = 60;
  for (const session of sessions) {
    let openTrade:
      | {
          side: "Long";
          entryAt: string;
          entryPrice: number;
          shares: number;
          stopPrice: number;
          targetPrice: number;
          riskPerShare: number;
        }
      | null = null;
    let tradesToday = 0;
    const dayCandles = session.candles;

    for (let index = warmup; index < dayCandles.length; index += 1) {
      const candle = dayCandles[index];
      const currentTime = new Date(candle.timestamp).getTime();
      while (allCandlesCursor < allSortedCandles.length && new Date(allSortedCandles[allCandlesCursor].timestamp).getTime() <= currentTime) {
        allCandlesCursor += 1;
      }
      const allCandlesThroughNow = allSortedCandles.slice(Math.max(0, allCandlesCursor - 2000), allCandlesCursor);
      const history = dayCandles.slice(0, index + 1);
      const market = confidenceMarketSnapshotFromCandles(history, allCandlesThroughNow);
      const currentPositionValue = openTrade ? openTrade.shares * candle.close : 0;
      const result = calculateConfidenceAggregationFromMarket(market, {
        hardFilters: confidenceBacktestHardFilters,
        sizing: (snapshot, signal, normalizedNetScore) => confidenceBacktestPositionSizing(snapshot, signal, normalizedNetScore, currentPositionValue),
      });

      if (openTrade) {
        const exit = confidenceBacktestExit(openTrade, candle, history, result);
        if (exit) {
          const trade = closeConfidenceBacktestTrade(openTrade, candle.timestamp, exit.price, exit.reason, startingCapital);
          trades.push(trade);
          equity = roundNumber(equity + trade.pnl, 2);
          peakEquity = Math.max(peakEquity, equity);
          maxDrawdown = Math.max(maxDrawdown, roundNumber(peakEquity - equity, 2));
          maxDrawdownPercent = peakEquity ? Math.max(maxDrawdownPercent, roundNumber(((peakEquity - equity) / peakEquity) * 100, 2)) : 0;
          openTrade = null;
          continue;
        }
      }

      if (!openTrade && market && confidenceBacktestCanEnter(result, market, tradesToday, defaults.maxDailyTrades)) {
        const quantity = result.sizing.finalQuantity;
        const entryPrice = roundNumber(candle.close + settings.slippagePerShare, 2);
        const riskPerShare = result.sizing.stopDistance;
        openTrade = {
          side: "Long",
          entryAt: candle.timestamp,
          entryPrice,
          shares: quantity,
          stopPrice: roundNumber(Math.max(0, entryPrice - riskPerShare), 2),
          targetPrice: roundNumber(entryPrice + targetProfitDistancePerShare(quantity, riskPerShare, settings.takeProfitR), 2),
          riskPerShare,
        };
        tradesToday += 1;
      }
    }

    const finalCandle = dayCandles.at(-1)!;
    if (openTrade) {
      const exitPrice = roundNumber(Math.max(0, finalCandle.close - settings.slippagePerShare), 2);
      const trade = closeConfidenceBacktestTrade(openTrade, finalCandle.timestamp, exitPrice, "End of session", startingCapital);
      trades.push(trade);
      equity = roundNumber(equity + trade.pnl, 2);
      peakEquity = Math.max(peakEquity, equity);
      maxDrawdown = Math.max(maxDrawdown, roundNumber(peakEquity - equity, 2));
      maxDrawdownPercent = peakEquity ? Math.max(maxDrawdownPercent, roundNumber(((peakEquity - equity) / peakEquity) * 100, 2)) : maxDrawdownPercent;
    }
  }

  const firstCandle = sessions[0].candles[0];
  const finalCandle = sessions.at(-1)!.candles.at(-1)!;
  const bars = sessions.reduce((sum, session) => sum + session.candles.length, 0);

  const totalPnl = roundNumber(trades.reduce((sum, trade) => sum + trade.pnl, 0), 2);
  const totalReturnPercent = startingCapital ? roundNumber((totalPnl / startingCapital) * 100, 2) : 0;
  const winners = trades.filter((trade) => trade.pnl > 0).length;
  const losers = trades.filter((trade) => trade.pnl <= 0).length;
  const grossProfit = roundNumber(trades.filter((trade) => trade.pnl > 0).reduce((sum, trade) => sum + trade.pnl, 0), 2);
  const grossLoss = roundNumber(trades.filter((trade) => trade.pnl < 0).reduce((sum, trade) => sum + trade.pnl, 0), 2);
  const result: BacktestResult = {
    timeframe: "1Min",
    dateLabel: formatBacktestDate(finalCandle.timestamp),
    rangeLabel:
      sessions.length === 1
        ? `${formatBacktestDate(firstCandle.timestamp)} WCA short-cycle replay`
        : `${formatBacktestDate(firstCandle.timestamp)} to ${formatBacktestDate(finalCandle.timestamp)} WCA short-cycle replay`,
    trades,
    totalTrades: trades.length,
    displayedTrades: trades.length,
    totalPnl,
    totalReturnPercent,
    startingCapital,
    finalEquity: roundNumber(startingCapital + totalPnl, 2),
    maxDrawdown,
    maxDrawdownPercent,
    grossProfit,
    grossLoss,
    profitFactor: grossLoss < 0 ? roundNumber(grossProfit / Math.abs(grossLoss), 2) : grossProfit > 0 ? null : 0,
    averageWin: winners ? roundNumber(grossProfit / winners, 2) : 0,
    averageLoss: losers ? roundNumber(grossLoss / losers, 2) : 0,
    expectancy: trades.length ? roundNumber(totalPnl / trades.length, 2) : 0,
    winners,
    losers,
    bars,
    sessions: sessions.length,
    startDate: firstCandle.timestamp,
    endDate: finalCandle.timestamp,
    strategyDescription: "WCA short-cycle long entries with automatic sell exits",
    riskConfig: {
      startingCapital,
      riskPerTradePercent: defaults.baseRiskPercent,
      maxDailyLossPercent: defaults.maxDailyLossPercent,
      maxTradesPerDay: defaults.maxDailyTrades,
      sessionStart: "09:30 ET",
      newTradesUntil: "15:30 ET",
      forceClose: "15:59 ET",
      execution: "WCA automatic short-cycle Buy, WCA short-cycle Sell exit",
      stopLossPercent: defaults.minimumStopDistancePercent,
      takeProfitR: settings.takeProfitR,
      slippagePerShare: settings.slippagePerShare,
      positionSizing: "WCA normalized score ladder with ATR stop distance",
    },
  };
  confidenceBacktestCache = { key: cacheKey, result };
  return result;
}

function confidenceBacktestCacheKey(candles: Candle[]) {
  const first = candles[0]?.timestamp ?? "";
  const last = candles.at(-1)?.timestamp ?? "";
  return JSON.stringify({
    count: candles.length,
    first,
    last,
    symbol: state.symbol,
    decision: state.confidenceDecisionSettings,
    trading: {
      startingCapital: state.confidenceTradingSettings.startingCapital,
      dailyAllocationPercent: state.confidenceTradingSettings.dailyAllocationPercent,
      takeProfitR: state.confidenceTradingSettings.takeProfitR,
      slippagePerShare: state.confidenceTradingSettings.slippagePerShare,
      useDefaultSizingSettings: state.confidenceTradingSettings.useDefaultSizingSettings,
      baseRiskPercent: state.confidenceTradingSettings.baseRiskPercent,
      maxPositionPercent: state.confidenceTradingSettings.maxPositionPercent,
      maxDailyTrades: state.confidenceTradingSettings.maxTradesPerDay,
      maxSpreadPercent: state.confidenceTradingSettings.maxSpreadPercent,
      minimumOneMinuteVolume: state.confidenceTradingSettings.minimumOneMinuteVolume,
      maxParticipationPercent: state.confidenceTradingSettings.maxParticipationPercent,
      maxAllowedShares: state.confidenceTradingSettings.maxAllowedShares,
    },
  });
}

function confidenceBacktestHardFilters(market: ConfidenceMarket, rawSignal: AlgoSignal): ConfidenceAggregationResult["hardFilters"] {
  return [
    {
      label: "Spread",
      status: market.spreadLiquidity.spreadTooWide ? "fail" : "pass",
      detail: `${formatProbability(market.spreadLiquidity.spreadPercent)} / max ${formatProbability(market.spreadLiquidity.maxSpreadPercent)}`,
    },
    {
      label: "Liquidity",
      status: market.spreadLiquidity.volumeTooLow ? "fail" : "pass",
      detail: confidenceLiquidityDetail(market),
    },
    {
      label: "ATR",
      status: market.atr.regime === "extreme" ? "fail" : market.atr.regime === "high" ? "info" : "pass",
      detail:
        market.atr.regime === "extreme"
          ? "Extreme volatility blocks new WCA backtest entries"
          : `${market.atr.regime.replaceAll("_", " ")} volatility, size x${market.atr.positionSizeMultiplier.toFixed(2)}`,
    },
    {
      label: "Time",
      status: market.timeOfDay.newTradesAllowed ? "pass" : rawSignal === "Hold" ? "info" : "fail",
      detail: `${market.timeOfDay.label}, new trades ${market.timeOfDay.newTradesAllowed ? "allowed" : "blocked"}`,
    },
  ];
}

function confidenceBacktestPositionSizing(
  market: ConfidenceMarket,
  signal: AlgoSignal,
  normalizedNetScore: number,
  currentPositionValue = 0,
): ConfidencePositionSizing {
  const settings = state.confidenceTradingSettings;
  const defaults = confidenceDefaultSizingSettings();
  const accountEquity = settings.startingCapital;
  const priceValue = Math.max(market.latest.close, 0.01);
  const signalStrength = Math.abs(normalizedNetScore);
  const sizeMultiplier = confidenceSizeMultiplier(signalStrength);
  const maxPositionDollars = accountEquity * (defaults.maxPositionPercent / 100);
  const maxOrderDollars = accountEquity * (settings.orderAllocationPercent / 100);
  const dailyBuyingPowerDollars = accountEquity * (settings.dailyAllocationPercent / 100);
  const availableBuyingPower = Math.max(0, Math.min(maxPositionDollars, dailyBuyingPowerDollars) - currentPositionValue);
  const riskDollars = accountEquity * (defaults.baseRiskPercent / 100) * sizeMultiplier;
  const stopDistance = defaultSizingStopDistance(defaults, priceValue, market.atr.stopDistance / Math.max(defaults.atrStopMultiplier, 0.01));
  const sharesByRisk = stopDistance > 0 ? riskDollars / stopDistance : 0;
  const sharesByOrder = maxOrderDollars / priceValue;
  const sharesByCapital = maxPositionDollars / priceValue;
  const sharesByBuyingPower = availableBuyingPower / priceValue;
  const sharesByParticipation =
    defaults.maxParticipationPercent > 0 ? (market.latest.volume * (defaults.maxParticipationPercent / 100)) : Number.POSITIVE_INFINITY;
  const sharesByMaxAllowed = defaults.maxAllowedShares > 0 ? defaults.maxAllowedShares : Number.POSITIVE_INFINITY;
  const sizingCaps = [
    { label: "risk budget", shares: sharesByRisk },
    { label: "order limit", shares: sharesByOrder },
    { label: "max position", shares: sharesByCapital },
    { label: "buying power", shares: sharesByBuyingPower },
    { label: "liquidity participation", shares: sharesByParticipation },
    { label: "max shares", shares: sharesByMaxAllowed },
  ].filter((cap) => Number.isFinite(cap.shares));
  const limitingCap = sizingCaps.reduce(
    (smallest, cap) => (cap.shares < smallest.shares ? cap : smallest),
    { label: "sizing", shares: Number.POSITIVE_INFINITY },
  );
  const rawQuantity = Math.min(sharesByRisk, sharesByOrder, sharesByCapital, sharesByBuyingPower, sharesByParticipation, sharesByMaxAllowed);
  const finalQuantity =
    signal === "Hold" || sizeMultiplier <= 0 || stopDistance <= 0 ? 0 : Math.max(0, Math.floor(Number.isFinite(rawQuantity) ? rawQuantity : 0));
  return {
    signalStrength,
    sizeMultiplier,
    riskDollars,
    stopDistance,
    sharesByRisk,
    sharesByOrder,
    sharesByCapital,
    sharesByBuyingPower,
    sharesByLiquidity: sharesByParticipation,
    finalQuantity,
    availableBuyingPower,
    accountEquity,
    maxPositionDollars,
    currentPositionValue,
    limitingFactor: limitingCap.label,
    blockedReason:
      signal === "Hold"
        ? "final signal is Hold"
        : sizeMultiplier <= 0
          ? `signal strength ${formatProbability(signalStrength)} is below 50%`
          : finalQuantity < 1
            ? `${limitingCap.label} allows ${formatShareLimit(limitingCap.shares)}, below 1 share`
            : "",
  };
}

function confidenceBacktestCanEnter(
  result: ConfidenceAggregationResult,
  market: ConfidenceMarket,
  tradesToday: number,
  maxTradesPerDay: number,
) {
  return (
    result.signal === "Buy" &&
    result.normalizedNetScore >= state.confidenceDecisionSettings.buyThreshold &&
    result.buyAgreement >= state.confidenceDecisionSettings.minimumDirectionalAgreement &&
    result.buyAverageConfidence >= state.confidenceDecisionSettings.minimumAverageConfidence &&
    result.sizing.finalQuantity > 0 &&
    market.latest.close > market.vwap &&
    market.timeOfDay.newTradesAllowed &&
    tradesToday < maxTradesPerDay
  );
}

function confidenceBacktestExit(
  trade: { stopPrice: number; targetPrice: number },
  candle: Candle,
  history: Candle[],
  result: ConfidenceAggregationResult,
): { price: number; reason: string } | null {
  const slippage = state.confidenceTradingSettings.slippagePerShare;
  if (candle.low <= trade.stopPrice) {
    return { price: roundNumber(Math.max(0, trade.stopPrice - slippage), 2), reason: "Protective stop" };
  }
  if (candle.high >= trade.targetPrice) {
    return { price: roundNumber(Math.max(0, trade.targetPrice - slippage), 2), reason: "Target order" };
  }
  if (confidenceBacktestShortCycleSellExit(history)) {
    return { price: roundNumber(Math.max(0, candle.close - slippage), 2), reason: "Short-cycle sell exit" };
  }
  if (result.signal === "Sell") {
    return { price: roundNumber(Math.max(0, candle.close - slippage), 2), reason: "WCA Sell signal" };
  }
  if (easternMinutes(candle.timestamp) >= 15 * 60 + 59) {
    return { price: roundNumber(Math.max(0, candle.close - slippage), 2), reason: "End of session" };
  }
  return null;
}

function confidenceBacktestShortCycleSellExit(history: Candle[]) {
  const market = confidenceMarketSnapshotFromCandles(history, history);
  if (!market) {
    return false;
  }
  if (market.latest.close < market.vwap) {
    return true;
  }
  const fiveMinuteCandles = aggregateCandlesToFiveMinute(history);
  const latestFive = fiveMinuteCandles.at(-1);
  const priorFive = fiveMinuteCandles.at(-2);
  if (!latestFive || !priorFive) {
    return false;
  }
  const tolerance = Math.max(0.05, priorFive.close * 0.0002);
  return latestFive.close < priorFive.close - tolerance;
}

function closeConfidenceBacktestTrade(
  trade: { side: "Long"; entryAt: string; entryPrice: number; shares: number; stopPrice: number; targetPrice: number; riskPerShare: number },
  exitAt: string,
  exitPrice: number,
  exitReason: string,
  startingCapital: number,
): BacktestTrade {
  const pnl = roundNumber((exitPrice - trade.entryPrice) * trade.shares, 2);
  const riskDollars = trade.riskPerShare * trade.shares;
  return {
    side: trade.side,
    entryAt: trade.entryAt,
    exitAt,
    entryPrice: trade.entryPrice,
    exitPrice,
    shares: trade.shares,
    stopPrice: trade.stopPrice,
    targetPrice: trade.targetPrice,
    exitReason,
    rMultiple: riskDollars ? roundNumber(pnl / riskDollars, 2) : 0,
    accountReturnPercent: startingCapital ? roundNumber((pnl / startingCapital) * 100, 2) : 0,
    grossPnl: pnl,
    expenses: 0,
    pnl,
    returnPercent: roundNumber(((exitPrice - trade.entryPrice) / trade.entryPrice) * 100, 2),
  };
}

function backtestVotingEnsembleLastDay(candles: Candle[], timeframe: BacktestResultTimeframe): BacktestResult {
  const empty: BacktestResult = {
    timeframe,
    dateLabel: "No candles",
    trades: [] as BacktestTrade[],
    totalPnl: 0,
    totalReturnPercent: 0,
    winners: 0,
  };
  if (candles.length < 2) {
    return empty;
  }

  const latestDate = candles[candles.length - 1].timestamp.slice(0, 10);
  const firstDayIndex = candles.findIndex((candle) => candle.timestamp.slice(0, 10) === latestDate);
  const dayCandles = candles.slice(firstDayIndex).filter((candle) => candle.timestamp.slice(0, 10) === latestDate);
  if (firstDayIndex < 0 || dayCandles.length < 20) {
    return { ...empty, dateLabel: latestDate || "Not enough candles" };
  }

  const priorClose = firstDayIndex > 0 ? candles[firstDayIndex - 1].close : dayCandles[0].open;
  const trades: BacktestTrade[] = [];
  let openTrade: { side: "Long" | "Short"; entryAt: string; entryPrice: number } | null = null;

  const warmup = timeframe === "1Min" ? 50 : 20;
  for (let index = Math.min(warmup, dayCandles.length - 1); index < dayCandles.length; index += 1) {
    const history = dayCandles.slice(0, index + 1);
    const signal = historicalWinnerSignal(history, priorClose);
    const candle = dayCandles[index];
    if (signal === "Hold") {
      continue;
    }
    const side: "Long" | "Short" = signal === "Buy" ? "Long" : "Short";
    if (openTrade !== null && openTrade.side === side) {
      continue;
    }
    if (openTrade) {
      trades.push(closeBacktestTrade(openTrade, candle.timestamp, candle.close));
    }
    openTrade = {
      side,
      entryAt: candle.timestamp,
      entryPrice: candle.close,
    };
  }

  const finalCandle = dayCandles[dayCandles.length - 1];
  if (openTrade) {
    trades.push(closeBacktestTrade(openTrade, finalCandle.timestamp, finalCandle.close));
  }

  const totalPnl = roundNumber(trades.reduce((sum, trade) => sum + trade.pnl, 0), 2);
  const totalReturnPercent = roundNumber(trades.reduce((sum, trade) => sum + trade.returnPercent, 0), 2);
  return {
    timeframe,
    dateLabel: formatBacktestDate(finalCandle.timestamp),
    trades,
    totalPnl,
    totalReturnPercent,
    winners: trades.filter((trade) => trade.pnl > 0).length,
  };
}

function aggregateCandlesToFiveMinute(candles: Candle[]): Candle[] {
  return aggregateCandlesToMinutes(candles, 5, "5Min");
}

function aggregateCandlesToMinutes(candles: Candle[], minutes: number, timeframe: Timeframe): Candle[] {
  const buckets = new Map<string, Candle[]>();
  candles.forEach((candle) => {
    const date = new Date(candle.timestamp);
    if (Number.isNaN(date.getTime())) {
      return;
    }
    const bucketMinute = Math.floor(date.getUTCMinutes() / minutes) * minutes;
    date.setUTCMinutes(bucketMinute, 0, 0);
    const bucketKey = date.toISOString();
    buckets.set(bucketKey, [...(buckets.get(bucketKey) ?? []), candle]);
  });

  return Array.from(buckets.entries())
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([timestamp, bucket]) => {
      const first = bucket[0];
      const last = bucket[bucket.length - 1];
      const volume = bucket.reduce((sum, candle) => sum + candle.volume, 0);
      const tradeCount = bucket.reduce((sum, candle) => sum + (candle.trade_count ?? 0), 0);
      const vwapNumerator = bucket.reduce((sum, candle) => sum + (candle.vwap ?? candle.close) * candle.volume, 0);
      return {
        ...last,
        timeframe,
        timestamp,
        open: first.open,
        high: Math.max(...bucket.map((candle) => candle.high)),
        low: Math.min(...bucket.map((candle) => candle.low)),
        close: last.close,
        volume,
        trade_count: tradeCount || null,
        vwap: volume ? vwapNumerator / volume : null,
      };
    });
}

function historicalWinnerSignal(history: Candle[], priorClose: number): AlgoSignal {
  const votes = historicalStrategyVotes(history, priorClose);
  return winningVoteSignal(
    votes.filter((vote) => vote.signal === "Buy").length,
    votes.filter((vote) => vote.signal === "Sell").length,
    votes.filter((vote) => vote.signal === "Hold").length,
  );
}

function historicalStrategyVotes(history: Candle[], priorClose: number): AlgoVote[] {
  const closes = history.map((candle) => candle.close);
  const latest = history[history.length - 1];
  const dayOpen = history[0].open;
  const sma20 = simpleMovingAverage(closes, 20);
  const sma50 = simpleMovingAverage(closes, 50);
  const rsi = relativeStrengthIndex(closes, 14);
  const vwap = sessionVwapValue(history);
  const openingRange = openingRangeValues(history, 15);
  const priorRange = history.slice(-21, -1);
  const priorHigh = priorRange.length ? Math.max(...priorRange.map((candle) => candle.high)) : latest.high;
  const priorLow = priorRange.length ? Math.min(...priorRange.map((candle) => candle.low)) : latest.low;
  const averageVolume = simpleMovingAverage(history.map((candle) => candle.volume), Math.min(20, history.length)) ?? latest.volume;
  const gapPercent = priorClose ? ((dayOpen - priorClose) / priorClose) * 100 : 0;
  const atr = averageTrueRange(history, Math.min(14, history.length - 1));
  const bands = bollingerBands(closes, 20, 2);
  const recentBase = closes[Math.max(0, closes.length - 20)];
  const recentReturn = recentBase ? ((latest.close - recentBase) / recentBase) * 100 : 0;
  const failedBreakout = (latest.high > priorHigh && latest.close < priorHigh) || (latest.low < priorLow && latest.close > priorLow);
  const liquiditySweep = latest.volume > averageVolume * 1.1 && failedBreakout;
  const trendUp = sma20 !== null && sma50 !== null && sma20 > sma50 && latest.close > sma20 && latest.close > vwap;
  const trendDown = sma20 !== null && sma50 !== null && sma20 < sma50 && latest.close < sma20 && latest.close < vwap;
  const rawVotes: AlgoVote[] = [
    {
      strategy: "Multi-Timeframe Trend Alignment",
      signal: trendUp ? "Buy" : trendDown ? "Sell" : "Hold",
      detail: "20/50 SMA plus VWAP alignment",
    },
    {
      strategy: "First Pullback After Open",
      signal:
        sma20 !== null && sma50 !== null && sma20 > sma50 && openingRange.high < latest.close && latest.close <= sma20 * 1.002
          ? "Buy"
          : sma20 !== null && sma50 !== null && sma20 < sma50 && openingRange.low > latest.close && latest.close >= sma20 * 0.998
            ? "Sell"
            : "Hold",
      detail: "Trend continuation after opening-range pullback",
    },
    {
      strategy: "Failed Breakout Strategy",
      signal: failedBreakout ? (latest.high > priorHigh ? "Sell" : "Buy") : "Hold",
      detail: "Fades failed prior-range expansion",
    },
    {
      strategy: "Liquidity Sweep Reversal",
      signal: liquiditySweep ? (latest.high > priorHigh ? "Sell" : "Buy") : "Hold",
      detail: "Fades volume-backed liquidity sweep",
    },
    {
      strategy: "Bollinger Band Reversion",
      signal:
        bands && rsi !== null && latest.close < bands.lower && rsi < 45
          ? "Buy"
          : bands && rsi !== null && latest.close > bands.upper && rsi > 55
            ? "Sell"
            : "Hold",
      detail: "Fades Bollinger extension with RSI confirmation",
    },
    {
      strategy: "ATR Overextension Reversion",
      signal:
        atr && sma20 !== null && latest.close < sma20 - atr * 1.25
          ? "Buy"
          : atr && sma20 !== null && latest.close > sma20 + atr * 1.25
            ? "Sell"
            : "Hold",
      detail: "Fades ATR distance from 20 SMA",
    },
    {
      strategy: "Relative Strength vs QQQ/IWM",
      signal: recentReturn > 1.2 && latest.close > vwap ? "Buy" : recentReturn < -1.2 && latest.close < vwap ? "Sell" : "Hold",
      detail: "SPY-relative-strength proxy until QQQ/IWM feeds are attached",
    },
    {
      strategy: "Market Breadth Momentum",
      signal: trendUp && recentReturn > 0.6 ? "Buy" : trendDown && recentReturn < -0.6 ? "Sell" : "Hold",
      detail: "SPY breadth-momentum proxy until breadth feed is attached",
    },
    {
      strategy: "Economic Event Reaction Strategy",
      signal: gapPercent > 0.15 && latest.close > openingRange.high ? "Buy" : gapPercent < -0.15 && latest.close < openingRange.low ? "Sell" : "Hold",
      detail: "Gap/opening-range event reaction",
    },
  ];
  const buyVotes = rawVotes.filter((vote) => vote.signal === "Buy").length;
  const sellVotes = rawVotes.filter((vote) => vote.signal === "Sell").length;
  rawVotes.push({
    strategy: "Ensemble Strategy Voting",
    signal: buyVotes >= 3 && buyVotes > sellVotes ? "Buy" : sellVotes >= 3 && sellVotes > buyVotes ? "Sell" : "Hold",
    detail: "Meta vote follows clear strategy majority",
  });
  return rawVotes;
}

function closeBacktestTrade(
  trade: { side: "Long" | "Short"; entryAt: string; entryPrice: number },
  exitAt: string,
  exitPrice: number,
): BacktestTrade {
  const direction = trade.side === "Long" ? 1 : -1;
  const pnl = roundNumber((exitPrice - trade.entryPrice) * direction, 2);
  return {
    side: trade.side,
    entryAt: trade.entryAt,
    exitAt,
    entryPrice: trade.entryPrice,
    exitPrice,
    pnl,
    returnPercent: roundNumber((pnl / trade.entryPrice) * 100, 2),
  };
}

function renderConfidenceBacktestSummary(backtest: BacktestResult) {
  const totalTrades = backtest.totalTrades ?? backtest.trades.length;
  const winRate = totalTrades ? formatProbability(backtest.winners / totalTrades) : "0%";
  const rangeLabel = backtest.rangeLabel ?? backtest.dateLabel;
  const profitFactor =
    backtest.profitFactor === null ? "Open-ended" : backtest.profitFactor === undefined ? "NA" : backtest.profitFactor.toFixed(2);
  return `
    <span>Backtest range: <strong>${escapeHtml(rangeLabel)}</strong></span>
    <span>Model: ${escapeHtml(backtest.strategyDescription ?? "WCA short-cycle backtest")}</span>
    <span>Bars: ${backtest.bars ?? "NA"} · trades: ${totalTrades} · win rate: ${winRate}</span>
    <span>P/L: <strong class="${backtest.totalPnl >= 0 ? "positive" : "negative"}">${signedCurrency(backtest.totalPnl)}</strong> (${signed(backtest.totalReturnPercent)}% account)</span>
    <span>Drawdown: ${currency(backtest.maxDrawdown ?? 0)} (${roundNumber(backtest.maxDrawdownPercent ?? 0, 2)}%) · profit factor: ${profitFactor}</span>
  `;
}

function renderBacktestTrades(trades: BacktestTrade[]) {
  if (!trades.length) {
    return `
      <tr>
        <td colspan="4">No closed trades generated for the selected backtest range.</td>
      </tr>
    `;
  }
  return trades
    .map(
      (trade) => `
        <tr>
          <td>${trade.side}${trade.shares ? ` · ${trade.shares} sh` : ""}</td>
          <td>${formatTime(trade.entryAt)} @ ${price(trade.entryPrice)}</td>
          <td>${formatTime(trade.exitAt)} @ ${price(trade.exitPrice)}${trade.exitReason ? ` · ${escapeHtml(trade.exitReason)}` : ""}</td>
          <td class="${trade.pnl >= 0 ? "positive" : "negative"}">${signedCurrency(trade.pnl)} (${signed(trade.returnPercent)}%, ${trade.rMultiple ?? "NA"}R)</td>
        </tr>
      `,
    )
    .join("");
}

function sessionVwapValue(candles: Candle[]) {
  let cumulativePriceVolume = 0;
  let cumulativeVolume = 0;
  candles.forEach((candle) => {
    const volume = Math.max(0, candle.volume);
    const typical = (candle.high + candle.low + candle.close) / 3;
    cumulativePriceVolume += typical * volume;
    cumulativeVolume += volume;
  });
  return cumulativeVolume ? cumulativePriceVolume / cumulativeVolume : candles[candles.length - 1]?.close ?? 0;
}

function openingRangeValues(candles: Candle[], count: number) {
  const opening = candles.slice(0, Math.min(count, candles.length));
  return {
    high: Math.max(...opening.map((candle) => candle.high)),
    low: Math.min(...opening.map((candle) => candle.low)),
  };
}

function bollingerBands(values: number[], period: number, deviations: number) {
  if (values.length < period) {
    return null;
  }
  const sample = values.slice(-period);
  const middle = sample.reduce((sum, value) => sum + value, 0) / period;
  const variance = sample.reduce((sum, value) => sum + (value - middle) ** 2, 0) / period;
  const width = Math.sqrt(variance) * deviations;
  return { middle, upper: middle + width, lower: middle - width };
}

function averageTrueRange(candles: Candle[], period: number) {
  if (period <= 0 || candles.length <= period) {
    return null;
  }
  const sample = candles.slice(-(period + 1));
  const ranges: number[] = [];
  for (let index = 1; index < sample.length; index += 1) {
    const current = sample[index];
    const previous = sample[index - 1];
    ranges.push(Math.max(current.high - current.low, Math.abs(current.high - previous.close), Math.abs(current.low - previous.close)));
  }
  return ranges.length ? ranges.reduce((sum, value) => sum + value, 0) / ranges.length : null;
}

function rollingAtrSeries(candles: Candle[], period: number) {
  if (period <= 0 || candles.length <= period) {
    return [];
  }
  const values: number[] = [];
  for (let end = period + 1; end <= candles.length; end += 1) {
    const atr = averageTrueRange(candles.slice(0, end), period);
    if (atr !== null) {
      values.push(atr);
    }
  }
  return values;
}

function averageDirectionalIndex(candles: Candle[], period: number): ConfidenceAdxContext | null {
  if (period <= 1 || candles.length <= period * 2) {
    return null;
  }
  const plusDm: number[] = [];
  const minusDm: number[] = [];
  const trueRanges: number[] = [];
  for (let index = 1; index < candles.length; index += 1) {
    const current = candles[index];
    const previous = candles[index - 1];
    const upMove = current.high - previous.high;
    const downMove = previous.low - current.low;
    plusDm.push(upMove > downMove && upMove > 0 ? upMove : 0);
    minusDm.push(downMove > upMove && downMove > 0 ? downMove : 0);
    trueRanges.push(Math.max(current.high - current.low, Math.abs(current.high - previous.close), Math.abs(current.low - previous.close)));
  }
  const rows: Array<{ plusDi: number; minusDi: number; dx: number }> = [];
  for (let index = period; index <= trueRanges.length; index += 1) {
    const tr = trueRanges.slice(index - period, index).reduce((sum, value) => sum + value, 0);
    if (!tr) {
      continue;
    }
    const plusDi = (plusDm.slice(index - period, index).reduce((sum, value) => sum + value, 0) / tr) * 100;
    const minusDi = (minusDm.slice(index - period, index).reduce((sum, value) => sum + value, 0) / tr) * 100;
    const directionalTotal = plusDi + minusDi;
    if (directionalTotal) {
      rows.push({ plusDi, minusDi, dx: (Math.abs(plusDi - minusDi) / directionalTotal) * 100 });
    }
  }
  const recent = rows.slice(-period);
  if (!recent.length) {
    return null;
  }
  const adx = recent.reduce((sum, row) => sum + row.dx, 0) / recent.length;
  const previous = rows.slice(-(period * 2), -period);
  const previousAdx = previous.length ? previous.reduce((sum, row) => sum + row.dx, 0) / previous.length : adx;
  const latest = rows.at(-1)!;
  const slope = adx - previousAdx;
  const regime = confidenceAdxRegime(adx, latest.plusDi, latest.minusDi, slope);
  return {
    adx: roundNumber(adx, 2),
    plusDi: roundNumber(latest.plusDi, 2),
    minusDi: roundNumber(latest.minusDi, 2),
    slope: roundNumber(slope, 2),
    regime,
  };
}

function confidenceAdxRegime(adx: number, plusDi: number, minusDi: number, slope: number): ConfidenceAdxRegime {
  if (adx < 15) {
    return "range";
  }
  if (adx <= 25) {
    return "mixed";
  }
  if (plusDi > minusDi) {
    return adx >= 35 && slope >= 0 ? "very_strong_bullish_trend" : "bullish_trend";
  }
  if (minusDi > plusDi) {
    return adx >= 35 && slope >= 0 ? "very_strong_bearish_trend" : "bearish_trend";
  }
  return "mixed";
}

function marketStructureSignal(candles: Candle[]) {
  if (candles.length < 12) {
    return "neutral" as const;
  }
  const recent = candles.slice(-6);
  const previous = candles.slice(-12, -6);
  const recentHigh = Math.max(...recent.map((candle) => candle.high));
  const recentLow = Math.min(...recent.map((candle) => candle.low));
  const previousHigh = Math.max(...previous.map((candle) => candle.high));
  const previousLow = Math.min(...previous.map((candle) => candle.low));
  if (recentHigh > previousHigh && recentLow > previousLow) {
    return "up" as const;
  }
  if (recentHigh < previousHigh && recentLow < previousLow) {
    return "down" as const;
  }
  return "neutral" as const;
}

function marketStructureContext(candles: Candle[], vwap: number) {
  const lookback = candles.slice(-50);
  if (lookback.length < 12) {
    return null;
  }
  const window = lookback.length >= 24 ? 4 : 3;
  const swingHighs: Array<{ index: number; value: number }> = [];
  const swingLows: Array<{ index: number; value: number }> = [];

  for (let index = window; index < lookback.length - window; index += 1) {
    const sample = lookback.slice(index - window, index + window + 1);
    const candle = lookback[index];
    if (candle.high === Math.max(...sample.map((item) => item.high))) {
      swingHighs.push({ index, value: candle.high });
    }
    if (candle.low === Math.min(...sample.map((item) => item.low))) {
      swingLows.push({ index, value: candle.low });
    }
  }

  const latest = lookback.at(-1)!;
  const highs = swingHighs.slice(-2);
  const lows = swingLows.slice(-2);
  const previousHigh = highs.at(-2);
  const latestHigh = highs.at(-1);
  const previousLow = lows.at(-2);
  const latestLow = lows.at(-1);
  const higherHigh = Boolean(previousHigh && latestHigh && latestHigh.value > previousHigh.value);
  const lowerHigh = Boolean(previousHigh && latestHigh && latestHigh.value < previousHigh.value);
  const higherLow = Boolean(previousLow && latestLow && latestLow.value > previousLow.value);
  const lowerLow = Boolean(previousLow && latestLow && latestLow.value < previousLow.value);
  const resistance = latestHigh?.value ?? Math.max(...lookback.slice(-20).map((candle) => candle.high));
  const support = latestLow?.value ?? Math.min(...lookback.slice(-20).map((candle) => candle.low));
  const tolerance = Math.max(latest.close * 0.0008, 0.01);
  const breakOfStructureUp = previousHigh ? latest.close > previousHigh.value : false;
  const breakOfStructureDown = previousLow ? latest.close < previousLow.value : false;
  const changeOfCharacterUp = lowerLow && latest.close > resistance;
  const changeOfCharacterDown = higherHigh && latest.close < support;
  const successfulSupportRetest = latest.low <= support + tolerance && latest.close > support && latest.close > vwap;
  const failedResistanceRetest = latest.high >= resistance - tolerance && latest.close < resistance && latest.close < vwap;
  const breakRetestSucceeded = breakOfStructureUp && latest.low <= resistance + tolerance && latest.close > resistance;
  const breakRetestFailed = breakOfStructureDown && latest.high >= support - tolerance && latest.close < support;
  const labels = [
    higherHigh ? "HH" : "",
    higherLow ? "HL" : "",
    lowerHigh ? "LH" : "",
    lowerLow ? "LL" : "",
    breakOfStructureUp ? "bullish BOS" : "",
    breakOfStructureDown ? "bearish BOS" : "",
    changeOfCharacterUp ? "bullish CHoCH" : "",
    changeOfCharacterDown ? "bearish CHoCH" : "",
  ].filter(Boolean);

  return {
    higherHigh,
    higherLow,
    lowerHigh,
    lowerLow,
    breakOfStructureUp,
    breakOfStructureDown,
    changeOfCharacterUp,
    changeOfCharacterDown,
    successfulSupportRetest,
    failedResistanceRetest,
    breakRetestSucceeded,
    breakRetestFailed,
    swingHigh: resistance,
    swingLow: support,
    summary: `${labels.length ? labels.join(", ") : "no clear swing sequence"}; support ${price(support)}, resistance ${price(resistance)}`,
  };
}

function roundNumber(value: number, digits: number) {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function roundNumberUp(value: number, digits: number) {
  const factor = 10 ** digits;
  return Math.ceil((value - Number.EPSILON) * factor) / factor;
}

function targetProfitDistancePerShare(quantity: number, riskDistance: number, takeProfitR: number) {
  const perShareForTradeFloor = quantity > 0 ? MIN_TARGET_PROFIT_PER_TRADE / quantity : MIN_TARGET_PROFIT_PER_TRADE;
  return Math.max(riskDistance * takeProfitR, MIN_TARGET_PROFIT_PER_SHARE, perShareForTradeFloor);
}

function signedPrice(value: number) {
  return `${value >= 0 ? "+" : ""}${price(value)}`;
}

function currency(value: number) {
  return Intl.NumberFormat("en", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function signedCurrency(value: number) {
  return `${value >= 0 ? "+" : ""}${currency(value)}`;
}

function formatBacktestDate(value: string) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "2-digit",
    year: "numeric",
  }).format(new Date(value));
}

function movingAverageTrendSignal(candles: Candle[]): AlgoVote {
  const closes = candles.map((candle) => candle.close);
  const shortAverage = simpleMovingAverage(closes, 20);
  const longAverage = simpleMovingAverage(closes, 50);
  const latest = closes[closes.length - 1];
  if (!latest || shortAverage === null || longAverage === null) {
    return { strategy: "Moving Average Trend", signal: "Hold", detail: "Waiting for 50 candles" };
  }
  if (shortAverage > longAverage && latest > shortAverage) {
    return { strategy: "Moving Average Trend", signal: "Buy", detail: `20 SMA ${price(shortAverage)} above 50 SMA ${price(longAverage)}` };
  }
  if (shortAverage < longAverage && latest < shortAverage) {
    return { strategy: "Moving Average Trend", signal: "Sell", detail: `20 SMA ${price(shortAverage)} below 50 SMA ${price(longAverage)}` };
  }
  return { strategy: "Moving Average Trend", signal: "Hold", detail: "Moving averages are mixed" };
}

function rsiMeanReversionSignal(candles: Candle[]): AlgoVote {
  const rsi = relativeStrengthIndex(candles.map((candle) => candle.close), 14);
  if (rsi === null) {
    return { strategy: "RSI Mean Reversion", signal: "Hold", detail: "Waiting for 15 candles" };
  }
  if (rsi <= 30) {
    return { strategy: "RSI Mean Reversion", signal: "Buy", detail: `RSI ${rsi.toFixed(1)} is oversold` };
  }
  if (rsi >= 70) {
    return { strategy: "RSI Mean Reversion", signal: "Sell", detail: `RSI ${rsi.toFixed(1)} is overbought` };
  }
  return { strategy: "RSI Mean Reversion", signal: "Hold", detail: `RSI ${rsi.toFixed(1)} is neutral` };
}

function breakoutSignal(candles: Candle[]): AlgoVote {
  if (candles.length < 21) {
    return { strategy: "Breakout Strategy", signal: "Hold", detail: "Waiting for 21 candles" };
  }
  const latest = candles[candles.length - 1];
  const prior = candles.slice(-21, -1);
  const priorHigh = Math.max(...prior.map((candle) => candle.high));
  const priorLow = Math.min(...prior.map((candle) => candle.low));
  if (latest.close > priorHigh) {
    return { strategy: "Breakout Strategy", signal: "Buy", detail: `Close broke above ${price(priorHigh)}` };
  }
  if (latest.close < priorLow) {
    return { strategy: "Breakout Strategy", signal: "Sell", detail: `Close broke below ${price(priorLow)}` };
  }
  return { strategy: "Breakout Strategy", signal: "Hold", detail: `Inside ${price(priorLow)}-${price(priorHigh)} range` };
}

function macdSignal(candles: Candle[]): AlgoVote {
  const closes = candles.map((candle) => candle.close);
  const macd = macdValues(closes);
  if (!macd) {
    return { strategy: "MACD Strategy", signal: "Hold", detail: "Waiting for MACD history" };
  }
  if (macd.macd > macd.signal && macd.histogram > 0) {
    return { strategy: "MACD Strategy", signal: "Buy", detail: `Histogram ${macd.histogram.toFixed(3)} positive` };
  }
  if (macd.macd < macd.signal && macd.histogram < 0) {
    return { strategy: "MACD Strategy", signal: "Sell", detail: `Histogram ${macd.histogram.toFixed(3)} negative` };
  }
  return { strategy: "MACD Strategy", signal: "Hold", detail: "MACD is flat or crossing" };
}

function simpleMovingAverage(values: number[], period: number) {
  if (values.length < period) {
    return null;
  }
  const sample = values.slice(-period);
  return sample.reduce((sum, value) => sum + value, 0) / period;
}

function relativeStrengthIndex(values: number[], period: number) {
  if (values.length <= period) {
    return null;
  }
  const sample = values.slice(-(period + 1));
  let gains = 0;
  let losses = 0;
  for (let index = 1; index < sample.length; index += 1) {
    const change = sample[index] - sample[index - 1];
    if (change >= 0) {
      gains += change;
    } else {
      losses += Math.abs(change);
    }
  }
  if (losses === 0) {
    return 100;
  }
  const rs = gains / period / (losses / period);
  return 100 - 100 / (1 + rs);
}

function macdValues(values: number[]) {
  if (values.length < 35) {
    return null;
  }
  const ema12 = exponentialMovingAverageSeries(values, 12);
  const ema26 = exponentialMovingAverageSeries(values, 26);
  const macdSeries = values
    .map((_, index) => (ema12[index] !== null && ema26[index] !== null ? ema12[index]! - ema26[index]! : null))
    .filter((value): value is number => value !== null);
  const signalSeries = exponentialMovingAverageSeries(macdSeries, 9).filter((value): value is number => value !== null);
  if (!macdSeries.length || !signalSeries.length) {
    return null;
  }
  const macd = macdSeries[macdSeries.length - 1];
  const signal = signalSeries[signalSeries.length - 1];
  return {
    macd,
    signal,
    histogram: macd - signal,
  };
}

function exponentialMovingAverageSeries(values: number[], period: number): Array<number | null> {
  const output: Array<number | null> = Array(values.length).fill(null);
  if (values.length < period) {
    return output;
  }
  const multiplier = 2 / (period + 1);
  let ema = values.slice(0, period).reduce((sum, value) => sum + value, 0) / period;
  output[period - 1] = ema;
  for (let index = period; index < values.length; index += 1) {
    ema = (values[index] - ema) * multiplier + ema;
    output[index] = ema;
  }
  return output;
}

function clampViewportOffset(offset: number) {
  return Math.max(0, Math.min(offset, maxViewportOffset()));
}

function maxViewportOffset() {
  return Math.max(0, state.candles.length - Math.min(state.visibleCount, state.candles.length));
}

function resetViewport() {
  state.viewportOffset = 0;
  state.hoveredIndex = -1;
  state.hoverX = -1;
  state.hoverY = -1;
  state.historyEndReached = false;
}

function resetZoomState() {
  state.visibleCount = defaultVisibleCandles;
  updateZoomLevel();
}

function zoomChart(direction: "in" | "out") {
  const nextCount =
    direction === "in"
      ? Math.round(state.visibleCount / zoomFactor)
      : Math.round(state.visibleCount * zoomFactor);
  state.visibleCount = Math.max(minVisibleCandles, Math.min(maxVisibleCandles, nextCount));
  state.viewportOffset = clampViewportOffset(state.viewportOffset);
  updateZoomLevel();
  state.hoveredIndex = -1;
  state.hoverX = -1;
  state.hoverY = -1;
  canvas.classList.remove("over-candle");
  drawChart();
  scheduleVisibleContextUpdate();
}

function resetZoom() {
  resetZoomState();
  state.viewportOffset = clampViewportOffset(state.viewportOffset);
  state.hoveredIndex = -1;
  state.hoverX = -1;
  state.hoverY = -1;
  canvas.classList.remove("over-candle");
  drawChart();
  scheduleVisibleContextUpdate();
}

function updateZoomLevel() {
  zoomLevel.textContent = `${Math.round((defaultVisibleCandles / state.visibleCount) * 100)}%`;
}

function setCandleSettingsOpen(open: boolean) {
  candleSettingsMenu.hidden = !open;
  candleSettingsButton.setAttribute("aria-expanded", String(open));
}

function updateCandleSettingsControls() {
  candleWidthInput.value = String(state.candleWidthPercent);
  candleWidthValue.textContent = `${state.candleWidthPercent}%`;
  wickToggle.checked = state.showWicks;
  volumeToggle.checked = state.showVolume;
  priceLineToggle.checked = state.showPriceLine;
}

function updateOverlayToggleControls() {
  visualConditionsButton.classList.toggle("active", state.showVisualConditions);
  visualConditionsButton.setAttribute("aria-pressed", String(state.showVisualConditions));
  layerBackgroundsButton.classList.toggle("active", state.showLayerBackgrounds);
  layerBackgroundsButton.setAttribute("aria-pressed", String(state.showLayerBackgrounds));
}

function chartBounds(width: number, height: number) {
  const rightAxisWidth = 88;
  return {
    left: 0,
    top: 18,
    right: width - rightAxisWidth,
    bottom: height - 42,
    volumeTop: height - 120,
    width: width - rightAxisWidth,
    height: height - 60,
  };
}

function drawLayerBackgrounds(
  ctx: CanvasRenderingContext2D,
  bounds: ReturnType<typeof chartBounds>,
  candles: Candle[],
) {
  const context = state.marketContext;
  if (!context || !candles.length) {
    return;
  }

  const layers = [context.regime, context.session, context.event];
  const candleSlot = (bounds.right - bounds.left) / candles.length;

  layers.forEach((layer) => {
    visibleLayerRanges(layer, candles).forEach((range) => {
      const x = bounds.left + candleSlot * range.startIndex;
      const width = Math.max(candleSlot, candleSlot * (range.endIndex - range.startIndex + 1));
      const lane = layerLane(layer.layer, bounds);
      ctx.fillStyle = chartLayerFill(layer.layer);
      ctx.fillRect(x, lane.top, width, lane.height);
    });
  });
}

function visibleLayerRanges(layer: MarketLayer, candles: Candle[]) {
  const window = layer.candleWindow;
  if (!window?.start || !window.end) {
    return [];
  }
  if (layer.layer !== "regime" && state.timeframe === "1Day") {
    return [];
  }

  if (layer.layer === "session") {
    return visibleSessionRanges(candles);
  }

  if (layer.layer === "event") {
    return visibleEventRanges(candles);
  }

  if (layer.layer === "regime" && state.timeframe !== "1Day") {
    const ranges = visibleRegimeRanges(layer, candles);
    return ranges.length ? ranges : [{ startIndex: 0, endIndex: candles.length - 1 }];
  }

  const candleTimes = candles.map((candle, index) => ({
    index,
    time: new Date(candle.timestamp).getTime(),
  }));

  return (window.segments?.length ? window.segments : [{ start: window.start, end: window.end }])
    .map((segment) => {
      if (!segment.start || !segment.end) {
        return null;
      }
      const start = new Date(segment.start).getTime();
      const end = new Date(segment.end).getTime();
      const indexes = candleTimes
        .filter((item) => item.time >= start && item.time <= end)
        .map((item) => item.index);
      if (!indexes.length) {
        return null;
      }
      return {
        startIndex: Math.min(...indexes),
        endIndex: Math.max(...indexes),
      };
    })
    .filter((range): range is { startIndex: number; endIndex: number } => Boolean(range));
}

function visibleRegimeRanges(layer: MarketLayer, candles: Candle[]) {
  const startDay = layer.candleWindow.start?.slice(0, 10);
  const endDay = layer.candleWindow.end?.slice(0, 10);
  if (!startDay || !endDay) {
    return [];
  }

  const indexes = candles
    .map((candle, index) => ({ day: candle.timestamp.slice(0, 10), index }))
    .filter((item) => item.day >= startDay && item.day <= endDay)
    .map((item) => item.index);

  if (!indexes.length) {
    return [];
  }

  return [
    {
      startIndex: Math.min(...indexes),
      endIndex: Math.max(...indexes),
    },
  ];
}

function visibleSessionRanges(candles: Candle[]) {
  return visibleDayGroups(candles).map((group) => ({
    startIndex: group.startIndex,
    endIndex: group.endIndex,
  }));
}

function visibleEventRanges(candles: Candle[]) {
  const visibleIndexByTimestamp = new Map(candles.map((candle, index) => [candle.timestamp, index]));
  const visibleDays = new Set(candles.map((candle) => candle.timestamp.slice(0, 10)));

  return fullDayGroups()
    .filter((group) => visibleDays.has(group.day))
    .flatMap((group) => {
      const length = group.endIndex - group.startIndex + 1;
      const openingEnd = group.startIndex + Math.min(14, length - 1);
      const closeStart = Math.max(group.startIndex, group.endIndex - 4);
      const ranges = [
        visibleRangeForGlobalSegment(group.startIndex, openingEnd, visibleIndexByTimestamp),
      ];

      if (closeStart > openingEnd + 1) {
        ranges.push(visibleRangeForGlobalSegment(closeStart, group.endIndex, visibleIndexByTimestamp));
      }

      return ranges.filter((range): range is { startIndex: number; endIndex: number } => Boolean(range));
    });
}

function visibleDayGroups(candles: Candle[]) {
  const groups: Array<{ day: string; startIndex: number; endIndex: number }> = [];
  candles.forEach((candle, index) => {
    const day = candle.timestamp.slice(0, 10);
    const latest = groups[groups.length - 1];
    if (latest?.day === day) {
      latest.endIndex = index;
      return;
    }
    groups.push({ day, startIndex: index, endIndex: index });
  });
  return groups;
}

function fullDayGroups() {
  const groups: Array<{ day: string; startIndex: number; endIndex: number }> = [];
  state.candles.forEach((candle, index) => {
    const day = candle.timestamp.slice(0, 10);
    const latest = groups[groups.length - 1];
    if (latest?.day === day) {
      latest.endIndex = index;
      return;
    }
    groups.push({ day, startIndex: index, endIndex: index });
  });
  return groups;
}

function visibleRangeForGlobalSegment(
  globalStart: number,
  globalEnd: number,
  visibleIndexByTimestamp: Map<string, number>,
) {
  const visibleIndexes: number[] = [];
  for (let index = globalStart; index <= globalEnd; index += 1) {
    const visibleIndex = visibleIndexByTimestamp.get(state.candles[index]?.timestamp);
    if (visibleIndex !== undefined) {
      visibleIndexes.push(visibleIndex);
    }
  }

  if (!visibleIndexes.length) {
    return null;
  }

  return {
    startIndex: Math.min(...visibleIndexes),
    endIndex: Math.max(...visibleIndexes),
  };
}

function chartLayerFill(layer: MarketLayer["layer"]) {
  const fills: Record<MarketLayer["layer"], string> = {
    regime: "rgba(37, 99, 235, 0.24)",
    session: "rgba(5, 150, 105, 0.12)",
    event: "rgba(217, 119, 6, 0.2)",
  };
  return fills[layer];
}

function layerLane(layer: MarketLayer["layer"], bounds: ReturnType<typeof chartBounds>) {
  const plotHeight = bounds.bottom - bounds.top;
  const lanes: Record<MarketLayer["layer"], { top: number; height: number }> = {
    regime: {
      top: bounds.top,
      height: plotHeight * 0.44,
    },
    session: {
      top: bounds.top + plotHeight * 0.44,
      height: plotHeight * 0.56,
    },
    event: {
      top: bounds.top,
      height: plotHeight,
    },
  };
  return lanes[layer];
}

function drawVisualConditions(
  ctx: CanvasRenderingContext2D,
  bounds: ReturnType<typeof chartBounds>,
  high: number,
  low: number,
  candles: Candle[],
) {
  drawVwapLine(ctx, bounds, high, low, candles);
  visualConditionLevels(candles).forEach((line) => {
    drawConditionLine(ctx, bounds, high, low, line.value, line.label, line.color, line.dash);
  });
  drawConditionBadges(ctx, bounds);
}

function visualConditionLevels(candles: Candle[]) {
  const day = candles[candles.length - 1]?.timestamp.slice(0, 10);
  if (!day) {
    return [];
  }

  const dayCandles = state.candles.filter((candle) => candle.timestamp.slice(0, 10) === day);
  const regularCandles = dayCandles.filter((candle) => isRegularSession(candle.timestamp));
  const openingCandles = (regularCandles.length ? regularCandles : dayCandles).slice(0, 15);
  const premarketCandles = dayCandles.filter((candle) => isPremarketSession(candle.timestamp));
  const previousClose = previousSessionClose(day);
  const levels: Array<{ label: string; value: number; color: string; dash?: number[] }> = [];

  if (previousClose !== null) {
    levels.push({ label: "Prev close", value: previousClose, color: "#64748b", dash: [5, 4] });
  }

  if (openingCandles.length >= 2) {
    levels.push({
      label: "OR high",
      value: Math.max(...openingCandles.map((candle) => candle.high)),
      color: "#2563eb",
    });
    levels.push({
      label: "OR low",
      value: Math.min(...openingCandles.map((candle) => candle.low)),
      color: "#2563eb",
    });
  }

  if (premarketCandles.length) {
    levels.push({
      label: "PM high",
      value: Math.max(...premarketCandles.map((candle) => candle.high)),
      color: "#9333ea",
      dash: [3, 3],
    });
    levels.push({
      label: "PM low",
      value: Math.min(...premarketCandles.map((candle) => candle.low)),
      color: "#9333ea",
      dash: [3, 3],
    });
  }

  return levels;
}

function drawVwapLine(
  ctx: CanvasRenderingContext2D,
  bounds: ReturnType<typeof chartBounds>,
  high: number,
  low: number,
  candles: Candle[],
) {
  const vwapByTimestamp = sessionVwapByTimestamp(state.candles);
  const candleSlot = (bounds.right - bounds.left) / candles.length;
  let started = false;

  ctx.save();
  ctx.strokeStyle = "#7c3aed";
  ctx.lineWidth = 1.4;
  ctx.beginPath();

  candles.forEach((candle, index) => {
    const value = candle.vwap ?? vwapByTimestamp.get(candle.timestamp);
    if (!value) {
      return;
    }
    const x = bounds.left + candleSlot * index + candleSlot / 2;
    const y = priceToY(value, high, low, bounds);
    if (!started) {
      ctx.moveTo(x, y);
      started = true;
      return;
    }
    ctx.lineTo(x, y);
  });

  if (started) {
    ctx.stroke();
    const latest = [...candles].reverse().find((candle) => candle.vwap ?? vwapByTimestamp.get(candle.timestamp));
    const latestValue = latest ? latest.vwap ?? vwapByTimestamp.get(latest.timestamp) : null;
    if (latestValue) {
      drawConditionLabel(ctx, bounds.right - 52, priceToY(latestValue, high, low, bounds), "VWAP", "#7c3aed");
    }
  }
  ctx.restore();
}

function drawConditionLine(
  ctx: CanvasRenderingContext2D,
  bounds: ReturnType<typeof chartBounds>,
  high: number,
  low: number,
  value: number,
  label: string,
  color: string,
  dash: number[] = [],
) {
  const y = priceToY(value, high, low, bounds);
  if (y < bounds.top || y > bounds.bottom) {
    return;
  }

  ctx.save();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.setLineDash(dash);
  ctx.beginPath();
  ctx.moveTo(bounds.left, y);
  ctx.lineTo(bounds.right, y);
  ctx.stroke();
  ctx.setLineDash([]);
  drawConditionLabel(ctx, bounds.left + 8, y, label, color);
  ctx.restore();
}

function drawConditionLabel(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  label: string,
  color: string,
) {
  ctx.save();
  ctx.font = "700 10px Inter, Arial, sans-serif";
  ctx.textBaseline = "middle";
  const width = ctx.measureText(label).width + 12;
  const left = Math.max(4, x);
  ctx.fillStyle = "rgba(255, 255, 255, 0.86)";
  ctx.fillRect(left, y - 10, width, 20);
  ctx.strokeStyle = color;
  ctx.strokeRect(left, y - 10, width, 20);
  ctx.fillStyle = color;
  ctx.fillText(label, left + 6, y);
  ctx.restore();
}

function drawConditionBadges(ctx: CanvasRenderingContext2D, bounds: ReturnType<typeof chartBounds>) {
  const context = state.marketContext;
  if (!context) {
    return;
  }
  const badges = [
    { label: context.regime.label, color: "#2563eb" },
    { label: context.session.label, color: "#059669" },
  ];
  let x = bounds.left + 8;
  const y = bounds.top + 8;

  ctx.save();
  ctx.font = "700 11px Inter, Arial, sans-serif";
  ctx.textBaseline = "top";
  badges.forEach((badge) => {
    const text = badge.label;
    const width = Math.min(170, ctx.measureText(text).width + 16);
    ctx.fillStyle = "rgba(255, 255, 255, 0.88)";
    ctx.fillRect(x, y, width, 22);
    ctx.strokeStyle = badge.color;
    ctx.strokeRect(x, y, width, 22);
    ctx.fillStyle = badge.color;
    ctx.fillText(text, x + 8, y + 6, width - 14);
    x += width + 8;
  });
  ctx.restore();
}

function sessionVwapByTimestamp(candles: Candle[]) {
  const result = new Map<string, number>();
  let currentDay = "";
  let cumulativePriceVolume = 0;
  let cumulativeVolume = 0;

  candles.forEach((candle) => {
    const day = candle.timestamp.slice(0, 10);
    if (day !== currentDay) {
      currentDay = day;
      cumulativePriceVolume = 0;
      cumulativeVolume = 0;
    }
    const volume = Math.max(0, candle.volume);
    const typical = (candle.high + candle.low + candle.close) / 3;
    cumulativePriceVolume += typical * volume;
    cumulativeVolume += volume;
    if (cumulativeVolume > 0) {
      result.set(candle.timestamp, cumulativePriceVolume / cumulativeVolume);
    }
  });

  return result;
}

function previousSessionClose(day: string) {
  const previous = fullDayGroups()
    .filter((group) => group.day < day)
    .at(-1);
  if (!previous) {
    return null;
  }
  return state.candles[previous.endIndex]?.close ?? null;
}

function isRegularSession(timestamp: string) {
  const minutes = easternMinutes(timestamp);
  return minutes >= 9 * 60 + 30 && minutes < 16 * 60;
}

function isPremarketSession(timestamp: string) {
  const minutes = easternMinutes(timestamp);
  return minutes >= 4 * 60 && minutes < 9 * 60 + 30;
}

function easternDateString(timestamp: string) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "America/New_York",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date(timestamp));
}

function easternMinutes(timestamp: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(new Date(timestamp));
  const hour = Number(parts.find((part) => part.type === "hour")?.value ?? 0);
  const minute = Number(parts.find((part) => part.type === "minute")?.value ?? 0);
  return hour * 60 + minute;
}

function drawGrid(
  ctx: CanvasRenderingContext2D,
  bounds: ReturnType<typeof chartBounds>,
  high: number,
  low: number,
  candles: Candle[],
) {
  ctx.strokeStyle = "#edf0f3";
  ctx.lineWidth = 1;
  ctx.font = "12px Inter, Arial, sans-serif";
  ctx.fillStyle = "#1f2933";

  for (let i = 0; i <= 6; i += 1) {
    const y = bounds.top + ((bounds.bottom - bounds.top) / 6) * i;
    ctx.beginPath();
    ctx.moveTo(bounds.left, y);
    ctx.lineTo(bounds.right + 78, y);
    ctx.stroke();
  }

  for (let i = 0; i <= 10; i += 1) {
    const x = bounds.left + ((bounds.right - bounds.left) / 10) * i;
    ctx.beginPath();
    ctx.moveTo(x, bounds.top);
    ctx.lineTo(x, bounds.bottom + 24);
    ctx.stroke();
  }

  if (state.showPriceLine) {
    const latest = candles[candles.length - 1];
    const y = priceToY(latest.close, high, low, bounds);
    ctx.setLineDash([2, 2]);
    ctx.strokeStyle = "#0f9f8b";
    ctx.beginPath();
    ctx.moveTo(bounds.left, y);
    ctx.lineTo(bounds.right, y);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

function drawCandles(
  ctx: CanvasRenderingContext2D,
  bounds: ReturnType<typeof chartBounds>,
  high: number,
  low: number,
  maxVolume: number,
  candles: Candle[],
) {
  const candleSlot = (bounds.right - bounds.left) / candles.length;
  const bodyWidth = Math.max(1, Math.min(14, candleSlot * (state.candleWidthPercent / 100)));

  candles.forEach((candle, index) => {
    const x = bounds.left + candleSlot * index + candleSlot / 2;
    const up = candle.close >= candle.open;
    const color = up ? "#089981" : "#f23645";
    const openY = priceToY(candle.open, high, low, bounds);
    const closeY = priceToY(candle.close, high, low, bounds);
    const highY = priceToY(candle.high, high, low, bounds);
    const lowY = priceToY(candle.low, high, low, bounds);
    const volumeHeight = (candle.volume / maxVolume) * 84;
    const isFlatPrint = Math.abs(candle.high - candle.low) < 0.0001 && Math.abs(candle.open - candle.close) < 0.0001;

    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.globalAlpha = 1;
    if (isFlatPrint) {
      const markerSize = Math.max(5, Math.min(9, bodyWidth + 3));
      ctx.save();
      ctx.lineWidth = 1.5;
      ctx.fillStyle = color;
      ctx.strokeStyle = "#ffffff";
      ctx.beginPath();
      ctx.arc(x, closeY, markerSize / 2, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
      ctx.restore();
    } else if (state.showWicks) {
      ctx.beginPath();
      ctx.moveTo(x, highY);
      ctx.lineTo(x, lowY);
      ctx.stroke();
    }
    if (!isFlatPrint) {
      ctx.fillRect(
        x - bodyWidth / 2,
        Math.min(openY, closeY),
        bodyWidth,
        Math.max(1, Math.abs(closeY - openY)),
      );
    }

    if (state.showVolume) {
      ctx.globalAlpha = 0.35;
      ctx.fillRect(x - bodyWidth / 2, bounds.bottom - volumeHeight, bodyWidth, volumeHeight);
      ctx.globalAlpha = 1;
    }
  });
}

function drawAxes(
  ctx: CanvasRenderingContext2D,
  bounds: ReturnType<typeof chartBounds>,
  high: number,
  low: number,
  candles: Candle[],
) {
  ctx.font = "12px Inter, Arial, sans-serif";
  ctx.fillStyle = "#343941";
  ctx.textBaseline = "middle";

  for (let i = 0; i <= 5; i += 1) {
    const value = high - ((high - low) / 5) * i;
    const y = bounds.top + ((bounds.bottom - bounds.top) / 5) * i;
    ctx.fillText(price(value), bounds.right + 8, y);
  }

  ctx.textBaseline = "top";
  const tickCount = 7;
  for (let i = 0; i < tickCount; i += 1) {
    const index = Math.floor((candles.length - 1) * (i / (tickCount - 1)));
    const candle = candles[index];
    const x = bounds.left + ((bounds.right - bounds.left) / (tickCount - 1)) * i;
    ctx.fillText(formatTime(candle.timestamp), x + 4, bounds.bottom + 10);
  }

  if (state.showPriceLine) {
    const latest = candles[candles.length - 1];
    const y = priceToY(latest.close, high, low, bounds);
    ctx.fillStyle = "#089981";
    ctx.fillRect(bounds.right + 4, y - 10, 66, 20);
    ctx.fillStyle = "#ffffff";
    ctx.font = "700 12px Inter, Arial, sans-serif";
    ctx.fillText(price(latest.close), bounds.right + 10, y);
  }
}

function drawHover(
  ctx: CanvasRenderingContext2D,
  bounds: ReturnType<typeof chartBounds>,
  high: number,
  low: number,
  candles: Candle[],
) {
  if (
    state.hoveredIndex < 0 ||
    state.hoveredIndex >= candles.length ||
    state.hoverX < bounds.left ||
    state.hoverX > bounds.right ||
    state.hoverY < bounds.top ||
    state.hoverY > bounds.bottom
  ) {
    return;
  }
  const candle = candles[state.hoveredIndex];
  const x = state.hoverX;
  const y = state.hoverY;

  ctx.strokeStyle = "#111827";
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  ctx.beginPath();
  ctx.moveTo(x, bounds.top);
  ctx.lineTo(x, bounds.bottom);
  ctx.moveTo(bounds.left, y);
  ctx.lineTo(bounds.right, y);
  ctx.stroke();
  ctx.setLineDash([]);

  const tooltip = `${formatDate(candle.timestamp)}  C ${price(candle.close)}  Vol ${compact(candle.volume)}`;
  ctx.font = "12px Inter, Arial, sans-serif";
  const width = ctx.measureText(tooltip).width + 18;
  const tx = Math.min(Math.max(x - width / 2, 8), bounds.right - width - 8);
  ctx.fillStyle = "rgba(17, 24, 39, 0.88)";
  ctx.fillRect(tx, bounds.top + 8, width, 28);
  ctx.fillStyle = "#ffffff";
  ctx.fillText(tooltip, tx + 9, bounds.top + 22);
}

function isOverCandle(
  x: number,
  y: number,
  bounds: ReturnType<typeof chartBounds>,
  candles: Candle[],
) {
  if (!candles.length) {
    return false;
  }

  const prices = candles.flatMap((candle) => [candle.high, candle.low]);
  const maxPrice = Math.max(...prices);
  const minPrice = Math.min(...prices);
  const padding = Math.max((maxPrice - minPrice) * 0.08, 0.05);
  const high = maxPrice + padding;
  const low = minPrice - padding;
  const candleSlot = (bounds.right - bounds.left) / candles.length;
  const index = Math.max(0, Math.min(candles.length - 1, Math.floor((x - bounds.left) / candleSlot)));
  const candle = candles[index];
  const centerX = bounds.left + candleSlot * index + candleSlot / 2;
  const bodyWidth = Math.max(1, Math.min(14, candleSlot * (state.candleWidthPercent / 100)));
  const openY = priceToY(candle.open, high, low, bounds);
  const closeY = priceToY(candle.close, high, low, bounds);
  const highY = priceToY(candle.high, high, low, bounds);
  const lowY = priceToY(candle.low, high, low, bounds);
  const bodyLeft = centerX - bodyWidth / 2;
  const bodyRight = centerX + bodyWidth / 2;
  const bodyTop = Math.min(openY, closeY);
  const bodyBottom = Math.max(openY, closeY);
  const nearWick = Math.abs(x - centerX) <= Math.max(3, bodyWidth / 2);
  const inWickRange = y >= highY && y <= lowY;
  const inBody = x >= bodyLeft && x <= bodyRight && y >= bodyTop && y <= bodyBottom;
  return inBody || (nearWick && inWickRange);
}

function priceToY(
  value: number,
  high: number,
  low: number,
  bounds: ReturnType<typeof chartBounds>,
) {
  return bounds.top + ((high - value) / (high - low)) * (bounds.bottom - bounds.top);
}

function price(value: number) {
  return value.toFixed(2);
}

function signed(value: number) {
  const sign = value >= 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}`;
}

function money(value: number) {
  return Intl.NumberFormat("en", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);
}

function moneyWithCents(value: number) {
  return Intl.NumberFormat("en", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value);
}

function loadTradeHistoryRows(storageKey: string): TradeHistoryRow[] {
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) {
      return [];
    }
    const parsed = JSON.parse(raw) as TradeHistoryRow[];
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed.filter((row) => row && (row.side === "Buy" || row.side === "Sell")).slice(0, 50);
  } catch {
    return [];
  }
}

function loadTradeHistory(): TradeHistoryRow[] {
  return loadTradeHistoryRows(TRADE_HISTORY_STORAGE_KEY);
}

function loadWeightedTradeHistory(): TradeHistoryRow[] {
  return loadTradeHistoryRows(WEIGHTED_TRADE_HISTORY_STORAGE_KEY);
}

function loadConfidenceTradeHistory(): TradeHistoryRow[] {
  return loadTradeHistoryRows(CONFIDENCE_TRADE_HISTORY_STORAGE_KEY);
}

function loadRegimeTradeHistory(): TradeHistoryRow[] {
  return loadTradeHistoryRows(REGIME_TRADE_HISTORY_STORAGE_KEY);
}

function loadMetaTradeHistory(): TradeHistoryRow[] {
  return loadTradeHistoryRows(META_TRADE_HISTORY_STORAGE_KEY);
}

function saveTradeHistory() {
  window.localStorage.setItem(TRADE_HISTORY_STORAGE_KEY, JSON.stringify(state.tradeHistory.slice(0, 50)));
}

function saveWeightedTradeHistory() {
  window.localStorage.setItem(WEIGHTED_TRADE_HISTORY_STORAGE_KEY, JSON.stringify(state.weightedTradeHistory.slice(0, 50)));
}

function saveConfidenceTradeHistory() {
  window.localStorage.setItem(CONFIDENCE_TRADE_HISTORY_STORAGE_KEY, JSON.stringify(state.confidenceTradeHistory.slice(0, 50)));
}

function saveRegimeTradeHistory() {
  window.localStorage.setItem(REGIME_TRADE_HISTORY_STORAGE_KEY, JSON.stringify(state.regimeTradeHistory.slice(0, 50)));
}

function saveMetaTradeHistory() {
  window.localStorage.setItem(META_TRADE_HISTORY_STORAGE_KEY, JSON.stringify(state.metaTradeHistory.slice(0, 50)));
}

function loadOrderControlModesFromStorage(storageKey: string): Record<string, SubmitOrderMode> {
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as Record<string, SubmitOrderMode>;
    if (!parsed || typeof parsed !== "object") {
      return {};
    }
    return Object.fromEntries(
      Object.entries(parsed).filter(([, value]) => value === "Manual" || value === "Automatic"),
    );
  } catch {
    return {};
  }
}

function loadOrderControlModes(): Record<string, SubmitOrderMode> {
  return loadOrderControlModesFromStorage(ORDER_CONTROL_MODES_STORAGE_KEY);
}

function loadWeightedOrderControlModes(): Record<string, SubmitOrderMode> {
  return loadOrderControlModesFromStorage(WEIGHTED_ORDER_CONTROL_MODES_STORAGE_KEY);
}

function loadConfidenceOrderControlModes(): Record<string, SubmitOrderMode> {
  return loadOrderControlModesFromStorage(CONFIDENCE_ORDER_CONTROL_MODES_STORAGE_KEY);
}

function loadRegimeOrderControlModes(): Record<string, SubmitOrderMode> {
  return loadOrderControlModesFromStorage(REGIME_ORDER_CONTROL_MODES_STORAGE_KEY);
}

function loadMetaOrderControlModes(): Record<string, SubmitOrderMode> {
  return loadOrderControlModesFromStorage(META_ORDER_CONTROL_MODES_STORAGE_KEY);
}

function saveOrderControlModes() {
  window.localStorage.setItem(ORDER_CONTROL_MODES_STORAGE_KEY, JSON.stringify(state.orderControlModes));
}

function saveWeightedOrderControlModes() {
  window.localStorage.setItem(WEIGHTED_ORDER_CONTROL_MODES_STORAGE_KEY, JSON.stringify(state.weightedOrderControlModes));
}

function saveConfidenceOrderControlModes() {
  window.localStorage.setItem(CONFIDENCE_ORDER_CONTROL_MODES_STORAGE_KEY, JSON.stringify(state.confidenceOrderControlModes));
}

function saveRegimeOrderControlModes() {
  window.localStorage.setItem(REGIME_ORDER_CONTROL_MODES_STORAGE_KEY, JSON.stringify(state.regimeOrderControlModes));
}

function saveMetaOrderControlModes() {
  window.localStorage.setItem(META_ORDER_CONTROL_MODES_STORAGE_KEY, JSON.stringify(state.metaOrderControlModes));
}

function loadOrderControlOverridesFromStorage(storageKey: string): Record<string, LotOrderOverride> {
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as Record<string, LotOrderOverride>;
    if (!parsed || typeof parsed !== "object") {
      return {};
    }
    return Object.fromEntries(
      Object.entries(parsed).map(([lotId, value]) => [lotId, sanitizeLotOrderOverride(value)]),
    );
  } catch {
    return {};
  }
}

function loadOrderControlOverrides(): Record<string, LotOrderOverride> {
  return loadOrderControlOverridesFromStorage(ORDER_CONTROL_OVERRIDES_STORAGE_KEY);
}

function loadWeightedOrderControlOverrides(): Record<string, LotOrderOverride> {
  return loadOrderControlOverridesFromStorage(WEIGHTED_ORDER_CONTROL_OVERRIDES_STORAGE_KEY);
}

function loadConfidenceOrderControlOverrides(): Record<string, LotOrderOverride> {
  return loadOrderControlOverridesFromStorage(CONFIDENCE_ORDER_CONTROL_OVERRIDES_STORAGE_KEY);
}

function loadRegimeOrderControlOverrides(): Record<string, LotOrderOverride> {
  return loadOrderControlOverridesFromStorage(REGIME_ORDER_CONTROL_OVERRIDES_STORAGE_KEY);
}

function loadMetaOrderControlOverrides(): Record<string, LotOrderOverride> {
  return loadOrderControlOverridesFromStorage(META_ORDER_CONTROL_OVERRIDES_STORAGE_KEY);
}

function sanitizeLotOrderOverride(value: unknown): LotOrderOverride {
  if (!value || typeof value !== "object") {
    return {};
  }
  const source = value as Record<string, unknown>;
  const next: LotOrderOverride = {};
  for (const key of ["quantity", "triggerPrice", "limitPrice", "stopPrice", "targetPrice", "riskDollars", "plannedStopRiskDollars", "estimatedSlippage"] as const) {
    const numeric = Number(source[key]);
    if (Number.isFinite(numeric)) {
      next[key] = numeric;
    }
  }
  return next;
}

function saveOrderControlOverrides() {
  window.localStorage.setItem(ORDER_CONTROL_OVERRIDES_STORAGE_KEY, JSON.stringify(state.orderControlOverrides));
}

function saveWeightedOrderControlOverrides() {
  window.localStorage.setItem(WEIGHTED_ORDER_CONTROL_OVERRIDES_STORAGE_KEY, JSON.stringify(state.weightedOrderControlOverrides));
}

function saveConfidenceOrderControlOverrides() {
  window.localStorage.setItem(CONFIDENCE_ORDER_CONTROL_OVERRIDES_STORAGE_KEY, JSON.stringify(state.confidenceOrderControlOverrides));
}

function saveRegimeOrderControlOverrides() {
  window.localStorage.setItem(REGIME_ORDER_CONTROL_OVERRIDES_STORAGE_KEY, JSON.stringify(state.regimeOrderControlOverrides));
}

function saveMetaOrderControlOverrides() {
  window.localStorage.setItem(META_ORDER_CONTROL_OVERRIDES_STORAGE_KEY, JSON.stringify(state.metaOrderControlOverrides));
}

function formatTradeHistoryTime(value: string) {
  return new Intl.DateTimeFormat("en", {
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function compact(value: number) {
  return Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 2 }).format(value);
}

function upcomingFallbackMacroEvents() {
  const now = Date.now();
  return fallbackMacroEvents
    .map((event) => ({
      ...event,
      daysUntil: Math.max(0, Math.ceil((new Date(event.releaseAt).getTime() - now) / (24 * 60 * 60 * 1000))),
    }))
    .filter((event) => new Date(event.releaseAt).getTime() >= now)
    .slice(0, 6);
}

function upcomingFallbackFedEvents() {
  const now = Date.now();
  return fallbackFedEvents
    .map((event) => ({
      ...event,
      daysUntil: Math.max(0, Math.ceil((new Date(event.releaseAt).getTime() - now) / (24 * 60 * 60 * 1000))),
    }))
    .filter((event) => new Date(event.releaseAt).getTime() >= now)
    .slice(0, 6);
}

function macroCategoryLabel(value: MacroEvent["category"]) {
  return value === "cpi" ? "CPI" : "Jobs";
}

function fedCategoryLabel(value: FedEvent["category"]) {
  return value === "fomc" ? "FOMC" : "Speech";
}

function tradingAlertLabel(value: TradingAlert["category"]) {
  return value === "luld" ? "LULD" : "Halt";
}

function mocSideLabel(value: MocImbalanceUpdate["side"]) {
  const labels: Record<MocImbalanceUpdate["side"], string> = {
    buy: "Buy",
    sell: "Sell",
    none: "Flat",
  };
  return labels[value];
}

function mocSideClass(value: MocImbalanceUpdate["side"]) {
  const classes: Record<MocImbalanceUpdate["side"], string> = {
    buy: "buy",
    sell: "sell",
    none: "clear",
  };
  return classes[value];
}

function vixLevelForValue(value: number, levels: VixRiskLevel[]) {
  return levels.find((level) => value >= level.min && (level.max === null || value < level.max)) ?? levels[levels.length - 1];
}

function vixSeverityClass(value: VixRiskLevel["severity"]) {
  const classes: Record<VixRiskLevel["severity"], string> = {
    low: "clear",
    normal: "buy",
    elevated: "level1",
    high: "level2",
    extreme: "level3",
  };
  return classes[value];
}

function esLevelForValue(value: number, levels: EsDirectionLevel[]) {
  return (
    levels.find((level) => (level.minPercent === null || value >= level.minPercent) && (level.maxPercent === null || value < level.maxPercent)) ??
    levels[2]
  );
}

function esSeverityClass(value: EsDirectionLevel["severity"]) {
  const classes: Record<EsDirectionLevel["severity"], string> = {
    strong_up: "buy",
    up: "clear",
    flat: "level1",
    down: "level2",
    strong_down: "level3",
  };
  return classes[value];
}

function cleanAlertDetail(value: string) {
  const parsed = new DOMParser().parseFromString(value, "text/html");
  const cells = Array.from(parsed.querySelectorAll("td")).map((cell) =>
    cell.textContent?.replace(/\s+/g, " ").trim() ?? "",
  );
  if (cells.length >= 10) {
    const [haltDate, haltTime, symbol, issueName, market, reasonCode, pauseThreshold, resumeDate, quoteTime, tradeTime] =
      cells;
    return [
      issueName,
      market,
      reasonCode ? `Code ${reasonCode}` : "",
      haltDate || haltTime ? `Halt ${haltDate} ${haltTime} ET` : "",
      pauseThreshold ? `Threshold ${pauseThreshold}` : "",
      tradeTime || quoteTime ? `Resumes ${resumeDate} ${tradeTime || quoteTime} ET` : "",
      symbol ? `Symbol ${symbol}` : "",
    ]
      .filter(Boolean)
      .join(" - ");
  }
  return parsed.body.textContent?.replace(/\s+/g, " ").trim() || value;
}

function formatCompactTime(value: string | null) {
  if (!value) {
    return "--";
  }
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "2-digit",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatMacroDate(value: string) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "2-digit",
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/New_York",
    timeZoneName: "short",
  }).format(new Date(value));
}

function formatMacroDay(value: string) {
  return new Intl.DateTimeFormat("en", {
    day: "2-digit",
    timeZone: "America/New_York",
  }).format(new Date(value));
}

function formatMacroMonth(value: string) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    timeZone: "America/New_York",
  }).format(new Date(value));
}

function formatMacroTime(value: string) {
  return new Intl.DateTimeFormat("en", {
    hour: "numeric",
    minute: "2-digit",
    timeZone: "America/New_York",
    timeZoneName: "short",
  }).format(new Date(value));
}

function daysUntilLabel(value: number) {
  if (value <= 0) {
    return "Today";
  }
  if (value === 1) {
    return "1 day";
  }
  return `${value} days`;
}

function statColumns(rows: Array<[string, string]>) {
  const splitAt = Math.ceil(rows.length / 2);
  return [rows.slice(0, splitAt), rows.slice(splitAt)]
    .map(
      (column) => `
        <div class="quote-grid-column">
          ${column
            .map(
              ([label, value]) => `
                <div class="quote-row">
                  <span>${label}</span>
                  <strong>${value}</strong>
                </div>
              `,
            )
            .join("")}
        </div>
      `,
    )
    .join("");
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("en", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function formatTimeWithSeconds(value: string) {
  return new Intl.DateTimeFormat("en", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function toAlpacaTime(value: string) {
  if (!value) {
    return "";
  }
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "" : date.toISOString();
}

function historyLookbackMs(timeframe: Timeframe) {
  const day = 24 * 60 * 60 * 1000;
  const spans: Record<Timeframe, number> = {
    "1Min": 10 * day,
    "3Min": 20 * day,
    "5Min": 30 * day,
    "15Min": 90 * day,
    "1Hour": 240 * day,
    "1Day": 900 * day,
  };
  return spans[timeframe];
}

function scheduleAutoRefresh() {
  if (refreshTimer) {
    window.clearTimeout(refreshTimer);
    refreshTimer = undefined;
  }
  nextChartRefreshAt = 0;
  if (!state.refreshSeconds) {
    markRefresh("off");
    return;
  }
  if (state.refreshSeconds === BAR_CLOSE_REFRESH_MODE) {
    const delay = millisecondsUntilNextOneMinuteBarRefresh();
    nextChartRefreshAt = Date.now() + delay;
    markRefresh(state.viewportOffset === 0 ? "next-bar" : "paused");
    refreshTimer = window.setTimeout(() => {
      refreshTimer = undefined;
      void refreshWhenMarketIsOpen();
    }, delay);
    return;
  }
  markRefresh(state.viewportOffset === 0 ? "waiting" : "paused");
  refreshTimer = window.setTimeout(() => {
    refreshTimer = undefined;
    void refreshWhenMarketIsOpen();
  }, state.refreshSeconds * 1000);
}

function sleepAppUntilMarketWake(status = "sleeping-market-closed") {
  if (refreshTimer) {
    window.clearTimeout(refreshTimer);
    refreshTimer = undefined;
  }
  nextChartRefreshAt = 0;
  markRefresh(status);
}

async function activateAppAfterWake(reason: string) {
  if (appActivationInFlight) {
    return;
  }
  appActivationInFlight = true;
  let shouldResumeRefresh = false;
  try {
    markRefresh(reason);
    backtestRangeCache = null;
    await loadMarketStatus();
    if (!marketAllowsTradingRefresh()) {
      sleepAppUntilMarketWake(state.marketStatus === "unknown" ? "market-unknown" : "sleeping-market-closed");
      if (reason === "wake") {
        void requestSystemSleepIfMarketClosed("wake_market_closed");
      }
      return;
    }
    shouldResumeRefresh = true;
    await startMarketForecastPredictionLedger(reason);
    await loadLatestDynamicTradingArtifact();
    await loadCandles({ showLoading: false, refresh: true });
    void loadAlgoBacktestCandles();
    void saveBrowserStorageSnapshot(`wake-${reason}`);
  } catch {
    markRefresh("wake-error");
  } finally {
    appActivationInFlight = false;
    if (shouldResumeRefresh) {
      scheduleAutoRefresh();
    }
  }
}

function startWakeActivationMonitor() {
  if (wakeCheckTimer !== null) {
    window.clearInterval(wakeCheckTimer);
  }
  lastWakeCheckAt = Date.now();
  wakeCheckTimer = window.setInterval(() => {
    const now = Date.now();
    const gap = now - lastWakeCheckAt;
    lastWakeCheckAt = now;
    if (gap > WAKE_GAP_THRESHOLD_MS) {
      void activateAppAfterWake("wake");
    }
  }, WAKE_CHECK_INTERVAL_MS);
  window.addEventListener("focus", () => {
    void activateAppAfterWake("focus");
  });
  window.addEventListener("online", () => {
    void activateAppAfterWake("online");
  });
  window.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") {
      void activateAppAfterWake("visible");
    }
  });
}

function millisecondsUntilNextOneMinuteBarRefresh() {
  const now = Date.now();
  const nextMinute = Math.floor(now / 60_000) * 60_000 + 60_000;
  return Math.max(1_000, nextMinute + BAR_CLOSE_REFRESH_DELAY_MS - now);
}

function marketAllowsTradingRefresh() {
  return state.marketStatus === "open";
}

function tradingRefreshCountdownText() {
  if (tradingRagRefreshInFlight || state.tradingRagStatus === "loading") {
    return "refreshing";
  }
  return marketAllowsTradingRefresh() ? "new candle" : "market closed";
}

function updateTradingRefreshCountdown() {
  const element = document.querySelector<HTMLElement>("#tradingRefreshCountdown");
  if (element) {
    element.textContent = tradingRefreshCountdownText();
  }
}

async function refreshWhenMarketIsOpen() {
  try {
    if (state.viewportOffset !== 0 || state.loadingOlder) {
      markRefresh("paused");
      return;
    }

    markRefresh("checking-bar");
    const status = await loadMarketStatus();
    if (status.isOpen && status.status === "open") {
      await loadCandles({ showLoading: false, refresh: true });
      return;
    }

    if (status.status === "holiday" || status.status === "closed") {
      sleepAppUntilMarketWake("sleeping-market-closed");
    } else {
      markRefresh("market-unknown");
    }
  } finally {
    if (marketAllowsTradingRefresh()) {
      scheduleAutoRefresh();
    }
  }
}

function markRefresh(status: string) {
  state.lastRefreshAt = formatTimeWithSeconds(new Date().toISOString());
  state.lastRefreshStatus = status;
  if (status === "off") {
    refreshStatus.textContent = "refresh off";
  } else if (status === "paused") {
    refreshStatus.textContent = "refresh paused";
  } else if (status === "holiday") {
    refreshStatus.textContent = `market holiday ${state.lastRefreshAt}`;
  } else if (status === "closed") {
    refreshStatus.textContent = `market closed ${state.lastRefreshAt}`;
  } else if (status === "market-unknown") {
    refreshStatus.textContent = `market status unknown ${state.lastRefreshAt}`;
  } else if (status === "next-bar") {
    refreshStatus.textContent = nextChartRefreshAt ? `next 1m bar ${formatTimeWithSeconds(new Date(nextChartRefreshAt).toISOString())}` : "next 1m bar";
  } else if (status === "checking-bar") {
    refreshStatus.textContent = `checking 1m bar ${state.lastRefreshAt}`;
  } else if (status === "failed") {
    refreshStatus.textContent = `refresh failed ${state.lastRefreshAt}`;
  } else if (status === "waiting") {
    refreshStatus.textContent = `waiting ${state.lastRefreshAt}`;
  } else {
    refreshStatus.textContent = `${status} ${state.lastRefreshAt}`;
  }
  refreshStatus.dataset.status = status;
  refreshStatus.title = status === "failed" && state.error ? state.error : "";
}

function updateMarketStatus(payload: MarketStatus) {
  const status = payload.status || "unknown";
  const detail =
    status === "open" && payload.nextClose
      ? `next close ${formatDate(payload.nextClose)}`
      : status !== "open" && payload.nextOpen
        ? `next open ${formatDate(payload.nextOpen)}`
        : payload.warning
          ? "status unavailable"
          : "";
  marketStatusBadge.textContent = detail ? `${status} - ${detail}` : status;
  marketStatusBadge.dataset.status = status;
  updateQuoteCard(currentCandle());
}

function updateMarketContext() {
  if (state.contextStatus === "loading" && !state.marketContext) {
    [regimeLayer, sessionLayer, eventLayer].forEach((element) => {
      element.classList.add("loading");
    });
    strategySummary.textContent = "Loading market context";
    contextUpdatedAt.textContent = "--";
    strategyList.innerHTML = skeletonStrategies();
    renderDecisionLoading();
    return;
  }

  if (state.contextStatus === "error" && !state.marketContext) {
    [regimeLayer, sessionLayer, eventLayer].forEach((element) => {
      element.classList.remove("loading");
      element.classList.add("error");
      element.querySelector(".layer-heading strong")!.textContent = "Context unavailable";
      element.querySelector(".layer-metrics")!.innerHTML = `<span class="metric neutral">retry needed</span>`;
      element.querySelector(".layer-signals")!.innerHTML = "";
      element.querySelector(".layer-reasons")!.innerHTML = `<span class="reason-chip">${escapeHtml(state.contextError)}</span>`;
    });
    strategySummary.textContent = "No strategy context available";
    contextUpdatedAt.textContent = "--";
    strategyList.innerHTML = "";
    renderDecisionUnavailable(state.contextError);
    return;
  }

  const context = state.marketContext;
  if (!context) {
    return;
  }

  renderLayer(regimeLayer, context.regime);
  renderLayer(sessionLayer, context.session);
  renderLayer(eventLayer, context.event);
  renderStrategies(context);
  renderDecision(context);
  updateAlgorithmPanel(visibleCandles());
}

function renderLayer(element: HTMLElement, layer: MarketLayer) {
  element.classList.remove("loading", "error");
  element.dataset.bias = layer.directionBias;
  element.dataset.volatility = layer.volatility;
  element.querySelector(".layer-heading strong")!.textContent = layer.label;
  element.querySelector(".layer-metrics")!.innerHTML = `
    <span class="metric ${layer.directionBias}">${biasLabel(layer.directionBias)}</span>
    <span class="metric ${layer.volatility}">${volatilityLabel(layer.volatility)}</span>
    <span class="metric confidence">${confidence(layer.confidence)}</span>
    <span class="metric candle-window ${layer.layer}">${candleWindowLabel(layer)}</span>
  `;
  element.querySelector(".layer-reasons")!.innerHTML = layer.reasons.length
    ? layer.reasons.map((reason) => `<span class="reason-chip">${escapeHtml(reason)}</span>`).join("")
    : `<span class="reason-chip">No active reason</span>`;
  element.querySelector(".layer-signals")!.innerHTML = layer.signals?.length
    ? layer.signals.map(renderSignalChip).join("")
    : `<span class="signal-chip" data-status="na"><span>Signals</span><strong>NA</strong></span>`;
}

function renderSignalChip(signal: MarketLayer["signals"][number]) {
  return `
    <span class="signal-chip" data-status="${signal.status === "na" ? "na" : "ok"}">
      <span>${escapeHtml(signal.name)}</span>
      <strong>${escapeHtml(signal.value)}</strong>
    </span>
  `;
}

function candleWindowLabel(layer: MarketLayer) {
  const count = layer.candleWindow?.count ?? 0;
  const timeframe = layer.candleWindow?.timeframe ?? "1Min";
  const unit = timeframe === "1Day" ? "daily candles" : `${timeframe.replace("Min", "m")} candles`;
  return `${count} ${unit}`;
}

function renderStrategies(context: MarketContext) {
  const visibleStrategies = strategyFitDisplayRows(context.strategies);
  const strong = visibleStrategies.filter((strategy) => strategy.status === "Strong Fit").length;
  strategySummary.textContent = strong
    ? `${strong} strong fit${strong === 1 ? "" : "s"} · ${visibleStrategies.length} strategies`
    : `${visibleStrategies.length} strategies ranked`;
  contextUpdatedAt.textContent = context.updatedAt ? `updated ${formatDate(context.updatedAt)}` : "updated --";
  strategyList.innerHTML = visibleStrategies
    .map(
      (strategy) => `
        <article class="strategy-card" data-status="${strategy.status.toLowerCase().replaceAll(" ", "-")}">
          <div class="strategy-main">
            <strong>${escapeHtml(strategy.name)}</strong>
            <span>${strategy.status} · ${strategy.score}%</span>
          </div>
          <div class="strategy-detail">
            <span>${strategy.matches.length ? escapeHtml(strategy.matches.join(" · ")) : "No strong confirming condition"}</span>
            ${
              strategy.risks.length
                ? `<span class="strategy-risk">${escapeHtml(strategy.risks.join(" · "))}</span>`
                : `<span class="strategy-clear">No major blocker</span>`
            }
          </div>
        </article>
      `,
    )
    .join("");
}

function strategyFitDisplayRows(sourceStrategies: StrategyFit[]) {
  const byName = new Map(sourceStrategies.map((strategy) => [strategy.name, strategy]));
  return [
    ...sourceStrategies,
    ...allAlgorithmStrategyFitNames()
      .filter((name) => !byName.has(name))
      .map(
        (name): StrategyFit => ({
          name,
          role: metaStrategyCatalog.find((strategy) => strategy.name === name)?.role,
          family: metaStrategyCatalog.find((strategy) => strategy.name === name)?.family,
          strategy_family: metaStrategyCatalog.find((strategy) => strategy.name === name)?.family,
          status: "Watch",
          score: 45,
          matches: ["Used by algorithm", "Waiting for backend fit score"],
          risks: [],
        }),
      ),
  ];
}

function allAlgorithmStrategyFitNames() {
  return Array.from(
    new Set([
      ...strategyVoteCatalog,
      ...weightedAlphaStrategies.map((strategy) => strategy.name),
      ...confidenceAggregationStrategies.map((strategy) => strategy.name),
      ...regimeSelectionStrategies.map((strategy) => strategy.name),
      ...metaStrategyCatalog.map((strategy) => strategy.name),
      "ADX Trend Strength Filter",
      "ATR Volatility Regime",
      "Breakout Strategy",
      "MACD Strategy",
    ]),
  );
}

function skeletonStrategies() {
  return [1, 2, 3]
    .map(
      () => `
        <article class="strategy-card loading">
          <div class="strategy-main">
            <strong>Loading strategy</strong>
            <span>--</span>
          </div>
          <div class="strategy-detail">
            <span>Waiting for context computation</span>
          </div>
        </article>
      `,
    )
    .join("");
}

function renderDecision(context: MarketContext) {
  const tags = new Set([
    ...context.regime.strategyTags,
    ...context.session.strategyTags,
    ...context.event.strategyTags,
  ]);
  const riskFlags = [
    tags.has("liquidity-stress") ? "Liquidity stress" : "",
    tags.has("news-risk") ? "News risk" : "",
    tags.has("chop") ? "Choppy tape" : "",
    tags.has("cash-filter") ? "Cash filter" : "",
  ].filter(Boolean);
  const risk = riskFlags.length ? riskFlags.join(", ") : "Normal";

  decisionAction.textContent = "";
  decisionBias.textContent = context.regime.label;
  decisionRisk.textContent = risk;
  decisionChecklist.innerHTML = [
    ["Session", context.session.label],
    ["Event", context.event.label],
    ["Regime Volatility", volatilityLabel(context.regime.volatility)],
    ["Session Volatility", volatilityLabel(context.session.volatility)],
  ]
    .map(
      ([label, value]) => `
        <span class="decision-chip">
          <span>${escapeHtml(label)}</span>
          <strong>${escapeHtml(value)}</strong>
        </span>
      `,
    )
    .join("");
  renderMarketForecastPanel();
}

function renderDecisionLoading() {
  decisionAction.textContent = "Loading forecast";
  decisionBias.textContent = "--";
  decisionRisk.textContent = "--";
  decisionChecklist.innerHTML = "";
  renderMarketForecastPanel();
}

function renderDecisionUnavailable(message: string) {
  decisionAction.textContent = "Forecast context unavailable";
  decisionBias.textContent = "--";
  decisionRisk.textContent = "Retry needed";
  decisionChecklist.innerHTML = "";
  renderMarketForecastPanel();
}

type MarketForecastImpact = "positive" | "negative" | "neutral";

function renderMarketForecastItem(label: string, value: string, impact: MarketForecastImpact = "neutral") {
  return `<span class="market-forecast-item" data-impact="${impact}"><b>${escapeHtml(label)}</b>${escapeHtml(value)}</span>`;
}

function thresholdImpact(value: number | null | undefined, threshold: number | null | undefined, higherIsBetter = true): MarketForecastImpact {
  if (value == null || threshold == null) {
    return "neutral";
  }
  const passes = higherIsBetter ? value >= threshold : value <= threshold;
  return passes ? "positive" : "negative";
}

function signedImpact(value: number | null | undefined): MarketForecastImpact {
  if (value == null || Math.abs(value) < 0.0001) {
    return "neutral";
  }
  return value > 0 ? "positive" : "negative";
}

function renderMarketForecastPanel() {
  if (state.marketForecastStatus === "loading" && !state.marketForecast) {
    marketForecastPanel.dataset.status = "loading";
    marketForecastPanel.innerHTML = `
      <div class="market-forecast-head">
        <span>Future Market Forecast</span>
        <strong>Loading</strong>
      </div>
      <div class="market-forecast-note">Building isolated 5-minute forecast from 1m candles.</div>
    `;
    return;
  }
  if (state.marketForecastStatus === "error") {
    marketForecastPanel.dataset.status = "error";
    marketForecastPanel.innerHTML = `
      <div class="market-forecast-head">
        <span>Future Market Forecast</span>
        <strong>Unavailable</strong>
      </div>
      <div class="market-forecast-note">${escapeHtml(state.marketForecastError || "Forecast endpoint unavailable.")}</div>
    `;
    return;
  }
  const forecast = state.marketForecast;
  if (!forecast) {
    marketForecastPanel.dataset.status = "waiting";
    marketForecastPanel.innerHTML = `
      <div class="market-forecast-head">
        <span>Future Market Forecast</span>
        <strong>Waiting</strong>
      </div>
      <div class="market-forecast-note">Forecast will appear after 1m candles load.</div>
    `;
    return;
  }
  const buyProbabilityValue = forecast.probabilityBuySuccess ?? forecast.probabilitySuccess;
  const sellProbabilityValue = forecast.probabilitySellSuccess ?? forecast.probabilityStop;
  const buyProbability = buyProbabilityValue === null ? "NA" : formatProbability(buyProbabilityValue);
  const sellProbability = sellProbabilityValue === null ? "NA" : formatProbability(sellProbabilityValue);
  const timeoutProbability = forecast.probabilityTimeout === null ? "NA" : formatProbability(forecast.probabilityTimeout);
  const expectedValue = forecast.expectedValue === null ? "NA" : `${forecast.expectedValue >= 0 ? "+" : ""}${currency(forecast.expectedValue)}/share`;
  const targetDistance = forecast.barriers?.targetDistance == null ? "NA" : currency(forecast.barriers.targetDistance);
  const stopDistance = forecast.barriers?.stopDistance == null ? "NA" : currency(forecast.barriers.stopDistance);
  const regimeLabel = forecast.marketRegime
    ? `${forecast.marketRegime.trend.replaceAll("_", " ")}, ${forecast.marketRegime.volatility.replaceAll("_", " ")}, ${forecast.marketRegime.session.replaceAll("_", " ")}`
    : `${forecast.regime.trend}, ${forecast.regime.volatility}, ${forecast.regime.vwap}`;
  const sizeLabel = forecast.decision.positionSizeMultiplier == null ? "NA" : `${Math.round(forecast.decision.positionSizeMultiplier * 100)}%`;
  const algoScores = forecast.algorithmSignals?.weightedScores;
  const algoScoreLabel = algoScores
    ? `B ${formatProbability(algoScores.buy ?? 0)} / S ${formatProbability(algoScores.sell ?? 0)} / H ${formatProbability(algoScores.hold ?? 0)}`
    : "NA";
  const statusLabel = forecast.decision.action === "buy" ? "Buy edge" : forecast.decision.action === "sell" ? "Sell edge" : forecast.status === "insufficient_data" ? "Need data" : "No trade";
  const modelLabel = forecast.model.status === "ready" ? forecast.model.kind : "Fallback";
  const edgeGap = forecast.decision.edgeGap === null ? "NA" : formatProbability(forecast.decision.edgeGap);
  const modelDisagreement = forecast.uncertainty?.modelDisagreement ?? forecast.decision.modelDisagreement;
  const maxDisagreement = forecast.uncertainty?.maximumModelDisagreement ?? forecast.decision.maximumModelDisagreement ?? 0.1;
  const uncertaintyLabel = modelDisagreement == null ? "NA" : `${formatProbability(modelDisagreement)} / max ${formatProbability(maxDisagreement)}`;
  const targetStopImpact =
    forecast.barriers?.targetDistance == null || forecast.barriers?.stopDistance == null
      ? "neutral"
      : forecast.barriers.targetDistance >= forecast.barriers.stopDistance
        ? "positive"
        : "negative";
  const regimeImpact =
    forecast.decision.action === "buy"
      ? forecast.marketRegime?.allowedLong ? "positive" : "negative"
      : forecast.decision.action === "sell"
        ? forecast.marketRegime?.allowedShort ? "positive" : "negative"
        : "negative";
  const sizeImpact = forecast.decision.positionSizeMultiplier > 0 ? "positive" : "negative";
  const modelImpact = forecast.model.status === "ready" ? "positive" : forecast.status === "fallback" ? "negative" : "neutral";
  const algoScoresImpact = algoScores
    ? forecast.decision.action === "buy"
      ? (algoScores.buy ?? 0) > Math.max(algoScores.sell ?? 0, algoScores.hold ?? 0) ? "positive" : "negative"
      : forecast.decision.action === "sell"
        ? (algoScores.sell ?? 0) > Math.max(algoScores.buy ?? 0, algoScores.hold ?? 0) ? "positive" : "negative"
        : "negative"
    : "neutral";
  marketForecastPanel.dataset.status = forecast.allowed ? "pass" : forecast.status === "insufficient_data" ? "info" : "blocked";
  marketForecastPanel.innerHTML = `
    <div class="market-forecast-head">
      <span>Future Market Forecast</span>
      <strong>${escapeHtml(statusLabel)}</strong>
    </div>
    <div class="market-forecast-grid">
      ${renderMarketForecastItem("P(buy success)", `${buyProbability} / need ${formatProbability(forecast.threshold)}`, forecast.decision.action === "buy" ? thresholdImpact(buyProbabilityValue, forecast.threshold) : "neutral")}
      ${renderMarketForecastItem("P(sell success)", sellProbability, forecast.decision.action === "sell" ? thresholdImpact(sellProbabilityValue, forecast.threshold) : "neutral")}
      ${renderMarketForecastItem("P(no trade)", timeoutProbability, thresholdImpact(forecast.probabilityTimeout, 0.35, false))}
      ${renderMarketForecastItem("Edge gap", `${edgeGap} / need ${formatProbability(forecast.minimumEdgeGap)}`, thresholdImpact(forecast.decision.edgeGap, forecast.minimumEdgeGap))}
      ${renderMarketForecastItem("Target / Stop", `${targetDistance} / ${stopDistance}`, targetStopImpact)}
      ${renderMarketForecastItem("Expected Value", expectedValue, signedImpact(forecast.expectedValue))}
      ${renderMarketForecastItem("Uncertainty", uncertaintyLabel, thresholdImpact(modelDisagreement, maxDisagreement, false))}
      ${renderMarketForecastItem("Regime", regimeLabel, regimeImpact)}
      ${renderMarketForecastItem("Size", sizeLabel, sizeImpact)}
      ${renderMarketForecastItem("Algo Scores", algoScoreLabel, algoScoresImpact)}
      ${renderMarketForecastItem("Model", modelLabel, modelImpact)}
    </div>
    <div class="market-forecast-note">${escapeHtml(forecast.topDrivers.slice(0, 3).join(" | "))}</div>
  `;
}

function biasLabel(value: MarketLayer["directionBias"]) {
  const labels: Record<MarketLayer["directionBias"], string> = {
    long: "Long bias",
    short: "Short bias",
    neutral: "Neutral bias",
    cash: "Cash filter",
  };
  return labels[value];
}

function volatilityLabel(value: MarketLayer["volatility"]) {
  const labels: Record<MarketLayer["volatility"], string> = {
    low: "Low volatility",
    normal: "Normal volatility",
    high: "High volatility",
    expanding: "Vol expanding",
    contracting: "Vol contracting",
  };
  return labels[value];
}

function confidence(value: number) {
  return `${Math.round(value * 100)}% confidence`;
}

function updateLastCandleStatus(candle?: Candle) {
  if (!candle) {
    lastCandleStatus.textContent = "returned 0 candles";
    return;
  }
  lastCandleStatus.textContent = `returned ${state.candles.length} candles - latest ${formatDate(candle.timestamp)} C ${price(candle.close)}`;
}

function candleChanged(previous?: Candle, next?: Candle) {
  if (!previous && next) {
    return true;
  }
  if (!previous || !next) {
    return false;
  }
  return previous.timestamp !== next.timestamp || previous.close !== next.close || previous.volume !== next.volume;
}

function tickClock() {
  document.querySelector("#clock")!.textContent = `${formatTime(new Date().toISOString())} UTC${utcOffset()}`;
}

function utcOffset() {
  const minutes = -new Date().getTimezoneOffset();
  const sign = minutes >= 0 ? "+" : "-";
  return `${sign}${Math.abs(minutes / 60)}`;
}

function escapeHtml(value: string) {
  return value.replace(/[&<>"']/g, (character) => {
    const entities: Record<string, string> = {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;",
    };
    return entities[character];
  });
}

tickClock();
setInterval(tickClock, 1000);
startBrowserStorageDiskSnapshots();
scheduleAutoRefresh();
startWakeActivationMonitor();
setMarketRailTab("summary");
void loadMarketStatus();
void loadMacroEvents();
void loadFedEvents();
void loadTradingAlerts();
void loadCircuitBreakers();
void loadMocImbalance();
void loadVixRisk();
void loadSpyNews();
void loadEsSnapshot();
void loadMarketContext();
void loadCandles();
void loadLatestDynamicTradingArtifact();
void loadAlgoBacktestCandles();
