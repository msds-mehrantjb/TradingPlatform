"""Backend-owned Regime settings and validation."""

from __future__ import annotations

from typing import Any

from backend.app.algorithms.regime.contracts import REGIME_SETTINGS_VERSION


DEFAULT_REGIME_SETTINGS: dict[str, Any] = {
    "startingCapital": 25_000.0,
    "baseRiskPercent": 0.25,
    "maxPositionPercent": 50.0,
    "dailyAllocationPercent": 50.0,
    "minimumWinningScore": 0.60,
    "minimumSignalEdge": 0.20,
    "minimumRegimeConfidence": 0.65,
    "minimumActiveStrategies": 3,
    "minimumIndependentFamilies": 2,
    "maximumAbstentionRate": 0.60,
    "maxSpreadPercent": 0.03,
    "minimumOneMinuteVolume": 0,
    "atrStopMultiplier": 2.0,
    "minimumStopDistancePercent": 0.05,
    "takeProfitR": 1.5,
    "maxParticipationPercent": 0.30,
    "maxAllowedShares": 0,
    "shortEntriesEnabled": False,
    "pyramidingEnabled": True,
    "mlMode": "shadow",
    "confirmationBars": 3,
    "immediateConfidenceThreshold": 0.65,
    "minimumDwellBars": 0,
    "transitionConfidenceGap": 0.0,
    "maximumUnknownBars": 3,
}


def validate_regime_settings(settings: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = {**DEFAULT_REGIME_SETTINGS, **(settings or {})}
    merged["startingCapital"] = max(0.0, float(merged["startingCapital"]))
    merged["baseRiskPercent"] = max(0.0, min(5.0, float(merged["baseRiskPercent"])))
    merged["maxPositionPercent"] = max(0.0, min(100.0, float(merged["maxPositionPercent"])))
    merged["dailyAllocationPercent"] = max(0.0, min(100.0, float(merged["dailyAllocationPercent"])))
    merged["minimumWinningScore"] = max(0.0, min(1.0, float(merged["minimumWinningScore"])))
    merged["minimumSignalEdge"] = max(0.0, min(1.0, float(merged["minimumSignalEdge"])))
    merged["minimumRegimeConfidence"] = max(0.0, min(1.0, float(merged["minimumRegimeConfidence"])))
    merged["minimumActiveStrategies"] = max(0, int(merged["minimumActiveStrategies"]))
    merged["minimumIndependentFamilies"] = max(1, int(merged["minimumIndependentFamilies"]))
    merged["maximumAbstentionRate"] = max(0.0, min(1.0, float(merged["maximumAbstentionRate"])))
    merged["maxSpreadPercent"] = max(0.0, float(merged["maxSpreadPercent"]))
    merged["minimumOneMinuteVolume"] = max(0, int(merged["minimumOneMinuteVolume"]))
    merged["atrStopMultiplier"] = max(0.01, float(merged["atrStopMultiplier"]))
    merged["minimumStopDistancePercent"] = max(0.0, float(merged["minimumStopDistancePercent"]))
    merged["takeProfitR"] = max(0.1, float(merged["takeProfitR"]))
    merged["maxParticipationPercent"] = max(0.0, min(1.0, float(merged["maxParticipationPercent"])))
    merged["maxAllowedShares"] = max(0, int(merged["maxAllowedShares"]))
    merged["shortEntriesEnabled"] = bool(merged["shortEntriesEnabled"])
    merged["pyramidingEnabled"] = bool(merged["pyramidingEnabled"])
    if merged["mlMode"] not in {"off", "shadow", "confirm_only", "active"}:
        merged["mlMode"] = "shadow"
    merged["settingsVersion"] = REGIME_SETTINGS_VERSION
    return merged

