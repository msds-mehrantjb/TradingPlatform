export function escapeHtml(value: unknown): string {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

export function numberValue(...values: unknown[]): number | null {
  for (const value of values) {
    const numeric = Number(value);
    if (Number.isFinite(numeric)) {
      return numeric;
    }
  }
  return null;
}

export function stringValue(...values: unknown[]): string {
  for (const value of values) {
    if (value !== undefined && value !== null && String(value).trim()) {
      return String(value);
    }
  }
  return "";
}

export function formatNumber(value: unknown, digits = 2): string {
  const numeric = numberValue(value);
  return numeric === null ? "n/a" : numeric.toFixed(digits);
}

export function formatPercent(value: unknown, digits = 1): string {
  const numeric = numberValue(value);
  if (numeric === null) {
    return "n/a";
  }
  const percent = Math.abs(numeric) <= 1 ? numeric * 100 : numeric;
  return `${percent.toFixed(digits)}%`;
}

export function formatCurrency(value: unknown): string {
  const numeric = numberValue(value);
  if (numeric === null) {
    return "n/a";
  }
  return numeric.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  });
}

export function formatInteger(value: unknown): string {
  const numeric = numberValue(value);
  return numeric === null ? "0" : Math.floor(numeric).toLocaleString();
}

export function sideLabel(value: unknown): string {
  const side = String(value ?? "HOLD").toUpperCase();
  if (side === "BUY") {
    return "Buy";
  }
  if (side === "SELL") {
    return "Sell";
  }
  return "Hold";
}

export function sideClass(value: unknown): string {
  const side = String(value ?? "HOLD").toLowerCase();
  return side === "buy" || side === "sell" ? side : "hold";
}

export function statusLabel(value: unknown): string {
  return stringValue(value, "not_applicable")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function reasonText(record: { reasonCodes?: string[]; reason_codes?: string[]; reason?: string; detail?: string } | undefined): string {
  if (!record) {
    return "";
  }
  const reasons = record.reasonCodes ?? record.reason_codes;
  if (Array.isArray(reasons) && reasons.length) {
    return reasons.join(", ");
  }
  return stringValue(record.reason, record.detail);
}

export function renderReadonlySettingRows(
  baseline: Record<string, unknown> | undefined,
  effective: Record<string, unknown> | undefined,
  keys: string[],
  reasonByKey: Record<string, string> = {},
): string {
  return keys
    .map((key) => {
      const baselineValue = baseline?.[key];
      const effectiveValue = effective?.[key];
      const reason = reasonByKey[key] || "backend effective profile";
      return `
        <div class="wca-setting-row">
          <span>${escapeHtml(labelFromKey(key))}</span>
          <strong>Baseline: ${escapeHtml(formatSettingValue(baselineValue))}</strong>
          <strong>Effective: ${escapeHtml(formatSettingValue(effectiveValue))}</strong>
          <em>Reason: ${escapeHtml(reason)}</em>
        </div>
      `;
    })
    .join("");
}

export function formatSettingValue(value: unknown): string {
  if (typeof value === "number") {
    if (Math.abs(value) <= 1) {
      return formatPercent(value, 2);
    }
    return formatNumber(value, 2);
  }
  if (typeof value === "boolean") {
    return value ? "enabled" : "disabled";
  }
  return stringValue(value, "n/a");
}

export function labelFromKey(key: string): string {
  return key
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

