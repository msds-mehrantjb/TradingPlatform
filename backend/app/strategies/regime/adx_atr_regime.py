from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain.feature_engine import FeatureQuality
from backend.app.domain.models import Direction, RegimeState, StrategyRole
from backend.app.strategies.base import StrategyEvaluationContext
from backend.app.strategies.registry import StrategyCollection, resolve_strategy


RegimeLabel = Literal[
    "strong_trend",
    "weak_trend",
    "range",
    "low_volatility",
    "high_volatility",
    "event_shock",
    "unknown",
]


class AdxAtrRegimeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "adx_atr_regime_v1"
    adxPeriod: int = Field(default=14, ge=5, le=50)
    strongTrendAdx: float = Field(default=28.0, ge=0, le=100)
    weakTrendAdx: float = Field(default=18.0, ge=0, le=100)
    rangeAdx: float = Field(default=16.0, ge=0, le=100)
    lowAtrPercentile: float = Field(default=0.25, ge=0, le=1)
    highAtrPercentile: float = Field(default=0.75, ge=0, le=1)
    lowRealizedVolatilityPercentile: float = Field(default=0.25, ge=0, le=1)
    highRealizedVolatilityPercentile: float = Field(default=0.75, ge=0, le=1)
    volatilityExpansionRatio: float = Field(default=1.25, gt=0)
    volatilityContractionRatio: float = Field(default=0.80, gt=0)
    atrBaselineWindow: int = Field(default=20, ge=5, le=120)
    maxFeatureAgeSeconds: int = Field(default=90, ge=0, le=900)

    @property
    def configurationHash(self) -> str:
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class RegimeEvidence:
    dataReady: bool
    label: RegimeLabel
    direction: Direction
    volatility: Literal["LOW", "NORMAL", "HIGH", "EXTREME"]
    confidence: float
    adx: float | None
    atr: float | None
    atrPercentile: float | None
    realizedVolatilityPercentile: float | None
    rangeTrendClassification: str
    volatilityState: str
    directionalBias: str
    trendFit: float
    breakoutFit: float
    reversalFit: float
    meanReversionFit: float
    gapSessionFit: float
    reasonCodes: list[str]


class AdxAtrRegimeClassifier:
    registryEntry = resolve_strategy("adx_atr_regime_classifier")

    def __init__(self, config: AdxAtrRegimeConfig | None = None) -> None:
        self.config = config or AdxAtrRegimeConfig()

    def evaluate(self, context: StrategyEvaluationContext) -> RegimeState:
        if context.registryEntry.collection != StrategyCollection.REGIME.value or context.registryEntry.role != StrategyRole.REGIME.value:
            raise ValueError("ADX/ATR regime classification must be registered as a regime")
        evidence = self._evidence(context)
        return RegimeState(
            regimeId="adx_atr_regime",
            label=evidence.label,
            direction=evidence.direction,
            volatility=evidence.volatility,
            confidence=evidence.confidence,
            features={
                "dataReady": evidence.dataReady,
                "trendStrengthAdx": evidence.adx,
                "atr": evidence.atr,
                "atrPercentile": evidence.atrPercentile,
                "realizedVolatilityPercentile": evidence.realizedVolatilityPercentile,
                "rangeTrendClassification": evidence.rangeTrendClassification,
                "volatilityExpansionContraction": evidence.volatilityState,
                "directionalBias": evidence.directionalBias,
                "directionalBiasContextOnly": True,
                "directionMustNotSubstituteStrategySignal": True,
                "confidenceRange": "0.0 to 1.0; classifier certainty, not a family fit.",
                "fitRange": "0.0 to 1.0; per-family suitability, separate from strategy confidence.",
                "trendFit": evidence.trendFit,
                "breakoutFit": evidence.breakoutFit,
                "reversalFit": evidence.reversalFit,
                "meanReversionFit": evidence.meanReversionFit,
                "gapSessionFit": evidence.gapSessionFit,
                "singleRegimeStateFromActualMeasurements": True,
                "reasonCodes": evidence.reasonCodes,
            },
            evaluatedAt=context.evaluatedAt,
            sessionDate=context.sessionDate,
            configurationHash=context.configurationHash,
        )

    def _evidence(self, context: StrategyEvaluationContext) -> RegimeEvidence:
        features = context.featureSnapshot.features
        required = (
            "spy1mAdx14",
            "spy1mAtr14",
            "spy1mRealizedVolatilityPercentile",
            "spy1mHigherHighHigherLow",
            "spy1mLowerHighLowerLow",
        )
        readiness_errors = self._readiness_errors(context, required)
        candles = _candles(context.featureSnapshot.rawInputs.get("spy1mCandles") or [])
        if len(candles) <= self.config.adxPeriod + self.config.atrBaselineWindow:
            readiness_errors.append("regime.insufficient_spy_1m_candles")
        if readiness_errors:
            return _unknown(readiness_errors)

        adx = _number(features["spy1mAdx14"].value)
        atr = _number(features["spy1mAtr14"].value)
        feature_realized_volatility_percentile = _number(features["spy1mRealizedVolatilityPercentile"].value)
        hh_hl = bool(features["spy1mHigherHighHigherLow"].value)
        lh_ll = bool(features["spy1mLowerHighLowerLow"].value)
        atr_series = _atr_series(candles, self.config.adxPeriod)
        atr_percentile = _percentile_rank(atr_series, atr)
        computed_realized_volatility_percentile = _realized_volatility_percentile(candles, 20)
        realized_volatility_percentile = (
            computed_realized_volatility_percentile
            if computed_realized_volatility_percentile is not None
            else feature_realized_volatility_percentile
        )
        if adx is None or atr is None or atr <= 0 or realized_volatility_percentile is None or atr_percentile is None:
            return _unknown(["regime.malformed_measurements"])

        volatility_state = self._volatility_state(atr_series, atr)
        direction = self._direction(hh_hl, lh_ll)
        directional_bias = {Direction.LONG: "bullish_context", Direction.SHORT: "bearish_context"}.get(direction, "neutral_context")
        volatility = self._volatility_label(atr_percentile, realized_volatility_percentile, volatility_state, context)
        label = self._label(adx, atr_percentile, realized_volatility_percentile, volatility_state, volatility, direction)
        range_trend_classification = self._range_trend_classification(label, adx, direction)
        fits = self._family_fits(label, adx, atr_percentile, realized_volatility_percentile, volatility_state, direction)
        confidence = self._confidence(adx, atr_percentile, realized_volatility_percentile, direction, label)
        return RegimeEvidence(
            dataReady=True,
            label=label,
            direction=direction,
            volatility=volatility,
            confidence=confidence,
            adx=round(adx, 4),
            atr=round(atr, 6),
            atrPercentile=round(atr_percentile, 4),
            realizedVolatilityPercentile=round(realized_volatility_percentile, 4),
            rangeTrendClassification=range_trend_classification,
            volatilityState=volatility_state,
            directionalBias=directional_bias,
            trendFit=fits["trendFit"],
            breakoutFit=fits["breakoutFit"],
            reversalFit=fits["reversalFit"],
            meanReversionFit=fits["meanReversionFit"],
            gapSessionFit=fits["gapSessionFit"],
            reasonCodes=[f"regime.{label}", f"regime.volatility_{volatility_state}"],
        )

    def _readiness_errors(self, context: StrategyEvaluationContext, required: tuple[str, ...]) -> list[str]:
        errors: list[str] = []
        for name in required:
            feature = context.featureSnapshot.features.get(name)
            if not feature or feature.quality != FeatureQuality.READY.value:
                errors.append(f"regime.missing_or_unready:{name}")
                continue
            if feature.sourceTimestamp:
                age_seconds = (context.evaluatedAt - feature.sourceTimestamp).total_seconds()
                if age_seconds > self.config.maxFeatureAgeSeconds:
                    errors.append(f"regime.stale:{name}")
        return errors

    def _volatility_state(self, atr_series: list[float | None], current_atr: float) -> str:
        ready = [value for value in atr_series if value is not None]
        if len(ready) <= self.config.atrBaselineWindow:
            return "unknown"
        baseline = ready[-self.config.atrBaselineWindow - 1 : -1]
        if not baseline:
            return "unknown"
        baseline_mean = mean(baseline)
        if baseline_mean <= 0:
            return "unknown"
        ratio = current_atr / baseline_mean
        if ratio >= self.config.volatilityExpansionRatio:
            return "expansion"
        if ratio <= self.config.volatilityContractionRatio:
            return "contraction"
        return "stable"

    def _direction(self, hh_hl: bool, lh_ll: bool) -> Direction:
        if hh_hl and not lh_ll:
            return Direction.LONG
        if lh_ll and not hh_hl:
            return Direction.SHORT
        return Direction.FLAT

    def _volatility_label(
        self,
        atr_percentile: float,
        realized_volatility_percentile: float,
        volatility_state: str,
        context: StrategyEvaluationContext,
    ) -> Literal["LOW", "NORMAL", "HIGH", "EXTREME"]:
        if self._event_shock_active(context) and (realized_volatility_percentile >= 0.65 or atr_percentile >= 0.65):
            return "EXTREME"
        if atr_percentile >= self.config.highAtrPercentile or realized_volatility_percentile >= self.config.highRealizedVolatilityPercentile:
            return "HIGH"
        if volatility_state == "contraction" or (
            atr_percentile <= self.config.lowAtrPercentile and realized_volatility_percentile <= 0.60
        ):
            return "LOW"
        return "NORMAL"

    def _label(
        self,
        adx: float,
        atr_percentile: float,
        realized_volatility_percentile: float,
        volatility_state: str,
        volatility: str,
        direction: Direction,
    ) -> RegimeLabel:
        if volatility == "EXTREME":
            return "event_shock"
        if volatility == "LOW" and adx <= self.config.weakTrendAdx:
            return "low_volatility"
        if volatility == "HIGH" and adx < self.config.strongTrendAdx:
            return "high_volatility"
        if direction != Direction.FLAT and adx >= self.config.strongTrendAdx:
            return "strong_trend"
        if direction != Direction.FLAT and adx >= self.config.weakTrendAdx:
            return "weak_trend"
        if adx <= self.config.rangeAdx or direction == Direction.FLAT:
            return "range"
        if atr_percentile >= self.config.highAtrPercentile or realized_volatility_percentile >= self.config.highRealizedVolatilityPercentile:
            return "high_volatility"
        if volatility_state == "contraction":
            return "low_volatility"
        return "unknown"

    def _range_trend_classification(self, label: RegimeLabel, adx: float, direction: Direction) -> str:
        if label in {"strong_trend", "weak_trend"} and direction != Direction.FLAT:
            return "trend"
        if label in {"range", "low_volatility"} or adx <= self.config.rangeAdx:
            return "range"
        if label in {"high_volatility", "event_shock"}:
            return "unstable"
        return "unknown"

    def _family_fits(
        self,
        label: RegimeLabel,
        adx: float,
        atr_percentile: float,
        realized_volatility_percentile: float,
        volatility_state: str,
        direction: Direction,
    ) -> dict[str, float]:
        trend_strength = _clamp(adx / max(self.config.strongTrendAdx, 1.0))
        vol_score = _clamp((atr_percentile + realized_volatility_percentile) / 2)
        directional_score = 1.0 if direction != Direction.FLAT else 0.25
        expansion_bonus = 0.15 if volatility_state == "expansion" else -0.10 if volatility_state == "contraction" else 0.0
        trend_fit = _clamp((0.60 * trend_strength) + (0.30 * directional_score) + (0.10 * vol_score))
        breakout_fit = _clamp((0.45 * trend_strength) + (0.30 * vol_score) + (0.25 * directional_score) + expansion_bonus)
        reversal_fit = _clamp((0.45 * (1 - trend_strength)) + (0.35 * vol_score) + (0.20 * (1 - directional_score)))
        mean_reversion_fit = _clamp((0.55 * (1 - trend_strength)) + (0.30 * (1 - vol_score)) + (0.15 * (1 - directional_score)))
        gap_session_fit = _clamp((0.35 * vol_score) + (0.25 if label in {"event_shock", "high_volatility"} else 0.15) + (0.20 * directional_score))
        if label == "strong_trend":
            mean_reversion_fit = min(mean_reversion_fit, 0.30)
            reversal_fit = min(reversal_fit, 0.45)
        if label in {"range", "low_volatility"} and direction == Direction.FLAT:
            trend_fit = min(trend_fit, 0.35)
            breakout_fit = min(breakout_fit, 0.35)
        if label == "event_shock":
            mean_reversion_fit = min(mean_reversion_fit, 0.25)
            gap_session_fit = max(gap_session_fit, 0.70)
        return {
            "trendFit": round(trend_fit, 4),
            "breakoutFit": round(breakout_fit, 4),
            "reversalFit": round(reversal_fit, 4),
            "meanReversionFit": round(mean_reversion_fit, 4),
            "gapSessionFit": round(gap_session_fit, 4),
        }

    def _confidence(
        self,
        adx: float,
        atr_percentile: float,
        realized_volatility_percentile: float,
        direction: Direction,
        label: RegimeLabel,
    ) -> float:
        if label == "unknown":
            return 0.0
        threshold_distance = max(
            abs(adx - self.config.strongTrendAdx) / max(self.config.strongTrendAdx, 1.0),
            abs(adx - self.config.rangeAdx) / max(self.config.strongTrendAdx, 1.0),
        )
        vol_certainty = abs(((atr_percentile + realized_volatility_percentile) / 2) - 0.5) * 2
        direction_certainty = 0.15 if direction != Direction.FLAT else 0.0
        return round(_clamp(0.45 + (0.25 * min(threshold_distance, 1.0)) + (0.20 * vol_certainty) + direction_certainty), 4)

    def _event_shock_active(self, context: StrategyEvaluationContext) -> bool:
        feature = context.featureSnapshot.features.get("economicEventState")
        state = feature.value if feature and isinstance(feature.value, dict) else context.featureSnapshot.rawInputs.get("economicEventState")
        if not isinstance(state, dict):
            return False
        importance = str(state.get("importance") or state.get("category") or "").lower()
        active = bool(state.get("active") or state.get("isActive"))
        return active and importance in {"high", "major", "fomc", "cpi", "jobs"}


def _unknown(reason_codes: list[str]) -> RegimeEvidence:
    return RegimeEvidence(
        dataReady=False,
        label="unknown",
        direction=Direction.FLAT,
        volatility="NORMAL",
        confidence=0.0,
        adx=None,
        atr=None,
        atrPercentile=None,
        realizedVolatilityPercentile=None,
        rangeTrendClassification="unknown",
        volatilityState="unknown",
        directionalBias="neutral_context",
        trendFit=0.0,
        breakoutFit=0.0,
        reversalFit=0.0,
        meanReversionFit=0.0,
        gapSessionFit=0.0,
        reasonCodes=reason_codes,
    )


def _candles(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(raw, key=lambda row: _timestamp(row["timestamp"]))


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _atr_series(candles: list[dict[str, Any]], period: int) -> list[float | None]:
    true_ranges: list[float] = []
    result: list[float | None] = []
    for index, current in enumerate(candles):
        if index == 0:
            result.append(None)
            continue
        previous_close = float(candles[index - 1]["close"])
        true_ranges.append(
            max(
                float(current["high"]) - float(current["low"]),
                abs(float(current["high"]) - previous_close),
                abs(float(current["low"]) - previous_close),
            )
        )
        if len(true_ranges) < period:
            result.append(None)
        else:
            result.append(mean(true_ranges[-period:]))
    return result


def _realized_volatility_percentile(candles: list[dict[str, Any]], period: int) -> float | None:
    closes = [float(candle["close"]) for candle in candles]
    series: list[float | None] = []
    for index in range(len(closes)):
        sample = closes[: index + 1]
        if len(sample) <= period:
            series.append(None)
            continue
        returns = [
            (sample[offset] - sample[offset - 1]) / sample[offset - 1]
            for offset in range(len(sample) - period, len(sample))
            if sample[offset - 1] != 0
        ]
        if not returns:
            series.append(None)
            continue
        squared_mean = mean([value * value for value in returns])
        series.append(squared_mean**0.5)
    return _percentile_rank(series, series[-1] if series else None)


def _percentile_rank(values: list[float | None], current: float | None) -> float | None:
    ready = [value for value in values if value is not None]
    if current is None or len(ready) < 5:
        return None
    below = sum(1 for value in ready if value < current)
    equal = sum(1 for value in ready if value == current)
    return (below + (0.5 * equal)) / len(ready)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
