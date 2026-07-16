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


class EconomicEventContextConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "economic_event_context_v1"
    eventWindowMinutes: int = Field(default=30, ge=1, le=240)
    highImportanceRiskCap: float = Field(default=0.35, ge=0, le=1)
    mediumImportanceRiskCap: float = Field(default=0.65, ge=0, le=1)
    lowImportanceRiskCap: float = Field(default=1.0, ge=0, le=1)
    volatilityShockThreshold: float = Field(default=1.8, gt=0)
    spreadShockBasisPoints: float = Field(default=8.0, ge=0)
    maxConfidenceAdjustment: float = Field(default=0.12, ge=0, le=0.25)

    @property
    def configurationHash(self) -> str:
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class EconomicEventEvidence:
    dataReady: bool
    eventImportance: str
    minutesUntilEvent: float | None
    minutesSinceEvent: float | None
    eventState: str
    directionalReaction: str
    volatilityShock: float | None
    spreadShock: float | None
    recommendedRiskCap: float
    contextEffect: str
    reasonCodes: list[str]


class EconomicEventContext:
    registryEntry = resolve_strategy("economic_event_context")

    def __init__(self, config: EconomicEventContextConfig | None = None) -> None:
        self.config = config or EconomicEventContextConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> ContextSignal:
        if self.registryEntry.collection != StrategyCollection.CONTEXT.value:
            raise ValueError("Economic Event Context must be registered as context")
        evidence = self._evidence(context)
        return ContextSignal(
            contextId=self.registryEntry.strategyId,
            signal=Signal.HOLD,
            direction=Direction.FLAT,
            confidence=self._confidence(evidence),
            dataReady=evidence.dataReady,
            explanation=self._explanation(evidence),
            features={
                "eventImportance": evidence.eventImportance,
                "minutesUntilEvent": evidence.minutesUntilEvent,
                "minutesSinceEvent": evidence.minutesSinceEvent,
                "eventState": evidence.eventState,
                "directionalReaction": evidence.directionalReaction,
                "volatilityShock": evidence.volatilityShock,
                "spreadShock": evidence.spreadShock,
                "recommendedRiskCap": evidence.recommendedRiskCap,
                "maxConfidenceAdjustment": self.config.maxConfidenceAdjustment,
                "contextEffect": evidence.contextEffect,
                "reasonCodes": evidence.reasonCodes,
            },
            evaluatedAt=context.evaluatedAt,
            sessionDate=context.sessionDate,
            configurationHash=context.configurationHash,
        )

    def _evidence(self, context: StrategyEvaluationContext) -> EconomicEventEvidence:
        feature = context.featureSnapshot.features.get("economicEventState")
        if not feature or feature.quality != FeatureQuality.READY.value or not isinstance(feature.value, dict):
            return _missing(["economic_event.missing_event_state"])
        event = feature.value
        if not event:
            return _missing(["economic_event.empty_event_state"])

        timestamp = _event_timestamp(event)
        minutes_until = ((timestamp - context.evaluatedAt).total_seconds() / 60) if timestamp else None
        minutes_since = ((context.evaluatedAt - timestamp).total_seconds() / 60) if timestamp else None
        importance = _importance(event)
        event_state = _event_state(event, minutes_until, minutes_since, self.config.eventWindowMinutes)
        risk_cap = self._risk_cap(importance, event_state)
        candles = _candles(context.featureSnapshot.rawInputs.get("spy1mCandles") or [])
        reaction = _observable_reaction(candles, timestamp, context.evaluatedAt) if timestamp else "none_observable"
        volatility_shock = _volatility_shock(candles)
        spread_bps = _number(context.featureSnapshot.features.get("spreadBasisPoints").value if context.featureSnapshot.features.get("spreadBasisPoints") else None)
        spread_shock = None if spread_bps is None else spread_bps / max(self.config.spreadShockBasisPoints, 0.01)
        shock = (volatility_shock is not None and volatility_shock >= self.config.volatilityShockThreshold) or (spread_shock is not None and spread_shock >= 1.0)
        effect = "reduce_risk" if risk_cap < 1.0 or shock else "neutral"
        return EconomicEventEvidence(
            dataReady=True,
            eventImportance=importance,
            minutesUntilEvent=round(minutes_until, 2) if minutes_until is not None else None,
            minutesSinceEvent=round(minutes_since, 2) if minutes_since is not None else None,
            eventState=event_state,
            directionalReaction=reaction,
            volatilityShock=round(volatility_shock, 4) if volatility_shock is not None else None,
            spreadShock=round(spread_shock, 4) if spread_shock is not None else None,
            recommendedRiskCap=risk_cap,
            contextEffect=effect,
            reasonCodes=[f"economic_event.{effect}", "economic_event.candidate_side_not_replaced"],
        )

    def _risk_cap(self, importance: str, event_state: str) -> float:
        if event_state == "none":
            return 1.0
        if importance == "high":
            return self.config.highImportanceRiskCap
        if importance == "medium":
            return self.config.mediumImportanceRiskCap
        return self.config.lowImportanceRiskCap

    def _confidence(self, evidence: EconomicEventEvidence) -> float:
        if not evidence.dataReady:
            return 0.0
        risk_score = 1.0 - evidence.recommendedRiskCap
        shock_score = max((evidence.volatilityShock or 0) / max(self.config.volatilityShockThreshold * 2, 0.01), (evidence.spreadShock or 0) / 2)
        return round(max(0.05, min(1.0, (0.65 * risk_score) + (0.35 * min(1.0, shock_score)))), 4)

    def _explanation(self, evidence: EconomicEventEvidence) -> str:
        if not evidence.dataReady:
            return f"HOLD context because economic-event inputs are unavailable: {', '.join(evidence.reasonCodes)}."
        return (
            "HOLD context only: Economic Event Context "
            f"{evidence.eventImportance} {evidence.eventState}, reaction {evidence.directionalReaction}, "
            f"risk cap {evidence.recommendedRiskCap:.2f}; candidate side is not replaced."
        )


def _missing(reason_codes: list[str]) -> EconomicEventEvidence:
    return EconomicEventEvidence(False, "unknown", None, None, "missing", "none_observable", None, None, 1.0, "neutral", reason_codes)


def _importance(event: dict[str, Any]) -> str:
    raw = str(event.get("importance") or event.get("impact") or event.get("severity") or "low").lower()
    return raw if raw in {"low", "medium", "high"} else "low"


def _event_state(event: dict[str, Any], minutes_until: float | None, minutes_since: float | None, window: int) -> str:
    if event.get("active") is True:
        return "active"
    if minutes_until is not None and 0 <= minutes_until <= window:
        return "upcoming"
    if minutes_since is not None and 0 <= minutes_since <= window:
        return "recent"
    if str(event.get("category") or event.get("state") or "").lower() in {"none", "no_event"}:
        return "none"
    return "outside_window"


def _event_timestamp(event: dict[str, Any]) -> datetime | None:
    for key in ("eventTimestamp", "eventTime", "timestamp"):
        if event.get(key):
            return _timestamp(event[key])
    return None


def _observable_reaction(candles: list[dict[str, Any]], event_at: datetime, evaluated_at: datetime) -> str:
    before = _latest_at_or_before(candles, event_at)
    latest = _latest_at_or_before(candles, evaluated_at)
    if not before or not latest:
        return "none_observable"
    change = (float(latest["close"]) - float(before["close"])) / float(before["close"])
    if change > 0.001:
        return "up"
    if change < -0.001:
        return "down"
    return "flat"


def _volatility_shock(candles: list[dict[str, Any]]) -> float | None:
    if len(candles) < 12:
        return None
    ranges = [float(row["high"]) - float(row["low"]) for row in candles[-11:-1]]
    baseline = mean(ranges)
    latest = float(candles[-1]["high"]) - float(candles[-1]["low"])
    return latest / baseline if baseline > 0 else None


def _candles(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(raw, key=lambda row: _timestamp(row["timestamp"]))


def _latest_at_or_before(candles: list[dict[str, Any]], timestamp: datetime) -> dict[str, Any] | None:
    candidates = [row for row in candles if _timestamp(row["timestamp"]) <= timestamp]
    return max(candidates, key=lambda row: _timestamp(row["timestamp"])) if candidates else None


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None
