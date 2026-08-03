import { escapeHtml, formatNumber, stringValue } from "./formatters";
import { renderWcaBacktestPanel } from "./WcaBacktestPanel";
import { renderWcaDynamicProfilePanel } from "./WcaDynamicProfilePanel";
import { renderWcaGatePanel } from "./WcaGatePanel";
import { renderWcaOrderPanel } from "./WcaOrderPanel";
import { renderWcaSettingsPanel } from "./WcaSettingsPanel";
import type { WcaConfigurationResponse } from "./types";
import type { WcaPresentationState } from "./state";

export type WcaPanelOptions = {
  onConfigurationSubmit?: (configuration: Partial<WcaConfigurationResponse>) => void;
};

export function renderWcaPanel(container: HTMLElement, state: WcaPresentationState, options: WcaPanelOptions = {}): void {
  container.innerHTML = renderWcaPanelHtml(state);
  bindConfigurationForm(container, state, options);
}

export function renderWcaPanelHtml(state: WcaPresentationState): string {
  const decision = state.latestDecision;
  return `
    <div class="wca-presentation-panel" data-wca-presentation-layer="backend">
      ${renderWcaSettingsPanel(state.configuration, state.baselineSettings, decision)}
      ${renderRuntimeControlSurface(state)}
      ${renderWcaOrderPanel(decision)}
      ${renderWcaDynamicProfilePanel(decision)}
      ${renderWcaGatePanel(decision)}
      ${renderWcaBacktestPanel(state.latestBacktest, backtestStatusFor(state.status), state.error)}
    </div>
  `;
}

function renderRuntimeControlSurface(state: WcaPresentationState): string {
  const status = state.backendStatus;
  const runtime = (status?.runtimeHealth ?? status?.runtime_health ?? {}) as Record<string, unknown>;
  const control = (state.runtimeControl ?? status?.runtimeControl ?? status?.runtime_control ?? {}) as Record<string, unknown>;
  const api = (status?.apiHealth ?? status?.api_health ?? {}) as Record<string, unknown>;
  const versions = (status?.activeVersions ?? status?.active_versions ?? {}) as Record<string, unknown>;
  const rollout = (status?.rollout ?? {}) as Record<string, unknown>;
  const observability = (status?.observability ?? {}) as Record<string, unknown>;
  const inventory = (status?.virtualInventory ?? status?.virtual_inventory ?? {}) as Record<string, unknown>;
  const position = childRecord(observability, "currentWcaPosition") ?? firstRecord(inventory.positions) ?? inventory;
  const lastOrder = childRecord(observability, "lastOrder");
  const lastFill = childRecord(observability, "lastFill");
  const lastDecision = childRecord(observability, "lastDecision") ?? (decisionRecord(state.latestDecision) ?? null);
  const lastFinalizedBar = childRecord(observability, "lastFinalizedBar");
  const reconciliation = childRecord(observability, "reconciliationStatus");
  const paperOnly = status?.paperOnly ?? status?.paper_only;
  const runtimeStatus = stringValue(runtime.status, "unknown");
  const paperRequested = Boolean(control.paperTradingRequested ?? control.paper_trading_requested);
  const effectivePaper = Boolean(control.effectivePaperTradingEnabled ?? control.effective_paper_trading_enabled);
  const effectiveAuto = Boolean(control.effectiveAutomaticEntriesEnabled ?? control.effective_automatic_entries_enabled);
  const paperVerified = Boolean(control.paperAccountVerified ?? control.paper_account_verified);
  const marketOpen = dependencyHealthy(control, "market_open");
  const dependencyHealth = (control.dependencyHealth ?? control.dependency_health ?? {}) as Record<string, unknown>;
  const controlReasons = reasonCodes(control);
  const dependencyReasons = Object.values(dependencyHealth).flatMap((value) => reasonCodes(value as Record<string, unknown>));
  const activeBlockingReasons = [...new Set([...(state.runtimeControlError ? ["wca.frontend.backend_unreachable_fail_closed", state.runtimeControlError] : []), ...controlReasons, ...dependencyReasons])].filter(Boolean);
  const runtimeTone = runtimeStatus.toLowerCase().includes("critical") || runtimeStatus.toLowerCase().includes("blocked") ? "block" : runtimeStatus.toLowerCase().includes("unknown") ? "warn" : "pass";
  const paperTone = state.runtimeControlStatus === "error" ? "block" : effectivePaper ? "pass" : paperRequested ? "warn" : "neutral";
  const entriesTone = effectiveAuto ? "pass" : "block";
  const brokerTone = paperVerified ? "pass" : "warn";
  const marketTone = marketOpen ? "pass" : "warn";
  return `
    <details class="wca-expander">
      <summary class="algo-expand-toggle wca-expand-summary">
        <span>Runtime Control Surface</span>
        <strong>${escapeHtml(wcaPaperLabel(state.runtimeControlStatus, paperRequested, effectivePaper, effectiveAuto))}</strong>
        <b>+</b>
      </summary>
      <div class="wca-expander-body">
        <div class="regime-summary-grid wca-summary-grid">
          <div class="regime-summary-row" data-tone="pass"><b>API health</b><span>${escapeHtml(stringValue(api.status, status?.status, "unknown"))}</span></div>
          <div class="regime-summary-row" data-tone="${runtimeTone}"><b>Runtime process</b><span>${escapeHtml(runtimeStatus)}</span></div>
          <div class="regime-summary-row" data-tone="${paperTone}"><b>Requested Paper</b><span>${paperRequested ? "ON" : "OFF"}</span></div>
          <div class="regime-summary-row" data-tone="${paperTone}"><b>Effective Paper</b><span>${effectivePaper ? "ON" : "OFF"}</span></div>
          <div class="regime-summary-row" data-tone="${entriesTone}"><b>Automatic entries</b><span>${effectiveAuto ? "ARMED" : "BLOCKED"}</span></div>
          <div class="regime-summary-row" data-tone="${marketTone}"><b>Market</b><span>${marketOpen ? "OPEN" : "CLOSED"}</span></div>
          <div class="regime-summary-row" data-tone="info"><b>Rollout stage</b><span>${escapeHtml(stringValue(rollout.stage, rollout.rolloutStage, rollout.currentStage, rollout.status, "unavailable"))}</span></div>
          <div class="regime-summary-row" data-tone="${brokerTone}"><b>Broker paper verification</b><span>${paperVerified ? "VERIFIED" : "BLOCKED"}</span></div>
          <div class="regime-summary-row" data-tone="neutral"><b>Reconciliation status</b><span>${escapeHtml(stringValue(reconciliation?.status, reconciliation?.reconciliation_status, "not run"))}</span></div>
          <div class="regime-summary-row" data-tone="${runtimeTone}"><b>Runtime health</b><span>${escapeHtml(runtimeStatus)}</span></div>
          <div class="regime-summary-row" data-tone="neutral"><b>Last finalized bar</b><span>${escapeHtml(describeRecord(lastFinalizedBar, ["timestamp", "finalizedAt", "finalized_at", "barEndTimestamp", "lastProcessedBar"]))}</span></div>
          <div class="regime-summary-row" data-tone="neutral"><b>Last decision</b><span>${escapeHtml(describeRecord(lastDecision, ["decisionId", "decision_id", "signal", "decisionLabel", "decision_label"]))}</span></div>
          <div class="regime-summary-row" data-tone="neutral"><b>Last order</b><span>${escapeHtml(describeRecord(lastOrder, ["client_order_id", "clientOrderId", "order_intent_id", "orderIntentId", "status"]))}</span></div>
          <div class="regime-summary-row" data-tone="neutral"><b>Last fill</b><span>${escapeHtml(describeRecord(lastFill, ["fill_id", "fillId", "broker_order_id", "brokerOrderId", "quantity", "filled_quantity"]))}</span></div>
          <div class="regime-summary-row" data-tone="neutral"><b>Current WCA position</b><span>${escapeHtml(describePosition(position))}</span></div>
        </div>
        <div class="wca-config-meta">
          <span>Config: ${escapeHtml(stringValue(versions.configuration, status?.configurationVersion, status?.configuration_version, "unavailable"))}</span>
          <span>Weights: ${escapeHtml(stringValue(versions.weight, "unavailable"))}</span>
          <span>Calibration: ${escapeHtml(Array.isArray(versions.calibrations) ? versions.calibrations.join(", ") || "none active" : "unavailable")}</span>
          <span>Inventory scope: WCA isolated</span>
          <span>Paper-only: ${paperOnly ? "yes" : "unavailable"}</span>
          <span>Control revision: ${escapeHtml(stringValue(control.controlRevision, control.control_revision, "unavailable"))}</span>
        </div>
        <div class="wca-note">
          Active blocking reasons: ${escapeHtml(activeBlockingReasons.length ? activeBlockingReasons.slice(0, 12).join(", ") : "none")}
        </div>
      </div>
    </details>
  `;
}

function wcaPaperLabel(status: WcaPresentationState["runtimeControlStatus"], requested: boolean, effectivePaper: boolean, effectiveEntries: boolean): string {
  if (status === "loading") {
    return "Paper Requested";
  }
  if (status === "error") {
    return "Paper Error";
  }
  if (!requested) {
    return "Paper Off";
  }
  if (effectivePaper && effectiveEntries) {
    return "Paper Effective";
  }
  return "Paper Blocked";
}

function childRecord(parent: Record<string, unknown>, key: string): Record<string, unknown> | null {
  const value = parent[key];
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function firstRecord(value: unknown): Record<string, unknown> | null {
  if (!Array.isArray(value)) {
    return null;
  }
  const first = value[0];
  return first && typeof first === "object" && !Array.isArray(first) ? first as Record<string, unknown> : null;
}

function decisionRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
}

function reasonCodes(record: Record<string, unknown> | null | undefined): string[] {
  const raw = record?.reasonCodes ?? record?.reason_codes;
  return Array.isArray(raw) ? raw.map((value) => String(value)) : [];
}

function dependencyHealthy(control: Record<string, unknown>, key: string): boolean {
  const dependencies = (control.dependencyHealth ?? control.dependency_health ?? {}) as Record<string, unknown>;
  const record = dependencies[key];
  if (!record || typeof record !== "object" || Array.isArray(record)) {
    return false;
  }
  return Boolean((record as Record<string, unknown>).healthy);
}

function describeRecord(record: Record<string, unknown> | null, preferredFields: string[]): string {
  if (!record) {
    return "none";
  }
  for (const field of preferredFields) {
    const value = record[field];
    if (value !== undefined && value !== null && String(value) !== "") {
      return String(value);
    }
  }
  return stringValue(record.status, record.state, record.id, "available");
}

function describePosition(position: Record<string, unknown> | null): string {
  if (!position) {
    return "0 SPY";
  }
  const quantity = stringValue(position.openQuantity, position.open_quantity, position.quantity, "0");
  const symbol = stringValue(position.symbol, "SPY");
  const average = stringValue(position.averageEntryPrice, position.average_entry_price, "");
  return average ? `${quantity} ${symbol} @ ${average}` : `${quantity} ${symbol}`;
}

function backtestStatusFor(status: WcaPresentationState["status"]): string {
  if (status === "loading") {
    return "loading";
  }
  if (status === "error") {
    return "error";
  }
  return "ready";
}

function bindConfigurationForm(container: HTMLElement, state: WcaPresentationState, options: WcaPanelOptions) {
  const form = container.querySelector<HTMLFormElement>("[data-wca-baseline-form='true']");
  if (!form || !options.onConfigurationSubmit) {
    return;
  }
  const onConfigurationSubmit = options.onConfigurationSubmit;
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const payload: Partial<WcaConfigurationResponse> = {
      decisionSettings: { ...(state.configuration?.decisionSettings ?? state.configuration?.decision_settings ?? {}) },
      tradingSettings: { ...(state.configuration?.tradingSettings ?? state.configuration?.trading_settings ?? {}) },
    };
    const data = new FormData(form);
    for (const [key, value] of data.entries()) {
      const [group, field] = key.split(".");
      if (!field || (group !== "decisionSettings" && group !== "tradingSettings")) {
        continue;
      }
      const parsed = parseConfigurationInput(value);
      (payload[group] as Record<string, unknown>)[field] = parsed;
    }
    onConfigurationSubmit(payload);
  });
}

function parseConfigurationInput(value: FormDataEntryValue): unknown {
  const text = String(value).trim();
  if (text === "true") {
    return true;
  }
  if (text === "false") {
    return false;
  }
  const numeric = Number(text);
  return Number.isFinite(numeric) ? numeric : text;
}
