export type RegimeFrontendRollbackPolicy = {
  algorithmId: "regime";
  disableNewEntries: true;
  preserveProtectiveExits: true;
  mlMode: "off";
  dynamicProfilesEnabled: false;
  deleteHistoricalRecords: false;
  liveOrders: false;
  reasonCodes: readonly string[];
};

export function regimeFrontendRollbackPolicy(): RegimeFrontendRollbackPolicy {
  return {
    algorithmId: "regime",
    disableNewEntries: true,
    preserveProtectiveExits: true,
    mlMode: "off",
    dynamicProfilesEnabled: false,
    deleteHistoricalRecords: false,
    liveOrders: false,
    reasonCodes: ["regime.rollout.rollback_safe_frontend_state"],
  };
}
