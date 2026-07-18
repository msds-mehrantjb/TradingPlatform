export const REGIME_ALGORITHM_ID = "regime";
export const REGIME_ALGORITHM_VERSION = "regime_algorithm_v2";
export const REGIME_SETTINGS_VERSION = "regime_base_settings_v1";
export const REGIME_STRATEGY_CATALOG_VERSION = "regime_strategy_catalog_v2";
export const REGIME_PROFILE_VERSION = "regime_profile_matrix_v1";

export const REGIME_IDENTITY_CONTRACT_FILES = [
  { file: "index.ts", responsibility: "Public Regime exports" },
  { file: "types.ts", responsibility: "Regime decisions, classifications, strategy outputs, profiles, orders and positions" },
  { file: "versions.ts", responsibility: "Algorithm, settings, strategy-catalog and profile versions" },
  { file: "config.ts", responsibility: "Regime defaults and thresholds" },
  { file: "validation.ts", responsibility: "Regime configuration and contract validation" },
] as const;

