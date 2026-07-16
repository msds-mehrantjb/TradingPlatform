import { escapeHtml, formatInteger, reasonText, statusLabel, stringValue } from "./formatters";
import type { WcaDecision, WcaGlobalGateResult, WcaLocalGateEvaluation, WcaLocalGateResult } from "./types";

function localGateFrom(decision: WcaDecision | null): WcaLocalGateResult | undefined {
  return decision?.localGateResult ?? decision?.local_gate_result;
}

function globalGateFrom(decision: WcaDecision | null): WcaGlobalGateResult | undefined {
  return decision?.globalGateResult ?? decision?.global_gate_result;
}

export function renderWcaGatePanel(decision: WcaDecision | null): string {
  const local = localGateFrom(decision);
  const global = globalGateFrom(decision);
  return `
    <section class="wca-section">
      <div class="algo-section-title">WCA-local Gates and Global Gates</div>
      <div class="wca-gate-columns">
        <div class="wca-gate-card">
          <strong>WCA-local block</strong>
          ${renderLocalGate(local)}
        </div>
        <div class="wca-gate-card">
          <strong>Global account block</strong>
          ${renderGlobalGate(global)}
        </div>
      </div>
      <div class="wca-note">ML/Meta result - separate algorithm. WCA displays it separately and does not use it as a gate in this panel.</div>
    </section>
  `;
}

function renderLocalGate(local: WcaLocalGateResult | undefined): string {
  if (!local) {
    return `<div class="wca-empty">Data unavailable - no backend WCA-local gate result.</div>`;
  }
  const evaluations = local.evaluations ?? local.gates ?? [];
  return `
    <span>Status: ${escapeHtml(statusLabel(local.status ?? local.decision))}</span>
    <span>Allow entry: ${local.allowEntry ?? local.allow_entry ? "yes" : "no"}</span>
    <span>Reason: ${escapeHtml(reasonText(local) || "backend local gate engine")}</span>
    ${evaluations.length ? `<div class="wca-gate-list">${evaluations.map(renderGateEvaluation).join("")}</div>` : `<span>Not applicable - no individual gate rows reported.</span>`}
  `;
}

function renderGlobalGate(global: WcaGlobalGateResult | undefined): string {
  if (!global) {
    return `<div class="wca-empty">Data unavailable - no backend global gate result.</div>`;
  }
  return `
    <span>Status: ${escapeHtml(statusLabel(global.status ?? global.decision))}</span>
    <span>Requested quantity: ${escapeHtml(formatInteger(global.requestedQuantity ?? global.requested_quantity))}</span>
    <span>Globally approved quantity: ${escapeHtml(formatInteger(global.approvedQuantity ?? global.approved_quantity))}</span>
    <span>Allow exit: ${global.allowExit ?? global.allow_exit ? "yes" : "no"}</span>
    ${renderStringList("Blockers", global.blockers)}
    ${renderStringList("Warnings", global.warnings)}
  `;
}

function renderGateEvaluation(gate: WcaLocalGateEvaluation): string {
  return `
    <div class="wca-gate-row">
      <strong>${escapeHtml(stringValue(gate.gateId, gate.gate_id, "gate"))}</strong>
      <span>${escapeHtml(statusLabel(gate.status))}</span>
      <span>${escapeHtml(stringValue(gate.reason, gate.detail, reasonText(gate), "backend gate check"))}</span>
      <small>Value: ${escapeHtml(String(gate.evaluatedValue ?? gate.evaluated_value ?? "n/a"))} / Required: ${escapeHtml(
        String(gate.requiredValue ?? gate.required_value ?? "n/a"),
      )}</small>
    </div>
  `;
}

function renderStringList(label: string, items: string[] | undefined): string {
  if (!items?.length) {
    return `<span>${escapeHtml(label)}: none</span>`;
  }
  return `<span>${escapeHtml(label)}: ${items.map((item) => escapeHtml(item)).join(", ")}</span>`;
}

