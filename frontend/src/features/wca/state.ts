import type { WcaBacktestResult, WcaBaselineSettings, WcaConfigurationResponse, WcaDecision, WcaStatusResponse } from "./types";

export type WcaPresentationStatus = "idle" | "loading" | "ready" | "error";

export type WcaPresentationState = {
  status: WcaPresentationStatus;
  error: string | null;
  backendStatus: WcaStatusResponse | null;
  configuration: WcaConfigurationResponse | null;
  baselineSettings: WcaBaselineSettings | null;
  latestDecision: WcaDecision | null;
  latestBacktest: WcaBacktestResult | null;
  configurationSaveStatus: WcaPresentationStatus;
  configurationSaveError: string | null;
};

export function createInitialWcaState(): WcaPresentationState {
  return {
    status: "idle",
    error: null,
    backendStatus: null,
    configuration: null,
    baselineSettings: null,
    latestDecision: null,
    latestBacktest: null,
    configurationSaveStatus: "idle",
    configurationSaveError: null,
  };
}

export function withWcaLoading(state: WcaPresentationState): WcaPresentationState {
  return { ...state, status: "loading", error: null };
}

export function withWcaReady(
  state: WcaPresentationState,
  payload: Partial<Omit<WcaPresentationState, "status" | "error" | "configurationSaveStatus" | "configurationSaveError">>,
): WcaPresentationState {
  return { ...state, ...payload, status: "ready", error: null };
}

export function withWcaError(state: WcaPresentationState, error: unknown): WcaPresentationState {
  return { ...state, status: "error", error: error instanceof Error ? error.message : String(error) };
}

export function withWcaConfigurationSaving(state: WcaPresentationState): WcaPresentationState {
  return { ...state, configurationSaveStatus: "loading", configurationSaveError: null };
}

export function withWcaConfigurationSaved(state: WcaPresentationState, configuration: WcaConfigurationResponse): WcaPresentationState {
  return { ...state, configuration, configurationSaveStatus: "ready", configurationSaveError: null };
}

export function withWcaConfigurationSaveError(state: WcaPresentationState, error: unknown): WcaPresentationState {
  return {
    ...state,
    configurationSaveStatus: "error",
    configurationSaveError: error instanceof Error ? error.message : String(error),
  };
}

