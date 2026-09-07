/**
 * The Voting Ensemble trading-settings editor: types, client and rendering.
 *
 * The panel is driven entirely by the backend catalogue rather than a local list of
 * fields. That is the point of the module. The dashboard used to keep its own
 * `defaultTradingSettings()` for this algorithm, and it had drifted: it offered a
 * starting capital of 25,000 against a backend baseline of 100,000, two controls the
 * backend had removed because sizing never read them, and none of the stop, target or
 * vote-threshold parameters the pipeline actually trades on. Nothing typed into it
 * reached the algorithm, because the live path resolved its settings from the baseline
 * and never looked at the browser.
 *
 * Every field rendered here comes from `GET /api/voting-ensemble/trading-settings`,
 * which lists only parameters with a real read site in the pipeline, and every save goes
 * back through `PUT` to the store the trading paths resolve against.
 */

import { BACKTEST_API_CANDIDATES } from "../api/client";

export type VotingEnsembleSettingKind = "number" | "integer" | "boolean" | "time" | "choice";
export type VotingEnsembleSettingBinding = "live" | "backtest" | "both";

export type VotingEnsembleSettingField = {
  key: string;
  label: string;
  group: string;
  kind: VotingEnsembleSettingKind;
  binding: VotingEnsembleSettingBinding;
  /** The read site that makes this value take effect, straight from the backend registry. */
  bindsAt: string;
  help: string;
  minimum: number | null;
  maximum: number | null;
  step: number | null;
  choices: string[];
  unit: string;
  baseline: number | string | boolean | null;
  value: number | string | boolean | null;
  overridden: boolean;
};

export type VotingEnsembleSettingGroup = {
  id: string;
  label: string;
  description: string;
  fields: VotingEnsembleSettingField[];
};

export type VotingEnsembleInertParameter = {
  key: string;
  label: string;
  why: string;
  baseline: unknown;
};

export type VotingEnsembleTradingSettingsView = {
  algorithmId: string;
  catalogVersion: string;
  settingsVersion: string;
  baselineVersion: string;
  configurationHash: string;
  baselineConfigurationHash: string;
  matchesBaseline: boolean;
  groups: VotingEnsembleSettingGroup[];
  overrides: Record<string, unknown>;
  overriddenKeys: string[];
  ignoredKeys: string[];
  updatedAt: string;
  updatedBy: string;
  reason: string;
  paperOnly: boolean;
  liveTradingEnabled: boolean;
  positionSizing: string;
  inertParameters: VotingEnsembleInertParameter[];
  reasonCodes: string[];
};

export type VotingEnsembleSettingValue = number | string | boolean;

const REQUEST_TIMEOUT_MS = 15000;

async function fetchVotingEnsembleJson(path: string, init: RequestInit = {}): Promise<Record<string, unknown>> {
  let lastMessage = "Voting Ensemble trading-settings route unavailable";
  for (const baseUrl of BACKTEST_API_CANDIDATES) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(`${baseUrl}/api/voting-ensemble${path}`, {
        ...init,
        signal: controller.signal,
        headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
      });
      if (response.ok) {
        return (await response.json()) as Record<string, unknown>;
      }
      lastMessage = await readableError(response);
      // A 404 means this candidate has not loaded the route; try the next base URL.
      // Anything else is a real answer from the right server, so stop and report it.
      if (response.status !== 404) {
        throw new Error(lastMessage);
      }
    } catch (error) {
      lastMessage = error instanceof Error ? error.message : lastMessage;
    } finally {
      window.clearTimeout(timer);
    }
  }
  throw new Error(lastMessage);
}

async function readableError(response: Response): Promise<string> {
  if (response.status === 404) {
    return "Voting Ensemble trading-settings route not loaded; restart the FastAPI backend.";
  }
  try {
    const payload = (await response.json()) as { detail?: { errors?: string[] } | string };
    const detail = payload?.detail;
    if (detail && typeof detail === "object" && Array.isArray(detail.errors) && detail.errors.length) {
      return detail.errors.join("; ");
    }
    if (typeof detail === "string" && detail) {
      return detail;
    }
  } catch {
    // Fall through to the status line below.
  }
  return `Voting Ensemble trading settings unavailable (${response.status})`;
}

export async function fetchVotingEnsembleTradingSettings(): Promise<VotingEnsembleTradingSettingsView> {
  return normalizeView(await fetchVotingEnsembleJson("/trading-settings"));
}

export async function saveVotingEnsembleTradingSettings(
  overrides: Record<string, VotingEnsembleSettingValue>,
  options: { reason?: string } = {},
): Promise<VotingEnsembleTradingSettingsView> {
  const payload = await fetchVotingEnsembleJson("/trading-settings", {
    method: "PUT",
    body: JSON.stringify({
      overrides,
      updatedBy: "dashboard",
      reason: options.reason || "voting_ensemble.dashboard.trading_settings_update",
    }),
  });
  return normalizeView(payload);
}

export async function resetVotingEnsembleTradingSettings(): Promise<VotingEnsembleTradingSettingsView> {
  const payload = await fetchVotingEnsembleJson("/trading-settings", {
    method: "PUT",
    body: JSON.stringify({ reset: true, updatedBy: "dashboard", reason: "voting_ensemble.dashboard.reset_to_baseline" }),
  });
  return normalizeView(payload);
}

function normalizeView(payload: Record<string, unknown>): VotingEnsembleTradingSettingsView {
  const groups = Array.isArray(payload.groups) ? (payload.groups as VotingEnsembleSettingGroup[]) : [];
  return {
    algorithmId: String(payload.algorithmId ?? "voting_ensemble"),
    catalogVersion: String(payload.catalogVersion ?? ""),
    settingsVersion: String(payload.settingsVersion ?? ""),
    baselineVersion: String(payload.baselineVersion ?? ""),
    configurationHash: String(payload.configurationHash ?? ""),
    baselineConfigurationHash: String(payload.baselineConfigurationHash ?? ""),
    matchesBaseline: Boolean(payload.matchesBaseline),
    groups,
    overrides: (payload.overrides as Record<string, unknown>) ?? {},
    overriddenKeys: Array.isArray(payload.overriddenKeys) ? (payload.overriddenKeys as string[]) : [],
    ignoredKeys: Array.isArray(payload.ignoredKeys) ? (payload.ignoredKeys as string[]) : [],
    updatedAt: String(payload.updatedAt ?? ""),
    updatedBy: String(payload.updatedBy ?? ""),
    reason: String(payload.reason ?? ""),
    paperOnly: payload.paperOnly !== false,
    liveTradingEnabled: Boolean(payload.liveTradingEnabled),
    positionSizing: String(payload.positionSizing ?? ""),
    inertParameters: Array.isArray(payload.inertParameters) ? (payload.inertParameters as VotingEnsembleInertParameter[]) : [],
    reasonCodes: Array.isArray(payload.reasonCodes) ? (payload.reasonCodes as string[]) : [],
  };
}

/** The value a field should show: the operator's unsaved edit if there is one. */
export function fieldValue(
  field: VotingEnsembleSettingField,
  draft: Record<string, VotingEnsembleSettingValue>,
): VotingEnsembleSettingValue {
  const pending = draft[field.key];
  if (pending !== undefined) {
    return pending;
  }
  if (field.value === null) {
    return field.kind === "boolean" ? false : "";
  }
  return field.value as VotingEnsembleSettingValue;
}

/**
 * Read one input back into a typed value.
 *
 * Returns `undefined` for a field left blank so a half-typed number is not saved as 0.
 */
export function readFieldInput(
  field: VotingEnsembleSettingField,
  element: HTMLInputElement | HTMLSelectElement,
): VotingEnsembleSettingValue | undefined {
  if (field.kind === "boolean") {
    return (element as HTMLInputElement).checked;
  }
  const raw = element.value;
  if (field.kind === "choice" || field.kind === "time") {
    return raw;
  }
  if (raw.trim() === "") {
    return undefined;
  }
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) {
    return undefined;
  }
  return field.kind === "integer" ? Math.round(parsed) : parsed;
}

/** Edits that differ from what the backend already has. Sending the rest is noise. */
export function changedOverrides(
  view: VotingEnsembleTradingSettingsView,
  draft: Record<string, VotingEnsembleSettingValue>,
): Record<string, VotingEnsembleSettingValue> {
  const merged: Record<string, VotingEnsembleSettingValue> = {};
  for (const group of view.groups) {
    for (const field of group.fields) {
      const value = fieldValue(field, draft);
      if (value === "" || value === null || value === undefined) {
        continue;
      }
      // Keep a field only when it differs from the baseline, so the stored override set
      // stays a description of what the operator deliberately changed.
      if (!valuesMatch(value, field.baseline)) {
        merged[field.key] = value;
      }
    }
  }
  return merged;
}

export function hasPendingEdits(
  view: VotingEnsembleTradingSettingsView,
  draft: Record<string, VotingEnsembleSettingValue>,
): boolean {
  for (const group of view.groups) {
    for (const field of group.fields) {
      const pending = draft[field.key];
      if (pending !== undefined && !valuesMatch(pending, field.value)) {
        return true;
      }
    }
  }
  return false;
}

function valuesMatch(left: unknown, right: unknown): boolean {
  if (typeof left === "number" && typeof right === "number") {
    // Percentages and R multiples round-trip through the input as text, so compare with
    // a tolerance rather than exactly.
    return Math.abs(left - right) < 1e-9;
  }
  return String(left) === String(right);
}
