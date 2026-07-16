import type { AlgoSignal } from "../domain/tradingSignals";

export type GateStatus = "pass" | "fail" | "caution" | "info";

export type GateEvaluation = {
  layer: string;
  status: GateStatus;
  signal: AlgoSignal | "NA" | "Stale" | "Cash" | "Neutral" | "Inactive" | "Watch" | string;
  detail: string;
};

