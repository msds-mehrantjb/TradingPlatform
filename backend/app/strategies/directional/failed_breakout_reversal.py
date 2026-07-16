from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.domain.models import Signal, StrategySignal
from backend.app.strategies.base import (
    StrategyEvaluationContext,
    hold_signal,
    required_features_ready,
    strategy_signal,
    unavailable_signal,
)
from backend.app.strategies.registry import resolve_strategy


ReferenceSide = Literal["HIGH", "LOW"]


class FailedBreakoutReversalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "failed_breakout_reversal_v1"
    failureLookbackCandles: int = Field(default=3, ge=1, le=20)
    swingLookbackCandles: int = Field(default=12, ge=4, le=60)
    intradayRangeLookbackCandles: int = Field(default=30, ge=10, le=120)
    minPenetrationDollars: float = Field(default=0.01, ge=0)
    penetrationAtrMultiplier: float = Field(default=0.08, ge=0)
    spreadBufferMultiplier: float = Field(default=1.0, ge=0)
    closeBackInsideAtrMultiplier: float = Field(default=0.03, ge=0)
    minReversalBodyToRange: float = Field(default=0.35, ge=0, le=1)
    maxSpreadBasisPoints: float = Field(default=12.0, gt=0)
    minLatestVolume: float = Field(default=50_000, ge=0)
    requireNextCandleConfirmation: bool = False

    @model_validator(mode="after")
    def range_lookback_must_cover_swing(self) -> FailedBreakoutReversalConfig:
        if self.intradayRangeLookbackCandles < self.swingLookbackCandles:
            raise ValueError("intradayRangeLookbackCandles must be at least swingLookbackCandles")
        return self

    @property
    def configurationHash(self) -> str:
        payload = self.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class ReferenceLevel:
    name: str
    side: ReferenceSide
    price: float
    sourceTimestamp: datetime | None = None


@dataclass(frozen=True)
class FailedBreakoutEvidence:
    signal: Signal
    setupId: str | None
    reference: ReferenceLevel | None
    penetrated: bool
    closedBackInside: bool
    confirmation: bool
    liquidityOk: bool
    penetrationDistance: float
    closeBackDistance: float
    bufferDollars: float
    failedAt: datetime | None
    structuralInvalidationPrice: float | None


class FailedBreakoutReversalStrategy:
    registryEntry = resolve_strategy("failed_breakout_reversal")

    def __init__(self, config: FailedBreakoutReversalConfig | None = None) -> None:
        self.config = config or FailedBreakoutReversalConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> StrategySignal:
        required_features = self.required_feature_names()
        if not context.featureSnapshot.dataReady:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Feature snapshot is not ready for failed breakout reversal.",
            )
        if not required_features_ready(context.featureSnapshot, required_features):
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Required feature measurements are unavailable for failed breakout reversal.",
            )

        candles = _session_candles(context.featureSnapshot.rawInputs.get("spy1mCandles") or [], context)
        if len(candles) < self.config.intradayRangeLookbackCandles + 1:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Insufficient completed 1-minute candles for failed breakout reversal.",
            )

        references = self._reference_levels(context, candles)
        if not references:
            return hold_signal(
                context,
                confidence=0.05,
                setupDetected=False,
                regimeFit=0.0,
                reliability=0.2,
                reasonCodes=["failed_breakout.no_reference_levels"],
                explanation="HOLD because no usable failed-breakout reference levels are available.",
                featureNames=required_features,
            )

        evidence = self._best_evidence(context, candles, references)
        if evidence.signal in {Signal.BUY, Signal.SELL}:
            return strategy_signal(
                context,
                signal=evidence.signal,
                confidence=self._confidence(evidence),
                eligible=True,
                setupDetected=True,
                regimeFit=self._regime_fit(evidence),
                reliability=self._reliability(evidence),
                reasonCodes=[
                    "failed_breakout.reversal_confirmed",
                    f"direction:{evidence.signal.value.lower()}",
                    f"level:{evidence.reference.name if evidence.reference else 'unknown'}",
                    f"setup_id:{evidence.setupId}",
                ],
                explanation=self._explanation(evidence),
                featureNames=required_features,
                structuralInvalidationPrice=evidence.structuralInvalidationPrice,
            )

        return hold_signal(
            context,
            confidence=self._hold_confidence(evidence),
            setupDetected=evidence.penetrated,
            regimeFit=0.3,
            reliability=self._reliability(evidence),
            reasonCodes=self._hold_reason_codes(evidence),
            explanation=self._hold_explanation(evidence),
            featureNames=required_features,
            structuralInvalidationPrice=evidence.structuralInvalidationPrice,
        )

    def required_feature_names(self) -> tuple[str, ...]:
        return (
            "spy1mAtr14",
            "spy1mRollingHigh20",
            "spy1mRollingLow20",
            "spreadDollars",
            "spreadBasisPoints",
        )

    def _reference_levels(self, context: StrategyEvaluationContext, candles: list[dict[str, Any]]) -> list[ReferenceLevel]:
        raw_inputs = context.featureSnapshot.rawInputs
        features = context.featureSnapshot.features
        levels: list[ReferenceLevel] = []

        opening_range = raw_inputs.get("openingRange") or {}
        if opening_range.get("high"):
            levels.append(ReferenceLevel("opening_range_high", "HIGH", float(opening_range["high"]), _optional_timestamp(opening_range.get("endTimestamp"))))
        if opening_range.get("low"):
            levels.append(ReferenceLevel("opening_range_low", "LOW", float(opening_range["low"]), _optional_timestamp(opening_range.get("endTimestamp"))))

        prior = raw_inputs.get("priorDayOHLC") or {}
        if prior.get("high"):
            levels.append(ReferenceLevel("prior_day_high", "HIGH", float(prior["high"])))
        if prior.get("low"):
            levels.append(ReferenceLevel("prior_day_low", "LOW", float(prior["low"])))

        premarket = raw_inputs.get("premarket") or {}
        if premarket.get("high"):
            levels.append(ReferenceLevel("premarket_high", "HIGH", float(premarket["high"]), _optional_timestamp(premarket.get("sourceTimestamp"))))
        if premarket.get("low"):
            levels.append(ReferenceLevel("premarket_low", "LOW", float(premarket["low"]), _optional_timestamp(premarket.get("sourceTimestamp"))))

        swing_high, swing_low = _recent_swing_levels(candles, self.config.swingLookbackCandles)
        if swing_high is not None:
            levels.append(ReferenceLevel("recent_swing_high", "HIGH", swing_high, _timestamp(candles[-2])))
        if swing_low is not None:
            levels.append(ReferenceLevel("recent_swing_low", "LOW", swing_low, _timestamp(candles[-2])))

        range_sample = candles[-self.config.intradayRangeLookbackCandles - 1 : -1]
        levels.append(ReferenceLevel("intraday_range_high", "HIGH", max(float(candle["high"]) for candle in range_sample), _timestamp(range_sample[-1])))
        levels.append(ReferenceLevel("intraday_range_low", "LOW", min(float(candle["low"]) for candle in range_sample), _timestamp(range_sample[-1])))

        rolling_high = _number(features["spy1mRollingHigh20"].value)
        rolling_low = _number(features["spy1mRollingLow20"].value)
        if rolling_high is not None:
            levels.append(ReferenceLevel("rolling_high_20", "HIGH", rolling_high, features["spy1mRollingHigh20"].sourceTimestamp))
        if rolling_low is not None:
            levels.append(ReferenceLevel("rolling_low_20", "LOW", rolling_low, features["spy1mRollingLow20"].sourceTimestamp))

        return _dedupe_levels(levels)

    def _best_evidence(
        self,
        context: StrategyEvaluationContext,
        candles: list[dict[str, Any]],
        references: list[ReferenceLevel],
    ) -> FailedBreakoutEvidence:
        evidences = [self._evidence_for_reference(context, candles, reference) for reference in references]
        signals = [evidence for evidence in evidences if evidence.signal in {Signal.BUY, Signal.SELL}]
        if signals:
            return max(signals, key=lambda evidence: self._confidence(evidence))
        return max(evidences, key=lambda evidence: (evidence.penetrated, evidence.closedBackInside, evidence.penetrationDistance))

    def _evidence_for_reference(
        self,
        context: StrategyEvaluationContext,
        candles: list[dict[str, Any]],
        reference: ReferenceLevel,
    ) -> FailedBreakoutEvidence:
        features = context.featureSnapshot.features
        atr = _number(features["spy1mAtr14"].value) or 0.0
        spread = _number(features["spreadDollars"].value) or 0.0
        spread_bps = _number(features["spreadBasisPoints"].value) or 999.0
        buffer_dollars = max(
            self.config.minPenetrationDollars,
            atr * self.config.penetrationAtrMultiplier,
            spread * self.config.spreadBufferMultiplier,
        )
        close_inside_buffer = atr * self.config.closeBackInsideAtrMultiplier
        latest = candles[-1]
        recent = candles[-self.config.failureLookbackCandles :]
        penetrations = [
            candle
            for candle in recent
            if _penetrates(candle, reference, buffer_dollars)
        ]
        penetrated = bool(penetrations)
        latest_close = float(latest["close"])
        if reference.side == "HIGH":
            closed_back_inside = latest_close <= reference.price - close_inside_buffer
            penetration_distance = max((float(candle["high"]) - reference.price for candle in penetrations), default=0.0)
            close_back_distance = reference.price - latest_close
            signal = Signal.SELL
            structural_invalidation = max(float(candle["high"]) for candle in recent) if penetrated else reference.price
        else:
            closed_back_inside = latest_close >= reference.price + close_inside_buffer
            penetration_distance = max((reference.price - float(candle["low"]) for candle in penetrations), default=0.0)
            close_back_distance = latest_close - reference.price
            signal = Signal.BUY
            structural_invalidation = min(float(candle["low"]) for candle in recent) if penetrated else reference.price

        confirmation = _reversal_confirmation(candles, reference, min_body_to_range=self.config.minReversalBodyToRange)
        liquidity_ok = spread_bps <= self.config.maxSpreadBasisPoints and float(latest["volume"]) >= self.config.minLatestVolume
        if self.config.requireNextCandleConfirmation:
            confirmation_ok = confirmation and penetrations and _timestamp(latest) > _timestamp(penetrations[-1])
        else:
            confirmation_ok = confirmation
        should_signal = penetrated and closed_back_inside and confirmation_ok and liquidity_ok
        failed_at = _timestamp(penetrations[-1]) if penetrations else None
        setup_id = self._setup_id(context, reference, signal, failed_at or _timestamp(latest)) if penetrated else None

        return FailedBreakoutEvidence(
            signal=signal if should_signal else Signal.HOLD,
            setupId=setup_id,
            reference=reference,
            penetrated=penetrated,
            closedBackInside=closed_back_inside,
            confirmation=confirmation,
            liquidityOk=liquidity_ok,
            penetrationDistance=penetration_distance,
            closeBackDistance=close_back_distance,
            bufferDollars=buffer_dollars,
            failedAt=failed_at,
            structuralInvalidationPrice=round(structural_invalidation, 4),
        )

    def _setup_id(
        self,
        context: StrategyEvaluationContext,
        reference: ReferenceLevel,
        signal: Signal,
        failed_at: datetime,
    ) -> str:
        payload = {
            "strategy": self.registryEntry.strategyId,
            "sessionDate": context.sessionDate.isoformat(),
            "reference": reference.name,
            "level": round(reference.price, 4),
            "signal": signal.value,
            "failedAt": failed_at.isoformat().replace("+00:00", "Z"),
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    def _confidence(self, evidence: FailedBreakoutEvidence) -> float:
        penetration_score = min(1.0, evidence.penetrationDistance / max(evidence.bufferDollars * 3, 0.01))
        close_back_score = min(1.0, evidence.closeBackDistance / max(evidence.bufferDollars * 3, 0.01))
        confirmation_score = 1.0 if evidence.confirmation else 0.4
        liquidity_score = 1.0 if evidence.liquidityOk else 0.0
        confidence = (0.3 * penetration_score) + (0.3 * close_back_score) + (0.25 * confirmation_score) + (0.15 * liquidity_score)
        return round(max(0.0, min(1.0, confidence)), 4)

    def _hold_confidence(self, evidence: FailedBreakoutEvidence) -> float:
        partials = [evidence.penetrated, evidence.closedBackInside, evidence.confirmation, evidence.liquidityOk]
        return round(min(0.44, max(0.05, sum(1 for item in partials if item) / len(partials) * 0.44)), 4)

    def _regime_fit(self, evidence: FailedBreakoutEvidence) -> float:
        failure_fit = min(1.0, (evidence.penetrationDistance + evidence.closeBackDistance) / max(evidence.bufferDollars * 4, 0.01))
        confirmation_fit = 1.0 if evidence.confirmation else 0.5
        return round(max(0.0, min(1.0, (0.65 * failure_fit) + (0.35 * confirmation_fit))), 4)

    def _reliability(self, evidence: FailedBreakoutEvidence) -> float:
        checks = [evidence.penetrated, evidence.closedBackInside, evidence.confirmation, evidence.liquidityOk]
        return round(max(0.0, min(1.0, sum(1 for item in checks if item) / len(checks))), 4)

    def _explanation(self, evidence: FailedBreakoutEvidence) -> str:
        assert evidence.reference is not None
        return (
            f"{evidence.signal.value} failed breakout reversal: price penetrated {evidence.reference.name} "
            f"at {evidence.reference.price:.2f} and closed back inside by {evidence.closeBackDistance:.2f}."
        )

    def _hold_reason_codes(self, evidence: FailedBreakoutEvidence) -> list[str]:
        if not evidence.penetrated:
            return ["failed_breakout.no_level_penetration"]
        if not evidence.closedBackInside:
            return ["failed_breakout.breakout_holding_outside"]
        if not evidence.confirmation:
            return ["failed_breakout.no_reversal_confirmation"]
        if not evidence.liquidityOk:
            return ["failed_breakout.liquidity_or_spread_failed"]
        return ["failed_breakout.weak_or_conflicting_evidence"]

    def _hold_explanation(self, evidence: FailedBreakoutEvidence) -> str:
        reason = self._hold_reason_codes(evidence)[0].removeprefix("failed_breakout.").replace("_", " ")
        return f"HOLD because failed-breakout reversal evidence is incomplete: {reason}."


def _session_candles(raw_candles: list[dict[str, Any]], context: StrategyEvaluationContext) -> list[dict[str, Any]]:
    completed = []
    for candle in raw_candles:
        timestamp = _timestamp(candle)
        if timestamp > context.evaluatedAt:
            continue
        if _new_york_datetime(timestamp).date() != context.sessionDate:
            continue
        completed.append(candle)
    return sorted(completed, key=lambda candle: _timestamp(candle))


def _penetrates(candle: dict[str, Any], reference: ReferenceLevel, buffer_dollars: float) -> bool:
    if reference.side == "HIGH":
        return float(candle["high"]) >= reference.price + buffer_dollars
    return float(candle["low"]) <= reference.price - buffer_dollars


def _reversal_confirmation(candles: list[dict[str, Any]], reference: ReferenceLevel, *, min_body_to_range: float) -> bool:
    latest = candles[-1]
    open_price = float(latest["open"])
    high = float(latest["high"])
    low = float(latest["low"])
    close = float(latest["close"])
    candle_range = high - low
    if candle_range <= 0:
        return False
    body_ratio = abs(close - open_price) / candle_range
    if reference.side == "HIGH":
        return close < open_price and close <= reference.price and body_ratio >= min_body_to_range
    return close > open_price and close >= reference.price and body_ratio >= min_body_to_range


def _recent_swing_levels(candles: list[dict[str, Any]], lookback: int) -> tuple[float | None, float | None]:
    if len(candles) <= lookback + 1:
        return None, None
    sample = candles[-lookback - 1 : -1]
    return max(float(candle["high"]) for candle in sample), min(float(candle["low"]) for candle in sample)


def _dedupe_levels(levels: list[ReferenceLevel]) -> list[ReferenceLevel]:
    result: list[ReferenceLevel] = []
    seen: set[tuple[str, str, float]] = set()
    for level in levels:
        key = (level.name, level.side, round(level.price, 4))
        if key in seen or level.price <= 0:
            continue
        seen.add(key)
        result.append(level)
    return result


def _timestamp(candle: dict[str, Any]) -> datetime:
    value = candle["timestamp"]
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _optional_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _nth_sunday(year: int, month: int, nth: int) -> int:
    first = datetime(year, month, 1)
    first_sunday = 1 + ((6 - first.weekday()) % 7)
    return first_sunday + ((nth - 1) * 7)


def _new_york_datetime(value: datetime) -> datetime:
    utc_value = value.astimezone(UTC)
    year = utc_value.year
    dst_start_utc = datetime(year, 3, _nth_sunday(year, 3, 2), 7, 0, tzinfo=UTC)
    dst_end_utc = datetime(year, 11, _nth_sunday(year, 11, 1), 6, 0, tzinfo=UTC)
    offset_hours = -4 if dst_start_utc <= utc_value < dst_end_utc else -5
    return utc_value.astimezone(timezone(timedelta(hours=offset_hours), "America/New_York"))
