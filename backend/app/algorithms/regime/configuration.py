"""Backend-owned Regime settings, versions, and validation."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from backend.app.algorithms.regime.contracts import (
    CANONICAL_MARKET_REGIMES,
    REGIME_ALLOWED_RUNTIME_MODE_VALUES,
    REGIME_ALGORITHM_ID,
    REGIME_PROFILE_VERSION,
    REGIME_SETTINGS_VERSION,
    REGIME_STRATEGY_CATALOG_VERSION,
    default_regime_account_id,
    default_regime_algorithm_instance_id,
    normalize_regime_runtime_mode,
)


REGIME_SETTINGS_MODEL_VERSION = "regime_settings_model_v1"
REGIME_SETTINGS_AUTHORITATIVE_SOURCE = "backend.app.algorithms.regime.repository.RegimeRepository"
REGIME_RUNTIME_MODES = set(REGIME_ALLOWED_RUNTIME_MODE_VALUES)
REGIME_STRATEGY_IDS: tuple[str, ...] = (
    "moving_average_trend",
    "trend_pullback",
    "rsi_mean_reversion",
    "bollinger_band_mean_reversion",
    "opening_range_breakout",
    "intraday_breakout",
    "macd_momentum",
    "market_structure",
    "gap_continuation_fade",
    "vwap_trend_continuation",
    "vwap_mean_reversion",
    "failed_breakout_reversal",
    "liquidity_sweep_reversal",
    "volatility_breakout",
    "volume_confirmation",
    "adx_trend_strength",
    "vwap_position",
    "atr_volatility_regime",
    "cash_avoid_filter",
    "missing_critical_data",
    "stale_data",
    "extreme_volatility",
    "excessive_spread",
    "insufficient_liquidity",
    "event_blackout",
    "halt_luld",
    "circuit_breaker",
    "unsupported_session",
)

REGIME_STRATEGY_PARAMETER_DEFAULTS: dict[str, dict[str, Any]] = {
    "moving_average_trend": {"minimumAdx": 18.0, "minimumEfficiency": 0.35, "maximumExtensionAtr": 2.2},
    "trend_pullback": {"minimumAdx": 18.0, "minimumPullbackAtr": 0.25, "maximumPullbackAtr": 1.8},
    "rsi_mean_reversion": {"oversold": 32.0, "overbought": 68.0, "recoveryBuffer": 2.0, "minimumTargetAtr": 0.45},
    "bollinger_band_mean_reversion": {"zscoreThreshold": 1.8, "maximumBandwidth": 0.025},
    "opening_range_breakout": {"rangeMinutes": 30, "minimumBreakoutDistanceBps": 3.0, "minimumVolumeExpansion": 1.2, "minimumRangeExpansion": 1.15, "latestEntryMinute": 90, "maximumExtensionBps": 45.0},
    "intraday_breakout": {"referenceLookback": 24, "maximumCompressionRatio": 0.85, "minimumRangeExpansion": 1.15, "minimumRelativeVolume": 1.1, "minimumNetEdgeBps": 4.0},
    "macd_momentum": {"minimumNormalizedMagnitude": 0.08, "crossoverFreshnessBars": 3, "minimumAdx": 14.0},
    "market_structure": {"requireBreakOfStructure": True},
    "gap_continuation_fade": {"minimumGapBps": 20.0, "openingWindowMinutes": 45},
    "vwap_trend_continuation": {"minimumVwapSlope": 0.00008, "maximumInteractionDistanceAtr": 0.9, "minimumAdx": 16.0},
    "vwap_mean_reversion": {"minimumVwapDistanceAtr": 0.65, "minimumNetEdgeBps": 4.0, "maximumAdx": 26.0},
    "failed_breakout_reversal": {"openingRangeMinutes": 30, "lookback": 24, "minimumTradeThroughBps": 2.0},
    "liquidity_sweep_reversal": {"openingRangeMinutes": 30, "lookback": 24, "minimumRelativeVolume": 1.05, "minimumWickFraction": 0.35},
    "volatility_breakout": {"maximumPriorCompression": 0.75, "minimumCurrentExpansion": 1.45, "minimumRelativeVolume": 1.05},
    "volume_confirmation": {},
    "adx_trend_strength": {},
    "vwap_position": {},
    "atr_volatility_regime": {},
    "cash_avoid_filter": {},
    "missing_critical_data": {},
    "stale_data": {},
    "extreme_volatility": {},
    "excessive_spread": {},
    "insufficient_liquidity": {},
    "event_blackout": {},
    "halt_luld": {},
    "circuit_breaker": {},
    "unsupported_session": {},
}


@dataclass(frozen=True)
class RegimeTradingSettings:
    identity: dict[str, Any]
    runtime: dict[str, Any]
    data_quality: dict[str, Any]
    classifier: dict[str, Any]
    hysteresis: dict[str, Any]
    strategy_catalog: dict[str, Any]
    strategy_settings: dict[str, Any]
    family_aggregation: dict[str, Any]
    local_risk: dict[str, Any]
    dynamic_profiles: dict[str, Any]
    position_sizing: dict[str, Any]
    entry_policy: dict[str, Any]
    exit_policy: dict[str, Any]
    execution: dict[str, Any]
    daily_limits: dict[str, Any]
    rollout: dict[str, Any]
    backtest: dict[str, Any]
    ml_shadow: dict[str, Any]
    settings_version: str
    settings_model_version: str
    profile_version: str
    strategy_catalog_version: str
    created_at: str
    created_by: str
    previous_settings_version: str | None
    configuration_hash: str

    def as_dict(self) -> dict[str, Any]:
        snapshot = {
            "identity": _deepcopy_json(self.identity),
            "runtime": _deepcopy_json(self.runtime),
            "data_quality": _deepcopy_json(self.data_quality),
            "classifier": _deepcopy_json(self.classifier),
            "hysteresis": _deepcopy_json(self.hysteresis),
            "strategy_catalog": _deepcopy_json(self.strategy_catalog),
            "strategy_settings": _deepcopy_json(self.strategy_settings),
            "family_aggregation": _deepcopy_json(self.family_aggregation),
            "local_risk": _deepcopy_json(self.local_risk),
            "dynamic_profiles": _deepcopy_json(self.dynamic_profiles),
            "position_sizing": _deepcopy_json(self.position_sizing),
            "entry_policy": _deepcopy_json(self.entry_policy),
            "exit_policy": _deepcopy_json(self.exit_policy),
            "execution": _deepcopy_json(self.execution),
            "daily_limits": _deepcopy_json(self.daily_limits),
            "rollout": _deepcopy_json(self.rollout),
            "backtest": _deepcopy_json(self.backtest),
            "ml_shadow": _deepcopy_json(self.ml_shadow),
            "settingsVersion": self.settings_version,
            "settingsModelVersion": self.settings_model_version,
            "profileVersion": self.profile_version,
            "strategyCatalogVersion": self.strategy_catalog_version,
            "createdAt": self.created_at,
            "createdBy": self.created_by,
            "previousSettingsVersion": self.previous_settings_version,
            "configurationHash": self.configuration_hash,
            "settingsHash": self.configuration_hash,
            "immutableVersionId": self.settings_version,
            "contentHash": self.configuration_hash,
            "createdSource": self.created_by,
            "sourceMetadata": {"createdBy": self.created_by, "source": self.created_by},
            "baselineSettings": _deepcopy_json(DEFAULT_REGIME_SETTINGS),
            "hardSafetyLimits": _hard_safety_limits(),
            "strategyLifecycleStates": _strategy_lifecycle_view(self.strategy_settings),
            "regimeProfileMatrixVersion": self.profile_version,
            "activationStatus": "inactive",
            "activationTimestamp": None,
            "reasonForActivationOrRollback": None,
        }
        snapshot.update(
            {
                "dataQuality": _deepcopy_json(self.data_quality),
                "strategyCatalog": _deepcopy_json(self.strategy_catalog),
                "strategyLifecycle": _strategy_lifecycle_view(self.strategy_settings),
                "strategySettings": _deepcopy_json(self.strategy_settings),
                "familyAggregation": _deepcopy_json(self.family_aggregation),
                "dynamicProfiles": _deepcopy_json(self.dynamic_profiles),
                "localRisk": _deepcopy_json(self.local_risk),
                "positionSizing": _deepcopy_json(self.position_sizing),
                "entryPolicy": _deepcopy_json(self.entry_policy),
                "exitPolicy": _deepcopy_json(self.exit_policy),
                "paperExecution": _deepcopy_json(self.execution),
                "dailyLimits": _deepcopy_json(self.daily_limits),
                "mlShadow": _deepcopy_json(self.ml_shadow),
            }
        )
        return snapshot


DEFAULT_REGIME_SETTINGS: dict[str, Any] = {
    "symbolAllowlist": ["SPY"],
    "timeframe": "1Min",
    "paperOnly": True,
    "regularHoursOnly": True,
    "startingCapital": 25_000.0,
    "baseRiskPercent": 0.10,
    "maxPositionPercent": 10.0,
    "dailyAllocationPercent": 20.0,
    "maxOpenRegimePositions": 1,
    "minimumWinningScore": 0.60,
    "minimumSignalEdge": 0.20,
    "minimumNetExpectedEdge": 0.02,
    "minimumRegimeConfidence": 0.65,
    "minimumActiveStrategies": 3,
    "minimumIndependentFamilies": 2,
    "maximumAbstentionRate": 0.60,
    "maximumContributionPerFamily": 0.40,
    "maxSpreadPercent": 0.03,
    "minimumOneMinuteVolume": 0,
    "atrStopMultiplier": 2.0,
    "minimumStopDistancePercent": 0.05,
    "takeProfitR": 1.5,
    "maxParticipationPercent": 0.02,
    "maxAllowedShares": 500,
    "maxOrderNotionalDollars": 2_500.0,
    "maxPositionNotionalDollars": 2_500.0,
    "maxNotionalDollars": 2_500.0,
    "maxHoldingBars": 45,
    "orderTimeToLiveSeconds": 60,
    "maxCancelReplaceAttempts": 2,
    "allowMarketEntryOrders": False,
    "maximumSlippageBps": 8.0,
    "maximumCostToEdgeRatio": 0.75,
    "conservativeCostFallbackApproved": False,
    "uncertaintyBufferBps": 1.0,
    "estimatedFeesBps": 0.1,
    "estimatedRegulatoryFeesBps": 0.0,
    "marketImpactBps": 0.0,
    "marketImpactBpsPerParticipationPct": 0.0,
    "adverseSelectionBufferBps": 0.5,
    "maximumCostModelAgeSeconds": 900,
    "staleBarToleranceSeconds": 90,
    "quoteAgeToleranceSeconds": 5,
    "shortEntriesEnabled": False,
    "allowShortEntries": False,
    "pyramidingEnabled": False,
    "mlMode": "shadow",
    "confirmationBars": 3,
    "immediateConfidenceThreshold": 0.65,
    "minimumDwellBars": 5,
    "transitionConfidenceGap": 0.10,
    "cooldownBars": 5,
    "maximumUnknownBars": 3,
    "entryCutoffTimeEt": "15:30",
    "flattenTimeEt": "15:55",
    "endOfDayFlattenEnabled": True,
    "mandatoryStop": True,
    "mandatoryMaxHoldingTime": True,
    "maxTradesPerDay": 3,
    "maxEntriesPerDay": 3,
    "maxConsecutiveLosses": 3,
    "maxDailyLossPercent": 0.50,
    "perStrategyMaxTradesPerDay": 1,
}


SETTINGS_SECTION_NAMES = (
    "identity",
    "runtime",
    "data_quality",
    "classifier",
    "hysteresis",
    "strategy_catalog",
    "strategy_settings",
    "family_aggregation",
    "local_risk",
    "dynamic_profiles",
    "position_sizing",
    "entry_policy",
    "exit_policy",
    "execution",
    "daily_limits",
    "rollout",
    "backtest",
    "ml_shadow",
)

SETTINGS_SECTION_ALIASES = {
    "dataQuality": "data_quality",
    "strategyCatalog": "strategy_catalog",
    "strategyLifecycle": "strategy_settings",
    "strategySettings": "strategy_settings",
    "familyAggregation": "family_aggregation",
    "dynamicProfiles": "dynamic_profiles",
    "localRisk": "local_risk",
    "positionSizing": "position_sizing",
    "entryPolicy": "entry_policy",
    "exitPolicy": "exit_policy",
    "paperExecution": "execution",
    "dailyLimits": "daily_limits",
    "mlShadow": "ml_shadow",
}

_SETTINGS_METADATA_FIELDS = {
    "settingsVersion",
    "settingsModelVersion",
    "profileVersion",
    "strategyCatalogVersion",
    "createdAt",
    "createdBy",
    "previousSettingsVersion",
    "configurationHash",
    "settingsHash",
    "immutableVersionId",
    "contentHash",
    "createdSource",
    "sourceMetadata",
    "baselineSettings",
    "hardSafetyLimits",
    "strategyLifecycleStates",
    "regimeProfileMatrixVersion",
    "activationStatus",
    "activationTimestamp",
    "reasonForActivationOrRollback",
}

_SECTION_ALLOWED_FIELDS: dict[str, set[str]] = {
    "identity": {"algorithmId", "algorithmInstanceId", "accountId", "runtimeMode", "symbol"},
    "runtime": {"runtimeMode", "backgroundWorkersRequired", "liveTradingEnabled", "paperTradingOnly", "paperOnly", "shadowOperationEnabled", "symbolAllowlist", "timeframe", "regularHoursOnly"},
    "data_quality": {"staleBarToleranceSeconds", "quoteAgeToleranceSeconds", "requireOneMinuteBars", "requiredSymbol", "timeframe", "regularHoursOnly"},
    "classifier": {"minimumRegimeConfidence", "minimumOneMinuteVolume", "maxSpreadPercent"},
    "hysteresis": {"confirmationBars", "minimumDwellBars", "transitionConfidenceGap", "cooldownBars", "maximumUnknownBars", "immediateConfidenceThreshold"},
    "strategy_catalog": {"catalogVersion", "strategyIds", "strategyCount"},
    "family_aggregation": {"minimumWinningScore", "minimumSignalEdge", "minimumNetExpectedEdge", "minimumNetExpectedEdgeBps", "minimumActiveStrategies", "minimumIndependentFamilies", "maximumAbstentionRate", "maximumContributionPerFamily", "maximumCostToEdgeRatio"},
    "local_risk": {"baseRiskPercent", "maxDailyLossPercent", "maxConsecutiveLosses", "familyRiskLimits"},
    "dynamic_profiles": {"profileVersion", "overlays"},
    "position_sizing": {"startingCapital", "baseRiskPercent", "maxPositionPercent", "dailyAllocationPercent", "maxParticipationPercent", "maxAllowedShares", "maxOrderNotionalDollars", "maxPositionNotionalDollars", "maxNotionalDollars", "maxOpenRegimePositions"},
    "entry_policy": {"pyramidingEnabled", "shortEntriesEnabled", "allowShortEntries", "confirmationBars", "entryCutoffTimeEt", "minimumNetExpectedEdge", "minimumNetExpectedEdgeBps"},
    "exit_policy": {"flattenTimeEt", "maxHoldingBars", "atrStopMultiplier", "minimumStopDistancePercent", "takeProfitR", "endOfDayFlattenEnabled", "mandatoryStop", "mandatoryMaxHoldingTime"},
    "execution": {"orderTimeToLiveSeconds", "maxCancelReplaceAttempts", "maximumSlippageBps", "maximumCostToEdgeRatio", "conservativeCostFallbackApproved", "uncertaintyBufferBps", "estimatedFeesBps", "estimatedRegulatoryFeesBps", "marketImpactBps", "marketImpactBpsPerParticipationPct", "adverseSelectionBufferBps", "maximumCostModelAgeSeconds", "allowMarketEntryOrders", "brokerTransportMode"},
    "daily_limits": {"maxTradesPerDay", "maxEntriesPerDay", "maxConsecutiveLosses", "maxDailyLossPercent", "dailyAllocationPercent", "perStrategyTradeLimits", "perFamilyDailyRiskLimits"},
    "rollout": {"runtimeMode", "requireRolloutEvidence", "mlMayPromoteOrders", "liveTradingEnabled"},
    "backtest": {"engineVersion", "oneMinuteOnly", "symbol", "settingsSource"},
    "ml_shadow": {"mode", "mayAlterSignals", "mayAlterSizing", "mayAlterOrders"},
}
_STRATEGY_SETTINGS_FIELDS = {"enabled", "lifecycle", "settingsType", "family", "role", "maxTradesPerDay", "minimumNetExpectedEdge", "riskMultiplier", "parameters"}
_PROFILE_OVERLAY_FIELDS = {
    "baseRiskPercentCap",
    "maxPositionPercentCap",
    "maxParticipationPercentCap",
    "maximumSlippageBps",
    "maximumCostToEdgeRatio",
    "conservativeCostFallbackApproved",
    "uncertaintyBufferBps",
    "estimatedFeesBps",
    "estimatedRegulatoryFeesBps",
    "marketImpactBps",
    "marketImpactBpsPerParticipationPct",
    "adverseSelectionBufferBps",
    "maximumCostModelAgeSeconds",
    "minimumNetExpectedEdge",
    "minimumNetExpectedEdgeBps",
    "orderTimeToLiveSeconds",
    "maxCancelReplaceAttempts",
    "pyramidingEnabled",
    "shortEntriesEnabled",
    "noNewEntries",
}


def build_default_regime_trading_settings(
    identity: dict[str, Any] | None = None,
    *,
    actor: str = "system",
    previous_settings_version: str | None = None,
    created_at: str | None = None,
) -> RegimeTradingSettings:
    sections = _default_sections(identity)
    return _settings_from_sections(sections, actor=actor, previous_settings_version=previous_settings_version, created_at=created_at)


def validate_regime_trading_settings_snapshot(
    payload: dict[str, Any] | None = None,
    *,
    actor: str = "system",
    previous_settings_version: str | None = None,
    created_at: str | None = None,
) -> RegimeTradingSettings:
    sections = _default_sections(_identity_from_payload(payload or {}))
    _reject_unknown_payload_fields(payload or {})
    update = _extract_sections(payload or {})
    _reject_unknown_sections(update)
    for section, value in update.items():
        if section == "strategy_settings":
            sections[section] = _merge_strategy_settings(sections[section], _require_dict(value, section))
        elif section == "dynamic_profiles":
            sections[section] = _merge_dynamic_profiles(sections[section], _require_dict(value, section), sections)
        else:
            sections[section] = _merge_section(sections[section], _require_dict(value, section), section)
    return _settings_from_sections(sections, actor=actor, previous_settings_version=previous_settings_version, created_at=created_at)


def regime_trading_settings_to_dict(settings: RegimeTradingSettings | dict[str, Any]) -> dict[str, Any]:
    return settings.as_dict() if isinstance(settings, RegimeTradingSettings) else _deepcopy_json(settings)


def flatten_regime_trading_settings(settings: RegimeTradingSettings | dict[str, Any] | None) -> dict[str, Any]:
    if settings is None:
        return validate_regime_settings()
    snapshot = regime_trading_settings_to_dict(settings)
    position = _record(snapshot.get("position_sizing"))
    if not position:
        position = _record(snapshot.get("positionSizing"))
    aggregation = _record(snapshot.get("family_aggregation")) or _record(snapshot.get("familyAggregation"))
    classifier = _record(snapshot.get("classifier"))
    hysteresis = _record(snapshot.get("hysteresis"))
    entry = _record(snapshot.get("entry_policy")) or _record(snapshot.get("entryPolicy"))
    exit_policy = _record(snapshot.get("exit_policy")) or _record(snapshot.get("exitPolicy"))
    execution = _record(snapshot.get("execution")) or _record(snapshot.get("paperExecution"))
    daily = _record(snapshot.get("daily_limits")) or _record(snapshot.get("dailyLimits"))
    ml = _record(snapshot.get("ml_shadow")) or _record(snapshot.get("mlShadow"))
    runtime = _record(snapshot.get("runtime"))
    data_quality = _record(snapshot.get("data_quality")) or _record(snapshot.get("dataQuality"))
    flat = {
        **DEFAULT_REGIME_SETTINGS,
        "symbolAllowlist": runtime.get("symbolAllowlist", DEFAULT_REGIME_SETTINGS["symbolAllowlist"]),
        "timeframe": runtime.get("timeframe", data_quality.get("timeframe", DEFAULT_REGIME_SETTINGS["timeframe"])),
        "paperOnly": runtime.get("paperOnly", runtime.get("paperTradingOnly", DEFAULT_REGIME_SETTINGS["paperOnly"])),
        "regularHoursOnly": runtime.get("regularHoursOnly", data_quality.get("regularHoursOnly", DEFAULT_REGIME_SETTINGS["regularHoursOnly"])),
        "startingCapital": position.get("startingCapital", DEFAULT_REGIME_SETTINGS["startingCapital"]),
        "baseRiskPercent": position.get("baseRiskPercent", DEFAULT_REGIME_SETTINGS["baseRiskPercent"]),
        "maxPositionPercent": position.get("maxPositionPercent", DEFAULT_REGIME_SETTINGS["maxPositionPercent"]),
        "dailyAllocationPercent": position.get("dailyAllocationPercent", DEFAULT_REGIME_SETTINGS["dailyAllocationPercent"]),
        "maxParticipationPercent": position.get("maxParticipationPercent", DEFAULT_REGIME_SETTINGS["maxParticipationPercent"]),
        "maxAllowedShares": position.get("maxAllowedShares", DEFAULT_REGIME_SETTINGS["maxAllowedShares"]),
        "maxOpenRegimePositions": position.get("maxOpenRegimePositions", DEFAULT_REGIME_SETTINGS["maxOpenRegimePositions"]),
        "maxOrderNotionalDollars": position.get("maxOrderNotionalDollars", position.get("maxNotionalDollars", DEFAULT_REGIME_SETTINGS["maxOrderNotionalDollars"])),
        "maxPositionNotionalDollars": position.get("maxPositionNotionalDollars", position.get("maxNotionalDollars", DEFAULT_REGIME_SETTINGS["maxPositionNotionalDollars"])),
        "maxNotionalDollars": position.get("maxNotionalDollars", position.get("maxOrderNotionalDollars", DEFAULT_REGIME_SETTINGS["maxNotionalDollars"])),
        "minimumWinningScore": aggregation.get("minimumWinningScore", DEFAULT_REGIME_SETTINGS["minimumWinningScore"]),
        "minimumSignalEdge": aggregation.get("minimumSignalEdge", DEFAULT_REGIME_SETTINGS["minimumSignalEdge"]),
        "minimumNetExpectedEdge": aggregation.get("minimumNetExpectedEdge", DEFAULT_REGIME_SETTINGS["minimumNetExpectedEdge"]),
        "minimumNetExpectedEdgeBps": aggregation.get("minimumNetExpectedEdgeBps", entry.get("minimumNetExpectedEdgeBps", DEFAULT_REGIME_SETTINGS["minimumNetExpectedEdge"] * 100.0)),
        "minimumActiveStrategies": aggregation.get("minimumActiveStrategies", DEFAULT_REGIME_SETTINGS["minimumActiveStrategies"]),
        "minimumIndependentFamilies": aggregation.get("minimumIndependentFamilies", DEFAULT_REGIME_SETTINGS["minimumIndependentFamilies"]),
        "maximumAbstentionRate": aggregation.get("maximumAbstentionRate", DEFAULT_REGIME_SETTINGS["maximumAbstentionRate"]),
        "maximumContributionPerFamily": aggregation.get("maximumContributionPerFamily", DEFAULT_REGIME_SETTINGS["maximumContributionPerFamily"]),
        "minimumRegimeConfidence": classifier.get("minimumRegimeConfidence", DEFAULT_REGIME_SETTINGS["minimumRegimeConfidence"]),
        "minimumOneMinuteVolume": classifier.get("minimumOneMinuteVolume", DEFAULT_REGIME_SETTINGS["minimumOneMinuteVolume"]),
        "maxSpreadPercent": classifier.get("maxSpreadPercent", DEFAULT_REGIME_SETTINGS["maxSpreadPercent"]),
        "confirmationBars": hysteresis.get("confirmationBars", entry.get("confirmationBars", DEFAULT_REGIME_SETTINGS["confirmationBars"])),
        "minimumDwellBars": hysteresis.get("minimumDwellBars", DEFAULT_REGIME_SETTINGS["minimumDwellBars"]),
        "transitionConfidenceGap": hysteresis.get("transitionConfidenceGap", DEFAULT_REGIME_SETTINGS["transitionConfidenceGap"]),
        "cooldownBars": hysteresis.get("cooldownBars", DEFAULT_REGIME_SETTINGS["cooldownBars"]),
        "maximumUnknownBars": hysteresis.get("maximumUnknownBars", DEFAULT_REGIME_SETTINGS["maximumUnknownBars"]),
        "immediateConfidenceThreshold": hysteresis.get("immediateConfidenceThreshold", DEFAULT_REGIME_SETTINGS["immediateConfidenceThreshold"]),
        "pyramidingEnabled": entry.get("pyramidingEnabled", DEFAULT_REGIME_SETTINGS["pyramidingEnabled"]),
        "shortEntriesEnabled": entry.get("shortEntriesEnabled", entry.get("allowShortEntries", DEFAULT_REGIME_SETTINGS["shortEntriesEnabled"])),
        "allowShortEntries": entry.get("allowShortEntries", entry.get("shortEntriesEnabled", DEFAULT_REGIME_SETTINGS["allowShortEntries"])),
        "entryCutoffTimeEt": entry.get("entryCutoffTimeEt", DEFAULT_REGIME_SETTINGS["entryCutoffTimeEt"]),
        "atrStopMultiplier": exit_policy.get("atrStopMultiplier", DEFAULT_REGIME_SETTINGS["atrStopMultiplier"]),
        "minimumStopDistancePercent": exit_policy.get("minimumStopDistancePercent", DEFAULT_REGIME_SETTINGS["minimumStopDistancePercent"]),
        "takeProfitR": exit_policy.get("takeProfitR", DEFAULT_REGIME_SETTINGS["takeProfitR"]),
        "flattenTimeEt": exit_policy.get("flattenTimeEt", DEFAULT_REGIME_SETTINGS["flattenTimeEt"]),
        "maxHoldingBars": exit_policy.get("maxHoldingBars", DEFAULT_REGIME_SETTINGS["maxHoldingBars"]),
        "endOfDayFlattenEnabled": exit_policy.get("endOfDayFlattenEnabled", DEFAULT_REGIME_SETTINGS["endOfDayFlattenEnabled"]),
        "mandatoryStop": exit_policy.get("mandatoryStop", DEFAULT_REGIME_SETTINGS["mandatoryStop"]),
        "mandatoryMaxHoldingTime": exit_policy.get("mandatoryMaxHoldingTime", DEFAULT_REGIME_SETTINGS["mandatoryMaxHoldingTime"]),
        "orderTimeToLiveSeconds": execution.get("orderTimeToLiveSeconds", DEFAULT_REGIME_SETTINGS["orderTimeToLiveSeconds"]),
        "maxCancelReplaceAttempts": execution.get("maxCancelReplaceAttempts", DEFAULT_REGIME_SETTINGS["maxCancelReplaceAttempts"]),
        "allowMarketEntryOrders": execution.get("allowMarketEntryOrders", DEFAULT_REGIME_SETTINGS["allowMarketEntryOrders"]),
        "maximumSlippageBps": execution.get("maximumSlippageBps", DEFAULT_REGIME_SETTINGS["maximumSlippageBps"]),
        "maximumCostToEdgeRatio": execution.get("maximumCostToEdgeRatio", aggregation.get("maximumCostToEdgeRatio", DEFAULT_REGIME_SETTINGS["maximumCostToEdgeRatio"])),
        "conservativeCostFallbackApproved": execution.get("conservativeCostFallbackApproved", DEFAULT_REGIME_SETTINGS["conservativeCostFallbackApproved"]),
        "uncertaintyBufferBps": execution.get("uncertaintyBufferBps", DEFAULT_REGIME_SETTINGS["uncertaintyBufferBps"]),
        "estimatedFeesBps": execution.get("estimatedFeesBps", DEFAULT_REGIME_SETTINGS["estimatedFeesBps"]),
        "estimatedRegulatoryFeesBps": execution.get("estimatedRegulatoryFeesBps", DEFAULT_REGIME_SETTINGS["estimatedRegulatoryFeesBps"]),
        "marketImpactBps": execution.get("marketImpactBps", DEFAULT_REGIME_SETTINGS["marketImpactBps"]),
        "marketImpactBpsPerParticipationPct": execution.get("marketImpactBpsPerParticipationPct", DEFAULT_REGIME_SETTINGS["marketImpactBpsPerParticipationPct"]),
        "adverseSelectionBufferBps": execution.get("adverseSelectionBufferBps", DEFAULT_REGIME_SETTINGS["adverseSelectionBufferBps"]),
        "maximumCostModelAgeSeconds": execution.get("maximumCostModelAgeSeconds", DEFAULT_REGIME_SETTINGS["maximumCostModelAgeSeconds"]),
        "maxTradesPerDay": daily.get("maxTradesPerDay", daily.get("maxEntriesPerDay", DEFAULT_REGIME_SETTINGS["maxTradesPerDay"])),
        "maxEntriesPerDay": daily.get("maxEntriesPerDay", daily.get("maxTradesPerDay", DEFAULT_REGIME_SETTINGS["maxEntriesPerDay"])),
        "maxConsecutiveLosses": daily.get("maxConsecutiveLosses", DEFAULT_REGIME_SETTINGS["maxConsecutiveLosses"]),
        "maxDailyLossPercent": daily.get("maxDailyLossPercent", DEFAULT_REGIME_SETTINGS["maxDailyLossPercent"]),
        "staleBarToleranceSeconds": data_quality.get("staleBarToleranceSeconds", DEFAULT_REGIME_SETTINGS["staleBarToleranceSeconds"]),
        "quoteAgeToleranceSeconds": data_quality.get("quoteAgeToleranceSeconds", DEFAULT_REGIME_SETTINGS["quoteAgeToleranceSeconds"]),
        "mlMode": ml.get("mode", DEFAULT_REGIME_SETTINGS["mlMode"]),
        "settingsVersion": snapshot.get("settingsVersion", REGIME_SETTINGS_VERSION),
        "settingsModelVersion": snapshot.get("settingsModelVersion", REGIME_SETTINGS_MODEL_VERSION),
        "profileVersion": snapshot.get("profileVersion", REGIME_PROFILE_VERSION),
        "strategyCatalogVersion": snapshot.get("strategyCatalogVersion", REGIME_STRATEGY_CATALOG_VERSION),
        "settingsConfigurationHash": snapshot.get("configurationHash", ""),
        "settingsHash": snapshot.get("settingsHash") or snapshot.get("configurationHash", ""),
    }
    return validate_regime_settings(flat)


def validate_regime_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    if _looks_like_settings_snapshot(settings or {}):
        return flatten_regime_trading_settings(settings)
    merged = {**DEFAULT_REGIME_SETTINGS, **(settings or {})}
    merged["symbolAllowlist"] = [str(symbol).upper() for symbol in (merged.get("symbolAllowlist") or ["SPY"])]
    merged["timeframe"] = str(merged.get("timeframe") or "1Min")
    merged["paperOnly"] = bool(merged.get("paperOnly", True))
    merged["regularHoursOnly"] = bool(merged.get("regularHoursOnly", True))
    merged["startingCapital"] = max(0.0, float(merged["startingCapital"]))
    merged["baseRiskPercent"] = max(0.0, min(5.0, float(merged["baseRiskPercent"])))
    merged["maxPositionPercent"] = max(0.0, min(100.0, float(merged["maxPositionPercent"])))
    merged["dailyAllocationPercent"] = max(0.0, min(100.0, float(merged["dailyAllocationPercent"])))
    merged["minimumWinningScore"] = max(0.0, min(1.0, float(merged["minimumWinningScore"])))
    merged["minimumSignalEdge"] = max(0.0, min(1.0, float(merged["minimumSignalEdge"])))
    merged["minimumNetExpectedEdge"] = max(0.0, min(1.0, float(merged.get("minimumNetExpectedEdge", merged["minimumSignalEdge"]))))
    merged["minimumNetExpectedEdgeBps"] = max(0.0, float(merged.get("minimumNetExpectedEdgeBps", merged["minimumNetExpectedEdge"] * 100.0)))
    merged["minimumRegimeConfidence"] = max(0.0, min(1.0, float(merged["minimumRegimeConfidence"])))
    merged["minimumActiveStrategies"] = max(0, int(merged["minimumActiveStrategies"]))
    merged["minimumIndependentFamilies"] = max(1, int(merged["minimumIndependentFamilies"]))
    merged["maximumAbstentionRate"] = max(0.0, min(1.0, float(merged["maximumAbstentionRate"])))
    merged["maximumContributionPerFamily"] = max(0.01, min(1.0, float(merged.get("maximumContributionPerFamily", DEFAULT_REGIME_SETTINGS["maximumContributionPerFamily"]))))
    merged["maxSpreadPercent"] = max(0.0, float(merged["maxSpreadPercent"]))
    merged["minimumOneMinuteVolume"] = max(0, int(merged["minimumOneMinuteVolume"]))
    merged["atrStopMultiplier"] = max(0.01, float(merged["atrStopMultiplier"]))
    merged["minimumStopDistancePercent"] = max(0.0, float(merged["minimumStopDistancePercent"]))
    merged["takeProfitR"] = max(0.1, float(merged["takeProfitR"]))
    merged["maxParticipationPercent"] = max(0.0, min(1.0, float(merged["maxParticipationPercent"])))
    merged["maxAllowedShares"] = max(0, int(merged["maxAllowedShares"]))
    merged["maxOpenRegimePositions"] = max(0, int(merged.get("maxOpenRegimePositions", DEFAULT_REGIME_SETTINGS["maxOpenRegimePositions"])))
    merged["maxOrderNotionalDollars"] = max(0.0, float(merged.get("maxOrderNotionalDollars", merged.get("maxNotionalDollars", 0.0))))
    merged["maxPositionNotionalDollars"] = max(0.0, float(merged.get("maxPositionNotionalDollars", merged.get("maxNotionalDollars", 0.0))))
    merged["maxNotionalDollars"] = max(0.0, float(merged["maxNotionalDollars"]))
    merged["maxHoldingBars"] = max(1, int(merged["maxHoldingBars"]))
    merged["orderTimeToLiveSeconds"] = max(1, int(merged["orderTimeToLiveSeconds"]))
    merged["maxCancelReplaceAttempts"] = max(0, int(merged["maxCancelReplaceAttempts"]))
    merged["allowMarketEntryOrders"] = bool(merged.get("allowMarketEntryOrders", DEFAULT_REGIME_SETTINGS["allowMarketEntryOrders"]))
    merged["maximumSlippageBps"] = max(0.0, float(merged["maximumSlippageBps"]))
    merged["maximumCostToEdgeRatio"] = max(0.0, float(merged.get("maximumCostToEdgeRatio", DEFAULT_REGIME_SETTINGS["maximumCostToEdgeRatio"])))
    merged["conservativeCostFallbackApproved"] = bool(merged.get("conservativeCostFallbackApproved", DEFAULT_REGIME_SETTINGS["conservativeCostFallbackApproved"]))
    merged["uncertaintyBufferBps"] = max(0.0, float(merged.get("uncertaintyBufferBps", DEFAULT_REGIME_SETTINGS["uncertaintyBufferBps"])))
    merged["estimatedFeesBps"] = max(0.0, float(merged.get("estimatedFeesBps", DEFAULT_REGIME_SETTINGS["estimatedFeesBps"])))
    merged["estimatedRegulatoryFeesBps"] = max(0.0, float(merged.get("estimatedRegulatoryFeesBps", DEFAULT_REGIME_SETTINGS["estimatedRegulatoryFeesBps"])))
    merged["marketImpactBps"] = max(0.0, float(merged.get("marketImpactBps", DEFAULT_REGIME_SETTINGS["marketImpactBps"])))
    merged["marketImpactBpsPerParticipationPct"] = max(0.0, float(merged.get("marketImpactBpsPerParticipationPct", DEFAULT_REGIME_SETTINGS["marketImpactBpsPerParticipationPct"])))
    merged["adverseSelectionBufferBps"] = max(0.0, float(merged.get("adverseSelectionBufferBps", DEFAULT_REGIME_SETTINGS["adverseSelectionBufferBps"])))
    merged["maximumCostModelAgeSeconds"] = max(1, int(merged.get("maximumCostModelAgeSeconds", DEFAULT_REGIME_SETTINGS["maximumCostModelAgeSeconds"])))
    merged["staleBarToleranceSeconds"] = max(1, int(merged["staleBarToleranceSeconds"]))
    merged["quoteAgeToleranceSeconds"] = max(1, int(merged["quoteAgeToleranceSeconds"]))
    merged["shortEntriesEnabled"] = bool(merged.get("shortEntriesEnabled", merged.get("allowShortEntries", False)))
    merged["allowShortEntries"] = bool(merged.get("allowShortEntries", merged["shortEntriesEnabled"]))
    merged["pyramidingEnabled"] = bool(merged["pyramidingEnabled"])
    merged["endOfDayFlattenEnabled"] = bool(merged.get("endOfDayFlattenEnabled", DEFAULT_REGIME_SETTINGS["endOfDayFlattenEnabled"]))
    merged["mandatoryStop"] = bool(merged.get("mandatoryStop", DEFAULT_REGIME_SETTINGS["mandatoryStop"]))
    merged["mandatoryMaxHoldingTime"] = bool(merged.get("mandatoryMaxHoldingTime", DEFAULT_REGIME_SETTINGS["mandatoryMaxHoldingTime"]))
    if merged["mlMode"] not in {"off", "shadow", "confirm_only"}:
        merged["mlMode"] = "shadow"
    merged["confirmationBars"] = max(1, int(merged["confirmationBars"]))
    merged["immediateConfidenceThreshold"] = max(0.0, min(1.0, float(merged["immediateConfidenceThreshold"])))
    merged["minimumDwellBars"] = max(0, int(merged["minimumDwellBars"]))
    merged["transitionConfidenceGap"] = max(0.0, min(1.0, float(merged["transitionConfidenceGap"])))
    merged["cooldownBars"] = max(0, int(merged["cooldownBars"]))
    merged["maximumUnknownBars"] = max(0, int(merged["maximumUnknownBars"]))
    merged["maxTradesPerDay"] = max(0, int(merged.get("maxTradesPerDay", merged.get("maxEntriesPerDay", 0))))
    merged["maxEntriesPerDay"] = max(0, int(merged.get("maxEntriesPerDay", merged["maxTradesPerDay"])))
    merged["maxConsecutiveLosses"] = max(0, int(merged["maxConsecutiveLosses"]))
    merged["maxDailyLossPercent"] = max(0.0, min(100.0, float(merged["maxDailyLossPercent"])))
    merged["settingsVersion"] = str(merged.get("settingsVersion") or REGIME_SETTINGS_VERSION)
    merged["profileVersion"] = str(merged.get("profileVersion") or REGIME_PROFILE_VERSION)
    merged["strategyCatalogVersion"] = str(merged.get("strategyCatalogVersion") or REGIME_STRATEGY_CATALOG_VERSION)
    return merged


def regime_settings_identity_from_payload(payload: dict[str, Any] | None = None) -> dict[str, str]:
    source = payload or {}
    market = _record(source.get("marketData"))
    identity = _identity_from_payload(source)
    if not identity.get("symbol") and market.get("symbol"):
        identity["symbol"] = str(market.get("symbol")).upper()
    runtime_mode = normalize_regime_runtime_mode(identity.get("runtimeMode") or source.get("runtimeMode") or "shadow").value
    normalized = {
        "algorithmId": str(identity.get("algorithmId") or REGIME_ALGORITHM_ID),
        "algorithmInstanceId": str(identity.get("algorithmInstanceId") or source.get("algorithmInstanceId") or default_regime_algorithm_instance_id(runtime_mode)),
        "accountId": str(identity.get("accountId") or source.get("accountId") or _record(source.get("account")).get("accountId") or default_regime_account_id(runtime_mode)),
        "runtimeMode": runtime_mode,
        "symbol": str(identity.get("symbol") or source.get("symbol") or market.get("symbol") or "SPY").upper(),
    }
    _validate_identity(normalized)
    return normalized


def _default_sections(identity: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = regime_settings_identity_from_payload({"identity": identity or {}})
    strategy_settings = {
        strategy_id: {
            "enabled": True,
            "lifecycle": "active",
            "settingsType": f"{strategy_id}_settings_v1",
            "family": "regime",
            "role": "safety_gate" if strategy_id in {"cash_avoid_filter", "missing_critical_data", "stale_data", "extreme_volatility", "excessive_spread", "insufficient_liquidity", "event_blackout", "halt_luld", "circuit_breaker", "unsupported_session"} else "directional",
            "maxTradesPerDay": DEFAULT_REGIME_SETTINGS["perStrategyMaxTradesPerDay"],
            "minimumNetExpectedEdge": DEFAULT_REGIME_SETTINGS["minimumNetExpectedEdge"],
            "riskMultiplier": 1.0,
            "parameters": _deepcopy_json(REGIME_STRATEGY_PARAMETER_DEFAULTS[strategy_id]),
        }
        for strategy_id in REGIME_STRATEGY_IDS
    }
    for strategy_id in {"volume_confirmation", "adx_trend_strength"}:
        strategy_settings[strategy_id]["role"] = "confirmation"
    for strategy_id in {"vwap_position", "atr_volatility_regime"}:
        strategy_settings[strategy_id]["role"] = "regime_context"
    return {
        "identity": normalized,
        "runtime": {
            "runtimeMode": normalized["runtimeMode"],
            "backgroundWorkersRequired": True,
            "liveTradingEnabled": False,
            "paperTradingOnly": True,
            "paperOnly": DEFAULT_REGIME_SETTINGS["paperOnly"],
            "symbolAllowlist": list(DEFAULT_REGIME_SETTINGS["symbolAllowlist"]),
            "timeframe": DEFAULT_REGIME_SETTINGS["timeframe"],
            "regularHoursOnly": DEFAULT_REGIME_SETTINGS["regularHoursOnly"],
            "shadowOperationEnabled": normalized["runtimeMode"] == "shadow",
        },
        "data_quality": {
            "staleBarToleranceSeconds": DEFAULT_REGIME_SETTINGS["staleBarToleranceSeconds"],
            "quoteAgeToleranceSeconds": DEFAULT_REGIME_SETTINGS["quoteAgeToleranceSeconds"],
            "requireOneMinuteBars": True,
            "requiredSymbol": "SPY",
            "timeframe": DEFAULT_REGIME_SETTINGS["timeframe"],
            "regularHoursOnly": DEFAULT_REGIME_SETTINGS["regularHoursOnly"],
        },
        "classifier": {
            "minimumRegimeConfidence": DEFAULT_REGIME_SETTINGS["minimumRegimeConfidence"],
            "minimumOneMinuteVolume": DEFAULT_REGIME_SETTINGS["minimumOneMinuteVolume"],
            "maxSpreadPercent": DEFAULT_REGIME_SETTINGS["maxSpreadPercent"],
        },
        "hysteresis": {
            "confirmationBars": DEFAULT_REGIME_SETTINGS["confirmationBars"],
            "minimumDwellBars": DEFAULT_REGIME_SETTINGS["minimumDwellBars"],
            "transitionConfidenceGap": DEFAULT_REGIME_SETTINGS["transitionConfidenceGap"],
            "cooldownBars": DEFAULT_REGIME_SETTINGS["cooldownBars"],
            "maximumUnknownBars": DEFAULT_REGIME_SETTINGS["maximumUnknownBars"],
            "immediateConfidenceThreshold": DEFAULT_REGIME_SETTINGS["immediateConfidenceThreshold"],
        },
        "strategy_catalog": {
            "catalogVersion": REGIME_STRATEGY_CATALOG_VERSION,
            "strategyIds": list(REGIME_STRATEGY_IDS),
            "strategyCount": len(REGIME_STRATEGY_IDS),
        },
        "strategy_settings": strategy_settings,
        "family_aggregation": {
            "minimumWinningScore": DEFAULT_REGIME_SETTINGS["minimumWinningScore"],
            "minimumSignalEdge": DEFAULT_REGIME_SETTINGS["minimumSignalEdge"],
            "minimumNetExpectedEdge": DEFAULT_REGIME_SETTINGS["minimumNetExpectedEdge"],
            "minimumNetExpectedEdgeBps": DEFAULT_REGIME_SETTINGS["minimumNetExpectedEdge"] * 100.0,
            "minimumActiveStrategies": DEFAULT_REGIME_SETTINGS["minimumActiveStrategies"],
            "minimumIndependentFamilies": DEFAULT_REGIME_SETTINGS["minimumIndependentFamilies"],
            "maximumAbstentionRate": DEFAULT_REGIME_SETTINGS["maximumAbstentionRate"],
            "maximumContributionPerFamily": DEFAULT_REGIME_SETTINGS["maximumContributionPerFamily"],
            "maximumCostToEdgeRatio": DEFAULT_REGIME_SETTINGS["maximumCostToEdgeRatio"],
        },
        "local_risk": {
            "baseRiskPercent": DEFAULT_REGIME_SETTINGS["baseRiskPercent"],
            "maxDailyLossPercent": DEFAULT_REGIME_SETTINGS["maxDailyLossPercent"],
            "maxConsecutiveLosses": DEFAULT_REGIME_SETTINGS["maxConsecutiveLosses"],
            "familyRiskLimits": {
                "trend": {"maxRiskPercent": 0.05},
                "momentum": {"maxRiskPercent": 0.04},
                "breakout": {"maxRiskPercent": 0.04},
                "mean_reversion": {"maxRiskPercent": 0.03},
                "vwap": {"maxRiskPercent": 0.03},
                "reversal": {"maxRiskPercent": 0.03},
                "structure": {"maxRiskPercent": 0.03},
                "event": {"maxRiskPercent": 0.02},
            },
        },
        "dynamic_profiles": {
            "profileVersion": REGIME_PROFILE_VERSION,
            "overlays": {regime: {} for regime in CANONICAL_MARKET_REGIMES},
        },
        "position_sizing": {
            "startingCapital": DEFAULT_REGIME_SETTINGS["startingCapital"],
            "baseRiskPercent": DEFAULT_REGIME_SETTINGS["baseRiskPercent"],
            "maxPositionPercent": DEFAULT_REGIME_SETTINGS["maxPositionPercent"],
            "dailyAllocationPercent": DEFAULT_REGIME_SETTINGS["dailyAllocationPercent"],
            "maxParticipationPercent": DEFAULT_REGIME_SETTINGS["maxParticipationPercent"],
            "maxAllowedShares": DEFAULT_REGIME_SETTINGS["maxAllowedShares"],
            "maxOpenRegimePositions": DEFAULT_REGIME_SETTINGS["maxOpenRegimePositions"],
            "maxOrderNotionalDollars": DEFAULT_REGIME_SETTINGS["maxOrderNotionalDollars"],
            "maxPositionNotionalDollars": DEFAULT_REGIME_SETTINGS["maxPositionNotionalDollars"],
            "maxNotionalDollars": DEFAULT_REGIME_SETTINGS["maxNotionalDollars"],
        },
        "entry_policy": {
            "pyramidingEnabled": DEFAULT_REGIME_SETTINGS["pyramidingEnabled"],
            "shortEntriesEnabled": DEFAULT_REGIME_SETTINGS["shortEntriesEnabled"],
            "allowShortEntries": DEFAULT_REGIME_SETTINGS["allowShortEntries"],
            "confirmationBars": DEFAULT_REGIME_SETTINGS["confirmationBars"],
            "entryCutoffTimeEt": DEFAULT_REGIME_SETTINGS["entryCutoffTimeEt"],
            "minimumNetExpectedEdge": DEFAULT_REGIME_SETTINGS["minimumNetExpectedEdge"],
            "minimumNetExpectedEdgeBps": DEFAULT_REGIME_SETTINGS["minimumNetExpectedEdge"] * 100.0,
        },
        "exit_policy": {
            "flattenTimeEt": DEFAULT_REGIME_SETTINGS["flattenTimeEt"],
            "maxHoldingBars": DEFAULT_REGIME_SETTINGS["maxHoldingBars"],
            "atrStopMultiplier": DEFAULT_REGIME_SETTINGS["atrStopMultiplier"],
            "minimumStopDistancePercent": DEFAULT_REGIME_SETTINGS["minimumStopDistancePercent"],
            "takeProfitR": DEFAULT_REGIME_SETTINGS["takeProfitR"],
            "endOfDayFlattenEnabled": DEFAULT_REGIME_SETTINGS["endOfDayFlattenEnabled"],
            "mandatoryStop": DEFAULT_REGIME_SETTINGS["mandatoryStop"],
            "mandatoryMaxHoldingTime": DEFAULT_REGIME_SETTINGS["mandatoryMaxHoldingTime"],
        },
        "execution": {
            "orderTimeToLiveSeconds": DEFAULT_REGIME_SETTINGS["orderTimeToLiveSeconds"],
            "maxCancelReplaceAttempts": DEFAULT_REGIME_SETTINGS["maxCancelReplaceAttempts"],
            "allowMarketEntryOrders": DEFAULT_REGIME_SETTINGS["allowMarketEntryOrders"],
            "maximumSlippageBps": DEFAULT_REGIME_SETTINGS["maximumSlippageBps"],
            "maximumCostToEdgeRatio": DEFAULT_REGIME_SETTINGS["maximumCostToEdgeRatio"],
            "conservativeCostFallbackApproved": DEFAULT_REGIME_SETTINGS["conservativeCostFallbackApproved"],
            "uncertaintyBufferBps": DEFAULT_REGIME_SETTINGS["uncertaintyBufferBps"],
            "estimatedFeesBps": DEFAULT_REGIME_SETTINGS["estimatedFeesBps"],
            "estimatedRegulatoryFeesBps": DEFAULT_REGIME_SETTINGS["estimatedRegulatoryFeesBps"],
            "marketImpactBps": DEFAULT_REGIME_SETTINGS["marketImpactBps"],
            "marketImpactBpsPerParticipationPct": DEFAULT_REGIME_SETTINGS["marketImpactBpsPerParticipationPct"],
            "adverseSelectionBufferBps": DEFAULT_REGIME_SETTINGS["adverseSelectionBufferBps"],
            "maximumCostModelAgeSeconds": DEFAULT_REGIME_SETTINGS["maximumCostModelAgeSeconds"],
            "brokerTransportMode": "paper",
        },
        "daily_limits": {
            "maxTradesPerDay": DEFAULT_REGIME_SETTINGS["maxTradesPerDay"],
            "maxEntriesPerDay": DEFAULT_REGIME_SETTINGS["maxEntriesPerDay"],
            "maxConsecutiveLosses": DEFAULT_REGIME_SETTINGS["maxConsecutiveLosses"],
            "maxDailyLossPercent": DEFAULT_REGIME_SETTINGS["maxDailyLossPercent"],
            "dailyAllocationPercent": DEFAULT_REGIME_SETTINGS["dailyAllocationPercent"],
            "perStrategyTradeLimits": {strategy_id: DEFAULT_REGIME_SETTINGS["perStrategyMaxTradesPerDay"] for strategy_id in REGIME_STRATEGY_IDS},
            "perFamilyDailyRiskLimits": {
                "trend": 0.05,
                "momentum": 0.04,
                "breakout": 0.04,
                "mean_reversion": 0.03,
                "vwap": 0.03,
                "reversal": 0.03,
                "structure": 0.03,
                "event": 0.02,
            },
        },
        "rollout": {
            "runtimeMode": normalized["runtimeMode"],
            "requireRolloutEvidence": True,
            "mlMayPromoteOrders": False,
            "liveTradingEnabled": False,
        },
        "backtest": {
            "engineVersion": "regime_backtest_v3_backend",
            "oneMinuteOnly": True,
            "symbol": "SPY",
            "settingsSource": REGIME_SETTINGS_AUTHORITATIVE_SOURCE,
        },
        "ml_shadow": {
            "mode": "shadow",
            "mayAlterSignals": False,
            "mayAlterSizing": False,
            "mayAlterOrders": False,
        },
    }


def _settings_from_sections(
    sections: dict[str, Any],
    *,
    actor: str,
    previous_settings_version: str | None,
    created_at: str | None,
) -> RegimeTradingSettings:
    _validate_sections(sections)
    stable_sections = _deepcopy_json(sections)
    created = created_at or _utc_now()
    digest_payload = {
        "sections": stable_sections,
        "settingsModelVersion": REGIME_SETTINGS_MODEL_VERSION,
        "profileVersion": REGIME_PROFILE_VERSION,
        "strategyCatalogVersion": REGIME_STRATEGY_CATALOG_VERSION,
    }
    configuration_hash = hashlib.sha256(_canonical_json(digest_payload).encode("utf-8")).hexdigest()
    settings_version = f"regime-settings-{configuration_hash[:16]}"
    return RegimeTradingSettings(
        **stable_sections,
        settings_version=settings_version,
        settings_model_version=REGIME_SETTINGS_MODEL_VERSION,
        profile_version=REGIME_PROFILE_VERSION,
        strategy_catalog_version=REGIME_STRATEGY_CATALOG_VERSION,
        created_at=created,
        created_by=str(actor or "system"),
        previous_settings_version=previous_settings_version,
        configuration_hash=configuration_hash,
    )


def _extract_sections(payload: dict[str, Any]) -> dict[str, Any]:
    if "settings" in payload and isinstance(payload["settings"], dict):
        return _extract_sections(payload["settings"])
    if "settingsSnapshot" in payload and isinstance(payload["settingsSnapshot"], dict):
        return _extract_sections(payload["settingsSnapshot"])
    sections: dict[str, Any] = {}
    for key, value in payload.items():
        canonical = SETTINGS_SECTION_ALIASES.get(key, key)
        if canonical not in SETTINGS_SECTION_NAMES:
            continue
        if canonical in sections and isinstance(sections[canonical], dict) and isinstance(value, dict):
            sections[canonical] = {**sections[canonical], **value}
        else:
            sections[canonical] = value
    return sections


def _identity_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    identity = _record(payload.get("identity"))
    runtime_mode = normalize_regime_runtime_mode(identity.get("runtimeMode") or payload.get("runtimeMode") or payload.get("runtime_mode") or "shadow").value
    return {
        "algorithmId": identity.get("algorithmId") or payload.get("algorithmId") or payload.get("algorithm_id") or REGIME_ALGORITHM_ID,
        "algorithmInstanceId": identity.get("algorithmInstanceId") or payload.get("algorithmInstanceId") or payload.get("algorithm_instance_id") or default_regime_algorithm_instance_id(runtime_mode),
        "accountId": identity.get("accountId") or payload.get("accountId") or payload.get("account_id") or default_regime_account_id(runtime_mode),
        "runtimeMode": runtime_mode,
        "symbol": str(identity.get("symbol") or payload.get("symbol") or "SPY").upper(),
    }


def _validate_identity(identity: dict[str, Any]) -> None:
    if identity.get("algorithmId") != REGIME_ALGORITHM_ID:
        raise ValueError("Regime settings require algorithmId=regime")
    normalize_regime_runtime_mode(identity.get("runtimeMode"))
    if str(identity.get("symbol") or "").upper() != "SPY":
        raise ValueError("Regime settings are currently limited to SPY")


def _validate_sections(sections: dict[str, Any]) -> None:
    _reject_unknown_sections(sections)
    _validate_identity(_record(sections.get("identity")))
    if _record(sections["runtime"]).get("liveTradingEnabled") is not False:
        raise ValueError("Regime settings cannot enable live trading")
    if _record(sections["runtime"]).get("paperTradingOnly") is not True or _record(sections["runtime"]).get("paperOnly") is not True:
        raise ValueError("Regime settings must remain paper-only")
    if _record(sections["rollout"]).get("liveTradingEnabled") is not False:
        raise ValueError("Regime rollout settings cannot enable live trading")
    if _record(sections["execution"]).get("allowMarketEntryOrders") is True:
        raise ValueError("Regime automatic paper entries cannot use market orders")
    if _record(sections["entry_policy"]).get("shortEntriesEnabled") is True or _record(sections["entry_policy"]).get("allowShortEntries") is True:
        raise ValueError("Regime initial automatic paper settings cannot enable short entries")
    if _record(sections["ml_shadow"]).get("mode") not in {"off", "shadow"}:
        raise ValueError("Regime ML must remain disabled or shadow-only")
    for key in ("mayAlterSignals", "mayAlterSizing", "mayAlterOrders"):
        if _record(sections["ml_shadow"]).get(key) is not False:
            raise ValueError("Regime ML shadow settings cannot alter signals, sizing, or orders")
    if set(_record(sections["strategy_settings"])) != set(REGIME_STRATEGY_IDS):
        raise ValueError("Regime settings require dedicated settings for every strategy ID")
    _require_non_negative(
        _record(sections["position_sizing"]),
        (
            "startingCapital",
            "baseRiskPercent",
            "maxPositionPercent",
            "dailyAllocationPercent",
            "maxParticipationPercent",
            "maxAllowedShares",
            "maxOpenRegimePositions",
            "maxOrderNotionalDollars",
            "maxPositionNotionalDollars",
            "maxNotionalDollars",
        ),
    )
    _require_non_negative(
        _record(sections["execution"]),
        (
            "orderTimeToLiveSeconds",
            "maxCancelReplaceAttempts",
            "maximumSlippageBps",
            "maximumCostToEdgeRatio",
            "uncertaintyBufferBps",
            "estimatedFeesBps",
            "estimatedRegulatoryFeesBps",
            "marketImpactBps",
            "marketImpactBpsPerParticipationPct",
            "adverseSelectionBufferBps",
            "maximumCostModelAgeSeconds",
        ),
    )
    _require_non_negative(_record(sections["daily_limits"]), ("maxTradesPerDay", "maxEntriesPerDay", "maxConsecutiveLosses", "maxDailyLossPercent", "dailyAllocationPercent"))
    if float(_record(sections["position_sizing"])["baseRiskPercent"]) > 0.10:
        raise ValueError("Initial paper baseRiskPercent cannot exceed 0.10")
    if float(_record(sections["position_sizing"])["maxPositionPercent"]) > 10.0:
        raise ValueError("Initial paper maxPositionPercent cannot exceed 10.0")
    if float(_record(sections["position_sizing"])["dailyAllocationPercent"]) > 20.0:
        raise ValueError("Initial paper dailyAllocationPercent cannot exceed 20.0")
    if int(_record(sections["position_sizing"])["maxOpenRegimePositions"]) > 1:
        raise ValueError("Initial paper maxOpenRegimePositions cannot exceed 1")
    if int(_record(sections["daily_limits"])["maxEntriesPerDay"]) > 3 or int(_record(sections["daily_limits"])["maxTradesPerDay"]) > 3:
        raise ValueError("Initial paper max entries per day cannot exceed 3")
    _validate_dynamic_profile_bounds(_record(sections["dynamic_profiles"]), sections)


def _merge_section(base: dict[str, Any], update: dict[str, Any], section: str) -> dict[str, Any]:
    allowed = _SECTION_ALLOWED_FIELDS[section]
    unknown = set(update) - allowed
    if unknown:
        raise ValueError(f"Unknown Regime settings field in {section}: {sorted(unknown)}")
    merged = _deepcopy_json(base)
    merged.update(_deepcopy_json(update))
    return merged


def _merge_strategy_settings(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    unknown_strategy_ids = set(update) - set(REGIME_STRATEGY_IDS)
    if unknown_strategy_ids:
        raise ValueError(f"Unknown Regime strategy settings IDs: {sorted(unknown_strategy_ids)}")
    merged = _deepcopy_json(base)
    for strategy_id, settings in update.items():
        settings_record = _require_dict(settings, f"strategy_settings.{strategy_id}")
        unknown = set(settings_record) - _STRATEGY_SETTINGS_FIELDS
        if unknown:
            raise ValueError(f"Unknown Regime strategy settings field for {strategy_id}: {sorted(unknown)}")
        if "lifecycle" in settings_record and settings_record["lifecycle"] not in {"active", "shadow", "disabled"}:
            raise ValueError(f"Unknown Regime strategy lifecycle for {strategy_id}: {settings_record['lifecycle']}")
        if "parameters" in settings_record:
            parameter_update = _require_dict(settings_record["parameters"], f"strategy_settings.{strategy_id}.parameters")
            allowed_parameters = set(REGIME_STRATEGY_PARAMETER_DEFAULTS[strategy_id])
            unknown_parameters = set(parameter_update) - allowed_parameters
            if unknown_parameters:
                raise ValueError(f"Unknown Regime strategy parameter for {strategy_id}: {sorted(unknown_parameters)}")
            merged_parameters = _deepcopy_json(merged[strategy_id].get("parameters") or {})
            merged_parameters.update(_deepcopy_json(parameter_update))
            settings_record = {**settings_record, "parameters": merged_parameters}
        merged[strategy_id].update(_deepcopy_json(settings_record))
    return merged


def _merge_dynamic_profiles(base: dict[str, Any], update: dict[str, Any], sections: dict[str, Any]) -> dict[str, Any]:
    unknown = set(update) - _SECTION_ALLOWED_FIELDS["dynamic_profiles"]
    if unknown:
        raise ValueError(f"Unknown Regime dynamic profile settings field: {sorted(unknown)}")
    merged = _deepcopy_json(base)
    for key, value in update.items():
        if key != "overlays":
            merged[key] = _deepcopy_json(value)
            continue
        overlays = _require_dict(value, "dynamic_profiles.overlays")
        unknown_regimes = set(overlays) - set(CANONICAL_MARKET_REGIMES)
        if unknown_regimes:
            raise ValueError(f"Unknown Regime dynamic profile overlays: {sorted(unknown_regimes)}")
        for regime, overlay in overlays.items():
            overlay_record = _require_dict(overlay, f"dynamic_profiles.overlays.{regime}")
            unknown_fields = set(overlay_record) - _PROFILE_OVERLAY_FIELDS
            if unknown_fields:
                raise ValueError(f"Unknown Regime dynamic profile overlay field for {regime}: {sorted(unknown_fields)}")
            merged["overlays"][regime] = _deepcopy_json(overlay_record)
    _validate_dynamic_profile_bounds(merged, {**sections, "dynamic_profiles": merged})
    return merged


def _validate_dynamic_profile_bounds(dynamic_profiles: dict[str, Any], sections: dict[str, Any]) -> None:
    position = _record(sections["position_sizing"])
    execution = _record(sections["execution"])
    aggregation = _record(sections["family_aggregation"])
    for regime, overlay in _record(dynamic_profiles.get("overlays")).items():
        overlay_record = _record(overlay)
        for field, baseline_field in (
            ("baseRiskPercentCap", "baseRiskPercent"),
            ("maxPositionPercentCap", "maxPositionPercent"),
            ("maxParticipationPercentCap", "maxParticipationPercent"),
        ):
            if field in overlay_record and float(overlay_record[field]) > float(position[baseline_field]):
                raise ValueError(f"Regime dynamic overlay {regime}.{field} cannot increase risk beyond baseline")
        if "maximumSlippageBps" in overlay_record and float(overlay_record["maximumSlippageBps"]) > float(execution["maximumSlippageBps"]):
            raise ValueError(f"Regime dynamic overlay {regime}.maximumSlippageBps cannot exceed baseline")
        if "maximumCostToEdgeRatio" in overlay_record and float(overlay_record["maximumCostToEdgeRatio"]) > float(execution["maximumCostToEdgeRatio"]):
            raise ValueError(f"Regime dynamic overlay {regime}.maximumCostToEdgeRatio cannot exceed baseline")
        if "minimumNetExpectedEdge" in overlay_record and float(overlay_record["minimumNetExpectedEdge"]) < float(aggregation["minimumNetExpectedEdge"]):
            raise ValueError(f"Regime dynamic overlay {regime}.minimumNetExpectedEdge cannot reduce baseline edge")
        baseline_edge_bps = float(aggregation.get("minimumNetExpectedEdgeBps", float(aggregation["minimumNetExpectedEdge"]) * 100.0))
        if "minimumNetExpectedEdgeBps" in overlay_record and float(overlay_record["minimumNetExpectedEdgeBps"]) < baseline_edge_bps:
            raise ValueError(f"Regime dynamic overlay {regime}.minimumNetExpectedEdgeBps cannot reduce baseline edge")
        if overlay_record.get("conservativeCostFallbackApproved") is True and _record(sections["execution"]).get("conservativeCostFallbackApproved") is not True:
            raise ValueError(f"Regime dynamic overlay {regime}.conservativeCostFallbackApproved cannot enable fallback beyond baseline")
        if overlay_record.get("pyramidingEnabled") is True or overlay_record.get("shortEntriesEnabled") is True:
            raise ValueError(f"Regime dynamic overlay {regime} cannot enable pyramiding or short entries")


def _reject_unknown_sections(sections: dict[str, Any]) -> None:
    unknown = set(sections) - set(SETTINGS_SECTION_NAMES)
    if unknown:
        raise ValueError(f"Unknown Regime settings sections: {sorted(unknown)}")


def _reject_unknown_payload_fields(payload: dict[str, Any]) -> None:
    if "settings" in payload and isinstance(payload["settings"], dict):
        _reject_unknown_payload_fields(payload["settings"])
        return
    if "settingsSnapshot" in payload and isinstance(payload["settingsSnapshot"], dict):
        _reject_unknown_payload_fields(payload["settingsSnapshot"])
        return
    allowed = set(SETTINGS_SECTION_NAMES) | set(SETTINGS_SECTION_ALIASES) | _SETTINGS_METADATA_FIELDS
    allowed.update({"algorithmId", "algorithmInstanceId", "accountId", "runtimeMode", "symbol"})
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(f"Unknown Regime settings fields: {sorted(unknown)}")


def _require_non_negative(section: dict[str, Any], fields: tuple[str, ...]) -> None:
    for field in fields:
        if float(section[field]) < 0:
            raise ValueError(f"Regime settings field must be non-negative: {field}")


def _require_dict(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"Regime settings section must be an object: {name}")
    return value


def _looks_like_settings_snapshot(settings: dict[str, Any]) -> bool:
    return any(section in settings for section in SETTINGS_SECTION_NAMES) or any(section in settings for section in SETTINGS_SECTION_ALIASES)


def _strategy_lifecycle_view(strategy_settings: dict[str, Any]) -> dict[str, Any]:
    return {
        strategy_id: {
            "enabled": bool(_record(settings).get("enabled", True)),
            "lifecycle": str(_record(settings).get("lifecycle") or "active"),
            "role": str(_record(settings).get("role") or "directional"),
            "family": str(_record(settings).get("family") or "regime"),
        }
        for strategy_id, settings in strategy_settings.items()
    }


def _hard_safety_limits() -> dict[str, Any]:
    return {
        "baseRiskPercent": DEFAULT_REGIME_SETTINGS["baseRiskPercent"],
        "maxPositionPercent": DEFAULT_REGIME_SETTINGS["maxPositionPercent"],
        "dailyAllocationPercent": DEFAULT_REGIME_SETTINGS["dailyAllocationPercent"],
        "maxOpenRegimePositions": DEFAULT_REGIME_SETTINGS["maxOpenRegimePositions"],
        "maxAllowedShares": DEFAULT_REGIME_SETTINGS["maxAllowedShares"],
        "maxOrderNotionalDollars": DEFAULT_REGIME_SETTINGS["maxOrderNotionalDollars"],
        "maxPositionNotionalDollars": DEFAULT_REGIME_SETTINGS["maxPositionNotionalDollars"],
        "maximumSlippageBps": DEFAULT_REGIME_SETTINGS["maximumSlippageBps"],
        "maximumCostToEdgeRatio": DEFAULT_REGIME_SETTINGS["maximumCostToEdgeRatio"],
        "minimumNetExpectedEdgeBps": DEFAULT_REGIME_SETTINGS["minimumNetExpectedEdge"] * 100.0,
        "conservativeCostFallbackApproved": DEFAULT_REGIME_SETTINGS["conservativeCostFallbackApproved"],
        "maxTradesPerDay": DEFAULT_REGIME_SETTINGS["maxTradesPerDay"],
        "maxEntriesPerDay": DEFAULT_REGIME_SETTINGS["maxEntriesPerDay"],
        "maxDailyLossPercent": DEFAULT_REGIME_SETTINGS["maxDailyLossPercent"],
        "maxHoldingBars": DEFAULT_REGIME_SETTINGS["maxHoldingBars"],
        "paperOnly": True,
        "allowMarketEntryOrders": False,
        "allowShortEntries": False,
        "endOfDayFlattenEnabled": True,
        "mandatoryStop": True,
        "mandatoryMaxHoldingTime": True,
        "liveTradingEnabled": False,
        "mlMayAlterOrders": False,
    }


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _deepcopy_json(value: Any) -> Any:
    return copy.deepcopy(value)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "DEFAULT_REGIME_SETTINGS",
    "REGIME_SETTINGS_AUTHORITATIVE_SOURCE",
    "REGIME_SETTINGS_MODEL_VERSION",
    "REGIME_STRATEGY_IDS",
    "RegimeTradingSettings",
    "build_default_regime_trading_settings",
    "flatten_regime_trading_settings",
    "regime_settings_identity_from_payload",
    "regime_trading_settings_to_dict",
    "validate_regime_settings",
    "validate_regime_trading_settings_snapshot",
]
