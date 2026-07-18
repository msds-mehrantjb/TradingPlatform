export type RegimePaperStabilityInput = {
  decisions: number;
  rejectedOrders: number;
  duplicateOrderIntents: number;
  reconciliationBreaks: number;
  rollbackDrillsPassed: number;
};

export type RegimePaperStabilityResult = {
  algorithmId: "regime";
  passed: boolean;
  reasonCodes: string[];
};

export function evaluateRegimePaperStability(input: RegimePaperStabilityInput): RegimePaperStabilityResult {
  const reasonCodes: string[] = [];
  if (input.decisions <= 0) reasonCodes.push("regime.paper_stability.no_decisions");
  if (input.rejectedOrders > 0) reasonCodes.push("regime.paper_stability.rejected_orders_present");
  if (input.duplicateOrderIntents > 0) reasonCodes.push("regime.paper_stability.duplicate_order_intents");
  if (input.reconciliationBreaks > 0) reasonCodes.push("regime.paper_stability.reconciliation_breaks");
  if (input.rollbackDrillsPassed <= 0) reasonCodes.push("regime.paper_stability.rollback_not_verified");
  return {
    algorithmId: "regime",
    passed: reasonCodes.length === 0,
    reasonCodes,
  };
}
