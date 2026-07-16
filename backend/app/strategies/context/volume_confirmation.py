from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain.feature_engine import FeatureQuality
from backend.app.domain.models import ContextSignal, Direction, Signal
from backend.app.strategies.base import StrategyEvaluationContext
from backend.app.strategies.registry import StrategyCollection, resolve_strategy


class VolumeConfirmationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "volume_confirmation_v1"
    lookbackCandles: int = Field(default=20, ge=5, le=120)
    breakoutRelativeVolumeThreshold: float = Field(default=1.4, ge=0)
    pullbackQuietVolumeThreshold: float = Field(default=0.9, ge=0)
    maxConfidenceAdjustment: float = Field(default=0.10, ge=0, le=0.25)

    @property
    def configurationHash(self) -> str:
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class VolumeEvidence:
    dataReady: bool
    relativeVolume: float | None
    breakoutVolumeConfirmation: bool
    pullbackVolumeBehavior: str
    volumeTrend: str
    dataQuality: float
    contextEffect: str
    reasonCodes: list[str]


class VolumeConfirmationContext:
    registryEntry = resolve_strategy("volume_confirmation")

    def __init__(self, config: VolumeConfirmationConfig | None = None) -> None:
        self.config = config or VolumeConfirmationConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> ContextSignal:
        if self.registryEntry.collection != StrategyCollection.CONTEXT.value:
            raise ValueError("Volume Confirmation must be registered as context")
        evidence = self._evidence(context)
        return ContextSignal(
            contextId=self.registryEntry.strategyId,
            signal=Signal.HOLD,
            direction=Direction.FLAT,
            confidence=self._confidence(evidence),
            dataReady=evidence.dataReady,
            explanation=self._explanation(evidence),
            features={
                "relativeVolume": evidence.relativeVolume,
                "breakoutVolumeConfirmation": evidence.breakoutVolumeConfirmation,
                "pullbackVolumeBehavior": evidence.pullbackVolumeBehavior,
                "volumeTrend": evidence.volumeTrend,
                "dataQuality": evidence.dataQuality,
                "maxConfidenceAdjustment": self.config.maxConfidenceAdjustment,
                "contextEffect": evidence.contextEffect,
                "reasonCodes": evidence.reasonCodes,
            },
            evaluatedAt=context.evaluatedAt,
            sessionDate=context.sessionDate,
            configurationHash=context.configurationHash,
        )

    def _evidence(self, context: StrategyEvaluationContext) -> VolumeEvidence:
        feature = context.featureSnapshot.features.get("spy1mRelativeVolume")
        if not feature or feature.quality != FeatureQuality.READY.value:
            return _missing(["volume_confirmation.missing_relative_volume"])
        relative_volume = _number(feature.value)
        candles = _candles(context.featureSnapshot.rawInputs.get("spy1mCandles") or [])
        if relative_volume is None or len(candles) < self.config.lookbackCandles + 1:
            return _missing(["volume_confirmation.insufficient_volume_history"])
        latest = candles[-1]
        prior = candles[-self.config.lookbackCandles - 1 : -1]
        prior_high = max(float(row["high"]) for row in prior)
        prior_low = min(float(row["low"]) for row in prior)
        close = float(latest["close"])
        breakout = (close > prior_high or close < prior_low) and relative_volume >= self.config.breakoutRelativeVolumeThreshold
        baseline = mean(float(row["volume"]) for row in prior)
        latest_ratio = float(latest["volume"]) / baseline if baseline > 0 else 0.0
        candle_range = max(0.0001, float(latest["high"]) - float(latest["low"]))
        body_ratio = abs(float(latest["close"]) - float(latest["open"])) / candle_range
        pullback_behavior = "quiet_pullback" if latest_ratio <= self.config.pullbackQuietVolumeThreshold and body_ratio < 0.45 else "active"
        first_half = mean(float(row["volume"]) for row in prior[: len(prior) // 2])
        second_half = mean(float(row["volume"]) for row in prior[len(prior) // 2 :])
        trend = "rising" if second_half > first_half * 1.1 else "falling" if second_half < first_half * 0.9 else "flat"
        if breakout:
            effect = "confirm_breakout_candidates"
        elif pullback_behavior == "quiet_pullback":
            effect = "confirm_pullback_candidates"
        else:
            effect = "neutral"
        return VolumeEvidence(True, round(relative_volume, 4), breakout, pullback_behavior, trend, 1.0, effect, [f"volume_confirmation.{effect}"])

    def _confidence(self, evidence: VolumeEvidence) -> float:
        if not evidence.dataReady or evidence.relativeVolume is None:
            return 0.0
        return round(max(0.05, min(1.0, evidence.relativeVolume / max(self.config.breakoutRelativeVolumeThreshold * 1.5, 0.01))), 4)

    def _explanation(self, evidence: VolumeEvidence) -> str:
        if not evidence.dataReady:
            return f"HOLD context because volume inputs are unavailable: {', '.join(evidence.reasonCodes)}."
        return f"HOLD context only: Volume Confirmation relative volume {evidence.relativeVolume:.2f}, trend {evidence.volumeTrend}, effect {evidence.contextEffect}."


def _missing(reason_codes: list[str]) -> VolumeEvidence:
    return VolumeEvidence(False, None, False, "unknown", "unknown", 0.0, "neutral", reason_codes)


def _candles(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(raw, key=lambda row: _timestamp(row["timestamp"]))


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None
