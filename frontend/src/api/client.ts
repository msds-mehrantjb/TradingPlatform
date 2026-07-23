export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export const BACKTEST_API_CANDIDATES = ["http://127.0.0.1:8020", API_BASE].filter(
  (value, index, values) => values.indexOf(value) === index,
);

export const TRADING_ALGORITHM_INVENTORY_ENDPOINTS = {
  votingEnsemble: "/api/v2/algorithms/voting-ensemble/inventory",
  metaStrategy: "/api/v2/algorithms/meta-strategy/inventory",
  regime: "/api/v2/algorithms/regime/inventory",
  wca: "/api/v2/algorithms/wca/inventory",
  weightedVoting: "/api/v2/algorithms/weighted-voting/inventory",
} as const;

export type TradingAlgorithmInventoryKey = keyof typeof TRADING_ALGORITHM_INVENTORY_ENDPOINTS;

export type ApiClient = {
  baseUrl: string;
  fetch: typeof fetch;
};

export const defaultApiClient: ApiClient = {
  baseUrl: API_BASE,
  fetch,
};
