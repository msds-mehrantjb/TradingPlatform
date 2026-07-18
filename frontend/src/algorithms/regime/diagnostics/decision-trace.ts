import type { RegimeSelectionResult } from "../types.ts";

export type RegimeDecisionTrace = {
  algorithmId: "regime";
  signal: string;
  tradeAllowed: boolean;
  tradeBlockers: readonly string[];
  buyScore: number;
  sellScore: number;
  winningDirection: string;
  directionalEdge: number;
  confidence: number;
  mlMode: string | null;
};

export function buildRegimeDecisionTrace(result: RegimeSelectionResult): RegimeDecisionTrace {
  return {
    algorithmId: "regime",
    signal: result.signal,
    tradeAllowed: result.tradeAllowed,
    tradeBlockers: result.tradeBlockers,
    buyScore: result.buyScore,
    sellScore: result.sellScore,
    winningDirection: result.winningDirection,
    directionalEdge: result.directionalEdge,
    confidence: result.confidence,
    mlMode: result.ml?.mode ?? null,
  };
}
