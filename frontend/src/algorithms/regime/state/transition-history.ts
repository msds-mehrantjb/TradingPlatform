import type { ConfirmedRegimeState, RegimeConditionSnapshot, RegimeTransitionHistoryEntry } from "../types.ts";

const REGIME_TRANSITION_HISTORY_LIMIT = 50;

export function appendRegimeTransitionHistory(
  previous: RegimeConditionSnapshot | null,
  state: ConfirmedRegimeState,
): RegimeTransitionHistoryEntry[] {
  const prior = previous?.transitionHistory ?? [];
  const entry: RegimeTransitionHistoryEntry = {
    rawRegime: state.rawRegime,
    confirmedRegime: state.confirmedRegime,
    previousRegime: state.previousRegime ?? null,
    candidateRegime: state.candidateRegime,
    candidateCount: state.candidateCount,
    dwellBars: state.dwellBars,
    transitionConfidence: state.transitionConfidence ?? state.rawConfidence,
    transitionReason: state.transitionReason,
    timestamp: state.timestamp,
  };
  return [...prior, entry].slice(-REGIME_TRANSITION_HISTORY_LIMIT);
}
