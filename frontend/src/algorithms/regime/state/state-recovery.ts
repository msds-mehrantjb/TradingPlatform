import type { MarketRegimeId, RegimeConditionSnapshot, RegimeHysteresisSnapshot } from "../types.ts";

export type RecoveredRegimeState = {
  previous: RegimeConditionSnapshot | null;
  previousRegime: MarketRegimeId;
  previousConfidence: number;
  previousDwellBars: number;
  regimeStartTime: string | null;
  unknownRegimeCount: number;
};

export function recoverRegimeHysteresisState(
  previousHysteresis: RegimeHysteresisSnapshot,
  contextKey: string,
): RecoveredRegimeState {
  const previous = previousHysteresis?.contextKey === contextKey ? previousHysteresis : null;
  return {
    previous,
    previousRegime: previous?.confirmedRegime ?? previous?.rawRegime ?? (previous?.key as MarketRegimeId | undefined) ?? "choppy_mixed",
    previousConfidence: previous?.confirmedConfidence ?? previous?.confidence ?? 0,
    previousDwellBars: previous?.dwellBars ?? 0,
    regimeStartTime: previous?.regimeStartTime ?? previous?.timestamp ?? null,
    unknownRegimeCount: previous?.unknownRegimeCount ?? 0,
  };
}
