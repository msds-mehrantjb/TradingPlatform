import type { ConfirmedRegimeState, MarketRegimeId, RegimeTransitionHistoryEntry } from "../types.ts";

export type ConfirmedRegimeStateInput = {
  rawRegime: MarketRegimeId;
  confirmedRegime: MarketRegimeId;
  rawConfidence: number;
  confirmedConfidence: number;
  candidateRegime: MarketRegimeId | null;
  candidateCount: number;
  dwellBars: number;
  heldPreviousRegime: boolean;
  transitionReason: string;
  timestamp: string;
  previousRegime?: MarketRegimeId | null;
  regimeStartTime?: string;
  minimumDwellSatisfied?: boolean;
  unknownRegimeCount?: number;
  transitionConfidence?: number;
  transitionEvidence?: Record<string, number | string | boolean | null>;
  transitionHistory?: RegimeTransitionHistoryEntry[];
};

export function createConfirmedRegimeState(input: ConfirmedRegimeStateInput): ConfirmedRegimeState {
  return {
    rawRegime: input.rawRegime,
    confirmedRegime: input.confirmedRegime,
    rawConfidence: input.rawConfidence,
    confirmedConfidence: input.confirmedConfidence,
    candidateRegime: input.candidateRegime,
    candidateCount: input.candidateCount,
    dwellBars: input.dwellBars,
    heldPreviousRegime: input.heldPreviousRegime,
    transitionReason: input.transitionReason,
    timestamp: input.timestamp,
    previousRegime: input.previousRegime ?? null,
    regimeStartTime: input.regimeStartTime ?? input.timestamp,
    minimumDwellSatisfied: input.minimumDwellSatisfied ?? true,
    unknownRegimeCount: input.unknownRegimeCount ?? 0,
    transitionConfidence: input.transitionConfidence ?? input.rawConfidence,
    transitionEvidence: input.transitionEvidence ?? {},
    transitionHistory: input.transitionHistory ?? [],
  };
}
