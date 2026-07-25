from __future__ import annotations

import hashlib
import json
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.app.algorithms.voting_ensemble.models import VotingCandle
from backend.app.algorithms.voting_ensemble.snapshot.models import VotingEnsembleEvaluationSnapshot
from backend.app.algorithms.voting_ensemble.strategies.directional.signal_contract import (
    DirectionalStrategySignal,
    directional_signal,
    hold_signal,
)
from backend.app.algorithms.voting_ensemble.strategies.directional.snapshot_helpers import (
    close_location,
    fifteen_minute_candles,
    five_minute_candles,
    spy_candles,
    trend_score,
)
from backend.app.domain.models import RegimeState


TrendSide = Literal["Buy", "Sell"]


class VwapTrendContinuationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    configVersion: str = "vwap_trend_continuation_v1"
    minimumBarsAfterOpen: int = Field(default=18, ge=6, le=240)
    establishedLookbackBars: int = Field(default=5, ge=2, le=30)
    pullbackLookbackBars: int = Field(default=4, ge=1, le=20)
    trendLookbackBars: int = Field(default=10, ge=4, le=60)
    minTrendScore: float = Field(default=0.06, ge=0.0, le=1.0)
    minVwapSlopeAtr: float = Field(default=0.005, ge=0.0, le=1.0)
    maxPullbackDistanceFromVwapAtr: float = Field(default=0.45, ge=0.0, le=5.0)
    maxVwapPenetrationAtr: float = Field(default=0.20, ge=0.0, le=5.0)
    minConfirmationBodyAtr: float = Field(default=0.05, ge=0.0, le=5.0)
    minConfirmationCloseLocation: float = Field(default=0.58, ge=0.0, le=1.0)
    minRelativeVolume: float = Field(default=0.70, ge=0.0, le=5.0)
    maxRelativeVolume: float = Field(default=3.00, ge=0.1, le=10.0)
    maxSpreadBasisPoints: float = Field(default=12.0, ge=0.0)
    minDisplayedLiquidityShares: float = Field(default=500.0, ge=0.0)
    minHigherTimeframeTrendScore: float = Field(default=0.02, ge=0.0, le=1.0)
    maxEntryDistanceFromVwapAtr: float = Field(default=2.00, ge=0.1, le=10.0)
    maxConfirmationRangeAtr: float = Field(default=1.50, ge=0.1, le=10.0)
    minRegimeFit: float = Field(default=0.15, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_volume_bounds(self) -> "VwapTrendContinuationConfig":
        if self.maxRelativeVolume < self.minRelativeVolume:
            raise ValueError("maxRelativeVolume must be >= minRelativeVolume")
        return self

    @property
    def configurationHash(self) -> str:
        serialized = json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:12]


class VwapTrendContinuationStrategy:
    strategyId = "vwap_trend_continuation"
    strategyName = "VWAP Trend Continuation"
    strategyVersion = "vwap_trend_continuation_snapshot_v1"
    family = "trend"

    def __init__(self, config: VwapTrendContinuationConfig | None = None) -> None:
        self.config = config or VwapTrendContinuationConfig()

    def evaluate(
        self,
        snapshot: VotingEnsembleEvaluationSnapshot,
        *,
        correlation_id: str,
        regime_state: RegimeState | None = None,
    ) -> DirectionalStrategySignal:
        candles = spy_candles(snapshot)
        event_id = self._event_correlation_id(snapshot)
        min_required = max(self.config.minimumBarsAfterOpen + 1, self.config.trendLookbackBars + self.config.pullbackLookbackBars + 1)
        if len(candles) < min_required:
            return self._hold(snapshot, event_id, "VWAP continuation requires later-session trend, pullback, and confirmation evidence.", "vwap_trend_continuation.insufficient_data", data_ready=False)
        if snapshot.nbbo is None:
            return self._hold(snapshot, event_id, "NBBO spread and displayed liquidity are mandatory.", "vwap_trend_continuation.missing_nbbo", data_ready=False)
        atr = snapshot.features.atr
        vwap = snapshot.features.vwap
        vwap_slope = snapshot.features.vwapSlope
        if atr is None or atr <= 0 or vwap is None or vwap <= 0 or vwap_slope is None:
            return self._hold(snapshot, event_id, "ATR, session VWAP, and VWAP slope are mandatory.", "vwap_trend_continuation.missing_vwap_features", data_ready=False)
        if self._regime_fit(regime_state) < self.config.minRegimeFit:
            return self._hold(snapshot, event_id, "Trend regime fit does not support VWAP continuation evidence.", "vwap_trend_continuation.regime_fit_too_low")
        if snapshot.nbbo.spreadBasisPoints > self.config.maxSpreadBasisPoints:
            return self._hold(snapshot, event_id, "Spread exceeds VWAP continuation permission.", "vwap_trend_continuation.spread_too_wide")
        displayed_liquidity = snapshot.nbbo.bidSize + snapshot.nbbo.askSize
        if displayed_liquidity < self.config.minDisplayedLiquidityShares:
            return self._hold(snapshot, event_id, "Displayed liquidity is insufficient.", "vwap_trend_continuation.insufficient_liquidity")

        latest = candles[-1]
        previous = candles[-2]
        side = self._side(latest, vwap, vwap_slope, atr)
        if side is None:
            return self._hold(snapshot, event_id, "Price is not established on the VWAP side supported by VWAP slope.", "vwap_trend_continuation.no_vwap_side")
        if not self._price_established(candles, side, vwap):
            return self._hold(snapshot, event_id, "Recent closes are not established on the correct side of session VWAP.", "vwap_trend_continuation.price_not_established")
        one_minute_trend = trend_score(candles, self.config.trendLookbackBars)
        if not self._trend_supports(side, one_minute_trend):
            return self._hold(snapshot, event_id, "One-minute trend structure does not support continuation.", "vwap_trend_continuation.trend_structure_unsupported", features={"trendScore": round(one_minute_trend, 4)})
        five_score = trend_score(five_minute_candles(snapshot), 4)
        fifteen_score = trend_score(fifteen_minute_candles(snapshot), 2)
        if not self._higher_timeframe_allows(side, five_score, fifteen_score):
            return self._hold(snapshot, event_id, "Higher-timeframe permission is absent.", "vwap_trend_continuation.higher_timeframe_permission_missing", features={"trend5m": round(five_score, 4), "trend15m": round(fifteen_score, 4)})

        pullback = candles[-self.config.pullbackLookbackBars - 1 : -1]
        pullback_distance = self._pullback_distance(side, pullback, vwap, atr)
        if pullback_distance is None or pullback_distance > self.config.maxPullbackDistanceFromVwapAtr:
            return self._hold(snapshot, event_id, "Pullback did not approach VWAP or a controlled VWAP distance.", "vwap_trend_continuation.pullback_not_near_vwap", features={"pullbackDistanceAtr": round(pullback_distance, 4) if pullback_distance is not None else -1.0})
        if self._pullback_invalidates(side, pullback, vwap, atr):
            return self._hold(snapshot, event_id, "Pullback invalidated the VWAP trend structure.", "vwap_trend_continuation.pullback_invalidated")
        if not self._confirmation_resumes(side, latest, previous, vwap, atr):
            return self._hold(snapshot, event_id, "Confirmation candle has not resumed the VWAP trend.", "vwap_trend_continuation.confirmation_missing")

        relative_volume = self._relative_volume(candles)
        if relative_volume < self.config.minRelativeVolume or relative_volume > self.config.maxRelativeVolume:
            return self._hold(snapshot, event_id, "Confirmation volume behaviour is outside the acceptable continuation band.", "vwap_trend_continuation.volume_unacceptable", features={"relativeVolume": round(relative_volume, 4)})
        entry_distance = abs(latest.close - vwap) / atr
        confirmation_range_atr = (latest.high - latest.low) / atr
        if entry_distance > self.config.maxEntryDistanceFromVwapAtr or confirmation_range_atr > self.config.maxConfirmationRangeAtr:
            return self._hold(
                snapshot,
                event_id,
                "Entry is excessively extended from VWAP or the confirmation point.",
                "vwap_trend_continuation.entry_extended",
                features={"entryDistanceAtr": round(entry_distance, 4), "confirmationRangeAtr": round(confirmation_range_atr, 4)},
            )

        return self._signal(
            snapshot=snapshot,
            event_id=event_id,
            side=side,
            trend_score_value=one_minute_trend,
            five_score=five_score,
            fifteen_score=fifteen_score,
            pullback_distance=pullback_distance,
            relative_volume=relative_volume,
            entry_distance=entry_distance,
            displayed_liquidity=displayed_liquidity,
        )

    def _signal(
        self,
        *,
        snapshot: VotingEnsembleEvaluationSnapshot,
        event_id: str,
        side: TrendSide,
        trend_score_value: float,
        five_score: float,
        fifteen_score: float,
        pullback_distance: float,
        relative_volume: float,
        entry_distance: float,
        displayed_liquidity: float,
    ) -> DirectionalStrategySignal:
        confidence = min(0.84, 0.50 + min(0.16, abs(trend_score_value) * 0.5) + min(0.10, max(0.0, relative_volume - 1.0) * 0.08) + min(0.08, abs(five_score) * 0.2))
        code = "vwap_trend_continuation.buy_confirmed" if side == "Buy" else "vwap_trend_continuation.sell_confirmed"
        return directional_signal(
            strategy_id=self.strategyId,
            strategy_name=self.strategyName,
            strategy_version=self.strategyVersion,
            family=self.family,
            signal=side,
            confidence=round(confidence, 4),
            evaluated_at=snapshot.evaluationTimestamp,
            correlation_id=event_id,
            evidence=(
                f"{side} VWAP trend continuation: established VWAP side, supportive VWAP slope, controlled pullback, and confirmation candle.",
            ),
            reason_codes=(code,),
            features={
                "trendScore": round(trend_score_value, 4),
                "trend5m": round(five_score, 4),
                "trend15m": round(fifteen_score, 4),
                "pullbackDistanceAtr": round(pullback_distance, 4),
                "relativeVolume": round(relative_volume, 4),
                "entryDistanceAtr": round(entry_distance, 4),
                "displayedLiquidityShares": round(displayed_liquidity, 4),
                "eventCorrelationId": event_id,
                "trendEventCorrelationId": event_id,
                "trendEvidenceRole": "anchor_behavior",
                "vwapContinuation": self._vwap_continuation_features(snapshot, pullback_distance),
                "shadowOnly": True,
            },
        )

    def _hold(
        self,
        snapshot: VotingEnsembleEvaluationSnapshot,
        event_id: str,
        reason: str,
        code: str,
        *,
        data_ready: bool = True,
        features: dict[str, Any] | None = None,
    ) -> DirectionalStrategySignal:
        return hold_signal(
            strategy_id=self.strategyId,
            strategy_name=self.strategyName,
            strategy_version=self.strategyVersion,
            family=self.family,
            evaluated_at=snapshot.evaluationTimestamp,
            correlation_id=event_id,
            reason=reason,
            reason_code=code,
            data_ready=data_ready,
            features={"shadowOnly": True, "eventCorrelationId": event_id, "trendEventCorrelationId": event_id, **(features or {})},
        )

    def _side(self, latest: VotingCandle, vwap: float, vwap_slope: float, atr: float) -> TrendSide | None:
        slope_atr = vwap_slope / atr
        if latest.close > vwap and slope_atr >= self.config.minVwapSlopeAtr:
            return "Buy"
        if latest.close < vwap and slope_atr <= -self.config.minVwapSlopeAtr:
            return "Sell"
        return None

    def _price_established(self, candles: tuple[VotingCandle, ...], side: TrendSide, vwap: float) -> bool:
        window = candles[-self.config.establishedLookbackBars :]
        if side == "Buy":
            return sum(1 for candle in window if candle.close > vwap) >= max(2, len(window) - 1)
        return sum(1 for candle in window if candle.close < vwap) >= max(2, len(window) - 1)

    def _trend_supports(self, side: TrendSide, score: float) -> bool:
        return score >= self.config.minTrendScore if side == "Buy" else score <= -self.config.minTrendScore

    def _higher_timeframe_allows(self, side: TrendSide, five_score: float, fifteen_score: float) -> bool:
        if side == "Buy":
            return five_score >= self.config.minHigherTimeframeTrendScore and fifteen_score >= self.config.minHigherTimeframeTrendScore
        return five_score <= -self.config.minHigherTimeframeTrendScore and fifteen_score <= -self.config.minHigherTimeframeTrendScore

    def _pullback_distance(self, side: TrendSide, pullback: tuple[VotingCandle, ...], vwap: float, atr: float) -> float | None:
        if not pullback:
            return None
        if side == "Buy":
            return max(0.0, min(candle.low for candle in pullback) - vwap) / atr
        return max(0.0, vwap - max(candle.high for candle in pullback)) / atr

    def _pullback_invalidates(self, side: TrendSide, pullback: tuple[VotingCandle, ...], vwap: float, atr: float) -> bool:
        tolerance = self.config.maxVwapPenetrationAtr * atr
        if side == "Buy":
            return any(candle.close < vwap - tolerance for candle in pullback)
        return any(candle.close > vwap + tolerance for candle in pullback)

    def _confirmation_resumes(self, side: TrendSide, latest: VotingCandle, previous: VotingCandle, vwap: float, atr: float) -> bool:
        body_atr = abs(latest.close - latest.open) / atr
        if body_atr < self.config.minConfirmationBodyAtr:
            return False
        if side == "Buy":
            return latest.close > latest.open and latest.close > previous.high and latest.close > vwap and close_location(latest) >= self.config.minConfirmationCloseLocation
        return latest.close < latest.open and latest.close < previous.low and latest.close < vwap and close_location(latest) <= 1.0 - self.config.minConfirmationCloseLocation

    def _relative_volume(self, candles: tuple[VotingCandle, ...]) -> float:
        if len(candles) < 2:
            return 1.0
        baseline = mean(candle.volume for candle in candles[:-1])
        return candles[-1].volume / max(baseline, 1.0)

    def _regime_fit(self, regime_state: RegimeState | None) -> float:
        if regime_state is None:
            return 1.0
        value = regime_state.features.get("trendFit")
        if not isinstance(value, int | float):
            return 1.0
        return max(0.0, min(1.0, float(value)))

    def _event_correlation_id(self, snapshot: VotingEnsembleEvaluationSnapshot) -> str:
        candles = spy_candles(snapshot)
        pullback_start = candles[-self.config.pullbackLookbackBars - 1].timestamp.isoformat() if len(candles) > self.config.pullbackLookbackBars else None
        confirmation = candles[-1].timestamp.isoformat() if candles else None
        side = "unknown"
        if candles and snapshot.features.vwap is not None:
            side = "long" if candles[-1].close > snapshot.features.vwap else "short" if candles[-1].close < snapshot.features.vwap else "flat"
        payload = {
            "eventType": "vwap_trend_continuation",
            "symbol": snapshot.symbol,
            "side": side,
            "pullbackStart": pullback_start,
            "confirmation": confirmation,
            "settingsHash": snapshot.settingsHash,
        }
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
        return f"trend-event-vwap-{digest}"

    def _vwap_continuation_features(self, snapshot: VotingEnsembleEvaluationSnapshot, pullback_distance: float) -> dict[str, str | float]:
        candles = spy_candles(snapshot)
        return {
            "pullbackTimestamp": candles[-2].timestamp.isoformat() if len(candles) >= 2 else snapshot.evaluationTimestamp.isoformat(),
            "confirmationTimestamp": candles[-1].timestamp.isoformat() if candles else snapshot.evaluationTimestamp.isoformat(),
            "sessionVwap": round(float(snapshot.features.vwap or 0.0), 4),
            "vwapSlope": round(float(snapshot.features.vwapSlope or 0.0), 6),
            "pullbackDistanceAtr": round(pullback_distance, 4),
        }


__all__ = ["VwapTrendContinuationConfig", "VwapTrendContinuationStrategy"]
