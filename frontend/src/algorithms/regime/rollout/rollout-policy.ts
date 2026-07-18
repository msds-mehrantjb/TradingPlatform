export const REGIME_ROLLOUT_FILE_INVENTORY = [
  "shadow-comparison.ts",
  "paper-stability.ts",
  "rollout-policy.ts",
  "rollback-policy.ts",
] as const;

export const REGIME_FRONTEND_ROLLOUT_PHASES = [
  "shadow_comparison",
  "paper_stability",
  "limited_paper_rollout",
  "rollback_ready",
] as const;

export type RegimeRolloutFile = typeof REGIME_ROLLOUT_FILE_INVENTORY[number];
export type RegimeFrontendRolloutPhase = typeof REGIME_FRONTEND_ROLLOUT_PHASES[number];

export type RegimeFrontendRolloutPolicyInput = {
  shadowComparisonPassed: boolean;
  paperStabilityPassed: boolean;
  globalRiskAdapterReady: boolean;
  brokerAdapterReady: boolean;
  rollbackReady: boolean;
};

export type RegimeFrontendRolloutPolicy = {
  algorithmId: "regime";
  fileInventory: readonly RegimeRolloutFile[];
  phases: readonly RegimeFrontendRolloutPhase[];
  limitedPaperAllowed: boolean;
  liveTradingAllowed: false;
  reasonCodes: string[];
};

export function evaluateRegimeFrontendRolloutPolicy(input: RegimeFrontendRolloutPolicyInput): RegimeFrontendRolloutPolicy {
  const reasonCodes: string[] = ["regime.rollout.frontend_paper_only"];
  if (!input.shadowComparisonPassed) reasonCodes.push("regime.rollout.shadow_comparison_required");
  if (!input.paperStabilityPassed) reasonCodes.push("regime.rollout.paper_stability_required");
  if (!input.globalRiskAdapterReady) reasonCodes.push("regime.rollout.global_risk_adapter_required");
  if (!input.brokerAdapterReady) reasonCodes.push("regime.rollout.broker_adapter_required");
  if (!input.rollbackReady) reasonCodes.push("regime.rollout.rollback_policy_required");
  return {
    algorithmId: "regime",
    fileInventory: REGIME_ROLLOUT_FILE_INVENTORY,
    phases: REGIME_FRONTEND_ROLLOUT_PHASES,
    limitedPaperAllowed:
      input.shadowComparisonPassed &&
      input.paperStabilityPassed &&
      input.globalRiskAdapterReady &&
      input.brokerAdapterReady &&
      input.rollbackReady,
    liveTradingAllowed: false,
    reasonCodes,
  };
}
