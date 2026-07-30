export type WcaSide = "BUY" | "SELL" | "HOLD" | "buy" | "sell" | "hold" | string;

export type WcaApplicability = "ACTIVE" | "NOT_APPLICABLE" | "INVALID" | "DEGRADED" | string;

export type WcaGateStatus = "pass" | "fail" | "warn" | "not_applicable" | "ALLOW" | "REDUCE_QUANTITY" | "REJECT_NEW_ENTRY" | "EXIT_ONLY" | "EMERGENCY_LIQUIDATE" | string;

export type WcaReasonedRecord = Record<string, unknown> & {
  reasonCodes?: string[];
  reason_codes?: string[];
  reason?: string;
  detail?: string;
};

export type WcaStrategyEvaluation = WcaReasonedRecord & {
  strategyId?: string;
  strategy_id?: string;
  name?: string;
  family?: string;
  version?: string;
  direction?: WcaSide;
  signal?: WcaSide;
  applicability?: WcaApplicability;
  dataQuality?: string;
  data_quality?: string;
  rawConfidence?: number;
  raw_confidence?: number;
  calibratedConfidence?: number;
  calibrated_confidence?: number;
  effectiveWeight?: number;
  effective_weight?: number;
  adjustedWeight?: number;
  adjusted_weight?: number;
  scoreContribution?: number;
  score_contribution?: number;
  baseWeight?: number;
  base_weight?: number;
  contribution?: number;
  excluded?: boolean;
  exclusionReason?: string;
  exclusion_reason?: string;
};

export type WcaStrategyContribution = WcaReasonedRecord & {
  strategyId?: string;
  strategy_id?: string;
  name?: string;
  family?: string;
  direction?: WcaSide;
  confidence?: number;
  calibratedConfidence?: number;
  calibrated_confidence?: number;
  baseWeight?: number;
  base_weight?: number;
  effectiveWeight?: number;
  effective_weight?: number;
  adjustedWeight?: number;
  adjusted_weight?: number;
  scoreContribution?: number;
  score_contribution?: number;
  contribution?: number;
  excluded?: boolean;
  exclusionReason?: string;
  exclusion_reason?: string;
};

export type WcaFamilyContribution = WcaReasonedRecord & {
  family?: string;
  buyScore?: number;
  buy_score?: number;
  sellScore?: number;
  sell_score?: number;
  buyContribution?: number;
  buy_contribution?: number;
  sellContribution?: number;
  sell_contribution?: number;
  holdContribution?: number;
  hold_contribution?: number;
  activeWeight?: number;
  active_weight?: number;
  directionalWeight?: number;
  directional_weight?: number;
  totalWeight?: number;
  total_weight?: number;
  capped?: boolean;
};

export type WcaAggregationResult = WcaReasonedRecord & {
  signal?: WcaSide;
  decisionLabel?: string;
  decision_label?: string;
  buyScore?: number;
  buy_score?: number;
  sellScore?: number;
  sell_score?: number;
  netScore?: number;
  net_score?: number;
  activeWeight?: number;
  active_weight?: number;
  normalizedNetScore?: number;
  normalized_net_score?: number;
  agreement?: number;
  activeStrategyCount?: number;
  active_strategy_count?: number;
  buyAgreement?: number;
  buy_agreement?: number;
  sellAgreement?: number;
  sell_agreement?: number;
  averageConfidence?: number;
  average_confidence?: number;
  buyAverageConfidence?: number;
  buy_average_confidence?: number;
  sellAverageConfidence?: number;
  sell_average_confidence?: number;
  winnerEdge?: number;
  winner_edge?: number;
  preGateDecision?: WcaSide;
  pre_gate_decision?: WcaSide;
  postLocalGateDecision?: WcaSide;
  post_local_gate_decision?: WcaSide;
  effectiveDecision?: WcaSide;
  effective_decision?: WcaSide;
  contributions?: WcaStrategyContribution[];
  strategyContributions?: WcaStrategyContribution[];
  strategy_contributions?: WcaStrategyContribution[];
  strategyEvaluations?: WcaStrategyEvaluation[];
  strategy_evaluations?: WcaStrategyEvaluation[];
  familyContributions?: WcaFamilyContribution[];
  family_contributions?: WcaFamilyContribution[];
  exclusions?: WcaStrategyContribution[];
};

export type WcaMarketStatus = WcaReasonedRecord & {
  profileId?: string;
  profile_id?: string;
  version?: string;
  trend?: string;
  volatility?: string;
  liquidity?: string;
  session?: string;
  eventRisk?: string;
  event_risk?: string;
  dataQuality?: string;
  data_quality?: string;
  algorithmRisk?: string;
  algorithm_risk?: string;
  confidence?: number;
  inputTimestamp?: string;
  input_timestamp?: string;
  expirationTimestamp?: string;
  expiration_timestamp?: string;
};

export type WcaEffectiveSettings = WcaReasonedRecord & {
  baselineConfigurationVersion?: string;
  baseline_configuration_version?: string;
  profileId?: string;
  profile_id?: string;
  profileVersion?: string;
  profile_version?: string;
  activeOverlays?: string[];
  active_overlays?: string[];
  baseline?: Record<string, unknown>;
  effective?: Record<string, unknown>;
  settings?: Record<string, unknown>;
  multipliers?: Record<string, number>;
  calculationTimestamp?: string;
  calculation_timestamp?: string;
  expirationTimestamp?: string;
  expiration_timestamp?: string;
};

export type WcaLocalGateEvaluation = WcaReasonedRecord & {
  gateId?: string;
  gate_id?: string;
  status?: WcaGateStatus;
  severity?: string;
  evaluatedValue?: unknown;
  evaluated_value?: unknown;
  requiredValue?: unknown;
  required_value?: unknown;
};

export type WcaLocalGateResult = WcaReasonedRecord & {
  gateId?: string;
  gate_id?: string;
  decision?: WcaGateStatus;
  status?: WcaGateStatus;
  allowEntry?: boolean;
  allow_entry?: boolean;
  blocksEntry?: boolean;
  blocks_entry?: boolean;
  entryBlocked?: boolean;
  entry_blocked?: boolean;
  exitAllowed?: boolean;
  exit_allowed?: boolean;
  evaluations?: WcaLocalGateEvaluation[];
  gates?: WcaLocalGateEvaluation[];
};

export type WcaGlobalGateResult = WcaReasonedRecord & {
  decision?: WcaGateStatus;
  status?: WcaGateStatus;
  allowEntry?: boolean;
  allow_entry?: boolean;
  allowExit?: boolean;
  allow_exit?: boolean;
  requestedQuantity?: number;
  requested_quantity?: number;
  approvedQuantity?: number;
  approved_quantity?: number;
  proposedQuantity?: number;
  proposed_quantity?: number;
  allowedQuantity?: number;
  allowed_quantity?: number;
  entryPermitted?: boolean;
  entry_permitted?: boolean;
  riskReducingExitPermitted?: boolean;
  risk_reducing_exit_permitted?: boolean;
  requestedRisk?: number;
  requested_risk?: number;
  approvedRisk?: number;
  approved_risk?: number;
  blockers?: string[];
  warnings?: string[];
};

export type WcaSizingResult = WcaReasonedRecord & {
  finalQuantity?: number;
  final_quantity?: number;
  proposedQuantity?: number;
  proposed_quantity?: number;
  globallyApprovedQuantity?: number;
  globally_approved_quantity?: number;
  limitingCap?: string;
  limiting_cap?: string;
  limitingFactor?: string;
  limiting_factor?: string;
  stopDistance?: number;
  stop_distance?: number;
  riskDollars?: number;
  risk_dollars?: number;
  entryPrice?: number;
  entry_price?: number;
  stopPrice?: number;
  stop_price?: number;
  targetPrice?: number;
  target_price?: number;
  side?: WcaSide;
};

export type WcaProposedOrder = WcaReasonedRecord & {
  side?: WcaSide;
  quantity?: number;
  approvedQuantity?: number;
  approved_quantity?: number;
  triggerPrice?: number;
  trigger_price?: number;
  limitPrice?: number;
  limit_price?: number;
  stopPrice?: number;
  stop_price?: number;
  targetPrice?: number;
  target_price?: number;
  plannedRisk?: number;
  planned_risk?: number;
};

export type WcaDecision = WcaReasonedRecord & {
  algorithmId?: string;
  algorithm_id?: string;
  decisionId?: string;
  decision_id?: string;
  decisionTimestamp?: string;
  decision_timestamp?: string;
  configurationVersion?: string;
  configuration_version?: string;
  engineVersion?: string;
  engine_version?: string;
  signal?: WcaSide;
  direction?: WcaSide;
  finalDecision?: WcaSide;
  final_decision?: WcaSide;
  effectiveDecision?: WcaSide;
  effective_decision?: WcaSide;
  aggregation?: WcaAggregationResult;
  aggregationResult?: WcaAggregationResult;
  aggregation_result?: WcaAggregationResult;
  strategyEvaluations?: WcaStrategyEvaluation[];
  strategy_evaluations?: WcaStrategyEvaluation[];
  strategies?: WcaStrategyEvaluation[];
  marketStatus?: WcaMarketStatus;
  market_status?: WcaMarketStatus;
  effectiveSettings?: WcaEffectiveSettings;
  effective_settings?: WcaEffectiveSettings;
  localGateResult?: WcaLocalGateResult;
  local_gate_result?: WcaLocalGateResult;
  localGates?: WcaLocalGateResult[];
  local_gates?: WcaLocalGateResult[];
  hardFilterResults?: WcaLocalGateResult[];
  hard_filter_results?: WcaLocalGateResult[];
  globalGateResult?: WcaGlobalGateResult;
  global_gate_result?: WcaGlobalGateResult;
  sizing?: WcaSizingResult;
  sizingResult?: WcaSizingResult;
  sizing_result?: WcaSizingResult;
  proposedOrder?: WcaProposedOrder;
  proposed_order?: WcaProposedOrder;
};

export type WcaBacktestTrade = WcaReasonedRecord & {
  side?: WcaSide;
  entryAt?: string;
  entry_at?: string;
  exitAt?: string;
  exit_at?: string;
  entryPrice?: number;
  entry_price?: number;
  exitPrice?: number;
  exit_price?: number;
  quantity?: number;
  shares?: number;
  pnl?: number;
  exitReason?: string;
  exit_reason?: string;
  decisionId?: string;
  decision_id?: string;
};

export type WcaBacktestResult = WcaReasonedRecord & {
  runId?: string;
  run_id?: string;
  status?: string;
  totalPnl?: number;
  total_pnl?: number;
  totalReturnPercent?: number;
  total_return_percent?: number;
  maxDrawdown?: number;
  max_drawdown?: number;
  trades?: WcaBacktestTrade[];
  decisions?: WcaDecision[];
  metrics?: Record<string, unknown>;
  diagnostics?: Record<string, unknown>;
  runConfiguration?: Record<string, unknown>;
  run_configuration?: Record<string, unknown>;
};

export type WcaCommandReceipt = WcaReasonedRecord & {
  commandId?: string;
  command_id?: string;
  commandType?: string;
  command_type?: string;
  job_id?: string;
  jobId?: string;
  status?: string;
  accepted?: boolean;
  queued?: boolean;
  paperOnly?: boolean;
  paper_only?: boolean;
};

export type WcaConfigurationResponse = {
  algorithmId?: string;
  algorithm_id?: string;
  configurationVersion?: string;
  configuration_version?: string;
  engineVersion?: string;
  engine_version?: string;
  decisionSettings?: Record<string, unknown>;
  decision_settings?: Record<string, unknown>;
  tradingSettings?: Record<string, unknown>;
  trading_settings?: Record<string, unknown>;
  baseWeights?: Record<string, number>;
  base_weights?: Record<string, number>;
  strategyCount?: number;
  strategy_count?: number;
  paperOnly?: boolean;
  paper_only?: boolean;
  activeVersions?: Record<string, unknown>;
  active_versions?: Record<string, unknown>;
};

export type WcaStatusResponse = {
  algorithmId?: string;
  algorithm_id?: string;
  serviceVersion?: string;
  service_version?: string;
  engineVersion?: string;
  engine_version?: string;
  configurationVersion?: string;
  configuration_version?: string;
  status?: string;
  mode?: string;
  strategyCount?: number;
  strategy_count?: number;
  paperOnly?: boolean;
  paper_only?: boolean;
  persistence?: Record<string, unknown>;
  runtimeHealth?: Record<string, unknown>;
  runtime_health?: Record<string, unknown>;
  apiHealth?: Record<string, unknown>;
  api_health?: Record<string, unknown>;
  activeVersions?: Record<string, unknown>;
  active_versions?: Record<string, unknown>;
  observability?: Record<string, unknown>;
  virtualInventory?: Record<string, unknown>;
  virtual_inventory?: Record<string, unknown>;
  reasonCodes?: string[];
  reason_codes?: string[];
};

export type WcaDecisionsResponse = {
  algorithmId?: string;
  algorithm_id?: string;
  decisions?: WcaDecision[];
};

export type WcaBaselineSettings = Record<string, unknown>;
