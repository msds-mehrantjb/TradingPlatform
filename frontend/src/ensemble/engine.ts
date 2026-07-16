import type { AlgoSignal } from "../domain/tradingSignals";
import type { EnsembleDecision, ContextSignal, GlobalGateDecision, RegimeState, StrategySignal } from "../domain/models";

export type EnsembleEngineVersion = "voting_ensemble_v1" | "voting_ensemble_v2";

export type EnsembleScores = Record<AlgoSignal, number>;

export type EnsembleResult = {
  version: EnsembleEngineVersion;
  signal: AlgoSignal;
  scores: EnsembleScores;
  explanation: string[];
};

export type EnsembleEngineApi = {
  version: EnsembleEngineVersion;
  aggregate(votes: Array<{ signal: AlgoSignal; eligible: boolean }>): EnsembleResult;
};

export type FamilyAwareEnsembleRequest = {
  strategySignals: StrategySignal[];
  contextSignals: ContextSignal[];
  regimeState: RegimeState | null;
  safetyDecision: GlobalGateDecision | null;
};

export type FamilyAwareEnsembleApi = {
  version: "voting_ensemble_v2";
  aggregate(request: FamilyAwareEnsembleRequest): Promise<EnsembleDecision>;
};
