import type { AlgoSignal } from "./tradingSignals";

export type EngineVersion = "voting_ensemble_v1" | "voting_ensemble_v2";

export type DecisionExplanation = {
  version: EngineVersion;
  configurationHash: string;
  decisionTimestamp: string;
  action: AlgoSignal;
  reasons: string[];
};

