import { escapeHtml, formatPercent, formatSettingValue, reasonText, statusLabel, stringValue } from "./formatters";
import type { WcaDecision, WcaEffectiveSettings, WcaMarketStatus } from "./types";

function marketStatusFrom(decision: WcaDecision | null): WcaMarketStatus | undefined {
  return decision?.marketStatus ?? decision?.market_status;
}

function effectiveSettingsFrom(decision: WcaDecision | null): WcaEffectiveSettings | undefined {
  return decision?.effectiveSettings ?? decision?.effective_settings;
}

export function renderWcaDynamicProfilePanel(decision: WcaDecision | null): string {
  const market = marketStatusFrom(decision);
  const effective = effectiveSettingsFrom(decision);
  const overlays = effective?.activeOverlays ?? effective?.active_overlays ?? [];
  const baseline = effective?.baseline ?? {};
  const settings = effective?.effective ?? effective?.settings ?? {};
  const multipliers = effective?.multipliers ?? {};
  const baselineRisk = baseline.baseRiskPercent ?? baseline.base_risk_percent ?? baseline.riskPercent ?? baseline.risk_percent;
  const effectiveRisk = settings.baseRiskPercent ?? settings.base_risk_percent ?? settings.riskPercent ?? settings.risk_percent;
  const riskMultiplier = multipliers.risk ?? multipliers.riskMultiplier ?? multipliers.risk_multiplier;

  return `
    <section class="wca-section">
      <div class="wca-section-header">
        <div class="algo-section-title">Current Dynamic Market Status</div>
        <span class="wca-pill">${escapeHtml(statusLabel(market?.dataQuality ?? market?.data_quality ?? "data unavailable"))}</span>
      </div>
      ${
        market
          ? `
            <div class="wca-status-grid">
              ${renderStatusItem("Trend", market.trend)}
              ${renderStatusItem("Volatility", market.volatility)}
              ${renderStatusItem("Liquidity", market.liquidity)}
              ${renderStatusItem("Session", market.session)}
              ${renderStatusItem("Event risk", market.eventRisk ?? market.event_risk)}
              ${renderStatusItem("Algorithm risk", market.algorithmRisk ?? market.algorithm_risk)}
            </div>
            <div class="wca-note">Reason: ${escapeHtml(reasonText(market) || "backend WCA market-status resolver")}</div>
          `
          : `<div class="wca-empty">Data unavailable - WCA market status has not been returned by the backend.</div>`
      }
      <div class="wca-overlay-list">
        <strong>Active dynamic overlays</strong>
        ${
          overlays.length
            ? overlays.map((overlay) => `<span>${escapeHtml(overlay)}</span>`).join("")
            : `<span>Not applicable - no active overlays reported.</span>`
        }
      </div>
      <div class="wca-risk-example">
        <strong>Baseline risk: ${escapeHtml(formatSettingValue(baselineRisk))}</strong>
        <strong>Effective risk: ${escapeHtml(formatSettingValue(effectiveRisk))}</strong>
        <span>Reason: ${escapeHtml(overlays.length ? `${overlays.join(" x ")} overlay` : "backend baseline profile")}${
          riskMultiplier !== undefined ? ` x ${escapeHtml(formatPercent(riskMultiplier))}` : ""
        }</span>
      </div>
      <div class="wca-config-meta">
        <span>Profile: ${escapeHtml(stringValue(effective?.profileId, effective?.profile_id, market?.profileId, market?.profile_id, "unavailable"))}</span>
        <span>Expires: ${escapeHtml(stringValue(effective?.expirationTimestamp, effective?.expiration_timestamp, market?.expirationTimestamp, market?.expiration_timestamp, "n/a"))}</span>
      </div>
    </section>
  `;
}

function renderStatusItem(label: string, value: unknown): string {
  return `
    <div class="wca-status-item">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(statusLabel(value || "Data unavailable"))}</strong>
    </div>
  `;
}

