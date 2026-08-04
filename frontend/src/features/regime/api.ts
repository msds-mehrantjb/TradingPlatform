import { API_BASE, type ApiClient, defaultApiClient } from "../../api/client";

type RegimeJobEnvelope<T> = {
  algorithmId?: string;
  jobId?: string;
  status?: "queued" | "running" | "completed" | "failed";
  result?: T | null;
  failureMessage?: string | null;
};

type RegimeSettingsCommand = {
  actor?: string;
  identity?: Record<string, unknown>;
  settings?: Record<string, unknown>;
  settingsSnapshot?: Record<string, unknown>;
  settingsVersion?: string;
  targetSettingsVersion?: string;
  rollbackToSettingsVersion?: string;
  reason?: string;
  activationReason?: string;
  rollbackReason?: string;
};

type RegimeAutomaticPaperControlCommand = {
  enabled: boolean;
  actor: string;
  reason: string;
};

const AUTHORITATIVE_REGIME_PAYLOAD_KEYS = new Set([
  "settings",
  "settingsSnapshot",
  "account",
  "accountSnapshot",
  "accountState",
  "buyingPowerState",
  "position",
  "currentPosition",
  "positionState",
  "positionSnapshot",
  "positions",
  "inventory",
  "inventorySnapshot",
  "inventoryState",
  "authoritativeInventory",
  "ownedInventory",
  "availableBuyingPower",
  "availableRisk",
  "buyingPower",
  "dailyPnl",
  "remainingAlgorithmRiskDollars",
  "globalRiskCapacityQuantity",
  "authoritativeDecision",
  "authoritativeRuntime",
  "authoritativeEngine",
  "regimeClassification",
  "classification",
  "classifierOutput",
  "hysteresis",
  "hysteresisState",
  "strategyOutputs",
  "strategyRouting",
  "strategyEvaluation",
  "strategyEvaluations",
  "familyAggregation",
  "weights",
  "strategyWeights",
  "calculatedWeights",
  "finalSignal",
  "finalDecision",
  "finalTradeDecision",
  "signal",
  "sizing",
  "sizingResult",
  "orderQuantity",
  "quantity",
  "orderIntent",
  "orderProposal",
  "exitDecision",
  "brokerOrder",
  "brokerRequest",
  "brokerSubmission",
  "brokerSubmissionResult",
  "submittedOrder",
  "orderSubmission",
  "paperOrder",
  "fill",
  "fills",
  "decisionResult",
  "backtestResult",
]);
const DIRECT_EVALUATION_DATA_KEYS = new Set([
  "marketData",
  "candles",
  "bars",
  "quotes",
  "latestBar",
  "features",
  "classificationInput",
]);
const REGIME_PROMOTION_EVIDENCE_KEYS = new Set([
  "evidence",
  "promotionEvidence",
  "promotion_evidence",
  "readinessEvidence",
  "readiness_evidence",
  "paperStabilityEvidence",
  "paper_stability_evidence",
]);

export async function runRegimeBacktestOnBackend<T>(
  payload: Record<string, unknown>,
  client: ApiClient = defaultApiClient,
): Promise<T> {
  assertRegimeTransportOnlyPayload(payload, "backtest");
  const response = await client.fetch(`${client.baseUrl || API_BASE}/api/regime/backtests/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Backend Regime backtest failed: ${response.status}`);
  }
  return await unwrapRegimeJobResponse<T>(await response.json(), client);
}

export async function evaluateRegimeOnBackend<T>(
  payload: Record<string, unknown>,
  client: ApiClient = defaultApiClient,
): Promise<T> {
  assertRegimeTransportOnlyPayload(payload, "evaluation");
  const response = await client.fetch(`${client.baseUrl || API_BASE}/api/regime/evaluate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Backend Regime evaluation failed: ${response.status}`);
  }
  return await unwrapRegimeJobResponse<T>(await response.json(), client);
}

export async function readLatestRegimeDecisionFromBackend<T>(client: ApiClient = defaultApiClient): Promise<T> {
  const response = await client.fetch(`${client.baseUrl || API_BASE}/api/regime/runtime/latest-decision`);
  if (!response.ok) {
    throw new Error(`Backend Regime latest decision read failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function fetchRegimeRuntimeStatus<T>(client: ApiClient = defaultApiClient): Promise<T> {
  const response = await client.fetch(`${client.baseUrl || API_BASE}/api/regime/runtime/status`);
  if (!response.ok) {
    throw new Error(`Backend Regime runtime status read failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function createRegimeSettingsVersion<T>(
  payload: RegimeSettingsCommand,
  client: ApiClient = defaultApiClient,
): Promise<T> {
  return await submitRegimeSettingsCommand<T>("/api/regime/settings/versions/create", payload, client);
}

export async function validateRegimeSettingsVersion<T>(
  payload: RegimeSettingsCommand,
  client: ApiClient = defaultApiClient,
): Promise<T> {
  return await submitRegimeSettingsCommand<T>("/api/regime/settings/versions/validate", payload, client);
}

export async function activateRegimeSettingsVersion<T>(
  payload: RegimeSettingsCommand,
  client: ApiClient = defaultApiClient,
): Promise<T> {
  return await submitRegimeSettingsCommand<T>("/api/regime/settings/versions/activate", payload, client);
}

export async function rollbackRegimeSettingsVersion<T>(
  payload: RegimeSettingsCommand,
  client: ApiClient = defaultApiClient,
): Promise<T> {
  return await submitRegimeSettingsCommand<T>("/api/regime/settings/versions/rollback", payload, client);
}

export async function setRegimeAutomaticPaperTrading<T>(
  payload: RegimeAutomaticPaperControlCommand,
  client: ApiClient = defaultApiClient,
): Promise<T> {
  assertRegimeControlPayload(payload, "automatic paper control");
  const response = await client.fetch(`${client.baseUrl || API_BASE}/api/regime/rollout/automatic-paper`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Backend Regime automatic paper control failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function readActiveRegimeSettings<T>(
  payload: Pick<RegimeSettingsCommand, "identity"> & Record<string, unknown>,
  client: ApiClient = defaultApiClient,
): Promise<T> {
  assertRegimeReadPayload(payload, "active settings read");
  const response = await client.fetch(`${client.baseUrl || API_BASE}/api/regime/settings/active`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Backend Regime active settings read failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

async function submitRegimeSettingsCommand<T>(
  path: string,
  payload: RegimeSettingsCommand,
  client: ApiClient,
): Promise<T> {
  const response = await client.fetch(`${client.baseUrl || API_BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Backend Regime settings command failed: ${response.status}`);
  }
  return await unwrapRegimeJobResponse<T>(await response.json(), client);
}

function assertRegimeTransportOnlyPayload(payload: Record<string, unknown>, kind: "evaluation" | "backtest") {
  const forbiddenPath = findRegimeForbiddenPayloadPath(payload, AUTHORITATIVE_REGIME_PAYLOAD_KEYS);
  if (forbiddenPath) {
    throw new Error(`Regime ${kind} payload cannot submit authoritative ${forbiddenPath}; backend workers own decisions`);
  }
  if (kind === "evaluation") {
    const directDataPath = findRegimeForbiddenPayloadPath(payload, DIRECT_EVALUATION_DATA_KEYS);
    if (directDataPath) {
      throw new Error(`Regime evaluation payload cannot submit ${directDataPath}; use a trusted finalized-bar reference`);
    }
  }
}

function assertRegimeControlPayload(payload: Record<string, unknown>, kind: string) {
  const evidencePath = findRegimeForbiddenPayloadPath(payload, REGIME_PROMOTION_EVIDENCE_KEYS);
  if (evidencePath) {
    throw new Error(`Regime ${kind} cannot submit promotion evidence at ${evidencePath}; backend workers own rollout evidence`);
  }
  const authoritativePath = findRegimeForbiddenPayloadPath(payload, AUTHORITATIVE_REGIME_PAYLOAD_KEYS);
  if (authoritativePath) {
    throw new Error(`Regime ${kind} cannot submit authoritative ${authoritativePath}; backend workers own decisions`);
  }
}

function assertRegimeReadPayload(payload: Record<string, unknown>, kind: string) {
  const authoritativePath = findRegimeForbiddenPayloadPath(payload, AUTHORITATIVE_REGIME_PAYLOAD_KEYS);
  if (authoritativePath) {
    throw new Error(`Regime ${kind} cannot submit authoritative ${authoritativePath}; backend workers own decisions`);
  }
}

function findRegimeForbiddenPayloadPath(payload: unknown, forbidden: Set<string>, path = ""): string | null {
  if (!payload || typeof payload !== "object") {
    return null;
  }
  if (Array.isArray(payload)) {
    for (let index = 0; index < payload.length; index += 1) {
      const nested = findRegimeForbiddenPayloadPath(payload[index], forbidden, `${path}[${index}]`);
      if (nested) {
        return nested;
      }
    }
    return null;
  }
  for (const [key, value] of Object.entries(payload as Record<string, unknown>)) {
    const currentPath = path ? `${path}.${key}` : key;
    if (forbidden.has(key)) {
      return currentPath;
    }
    const nested = findRegimeForbiddenPayloadPath(value, forbidden, currentPath);
    if (nested) {
      return nested;
    }
  }
  return null;
}

async function unwrapRegimeJobResponse<T>(body: unknown, client: ApiClient): Promise<T> {
  const envelope = body as RegimeJobEnvelope<T>;
  if (!envelope || envelope.algorithmId !== "regime" || !envelope.jobId || !envelope.status) {
    return body as T;
  }
  if (envelope.status === "completed" && envelope.result) {
    return envelope.result;
  }
  if (envelope.status === "failed") {
    throw new Error(envelope.failureMessage || "Backend Regime job failed closed");
  }
  return await pollRegimeJob<T>(envelope.jobId, client);
}

async function pollRegimeJob<T>(jobId: string, client: ApiClient): Promise<T> {
  const baseUrl = client.baseUrl || API_BASE;
  for (let attempt = 0; attempt < 80; attempt += 1) {
    await waitForRegimeJobPoll(attempt < 10 ? 100 : 250);
    const response = await client.fetch(`${baseUrl}/api/regime/jobs/${encodeURIComponent(jobId)}`);
    if (!response.ok) {
      throw new Error(`Backend Regime job status failed: ${response.status}`);
    }
    const envelope = (await response.json()) as RegimeJobEnvelope<T>;
    if (envelope.status === "completed" && envelope.result) {
      return envelope.result;
    }
    if (envelope.status === "failed") {
      throw new Error(envelope.failureMessage || "Backend Regime job failed closed");
    }
  }
  throw new Error("Backend Regime job did not finish before the UI polling timeout");
}

function waitForRegimeJobPoll(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
