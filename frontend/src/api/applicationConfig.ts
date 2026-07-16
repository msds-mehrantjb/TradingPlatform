import { API_BASE } from "./client";

export type FeatureFlags = {
  strategyEngineV2Enabled: boolean;
  familyEnsembleV2Enabled: boolean;
  metaModelV2Enabled: boolean;
  dynamicTradingPolicyEnabled: boolean;
  globalGateEngineEnabled: boolean;
  mlFamilyWeightingEnabled: boolean;
  weightedVotingV2Enabled: boolean;
  weightedVotingAutoSubmitEnabled: boolean;
  wcaBackendEngineEnabled: boolean;
  wcaCorrectedStrategyCatalogEnabled: boolean;
  wcaDynamicWeightsEnabled: boolean;
  wcaDynamicProfileEnabled: boolean;
  wcaBackendBacktestEnabled: boolean;
  wcaPaperExecutionEnabled: boolean;
  regimeV2Enabled: boolean;
  regimeDynamicProfileEnabled: boolean;
  regimeMlMode: string;
  regimeGlobalRiskManagerEnabled: boolean;
  regimeShortEntriesEnabled: boolean;
};

export type ApplicationConfig = {
  version: string;
  featureFlags: FeatureFlags;
  configurationHash: string;
};

export async function fetchApplicationConfig(): Promise<ApplicationConfig> {
  const response = await fetch(`${API_BASE}/api/application-config`);
  if (!response.ok) {
    throw new Error(`Application config request failed with ${response.status}`);
  }
  return (await response.json()) as ApplicationConfig;
}
