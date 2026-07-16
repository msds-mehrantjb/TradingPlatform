from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain.feature_engine import FeatureQuality
from backend.app.domain.models import ContextSignal, Direction, Signal
from backend.app.strategies.base import StrategyEvaluationContext
from backend.app.strategies.registry import StrategyCollection, resolve_strategy


class VwapPositionContextConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "vwap_position_context_v1"
    reclaimLookbackCandles: int = Field(default=3, ge=2, le=10)
    rejectionAtrBuffer: float = Field(default=0.05, ge=0)
    maxConfidenceAdjustment: float = Field(default=0.10, ge=0, le=0.25)

    @property
    def configurationHash(self) -> str:
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class VwapPositionEvidence:
    dataReady: bool
    pricePosition: str
    distanceFromVwapAtr: float | None
    vwapSlope: float | None
    reclaimRejectionState: str
    contextEffect: str
    reasonCodes: list[str]


class VwapPositionContext:
    registryEntry = resolve_strategy("vwap_position_context")

    def __init__(self, config: VwapPositionContextConfig | None = None) -> None:
        self.config = config or VwapPositionContextConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> ContextSignal:
        if self.registryEntry.collection != StrategyCollection.CONTEXT.value:
            raise ValueError("VWAP Position Context must be registered as context")
        evidence = self._evidence(context)
        return ContextSignal(
            contextId=self.registryEntry.strategyId,
            signal=Signal.HOLD,
            direction=Direction.FLAT,
            confidence=self._confidence(evidence),
            dataReady=evidence.dataReady,
            explanation=self._explanation(evidence),
            features={
                "pricePosition": evidence.pricePosition,
                "distanceFromVwapAtr": evidence.distanceFromVwapAtr,
                "vwapSlope": evidence.vwapSlope,
                "reclaimRejectionState": evidence.reclaimRejectionState,
                "maxConfidenceAdjustment": self.config.maxConfidenceAdjustment,
                "contextEffect": evidence.contextEffect,
                "reasonCodes": evidence.reasonCodes,
            },
            evaluatedAt=context.evaluatedAt,
            sessionDate=context.sessionDate,
            configurationHash=context.configurationHash,
        )

    def _evidence(self, context: StrategyEvaluationContext) -> VwapPositionEvidence:
        features = context.featureSnapshot.features
        for name in ("sessionVwap", "sessionVwapSlope", "distanceFromVwapAtr", "spy1mAtr14"):
            feature = features.get(name)
            if not feature or feature.quality != FeatureQuality.READY.value:
                return _missing([f"vwap_position.missing_or_unready:{name}"])
        distance = _number(features["distanceFromVwapAtr"].value)
        slope = _number(features["sessionVwapSlope"].value)
        session_vwap = _number(features["sessionVwap"].value)
        atr = _number(features["spy1mAtr14"].value)
        candles = _candles(context.featureSnapshot.rawInputs.get("spy1mCandles") or [])
        if None in {distance, slope, session_vwap, atr} or len(candles) < self.config.reclaimLookbackCandles:
            return _missing(["vwap_position.insufficient_inputs"])
        assert distance is not None
        assert slope is not None
        assert session_vwap is not None
        assert atr is not None
        latest = candles[-1]
        position = "above_vwap" if distance > 0 else "below_vwap" if distance < 0 else "at_vwap"
        recent = candles[-self.config.reclaimLookbackCandles :]
        buffer = atr * self.config.rejectionAtrBuffer
        crossed_up = any(float(row["close"]) < session_vwap - buffer for row in recent[:-1]) and float(latest["close"]) > session_vwap + buffer
        crossed_down = any(float(row["close"]) > session_vwap + buffer for row in recent[:-1]) and float(latest["close"]) < session_vwap - buffer
        if crossed_up:
            state = "bullish_reclaim"
            effect = "confirm_or_strengthen_long_candidates"
        elif crossed_down:
            state = "bearish_rejection"
            effect = "confirm_or_strengthen_short_candidates"
        elif position == "above_vwap" and slope > 0:
            state = "above_rising_vwap"
            effect = "confirm_or_strengthen_long_candidates"
        elif position == "below_vwap" and slope < 0:
            state = "below_falling_vwap"
            effect = "confirm_or_strengthen_short_candidates"
        else:
            state = "neutral"
            effect = "neutral"
        return VwapPositionEvidence(True, position, round(distance, 4), round(slope, 6), state, effect, [f"vwap_position.{effect}"])

    def _confidence(self, evidence: VwapPositionEvidence) -> float:
        if not evidence.dataReady or evidence.distanceFromVwapAtr is None:
            return 0.0
        return round(max(0.05, min(1.0, abs(evidence.distanceFromVwapAtr) / 2.0)), 4)

    def _explanation(self, evidence: VwapPositionEvidence) -> str:
        if not evidence.dataReady:
            return f"HOLD context because VWAP-position inputs are unavailable: {', '.join(evidence.reasonCodes)}."
        return f"HOLD context only: VWAP Position Context {evidence.pricePosition}, state {evidence.reclaimRejectionState}, effect {evidence.contextEffect}."


def _missing(reason_codes: list[str]) -> VwapPositionEvidence:
    return VwapPositionEvidence(False, "unknown", None, None, "unknown", "neutral", reason_codes)


def _candles(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(raw, key=lambda row: _timestamp(row["timestamp"]))


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None
