from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GlobalRiskSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    masterNewEntryEnabled: bool = True
    emergencyKillSwitch: bool = False
    cancelPendingEntriesOnEmergency: bool = True
    flattenOnEmergency: bool = False
    tradingEnabled: bool = True

    globalMaximumDailyLossPercent: float = Field(default=0, ge=0)
    globalMaximumGrossExposurePercent: float = Field(default=0, ge=0)
    globalMaximumNetExposurePercent: float = Field(default=0, ge=0)
    globalMaximumSymbolExposurePercent: float = Field(default=0, ge=0)
    globalMaximumSectorExposurePercent: float = Field(default=0, ge=0)
    globalMaximumOpenRiskPercent: float = Field(default=0, ge=0)
    globalMaximumConcurrentPositions: int = Field(default=0, ge=0)
    globalMaximumPendingOrders: int = Field(default=0, ge=0)
    globalMaximumTradesPerDay: int = Field(default=0, ge=0)
    globalMaximumOrdersPerMinute: int = Field(default=0, ge=0)
    globalMaximumSpreadPercent: float = Field(default=0, ge=0)
    globalMaximumEstimatedSlippagePercent: float = Field(default=0, ge=0)
    globalQuoteStaleSeconds: int = Field(default=15, ge=0)
    globalCandleStaleSeconds: int = Field(default=120, ge=0)
    globalNewEntryCutoff: str = "15:30"
    minimumOneMinuteVolume: int = Field(default=0, ge=0)
    maximumShareCap: int = Field(default=0, ge=0)
    maximumNotionalCap: float = Field(default=0, ge=0)
    requireSettledCash: bool = False
    shortSalesEnabled: bool = False
    eventBlackoutPolicy: str = "block_new_entries"


DEFAULT_GLOBAL_RISK_SETTINGS = GlobalRiskSettings()
