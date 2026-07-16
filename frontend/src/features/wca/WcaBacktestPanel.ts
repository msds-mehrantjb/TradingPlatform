import { escapeHtml, formatCurrency, formatInteger, formatNumber, formatPercent, reasonText, sideLabel, stringValue } from "./formatters";
import type { WcaBacktestResult, WcaBacktestTrade } from "./types";

export function renderWcaBacktestPanel(backtest: WcaBacktestResult | null, status = "idle", error: string | null = null): string {
  if (status === "running") {
    return renderBacktestShell("Backtest status: Running", "Backend-authoritative WCA replay is running.", []);
  }
  if (status === "waiting") {
    return renderBacktestShell("Backtest status: Waiting", error || "Waiting for a complete backend dataset.", backtest?.trades ?? []);
  }
  if (status === "error") {
    return renderBacktestShell("Backtest status: Error", error || "Backend WCA backtest failed.", []);
  }
  if (!backtest) {
    return renderBacktestShell("Backtest status: Scheduled", "No backend-authoritative WCA backtest result is loaded.", []);
  }
  const metrics = backtest.metrics ?? {};
  const runConfig = backtest.runConfiguration ?? backtest.run_configuration ?? {};
  const diagnostics = backtest.diagnostics ?? {};
  const summary = `
    <span>Run: <strong>${escapeHtml(stringValue(backtest.runId, backtest.run_id, runConfig.run_id, "backend run unavailable"))}</strong></span>
    <span>Net P/L: <strong>${escapeHtml(formatCurrency(backtest.totalPnl ?? backtest.total_pnl ?? metrics.netProfit))}</strong></span>
    <span>Return: <strong>${escapeHtml(formatPercent(backtest.totalReturnPercent ?? backtest.total_return_percent ?? metrics.totalReturnPercent))}</strong></span>
    <span>Max drawdown: <strong>${escapeHtml(formatCurrency(backtest.maxDrawdown ?? backtest.max_drawdown ?? metrics.maximumClosedEquityDrawdown))}</strong></span>
    <span>Trades: <strong>${escapeHtml(formatInteger(backtest.trades?.length ?? metrics.tradeCount))}</strong></span>
    <span>Reason: <strong>${escapeHtml(reasonText(backtest) || "backend WCA backtest engine")}</strong></span>
  `;
  return `
    <section class="wca-section">
      <div class="wca-section-header">
        <div class="algo-section-title">Backtest Status</div>
        <span class="wca-pill">Backend authoritative</span>
      </div>
      <div class="wca-backtest-summary">${summary}</div>
      <div class="wca-note">Diagnostics: ${escapeHtml(stringValue(diagnostics.status, diagnostics.summary, "available in backend report when returned"))}</div>
      ${renderTradeTable(backtest.trades ?? [])}
    </section>
  `;
}

function renderBacktestShell(title: string, message: string, trades: WcaBacktestTrade[]): string {
  return `
    <section class="wca-section">
      <div class="wca-section-header">
        <div class="algo-section-title">Backtest Status</div>
        <span class="wca-pill">${escapeHtml(title)}</span>
      </div>
      <div class="wca-empty">${escapeHtml(message)}</div>
      ${renderTradeTable(trades)}
    </section>
  `;
}

function renderTradeTable(trades: WcaBacktestTrade[]): string {
  if (!trades.length) {
    return `<div class="wca-empty">No backend WCA trades to display.</div>`;
  }
  return `
    <div class="wca-table-wrap">
      <table class="wca-table">
        <thead>
          <tr>
            <th>Side</th>
            <th>Entry</th>
            <th>Exit</th>
            <th>Qty</th>
            <th>P/L</th>
            <th>Decision</th>
          </tr>
        </thead>
        <tbody>
          ${trades.slice(0, 12).map(renderTradeRow).join("")}
        </tbody>
      </table>
    </div>
  `;
}

function renderTradeRow(trade: WcaBacktestTrade): string {
  return `
    <tr>
      <td>${escapeHtml(sideLabel(trade.side))}</td>
      <td>${escapeHtml(stringValue(trade.entryAt, trade.entry_at, "n/a"))}<span>${escapeHtml(formatNumber(trade.entryPrice ?? trade.entry_price))}</span></td>
      <td>${escapeHtml(stringValue(trade.exitAt, trade.exit_at, "n/a"))}<span>${escapeHtml(formatNumber(trade.exitPrice ?? trade.exit_price))}</span></td>
      <td>${escapeHtml(formatInteger(trade.quantity ?? trade.shares))}</td>
      <td>${escapeHtml(formatCurrency(trade.pnl))}</td>
      <td>${escapeHtml(stringValue(trade.decisionId, trade.decision_id, trade.exitReason, trade.exit_reason, "n/a"))}</td>
    </tr>
  `;
}

