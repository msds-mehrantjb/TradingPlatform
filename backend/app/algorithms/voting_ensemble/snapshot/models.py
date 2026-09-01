"""Immutable point-in-time snapshot contract for Voting Ensemble evaluation."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.algorithms.voting_ensemble.models import VotingCandle


VOTING_ENSEMBLE_SNAPSHOT_VERSION = "voting_ensemble_point_in_time_snapshot_v1"
FeedHealthStatus = Literal["ready", "fail_closed"]


class ImmutableSnapshotModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FinalizedCandleEvidence(ImmutableSnapshotModel):
    candle: VotingCandle
    completionTimestamp: datetime
    finalizationTimestamp: datetime
    finalizationLagSeconds: float = Field(ge=0.0)


class AggregatedTimeframeEvidence(ImmutableSnapshotModel):
    timeframe: Literal["5Min", "15Min"]
    candles: tuple[FinalizedCandleEvidence, ...]


class NBBOSnapshot(ImmutableSnapshotModel):
    bid: float = Field(gt=0.0)
    ask: float = Field(gt=0.0)
    bidSize: float = Field(gt=0.0)
    askSize: float = Field(gt=0.0)
    spreadDollars: float = Field(ge=0.0)
    spreadBasisPoints: float = Field(ge=0.0)
    quoteTimestamp: datetime
    lastTradeTimestamp: datetime
    marketDataReceiptTimestamp: datetime
    marketDataAgeSeconds: float = Field(ge=0.0)
    quoteAgeSeconds: float = Field(ge=0.0)

    @model_validator(mode="after")
    def bid_must_not_cross_ask(self) -> "NBBOSnapshot":
        if self.ask < self.bid:
            raise ValueError("NBBO ask must be greater than or equal to bid")
        return self


class SymbolPointInTimeData(ImmutableSnapshotModel):
    symbol: str
    candles: tuple[FinalizedCandleEvidence, ...]
    latestClose: float | None = None
    latestTimestamp: datetime | None = None


class BreadthPointInTimeData(ImmutableSnapshotModel):
    components: dict[str, SymbolPointInTimeData] = Field(default_factory=dict)
    externalFeed: dict[str, Any] | None = None
    timestamp: datetime | None = None
    providerTimestamp: datetime | None = None
    receiptTimestamp: datetime | None = None


class SessionFeatureSnapshot(ImmutableSnapshotModel):
    vwap: float | None = None
    vwapSlope: float | None = None
    atr: float | None = None
    adx: float | None = None
    bollingerMiddle: float | None = None
    bollingerUpper: float | None = None
    bollingerLower: float | None = None
    volumeCurrent: float | None = None
    volumeAverage20: float | None = None
    volumeRelative20: float | None = None


class LevelSnapshot(ImmutableSnapshotModel):
    high: float | None = None
    low: float | None = None
    open: float | None = None
    close: float | None = None


class EventStateSnapshot(ImmutableSnapshotModel):
    state: dict[str, Any] = Field(default_factory=dict)
    providerTimestamp: datetime | None = None
    receiptTimestamp: datetime | None = None


class VotingEnsembleReadinessDecision(ImmutableSnapshotModel):
    ready: bool
    status: FeedHealthStatus
    mandatoryFailures: tuple[str, ...] = ()
    staleInputs: tuple[str, ...] = ()
    malformedInputs: tuple[str, ...] = ()
    reasonCodes: tuple[str, ...]


class VotingEnsembleEvaluationSnapshot(ImmutableSnapshotModel):
    algorithmId: Literal["voting_ensemble"] = "voting_ensemble"
    snapshotVersion: str = VOTING_ENSEMBLE_SNAPSHOT_VERSION
    symbol: Literal["SPY"] = "SPY"
    spyOneMinuteCandles: tuple[FinalizedCandleEvidence, ...]
    aggregatedFiveMinuteEvidence: AggregatedTimeframeEvidence
    aggregatedFifteenMinuteEvidence: AggregatedTimeframeEvidence
    nbbo: NBBOSnapshot | None
    qqq: SymbolPointInTimeData
    iwm: SymbolPointInTimeData
    breadth: BreadthPointInTimeData
    features: SessionFeatureSnapshot
    priorDayLevels: LevelSnapshot
    premarketLevels: LevelSnapshot
    openingRangeLevels: LevelSnapshot
    economicEventState: EventStateSnapshot
    sessionState: dict[str, Any] = Field(default_factory=dict)
    marketForecast: dict[str, Any] = Field(default_factory=dict)
    accountRiskSnapshot: dict[str, Any] = Field(default_factory=dict)
    operationalHealthSnapshot: dict[str, Any] = Field(default_factory=dict)
    settingsHash: str
    evaluationTimestamp: datetime
    barFinalizationTimestamp: datetime
    feedHealthStatus: FeedHealthStatus
    dataReadiness: VotingEnsembleReadinessDecision
    snapshotHash: str
    reasonCodes: tuple[str, ...]

    def to_evaluate_payload(self) -> dict[str, Any]:
        snapshot_payload = self.model_dump(mode="json")
        context = {
            "pointInTimeSnapshot": snapshot_payload,
            "priorDayOHLC": self.priorDayLevels.model_dump(mode="json"),
            "premarket": self.premarketLevels.model_dump(mode="json"),
            "openingRange": self.openingRangeLevels.model_dump(mode="json"),
            "event": self.economicEventState.model_dump(mode="json"),
            "sessionState": self.sessionState,
            "marketForecast": self.marketForecast,
            "accountRiskSnapshot": self.accountRiskSnapshot,
            "operationalHealthSnapshot": self.operationalHealthSnapshot,
            "settingsHash": self.settingsHash,
        }
        if self.breadth.externalFeed:
            context["externalBreadthFeed"] = self.breadth.externalFeed
        return {
            "symbol": self.symbol,
            "data_timestamp": self.evaluationTimestamp.isoformat(),
            "candles": [item.candle.model_dump(mode="json") for item in self.spyOneMinuteCandles],
            "spy_5m_candles": [item.candle.model_dump(mode="json") for item in self.aggregatedFiveMinuteEvidence.candles],
            "spy_15m_candles": [item.candle.model_dump(mode="json") for item in self.aggregatedFifteenMinuteEvidence.candles],
            "market_context": context,
            "qqq_candles": [item.candle.model_dump(mode="json") for item in self.qqq.candles],
            "iwm_candles": [item.candle.model_dump(mode="json") for item in self.iwm.candles],
            "breadth_components": {
                symbol: [item.candle.model_dump(mode="json") for item in data.candles]
                for symbol, data in self.breadth.components.items()
            },
            "external_breadth_feed": self.breadth.externalFeed,
            "nbbo": self.nbbo.model_dump(mode="json") if self.nbbo else None,
        }
