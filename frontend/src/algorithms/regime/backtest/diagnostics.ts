import {
  REGIME_BACKTEST_FILE_INVENTORY,
  REGIME_BACKTEST_OWNED_CAPABILITIES,
  type RegimeBacktestInventoryStatus,
  type RegimeBacktestResult,
} from "./types.ts";

export function regimeBacktestInventoryStatus(): RegimeBacktestInventoryStatus {
  return {
    algorithmId: "regime",
    authoritativeEngine: "frontend/src/algorithms/regime/backtest/engine.ts",
    files: REGIME_BACKTEST_FILE_INVENTORY,
    ownedCapabilities: REGIME_BACKTEST_OWNED_CAPABILITIES,
    isolatedFromWca: true,
  };
}

export function regimeBacktestDiagnostics(result: RegimeBacktestResult): string[] {
  return [`decisions:${result.decisions.length}`, `trades:${result.trades.length}`];
}
