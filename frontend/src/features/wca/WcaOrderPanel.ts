import { escapeHtml, formatCurrency, formatInteger, formatNumber, reasonText, sideLabel, stringValue } from "./formatters";
import type { WcaDecision, WcaGlobalGateResult, WcaProposedOrder, WcaSizingResult } from "./types";

function sizingFrom(decision: WcaDecision | null): WcaSizingResult | undefined {
  return decision?.sizingResult ?? decision?.sizing_result;
}

function orderFrom(decision: WcaDecision | null): WcaProposedOrder | undefined {
  return decision?.proposedOrder ?? decision?.proposed_order;
}

function globalFrom(decision: WcaDecision | null): WcaGlobalGateResult | undefined {
  return decision?.globalGateResult ?? decision?.global_gate_result;
}

export function renderWcaOrderPanel(decision: WcaDecision | null): string {
  const sizing = sizingFrom(decision);
  const order = orderFrom(decision);
  const global = globalFrom(decision);
  const proposedQuantity = order?.quantity ?? sizing?.proposedQuantity ?? sizing?.proposed_quantity ?? sizing?.finalQuantity ?? sizing?.final_quantity;
  const approvedQuantity = order?.approvedQuantity ?? order?.approved_quantity ?? sizing?.globallyApprovedQuantity ?? sizing?.globally_approved_quantity ?? global?.approvedQuantity ?? global?.approved_quantity;
  return `
    <section class="wca-section">
      <div class="algo-section-title">Order Proposal</div>
      ${
        sizing || order
          ? `
            <div class="wca-order-grid">
              ${renderOrderMetric("Side", sideLabel(order?.side ?? decision?.effectiveDecision ?? decision?.effective_decision ?? decision?.signal))}
              ${renderOrderMetric("Proposed quantity", formatInteger(proposedQuantity))}
              ${renderOrderMetric("Globally approved quantity", formatInteger(approvedQuantity))}
              ${renderOrderMetric("Trigger", formatNumber(order?.triggerPrice ?? order?.trigger_price))}
              ${renderOrderMetric("Limit", formatNumber(order?.limitPrice ?? order?.limit_price))}
              ${renderOrderMetric("Stop", formatNumber(order?.stopPrice ?? order?.stop_price))}
              ${renderOrderMetric("Target", formatNumber(order?.targetPrice ?? order?.target_price))}
              ${renderOrderMetric("Planned risk", formatCurrency(order?.plannedRisk ?? order?.planned_risk ?? sizing?.riskDollars ?? sizing?.risk_dollars))}
              ${renderOrderMetric("Limiting factor", stringValue(sizing?.limitingCap, sizing?.limiting_cap, "backend sizing cap"))}
            </div>
            <div class="wca-note">Reason: ${escapeHtml(reasonText(order) || reasonText(sizing) || "backend WCA sizing and order-proposal services")}</div>
          `
          : `<div class="wca-empty">Data unavailable - no proposed order from backend.</div>`
      }
    </section>
  `;
}

function renderOrderMetric(label: string, value: string): string {
  return `
    <div class="wca-order-metric">
      <span>${escapeHtml(label)}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `;
}

