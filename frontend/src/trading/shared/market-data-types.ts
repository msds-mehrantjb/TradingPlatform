export type TradingTimeframe = "1Min" | "3Min" | "5Min" | "15Min" | "1Hour" | "1Day";

export type MarketCandle = {
  provider: string;
  feed: string;
  symbol: string;
  timeframe: TradingTimeframe;
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  trade_count: number | null;
  vwap: number | null;
};

export type MarketDataSnapshot = {
  symbol: string;
  primaryCandles: MarketCandle[];
  oneMinuteCandles?: MarketCandle[];
  fiveMinuteCandles?: MarketCandle[];
  allCandles?: MarketCandle[];
};
