from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.domain.models import Signal, StrategySignal
from backend.app.algorithms.voting_ensemble.strategies.base import (
    StrategyEvaluationContext,
    hold_signal,
    required_features_ready,
    strategy_signal,
    unavailable_signal,
)
from backend.app.algorithms.voting_ensemble.strategies.registry import resolve_strategy


LiquiditySide = Literal["HIGH", "LOW"]


class LiquiditySweepReversalConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "liquidity_sweep_reversal_v1"
    sweepLookbackCandles: int = Field(default=3, ge=1, le=20)
    swingLookbackCandles: int = Field(default=12, ge=4, le=60)
    minSweepDollars: float = Field(default=0.01, ge=0)
    sweepAtrMultiplier: float = Field(default=0.12, ge=0)
    spreadBufferMultiplier: float = Field(default=1.0, ge=0)
    minWickToRangeRatio: float = Field(default=0.35, ge=0, le=1)
    minRejectionBodyToRange: float = Field(default=0.3, ge=0, le=1)
    minCloseBackAtrMultiplier: float = Field(default=0.02, ge=0)
    minActivityVolumeRatio: float = Field(default=1.15, ge=0)
    minTradeCountRatio: float = Field(default=1.05, ge=0)
    activityLookbackCandles: int = Field(default=20, ge=5, le=60)
    maxSpreadBasisPoints: float = Field(default=12.0, gt=0)
    includeDerivedSessionLevels: bool = True

    @model_validator(mode="after")
    def activity_lookback_must_cover_sweep(self) -> LiquiditySweepReversalConfig:
        if self.activityLookbackCandles < self.sweepLookbackCandles:
            raise ValueError("activityLookbackCandles must be at least sweepLookbackCandles")
        return self

    @property
    def configurationHash(self) -> str:
        payload = self.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class LiquidityLevel:
    name: str
    side: LiquiditySide
    price: float
    sourceTimestamp: datetime | None = None


@dataclass(frozen=True)
class SweepEvidence:
    signal: Signal
    setupId: str | None
    level: LiquidityLevel | None
    swept: bool
    reclaimed: bool
    rejectionQuality: bool
    activityConfirmed: bool
    continuedBeyondLevel: bool
    sweepDistance: float
    closeBackDistance: float
    wickRatio: float
    volumeRatio: float | None
    tradeCountRatio: float | None
    bufferDollars: float
    sweptAt: datetime | None
    structuralInvalidationPrice: float | None


class LiquiditySweepReversalStrategy:
    registryEntry = resolve_strategy("liquidity_sweep_reversal")

    def __init__(self, config: LiquiditySweepReversalConfig | None = None) -> None:
        self.config = config or LiquiditySweepReversalConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> StrategySignal:
        required_features = self.required_feature_names()
        if not context.featureSnapshot.dataReady:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Feature snapshot is not ready for liquidity sweep reversal.",
            )
        if not required_features_ready(context.featureSnapshot, required_features):
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Required feature measurements are unavailable for liquidity sweep reversal.",
            )

        candles = _session_candles(context.featureSnapshot.rawInputs.get("spy1mCandles") or [], context)
        if len(candles) < max(self.config.activityLookbackCandles + 1, self.config.swingLookbackCandles + 1):
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Insufficient completed 1-minute candles for liquidity sweep reversal.",
            )

        levels = self._liquidity_levels(context, candles)
        if not levels:
            return hold_signal(
                context,
                confidence=0.0,
                setupDetected=False,
                regimeFit=0.0,
                reliability=0.0,
                reasonCodes=["liquidity_sweep.no_reference_levels"],
                explanation="HOLD because no identifiable liquidity levels are available.",
                featureNames=required_features,
            )

        evidence = self._best_evidence(context, candles, levels)
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
                    "liquidity_sweep.reversal_confirmed",
                    f"direction:{evidence.signal.value.lower()}",
                    f"level:{evidence.level.name if evidence.level else 'unknown'}",
                    f"setup_id:{evidence.setupId}",
                ],
                explanation=self._explanation(evidence),
                featureNames=required_features,
                structuralInvalidationPrice=evidence.structuralInvalidationPrice,
            )

        return hold_signal(
            context,
            confidence=self._hold_confidence(evidence),
            setupDetected=evidence.swept,
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
            "spy1mRelativeVolume",
            "spy1mRollingHigh20",
            "spy1mRollingLow20",
            "spreadDollars",
            "spreadBasisPoints",
        )

    def _liquidity_levels(self, context: StrategyEvaluationContext, candles: list[dict[str, Any]]) -> list[LiquidityLevel]:
        raw_inputs = context.featureSnapshot.rawInputs
        features = context.featureSnapshot.features
        levels: list[LiquidityLevel] = []

        prior = raw_inputs.get("priorDayOHLC") or {}
        if prior.get("high"):
            levels.append(LiquidityLevel("prior_day_high", "HIGH", float(prior["high"])))
        if prior.get("low"):
            levels.append(LiquidityLevel("prior_day_low", "LOW", float(prior["low"])))

        premarket = raw_inputs.get("premarket") or {}
        if premarket.get("high"):
            levels.append(LiquidityLevel("premarket_high", "HIGH", float(premarket["high"]), _optional_timestamp(premarket.get("sourceTimestamp"))))
        if premarket.get("low"):
            levels.append(LiquidityLevel("premarket_low", "LOW", float(premarket["low"]), _optional_timestamp(premarket.get("sourceTimestamp"))))

        opening_range = raw_inputs.get("openingRange") or {}
        if opening_range.get("high"):
            levels.append(LiquidityLevel("opening_range_high", "HIGH", float(opening_range["high"]), _optional_timestamp(opening_range.get("endTimestamp"))))
        if opening_range.get("low"):
            levels.append(LiquidityLevel("opening_range_low", "LOW", float(opening_range["low"]), _optional_timestamp(opening_range.get("endTimestamp"))))

        if self.config.includeDerivedSessionLevels:
            swing_high, swing_low = _recent_swing_levels(candles, self.config.swingLookbackCandles)
            if swing_high is not None:
                levels.append(LiquidityLevel("prior_swing_high", "HIGH", swing_high, _timestamp(candles[-2])))
            if swing_low is not None:
                levels.append(LiquidityLevel("prior_swing_low", "LOW", swing_low, _timestamp(candles[-2])))

            session_sample = candles[:-1]
            if session_sample:
                levels.append(LiquidityLevel("session_high", "HIGH", max(float(candle["high"]) for candle in session_sample), _timestamp(session_sample[-1])))
                levels.append(LiquidityLevel("session_low", "LOW", min(float(candle["low"]) for candle in session_sample), _timestamp(session_sample[-1])))

            rolling_high = _number(features["spy1mRollingHigh20"].value)
            rolling_low = _number(features["spy1mRollingLow20"].value)
            if rolling_high is not None:
                levels.append(LiquidityLevel("rolling_high_20", "HIGH", rolling_high, features["spy1mRollingHigh20"].sourceTimestamp))
            if rolling_low is not None:
                levels.append(LiquidityLevel("rolling_low_20", "LOW", rolling_low, features["spy1mRollingLow20"].sourceTimestamp))

        return _dedupe_levels(levels)

    def _best_evidence(
        self,
        context: StrategyEvaluationContext,
        candles: list[dict[str, Any]],
        levels: list[LiquidityLevel],
    ) -> SweepEvidence:
        evidences = [self._evidence_for_level(context, candles, level) for level in levels]
        signals = [evidence for evidence in evidences if evidence.signal in {Signal.BUY, Signal.SELL}]
        if signals:
            return max(signals, key=lambda evidence: self._confidence(evidence))
        continued = [evidence for evidence in evidences if evidence.swept and evidence.continuedBeyondLevel]
        if continued:
            return max(continued, key=lambda evidence: evidence.sweepDistance)
        return max(evidences, key=lambda evidence: (evidence.swept, evidence.reclaimed, evidence.sweepDistance, evidence.wickRatio))

    def _evidence_for_level(self, context: StrategyEvaluationContext, candles: list[dict[str, Any]], level: LiquidityLevel) -> SweepEvidence:
        features = context.featureSnapshot.features
        latest = candles[-1]
        recent = candles[-self.config.sweepLookbackCandles :]
        atr = _number(features["spy1mAtr14"].value) or 0.0
        spread = _number(features["spreadDollars"].value) or 0.0
        spread_bps = _number(features["spreadBasisPoints"].value) or 999.0
        buffer_dollars = max(
            self.config.minSweepDollars,
            atr * self.config.sweepAtrMultiplier,
            spread * self.config.spreadBufferMultiplier,
        )
        sweep_candles = [candle for candle in recent if _sweeps_level(candle, level, buffer_dollars)]
        swept = bool(sweep_candles)
        latest_close = float(latest["close"])
        close_back_buffer = atr * self.config.minCloseBackAtrMultiplier

        if level.side == "HIGH":
            reclaimed = latest_close <= level.price - close_back_buffer
            continued = latest_close > level.price
            signal = Signal.SELL
            sweep_distance = max((float(candle["high"]) - level.price for candle in sweep_candles), default=0.0)
            close_back_distance = level.price - latest_close
            wick_ratio = max((_upper_wick_ratio(candle) for candle in sweep_candles), default=0.0)
            structural_invalidation = max(float(candle["high"]) for candle in recent) if swept else level.price
        else:
            reclaimed = latest_close >= level.price + close_back_buffer
            continued = latest_close < level.price
            signal = Signal.BUY
            sweep_distance = max((level.price - float(candle["low"]) for candle in sweep_candles), default=0.0)
            close_back_distance = latest_close - level.price
            wick_ratio = max((_lower_wick_ratio(candle) for candle in sweep_candles), default=0.0)
            structural_invalidation = min(float(candle["low"]) for candle in recent) if swept else level.price

        rejection_quality = _rejection_quality(latest, level, min_body_to_range=self.config.minRejectionBodyToRange)
        volume_ratio = _volume_ratio(candles, self.config.activityLookbackCandles)
        trade_count_ratio = _trade_count_ratio(candles, self.config.activityLookbackCandles)
        activity_confirmed = (
            volume_ratio is not None
            and volume_ratio >= self.config.minActivityVolumeRatio
            and trade_count_ratio is not None
            and trade_count_ratio >= self.config.minTradeCountRatio
            and spread_bps <= self.config.maxSpreadBasisPoints
        )
        swept_at = _timestamp(sweep_candles[-1]) if sweep_candles else None
        setup_id = self._setup_id(context, level, signal, swept_at or _timestamp(latest)) if swept else None
        should_signal = swept and reclaimed and not continued and wick_ratio >= self.config.minWickToRangeRatio and rejection_quality and activity_confirmed

        return SweepEvidence(
            signal=signal if should_signal else Signal.HOLD,
            setupId=setup_id,
            level=level,
            swept=swept,
            reclaimed=reclaimed,
            rejectionQuality=rejection_quality,
            activityConfirmed=activity_confirmed,
            continuedBeyondLevel=continued,
            sweepDistance=sweep_distance,
            closeBackDistance=close_back_distance,
            wickRatio=wick_ratio,
            volumeRatio=volume_ratio,
            tradeCountRatio=trade_count_ratio,
            bufferDollars=buffer_dollars,
            sweptAt=swept_at,
            structuralInvalidationPrice=round(structural_invalidation, 4),
        )

    def _setup_id(self, context: StrategyEvaluationContext, level: LiquidityLevel, signal: Signal, swept_at: datetime) -> str:
        payload = {
            "strategy": self.registryEntry.strategyId,
            "sessionDate": context.sessionDate.isoformat(),
            "level": level.name,
            "price": round(level.price, 4),
            "signal": signal.value,
            "sweptAt": swept_at.isoformat().replace("+00:00", "Z"),
        }
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:16]

    def _confidence(self, evidence: SweepEvidence) -> float:
        sweep_score = min(1.0, evidence.sweepDistance / max(evidence.bufferDollars * 3, 0.01))
        reclaim_score = min(1.0, evidence.closeBackDistance / max(evidence.bufferDollars * 3, 0.01))
        wick_score = min(1.0, evidence.wickRatio / max(self.config.minWickToRangeRatio * 1.5, 0.01))
        activity_score = 0.0 if evidence.volumeRatio is None else min(1.0, evidence.volumeRatio / max(self.config.minActivityVolumeRatio * 1.5, 0.01))
        confidence = (0.3 * sweep_score) + (0.25 * reclaim_score) + (0.25 * wick_score) + (0.2 * activity_score)
        return round(max(0.0, min(1.0, confidence)), 4)

    def _hold_confidence(self, evidence: SweepEvidence) -> float:
        partials = [evidence.swept, evidence.reclaimed, evidence.rejectionQuality, evidence.activityConfirmed]
        return round(min(0.44, max(0.05, sum(1 for item in partials if item) / len(partials) * 0.44)), 4)

    def _regime_fit(self, evidence: SweepEvidence) -> float:
        sweep_fit = min(1.0, evidence.sweepDistance / max(evidence.bufferDollars * 4, 0.01))
        wick_fit = min(1.0, evidence.wickRatio / max(self.config.minWickToRangeRatio, 0.01))
        return round(max(0.0, min(1.0, (0.55 * sweep_fit) + (0.45 * wick_fit))), 4)

    def _reliability(self, evidence: SweepEvidence) -> float:
        checks = [evidence.swept, evidence.reclaimed, evidence.rejectionQuality, evidence.activityConfirmed, not evidence.continuedBeyondLevel]
        return round(max(0.0, min(1.0, sum(1 for item in checks if item) / len(checks))), 4)

    def _explanation(self, evidence: SweepEvidence) -> str:
        assert evidence.level is not None
        return (
            f"{evidence.signal.value} liquidity sweep reversal: {evidence.level.name} at {evidence.level.price:.2f} "
            f"was swept by {evidence.sweepDistance:.2f} and reclaimed by {evidence.closeBackDistance:.2f}."
        )

    def _hold_reason_codes(self, evidence: SweepEvidence) -> list[str]:
        if evidence.continuedBeyondLevel:
            return ["liquidity_sweep.continued_beyond_level"]
        if not evidence.swept:
            return ["liquidity_sweep.no_level_sweep"]
        if not evidence.reclaimed:
            return ["liquidity_sweep.no_reclaim"]
        if evidence.wickRatio < self.config.minWickToRangeRatio:
            return ["liquidity_sweep.insufficient_wick"]
        if not evidence.rejectionQuality:
            return ["liquidity_sweep.weak_rejection"]
        if not evidence.activityConfirmed:
            return ["liquidity_sweep.no_activity_confirmation"]
        return ["liquidity_sweep.weak_or_conflicting_evidence"]

    def _hold_explanation(self, evidence: SweepEvidence) -> str:
        reason = self._hold_reason_codes(evidence)[0].removeprefix("liquidity_sweep.").replace("_", " ")
        return f"HOLD because liquidity sweep reversal evidence is incomplete: {reason}."


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


def _sweeps_level(candle: dict[str, Any], level: LiquidityLevel, buffer_dollars: float) -> bool:
    if level.side == "HIGH":
        return float(candle["high"]) >= level.price + buffer_dollars
    return float(candle["low"]) <= level.price - buffer_dollars


def _upper_wick_ratio(candle: dict[str, Any]) -> float:
    high = float(candle["high"])
    low = float(candle["low"])
    body_top = max(float(candle["open"]), float(candle["close"]))
    candle_range = high - low
    return (high - body_top) / candle_range if candle_range > 0 else 0.0


def _lower_wick_ratio(candle: dict[str, Any]) -> float:
    high = float(candle["high"])
    low = float(candle["low"])
    body_bottom = min(float(candle["open"]), float(candle["close"]))
    candle_range = high - low
    return (body_bottom - low) / candle_range if candle_range > 0 else 0.0


def _rejection_quality(candle: dict[str, Any], level: LiquidityLevel, *, min_body_to_range: float) -> bool:
    open_price = float(candle["open"])
    close = float(candle["close"])
    high = float(candle["high"])
    low = float(candle["low"])
    candle_range = high - low
    if candle_range <= 0:
        return False
    body_ratio = abs(close - open_price) / candle_range
    if level.side == "HIGH":
        return close < open_price and close <= level.price and body_ratio >= min_body_to_range
    return close > open_price and close >= level.price and body_ratio >= min_body_to_range


def _volume_ratio(candles: list[dict[str, Any]], lookback: int) -> float | None:
    if len(candles) < lookback + 1:
        return None
    baseline = mean(float(candle["volume"]) for candle in candles[-lookback - 1 : -1])
    return float(candles[-1]["volume"]) / baseline if baseline else None


def _trade_count_ratio(candles: list[dict[str, Any]], lookback: int) -> float | None:
    if len(candles) < lookback + 1:
        return None
    baseline = mean(float(candle.get("tradeCount") or 0) for candle in candles[-lookback - 1 : -1])
    return float(candles[-1].get("tradeCount") or 0) / baseline if baseline else None


def _recent_swing_levels(candles: list[dict[str, Any]], lookback: int) -> tuple[float | None, float | None]:
    if len(candles) <= lookback + 1:
        return None, None
    sample = candles[-lookback - 1 : -1]
    return max(float(candle["high"]) for candle in sample), min(float(candle["low"]) for candle in sample)


def _dedupe_levels(levels: list[LiquidityLevel]) -> list[LiquidityLevel]:
    result: list[LiquidityLevel] = []
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
