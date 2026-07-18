import type { MarketCandle } from "../../../trading/shared/market-data-types.ts";
import type {
  ContextResult,
  EffectiveRegimeSettings,
  MarketRegimeId,
  RegimeFamilyScore,
  RegimeProfileModifierBreakdown,
  RegimeSelectedStrategy,
  RegimeSizingDefaults,
  RegimeTradingSettings,
  SafetyGateResult,
} from "../types.ts";
import type { RegimeMlArtifact, RegimeMlMode } from "../ml/types.ts";

export const REGIME_BACKTEST_FILE_INVENTORY = [
  "engine.ts",
  "execution-simulator.ts",
  "metrics.ts",
  "diagnostics.ts",
  "walk-forward.ts",
  "runner.ts",
  "types.ts",
] as const;

export const REGIME_BACKTEST_OWNED_CAPABILITIES = [
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
] as const;

export type RegimeBacktestInventoryFile = typeof REGIME_BACKTEST_FILE_INVENTORY[number];
export type RegimeBacktestOwnedCapability = typeof REGIME_BACKTEST_OWNED_CAPABILITIES[number];

export type RegimeBacktestInventoryStatus = {
  algorithmId: "regime";
  authoritativeEngine: "frontend/src/algorithms/regime/backtest/engine.ts";
  files: readonly RegimeBacktestInventoryFile[];
  ownedCapabilities: readonly RegimeBacktestOwnedCapability[];
  isolatedFromWca: true;
};

export type RegimeBacktestVariantId =
  | "rule_static"
  | "rule_dynamic"
  | "ml_shadow"
  | "ml_confirm_only"
  | "without_context_modifiers"
  | "with_context_modifiers"
  | "without_family_caps"
  | "with_family_caps"
  | "long_only"
  | "long_and_short";

export type RegimeBacktestExecutionCostModel = {
  spreadPercent: number;
  slippagePerShare: number;
  feePerShare: number;
  maximumVolumeParticipationPercent: number;
  orderDelayBars: number;
  rejectWhenParticipationQuantityZero: boolean;
};

export type RegimeBacktestGlobalGateSettings = {
  maximumApprovedQuantity: number | null;
  maximumRiskDollars: number | null;
  maximumNotionalDollars: number | null;
};

export type RegimeBacktestInput = {
  symbol: string;
  candles: MarketCandle[];
  settings?: RegimeTradingSettings;
  sizingDefaults?: RegimeSizingDefaults;
  startingCapital?: number;
  costModel?: Partial<RegimeBacktestExecutionCostModel>;
  globalGate?: Partial<RegimeBacktestGlobalGateSettings>;
  mlMode?: RegimeMlMode;
  mlArtifact?: RegimeMlArtifact | null;
  variantId?: RegimeBacktestVariantId;
  shortEntriesEnabled?: boolean;
  useDynamicProfiles?: boolean;
  useContextModifiers?: boolean;
  useFamilyCaps?: boolean;
};

export type RegimeBacktestDecision = {
  timestamp: string;
  rawRegime: MarketRegimeId | null;
  confirmedRegime: MarketRegimeId | null;
  regimeConfidence: number;
  candidateRegime: MarketRegimeId | null;
  confirmationCount: number;
  regimeDwell: number;
  regimeTransition: string;
  selectedStrategies: string[];
  skippedStrategies: Array<{ strategyId: string; reason: string }>;
  individualSignals: Array<{ strategyId: string; signal: string; confidence: number; reason: string }>;
  contextResults: ContextResult[];
  safetyResults: SafetyGateResult[];
  familyScores: RegimeFamilyScore[];
  buyScore: number;
  sellScore: number;
  winningDirection: string;
  winningScore: number;
  secondBestScore: number;
  directionalEdge: number;
  activeStrategyCount: number;
  activeFamilyCount: number;
  baseSettings: RegimeTradingSettings;
  dynamicModifiers: RegimeProfileModifierBreakdown | null;
  effectiveSettings: EffectiveRegimeSettings | null;
  requestedRisk: number;
  requestedQuantity: number;
  globalApprovedQuantity: number;
  limitingCap: string;
  entryBlockers: string[];
  entryPrice: number | null;
  stopPrice: number | null;
  targetPrice: number | null;
  exitPrice: number | null;
  exitReason: string | null;
  spread: number;
  slippage: number;
  fees: number;
  mae: number;
  mfe: number;
  realizedPnl: number;
  rMultiple: number;
  abstentionRate: number;
  profileId: string | null;
  signalStrengthBucket: string;
  winningScoreBucket: string;
  edgeBucket: string;
  regimeConfidenceBucket: string;
  timeOfDay: string;
  volatilityState: string;
  liquidityState: string;
  eventPeriod: boolean;
};

export type RegimeBacktestTrade = {
  tradeId: string;
  entryDecisionTimestamp: string;
  entryAt: string;
  exitAt: string;
  side: "Long" | "Short";
  entryPrice: number;
  exitPrice: number;
  stopPrice: number;
  targetPrice: number;
  quantity: number;
  requestedQuantity: number;
  globalApprovedQuantity: number;
  pnl: number;
  fees: number;
  slippage: number;
  mae: number;
  mfe: number;
  rMultiple: number;
  holdingMinutes: number;
  exitReason: string;
  confirmedRegime: MarketRegimeId | null;
  rawRegime: MarketRegimeId | null;
  strategyIds: string[];
  familyScores: RegimeFamilyScore[];
  limitingCap: string;
  dynamicProfileId: string | null;
};

export type RegimeBacktestMetrics = {
  netReturn: number;
  netProfit: number;
  tradeCount: number;
  winRate: number;
  profitFactor: number | null;
  expectancy: number;
  averageR: number;
  sharpeRatio: number | null;
  sortinoRatio: number | null;
  maximumDrawdown: number;
  drawdownDuration: number;
  calmarRatio: number | null;
  exposure: number;
  turnover: number;
  averageHoldingMinutes: number;
  longPerformance: number;
  shortPerformance: number;
  regimeCoverage: number;
  noTradePercentage: number;
  regimeSwitchFrequency: number;
  averageConfirmationDelay: number;
  falseTransitionRate: number;
  blockedTradeCounterfactualResult: number;
  staticVersusDynamicProfileDifference: number;
};

export type RegimeBacktestReportRow = {
  key: string;
  trades: number;
  netPnl: number;
  averageR: number;
  winRate: number;
};

export type RegimeBacktestReports = Record<
  | "confirmedRegime"
  | "rawRegime"
  | "transitionState"
  | "strategy"
  | "strategyFamily"
  | "side"
  | "timeOfDay"
  | "volatilityState"
  | "liquidityState"
  | "eventPeriod"
  | "dynamicProfile"
  | "signalStrengthBucket"
  | "winningScoreBucket"
  | "edgeBucket"
  | "regimeConfidenceBucket"
  | "month"
  | "year"
  | "exitReason"
  | "limitingQuantityCap",
  RegimeBacktestReportRow[]
>;

export type RegimeBacktestComparison = {
  variantId: RegimeBacktestVariantId;
  metrics: RegimeBacktestMetrics;
  accepted: boolean;
  rejectionReasons: string[];
};

export type RegimeBacktestWalkForwardFold = {
  foldId: string;
  trainingStart: string;
  trainingEnd: string;
  validationStart: string;
  validationEnd: string;
  testStart: string;
  testEnd: string;
  tradeCount: number;
  netProfit: number;
  accepted: boolean;
  rejectionReasons: string[];
};

export type RegimeBacktestResult = {
  engineVersion: "regime_backtest_v2";
  algorithmId: "regime";
  symbol: string;
  candles: number;
  decisions: RegimeBacktestDecision[];
  trades: RegimeBacktestTrade[];
  totalPnl: number;
  metrics: RegimeBacktestMetrics;
  reports: RegimeBacktestReports;
  comparisons: RegimeBacktestComparison[];
  walkForward: RegimeBacktestWalkForwardFold[];
  diagnostics: string[];
  artifactPath: string;
  cacheKey: string;
  storageKey: string;
  failureMessage: string | null;
};
