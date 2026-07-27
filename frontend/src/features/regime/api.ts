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
  targetSettingsVersion?: string;
  rollbackToSettingsVersion?: string;
};

const AUTHORITATIVE_REGIME_PAYLOAD_KEYS = new Set([
  "settings",
  "settingsSnapshot",
  "account",
  "accountSnapshot",
  "position",
  "positionState",
  "positions",
  "inventory",
  "inventorySnapshot",
  "availableBuyingPower",
  "remainingAlgorithmRiskDollars",
  "globalRiskCapacityQuantity",
  "authoritativeDecision",
  "authoritativeRuntime",
  "authoritativeEngine",
  "classification",
  "hysteresis",
  "hysteresisState",
  "strategyRouting",
  "finalSignal",
  "signal",
  "sizing",
  "orderIntent",
  "orderProposal",
  "exitDecision",
  "brokerSubmission",
  "decisionResult",
  "backtestResult",
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

export async function readActiveRegimeSettings<T>(
  payload: Pick<RegimeSettingsCommand, "identity"> & Record<string, unknown>,
  client: ApiClient = defaultApiClient,
): Promise<T> {
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
  for (const key of AUTHORITATIVE_REGIME_PAYLOAD_KEYS) {
    if (Object.prototype.hasOwnProperty.call(payload, key)) {
      throw new Error(`Regime ${kind} payload cannot submit authoritative ${key}; backend workers own decisions`);
    }
  }
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
