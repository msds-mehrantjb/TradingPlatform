import { escapeHtml, labelFromKey, renderReadonlySettingRows, stringValue } from "./formatters";
import type { WcaBaselineSettings, WcaConfigurationResponse, WcaDecision, WcaEffectiveSettings } from "./types";

function effectiveSettingsFrom(decision: WcaDecision | null): WcaEffectiveSettings | undefined {
  return decision?.effectiveSettings ?? decision?.effective_settings;
}

function flattenSettings(record: Record<string, unknown> | undefined): Record<string, unknown> {
  if (!record) {
    return {};
  }
  const flattened: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(record)) {
    if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      for (const [innerKey, innerValue] of Object.entries(value as Record<string, unknown>)) {
        flattened[innerKey] = innerValue;
      }
    } else {
      flattened[key] = value;
    }
  }
  return flattened;
}

export function renderWcaSettingsPanel(
  configuration: WcaConfigurationResponse | null,
  baselineSettings: WcaBaselineSettings | null,
  decision: WcaDecision | null,
): string {
  const effectiveSnapshot = effectiveSettingsFrom(decision);
  const baseline = {
    ...flattenSettings(baselineSettings ?? undefined),
    ...flattenSettings(configuration?.decisionSettings ?? configuration?.decision_settings),
    ...flattenSettings(configuration?.tradingSettings ?? configuration?.trading_settings),
    ...flattenSettings(effectiveSnapshot?.baseline),
  };
  const effective = {
    ...flattenSettings(effectiveSnapshot?.settings),
    ...flattenSettings(effectiveSnapshot?.effective),
  };
  const keys = prioritizedKeys(baseline, effective);

  return `
    <section class="wca-section">
      <div class="wca-section-header">
        <div class="algo-section-title">Baseline and Effective Settings</div>
        <span class="wca-pill">Effective settings are read-only</span>
      </div>
      <div class="wca-note">
        Editing baseline settings is routed through <code>PUT /api/wca/configuration</code> and creates a backend configuration version.
      </div>
      ${
        keys.length
          ? `<div class="wca-setting-grid">${renderReadonlySettingRows(baseline, effective, keys, overlayReasons(effectiveSnapshot))}</div>`
          : `<div class="wca-empty">Data unavailable - waiting for backend baseline and effective settings.</div>`
      }
      ${renderWcaBaselineEditForm(configuration)}
      <div class="wca-config-meta">
        <span>Configuration: ${escapeHtml(stringValue(configuration?.configurationVersion, configuration?.configuration_version, "unavailable"))}</span>
        <span>Engine: ${escapeHtml(stringValue(configuration?.engineVersion, configuration?.engine_version, "unavailable"))}</span>
        <span>Paper only: ${configuration?.paperOnly ?? configuration?.paper_only ? "yes" : "backend status unavailable"}</span>
      </div>
    </section>
  `;
}

function prioritizedKeys(baseline: Record<string, unknown>, effective: Record<string, unknown>): string[] {
  const priority = [
    "baseRiskPercent",
    "base_risk_percent",
    "maxPositionPercent",
    "max_position_percent",
    "minimumScore",
    "minimum_score",
    "minimumAgreement",
    "minimum_agreement",
    "minimumAverageConfidence",
    "minimum_average_confidence",
    "maxDailyTrades",
    "max_daily_trades",
    "maxSpreadPercent",
    "max_spread_percent",
    "maxParticipationPercent",
    "max_participation_percent",
    "entryCutoff",
    "entry_cutoff",
    "atrStopMultiplier",
    "atr_stop_multiplier",
    "takeProfitR",
    "take_profit_r",
  ];
  const available = new Set([...Object.keys(baseline), ...Object.keys(effective)]);
  const ordered = priority.filter((key) => available.has(key));
  for (const key of available) {
    if (!ordered.includes(key) && ordered.length < 12) {
      ordered.push(key);
    }
  }
  return ordered;
}

function overlayReasons(effectiveSnapshot: WcaEffectiveSettings | undefined): Record<string, string> {
  const overlays = effectiveSnapshot?.activeOverlays ?? effectiveSnapshot?.active_overlays ?? [];
  const reason = overlays.length ? `${overlays.join(" x ")} overlay` : "backend effective profile";
  return Object.fromEntries(
    [
      "baseRiskPercent",
      "base_risk_percent",
      "maxPositionPercent",
      "max_position_percent",
      "minimumScore",
      "minimum_score",
      "minimumAgreement",
      "minimum_agreement",
      "maxDailyTrades",
      "max_daily_trades",
    ].map((key) => [key, reason]),
  );
}

export function renderWcaBaselineEditForm(configuration: WcaConfigurationResponse | null): string {
  const trading = configuration?.tradingSettings ?? configuration?.trading_settings ?? {};
  const decision = configuration?.decisionSettings ?? configuration?.decision_settings ?? {};
  const tradingKeys = ["baseRiskPercent", "maxPositionPercent", "maxDailyTrades", "maxSpreadPercent"];
  const decisionKeys = ["minimumActiveStrategies", "minimumDirectionalAgreement", "minimumAverageConfidence", "minimumSignalEdge"];
  return `
    <form class="wca-baseline-form" data-wca-baseline-form="true">
      <strong>Editable baseline settings</strong>
      ${tradingKeys
        .map((key) => {
          const value = (trading as Record<string, unknown>)[key] ?? "";
          return `
            <label>
              <span>${escapeHtml(labelFromKey(key))}</span>
              <input name="tradingSettings.${escapeHtml(key)}" value="${escapeHtml(String(value))}" data-wca-baseline-input="true" />
            </label>
          `;
        })
        .join("")}
      ${decisionKeys
        .map((key) => {
          const value = (decision as Record<string, unknown>)[key] ?? "";
          return `
            <label>
              <span>${escapeHtml(labelFromKey(key))}</span>
              <input name="decisionSettings.${escapeHtml(key)}" value="${escapeHtml(String(value))}" data-wca-baseline-input="true" />
            </label>
          `;
        })
        .join("")}
      <button type="submit">Save Baseline Configuration</button>
    </form>
  `;
}
