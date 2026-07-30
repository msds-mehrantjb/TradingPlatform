import { escapeHtml, formatInteger, reasonText, statusLabel, stringValue } from "./formatters";
import type { WcaDecision, WcaGlobalGateResult, WcaLocalGateEvaluation, WcaLocalGateResult } from "./types";

function localGatesFrom(decision: WcaDecision | null): WcaLocalGateResult[] {
  const explicit = decision?.localGateResult ?? decision?.local_gate_result;
  const localGates = decision?.localGates ?? decision?.local_gates;
  const hardFilters = decision?.hardFilterResults ?? decision?.hard_filter_results;
  if (explicit) {
    return [explicit];
  }
  if (Array.isArray(localGates) && localGates.length) {
    return localGates;
  }
  return Array.isArray(hardFilters) ? hardFilters : [];
}

function globalGateFrom(decision: WcaDecision | null): WcaGlobalGateResult | undefined {
  return decision?.globalGateResult ?? decision?.global_gate_result;
}

export function renderWcaGatePanel(decision: WcaDecision | null): string {
  const localGates = localGatesFrom(decision);
  const local = localGates[0];
  const global = globalGateFrom(decision);
  const blockedCount = localGates.filter((gate) => gate.blocksEntry ?? gate.blocks_entry ?? gate.entryBlocked ?? gate.entry_blocked).length;
  return `
    <details class="wca-expander">
      <summary class="algo-expand-toggle wca-expand-summary">
        <span>WCA-local Gates and Global Gates</span>
        <strong>${escapeHtml(localGates.length ? `${blockedCount} blocked / ${localGates.length} checks` : statusLabel(global?.status ?? global?.decision ?? "waiting"))}</strong>
        <b>+</b>
      </summary>
      <div class="wca-expander-body">
        <div class="wca-gate-columns">
          <div class="regime-section-card wca-gate-card">
            <div class="regime-section-head"><strong>WCA-local block</strong><span>${escapeHtml(localGates.length ? `${blockedCount}/${localGates.length} blocking` : "unavailable")}</span></div>
            ${renderLocalGates(localGates)}
          </div>
          <div class="regime-section-card wca-gate-card">
            <div class="regime-section-head"><strong>Global account block</strong><span>${escapeHtml(statusLabel(global?.status ?? global?.decision ?? "unavailable"))}</span></div>
            ${renderGlobalGate(global)}
          </div>
        </div>
        <div class="wca-note">ML/Meta result - separate algorithm. WCA displays it separately and does not use it as a gate in this panel.</div>
      </div>
    </details>
  `;
}

function renderLocalGates(localGates: WcaLocalGateResult[]): string {
  if (!localGates.length) {
    return `<div class="wca-empty">Data unavailable - no backend WCA-local gate result.</div>`;
  }
  const local = localGates[0];
  const allowEntry = !localGates.some((gate) => gate.blocksEntry ?? gate.blocks_entry ?? gate.entryBlocked ?? gate.entry_blocked);
  return `
    <div class="regime-detail-grid">
      <div class="regime-detail-item"><span>Status</span><strong>${escapeHtml(statusLabel(local.status ?? local.decision))}</strong></div>
      <div class="regime-detail-item"><span>Allow entry</span><strong>${allowEntry ? "yes" : "no"}</strong></div>
      <div class="regime-detail-item wide"><span>Reason</span><strong>${escapeHtml(reasonText(local) || "backend local gate engine")}</strong></div>
    </div>
    <div class="wca-gate-list">${localGates.map(renderGateEvaluation).join("")}</div>
  `;
}

function renderGlobalGate(global: WcaGlobalGateResult | undefined): string {
  if (!global) {
    return `<div class="wca-empty">Data unavailable - no backend global gate result.</div>`;
  }
  return `
      <div class="regime-detail-grid">
      <div class="regime-detail-item"><span>Status</span><strong>${escapeHtml(statusLabel(global.status ?? global.decision))}</strong></div>
      <div class="regime-detail-item"><span>Requested quantity</span><strong>${escapeHtml(formatInteger(global.requestedQuantity ?? global.requested_quantity ?? global.proposedQuantity ?? global.proposed_quantity))}</strong></div>
      <div class="regime-detail-item"><span>Approved quantity</span><strong>${escapeHtml(formatInteger(global.approvedQuantity ?? global.approved_quantity ?? global.allowedQuantity ?? global.allowed_quantity))}</strong></div>
      <div class="regime-detail-item"><span>Allow exit</span><strong>${global.allowExit ?? global.allow_exit ?? global.riskReducingExitPermitted ?? global.risk_reducing_exit_permitted ? "yes" : "no"}</strong></div>
    </div>
    ${renderStringList("Blockers", global.blockers)}
    ${renderStringList("Warnings", global.warnings)}
  `;
}

function renderGateEvaluation(gate: WcaLocalGateEvaluation): string {
  return `
    <div class="wca-gate-row regime-routing-card">
      <strong>${escapeHtml(stringValue(gate.gateId, gate.gate_id, "gate"))}</strong>
      <div>
        <span>${escapeHtml(statusLabel(gate.status))}</span>
        <span>${escapeHtml(stringValue(gate.reason, gate.detail, reasonText(gate), "backend gate check"))}</span>
        <span>Value: ${escapeHtml(String(gate.evaluatedValue ?? gate.evaluated_value ?? "n/a"))} / Required: ${escapeHtml(
          String(gate.requiredValue ?? gate.required_value ?? "n/a"),
        )}</span>
      </div>
    </div>
  `;
}

function renderStringList(label: string, items: string[] | undefined): string {
  if (!items?.length) {
    return `<span>${escapeHtml(label)}: none</span>`;
  }
  return `<span>${escapeHtml(label)}: ${items.map((item) => escapeHtml(item)).join(", ")}</span>`;
}
