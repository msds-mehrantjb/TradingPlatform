import { escapeHtml, formatInteger, formatNumber, sideLabel } from "./formatters";
import type { WcaDecision, WcaProposedOrder, WcaSizingResult } from "./types";

function sizingFrom(decision: WcaDecision | null): WcaSizingResult | undefined {
  return decision?.sizingResult ?? decision?.sizing_result ?? decision?.sizing;
}

function orderFrom(decision: WcaDecision | null): WcaProposedOrder | undefined {
  return decision?.proposedOrder ?? decision?.proposed_order;
}

export function renderWcaOrderPanel(decision: WcaDecision | null): string {
  const order = orderFrom(decision);
  const sizing = sizingFrom(decision);
  const side = sideLabel(order?.side ?? sizing?.side ?? "HOLD");
  const quantity = order?.quantity ?? sizing?.finalQuantity ?? sizing?.final_quantity ?? 0;

  return `
    <details class="wca-expander wca-order-panel">
      <summary class="algo-expand-toggle wca-expand-summary">
        <span>Target Order</span>
        <strong>${escapeHtml(order ? `${side} ${formatInteger(quantity)}` : "No order")}</strong>
        <b>+</b>
      </summary>
      <div class="wca-expander-body">
        <div class="regime-summary-grid wca-summary-grid">
          <div class="regime-summary-row" data-tone="neutral"><b>Side</b><span>${escapeHtml(side)}</span></div>
          <div class="regime-summary-row" data-tone="neutral"><b>Quantity</b><span>${escapeHtml(formatInteger(quantity))}</span></div>
          <div class="regime-summary-row" data-tone="neutral"><b>Limit</b><span>${escapeHtml(formatPlain(order?.limitPrice ?? order?.limit_price))}</span></div>
          <div class="regime-summary-row" data-tone="neutral"><b>Stop</b><span>${escapeHtml(formatPlain(order?.stopPrice ?? order?.stop_price ?? sizing?.stopPrice ?? sizing?.stop_price))}</span></div>
          <div class="regime-summary-row" data-tone="neutral"><b>Target</b><span>${escapeHtml(formatPlain(order?.targetPrice ?? order?.target_price ?? sizing?.targetPrice ?? sizing?.target_price))}</span></div>
          <div class="regime-summary-row" data-tone="info"><b>Source</b><span>Backend WCA decision only</span></div>
        </div>
      </div>
    </details>
  `;
}

function formatPlain(value: unknown): string {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? formatNumber(numeric, 2).replace(/\.00$/, "") : "n/a";
}
