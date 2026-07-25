from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.app.domain.feature_engine import FeatureQuality
from backend.app.domain.models import Direction, RegimeState, StrategyRole
from backend.app.algorithms.voting_ensemble.snapshot.models import VotingEnsembleEvaluationSnapshot
from backend.app.algorithms.voting_ensemble.strategies.base import StrategyEvaluationContext
from backend.app.algorithms.voting_ensemble.strategies.registry import StrategyCollection, resolve_strategy


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
    transitionConfirmationBars: int = Field(default=2, ge=1, le=10)
    persistenceNamespace: str = "voting_ensemble.regime.adx_atr.state"
    promotionVersion: str = "adx_atr_regime_classifier_promotion_v1"

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


class AdxAtrRegimeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    regimeId: str = "adx_atr_regime"
    label: RegimeLabel
    rawLabel: RegimeLabel
    trendState: str
    volatilityState: str
    marketStructureState: str
    liquidityState: str
    sessionState: str
    eventRiskState: str
    transitionState: str
    direction: Direction
    volatility: Literal["LOW", "NORMAL", "HIGH", "EXTREME"]
    confidence: float
    dataReady: bool
    trendFit: float
    breakoutFit: float
    reversalFit: float
    meanReversionFit: float
    gapSessionFit: float
    reasonCodes: tuple[str, ...]
    evaluatedAt: datetime
    configurationHash: str
    stateNamespace: str
    persistenceNamespace: str
    promotionVersion: str


class AdxAtrRegimeRuntimeState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    activeLabel: RegimeLabel = "unknown"
    pendingLabel: RegimeLabel | None = None
    pendingCount: int = 0
    lastEvaluatedAt: datetime | None = None
    transitionState: str = "initial"


class InMemoryAdxAtrRegimeStateStore:
    namespace = "voting_ensemble.regime.adx_atr.state.memory"

    def __init__(self) -> None:
        self._state: dict[str, AdxAtrRegimeRuntimeState] = {}

    def load(self, key: str) -> AdxAtrRegimeRuntimeState:
        return self._state.get(key, AdxAtrRegimeRuntimeState())

    def save(self, key: str, state: AdxAtrRegimeRuntimeState) -> None:
        self._state[key] = state


class AdxAtrRegimeClassifier:
    registryEntry = resolve_strategy("adx_atr_regime_classifier")

    def __init__(self, config: AdxAtrRegimeConfig | None = None, state_store: InMemoryAdxAtrRegimeStateStore | None = None) -> None:
        self.config = config or AdxAtrRegimeConfig()
        self.state_store = state_store or InMemoryAdxAtrRegimeStateStore()

    def evaluate(self, context: StrategyEvaluationContext) -> RegimeState:
        if context.registryEntry.collection != StrategyCollection.REGIME.value or context.registryEntry.role != StrategyRole.REGIME.value:
            raise ValueError("ADX/ATR regime classification must be registered as a regime")
        evidence = self._evidence(context)
        output = self._output_from_evidence(
            evidence,
            evaluated_at=context.evaluatedAt,
            state_key="SPY",
            session_state=_session_state_from_raw(context.featureSnapshot.rawInputs.get("sessionState")),
            event_risk_state="event_risk_active" if self._event_shock_active(context) else "event_risk_clear",
            liquidity_state=_liquidity_state(None, None),
        )
        return self._to_domain_state(output, context.sessionDate, context.configurationHash)

    def evaluate_snapshot(self, snapshot: VotingEnsembleEvaluationSnapshot) -> RegimeState:
        output = self.evaluate_snapshot_output(snapshot)
        return self._to_domain_state(output, snapshot.evaluationTimestamp.date(), snapshot.settingsHash)

    def evaluate_snapshot_output(self, snapshot: VotingEnsembleEvaluationSnapshot) -> AdxAtrRegimeOutput:
        evidence = self._snapshot_evidence(snapshot)
        return self._output_from_evidence(
            evidence,
            evaluated_at=snapshot.evaluationTimestamp,
            state_key=snapshot.symbol,
            session_state=_session_state_from_raw(snapshot.sessionState),
            event_risk_state=_event_risk_state(snapshot.economicEventState.model_dump(mode="json")),
            liquidity_state=_liquidity_state(
                snapshot.nbbo.spreadBasisPoints if snapshot.nbbo else None,
                snapshot.nbbo.bidSize + snapshot.nbbo.askSize if snapshot.nbbo else None,
            ),
        )

    def _to_domain_state(self, output: AdxAtrRegimeOutput, session_date: date, configuration_hash: str) -> RegimeState:
        return RegimeState(
            regimeId="adx_atr_regime",
            label=output.label,
            direction=output.direction,
            volatility=output.volatility,
            confidence=output.confidence,
            features={
                **output.model_dump(mode="json"),
                "dataReady": output.dataReady,
                "rangeTrendClassification": output.marketStructureState,
                "volatilityExpansionContraction": output.volatilityState,
                "directionalBiasContextOnly": True,
                "directionMustNotSubstituteStrategySignal": True,
                "confidenceRange": "0.0 to 1.0; classifier certainty, not a family fit.",
                "fitRange": "0.0 to 1.0; per-family suitability, separate from strategy confidence.",
                "trendFit": output.trendFit,
                "breakoutFit": output.breakoutFit,
                "reversalFit": output.reversalFit,
                "meanReversionFit": output.meanReversionFit,
                "gapSessionFit": output.gapSessionFit,
                "singleRegimeStateFromActualMeasurements": True,
                "reasonCodes": list(output.reasonCodes),
            },
            evaluatedAt=output.evaluatedAt,
            sessionDate=session_date,
            configurationHash=configuration_hash,
        )

    def _output_from_evidence(
        self,
        evidence: RegimeEvidence,
        *,
        evaluated_at: datetime,
        state_key: str,
        session_state: str,
        event_risk_state: str,
        liquidity_state: str,
    ) -> AdxAtrRegimeOutput:
        stable_label, transition_state, transition_reason = self._stable_label(state_key, evidence.label, evaluated_at)
        return AdxAtrRegimeOutput(
            label=stable_label,
            rawLabel=evidence.label,
            trendState=evidence.rangeTrendClassification,
            volatilityState=evidence.volatilityState,
            marketStructureState=evidence.rangeTrendClassification,
            liquidityState=liquidity_state,
            sessionState=session_state,
            eventRiskState=event_risk_state,
            transitionState=transition_state,
            direction=evidence.direction,
            volatility=evidence.volatility,
            confidence=evidence.confidence,
            dataReady=evidence.dataReady,
            trendFit=evidence.trendFit if stable_label == evidence.label else _transition_fit(evidence.trendFit),
            breakoutFit=evidence.breakoutFit if stable_label == evidence.label else _transition_fit(evidence.breakoutFit),
            reversalFit=evidence.reversalFit,
            meanReversionFit=evidence.meanReversionFit,
            gapSessionFit=evidence.gapSessionFit,
            reasonCodes=tuple([*evidence.reasonCodes, transition_reason]),
            evaluatedAt=evaluated_at.astimezone(UTC) if evaluated_at.tzinfo else evaluated_at.replace(tzinfo=UTC),
            configurationHash=self.config.configurationHash,
            stateNamespace="voting_ensemble.regime.adx_atr.runtime_state",
            persistenceNamespace=self.config.persistenceNamespace,
            promotionVersion=self.config.promotionVersion,
        )

    def _stable_label(self, state_key: str, raw_label: RegimeLabel, evaluated_at: datetime) -> tuple[RegimeLabel, str, str]:
        previous = self.state_store.load(state_key)
        timestamp = evaluated_at.astimezone(UTC) if evaluated_at.tzinfo else evaluated_at.replace(tzinfo=UTC)
        if previous.activeLabel in {"unknown", raw_label}:
            next_state = AdxAtrRegimeRuntimeState(activeLabel=raw_label, pendingLabel=None, pendingCount=0, lastEvaluatedAt=timestamp, transitionState="stable")
            self.state_store.save(state_key, next_state)
            return raw_label, "stable", "regime.transition_stable"
        pending_count = previous.pendingCount + 1 if previous.pendingLabel == raw_label else 1
        if pending_count >= self.config.transitionConfirmationBars:
            next_state = AdxAtrRegimeRuntimeState(activeLabel=raw_label, pendingLabel=None, pendingCount=0, lastEvaluatedAt=timestamp, transitionState="confirmed_transition")
            self.state_store.save(state_key, next_state)
            return raw_label, "confirmed_transition", "regime.transition_confirmed"
        next_state = AdxAtrRegimeRuntimeState(activeLabel=previous.activeLabel, pendingLabel=raw_label, pendingCount=pending_count, lastEvaluatedAt=timestamp, transitionState="pending_transition")
        self.state_store.save(state_key, next_state)
        return previous.activeLabel, "pending_transition", "regime.transition_pending_confirmation"

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

    def _snapshot_evidence(self, snapshot: VotingEnsembleEvaluationSnapshot) -> RegimeEvidence:
        adx = snapshot.features.adx
        atr = snapshot.features.atr
        candles = [item.candle.model_dump(mode="json") for item in snapshot.spyOneMinuteCandles]
        if adx is None or atr is None or atr <= 0 or len(candles) < max(5, self.config.adxPeriod):
            return _unknown(["regime.snapshot_missing_adx_atr_or_candles"])
        direction = _snapshot_direction(snapshot)
        atr_series = _atr_series(candles, min(self.config.adxPeriod, max(2, len(candles) - 1)))
        atr_percentile = _percentile_rank(atr_series, atr) or _atr_percentile_from_snapshot(snapshot)
        realized_volatility_percentile = _realized_volatility_percentile(candles, min(20, max(2, len(candles) - 2))) or 0.5
        volatility_state = self._volatility_state(atr_series, atr)
        volatility = self._volatility_label(atr_percentile, realized_volatility_percentile, volatility_state, None)
        label = self._label(adx, atr_percentile, realized_volatility_percentile, volatility_state, volatility, direction)
        range_trend_classification = self._range_trend_classification(label, adx, direction)
        fits = self._family_fits(label, adx, atr_percentile, realized_volatility_percentile, volatility_state, direction)
        confidence = self._confidence(adx, atr_percentile, realized_volatility_percentile, direction, label)
        return RegimeEvidence(
            dataReady=snapshot.dataReadiness.ready,
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
            directionalBias={Direction.LONG: "bullish_context", Direction.SHORT: "bearish_context"}.get(direction, "neutral_context"),
            trendFit=fits["trendFit"],
            breakoutFit=fits["breakoutFit"],
            reversalFit=fits["reversalFit"],
            meanReversionFit=fits["meanReversionFit"],
            gapSessionFit=fits["gapSessionFit"],
            reasonCodes=[f"regime.{label}", f"regime.volatility_{volatility_state}", "regime.snapshot_point_in_time"],
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
        context: StrategyEvaluationContext | None,
    ) -> Literal["LOW", "NORMAL", "HIGH", "EXTREME"]:
        if context is not None and self._event_shock_active(context) and (realized_volatility_percentile >= 0.65 or atr_percentile >= 0.65):
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


def _snapshot_direction(snapshot: VotingEnsembleEvaluationSnapshot) -> Direction:
    candles = [item.candle for item in snapshot.spyOneMinuteCandles]
    if len(candles) < 3:
        return Direction.FLAT
    lookback = candles[-min(8, len(candles)) :]
    rising = lookback[-1].close > lookback[0].close and lookback[-1].high >= max(candle.high for candle in lookback[:-1])
    falling = lookback[-1].close < lookback[0].close and lookback[-1].low <= min(candle.low for candle in lookback[:-1])
    if rising and not falling:
        return Direction.LONG
    if falling and not rising:
        return Direction.SHORT
    slope = snapshot.features.vwapSlope
    if isinstance(slope, int | float):
        if slope > 0:
            return Direction.LONG
        if slope < 0:
            return Direction.SHORT
    return Direction.FLAT


def _atr_percentile_from_snapshot(snapshot: VotingEnsembleEvaluationSnapshot) -> float:
    atr = snapshot.features.atr or 0.0
    candles = [item.candle for item in snapshot.spyOneMinuteCandles[-20:]]
    if not candles:
        return 0.5
    average_range = mean(max(0.01, candle.high - candle.low) for candle in candles)
    ratio = atr / max(average_range, 0.01)
    return _clamp(0.5 + ((ratio - 1.0) / 2.0))


def _session_state_from_raw(raw: Any) -> str:
    if isinstance(raw, dict):
        phase = str(raw.get("phase") or raw.get("state") or raw.get("session") or "").lower()
        if phase:
            return phase
    return "regular"


def _event_risk_state(raw: dict[str, Any]) -> str:
    name = str(raw.get("name") or "").lower()
    active = bool(raw.get("active") or raw.get("isActive"))
    if active or name not in {"", "none", "no_event"}:
        return "event_risk_active"
    return "event_risk_clear"


def _liquidity_state(spread_bps: float | None, displayed_size: float | None) -> str:
    if spread_bps is None:
        return "liquidity_unknown"
    if spread_bps <= 3 and (displayed_size or 0) >= 1000:
        return "liquidity_good"
    if spread_bps <= 8:
        return "liquidity_acceptable"
    return "liquidity_thin"


def _transition_fit(value: float) -> float:
    return round(_clamp(value * 0.75), 4)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
