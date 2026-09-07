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
from backend.app.algorithms.voting_ensemble.trading_settings.editable import (
    EDITABLE_ONE_MINUTE_FIELDS,
    EditableTradingSettingField,
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
from backend.app.algorithms.voting_ensemble.trading_settings.validation import (
    TIME_PATTERN,
    reject_forbidden_runtime_keys,
    validate_one_minute_settings,
)


def resolve_one_minute_trading_settings(settings_payload: dict[str, Any] | None = None) -> VotingEnsembleOneMinuteSettings:
    settings_dict = settings_payload if isinstance(settings_payload, dict) else {}
    reject_forbidden_runtime_keys(settings_dict)
    baseline = one_minute_baseline_settings()
    config = _apply_payload_overrides(baseline, settings_dict)
    profile = resolve_dynamic_trading_profile(settings_dict)
    effective = apply_profile_to_config(config, profile)
    # One sizing rule, stated by the baseline: the minimum over every cap. The old
    # mode switch chose between two descriptions of a rule that sizing never read.
    effective["positionSizing"] = baseline["positionSizing"]
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


def effective_override_config(settings_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """The baseline with the overrides folded in, before any dynamic profile overlay.

    This is what the settings editor reads back to show the operator the value that will
    actually trade, rather than echoing what they typed: the registry bounds clamp it and
    the trade cap is limited by what the daily allocation can fund.
    """
    return _apply_payload_overrides(one_minute_baseline_settings(), settings_payload if isinstance(settings_payload, dict) else {})


def _apply_payload_overrides(baseline: dict[str, Any], settings_payload: dict[str, Any]) -> dict[str, Any]:
    """Fold the operator's overrides onto the baseline, one registry field at a time.

    This used to name ten keys by hand, so the other parameters the pipeline reads --
    the ATR stop multiplier, the trailing geometry, the vote thresholds, the family
    weights, the session window -- were unreachable from any caller. The registry in
    `editable.py` is now the list, and every field on it names the read site that makes
    it take effect, so nothing here can drift from what actually trades.

    Unknown keys are ignored rather than rejected. A payload arriving from an older
    client still carries `riskBudgetPercentOfOrder` and `positionSizingMode`, neither of
    which sizing ever read; failing the save would strand that client.
    """
    config = deepcopy(baseline)

    for spec in EDITABLE_ONE_MINUTE_FIELDS:
        raw = _payload_value(settings_payload, spec.path)
        if raw is None:
            continue
        value = _coerce_field(spec, raw)
        if value is None:
            continue
        _set_config_value(config, spec.path, value)

    # Zero is "no fixed cap": trading for the day is then bounded by the daily-loss,
    # drawdown and exposure limits. A positive cap is still clamped to what the daily
    # allocation can fund at the per-order allocation.
    requested_max_trades = int(config["maxTradesPerDay"])
    allocation_trade_cap = max(1, int(config["dailyAllocationPercent"] // max(config["orderAllocationPercent"], 0.1)))
    config["maxTradesPerDay"] = 0 if requested_max_trades <= 0 else min(requested_max_trades, allocation_trade_cap)
    return config


def _payload_value(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    """Read one field from the payload, accepting the dotted key or the nested object.

    A client may send `{"familyWeights": {"trend": 1.2}}` or `{"familyWeights.trend": 1.2}`;
    the dashboard sends the flat form and the stored override file keeps the nested one.
    """
    dotted = ".".join(path)
    if dotted in payload:
        return payload[dotted]
    cursor: Any = payload
    for segment in path:
        if not isinstance(cursor, dict) or segment not in cursor:
            return None
        cursor = cursor[segment]
    return cursor


def _config_value(config: dict[str, Any], path: tuple[str, ...]) -> Any:
    cursor: Any = config
    for segment in path:
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(segment)
    return cursor


def _set_config_value(config: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    cursor = config
    for segment in path[:-1]:
        nested = cursor.get(segment)
        if not isinstance(nested, dict):
            nested = {}
            cursor[segment] = nested
        cursor = nested
    cursor[path[-1]] = value


def _coerce_field(spec: EditableTradingSettingField, raw: Any) -> Any:
    """Coerce and clamp one override, or return None to keep the baseline value.

    A value that cannot be read as the declared kind is discarded rather than raised on:
    the alternative is one malformed field failing a whole save, and the resolver runs on
    every evaluation, not only on an operator edit.
    """
    if spec.kind == "boolean":
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, str) and raw.strip().lower() in {"true", "false"}:
            return raw.strip().lower() == "true"
        return None
    if spec.kind == "choice":
        candidate = str(raw).strip()
        return candidate if candidate in spec.choices else None
    if spec.kind == "time":
        candidate = str(raw).strip()
        return candidate if TIME_PATTERN.match(candidate) else None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    if spec.minimum is not None:
        number = max(float(spec.minimum), number)
    if spec.maximum is not None:
        number = min(float(spec.maximum), number)
    if spec.kind == "integer":
        return int(round(number))
    return number


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
        # Documented inert: warmupBars and entryConfirmationBars are consumed by nothing
        # (snapshot readiness has its own history requirement; strategies confirm their
        # own entries), and the two feed-age limits have no consumer either. The producer
        # freshness checks use their own constants (5 s quote, 10 s trade, 90 s auxiliary).
        # A gate reading the bar's age since completion and the auxiliary feeds' age from
        # the snapshot against these values is what would make them bind.
        "dataFreshness": DataFreshnessSettings(
            warmupBars=int(config["warmupBars"]),
            entryConfirmationBars=int(config["entryConfirmationBars"]),
            maxPrimaryFeedAgeSeconds=int(config["maxPrimaryFeedAgeSeconds"]),
            maxAuxiliaryFeedAgeSeconds=int(config["maxAuxiliaryFeedAgeSeconds"]),
        ),
        # Documented inert: only commandDeadlineSeconds is consumed (the decision-deadline
        # gate reads it against decisionAgeSeconds). maxDecisionLatencyMs and
        # maxQueueLatencyMs have no consumer; the worker measuring its queue delay and the
        # service its in-process durations against them is what would feed them.
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
            # Documented inert: no gate reads maxConcurrentPositions. The exposure caps
            # bound a second position in practice, and an opposite candidate is the
            # reversal exit the paper account nets. A gate counting open local positions
            # (letting a netting candidate through) is what would feed it.
            maxConcurrentPositions=int(config["maxConcurrentPositions"]),
        ),
        "stopPolicy": StopPolicySettings(
            stopLossPercent=float(config["stopLossPercent"]),
            fixedStopDistanceDollars=float(config["fixedStopDistanceDollars"]),
            minimumStopDistanceDollars=float(config["minimumStopDistanceDollars"]),
            atrMultiplier=float(config.get("stopAtrMultiplier", 1.5)),
            breakevenTriggerR=float(config.get("breakevenTriggerR", 1.0)),
            trailingStopR=float(config.get("trailingStopR", 1.0)),
            trailingEnabled=bool(config.get("trailingStopEnabled", True)),
        ),
        "targetPolicy": TargetPolicySettings(
            takeProfitR=float(config["takeProfitR"]),
            minimumTakeProfitR=float(config.get("minimumTakeProfitR", 1.0)),
            structuralTargets=bool(config.get("structuralTargets", True)),
        ),
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
