/**
 * Markup for the Voting Ensemble trading-settings editor.
 *
 * Kept apart from the client so the rendering can be read on its own, and so `main.ts`
 * only has to own the state and the event wiring.
 *
 * Two things here are deliberate rather than decorative. Each field carries the read site
 * the backend reported, so an operator can see what a control actually moves instead of
 * trusting a label. And the parameters the backend reports as inert are rendered too, in
 * their own list with the reason, because a control that is missing without explanation
 * reads as an oversight.
 */

import {
  fieldValue,
  type VotingEnsembleSettingField,
  type VotingEnsembleSettingValue,
  type VotingEnsembleTradingSettingsView,
} from "./voting-ensemble";

export type VotingEnsembleSettingsPanelState = {
  view: VotingEnsembleTradingSettingsView | null;
  status: "idle" | "loading" | "ready" | "saving" | "error";
  warning: string;
  draft: Record<string, VotingEnsembleSettingValue>;
  expanded: boolean;
  expandedGroups: Record<string, boolean>;
  showInert: boolean;
  dirty: boolean;
};

export function renderVotingEnsembleTradingSettings(
  panel: VotingEnsembleSettingsPanelState,
  extraSections = "",
): string {
  const { view, status } = panel;
  return `
    <div class="trading-settings-panel ve-settings-panel" data-status="${escapeHtml(status)}" data-expanded="${String(panel.expanded)}">
      <button id="tradingSettingsToggle" class="trading-settings-head" type="button" aria-expanded="${String(panel.expanded)}" aria-controls="tradingSettingsBody">
        <span class="trading-settings-title">
          <b>${panel.expanded ? "-" : "+"}</b>
          <strong>Trading Settings</strong>
        </span>
        <span class="trading-settings-summary">${escapeHtml(summaryText(panel))}</span>
      </button>
      <div id="tradingSettingsBody" class="trading-settings-body" ${panel.expanded ? "" : "hidden"}>
        ${panel.warning ? `<p class="trading-settings-warning" role="alert">${escapeHtml(panel.warning)}</p>` : ""}
        ${view ? renderGroups(panel, view) : renderPlaceholder(status)}
        ${view ? renderActions(panel) : ""}
        ${extraSections}
        ${view ? renderInertSection(panel, view) : ""}
      </div>
    </div>
  `;
}

function renderPlaceholder(status: VotingEnsembleSettingsPanelState["status"]): string {
  const message =
    status === "error"
      ? "Voting Ensemble trading settings could not be loaded. The values below the algorithm trades on come from the backend, so nothing is editable until it answers."
      : "Loading the Voting Ensemble trading settings from the backend.";
  return `<p class="ve-settings-placeholder">${escapeHtml(message)}</p>`;
}

function renderGroups(panel: VotingEnsembleSettingsPanelState, view: VotingEnsembleTradingSettingsView): string {
  return view.groups
    .map((group) => {
      const expanded = panel.expandedGroups[group.id] ?? true;
      return `
        <section class="ve-settings-group" data-group="${escapeHtml(group.id)}" data-expanded="${String(expanded)}">
          <button class="ve-settings-group-head" type="button" data-ve-group-toggle="${escapeHtml(group.id)}" aria-expanded="${String(expanded)}">
            <b>${expanded ? "-" : "+"}</b>
            <strong>${escapeHtml(group.label)}</strong>
            <span class="ve-settings-group-note">${escapeHtml(group.description)}</span>
          </button>
          <div class="ve-settings-grid" ${expanded ? "" : "hidden"}>
            ${group.fields.map((field) => renderField(panel, field)).join("")}
          </div>
        </section>
      `;
    })
    .join("");
}

function renderField(panel: VotingEnsembleSettingsPanelState, field: VotingEnsembleSettingField): string {
  const value = fieldValue(field, panel.draft);
  const pending = panel.draft[field.key] !== undefined;
  const title = `${field.help}\n\nTakes effect at: ${field.bindsAt}\nBaseline: ${formatValue(field.baseline)}`;
  return `
    <label class="ve-settings-field" data-kind="${escapeHtml(field.kind)}" data-overridden="${String(field.overridden)}" data-pending="${String(pending)}" title="${escapeHtml(title)}">
      <span class="ve-settings-label">
        ${escapeHtml(field.label)}${field.unit ? ` <i>${escapeHtml(field.unit)}</i>` : ""}
        ${renderBindingBadge(field)}
      </span>
      ${renderInput(field, value)}
      <span class="ve-settings-baseline">${field.overridden || pending ? `baseline ${escapeHtml(formatValue(field.baseline))}` : "&nbsp;"}</span>
    </label>
  `;
}

function renderBindingBadge(field: VotingEnsembleSettingField): string {
  // "backtest" is the one an operator has to notice: the value shapes replay but does not
  // move a live stop, so labelling it is the difference between a documented limit and a
  // control that quietly does nothing.
  if (field.binding === "backtest") {
    return `<em class="ve-settings-binding" data-binding="backtest" title="Replay engine only; does not change a live order">replay</em>`;
  }
  return "";
}

function renderInput(field: VotingEnsembleSettingField, value: VotingEnsembleSettingValue): string {
  const name = escapeHtml(field.key);
  if (field.kind === "boolean") {
    return `<input data-ve-setting="${name}" type="checkbox" ${value ? "checked" : ""} />`;
  }
  if (field.kind === "choice") {
    return `
      <select data-ve-setting="${name}">
        ${field.choices
          .map((choice) => `<option value="${escapeHtml(choice)}" ${String(choice) === String(value) ? "selected" : ""}>${escapeHtml(choice)}</option>`)
          .join("")}
      </select>
    `;
  }
  if (field.kind === "time") {
    return `<input data-ve-setting="${name}" type="time" value="${escapeHtml(String(value))}" />`;
  }
  const bounds = [
    field.minimum === null ? "" : `min="${field.minimum}"`,
    field.maximum === null ? "" : `max="${field.maximum}"`,
    field.step === null ? "" : `step="${field.step}"`,
  ]
    .filter(Boolean)
    .join(" ");
  return `<input data-ve-setting="${name}" type="number" ${bounds} value="${escapeHtml(String(value))}" />`;
}

function renderActions(panel: VotingEnsembleSettingsPanelState): string {
  const saving = panel.status === "saving";
  return `
    <div class="ve-settings-actions">
      <button type="button" data-ve-settings-action="save" ${saving || !panel.dirty ? "disabled" : ""}>
        ${saving ? "Saving..." : "Save to algorithm"}
      </button>
      <button type="button" data-ve-settings-action="revert" ${saving || !panel.dirty ? "disabled" : ""}>Discard edits</button>
      <button type="button" data-ve-settings-action="reset" ${saving ? "disabled" : ""}>Reset to baseline</button>
      <button type="button" data-ve-settings-action="refresh" ${saving ? "disabled" : ""}>Reload</button>
      <span class="ve-settings-hint">${escapeHtml(actionHint(panel))}</span>
    </div>
  `;
}

function actionHint(panel: VotingEnsembleSettingsPanelState): string {
  if (panel.dirty) {
    return "Unsaved. The algorithm is still trading the saved values until you save.";
  }
  const view = panel.view;
  if (!view) {
    return "";
  }
  if (view.matchesBaseline) {
    return "Running the baseline configuration.";
  }
  const who = view.updatedBy ? ` by ${view.updatedBy}` : "";
  const when = view.updatedAt ? ` ${formatTimestamp(view.updatedAt)}` : "";
  return `${view.overriddenKeys.length} parameter${view.overriddenKeys.length === 1 ? "" : "s"} overridden${who}${when}. Applied from the next bar.`;
}

function renderInertSection(panel: VotingEnsembleSettingsPanelState, view: VotingEnsembleTradingSettingsView): string {
  if (!view.inertParameters.length) {
    return "";
  }
  return `
    <section class="ve-settings-inert" data-expanded="${String(panel.showInert)}">
      <button class="ve-settings-inert-head" type="button" data-ve-settings-action="toggle-inert" aria-expanded="${String(panel.showInert)}">
        <b>${panel.showInert ? "-" : "+"}</b>
        <strong>Not editable (${view.inertParameters.length})</strong>
        <span>Resolved into the settings, read by nothing. Editing them would change no trade.</span>
      </button>
      <div class="ve-settings-inert-body" ${panel.showInert ? "" : "hidden"}>
        <ul>
          ${view.inertParameters
            .map(
              (item) => `
                <li>
                  <strong>${escapeHtml(item.label)}</strong>
                  <code>${escapeHtml(formatValue(item.baseline))}</code>
                  <span>${escapeHtml(item.why)}</span>
                </li>
              `,
            )
            .join("")}
        </ul>
      </div>
    </section>
  `;
}

function summaryText(panel: VotingEnsembleSettingsPanelState): string {
  if (panel.status === "loading") {
    return "Loading settings from the backend.";
  }
  if (panel.status === "error") {
    return panel.warning || "Backend settings unavailable.";
  }
  const view = panel.view;
  if (!view) {
    return "";
  }
  const hash = view.configurationHash ? ` Hash ${view.configurationHash}.` : "";
  const dirty = panel.dirty ? " Unsaved edits." : "";
  const paper = view.paperOnly ? " Paper only." : "";
  return `${view.matchesBaseline ? "Baseline" : `${view.overriddenKeys.length} overridden`}.${hash}${paper}${dirty}`;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "-";
  }
  if (typeof value === "boolean") {
    return value ? "on" : "off";
  }
  return String(value);
}

function formatTimestamp(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function escapeHtml(value: string): string {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}
