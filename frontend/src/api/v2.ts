import { defaultApiClient, type ApiClient } from "./client";
import type {
  ContextSignal,
  DecisionSnapshotV2,
  EffectiveTradePolicy,
  EnsembleDecision,
  GateResult,
  GlobalGateDecision,
  OrderPlan,
  RegimeState,
  StrategySignal,
} from "../domain/models";

export type ApiV2Envelope<TPayload> = {
  apiVersion: "api_v2";
  endpointVersion: string;
  configurationHash: string;
  payload: TPayload;
  explanation: string;
};

export type MarketCandlePayload = {
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  tradeCount?: number | null;
  symbol?: string | null;
  timeframe?: string | null;
};

export type FeatureEvaluateRequest = {
  evaluationTimestamp: string;
  sessionDate: string;
  spy1mCandles: MarketCandlePayload[];
  spy5mCandles: MarketCandlePayload[];
  spy15mCandles: MarketCandlePayload[];
  qqqAlignedCandles?: MarketCandlePayload[];
  iwmAlignedCandles?: MarketCandlePayload[];
  priorDayOHLC?: Record<string, unknown> | null;
  premarket?: Record<string, unknown> | null;
  openingRange?: Record<string, unknown> | null;
  bidAskQuote?: Record<string, unknown> | null;
  breadthComponents?: Record<string, MarketCandlePayload[]>;
  economicEventState?: Record<string, unknown>;
  executionStyle?: "live" | "replay";
};

export type ReplayDecisionEvaluateRequest = {
  symbol?: string;
  sessionDate: string;
  evaluationTimestamp: string;
  spy1mCandles: MarketCandlePayload[];
  spy5mCandles?: MarketCandlePayload[];
  spy15mCandles?: MarketCandlePayload[];
  qqqCandles?: MarketCandlePayload[];
  iwmCandles?: MarketCandlePayload[];
  priorDayOHLC?: Record<string, unknown> | null;
  premarket?: Record<string, unknown> | null;
  openingRange?: Record<string, unknown> | null;
  breadthComponents?: Record<string, MarketCandlePayload[]>;
  economicEventState?: Record<string, unknown>;
};

export type BacktestRunRequest = Omit<ReplayDecisionEvaluateRequest, "evaluationTimestamp">;

export type SafeMLInferenceResult = {
  mode: string;
  effectiveMode: string;
  deterministicSignal: string;
  finalSignal: string;
  candidateAccepted: boolean;
  mlWouldAcceptCandidate: boolean;
  appliedToOrder: boolean;
  successProbability: number | null;
  calibratedProbability: number | null;
  expectedValueAfterCosts: number | null;
  uncertainty: number | null;
  outOfDistributionScore: number | null;
  featureMissingness: number;
  modelHealth: Record<string, unknown>;
  recommendedRiskCap: number;
  reasonCodes: string[];
  predictedAt: string;
  sessionDate: string;
  configurationHash: string;
};

export type PaperDecisionPayload = {
  strategyOutputs: StrategySignal[];
  contextOutputs: ContextSignal[];
  regime: RegimeState;
  familyEnsemble: EnsembleDecision;
  gateResults: GateResult[];
  mlResult: SafeMLInferenceResult | null;
  effectivePolicy: EffectiveTradePolicy;
  orderPlan: OrderPlan | null;
  eligibility: {
    eligible: boolean;
    orderSubmissionRequired: boolean;
    submissionSeparated: true;
  };
  explanation: string;
};

export type OrderValidationRequest = {
  orderPlan: OrderPlan;
  gateDecision?: GlobalGateDecision | null;
};

export type OrderValidationPayload = {
  eligible: boolean;
  validationErrors: string[];
  submissionSeparated: true;
  explanation: string;
};

export type BacktestRunPayload = {
  backtestId: string;
  result: Record<string, unknown>;
  snapshotsRecorded: number;
};

export type ModelStatusPayload = {
  metaModel: Record<string, unknown>;
  forecastModel: Record<string, unknown>;
};

export function evaluateFeaturesV2(
  request: FeatureEvaluateRequest,
  client: ApiClient = defaultApiClient,
): Promise<ApiV2Envelope<{ featureSnapshot: Record<string, unknown> }>> {
  return postApiV2("/api/v2/features/evaluate", request, client);
}

export function evaluatePaperDecisionV2(
  request: ReplayDecisionEvaluateRequest,
  client: ApiClient = defaultApiClient,
): Promise<ApiV2Envelope<PaperDecisionPayload>> {
  return postApiV2("/api/v2/paper-decisions/evaluate", request, client);
}

export function validateOrderV2(
  request: OrderValidationRequest,
  client: ApiClient = defaultApiClient,
): Promise<ApiV2Envelope<OrderValidationPayload>> {
  return postApiV2("/api/v2/orders/validate", request, client);
}

export function runBacktestV2(
  request: BacktestRunRequest,
  client: ApiClient = defaultApiClient,
): Promise<ApiV2Envelope<BacktestRunPayload>> {
  return postApiV2("/api/v2/backtests/run", request, client);
}

export function getBacktestV2(
  backtestId: string,
  client: ApiClient = defaultApiClient,
): Promise<ApiV2Envelope<BacktestRunPayload>> {
  return getApiV2(`/api/v2/backtests/${encodeURIComponent(backtestId)}`, client);
}

export function getModelStatusV2(client: ApiClient = defaultApiClient): Promise<ApiV2Envelope<ModelStatusPayload>> {
  return getApiV2("/api/v2/models/status", client);
}

export function getDecisionSnapshotV2(
  snapshotId: string,
  client: ApiClient = defaultApiClient,
): Promise<ApiV2Envelope<{ snapshot: DecisionSnapshotV2 }>> {
  return getApiV2(`/api/v2/decision-snapshots/${encodeURIComponent(snapshotId)}`, client);
}

async function postApiV2<TRequest, TPayload>(
  path: string,
  request: TRequest,
  client: ApiClient,
): Promise<ApiV2Envelope<TPayload>> {
  return requestApiV2(path, client, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });
}

async function getApiV2<TPayload>(path: string, client: ApiClient): Promise<ApiV2Envelope<TPayload>> {
  return requestApiV2(path, client, { method: "GET" });
}

async function requestApiV2<TPayload>(
  path: string,
  client: ApiClient,
  init: RequestInit,
): Promise<ApiV2Envelope<TPayload>> {
  const response = await client.fetch(`${client.baseUrl}${path}`, init);
  const body = await response.json();
  if (!response.ok) {
    throw new Error(body?.detail ?? body?.explanation ?? `API V2 request failed with ${response.status}`);
  }
  return body as ApiV2Envelope<TPayload>;
}
