export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export const BACKTEST_API_CANDIDATES = ["http://127.0.0.1:8020", API_BASE].filter(
  (value, index, values) => values.indexOf(value) === index,
);

export type ApiClient = {
  baseUrl: string;
  fetch: typeof fetch;
};

export const defaultApiClient: ApiClient = {
  baseUrl: API_BASE,
  fetch,
};

