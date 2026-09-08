/**
 * Exit geometry for the Voting Ensemble, derived from the backend-resolved settings.
 *
 * Deliberately free of imports: this is the arithmetic that decides the stop and target
 * prices the trading window shows, and keeping it in a module with no HTTP client
 * attached is what lets it be tested directly.
 *
 * The rules mirror `_exit_geometry` in
 * backend/app/algorithms/voting_ensemble/service.py. They are restated rather than
 * shared, since one side is Python and the other TypeScript, so the parity test in
 * frontend/tests/VotingEnsembleExitGeometry.test.ts is what holds the two together.
 */

/** The subset of the resolved settings the trading window needs. */
export type VotingEnsembleTradingGeometry = {
  atrMultiplier: number;
  fixedStopDistanceDollars: number;
  minimumStopDistanceDollars: number;
  takeProfitR: number;
  minimumTakeProfitR: number;
  structuralTargets: boolean;
  breakevenTriggerR: number;
  trailingStopR: number;
  trailingEnabled: boolean;
  maximumHoldingMinutes: number;
  slippagePerShare: number;
  startingCapital: number;
  maxDailyLossPercent: number;
  entriesBlocked: boolean;
  configurationHash: string;
};

export type VotingEnsembleExitGeometry = {
  stopDistance: number;
  /** "atr" or "fixed_dollars", the same two sources the backend reports. */
  stopSource: "atr" | "fixed_dollars";
  stopPrice: number;
  targetPrice: number;
  targetDistance: number;
  breakevenPrice: number | null;
  trailingDistance: number | null;
  /** True when a structural level could tighten this target on the live decision. */
  structuralTargetPossible: boolean;
};

/** The shape `tradingGeometryFromView` reads; the full view is a superset of it. */
export type GeometrySourceView = {
  configurationHash: string;
  entriesBlocked: boolean;
  groups: Array<{ fields: Array<{ key: string; value: unknown }> }>;
};

const BASELINE_FALLBACKS = {
  atrMultiplier: 1.5,
  fixedStopDistanceDollars: 1,
  minimumStopDistanceDollars: 0.01,
  takeProfitR: 1.5,
  minimumTakeProfitR: 1,
  breakevenTriggerR: 1,
  trailingStopR: 1,
  maximumHoldingMinutes: 30,
  slippagePerShare: 0.02,
  startingCapital: 100000,
  maxDailyLossPercent: 2,
} as const;

/**
 * Read the geometry out of the backend catalogue.
 *
 * A field the catalogue does not carry falls back to the documented baseline value, never
 * to zero: a zero stop multiplier or zero capital would quietly produce nonsense prices
 * rather than an obviously missing one.
 */
export function tradingGeometryFromView(view: GeometrySourceView | null): VotingEnsembleTradingGeometry | null {
  if (!view) {
    return null;
  }
  const values = new Map<string, unknown>();
  for (const group of view.groups) {
    for (const field of group.fields) {
      if (field.value !== null && field.value !== undefined) {
        values.set(field.key, field.value);
      }
    }
  }
  const num = (key: string, fallback: number) => {
    const value = values.get(key);
    return typeof value === "number" && Number.isFinite(value) ? value : fallback;
  };
  const bool = (key: string, fallback: boolean) => {
    const value = values.get(key);
    return typeof value === "boolean" ? value : fallback;
  };
  return {
    atrMultiplier: num("stopAtrMultiplier", BASELINE_FALLBACKS.atrMultiplier),
    fixedStopDistanceDollars: num("fixedStopDistanceDollars", BASELINE_FALLBACKS.fixedStopDistanceDollars),
    minimumStopDistanceDollars: num("minimumStopDistanceDollars", BASELINE_FALLBACKS.minimumStopDistanceDollars),
    takeProfitR: num("takeProfitR", BASELINE_FALLBACKS.takeProfitR),
    minimumTakeProfitR: num("minimumTakeProfitR", BASELINE_FALLBACKS.minimumTakeProfitR),
    structuralTargets: bool("structuralTargets", true),
    breakevenTriggerR: num("breakevenTriggerR", BASELINE_FALLBACKS.breakevenTriggerR),
    trailingStopR: num("trailingStopR", BASELINE_FALLBACKS.trailingStopR),
    trailingEnabled: bool("trailingStopEnabled", true),
    maximumHoldingMinutes: num("maximumHoldingMinutes", BASELINE_FALLBACKS.maximumHoldingMinutes),
    slippagePerShare: num("slippagePerShare", BASELINE_FALLBACKS.slippagePerShare),
    startingCapital: num("startingCapital", BASELINE_FALLBACKS.startingCapital),
    maxDailyLossPercent: num("maxDailyLossPercent", BASELINE_FALLBACKS.maxDailyLossPercent),
    entriesBlocked: Boolean(view.entriesBlocked),
    configurationHash: String(view.configurationHash ?? ""),
  };
}

/**
 * The stop, target and management plan for a position opened at `entryPrice`.
 *
 * The rule order matters and used to be inverted in the dashboard: the backend takes the
 * ATR stop first and falls back to the fixed dollar distance only when the session has no
 * ATR, while the old client returned the fixed distance whenever it was above zero and so
 * never reached the ATR branch at all.
 *
 * The structural-target substitution is deliberately not reproduced. It depends on VWAP
 * and session levels resolved at decision time, and a second implementation would drift
 * from the first; `structuralTargetPossible` says a level may tighten the target instead
 * of inventing a number that disagrees with the algorithm.
 */
export function exitGeometryForEntry(
  entryPrice: number,
  geometry: VotingEnsembleTradingGeometry,
  sessionAtr: number,
  side: "Buy" | "Sell" = "Buy",
): VotingEnsembleExitGeometry {
  const useAtr = sessionAtr > 0 && geometry.atrMultiplier > 0;
  const rawDistance = useAtr ? sessionAtr * geometry.atrMultiplier : geometry.fixedStopDistanceDollars;
  const stopDistance = Math.max(rawDistance, geometry.minimumStopDistanceDollars, 0.01);
  const targetDistance = stopDistance * geometry.takeProfitR;
  const direction = side === "Sell" ? -1 : 1;
  const trailingDistance =
    geometry.trailingEnabled && geometry.trailingStopR > 0 ? stopDistance * geometry.trailingStopR : null;
  const breakevenPrice =
    geometry.trailingEnabled && geometry.breakevenTriggerR > 0
      ? entryPrice + direction * stopDistance * geometry.breakevenTriggerR
      : null;
  return {
    stopDistance,
    stopSource: useAtr ? "atr" : "fixed_dollars",
    stopPrice: entryPrice - direction * stopDistance,
    targetPrice: entryPrice + direction * targetDistance,
    targetDistance,
    breakevenPrice,
    trailingDistance,
    structuralTargetPossible: geometry.structuralTargets && geometry.minimumTakeProfitR < geometry.takeProfitR,
  };
}
