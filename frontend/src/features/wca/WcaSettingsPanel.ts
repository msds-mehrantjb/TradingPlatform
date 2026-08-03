import { escapeHtml, formatCurrency, formatInteger, formatNumber, labelFromKey, numberValue, sideLabel, stringValue } from "./formatters";
import type {
  WcaBaselineSettings,
  WcaConfigurationResponse,
  WcaDecision,
  WcaEffectiveSettings,
  WcaGlobalGateResult,
  WcaProposedOrder,
  WcaSizingResult,
} from "./types";

function effectiveSettingsFrom(decision: WcaDecision | null): WcaEffectiveSettings | undefined {
  return decision?.effectiveSettings ?? decision?.effective_settings;
}

function sizingFrom(decision: WcaDecision | null): WcaSizingResult | undefined {
  return decision?.sizingResult ?? decision?.sizing_result ?? decision?.sizing;
}

function orderFrom(decision: WcaDecision | null): WcaProposedOrder | undefined {
  return decision?.proposedOrder ?? decision?.proposed_order;
}

function globalFrom(decision: WcaDecision | null): WcaGlobalGateResult | undefined {
  return decision?.globalGateResult ?? decision?.global_gate_result;
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
  const trading = {
    ...flattenSettings(baselineSettings ?? undefined),
    ...flattenSettings(configuration?.tradingSettings ?? configuration?.trading_settings),
    ...flattenSettings(effectiveSnapshot?.baseline),
  };
  const decisionSettings = {
    ...flattenSettings(configuration?.decisionSettings ?? configuration?.decision_settings),
    ...flattenSettings(effectiveSnapshot?.settings),
    ...flattenSettings(effectiveSnapshot?.effective),
  };
  const configurationVersion = stringValue(configuration?.configurationVersion, configuration?.configuration_version, "backend config pending");
  const engineVersion = stringValue(configuration?.engineVersion, configuration?.engine_version, "engine pending");
  const summary = `${configurationVersion} / ${engineVersion}`;

  return `
    <details class="trading-settings-panel wca-trading-settings-panel" data-status="${configuration ? "ready" : "waiting"}" open>
      <summary class="trading-settings-head wca-trading-settings-head">
        <span class="trading-settings-title">
          <b>+</b>
          <strong>Trading Settings</strong>
        </span>
        <span class="trading-settings-summary">${escapeHtml(summary)}</span>
      </summary>
      <form class="trading-settings-body wca-trading-settings-body" data-wca-baseline-form="true">
        <div class="trading-settings-grid">
          ${renderTradingInput(trading, "startingCapital", "Total balance", 25000, ["starting_equity", "accountEquity", "account_equity"])}
          ${renderTradingInput(trading, "orderAllocationPercent", "Order limit %", 10, ["order_allocation_percent", "maxPositionPercent", "max_position_percent"])}
          ${renderTradingInput(trading, "dailyAllocationPercent", "Daily max %", 50, ["daily_allocation_percent", "dailyMaxPercent", "daily_max_percent"])}
          ${renderTradingInput(trading, "riskBudgetPercentOfOrder", "Risk budget %", 50, ["risk_budget_percent_of_order", "riskBudgetPercent", "risk_budget_percent"])}
          ${renderTradingInput(trading, "maxTradesPerDay", "Max trades/day", 10, ["max_trades_per_day", "maxDailyTrades", "max_daily_trades"])}
          ${renderTradingInput(trading, "fixedStopDistanceDollars", "Stop $/share", 1, ["fixed_stop_distance_dollars", "stopDollarsPerShare", "stop_dollars_per_share"])}
          ${renderTradingInput(trading, "stopLossPercent", "Stop %", 0.35, ["stop_loss_percent", "minimumStopDistancePercent", "minimum_stop_distance_percent"])}
          ${renderTradingInput(trading, "takeProfitR", "Target R", 1.5, ["take_profit_r"])}
          ${renderTradingInput(trading, "slippagePerShare", "Slippage/share", 0.02, ["slippage_per_share"])}
        </div>
        ${renderWcaTargetOrder(decision, trading)}
        ${renderWcaDefaultSettings(decisionSettings, trading)}
        <div class="trading-settings-actions wca-trading-actions">
          <span>Effective settings are read-only. Edits create a candidate configuration only.</span>
          <span>WCA settings and WCA inventory are isolated from every other algorithm.</span>
          <button class="primary-action" type="submit">Create Candidate Configuration</button>
        </div>
      </form>
    </details>
  `;
}

function renderTradingInput(settings: Record<string, unknown>, key: string, label: string, fallback: number | string, aliases: string[] = []): string {
  return `
    <label>
      <span>${escapeHtml(label)}</span>
      <input name="tradingSettings.${escapeHtml(key)}" value="${escapeHtml(settingValue(settings, fallback, key, ...aliases))}" data-wca-baseline-input="true" />
    </label>
  `;
}

function renderDefaultInput(group: "tradingSettings" | "decisionSettings", settings: Record<string, unknown>, key: string, label: string, fallback: number | string, aliases: string[] = []): string {
  return `
    <label>
      <span>${escapeHtml(label)}</span>
      <input name="${group}.${escapeHtml(key)}" value="${escapeHtml(settingValue(settings, fallback, key, ...aliases))}" data-wca-baseline-input="true" />
    </label>
  `;
}

function renderWcaTargetOrder(decision: WcaDecision | null, trading: Record<string, unknown>): string {
  const sizing = sizingFrom(decision);
  const order = orderFrom(decision);
  const global = globalFrom(decision);
  const side = sideLabel(order?.side ?? sizing?.side ?? decision?.effectiveDecision ?? decision?.effective_decision ?? decision?.signal ?? decision?.aggregation?.signal ?? decision?.aggregation?.post_local_gate_decision);
  const proposedQuantity = order?.quantity ?? sizing?.proposedQuantity ?? sizing?.proposed_quantity ?? sizing?.finalQuantity ?? sizing?.final_quantity;
  const approvedQuantity =
    order?.approvedQuantity ??
    order?.approved_quantity ??
    sizing?.globallyApprovedQuantity ??
    sizing?.globally_approved_quantity ??
    global?.approvedQuantity ??
    global?.approved_quantity ??
    global?.allowedQuantity ??
    global?.allowed_quantity;
  const startingCapital = numberValue(settingRawValue(trading, "startingCapital", "starting_equity"), 25000) ?? 25000;
  const dailyPercent = numberValue(settingRawValue(trading, "dailyAllocationPercent", "daily_allocation_percent"), 50) ?? 50;
  const orderPercent = numberValue(settingRawValue(trading, "orderAllocationPercent", "order_allocation_percent", "maxPositionPercent", "max_position_percent"), 10) ?? 10;
  const riskBudgetPercent = numberValue(settingRawValue(trading, "riskBudgetPercentOfOrder", "risk_budget_percent_of_order"), 50) ?? 50;
  const orderLimit = startingCapital * (orderPercent / 100);
  const buyingPower = startingCapital * (dailyPercent / 100);
  const riskBudget = orderLimit * (riskBudgetPercent / 1000);
  return `
    <div class="target-settings-panel weighted-target-settings-panel wca-target-settings-panel" data-side="${escapeHtml(side.toLowerCase())}">
      <strong>Target Order</strong>
      <span class="target-settings-note">Generated from WCA sizing, WCA settings, and WCA isolated inventory</span>
      <div class="target-settings-grid">
        ${renderTargetInput("Total balance", formatTargetNumber(startingCapital))}
        ${renderTargetInput("Buying power", formatTargetNumber(buyingPower))}
        ${renderTargetInput("Order limit", formatTargetNumber(orderLimit))}
        ${renderTargetInput("Order value", "0")}
        ${renderTargetInput("Symbol", "SPY")}
        ${renderTargetSelect("Side", side, ["Hold", "Buy", "Sell"])}
        ${renderTargetSelect("Order type", approvedQuantity ? "Limit" : "No order", ["No order", "Market", "Limit"])}
        ${renderTargetInput("Quantity", formatInteger(approvedQuantity ?? proposedQuantity))}
        ${renderTargetInput("Trigger / stop price", formatPlainNumber(order?.triggerPrice ?? order?.trigger_price ?? sizing?.entryPrice ?? sizing?.entry_price))}
        ${renderTargetInput("Limit price", formatPlainNumber(order?.limitPrice ?? order?.limit_price))}
        ${renderTargetInput("Protective stop", formatPlainNumber(order?.stopPrice ?? order?.stop_price ?? sizing?.stopPrice ?? sizing?.stop_price))}
        ${renderTargetInput("Take profit", formatPlainNumber(order?.targetPrice ?? order?.target_price ?? sizing?.targetPrice ?? sizing?.target_price))}
        ${renderTargetInput("Risk budget", formatPlainNumber(global?.approvedRisk ?? global?.approved_risk ?? riskBudget))}
        ${renderTargetInput("Planned stop risk", formatPlainNumber(order?.plannedRisk ?? order?.planned_risk ?? sizing?.riskDollars ?? sizing?.risk_dollars))}
        ${renderTargetInput("Estimated slippage", formatCurrency((numberValue(approvedQuantity ?? proposedQuantity, 0) ?? 0) * (numberValue(settingRawValue(trading, "slippagePerShare", "slippage_per_share"), 0) ?? 0)))}
        ${renderTargetInput("Time in force", "Day")}
        ${renderTargetInput("Cutoff", "Backend session policy")}
        ${renderTargetSelect("Submit order", "Manual", ["Manual", "Automatic paper"])}
      </div>
    </div>
  `;
}

function renderWcaDefaultSettings(decisionSettings: Record<string, unknown>, trading: Record<string, unknown>): string {
  const pyramiding = String(settingRawValue(trading, "pyramidingEnabled", "pyramiding_enabled") ?? "false") === "true";
  return `
    <details class="trading-default-section wca-default-settings-section">
      <summary class="trading-default-head wca-default-summary">
        <span class="trading-default-expand">
          <b>+</b>
          <strong>Default Settings</strong>
        </span>
        <label class="trading-default-toggle">
          <span>On / Off</span>
          <input name="tradingSettings.useDefaultSizingSettings" value="true" type="checkbox" checked data-wca-baseline-input="true" />
        </label>
      </summary>
      <div class="trading-default-body">
        <div class="trading-settings-grid trading-default-grid">
          ${renderDefaultInput("decisionSettings", decisionSettings, "minimumScore", "Minimum buy score", 0.6, ["minimum_score"])}
          ${renderDefaultInput("decisionSettings", decisionSettings, "minimumSignalEdge", "Minimum signal edge", 0.2, ["minimum_signal_edge"])}
          ${renderDefaultInput("tradingSettings", trading, "baseRiskPercent", "Base risk %", 0.25, ["base_risk_percent"])}
          ${renderDefaultInput("tradingSettings", trading, "maxPositionPercent", "Max position %", 50, ["max_position_percent"])}
          ${renderDefaultInput("tradingSettings", trading, "fixedStopDistanceDollars", "Stop $/share", 1, ["fixed_stop_distance_dollars"])}
          ${renderDefaultInput("tradingSettings", trading, "atrStopMultiplier", "ATR stop multiplier", 2, ["atr_stop_multiplier"])}
          ${renderDefaultInput("tradingSettings", trading, "minimumStopDistancePercent", "Min stop distance %", 0.05, ["minimum_stop_distance_percent"])}
          ${renderDefaultInput("tradingSettings", trading, "maxParticipationPercent", "Max participation %", 0.3, ["max_participation_percent"])}
          ${renderDefaultInput("tradingSettings", trading, "maxAllowedShares", "Max shares (0 auto)", 0, ["max_allowed_shares"])}
          ${renderDefaultInput("tradingSettings", trading, "maxDailyLossPercent", "Max daily loss %", 1, ["max_daily_loss_percent"])}
          <label>
            <span>Pyramiding</span>
            <input name="tradingSettings.pyramidingEnabled" value="${pyramiding ? "true" : "false"}" type="hidden" data-wca-baseline-input="true" />
            <span class="wca-switch-display" data-checked="${String(pyramiding)}"></span>
          </label>
        </div>
      </div>
    </details>
  `;
}

function renderTargetInput(label: string, value: string): string {
  return `
    <label data-generated="true">
      <span>${escapeHtml(label)}</span>
      <input value="${escapeHtml(value)}" readonly />
    </label>
  `;
}

function renderTargetSelect(label: string, value: string, options: string[]): string {
  return `
    <label data-generated="true">
      <span>${escapeHtml(label)}</span>
      <select disabled>
        ${options.map((option) => `<option ${option === value ? "selected" : ""}>${escapeHtml(option)}</option>`).join("")}
      </select>
    </label>
  `;
}

function settingRawValue(settings: Record<string, unknown>, ...keys: string[]): unknown {
  for (const key of keys) {
    if (settings[key] !== undefined && settings[key] !== null && String(settings[key]).trim() !== "") {
      return settings[key];
    }
  }
  return undefined;
}

function settingValue(settings: Record<string, unknown>, fallback: number | string, ...keys: string[]): string {
  const value = settingRawValue(settings, ...keys);
  return String(value ?? fallback);
}

function formatPlainNumber(value: unknown): string {
  const numeric = numberValue(value);
  return numeric === null ? "0" : formatNumber(numeric, 2).replace(/\.00$/, "");
}

function formatTargetNumber(value: unknown): string {
  const numeric = numberValue(value);
  return numeric === null ? "0" : String(Math.round(numeric * 100) / 100);
}
