from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import mean, pstdev
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.domain.feature_engine import FeatureQuality
from backend.app.domain.models import ContextSignal, Direction, Signal
from backend.app.strategies.base import StrategyEvaluationContext
from backend.app.strategies.registry import StrategyCollection, resolve_strategy


class RelativeStrengthQqqIwmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "relative_strength_qqq_iwm_v1"
    horizonsMinutes: tuple[int, ...] = (1, 5, 15)
    primaryHorizonMinutes: int = 5
    rollingScoreHorizonMinutes: int = 5
    rollingLookbackPeriods: int = Field(default=20, ge=5, le=120)
    positiveThreshold: float = Field(default=0.001, ge=0)
    negativeThreshold: float = Field(default=-0.001, le=0)
    strongConflictThreshold: float = Field(default=0.006, gt=0)
    maxAlignmentLagSeconds: int = Field(default=0, ge=0, le=300)

    @model_validator(mode="after")
    def horizons_must_include_primary(self) -> RelativeStrengthQqqIwmConfig:
        if self.primaryHorizonMinutes not in self.horizonsMinutes:
            raise ValueError("primaryHorizonMinutes must be listed in horizonsMinutes")
        if self.rollingScoreHorizonMinutes not in self.horizonsMinutes:
            raise ValueError("rollingScoreHorizonMinutes must be listed in horizonsMinutes")
        if any(horizon <= 0 for horizon in self.horizonsMinutes):
            raise ValueError("horizonsMinutes must contain positive values")
        return self

    @property
    def configurationHash(self) -> str:
        payload = self.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class RelativeStrengthEvidence:
    dataReady: bool
    relativeReturns: dict[int, float]
    spyReturns: dict[int, float]
    qqqReturns: dict[int, float]
    iwmReturns: dict[int, float]
    normalizedScore: float | None
    primaryRelativeReturn: float | None
    contextEffect: str
    reasonCodes: list[str]
    inputTimestamps: dict[str, datetime]


class RelativeStrengthQqqIwmContext:
    registryEntry = resolve_strategy("relative_strength_qqq_iwm")

    def __init__(self, config: RelativeStrengthQqqIwmConfig | None = None) -> None:
        self.config = config or RelativeStrengthQqqIwmConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> ContextSignal:
        if self.registryEntry.collection != StrategyCollection.CONTEXT.value:
            raise ValueError("Relative Strength vs QQQ/IWM must be registered as context")

        evidence = self._evidence(context)
        confidence = self._confidence(evidence)
        return ContextSignal(
            contextId=self.registryEntry.strategyId,
            signal=Signal.HOLD,
            direction=Direction.FLAT,
            confidence=confidence,
            dataReady=evidence.dataReady,
            explanation=self._explanation(evidence),
            features={
                "relativeReturns": {str(key): value for key, value in evidence.relativeReturns.items()},
                "spyReturns": {str(key): value for key, value in evidence.spyReturns.items()},
                "qqqReturns": {str(key): value for key, value in evidence.qqqReturns.items()},
                "iwmReturns": {str(key): value for key, value in evidence.iwmReturns.items()},
                "normalizedRelativeStrengthScore": evidence.normalizedScore,
                "primaryRelativeReturn": evidence.primaryRelativeReturn,
                "contextEffect": evidence.contextEffect,
                "reasonCodes": evidence.reasonCodes,
            },
            evaluatedAt=context.evaluatedAt,
            sessionDate=context.sessionDate,
            configurationHash=context.configurationHash,
        )

    def _evidence(self, context: StrategyEvaluationContext) -> RelativeStrengthEvidence:
        unavailable = self._unavailable_reason_codes(context)
        if unavailable:
            return _empty_evidence(unavailable)

        raw_inputs = context.featureSnapshot.rawInputs
        spy = _candles(raw_inputs.get("spy1mCandles") or [])
        qqq = _candles(raw_inputs.get("qqqAlignedCandles") or [])
        iwm = _candles(raw_inputs.get("iwmAlignedCandles") or [])
        if not spy or not qqq or not iwm:
            return _empty_evidence(["relative_strength.missing_aligned_auxiliary_history"])

        anchor = context.featureSnapshot.anchorTimestamp or context.evaluatedAt
        latest = {
            "SPY": _aligned_candle(spy, anchor, self.config.maxAlignmentLagSeconds),
            "QQQ": _aligned_candle(qqq, anchor, self.config.maxAlignmentLagSeconds),
            "IWM": _aligned_candle(iwm, anchor, self.config.maxAlignmentLagSeconds),
        }
        if not all(latest.values()):
            return _empty_evidence(["relative_strength.latest_timestamp_not_aligned"])

        relative_returns: dict[int, float] = {}
        spy_returns: dict[int, float] = {}
        qqq_returns: dict[int, float] = {}
        iwm_returns: dict[int, float] = {}
        for horizon in sorted(set(self.config.horizonsMinutes)):
            target = anchor - timedelta(minutes=horizon)
            spy_return = _return_between(spy, target, anchor, self.config.maxAlignmentLagSeconds)
            qqq_return = _return_between(qqq, target, anchor, self.config.maxAlignmentLagSeconds)
            iwm_return = _return_between(iwm, target, anchor, self.config.maxAlignmentLagSeconds)
            if spy_return is None or qqq_return is None or iwm_return is None:
                return _empty_evidence([f"relative_strength.missing_horizon:{horizon}"])
            spy_returns[horizon] = spy_return
            qqq_returns[horizon] = qqq_return
            iwm_returns[horizon] = iwm_return
            relative_returns[horizon] = spy_return - (0.5 * qqq_return) - (0.5 * iwm_return)

        primary = relative_returns.get(self.config.primaryHorizonMinutes)
        normalized = _rolling_normalized_score(
            spy,
            qqq,
            iwm,
            anchor,
            horizon_minutes=self.config.rollingScoreHorizonMinutes,
            lookback_periods=self.config.rollingLookbackPeriods,
            max_lag_seconds=self.config.maxAlignmentLagSeconds,
        )
        effect = self._context_effect(primary, normalized)
        timestamps = {
            "spy": latest["SPY"].timestamp,  # type: ignore[union-attr]
            "qqq": latest["QQQ"].timestamp,  # type: ignore[union-attr]
            "iwm": latest["IWM"].timestamp,  # type: ignore[union-attr]
        }
        return RelativeStrengthEvidence(
            dataReady=True,
            relativeReturns=relative_returns,
            spyReturns=spy_returns,
            qqqReturns=qqq_returns,
            iwmReturns=iwm_returns,
            normalizedScore=normalized,
            primaryRelativeReturn=primary,
            contextEffect=effect,
            reasonCodes=[f"relative_strength.{effect}"],
            inputTimestamps=timestamps,
        )

    def _unavailable_reason_codes(self, context: StrategyEvaluationContext) -> list[str]:
        if not context.featureSnapshot.dataReady:
            reason_codes = [code for code in context.featureSnapshot.reasonCodes if code.startswith(("qqq_", "iwm_"))]
            return ["relative_strength.feature_snapshot_not_ready", *reason_codes]
        for name in ("qqqClose", "iwmClose", "relativeStrengthQqq", "relativeStrengthIwm"):
            feature = context.featureSnapshot.features.get(name)
            if not feature or feature.quality != FeatureQuality.READY.value:
                return [f"relative_strength.missing_or_unready:{name}"]
        return []

    def _context_effect(self, primary: float | None, normalized: float | None) -> str:
        if primary is None:
            return "neutral"
        normalized_bias = 0.0 if normalized is None else normalized - 0.5
        if primary >= self.config.strongConflictThreshold:
            return "veto_short_candidates"
        if primary <= -self.config.strongConflictThreshold:
            return "veto_long_candidates"
        if primary >= self.config.positiveThreshold:
            return "confirm_or_strengthen_long_candidates"
        if primary <= self.config.negativeThreshold:
            return "confirm_or_strengthen_short_candidates"
        if normalized_bias >= 0.18:
            return "confirm_or_strengthen_long_candidates"
        if normalized_bias <= -0.18:
            return "confirm_or_strengthen_short_candidates"
        return "neutral"

    def _confidence(self, evidence: RelativeStrengthEvidence) -> float:
        if not evidence.dataReady or evidence.primaryRelativeReturn is None:
            return 0.0
        magnitude_score = min(1.0, abs(evidence.primaryRelativeReturn) / max(self.config.strongConflictThreshold, 0.000001))
        normalized_score = 0.5 if evidence.normalizedScore is None else abs(evidence.normalizedScore - 0.5) * 2
        return round(max(0.05, min(1.0, (0.7 * magnitude_score) + (0.3 * normalized_score))), 4)

    def _explanation(self, evidence: RelativeStrengthEvidence) -> str:
        if not evidence.dataReady:
            return f"HOLD context because relative strength inputs are unavailable: {', '.join(evidence.reasonCodes)}."
        primary = evidence.primaryRelativeReturn or 0.0
        score = "unavailable" if evidence.normalizedScore is None else f"{evidence.normalizedScore:.2f}"
        return (
            "HOLD context only: Relative Strength vs QQQ/IWM "
            f"primary return {primary:.4f}, normalized score {score}, effect {evidence.contextEffect}."
        )


def _empty_evidence(reason_codes: list[str]) -> RelativeStrengthEvidence:
    return RelativeStrengthEvidence(
        dataReady=False,
        relativeReturns={},
        spyReturns={},
        qqqReturns={},
        iwmReturns={},
        normalizedScore=None,
        primaryRelativeReturn=None,
        contextEffect="neutral",
        reasonCodes=reason_codes,
        inputTimestamps={},
    )


def _candles(raw: list[dict[str, Any]]) -> list[Any]:
    return sorted((_Candle.from_raw(row) for row in raw), key=lambda candle: candle.timestamp)


@dataclass(frozen=True)
class _Candle:
    timestamp: datetime
    close: float

    @classmethod
    def from_raw(cls, row: dict[str, Any]) -> _Candle:
        return cls(timestamp=_timestamp(row["timestamp"]), close=float(row["close"]))


def _return_between(candles: list[_Candle], start: datetime, end: datetime, max_lag_seconds: int) -> float | None:
    start_candle = _aligned_candle(candles, start, max_lag_seconds)
    end_candle = _aligned_candle(candles, end, max_lag_seconds)
    if not start_candle or not end_candle or start_candle.close <= 0:
        return None
    return (end_candle.close - start_candle.close) / start_candle.close


def _aligned_candle(candles: list[_Candle], timestamp: datetime, max_lag_seconds: int) -> _Candle | None:
    candidates = [candle for candle in candles if candle.timestamp <= timestamp]
    if not candidates:
        return None
    latest = max(candidates, key=lambda candle: candle.timestamp)
    age = abs((timestamp - latest.timestamp).total_seconds())
    if age > max_lag_seconds:
        return None
    return latest


def _rolling_normalized_score(
    spy: list[_Candle],
    qqq: list[_Candle],
    iwm: list[_Candle],
    anchor: datetime,
    *,
    horizon_minutes: int,
    lookback_periods: int,
    max_lag_seconds: int,
) -> float | None:
    values: list[float] = []
    for offset in range(lookback_periods, -1, -1):
        end = anchor - timedelta(minutes=offset)
        start = end - timedelta(minutes=horizon_minutes)
        spy_return = _return_between(spy, start, end, max_lag_seconds)
        qqq_return = _return_between(qqq, start, end, max_lag_seconds)
        iwm_return = _return_between(iwm, start, end, max_lag_seconds)
        if spy_return is None or qqq_return is None or iwm_return is None:
            continue
        values.append(spy_return - (0.5 * qqq_return) - (0.5 * iwm_return))
    if len(values) < max(5, lookback_periods // 2):
        return None
    current = values[-1]
    baseline = values[:-1]
    deviation = pstdev(baseline)
    if deviation <= 0:
        return 0.5
    z_score = (current - mean(baseline)) / deviation
    return round(max(0.0, min(1.0, 0.5 + (z_score / 6))), 4)


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
