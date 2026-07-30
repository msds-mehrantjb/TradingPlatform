import { escapeHtml, formatNumber, stringValue } from "./formatters";
import { renderWcaBacktestPanel } from "./WcaBacktestPanel";
import { renderWcaDynamicProfilePanel } from "./WcaDynamicProfilePanel";
import { renderWcaGatePanel } from "./WcaGatePanel";
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
      ${renderWcaDynamicProfilePanel(decision)}
      ${renderWcaGatePanel(decision)}
      ${renderWcaBacktestPanel(state.latestBacktest, backtestStatusFor(state.status), state.error)}
    </div>
  `;
}

function renderRuntimeControlSurface(state: WcaPresentationState): string {
  const status = state.backendStatus;
  const runtime = (status?.runtimeHealth ?? status?.runtime_health ?? {}) as Record<string, unknown>;
  const api = (status?.apiHealth ?? status?.api_health ?? {}) as Record<string, unknown>;
  const versions = (status?.activeVersions ?? status?.active_versions ?? {}) as Record<string, unknown>;
  const observability = (status?.observability ?? {}) as Record<string, unknown>;
  const inventory = (status?.virtualInventory ?? status?.virtual_inventory ?? {}) as Record<string, unknown>;
  const paperOnly = status?.paperOnly ?? status?.paper_only;
  const inventorySymbol = stringValue(inventory.symbol, "SPY");
  const inventoryAccount = stringValue(inventory.accountId, inventory.account_id, inventory.account, "wca-paper");
  const inventoryQuantity = stringValue(inventory.quantity, inventory.shares, inventory.positionQuantity, inventory.position_quantity, inventory.currentPositionQuantity, inventory.current_position_quantity, "0");
  const runtimeStatus = stringValue(runtime.status, "unknown");
  const runtimeTone = runtimeStatus.toLowerCase().includes("critical") || runtimeStatus.toLowerCase().includes("blocked") ? "block" : runtimeStatus.toLowerCase().includes("unknown") ? "warn" : "pass";
  return `
    <details class="wca-expander">
      <summary class="algo-expand-toggle wca-expand-summary">
        <span>Runtime Control Surface</span>
        <strong>${paperOnly ? "Paper only" : "paper status unavailable"}</strong>
        <b>+</b>
      </summary>
      <div class="wca-expander-body">
        <div class="regime-summary-grid wca-summary-grid">
          <div class="regime-summary-row" data-tone="pass"><b>API health</b><span>${escapeHtml(stringValue(api.status, status?.status, "unknown"))}</span></div>
          <div class="regime-summary-row" data-tone="${runtimeTone}"><b>Runtime process</b><span>${escapeHtml(runtimeStatus)}</span></div>
          <div class="regime-summary-row" data-tone="info"><b>Runtime lag</b><span>${escapeHtml(formatNumber(observability.eventLagSeconds ?? runtime.lag_seconds ?? runtime.lagSeconds, 1))}s</span></div>
          <div class="regime-summary-row" data-tone="info"><b>Decision latency</b><span>${escapeHtml(formatNumber(observability.decisionLatencySeconds, 3))}s</span></div>
          <div class="regime-summary-row" data-tone="neutral"><b>Broker status</b><span>${escapeHtml(stringValue((observability.brokerStatus as Record<string, unknown> | undefined)?.status, "unavailable"))}</span></div>
          <div class="regime-summary-row" data-tone="neutral"><b>Reconciliation</b><span>${escapeHtml(stringValue((observability.reconciliationStatus as Record<string, unknown> | undefined)?.status, "not run"))}</span></div>
        </div>
        <div class="wca-config-meta">
          <span>Config: ${escapeHtml(stringValue(versions.configuration, status?.configurationVersion, status?.configuration_version, "unavailable"))}</span>
          <span>Weights: ${escapeHtml(stringValue(versions.weight, "unavailable"))}</span>
          <span>Calibration: ${escapeHtml(Array.isArray(versions.calibrations) ? versions.calibrations.join(", ") || "none active" : "unavailable")}</span>
          <span>Inventory scope: WCA isolated</span>
          <span>Inventory account: ${escapeHtml(inventoryAccount)}</span>
          <span>Position: ${escapeHtml(inventoryQuantity)} ${escapeHtml(inventorySymbol)}</span>
        </div>
      </div>
    </details>
  `;
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
