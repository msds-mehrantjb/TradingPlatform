from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from enum import Enum
from statistics import mean
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.domain.exchange_calendar import ExchangeCalendarService, ExchangeSession, NEW_YORK
from backend.app.domain.indicator_service import PointInTimeIndicatorService
from backend.app.domain.models import Signal, StrategySignal
from backend.app.domain.relative_volume import PointInTimeRelativeVolumeService
from backend.app.algorithms.voting_ensemble.strategy_performance import StrategyReliabilityEstimate, VotingEnsembleStrategyPerformanceTracker
from backend.app.algorithms.voting_ensemble.strategies.base import (
    StrategyEvaluationContext,
    hold_signal,
    required_features_ready,
    strategy_signal,
    unavailable_signal,
)
from backend.app.algorithms.voting_ensemble.strategies.registry import resolve_strategy


INDICATORS = PointInTimeIndicatorService()
PERFORMANCE_TRACKER = VotingEnsembleStrategyPerformanceTracker()
EXCHANGE_CALENDAR = ExchangeCalendarService()


class FirstPullbackState(str, Enum):
    WAITING_FOR_OPEN = "waiting_for_open"
    WAITING_FOR_IMPULSE = "waiting_for_impulse"
    IMPULSE_BUILDING = "impulse_building"
    WAITING_FOR_FIRST_PULLBACK = "waiting_for_first_pullback"
    PULLBACK_ACTIVE = "pullback_active"
    PULLBACK_DECELERATING = "pullback_decelerating"
    CONFIRMATION_CANDIDATE = "confirmation_candidate"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    CONFIRMATION_FAILED = "confirmation_failed"
    SIGNAL_EMITTED = "signal_emitted"
    FIRST_PULLBACK_REJECTED = "first_pullback_rejected"
    INVALIDATED = "invalidated"
    SESSION_COMPLETE = "session_complete"


class FirstPullbackClassification(str, Enum):
    FORMING = "forming"
    QUALIFIED = "qualified"
    TOO_SHALLOW = "too_shallow"
    TOO_DEEP = "too_deep"
    TOO_HIGH_VOLUME = "too_high_volume"
    VOLUME_UNAVAILABLE = "volume_unavailable"
    TREND_REVERSAL = "trend_reversal"
    EXPIRED = "expired"
    CONFIRMED = "confirmed"


class VwapPreservationMode(str, Enum):
    STRICT = "strict"
    MODERATE = "moderate"
    CONTEXT = "context"


class RelativeVolumeEvidenceMode(str, Enum):
    STRICT = "strict"
    OPTIONAL = "optional"


class FirstPullbackAfterOpenConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "first_pullback_after_open_v2"
    sessionStartMinute: int = Field(default=0, ge=0, le=390)
    impulseWindowEndMinute: int = Field(default=45, ge=1, le=390)
    maxImpulseStartMinute: int = Field(default=30, ge=0, le=390)
    entryWindowEndMinute: int = Field(default=150, ge=1, le=390)
    atrLookbackCandles: int = Field(default=14, ge=2, le=60)
    minImpulseCandles: int = Field(default=3, ge=2, le=20)
    maxImpulseCandles: int = Field(default=8, ge=2, le=30)
    minImpulseAtrMultiple: float = Field(default=1.2, ge=0)
    minImpulsePercent: float = Field(default=0.0025, ge=0)
    minImpulseStructureBreakAtr: float = Field(default=0.2, ge=0)
    minRelativeVolume: float = Field(default=1.15, ge=0)
    requireRelativeVolumeWhenAvailable: bool = True
    relativeVolumeEvidenceMode: RelativeVolumeEvidenceMode = RelativeVolumeEvidenceMode.OPTIONAL
    optionalRelativeVolumeMissingContribution: float = Field(default=0.35, ge=0, le=1)
    pullbackRetracementMin: float = Field(default=0.25, ge=0, le=1)
    pullbackRetracementMax: float = Field(default=0.65, ge=0, le=1)
    pullbackZoneAtrTolerance: float = Field(default=0.25, ge=0)
    maxPullbackVolumeRatio: float = Field(default=0.8, ge=0, le=2)
    requireReducedPullbackVolume: bool = True
    originBreakAtrBuffer: float = Field(default=0.05, ge=0)
    originAcceptanceBars: int = Field(default=2, ge=1, le=5)
    confirmationCloseBeyondPullbackAtr: float = Field(default=0.05, ge=0)
    confirmationMinimumBodyAtr: float = Field(default=0.1, ge=0)
    minConfirmationCloseLocation: float = Field(default=0.65, ge=0, le=1)
    maxConfirmationUpperWickFraction: float = Field(default=0.35, ge=0, le=1)
    maxConfirmationRangeAtr: float = Field(default=3.0, ge=0.1, le=10)
    maxConfirmationVwapDistanceAtr: float = Field(default=5.0, ge=0, le=10)
    maxConfirmationEma20DistanceAtr: float = Field(default=5.0, ge=0, le=10)
    minOpposingLevelDistanceAtr: float = Field(default=0.0, ge=0, le=10)
    confirmationExpiryCandles: int = Field(default=3, ge=1, le=30)
    maximumPullbackBars: int = Field(default=5, ge=1, le=60)
    maximumPullbackMinutes: int = Field(default=8, ge=1, le=120)
    maximumBarsFromImpulseToPullback: int = Field(default=4, ge=0, le=60)
    maximumBarsFromQualificationToConfirmation: int = Field(default=3, ge=1, le=60)
    includeApprovedPremarketInIndicatorWarmup: bool = True
    minEstablishedTrendScore: float = Field(default=0.75, ge=0, le=1)
    minDirectionalEfficiency: float = Field(default=0.65, ge=0, le=1)
    minDirectionalCloses: int = Field(default=3, ge=1, le=20)
    minImpulseCloseLocation: float = Field(default=0.65, ge=0, le=1)
    minimumDirectionalCandleRatio: float = Field(default=0.66, ge=0, le=1)
    minimumBodyToRangeRatio: float = Field(default=0.35, ge=0, le=1)
    minimumImpulseCloseLocation: float = Field(default=0.65, ge=0, le=1)
    minimumEfficiencyRatio: float = Field(default=0.65, ge=0, le=1)
    maximumInternalRetracement: float = Field(default=0.55, ge=0, le=1)
    maximumOpposingCandleCount: int = Field(default=1, ge=0, le=20)
    maximumOpposingVolumeRatio: float = Field(default=0.80, ge=0, le=5)
    maxImmediateRetracement: float = Field(default=0.80, ge=0, le=2)
    requireFiveMinutePermission: bool = False
    vwapPreservationMode: VwapPreservationMode = VwapPreservationMode.STRICT
    vwapPenetrationToleranceAtr: float = Field(default=0.50, ge=0, le=5)
    maxVwapWrongSideClosesStrict: int = Field(default=0, ge=0, le=10)
    vwapReclaimBarsModerate: int = Field(default=2, ge=1, le=20)
    vwapContextConfidencePenalty: float = Field(default=0.20, ge=0, le=1)
    minimumActionableConfidence: float = Field(default=0.35, ge=0, le=1)
    lateSetupPenaltyMax: float = Field(default=0.08, ge=0, le=1)
    deepPullbackPenaltyMax: float = Field(default=0.10, ge=0, le=1)
    longPullbackDurationPenaltyMax: float = Field(default=0.08, ge=0, le=1)
    repeatedVwapLossPenalty: float = Field(default=0.08, ge=0, le=1)
    largeConfirmationCandlePenaltyMax: float = Field(default=0.10, ge=0, le=1)
    nearbyOpposingLevelPenalty: float = Field(default=0.08, ge=0, le=1)
    higherTimeframeDisagreementPenalty: float = Field(default=0.08, ge=0, le=1)
    abnormalSpreadPenalty: float = Field(default=0.08, ge=0, le=1)
    abnormalSpreadAtrThreshold: float = Field(default=0.08, ge=0)
    missingOptionalEvidencePenalty: float = Field(default=0.05, ge=0, le=1)

    @model_validator(mode="after")
    def windows_and_retracement_must_be_ordered(self) -> FirstPullbackAfterOpenConfig:
        if self.entryWindowEndMinute <= self.impulseWindowEndMinute:
            raise ValueError("entryWindowEndMinute must be after impulseWindowEndMinute")
        if self.maxImpulseStartMinute > self.impulseWindowEndMinute:
            raise ValueError("maxImpulseStartMinute cannot be after impulseWindowEndMinute")
        if self.pullbackRetracementMin > self.pullbackRetracementMax:
            raise ValueError("pullbackRetracementMin cannot exceed pullbackRetracementMax")
        if self.minImpulseCandles > self.maxImpulseCandles:
            raise ValueError("minImpulseCandles cannot exceed maxImpulseCandles")
        if self.minDirectionalCloses > self.maxImpulseCandles:
            raise ValueError("minDirectionalCloses cannot exceed maxImpulseCandles")
        if self.maximumOpposingCandleCount > self.maxImpulseCandles:
            raise ValueError("maximumOpposingCandleCount cannot exceed maxImpulseCandles")
        return self

    @property
    def configurationHash(self) -> str:
        payload = self.model_dump(mode="json")
        serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class OriginViolationResult:
    level: float
    buffer: float
    wickViolation: bool
    closeViolation: bool
    acceptanceBeyondLevel: bool
    hardViolation: bool
    reasonCodes: tuple[str, ...]


@dataclass(frozen=True)
class InvalidationLevel:
    name: str
    level: float | None
    basis: str
    violation: OriginViolationResult | None = None


@dataclass(frozen=True)
class InvalidationLevels:
    entryInvalidation: InvalidationLevel
    setupInvalidation: InvalidationLevel
    thesisInvalidation: InvalidationLevel


@dataclass(frozen=True)
class ImpulseQualityResult:
    passed: bool
    directionalCandleRatio: float
    averageBodyToRangeRatio: float
    impulseCloseLocation: float
    efficiencyRatio: float
    maximumInternalRetracement: float
    opposingCandleCount: int
    opposingVolumeRatio: float | None
    startMinute: float
    reasonCodes: tuple[str, ...]


@dataclass(frozen=True)
class Impulse:
    direction: Signal
    startIndex: int
    endIndex: int
    startTimestamp: datetime
    endTimestamp: datetime
    originPrice: float
    extremePrice: float
    atr: float
    displacementAtr: float
    displacementPercent: float
    relativeVolume: float | None
    averageRelativeVolume: float | None
    averageVolume: float
    quality: ImpulseQualityResult


@dataclass(frozen=True)
class EstablishedTrendResult:
    established: bool
    direction: Signal
    score: float
    priceVwapOk: bool
    emaRelationshipOk: bool
    ema20SlopeOk: bool
    structureOk: bool
    directionalEfficiency: float
    directionalCloses: int
    impulseCloseLocation: float
    immediateRetracement: float
    fiveMinutePermissionOk: bool | None
    originViolation: OriginViolationResult | None
    reasonCodes: tuple[str, ...]


@dataclass(frozen=True)
class VwapPolicyResult:
    mode: VwapPreservationMode
    passed: bool
    vwapPreserved: bool
    maximumVwapPenetrationAtr: float
    barsClosedWrongSideOfVwap: int
    vwapReclaimed: bool
    vwapReclaimTimestamp: datetime | None
    confirmationCorrectSide: bool
    bearishStructureBelowVwap: bool
    reasonCodes: tuple[str, ...]


@dataclass(frozen=True)
class FirstPullbackRelativeVolumeEvidence:
    dataReady: bool
    impulseActualVolume: float
    impulseExpectedVolume: float | None
    impulseCumulativeRelativeVolume: float | None
    impulseAverageRelativeVolume: float | None
    pullbackActualVolume: float | None
    pullbackExpectedVolume: float | None
    pullbackAverageRelativeVolume: float | None
    confirmationActualVolume: float | None
    confirmationExpectedVolume: float | None
    confirmationRelativeVolume: float | None
    pullbackVolumeRatio: float | None
    reasonCodes: tuple[str, ...]


@dataclass(frozen=True)
class ConfidenceModel:
    impulseQuality: float
    establishedTrendQuality: float
    pullbackDepthAndStructure: float
    pullbackVolumeQuality: float
    vwapAnchorPreservation: float
    confirmationQuality: float
    timingAndDataQuality: float
    grossConfidence: float
    penalties: dict[str, float]
    finalConfidence: float
    minimumActionableConfidence: float
    actionable: bool


@dataclass(frozen=True)
class RegimeFitResult:
    score: float
    regimeKey: str
    trendStrength: float
    choppiness: float
    atrPercentile: float
    openingRangeExpansion: float
    gapState: float
    fiveMinuteStructure: float
    vwapCrossingFrequency: float
    economicEventRisk: float
    reasonCodes: tuple[str, ...]


@dataclass(frozen=True)
class ConfirmationQualityResult:
    passed: bool
    closeBeyondPullbackPivot: bool
    closeBeyondPreviousExtreme: bool
    closeAboveAnchor: bool
    vwapOk: bool
    closeLocation: float
    closeLocationOk: bool
    confirmationVolumeOk: bool
    rejectionWickFraction: float
    rejectionWickOk: bool
    rangeAtr: float
    rangeOk: bool
    vwapDistanceAtr: float | None
    ema20DistanceAtr: float | None
    extensionOk: bool
    nearestOpposingLevelDistanceAtr: float | None
    opposingLevelOk: bool
    reasonCodes: tuple[str, ...]


@dataclass(frozen=True)
class BarFinalization:
    barStartTimestamp: datetime
    barEndTimestamp: datetime
    wasFinalized: bool
    providerRevision: str | None


@dataclass(frozen=True)
class ImpulseCandidate:
    direction: Signal
    startIndex: int
    endIndex: int
    originPrice: float
    extremePrice: float
    atr: float


@dataclass(frozen=True)
class Pullback:
    startIndex: int
    endIndex: int
    pullbackExtreme: float
    averageVolume: float
    retracement: float
    pullbackStart: datetime
    pullbackEnd: datetime
    pullbackDuration: float
    countertrendCandleCount: int
    pauseCandleCount: int
    directionalEfficiency: float
    maximumRetracement: float
    averageCountertrendVolume: float
    classification: FirstPullbackClassification = FirstPullbackClassification.FORMING


@dataclass(frozen=True)
class SessionSeriesQuality:
    isComplete: bool
    isFresh: bool
    hasDuplicates: bool
    hasMissingIntervals: bool
    hasOutOfOrderBars: bool
    hasZeroVolumeBars: bool
    symbolMatches: bool
    timeframeMatches: bool
    sessionMatches: bool
    latestCompletedBarEnd: datetime | None
    qualityReasonCodes: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.qualityReasonCodes


@dataclass(frozen=True)
class StateMachineResult:
    state: FirstPullbackState
    signal: Signal
    confidence: float
    setupDetected: bool
    reasonCodes: list[str]
    explanation: str
    impulse: Impulse | None = None
    establishedTrend: EstablishedTrendResult | None = None
    vwapPolicy: VwapPolicyResult | None = None
    relativeVolume: FirstPullbackRelativeVolumeEvidence | None = None
    confirmationQuality: ConfirmationQualityResult | None = None
    pullback: Pullback | None = None
    confirmationBar: BarFinalization | None = None
    earliestExecutionTimestamp: datetime | None = None
    structuralInvalidationPrice: float | None = None
    invalidationLevels: InvalidationLevels | None = None
    confidenceModel: ConfidenceModel | None = None


@dataclass(frozen=True)
class FirstPullbackPersistentState:
    algorithmId: str
    strategyId: str
    symbol: str
    sessionDate: date
    setupId: str | None
    eventId: str | None
    state: str
    signalEmitted: bool
    signalEmittedAt: datetime | None
    signalConsumed: bool
    invalidationReason: str | None
    lastProcessedBarEnd: datetime | None


class FirstPullbackStateStore:
    def __init__(self) -> None:
        self._states: dict[tuple[str, str, str, date], FirstPullbackPersistentState] = {}

    def read(self, key: tuple[str, str, str, date]) -> FirstPullbackPersistentState | None:
        return self._states.get(key)

    def write(self, key: tuple[str, str, str, date], state: FirstPullbackPersistentState) -> None:
        self._states[key] = state

    def clear(self) -> None:
        self._states.clear()


class FirstPullbackAfterOpenStrategy:
    registryEntry = resolve_strategy("first_pullback_after_open")
    _stateStore = FirstPullbackStateStore()

    def __init__(self, config: FirstPullbackAfterOpenConfig | None = None) -> None:
        self.config = config or FirstPullbackAfterOpenConfig()

    @classmethod
    def reset_state_store(cls) -> None:
        cls._stateStore.clear()

    def evaluate(self, context: StrategyEvaluationContext) -> StrategySignal:
        required_features = self.required_feature_names()
        if not _strategy_required_data_ready(context.featureSnapshot):
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Strategy-required price data is not ready for first pullback after open.",
            )
        if not required_features_ready(context.featureSnapshot, required_features):
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Required feature measurements are unavailable for first pullback after open.",
            )

        raw_candles = context.featureSnapshot.rawInputs.get("spy1mCandles") or []
        five_minute_candles = context.featureSnapshot.rawInputs.get("spy5mCandles") or []
        quote = context.featureSnapshot.rawInputs.get("quote")
        finalization_lag_seconds = int(context.featureSnapshot.rawInputs.get("finalizationLagSeconds") or 0)
        exchange_session = _exchange_session_from_context(context)
        if not exchange_session.can_trade:
            signal = hold_signal(
                context,
                confidence=0.0,
                setupDetected=False,
                regimeFit=0.0,
                reliability=0.5,
                reasonCodes=[
                    "first_pullback.exchange_session_closed",
                    f"exchange_session:{exchange_session.sessionId}",
                ],
                explanation="HOLD because the official exchange calendar has no tradable session.",
                featureNames=required_features,
            )
            return signal.model_copy(
                update={
                    "features": {
                        **signal.features,
                        "firstPullbackExchangeSession": _exchange_session_payload(exchange_session),
                    }
                }
            )
        session_series_quality = _session_series_quality(raw_candles, context, finalization_lag_seconds, exchange_session)
        if not session_series_quality.passed:
            signal = hold_signal(
                context,
                confidence=0.0,
                setupDetected=False,
                regimeFit=0.0,
                reliability=0.5,
                reasonCodes=[
                    "first_pullback.session_series_quality_failed",
                    *session_series_quality.qualityReasonCodes,
                ],
                explanation="HOLD because the current-session 1-minute candle series failed quality checks.",
                featureNames=required_features,
            )
            return signal.model_copy(
                update={
                    "features": {
                        **signal.features,
                        "firstPullbackSessionSeriesQuality": _session_series_quality_payload(session_series_quality),
                    }
                }
            )
        setup_candles = _regular_session_candles(raw_candles, context, exchange_session)
        indicator_candles = _indicator_candles(raw_candles, context, include_premarket=self.config.includeApprovedPremarketInIndicatorWarmup, exchange_session=exchange_session)
        if len(setup_candles) < self.config.minImpulseCandles:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Insufficient completed regular-session 1-minute candles for first pullback after open.",
            )
        if len(indicator_candles) < self.config.atrLookbackCandles + self.config.minImpulseCandles:
            return unavailable_signal(
                context,
                requiredFeatureNames=required_features,
                explanation="Insufficient completed indicator warmup history for first pullback after open.",
            )

        result = self._run_state_machine(
            setup_candles,
            indicator_candles,
            context.sessionDate,
            five_minute_candles,
            context.evaluatedAt,
            finalization_lag_seconds,
            quote,
            exchange_session,
        )
        persistent_state = self._persistent_state(context, setup_candles, result)
        regime_fit = self._regime_fit(context, result)
        historical_reliability = self._historical_reliability(context, regime_fit)
        already_emitted = bool(persistent_state.signalConsumed and result.signal in {Signal.BUY, Signal.SELL})
        reason_codes = [*result.reasonCodes]
        if already_emitted:
            reason_codes.append("first_pullback.signal_already_emitted")
        if result.signal in {Signal.BUY, Signal.SELL}:
            meets_confidence = result.confidence >= self.config.minimumActionableConfidence
            actionable = not already_emitted and meets_confidence
            if not meets_confidence:
                reason_codes.append("first_pullback.confidence_below_actionable_minimum")
            signal = strategy_signal(
                context,
                signal=result.signal,
                confidence=result.confidence,
                eligible=actionable,
                setupDetected=True,
                regimeFit=regime_fit.score,
                reliability=historical_reliability.score,
                reasonCodes=reason_codes,
                explanation=result.explanation,
                featureNames=required_features,
                structuralInvalidationPrice=result.structuralInvalidationPrice,
            )
            signal = signal.model_copy(
                update={
                    "reliabilityVersion": historical_reliability.version,
                    "reliabilitySourceWindow": historical_reliability.sourceWindow,
                }
            )
            if actionable:
                self._stateStore.write(self._state_key(context, persistent_state.symbol), _consume_persistent_state(persistent_state))
            return self._with_state_machine_features(signal, result, persistent_state, regime_fit, historical_reliability, session_series_quality, exchange_session)

        signal = hold_signal(
            context,
            confidence=result.confidence,
            setupDetected=result.setupDetected,
            regimeFit=regime_fit.score,
            reliability=historical_reliability.score,
            reasonCodes=reason_codes,
            explanation=result.explanation,
            featureNames=required_features,
            structuralInvalidationPrice=result.structuralInvalidationPrice,
        )
        signal = signal.model_copy(
            update={
                "reliabilityVersion": historical_reliability.version,
                "reliabilitySourceWindow": historical_reliability.sourceWindow,
            }
        )
        if persistent_state.setupId is not None or persistent_state.invalidationReason is not None:
            self._stateStore.write(self._state_key(context, persistent_state.symbol), persistent_state)
        return self._with_state_machine_features(signal, result, persistent_state, regime_fit, historical_reliability, session_series_quality, exchange_session)

    def required_feature_names(self) -> tuple[str, ...]:
        return (
            "timeSinceMarketOpenMinutes",
        )

    def _state_key(self, context: StrategyEvaluationContext, symbol: str) -> tuple[str, str, str, date]:
        raw_inputs = context.featureSnapshot.rawInputs
        algorithm_id = str(raw_inputs.get("algorithmId") or raw_inputs.get("algorithm_id") or "voting_ensemble")
        return (algorithm_id, context.registryEntry.strategyId, symbol, context.sessionDate)

    def _persistent_state(
        self,
        context: StrategyEvaluationContext,
        candles: list[dict[str, Any]],
        result: StateMachineResult,
    ) -> FirstPullbackPersistentState:
        symbol = _symbol_from_context(context, candles)
        key = self._state_key(context, symbol)
        existing = self._stateStore.read(key) or _persistent_state_from_raw_inputs(context.featureSnapshot.rawInputs, key)
        setup_id = _setup_id(context, result)
        event_id = _event_id(context, result)
        signal_emitted = result.signal in {Signal.BUY, Signal.SELL}
        existing_same_setup = bool(existing and existing.setupId == setup_id)
        duplicate_signal = bool(
            signal_emitted
            and event_id
            and existing
            and existing.eventId == event_id
            and existing.signalEmitted
        )
        signal_emitted_at = context.evaluatedAt if signal_emitted else None
        invalidation_reason = _invalidation_reason(result)
        last_processed_bar_end = (
            result.confirmationBar.barEndTimestamp
            if result.confirmationBar is not None
            else _bar_end_timestamp(candles[-1])
            if candles
            else None
        )
        return FirstPullbackPersistentState(
            algorithmId=key[0],
            strategyId=key[1],
            symbol=symbol,
            sessionDate=key[3],
            setupId=setup_id,
            eventId=event_id or (existing.eventId if existing_same_setup and existing else None),
            state=result.state.value,
            signalEmitted=signal_emitted or bool(existing.signalEmitted if existing_same_setup and existing else False),
            signalEmittedAt=(existing.signalEmittedAt if (duplicate_signal or existing_same_setup) and existing else signal_emitted_at),
            signalConsumed=duplicate_signal or bool(existing.signalConsumed if existing_same_setup and existing else False),
            invalidationReason=invalidation_reason,
            lastProcessedBarEnd=last_processed_bar_end,
        )

    def _run_state_machine(
        self,
        candles: list[dict[str, Any]],
        indicator_candles: list[dict[str, Any]],
        session_date: date,
        five_minute_candles: list[dict[str, Any]] | None = None,
        evaluation_timestamp: datetime | None = None,
        finalization_lag_seconds: int = 0,
        quote: dict[str, Any] | None = None,
        exchange_session: ExchangeSession | None = None,
    ) -> StateMachineResult:
        exchange_session = exchange_session or EXCHANGE_CALENDAR.session_for_date(session_date)
        state = FirstPullbackState.WAITING_FOR_OPEN
        impulse: Impulse | None = None
        impulse_candidate: ImpulseCandidate | None = None
        established_trend: EstablishedTrendResult | None = None
        vwap_policy: VwapPolicyResult | None = None
        last_confirmation_quality: ConfirmationQualityResult | None = None
        pullback: Pullback | None = None
        pullback_start: int | None = None
        pullback_extreme: float | None = None
        pullback_qualified_index: int | None = None
        last_impulse_rejection_codes: list[str] = []
        pause_candle_count = 0
        pullback_volumes: list[float] = []
        atr_by_timestamp = _indicator_by_timestamp(
            indicator_candles,
            INDICATORS.atr_series(indicator_candles, self.config.atrLookbackCandles),
        )
        closes = INDICATORS.close_series(indicator_candles)
        ema9_by_timestamp = _indicator_by_timestamp(indicator_candles, INDICATORS.ema_series(closes, 9))
        ema20_series = INDICATORS.ema_series(closes, 20)
        ema20_by_timestamp = _indicator_by_timestamp(indicator_candles, ema20_series)
        ema20_previous_by_timestamp = _previous_indicator_by_timestamp(indicator_candles, ema20_series)
        vwap_series = INDICATORS.vwap_series(candles)
        relative_volume_service = PointInTimeRelativeVolumeService()
        volume_baseline = relative_volume_service.time_of_day_baseline(indicator_candles, session_date)
        five_minute_permission = _five_minute_permission_context(five_minute_candles or [], exchange_session)

        for index, candle in enumerate(candles):
            minute = _minutes_after_open(_timestamp(candle), exchange_session)
            if minute < self.config.sessionStartMinute:
                continue
            if minute > self.config.entryWindowEndMinute:
                break

            timestamp = _timestamp(candle)
            atr = atr_by_timestamp.get(timestamp)
            if atr is None:
                continue

            if state == FirstPullbackState.WAITING_FOR_OPEN:
                state = FirstPullbackState.WAITING_FOR_IMPULSE

            if state == FirstPullbackState.WAITING_FOR_IMPULSE:
                if minute > self.config.impulseWindowEndMinute:
                    return self._hold_no_impulse(last_impulse_rejection_codes)
                impulse_candidate, rejection_codes = self._detect_impulse_candidate(candles, atr_by_timestamp, relative_volume_service, volume_baseline, index, exchange_session)
                if rejection_codes:
                    last_impulse_rejection_codes = rejection_codes
                if impulse_candidate is None:
                    continue
                state = FirstPullbackState.IMPULSE_BUILDING
                continue

            if state == FirstPullbackState.IMPULSE_BUILDING and impulse_candidate is not None:
                if self._continues_impulse(candle, impulse_candidate):
                    impulse_candidate = self._update_impulse_candidate(impulse_candidate, candle, index, atr)
                    continue
                if self._begins_countertrend_episode(candle, impulse_candidate):
                    impulse = self._finalize_impulse(impulse_candidate, candles, relative_volume_service, volume_baseline, exchange_session)
                    if not impulse.quality.passed:
                        return self._impulse_quality_failed(impulse)
                    established_trend = self._established_trend(
                        candles,
                        index,
                        impulse,
                        vwap_series,
                        ema9_by_timestamp,
                        ema20_by_timestamp,
                        ema20_previous_by_timestamp,
                        five_minute_permission,
                    )
                    if not established_trend.established:
                        return self._trend_not_established(impulse, established_trend)
                    if index - impulse.endIndex > self.config.maximumBarsFromImpulseToPullback:
                        pullback = self._build_pullback(
                            candles,
                            impulse,
                            index,
                            index,
                            float(candle["low"] if impulse.direction == Signal.BUY else candle["high"]),
                            [float(candle["volume"])],
                            FirstPullbackClassification.EXPIRED,
                        )
                        return self._rejected(impulse, pullback, "first_pullback.expired", established_trend=established_trend, vwap_policy=vwap_policy)
                    pullback_start = index
                    pullback_extreme = float(candle["low"] if impulse.direction == Signal.BUY else candle["high"])
                    pullback_volumes = [float(candle["volume"])]
                    state = FirstPullbackState.PULLBACK_ACTIVE
                    continue
                else:
                    continue

            if state == FirstPullbackState.WAITING_FOR_FIRST_PULLBACK and impulse is not None:
                origin_violation = self._origin_violation(candles, index, impulse)
                if origin_violation.hardViolation:
                    return self._invalidated(impulse, "first_pullback.impulse_origin_broken", origin_violation=origin_violation)
                if self._moves_against_impulse(candle, impulse) and self._touches_pullback_zone(candle, impulse, atr, ema9_by_timestamp.get(timestamp), ema20_by_timestamp.get(timestamp), vwap_series[index]):
                    if index - impulse.endIndex > self.config.maximumBarsFromImpulseToPullback:
                        pullback = self._build_pullback(
                            candles,
                            impulse,
                            index,
                            index,
                            float(candle["low"] if impulse.direction == Signal.BUY else candle["high"]),
                            [float(candle["volume"])],
                            FirstPullbackClassification.EXPIRED,
                        )
                        return self._rejected(impulse, pullback, "first_pullback.expired", established_trend=established_trend, vwap_policy=vwap_policy)
                    pullback_start = index
                    pullback_extreme = float(candle["low"] if impulse.direction == Signal.BUY else candle["high"])
                    pullback_volumes = [float(candle["volume"])]
                    state = FirstPullbackState.PULLBACK_ACTIVE
                continue

            if state == FirstPullbackState.PULLBACK_ACTIVE and impulse is not None:
                origin_violation = self._origin_violation(candles, index, impulse)
                if origin_violation.hardViolation:
                    pullback = self._build_pullback(
                        candles,
                        impulse,
                        pullback_start,
                        max(index, pullback_start or index),
                        pullback_extreme,
                        [*pullback_volumes, float(candle["volume"])],
                        FirstPullbackClassification.TREND_REVERSAL,
                    )
                    return self._rejected(impulse, pullback, "first_pullback.trend_reversal", established_trend=established_trend, vwap_policy=vwap_policy, extra_reason="first_pullback.impulse_origin_broken", origin_violation=origin_violation)
                if pullback_start is None or pullback_extreme is None:
                    return self._invalidated(impulse, "first_pullback.state_malformed")
                if self._moves_against_impulse(candle, impulse):
                    current_extreme = float(candle["low"] if impulse.direction == Signal.BUY else candle["high"])
                    if impulse.direction == Signal.BUY:
                        pullback_extreme = min(pullback_extreme, current_extreme)
                    else:
                        pullback_extreme = max(pullback_extreme, current_extreme)
                    pullback_volumes.append(float(candle["volume"]))
                    pullback = self._build_pullback(
                        candles,
                        impulse,
                        pullback_start,
                        index,
                        pullback_extreme,
                        pullback_volumes,
                        FirstPullbackClassification.FORMING,
                    )
                    depth_state = self._pullback_depth_state(pullback)
                    if depth_state == FirstPullbackClassification.TOO_DEEP:
                        rejected = self._with_pullback_classification(pullback, FirstPullbackClassification.TOO_DEEP)
                        return self._rejected(impulse, rejected, "first_pullback.too_deep", established_trend=established_trend, vwap_policy=vwap_policy)
                    if depth_state == FirstPullbackClassification.QUALIFIED and pullback_qualified_index is None:
                        pullback_qualified_index = index
                    if self._pullback_window_expired(candles, impulse, pullback, pullback_qualified_index, index):
                        expired = self._with_pullback_classification(pullback, FirstPullbackClassification.EXPIRED)
                        return self._rejected(impulse, expired, "first_pullback.expired", established_trend=established_trend, vwap_policy=vwap_policy)
                    continue
                pullback = self._build_pullback(
                    candles,
                    impulse,
                    pullback_start,
                    index - 1,
                    pullback_extreme,
                    pullback_volumes,
                    FirstPullbackClassification.FORMING,
                )
                depth_state = self._pullback_depth_state(pullback)
                if depth_state == FirstPullbackClassification.TOO_DEEP:
                    pullback = self._with_pullback_classification(pullback, FirstPullbackClassification.TOO_DEEP)
                    return self._rejected(impulse, pullback, "first_pullback.too_deep", established_trend=established_trend, vwap_policy=vwap_policy)
                if depth_state == FirstPullbackClassification.FORMING:
                    classification = FirstPullbackClassification.TOO_SHALLOW
                else:
                    classification = self._classify_pullback(impulse, pullback, candles, relative_volume_service, volume_baseline)
                pullback = self._with_pullback_classification(pullback, classification)
                if classification != FirstPullbackClassification.QUALIFIED:
                    return self._rejected(impulse, pullback, f"first_pullback.{classification.value}", established_trend=established_trend, vwap_policy=vwap_policy)
                if pullback_qualified_index is None:
                    pullback_qualified_index = pullback.endIndex
                state = FirstPullbackState.CONFIRMATION_CANDIDATE

            if state in {FirstPullbackState.PULLBACK_DECELERATING, FirstPullbackState.CONFIRMATION_CANDIDATE, FirstPullbackState.WAITING_FOR_CONFIRMATION} and impulse is not None:
                origin_violation = self._origin_violation(candles, index, impulse)
                if origin_violation.hardViolation:
                    pullback = self._with_pullback_classification(pullback, FirstPullbackClassification.TREND_REVERSAL) if pullback else None
                    return self._rejected(impulse, pullback, "first_pullback.trend_reversal", established_trend=established_trend, vwap_policy=vwap_policy, extra_reason="first_pullback.impulse_origin_broken", origin_violation=origin_violation)

                if pullback_start is None or pullback_extreme is None or pullback is None:
                    return self._invalidated(impulse, "first_pullback.state_malformed")

                qualification_index = pullback_qualified_index if pullback_qualified_index is not None else pullback.endIndex
                if index - qualification_index > self.config.maximumBarsFromQualificationToConfirmation:
                    expired = self._with_pullback_classification(pullback, FirstPullbackClassification.EXPIRED)
                    return self._rejected(impulse, expired, "first_pullback.expired", established_trend=established_trend, vwap_policy=vwap_policy)
                confirmation_quality = self._confirmation_quality(
                    candles,
                    index,
                    impulse,
                    pullback,
                    atr,
                    ema9_by_timestamp.get(timestamp),
                    ema20_by_timestamp.get(timestamp),
                    vwap_series[index] if index < len(vwap_series) else None,
                )
                if confirmation_quality.passed:
                    confirmation_bar = _bar_finalization(candles[index], evaluation_timestamp, finalization_lag_seconds)
                    if not confirmation_bar.wasFinalized:
                        return self._hold_waiting_for_confirmation(impulse, pullback, established_trend, vwap_policy)
                    pullback = self._with_pullback_classification(pullback, FirstPullbackClassification.CONFIRMED)
                    relative_volume = self._relative_volume_evidence(
                        candles,
                        impulse,
                        pullback,
                        index,
                        relative_volume_service,
                        volume_baseline,
                    )
                    vwap_policy = self._vwap_policy(candles, pullback, index, impulse, vwap_series, atr_by_timestamp)
                    if not vwap_policy.passed:
                        return self._rejected(
                            impulse,
                            pullback,
                            "first_pullback.vwap_not_preserved",
                            established_trend=established_trend,
                            vwap_policy=vwap_policy,
                            relative_volume=relative_volume,
                            confirmation_quality=confirmation_quality,
                        )
                    if index < len(candles) - 1:
                        return self._hold_completed(impulse, pullback, established_trend, vwap_policy, confirmation_bar, relative_volume, confirmation_quality)
                    return self._completed(
                        impulse,
                        pullback,
                        established_trend,
                        vwap_policy,
                            confirmation_bar,
                            evaluation_timestamp,
                            relative_volume,
                            confirmation_quality,
                            quote,
                            exchange_session,
                        )
                pause_candle_count += 1
                pullback = self._with_pullback_pause(pullback, pause_candle_count)
                attempted_confirmation = confirmation_quality.closeBeyondPullbackPivot or confirmation_quality.closeBeyondPreviousExtreme
                if attempted_confirmation:
                    pullback = self._with_pullback_classification(pullback, FirstPullbackClassification.EXPIRED)
                    return self._rejected(
                        impulse,
                        pullback,
                        "first_pullback.confirmation_failed",
                        established_trend=established_trend,
                        vwap_policy=vwap_policy,
                        confirmation_quality=confirmation_quality,
                    )
                state = FirstPullbackState.CONFIRMATION_FAILED if attempted_confirmation else FirstPullbackState.PULLBACK_DECELERATING

        if state in {FirstPullbackState.WAITING_FOR_OPEN, FirstPullbackState.WAITING_FOR_IMPULSE}:
            return self._hold_no_impulse(last_impulse_rejection_codes)
        if state in {FirstPullbackState.IMPULSE_BUILDING, FirstPullbackState.WAITING_FOR_FIRST_PULLBACK}:
            return self._hold_waiting_for_pullback(impulse, established_trend, vwap_policy)
        if state == FirstPullbackState.PULLBACK_ACTIVE:
            pullback = self._build_pullback(
                candles,
                impulse,
                pullback_start,
                len(candles) - 1,
                pullback_extreme,
                pullback_volumes,
                FirstPullbackClassification.FORMING,
                pause_candle_count,
            )
        return self._hold_waiting_for_confirmation(impulse, pullback, established_trend, vwap_policy, state=state, confirmation_quality=last_confirmation_quality)

    def _with_state_machine_features(
        self,
        signal: StrategySignal,
        result: StateMachineResult,
        persistent_state: FirstPullbackPersistentState,
        regime_fit: RegimeFitResult,
        historical_reliability: StrategyReliabilityEstimate,
        session_series_quality: SessionSeriesQuality,
        exchange_session: ExchangeSession,
    ) -> StrategySignal:
        features = {
            **signal.features,
            "setupConfidence": result.confidence,
            "firstPullbackState": result.state.value,
            "firstPullbackPersistentState": _persistent_state_payload(persistent_state),
            "firstPullbackImpulse": _impulse_payload(result.impulse),
            "firstPullbackEstablishedTrend": _established_trend_payload(result.establishedTrend),
            "firstPullbackVwapPolicy": _vwap_policy_payload(result.vwapPolicy),
            "firstPullbackRelativeVolume": _relative_volume_payload(result.relativeVolume),
            "firstPullbackConfirmationQuality": _confirmation_quality_payload(result.confirmationQuality),
            "firstPullback": _pullback_payload(result.pullback),
            "firstPullbackConfirmationBar": _bar_finalization_payload(result.confirmationBar),
            "firstPullbackExecution": _execution_payload(result),
            "firstPullbackInvalidationLevels": _invalidation_levels_payload(result.invalidationLevels),
            "firstPullbackConfidence": _confidence_model_payload(result.confidenceModel),
            "firstPullbackRegimeFit": _regime_fit_payload(regime_fit),
            "firstPullbackHistoricalReliability": _historical_reliability_payload(historical_reliability),
            "firstPullbackSessionSeriesQuality": _session_series_quality_payload(session_series_quality),
            "firstPullbackExchangeSession": _exchange_session_payload(exchange_session),
        }
        return signal.model_copy(update={"features": features})

    def _detect_impulse_candidate(
        self,
        candles: list[dict[str, Any]],
        atr_by_timestamp: dict[datetime, float | None],
        relative_volume_service: PointInTimeRelativeVolumeService,
        volume_baseline: dict[int, float],
        end_index: int,
        exchange_session: ExchangeSession,
    ) -> tuple[ImpulseCandidate | None, list[str]]:
        rejection_codes: list[str] = []
        for length in range(self.config.minImpulseCandles, self.config.maxImpulseCandles + 1):
            start_index = end_index - length + 1
            if start_index < 0:
                continue
            start = candles[start_index]
            end = candles[end_index]
            start_minute = _minutes_after_open(_timestamp(start), exchange_session)
            if start_minute > self.config.maxImpulseStartMinute:
                rejection_codes = ["impulse_quality.started_too_late"]
                continue
            atr = atr_by_timestamp.get(_timestamp(end))
            if atr is None:
                continue
            origin = float(start["open"])
            close = float(end["close"])
            highest_high = max(float(candle["high"]) for candle in candles[start_index : end_index + 1])
            lowest_low = min(float(candle["low"]) for candle in candles[start_index : end_index + 1])
            direction = Signal.BUY if close > origin else Signal.SELL if close < origin else Signal.HOLD
            if direction == Signal.HOLD:
                continue
            extreme = highest_high if direction == Signal.BUY else lowest_low
            displacement = abs(close - origin)
            displacement_percent = displacement / origin if origin else 0
            displacement_atr = displacement / atr if atr else 0
            structure_break = abs(extreme - origin) / atr if atr else 0
            relative_volume_window = relative_volume_service.measure_window(
                candles,
                start_index=start_index,
                end_index=end_index,
                baseline=volume_baseline,
            )
            relative_volume = relative_volume_window.cumulativeRelativeVolume

            if displacement_atr < self.config.minImpulseAtrMultiple:
                continue
            if displacement_percent < self.config.minImpulsePercent:
                continue
            if structure_break < self.config.minImpulseStructureBreakAtr:
                continue
            if (
                self.config.requireRelativeVolumeWhenAvailable
                and relative_volume is not None
                and relative_volume < self.config.minRelativeVolume
            ):
                continue

            if not _impulse_structure_ok(candles, start_index, end_index, direction):
                continue
            quality = self._impulse_quality(candles, start_index, end_index, direction, exchange_session)
            if not quality.passed:
                rejection_codes = list(quality.reasonCodes)
                continue

            return ImpulseCandidate(
                direction=direction,
                startIndex=start_index,
                endIndex=end_index,
                originPrice=origin,
                extremePrice=extreme,
                atr=atr,
            ), []
        return None, rejection_codes

    def _continues_impulse(self, candle: dict[str, Any], impulse: ImpulseCandidate) -> bool:
        open_price = float(candle["open"])
        close = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        if impulse.direction == Signal.BUY:
            return close >= open_price and high >= impulse.extremePrice
        return close <= open_price and low <= impulse.extremePrice

    def _begins_countertrend_episode(self, candle: dict[str, Any], impulse: ImpulseCandidate) -> bool:
        open_price = float(candle["open"])
        close = float(candle["close"])
        if impulse.direction == Signal.BUY:
            return close < open_price
        return close > open_price

    def _update_impulse_candidate(
        self,
        impulse: ImpulseCandidate,
        candle: dict[str, Any],
        index: int,
        atr: float,
    ) -> ImpulseCandidate:
        extreme = max(impulse.extremePrice, float(candle["high"])) if impulse.direction == Signal.BUY else min(impulse.extremePrice, float(candle["low"]))
        return ImpulseCandidate(
            direction=impulse.direction,
            startIndex=impulse.startIndex,
            endIndex=index,
            originPrice=impulse.originPrice,
            extremePrice=extreme,
            atr=atr,
        )

    def _finalize_impulse(
        self,
        impulse: ImpulseCandidate,
        candles: list[dict[str, Any]],
        relative_volume_service: PointInTimeRelativeVolumeService,
        volume_baseline: dict[int, float],
        exchange_session: ExchangeSession,
    ) -> Impulse:
        impulse_candles = candles[impulse.startIndex : impulse.endIndex + 1]
        close = float(impulse_candles[-1]["close"])
        displacement = abs(close - impulse.originPrice)
        quality = self._impulse_quality(candles, impulse.startIndex, impulse.endIndex, impulse.direction, exchange_session)
        relative_volume = relative_volume_service.measure_window(
            candles,
            start_index=impulse.startIndex,
            end_index=impulse.endIndex,
            baseline=volume_baseline,
        )
        return Impulse(
            direction=impulse.direction,
            startIndex=impulse.startIndex,
            endIndex=impulse.endIndex,
            startTimestamp=_timestamp(impulse_candles[0]),
            endTimestamp=_bar_end_timestamp(impulse_candles[-1]),
            originPrice=impulse.originPrice,
            extremePrice=impulse.extremePrice,
            atr=impulse.atr,
            displacementAtr=displacement / impulse.atr if impulse.atr else 0,
            displacementPercent=displacement / impulse.originPrice if impulse.originPrice else 0,
            relativeVolume=relative_volume.cumulativeRelativeVolume,
            averageRelativeVolume=relative_volume.averageRelativeVolume,
            averageVolume=mean(float(candle["volume"]) for candle in impulse_candles),
            quality=quality,
        )

    def _impulse_quality(
        self,
        candles: list[dict[str, Any]],
        start_index: int,
        end_index: int,
        direction: Signal,
        exchange_session: ExchangeSession,
    ) -> ImpulseQualityResult:
        selected = candles[start_index : end_index + 1]
        directional_count = _directional_closes(candles, start_index, end_index, direction)
        directional_ratio = directional_count / len(selected) if selected else 0.0
        body_to_range_values = [_body_to_range_ratio(candle) for candle in selected]
        average_body_to_range = mean(body_to_range_values) if body_to_range_values else 0.0
        close_location = _impulse_range_close_location(selected, direction)
        efficiency = _directional_efficiency(candles, start_index, end_index)
        internal_retracement = _maximum_internal_retracement(selected, direction)
        opposing_candles = [candle for candle in selected if _opposes_direction(candle, direction)]
        directional_candles = [candle for candle in selected if _supports_direction(candle, direction)]
        opposing_count = len(opposing_candles)
        directional_volume = mean([float(candle["volume"]) for candle in directional_candles]) if directional_candles else None
        opposing_volume = mean([float(candle["volume"]) for candle in opposing_candles]) if opposing_candles else None
        opposing_volume_ratio = (
            opposing_volume / directional_volume
            if opposing_volume is not None and directional_volume is not None and directional_volume > 0
            else None
        )
        start_minute = _minutes_after_open(_timestamp(selected[0]), exchange_session) if selected else 0.0
        reason_codes: list[str] = []
        if start_minute > self.config.maxImpulseStartMinute:
            reason_codes.append("impulse_quality.started_too_late")
        if directional_ratio < self.config.minimumDirectionalCandleRatio:
            reason_codes.append("impulse_quality.directional_candle_ratio_failed")
        if average_body_to_range < self.config.minimumBodyToRangeRatio:
            reason_codes.append("impulse_quality.body_to_range_failed")
        if close_location < self.config.minimumImpulseCloseLocation:
            reason_codes.append("impulse_quality.close_location_failed")
        if efficiency < self.config.minimumEfficiencyRatio:
            reason_codes.append("impulse_quality.efficiency_failed")
        if internal_retracement > self.config.maximumInternalRetracement:
            reason_codes.append("impulse_quality.internal_retracement_failed")
        if opposing_count > self.config.maximumOpposingCandleCount:
            reason_codes.append("impulse_quality.too_many_opposing_candles")
        if opposing_volume_ratio is not None and opposing_volume_ratio > self.config.maximumOpposingVolumeRatio:
            reason_codes.append("impulse_quality.opposing_volume_failed")
        return ImpulseQualityResult(
            passed=not reason_codes,
            directionalCandleRatio=round(directional_ratio, 4),
            averageBodyToRangeRatio=round(average_body_to_range, 4),
            impulseCloseLocation=round(close_location, 4),
            efficiencyRatio=round(efficiency, 4),
            maximumInternalRetracement=round(internal_retracement, 4),
            opposingCandleCount=opposing_count,
            opposingVolumeRatio=round(opposing_volume_ratio, 4) if opposing_volume_ratio is not None else None,
            startMinute=round(start_minute, 4),
            reasonCodes=tuple(reason_codes),
        )

    def _established_trend(
        self,
        candles: list[dict[str, Any]],
        countertrend_index: int,
        impulse: Impulse,
        vwap_series: list[float | None],
        ema9_by_timestamp: dict[datetime, float | None],
        ema20_by_timestamp: dict[datetime, float | None],
        ema20_previous_by_timestamp: dict[datetime, float | None],
        five_minute_permission: dict[str, bool],
    ) -> EstablishedTrendResult:
        end_candle = candles[impulse.endIndex]
        countertrend_candle = candles[countertrend_index]
        end_timestamp = _timestamp(end_candle)
        final_close = float(end_candle["close"])
        vwap = vwap_series[impulse.endIndex] if impulse.endIndex < len(vwap_series) else None
        ema9 = ema9_by_timestamp.get(end_timestamp)
        ema20 = ema20_by_timestamp.get(end_timestamp)
        previous_ema20 = ema20_previous_by_timestamp.get(end_timestamp)
        direction = impulse.direction
        direction_sign = 1 if direction == Signal.BUY else -1
        price_vwap_ok = vwap is not None and ((final_close > vwap) if direction == Signal.BUY else (final_close < vwap))
        ema_relationship_ok = ema9 is not None and ema20 is not None and ((ema9 > ema20) if direction == Signal.BUY else (ema9 < ema20))
        ema20_slope_ok = ema20 is not None and previous_ema20 is not None and ((ema20 >= previous_ema20) if direction == Signal.BUY else (ema20 <= previous_ema20))
        structure_ok = _impulse_structure_ok(candles, impulse.startIndex, impulse.endIndex, direction)
        directional_efficiency = _directional_efficiency(candles, impulse.startIndex, impulse.endIndex)
        directional_closes = _directional_closes(candles, impulse.startIndex, impulse.endIndex, direction)
        close_location = _impulse_close_location(end_candle, direction)
        immediate_retracement = self._retracement(
            impulse,
            float(countertrend_candle["low"] if direction == Signal.BUY else countertrend_candle["high"]),
        )
        origin_violation = self._origin_violation(candles, countertrend_index, impulse)
        no_full_retracement = immediate_retracement <= self.config.maxImmediateRetracement
        five_minute_ok: bool | None = None
        if self.config.requireFiveMinutePermission:
            key = "bullish" if direction == Signal.BUY else "bearish"
            five_minute_ok = five_minute_permission.get(key, False)
        efficiency_ok = directional_efficiency >= self.config.minDirectionalEfficiency
        directional_closes_ok = directional_closes >= self.config.minDirectionalCloses
        close_location_ok = close_location >= self.config.minImpulseCloseLocation
        checks = [
            price_vwap_ok,
            ema_relationship_ok,
            ema20_slope_ok,
            structure_ok,
            efficiency_ok,
            directional_closes_ok,
            close_location_ok,
            no_full_retracement,
        ]
        if five_minute_ok is not None:
            checks.append(five_minute_ok)
        score = sum(1 for item in checks if item) / len(checks)
        hard_requirements_met = (
            price_vwap_ok
            and ema_relationship_ok
            and ema20_slope_ok
            and structure_ok
            and efficiency_ok
            and directional_closes_ok
            and close_location_ok
            and no_full_retracement
            and five_minute_ok is not False
        )
        reason_codes: list[str] = []
        if not price_vwap_ok:
            reason_codes.append("established_trend.price_vwap_failed")
        if not ema_relationship_ok:
            reason_codes.append("established_trend.ema_relationship_failed")
        if not ema20_slope_ok:
            reason_codes.append("established_trend.ema20_slope_failed")
        if not structure_ok:
            reason_codes.append("established_trend.structure_failed")
        if directional_efficiency < self.config.minDirectionalEfficiency:
            reason_codes.append("established_trend.directional_efficiency_failed")
        if directional_closes < self.config.minDirectionalCloses:
            reason_codes.append("established_trend.directional_closes_failed")
        if close_location < self.config.minImpulseCloseLocation:
            reason_codes.append("established_trend.close_location_failed")
        if not no_full_retracement:
            reason_codes.append("established_trend.immediate_retracement_failed")
            reason_codes.extend(origin_violation.reasonCodes)
            if origin_violation.hardViolation:
                reason_codes.extend(["first_pullback.trend_reversal", "first_pullback.impulse_origin_broken"])
        if five_minute_ok is False:
            reason_codes.append("established_trend.five_minute_permission_failed")
        return EstablishedTrendResult(
            established=hard_requirements_met and score >= self.config.minEstablishedTrendScore,
            direction=direction,
            score=round(score, 4),
            priceVwapOk=price_vwap_ok,
            emaRelationshipOk=ema_relationship_ok,
            ema20SlopeOk=ema20_slope_ok,
            structureOk=structure_ok,
            directionalEfficiency=round(directional_efficiency, 4),
            directionalCloses=directional_closes,
            impulseCloseLocation=round(close_location, 4),
            immediateRetracement=round(immediate_retracement, 4),
            fiveMinutePermissionOk=five_minute_ok,
            originViolation=origin_violation,
            reasonCodes=tuple(reason_codes),
        )

    def _impulse_quality_failed(self, impulse: Impulse) -> StateMachineResult:
        invalidation_levels = self._invalidation_levels(impulse)
        return StateMachineResult(
            state=FirstPullbackState.WAITING_FOR_IMPULSE,
            signal=Signal.HOLD,
            confidence=0.0,
            setupDetected=False,
            reasonCodes=[
                "first_pullback.no_opening_impulse",
                "first_pullback.impulse_quality_failed",
                f"state:{FirstPullbackState.WAITING_FOR_IMPULSE.value}",
                *impulse.quality.reasonCodes,
            ],
            explanation="HOLD because the detected opening move did not meet impulse-quality requirements.",
            impulse=impulse,
            invalidationLevels=invalidation_levels,
        )

    def _trend_not_established(self, impulse: Impulse, established_trend: EstablishedTrendResult) -> StateMachineResult:
        structural_failure_codes: list[str] = []
        if "first_pullback.trend_reversal" in established_trend.reasonCodes:
            structural_failure_codes.extend(["pullback:trend_reversal", "first_pullback.session_locked"])
        invalidation_levels = self._invalidation_levels(impulse, origin_violation=established_trend.originViolation)
        return StateMachineResult(
            state=FirstPullbackState.FIRST_PULLBACK_REJECTED,
            signal=Signal.HOLD,
            confidence=0.0,
            setupDetected=True,
            reasonCodes=[
                "first_pullback.trend_not_established",
                f"state:{FirstPullbackState.FIRST_PULLBACK_REJECTED.value}",
                *established_trend.reasonCodes,
                *structural_failure_codes,
            ],
            explanation="HOLD because the opening impulse did not establish a reliable trend before the first pullback.",
            impulse=impulse,
            establishedTrend=established_trend,
            structuralInvalidationPrice=invalidation_levels.entryInvalidation.level if invalidation_levels else None,
            invalidationLevels=invalidation_levels,
        )

    def _build_pullback(
        self,
        candles: list[dict[str, Any]],
        impulse: Impulse | None,
        start_index: int | None,
        end_index: int,
        pullback_extreme: float | None,
        volumes: list[float],
        classification: FirstPullbackClassification,
        pause_candle_count: int = 0,
    ) -> Pullback | None:
        if impulse is None or start_index is None or pullback_extreme is None:
            return None
        pullback_start = _timestamp(candles[start_index])
        pullback_end = _bar_end_timestamp(candles[end_index])
        duration = max(0.0, (pullback_end - pullback_start).total_seconds() / 60)
        retracement = self._retracement(impulse, pullback_extreme)
        return Pullback(
            startIndex=start_index,
            endIndex=end_index,
            pullbackExtreme=pullback_extreme,
            averageVolume=mean(volumes) if volumes else 0.0,
            retracement=retracement,
            pullbackStart=pullback_start,
            pullbackEnd=pullback_end,
            pullbackDuration=duration,
            countertrendCandleCount=len(volumes),
            pauseCandleCount=pause_candle_count,
            directionalEfficiency=_directional_efficiency(candles, start_index, end_index),
            maximumRetracement=retracement,
            averageCountertrendVolume=mean(volumes) if volumes else 0.0,
            classification=classification,
        )

    def _classify_pullback(
        self,
        impulse: Impulse,
        pullback: Pullback,
        candles: list[dict[str, Any]],
        relative_volume_service: PointInTimeRelativeVolumeService,
        volume_baseline: dict[int, float],
    ) -> FirstPullbackClassification:
        volume_ratio = self._pullback_volume_ratio(candles, impulse, pullback, relative_volume_service, volume_baseline)
        if volume_ratio is None and self.config.relativeVolumeEvidenceMode == RelativeVolumeEvidenceMode.STRICT:
            return FirstPullbackClassification.VOLUME_UNAVAILABLE
        if self.config.requireReducedPullbackVolume and volume_ratio is not None and volume_ratio > self.config.maxPullbackVolumeRatio:
            return FirstPullbackClassification.TOO_HIGH_VOLUME
        if pullback.retracement < self.config.pullbackRetracementMin:
            return FirstPullbackClassification.TOO_SHALLOW
        if pullback.retracement > self.config.pullbackRetracementMax:
            return FirstPullbackClassification.TOO_DEEP
        return FirstPullbackClassification.QUALIFIED

    def _pullback_depth_state(self, pullback: Pullback | None) -> FirstPullbackClassification:
        if pullback is None:
            return FirstPullbackClassification.FORMING
        if pullback.retracement < self.config.pullbackRetracementMin:
            return FirstPullbackClassification.FORMING
        if pullback.retracement <= self.config.pullbackRetracementMax:
            return FirstPullbackClassification.QUALIFIED
        return FirstPullbackClassification.TOO_DEEP

    def _pullback_window_expired(
        self,
        candles: list[dict[str, Any]],
        impulse: Impulse,
        pullback: Pullback | None,
        qualified_index: int | None,
        current_index: int,
    ) -> bool:
        if pullback is None:
            return False
        if pullback.startIndex - impulse.endIndex > self.config.maximumBarsFromImpulseToPullback:
            return True
        if current_index - pullback.startIndex + 1 > self.config.maximumPullbackBars:
            return True
        start = _timestamp(candles[pullback.startIndex])
        current = _timestamp(candles[current_index])
        if (current - start).total_seconds() / 60 > self.config.maximumPullbackMinutes:
            return True
        if qualified_index is not None and current_index - qualified_index > self.config.maximumBarsFromQualificationToConfirmation:
            return True
        return False

    def _pullback_volume_ratio(
        self,
        candles: list[dict[str, Any]],
        impulse: Impulse,
        pullback: Pullback,
        relative_volume_service: PointInTimeRelativeVolumeService,
        volume_baseline: dict[int, float],
    ) -> float | None:
        impulse_volume = relative_volume_service.measure_window(
            candles,
            start_index=impulse.startIndex,
            end_index=impulse.endIndex,
            baseline=volume_baseline,
        )
        pullback_volume = relative_volume_service.measure_window(
            candles,
            start_index=pullback.startIndex,
            end_index=pullback.endIndex,
            baseline=volume_baseline,
        )
        if not impulse_volume.dataReady or not pullback_volume.dataReady:
            return None
        if not impulse_volume.cumulativeRelativeVolume or not pullback_volume.cumulativeRelativeVolume:
            return None
        return pullback_volume.cumulativeRelativeVolume / impulse_volume.cumulativeRelativeVolume

    def _relative_volume_evidence(
        self,
        candles: list[dict[str, Any]],
        impulse: Impulse,
        pullback: Pullback,
        confirmation_index: int,
        relative_volume_service: PointInTimeRelativeVolumeService,
        volume_baseline: dict[int, float],
    ) -> FirstPullbackRelativeVolumeEvidence:
        impulse_volume = relative_volume_service.measure_window(
            candles,
            start_index=impulse.startIndex,
            end_index=impulse.endIndex,
            baseline=volume_baseline,
        )
        pullback_volume = relative_volume_service.measure_window(
            candles,
            start_index=pullback.startIndex,
            end_index=pullback.endIndex,
            baseline=volume_baseline,
        )
        confirmation_volume = relative_volume_service.measure_window(
            candles,
            start_index=confirmation_index,
            end_index=confirmation_index,
            baseline=volume_baseline,
        )
        ratio = None
        if (
            impulse_volume.dataReady
            and pullback_volume.dataReady
            and impulse_volume.cumulativeRelativeVolume
            and pullback_volume.cumulativeRelativeVolume
        ):
            ratio = pullback_volume.cumulativeRelativeVolume / impulse_volume.cumulativeRelativeVolume
        windows = (impulse_volume, pullback_volume, confirmation_volume)
        reason_codes = tuple(reason for window in windows for reason in window.reasonCodes)
        return FirstPullbackRelativeVolumeEvidence(
            dataReady=all(window.dataReady for window in windows),
            impulseActualVolume=impulse_volume.actualVolume,
            impulseExpectedVolume=impulse_volume.expectedVolume,
            impulseCumulativeRelativeVolume=impulse_volume.cumulativeRelativeVolume,
            impulseAverageRelativeVolume=impulse_volume.averageRelativeVolume,
            pullbackActualVolume=pullback_volume.actualVolume,
            pullbackExpectedVolume=pullback_volume.expectedVolume,
            pullbackAverageRelativeVolume=pullback_volume.averageRelativeVolume,
            confirmationActualVolume=confirmation_volume.actualVolume,
            confirmationExpectedVolume=confirmation_volume.expectedVolume,
            confirmationRelativeVolume=confirmation_volume.cumulativeRelativeVolume,
            pullbackVolumeRatio=ratio,
            reasonCodes=reason_codes,
        )

    def _vwap_policy(
        self,
        candles: list[dict[str, Any]],
        pullback: Pullback,
        confirmation_index: int,
        impulse: Impulse,
        vwap_series: list[float | None],
        atr_by_timestamp: dict[datetime, float | None],
    ) -> VwapPolicyResult:
        window = candles[pullback.startIndex : confirmation_index + 1]
        maximum_penetration_atr = 0.0
        wrong_side_closes = 0
        first_wrong_side_index: int | None = None
        reclaim_timestamp: datetime | None = None

        for offset, candle in enumerate(window):
            candle_index = pullback.startIndex + offset
            vwap = vwap_series[candle_index] if candle_index < len(vwap_series) else None
            if vwap is None:
                continue
            atr = atr_by_timestamp.get(_timestamp(candle)) or impulse.atr
            penetration = _vwap_penetration_atr(candle, vwap, atr, impulse.direction)
            maximum_penetration_atr = max(maximum_penetration_atr, penetration)
            if _vwap_close_wrong_side(candle, vwap, impulse.direction):
                wrong_side_closes += 1
                if first_wrong_side_index is None:
                    first_wrong_side_index = offset
            elif first_wrong_side_index is not None and reclaim_timestamp is None:
                reclaim_timestamp = _timestamp(candle)

        confirmation_vwap = vwap_series[confirmation_index] if confirmation_index < len(vwap_series) else None
        confirmation_correct_side = confirmation_vwap is not None and not _vwap_close_wrong_side(candles[confirmation_index], confirmation_vwap, impulse.direction)
        if wrong_side_closes == 0:
            vwap_reclaimed = True
        else:
            vwap_reclaimed = reclaim_timestamp is not None

        bars_to_reclaim = None
        if first_wrong_side_index is not None and reclaim_timestamp is not None:
            reclaim_index = next(
                (
                    offset
                    for offset, candle in enumerate(window)
                    if _timestamp(candle) == reclaim_timestamp
                ),
                None,
            )
            if reclaim_index is not None:
                bars_to_reclaim = reclaim_index - first_wrong_side_index

        wrong_side_structure = _wrong_side_vwap_structure(window, vwap_series[pullback.startIndex : confirmation_index + 1], impulse.direction)
        strict_preserved = (
            maximum_penetration_atr <= self.config.vwapPenetrationToleranceAtr
            and wrong_side_closes <= self.config.maxVwapWrongSideClosesStrict
            and confirmation_correct_side
        )
        moderate_preserved = (
            confirmation_correct_side
            and not wrong_side_structure
            and (
                wrong_side_closes == 0
                or (
                    vwap_reclaimed
                    and bars_to_reclaim is not None
                    and bars_to_reclaim <= self.config.vwapReclaimBarsModerate
                )
            )
        )

        if self.config.vwapPreservationMode == VwapPreservationMode.STRICT:
            passed = strict_preserved
            vwap_preserved = strict_preserved
        elif self.config.vwapPreservationMode == VwapPreservationMode.MODERATE:
            passed = moderate_preserved
            vwap_preserved = moderate_preserved
        else:
            passed = True
            vwap_preserved = strict_preserved

        reason_codes: list[str] = [f"vwap_policy:{self.config.vwapPreservationMode.value}"]
        if maximum_penetration_atr > self.config.vwapPenetrationToleranceAtr:
            reason_codes.append("vwap_policy.penetration_exceeded")
        if wrong_side_closes > self.config.maxVwapWrongSideClosesStrict:
            reason_codes.append("vwap_policy.wrong_side_closes")
        if not confirmation_correct_side:
            reason_codes.append("vwap_policy.confirmation_not_reclaimed")
        if not vwap_reclaimed:
            reason_codes.append("vwap_policy.not_reclaimed")
        if bars_to_reclaim is not None and bars_to_reclaim > self.config.vwapReclaimBarsModerate:
            reason_codes.append("vwap_policy.reclaim_too_late")
        if wrong_side_structure:
            reason_codes.append("vwap_policy.wrong_side_structure")
        if self.config.vwapPreservationMode == VwapPreservationMode.CONTEXT and not vwap_preserved:
            reason_codes.append("vwap_policy.context_penalty")

        return VwapPolicyResult(
            mode=self.config.vwapPreservationMode,
            passed=passed,
            vwapPreserved=vwap_preserved,
            maximumVwapPenetrationAtr=round(maximum_penetration_atr, 4),
            barsClosedWrongSideOfVwap=wrong_side_closes,
            vwapReclaimed=vwap_reclaimed,
            vwapReclaimTimestamp=reclaim_timestamp,
            confirmationCorrectSide=confirmation_correct_side,
            bearishStructureBelowVwap=wrong_side_structure if impulse.direction == Signal.BUY else False,
            reasonCodes=tuple(reason_codes),
        )

    def _with_pullback_classification(self, pullback: Pullback | None, classification: FirstPullbackClassification) -> Pullback | None:
        if pullback is None:
            return None
        return Pullback(
            startIndex=pullback.startIndex,
            endIndex=pullback.endIndex,
            pullbackExtreme=pullback.pullbackExtreme,
            averageVolume=pullback.averageVolume,
            retracement=pullback.retracement,
            pullbackStart=pullback.pullbackStart,
            pullbackEnd=pullback.pullbackEnd,
            pullbackDuration=pullback.pullbackDuration,
            countertrendCandleCount=pullback.countertrendCandleCount,
            pauseCandleCount=pullback.pauseCandleCount,
            directionalEfficiency=pullback.directionalEfficiency,
            maximumRetracement=pullback.maximumRetracement,
            averageCountertrendVolume=pullback.averageCountertrendVolume,
            classification=classification,
        )

    def _with_pullback_pause(self, pullback: Pullback | None, pause_candle_count: int) -> Pullback | None:
        if pullback is None:
            return None
        return Pullback(
            startIndex=pullback.startIndex,
            endIndex=pullback.endIndex,
            pullbackExtreme=pullback.pullbackExtreme,
            averageVolume=pullback.averageVolume,
            retracement=pullback.retracement,
            pullbackStart=pullback.pullbackStart,
            pullbackEnd=pullback.pullbackEnd,
            pullbackDuration=pullback.pullbackDuration,
            countertrendCandleCount=pullback.countertrendCandleCount,
            pauseCandleCount=pause_candle_count,
            directionalEfficiency=pullback.directionalEfficiency,
            maximumRetracement=pullback.maximumRetracement,
            averageCountertrendVolume=pullback.averageCountertrendVolume,
            classification=pullback.classification,
        )

    def _touches_pullback_zone(
        self,
        candle: dict[str, Any],
        impulse: Impulse,
        atr: float,
        ema9: float | None,
        ema20: float | None,
        vwap: float | None,
    ) -> bool:
        low = float(candle["low"])
        high = float(candle["high"])
        retracement_min, retracement_max = self._retracement_zone(impulse)
        tolerance = atr * self.config.pullbackZoneAtrTolerance
        zone_values = [value for value in (ema9, ema20, vwap) if value is not None]

        if impulse.direction == Signal.BUY:
            touched_retracement = low <= retracement_max + tolerance and high >= retracement_min - tolerance
            touched_dynamic = any(low <= value + tolerance <= high + tolerance for value in zone_values)
            preserved = low >= impulse.originPrice - (atr * self.config.originBreakAtrBuffer)
            return preserved and (touched_retracement or touched_dynamic)

        touched_retracement = high >= retracement_min - tolerance and low <= retracement_max + tolerance
        touched_dynamic = any(high >= value - tolerance >= low - tolerance for value in zone_values)
        preserved = high <= impulse.originPrice + (atr * self.config.originBreakAtrBuffer)
        return preserved and (touched_retracement or touched_dynamic)

    def _moves_against_impulse(self, candle: dict[str, Any], impulse: Impulse) -> bool:
        open_price = float(candle["open"])
        close = float(candle["close"])
        if impulse.direction == Signal.BUY:
            return close < open_price
        return close > open_price

    def _confirmation_quality(
        self,
        candles: list[dict[str, Any]],
        index: int,
        impulse: Impulse,
        pullback: Pullback,
        atr: float,
        ema9: float | None,
        ema20: float | None,
        vwap: float | None,
    ) -> ConfirmationQualityResult:
        if index <= pullback.startIndex:
            return _failed_confirmation_quality(("confirmation.before_pullback",))
        candle = candles[index]
        previous = candles[index - 1]
        open_price = float(candle["open"])
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        candle_range = max(high - low, 0.01)
        body = abs(close - open_price)
        threshold = atr * self.config.confirmationCloseBeyondPullbackAtr
        pullback_pivot = _pullback_pivot(candles, pullback, impulse.direction)
        close_location = _close_location(candle, impulse.direction)
        range_atr = candle_range / atr if atr else float("inf")
        volume_ok = float(candle["volume"]) > pullback.averageCountertrendVolume
        if impulse.direction == Signal.BUY:
            close_beyond_pivot = close >= pullback_pivot + threshold
            close_beyond_previous = close >= float(previous["high"]) + threshold
            close_above_anchor = any(anchor is not None and close >= anchor for anchor in (ema9, ema20))
            vwap_ok = vwap is not None and close >= vwap
            rejection_wick = (high - close) / candle_range
            extension_vwap = abs(close - vwap) / atr if vwap is not None and atr else None
            extension_ema20 = abs(close - ema20) / atr if ema20 is not None and atr else None
        else:
            close_beyond_pivot = close <= pullback_pivot - threshold
            close_beyond_previous = close <= float(previous["low"]) - threshold
            close_above_anchor = any(anchor is not None and close <= anchor for anchor in (ema9, ema20))
            vwap_ok = vwap is not None and close <= vwap
            rejection_wick = (close - low) / candle_range
            extension_vwap = abs(close - vwap) / atr if vwap is not None and atr else None
            extension_ema20 = abs(close - ema20) / atr if ema20 is not None and atr else None
        close_location_ok = close_location >= self.config.minConfirmationCloseLocation
        wick_ok = rejection_wick <= self.config.maxConfirmationUpperWickFraction
        range_ok = range_atr <= self.config.maxConfirmationRangeAtr and body >= atr * self.config.confirmationMinimumBodyAtr
        extension_ok = (
            (extension_vwap is None or extension_vwap <= self.config.maxConfirmationVwapDistanceAtr)
            and (extension_ema20 is None or extension_ema20 <= self.config.maxConfirmationEma20DistanceAtr)
        )
        nearest_level_distance = _nearest_opposing_level_distance_atr(candles, index, impulse.direction, close, atr)
        opposing_level_ok = nearest_level_distance is None or nearest_level_distance >= self.config.minOpposingLevelDistanceAtr
        reason_codes: list[str] = []
        if not close_beyond_pivot:
            reason_codes.append("confirmation.pivot_not_cleared")
        if not close_beyond_previous:
            reason_codes.append("confirmation.previous_extreme_not_cleared")
        if not close_above_anchor:
            reason_codes.append("confirmation.anchor_not_reclaimed")
        if not vwap_ok:
            reason_codes.append("confirmation.vwap_not_preserved_or_reclaimed")
        if not close_location_ok:
            reason_codes.append("confirmation.close_location_weak")
        if not volume_ok:
            reason_codes.append("confirmation.volume_not_greater_than_pullback")
        if not wick_ok:
            reason_codes.append("confirmation.rejection_wick_too_large")
        if not range_ok:
            reason_codes.append("confirmation.trigger_range_invalid")
        if not extension_ok:
            reason_codes.append("confirmation.entry_extension_excessive")
        if not opposing_level_ok:
            reason_codes.append("confirmation.near_opposing_level")
        return ConfirmationQualityResult(
            passed=not reason_codes,
            closeBeyondPullbackPivot=close_beyond_pivot,
            closeBeyondPreviousExtreme=close_beyond_previous,
            closeAboveAnchor=close_above_anchor,
            vwapOk=vwap_ok,
            closeLocation=round(close_location, 4),
            closeLocationOk=close_location_ok,
            confirmationVolumeOk=volume_ok,
            rejectionWickFraction=round(rejection_wick, 4),
            rejectionWickOk=wick_ok,
            rangeAtr=round(range_atr, 4),
            rangeOk=range_ok,
            vwapDistanceAtr=round(extension_vwap, 4) if extension_vwap is not None else None,
            ema20DistanceAtr=round(extension_ema20, 4) if extension_ema20 is not None else None,
            extensionOk=extension_ok,
            nearestOpposingLevelDistanceAtr=round(nearest_level_distance, 4) if nearest_level_distance is not None else None,
            opposingLevelOk=opposing_level_ok,
            reasonCodes=tuple(reason_codes),
        )

    def _breaks_impulse_origin(self, candle: dict[str, Any], impulse: Impulse) -> bool:
        return self._origin_violation([candle], 0, impulse).hardViolation

    def _origin_violation(self, candles: list[dict[str, Any]], index: int, impulse: Impulse) -> OriginViolationResult:
        buffer = impulse.atr * self.config.originBreakAtrBuffer
        level = impulse.originPrice - buffer if impulse.direction == Signal.BUY else impulse.originPrice + buffer
        candle = candles[index]
        if impulse.direction == Signal.BUY:
            wick_violation = float(candle["low"]) < level
            close_violation = float(candle["close"]) < level
            recent = candles[max(0, index - self.config.originAcceptanceBars + 1) : index + 1]
            acceptance = len(recent) >= self.config.originAcceptanceBars and all(float(row["close"]) < level for row in recent)
        else:
            wick_violation = float(candle["high"]) > level
            close_violation = float(candle["close"]) > level
            recent = candles[max(0, index - self.config.originAcceptanceBars + 1) : index + 1]
            acceptance = len(recent) >= self.config.originAcceptanceBars and all(float(row["close"]) > level for row in recent)
        reason_codes: list[str] = []
        if wick_violation:
            reason_codes.append("origin.wick_violation")
        if close_violation:
            reason_codes.append("origin.close_violation")
        if acceptance:
            reason_codes.append("origin.acceptance_beyond_level")
        return OriginViolationResult(
            level=round(level, 4),
            buffer=round(buffer, 4),
            wickViolation=wick_violation,
            closeViolation=close_violation,
            acceptanceBeyondLevel=acceptance,
            hardViolation=close_violation or acceptance,
            reasonCodes=tuple(reason_codes),
        )

    def _invalidation_levels(
        self,
        impulse: Impulse | None,
        pullback: Pullback | None = None,
        origin_violation: OriginViolationResult | None = None,
    ) -> InvalidationLevels | None:
        if impulse is None:
            return None
        entry_level = None
        entry_basis = "unavailable_before_pullback"
        if pullback is not None:
            entry_level = pullback.pullbackExtreme
            entry_basis = "pullback_extreme"
        setup_level = self._setup_invalidation_level(impulse)
        thesis_violation = origin_violation or self._pullback_origin_violation(impulse, pullback) or OriginViolationResult(
            level=round(impulse.originPrice - (impulse.atr * self.config.originBreakAtrBuffer), 4)
            if impulse.direction == Signal.BUY
            else round(impulse.originPrice + (impulse.atr * self.config.originBreakAtrBuffer), 4),
            buffer=round(impulse.atr * self.config.originBreakAtrBuffer, 4),
            wickViolation=False,
            closeViolation=False,
            acceptanceBeyondLevel=False,
            hardViolation=False,
            reasonCodes=(),
        )
        return InvalidationLevels(
            entryInvalidation=InvalidationLevel(
                name="entryInvalidation",
                level=round(entry_level, 4) if entry_level is not None else None,
                basis=entry_basis,
            ),
            setupInvalidation=InvalidationLevel(
                name="setupInvalidation",
                level=round(setup_level, 4),
                basis="maximum_valid_pullback_depth_for_vwap_ema_structure",
            ),
            thesisInvalidation=InvalidationLevel(
                name="thesisInvalidation",
                level=thesis_violation.level,
                basis="opening_impulse_origin",
                violation=thesis_violation,
            ),
        )

    def _setup_invalidation_level(self, impulse: Impulse) -> float:
        impulse_range = abs(impulse.extremePrice - impulse.originPrice)
        if impulse.direction == Signal.BUY:
            return impulse.extremePrice - (impulse_range * self.config.pullbackRetracementMax)
        return impulse.extremePrice + (impulse_range * self.config.pullbackRetracementMax)

    def _pullback_origin_violation(self, impulse: Impulse, pullback: Pullback | None) -> OriginViolationResult | None:
        if pullback is None:
            return None
        buffer = impulse.atr * self.config.originBreakAtrBuffer
        level = impulse.originPrice - buffer if impulse.direction == Signal.BUY else impulse.originPrice + buffer
        wick_violation = pullback.pullbackExtreme < level if impulse.direction == Signal.BUY else pullback.pullbackExtreme > level
        if not wick_violation:
            return None
        return OriginViolationResult(
            level=round(level, 4),
            buffer=round(buffer, 4),
            wickViolation=True,
            closeViolation=False,
            acceptanceBeyondLevel=False,
            hardViolation=False,
            reasonCodes=("origin.wick_violation",),
        )

    def _retracement_zone(self, impulse: Impulse) -> tuple[float, float]:
        impulse_range = abs(impulse.extremePrice - impulse.originPrice)
        if impulse.direction == Signal.BUY:
            shallow = impulse.extremePrice - (impulse_range * self.config.pullbackRetracementMin)
            deep = impulse.extremePrice - (impulse_range * self.config.pullbackRetracementMax)
            return deep, shallow
        shallow = impulse.extremePrice + (impulse_range * self.config.pullbackRetracementMin)
        deep = impulse.extremePrice + (impulse_range * self.config.pullbackRetracementMax)
        return shallow, deep

    def _retracement(self, impulse: Impulse, pullback_extreme: float) -> float:
        impulse_range = abs(impulse.extremePrice - impulse.originPrice)
        if impulse_range == 0:
            return 0
        return abs(impulse.extremePrice - pullback_extreme) / impulse_range

    def _completed(
        self,
        impulse: Impulse,
        pullback: Pullback,
        established_trend: EstablishedTrendResult | None,
        vwap_policy: VwapPolicyResult | None,
        confirmation_bar: BarFinalization,
        evaluation_timestamp: datetime | None,
        relative_volume: FirstPullbackRelativeVolumeEvidence | None,
        confirmation_quality: ConfirmationQualityResult | None,
        quote: dict[str, Any] | None = None,
        exchange_session: ExchangeSession | None = None,
    ) -> StateMachineResult:
        exchange_session = exchange_session or EXCHANGE_CALENDAR.session_for_date(impulse.startTimestamp.date())
        confidence_model = self._confidence(
            impulse,
            pullback,
            established_trend,
            vwap_policy,
            relative_volume,
            confirmation_quality,
            quote,
            exchange_session,
        )
        confidence = confidence_model.finalConfidence
        reason_codes = [
            "first_pullback.completed",
            f"state:{FirstPullbackState.SIGNAL_EMITTED.value}",
            f"direction:{impulse.direction.value.lower()}",
            f"pullback:{FirstPullbackClassification.CONFIRMED.value}",
        ]
        if vwap_policy is not None:
            reason_codes.extend(vwap_policy.reasonCodes)
        invalidation_levels = self._invalidation_levels(impulse, pullback)
        return StateMachineResult(
            state=FirstPullbackState.SIGNAL_EMITTED,
            signal=impulse.direction,
            confidence=confidence,
            setupDetected=True,
            reasonCodes=reason_codes,
            explanation=(
                f"{impulse.direction.value} first pullback after open: impulse "
                f"{impulse.displacementAtr:.2f} ATR, retracement {pullback.retracement:.2f}, confirmation candle complete."
            ),
            impulse=impulse,
            establishedTrend=established_trend,
            vwapPolicy=vwap_policy,
            relativeVolume=relative_volume,
            confirmationQuality=confirmation_quality,
            pullback=pullback,
            confirmationBar=confirmation_bar,
            earliestExecutionTimestamp=evaluation_timestamp,
            structuralInvalidationPrice=invalidation_levels.entryInvalidation.level if invalidation_levels else None,
            invalidationLevels=invalidation_levels,
            confidenceModel=confidence_model,
        )

    def _rejected(
        self,
        impulse: Impulse,
        pullback: Pullback | None,
        reason_code: str,
        *,
        established_trend: EstablishedTrendResult | None = None,
        vwap_policy: VwapPolicyResult | None = None,
        relative_volume: FirstPullbackRelativeVolumeEvidence | None = None,
        confirmation_quality: ConfirmationQualityResult | None = None,
        extra_reason: str | None = None,
        origin_violation: OriginViolationResult | None = None,
    ) -> StateMachineResult:
        reason_codes = [
            reason_code,
            f"state:{FirstPullbackState.FIRST_PULLBACK_REJECTED.value}",
            f"pullback:{pullback.classification.value if pullback else FirstPullbackClassification.FORMING.value}",
            "first_pullback.session_locked",
        ]
        if extra_reason:
            reason_codes.append(extra_reason)
        if vwap_policy is not None:
            reason_codes.extend(vwap_policy.reasonCodes)
        if confirmation_quality is not None:
            reason_codes.extend(confirmation_quality.reasonCodes)
        if origin_violation is not None:
            reason_codes.extend(origin_violation.reasonCodes)
        invalidation_levels = self._invalidation_levels(impulse, pullback, origin_violation)
        return StateMachineResult(
            state=FirstPullbackState.FIRST_PULLBACK_REJECTED,
            signal=Signal.HOLD,
            confidence=0.0,
            setupDetected=True,
            reasonCodes=reason_codes,
            explanation="HOLD because the first countertrend pullback was rejected; later pullbacks are not relabeled as first pullback.",
            impulse=impulse,
            establishedTrend=established_trend,
            vwapPolicy=vwap_policy,
            relativeVolume=relative_volume,
            confirmationQuality=confirmation_quality,
            pullback=pullback,
            structuralInvalidationPrice=invalidation_levels.entryInvalidation.level if invalidation_levels else None,
            invalidationLevels=invalidation_levels,
        )

    def _invalidated(
        self,
        impulse: Impulse | None,
        reason_code: str,
        origin_violation: OriginViolationResult | None = None,
    ) -> StateMachineResult:
        invalidation_levels = self._invalidation_levels(impulse, origin_violation=origin_violation)
        return StateMachineResult(
            state=FirstPullbackState.INVALIDATED,
            signal=Signal.HOLD,
            confidence=0.0,
            setupDetected=impulse is not None,
            reasonCodes=[
                reason_code,
                f"state:{FirstPullbackState.INVALIDATED.value}",
                *(origin_violation.reasonCodes if origin_violation else ()),
            ],
            explanation="HOLD because the first pullback setup was invalidated before confirmation.",
            impulse=impulse,
            structuralInvalidationPrice=invalidation_levels.entryInvalidation.level if invalidation_levels else None,
            invalidationLevels=invalidation_levels,
        )

    def _hold_no_impulse(self, extra_reason_codes: list[str] | None = None) -> StateMachineResult:
        return StateMachineResult(
            state=FirstPullbackState.WAITING_FOR_IMPULSE,
            signal=Signal.HOLD,
            confidence=0.1,
            setupDetected=False,
            reasonCodes=[
                "first_pullback.no_opening_impulse",
                f"state:{FirstPullbackState.WAITING_FOR_IMPULSE.value}",
                *(extra_reason_codes or []),
            ],
            explanation="HOLD because no qualifying opening impulse has been identified.",
        )

    def _hold_waiting_for_pullback(
        self,
        impulse: Impulse | None,
        established_trend: EstablishedTrendResult | None = None,
        vwap_policy: VwapPolicyResult | None = None,
    ) -> StateMachineResult:
        invalidation_levels = self._invalidation_levels(impulse)
        return StateMachineResult(
            state=FirstPullbackState.WAITING_FOR_FIRST_PULLBACK,
            signal=Signal.HOLD,
            confidence=0.25,
            setupDetected=impulse is not None,
            reasonCodes=["first_pullback.waiting_for_pullback", f"state:{FirstPullbackState.WAITING_FOR_FIRST_PULLBACK.value}"],
            explanation="HOLD because the opening impulse is present but the first pullback has not qualified.",
            impulse=impulse,
            establishedTrend=established_trend,
            vwapPolicy=vwap_policy,
            structuralInvalidationPrice=invalidation_levels.entryInvalidation.level if invalidation_levels else None,
            invalidationLevels=invalidation_levels,
        )

    def _hold_waiting_for_confirmation(
        self,
        impulse: Impulse | None,
        pullback: Pullback | None,
        established_trend: EstablishedTrendResult | None = None,
        vwap_policy: VwapPolicyResult | None = None,
        state: FirstPullbackState = FirstPullbackState.WAITING_FOR_CONFIRMATION,
        confirmation_quality: ConfirmationQualityResult | None = None,
    ) -> StateMachineResult:
        reason_codes = [
            "first_pullback.waiting_for_confirmation",
            f"state:{state.value}",
            f"pullback:{pullback.classification.value if pullback else FirstPullbackClassification.FORMING.value}",
        ]
        if confirmation_quality is not None:
            reason_codes.extend(confirmation_quality.reasonCodes)
        invalidation_levels = self._invalidation_levels(impulse, pullback)
        return StateMachineResult(
            state=state,
            signal=Signal.HOLD,
            confidence=0.35,
            setupDetected=impulse is not None and pullback is not None,
            reasonCodes=reason_codes,
            explanation="HOLD because the first pullback is present but no continuation confirmation candle has completed.",
            impulse=impulse,
            establishedTrend=established_trend,
            vwapPolicy=vwap_policy,
            confirmationQuality=confirmation_quality,
            pullback=pullback,
            structuralInvalidationPrice=invalidation_levels.entryInvalidation.level if invalidation_levels else None,
            invalidationLevels=invalidation_levels,
        )

    def _hold_completed(
        self,
        impulse: Impulse,
        pullback: Pullback,
        established_trend: EstablishedTrendResult | None = None,
        vwap_policy: VwapPolicyResult | None = None,
        confirmation_bar: BarFinalization | None = None,
        relative_volume: FirstPullbackRelativeVolumeEvidence | None = None,
        confirmation_quality: ConfirmationQualityResult | None = None,
    ) -> StateMachineResult:
        reason_codes = ["first_pullback.already_completed", f"state:{FirstPullbackState.SESSION_COMPLETE.value}"]
        if vwap_policy is not None:
            reason_codes.extend(vwap_policy.reasonCodes)
        invalidation_levels = self._invalidation_levels(impulse, pullback)
        return StateMachineResult(
            state=FirstPullbackState.SESSION_COMPLETE,
            signal=Signal.HOLD,
            confidence=0.2,
            setupDetected=False,
            reasonCodes=reason_codes,
            explanation="HOLD because the first qualifying pullback already completed earlier in the session.",
            impulse=impulse,
            establishedTrend=established_trend,
            vwapPolicy=vwap_policy,
            relativeVolume=relative_volume,
            confirmationQuality=confirmation_quality,
            pullback=pullback,
            confirmationBar=confirmation_bar,
            structuralInvalidationPrice=invalidation_levels.entryInvalidation.level if invalidation_levels else None,
            invalidationLevels=invalidation_levels,
        )

    def _confidence(
        self,
        impulse: Impulse,
        pullback: Pullback,
        established_trend: EstablishedTrendResult | None = None,
        vwap_policy: VwapPolicyResult | None = None,
        relative_volume: FirstPullbackRelativeVolumeEvidence | None = None,
        confirmation_quality: ConfirmationQualityResult | None = None,
        quote: dict[str, Any] | None = None,
        exchange_session: ExchangeSession | None = None,
    ) -> ConfidenceModel:
        exchange_session = exchange_session or EXCHANGE_CALENDAR.session_for_date(impulse.startTimestamp.date())
        impulse_quality = self._impulse_confidence(impulse)
        established_quality = established_trend.score if established_trend is not None else 0.5
        pullback_quality = self._pullback_structure_confidence(pullback)
        pullback_volume_quality = self._pullback_volume_confidence(relative_volume)
        vwap_anchor_quality = self._vwap_anchor_confidence(vwap_policy, confirmation_quality)
        confirmation_score = self._confirmation_confidence(confirmation_quality)
        timing_data_quality = self._timing_data_confidence(impulse, pullback, relative_volume, confirmation_quality, exchange_session)
        gross = (
            (0.20 * impulse_quality)
            + (0.15 * established_quality)
            + (0.20 * pullback_quality)
            + (0.15 * pullback_volume_quality)
            + (0.10 * vwap_anchor_quality)
            + (0.15 * confirmation_score)
            + (0.05 * timing_data_quality)
        )
        penalties = self._confidence_penalties(
            impulse,
            pullback,
            established_trend,
            vwap_policy,
            relative_volume,
            confirmation_quality,
            quote,
            exchange_session,
        )
        final = max(0.0, min(1.0, gross - sum(penalties.values())))
        return ConfidenceModel(
            impulseQuality=round(impulse_quality, 4),
            establishedTrendQuality=round(established_quality, 4),
            pullbackDepthAndStructure=round(pullback_quality, 4),
            pullbackVolumeQuality=round(pullback_volume_quality, 4),
            vwapAnchorPreservation=round(vwap_anchor_quality, 4),
            confirmationQuality=round(confirmation_score, 4),
            timingAndDataQuality=round(timing_data_quality, 4),
            grossConfidence=round(gross, 4),
            penalties={key: round(value, 4) for key, value in penalties.items() if value > 0},
            finalConfidence=round(final, 4),
            minimumActionableConfidence=self.config.minimumActionableConfidence,
            actionable=final >= self.config.minimumActionableConfidence,
        )

    def _relative_volume_confidence_contribution(self, relative_volume: FirstPullbackRelativeVolumeEvidence | None) -> float:
        if relative_volume is None or not relative_volume.dataReady:
            return self.config.optionalRelativeVolumeMissingContribution
        values = [
            relative_volume.impulseCumulativeRelativeVolume,
            relative_volume.impulseAverageRelativeVolume,
            relative_volume.confirmationRelativeVolume,
        ]
        ready_values = [value for value in values if value is not None]
        if not ready_values:
            return self.config.optionalRelativeVolumeMissingContribution
        return min(1.0, mean(ready_values) / max(self.config.minRelativeVolume * 1.5, 0.01))

    def _impulse_confidence(self, impulse: Impulse) -> float:
        quality = impulse.quality
        parts = [
            quality.directionalCandleRatio,
            quality.averageBodyToRangeRatio,
            quality.impulseCloseLocation,
            quality.efficiencyRatio,
            max(0.0, 1.0 - quality.maximumInternalRetracement),
        ]
        displacement = min(1.0, impulse.displacementAtr / max(self.config.minImpulseAtrMultiple * 2, 0.01))
        return max(0.0, min(1.0, (0.75 * mean(parts)) + (0.25 * displacement)))

    def _pullback_structure_confidence(self, pullback: Pullback) -> float:
        center = (self.config.pullbackRetracementMin + self.config.pullbackRetracementMax) / 2
        half_width = max((self.config.pullbackRetracementMax - self.config.pullbackRetracementMin) / 2, 0.01)
        depth_score = max(0.0, 1.0 - (abs(pullback.retracement - center) / half_width))
        efficiency_score = max(0.0, min(1.0, pullback.directionalEfficiency))
        duration_score = max(0.0, min(1.0, 1.0 - (pullback.pullbackDuration / max(self.config.maximumPullbackMinutes * 1.5, 0.01))))
        return max(0.0, min(1.0, (0.55 * depth_score) + (0.25 * efficiency_score) + (0.20 * duration_score)))

    def _pullback_volume_confidence(self, relative_volume: FirstPullbackRelativeVolumeEvidence | None) -> float:
        if relative_volume is None or not relative_volume.dataReady or relative_volume.pullbackVolumeRatio is None:
            return self.config.optionalRelativeVolumeMissingContribution
        return max(0.0, min(1.0, 1.0 - (relative_volume.pullbackVolumeRatio / max(self.config.maxPullbackVolumeRatio, 0.01))))

    def _vwap_anchor_confidence(
        self,
        vwap_policy: VwapPolicyResult | None,
        confirmation_quality: ConfirmationQualityResult | None,
    ) -> float:
        if vwap_policy is None:
            return 0.5
        score = 1.0
        if not vwap_policy.vwapPreserved:
            score -= 0.35
        if vwap_policy.barsClosedWrongSideOfVwap:
            score -= min(0.4, 0.15 * vwap_policy.barsClosedWrongSideOfVwap)
        if not vwap_policy.vwapReclaimed and vwap_policy.mode != VwapPreservationMode.STRICT:
            score -= 0.15
        if confirmation_quality is not None and not confirmation_quality.closeAboveAnchor:
            score -= 0.20
        return max(0.0, min(1.0, score))

    def _confirmation_confidence(self, confirmation_quality: ConfirmationQualityResult | None) -> float:
        if confirmation_quality is None:
            return 0.0
        checks = [
            confirmation_quality.closeBeyondPullbackPivot,
            confirmation_quality.closeBeyondPreviousExtreme,
            confirmation_quality.closeAboveAnchor,
            confirmation_quality.vwapOk,
            confirmation_quality.closeLocationOk,
            confirmation_quality.confirmationVolumeOk,
            confirmation_quality.rejectionWickOk,
            confirmation_quality.rangeOk,
            confirmation_quality.extensionOk,
            confirmation_quality.opposingLevelOk,
        ]
        base = sum(1 for check in checks if check) / len(checks)
        close_location_bonus = min(1.0, confirmation_quality.closeLocation)
        return max(0.0, min(1.0, (0.75 * base) + (0.25 * close_location_bonus)))

    def _timing_data_confidence(
        self,
        impulse: Impulse,
        pullback: Pullback,
        relative_volume: FirstPullbackRelativeVolumeEvidence | None,
        confirmation_quality: ConfirmationQualityResult | None,
        exchange_session: ExchangeSession,
    ) -> float:
        start_score = max(0.0, min(1.0, 1.0 - (max(0.0, _minutes_after_open(impulse.startTimestamp, exchange_session) - self.config.sessionStartMinute) / max(self.config.maxImpulseStartMinute, 1))))
        duration_score = max(0.0, min(1.0, 1.0 - (pullback.pullbackDuration / max(self.config.maximumPullbackMinutes, 1))))
        data_score = 1.0
        if relative_volume is None or not relative_volume.dataReady:
            data_score -= 0.35
        if confirmation_quality is None:
            data_score -= 0.35
        return max(0.0, min(1.0, (0.35 * start_score) + (0.30 * duration_score) + (0.35 * data_score)))

    def _confidence_penalties(
        self,
        impulse: Impulse,
        pullback: Pullback,
        established_trend: EstablishedTrendResult | None,
        vwap_policy: VwapPolicyResult | None,
        relative_volume: FirstPullbackRelativeVolumeEvidence | None,
        confirmation_quality: ConfirmationQualityResult | None,
        quote: dict[str, Any] | None,
        exchange_session: ExchangeSession,
    ) -> dict[str, float]:
        penalties: dict[str, float] = {}
        start_minute = _minutes_after_open(impulse.startTimestamp, exchange_session)
        if start_minute > self.config.sessionStartMinute:
            penalties["lateSetup"] = min(
                self.config.lateSetupPenaltyMax,
                self.config.lateSetupPenaltyMax * (start_minute / max(self.config.maxImpulseStartMinute, 1)),
            )
        if pullback.retracement > self.config.pullbackRetracementMax * 0.85:
            penalties["deepPullback"] = min(
                self.config.deepPullbackPenaltyMax,
                self.config.deepPullbackPenaltyMax * (pullback.retracement / max(self.config.pullbackRetracementMax, 0.01)),
            )
        if pullback.pullbackDuration > self.config.maximumPullbackMinutes * 0.5:
            penalties["longPullbackDuration"] = min(
                self.config.longPullbackDurationPenaltyMax,
                self.config.longPullbackDurationPenaltyMax * (pullback.pullbackDuration / max(self.config.maximumPullbackMinutes, 1)),
            )
        if vwap_policy is not None and vwap_policy.barsClosedWrongSideOfVwap > 0:
            penalties["repeatedVwapLoss"] = min(
                self.config.repeatedVwapLossPenalty,
                self.config.repeatedVwapLossPenalty * vwap_policy.barsClosedWrongSideOfVwap,
            )
        if confirmation_quality is not None:
            if confirmation_quality.rangeAtr > self.config.maxConfirmationRangeAtr * 0.75:
                penalties["largeConfirmationCandle"] = min(
                    self.config.largeConfirmationCandlePenaltyMax,
                    self.config.largeConfirmationCandlePenaltyMax
                    * (confirmation_quality.rangeAtr / max(self.config.maxConfirmationRangeAtr, 0.01)),
                )
            if confirmation_quality.nearestOpposingLevelDistanceAtr is not None and confirmation_quality.nearestOpposingLevelDistanceAtr < max(self.config.minOpposingLevelDistanceAtr, 0.25):
                penalties["nearbyOpposingLevel"] = self.config.nearbyOpposingLevelPenalty
        if established_trend is not None and established_trend.fiveMinutePermissionOk is False:
            penalties["higherTimeframeDisagreement"] = self.config.higherTimeframeDisagreementPenalty
        spread_penalty = self._spread_penalty(quote, impulse.atr)
        if spread_penalty > 0:
            penalties["abnormalSpread"] = spread_penalty
        if relative_volume is None or not relative_volume.dataReady:
            penalties["missingOptionalEvidence"] = self.config.missingOptionalEvidencePenalty
        if (
            vwap_policy is not None
            and vwap_policy.mode == VwapPreservationMode.CONTEXT
            and not vwap_policy.vwapPreserved
        ):
            penalties["vwapContext"] = self.config.vwapContextConfidencePenalty
        return penalties

    def _spread_penalty(self, quote: dict[str, Any] | None, atr: float) -> float:
        if not quote or atr <= 0:
            return 0.0
        bid = quote.get("bid")
        ask = quote.get("ask")
        if bid is None or ask is None:
            return 0.0
        spread_atr = max(0.0, (float(ask) - float(bid)) / atr)
        if spread_atr <= self.config.abnormalSpreadAtrThreshold:
            return 0.0
        return min(
            self.config.abnormalSpreadPenalty,
            self.config.abnormalSpreadPenalty * (spread_atr / max(self.config.abnormalSpreadAtrThreshold * 2, 0.01)),
        )

    def _historical_reliability(
        self,
        context: StrategyEvaluationContext,
        regime_fit: RegimeFitResult,
    ) -> StrategyReliabilityEstimate:
        return PERFORMANCE_TRACKER.reliability_for(
            raw_inputs=context.featureSnapshot.rawInputs,
            strategy_id=context.registryEntry.strategyId,
            regime_key=regime_fit.regimeKey,
        )

    def _regime_fit(self, context: StrategyEvaluationContext, result: StateMachineResult) -> RegimeFitResult:
        features = context.featureSnapshot.features
        raw_inputs = context.featureSnapshot.rawInputs
        direction = result.impulse.direction if result.impulse is not None else result.signal
        adx = _feature_number(features, "spy1mAdx14")
        trend_strength = _score_between(adx, low=15.0, high=35.0, default=0.5)
        vwap_crossing_frequency = _vwap_crossing_frequency(raw_inputs.get("spy1mCandles") or [], _feature_number(features, "sessionVwap"))
        choppiness = max(0.0, min(1.0, 1.0 - (vwap_crossing_frequency * 1.5)))
        atr_percentile = _feature_number(features, "spy1mRealizedVolatilityPercentile")
        if atr_percentile is None:
            atr_percentile = _feature_number(features, "spy1mBollingerWidthPercentile")
        atr_percentile_score = _balanced_percentile_score(atr_percentile)
        opening_expansion = self._opening_range_expansion_score(features)
        gap_state = self._gap_state_score(features)
        five_minute_structure = self._five_minute_structure_score(features, direction)
        economic_event_risk = self._economic_event_risk(features)
        score = (
            (0.22 * trend_strength)
            + (0.16 * choppiness)
            + (0.14 * atr_percentile_score)
            + (0.14 * opening_expansion)
            + (0.10 * gap_state)
            + (0.16 * five_minute_structure)
            + (0.08 * (1.0 - economic_event_risk))
        )
        reason_codes: list[str] = []
        if trend_strength >= 0.65:
            reason_codes.append("regime.trend_strength_supportive")
        if choppiness < 0.45:
            reason_codes.append("regime.vwap_chop_penalty")
        if economic_event_risk > 0:
            reason_codes.append("regime.economic_event_risk")
        if five_minute_structure < 0.45:
            reason_codes.append("regime.five_minute_structure_conflict")
        regime_key = "first_pullback_trend_open"
        if economic_event_risk > 0:
            regime_key = "first_pullback_event_risk"
        elif score < 0.45:
            regime_key = "first_pullback_choppy_or_misaligned"
        elif trend_strength >= 0.65 and five_minute_structure >= 0.65:
            regime_key = "first_pullback_trend_aligned"
        return RegimeFitResult(
            score=round(max(0.0, min(1.0, score)), 4),
            regimeKey=regime_key,
            trendStrength=round(trend_strength, 4),
            choppiness=round(choppiness, 4),
            atrPercentile=round(atr_percentile_score, 4),
            openingRangeExpansion=round(opening_expansion, 4),
            gapState=round(gap_state, 4),
            fiveMinuteStructure=round(five_minute_structure, 4),
            vwapCrossingFrequency=round(vwap_crossing_frequency, 4),
            economicEventRisk=round(economic_event_risk, 4),
            reasonCodes=tuple(reason_codes),
        )

    def _opening_range_expansion_score(self, features: dict[str, Any]) -> float:
        high = _feature_number(features, "openingRangeHigh")
        low = _feature_number(features, "openingRangeLow")
        atr = _feature_number(features, "spy1mAtr14")
        if high is None or low is None or atr is None or atr <= 0:
            return 0.5
        expansion_atr = abs(high - low) / atr
        if expansion_atr < 1.0:
            return max(0.2, expansion_atr)
        if expansion_atr <= 4.0:
            return 1.0
        return max(0.25, 1.0 - ((expansion_atr - 4.0) / 6.0))

    def _gap_state_score(self, features: dict[str, Any]) -> float:
        gap = _feature_number(features, "gapPercent")
        if gap is None:
            return 0.5
        magnitude = abs(gap)
        if magnitude <= 0.4:
            return 0.9
        if magnitude <= 1.2:
            return 0.75
        return max(0.25, 1.0 - (magnitude / 4.0))

    def _five_minute_structure_score(self, features: dict[str, Any], direction: Signal) -> float:
        bullish = _feature_bool(features, "spy5mHigherHighHigherLow")
        bearish = _feature_bool(features, "spy5mLowerHighLowerLow")
        if direction == Signal.BUY:
            if bullish:
                return 1.0
            if bearish:
                return 0.15
        if direction == Signal.SELL:
            if bearish:
                return 1.0
            if bullish:
                return 0.15
        return 0.55

    def _economic_event_risk(self, features: dict[str, Any]) -> float:
        event = _feature_value(features, "economicEventState")
        if not isinstance(event, dict):
            return 0.0
        if event.get("active") is True:
            return 1.0
        severity = str(event.get("severity") or event.get("risk") or "").lower()
        return 0.5 if severity in {"high", "elevated"} else 0.0


def _regular_session_candles(
    raw_candles: list[dict[str, Any]],
    context: StrategyEvaluationContext,
    exchange_session: ExchangeSession | None = None,
) -> list[dict[str, Any]]:
    exchange_session = exchange_session or _exchange_session_from_context(context)
    completed = []
    finalization_lag_seconds = int(context.featureSnapshot.rawInputs.get("finalizationLagSeconds") or 0)
    cutoff = context.evaluatedAt - timedelta(seconds=finalization_lag_seconds)
    for candle in raw_candles:
        timestamp = _timestamp(candle)
        if _bar_end_timestamp(candle) > cutoff:
            continue
        if not exchange_session.contains_timestamp(timestamp):
            continue
        completed.append(candle)
    return sorted(completed, key=lambda candle: _timestamp(candle))


def _exchange_session_from_context(context: StrategyEvaluationContext) -> ExchangeSession:
    return EXCHANGE_CALENDAR.session_from_raw_inputs(
        context.featureSnapshot.rawInputs,
        fallback_session_date=context.sessionDate,
    )


def _bar_overlaps_session(candle: dict[str, Any], exchange_session: ExchangeSession) -> bool:
    if not exchange_session.can_trade:
        return False
    start = _timestamp(candle)
    end = _bar_end_timestamp(candle)
    return bool(
        exchange_session.openTimestamp
        and exchange_session.closeTimestamp
        and start < exchange_session.closeTimestamp
        and end > exchange_session.openTimestamp
    )


def _bar_in_exchange_regular_session(candle: dict[str, Any], exchange: str = "XNYS") -> bool:
    timestamp = _timestamp(candle)
    local_date = _new_york_datetime(timestamp).date()
    session = EXCHANGE_CALENDAR.session_for_date(local_date, exchange=exchange)
    return session.contains_timestamp(timestamp)


def _session_series_quality(
    raw_candles: list[dict[str, Any]],
    context: StrategyEvaluationContext,
    finalization_lag_seconds: int,
    exchange_session: ExchangeSession,
) -> SessionSeriesQuality:
    cutoff = context.evaluatedAt - timedelta(seconds=finalization_lag_seconds)
    expected_symbol = str(
        context.featureSnapshot.rawInputs.get("symbol")
        or context.featureSnapshot.rawInputs.get("underlyingSymbol")
        or "SPY"
    ).upper()
    session_rows: list[dict[str, Any]] = []
    all_session_rows: list[dict[str, Any]] = []
    incomplete_session_rows: list[dict[str, Any]] = []
    for candle in raw_candles:
        timestamp = _timestamp(candle)
        if not _bar_overlaps_session(candle, exchange_session):
            continue
        all_session_rows.append(candle)
        if _bar_end_timestamp(candle) <= cutoff:
            session_rows.append(candle)
        else:
            incomplete_session_rows.append(candle)

    timestamps = [_timestamp(candle) for candle in session_rows]
    unique_timestamps = set(timestamps)
    is_ordered = all(left < right for left, right in zip(timestamps, timestamps[1:]))
    sorted_rows = sorted(session_rows, key=lambda candle: _timestamp(candle))
    sorted_timestamps = [_timestamp(candle) for candle in sorted_rows]
    has_missing_intervals = any(
        right - left != timedelta(minutes=1)
        for left, right in zip(sorted_timestamps, sorted_timestamps[1:])
    )
    latest_completed_bar_end = max((_bar_end_timestamp(candle) for candle in session_rows), default=None)
    expected_latest_bar_end = _expected_latest_one_minute_bar_end(context.evaluatedAt, finalization_lag_seconds)
    is_fresh = bool(latest_completed_bar_end and latest_completed_bar_end >= expected_latest_bar_end)
    symbol_matches = all(str(candle.get("symbol") or expected_symbol).upper() == expected_symbol for candle in all_session_rows)
    timeframe_matches = all(str(candle.get("timeframe") or "1Min") == "1Min" for candle in all_session_rows)
    has_zero_volume = any(float(candle.get("volume", 0)) <= 0 for candle in all_session_rows)
    reason_codes: list[str] = []
    if incomplete_session_rows:
        reason_codes.append("session_series.incomplete")
    if not is_fresh:
        reason_codes.append("session_series.stale")
    if len(unique_timestamps) != len(timestamps):
        reason_codes.append("session_series.duplicates")
    if has_missing_intervals:
        reason_codes.append("session_series.missing_intervals")
    if not is_ordered:
        reason_codes.append("session_series.out_of_order")
    if has_zero_volume:
        reason_codes.append("session_series.zero_volume")
    if not symbol_matches:
        reason_codes.append("session_series.symbol_mismatch")
    if not timeframe_matches:
        reason_codes.append("session_series.timeframe_mismatch")
    if not session_rows:
        reason_codes.append("session_series.session_mismatch")
    return SessionSeriesQuality(
        isComplete=not incomplete_session_rows,
        isFresh=is_fresh,
        hasDuplicates=len(unique_timestamps) != len(timestamps),
        hasMissingIntervals=has_missing_intervals,
        hasOutOfOrderBars=not is_ordered,
        hasZeroVolumeBars=has_zero_volume,
        symbolMatches=symbol_matches,
        timeframeMatches=timeframe_matches,
        sessionMatches=bool(session_rows),
        latestCompletedBarEnd=latest_completed_bar_end,
        qualityReasonCodes=tuple(reason_codes),
    )


def _expected_latest_one_minute_bar_end(evaluation_timestamp: datetime, finalization_lag_seconds: int) -> datetime:
    cutoff = evaluation_timestamp - timedelta(seconds=finalization_lag_seconds)
    return cutoff.replace(second=0, microsecond=0)


def _feature_value(features: dict[str, Any], name: str) -> Any:
    value = features.get(name)
    if value is None:
        return None
    return getattr(value, "value", value.get("value") if isinstance(value, dict) else value)


def _feature_number(features: dict[str, Any], name: str) -> float | None:
    value = _feature_value(features, name)
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _feature_bool(features: dict[str, Any], name: str) -> bool | None:
    value = _feature_value(features, name)
    return value if isinstance(value, bool) else None


def _score_between(value: float | None, *, low: float, high: float, default: float) -> float:
    if value is None:
        return default
    if value <= low:
        return 0.0
    if value >= high:
        return 1.0
    return (value - low) / (high - low)


def _balanced_percentile_score(value: float | None) -> float:
    if value is None:
        return 0.5
    if value < 0.1:
        return 0.25
    if value <= 0.75:
        return 0.9
    return max(0.2, 1.0 - ((value - 0.75) / 0.25) * 0.55)


def _vwap_crossing_frequency(candles: list[dict[str, Any]], vwap: float | None) -> float:
    if vwap is None or len(candles) < 3:
        return 0.0
    recent = candles[-20:]
    sides: list[int] = []
    for candle in recent:
        close = float(candle["close"])
        sides.append(1 if close > vwap else -1 if close < vwap else 0)
    compact = [side for side in sides if side != 0]
    if len(compact) < 2:
        return 0.0
    crossings = sum(1 for left, right in zip(compact, compact[1:]) if left != right)
    return max(0.0, min(1.0, crossings / max(len(compact) - 1, 1)))


def _indicator_candles(
    raw_candles: list[dict[str, Any]],
    context: StrategyEvaluationContext,
    *,
    include_premarket: bool,
    exchange_session: ExchangeSession | None = None,
) -> list[dict[str, Any]]:
    exchange_session = exchange_session or _exchange_session_from_context(context)
    allow_extended = bool(context.featureSnapshot.rawInputs.get("allowExtendedHours"))
    completed = []
    finalization_lag_seconds = int(context.featureSnapshot.rawInputs.get("finalizationLagSeconds") or 0)
    cutoff = context.evaluatedAt - timedelta(seconds=finalization_lag_seconds)
    for candle in raw_candles:
        timestamp = _timestamp(candle)
        if _bar_end_timestamp(candle) > cutoff:
            continue
        is_current_session = _bar_overlaps_session(candle, exchange_session)
        is_regular_warmup_session = _bar_in_exchange_regular_session(candle, exchange_session.exchange)
        if not is_current_session and not is_regular_warmup_session and not allow_extended:
            continue
        if timestamp < (exchange_session.openTimestamp or timestamp) and not is_regular_warmup_session and not (include_premarket and allow_extended):
            continue
        completed.append(candle)
    return sorted(completed, key=lambda candle: _timestamp(candle))


def _indicator_by_timestamp(candles: list[dict[str, Any]], values: list[float | None]) -> dict[datetime, float | None]:
    return {_timestamp(candle): value for candle, value in zip(candles, values)}


def _strategy_required_data_ready(snapshot: Any) -> bool:
    readiness = snapshot.rawInputs.get("readiness") if isinstance(snapshot.rawInputs, dict) else None
    if isinstance(readiness, dict) and "strategyRequiredDataReady" in readiness:
        return bool(readiness["strategyRequiredDataReady"])
    if hasattr(snapshot, "strategyRequiredDataReady"):
        return bool(snapshot.strategyRequiredDataReady)
    if hasattr(snapshot, "corePriceDataReady"):
        return bool(snapshot.corePriceDataReady)
    return bool(snapshot.dataReady)


def _impulse_payload(impulse: Impulse | None) -> dict[str, Any] | None:
    if impulse is None:
        return None
    return {
        "direction": impulse.direction.value,
        "startIndex": impulse.startIndex,
        "endIndex": impulse.endIndex,
        "startTimestamp": impulse.startTimestamp.isoformat().replace("+00:00", "Z"),
        "endTimestamp": impulse.endTimestamp.isoformat().replace("+00:00", "Z"),
        "originPrice": round(impulse.originPrice, 4),
        "extremePrice": round(impulse.extremePrice, 4),
        "atr": round(impulse.atr, 4),
        "displacementAtr": round(impulse.displacementAtr, 4),
        "displacementPercent": round(impulse.displacementPercent, 6),
        "relativeVolume": round(impulse.relativeVolume, 4) if impulse.relativeVolume is not None else None,
        "averageRelativeVolume": round(impulse.averageRelativeVolume, 4) if impulse.averageRelativeVolume is not None else None,
        "averageVolume": round(impulse.averageVolume, 4),
        "quality": _impulse_quality_payload(impulse.quality),
    }


def _impulse_quality_payload(quality: ImpulseQualityResult) -> dict[str, Any]:
    return {
        "passed": quality.passed,
        "directionalCandleRatio": quality.directionalCandleRatio,
        "averageBodyToRangeRatio": quality.averageBodyToRangeRatio,
        "impulseCloseLocation": quality.impulseCloseLocation,
        "efficiencyRatio": quality.efficiencyRatio,
        "maximumInternalRetracement": quality.maximumInternalRetracement,
        "opposingCandleCount": quality.opposingCandleCount,
        "opposingVolumeRatio": quality.opposingVolumeRatio,
        "startMinute": quality.startMinute,
        "reasonCodes": list(quality.reasonCodes),
    }


def _persistent_state_payload(state: FirstPullbackPersistentState) -> dict[str, Any]:
    return {
        "algorithmId": state.algorithmId,
        "strategyId": state.strategyId,
        "symbol": state.symbol,
        "sessionDate": state.sessionDate.isoformat(),
        "setupId": state.setupId,
        "eventId": state.eventId,
        "state": state.state,
        "signalEmitted": state.signalEmitted,
        "signalEmittedAt": state.signalEmittedAt.isoformat().replace("+00:00", "Z") if state.signalEmittedAt else None,
        "signalConsumed": state.signalConsumed,
        "invalidationReason": state.invalidationReason,
        "lastProcessedBarEnd": state.lastProcessedBarEnd.isoformat().replace("+00:00", "Z") if state.lastProcessedBarEnd else None,
    }


def _persistent_state_from_raw_inputs(
    raw_inputs: dict[str, Any],
    key: tuple[str, str, str, date],
) -> FirstPullbackPersistentState | None:
    payload = raw_inputs.get("firstPullbackPersistentState")
    if not isinstance(payload, dict):
        return None
    session_date = _date_from_payload(payload.get("sessionDate"))
    if (
        str(payload.get("algorithmId") or "") != key[0]
        or str(payload.get("strategyId") or "") != key[1]
        or str(payload.get("symbol") or "").upper() != key[2].upper()
        or session_date != key[3]
    ):
        return None
    return FirstPullbackPersistentState(
        algorithmId=key[0],
        strategyId=key[1],
        symbol=key[2],
        sessionDate=key[3],
        setupId=str(payload["setupId"]) if payload.get("setupId") else None,
        eventId=str(payload["eventId"]) if payload.get("eventId") else None,
        state=str(payload.get("state") or FirstPullbackState.WAITING_FOR_OPEN.value),
        signalEmitted=bool(payload.get("signalEmitted")),
        signalEmittedAt=_datetime_from_payload(payload.get("signalEmittedAt")),
        signalConsumed=bool(payload.get("signalConsumed")),
        invalidationReason=str(payload["invalidationReason"]) if payload.get("invalidationReason") else None,
        lastProcessedBarEnd=_datetime_from_payload(payload.get("lastProcessedBarEnd")),
    )


def _date_from_payload(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if value is None:
        return None
    return date.fromisoformat(str(value))


def _datetime_from_payload(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if value is None:
        return None
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _consume_persistent_state(state: FirstPullbackPersistentState) -> FirstPullbackPersistentState:
    return FirstPullbackPersistentState(
        algorithmId=state.algorithmId,
        strategyId=state.strategyId,
        symbol=state.symbol,
        sessionDate=state.sessionDate,
        setupId=state.setupId,
        eventId=state.eventId,
        state=state.state,
        signalEmitted=state.signalEmitted,
        signalEmittedAt=state.signalEmittedAt,
        signalConsumed=True,
        invalidationReason=state.invalidationReason,
        lastProcessedBarEnd=state.lastProcessedBarEnd,
    )


def _established_trend_payload(established_trend: EstablishedTrendResult | None) -> dict[str, Any] | None:
    if established_trend is None:
        return None
    return {
        "established": established_trend.established,
        "direction": established_trend.direction.value,
        "score": established_trend.score,
        "priceVwapOk": established_trend.priceVwapOk,
        "emaRelationshipOk": established_trend.emaRelationshipOk,
        "ema20SlopeOk": established_trend.ema20SlopeOk,
        "structureOk": established_trend.structureOk,
        "directionalEfficiency": established_trend.directionalEfficiency,
        "directionalCloses": established_trend.directionalCloses,
        "impulseCloseLocation": established_trend.impulseCloseLocation,
        "immediateRetracement": established_trend.immediateRetracement,
        "fiveMinutePermissionOk": established_trend.fiveMinutePermissionOk,
        "originViolation": _origin_violation_payload(established_trend.originViolation),
        "reasonCodes": list(established_trend.reasonCodes),
    }


def _vwap_policy_payload(vwap_policy: VwapPolicyResult | None) -> dict[str, Any] | None:
    if vwap_policy is None:
        return None
    return {
        "mode": vwap_policy.mode.value,
        "passed": vwap_policy.passed,
        "vwapPreserved": vwap_policy.vwapPreserved,
        "maximumVwapPenetrationAtr": vwap_policy.maximumVwapPenetrationAtr,
        "barsClosedWrongSideOfVwap": vwap_policy.barsClosedWrongSideOfVwap,
        "vwapReclaimed": vwap_policy.vwapReclaimed,
        "vwapReclaimTimestamp": vwap_policy.vwapReclaimTimestamp.isoformat() if vwap_policy.vwapReclaimTimestamp else None,
        "confirmationCorrectSide": vwap_policy.confirmationCorrectSide,
        "bearishStructureBelowVwap": vwap_policy.bearishStructureBelowVwap,
        "reasonCodes": list(vwap_policy.reasonCodes),
    }


def _relative_volume_payload(relative_volume: FirstPullbackRelativeVolumeEvidence | None) -> dict[str, Any] | None:
    if relative_volume is None:
        return None
    return {
        "dataReady": relative_volume.dataReady,
        "impulseActualVolume": round(relative_volume.impulseActualVolume, 4),
        "impulseExpectedVolume": round(relative_volume.impulseExpectedVolume, 4) if relative_volume.impulseExpectedVolume is not None else None,
        "impulseCumulativeRelativeVolume": round(relative_volume.impulseCumulativeRelativeVolume, 4) if relative_volume.impulseCumulativeRelativeVolume is not None else None,
        "impulseAverageRelativeVolume": round(relative_volume.impulseAverageRelativeVolume, 4) if relative_volume.impulseAverageRelativeVolume is not None else None,
        "pullbackActualVolume": round(relative_volume.pullbackActualVolume, 4) if relative_volume.pullbackActualVolume is not None else None,
        "pullbackExpectedVolume": round(relative_volume.pullbackExpectedVolume, 4) if relative_volume.pullbackExpectedVolume is not None else None,
        "pullbackAverageRelativeVolume": round(relative_volume.pullbackAverageRelativeVolume, 4) if relative_volume.pullbackAverageRelativeVolume is not None else None,
        "confirmationActualVolume": round(relative_volume.confirmationActualVolume, 4) if relative_volume.confirmationActualVolume is not None else None,
        "confirmationExpectedVolume": round(relative_volume.confirmationExpectedVolume, 4) if relative_volume.confirmationExpectedVolume is not None else None,
        "confirmationRelativeVolume": round(relative_volume.confirmationRelativeVolume, 4) if relative_volume.confirmationRelativeVolume is not None else None,
        "pullbackVolumeRatio": round(relative_volume.pullbackVolumeRatio, 4) if relative_volume.pullbackVolumeRatio is not None else None,
        "reasonCodes": list(relative_volume.reasonCodes),
    }


def _confirmation_quality_payload(quality: ConfirmationQualityResult | None) -> dict[str, Any] | None:
    if quality is None:
        return None
    return {
        "passed": quality.passed,
        "closeBeyondPullbackPivot": quality.closeBeyondPullbackPivot,
        "closeBeyondPreviousExtreme": quality.closeBeyondPreviousExtreme,
        "closeAboveAnchor": quality.closeAboveAnchor,
        "vwapOk": quality.vwapOk,
        "closeLocation": quality.closeLocation,
        "closeLocationOk": quality.closeLocationOk,
        "confirmationVolumeOk": quality.confirmationVolumeOk,
        "rejectionWickFraction": quality.rejectionWickFraction,
        "rejectionWickOk": quality.rejectionWickOk,
        "rangeAtr": quality.rangeAtr,
        "rangeOk": quality.rangeOk,
        "vwapDistanceAtr": quality.vwapDistanceAtr,
        "ema20DistanceAtr": quality.ema20DistanceAtr,
        "extensionOk": quality.extensionOk,
        "nearestOpposingLevelDistanceAtr": quality.nearestOpposingLevelDistanceAtr,
        "opposingLevelOk": quality.opposingLevelOk,
        "reasonCodes": list(quality.reasonCodes),
    }


def _bar_finalization_payload(bar: BarFinalization | None) -> dict[str, Any] | None:
    if bar is None:
        return None
    return {
        "barStartTimestamp": bar.barStartTimestamp.isoformat().replace("+00:00", "Z"),
        "barEndTimestamp": bar.barEndTimestamp.isoformat().replace("+00:00", "Z"),
        "wasFinalized": bar.wasFinalized,
        "providerRevision": bar.providerRevision,
    }


def _execution_payload(result: StateMachineResult) -> dict[str, Any] | None:
    if result.confirmationBar is None:
        return None
    return {
        "executionTiming": "next_permitted_event_after_confirmation",
        "executionPricePolicy": "external_execution_engine_next_executable_price",
        "earliestExecutionTimestamp": result.earliestExecutionTimestamp.isoformat().replace("+00:00", "Z") if result.earliestExecutionTimestamp else None,
        "doesNotAssumeConfirmationCandlePrice": True,
    }


def _invalidation_levels_payload(levels: InvalidationLevels | None) -> dict[str, Any] | None:
    if levels is None:
        return None
    return {
        "entryInvalidation": _invalidation_level_payload(levels.entryInvalidation),
        "setupInvalidation": _invalidation_level_payload(levels.setupInvalidation),
        "thesisInvalidation": _invalidation_level_payload(levels.thesisInvalidation),
    }


def _invalidation_level_payload(level: InvalidationLevel) -> dict[str, Any]:
    return {
        "name": level.name,
        "level": level.level,
        "basis": level.basis,
        "violation": _origin_violation_payload(level.violation),
    }


def _origin_violation_payload(violation: OriginViolationResult | None) -> dict[str, Any] | None:
    if violation is None:
        return None
    return {
        "level": violation.level,
        "buffer": violation.buffer,
        "wickViolation": violation.wickViolation,
        "closeViolation": violation.closeViolation,
        "acceptanceBeyondLevel": violation.acceptanceBeyondLevel,
        "hardViolation": violation.hardViolation,
        "reasonCodes": list(violation.reasonCodes),
    }


def _confidence_model_payload(model: ConfidenceModel | None) -> dict[str, Any] | None:
    if model is None:
        return None
    return {
        "weights": {
            "impulseQuality": 0.20,
            "establishedTrendQuality": 0.15,
            "pullbackDepthAndStructure": 0.20,
            "pullbackVolumeQuality": 0.15,
            "vwapAnchorPreservation": 0.10,
            "confirmationQuality": 0.15,
            "timingAndDataQuality": 0.05,
        },
        "components": {
            "impulseQuality": model.impulseQuality,
            "establishedTrendQuality": model.establishedTrendQuality,
            "pullbackDepthAndStructure": model.pullbackDepthAndStructure,
            "pullbackVolumeQuality": model.pullbackVolumeQuality,
            "vwapAnchorPreservation": model.vwapAnchorPreservation,
            "confirmationQuality": model.confirmationQuality,
            "timingAndDataQuality": model.timingAndDataQuality,
        },
        "grossConfidence": model.grossConfidence,
        "penalties": model.penalties,
        "finalConfidence": model.finalConfidence,
        "minimumActionableConfidence": model.minimumActionableConfidence,
        "actionable": model.actionable,
    }


def _regime_fit_payload(regime_fit: RegimeFitResult) -> dict[str, Any]:
    return {
        "score": regime_fit.score,
        "regimeKey": regime_fit.regimeKey,
        "components": {
            "trendStrength": regime_fit.trendStrength,
            "choppiness": regime_fit.choppiness,
            "atrPercentile": regime_fit.atrPercentile,
            "openingRangeExpansion": regime_fit.openingRangeExpansion,
            "gapState": regime_fit.gapState,
            "fiveMinuteStructure": regime_fit.fiveMinuteStructure,
            "vwapCrossingFrequency": regime_fit.vwapCrossingFrequency,
            "economicEventRisk": regime_fit.economicEventRisk,
        },
        "reasonCodes": list(regime_fit.reasonCodes),
    }


def _historical_reliability_payload(reliability: StrategyReliabilityEstimate) -> dict[str, Any]:
    return {
        "score": reliability.score,
        "version": reliability.version,
        "sourceWindow": reliability.sourceWindow,
        "reasonCodes": list(reliability.reasonCodes),
    }


def _session_series_quality_payload(quality: SessionSeriesQuality) -> dict[str, Any]:
    return {
        "isComplete": quality.isComplete,
        "isFresh": quality.isFresh,
        "hasDuplicates": quality.hasDuplicates,
        "hasMissingIntervals": quality.hasMissingIntervals,
        "hasOutOfOrderBars": quality.hasOutOfOrderBars,
        "hasZeroVolumeBars": quality.hasZeroVolumeBars,
        "symbolMatches": quality.symbolMatches,
        "timeframeMatches": quality.timeframeMatches,
        "sessionMatches": quality.sessionMatches,
        "latestCompletedBarEnd": quality.latestCompletedBarEnd.isoformat().replace("+00:00", "Z") if quality.latestCompletedBarEnd else None,
        "qualityReasonCodes": list(quality.qualityReasonCodes),
    }


def _exchange_session_payload(session: ExchangeSession) -> dict[str, Any]:
    return session.model_dump(mode="json")


def _pullback_payload(pullback: Pullback | None) -> dict[str, Any] | None:
    if pullback is None:
        return None
    return {
        "startIndex": pullback.startIndex,
        "endIndex": pullback.endIndex,
        "pullbackExtreme": round(pullback.pullbackExtreme, 4),
        "averageVolume": round(pullback.averageVolume, 4),
        "retracement": round(pullback.retracement, 4),
        "pullbackStart": pullback.pullbackStart.isoformat().replace("+00:00", "Z"),
        "pullbackEnd": pullback.pullbackEnd.isoformat().replace("+00:00", "Z"),
        "pullbackDuration": round(pullback.pullbackDuration, 4),
        "countertrendCandleCount": pullback.countertrendCandleCount,
        "pauseCandleCount": pullback.pauseCandleCount,
        "directionalEfficiency": round(pullback.directionalEfficiency, 4),
        "maximumRetracement": round(pullback.maximumRetracement, 4),
        "averageCountertrendVolume": round(pullback.averageCountertrendVolume, 4),
        "classification": pullback.classification.value,
    }


def _vwap_penetration_atr(candle: dict[str, Any], vwap: float, atr: float, direction: Signal) -> float:
    if atr <= 0:
        return 0.0
    if direction == Signal.BUY:
        return max(0.0, (vwap - float(candle["low"])) / atr)
    return max(0.0, (float(candle["high"]) - vwap) / atr)


def _close_location(candle: dict[str, Any], direction: Signal) -> float:
    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])
    candle_range = max(high - low, 0.01)
    if direction == Signal.BUY:
        return max(0.0, min(1.0, (close - low) / candle_range))
    return max(0.0, min(1.0, (high - close) / candle_range))


def _pullback_pivot(candles: list[dict[str, Any]], pullback: Pullback, direction: Signal) -> float:
    selected = candles[pullback.startIndex : pullback.endIndex + 1]
    if direction == Signal.BUY:
        return max(float(candle["high"]) for candle in selected)
    return min(float(candle["low"]) for candle in selected)


def _nearest_opposing_level_distance_atr(candles: list[dict[str, Any]], index: int, direction: Signal, close: float, atr: float) -> float | None:
    if atr <= 0:
        return None
    lookback = candles[max(0, index - 20) : index]
    if not lookback:
        return None
    if direction == Signal.BUY:
        levels = [float(candle["high"]) for candle in lookback if float(candle["high"]) > close]
        nearest = min(levels, default=None)
        return (nearest - close) / atr if nearest is not None else None
    levels = [float(candle["low"]) for candle in lookback if float(candle["low"]) < close]
    nearest = max(levels, default=None)
    return (close - nearest) / atr if nearest is not None else None


def _failed_confirmation_quality(reason_codes: tuple[str, ...]) -> ConfirmationQualityResult:
    return ConfirmationQualityResult(
        passed=False,
        closeBeyondPullbackPivot=False,
        closeBeyondPreviousExtreme=False,
        closeAboveAnchor=False,
        vwapOk=False,
        closeLocation=0.0,
        closeLocationOk=False,
        confirmationVolumeOk=False,
        rejectionWickFraction=1.0,
        rejectionWickOk=False,
        rangeAtr=0.0,
        rangeOk=False,
        vwapDistanceAtr=None,
        ema20DistanceAtr=None,
        extensionOk=False,
        nearestOpposingLevelDistanceAtr=None,
        opposingLevelOk=False,
        reasonCodes=reason_codes,
    )


def _vwap_close_wrong_side(candle: dict[str, Any], vwap: float, direction: Signal) -> bool:
    close = float(candle["close"])
    if direction == Signal.BUY:
        return close < vwap
    return close > vwap


def _wrong_side_vwap_structure(candles: list[dict[str, Any]], vwaps: list[float | None], direction: Signal) -> bool:
    wrong_side = [
        candle
        for candle, vwap in zip(candles, vwaps)
        if vwap is not None and _vwap_close_wrong_side(candle, vwap, direction)
    ]
    if len(wrong_side) < 2:
        return False
    previous = wrong_side[-2]
    current = wrong_side[-1]
    if direction == Signal.BUY:
        return float(current["high"]) < float(previous["high"]) and float(current["low"]) < float(previous["low"])
    return float(current["high"]) > float(previous["high"]) and float(current["low"]) > float(previous["low"])


def _previous_indicator_by_timestamp(candles: list[dict[str, Any]], values: list[float | None]) -> dict[datetime, float | None]:
    result: dict[datetime, float | None] = {}
    for index, candle in enumerate(candles):
        result[_timestamp(candle)] = values[index - 1] if index > 0 else None
    return result


def _directional_efficiency(candles: list[dict[str, Any]], start_index: int, end_index: int) -> float:
    if end_index <= start_index:
        return 0.0
    origin = float(candles[start_index]["open"])
    final_close = float(candles[end_index]["close"])
    path = 0.0
    previous_close = origin
    for candle in candles[start_index : end_index + 1]:
        current_close = float(candle["close"])
        path += abs(current_close - previous_close)
        previous_close = current_close
    if path <= 0:
        return 1.0 if final_close != origin else 0.0
    return max(0.0, min(1.0, abs(final_close - origin) / path))


def _directional_closes(candles: list[dict[str, Any]], start_index: int, end_index: int, direction: Signal) -> int:
    selected = candles[start_index : end_index + 1]
    if direction == Signal.BUY:
        return sum(1 for candle in selected if float(candle["close"]) > float(candle["open"]))
    if direction == Signal.SELL:
        return sum(1 for candle in selected if float(candle["close"]) < float(candle["open"]))
    return 0


def _impulse_close_location(candle: dict[str, Any], direction: Signal) -> float:
    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])
    candle_range = high - low
    if candle_range <= 0:
        return 0.5
    if direction == Signal.BUY:
        return max(0.0, min(1.0, (close - low) / candle_range))
    return max(0.0, min(1.0, (high - close) / candle_range))


def _impulse_range_close_location(candles: list[dict[str, Any]], direction: Signal) -> float:
    if not candles:
        return 0.5
    highest_high = max(float(candle["high"]) for candle in candles)
    lowest_low = min(float(candle["low"]) for candle in candles)
    final_close = float(candles[-1]["close"])
    impulse_range = highest_high - lowest_low
    if impulse_range <= 0:
        return 0.5
    if direction == Signal.BUY:
        return max(0.0, min(1.0, (final_close - lowest_low) / impulse_range))
    return max(0.0, min(1.0, (highest_high - final_close) / impulse_range))


def _body_to_range_ratio(candle: dict[str, Any]) -> float:
    candle_range = float(candle["high"]) - float(candle["low"])
    if candle_range <= 0:
        return 0.0
    return abs(float(candle["close"]) - float(candle["open"])) / candle_range


def _supports_direction(candle: dict[str, Any], direction: Signal) -> bool:
    if direction == Signal.BUY:
        return float(candle["close"]) > float(candle["open"])
    if direction == Signal.SELL:
        return float(candle["close"]) < float(candle["open"])
    return False


def _opposes_direction(candle: dict[str, Any], direction: Signal) -> bool:
    if direction == Signal.BUY:
        return float(candle["close"]) < float(candle["open"])
    if direction == Signal.SELL:
        return float(candle["close"]) > float(candle["open"])
    return False


def _maximum_internal_retracement(candles: list[dict[str, Any]], direction: Signal) -> float:
    if len(candles) < 2:
        return 0.0
    closes = [float(candle["close"]) for candle in candles]
    impulse_range = abs(closes[-1] - float(candles[0]["open"]))
    if impulse_range <= 0:
        return 0.0
    maximum_retracement = 0.0
    if direction == Signal.BUY:
        best_close = closes[0]
        for close in closes[1:]:
            best_close = max(best_close, close)
            maximum_retracement = max(maximum_retracement, (best_close - close) / impulse_range)
    elif direction == Signal.SELL:
        best_close = closes[0]
        for close in closes[1:]:
            best_close = min(best_close, close)
            maximum_retracement = max(maximum_retracement, (close - best_close) / impulse_range)
    return max(0.0, maximum_retracement)


def _five_minute_permission_context(five_minute_candles: list[dict[str, Any]], exchange_session: ExchangeSession) -> dict[str, bool]:
    regular = [
        candle
        for candle in five_minute_candles
        if exchange_session.contains_timestamp(_timestamp(candle))
    ]
    if not regular:
        return {"bullish": False, "bearish": False}
    window = regular[-3:]
    first_open = float(window[0]["open"])
    last_close = float(window[-1]["close"])
    bullish_closes = sum(1 for candle in window if float(candle["close"]) > float(candle["open"]))
    bearish_closes = sum(1 for candle in window if float(candle["close"]) < float(candle["open"]))
    return {
        "bullish": last_close > first_open and bullish_closes >= max(1, len(window) - 1),
        "bearish": last_close < first_open and bearish_closes >= max(1, len(window) - 1),
    }


def _timestamp(candle: dict[str, Any]) -> datetime:
    value = candle["timestamp"]
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


def _symbol_from_context(context: StrategyEvaluationContext, candles: list[dict[str, Any]]) -> str:
    raw_inputs = context.featureSnapshot.rawInputs
    symbol = raw_inputs.get("symbol") or raw_inputs.get("underlyingSymbol")
    if symbol:
        return str(symbol)
    for candle in reversed(candles):
        value = candle.get("symbol")
        if value:
            return str(value)
    return "SPY"


def _setup_id(context: StrategyEvaluationContext, result: StateMachineResult) -> str | None:
    if result.impulse is None or result.pullback is None:
        return None
    payload = "|".join(
        (
            context.registryEntry.strategyId,
            context.sessionDate.isoformat(),
            result.impulse.startTimestamp.isoformat().replace("+00:00", "Z"),
            result.impulse.direction.value,
            result.pullback.pullbackStart.isoformat().replace("+00:00", "Z"),
            context.configurationHash,
        )
    )
    return f"fpao_setup_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _event_id(context: StrategyEvaluationContext, result: StateMachineResult) -> str | None:
    if result.impulse is None or result.pullback is None or result.confirmationBar is None:
        return None
    payload = "|".join(
        (
            context.registryEntry.strategyId,
            context.sessionDate.isoformat(),
            result.impulse.startTimestamp.isoformat().replace("+00:00", "Z"),
            result.impulse.direction.value,
            result.pullback.pullbackStart.isoformat().replace("+00:00", "Z"),
            result.confirmationBar.barEndTimestamp.isoformat().replace("+00:00", "Z"),
            context.configurationHash,
        )
    )
    return f"fpao_event_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]}"


def _invalidation_reason(result: StateMachineResult) -> str | None:
    if result.state not in {FirstPullbackState.INVALIDATED, FirstPullbackState.FIRST_PULLBACK_REJECTED}:
        return None
    for reason in result.reasonCodes:
        if not reason.startswith("state:") and not reason.startswith("pullback:"):
            return reason
    return result.reasonCodes[0] if result.reasonCodes else None


def _bar_finalization(candle: dict[str, Any], evaluation_timestamp: datetime | None, finalization_lag_seconds: int) -> BarFinalization:
    bar_start = _timestamp(candle)
    bar_end = _bar_end_timestamp(candle)
    cutoff = evaluation_timestamp - timedelta(seconds=finalization_lag_seconds) if evaluation_timestamp else None
    return BarFinalization(
        barStartTimestamp=bar_start,
        barEndTimestamp=bar_end,
        wasFinalized=bool(cutoff and bar_end <= cutoff),
        providerRevision=_provider_revision(candle),
    )


def _bar_end_timestamp(candle: dict[str, Any]) -> datetime:
    return _timestamp(candle) + _timeframe_duration(candle.get("timeframe"))


def _timeframe_duration(timeframe: Any) -> timedelta:
    if timeframe == "5Min":
        return timedelta(minutes=5)
    if timeframe == "15Min":
        return timedelta(minutes=15)
    return timedelta(minutes=1)


def _provider_revision(candle: dict[str, Any]) -> str | None:
    value = candle.get("providerRevision", candle.get("provider_revision", candle.get("revision")))
    return str(value) if value is not None else None


def _impulse_structure_ok(candles: list[dict[str, Any]], start_index: int, end_index: int, direction: Signal) -> bool:
    highs = [float(candle["high"]) for candle in candles[start_index : end_index + 1]]
    lows = [float(candle["low"]) for candle in candles[start_index : end_index + 1]]
    if len(highs) < 3:
        return False
    if direction == Signal.BUY:
        return highs[-1] >= max(highs[:-1]) and lows[-1] >= min(lows[:-1])
    return lows[-1] <= min(lows[:-1]) and highs[-1] <= max(highs[:-1])


def _minutes_after_open(timestamp: datetime, exchange_session: ExchangeSession) -> float:
    return exchange_session.minutes_after_open(timestamp)


def _new_york_datetime(value: datetime) -> datetime:
    return value.astimezone(UTC).astimezone(NEW_YORK)
