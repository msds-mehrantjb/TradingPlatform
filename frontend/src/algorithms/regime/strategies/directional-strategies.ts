export const REGIME_DIRECTIONAL_STRATEGY_INVENTORY = [
  { key: "C1", id: "moving_average_trend", name: "Moving Average Trend", family: "Trend/momentum" },
  { key: "C3", id: "trend_pullback", name: "Trend Pullback", family: "Trend/pullback" },
  { key: "C4", id: "rsi_mean_reversion", name: "RSI Mean Reversion", family: "Mean reversion" },
  { key: "C5", id: "bollinger_band_mean_reversion", name: "Bollinger Band Mean Reversion", family: "Mean reversion" },
  { key: "C6", id: "opening_range_breakout", name: "Opening Range Breakout", family: "Breakout" },
  { key: "C7", id: "intraday_breakout", name: "Intraday Breakout", family: "Breakout" },
  { key: "C8", id: "macd_momentum", name: "MACD Momentum", family: "Momentum" },
  { key: "C9", id: "market_structure", name: "Market Structure", family: "Structure/trend" },
  { key: "C10", id: "gap_continuation_fade", name: "Gap Continuation/Fade", family: "Event/gap" },
  { key: "R1", id: "vwap_trend_continuation", name: "VWAP Trend Continuation", family: "Trend" },
  { key: "R2", id: "vwap_mean_reversion", name: "VWAP Mean Reversion", family: "Mean reversion" },
  { key: "R3", id: "failed_breakout_reversal", name: "Failed Breakout Reversal", family: "Reversal" },
  { key: "R4", id: "liquidity_sweep_reversal", name: "Liquidity Sweep Reversal", family: "Reversal" },
  { key: "R7", id: "volatility_breakout", name: "Volatility Breakout", family: "Breakout" },
] as const;

