import { escapeHtml, formatNumber, formatPercent, reasonText, sideClass, sideLabel, stringValue } from "./formatters";
import { renderWcaBacktestPanel } from "./WcaBacktestPanel";
import { renderWcaDynamicProfilePanel } from "./WcaDynamicProfilePanel";
import { renderWcaGatePanel } from "./WcaGatePanel";
import { renderWcaOrderPanel } from "./WcaOrderPanel";
import { renderWcaSettingsPanel } from "./WcaSettingsPanel";
import { renderWcaFamilyContributions, renderWcaStrategyTable } from "./WcaStrategyTable";
import type { WcaAggregationResult, WcaConfigurationResponse, WcaDecision } from "./types";
import type { WcaPresentationState } from "./state";

function aggregationFrom(decision: WcaDecision | null): WcaAggregationResult | undefined {
  return decision?.aggregation ?? decision?.aggregationResult ?? decision?.aggregation_result;
}

export type WcaPanelOptions = {
  onConfigurationSubmit?: (configuration: Partial<WcaConfigurationResponse>) => void;
};

export function renderWcaPanel(container: HTMLElement, state: WcaPresentationState, options: WcaPanelOptions = {}): void {
  container.innerHTML = renderWcaPanelHtml(state);
  bindConfigurationForm(container, state, options);
}

export function renderWcaPanelHtml(state: WcaPresentationState): string {
  const decision = state.latestDecision;
  const aggregation = aggregationFrom(decision);
  const finalDecision = decision?.finalDecision ?? decision?.final_decision ?? decision?.effectiveDecision ?? decision?.effective_decision ?? decision?.signal ?? "HOLD";
  return `
    <div class="wca-presentation-panel" data-wca-presentation-layer="backend">
      <section class="wca-section wca-decision-section">
        <div class="wca-section-header">
          <div>
            <div class="algo-section-title">Final Decision</div>
            <div class="wca-backend-meta">Backend authoritative - frontend display only</div>
          </div>
          <span class="wca-pill">${escapeHtml(stringValue(state.backendStatus?.mode, state.backendStatus?.status, "status unavailable"))}</span>
        </div>
        ${
          state.status === "error"
            ? `<div class="wca-empty">Data unavailable - ${escapeHtml(state.error)}</div>`
            : `
              <div class="wca-final-row">
                <div class="algo-final ${sideClass(finalDecision)}">${escapeHtml(sideLabel(finalDecision))}</div>
                <div class="wca-score-strip">
                  <span>Buy score <strong>${escapeHtml(formatNumber(aggregation?.buyScore ?? aggregation?.buy_score, 3))}</strong></span>
                  <span>Sell score <strong>${escapeHtml(formatNumber(aggregation?.sellScore ?? aggregation?.sell_score, 3))}</strong></span>
                  <span>Net <strong>${escapeHtml(formatNumber(aggregation?.normalizedNetScore ?? aggregation?.normalized_net_score, 3))}</strong></span>
                  <span>Edge <strong>${escapeHtml(formatNumber(aggregation?.winnerEdge ?? aggregation?.winner_edge, 3))}</strong></span>
                  <span>Agreement <strong>${escapeHtml(formatPercent(aggregation?.agreement))}</strong></span>
                </div>
              </div>
              <div class="wca-note">Reason: ${escapeHtml(reasonText(decision ?? undefined) || reasonText(aggregation) || "backend WCA decision snapshot")}</div>
            `
        }
      </section>
      ${renderRuntimeControlSurface(state)}
      ${renderWcaFamilyContributions(decision)}
      ${renderWcaStrategyTable(decision, state.configuration)}
      ${renderWcaSettingsPanel(state.configuration, state.baselineSettings, decision)}
      ${renderWcaDynamicProfilePanel(decision)}
      ${renderWcaGatePanel(decision)}
      ${renderWcaOrderPanel(decision)}
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
  return `
    <section class="wca-section">
      <div class="wca-section-header">
        <div class="algo-section-title">Runtime Control Surface</div>
        <span class="wca-pill">${paperOnly ? "PAPER ONLY" : "paper status unavailable"}</span>
      </div>
      <div class="wca-status-grid">
        <div class="wca-status-item"><span>API health</span><strong>${escapeHtml(stringValue(api.status, status?.status, "unknown"))}</strong></div>
        <div class="wca-status-item"><span>Runtime process</span><strong>${escapeHtml(stringValue(runtime.status, "unknown"))}</strong></div>
        <div class="wca-status-item"><span>Runtime lag</span><strong>${escapeHtml(formatNumber(observability.eventLagSeconds ?? runtime.lag_seconds ?? runtime.lagSeconds, 1))}s</strong></div>
        <div class="wca-status-item"><span>Decision latency</span><strong>${escapeHtml(formatNumber(observability.decisionLatencySeconds, 3))}s</strong></div>
        <div class="wca-status-item"><span>Broker status</span><strong>${escapeHtml(stringValue((observability.brokerStatus as Record<string, unknown> | undefined)?.status, "unavailable"))}</strong></div>
        <div class="wca-status-item"><span>Reconciliation</span><strong>${escapeHtml(stringValue((observability.reconciliationStatus as Record<string, unknown> | undefined)?.status, "not run"))}</strong></div>
      </div>
      <div class="wca-config-meta">
        <span>Config: ${escapeHtml(stringValue(versions.configuration, status?.configurationVersion, status?.configuration_version, "unavailable"))}</span>
        <span>Weights: ${escapeHtml(stringValue(versions.weight, "unavailable"))}</span>
        <span>Calibration: ${escapeHtml(Array.isArray(versions.calibrations) ? versions.calibrations.join(", ") || "none active" : "unavailable")}</span>
        <span>WCA virtual inventory: ${escapeHtml(stringValue((inventory as Record<string, unknown>).symbol, "SPY"))} isolated</span>
      </div>
    </section>
  `;
}

function backtestStatusFor(status: WcaPresentationState["status"]): string {
  if (status === "loading") {
    return "waiting";
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
