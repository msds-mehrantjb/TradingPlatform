export type TradingSettingsScope = "voting" | "weighted" | "confidence" | "regime" | "meta";

export type TradingSettingsPersistence = {
  scope: TradingSettingsScope;
  storageKey: string;
};

export {
  TRADING_SETTINGS_FIELD_GROUPS,
  TRADING_SETTINGS_SCHEMA_VERSION,
  type CanonicalBaselineTradingSettings,
  type CanonicalDynamicPolicyBounds,
  type CanonicalHardRiskLimits,
} from "./schema";
