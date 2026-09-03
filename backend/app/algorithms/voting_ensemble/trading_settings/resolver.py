"""Authoritative one-minute settings resolver for Voting Ensemble."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from math import floor
from typing import Any

from backend.app.algorithms.voting_ensemble.strategies.registry import StrategyCollection, active_module_ids
from backend.app.algorithms.voting_ensemble.trading_settings.baseline import (
    VOTING_ENSEMBLE_ONE_MINUTE_BASELINE_VERSION,
    one_minute_baseline_settings,
)
from backend.app.algorithms.voting_ensemble.trading_settings.hashing import trading_settings_hash
from backend.app.algorithms.voting_ensemble.trading_settings.models import (
    AggregationThresholdSettings,
    CancellationReplacementPolicySettings,
    ContextBoundsSettings,
    DailyLossCapSettings,
    DataFreshnessSettings,
    EventBlackoutSettings,
    ExpenseModelSettings,
    HoldingTimePolicySettings,
    LatencyLimitSettings,
    MaximumTradesSettings,
    MinimumFamilySupportSettings,
    NetEdgeRequirementSettings,
    OrderTypeLimitPolicySettings,
    PaperExecutionModeSettings,
    PositionNotionalCapSettings,
    ProfileOverlayLimitSettings,
    ResolvedTradingProfileSettings,
    RiskPerTradeSettings,
    SessionWindowSettings,
    SlippageLimitSettings,
    SpreadLimitSettings,
    StopPolicySettings,
    StrategyEnablementSettings,
    TargetPolicySettings,
    VOTING_ENSEMBLE_ONE_MINUTE_PROFILE_VERSION,
    VOTING_ENSEMBLE_ONE_MINUTE_SETTINGS_VERSION,
    VotingEnsembleOneMinuteSettings,
)
from backend.app.algorithms.voting_ensemble.trading_settings.profiles import apply_profile_to_config, resolve_dynamic_trading_profile
from backend.app.algorithms.voting_ensemble.trading_settings.validation import reject_forbidden_runtime_keys, validate_one_minute_settings


def resolve_one_minute_trading_settings(settings_payload: dict[str, Any] | None = None) -> VotingEnsembleOneMinuteSettings:
    settings_dict = settings_payload if isinstance(settings_payload, dict) else {}
    reject_forbidden_runtime_keys(settings_dict)
    baseline = one_minute_baseline_settings()
    config = _apply_payload_overrides(baseline, settings_dict)
    profile = resolve_dynamic_trading_profile(settings_dict)
    effective = apply_profile_to_config(config, profile)
    effective["positionSizing"] = (
        "shares = per-order allocation dollars / entry price, with planned risk checked against order risk budget"
        if effective["positionSizingMode"] == "allocation"
        else baseline["positionSizing"]
    )
    settings_without_hash = _settings_model_payload(effective, profile, configuration_hash="pending")
    configuration_hash = trading_settings_hash(settings_without_hash)
    resolved = VotingEnsembleOneMinuteSettings.model_validate(
        {
            **settings_without_hash,
            "configurationHash": configuration_hash,
            "resolutionTimestamp": datetime.now(UTC),
        }
    )
    return validate_one_minute_settings(resolved)


def dynamic_risk_config(settings_payload: dict[str, Any]) -> dict[str, Any]:
    settings_dict = settings_payload if isinstance(settings_payload, dict) else {}
    profile = resolve_dynamic_trading_profile(settings_dict)
    settings = resolve_one_minute_trading_settings(settings_payload)
    return one_minute_settings_to_legacy_risk_config(settings, profile=profile)


def apply_dynamic_trading_profile(config: dict[str, Any], settings_payload: dict[str, Any]) -> dict[str, Any]:
    base = _one_minute_only_config(config)
    settings_dict = settings_payload if isinstance(settings_payload, dict) else {}
    reject_forbidden_runtime_keys(settings_dict)
    profile = resolve_dynamic_trading_profile(settings_dict)
    effective = apply_profile_to_config(base, profile)
    resolved = resolve_one_minute_trading_settings({**settings_dict, **effective})
    return one_minute_settings_to_legacy_risk_config(resolved, profile=profile)


def one_minute_settings_to_legacy_risk_config(settings: VotingEnsembleOneMinuteSettings, *, profile: dict[str, Any] | None = None) -> dict[str, Any]:
    profile_payload = profile or {
        "profileId": "baseline" if settings.appliedOverlays == ("baseline",) else "dynamic-" + "-".join(name.replace(".", "_") for name in settings.appliedOverlays if name != "baseline"),
        "activeOverlays": settings.appliedOverlays,
        "riskMultiplier": 1.0,
        "allocationMultiplier": 1.0,
        "dailyAllocationMultiplier": 1.0,
        "maxTradesMultiplier": 1.0,
        "slippageMultiplier": 1.0,
        "estimatedCostMultiplier": 1.0,
        "blockNewEntries": settings.entriesBlocked,
        "reasonCodes": tuple(f"voting_ensemble.trading_profile.{name}" for name in settings.appliedOverlays),
    }
    payload = {
        "algorithmId": settings.algorithmId,
        "settingsVersion": settings.settingsVersion,
        "profileVersion": settings.profileVersion,
        "configurationHash": settings.configurationHash,
        "sourceBaselineVersion": settings.sourceBaselineVersion,
        "appliedOverlays": settings.appliedOverlays,
        "resolutionTimestamp": settings.resolutionTimestamp.isoformat(),
        "reasonCodes": settings.reasonCodes,
        "startingCapital": settings.riskPerTrade.startingCapital,
        "riskPerTradePercent": settings.riskPerTrade.riskPerTradePercent,
        "riskBudgetPercentOfOrder": settings.riskPerTrade.riskBudgetPercentOfOrder,
        "maxDailyLossPercent": settings.dailyLossCap.maxDailyLossPercent,
        "maxTradesPerDay": settings.maximumTrades.maxTradesPerDay,
        "sessionStart": settings.sessionWindows.sessionStart,
        "newTradesUntil": settings.sessionWindows.newTradesUntil,
        "forceClose": settings.sessionWindows.forceClose,
        "execution": "next candle open",
        "stopLossPercent": settings.stopPolicy.stopLossPercent,
        "fixedStopDistanceDollars": settings.stopPolicy.fixedStopDistanceDollars,
        "takeProfitR": settings.targetPolicy.takeProfitR,
        "slippagePerShare": settings.slippageLimits.slippagePerShare,
        "expenseModel": settings.expenseModel.model_dump(mode="json"),
        "positionSizing": settings.positionSizing,
        "entryConfirmationBars": settings.dataFreshness.entryConfirmationBars,
        "warmupBars": settings.dataFreshness.warmupBars,
        "allowedEntryHours": list(settings.sessionWindows.allowedEntryHours),
        "orderAllocationPercent": settings.positionNotionalCap.orderAllocationPercent,
        "dailyAllocationPercent": settings.positionNotionalCap.dailyAllocationPercent,
        "maximumPositionPercent": settings.positionNotionalCap.maximumPositionPercent,
        "maxShareQuantity": settings.positionNotionalCap.maxShareQuantity,
        "positionSizingMode": settings.positionSizingMode,
        "entriesBlocked": settings.entriesBlocked,
        "paperExecutionMode": settings.paperExecutionMode.model_dump(mode="json"),
        "tradingProfile": {
            "profileId": profile_payload["profileId"],
            "profileVersion": settings.profileVersion,
            "baselineSettingsVersion": settings.sourceBaselineVersion,
            "activeOverlays": settings.appliedOverlays,
            "riskMultiplier": profile_payload["riskMultiplier"],
            "allocationMultiplier": profile_payload["allocationMultiplier"],
            "dailyAllocationMultiplier": profile_payload["dailyAllocationMultiplier"],
            "maxTradesMultiplier": profile_payload["maxTradesMultiplier"],
            "slippageMultiplier": profile_payload["slippageMultiplier"],
            "estimatedCostMultiplier": profile_payload.get("estimatedCostMultiplier", profile_payload["slippageMultiplier"]),
            "blockNewEntries": settings.entriesBlocked,
            "reasonCodes": profile_payload["reasonCodes"],
        },
        "resolvedTradingProfile": settings.resolvedTradingProfile.model_dump(mode="json"),
        "oneMinuteSettingsHash": settings.configurationHash,
        "minimumFinalScore": settings.resolvedTradingProfile.minimumFinalScore,
        "minimumIndependentFamilySupport": settings.resolvedTradingProfile.minimumIndependentFamilySupport,
        "minimumEdgeToCostRatio": settings.resolvedTradingProfile.minimumEdgeToCostRatio,
        "maximumSlippagePerShare": settings.resolvedTradingProfile.maximumSlippagePerShare,
        "limitOrderOffsetBps": settings.resolvedTradingProfile.limitOrderOffsetBps,
        "cancelReplaceTimeoutSeconds": settings.resolvedTradingProfile.cancelReplaceTimeoutSeconds,
        "cooldownSeconds": settings.resolvedTradingProfile.cooldownSeconds,
    }
    payload["configurationHash"] = trading_settings_hash(payload)
    return payload


def risk_config_hash(config: dict[str, Any]) -> str:
    return trading_settings_hash(config)


def _apply_payload_overrides(baseline: dict[str, Any], settings_payload: dict[str, Any]) -> dict[str, Any]:
    config = deepcopy(baseline)

    config["startingCapital"] = _number(settings_payload, "startingCapital", config["startingCapital"], minimum=1000.0, maximum=10_000_000.0)
    config["orderAllocationPercent"] = _number(settings_payload, "orderAllocationPercent", 10.0, minimum=0.1, maximum=100.0)
    config["dailyAllocationPercent"] = _number(settings_payload, "dailyAllocationPercent", 30.0, minimum=0.1, maximum=100.0)
    config["riskBudgetPercentOfOrder"] = _number(settings_payload, "riskBudgetPercentOfOrder", 50.0, minimum=0.1, maximum=100.0)
    config["riskPerTradePercent"] = _number(settings_payload, "riskPerTradePercent", config["riskPerTradePercent"], minimum=0.01, maximum=100.0)
    config["maxDailyLossPercent"] = _number(settings_payload, "maxDailyLossPercent", config["maxDailyLossPercent"], minimum=0.1, maximum=100.0)
    # Zero is "no fixed cap": trading for the day is then bounded by the daily-loss,
    # drawdown and exposure limits. A positive cap is still clamped to what the daily
    # allocation can fund at the per-order allocation.
    requested_max_trades = int(_number(settings_payload, "maxTradesPerDay", config["maxTradesPerDay"], minimum=0, maximum=50))
    allocation_trade_cap = max(1, int(config["dailyAllocationPercent"] // max(config["orderAllocationPercent"], 0.1)))
    config["maxTradesPerDay"] = 0 if requested_max_trades <= 0 else min(requested_max_trades, allocation_trade_cap)
    config["stopLossPercent"] = _number(settings_payload, "stopLossPercent", config["stopLossPercent"], minimum=0.01, maximum=20.0)
    config["fixedStopDistanceDollars"] = _number(settings_payload, "fixedStopDistanceDollars", config["fixedStopDistanceDollars"], minimum=0.0, maximum=100.0)
    config["takeProfitR"] = _number(settings_payload, "takeProfitR", config["takeProfitR"], minimum=0.1, maximum=20.0)
    config["slippagePerShare"] = _number(settings_payload, "slippagePerShare", config["slippagePerShare"], minimum=0.0, maximum=10.0)
    config["positionSizingMode"] = str(settings_payload.get("positionSizingMode") or config["positionSizingMode"])
    return config


def _settings_model_payload(config: dict[str, Any], profile: dict[str, Any], *, configuration_hash: str) -> dict[str, Any]:
    return {
        "algorithmId": "voting_ensemble",
        "settingsVersion": VOTING_ENSEMBLE_ONE_MINUTE_SETTINGS_VERSION,
        "profileVersion": VOTING_ENSEMBLE_ONE_MINUTE_PROFILE_VERSION,
        "configurationHash": configuration_hash,
        "sourceBaselineVersion": VOTING_ENSEMBLE_ONE_MINUTE_BASELINE_VERSION,
        "appliedOverlays": tuple(profile["activeOverlays"]),
        "resolutionTimestamp": datetime.now(UTC),
        "reasonCodes": (
            "voting_ensemble.trading_settings.one_minute_resolved",
            *tuple(profile["reasonCodes"]),
        ),
        "strategyEnablement": StrategyEnablementSettings(
            enabledDirectionalStrategies=active_module_ids(StrategyCollection.DIRECTIONAL),
            enabledContextModules=active_module_ids(StrategyCollection.CONTEXT),
            enabledSafetyFilters=active_module_ids(StrategyCollection.SAFETY),
        ),
        "aggregationThresholds": AggregationThresholdSettings(
            minEligibleDirectionalVotes=int(config["minEligibleDirectionalVotes"]),
            minWinningVotes=int(config["minWinningVotes"]),
            minVoteEdge=float(config["minVoteEdge"]),
            holdBand=float(config["holdBand"]),
            reliabilityWeightingMode=str(config.get("reliabilityWeightingMode", "shadow")),
            reliabilitySampleWindow=str(config.get("reliabilitySampleWindow", "rolling_60_trades")),
        ),
        "minimumFamilySupport": MinimumFamilySupportSettings(
            minimumFamiliesForTrade=int(config["minimumFamiliesForTrade"]),
            familyWeights=dict(config["familyWeights"]),
        ),
        "contextBounds": ContextBoundsSettings(
            maxContextBoost=float(config["maxContextBoost"]),
            maxContextPenalty=float(config["maxContextPenalty"]),
        ),
        "sessionWindows": SessionWindowSettings(
            sessionStart=str(config["sessionStart"]),
            newTradesUntil=str(config["newTradesUntil"]),
            forceClose=str(config["forceClose"]),
            allowedEntryHours=tuple(str(value) for value in config["allowedEntryHours"]),
        ),
        "eventBlackouts": EventBlackoutSettings(blackoutMinutesBefore=0, blackoutMinutesAfter=0),
        "dataFreshness": DataFreshnessSettings(
            warmupBars=int(config["warmupBars"]),
            entryConfirmationBars=int(config["entryConfirmationBars"]),
            maxPrimaryFeedAgeSeconds=int(config["maxPrimaryFeedAgeSeconds"]),
            maxAuxiliaryFeedAgeSeconds=int(config["maxAuxiliaryFeedAgeSeconds"]),
        ),
        "latencyLimits": LatencyLimitSettings(
            maxDecisionLatencyMs=int(config["maxDecisionLatencyMs"]),
            maxQueueLatencyMs=int(config["maxQueueLatencyMs"]),
            commandDeadlineSeconds=int(config["commandDeadlineSeconds"]),
        ),
        "spreadLimits": SpreadLimitSettings(
            maximumSpreadBps=float(config["maximumSpreadBps"]),
            maximumSpreadDollars=float(config["maximumSpreadDollars"]),
        ),
        "slippageLimits": SlippageLimitSettings(
            slippagePerShare=float(config["slippagePerShare"]),
            maxSlippagePerShare=float(config["maxSlippagePerShare"]),
            slippageReserveMultiplier=1.0,
        ),
        "netEdgeRequirements": NetEdgeRequirementSettings(minimumNetEdgeR=float(config["minimumNetEdgeR"])),
        "riskPerTrade": RiskPerTradeSettings(
            startingCapital=float(config["startingCapital"]),
            riskPerTradePercent=float(config["riskPerTradePercent"]),
            riskBudgetPercentOfOrder=float(config["riskBudgetPercentOfOrder"]),
        ),
        "dailyLossCap": DailyLossCapSettings(maxDailyLossPercent=float(config["maxDailyLossPercent"])),
        "positionNotionalCap": PositionNotionalCapSettings(
            orderAllocationPercent=float(config["orderAllocationPercent"]),
            dailyAllocationPercent=float(config["dailyAllocationPercent"]),
            maximumPositionPercent=float(config["maximumPositionPercent"]),
            maxShareQuantity=int(config["maxShareQuantity"]),
        ),
        "maximumTrades": MaximumTradesSettings(
            maxTradesPerDay=int(config["maxTradesPerDay"]),
            maxConcurrentPositions=int(config["maxConcurrentPositions"]),
        ),
        "stopPolicy": StopPolicySettings(
            stopLossPercent=float(config["stopLossPercent"]),
            fixedStopDistanceDollars=float(config["fixedStopDistanceDollars"]),
            minimumStopDistanceDollars=float(config["minimumStopDistanceDollars"]),
        ),
        "targetPolicy": TargetPolicySettings(takeProfitR=float(config["takeProfitR"])),
        "holdingTimePolicy": HoldingTimePolicySettings(maximumHoldingMinutes=int(config["maximumHoldingMinutes"])),
        "orderTypeAndLimitPolicy": OrderTypeLimitPolicySettings(),
        "cancellationAndReplacementPolicy": CancellationReplacementPolicySettings(
            cancelUnfilledAfterSeconds=int(config["cancelUnfilledAfterSeconds"]),
            maxReplacementAttempts=int(config["maxReplacementAttempts"]),
        ),
        "profileOverlayLimits": ProfileOverlayLimitSettings(
            minimumRiskMultiplier=float(config["minimumRiskMultiplier"]),
            minimumAllocationMultiplier=float(config["minimumAllocationMultiplier"]),
            maximumSlippageMultiplier=float(config["maximumSlippageMultiplier"]),
        ),
        "paperExecutionMode": PaperExecutionModeSettings(),
        "expenseModel": ExpenseModelSettings.model_validate(config["expenseModel"]),
        "resolvedTradingProfile": _resolved_profile_settings(config, profile),
        "entriesBlocked": bool(config.get("entriesBlocked")),
        "positionSizingMode": str(config["positionSizingMode"]),
        "positionSizing": str(config["positionSizing"]),
    }


def _resolved_profile_settings(config: dict[str, Any], profile: dict[str, Any]) -> ResolvedTradingProfileSettings:
    return ResolvedTradingProfileSettings(
        profileId=str(profile["profileId"]),
        activeOverlays=tuple(profile["activeOverlays"]),
        overlayReasons=tuple(profile["reasonCodes"]),
        entryPermission="block_new_entries" if bool(config.get("entriesBlocked")) else "allow_new_entries",
        entriesBlocked=bool(config.get("entriesBlocked")),
        exitManagementEnabled=True,
        riskMultiplier=float(profile["riskMultiplier"]),
        allocationMultiplier=float(profile["allocationMultiplier"]),
        dailyAllocationMultiplier=float(profile["dailyAllocationMultiplier"]),
        maxTradesMultiplier=float(profile["maxTradesMultiplier"]),
        estimatedCostMultiplier=float(profile.get("estimatedCostMultiplier", profile["slippageMultiplier"])),
        riskPerTradePercent=float(config["riskPerTradePercent"]),
        orderAllocationPercent=float(config["orderAllocationPercent"]),
        dailyAllocationPercent=float(config["dailyAllocationPercent"]),
        maximumPositionPercent=float(config["maximumPositionPercent"]),
        maxShareQuantity=int(config["maxShareQuantity"]),
        maxTradesPerDay=int(config["maxTradesPerDay"]),
        minimumFinalScore=float(config["minVoteEdge"]),
        minimumIndependentFamilySupport=int(config["minimumFamiliesForTrade"]),
        minimumNetEdgeR=float(config["minimumNetEdgeR"]),
        minimumEdgeToCostRatio=float(config.get("minimumEdgeToCostRatio", 1.0)),
        maximumSpreadBps=float(config["maximumSpreadBps"]),
        maximumSpreadDollars=float(config["maximumSpreadDollars"]),
        maximumSlippagePerShare=float(config["maxSlippagePerShare"]),
        stopMultiplier=float(profile["stopMultiplier"]),
        targetMultiplier=float(profile["targetMultiplier"]),
        maximumHoldingMinutes=int(config["maximumHoldingMinutes"]),
        limitOrderOffsetBps=float(config.get("limitOrderOffsetBps", 0.0)),
        cancelReplaceTimeoutSeconds=int(config["cancelUnfilledAfterSeconds"]),
        cooldownSeconds=int(config.get("cooldownSeconds", 0)),
        sourceInputs=dict(profile.get("sourceInputs") or {}),
    )


def _one_minute_only_config(config: dict[str, Any]) -> dict[str, Any]:
    baseline = one_minute_baseline_settings()
    return {key: deepcopy(config.get(key, value)) for key, value in baseline.items()}


def _number(payload: dict[str, Any], name: str, default: float, *, minimum: float, maximum: float) -> float:
    try:
        value = float(payload.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))
