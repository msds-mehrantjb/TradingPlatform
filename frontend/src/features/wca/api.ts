import { API_BASE, type ApiClient, defaultApiClient } from "../../api/client";
import type {
  WcaBacktestResult,
  WcaBaselineSettings,
  WcaCommandReceipt,
  WcaConfigurationResponse,
  WcaDecision,
  WcaDecisionsResponse,
  WcaStatusResponse,
} from "./types";

async function requestJson<T>(
  path: string,
  options: RequestInit = {},
  client: ApiClient = defaultApiClient,
): Promise<T> {
  const response = await client.fetch(`${client.baseUrl || API_BASE}${path}`, {
    ...options,
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers ?? {}),
    },
  });
  if (!response.ok) {
    throw new Error(`WCA backend request failed (${response.status}): ${await response.text()}`);
  }
  return (await response.json()) as T;
}

export function fetchWcaStatus(client?: ApiClient): Promise<WcaStatusResponse> {
  return requestJson<WcaStatusResponse>("/api/wca/status", {}, client);
}

export function fetchWcaConfiguration(client?: ApiClient): Promise<WcaConfigurationResponse> {
  return requestJson<WcaConfigurationResponse>("/api/wca/configuration", {}, client);
}

export function fetchWcaBaselineSettings(client?: ApiClient): Promise<WcaBaselineSettings> {
  return requestJson<WcaBaselineSettings>("/api/wca/config/baseline", {}, client);
}

export async function fetchLatestWcaDecision(client?: ApiClient): Promise<WcaDecision | null> {
  const response = await requestJson<WcaDecisionsResponse>("/api/wca/decisions?limit=1", {}, client);
  return Array.isArray(response.decisions) ? response.decisions[0] ?? null : null;
}

export function updateWcaConfiguration(
  configuration: Partial<WcaConfigurationResponse>,
  client?: ApiClient,
): Promise<WcaCommandReceipt> {
  return requestJson<WcaCommandReceipt>(
    "/api/wca/configuration",
    {
      method: "PUT",
      body: JSON.stringify(configuration),
    },
    client,
  );
}

export function runWcaBacktest(payload: Record<string, unknown>, client?: ApiClient): Promise<WcaCommandReceipt> {
  return requestJson<WcaCommandReceipt>(
    "/api/wca/backtests",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    client,
  );
}

export function runWcaPreparedBacktest(payload: Record<string, unknown>, client?: ApiClient): Promise<WcaCommandReceipt & { preparedData?: Record<string, unknown> }> {
  return requestJson<WcaCommandReceipt & { preparedData?: Record<string, unknown> }>(
    "/api/wca/backtests/prepared-data",
    {
      method: "POST",
      body: JSON.stringify(payload),
    },
    client,
  );
}

export function fetchWcaBacktest(runId: string, client?: ApiClient): Promise<WcaBacktestResult> {
  return requestJson<WcaBacktestResult>(`/api/wca/backtests/${encodeURIComponent(runId)}`, {}, client);
}

export function fetchWcaBacktestStatus(runId: string, client?: ApiClient): Promise<Record<string, unknown>> {
  return requestJson<Record<string, unknown>>(`/api/wca/backtests/${encodeURIComponent(runId)}/status`, {}, client);
}
