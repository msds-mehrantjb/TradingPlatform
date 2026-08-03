import type { WcaBacktestResult, WcaBaselineSettings, WcaConfigurationResponse, WcaDecision, WcaRuntimeControl, WcaStatusResponse } from "./types";

export type WcaPresentationStatus = "idle" | "loading" | "ready" | "error";
export type WcaRuntimeControlStatus = "idle" | "loading" | "ready" | "blocked" | "error";

export type WcaPresentationState = {
  status: WcaPresentationStatus;
  error: string | null;
  backendStatus: WcaStatusResponse | null;
  runtimeControl: WcaRuntimeControl | null;
  runtimeControlStatus: WcaRuntimeControlStatus;
  runtimeControlError: string | null;
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
    runtimeControl: null,
    runtimeControlStatus: "idle",
    runtimeControlError: null,
    configuration: null,
    baselineSettings: null,
    latestDecision: null,
    latestBacktest: null,
    configurationSaveStatus: "idle",
    configurationSaveError: null,
  };
}

export function withWcaLoading(state: WcaPresentationState): WcaPresentationState {
  return { ...state, status: "loading", error: null, runtimeControlStatus: state.runtimeControl ? state.runtimeControlStatus : "loading" };
}

export function withWcaReady(
  state: WcaPresentationState,
  payload: Partial<Omit<WcaPresentationState, "status" | "error" | "configurationSaveStatus" | "configurationSaveError">>,
): WcaPresentationState {
  const runtimeControl = payload.runtimeControl ?? payload.backendStatus?.runtimeControl ?? payload.backendStatus?.runtime_control ?? state.runtimeControl;
  const effectiveEntries = Boolean(runtimeControl?.effectiveAutomaticEntriesEnabled ?? runtimeControl?.effective_automatic_entries_enabled);
  const requestedPaper = Boolean(runtimeControl?.paperTradingRequested ?? runtimeControl?.paper_trading_requested);
  return {
    ...state,
    ...payload,
    runtimeControl,
    runtimeControlStatus: requestedPaper && !effectiveEntries ? "blocked" : "ready",
    runtimeControlError: null,
    status: "ready",
    error: null,
  };
}

export function withWcaError(state: WcaPresentationState, error: unknown): WcaPresentationState {
  const message = error instanceof Error ? error.message : String(error);
  return {
    ...state,
    backendStatus: null,
    runtimeControl: failClosedWcaRuntimeControl(message),
    runtimeControlStatus: "error",
    runtimeControlError: message,
    status: "error",
    error: message,
  };
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

export function withWcaRuntimeControlLoading(state: WcaPresentationState): WcaPresentationState {
  return { ...state, runtimeControlStatus: "loading", runtimeControlError: null };
}

export function withWcaRuntimeControlReady(state: WcaPresentationState, runtimeControl: WcaRuntimeControl): WcaPresentationState {
  const requestedPaper = Boolean(runtimeControl.paperTradingRequested ?? runtimeControl.paper_trading_requested);
  const effectiveEntries = Boolean(runtimeControl.effectiveAutomaticEntriesEnabled ?? runtimeControl.effective_automatic_entries_enabled);
  return {
    ...state,
    runtimeControl,
    runtimeControlStatus: requestedPaper && !effectiveEntries ? "blocked" : "ready",
    runtimeControlError: null,
    backendStatus: state.backendStatus ? { ...state.backendStatus, runtimeControl } : state.backendStatus,
  };
}

export function withWcaRuntimeControlError(state: WcaPresentationState, error: unknown): WcaPresentationState {
  const message = error instanceof Error ? error.message : String(error);
  const runtimeControl = failClosedWcaRuntimeControl(message);
  return {
    ...state,
    runtimeControl,
    runtimeControlStatus: "error",
    runtimeControlError: message,
    backendStatus: state.backendStatus ? { ...state.backendStatus, runtimeControl } : state.backendStatus,
  };
}

export function failClosedWcaRuntimeControl(reason: string): WcaRuntimeControl {
  return {
    algorithmId: "wca",
    algorithm_id: "wca",
    brokerAccountId: "paper",
    broker_account_id: "paper",
    symbol: "SPY",
    paperTradingRequested: false,
    paper_trading_requested: false,
    automaticEntriesRequested: false,
    automatic_entries_requested: false,
    pauseNewEntries: true,
    pause_new_entries: true,
    killSwitchOpen: true,
    kill_switch_open: true,
    effectivePaperTradingEnabled: false,
    effective_paper_trading_enabled: false,
    effectiveAutomaticEntriesEnabled: false,
    effective_automatic_entries_enabled: false,
    automaticPaperPermitted: false,
    automatic_paper_permitted: false,
    automaticEntryCurrentlyPermitted: false,
    automatic_entry_currently_permitted: false,
    paperAccountVerified: false,
    paper_account_verified: false,
    reason: reason || "WCA backend unreachable",
    reasonCodes: ["wca.frontend.backend_unreachable_fail_closed", reason || "wca.frontend.backend_unreachable"],
    reason_codes: ["wca.frontend.backend_unreachable_fail_closed", reason || "wca.frontend.backend_unreachable"],
  };
}
