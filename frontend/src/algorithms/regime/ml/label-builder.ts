import type { RegimeLabelBuildInput, RegimeOfflineLabel } from "./types.ts";

export function buildOfflineRegimeLabel(input: RegimeLabelBuildInput): RegimeOfflineLabel | null {
  const futureCandles = input.futureCandles.slice(0, input.futureObservationWindowBars);
  if (!futureCandles.length) {
    return null;
  }
  const first = futureCandles[0];
  const last = futureCandles.at(-1)!;
  const high = Math.max(...futureCandles.map((candle) => candle.high));
  const low = Math.min(...futureCandles.map((candle) => candle.low));
  const returnThreshold = input.thresholds.returnThreshold ?? 0.002;
  const rangeThreshold = input.thresholds.rangeThreshold ?? 0.004;
  const realizedReturn = first.close ? (last.close - first.close) / first.close : 0;
  const realizedRange = first.close ? (high - low) / first.close : 0;
  const realizedRegime =
    realizedRange >= rangeThreshold
      ? realizedReturn >= returnThreshold
        ? "strong_uptrend"
        : realizedReturn <= -returnThreshold
          ? "strong_downtrend"
          : "intraday_expansion"
      : "range_bound";
  return {
    algorithm_id: "regime",
    label_definition_version: input.labelDefinitionVersion,
    future_observation_window_bars: input.futureObservationWindowBars,
    thresholds: input.thresholds,
    label_timestamp: last.timestamp,
    source_candle_range: { start: first.timestamp, end: last.timestamp },
    realizedRegime,
    transitionOccurred: realizedRegime !== "range_bound",
  };
}
