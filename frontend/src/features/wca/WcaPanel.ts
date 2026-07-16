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
