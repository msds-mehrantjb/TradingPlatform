import { API_BASE, type ApiClient, defaultApiClient } from "../../api/client";

export async function runRegimeBacktestOnBackend<T>(
  payload: Record<string, unknown>,
  client: ApiClient = defaultApiClient,
): Promise<T> {
  const response = await client.fetch(`${client.baseUrl || API_BASE}/api/regime/backtests/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Backend Regime backtest failed: ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function evaluateRegimeOnBackend<T>(
  payload: Record<string, unknown>,
  client: ApiClient = defaultApiClient,
): Promise<T> {
  const response = await client.fetch(`${client.baseUrl || API_BASE}/api/regime/evaluate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw new Error(`Backend Regime evaluation failed: ${response.status}`);
  }
  return (await response.json()) as T;
}
