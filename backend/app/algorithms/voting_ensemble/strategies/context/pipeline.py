from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from backend.app.algorithms.voting_ensemble.models import FeatureValue, VotingStrategyVote
from backend.app.algorithms.voting_ensemble.snapshot.models import VotingEnsembleEvaluationSnapshot
from backend.app.algorithms.voting_ensemble.strategies.registry import StrategyRegistryEntry, resolve_strategy


CONTEXT_PIPELINE_VERSION = "voting_ensemble_context_pipeline_v1"
ALLOWED_CONTEXT_EFFECTS = {"confirm", "conflict", "risk_reduction", "entry_block", "neutral"}
_SHADOW_CONTEXT_OUTPUTS: list[dict[str, Any]] = []


class SnapshotContextModule(Protocol):
    strategyId: str

    def evaluate(self, snapshot: VotingEnsembleEvaluationSnapshot, *, active: bool) -> VotingStrategyVote:
        ...


@dataclass(frozen=True)
class ContextPipelineResult:
    active: tuple[VotingStrategyVote, ...]
    shadow: tuple[VotingStrategyVote, ...]


class VotingEnsembleContextPipeline:
    pipelineVersion = CONTEXT_PIPELINE_VERSION

    def __init__(self, modules: tuple[SnapshotContextModule, ...] | None = None) -> None:
        self.modules = modules or (
            RelativeStrengthQqqIwmSnapshotContext(),
            MarketBreadthMomentumSnapshotContext(),
            EconomicEventSnapshotContext(),
            MarketStructureSnapshotContext(),
            VolumeConfirmationSnapshotContext(),
            VwapPositionSnapshotContext(),
        )

    def evaluate(
        self,
        snapshot: VotingEnsembleEvaluationSnapshot,
        *,
        active_module_ids: tuple[str, ...],
        shadow_module_ids: tuple[str, ...],
    ) -> ContextPipelineResult:
        by_id = {module.strategyId: module for module in self.modules}
        active = tuple(by_id[module_id].evaluate(snapshot, active=True) for module_id in active_module_ids if module_id in by_id)
        shadow = tuple(by_id[module_id].evaluate(snapshot, active=False) for module_id in shadow_module_ids if module_id in by_id)
        persist_shadow_context_outputs(snapshot, shadow)
        return ContextPipelineResult(active=active, shadow=shadow)


class RelativeStrengthQqqIwmSnapshotContext:
    strategyId = "relative_strength_qqq_iwm"
    strategyName = "Relative Strength vs QQQ/IWM"
    strategyVersion = "2.1.0"
    maxAdjustment = 0.08

    def evaluate(self, snapshot: VotingEnsembleEvaluationSnapshot, *, active: bool) -> VotingStrategyVote:
        if len(snapshot.spyOneMinuteCandles) < 2 or len(snapshot.qqq.candles) < 2 or len(snapshot.iwm.candles) < 2:
            return _vote(
                self,
                active,
                "neutral",
                0.0,
                False,
                "QQQ/IWM point-in-time data is unavailable; relative strength remains neutral.",
                ("voting_ensemble.context.relative_strength.missing_inputs",),
                snapshot,
                {"maxConfidenceAdjustment": 0.0},
            )
        spy_return = _return(snapshot.spyOneMinuteCandles[-2].candle.close, snapshot.spyOneMinuteCandles[-1].candle.close)
        qqq_return = _return(snapshot.qqq.candles[-2].candle.close, snapshot.qqq.candles[-1].candle.close)
        iwm_return = _return(snapshot.iwm.candles[-2].candle.close, snapshot.iwm.candles[-1].candle.close)
        spread = spy_return - ((qqq_return + iwm_return) / 2.0)
        if spread > 0.001:
            effect = "confirm_long"
            reason_codes = ("voting_ensemble.context.relative_strength.confirm_long",)
            reason = "SPY relative strength can confirm long candidates only."
        elif spread < -0.001:
            effect = "confirm_short"
            reason_codes = ("voting_ensemble.context.relative_strength.confirm_short",)
            reason = "SPY relative weakness can confirm short candidates only."
        else:
            effect = "neutral"
            reason_codes = ("voting_ensemble.context.relative_strength.neutral",)
            reason = "SPY relative strength is neutral."
        return _vote(
            self,
            active,
            effect,
            0.45,
            True,
            reason,
            reason_codes,
            snapshot,
            {
                "relativeStrengthSpread": round(spread, 6),
                "spyReturn": round(spy_return, 6),
                "qqqReturn": round(qqq_return, 6),
                "iwmReturn": round(iwm_return, 6),
            },
            source_timestamps=_source_timestamps(snapshot, qqq=snapshot.qqq.latestTimestamp, iwm=snapshot.iwm.latestTimestamp),
        )


class MarketBreadthMomentumSnapshotContext:
    strategyId = "market_breadth_momentum"
    strategyName = "Market Breadth Momentum"
    strategyVersion = "2.1.0"
    maxAdjustment = 0.08

    def evaluate(self, snapshot: VotingEnsembleEvaluationSnapshot, *, active: bool) -> VotingStrategyVote:
        feed = snapshot.breadth.externalFeed or {}
        advancing = _number(feed.get("percentageAdvancing"))
        if advancing is None:
            return _vote(
                self,
                active,
                "neutral",
                0.0,
                False,
                "Breadth feed is missing; missing context does not become confirmation.",
                ("voting_ensemble.context.market_breadth.missing_inputs",),
                snapshot,
                {"maxConfidenceAdjustment": 0.0},
                source_timestamps=_source_timestamps(snapshot, breadth=snapshot.breadth.timestamp),
            )
        if advancing >= 0.58:
            effect, code, reason = "confirm_long", "voting_ensemble.context.market_breadth.confirm_long", "Breadth can confirm long candidates only."
        elif advancing <= 0.42:
            effect, code, reason = "confirm_short", "voting_ensemble.context.market_breadth.confirm_short", "Breadth can confirm short candidates only."
        else:
            effect, code, reason = "neutral", "voting_ensemble.context.market_breadth.neutral", "Breadth is neutral."
        return _vote(
            self,
            active,
            effect,
            0.45,
            True,
            reason,
            (code,),
            snapshot,
            {"percentageAdvancing": round(advancing, 4), "breadthCoverage": round(_number(feed.get("dataCoverage")) or 0.0, 4)},
            source_timestamps=_source_timestamps(snapshot, breadth=snapshot.breadth.timestamp),
        )


class EconomicEventSnapshotContext:
    strategyId = "economic_event_context"
    strategyName = "Economic Event Context"
    strategyVersion = "2.1.0"
    maxAdjustment = 0.08

    def evaluate(self, snapshot: VotingEnsembleEvaluationSnapshot, *, active: bool) -> VotingStrategyVote:
        event = snapshot.economicEventState.state
        high_risk = _event_blackout_active(event)
        if high_risk:
            return _vote(
                self,
                active,
                "entry_block",
                0.80,
                True,
                "High-risk economic event state is represented as an enforceable entry blackout/risk cap if promoted active.",
                ("voting_ensemble.context.economic_event.entry_blackout",),
                snapshot,
                {"entryBlackout": True, "riskMultiplierCap": 0.0, "eventState": str(event.get("state") or "")},
                source_timestamps=_source_timestamps(snapshot, provider=snapshot.economicEventState.providerTimestamp, receipt=snapshot.economicEventState.receiptTimestamp),
            )
        return _vote(
            self,
            active,
            "neutral",
            0.25,
            True,
            "Economic event context is neutral.",
            ("voting_ensemble.context.economic_event.neutral",),
            snapshot,
            {"entryBlackout": False, "riskMultiplierCap": 1.0},
            source_timestamps=_source_timestamps(snapshot, provider=snapshot.economicEventState.providerTimestamp, receipt=snapshot.economicEventState.receiptTimestamp),
        )


class MarketStructureSnapshotContext:
    strategyId = "market_structure_context"
    strategyName = "Market Structure Context"
    strategyVersion = "2.1.0"
    maxAdjustment = 0.06

    def evaluate(self, snapshot: VotingEnsembleEvaluationSnapshot, *, active: bool) -> VotingStrategyVote:
        latest = snapshot.spyOneMinuteCandles[-1].candle
        prior_high = snapshot.priorDayLevels.high
        prior_low = snapshot.priorDayLevels.low
        if prior_high is None or prior_low is None:
            return _vote(self, active, "neutral", 0.0, False, "Prior-day structure levels are missing.", ("voting_ensemble.context.market_structure.missing_inputs",), snapshot, {"maxConfidenceAdjustment": 0.0})
        if latest.close > prior_high:
            effect, code, reason = "confirm_long", "voting_ensemble.context.market_structure.above_prior_high", "Price structure can confirm long candidates only."
        elif latest.close < prior_low:
            effect, code, reason = "confirm_short", "voting_ensemble.context.market_structure.below_prior_low", "Price structure can confirm short candidates only."
        else:
            effect, code, reason = "neutral", "voting_ensemble.context.market_structure.inside_prior_range", "Price is inside prior-day structure."
        return _vote(self, active, effect, 0.35, True, reason, (code,), snapshot, {"priorDayHigh": prior_high, "priorDayLow": prior_low})


class VolumeConfirmationSnapshotContext:
    strategyId = "volume_confirmation_context"
    strategyName = "Volume Confirmation"
    strategyVersion = "2.1.0"
    maxAdjustment = 0.05

    def evaluate(self, snapshot: VotingEnsembleEvaluationSnapshot, *, active: bool) -> VotingStrategyVote:
        relative_volume = snapshot.features.volumeRelative20
        if relative_volume is None:
            return _vote(self, active, "neutral", 0.0, False, "Relative-volume context is missing.", ("voting_ensemble.context.volume.missing_inputs",), snapshot, {"maxConfidenceAdjustment": 0.0})
        if relative_volume >= 1.25:
            effect, code, reason = "confirm", "voting_ensemble.context.volume.confirmation", "Volume can confirm an existing directional candidate."
        elif relative_volume <= 0.65:
            effect, code, reason = "risk_reduction", "voting_ensemble.context.volume.low_participation", "Low participation reduces risk for an existing candidate."
        else:
            effect, code, reason = "neutral", "voting_ensemble.context.volume.neutral", "Volume confirmation is neutral."
        return _vote(self, active, effect, 0.35, True, reason, (code,), snapshot, {"relativeVolume20": round(relative_volume, 4)})


class VwapPositionSnapshotContext:
    strategyId = "vwap_position_context"
    strategyName = "VWAP Position Context"
    strategyVersion = "2.1.0"
    maxAdjustment = 0.05

    def evaluate(self, snapshot: VotingEnsembleEvaluationSnapshot, *, active: bool) -> VotingStrategyVote:
        vwap = snapshot.features.vwap
        slope = snapshot.features.vwapSlope
        if vwap is None or slope is None:
            return _vote(self, active, "neutral", 0.0, False, "VWAP context is missing.", ("voting_ensemble.context.vwap.missing_inputs",), snapshot, {"maxConfidenceAdjustment": 0.0})
        latest = snapshot.spyOneMinuteCandles[-1].candle.close
        if latest > vwap and slope > 0:
            effect, code, reason = "confirm_long", "voting_ensemble.context.vwap.confirm_long", "VWAP position can confirm long candidates only."
        elif latest < vwap and slope < 0:
            effect, code, reason = "confirm_short", "voting_ensemble.context.vwap.confirm_short", "VWAP position can confirm short candidates only."
        else:
            effect, code, reason = "neutral", "voting_ensemble.context.vwap.neutral", "VWAP position is neutral or conflicting."
        return _vote(self, active, effect, 0.35, True, reason, (code,), snapshot, {"sessionVwap": round(vwap, 4), "vwapSlope": round(slope, 6), "distanceFromVwap": round(latest - vwap, 4)})


def persist_shadow_context_outputs(snapshot: VotingEnsembleEvaluationSnapshot, outputs: tuple[VotingStrategyVote, ...]) -> None:
    if not outputs:
        return
    _SHADOW_CONTEXT_OUTPUTS.append(
        {
            "snapshotHash": snapshot.snapshotHash,
            "settingsHash": snapshot.settingsHash,
            "evaluationTimestamp": snapshot.evaluationTimestamp.isoformat(),
            "outputs": [output.model_dump(mode="json") for output in outputs],
        }
    )


def shadow_context_outputs() -> tuple[dict[str, Any], ...]:
    return tuple(_SHADOW_CONTEXT_OUTPUTS)


def clear_shadow_context_outputs() -> None:
    _SHADOW_CONTEXT_OUTPUTS.clear()


def _vote(
    module: SnapshotContextModule,
    active: bool,
    effect: str,
    confidence: float,
    data_ready: bool,
    reason: str,
    reason_codes: tuple[str, ...],
    snapshot: VotingEnsembleEvaluationSnapshot,
    features: dict[str, FeatureValue] | None = None,
    *,
    source_timestamps: dict[str, datetime | None] | None = None,
) -> VotingStrategyVote:
    entry: StrategyRegistryEntry = resolve_strategy(module.strategyId)
    bounded_effect = effect if effect.split("_")[0] in ALLOWED_CONTEXT_EFFECTS or effect in ALLOWED_CONTEXT_EFFECTS else "neutral"
    timestamps = source_timestamps or _source_timestamps(snapshot)
    latest_source = max((value for value in timestamps.values() if value is not None), default=None)
    freshness = _freshness_seconds(snapshot.evaluationTimestamp, latest_source)
    max_adjustment = 0.0 if not data_ready else min(float(getattr(module, "maxAdjustment", 0.08)), 0.08)
    payload_features: dict[str, FeatureValue] = {
        "strategyId": module.strategyId,
        "strategyVersion": getattr(module, "strategyVersion", entry.strategyVersion),
        "pipelineVersion": CONTEXT_PIPELINE_VERSION,
        "contextEffect": bounded_effect,
        "maxConfidenceAdjustment": round(max_adjustment, 4),
        "freshnessSeconds": round(freshness, 4) if freshness is not None else -1.0,
        "sourceTimestamps": _source_timestamp_string(timestamps),
        "reasonCode": reason_codes[0],
        "reasonCodes": ",".join(reason_codes),
        "shadowOnly": not active,
        "dataReadinessExplicit": data_ready,
        **(features or {}),
    }
    return VotingStrategyVote(
        strategy=getattr(module, "strategyName", entry.strategyName),
        family="event",
        role="context",
        signal="Hold",
        direction=0,
        confidence=max(0.0, min(1.0, confidence)),
        active=active,
        eligible=False,
        dataReady=data_ready,
        regimeFit=1.0,
        reliability=0.5,
        reason=reason,
        features=payload_features,
    )


def _source_timestamps(snapshot: VotingEnsembleEvaluationSnapshot, **extra: datetime | None) -> dict[str, datetime | None]:
    return {
        "spyLatest": snapshot.spyOneMinuteCandles[-1].candle.timestamp if snapshot.spyOneMinuteCandles else None,
        "quote": snapshot.nbbo.quoteTimestamp if snapshot.nbbo else None,
        "marketDataReceipt": snapshot.nbbo.marketDataReceiptTimestamp if snapshot.nbbo else None,
        **extra,
    }


def _source_timestamp_string(timestamps: dict[str, datetime | None]) -> str:
    parts = []
    for key in sorted(timestamps):
        value = timestamps[key]
        if value is not None:
            parts.append(f"{key}={_utc(value).isoformat()}")
    return ";".join(parts)


def _freshness_seconds(evaluation_timestamp: datetime, source_timestamp: datetime | None) -> float | None:
    if source_timestamp is None:
        return None
    return max(0.0, (_utc(evaluation_timestamp) - _utc(source_timestamp)).total_seconds())


def _utc(timestamp: datetime) -> datetime:
    return timestamp.astimezone(UTC) if timestamp.tzinfo else timestamp.replace(tzinfo=UTC)


def _return(start: float, end: float) -> float:
    return 0.0 if start == 0 else (end - start) / start


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _event_blackout_active(event: dict[str, Any]) -> bool:
    if bool(event.get("eventBlackoutActive", False)):
        return True
    importance = str(event.get("importance") or event.get("eventImportance") or "").lower()
    state = str(event.get("state") or event.get("eventState") or "").lower()
    return importance in {"high", "critical"} and state in {"active", "imminent", "shock"}
