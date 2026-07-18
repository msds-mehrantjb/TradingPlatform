export type RegimeBacktestTrade = {
  tradeId?: string;
  side: "Long" | "Short";
  quantity: number;
  entryAt: string;
  entryPrice: number;
  exitAt: string;
  exitPrice: number;
  exitReason: string;
  pnl: number;
  rMultiple: number;
};

export type RegimeBacktestMetrics = {
  netProfit: number;
  tradeCount: number;
  winRate: number;
  netReturn: number;
  maximumDrawdown: number;
  profitFactor: number | null;
  noTradePercentage: number;
};

export type RegimeBacktestWalkForwardFold = {
  accepted: boolean;
  folds?: number;
  walkForwardStable?: boolean;
  holdoutUntouched?: boolean;
  splitIndex?: number;
  tradeCount?: number;
};

export type RegimeBacktestResult = {
  algorithmId: "regime";
  engineVersion: string;
  authoritativeEngine: "backend.app.algorithms.regime.backtest.engine";
  symbol: string;
  candles: number;
  decisions: Array<Record<string, unknown>>;
  trades: RegimeBacktestTrade[];
  totalPnl: number;
  metrics: RegimeBacktestMetrics;
  walkForward: RegimeBacktestWalkForwardFold[];
  diagnostics: string[];
  artifactPath: string;
  cacheKey: string;
  storageKey: string;
  failureMessage: string | null;
};
