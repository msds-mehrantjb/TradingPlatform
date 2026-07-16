export const Signal = {
  BUY: "BUY",
  SELL: "SELL",
  HOLD: "HOLD",
} as const;

export type Signal = (typeof Signal)[keyof typeof Signal];

export const Direction = {
  SHORT: -1,
  FLAT: 0,
  LONG: 1,
} as const;

export type Direction = (typeof Direction)[keyof typeof Direction];

export const StrategyRole = {
  DIRECTIONAL: "DIRECTIONAL",
  CONTEXT: "CONTEXT",
  REGIME: "REGIME",
  SAFETY: "SAFETY",
  AGGREGATOR: "AGGREGATOR",
} as const;

export type StrategyRole = (typeof StrategyRole)[keyof typeof StrategyRole];

export const StrategyFamily = {
  TREND: "TREND",
  BREAKOUT: "BREAKOUT",
  REVERSAL: "REVERSAL",
  MEAN_REVERSION: "MEAN_REVERSION",
  GAP_SESSION: "GAP_SESSION",
  MARKET_CONTEXT: "MARKET_CONTEXT",
  SAFETY: "SAFETY",
} as const;

export type StrategyFamily = (typeof StrategyFamily)[keyof typeof StrategyFamily];

export const GateStatus = {
  PASS: "PASS",
  CAUTION: "CAUTION",
  FAIL: "FAIL",
  INFO: "INFO",
} as const;

export type GateStatus = (typeof GateStatus)[keyof typeof GateStatus];

export type SafetyOrderIntent =
  | "new_entry"
  | "protective_exit"
  | "risk_reducing"
  | "end_of_day_liquidation"
  | "reconciliation";

export const OperatingMode = {
  OFF: "OFF",
  SHADOW: "SHADOW",
  ACTIVE: "ACTIVE",
  FALLBACK: "FALLBACK",
} as const;

export type OperatingMode = (typeof OperatingMode)[keyof typeof OperatingMode];

export type UtcIsoTimestamp = string;
export type NewYorkSessionDate = string;
export type Score01 = number; // Range: 0.0 to 1.0 inclusive.

export type StrategySignal = {
  strategyId: string;
  strategyName: string;
  strategyVersion: string;
  family: StrategyFamily;
  role: StrategyRole;
  signal: Signal;
  direction: Direction;
  confidence: Score01; // Current signal confidence, 0.0 to 1.0.
  active: boolean;
  eligible: boolean;
  dataReady: boolean;
  setupDetected: boolean;
  regimeFit: Score01; // Current market-regime fit, 0.0 to 1.0.
  reliability: Score01; // Historical or calibrated reliability, 0.0 to 1.0.
  reliabilityVersion: string;
  reliabilitySourceWindow: Record<string, unknown>;
  structuralInvalidationPrice: number | null;
  reasonCodes: string[];
  explanation: string;
  features: Record<string, unknown>;
  requiredInputs: string[];
  inputTimestamps: Record<string, UtcIsoTimestamp>;
  evaluatedAt: UtcIsoTimestamp;
  sessionDate: NewYorkSessionDate;
  configurationHash: string;
};

export type ContextSignal = {
  contextId: string;
  signal: Signal;
  direction: Direction;
  confidence: Score01; // 0.0 to 1.0.
  dataReady: boolean;
  explanation: string;
  features: Record<string, unknown>;
  evaluatedAt: UtcIsoTimestamp;
  sessionDate: NewYorkSessionDate;
  configurationHash: string;
};

export type BreadthSourceKind = "breadth_feed" | "breadth_proxy";

export type MarketBreadthMomentumFeatures = {
  breadthSourceKind: BreadthSourceKind;
  breadthSourceLabel: string;
  percentagePositiveReturn: number | null;
  percentageAboveVwap: number | null;
  percentageAboveEma20: number | null;
  medianComponentReturn: number | null;
  upDownVolumeRatio: number | null;
  dispersion: number | null;
  dataCoverage: number;
  componentCount: number;
  freshComponentCount: number;
  proxyBasket: string[];
  contextEffect: string;
  reasonCodes: string[];
};

export type EconomicEventContextFeatures = {
  eventImportance: string;
  minutesUntilEvent: number | null;
  minutesSinceEvent: number | null;
  eventState: string;
  directionalReaction: string;
  volatilityShock: number | null;
  spreadShock: number | null;
  recommendedRiskCap: number;
  maxConfidenceAdjustment: number;
  contextEffect: string;
  reasonCodes: string[];
};

export type MarketStructureContextFeatures = {
  higherHighsHigherLows: boolean;
  lowerHighsLowerLows: boolean;
  rangeStructure: boolean;
  breakOfStructure: string;
  structureQuality: number;
  maxConfidenceAdjustment: number;
  contextEffect: string;
  reasonCodes: string[];
};

export type VolumeConfirmationFeatures = {
  relativeVolume: number | null;
  breakoutVolumeConfirmation: boolean;
  pullbackVolumeBehavior: string;
  volumeTrend: string;
  dataQuality: number;
  maxConfidenceAdjustment: number;
  contextEffect: string;
  reasonCodes: string[];
};

export type VwapPositionContextFeatures = {
  pricePosition: string;
  distanceFromVwapAtr: number | null;
  vwapSlope: number | null;
  reclaimRejectionState: string;
  maxConfidenceAdjustment: number;
  contextEffect: string;
  reasonCodes: string[];
};

export type AdxAtrRegimeFeatures = {
  dataReady: boolean;
  trendStrengthAdx: number | null;
  atr: number | null;
  atrPercentile: number | null;
  realizedVolatilityPercentile: number | null;
  rangeTrendClassification: "trend" | "range" | "unstable" | "unknown";
  volatilityExpansionContraction: "expansion" | "contraction" | "stable" | "unknown";
  directionalBias: "bullish_context" | "bearish_context" | "neutral_context";
  directionalBiasContextOnly: true;
  directionMustNotSubstituteStrategySignal: true;
  confidenceRange: string;
  fitRange: string;
  trendFit: Score01;
  breakoutFit: Score01;
  reversalFit: Score01;
  meanReversionFit: Score01;
  gapSessionFit: Score01;
  singleRegimeStateFromActualMeasurements: true;
  reasonCodes: string[];
};

export type RegimeState = {
  regimeId: string;
  label:
    | "strong_trend"
    | "weak_trend"
    | "range"
    | "low_volatility"
    | "high_volatility"
    | "event_shock"
    | "unknown";
  direction: Direction;
  volatility: "LOW" | "NORMAL" | "HIGH" | "EXTREME";
  confidence: Score01; // 0.0 to 1.0.
  features: AdxAtrRegimeFeatures | Record<string, unknown>;
  evaluatedAt: UtcIsoTimestamp;
  sessionDate: NewYorkSessionDate;
  configurationHash: string;
};

export type FamilyScore = {
  family: StrategyFamily;
  buyScore: Score01; // 0.0 to 1.0.
  sellScore: Score01; // 0.0 to 1.0.
  holdScore: Score01; // 0.0 to 1.0.
  confidence: Score01; // 0.0 to 1.0.
  reliability: Score01; // 0.0 to 1.0.
  explanation: string;
};

export type StrategyReliabilityEstimate = {
  strategyId: string;
  reliability: Score01;
  appliedReliability: Score01;
  neutralReliability: Score01;
  sampleSize: number;
  effectiveSampleSize: number;
  sourceWindowStart: UtcIsoTimestamp | null;
  sourceWindowEnd: UtcIsoTimestamp | null;
  mode: OperatingMode;
  reliabilityVersion: string;
  configurationHash: string;
  components: Record<string, number>;
  reasonCodes: string[];
  explanation: string;
};

export type EnsembleDecision = {
  decisionId: string;
  signal: Signal;
  direction: Direction;
  confidence: Score01; // 0.0 to 1.0.
  rawScore: number; // -1.0 to 1.0.
  finalScore: number; // -1.0 to 1.0.
  buyConfidence: Score01; // 0.0 to 1.0.
  sellConfidence: Score01; // 0.0 to 1.0.
  holdConfidence: Score01; // 0.0 to 1.0.
  supportingFamilies: StrategyFamily[];
  opposingFamilies: StrategyFamily[];
  eligibleStrategyCount: number;
  familyScores: FamilyScore[];
  strategySignals: StrategySignal[];
  contextAdjustments: Record<string, unknown>[];
  safetyStatus: GateStatus;
  reasonCodes: string[];
  explanation: string;
  dataReady: boolean;
  eligible: boolean;
  decidedAt: UtcIsoTimestamp;
  sessionDate: NewYorkSessionDate;
  configurationHash: string;
  engineVersion: string;
};

export type MetaModelPrediction = {
  modelId: string;
  modelVersion: string;
  objective: "candidate_success_probability";
  candidateSide: Signal | null;
  probabilityCandidateSuccess: Score01 | null;
  probabilityTargetBeforeStop: Score01 | null;
  probabilityProfitableAfterCosts: Score01 | null;
  signal: Signal;
  probabilityBuy: Score01; // 0.0 to 1.0.
  probabilitySell: Score01; // 0.0 to 1.0.
  probabilityHold: Score01; // 0.0 to 1.0.
  confidence: Score01; // 0.0 to 1.0.
  reliability: Score01; // 0.0 to 1.0.
  features: Record<string, unknown>;
  predictedAt: UtcIsoTimestamp;
  sessionDate: NewYorkSessionDate;
  configurationHash: string;
};

export type GateResult = {
  gateId: string;
  gateName: string;
  status: GateStatus;
  blocksTrading: boolean;
  reasonCodes: string[];
  explanation: string;
  checkedAt: UtcIsoTimestamp;
  configurationHash: string;
};

export type GlobalGateDecision = {
  status: GateStatus;
  eligible: boolean;
  dataReady: boolean;
  gateResults: GateResult[];
  reasonCodes: string[];
  explanation: string;
  checkedAt: UtcIsoTimestamp;
  sessionDate: NewYorkSessionDate;
  configurationHash: string;
};

export type SafetyOperationalState = {
  marketOpen: boolean | null;
  eventBlackoutActive: boolean | null;
  haltOrLuld: boolean | null;
  circuitBreaker: boolean | null;
  brokerAccountRestricted: boolean | null;
  manualCashMode: boolean | null;
  restrictionExplanation: string | null;
  observedAt: UtcIsoTimestamp;
};

export type CashAvoidTradingSettings = {
  configVersion: string;
  manualCashMode: boolean;
  maxSpreadBasisPoints: number;
  extremeAtrPercentile: Score01;
  extremeRealizedVolatilityPercentile: Score01;
  maxDailyLossPercent: number;
  maxAccountStateAgeSeconds: number;
  maxOperationalStateAgeSeconds: number;
  eventBlackoutImportance: string[];
  configurationHash: string;
};

export type BaselineTradingSettings = {
  baseRiskPercent: number;
  basePositionPercent: number;
  baseOrderAllocationPercent: number;
  baseDailyAllocationPercent: number;
  baseAtrStopMultiplier: number;
  baseMinimumStopPercent: number;
  baseTargetR: number;
  baseMaximumHoldingMinutes: number;
  baseParticipationPercent: number;
  baseEntryOffsetBps: number;
  baseSlippagePerShare: number;
  minimumExpectedValue: number;
  minimumModelProbability: Score01;
  settingsVersion: string;
  configurationHash: string;
  startingCapital: number;
  orderAllocationPercent: number;
  dailyAllocationPercent: number;
  riskBudgetPercentOfOrder: number;
  maxTradesPerDay: number;
  stopLossPercent: number;
  fixedStopDistanceDollars: number;
  takeProfitR: number;
  slippagePerShare: number;
  positionSizingMode: "allocation" | "risk";
};

export type HardRiskLimits = {
  maximumRiskPerTradePercent: number;
  maximumDailyLossPercent: number;
  maximumOpenRiskPercent: number;
  maximumPositionPercent: number;
  maximumOrderNotionalPercent: number;
  maximumDailyNotionalPercent: number;
  maximumShares: number;
  maximumVolumeParticipationPercent: number;
  maximumTradesPerDay: number;
  maximumConsecutiveLosses: number;
  maximumSpreadBps: number;
  allowPyramiding: boolean;
  newEntryCutoff: string;
  configurationHash: string;
  maxDailyLossPercent: number;
  maxOrderNotional: number;
  maxPositionNotional: number;
  maxShareQuantity: number;
  minStopDistanceDollars: number;
  maxSlippagePerShare: number;
};

export type DynamicPolicyBounds = {
  minimumRiskMultiplier: number;
  maximumRiskMultiplier: number;
  minimumTargetR: number;
  maximumTargetR: number;
  minimumHoldingMinutes: number;
  maximumHoldingMinutes: number;
  minimumAtrStopMultiplier: number;
  maximumAtrStopMultiplier: number;
  minConfidence: Score01; // 0.0 to 1.0.
  minReliability: Score01; // 0.0 to 1.0.
  minRegimeFit: Score01; // 0.0 to 1.0.
  maxSpreadPercent: number;
  maxParticipationPercent: number;
  minLiquidityShares: number;
  configurationHash: string;
};

export type TradeCandidate = {
  candidateId: string;
  symbol: string;
  signal: Signal;
  direction: Direction;
  entryPrice: number;
  stopPrice: number | null;
  targetPrice: number | null;
  quantity: number;
  confidence: Score01; // 0.0 to 1.0.
  expectedValue: number | null;
  features: Record<string, unknown>;
  reasonCodes: string[];
  explanation: string;
  generatedAt: UtcIsoTimestamp;
  sessionDate: NewYorkSessionDate;
  configurationHash: string;
};

export type AccountRiskState = {
  accountId: string;
  equity: number;
  buyingPower: number;
  openPositionNotional: number;
  realizedPnlToday: number;
  unrealizedPnlToday: number;
  estimatedExitCosts: number;
  dailyNetPnlAfterExitCosts: number | null;
  intradayEquityHigh: number | null;
  drawdownFromIntradayHighPercent: number;
  totalOpenRiskPercent: number;
  totalSpyNotionalPercent: number;
  sameDirectionExposurePercent: number;
  tradesToday: number;
  observedAt: UtcIsoTimestamp;
  sessionDate: NewYorkSessionDate;
};

export type EffectiveTradePolicy = {
  mode: OperatingMode;
  baselineSettings: BaselineTradingSettings;
  hardRiskLimits: HardRiskLimits;
  dynamicBounds: DynamicPolicyBounds;
  accountRiskState: AccountRiskState;
  maxQuantity: number;
  maxNotional: number;
  riskDollars: number;
  explanation: string;
  effectiveAt: UtcIsoTimestamp;
  sessionDate: NewYorkSessionDate;
  configurationHash: string;
};

export type OrderPlan = {
  orderPlanId: string;
  candidateId: string;
  symbol: string;
  side: Signal;
  orderType: "STOP_LIMIT" | "LIMIT" | "MARKET" | "NO_ORDER";
  quantity: number;
  entryPrice: number;
  stopPrice: number | null;
  targetPrice: number | null;
  limitPrice: number | null;
  maximumHoldingMinutes: number | null;
  strategyInvalidationPrice: number | null;
  endOfDayExit: boolean;
  timeInForce: "DAY" | "GTC";
  eligible: boolean;
  validationErrors: string[];
  explanation: string;
  generatedAt: UtcIsoTimestamp;
  sessionDate: NewYorkSessionDate;
  configurationHash: string;
};

export type FillResult = {
  fillId: string;
  orderPlanId: string;
  symbol: string;
  side: Signal;
  quantity: number;
  averageFillPrice: number;
  fees: number;
  slippagePerShare: number;
  filledAt: UtcIsoTimestamp;
  sessionDate: NewYorkSessionDate;
  explanation: string;
};

export type CandidateMetaLabel = {
  labelSchemaVersion: "candidate_meta_label_v1";
  labelVersion: string;
  labelId: string;
  snapshotId: string;
  symbol: string;
  candidateId: string | null;
  candidateSide: Signal;
  decisionTimestampUtc: UtcIsoTimestamp;
  sessionDateNewYork: NewYorkSessionDate;
  entryTimestampUtc: UtcIsoTimestamp | null;
  entryPrice: number | null;
  profitTargetPrice: number | null;
  protectiveStopPrice: number | null;
  upperBarrierPrice: number | null;
  lowerBarrierPrice: number | null;
  verticalBarrierTimestampUtc: UtcIsoTimestamp | null;
  firstBarrierHit: "TARGET" | "STOP" | "VERTICAL" | "NO_CANDIDATE" | "NO_ENTRY" | "INVALID_GEOMETRY";
  firstBarrierTimestampUtc: UtcIsoTimestamp | null;
  exitPrice: number | null;
  strictOutcomeLabel: 0 | 1 | null;
  costAdjustedTrainingLabel: 0 | 1 | null;
  grossPnlPerShare: number | null;
  netPnlAfterCosts: number | null;
  quantity: number;
  spreadDollars: number;
  slippagePerShare: number;
  fees: number;
  latencyMilliseconds: number;
  orderFillBehavior: string;
  barrierExplanation: string;
  eligibleForTraining: boolean;
  reasonCodes: string[];
  createdAt: UtcIsoTimestamp;
  configurationHash: string;
};

export type MLFeatureSpec = {
  name: string;
  group: "directional_strategy" | "family" | "context" | "regime" | "execution" | "candidate" | "upstream_forecast";
  valueType: "numeric" | "categorical";
};

export type MLFeatureSet = {
  schemaVersion: "candidate_meta_feature_schema_v1";
  schemaHash: string;
  snapshotId: string;
  symbol: string;
  decisionTimestampUtc: UtcIsoTimestamp;
  featureValues: Record<string, unknown>;
  missingIndicators: Record<string, boolean>;
  forbiddenFieldsChecked: string[];
  explanation: string;
};

export type OutOfSampleForecastFeature = {
  featureVersion: "market_forecast_oos_feature_v1";
  status: "out_of_sample" | "live_approved_artifact";
  rowId: string;
  symbol: string;
  decisionTimestampUtc: UtcIsoTimestamp;
  trainingWindowStartUtc: UtcIsoTimestamp;
  trainingWindowEndUtc: UtcIsoTimestamp;
  validationWindowStartUtc: UtcIsoTimestamp | null;
  validationWindowEndUtc: UtcIsoTimestamp | null;
  fold: number | null;
  artifactId: string;
  modelKind: string;
  probabilityBuySuccess: number;
  probabilitySellSuccess: number;
  probabilityTimeout: number;
  modelDisagreement: number | null;
  reasonCodes: string[];
  explanation: string;
};

export type ForecastFallbackFeature = {
  featureVersion: "market_forecast_oos_feature_v1";
  status: "missing_approved_forecast_model";
  probabilityBuySuccess: null;
  probabilitySellSuccess: null;
  trainingWindowEndUtc: null;
  artifactId: null;
  reasonCodes: string[];
  explanation: string;
};

export type V1SnapshotArchiveRecord = {
  archiveSchemaVersion: "v1_snapshot_archive_v1";
  archiveId: string;
  sourceSnapshotId: string;
  sourceSchemaVersion: string;
  archivedAt: UtcIsoTimestamp;
  preservedFor: "historical_comparison";
  trainingCompatibleWithV2: false;
  containsDuplicatedVoteSignals: boolean;
  migrationMetadata: Record<string, unknown>;
  explanation: string;
};

export type DecisionSnapshotV2 = {
  snapshotSchemaVersion: "decision_snapshot_v2";
  snapshotVersion: "decision_snapshot_v2";
  strategySchemaVersion: string;
  featureSchemaVersion: string;
  labelVersion: string;
  executionModelVersion: string;
  gateVersion: string;
  policyVersion: string;
  modelVersion: string;
  snapshotId: string;
  codeVersion: string;
  symbol: string;
  marketDataFeed: string;
  decisionTimestampUtc: UtcIsoTimestamp;
  sessionDateNewYork: NewYorkSessionDate;
  sessionDate: NewYorkSessionDate;
  decisionTimestamp: UtcIsoTimestamp;
  operatingMode: OperatingMode;
  dataQuality: Record<string, unknown>;
  rawMarketReferences: Record<string, unknown>;
  featureSnapshot: Record<string, unknown>;
  strategySignals: StrategySignal[];
  directionalStrategyOutputs: StrategySignal[];
  contextSignals: ContextSignal[];
  contextOutputs: ContextSignal[];
  regimeState: RegimeState;
  safetyOutput: GlobalGateDecision | null;
  ensembleDecision: EnsembleDecision;
  metaModelPrediction: MetaModelPrediction | null;
  globalGateDecision: GlobalGateDecision;
  globalGateResults: GateResult[];
  effectiveTradePolicy: EffectiveTradePolicy;
  tradeCandidate: TradeCandidate | null;
  orderPlan: OrderPlan | null;
  brokerSubmissionResult: Record<string, unknown> | null;
  fillResult: FillResult | null;
  fills: FillResult[];
  positionState: Record<string, unknown>;
  finalOutcome: Record<string, unknown> | null;
  eligibleForTraining: boolean;
  trainingIncompatibilityReasons: string[];
  samplingProbability: number;
  sampleWeight: number;
  samplingReason: string;
  explanation: string;
  engineVersion: string;
  strategyConfigurationHash: string;
  tradingSettingsHash: string;
  configurationHash: string;
};
