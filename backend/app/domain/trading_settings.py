from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from typing import Any

from backend.app.domain.models import BaselineTradingSettings, DynamicPolicyBounds, HardRiskLimits


TRADING_SETTINGS_SCHEMA_VERSION = "canonical_trading_settings_v2"

TRADING_SETTINGS_FIELD_GROUPS: dict[str, tuple[str, ...]] = {
    "baselineSettings": (
        "baseRiskPercent",
        "basePositionPercent",
        "baseOrderAllocationPercent",
        "baseDailyAllocationPercent",
        "baseAtrStopMultiplier",
        "baseMinimumStopPercent",
        "baseTargetR",
        "baseMaximumHoldingMinutes",
        "baseParticipationPercent",
        "baseEntryOffsetBps",
        "baseSlippagePerShare",
        "minimumExpectedValue",
        "minimumModelProbability",
    ),
    "hardLimits": (
        "maximumRiskPerTradePercent",
        "maximumDailyLossPercent",
        "maximumOpenRiskPercent",
        "maximumPositionPercent",
        "maximumOrderNotionalPercent",
        "maximumDailyNotionalPercent",
        "maximumShares",
        "maximumVolumeParticipationPercent",
        "maximumTradesPerDay",
        "maximumConsecutiveLosses",
        "maximumSpreadBps",
        "allowPyramiding",
        "newEntryCutoff",
    ),
    "dynamicBounds": (
        "minimumRiskMultiplier",
        "maximumRiskMultiplier",
        "minimumTargetR",
        "maximumTargetR",
        "minimumHoldingMinutes",
        "maximumHoldingMinutes",
        "minimumAtrStopMultiplier",
        "maximumAtrStopMultiplier",
    ),
}


def default_baseline_trading_settings() -> BaselineTradingSettings:
    return BaselineTradingSettings(
        configurationHash="pending",
    )


def default_hard_risk_limits() -> HardRiskLimits:
    return HardRiskLimits(
        configurationHash="pending",
    )


def default_dynamic_policy_bounds() -> DynamicPolicyBounds:
    return DynamicPolicyBounds(
        minConfidence=0.0,
        minReliability=0.0,
        minRegimeFit=0.0,
        maxSpreadPercent=100.0,
        maxParticipationPercent=100.0,
        minLiquidityShares=0,
        configurationHash="pending",
    )


def canonical_trading_settings_payload(
    *,
    baseline_settings: BaselineTradingSettings,
    hard_limits: HardRiskLimits,
    dynamic_bounds: DynamicPolicyBounds,
    strategy_configuration_hash: str = "",
    ensemble_configuration_hash: str = "",
    ml_configuration_hash: str = "",
    risk_configuration_hash: str = "",
    sizing_configuration_hash: str = "",
    entry_configuration_hash: str = "",
    exit_configuration_hash: str = "",
    gate_configuration_hash: str = "",
    backtest_configuration_hash: str = "",
) -> dict[str, Any]:
    return {
        "schemaVersion": TRADING_SETTINGS_SCHEMA_VERSION,
        "baselineSettings": _select_fields(baseline_settings, TRADING_SETTINGS_FIELD_GROUPS["baselineSettings"]),
        "hardLimits": _select_fields(hard_limits, TRADING_SETTINGS_FIELD_GROUPS["hardLimits"]),
        "dynamicBounds": _select_fields(dynamic_bounds, TRADING_SETTINGS_FIELD_GROUPS["dynamicBounds"]),
        "artifactInputs": {
            "strategyConfigurationHash": strategy_configuration_hash,
            "ensembleConfigurationHash": ensemble_configuration_hash,
            "mlConfigurationHash": ml_configuration_hash,
            "riskConfigurationHash": risk_configuration_hash,
            "sizingConfigurationHash": sizing_configuration_hash,
            "entryConfigurationHash": entry_configuration_hash,
            "exitConfigurationHash": exit_configuration_hash,
            "gateConfigurationHash": gate_configuration_hash,
            "backtestConfigurationHash": backtest_configuration_hash,
        },
    }


def trading_settings_configuration_hash(
    *,
    baseline_settings: BaselineTradingSettings,
    hard_limits: HardRiskLimits,
    dynamic_bounds: DynamicPolicyBounds,
    strategy_configuration_hash: str = "",
    ensemble_configuration_hash: str = "",
    ml_configuration_hash: str = "",
    risk_configuration_hash: str = "",
    sizing_configuration_hash: str = "",
    entry_configuration_hash: str = "",
    exit_configuration_hash: str = "",
    gate_configuration_hash: str = "",
    backtest_configuration_hash: str = "",
) -> str:
    payload = canonical_trading_settings_payload(
        baseline_settings=baseline_settings,
        hard_limits=hard_limits,
        dynamic_bounds=dynamic_bounds,
        strategy_configuration_hash=strategy_configuration_hash,
        ensemble_configuration_hash=ensemble_configuration_hash,
        ml_configuration_hash=ml_configuration_hash,
        risk_configuration_hash=risk_configuration_hash,
        sizing_configuration_hash=sizing_configuration_hash,
        entry_configuration_hash=entry_configuration_hash,
        exit_configuration_hash=exit_configuration_hash,
        gate_configuration_hash=gate_configuration_hash,
        backtest_configuration_hash=backtest_configuration_hash,
    )
    return _stable_hash(payload)


def risk_dollars_for_signal_multiplier(
    *,
    account_equity: float,
    baseline_settings: BaselineTradingSettings,
    signal_multiplier: float,
    risk_multiplier: float = 1.0,
) -> float:
    baseline_risk = max(0.0, float(account_equity)) * (max(0.0, baseline_settings.baseRiskPercent) / 100.0)
    return baseline_risk * max(0.0, float(signal_multiplier)) * max(0.0, float(risk_multiplier))


def _select_fields(model: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    payload = model.model_dump(mode="json") if hasattr(model, "model_dump") else dict(model)
    return {field: payload[field] for field in fields}


def _stable_hash(value: Any) -> str:
    serialized = json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return value
