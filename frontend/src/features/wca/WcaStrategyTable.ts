import { escapeHtml, formatNumber, formatPercent, reasonText, sideLabel, statusLabel, stringValue } from "./formatters";
import type { WcaAggregationResult, WcaConfigurationResponse, WcaDecision, WcaStrategyContribution, WcaStrategyEvaluation } from "./types";

function aggregationFrom(decision: WcaDecision | null): WcaAggregationResult | undefined {
  return decision?.aggregation ?? decision?.aggregationResult ?? decision?.aggregation_result;
}

function strategyRows(decision: WcaDecision | null): Array<WcaStrategyEvaluation | WcaStrategyContribution> {
  const aggregation = aggregationFrom(decision);
  const contributions = aggregation?.contributions;
  if (Array.isArray(contributions) && contributions.length) {
    return contributions;
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
      <section class="wca-section">
        <div class="algo-section-title">Strategy Contributions</div>
        <div class="wca-empty">Data unavailable - waiting for a backend WCA decision snapshot.</div>
      </section>
    `;
  }

  return `
    <section class="wca-section">
      <div class="algo-section-title">Strategy Contributions</div>
      <div class="wca-table-wrap">
        <table class="wca-table">
          <thead>
            <tr>
              <th>Strategy</th>
              <th>Family</th>
              <th>Direction</th>
              <th>Applicability</th>
              <th>Base Weight</th>
              <th>Effective Weight</th>
              <th>Confidence</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            ${rows.map((row) => renderStrategyRow(row, baseWeightFor(row, configuration))).join("")}
          </tbody>
        </table>
      </div>
      ${renderExclusions(exclusions)}
    </section>
  `;
}

function renderStrategyRow(row: WcaStrategyEvaluation | WcaStrategyContribution, baseWeight: number | undefined) {
  const strategyId = stringValue(row.strategyId, row.strategy_id, row.name, "unknown_strategy");
  const applicability = stringValue(row.applicability, row.excluded ? "NOT_APPLICABLE" : "ACTIVE");
  const confidence = row.calibratedConfidence ?? row.calibrated_confidence ?? row.confidence ?? row.rawConfidence ?? row.raw_confidence;
  return `
    <tr>
      <td><strong>${escapeHtml(strategyId)}</strong><span>${escapeHtml(stringValue(row.version))}</span></td>
      <td>${escapeHtml(stringValue(row.family, "unclassified"))}</td>
      <td>${escapeHtml(sideLabel(row.direction ?? row.signal))}</td>
      <td>${escapeHtml(statusLabel(applicability))}</td>
      <td>${escapeHtml(formatPercent(baseWeight))}</td>
      <td>${escapeHtml(formatPercent(row.effectiveWeight ?? row.effective_weight))}</td>
      <td>${escapeHtml(formatPercent(confidence))}</td>
      <td>${escapeHtml(reasonText(row) || stringValue(row.exclusionReason, row.exclusion_reason, "backend contribution"))}</td>
    </tr>
  `;
}

function renderExclusions(exclusions: Array<WcaStrategyEvaluation | WcaStrategyContribution> | undefined): string {
  const excluded = (exclusions ?? []).filter((row) => row.excluded || row.exclusionReason || row.exclusion_reason);
  if (!excluded.length) {
    return `<div class="wca-note">Excluded strategies and reasons: none reported by backend.</div>`;
  }
  return `
    <div class="wca-exclusions">
      <strong>Excluded strategies and reasons</strong>
      ${excluded
        .map(
          (row) =>
            `<span>${escapeHtml(stringValue(row.strategyId, row.strategy_id, row.name, "strategy"))}: ${escapeHtml(
              stringValue(row.exclusionReason, row.exclusion_reason, reasonText(row), "Not applicable"),
            )}</span>`,
        )
        .join("")}
    </div>
  `;
}

export function renderWcaFamilyContributions(decision: WcaDecision | null): string {
  const aggregation = aggregationFrom(decision);
  const families = aggregation?.familyContributions ?? aggregation?.family_contributions ?? [];
  if (!families.length) {
    return `<div class="wca-empty">Strategy family contributions: data unavailable.</div>`;
  }
  return `
    <div class="wca-family-grid">
      ${families
        .map(
          (family) => `
            <div class="wca-family-card">
              <strong>${escapeHtml(stringValue(family.family, "Family"))}</strong>
              <span>Active weight: ${escapeHtml(formatPercent(family.activeWeight ?? family.active_weight))}</span>
              <span>Buy: ${escapeHtml(formatNumber(family.buyContribution ?? family.buy_contribution, 3))}</span>
              <span>Sell: ${escapeHtml(formatNumber(family.sellContribution ?? family.sell_contribution, 3))}</span>
              <span>Hold: ${escapeHtml(formatNumber(family.holdContribution ?? family.hold_contribution, 3))}</span>
            </div>
          `,
        )
        .join("")}
    </div>
  `;
}

