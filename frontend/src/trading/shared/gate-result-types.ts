export type GateStatus = "pass" | "fail" | "info" | "warn";

export type GateResult = {
  gateId: string;
  label: string;
  status: GateStatus;
  detail: string;
  blocksTrading: boolean;
};
