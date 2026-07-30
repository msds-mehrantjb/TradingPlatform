import { escapeHtml, formatNumber, formatPercent, sideLabel, statusLabel, stringValue } from "./formatters";
import type { WcaAggregationResult, WcaConfigurationResponse, WcaDecision, WcaStrategyContribution, WcaStrategyEvaluation } from "./types";

function aggregationFrom(decision: WcaDecision | null): WcaAggregationResult | undefined {
  return decision?.aggregation ?? decision?.aggregationResult ?? decision?.aggregation_result;
}

function strategyRows(decision: WcaDecision | null): Array<WcaStrategyEvaluation | WcaStrategyContribution> {
  const aggregation = aggregationFrom(decision);
  const contributions = aggregation?.strategyContributions ?? aggregation?.strategy_contributions ?? aggregation?.contributions;
  if (Array.isArray(contributions) && contributions.length) {
    return contributions;
  }
  const aggregationEvaluations = aggregation?.strategyEvaluations ?? aggregation?.strategy_evaluations;
  if (Array.isArray(aggregationEvaluations) && aggregationEvaluations.length) {
    return aggregationEvaluations;
  }
  return decision?.strategyEvaluations ?? decision?.strategy_evaluations ?? decision?.strategies ?? [];
}

function baseWeightFor(row: WcaStrategyEvaluation | WcaStrategyContribution, configuration: WcaConfigurationResponse | null): number | undefined {
  const strategyId = stringValue(row.strategyId, row.strategy_id, row.name);
  const weights = configuration?.baseWeights ?? configuration?.base_weights ?? {};
  return Number(row.baseWeight ?? row.base_weight ?? weights[strategyId]);
}

export function renderWcaStrategyTable(decision: WcaDecision | null, configuration: WcaConfigurationResponse | null): string {
  const rows = strategyRows(decision);
  const aggregation = aggregationFrom(decision);
  const exclusions = aggregation?.exclusions ?? rows.filter((row) => row.excluded);
  if (!rows.length && !exclusions.length) {
    return `
      <details class="wca-expander">
        <summary class="algo-expand-toggle wca-expand-summary">
          <span>Strategy Contributions</span>
          <strong>Waiting for backend snapshot</strong>
          <b>+</b>
        </summary>
        <div class="wca-expander-body">
          <div class="wca-empty">Data unavailable - waiting for a backend WCA decision snapshot.</div>
        </div>
      </details>
    `;
  }

  return `
    <details class="wca-expander">
      <summary class="algo-expand-toggle wca-expand-summary">
        <span>Strategy Contributions</span>
        <strong>${rows.length} strategy rows / ${exclusions.length} exclusions</strong>
        <b>+</b>
      </summary>
      <div class="wca-expander-body weighted-strategy-list expanded">
        <table class="weighted-strategy-table wca-strategy-table">
          <tbody>
            ${rows.map((row) => renderStrategyRow(row, baseWeightFor(row, configuration))).join("")}
          </tbody>
        </table>
        ${renderExclusions(exclusions)}
      </div>
    </details>
  `;
}

function renderStrategyRow(row: WcaStrategyEvaluation | WcaStrategyContribution, baseWeight: number | undefined) {
  const strategyId = stringValue(row.strategyId, row.strategy_id, row.name, "unknown_strategy");
  const applicability = stringValue(row.applicability, row.excluded ? "NOT_APPLICABLE" : "ACTIVE");
  const confidence = row.calibratedConfidence ?? row.calibrated_confidence ?? row.confidence ?? row.rawConfidence ?? row.raw_confidence;
  const signal = sideLabel(row.direction ?? row.signal);
  const tone = signal === "Buy" ? "1" : signal === "Sell" ? "2" : row.excluded ? "3" : "0";
  return `
    <tr class="weighted-strategy-name-row" data-tone="${tone}" data-disabled="${String(row.excluded || applicability.toUpperCase() === "NOT_APPLICABLE")}">
      <td colspan="4">
        ${escapeHtml(strategyId)}
        <span class="module-status-badge" data-module-status="${row.excluded ? "not_data_ready" : "active"}">${escapeHtml(statusLabel(applicability))}</span>
      </td>
      <td>${escapeHtml(signal)}</td>
    </tr>
    <tr class="weighted-strategy-detail-row" data-tone="${tone}" data-disabled="${String(row.excluded || applicability.toUpperCase() === "NOT_APPLICABLE")}">
      ${renderMetricCell("Base", formatPercent(baseWeight))}
      ${renderMetricCell("Effective", formatPercent(row.effectiveWeight ?? row.effective_weight ?? row.adjustedWeight ?? row.adjusted_weight))}
      ${renderMetricCell("Confidence", formatPercent(confidence))}
      ${renderMetricCell("Contribution", formatNumber(row.scoreContribution ?? row.score_contribution ?? row.contribution, 3))}
    </tr>
  `;
}

function renderMetricCell(label: string, value: string): string {
  return `
    <td>
      <small>${escapeHtml(label)}</small>
      <strong>${escapeHtml(value)}</strong>
    </td>
  `;
}

function renderExclusions(exclusions: Array<WcaStrategyEvaluation | WcaStrategyContribution> | undefined): string {
  const excluded = (exclusions ?? []).filter((row) => row.excluded || row.exclusionReason || row.exclusion_reason);
  if (!excluded.length) {
    return `<div class="wca-note">Excluded strategies: none reported by backend.</div>`;
  }
  return `
    <div class="wca-exclusions">
      <strong>Excluded strategies</strong>
      ${excluded
        .map(
          (row) =>
            `<span>${escapeHtml(stringValue(row.strategyId, row.strategy_id, row.name, "strategy"))}</span>`,
        )
        .join("")}
    </div>
  `;
}

export function renderWcaFamilyContributions(decision: WcaDecision | null): string {
  const aggregation = aggregationFrom(decision);
  const families = aggregation?.familyContributions ?? aggregation?.family_contributions ?? [];
  if (!families.length) {
    return `
      <details class="wca-expander">
        <summary class="algo-expand-toggle wca-expand-summary">
          <span>Family Contributions</span>
          <strong>Data unavailable</strong>
          <b>+</b>
        </summary>
        <div class="wca-expander-body">
          <div class="wca-empty">Strategy family contributions: data unavailable.</div>
        </div>
      </details>
    `;
  }
  return `
    <details class="wca-expander">
      <summary class="algo-expand-toggle wca-expand-summary">
        <span>Family Contributions</span>
        <strong>${families.length} families</strong>
        <b>+</b>
      </summary>
      <div class="wca-expander-body">
        <div class="meta-family-grid wca-family-grid expanded">
          ${families
            .map((family) => {
              const buy = Number(family.buyContribution ?? family.buy_contribution ?? family.buyScore ?? family.buy_score ?? 0);
              const sell = Number(family.sellContribution ?? family.sell_contribution ?? family.sellScore ?? family.sell_score ?? 0);
              const tone = buy > sell ? "buy" : sell > buy ? "sell" : "hold";
              return `
                <div class="meta-family-card wca-family-card" data-tone="${tone}" data-capped="${String(Boolean(family.capped))}">
                  <div class="meta-family-head">
                    <span>${escapeHtml(stringValue(family.family, "Family"))}</span>
                    <strong>${escapeHtml(formatPercent(family.activeWeight ?? family.active_weight ?? family.directionalWeight ?? family.directional_weight ?? family.totalWeight ?? family.total_weight))}</strong>
                  </div>
                  <div class="meta-family-values">
                    <span><b>Buy</b>${escapeHtml(formatNumber(buy, 3))}</span>
                    <span><b>Sell</b>${escapeHtml(formatNumber(sell, 3))}</span>
                    <span><b>Hold</b>${escapeHtml(formatNumber(family.holdContribution ?? family.hold_contribution, 3))}</span>
                  </div>
                </div>
              `;
            })
            .join("")}
        </div>
      </div>
    </details>
  `;
}
