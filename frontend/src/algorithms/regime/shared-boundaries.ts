export const REGIME_ALLOWED_SHARED_COMPONENTS = [
  { component: "Raw market-data service", allowedUse: "Read-only input" },
  { component: "Quote and candle cache", allowedUse: "Read-only input" },
  { component: "Market clock and calendar", allowedUse: "Read-only input" },
  { component: "Economic-event feed", allowedUse: "Read-only input" },
  { component: "Account equity and buying power", allowedUse: "Read-only snapshot" },
  { component: "Broker client", allowedUse: "Submit approved Regime intents" },
  { component: "Global account-risk engine", allowedUse: "Reduce or reject Regime proposals" },
  { component: "Global risk reservations", allowedUse: "Account-wide exposure control" },
  { component: "Database connection utilities", allowedUse: "Infrastructure only" },
  { component: "Logging and telemetry", allowedUse: "Must include algorithm_id=regime" },
  { component: "Order-side contract types", allowedUse: "Type definitions only" },
  { component: "Authentication and API framework", allowedUse: "Transport only" },
] as const;

export const REGIME_NEVER_SHARED_COMPONENTS = [
  "Regime classification formulas",
  "Regime classification thresholds",
  "Regime axes and composite-state mapping",
  "Regime hysteresis state",
  "Regime transition history",
  "Regime strategy implementations",
  "Regime strategy compatibility matrix",
  "Regime strategy aliases",
  "Regime strategy health",
  "Regime strategy outputs",
  "Regime context outputs",
  "Regime family scores",
  "Regime aggregation",
  "Regime local gates",
  "Regime baseline settings",
  "Regime dynamic profiles",
  "Regime position sizing",
  "Regime entry and exit policy",
  "Regime decisions",
  "Regime order intents",
  "Regime positions and trades",
  "Regime backtest state",
  "Regime backtest results",
  "Regime ML features and artifacts",
  "Regime rollout state",
] as const;

export type RegimeAllowedSharedComponent = typeof REGIME_ALLOWED_SHARED_COMPONENTS[number];
export type RegimeNeverSharedComponent = typeof REGIME_NEVER_SHARED_COMPONENTS[number];

export type RegimeSharedBoundaryStatus = {
  algorithmId: "regime";
  allowedSharedComponents: readonly RegimeAllowedSharedComponent[];
  neverSharedComponents: readonly RegimeNeverSharedComponent[];
  globalRiskLayerSharedServerSide: true;
  localControlsRemainRegimeOwned: true;
  sharedComponentsMayRewriteRegimeState: false;
  otherAlgorithmsMayModifyPrivateRegimeComponents: false;
};

export function regimeSharedBoundaryStatus(): RegimeSharedBoundaryStatus {
  return {
    algorithmId: "regime",
    allowedSharedComponents: REGIME_ALLOWED_SHARED_COMPONENTS,
    neverSharedComponents: REGIME_NEVER_SHARED_COMPONENTS,
    globalRiskLayerSharedServerSide: true,
    localControlsRemainRegimeOwned: true,
    sharedComponentsMayRewriteRegimeState: false,
    otherAlgorithmsMayModifyPrivateRegimeComponents: false,
  };
}
