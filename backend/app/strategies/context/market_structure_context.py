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


class MarketStructureContextConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "market_structure_context_v1"
    structureLookbackCandles: int = Field(default=20, ge=5, le=120)
    rangeCompressionAtrMultiple: float = Field(default=2.2, gt=0)
    breakBufferAtr: float = Field(default=0.12, ge=0)
    maxConfidenceAdjustment: float = Field(default=0.10, ge=0, le=0.25)

    @property
    def configurationHash(self) -> str:
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class StructureEvidence:
    dataReady: bool
    higherHighsHigherLows: bool
    lowerHighsLowerLows: bool
    rangeStructure: bool
    breakOfStructure: str
    structureQuality: float
    contextEffect: str
    reasonCodes: list[str]


class MarketStructureContext:
    registryEntry = resolve_strategy("market_structure_context")

    def __init__(self, config: MarketStructureContextConfig | None = None) -> None:
        self.config = config or MarketStructureContextConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> ContextSignal:
        if self.registryEntry.collection != StrategyCollection.CONTEXT.value:
            raise ValueError("Market Structure Context must be registered as context")
        evidence = self._evidence(context)
        return ContextSignal(
            contextId=self.registryEntry.strategyId,
            signal=Signal.HOLD,
            direction=Direction.FLAT,
            confidence=round(evidence.structureQuality, 4) if evidence.dataReady else 0.0,
            dataReady=evidence.dataReady,
            explanation=self._explanation(evidence),
            features={
                "higherHighsHigherLows": evidence.higherHighsHigherLows,
                "lowerHighsLowerLows": evidence.lowerHighsLowerLows,
                "rangeStructure": evidence.rangeStructure,
                "breakOfStructure": evidence.breakOfStructure,
                "structureQuality": evidence.structureQuality,
                "maxConfidenceAdjustment": self.config.maxConfidenceAdjustment,
                "contextEffect": evidence.contextEffect,
                "reasonCodes": evidence.reasonCodes,
            },
            evaluatedAt=context.evaluatedAt,
            sessionDate=context.sessionDate,
            configurationHash=context.configurationHash,
        )

    def _evidence(self, context: StrategyEvaluationContext) -> StructureEvidence:
        features = context.featureSnapshot.features
        required = ("spy1mHigherHighHigherLow", "spy1mLowerHighLowerLow", "spy1mAtr14")
        for name in required:
            feature = features.get(name)
            if not feature or feature.quality != FeatureQuality.READY.value:
                return _missing([f"market_structure.missing_or_unready:{name}"])
        candles = _candles(context.featureSnapshot.rawInputs.get("spy1mCandles") or [])
        if len(candles) < self.config.structureLookbackCandles + 1:
            return _missing(["market_structure.insufficient_candles"])
        atr = _number(features["spy1mAtr14"].value) or 0.0
        latest = candles[-1]
        prior = candles[-self.config.structureLookbackCandles - 1 : -1]
        prior_high = max(float(row["high"]) for row in prior)
        prior_low = min(float(row["low"]) for row in prior)
        range_atr = (prior_high - prior_low) / atr if atr > 0 else 999.0
        range_structure = range_atr <= self.config.rangeCompressionAtrMultiple
        close = float(latest["close"])
        buffer = atr * self.config.breakBufferAtr
        if close > prior_high + buffer:
            break_state = "bullish_break"
        elif close < prior_low - buffer:
            break_state = "bearish_break"
        else:
            break_state = "none"
        hh_hl = bool(features["spy1mHigherHighHigherLow"].value)
        lh_ll = bool(features["spy1mLowerHighLowerLow"].value)
        directional_alignment = 1.0 if (hh_hl or lh_ll) else 0.35
        break_score = 1.0 if break_state != "none" else 0.5 if range_structure else 0.35
        quality = max(0.0, min(1.0, (0.55 * directional_alignment) + (0.45 * break_score)))
        if break_state == "bullish_break" or hh_hl:
            effect = "confirm_or_strengthen_long_candidates"
        elif break_state == "bearish_break" or lh_ll:
            effect = "confirm_or_strengthen_short_candidates"
        elif range_structure:
            effect = "reduce_breakout_confidence"
        else:
            effect = "neutral"
        return StructureEvidence(True, hh_hl, lh_ll, range_structure, break_state, round(quality, 4), effect, [f"market_structure.{effect}"])

    def _explanation(self, evidence: StructureEvidence) -> str:
        if not evidence.dataReady:
            return f"HOLD context because market-structure inputs are unavailable: {', '.join(evidence.reasonCodes)}."
        return f"HOLD context only: Market Structure Context {evidence.breakOfStructure}, quality {evidence.structureQuality:.2f}, effect {evidence.contextEffect}."


def _missing(reason_codes: list[str]) -> StructureEvidence:
    return StructureEvidence(False, False, False, False, "unknown", 0.0, "neutral", reason_codes)


def _candles(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(raw, key=lambda row: _timestamp(row["timestamp"]))


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None
